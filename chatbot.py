import os
import re
import datetime
import json
from database import get_all_settings, get_inventory_analytics
import storage
from llm_extractor import CHAT_MODEL, VISION_MODEL, encode_image, get_client
from thefuzz import fuzz

# A message word must be this similar to an item word (0-100) to count as a mention; tolerates typos.
WORD_MATCH_THRESHOLD = 85
CHAT_ROLES = {'user', 'assistant'}
# Words that appear in product names or questions but say nothing about which item is meant.
MATCH_STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'what', 'when', 'which', 'this', 'that', 'my', 'inch', 'pack',
    'new', 'black', 'white', 'blue', 'grey', 'gray', 'silver', 'core', 'chip', 'gen', 'pro', 'max', 'plus',
    'warranty', 'receipt', 'invoice', 'item', 'product', 'card', 'bill',
}


def _match_words(text):
    words = re.findall(r'[a-z][a-z0-9]+', str(text or '').lower())
    return {w for w in words if len(w) >= 3 and w not in MATCH_STOPWORDS}


def find_item_fuzzy(user_msg, items):
    """Returns the item whose name or brand words are mentioned in the message, or None."""
    message_words = _match_words(user_msg)
    if not message_words:
        return None

    best_item, best_score = None, 0
    for item in items:
        item_words = _match_words(f"{item.get('item_name') or ''} {item.get('brand') or ''}")
        score = sum(
            1 for word in item_words
            if any(fuzz.ratio(word, msg_word) >= WORD_MATCH_THRESHOLD for msg_word in message_words)
        )
        if score > best_score:
            best_item, best_score = item, score
    return best_item


def ask_digital_twin(user_message, chat_history, workspace_id):
    # Only this workspace's inventory is ever shown to the model.
    items, total, active, _alerts = get_inventory_analytics(workspace_id)
    settings = get_all_settings(workspace_id)
    currency_code = settings.get('currency_code', 'INR')
    currency_symbol = settings.get('currency_symbol', '₹')
    
    today = datetime.date.today().strftime("%B %d, %Y")
    # Keep zeros (e.g. has_warranty_card=0, days_remaining=0); drop only empty values and local file paths.
    hidden_fields = {'id', 'file_path', 'warranty_card_path'}
    clean_items = [{k: v for k, v in i.items() if k not in hidden_fields and v not in (None, '')} for i in items]
    db_summary = json.dumps(clean_items)
    
    sys_prompt = (
        f"Today is {today}. You are an intelligent Digital Twin Assistant managing a user's inventory. "
        f"Here is their full database in JSON format: {db_summary}. "
        f"Overall Stats: Total Spent={currency_symbol}{total}, Active Warranties={active}. "
        f"Amounts in the data are in {currency_code}; always write money with the {currency_symbol} symbol. "
        "Answer the user's query conversationally. Pay close attention to the Chat History to understand context. "
        "CRITICAL: Always read the 'total_amount' and 'price' fields in the JSON when a user asks about spending or costs. Do not claim data is missing if it exists in the JSON. "
        "Each item's 'warranty_status', 'warranty_expires_on' and 'days_remaining' are computed by the app and are authoritative; "
        "use them directly when asked whether something is under warranty. A status of 'Unknown' means the purchase date is missing. "
        "'has_warranty_card' is 1 when a warranty card file is attached and 0 when it is not."
    )
    
    messages = [{"role": "system", "content": sys_prompt}]
    for msg in chat_history:
        # Only replay real conversation turns; never let stored or client data inject system prompts.
        if msg.get('role') in CHAT_ROLES and msg.get('content'):
            messages.append({"role": msg['role'], "content": str(msg['content'])})

    target_model = CHAT_MODEL
    target_item = find_item_fuzzy(user_message, items)
    action = None

    def needs_card(item):
        # A card only helps with claims while the warranty is still running.
        return int(item.get('has_warranty_card') or 0) == 0 and item.get('warranty_status') in ('Active', 'Expiring Soon')

    if target_item and needs_card(target_item):
        action = {
            "type": "attach_warranty_card",
            "item_id": target_item.get('id'),
            "item_name": target_item.get('item_name') or "this item"
        }
    elif not target_item and any(k in user_message.lower() for k in ["warranty card", "attach card", "missing card"]):
        first_missing = next((i for i in items if needs_card(i)), None)
        if first_missing:
            action = {
                "type": "attach_warranty_card",
                "item_id": first_missing.get('id'),
                "item_name": first_missing.get('item_name') or "this item"
            }
    
    if target_item and target_item.get('file_path'):
        try:
            image_list = encode_image(storage.read(target_item['file_path']), target_item['file_path'])
            target_model = VISION_MODEL
            
            content_payload = [{"type": "text", "text": f"{user_message}\n\n[System Note: I have attached the original receipt documents for the mentioned item. Use them to find fine-print details.]"}]
            for base64_image, mime_type in image_list:
                content_payload.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
                })
                
            messages.append({"role": "user", "content": content_payload})
        except Exception as e:
            print(f"Vision Attachment Error: {e}")
            messages.append({"role": "user", "content": user_message})
    else:
        messages.append({"role": "user", "content": user_message})

    try:
        chat_completion = get_client().chat.completions.create(
            model=target_model,
            messages=messages,
            temperature=0.5
        )
        reply = chat_completion.choices[0].message.content
        return {
            "reply": reply,
            "action": action
        }
    except RuntimeError as e:
        return {"reply": str(e), "action": action, "error": True}
    except Exception as e:
        print(f"Groq Error: {e}")
        return {
            "reply": "I'm having trouble reaching the AI service right now. Please try again in a moment.",
            "action": action,
            "error": True
        }