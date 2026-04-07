import os
import datetime
import json
import mimetypes
from dotenv import load_dotenv
from google import genai
from google.genai import types
from database import get_inventory_analytics
from thefuzz import process, fuzz

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def find_item_fuzzy(user_msg, items):
    item_names = [i['item_name'] for i in items if i['item_name']]
    if not item_names: return None
    match, score = process.extractOne(user_msg, item_names, scorer=fuzz.partial_ratio)
    if score > 50: 
        return next(item for item in items if item['item_name'] == match)
    return None

def ask_digital_twin(user_message, chat_history):
    items, total, active = get_inventory_analytics()
    
    # Format the chat history so Gemini remembers context
    formatted_history = ""
    for msg in chat_history:
        role = "User" if msg['role'] == 'user' else "Assistant"
        formatted_history += f"{role}: {msg['content']}\n"

    target_item = find_item_fuzzy(user_message.lower(), items)
    today = datetime.date.today().strftime("%B %d, %Y")
    
    clean_items = [{k: v for k, v in i.items() if k not in ['id', 'file_path'] and v} for i in items]
    db_summary = json.dumps(clean_items)
    
    sys_prompt = (
        f"Today is {today}. You are an intelligent Digital Twin Assistant managing a user's inventory. "
        f"Here is their full database in JSON format: {db_summary}. "
        f"Overall Stats: Total Spent=₹{total}, Active Warranties={active}. "
        "Answer the user's query conversationally. Pay close attention to the Chat History to understand context and follow-up questions. "
        "If a specific duration for a warranty isn't explicitly listed in the data, logically deduce standard terms if appropriate, or advise them to check the attached document."
    )
    
    full_prompt = f"{sys_prompt}\n\nChat History:\n{formatted_history}\nUser: {user_message}\nAssistant:"
    contents = [full_prompt]
    
    # Vision Pipeline remains - automatically looks at the image if an item is mentioned
    if target_item and target_item.get('file_path') and os.path.exists(target_item['file_path']):
        try:
            file_path = target_item['file_path']
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type: mime_type = 'application/octet-stream'
            
            with open(file_path, "rb") as f:
                file_bytes = f.read()
                
            doc_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            contents.append(doc_part)
            contents[0] += "\n[I have attached the original receipt document for the mentioned item. Use it to find fine-print details.]"
        except Exception as e:
            print(f"Vision Attachment Error: {e}")

    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=contents)
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "I'm having trouble connecting right now."