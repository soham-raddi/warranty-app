"""Exchange rates (today's and each purchase date's) and converting amounts between currencies."""
import json
import re

from .connection import conn, cursor


def get_exchange_rates():
    """{currency code: units per 1 EUR}, or {} if never fetched."""
    cursor.execute("SELECT value FROM app_settings WHERE key = 'exchange_rates'")
    row = cursor.fetchone()
    try:
        return json.loads(row[0])['rates'] if row else {}
    except (ValueError, KeyError, TypeError):
        return {}


def exchange_rates_date():
    cursor.execute("SELECT value FROM app_settings WHERE key = 'exchange_rates'")
    row = cursor.fetchone()
    try:
        return json.loads(row[0]).get('date') if row else None
    except (ValueError, TypeError):
        return None


def save_exchange_rates(rates, date):
    cursor.execute(
        '''INSERT INTO app_settings (key, value) VALUES ('exchange_rates', ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value''',
        (json.dumps({'rates': rates, 'date': date}),)
    )
    conn.commit()


def convert_amount(amount, from_currency, to_currency, rates):
    if from_currency == to_currency:
        return amount
    from_rate, to_rate = rates.get(from_currency), rates.get(to_currency)
    if not from_rate or not to_rate:
        return None
    return round(amount / from_rate * to_rate, 2)


def foreign_currencies_in_use():
    """Currencies on saved items, so the daily job knows whether rates are needed at all."""
    cursor.execute("SELECT DISTINCT currency FROM appliances_v5 WHERE currency IS NOT NULL AND currency != ''")
    return {row[0] for row in cursor.fetchall()}


def get_historical_rates(dates):
    """{date: {currency: units per 1 EUR}} for the dates already fetched."""
    wanted = sorted(d for d in dates if d and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(d)))
    if not wanted:
        return {}
    cursor.execute(f"SELECT rate_date, rates FROM historical_rates WHERE rate_date IN ({', '.join('?' for _ in wanted)})",
                   wanted)
    result = {}
    for rate_date, rates in cursor.fetchall():
        try:
            result[rate_date] = json.loads(rates)
        except ValueError:
            continue
    return result


def save_historical_rates(rate_date, rates):
    cursor.execute(
        '''INSERT INTO historical_rates (rate_date, rates) VALUES (?, ?)
           ON CONFLICT (rate_date) DO UPDATE SET rates = excluded.rates''',
        (rate_date, json.dumps(rates))
    )
    conn.commit()


def purchase_dates_missing_rates(limit=50):
    """Purchase dates of items bought in another currency than their family's, whose rates aren't stored yet."""
    cursor.execute('''
        SELECT DISTINCT a.date_of_purchase FROM appliances_v5 a
        LEFT JOIN workspace_settings s ON s.workspace_id = a.workspace_id AND s.key = 'currency_code'
        WHERE a.currency IS NOT NULL AND a.currency != '' AND a.date_of_purchase IS NOT NULL
          AND a.currency != COALESCE(s.value, 'INR')
          AND a.date_of_purchase NOT IN (SELECT rate_date FROM historical_rates)
        ORDER BY a.date_of_purchase DESC
        LIMIT ?
    ''', (limit,))
    return [row[0] for row in cursor.fetchall() if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(row[0] or ''))]
