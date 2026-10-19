import os
import json
import base64
import io
from datetime import datetime

import fitz
from PIL import Image
from groq import Groq

# .env is loaded once by app.py; this module just reads the environment.
_client = None
MISSING_KEY_MESSAGE = "GROQ_API_KEY is not set. Add it to your .env file to enable receipt scanning and the assistant."


def get_client():
    """Creates the Groq client on first use so the app can start without an API key."""
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(MISSING_KEY_MESSAGE)
        _client = Groq(api_key=api_key)
    return _client

# Groq retires models regularly, so both are overridable from .env.
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")

RECEIPT_FIELDS = [
    "item_name", "brand", "model_number", "serial_number", "category", "date_of_purchase",
    "price", "tax_amount", "total_amount", "payment_method", "store_name", "store_contact",
    "invoice_number", "warranty_info", "return_policy",
]

# Placeholder answers models give for missing data; stored as empty instead.
MISSING_VALUE_MARKERS = {
    "", "n/a", "na", "none", "null", "unknown", "not found", "not available",
    "not mentioned", "not specified", "not specified on receipt", "-",
}


def encode_image(data, filename):
    """Converts image or MULTI-PAGE PDF bytes to a list of Base64 strings, compressing large images."""
    encoded_images = []

    if filename.lower().endswith('.pdf'):
        doc = fitz.open(stream=data, filetype='pdf')
        # Process up to the first 3 pages
        for i in range(min(len(doc), 3)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_bytes = pix.tobytes("png")
            encoded_images.append((base64.b64encode(img_bytes).decode('utf-8'), "image/png"))
        doc.close()
    else:
        with Image.open(io.BytesIO(data)) as img:  #compression logic for large images
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


def _clean_receipt_data(raw):
    """Keeps only known fields, blanks placeholder values and drops dates that are not YYYY-MM-DD."""
    data = {}
    for field in RECEIPT_FIELDS:
        value = raw.get(field)
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        value = str(value).strip() if value is not None else ""
        data[field] = None if value.lower() in MISSING_VALUE_MARKERS else value

    if data["date_of_purchase"]:
        try:
            datetime.strptime(data["date_of_purchase"], "%Y-%m-%d")
        except ValueError:
            data["date_of_purchase"] = None

    # Keep a warranty length only if it is a sensible whole number of months (1 month to 30 years).
    try:
        months = int(float(raw.get("warranty_months")))
        data["warranty_months"] = months if 1 <= months <= 360 else None
    except (TypeError, ValueError):
        data["warranty_months"] = None
    return data


def parse_receipt_image_to_json(data, filename):
    try:
        image_list = encode_image(data, filename)
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
        "invoice_number, warranty_info, return_policy, "
        "warranty_months (the warranty length as a whole number of months, e.g. 12 for '1 year'; null if not stated).\n"
        "Use null for any field that is not present on the document; never guess or write placeholders like 'not found'.\n"
        "Return ONLY valid JSON. Do not add any conversational text or Markdown formatting."
    )

    content_payload = [{"type": "text", "text": prompt}]
    for base64_image, mime_type in image_list:
        content_payload.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
        })

    try:
        chat_completion = get_client().chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": content_payload}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        text = chat_completion.choices[0].message.content
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        print(f"Groq extraction error: {e}")
        return {"error": "The AI service could not process this document. Check your Groq API key and model settings, then try again."}

    marker = chr(96) * 3
    if f"{marker}json" in text:
        text = text.split(f"{marker}json")[1].split(marker)[0]
    elif marker in text:
        text = text.split(marker)[1].split(marker)[0]

    try:
        raw = json.loads(text.strip())
    except json.JSONDecodeError:
        return {"error": "The AI returned an unreadable response. Please try uploading the document again."}

    if not isinstance(raw, dict):
        return {"error": "The AI returned an unexpected response. Please try uploading the document again."}
    if raw.get("error"):
        if raw["error"] == "NOT_A_RECEIPT":
            return {"error": "This doesn't look like a receipt or invoice. Please upload a purchase receipt."}
        return {"error": str(raw["error"])}

    data = _clean_receipt_data(raw)
    if not data["item_name"]:
        return {"error": "Couldn't identify the purchased item on this document."}
    return data
