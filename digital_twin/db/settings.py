"""Each family's settings, and each person's own appearance."""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connection import conn, cursor


# Display symbol for each supported currency; the symbol is always derived from the code.
CURRENCY_SYMBOLS = {
    'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ',
    'CAD': 'CA$', 'AUD': 'A$', 'SGD': 'S$', 'JPY': '¥', 'CNY': 'CN¥',
    'MYR': 'RM', 'THB': '฿', 'IDR': 'Rp', 'PHP': '₱', 'HKD': 'HK$', 'NZD': 'NZ$', 'CHF': 'CHF',
    'SAR': 'SAR', 'QAR': 'QAR', 'KRW': '₩', 'LKR': 'Rs', 'NPR': 'Rs', 'BDT': '৳', 'ZAR': 'R',
}


DEFAULT_SETTINGS = {
    'timezone': 'UTC',
    'currency_symbol': '₹',
    'currency_code': 'INR',
    'warranty_period_days': '365',
    'theme_mode': 'system',
    'accent_color': 'blue',
    'date_format': 'auto',
    'time_format': '12h',
    'reduced_motion': '0',
    'density': 'comfortable',
    'expiry_alert_days': '30',
    'reminders_enabled': '0',
    'reminder_days_before': '30',
    'reminder_recipients': '',
}


def get_setting_value(workspace_id, key, default_value=None):
    cursor.execute(
        'SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = ?',
        (workspace_id, str(key))
    )
    row = cursor.fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key, default_value) if default_value is None else default_value
    return row[0]


def set_setting_value(workspace_id, key, value):
    cursor.execute(
        '''
        INSERT INTO workspace_settings (workspace_id, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(workspace_id, key) DO UPDATE SET value = excluded.value
        ''',
        (workspace_id, str(key), str(value))
    )
    conn.commit()
    return True


def workspace_today(workspace_id):
    """Today's date where the family lives (the server itself runs on UTC)."""
    try:
        zone = ZoneInfo(get_setting_value(workspace_id, 'timezone', 'UTC'))
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo('UTC')
    return datetime.now(zone).date()


def get_all_settings(workspace_id):
    result = dict(DEFAULT_SETTINGS)
    cursor.execute('SELECT key, value FROM workspace_settings WHERE workspace_id = ?', (workspace_id,))
    stored = cursor.fetchall()
    for key, value in stored:
        result[key] = value
    # Families who never chose a time zone get the browser's one in the app instead of UTC.
    result['timezone_is_default'] = 'timezone' not in {key for key, _ in stored}
    result['currency_symbol'] = CURRENCY_SYMBOLS.get(result.get('currency_code'), result.get('currency_symbol'))
    return result


APPEARANCE_OPTIONS = {
    'theme_mode': {'system', 'light', 'dark'},
    'accent_color': {'blue', 'teal', 'amber', 'rose', 'slate'},
    'density': {'comfortable', 'compact'},
    'reduced_motion': {'0', '1'},
}


def get_appearance(user_id, workspace_id=None):
    """This person's look. Anything they haven't chosen falls back to their family's old shared setting."""
    defaults = {'theme_mode': 'system', 'accent_color': 'blue', 'density': 'comfortable', 'reduced_motion': '0'}
    if workspace_id:
        shared = get_all_settings(workspace_id)
        defaults = {key: shared.get(key, value) for key, value in defaults.items()}
    if user_id:
        cursor.execute('SELECT key, value FROM user_preferences WHERE user_id = ?', (user_id,))
        defaults.update({key: value for key, value in cursor.fetchall() if key in APPEARANCE_OPTIONS})
    return defaults


def set_appearance(user_id, changes):
    for key, value in changes.items():
        cursor.execute(
            '''INSERT INTO user_preferences (user_id, key, value) VALUES (?, ?, ?)
               ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value''',
            (user_id, key, value)
        )
    conn.commit()
