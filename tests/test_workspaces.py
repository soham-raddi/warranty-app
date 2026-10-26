"""Workspace isolation, security and data tests.

Run from the project folder:  python -m unittest discover -s tests -v

Each run uses a throwaway database seeded like a pre-workspace (single family) install, so the
upgrade path is tested too. Groq is never called and .env is never read.

To run the same tests against Postgres, point TEST_DATABASE_URL at an EMPTY, throwaway database
(everything in it is deleted first):  TEST_DATABASE_URL=postgresql://... python -m unittest discover -s tests
"""
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

import app as app_module  # noqa: E402  (must import after the environment is prepared)
import database  # noqa: E402
import chatbot  # noqa: E402
import emails  # noqa: E402
import routes.receipts as receipts_routes  # noqa: E402



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


import fx  # noqa: E402
fx.refresh_exchange_rates = lambda: False  # tests never call the exchange rate service

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


def upload(client, name='receipt.jpg'):
    """Scans a receipt and saves everything that was found, as the review screen does by default."""
    scanned = scan(client, name)
    if scanned.status_code != 200:
        return scanned
    return save(client, scanned.json, name)



def tearDownModule():
    # Runs once after every test class, so no test ever runs with the project folder as working directory
    # (that would write test uploads into the real uploads/ folder).
    database.end_request()
    os.chdir(PROJECT_ROOT)
    shutil.rmtree(TEST_DIR, ignore_errors=True)

class WorkspaceTests(unittest.TestCase):

    # --- upgrade of an existing single-family install --------------------------------------------

    def test_01_existing_install_becomes_first_workspace(self):
        owner = sign_in(OWNER)
        me = owner.get('/api/auth/me').json
        self.assertEqual(me['role'], 'owner')
        self.assertEqual(me['workspace']['name'], "Soham's Family")

        items = owner.get('/api/inventory').json['items']
        self.assertEqual([i['item_name'] for i in items], ['Kettle'])
        self.assertEqual(owner.get('/api/settings').json['settings']['currency_code'], 'USD')

        conversations = owner.get('/api/chat/conversations').json['conversations']
        self.assertEqual([c['title'] for c in conversations], ['old question'])

        members = {m['email']: m['role'] for m in owner.get('/api/workspace/members').json['members']}
        self.assertEqual(members, {OWNER: 'owner', FAMILY: 'member', 'owner@digitaltwin.local': 'admin'})

    # --- a friend starts their own family workspace ----------------------------------------------

    def test_02_new_person_is_sent_to_welcome_and_can_create_workspace(self):
        friend = sign_in(FRIEND)
        response = friend.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/welcome', response.headers['Location'])
        self.assertEqual(friend.get('/welcome').status_code, 200)

        blocked = friend.get('/api/inventory')
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json['code'], 'no_workspace')

        created = friend.post('/api/workspaces', json={'name': 'Mehta Family'})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(friend.get('/api/auth/me').json['role'], 'owner')
        self.assertEqual(friend.get('/').status_code, 200)
        self.assertEqual(friend.get('/api/inventory').json['items'], [], 'a new workspace starts empty')

    def test_03_families_cannot_see_or_touch_each_others_data(self):
        owner, friend = sign_in(OWNER), sign_in(FRIEND)
        self.assertEqual(upload(friend).status_code, 201)
        friend_item = friend.get('/api/inventory').json['items'][0]
        owner_item = owner.get('/api/inventory').json['items'][0]
        friend_item_id, owner_item_id = friend_item['id'], owner_item['id']

        self.assertNotIn('Fridge', [i['item_name'] for i in owner.get('/api/inventory').json['items']])
        self.assertNotIn('Kettle', [i['item_name'] for i in friend.get('/api/inventory').json['items']])

        # Guessing another family's item ids gets nothing.
        self.assertEqual(owner.get(f'/files/receipt/{friend_item_id}').status_code, 404)
        self.assertEqual(owner.get(f'/api/inventory/{friend_item_id}/service-history').status_code, 404)
        self.assertEqual(owner.post(f'/api/inventory/{friend_item_id}/service-history',
                                    json={'service_date': '2026-06-01', 'service_type': 'Repair'}).status_code, 404)
        self.assertEqual(owner.post(f'/api/inventory/{friend_item_id}/attach-warranty-card',
                                    data={'warranty_card': (io.BytesIO(b'x'), 'card.jpg')},
                                    content_type='multipart/form-data').status_code, 404)
        self.assertEqual(owner.delete(f'/api/delete/{friend_item_id}').status_code, 404)
        self.assertEqual(friend.delete(f'/api/delete/{owner_item_id}').status_code, 404)
        self.assertEqual(friend.get(f'/files/receipt/{friend_item_id}').status_code, 200)

        # New uploads are stored in the family's own folder.
        friend_workspace_id = friend.get('/api/auth/me').json['workspace']['id']
        self.assertTrue(friend_item['file_path'].replace('\\', '/').startswith(f'uploads/{friend_workspace_id}/'))

        # The same invoice may exist in two families without being called a duplicate.
        _extract_counter['n'] -= 1
        self.assertEqual(upload(owner).status_code, 201)

    def test_04_cannot_switch_into_another_family(self):
        owner, friend = sign_in(OWNER), sign_in(FRIEND)
        friend_workspace_id = friend.get('/api/auth/me').json['workspace']['id']
        response = owner.post('/api/workspaces/switch', json={'workspace_id': friend_workspace_id})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(owner.get('/api/auth/me').json['workspace']['name'], "Soham's Family")

    def test_05_settings_are_per_family(self):
        owner, friend = sign_in(OWNER), sign_in(FRIEND)
        settings = friend.get('/api/settings').json['settings']
        self.assertEqual(friend.put('/api/settings', json={**settings, 'currency_code': 'GBP'}).status_code, 200)
        self.assertEqual(friend.get('/api/settings').json['settings']['currency_code'], 'GBP')
        self.assertEqual(owner.get('/api/settings').json['settings']['currency_code'], 'USD')

    def test_06_assistant_only_sees_its_own_family(self):
        friend = sign_in(FRIEND)
        friend.post('/api/chat', json={'message': 'what do I own?', 'conversation_id': 'f1'})
        system_prompt = _FakeCompletions.last_messages[0]['content']
        self.assertIn('Fridge', system_prompt)
        self.assertNotIn('Kettle', system_prompt)

    # --- inviting family ---------------------------------------------------------------------------

    def test_07_invited_family_member_joins_the_right_workspace(self):
        friend = sign_in(FRIEND)
        invited = friend.post('/api/workspace/invites', json={'email': FRIEND_FAMILY, 'role': 'member'})
        self.assertEqual(invited.status_code, 201)
        self.assertEqual(friend.post('/api/workspace/invites', json={'email': FRIEND_FAMILY}).status_code, 400)

        wife = sign_in(FRIEND_FAMILY)
        me = wife.get('/api/auth/me').json
        self.assertEqual(me['workspace']['name'], 'Mehta Family')
        self.assertEqual(me['role'], 'member')
        self.assertIn('Fridge', [i['item_name'] for i in wife.get('/api/inventory').json['items']])

        # Members can use the inventory but not manage it.
        item_id = wife.get('/api/inventory').json['items'][0]['id']
        self.assertEqual(wife.delete(f'/api/delete/{item_id}').status_code, 403)
        self.assertEqual(wife.put('/api/settings', json={}).status_code, 403)
        self.assertEqual(wife.post('/api/workspace/invites', json={'email': 'x@example.com'}).status_code, 403)
        self.assertEqual(wife.patch('/api/workspace', json={'name': 'Hacked'}).status_code, 403)
        self.assertEqual(upload(wife).status_code, 201)

    def test_08_chats_are_private_to_each_person(self):
        friend, wife = sign_in(FRIEND), sign_in(FRIEND_FAMILY)
        wife.post('/api/chat', json={'message': 'secret gift idea', 'conversation_id': 'w1'})
        friend_titles = [c['title'] for c in friend.get('/api/chat/conversations').json['conversations']]
        self.assertNotIn('secret gift idea', friend_titles)
        self.assertEqual(friend.get('/api/chat/conversations/w1/messages').json['messages'], [])

        # Clearing your history only clears yours.
        friend.delete('/api/chat/history')
        wife_titles = [c['title'] for c in wife.get('/api/chat/conversations').json['conversations']]
        self.assertIn('secret gift idea', wife_titles)

    def test_09_people_without_an_invite_cannot_join(self):
        stranger = sign_in(STRANGER)
        self.assertEqual(stranger.get('/api/inventory').status_code, 403)
        self.assertEqual(stranger.post('/api/workspaces/switch', json={'workspace_id': 1}).status_code, 403)

    def test_10_revoked_invite_cannot_be_used(self):
        owner = sign_in(OWNER)
        invite = owner.post('/api/workspace/invites', json={'email': 'cousin@example.com'}).json['invite']
        self.assertEqual(owner.delete(f"/api/workspace/invites/{invite['id']}").status_code, 200)
        cousin = sign_in('cousin@example.com')
        self.assertIn('/welcome', cousin.get('/').headers['Location'])

    def test_11_invite_reaches_someone_already_signed_in(self):
        stranger = sign_in(STRANGER)
        self.assertEqual(stranger.get('/api/inventory').status_code, 403)
        sign_in(OWNER).post('/api/workspace/invites', json={'email': STRANGER})
        self.assertEqual(stranger.get('/api/inventory').status_code, 200)

    def test_12_roles_and_removal_rules(self):
        owner = sign_in(OWNER)
        members = {m['email']: m['id'] for m in owner.get('/api/workspace/members').json['members']}
        owner_id = members[OWNER]

        # The placeholder admin cannot remove the owner or invite admins.
        admin = sign_in('owner@digitaltwin.local')
        self.assertEqual(admin.delete(f'/api/workspace/members/{owner_id}').status_code, 403)
        self.assertEqual(admin.post('/api/workspace/invites', json={'email': 'a@example.com', 'role': 'admin'}).status_code, 403)
        self.assertEqual(admin.post('/api/workspace/invites', json={'email': 'a@example.com'}).status_code, 201)

        # The owner can remove members; a removed member loses access immediately.
        sister = sign_in(FAMILY)
        self.assertEqual(sister.get('/api/inventory').status_code, 200)
        self.assertEqual(owner.delete(f'/api/workspace/members/{members[FAMILY]}').status_code, 200)
        self.assertEqual(sister.get('/api/inventory').status_code, 403)

        # The owner cannot leave their own workspace.
        self.assertEqual(owner.post('/api/workspace/leave').status_code, 400)

    def test_13_member_can_belong_to_two_families_and_switch(self):
        friend = sign_in(FRIEND)
        friend.post('/api/workspace/invites', json={'email': OWNER})
        owner = sign_in(OWNER)
        names = sorted(w['name'] for w in owner.get('/api/workspaces').json['workspaces'])
        self.assertEqual(names, ['Mehta Family', "Soham's Family"])

        mehta = next(w for w in owner.get('/api/workspaces').json['workspaces'] if w['name'] == 'Mehta Family')
        self.assertEqual(owner.post('/api/workspaces/switch', json={'workspace_id': mehta['id']}).status_code, 200)
        self.assertEqual(owner.get('/api/auth/me').json['role'], 'member')
        self.assertIn('Fridge', [i['item_name'] for i in owner.get('/api/inventory').json['items']])

    def test_14_logout_requires_post(self):
        owner = sign_in(OWNER)
        page = owner.get('/auth/logout')
        self.assertIn('Sign out?', page.get_data(as_text=True), 'opening the address only asks')
        self.assertEqual(owner.get('/api/inventory').status_code, 200, 'still signed in')
        self.assertEqual(owner.post('/auth/logout').status_code, 302)
        self.assertEqual(owner.get('/api/inventory').status_code, 401)


    # --- invite links -----------------------------------------------------------------------------

    def test_15_invite_link_works_with_any_google_account(self):
        owner = sign_in(OWNER)
        created = owner.post('/api/workspace/invites', json={'email': 'grandma@example.com'}).json
        self.assertIn('/join/', created['link'])
        self.assertNotIn('token', created['invite'], 'the raw token is only returned inside the link')
        token = token_from(created['link'])

        # Anyone with the link can see who invited them, without signing in.
        page = app_module.app.test_client().get(f'/join/{token}')
        self.assertEqual(page.status_code, 200)
        self.assertIn("Soham&#39;s Family", page.get_data(as_text=True))

        # She signs in with a different Google account than the one invited.
        grandma = sign_in('grandma.personal@example.com')
        self.assertEqual(grandma.get(f'/join/{token}').status_code, 200)
        self.assertEqual(grandma.get('/api/inventory').status_code, 403, 'viewing the link must not join')

        joined = grandma.post(f'/join/{token}')
        self.assertEqual(joined.status_code, 302)
        self.assertEqual(grandma.get('/api/auth/me').json['workspace']['name'], "Soham's Family")
        self.assertEqual(grandma.get('/api/auth/me').json['role'], 'member')
        pending = [i['email'] for i in owner.get('/api/workspace/members').json['invites']]
        self.assertNotIn('grandma@example.com', pending, 'a used invite disappears')

    def test_16_invite_link_works_only_once(self):
        owner = sign_in(OWNER)
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'uncle@example.com'}).json['link'])
        self.assertEqual(sign_in('uncle@example.com').post(f'/join/{token}').status_code, 302)
        someone_else = sign_in('forwarded.to@example.com')
        self.assertEqual(someone_else.post(f'/join/{token}').status_code, 400)
        self.assertEqual(someone_else.get('/api/inventory').status_code, 403)

    def test_17_expired_invite_cannot_be_used(self):
        owner = sign_in(OWNER)
        created = owner.post('/api/workspace/invites', json={'email': 'late@example.com'}).json
        database.cursor.execute("UPDATE workspace_invites SET expires_at = '2020-01-01T00:00:00' WHERE id = ?",
                                (created['invite']['id'],))
        database.conn.commit()
        token = token_from(created['link'])

        self.assertIn('expired', app_module.app.test_client().get(f'/join/{token}').get_data(as_text=True))
        late = sign_in('late@example.com')  # even the invited email no longer joins automatically
        self.assertIn('/welcome', late.get('/').headers['Location'])
        self.assertEqual(late.post(f'/join/{token}').status_code, 400)
        expired = [i for i in owner.get('/api/workspace/members').json['invites'] if i['email'] == 'late@example.com']
        self.assertTrue(expired[0]['expired'])

        # Inviting the same email again refreshes the expired invite.
        again = owner.post('/api/workspace/invites', json={'email': 'late@example.com'})
        self.assertEqual(again.status_code, 201)
        self.assertEqual(late.post(f"/join/{token_from(again.json['link'])}").status_code, 302)

    def test_18_new_link_replaces_the_old_one(self):
        owner = sign_in(OWNER)
        created = owner.post('/api/workspace/invites', json={'email': 'aunt@example.com'}).json
        old_token = token_from(created['link'])
        fresh = owner.post(f"/api/workspace/invites/{created['invite']['id']}/link").json
        new_token = token_from(fresh['link'])
        self.assertNotEqual(old_token, new_token)

        aunt = sign_in('aunt.other@example.com')
        self.assertEqual(aunt.get(f'/join/{old_token}').status_code, 404)
        self.assertEqual(aunt.post(f'/join/{old_token}').status_code, 400)
        self.assertEqual(aunt.post(f'/join/{new_token}').status_code, 302)

    def test_19_signing_in_from_a_link_returns_to_it(self):
        owner = sign_in(OWNER)
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'nephew@example.com'}).json['link'])
        visitor = app_module.app.test_client()
        page = visitor.get(f'/join/{token}').get_data(as_text=True)
        self.assertIn(f'next=/join/{token}', page.replace('%2F', '/'))

        response = visitor.get(f'/auth/dev-login?email=nephew.alt@example.com&next=/join/{token}')
        self.assertEqual(response.headers['Location'], f'/join/{token}')

    def test_20_unknown_link_shows_not_valid(self):
        page = sign_in(STRANGER).get('/join/not-a-real-token')
        self.assertEqual(page.status_code, 404)
        self.assertIn("isn't valid", page.get_data(as_text=True))

    def test_21_raw_tokens_are_never_stored(self):
        owner = sign_in(OWNER)
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'cousin2@example.com'}).json['link'])
        database.cursor.execute('SELECT token_hash FROM workspace_invites')
        stored = [row[0] for row in database.cursor.fetchall()]
        self.assertNotIn(token, stored)
        self.assertTrue(all(len(h) == 64 for h in stored if h))

    def test_22_other_families_cannot_touch_your_invites(self):
        owner, friend = sign_in(OWNER), sign_in(FRIEND)
        invite = owner.post('/api/workspace/invites', json={'email': 'target@example.com'}).json['invite']
        self.assertEqual(friend.post(f"/api/workspace/invites/{invite['id']}/link").status_code, 404)
        self.assertEqual(friend.delete(f"/api/workspace/invites/{invite['id']}").status_code, 404)
        self.assertIn('target@example.com', [i['email'] for i in owner.get('/api/workspace/members').json['invites']])


    def test_23_invited_email_opening_its_link_sees_a_friendly_page(self):
        owner = sign_in(OWNER)
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'brother@example.com'}).json['link'])
        brother = app_module.app.test_client()
        brother.get(f'/join/{token}')
        # Signing in with the invited email joins automatically, then returns to the link.
        landing = brother.get(f'/auth/dev-login?email=brother@example.com&next=/join/{token}')
        self.assertEqual(landing.headers['Location'], f'/join/{token}')
        page = brother.get(f'/join/{token}')
        self.assertEqual(page.status_code, 200)
        self.assertIn('already in', page.get_data(as_text=True))
        self.assertEqual(brother.post(f'/join/{token}').status_code, 302)

        # To anyone else the used link is simply not valid.
        self.assertEqual(sign_in('random@example.com').get(f'/join/{token}').status_code, 404)


    def test_24_inviter_opening_their_own_link_does_not_use_it(self):
        owner = sign_in(OWNER)
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'alt.account@example.com'}).json['link'])
        page = owner.get(f'/join/{token}').get_data(as_text=True)
        self.assertIn('for someone else', page)
        self.assertEqual(owner.post(f'/join/{token}').status_code, 302)  # just opens the workspace

        # "Join with a different account" signs out and comes back to the invite.
        switched = owner.post(f'/auth/logout?next=/join/{token}')
        self.assertIn(f'next=/join/{token}', switched.headers['Location'].replace('%2F', '/'))

        alt = sign_in('alt.other@example.com')
        self.assertEqual(alt.post(f'/join/{token}').status_code, 302)
        self.assertEqual(alt.get('/api/auth/me').json['workspace']['name'], "Soham's Family")


def new_family(email, name):
    """Signs in a fresh person and gives them their own workspace."""
    client = sign_in(email)
    assert client.post('/api/workspaces', json={'name': name}).status_code == 201
    return client


class ZDataAndSecurityTests(unittest.TestCase):
    """Production hardening, item editing and data controls (independent of the workspace tests above)."""

    # --- editing items and per-item warranty length -------------------------------------------------

    def test_edit_item_and_per_item_warranty(self):
        owner = new_family('editor@example.com', 'Edit Family')
        upload(owner)
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual(item['warranty_expires_on'], '2027-05-01', 'default: purchase date + 365 days')

        changed = owner.patch(f"/api/inventory/{item['id']}", json={
            'item_name': 'Big Fridge', 'warranty_months': 24, 'date_of_purchase': '2026-01-31'})
        self.assertEqual(changed.status_code, 200)
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertEqual(item['item_name'], 'Big Fridge')
        self.assertEqual(item['warranty_months'], 24)
        self.assertEqual(item['warranty_expires_on'], '2028-01-31')

        # Month arithmetic clamps to the end of shorter months.
        owner.patch(f"/api/inventory/{item['id']}", json={'warranty_months': 1})
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertEqual(item['warranty_expires_on'], '2026-02-28')

        # Clearing the length falls back to the workspace default.
        owner.patch(f"/api/inventory/{item['id']}", json={'warranty_months': ''})
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertIsNone(item['warranty_months'])

        for bad in ({'warranty_months': 0}, {'warranty_months': 'two'}, {'date_of_purchase': '31/01/2026'},
                    {'item_name': '   '}):
            self.assertEqual(owner.patch(f"/api/inventory/{item['id']}", json=bad).status_code, 400, bad)

    def test_members_can_edit_but_other_families_cannot(self):
        owner = new_family('edit.owner@example.com', 'Edit Owner Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'edit.member@example.com'}).json['link'])
        member = sign_in('edit.member@example.com')
        member.post(f'/join/{token}')
        self.assertEqual(member.patch(f'/api/inventory/{item_id}', json={'brand': 'LG'}).status_code, 200)

        outsider = new_family('edit.outsider@example.com', 'Outsider Family')
        self.assertEqual(outsider.patch(f'/api/inventory/{item_id}', json={'brand': 'Hacked'}).status_code, 404)
        self.assertEqual(outsider.get(f'/api/inventory/{item_id}').status_code, 404)
        self.assertEqual(owner.get(f'/api/inventory/{item_id}').json['item']['brand'], 'LG')

    # --- export, transfer, deleting -----------------------------------------------------------------

    def test_csv_export_is_scoped_and_neutralises_formulas(self):
        owner = new_family('export@example.com', 'Export Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        owner.patch(f'/api/inventory/{item_id}', json={'item_name': '=HYPERLINK("http://evil")'})
        response = owner.get('/api/workspace/export.csv')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        body = response.get_data(as_text=True)
        self.assertIn("'=HYPERLINK", body, 'formulas from receipts must not run in Excel')
        self.assertNotIn('Kettle', body, 'only this workspace is exported')

    def test_transfer_ownership(self):
        owner = new_family('transfer.owner@example.com', 'Transfer Family')
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'transfer.new@example.com'}).json['link'])
        heir = sign_in('transfer.new@example.com')
        heir.post(f'/join/{token}')
        heir_id = heir.get('/api/auth/me').json['user']['id']

        self.assertEqual(heir.post('/api/workspace/transfer', json={'user_id': heir_id}).status_code, 403)
        self.assertEqual(owner.post('/api/workspace/transfer', json={'user_id': 999999}).status_code, 400)
        self.assertEqual(owner.post('/api/workspace/transfer', json={'user_id': heir_id}).status_code, 200)
        self.assertEqual(heir.get('/api/auth/me').json['role'], 'owner')
        self.assertEqual(owner.get('/api/auth/me').json['role'], 'admin')

    def test_delete_workspace_removes_everything(self):
        owner = new_family('delete.ws@example.com', 'Doomed Family')
        upload(owner)
        item = owner.get('/api/inventory').json['items'][0]
        stored_file = os.path.join(TEST_DIR, item['file_path'])
        self.assertTrue(os.path.isfile(stored_file))
        workspace_id = owner.get('/api/auth/me').json['workspace']['id']

        self.assertEqual(owner.delete('/api/workspace', json={'confirm_name': 'wrong'}).status_code, 400)
        response = owner.delete('/api/workspace', json={'confirm_name': 'Doomed Family'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(stored_file), 'receipt files are deleted too')
        database.cursor.execute('SELECT COUNT(*) FROM appliances_v5 WHERE workspace_id = ?', (workspace_id,))
        self.assertEqual(database.cursor.fetchone()[0], 0)
        self.assertIn('/welcome', owner.get('/').headers['Location'])

    def test_delete_account_rules(self):
        owner = new_family('leaving@example.com', 'Shared Family')
        token = token_from(owner.post('/api/workspace/invites', json={'email': 'stays@example.com'}).json['link'])
        sign_in('stays@example.com').post(f'/join/{token}')

        self.assertEqual(owner.delete('/api/account', json={'confirm_email': 'nope@example.com'}).status_code, 400)
        blocked = owner.delete('/api/account', json={'confirm_email': 'leaving@example.com'})
        self.assertEqual(blocked.status_code, 400)
        self.assertIn('Shared Family', blocked.json['error'], 'must transfer shared workspaces first')

        solo = new_family('solo@example.com', 'Solo Family')
        upload(solo)
        self.assertEqual(solo.delete('/api/account', json={'confirm_email': 'SOLO@example.com'}).status_code, 200)
        self.assertEqual(solo.get('/api/inventory').status_code, 401, 'signed out')
        database.cursor.execute('SELECT COUNT(*) FROM users WHERE email = ?', ('solo@example.com',))
        self.assertEqual(database.cursor.fetchone()[0], 0)

    # --- security ---------------------------------------------------------------------------------

    def test_cross_site_requests_are_blocked(self):
        owner = new_family('csrf@example.com', 'CSRF Family')
        evil = owner.post('/api/workspace/invites', json={'email': 'x1@example.com'},
                          headers={'Origin': 'https://evil.example'})
        self.assertEqual(evil.status_code, 403)
        null_origin = owner.patch('/api/workspace', json={'name': 'Hacked'}, headers={'Origin': 'null'})
        self.assertEqual(null_origin.status_code, 403)
        same_site = owner.post('/api/workspace/invites', json={'email': 'x2@example.com'},
                               headers={'Origin': 'http://localhost'})
        self.assertEqual(same_site.status_code, 201)
        # Reading is never blocked.
        self.assertEqual(owner.get('/api/inventory', headers={'Origin': 'https://evil.example'}).status_code, 200)

    def test_security_headers(self):
        owner = new_family('headers@example.com', 'Header Family')
        page = owner.get('/settings')
        self.assertIn("frame-ancestors 'none'", page.headers['Content-Security-Policy'])
        self.assertEqual(page.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(page.headers['X-Frame-Options'], 'DENY')
        self.assertIn('no-store', owner.get('/api/inventory').headers['Cache-Control'])

    def test_dev_login_is_impossible_in_production(self):
        import routes.auth as auth_routes
        auth_routes.IS_PRODUCTION = True
        try:
            response = app_module.app.test_client().get('/auth/dev-login?email=intruder@example.com')
            self.assertIn('error=dev_disabled', response.headers['Location'])
        finally:
            auth_routes.IS_PRODUCTION = False

    def test_cron_requires_the_secret(self):
        client = app_module.app.test_client()
        self.assertEqual(client.get('/api/cron/daily').status_code, 401)
        self.assertEqual(client.get('/api/cron/daily', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
        ok = client.get('/api/cron/daily', headers={'Authorization': 'Bearer test-cron-secret'})
        self.assertEqual(ok.status_code, 200)
        self.assertIn('old_invites_removed', ok.json)

    def test_rate_limits(self):
        owner = new_family('busy@example.com', 'Busy Family')
        os.environ['RATE_LIMITS_ENABLED'] = 'true'
        try:
            statuses = [owner.post('/api/chat', json={'message': f'hi {n}', 'conversation_id': 'rl'}).status_code
                        for n in range(12)]
        finally:
            os.environ['RATE_LIMITS_ENABLED'] = 'false'
        self.assertEqual(statuses[:10], [200] * 10)
        self.assertEqual(statuses[10:], [429, 429], 'the 11th message in a minute is refused')

    def test_files_cannot_escape_the_uploads_folder(self):
        import storage
        with self.assertRaises(ValueError):
            storage._safe_local_path('uploads/../app.py')

    def test_public_pages(self):
        client = app_module.app.test_client()
        for path in ('/privacy', '/terms', '/login', '/manifest.webmanifest', '/static/icons/icon-192.png'):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertIn('Groq', client.get('/privacy').get_data(as_text=True))
        self.assertEqual(client.get('/inventory').status_code, 302, 'app pages still need sign-in')


    def test_hosted_site_never_asks_families_for_mail_server_details(self):
        owner = new_family('mailless@example.com', 'Mailless Family')
        emails.EMAIL_ENABLED = False
        try:
            page = owner.get('/settings').get_data(as_text=True)
            self.assertNotIn('id="settingsReminderRecipients"', page)
            self.assertIn("Email reminders aren't available yet", page)
            self.assertEqual(owner.post('/api/reminders/send').status_code, 404)
        finally:
            emails.EMAIL_ENABLED = True
        page = owner.get('/settings').get_data(as_text=True)
        self.assertNotIn('SMTP', page, 'families never configure a mail server')
        self.assertIn('id="settingsReminderRecipients"', page)

    # --- warranty cards -----------------------------------------------------------------------------

    def test_warranty_card_sets_the_real_warranty_length(self):
        owner = new_family('cards@example.com', 'Card Family')
        upload(owner)
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual(item['warranty_expires_on'], '2027-05-01', 'default period before the card')

        receipts_routes.parse_warranty_card = lambda _d, _f: {
            'warranty_months': 24, 'warranty_start_date': '2020-01-01', 'coverage_notes': '2 years, 10 on compressor'}
        try:
            response = attach_card(owner, item['id'])
        finally:
            receipts_routes.parse_warranty_card = _unreadable_card
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['card_read'])
        self.assertIn('2-year', response.json['message'])
        item = next(i for i in owner.get('/api/inventory').json['items'] if i['id'] == item['id'])
        self.assertEqual(item['warranty_months'], 24)
        self.assertEqual(item['warranty_expires_on'], '2028-05-01', 'card length counts from the purchase date')
        self.assertEqual(item['date_of_purchase'], '2026-05-01', 'the purchase date from the receipt is kept')
        self.assertIn('compressor', item['warranty_info'])

    def test_unreadable_warranty_card_keeps_the_default(self):
        owner = new_family('blurry@example.com', 'Blurry Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        results = ({'error': 'nope'}, {'warranty_months': None, 'warranty_start_date': None, 'coverage_notes': None})
        try:
            for result in results:
                receipts_routes.parse_warranty_card = lambda _d, _f, r=result: r
                response = attach_card(owner, item_id)
                self.assertEqual(response.status_code, 200, 'the card is still attached')
                self.assertFalse(response.json['card_read'])
        finally:
            receipts_routes.parse_warranty_card = _unreadable_card
        item = owner.get('/api/inventory').json['items'][0]
        self.assertTrue(item['has_warranty_card'])
        self.assertIsNone(item['warranty_months'])

    # --- email (Resend is never called) --------------------------------------------------------------

    def test_invite_can_be_emailed(self):
        owner = new_family('inviter@example.com', 'Mail Family')
        sent.clear()
        created = owner.post('/api/workspace/invites', json={'email': 'cousin@example.com', 'send_email': True})
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json['email_sent'])
        self.assertEqual(sent[-1]['to'], 'cousin@example.com')
        self.assertIn(created.json['link'], sent[-1]['text'])
        self.assertIn('Mail Family', sent[-1]['subject'])

        again = owner.post(f"/api/workspace/invites/{created.json['invite']['id']}/email")
        self.assertEqual(again.status_code, 200)
        self.assertNotEqual(again.json['link'], created.json['link'], 'emailing again issues a fresh link')
        self.assertIn(again.json['link'], sent[-1]['text'])

        count = len(sent)
        quiet = owner.post('/api/workspace/invites', json={'email': 'aunt@example.com', 'send_email': False})
        self.assertFalse(quiet.json['email_sent'])
        self.assertEqual(len(sent), count, 'no email unless asked')

    def test_failed_invite_email_still_gives_a_link(self):
        owner = new_family('unlucky@example.com', 'Unlucky Family')

        def refuse(*_args, **_kwargs):
            raise emails.EmailError('The email service refused this address.')
        emails.send_email = refuse
        try:
            created = owner.post('/api/workspace/invites', json={'email': 'x@example.com', 'send_email': True})
        finally:
            emails.send_email = fake_send_email
        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.json['email_sent'])
        self.assertIn('/join/', created.json['link'])
        self.assertIn('refused', created.json['message'])

    def test_reminders_are_sent_once_per_stage(self):
        from datetime import date, timedelta
        owner = new_family('remind@example.com', 'Remind Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        # Make the warranty end in 20 days: inside the 30-day window but not yet at the 7-day stage.
        start = (date.today() + timedelta(days=20) - timedelta(days=365)).isoformat()
        owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': start})
        settings = owner.get('/api/settings').json['settings']
        settings.update({'reminders_enabled': '1', 'reminder_days_before': 30, 'reminder_recipients': ''})
        self.assertEqual(owner.put('/api/settings', json=settings).status_code, 200)

        cron = app_module.app.test_client()
        auth = {'Authorization': 'Bearer test-cron-secret'}
        mine = lambda: [m for m in sent if m['to'] == ['remind@example.com']]  # noqa: E731
        sent.clear()
        cron.get('/api/cron/daily', headers=auth)
        self.assertEqual(len(mine()), 1, 'members get the reminder when no recipients are set')
        self.assertIn('Fridge', mine()[0]['subject'])
        cron.get('/api/cron/daily', headers=auth)
        self.assertEqual(len(mine()), 1, 'not repeated the next day')

        # Reaching the 7-day stage sends one more.
        start = (date.today() + timedelta(days=5) - timedelta(days=365)).isoformat()
        owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': start})
        cron.get('/api/cron/daily', headers=auth)
        self.assertEqual(len(mine()), 2)

        # "Send now" always sends what is in the window.
        response = owner.post('/api/reminders/send')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['items'], 1)

    def test_reminder_recipients_are_validated(self):
        owner = new_family('validate@example.com', 'Validate Family')
        settings = owner.get('/api/settings').json['settings']
        settings['reminder_recipients'] = 'good@example.com, not-an-email'
        self.assertEqual(owner.put('/api/settings', json=settings).status_code, 400)
        settings['reminder_recipients'] = 'Good@Example.com; dad@example.com'
        saved = owner.put('/api/settings', json=settings).json['settings']
        self.assertEqual(saved['reminder_recipients'], 'good@example.com, dad@example.com')


class ZZReviewAndInventoryTests(unittest.TestCase):
    """Reviewing scans, receipts with several products, re-reading cards and unsubscribing."""

    def test_nothing_is_saved_until_the_review_is_confirmed(self):
        owner = new_family('reviewer@example.com', 'Review Family')
        scanned = scan(owner)
        self.assertEqual(scanned.status_code, 200)
        self.assertEqual(owner.get('/api/inventory').json['items'], [], 'scanning alone saves nothing')

        details = scanned.json
        details['items'][0].update({'item_name': 'Samsung Fridge', 'warranty_months': '24', 'brand': 'Samsung'})
        details['receipt']['store_name'] = 'Croma'
        saved = save(owner, details)
        self.assertEqual(saved.status_code, 201)
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual((item['item_name'], item['brand'], item['store_name']), ('Samsung Fridge', 'Samsung', 'Croma'))
        self.assertEqual(item['warranty_months'], 24)

        again = save(owner, details)
        self.assertEqual(again.status_code, 409, 'saving the same receipt twice is refused')
        self.assertTrue(scan(owner).json['items'][0]['duplicate_item_id'] is None, 'new invoice number, not a duplicate')

    def test_review_rejects_bad_details(self):
        owner = new_family('badreview@example.com', 'Bad Review Family')
        details = scan(owner).json
        for change in ({'item_name': ''}, {'warranty_months': '999'}):
            broken = json.loads(json.dumps(details))
            broken['items'][0].update(change)
            self.assertEqual(save(owner, broken).status_code, 400, change)
        broken = json.loads(json.dumps(details))
        broken['receipt']['date_of_purchase'] = '31/12/2026'
        self.assertEqual(save(owner, broken).status_code, 400)
        self.assertEqual(save(owner, {'receipt': {}, 'items': []}).status_code, 400)
        self.assertEqual(owner.get('/api/inventory').json['items'], [])

    def test_receipt_with_several_products(self):
        owner = new_family('multi@example.com', 'Multi Family')
        details = {
            'receipt': {'store_name': 'Electronics Mart', 'invoice_number': 'EM-77', 'date_of_purchase': '2026-06-01',
                        'total_amount': '91000'},
            'items': [{'item_name': 'Television', 'amount': '60000', 'warranty_months': '12'},
                      {'item_name': 'Soundbar', 'amount': '25000'},
                      {'item_name': 'Wall mount', 'amount': '6000'}],
        }
        saved = save(owner, details)
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json['saved'], 3)
        data = owner.get('/api/inventory').json
        self.assertEqual(sorted(i['item_name'] for i in data['items']), ['Soundbar', 'Television', 'Wall mount'])
        self.assertEqual(data['total_spent'], 91000, 'each item counts its own amount, not the receipt total')
        self.assertEqual({i['store_name'] for i in data['items']}, {'Electronics Mart'})

        # All three share one stored receipt, which is only deleted with the last of them.
        ids = [i['id'] for i in data['items']]
        self.assertEqual(owner.get(f'/files/receipt/{ids[0]}').status_code, 200)
        owner.delete(f'/api/delete/{ids[0]}')
        self.assertEqual(owner.get(f'/files/receipt/{ids[1]}').status_code, 200, 'still there for the others')
        owner.delete(f'/api/delete/{ids[1]}')
        owner.delete(f'/api/delete/{ids[2]}')
        self.assertEqual(len(os.listdir(os.path.join(TEST_DIR, 'uploads', str(owner.get('/api/auth/me').json['workspace']['id'])))), 0)

        # Saving the receipt again skips what is already there and adds only new items.
        save(owner, {**details, 'items': details['items'][:1]})
        again = save(owner, details)
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again.json['saved'], 2)
        self.assertEqual(again.json['skipped'], ['Television'])

    def test_card_can_be_read_again(self):
        owner = new_family('reread@example.com', 'Reread Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        self.assertEqual(owner.post(f'/api/inventory/{item_id}/read-warranty-card').status_code, 404, 'no card yet')

        attach_card(owner, item_id)  # read fails in tests, like cards attached before cards were read
        self.assertIsNone(owner.get('/api/inventory').json['items'][0]['warranty_months'])
        receipts_routes.parse_warranty_card = lambda data, _f: (
            {'warranty_months': 36, 'warranty_start_date': None, 'coverage_notes': None} if data == b'fake card'
            else {'error': 'wrong file'})
        try:
            response = owner.post(f'/api/inventory/{item_id}/read-warranty-card')
        finally:
            receipts_routes.parse_warranty_card = _unreadable_card
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['card_read'], 'the stored card file is what gets read')
        self.assertEqual(owner.get('/api/inventory').json['items'][0]['warranty_months'], 36)

        outsider = new_family('reread.outsider@example.com', 'Outsider Family')
        self.assertEqual(outsider.post(f'/api/inventory/{item_id}/read-warranty-card').status_code, 404)

    def test_unsubscribe_link_and_personal_setting(self):
        import reminders
        from datetime import date, timedelta
        owner = new_family('unsub.owner@example.com', 'Unsub Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        start = (date.today() + timedelta(days=10) - timedelta(days=365)).isoformat()
        owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': start})
        settings = owner.get('/api/settings').json['settings']
        settings.update({'reminder_recipients': 'unsub.owner@example.com, grandpa@example.com'})
        owner.put('/api/settings', json=settings)

        sent.clear()
        owner.post('/api/reminders/send')
        self.assertEqual(sorted(m['to'][0] for m in sent), ['grandpa@example.com', 'unsub.owner@example.com'],
                         'one email per person')
        grandpa_mail = next(m for m in sent if m['to'] == ['grandpa@example.com'])
        link = grandpa_mail['headers']['List-Unsubscribe'].strip('<>')
        self.assertIn(link, grandpa_mail['text'])
        path = '/unsubscribe/' + link.rsplit('/unsubscribe/', 1)[1]

        visitor = app_module.app.test_client()  # not signed in, like someone clicking in their inbox
        page = visitor.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('grandpa@example.com', page.get_data(as_text=True))
        sent.clear()
        owner.post('/api/reminders/send')
        self.assertEqual(len(sent), 2, 'just opening the link does not unsubscribe')

        self.assertIn("unsubscribed", visitor.post(path).get_data(as_text=True))
        sent.clear()
        owner.post('/api/reminders/send')
        self.assertEqual([m['to'] for m in sent], [['unsub.owner@example.com']])

        tampered = path[:-3] + ('aaa' if not path.endswith('aaa') else 'bbb')
        self.assertEqual(visitor.get(tampered).status_code, 404)
        with app_module.app.app_context():
            self.assertIsNone(reminders.read_unsubscribe_token('nonsense'))

        # Anyone can switch reminders off (and on) for themselves from Settings.
        self.assertTrue(owner.get('/api/me/reminders').json['subscribed'])
        self.assertFalse(owner.put('/api/me/reminders', json={'subscribed': False}).json['subscribed'])
        sent.clear()
        response = owner.post('/api/reminders/send')
        self.assertEqual(response.status_code, 400, 'everyone has unsubscribed')
        self.assertEqual(sent, [])
        visitor.post(path, data={'resubscribe': '1'})
        owner.post('/api/reminders/send')
        self.assertEqual([m['to'] for m in sent], [['grandpa@example.com']])


class ZZZEmailImportTests(unittest.TestCase):
    """Order emails forwarded to a workspace's private address (Resend is never called)."""

    SECRET = 'whsec_' + base64.b64encode(b'test-webhook-secret-0123456789').decode()

    @classmethod
    def setUpClass(cls):
        import inbound
        cls.inbound = inbound
        cls.saved = (inbound.INBOUND_DOMAIN, inbound.WEBHOOK_SECRET, inbound.fetch_received_email, inbound.parse_order_email)
        inbound.INBOUND_DOMAIN = 'abc123.resend.app'
        inbound.WEBHOOK_SECRET = cls.SECRET
        cls.email = {'subject': 'Your Amazon order of Philips Air Fryer', 'from': 'auto-confirm@amazon.in',
                     'date': '2026-10-20T10:00:00Z', 'text': 'Order #402-1 Philips Air Fryer Rs 7,999', 'invoice': None}
        inbound.fetch_received_email = lambda _id: dict(cls.email)
        cls.reading = {'receipt': {'store_name': 'Amazon', 'invoice_number': '402-1', 'date_of_purchase': '2026-10-20'},
                       'items': [{'item_name': 'Philips Air Fryer', 'amount': '7999', 'warranty_months': 24}]}
        inbound.parse_order_email = lambda *_args: json.loads(json.dumps(cls.reading))

    @classmethod
    def tearDownClass(cls):
        (cls.inbound.INBOUND_DOMAIN, cls.inbound.WEBHOOK_SECRET, cls.inbound.fetch_received_email,
         cls.inbound.parse_order_email) = cls.saved

    def post_event(self, event, secret=None, timestamp=None):
        import hashlib
        import hmac
        import time
        body = json.dumps(event).encode()
        msg_id, ts = 'msg_' + str(time.time_ns()), str(timestamp or int(time.time()))
        key = base64.b64decode((secret or self.SECRET).split('_', 1)[1])
        signature = base64.b64encode(hmac.new(key, f'{msg_id}.{ts}.'.encode() + body, hashlib.sha256).digest()).decode()
        return app_module.app.test_client().post('/api/inbound/email', data=body, content_type='application/json',
                                                 headers={'svix-id': msg_id, 'svix-timestamp': ts,
                                                          'svix-signature': f'v1,{signature}'})

    def received(self, to, sender, email_id, received_for=()):
        return {'type': 'email.received', 'data': {'email_id': email_id, 'from': sender, 'to': [to],
                                                   'received_for': list(received_for), 'subject': 'x'}}

    def family_with_address(self, email, name):
        client = new_family(email, name)
        address = client.get('/api/inbound/address').json['address']
        self.assertTrue(address.startswith('receipts-') and address.endswith('@abc123.resend.app'))
        return client, address

    def test_forwarded_email_waits_for_review_then_saves(self):
        owner, address = self.family_with_address('mailer@example.com', 'Mailer Family')
        response = self.post_event(self.received(address, 'Mailer <mailer@example.com>', 'em-1'))
        self.assertEqual(response.json['status'], 'stored')
        self.assertEqual(owner.get('/api/inventory').json['items'], [], 'nothing is added without review')
        self.assertEqual(owner.get('/api/inventory').json['pending_imports'], 1)

        pending = owner.get('/api/pending').json['pending']
        self.assertEqual(pending[0]['items'][0]['item_name'], 'Philips Air Fryer')
        file = owner.get(f"/files/pending/{pending[0]['id']}")
        self.assertEqual(file.status_code, 200)
        self.assertTrue(file.data.startswith(b'%PDF'), 'the email is kept as a PDF receipt')

        saved = owner.post(f"/api/pending/{pending[0]['id']}/save", json={'receipt': pending[0]['receipt'],
                                                                         'items': pending[0]['items']})
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(owner.get('/api/pending').json['pending'], [])
        item = owner.get('/api/inventory').json['items'][0]
        self.assertEqual((item['item_name'], item['store_name'], item['warranty_months']), ('Philips Air Fryer', 'Amazon', 24))
        self.assertEqual(owner.get(f"/files/receipt/{item['id']}").status_code, 200, 'the email PDF became the receipt')

        again = self.post_event(self.received(address, 'mailer@example.com', 'em-1'))
        self.assertEqual(again.json['status'], 'ignored: already imported')

    def test_only_family_members_can_forward(self):
        owner, address = self.family_with_address('guarded@example.com', 'Guarded Family')
        self.assertEqual(self.post_event(self.received(address, 'spammer@example.com', 'em-spam')).json['status'],
                         'ignored: not from a member')
        # Auto-forwarding keeps the shop as sender but says who it was forwarded for.
        auto = self.post_event(self.received(f'Receipts <{address}>', 'orders@flipkart.com', 'em-auto', ['guarded@example.com']))
        self.assertEqual(auto.json['status'], 'stored')
        self.assertEqual(self.post_event(self.received('receipts-0000000000@abc123.resend.app', 'guarded@example.com',
                                                       'em-unknown')).json['status'], 'ignored: unknown address')
        self.assertEqual(len(owner.get('/api/pending').json['pending']), 1)

    def test_unsigned_or_old_requests_are_rejected(self):
        import time
        _, address = self.family_with_address('signed@example.com', 'Signed Family')
        event = self.received(address, 'signed@example.com', 'em-sig')
        wrong = 'whsec_' + base64.b64encode(b'someone-else').decode()
        self.assertEqual(self.post_event(event, secret=wrong).status_code, 401)
        self.assertEqual(self.post_event(event, timestamp=int(time.time()) - 3600).status_code, 401)
        unsigned = app_module.app.test_client().post('/api/inbound/email', json=event)
        self.assertEqual(unsigned.status_code, 401)

    def test_families_only_see_their_own_imports(self):
        owner, address = self.family_with_address('mine@example.com', 'Mine Family')
        self.post_event(self.received(address, 'mine@example.com', 'em-mine'))
        pending_id = owner.get('/api/pending').json['pending'][0]['id']
        other = new_family('theirs@example.com', 'Theirs Family')
        self.assertEqual(other.get('/api/pending').json['pending'], [])
        self.assertEqual(other.get(f'/files/pending/{pending_id}').status_code, 404)
        self.assertEqual(other.post(f'/api/pending/{pending_id}/save', json=self.reading).status_code, 404)
        self.assertEqual(other.delete(f'/api/pending/{pending_id}').status_code, 404)
        self.assertEqual(owner.delete(f'/api/pending/{pending_id}').status_code, 200)
        self.assertEqual(owner.get('/api/pending').json['pending'], [])

    def test_unreadable_email_is_kept_for_manual_details(self):
        owner, address = self.family_with_address('vague@example.com', 'Vague Family')
        self.inbound.parse_order_email = lambda *_args: {'error': "This email doesn't look like a purchase."}
        try:
            self.post_event(self.received(address, 'vague@example.com', 'em-vague'))
        finally:
            self.inbound.parse_order_email = lambda *_args: json.loads(json.dumps(self.reading))
        pending = owner.get('/api/pending').json['pending'][0]
        self.assertIn("doesn't look like a purchase", pending['note'])
        self.assertEqual(pending['items'][0]['item_name'], self.email['subject'])

    def test_replacing_the_address_and_gmail_confirmation(self):
        owner, address = self.family_with_address('rotate@example.com', 'Rotate Family')
        self.post_event(self.received(address, 'forwarding-noreply@google.com', 'em-gmail'))
        self.assertIn('Air Fryer', owner.get('/api/inbound/address').json['gmail_confirmation'])

        new_address = owner.post('/api/inbound/address').json['address']
        self.assertNotEqual(new_address, address)
        self.assertEqual(self.post_event(self.received(address, 'rotate@example.com', 'em-old')).json['status'],
                         'ignored: unknown address')
        self.assertEqual(self.post_event(self.received(new_address, 'rotate@example.com', 'em-new')).json['status'], 'stored')

        owner.post('/api/workspace/invites', json={'email': 'kid@example.com'})
        kid = sign_in('kid@example.com')
        self.assertEqual(kid.post('/api/inbound/address').status_code, 403, 'only owners and admins replace it')

    def test_email_helpers(self):
        self.assertEqual(self.inbound.html_to_text('<style>x{}</style><p>Order <b>#1</b></p><table><tr><td>Fryer</td>'
                                                   '<td>Rs 7,999</td></tr></table>'), 'Order #1\n\nFryer | Rs 7,999')
        pdf = self.inbound.email_as_pdf('Subject', 'shop@example.com', 'today', 'line ' * 400)
        self.assertTrue(pdf.startswith(b'%PDF'))


class ZZZZCurrencyTests(unittest.TestCase):
    """Items bought in another currency keep it; totals convert to the workspace currency."""

    def test_items_keep_their_currency_and_totals_convert(self):
        owner = new_family('traveller@example.com', 'Traveller Family')  # workspace currency is INR
        details = {'receipt': {'store_name': 'Cytron', 'invoice_number': '548720', 'currency': 'myr'},
                   'items': [{'item_name': 'SG90 Micro Servo', 'amount': '6.50'},
                             {'item_name': 'Vibrating Motor', 'amount': '9.00'}]}
        self.assertEqual(save(owner, details).status_code, 201)
        save(owner, {'receipt': {'invoice_number': 'IN-1'}, 'items': [{'item_name': 'Kettle', 'amount': '700'}]})

        data = owner.get('/api/inventory').json
        servo = next(i for i in data['items'] if i['item_name'] == 'SG90 Micro Servo')
        self.assertEqual(servo['currency'], 'MYR')
        self.assertIsNone(servo['home_amount'], 'no exchange rate yet')
        self.assertEqual(data['total_spent'], 700, 'ringgit items are left out until a rate is known')
        self.assertEqual(data['unconverted_items'], 2)

        database.save_exchange_rates({'EUR': 1.0, 'INR': 100.0, 'MYR': 5.0}, '2026-10-25')
        data = owner.get('/api/inventory').json
        servo = next(i for i in data['items'] if i['item_name'] == 'SG90 Micro Servo')
        self.assertEqual(servo['home_amount'], 130.0, 'RM6.50 at 20 rupees per ringgit')
        self.assertEqual(data['total_spent'], 700 + 130 + 180)
        self.assertEqual((data['converted_items'], data['unconverted_items']), (2, 0))
        kettle = next(i for i in data['items'] if i['item_name'] == 'Kettle')
        self.assertEqual((kettle['currency'], kettle['home_amount']), ('INR', 700), 'no currency means the workspace one')

    def test_currency_can_be_corrected(self):
        owner = new_family('fixer@example.com', 'Fixer Family')
        save(owner, {'receipt': {'invoice_number': 'FX-1'}, 'items': [{'item_name': 'Servo', 'amount': '6.5'}]})
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'currency': 'ringgit'}).status_code, 400)
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'currency': 'myr'}).status_code, 200)
        self.assertEqual(owner.get('/api/inventory').json['items'][0]['currency'], 'MYR')
        owner.patch(f'/api/inventory/{item_id}', json={'currency': ''})
        self.assertEqual(owner.get('/api/inventory').json['items'][0]['currency'], 'MYR', 'blank keeps the currency')

    def test_changing_the_workspace_currency_converts_instead_of_relabelling(self):
        owner = new_family('switcher@example.com', 'Switcher Family')  # INR
        save(owner, {'receipt': {'invoice_number': 'SW-1'}, 'items': [{'item_name': 'Phone', 'amount': '33999'}]})
        # An item from before currencies were tracked has none stored.
        workspace_id = owner.get('/api/auth/me').json['workspace']['id']
        database.cursor.execute("UPDATE appliances_v5 SET currency = NULL WHERE workspace_id = ?", (workspace_id,))
        database.conn.commit()
        database.save_exchange_rates({'EUR': 1.0, 'INR': 100.0, 'USD': 1.25}, '2026-10-25')

        settings = owner.get('/api/settings').json['settings']
        settings['currency_code'] = 'USD'
        self.assertEqual(owner.put('/api/settings', json=settings).status_code, 200)
        data = owner.get('/api/inventory').json
        self.assertEqual(data['items'][0]['currency'], 'INR', 'the phone was bought in rupees')
        self.assertEqual(data['total_spent'], 424.99, 'Rs 33,999 is about $425, not $33,999')

    def test_receipt_currency_is_read(self):
        import llm_extractor
        cleaned = llm_extractor._clean_receipt_data({'currency': 'myr', 'items': [{'item_name': 'Servo'}]})
        self.assertEqual(cleaned['receipt']['currency'], 'MYR')
        self.assertIsNone(llm_extractor._clean_receipt_data({'currency': 'RM', 'items': []})['receipt']['currency'])


class ZZZZZAuditFixTests(unittest.TestCase):
    """Fixes from the full app audit."""

    def test_service_records_are_checked_and_can_be_edited_or_deleted(self):
        owner = new_family('servicer@example.com', 'Service Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        url = f'/api/inventory/{item_id}/service-history'
        for bad in ({'service_date': '2026-13-45', 'service_type': 'Repair'},
                    {'service_date': '2026-10-01', 'service_type': ''},
                    {'service_date': '2026-10-01', 'service_type': 'Repair', 'cost': 'lots'},
                    {'service_date': '2026-10-01', 'service_type': 'Repair', 'next_service_date': 'whenever'},
                    {'service_date': '2026-10-01', 'service_type': 'Repair', 'next_service_date': '2026-09-01'},
                    {'service_date': '2099-01-01', 'service_type': 'Repair'}):
            response = owner.post(url, json=bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertTrue(response.json['error'].endswith('.'), 'friendly sentence, not a field name')
        created = owner.post(url, json={'service_date': '2026-10-01', 'service_type': 'Repair', 'cost': 'Rs 1,500.00'})
        self.assertEqual(created.status_code, 200)
        entry = created.json['entries'][0]
        self.assertEqual(entry['cost'], '1500')

        edited = owner.patch(f"{url}/{entry['id']}", json={'service_date': '2026-10-02', 'service_type': 'Gas refill'})
        self.assertEqual(edited.json['entries'][0]['service_type'], 'Gas refill')
        outsider = new_family('service.outsider@example.com', 'Other Service Family')
        self.assertEqual(outsider.delete(f"{url}/{entry['id']}").status_code, 404)
        self.assertEqual(owner.delete(f"{url}/{entry['id']}").json['entries'], [])

    def test_item_amounts_and_dates_are_checked(self):
        owner = new_family('checker@example.com', 'Checker Family')
        upload(owner)
        item_id = owner.get('/api/inventory').json['items'][0]['id']
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'total_amount': 'about seven hundred'}).status_code, 400)
        self.assertEqual(owner.patch(f'/api/inventory/{item_id}', json={'date_of_purchase': '2099-01-01'}).status_code, 400)
        owner.patch(f'/api/inventory/{item_id}', json={'total_amount': '24990.0'})
        self.assertEqual(owner.get(f'/api/inventory/{item_id}').json['item']['total_amount'], '24990')

    def test_unreadable_file_gives_a_friendly_error(self):
        import llm_extractor
        result = llm_extractor.parse_receipt_image_to_json(b'not really a png', 'broken.png')
        self.assertNotIn('BytesIO', result['error'])
        self.assertIn("couldn't be opened", result['error'])

    def test_one_chat_can_be_deleted(self):
        owner = new_family('chatter@example.com', 'Chat Family')
        for conversation in ('keep', 'gone'):
            owner.post('/api/chat', json={'message': f'hello {conversation}', 'conversation_id': conversation})
        self.assertEqual(owner.delete('/api/chat/conversations/gone').status_code, 200)
        left = [c['conversation_id'] for c in owner.get('/api/chat/conversations').json['conversations']]
        self.assertEqual(left, ['keep'])
        other = new_family('chat.outsider@example.com', 'Other Chat Family')
        self.assertEqual(other.delete('/api/chat/conversations/keep').status_code, 404, "can't delete someone else's")

    def test_new_families_start_in_their_own_time_zone(self):
        client = sign_in('kl.family@example.com')
        client.post('/api/workspaces', json={'name': 'KL Family', 'timezone': 'Asia/Kuala_Lumpur'})
        self.assertEqual(client.get('/api/settings').json['settings']['timezone'], 'Asia/Kuala_Lumpur')

        legacy = new_family('utc.family@example.com', 'UTC Family')  # created without a time zone
        settings = legacy.get('/api/settings').json['settings']
        self.assertTrue(settings['timezone_is_default'])
        self.assertEqual(legacy.put('/api/settings/timezone', json={'timezone': 'Mars/Olympus'}).status_code, 400)
        self.assertEqual(legacy.put('/api/settings/timezone', json={'timezone': 'Asia/Kolkata'}).json['timezone'], 'Asia/Kolkata')
        # Once chosen, it isn't overwritten by another device.
        self.assertEqual(legacy.put('/api/settings/timezone', json={'timezone': 'Europe/London'}).json['timezone'], 'Asia/Kolkata')
        settings = legacy.get('/api/settings').json['settings']
        settings['timezone'] = 'Not/AZone'
        self.assertEqual(legacy.put('/api/settings', json=settings).status_code, 400)

    def test_email_again_shows_the_new_link_when_sending_fails(self):
        owner = new_family('resender@example.com', 'Resend Family')
        invite = owner.post('/api/workspace/invites', json={'email': 'aunt@example.com'}).json['invite']

        def refuse(*_args, **_kwargs):
            raise emails.EmailError('The email could not be sent.')
        emails.send_email = refuse
        try:
            response = owner.post(f"/api/workspace/invites/{invite['id']}/email")
        finally:
            emails.send_email = fake_send_email
        self.assertEqual(response.status_code, 502)
        self.assertIn('/join/', response.json['link'], 'the replacement link is still shown')
        self.assertTrue(response.json['expires_at'])

    def test_friendly_error_pages_and_strict_scripts(self):
        owner = new_family('pages@example.com', 'Pages Family')
        page = owner.get('/no-such-page')
        self.assertEqual(page.status_code, 404)
        self.assertIn('Page not found', page.get_data(as_text=True))
        api = owner.get('/api/no-such-thing')
        self.assertEqual(api.status_code, 404)
        self.assertIn('error', api.json)

        settings_page = owner.get('/settings')
        policy = settings_page.headers['Content-Security-Policy']
        self.assertNotIn("'unsafe-inline' https://cdn", policy.split('style-src')[0], 'scripts need the nonce')
        nonce = policy.split("'nonce-")[1].split("'")[0]
        html = settings_page.get_data(as_text=True)
        self.assertIn(f'<script nonce="{nonce}">', html)
        self.assertNotIn('onclick=', html)
        self.assertNotEqual(nonce, owner.get('/settings').headers['Content-Security-Policy'].split("'nonce-")[1].split("'")[0])

    def test_csv_export_includes_currencies(self):
        owner = new_family('csv.currency@example.com', 'CSV Currency Family')
        save(owner, {'receipt': {'invoice_number': 'CSV-1', 'currency': 'MYR'}, 'items': [{'item_name': 'Servo', 'amount': '6.5'}]})
        text = owner.get('/api/workspace/export.csv').get_data(as_text=True)
        header = text.splitlines()[1]
        self.assertIn('currency', header)
        self.assertIn('MYR', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
