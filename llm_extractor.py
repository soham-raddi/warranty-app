import os
import json
import base64
import io
import fitz
from PIL import Image
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def encode_image(file_path):
    """Converts images or MULTI-PAGE PDFs to a list of Base64 strings, compressing large images."""
    encoded_images = []
    
    if file_path.lower().endswith('.pdf'):
        doc = fitz.open(file_path)
        # Process up to the first 3 pages
        for i in range(min(len(doc), 3)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_bytes = pix.tobytes("png")
            encoded_images.append((base64.b64encode(img_bytes).decode('utf-8'), "image/png"))
        doc.close()
    else:
        with Image.open(file_path) as img:  #compression logic for large images
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Scale down if width exceeds 1200px
            max_width = 1200
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_size = (max_width, int(float(img.height) * float(ratio)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Save compressed image to buffer
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            img_bytes = buffer.getvalue()
            
            encoded_images.append((base64.b64encode(img_bytes).decode('utf-8'), "image/jpeg"))
            
    return encoded_images

def parse_receipt_image_to_json(file_path):
    try:
        image_list = encode_image(file_path)
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}

    prompt = (
        "Analyze these document pages carefully. They belong to a single invoice or receipt.\n"
        "1. If the document is NOT a receipt, bill, or invoice, return: {\"error\": \"NOT_A_RECEIPT\"}\n"
        "2. Identify the PRIMARY purchased item. If the invoice lists multiple items, extract the details for the main/highest-value product. "
        "Strict Rule: Do NOT extract supplementary charges (like shipping, handling, platform fees, bags, or tips) as the primary item.\n"
        "3. Extract the following details: item_name, brand, model_number, serial_number, "
        "category (Use broad categories like 'Electronic Gadgets', 'Kitchen Appliances', 'Apparel', 'Automotive', etc.), "
        "date_of_purchase (YYYY-MM-DD), price, tax_amount, total_amount (the grand total of the entire invoice), payment_method, "
        "store_name (The overall retailer or marketplace name), store_contact, "
        "invoice_number, warranty_info, return_policy.\n"
        "Return ONLY valid JSON. Do not add any conversational text or Markdown formatting."
    )
    
    content_payload = [{"type": "text", "text": prompt}]
    for base64_image, mime_type in image_list:
        content_payload.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
        })
    
    try:
        chat_completion = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": content_payload}],
            temperature=0.1, 
        )
        
        text = chat_completion.choices[0].message.content
        
        marker = chr(96) * 3 
        if f"{marker}json" in text:
            text = text.split(f"{marker}json")[1].split(marker)[0]
        elif marker in text:
            text = text.split(marker)[1].split(marker)[0]
            
        return json.loads(text.strip())
    except Exception as e:
        return {"error": str(e)}