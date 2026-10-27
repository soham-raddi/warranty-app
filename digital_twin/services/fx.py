"""Exchange rates for items bought in another currency, from Frankfurter (https://frankfurter.dev, free, no key).

Rates are stored in the database and refreshed by the daily job (or right away when the first item in a new
currency is saved), so showing the inventory never waits on the rate service.
"""
from datetime import date

import requests

from ..db import (
    exchange_rates_date,
    foreign_currencies_in_use,
    get_exchange_rates,
    get_historical_rates,
    purchase_dates_missing_rates,
    save_exchange_rates,
    save_historical_rates,
)

RATES_URL = 'https://api.frankfurter.dev/v2/rates?base=EUR'
DAY_RATES_URL = 'https://api.frankfurter.dev/v2/rates?base=EUR&date={}'
MAX_DAYS_PER_RUN = 20  # keeps a request (or the daily job) quick even with many old purchases
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


def fetch_day_rates(day):
    """Fetches and stores the rates for one past date (a weekend or holiday gives the last working day's)."""
    try:
        response = requests.get(DAY_RATES_URL.format(day), timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        rates = {row['quote']: float(row['rate']) for row in response.json() if row.get('rate')}
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        print(f'Exchange rates for {day} could not be fetched: {error}')
        return False
    if not rates:
        return False
    rates['EUR'] = 1.0
    save_historical_rates(day, rates)
    return True


def ensure_day_rates(*days):
    """Fetches any of these purchase dates' rates that aren't stored yet (only future dates are skipped)."""
    today = date.today().isoformat()
    known = get_historical_rates(set(days))
    missing = sorted({d for d in days if d and d not in known and d <= today})
    for day in missing[:MAX_DAYS_PER_RUN]:
        fetch_day_rates(day)


def ensure_rates_for(*currencies):
    """Fetches rates now if any of these currencies has none yet (e.g. the first item bought in ringgit)."""
    known = get_exchange_rates()
    if any(code and code not in known for code in currencies):
        refresh_exchange_rates()


def refresh_if_needed():
    """Daily job: refresh today's rates when some item uses another currency, and fill in purchase-date rates for
    items saved before they were stored (or while the rate service was down)."""
    refreshed = False
    if foreign_currencies_in_use() and exchange_rates_date() != date.today().isoformat():
        refreshed = refresh_exchange_rates()
    ensure_day_rates(*purchase_dates_missing_rates(MAX_DAYS_PER_RUN))
    return refreshed
