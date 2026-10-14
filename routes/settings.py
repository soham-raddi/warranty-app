import os
import smtplib
from email.message import EmailMessage

from flask import Blueprint, jsonify, request

from database import CURRENCY_SYMBOLS, get_all_settings, get_inventory_analytics, set_setting_value
from .auth import role_required

settings_bp = Blueprint('settings', __name__)


def _pick_option(payload, key, default_value, allowed_values):
    value = str(payload.get(key, default_value)).strip() or default_value
    if value not in allowed_values:
        return None
    return value


def _normalize_toggle(payload, key, default_value='0'):
    value = str(payload.get(key, default_value)).strip().lower()
    return '1' if value in {'1', 'true', 'yes', 'on'} else '0'


def _normalize_text(payload, key, default_value=''):
    return str(payload.get(key, default_value)).strip()


DEFAULT_REMINDER_BODY = 'Your warranty for {{item_name}} expires on {{expiry_date}}. Days left: {{days_remaining}}.'


def _render_reminder(template, item):
    """Fills the reminder template placeholders for one item."""
    values = {
        'item_name': item.get('item_name') or 'Item',
        'brand': item.get('brand') or '',
        'expiry_date': item.get('warranty_expires_on') or 'unknown',
        'days_remaining': item.get('days_remaining'),
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace('{{' + key + '}}', str(value))
    return f"- {rendered}"


def _parse_recipients(raw_value):
    values = str(raw_value or '').replace(';', ',').replace('\n', ',').split(',')
    return [value.strip() for value in values if value.strip()]


@settings_bp.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify({'settings': get_all_settings()})


@settings_bp.route('/api/settings', methods=['PUT'])
@role_required('owner', 'admin')
def update_settings():
    payload = request.json or {}

    timezone = str(payload.get('timezone', 'UTC')).strip() or 'UTC'
    currency_code = str(payload.get('currency_code', 'INR')).strip().upper() or 'INR'
    theme_mode = _pick_option(payload, 'theme_mode', 'system', {'system', 'light', 'dark'})
    accent_color = _pick_option(payload, 'accent_color', 'blue', {'blue', 'teal', 'amber', 'rose', 'slate'})
    date_format = _pick_option(payload, 'date_format', 'auto', {'auto', 'iso', 'dmy', 'mdy', 'long'})
    time_format = _pick_option(payload, 'time_format', '12h', {'12h', '24h'})
    density = _pick_option(payload, 'density', 'comfortable', {'comfortable', 'compact'})
    reduced_motion = _normalize_toggle(payload, 'reduced_motion', '0')
    reminders_enabled = _normalize_toggle(payload, 'reminders_enabled', '0')

    try:
        expiry_alert_days = max(0, int(payload.get('expiry_alert_days', 30)))
    except (TypeError, ValueError):
        return jsonify({'error': 'expiry_alert_days must be a number'}), 400

    try:
        reminder_days_before = max(0, int(payload.get('reminder_days_before', 30)))
    except (TypeError, ValueError):
        return jsonify({'error': 'reminder_days_before must be a number'}), 400

    reminder_recipients = _normalize_text(payload, 'reminder_recipients', '')
    reminder_subject = _normalize_text(payload, 'reminder_subject', 'Warranty expiry reminder') or 'Warranty expiry reminder'
    reminder_body = _normalize_text(
        payload,
        'reminder_body',
        'Your warranty for {{item_name}} expires on {{expiry_date}}. Days left: {{days_remaining}}.'
    ) or 'Your warranty for {{item_name}} expires on {{expiry_date}}. Days left: {{days_remaining}}.'
    smtp_host = _normalize_text(payload, 'smtp_host', '')
    smtp_port = str(payload.get('smtp_port', '587')).strip() or '587'
    smtp_username = _normalize_text(payload, 'smtp_username', '')
    smtp_sender_email = _normalize_text(payload, 'smtp_sender_email', '')

    if currency_code not in CURRENCY_SYMBOLS:
        return jsonify({'error': 'currency_code must be a supported currency'}), 400
    currency_symbol = CURRENCY_SYMBOLS[currency_code]

    if not theme_mode:
        return jsonify({'error': 'theme_mode must be system, light, or dark'}), 400
    if not accent_color:
        return jsonify({'error': 'accent_color must be blue, teal, amber, rose, or slate'}), 400
    if not date_format:
        return jsonify({'error': 'date_format must be auto, iso, dmy, mdy, or long'}), 400
    if not time_format:
        return jsonify({'error': 'time_format must be 12h or 24h'}), 400
    if not density:
        return jsonify({'error': 'density must be comfortable or compact'}), 400
    if expiry_alert_days > 3650:
        return jsonify({'error': 'expiry_alert_days must be between 0 and 3650'}), 400
    if reminder_days_before > 3650:
        return jsonify({'error': 'reminder_days_before must be between 0 and 3650'}), 400

    try:
        warranty_period_days = int(payload.get('warranty_period_days', 365))
    except (TypeError, ValueError):
        return jsonify({'error': 'warranty_period_days must be a number'}), 400

    if warranty_period_days < 1 or warranty_period_days > 3650:
        return jsonify({'error': 'warranty_period_days must be between 1 and 3650'}), 400

    set_setting_value('timezone', timezone)
    set_setting_value('currency_code', currency_code)
    set_setting_value('currency_symbol', currency_symbol)
    set_setting_value('warranty_period_days', warranty_period_days)
    set_setting_value('theme_mode', theme_mode)
    set_setting_value('accent_color', accent_color)
    set_setting_value('date_format', date_format)
    set_setting_value('time_format', time_format)
    set_setting_value('reduced_motion', reduced_motion)
    set_setting_value('density', density)
    set_setting_value('expiry_alert_days', expiry_alert_days)
    set_setting_value('reminders_enabled', reminders_enabled)
    set_setting_value('reminder_days_before', reminder_days_before)
    set_setting_value('reminder_recipients', reminder_recipients)
    set_setting_value('reminder_subject', reminder_subject)
    set_setting_value('reminder_body', reminder_body)
    set_setting_value('smtp_host', smtp_host)
    set_setting_value('smtp_port', smtp_port)
    set_setting_value('smtp_username', smtp_username)
    set_setting_value('smtp_sender_email', smtp_sender_email)

    return jsonify({'message': 'Settings updated successfully', 'settings': get_all_settings()})


@settings_bp.route('/api/reminders/send', methods=['POST'])
@role_required('owner', 'admin')
def send_reminders_now():
    settings = get_all_settings()
    items, _, _, _ = get_inventory_analytics()

    try:
        reminder_days_before = max(0, int(settings.get('reminder_days_before', '30')))
    except (TypeError, ValueError):
        reminder_days_before = 30

    # Remind only about warranties that are still active and end within the reminder window.
    due_items = [
        item for item in items
        if isinstance(item.get('days_remaining'), int) and 0 <= item['days_remaining'] <= reminder_days_before
    ]
    if not due_items:
        return jsonify({'message': f'No warranties expire in the next {reminder_days_before} days', 'sent': 0, 'alerts': []})

    recipients = _parse_recipients(settings.get('reminder_recipients', ''))
    if not recipients:
        return jsonify({'error': 'No reminder recipients configured'}), 400

    try:
        smtp_port = int(str(settings.get('smtp_port', '587')).strip() or '587')
    except ValueError:
        return jsonify({'error': 'SMTP port must be a number'}), 400

    smtp_host = str(settings.get('smtp_host', '')).strip()
    smtp_username = str(settings.get('smtp_username', '')).strip()
    smtp_sender_email = str(settings.get('smtp_sender_email', '')).strip() or smtp_username
    smtp_password = os.environ.get('WARRANTY_SMTP_PASSWORD') or os.environ.get('SMTP_PASSWORD')

    if not smtp_host or not smtp_sender_email:
        return jsonify({'error': 'SMTP host and sender email must be configured'}), 400
    if not smtp_password:
        return jsonify({'error': 'SMTP password must be provided via WARRANTY_SMTP_PASSWORD or SMTP_PASSWORD'}), 400

    subject = str(settings.get('reminder_subject', 'Warranty expiry reminder')).strip() or 'Warranty expiry reminder'
    body_template = str(settings.get('reminder_body', '')).strip() or DEFAULT_REMINDER_BODY

    lines = [
        _render_reminder(body_template, item)
        for item in sorted(due_items, key=lambda i: i['days_remaining'])
    ]
    email_body = ['Warranty expiry reminders', '', *lines]

    msg = EmailMessage()
    msg['From'] = smtp_sender_email
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject
    msg.set_content('\n'.join(email_body))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            if smtp_username:
                server.login(smtp_username, smtp_password)
            else:
                server.login(smtp_sender_email, smtp_password)
            server.send_message(msg)
    except Exception as exc:
        print(f"Reminder email error: {exc}")
        return jsonify({'error': 'Failed to send reminder email. Check the SMTP settings and password.'}), 500

    alerts = [{'id': i.get('id'), 'item_name': i.get('item_name'), 'expiry_date': i.get('warranty_expires_on'),
               'days_remaining': i.get('days_remaining')} for i in due_items]
    return jsonify({'message': 'Reminder email sent successfully', 'sent': len(recipients), 'alerts': alerts})
