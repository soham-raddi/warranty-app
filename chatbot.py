import os
import datetime
import json
import base64
import io
import fitz 
from PIL import Image
from dotenv import load_dotenv
from groq import Groq
from database import get_inventory_analytics
from thefuzz import process, fuzz

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def find_item_fuzzy(user_msg, items):
    item_names = [i['item_name'] for i in items if i['item_name']]
    if not item_names: return None
    match, score = process.extractOne(user_msg, item_names, scorer=fuzz.partial_ratio)
    if score > 50: 
        return next(item for item in items if item['item_name'] == match)
    return None

def encode_image(file_path):
    """Converts images or PDFs to Base64, with compression to prevent API timeouts."""
    encoded_images = []
    if file_path.lower().endswith('.pdf'):
        doc = fitz.open(file_path)
        for i in range(min(len(doc), 3)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_bytes = pix.tobytes("png")
            encoded_images.append((base64.b64encode(img_bytes).decode('utf-8'), "image/png"))
        doc.close()
    else:
        with Image.open(file_path) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            max_width = 1200
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_size = (max_width, int(float(img.height) * float(ratio)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            img_bytes = buffer.getvalue()
            
            encoded_images.append((base64.b64encode(img_bytes).decode('utf-8'), "image/jpeg"))
    return encoded_images

def ask_digital_twin(user_message, chat_history):
    items, total, active = get_inventory_analytics()
    
    today = datetime.date.today().strftime("%B %d, %Y")
    clean_items = [{k: v for k, v in i.items() if k not in ['id', 'file_path'] and v} for i in items]
    db_summary = json.dumps(clean_items)
    
    sys_prompt = (
        f"Today is {today}. You are an intelligent Digital Twin Assistant managing a user's inventory. "
        f"Here is their full database in JSON format: {db_summary}. "
        f"Overall Stats: Total Spent=₹{total}, Active Warranties={active}. "
        "Answer the user's query conversationally. Pay close attention to the Chat History to understand context. "
        "CRITICAL: Always read the 'total_amount' and 'price' fields in the JSON when a user asks about spending or costs. Do not claim data is missing if it exists in the JSON. "
        "If a specific duration for a warranty isn't explicitly listed, logically deduce standard terms if appropriate."
    )
    
    messages = [{"role": "system", "content": sys_prompt}]
    for msg in chat_history:
        messages.append({"role": msg['role'], "content": msg['content']})

    target_model = "llama-3.3-70b-versatile"
    target_item = find_item_fuzzy(user_message.lower(), items)
    
    if target_item and target_item.get('file_path') and os.path.exists(target_item['file_path']):
        try:
            image_list = encode_image(target_item['file_path'])
            target_model = "meta-llama/llama-4-scout-17b-16e-instruct"
            
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
        chat_completion = client.chat.completions.create(
            model=target_model,
            messages=messages,
            temperature=0.5
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"Groq Error: {e}")
        return "I'm having trouble connecting right now."