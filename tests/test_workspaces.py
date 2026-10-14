"""Workspace isolation tests.

Run from the project folder:  python -m unittest discover -s tests -v

Each run uses a throwaway database seeded like a pre-workspace (single family) install, so the
upgrade path is tested too. Groq is never called.
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


_seed_legacy_database()
os.environ.update({
    'DATABASE_PATH': DB_FILE,
    'OWNER_EMAIL': OWNER,
    'GROQ_API_KEY': 'test-key',
    'SECRET_KEY': 'test-secret-key-0123456789abcdef',
})
os.chdir(TEST_DIR)  # uploads/ is created here, not in the project
sys.path.insert(0, PROJECT_ROOT)

import app as app_module  # noqa: E402  (must import after the environment is prepared)
import chatbot  # noqa: E402
import routes.receipts as receipts_routes  # noqa: E402

# app.py lets .env override the environment; force the values these tests depend on.
os.environ['ALLOW_DEV_LOGIN'] = 'true'
os.environ['ALLOW_NEW_WORKSPACES'] = 'true'

_extract_counter = {'n': 0}


def _fake_extract(_path):
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


def upload(client, name='receipt.jpg'):
    return client.post('/upload', data={'receipt_image': (io.BytesIO(b'fake image'), name)},
                       content_type='multipart/form-data')


class WorkspaceTests(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        os.chdir(PROJECT_ROOT)
        shutil.rmtree(TEST_DIR, ignore_errors=True)

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
