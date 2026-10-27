"""Sending email through Resend (https://resend.com), configured once for the whole site.

Set RESEND_API_KEY and EMAIL_FROM in the hosting environment. Without a verified domain in Resend, the
test sender (onboarding@resend.dev) can only deliver to the email address of your own Resend account.
"""
import html
import os

import requests

RESEND_API_URL = 'https://api.resend.com/emails'
RESEND_BATCH_URL = 'https://api.resend.com/emails/batch'
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '').strip()
EMAIL_FROM = os.getenv('EMAIL_FROM', '').strip() or 'Digital Twin <onboarding@resend.dev>'
EMAIL_ENABLED = bool(RESEND_API_KEY)
TIMEOUT_SECONDS = 15
BRAND_BLUE = '#1d4ed8'


class EmailError(Exception):
    pass


def _post(url, payload, idempotency_key=None):
    if not EMAIL_ENABLED:
        raise EmailError('Email is not set up on this site.')
    headers = {'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'}
    if idempotency_key:
        # Resend ignores a repeated key for 24 hours, so a retried request never sends twice.
        headers['Idempotency-Key'] = idempotency_key[:256]
    try:
        response = requests.post(url, timeout=TIMEOUT_SECONDS, headers=headers, json=payload)
    except requests.RequestException as error:
        raise EmailError('Could not reach the email service.') from error
    if response.status_code >= 300:
        print(f'Resend error {response.status_code}: {response.text[:300]}')
        if response.status_code == 403:
            raise EmailError('The email service refused this address. A verified sending domain is needed to email '
                             'people other than the site owner.')
        raise EmailError('The email could not be sent. Please try again later.')
    return response.json()


def send_email(to, subject, html_body, text_body, idempotency_key=None):
    """Sends one email. `to` may be a string or a list (Resend allows up to 50). Raises EmailError on failure."""
    recipients = [to] if isinstance(to, str) else list(to)
    return _post(RESEND_API_URL, {
        'from': EMAIL_FROM, 'to': recipients[:50], 'subject': subject, 'html': html_body, 'text': text_body,
    }, idempotency_key).get('id')


def send_batch(messages, idempotency_key=None):
    """Sends up to 100 separate emails in one request. Each message: to, subject, html, text, optional headers."""
    payload = [{'from': EMAIL_FROM, **message} for message in messages[:100]]
    return _post(RESEND_BATCH_URL, payload, idempotency_key)


def _layout(title, body_html):
    """Wraps content in a simple, mobile-friendly email layout (inline styles only, as email clients need)."""
    return f'''<!doctype html>
<html><body style="margin:0;background:#eef2f8;font-family:Segoe UI,Arial,sans-serif;color:#0f172a;">
  <div style="max-width:560px;margin:0 auto;padding:24px 16px;">
    <div style="background:#ffffff;border-radius:16px;padding:28px;box-shadow:0 8px 24px rgba(15,23,42,.08);">
      <div style="font-weight:800;font-size:18px;color:{BRAND_BLUE};margin-bottom:16px;">Digital Twin</div>
      <h1 style="font-size:20px;margin:0 0 12px;">{html.escape(title)}</h1>
      {body_html}
    </div>
    <p style="font-size:12px;color:#64748b;text-align:center;margin-top:16px;">
      Digital Twin &middot; your family's receipts and warranties in one private place
    </p>
  </div>
</body></html>'''


def _button(url, label):
    return (f'<p style="margin:24px 0;"><a href="{html.escape(url)}" style="background:{BRAND_BLUE};color:#ffffff;'
            f'padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:600;display:inline-block;">'
            f'{html.escape(label)}</a></p>')


def invite_email(inviter_name, workspace_name, link, expires_on):
    """Returns (subject, html, text) for a workspace invite."""
    subject = f'{inviter_name} invited you to {workspace_name} on Digital Twin'
    body = (f'<p><strong>{html.escape(inviter_name)}</strong> invited you to join '
            f'<strong>{html.escape(workspace_name)}</strong>, a shared place for your family\'s receipts and warranties.</p>'
            + _button(link, f'Join {workspace_name}') +
            f'<p style="font-size:13px;color:#64748b;">Sign in with any Google account. This link works once and expires '
            f'on {html.escape(expires_on)}. If you weren\'t expecting it, you can ignore this email.</p>')
    text = (f'{inviter_name} invited you to join {workspace_name} on Digital Twin.\n\n'
            f'Join here: {link}\n\nThe link works once and expires on {expires_on}.')
    return subject, _layout(f'Join {workspace_name}', body), text


def reminder_email(workspace_name, items, app_url, unsubscribe_url):
    """Returns (subject, html, text) listing warranties that are about to expire. items: dicts with
    item_name, brand, warranty_expires_on, days_remaining."""
    count = len(items)
    subject = (f'{items[0]["item_name"][:60]}: warranty ends soon' if count == 1
               else f'{count} warranties in {workspace_name} end soon')
    rows, lines = [], []
    for item in items:
        days = item['days_remaining']
        when = 'today' if days == 0 else ('tomorrow' if days == 1 else f'in {days} days')
        name = item.get('item_name') or 'Item'
        brand = f" ({item['brand']})" if item.get('brand') else ''
        rows.append(f'<tr><td style="padding:10px 0;border-bottom:1px solid #e2e8f0;">'
                    f'<strong>{html.escape(name)}</strong>{html.escape(brand)}<br>'
                    f'<span style="color:#64748b;font-size:13px;">Warranty ends {html.escape(when)} '
                    f'({html.escape(item.get("warranty_expires_on") or "")})</span></td></tr>')
        lines.append(f'- {name}{brand}: warranty ends {when} ({item.get("warranty_expires_on")})')
    body = (f'<p>These warranties in <strong>{html.escape(workspace_name)}</strong> are about to expire. '
            f'If something isn\'t working right, now is the time to claim.</p>'
            f'<table style="width:100%;border-collapse:collapse;">{"".join(rows)}</table>'
            + _button(app_url, 'Open Digital Twin') +
            '<p style="font-size:13px;color:#64748b;">You get this because warranty reminders are turned on for '
            f'{html.escape(workspace_name)}. <a href="{html.escape(unsubscribe_url)}" style="color:#64748b;">'
            'Unsubscribe from these reminders</a>.</p>')
    text = (f'These warranties in {workspace_name} are about to expire:\n\n' + '\n'.join(lines) +
            f'\n\nOpen Digital Twin: {app_url}\n\nUnsubscribe from these reminders: {unsubscribe_url}')
    return subject, _layout('Warranties ending soon', body), text
