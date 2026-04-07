import os
import json
import mimetypes
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def parse_receipt_image_to_json(file_path):
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = 'application/octet-stream'

    with open(file_path, 'rb') as f:
        file_bytes = f.read()

    document = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    
    prompt = (
        "Analyze this document/image carefully.\n"
        "1. If it is NOT a receipt, bill, or invoice, return: {\"error\": \"NOT_A_RECEIPT\"}\n"
        "2. If it IS a receipt, extract: item_name, brand, model_number, serial_number, "
        "category (Use broad categories like 'Electronic Gadgets', 'Kitchen Appliances'), "
        "date_of_purchase (YYYY-MM-DD), price, tax_amount, total_amount, payment_method, "
        "store_name (Marketplace name if applicable), store_contact, "
        "invoice_number (Extract the exact invoice or bill number), "
        "warranty_info (Extract exact duration if visible, e.g., '1 Year', '6 Months'), return_policy.\n"
        "Return ONLY valid JSON."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=[prompt, document]
        )
        text = response.text
        
        marker = chr(96) * 3 
        if f"{marker}json" in text:
            text = text.split(f"{marker}json")[1].split(marker)[0]
        elif marker in text:
            text = text.split(marker)[1].split(marker)[0]
            
        return json.loads(text.strip())
    except Exception as e:
        return {"error": str(e)}