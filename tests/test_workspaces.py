"""Workspace isolation, security and data tests.

Run from the project folder:  python -m unittest discover -s tests -v

Each run uses a throwaway database seeded like a pre-workspace (single family) install, so the
upgrade path is tested too. Groq is never called and .env is never read.

To run the same tests against Postgres, point TEST_DATABASE_URL at an EMPTY, throwaway database
(everything in it is deleted first):  TEST_DATABASE_URL=postgresql://... python -m unittest discover -s tests
"""
import io
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
    return {'item_name': 'Fridge', 'total_amount': '50000', 'date_of_purchase': '2026-05-01',
            'invoice_number': f"F-{_extract_counter['n']}", 'store_name': 'Shop'}


class _FakeCompletions:
    last_messages = None

    def create(self, **kwargs):
        _FakeCompletions.last_messages = kwargs['messages']
        message = types.SimpleNamespace(content='ok')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


receipts_routes.parse_receipt_image_to_json = _fake_extract
chatbot.get_client = lambda: types.SimpleNamespace(chat=types.SimpleNamespace(completions=_FakeCompletions()))


def sign_in(email):
    client = app_module.app.test_client()
    client.get(f'/auth/dev-login?email={email}')
    return client


def token_from(link):
    return link.rstrip('/').rsplit('/join/', 1)[1]


def upload(client, name='receipt.jpg'):
    return client.post('/upload', data={'receipt_image': (io.BytesIO(b'fake image'), name)},
                       content_type='multipart/form-data')



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
        self.assertEqual(upload(friend).status_code, 200)
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
        self.assertEqual(upload(owner).status_code, 200)

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
        self.assertEqual(upload(wife).status_code, 200)

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
        self.assertEqual(owner.get('/auth/logout').status_code, 405)
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
        self.assertIn('not valid', page.get_data(as_text=True))

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
        import routes.settings as settings_routes
        owner = new_family('mailless@example.com', 'Mailless Family')
        app_module.EMAIL_REMINDERS_AVAILABLE = settings_routes.EMAIL_REMINDERS_AVAILABLE = False
        try:
            page = owner.get('/settings').get_data(as_text=True)
            self.assertNotIn('SMTP', page)
            self.assertNotIn('settingsReminderRecipients', page)
            self.assertIn("Email reminders aren't available yet", page)
            self.assertEqual(owner.post('/api/reminders/send').status_code, 404)
        finally:
            app_module.EMAIL_REMINDERS_AVAILABLE = settings_routes.EMAIL_REMINDERS_AVAILABLE = True
        self.assertIn('SMTP', owner.get('/settings').get_data(as_text=True), 'still available locally')


if __name__ == '__main__':
    unittest.main(verbosity=2)
