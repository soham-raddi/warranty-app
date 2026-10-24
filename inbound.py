"""Purchases from forwarded order emails, received through Resend (https://resend.com/docs/dashboard/receiving).

Each workspace gets a private address like receipts-<token>@<INBOUND_EMAIL_DOMAIN>. Resend calls our webhook when
mail arrives; we fetch the full email, read the purchase with the AI and keep it as a pending receipt until someone
in the workspace reviews and saves it. Only email sent or forwarded by a member of the workspace is accepted.
"""
import base64
import email
import email.policy
import hashlib
import hmac
import io
import os
import re
import secrets
import time
import uuid
from email.utils import parseaddr
from html.parser import HTMLParser

import fitz
import requests

import emails
import storage
from database import (
    add_pending_receipt,
    find_duplicate_receipt,
    get_all_settings,
    pending_receipt_exists,
    set_setting_value,
    workspace_by_inbound_token,
    workspace_member_emails,
)
from llm_extractor import parse_order_email, parse_receipt_image_to_json

INBOUND_DOMAIN = os.getenv('INBOUND_EMAIL_DOMAIN', '').strip().lstrip('@').lower()
WEBHOOK_SECRET = os.getenv('RESEND_WEBHOOK_SECRET', '').strip()
RECEIVED_EMAIL_URL = 'https://api.resend.com/emails/receiving/{}'
ADDRESS_PATTERN = re.compile(r'receipts-([a-z0-9]{10,40})@', re.IGNORECASE)
INVOICE_TYPES = {'application/pdf': 'pdf', 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}
GMAIL_FORWARDING_SENDER = 'forwarding-noreply@google.com'
SIGNATURE_TOLERANCE_SECONDS = 5 * 60
MAX_INVOICE_BYTES = 10 * 1024 * 1024


def is_enabled():
    return bool(INBOUND_DOMAIN and WEBHOOK_SECRET and emails.EMAIL_ENABLED)


def address_for(workspace_id):
    """The workspace's forwarding address, created on first use."""
    token = get_all_settings(workspace_id).get('inbound_token')
    if not token:
        token = new_address_token(workspace_id)
    return f'receipts-{token}@{INBOUND_DOMAIN}'


def new_address_token(workspace_id):
    """Replaces the workspace's address (the old one stops working) and returns the new token."""
    token = secrets.token_hex(8)
    set_setting_value(workspace_id, 'inbound_token', token)
    return token


# ---------------------------------------------------------------------------
# Webhook signature (Resend signs webhooks the Svix way)
# ---------------------------------------------------------------------------

def signature_is_valid(headers, body):
    msg_id, timestamp, signatures = (headers.get('svix-id'), headers.get('svix-timestamp'),
                                     headers.get('svix-signature'))
    if not (WEBHOOK_SECRET and msg_id and timestamp and signatures):
        return False
    try:
        if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
            return False  # old requests can't be replayed
        key = base64.b64decode(WEBHOOK_SECRET.split('_', 1)[1] if WEBHOOK_SECRET.startswith('whsec_') else WEBHOOK_SECRET)
    except (ValueError, TypeError):
        return False
    signed = f'{msg_id}.{timestamp}.'.encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, part.split(',', 1)[-1]) for part in signatures.split())


# ---------------------------------------------------------------------------
# Reading the email
# ---------------------------------------------------------------------------

class _TextFromHtml(HTMLParser):
    BLOCKS = {'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'table', 'section'}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('style', 'script', 'head'):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append('\n')
        elif tag == 'td':
            self.parts.append(' | ')

    def handle_endtag(self, tag):
        if tag in ('style', 'script', 'head'):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html_body):
    parser = _TextFromHtml()
    parser.feed(html_body or '')
    text = ''.join(parser.parts)
    lines = [re.sub(r'[ \t ]+', ' ', line).strip(' |') for line in text.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def fetch_received_email(email_id):
    """Full email from Resend: subject, from, date, text and the first PDF or image invoice attached, if any."""
    response = requests.get(RECEIVED_EMAIL_URL.format(email_id), timeout=20,
                            headers={'Authorization': f'Bearer {emails.RESEND_API_KEY}'})
    response.raise_for_status()
    data = response.json()
    result = {
        'subject': data.get('subject') or '',
        'from': data.get('from') or '',
        'date': data.get('created_at') or '',
        'text': data.get('text') or html_to_text(data.get('html')),
        'invoice': None,
    }
    raw_url = (data.get('raw') or {}).get('download_url')
    if raw_url and any((a.get('content_type') or '').lower() in INVOICE_TYPES for a in data.get('attachments') or []):
        raw = requests.get(raw_url, timeout=20)
        if raw.ok:
            result['invoice'] = _first_invoice(raw.content)
    return result


def _first_invoice(raw_bytes):
    message = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    for part in message.iter_attachments():
        content_type = part.get_content_type().lower()
        if content_type in INVOICE_TYPES:
            data = part.get_payload(decode=True) or b''
            if 0 < len(data) <= MAX_INVOICE_BYTES:
                name = part.get_filename() or f'invoice.{INVOICE_TYPES[content_type]}'
                return {'data': data, 'filename': name, 'content_type': content_type}
    return None


def email_as_pdf(subject, sender, date, text):
    """A simple PDF copy of the email, kept as the purchase's receipt."""
    doc = fitz.open()
    # The built-in PDF font has no rupee sign.
    text = text.replace('₹', 'Rs. ')
    lines = [f'Subject: {subject}', f'From: {sender}', f'Date: {date}', ''] + text.splitlines()
    wrapped = []
    for line in lines:
        while len(line) > 95:
            cut = line.rfind(' ', 0, 95)
            cut = cut if cut > 40 else 95
            wrapped.append(line[:cut])
            line = line[cut:].lstrip()
        wrapped.append(line)
    for start in range(0, max(len(wrapped), 1), 60):
        page = doc.new_page()
        page.insert_text((40, 50), '\n'.join(wrapped[start:start + 60]), fontsize=9, fontname='helv')
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Handling a received email
# ---------------------------------------------------------------------------

def _workspace_for(recipients):
    for recipient in recipients:
        address = parseaddr(recipient or '')[1].lower()  # also handles "Name <receipts-...@...>"
        match = ADDRESS_PATTERN.match(address)
        if match and address.endswith('@' + INBOUND_DOMAIN):
            workspace_id = workspace_by_inbound_token(match.group(1).lower())
            if workspace_id:
                return workspace_id
    return None


def handle_received(event):
    """Processes one email.received webhook event. Returns a short status string (for logs and tests)."""
    data = event.get('data') or {}
    email_id = data.get('email_id')
    workspace_id = _workspace_for(data.get('to') or [])
    if not email_id or not workspace_id:
        return 'ignored: unknown address'
    if pending_receipt_exists(email_id):
        return 'ignored: already imported'  # Resend retried a delivery we already handled

    sender = parseaddr(data.get('from') or '')[1].lower()
    if sender == GMAIL_FORWARDING_SENDER:
        # Gmail asks to confirm a new forwarding address by email; keep it so the family can see the link.
        details = fetch_received_email(email_id)
        set_setting_value(workspace_id, 'inbound_gmail_confirmation', details['text'][:1500])
        return 'stored: gmail confirmation'

    # Manually forwarded mail comes from the member; auto-forwarded mail keeps the shop as sender and lists the
    # member it was forwarded for.
    members = {e.lower() for e in workspace_member_emails(workspace_id)}
    forwarded_for = {parseaddr(a)[1].lower() for a in data.get('received_for') or []}
    if sender not in members and not (forwarded_for & members):
        return 'ignored: not from a member'

    details = fetch_received_email(email_id)
    invoice = details['invoice']
    result = parse_receipt_image_to_json(invoice['data'], invoice['filename']) if invoice else {'error': 'none'}
    if result.get('error'):
        result = parse_order_email(details['subject'], details['from'], details['date'], details['text'])

    if invoice:
        file_data, filename, content_type = invoice['data'], invoice['filename'], invoice['content_type']
    else:
        file_data = email_as_pdf(details['subject'], details['from'], details['date'], details['text'])
        filename, content_type = 'order-email.pdf', 'application/pdf'
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'pdf'
    key = f'{workspace_id}/emails/{uuid.uuid4().hex[:12]}_email.{extension}'
    file_path = storage.save(file_data, key, content_type)

    if result.get('error'):
        # Keep the email anyway so nothing is lost; the family can fill in the details themselves.
        pending = {'receipt': {}, 'items': [{'item_name': details['subject'][:200] or 'Purchase'}],
                   'note': f"{result['error']} Check the details before saving."}
    else:
        pending = result
        for item in pending['items']:
            item['duplicate_item_id'] = find_duplicate_receipt(
                workspace_id, pending['receipt'].get('invoice_number'), pending['receipt'].get('store_name'),
                item.get('item_name'))
    if add_pending_receipt(workspace_id, email_id, sender, details['subject'], pending, file_path) is None:
        storage.delete(file_path)
        return 'ignored: already imported'
    return 'stored'
