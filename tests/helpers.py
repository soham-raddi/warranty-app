"""Shared setup for the tests: a throwaway database, fake AI, email and rate services, and sign-in helpers.

Run from the project folder:  python -m unittest discover -s tests -v

Each run uses a throwaway database seeded like a pre-workspace (single family) install, so the
upgrade path is tested too. Groq is never called and .env is never read.

To run the same tests against Postgres, point TEST_DATABASE_URL at an EMPTY, throwaway database
(everything in it is deleted first):  TEST_DATABASE_URL=postgresql://... python -m unittest discover -s tests
"""
import atexit
import base64
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = tempfile.mkdtemp(prefix='warranty-tests-')
DB_FILE = os.path.join(TEST_DIR, 'test.db')

OWNER = 'soham@example.com'
FAMILY = 'sister@example.com'
FRIEND = 'friend@example.com'
FRIEND_FAMILY = 'friend.wife@example.com'
STRANGER = 'stranger@example.com'


def _seed_legacy_database():
    """Creates the schema and data an install had before workspaces existed."""
    db = sqlite3.connect(DB_FILE)
    db.executescript('''
        CREATE TABLE appliances_v5 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT, brand TEXT, model_number TEXT, serial_number TEXT,
            category TEXT, date_of_purchase TEXT, price TEXT, tax_amount TEXT,
            total_amount TEXT, payment_method TEXT, store_name TEXT,
            store_contact TEXT, warranty_info TEXT, return_policy TEXT,
            invoice_number TEXT, file_path TEXT, has_warranty_card INTEGER DEFAULT 0, warranty_card_path TEXT
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, google_id TEXT UNIQUE, email TEXT UNIQUE NOT NULL,
            name TEXT, picture TEXT, role TEXT NOT NULL DEFAULT 'owner', is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_login TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    db.execute("INSERT INTO users (email, name, role) VALUES ('owner@digitaltwin.local', 'Dev Placeholder', 'admin')")
    db.execute("INSERT INTO users (email, name, role) VALUES (?, 'Soham Raddi', 'owner')", (OWNER,))
    db.execute("INSERT INTO users (email, name, role) VALUES (?, 'Sister', 'member')", (FAMILY,))
    db.execute("INSERT INTO appliances_v5 (item_name, date_of_purchase, total_amount, invoice_number) "
               "VALUES ('Kettle', '2026-01-01', '700', 'INV-1')")
    db.execute("INSERT INTO app_settings (key, value) VALUES ('currency_code', 'USD')")
    db.execute("INSERT INTO chat_messages (conversation_id, role, content) VALUES ('c1', 'user', 'old question')")
    db.commit()
    db.close()


TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL', '').strip()

if TEST_DATABASE_URL:
    import psycopg
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute('DROP SCHEMA public CASCADE')
        admin.execute('CREATE SCHEMA public')
else:
    _seed_legacy_database()

os.environ.update({
    'SKIP_DOTENV': '1',            # never read real credentials from .env
    'DATABASE_URL': TEST_DATABASE_URL,
    'DATABASE_PATH': DB_FILE,
    'OWNER_EMAIL': OWNER,
    'GROQ_API_KEY': 'test-key',
    'SECRET_KEY': 'test-secret-key-0123456789abcdef',
    'ALLOW_DEV_LOGIN': 'true',
    'ALLOW_NEW_WORKSPACES': 'true',
    'RATE_LIMITS_ENABLED': 'false',
    'CRON_SECRET': 'test-cron-secret',
    'SUPABASE_URL': '',            # files go to the local uploads/ folder in the temp directory
    'SUPABASE_SERVICE_ROLE_KEY': '',
})
for variable in ('VERCEL', 'APP_ENV'):
    os.environ.pop(variable, None)
os.chdir(TEST_DIR)  # uploads/ is created here, not in the project
sys.path.insert(0, PROJECT_ROOT)

import digital_twin as app_module  # noqa: E402  (must import after the environment is prepared)
from digital_twin import db as database  # noqa: E402
from digital_twin.services import chatbot  # noqa: E402
from digital_twin.services import emails  # noqa: E402
from digital_twin.routes import receipts as receipts_routes  # noqa: E402



def _seed_postgres_like_legacy_install():
    """Postgres equivalent of _seed_legacy_database: rows without workspaces, then the same upgrade step."""
    run = database.cursor.execute
    run("INSERT INTO users (email, name, role) VALUES ('owner@digitaltwin.local', 'Dev Placeholder', 'admin')")
    run("INSERT INTO users (email, name, role) VALUES (?, 'Soham Raddi', 'owner')", (OWNER,))
    run("INSERT INTO users (email, name, role) VALUES (?, 'Sister', 'member')", (FAMILY,))
    run("INSERT INTO appliances_v5 (item_name, date_of_purchase, total_amount, invoice_number) "
        "VALUES ('Kettle', '2026-01-01', '700', 'INV-1')")
    run("INSERT INTO app_settings (key, value) VALUES ('currency_code', 'USD')")
    run("INSERT INTO chat_messages (conversation_id, role, content) VALUES ('c1', 'user', 'old question')")
    database.conn.commit()
    database._migrate_existing_install()


if TEST_DATABASE_URL:
    assert database.IS_POSTGRES
    _seed_postgres_like_legacy_install()

_extract_counter = {'n': 0}


def _fake_extract(_data, _filename):
    _extract_counter['n'] += 1
    return {'receipt': {'total_amount': '50000', 'date_of_purchase': '2026-05-01',
                        'invoice_number': f"F-{_extract_counter['n']}", 'store_name': 'Shop'},
            'items': [{'item_name': 'Fridge', 'amount': '50000', 'category': 'Kitchen Appliances'}]}


class _FakeCompletions:
    last_messages = None

    def create(self, **kwargs):
        _FakeCompletions.last_messages = kwargs['messages']
        message = types.SimpleNamespace(content='ok')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


receipts_routes.parse_receipt_image_to_json = _fake_extract


def _unreadable_card(_data, _filename):
    return {'error': 'not read in tests'}


receipts_routes.parse_warranty_card = _unreadable_card

sent = []


def fake_send_email(to, subject, html_body, text_body, idempotency_key=None):
    sent.append({'to': to, 'subject': subject, 'html': html_body, 'text': text_body, 'key': idempotency_key})
    return f'fake-{len(sent)}'


def fake_send_batch(messages, idempotency_key=None):
    for message in messages:
        sent.append({**message, 'key': idempotency_key})


from digital_twin.services import fx  # noqa: E402
fx.refresh_exchange_rates = lambda: False  # tests never call the exchange rate service
fx.fetch_day_rates = lambda _day: False

emails.EMAIL_ENABLED = True
emails.send_email = fake_send_email
emails.send_batch = fake_send_batch
chatbot.get_client = lambda: types.SimpleNamespace(chat=types.SimpleNamespace(completions=_FakeCompletions()))


def sign_in(email):
    client = app_module.app.test_client()
    client.get(f'/auth/dev-login?email={email}')
    return client


def token_from(link):
    return link.rstrip('/').rsplit('/join/', 1)[1]


def attach_card(client, item_id):
    return client.post(f'/api/inventory/{item_id}/attach-warranty-card',
                       data={'warranty_card': (io.BytesIO(b'fake card'), 'card.jpg')},
                       content_type='multipart/form-data')


def scan(client, name='receipt.jpg'):
    return client.post('/api/receipts/scan', data={'receipt_image': (io.BytesIO(b'fake image'), name)},
                       content_type='multipart/form-data')


def save(client, details, name='receipt.jpg'):
    return client.post('/api/receipts/save', content_type='multipart/form-data', data={
        'receipt_image': (io.BytesIO(b'fake image'), name), 'details': json.dumps(details)})


def read_workbook(response):
    import openpyxl
    return openpyxl.load_workbook(io.BytesIO(response.data))


def upload(client, name='receipt.jpg'):
    """Scans a receipt and saves everything that was found, as the review screen does by default."""
    scanned = scan(client, name)
    if scanned.status_code != 200:
        return scanned
    return save(client, scanned.json, name)


def new_family(email, name):
    """Signs in a fresh person and gives them their own workspace."""
    client = sign_in(email)
    assert client.post('/api/workspaces', json={'name': name}).status_code == 201
    return client


def _clean_up():
    # Runs once after all tests, so no test ever runs with the project folder as working directory
    # (that would write test uploads into the real uploads/ folder).
    database.close_connection()  # Windows can't delete the test database while it's open
    os.chdir(PROJECT_ROOT)
    shutil.rmtree(TEST_DIR, ignore_errors=True)


atexit.register(_clean_up)

# Test files use `from helpers import *`, which brings in everything above (fakes, helpers, modules).
__all__ = [name for name in dir() if not name.startswith('__')]
