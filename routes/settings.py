from flask import Blueprint, g, jsonify, request

import emails
import reminders
from database import CURRENCY_SYMBOLS, get_all_settings, set_setting_value
from .auth import role_required
from .workspaces import EMAIL_PATTERN

settings_bp = Blueprint('settings', __name__)


def _pick_option(payload, key, default_value, allowed_values):
    value = str(payload.get(key, default_value)).strip() or default_value
    if value not in allowed_values:
        return None
    return value


def _normalize_toggle(payload, key, default_value='0'):
    value = str(payload.get(key, default_value)).strip().lower()
    return '1' if value in {'1', 'true', 'yes', 'on'} else '0'


@settings_bp.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify({'settings': get_all_settings(g.workspace_id)})


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

    recipients = reminders.parse_recipients(payload.get('reminder_recipients', ''))
    bad = [r for r in recipients if not EMAIL_PATTERN.match(r)]
    if bad:
        return jsonify({'error': f'"{bad[0]}" is not a valid email address'}), 400
    if len(recipients) > reminders.MAX_RECIPIENTS:
        return jsonify({'error': f'Add at most {reminders.MAX_RECIPIENTS} reminder recipients'}), 400
    reminder_recipients = ', '.join(recipients)

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

    set_setting_value(g.workspace_id, 'timezone', timezone)
    set_setting_value(g.workspace_id, 'currency_code', currency_code)
    set_setting_value(g.workspace_id, 'currency_symbol', currency_symbol)
    set_setting_value(g.workspace_id, 'warranty_period_days', warranty_period_days)
    set_setting_value(g.workspace_id, 'theme_mode', theme_mode)
    set_setting_value(g.workspace_id, 'accent_color', accent_color)
    set_setting_value(g.workspace_id, 'date_format', date_format)
    set_setting_value(g.workspace_id, 'time_format', time_format)
    set_setting_value(g.workspace_id, 'reduced_motion', reduced_motion)
    set_setting_value(g.workspace_id, 'density', density)
    set_setting_value(g.workspace_id, 'expiry_alert_days', expiry_alert_days)
    set_setting_value(g.workspace_id, 'reminders_enabled', reminders_enabled)
    set_setting_value(g.workspace_id, 'reminder_days_before', reminder_days_before)
    set_setting_value(g.workspace_id, 'reminder_recipients', reminder_recipients)

    return jsonify({'message': 'Settings updated successfully', 'settings': get_all_settings(g.workspace_id)})


@settings_bp.route('/api/reminders/send', methods=['POST'])
@role_required('owner', 'admin')
def send_reminders_now():
    if not emails.EMAIL_ENABLED:
        return jsonify({'error': "Email isn't set up on this site yet."}), 404
    try:
        result = reminders.send_workspace_reminders(g.workspace_id, request.url_root, only_new=False)
    except emails.EmailError as error:
        return jsonify({'error': str(error)}), 502
    if not result['items']:
        return jsonify({'message': f"No warranties end in the next {result['window']} days, so no email was sent.",
                        **result})
    if not result['sent']:
        return jsonify({'error': 'No one to email. Add reminder recipients first.'}), 400
    people = 'person' if result['sent'] == 1 else 'people'
    return jsonify({'message': f"Reminder about {result['items']} warranty(s) emailed to {result['sent']} {people}.",
                    **result})
