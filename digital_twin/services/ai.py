import base64
import io
import json
import os
import re
from datetime import datetime

import fitz
from groq import Groq
from PIL import Image

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

# Details shared by everything on one receipt, and details of each purchased product.
RECEIPT_FIELDS = [
    "date_of_purchase", "store_name", "store_contact", "invoice_number", "payment_method",
    "total_amount", "tax_amount", "return_policy", "currency",
]
ITEM_FIELDS = ["item_name", "brand", "model_number", "serial_number", "category", "amount", "warranty_info"]
MAX_ITEMS = 20

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


def _clean_text(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    value = str(value).strip() if value is not None else ""
    return None if value.lower() in MISSING_VALUE_MARKERS else value


def _clean_months(value):
    """A warranty length only if it is a sensible whole number of months (1 month to 30 years)."""
    try:
        months = int(float(value))
    except (TypeError, ValueError):
        return None
    return months if 1 <= months <= 360 else None


def _clean_date(value):
    value = _clean_text(value)
    try:
        datetime.strptime(value or "", "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _clean_receipt_data(raw):
    """Keeps only known fields, blanks placeholder values and drops dates that are not YYYY-MM-DD."""
    receipt = {field: _clean_text(raw.get(field)) for field in RECEIPT_FIELDS}
    receipt["date_of_purchase"] = _clean_date(raw.get("date_of_purchase"))
    currency = str(raw.get("currency") or "").strip().upper()
    receipt["currency"] = currency if re.fullmatch(r"[A-Z]{3}", currency) else None

    items = []
    raw_items = raw.get("items") if isinstance(raw.get("items"), list) else [raw]
    for raw_item in raw_items[:MAX_ITEMS]:
        if not isinstance(raw_item, dict):
            continue
        item = {field: _clean_text(raw_item.get(field)) for field in ITEM_FIELDS}
        item["warranty_months"] = _clean_months(raw_item.get("warranty_months"))
        if item["item_name"]:
            items.append(item)
    # With a single product, its amount is what was paid for the whole receipt.
    if len(items) == 1 and receipt["total_amount"]:
        items[0]["amount"] = receipt["total_amount"]
    return {"receipt": receipt, "items": items}


def _ask_vision_model(prompt, data, filename):
    """Sends document pages with a prompt and returns the model's JSON object, or {"error": ...}."""
    try:
        image_list = encode_image(data, filename)
    except Exception as e:
        print(f"Could not open uploaded file: {e}")
        return {"error": "This file couldn't be opened. It may be damaged; try another photo or PDF."}

    content_payload = [{"type": "text", "text": prompt}]
    for base64_image, mime_type in image_list:
        content_payload.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}})

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
    return raw


RECEIPT_RULES = (
    "Extract the receipt details: date_of_purchase (YYYY-MM-DD), store_name (the overall retailer or "
    "marketplace name), store_contact, invoice_number (or order number), payment_method, total_amount (the grand "
    "total of the entire order), tax_amount, return_policy, currency (the ISO 4217 code of the amounts, e.g. INR for "
    "the rupee sign or Rs, MYR for RM, USD, EUR, GBP, SGD; use the store's country and address to tell apart symbols "
    "like $; null if unclear).\n"
    "List every purchased PRODUCT in \"items\", most expensive first. Strict rule: do NOT list "
    "supplementary charges (shipping, handling, platform fees, bags, tips, discounts) as items. For each item: "
    "item_name, brand, model_number, serial_number, category (broad, like 'Electronic Gadgets', "
    "'Kitchen Appliances', 'Apparel', 'Automotive'), amount (that line's total as printed, including its tax if "
    "shown per line), warranty_info, warranty_months (the warranty length as a whole number of months, e.g. 12 "
    "for '1 year'; null if not stated).\n"
    "Use null for any field that is not present; never guess or write placeholders like 'not found'.\n"
    "Return ONLY valid JSON shaped like {\"date_of_purchase\": ..., ..., \"items\": [{\"item_name\": ..., ...}]}."
)
RECEIPT_PROMPT = (
    "Analyze these document pages carefully. They belong to a single invoice or receipt.\n"
    "If the document is NOT a receipt, bill, or invoice, return: {\"error\": \"NOT_A_RECEIPT\"}\n"
    + RECEIPT_RULES
)
ORDER_EMAIL_PROMPT = (
    "Below is an email, usually an order confirmation, invoice or shipping notice from a shop, sometimes "
    "forwarded by the buyer. Ignore forwarding headers and the buyer's own notes.\n"
    "If it is NOT about a purchase someone made (for example a newsletter, advert or account notice), return: "
    "{\"error\": \"NOT_A_RECEIPT\"}\n"
    "If the email is from a marketplace, use the marketplace as store_name. If only an order date is given, use it "
    "as date_of_purchase; if no date is given, use the date the email was sent.\n"
    + RECEIPT_RULES
)
MAX_EMAIL_CHARS = 15000


def parse_order_email(subject, sender, sent_at, text):
    """Reads a purchase from an order email's text. Returns {"receipt", "items"} or {"error": ...}."""
    body = (f"Subject: {subject}\nFrom: {sender}\nSent: {sent_at}\n\n{text}")[:MAX_EMAIL_CHARS]
    try:
        chat_completion = get_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "system", "content": ORDER_EMAIL_PROMPT}, {"role": "user", "content": body}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = json.loads(chat_completion.choices[0].message.content.strip())
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        print(f"Groq order email error: {e}")
        return {"error": "The AI service could not read this email."}
    if not isinstance(raw, dict):
        return {"error": "The AI returned an unexpected response."}
    if raw.get("error"):
        return {"error": "This email doesn't look like a purchase." if raw["error"] == "NOT_A_RECEIPT" else str(raw["error"])}
    result = _clean_receipt_data(raw)
    if not result["items"]:
        return {"error": "Couldn't find a purchased item in this email."}
    return result


def parse_receipt_image_to_json(data, filename):
    """Reads a receipt. Returns {"receipt": shared details, "items": [products]} or {"error": ...}."""
    prompt = RECEIPT_PROMPT
    raw = _ask_vision_model(prompt, data, filename)
    if raw.get("error"):
        if raw["error"] == "NOT_A_RECEIPT":
            return {"error": "This doesn't look like a receipt or invoice. Please upload a purchase receipt."}
        return {"error": str(raw["error"])}

    result = _clean_receipt_data(raw)
    if not result["items"]:
        return {"error": "Couldn't identify the purchased item on this document."}
    return result


def _clean_card_data(raw):
    """Keeps a sensible warranty length, a valid start date and short notes from a warranty card reading."""
    return {"warranty_months": _clean_months(raw.get("warranty_months")),
            "warranty_start_date": _clean_date(raw.get("warranty_start_date")),
            "coverage_notes": _clean_text(raw.get("coverage_notes"))}


def parse_warranty_card(data, filename):
    """Reads a warranty card or certificate. Returns warranty_months, warranty_start_date and coverage_notes
    (each may be None), or {"error": ...} when the file can't be read."""
    prompt = (
        "This is a product warranty card, warranty certificate or guarantee document.\n"
        "Extract:\n"
        "- warranty_months: the total standard warranty length for the main product as a whole number of months "
        "(e.g. 24 for '2 years'). If parts differ (e.g. '1 year on product, 10 years on motor'), use the main "
        "product's warranty, not the longest part.\n"
        "- warranty_start_date: the date the warranty starts or the purchase/installation date written on the card, "
        "as YYYY-MM-DD; null if not written.\n"
        "- coverage_notes: one short sentence summarising what is covered, including any longer coverage on specific "
        "parts (e.g. '2 years comprehensive, 10 years on compressor'); null if nothing useful.\n"
        "Use null for anything not on the document; never guess.\n"
        "Return ONLY valid JSON with exactly these keys."
    )
    raw = _ask_vision_model(prompt, data, filename)
    if raw.get("error"):
        return {"error": "The AI service could not read this warranty card."}
    return _clean_card_data(raw)
