from flask import Blueprint, g, jsonify, render_template, request

import emails
import fx
import reminders
from database import (
    APPEARANCE_OPTIONS,
    get_appearance,
    set_appearance,
    CURRENCY_SYMBOLS,
    get_all_settings,
    get_workspace,
    opted_out_emails,
    pin_item_currencies,
    purchase_dates_missing_rates,
    set_reminder_optout,
    set_setting_value,
)
from ratelimit import limit
from .auth import role_required
from .workspaces import EMAIL_PATTERN, valid_timezone

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
    # Appearance is personal, so the viewer's own choices replace the family's.
    settings = {**get_all_settings(g.workspace_id), **get_appearance(g.current_user['id'], g.workspace_id)}
    return jsonify({'settings': settings})


@settings_bp.route('/api/me/appearance', methods=['PUT'])
def update_my_appearance():
    """Theme, accent colour, spacing and motion are each person's own choice, whatever their role."""
    payload = request.json or {}
    changes = {}
    for key, allowed in APPEARANCE_OPTIONS.items():
        if key in payload:
            value = str(payload[key]).strip().lower()
            if key == 'reduced_motion':
                value = '1' if value in {'1', 'true', 'yes', 'on'} else '0'
            if value not in allowed:
                return jsonify({'error': f"Choose one of: {', '.join(sorted(allowed))}."}), 400
            changes[key] = value
    set_appearance(g.current_user['id'], changes)
    return jsonify({'appearance': get_appearance(g.current_user['id'], g.workspace_id)})


@settings_bp.route('/api/settings', methods=['PUT'])
@role_required('owner', 'admin')
def update_settings():
    payload = request.json or {}

    timezone = valid_timezone(str(payload.get('timezone', 'UTC')).strip() or 'UTC')
    if not timezone:
        return jsonify({'error': 'Choose a time zone from the list.'}), 400
    currency_code = str(payload.get('currency_code', 'INR')).strip().upper() or 'INR'
    date_format = _pick_option(payload, 'date_format', 'auto', {'auto', 'iso', 'dmy', 'mdy', 'long'})
    time_format = _pick_option(payload, 'time_format', '12h', {'12h', '24h'})
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
        return jsonify({'error': f'Add at most {reminders.MAX_RECIPIENTS} reminder recipients.'}), 400
    reminder_recipients = ', '.join(recipients)

    if currency_code not in CURRENCY_SYMBOLS:
        return jsonify({'error': 'currency_code must be a supported currency'}), 400
    currency_symbol = CURRENCY_SYMBOLS[currency_code]

    if not date_format:
        return jsonify({'error': 'date_format must be auto, iso, dmy, mdy, or long'}), 400
    if not time_format:
        return jsonify({'error': 'time_format must be 12h or 24h'}), 400
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
    previous_currency = get_all_settings(g.workspace_id).get('currency_code')
    if currency_code != previous_currency:
        # Items keep the currency they were bought in; totals are converted to the new one.
        pin_item_currencies(g.workspace_id, previous_currency)
        fx.ensure_rates_for(previous_currency, currency_code)
    set_setting_value(g.workspace_id, 'currency_code', currency_code)
    if currency_code != previous_currency:
        fx.ensure_day_rates(*purchase_dates_missing_rates(fx.MAX_DAYS_PER_RUN))
    set_setting_value(g.workspace_id, 'currency_symbol', currency_symbol)
    set_setting_value(g.workspace_id, 'warranty_period_days', warranty_period_days)
    set_setting_value(g.workspace_id, 'date_format', date_format)
    set_setting_value(g.workspace_id, 'time_format', time_format)
    set_setting_value(g.workspace_id, 'expiry_alert_days', expiry_alert_days)
    set_setting_value(g.workspace_id, 'reminders_enabled', reminders_enabled)
    set_setting_value(g.workspace_id, 'reminder_days_before', reminder_days_before)
    set_setting_value(g.workspace_id, 'reminder_recipients', reminder_recipients)

    return jsonify({'message': 'Settings saved.', 'settings': get_all_settings(g.workspace_id)})


@settings_bp.route('/api/settings/timezone', methods=['PUT'])
@role_required('owner', 'admin')
def set_timezone():
    """Remembers the family's time zone the first time an owner or admin opens the app (instead of UTC)."""
    timezone = valid_timezone((request.json or {}).get('timezone'))
    if not timezone:
        return jsonify({'error': 'Choose a time zone from the list.'}), 400
    if get_all_settings(g.workspace_id).get('timezone_is_default'):
        set_setting_value(g.workspace_id, 'timezone', timezone)
    return jsonify({'timezone': get_all_settings(g.workspace_id)['timezone']})


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
    warranties = 'warranty' if result['items'] == 1 else 'warranties'
    return jsonify({'message': f"Reminder about {result['items']} {warranties} emailed to {result['sent']} {people}.",
                    **result})


@settings_bp.route('/api/me/reminders', methods=['GET', 'PUT'])
def my_reminder_emails():
    """Each person can stop (or restart) reminder emails for themselves, whatever their role."""
    email = g.current_user['email']
    if request.method == 'PUT':
        subscribed = bool((request.json or {}).get('subscribed'))
        set_reminder_optout(g.workspace_id, email, not subscribed)
    subscribed = email.lower() not in opted_out_emails(g.workspace_id)
    return jsonify({'subscribed': subscribed, 'email': email})


@settings_bp.route('/unsubscribe/<token>', methods=['GET', 'POST'])
@limit('unsubscribe', per_ip=True, hourly=60, daily=300)
def unsubscribe(token):
    """The link in every reminder email. Mail apps may POST to it directly ("one-click unsubscribe")."""
    details = reminders.read_unsubscribe_token(token)
    workspace = get_workspace(details[0]) if details else None
    if not workspace:
        return render_template('unsubscribe.html', state='invalid'), 404
    email = details[1]
    context = {'email': email, 'workspace_name': workspace['name']}
    if request.method == 'GET':
        # Opening the link only asks; link scanners in mail systems must not unsubscribe people.
        return render_template('unsubscribe.html', state='confirm', **context)
    resubscribe = request.form.get('resubscribe') == '1'
    set_reminder_optout(workspace['id'], email, not resubscribe)
    return render_template('unsubscribe.html', state='resubscribed' if resubscribe else 'unsubscribed', **context)
