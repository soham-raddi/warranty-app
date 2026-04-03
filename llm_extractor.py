from google import genai
import json
import os
from dotenv import load_dotenv
import PIL.Image

load_dotenv()
# Put your actual API key here!
# This grabs the key securely
API_KEY = os.getenv("GEMINI_API_KEY") 

# Initialize the new Client
client = genai.Client(api_key=API_KEY)

def parse_receipt_image_to_json(image_path):
    img = PIL.Image.open(image_path)
    prompt = """
    You are an AI assistant analyzing an image.
    
    Step 1: Determine if the image is a receipt, bill, or invoice. 
    Step 2: If it is clearly NOT a receipt (e.g., a landscape, person, animal, random object), abort and return exactly this JSON and nothing else:
    {"error": "NOT_A_RECEIPT"}
    
    Step 3: If it IS a receipt, extract the following info. Return ONLY valid JSON. Use null if missing.
    Keys:
    - "item_name": Main product
    - "brand": Brand
    - "model_number": Model
    - "serial_number": S/N or IMEI
    - "category": e.g., Electronics, Kitchen
    - "date_of_purchase": YYYY-MM-DD
    - "price": Base price
    - "tax_amount": Tax
    - "total_amount": Final paid
    - "payment_method": Cash/Card/UPI
    - "store_name": Retailer name
    - "store_contact": Phone/Web
    - "warranty_info": Warranty text
    - "return_policy": Return/Exchange window text
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, img]
        )
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        return {"error": str(e)}