"""Exchange rates for items bought in another currency, from Frankfurter (https://frankfurter.dev, free, no key).

Rates are stored in the database and refreshed by the daily job (or right away when the first item in a new
currency is saved), so showing the inventory never waits on the rate service.
"""
from datetime import date

import requests

from database import exchange_rates_date, foreign_currencies_in_use, get_exchange_rates, save_exchange_rates

RATES_URL = 'https://api.frankfurter.dev/v2/rates?base=EUR'
TIMEOUT_SECONDS = 6


def refresh_exchange_rates():
    """Fetches today's rates. Returns True when they were saved."""
    try:
        response = requests.get(RATES_URL, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        rates = {row['quote']: float(row['rate']) for row in response.json() if row.get('rate')}
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        print(f'Exchange rates could not be fetched: {error}')
        return False
    rates['EUR'] = 1.0
    save_exchange_rates(rates, date.today().isoformat())
    return True


def ensure_rates_for(*currencies):
    """Fetches rates now if any of these currencies has none yet (e.g. the first item bought in ringgit)."""
    known = get_exchange_rates()
    if any(code and code not in known for code in currencies):
        refresh_exchange_rates()


def refresh_if_needed():
    """Daily job: refresh only when some item uses another currency and today's rates aren't stored yet."""
    if foreign_currencies_in_use() and exchange_rates_date() != date.today().isoformat():
        return refresh_exchange_rates()
    return False
