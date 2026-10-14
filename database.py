import hashlib
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

DB_PATH = os.getenv('DATABASE_PATH', 'inventory.db')
_local = threading.local()


def _thread_connection():
    """Each server thread gets its own connection, so concurrent requests never share a cursor."""
    if getattr(_local, 'conn', None) is None:
        _local.conn = sqlite3.connect(DB_PATH, timeout=10)
        _local.cursor = _local.conn.cursor()
    return _local.conn


class _ThreadLocalProxy:
    """Forwards attribute access to this thread's connection or cursor."""

    def __init__(self, attribute):
        self._attribute = attribute

    def __getattr__(self, name):
        _thread_connection()
        return getattr(getattr(_local, self._attribute), name)


conn = _ThreadLocalProxy('conn')
cursor = _ThreadLocalProxy('cursor')

# WAL lets readers proceed while another request is writing.
_thread_connection().execute('PRAGMA journal_mode=WAL')

# Display symbol for each supported currency; the symbol is always derived from the code.
CURRENCY_SYMBOLS = {
    'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ',
    'CAD': 'CA$', 'AUD': 'A$', 'SGD': 'S$', 'JPY': '¥', 'CNY': 'CN¥',
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
    'reminder_subject': 'Warranty expiry reminder',
    'reminder_body': 'Your warranty for {{item_name}} expires on {{expiry_date}}. Days left: {{days_remaining}}.',
    'smtp_host': '',
    'smtp_port': '587',
    'smtp_username': '',
    'smtp_sender_email': ''
}

WORKSPACE_ROLES = ('owner', 'admin', 'member')
INVITE_LIFETIME_DAYS = 7


def _add_column(table, column_sql):
    try:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column_sql}')
    except sqlite3.OperationalError:
        pass  # column already exists


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Upgraded to v5 to include invoice_number
cursor.execute('''
    CREATE TABLE IF NOT EXISTS appliances_v5 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT, brand TEXT, model_number TEXT, serial_number TEXT,
        category TEXT, date_of_purchase TEXT, price TEXT, tax_amount TEXT,
        total_amount TEXT, payment_method TEXT, store_name TEXT,
        store_contact TEXT, warranty_info TEXT, return_policy TEXT,
        invoice_number TEXT, file_path TEXT,
        has_warranty_card INTEGER DEFAULT 0,
        warranty_card_path TEXT
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS service_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        service_date TEXT NOT NULL,
        service_type TEXT NOT NULL,
        vendor TEXT,
        cost TEXT,
        notes TEXT,
        next_service_date TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
''')

# Settings from before workspaces existed; kept read-only so older versions of the app still work.
cursor.execute('''
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_id TEXT UNIQUE,
        email TEXT UNIQUE NOT NULL,
        name TEXT,
        picture TEXT,
        role TEXT NOT NULL DEFAULT 'owner',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_login TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
''')

# A workspace is one family's private space: its own items, settings, members and chats.
cursor.execute('''
    CREATE TABLE IF NOT EXISTS workspaces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS workspace_members (
        workspace_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (workspace_id, user_id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS workspace_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        invited_by INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (workspace_id, email)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS workspace_settings (
        workspace_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (workspace_id, key)
    )
''')

_add_column('chat_messages', 'conversation_id TEXT NOT NULL DEFAULT ""')
_add_column('appliances_v5', 'has_warranty_card INTEGER DEFAULT 0')
_add_column('appliances_v5', 'warranty_card_path TEXT')
_add_column('appliances_v5', 'workspace_id INTEGER')
_add_column('chat_messages', 'workspace_id INTEGER')
_add_column('chat_messages', 'user_id INTEGER')
_add_column('workspace_invites', 'token_hash TEXT')
_add_column('workspace_invites', 'expires_at TEXT')
_add_column('workspace_invites', 'accepted_at TEXT')
_add_column('workspace_invites', 'accepted_by INTEGER')

cursor.execute("UPDATE chat_messages SET conversation_id = 'legacy' WHERE conversation_id IS NULL OR conversation_id = ''")
cursor.execute('CREATE INDEX IF NOT EXISTS idx_service_history_item_id ON service_history(item_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_appliances_workspace ON appliances_v5(workspace_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_owner ON chat_messages(workspace_id, user_id, conversation_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_members_user ON workspace_members(user_id)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_invites_email ON workspace_invites(email)')
cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_invites_token ON workspace_invites(token_hash) WHERE token_hash IS NOT NULL')
conn.commit()

# Earlier extractions stored placeholders such as "not found"; store them as empty instead.
PLACEHOLDER_VALUES = ('', 'n/a', 'na', 'none', 'null', 'unknown', 'not found', 'not available',
                      'not mentioned', 'not specified', 'not specified on receipt', '-')
_TEXT_COLUMNS = ('item_name', 'brand', 'model_number', 'serial_number', 'category', 'date_of_purchase',
                 'price', 'tax_amount', 'total_amount', 'payment_method', 'store_name', 'store_contact',
                 'warranty_info', 'return_policy', 'invoice_number')
_placeholder_marks = ', '.join('?' for _ in PLACEHOLDER_VALUES)
for _column in _TEXT_COLUMNS:
    cursor.execute(
        f'UPDATE appliances_v5 SET {_column} = NULL WHERE LOWER(TRIM({_column})) IN ({_placeholder_marks})',
        PLACEHOLDER_VALUES
    )

# "Has warranty card" means a card file is attached, not that the receipt mentions a warranty.
cursor.execute('''
    UPDATE appliances_v5
    SET has_warranty_card = CASE WHEN COALESCE(warranty_card_path, '') != '' THEN 1 ELSE 0 END
''')
conn.commit()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

_USER_COLUMNS = 'id, google_id, email, name, picture, role, is_active, created_at, last_login'


def _row_to_user(row):
    if not row:
        return None
    return {
        'id': row[0],
        'google_id': row[1],
        'email': row[2],
        'name': row[3],
        'picture': row[4],
        'is_active': bool(row[6]),
        'created_at': row[7],
        'last_login': row[8],
    }


def get_user_by_id(user_id):
    cursor.execute(f'SELECT {_USER_COLUMNS} FROM users WHERE id = ?', (user_id,))
    return _row_to_user(cursor.fetchone())


def get_user_by_email(email):
    if not email:
        return None
    cursor.execute(f'SELECT {_USER_COLUMNS} FROM users WHERE LOWER(email) = LOWER(?)', (email.strip(),))
    return _row_to_user(cursor.fetchone())


def upsert_user(google_id, email, name=None, picture=None):
    clean_email = email.strip().lower()
    user = get_user_by_email(clean_email)
    now = datetime.now().isoformat()
    if user:
        cursor.execute('''
            UPDATE users
            SET google_id = COALESCE(?, google_id),
                name = COALESCE(?, name),
                picture = COALESCE(?, picture),
                last_login = ?
            WHERE id = ?
        ''', (google_id, name, picture, now, user['id']))
        conn.commit()
        return get_user_by_id(user['id'])

    cursor.execute('''
        INSERT INTO users (google_id, email, name, picture, role, is_active, created_at, last_login)
        VALUES (?, ?, ?, ?, 'member', 1, ?, ?)
    ''', (google_id, clean_email, name or clean_email, picture or '', now, now))
    conn.commit()
    return get_user_by_id(cursor.lastrowid)


# ---------------------------------------------------------------------------
# Workspaces, members and invites
# ---------------------------------------------------------------------------

def create_workspace(name, owner_user_id):
    """Creates a workspace with owner_user_id as its owner and returns its id."""
    cursor.execute('SELECT COUNT(*) FROM workspaces')
    is_first_workspace = cursor.fetchone()[0] == 0

    cursor.execute(
        'INSERT INTO workspaces (name, created_by, created_at) VALUES (?, ?, ?)',
        (name.strip(), owner_user_id, datetime.now().isoformat())
    )
    workspace_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
        (workspace_id, owner_user_id, datetime.now().isoformat())
    )

    if is_first_workspace:
        # Data saved before workspaces existed belongs to the first workspace created on this install.
        _adopt_unassigned_data(workspace_id, owner_user_id)
    conn.commit()
    return workspace_id


def _adopt_unassigned_data(workspace_id, owner_user_id):
    cursor.execute('UPDATE appliances_v5 SET workspace_id = ? WHERE workspace_id IS NULL', (workspace_id,))
    cursor.execute(
        'UPDATE chat_messages SET workspace_id = ?, user_id = COALESCE(user_id, ?) WHERE workspace_id IS NULL',
        (workspace_id, owner_user_id)
    )
    cursor.execute('SELECT key, value FROM app_settings')
    for key, value in cursor.fetchall():
        cursor.execute(
            'INSERT OR IGNORE INTO workspace_settings (workspace_id, key, value) VALUES (?, ?, ?)',
            (workspace_id, key, value)
        )


def get_workspace(workspace_id):
    cursor.execute('SELECT id, name, created_by, created_at FROM workspaces WHERE id = ?', (workspace_id,))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1], 'created_by': row[2], 'created_at': row[3]} if row else None


def rename_workspace(workspace_id, name):
    cursor.execute('UPDATE workspaces SET name = ? WHERE id = ?', (name.strip(), workspace_id))
    conn.commit()
    return cursor.rowcount > 0


def get_user_workspaces(user_id):
    """Workspaces the user belongs to, oldest membership first."""
    cursor.execute('''
        SELECT w.id, w.name, m.role
        FROM workspace_members m
        JOIN workspaces w ON w.id = m.workspace_id
        WHERE m.user_id = ?
        ORDER BY m.joined_at ASC, w.id ASC
    ''', (user_id,))
    return [{'id': row[0], 'name': row[1], 'role': row[2]} for row in cursor.fetchall()]


def get_membership_role(workspace_id, user_id):
    cursor.execute(
        'SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?',
        (workspace_id, user_id)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def list_workspace_members(workspace_id):
    cursor.execute(f'''
        SELECT {', '.join('u.' + c.strip() for c in _USER_COLUMNS.split(','))}, m.role, m.joined_at
        FROM workspace_members m
        JOIN users u ON u.id = m.user_id
        WHERE m.workspace_id = ?
        ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, m.joined_at ASC
    ''', (workspace_id,))
    members = []
    for row in cursor.fetchall():
        user = _row_to_user(row[:9])
        user['role'] = row[9]
        user['joined_at'] = row[10]
        members.append(user)
    return members


def add_workspace_member(workspace_id, user_id, role='member'):
    cursor.execute(
        'INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)',
        (workspace_id, user_id, role, datetime.now().isoformat())
    )
    conn.commit()


def remove_workspace_member(workspace_id, user_id):
    role = get_membership_role(workspace_id, user_id)
    if role is None:
        return False, 'This person is not a member of the workspace'
    if role == 'owner':
        return False, 'The workspace owner cannot be removed'
    cursor.execute('DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?', (workspace_id, user_id))
    conn.commit()
    return True, 'Member removed'


def _hash_token(token):
    """Invite tokens are stored only as SHA-256 hashes, so a copy of the database cannot be used to join."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _new_token_and_expiry():
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(days=INVITE_LIFETIME_DAYS)).isoformat(timespec='seconds')
    return token, expires_at


def _is_expired(expires_at):
    # Invites created before links existed have no expiry date and stay valid until revoked.
    return bool(expires_at) and datetime.fromisoformat(expires_at) < datetime.now()


def create_invite(workspace_id, email, role, invited_by):
    """Invites an email address into a workspace. Returns (invite including its one-time token, error)."""
    clean_email = email.strip().lower()
    existing_user = get_user_by_email(clean_email)
    if existing_user and get_membership_role(workspace_id, existing_user['id']):
        return None, f'{clean_email} is already a member of this workspace'

    cursor.execute(
        'SELECT id, expires_at, accepted_at FROM workspace_invites WHERE workspace_id = ? AND email = ?',
        (workspace_id, clean_email)
    )
    existing = cursor.fetchone()
    if existing and not existing[2] and not _is_expired(existing[1]):
        return None, f'{clean_email} has already been invited. Use "New link" on their pending invite to share it again.'

    token, expires_at = _new_token_and_expiry()
    if existing:
        # Re-inviting after the old link expired (or after a past invite was used) refreshes that invite.
        cursor.execute(
            '''UPDATE workspace_invites SET role = ?, invited_by = ?, token_hash = ?, expires_at = ?, created_at = ?,
                   accepted_at = NULL, accepted_by = NULL WHERE id = ?''',
            (role, invited_by, _hash_token(token), expires_at, datetime.now().isoformat(), existing[0])
        )
        invite_id = existing[0]
    else:
        cursor.execute(
            '''INSERT INTO workspace_invites (workspace_id, email, role, invited_by, created_at, token_hash, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (workspace_id, clean_email, role, invited_by, datetime.now().isoformat(), _hash_token(token), expires_at)
        )
        invite_id = cursor.lastrowid
    conn.commit()
    return {'id': invite_id, 'email': clean_email, 'role': role, 'token': token, 'expires_at': expires_at}, None


def regenerate_invite_token(workspace_id, invite_id):
    """Issues a fresh link for a pending invite (the old link stops working). Returns (token, expires_at) or None."""
    token, expires_at = _new_token_and_expiry()
    cursor.execute(
        'UPDATE workspace_invites SET token_hash = ?, expires_at = ? WHERE id = ? AND workspace_id = ? AND accepted_at IS NULL',
        (_hash_token(token), expires_at, invite_id, workspace_id)
    )
    conn.commit()
    return (token, expires_at) if cursor.rowcount else None


def list_invites(workspace_id):
    cursor.execute(
        '''SELECT id, email, role, created_at, expires_at FROM workspace_invites
           WHERE workspace_id = ? AND accepted_at IS NULL ORDER BY created_at ASC''',
        (workspace_id,)
    )
    return [
        {'id': row[0], 'email': row[1], 'role': row[2], 'created_at': row[3],
         'expires_at': row[4], 'expired': _is_expired(row[4])}
        for row in cursor.fetchall()
    ]


def delete_invite(workspace_id, invite_id):
    cursor.execute('DELETE FROM workspace_invites WHERE id = ? AND workspace_id = ? AND accepted_at IS NULL',
                   (invite_id, workspace_id))
    conn.commit()
    return cursor.rowcount > 0


def get_invite_by_token(token):
    """Looks up an invite link. Returns details for the join page, or None if the link is unknown."""
    if not token or len(token) > 200:
        return None
    cursor.execute('''
        SELECT i.id, i.workspace_id, i.email, i.role, i.expires_at, w.name, u.name, u.email, i.accepted_at
        FROM workspace_invites i
        JOIN workspaces w ON w.id = i.workspace_id
        LEFT JOIN users u ON u.id = i.invited_by
        WHERE i.token_hash = ?
    ''', (_hash_token(token),))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'id': row[0], 'workspace_id': row[1], 'email': row[2], 'role': row[3],
        'expires_at': row[4], 'expired': _is_expired(row[4]),
        'workspace_name': row[5], 'invited_by_name': row[6] or row[7] or 'Someone',
        'accepted': bool(row[8]),
    }


def accept_invite_token(token, user):
    """Joins the user to the invite's workspace and uses up the link. Returns (workspace_id, error)."""
    invite = get_invite_by_token(token)
    if not invite:
        return None, 'This invite link is not valid. It may have been used already or revoked.'
    if invite['accepted']:
        # Opening your own used link again (e.g. after joining via email at sign-in) just takes you there.
        if get_membership_role(invite['workspace_id'], user['id']):
            return invite['workspace_id'], None
        return None, 'This invite link has already been used. Ask for a new one.'
    if get_membership_role(invite['workspace_id'], user['id']):
        # Already a member (e.g. the inviter testing their own link): open the workspace but leave the
        # invite unused, because it is meant for someone else.
        return invite['workspace_id'], None
    if invite['expired']:
        return None, 'This invite link has expired. Ask for a new one.'

    cursor.execute(
        'INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)',
        (invite['workspace_id'], user['id'], invite['role'], datetime.now().isoformat())
    )
    _mark_invite_used(invite['id'], user['id'])
    conn.commit()
    return invite['workspace_id'], None


def _mark_invite_used(invite_id, user_id):
    # Used invites are kept (not deleted) so their link can still recognise the person who used it.
    cursor.execute(
        'UPDATE workspace_invites SET accepted_at = ?, accepted_by = ? WHERE id = ?',
        (datetime.now().isoformat(timespec='seconds'), user_id, invite_id)
    )


def accept_pending_invites(user):
    """Turns unexpired invites for the user's email into memberships. Returns the workspace ids joined."""
    cursor.execute(
        'SELECT id, workspace_id, role, expires_at FROM workspace_invites WHERE email = ? AND accepted_at IS NULL',
        (user['email'].lower(),)
    )
    invites = [row for row in cursor.fetchall() if not _is_expired(row[3])]
    joined = []
    for invite_id, workspace_id, role, _expires_at in invites:
        cursor.execute(
            'INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)',
            (workspace_id, user['id'], role, datetime.now().isoformat())
        )
        _mark_invite_used(invite_id, user['id'])
        joined.append(workspace_id)
    if invites:
        conn.commit()
    return joined


def _migrate_existing_install():
    """One-time upgrade: turns a single-family install (users with global roles) into its first workspace."""
    cursor.execute('SELECT COUNT(*) FROM workspaces')
    if cursor.fetchone()[0]:
        return

    cursor.execute('SELECT id, email, name, role FROM users WHERE is_active = 1 ORDER BY id ASC')
    users = cursor.fetchall()
    if not users:
        return  # fresh install: the first person to sign in creates the first workspace

    owner_email = os.getenv('OWNER_EMAIL', '').strip().lower()
    owner = (next((u for u in users if u[1].lower() == owner_email), None)
             or next((u for u in users if u[3] == 'owner'), None)
             or users[0])
    first_name = (owner[2] or owner[1].split('@')[0]).split()[0]
    workspace_id = create_workspace(f"{first_name}'s Family", owner[0])

    for user_id, _email, _name, role in users:
        if user_id == owner[0]:
            continue
        member_role = 'admin' if role in ('owner', 'admin') else 'member'
        add_workspace_member(workspace_id, user_id, member_role)
    conn.commit()


_migrate_existing_install()


# ---------------------------------------------------------------------------
# Inventory (always scoped to a workspace)
# ---------------------------------------------------------------------------

def save_receipt_to_db(workspace_id, data):
    if "error" in data: return False
    has_warranty_card = 1 if data.get("warranty_card_path") else 0

    cursor.execute('''
        INSERT INTO appliances_v5 (
            item_name, brand, model_number, serial_number, category, date_of_purchase,
            price, tax_amount, total_amount, payment_method,
            store_name, store_contact, warranty_info, return_policy, invoice_number, file_path,
            has_warranty_card, warranty_card_path, workspace_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get("item_name"), data.get("brand"), data.get("model_number"), data.get("serial_number"),
        data.get("category"), data.get("date_of_purchase"), data.get("price"),
        data.get("tax_amount"), data.get("total_amount"), data.get("payment_method"),
        data.get("store_name"), data.get("store_contact"), data.get("warranty_info"),
        data.get("return_policy"), data.get("invoice_number"), data.get("file_path"),
        has_warranty_card, data.get("warranty_card_path"), workspace_id
    ))
    conn.commit()
    return True


def find_duplicate_receipt(workspace_id, invoice_number, store_name, item_name):
    """Returns the id of an item already saved from the same invoice in this workspace, or None."""
    if not invoice_number:
        return None
    cursor.execute(
        '''
        SELECT id FROM appliances_v5
        WHERE workspace_id = ?
          AND LOWER(TRIM(invoice_number)) = LOWER(TRIM(?))
          AND LOWER(TRIM(COALESCE(store_name, ''))) = LOWER(TRIM(COALESCE(?, '')))
          AND LOWER(TRIM(COALESCE(item_name, ''))) = LOWER(TRIM(COALESCE(?, '')))
        LIMIT 1
        ''',
        (workspace_id, invoice_number, store_name, item_name)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def get_all_appliances(workspace_id):
    cursor.execute('SELECT * FROM appliances_v5 WHERE workspace_id = ? ORDER BY id ASC', (workspace_id,))
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return columns, rows


def item_exists(workspace_id, item_id):
    cursor.execute('SELECT 1 FROM appliances_v5 WHERE id = ? AND workspace_id = ?', (item_id, workspace_id))
    return cursor.fetchone() is not None


def delete_appliance(workspace_id, item_id):
    """Deletes an item in this workspace. Returns (found, file_path, warranty_card_path)."""
    cursor.execute(
        'SELECT file_path, warranty_card_path FROM appliances_v5 WHERE id = ? AND workspace_id = ?',
        (item_id, workspace_id)
    )
    row = cursor.fetchone()
    if not row:
        return False, None, None
    cursor.execute('DELETE FROM appliances_v5 WHERE id = ? AND workspace_id = ?', (item_id, workspace_id))
    cursor.execute('DELETE FROM service_history WHERE item_id = ?', (item_id,))
    conn.commit()
    return True, row[0], row[1]


def get_appliance_file_paths(workspace_id, item_id):
    cursor.execute(
        'SELECT file_path, warranty_card_path FROM appliances_v5 WHERE id = ? AND workspace_id = ?',
        (item_id, workspace_id)
    )
    row = cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)


def relocate_upload_paths(old_root, new_root):
    """Rewrites stored file paths that point inside old_root so they point inside new_root."""
    old_prefix = old_root.replace('\\', '/').rstrip('/') + '/'
    new_prefix = new_root.replace('\\', '/').rstrip('/') + '/'

    def relocate(path):
        if not path:
            return path
        normalized = path.replace('\\', '/')
        return new_prefix + normalized[len(old_prefix):] if normalized.startswith(old_prefix) else path

    cursor.execute('SELECT id, file_path, warranty_card_path FROM appliances_v5')
    for item_id, file_path, warranty_card_path in cursor.fetchall():
        new_file_path = relocate(file_path)
        new_card_path = relocate(warranty_card_path)
        if (new_file_path, new_card_path) != (file_path, warranty_card_path):
            cursor.execute(
                'UPDATE appliances_v5 SET file_path = ?, warranty_card_path = ? WHERE id = ?',
                (new_file_path, new_card_path, item_id)
            )
    conn.commit()


def parse_amount(value):
    """Extracts a number from amounts like '₹1,299.00', 'Rs. 1,200' or 'INR 500'. Returns None if absent."""
    match = re.search(r'-?\d[\d,]*(?:\.\d+)?', str(value or ''))
    if not match:
        return None
    try:
        return float(match.group(0).replace(',', ''))
    except ValueError:
        return None


def update_warranty_card(workspace_id, item_id, warranty_card_path):
    cursor.execute(
        '''
        UPDATE appliances_v5
        SET has_warranty_card = 1,
            warranty_card_path = ?
        WHERE id = ? AND workspace_id = ?
        ''',
        (warranty_card_path, item_id, workspace_id)
    )
    conn.commit()
    return cursor.rowcount > 0


def get_inventory_analytics(workspace_id):
    columns, rows = get_all_appliances(workspace_id)
    items = [dict(zip(columns, row)) for row in rows]
    total_spent = 0.0
    active_warranties = 0

    try:
        warranty_period_days = max(1, int(get_setting_value(workspace_id, 'warranty_period_days', '365')))
    except (TypeError, ValueError):
        warranty_period_days = 365

    try:
        alert_window_days = max(0, int(get_setting_value(workspace_id, 'expiry_alert_days', '30')))
    except (TypeError, ValueError):
        alert_window_days = 30

    alerts = []

    for item in items:
        amount = parse_amount(item.get('total_amount'))
        if amount is not None:
            total_spent += amount

        try:
            p_date = datetime.strptime(item['date_of_purchase'], '%Y-%m-%d')
        except (TypeError, ValueError):
            item['warranty_status'] = "Unknown"
            item['warranty_expires_on'] = None
            item['days_remaining'] = None
            continue

        expiry_date = p_date + timedelta(days=warranty_period_days)
        days_remaining = (expiry_date.date() - datetime.now().date()).days
        item['warranty_expires_on'] = expiry_date.strftime('%Y-%m-%d')
        item['days_remaining'] = days_remaining

        if days_remaining >= 0 and days_remaining <= alert_window_days:
            # Still covered, so it counts as an active warranty too
            active_warranties += 1
            item['warranty_status'] = 'Expiring Soon'
            alerts.append({
                'id': item.get('id'),
                'item_name': item.get('item_name') or 'Item',
                'status': 'expiring_soon',
                'days_remaining': days_remaining,
                'expiry_date': item['warranty_expires_on'],
                'category': item.get('category') or 'N/A',
                'brand': item.get('brand') or 'N/A'
            })
        elif days_remaining < 0:
            item['warranty_status'] = 'Expired'
            # Only recently expired items need attention; older ones are no longer actionable.
            if -days_remaining > alert_window_days:
                continue
            alerts.append({
                'id': item.get('id'),
                'item_name': item.get('item_name') or 'Item',
                'status': 'expired',
                'days_remaining': days_remaining,
                'expiry_date': item['warranty_expires_on'],
                'category': item.get('category') or 'N/A',
                'brand': item.get('brand') or 'N/A'
            })
        else:
            active_warranties += 1
            item['warranty_status'] = "Active"

    return items, round(total_spent, 2), active_warranties, alerts


def add_service_history_entry(item_id, service_date, service_type, vendor='', cost='', notes='', next_service_date=''):
    """Callers must check the item belongs to their workspace first (item_exists)."""
    cursor.execute(
        '''
        INSERT INTO service_history (
            item_id, service_date, service_type, vendor, cost, notes, next_service_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (item_id, service_date, service_type, vendor, cost, notes, next_service_date)
    )
    conn.commit()
    return cursor.lastrowid


def get_service_history(item_id):
    """Callers must check the item belongs to their workspace first (item_exists)."""
    cursor.execute(
        '''
        SELECT id, item_id, service_date, service_type, vendor, cost, notes, next_service_date, created_at
        FROM service_history
        WHERE item_id = ?
        ORDER BY service_date DESC, id DESC
        ''',
        (item_id,)
    )
    rows = cursor.fetchall()
    return [
        {
            'id': row[0],
            'item_id': row[1],
            'service_date': row[2],
            'service_type': row[3],
            'vendor': row[4],
            'cost': row[5],
            'notes': row[6],
            'next_service_date': row[7],
            'created_at': row[8]
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Chat (private to each person within a workspace)
# ---------------------------------------------------------------------------

def _normalize_conversation_id(conversation_id):
    normalized = str(conversation_id or '').strip()
    return normalized if normalized else 'legacy'


def _rows_to_messages(rows):
    messages = [{'id': row[0], 'role': row[1], 'content': row[2], 'created_at': row[3]} for row in rows]
    messages.reverse()
    return messages


def save_chat_message(workspace_id, user_id, role, content, conversation_id='legacy'):
    if not role or content is None:
        return False

    cursor.execute(
        'INSERT INTO chat_messages (workspace_id, user_id, conversation_id, role, content) VALUES (?, ?, ?, ?, ?)',
        (workspace_id, user_id, _normalize_conversation_id(conversation_id), str(role), str(content))
    )
    conn.commit()
    return True


def get_chat_messages(workspace_id, user_id, conversation_id='legacy', limit=100):
    safe_limit = max(1, min(int(limit), 500))
    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE workspace_id = ? AND user_id = ? AND conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (workspace_id, user_id, _normalize_conversation_id(conversation_id), safe_limit)
    )
    return _rows_to_messages(cursor.fetchall())


def search_chat_messages(workspace_id, user_id, query, conversation_id='legacy', limit=120):
    safe_limit = max(1, min(int(limit), 500))
    query_text = (query or '').strip()
    if not query_text:
        return get_chat_messages(workspace_id, user_id, conversation_id=conversation_id, limit=safe_limit)

    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE workspace_id = ? AND user_id = ? AND conversation_id = ? AND content LIKE ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (workspace_id, user_id, _normalize_conversation_id(conversation_id), f"%{query_text}%", safe_limit)
    )
    return _rows_to_messages(cursor.fetchall())


def get_chat_conversations(workspace_id, user_id, query='', limit=100):
    safe_limit = max(1, min(int(limit), 500))
    query_text = (query or '').strip()
    like_query = f"%{query_text}%" if query_text else None

    cursor.execute(
        '''
        WITH mine AS (
            SELECT * FROM chat_messages WHERE workspace_id = ? AND user_id = ?
        ),
        conversation_summaries AS (
            SELECT conversation_id, MIN(created_at) AS created_at, MAX(created_at) AS updated_at, COUNT(*) AS message_count
            FROM mine
            GROUP BY conversation_id
        )
        SELECT
            c.conversation_id,
            c.created_at,
            c.updated_at,
            c.message_count,
            (SELECT content FROM mine m1 WHERE m1.conversation_id = c.conversation_id AND m1.role = 'user'
             ORDER BY m1.id ASC LIMIT 1) AS title,
            (SELECT content FROM mine m2 WHERE m2.conversation_id = c.conversation_id
             ORDER BY m2.id DESC LIMIT 1) AS preview
        FROM conversation_summaries c
        WHERE (? IS NULL OR EXISTS (
            SELECT 1 FROM mine m3 WHERE m3.conversation_id = c.conversation_id AND m3.content LIKE ?
        ))
        ORDER BY c.updated_at DESC
        LIMIT ?
        ''',
        (workspace_id, user_id, like_query, like_query, safe_limit)
    )

    return [
        {
            'conversation_id': row[0],
            'created_at': row[1],
            'updated_at': row[2],
            'message_count': row[3],
            'title': row[4],
            'preview': row[5]
        }
        for row in cursor.fetchall()
    ]


def clear_chat_messages(workspace_id, user_id):
    """Deletes this person's chat history in this workspace (never anyone else's)."""
    cursor.execute('DELETE FROM chat_messages WHERE workspace_id = ? AND user_id = ?', (workspace_id, user_id))
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Settings (per workspace)
# ---------------------------------------------------------------------------

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


def get_all_settings(workspace_id):
    result = dict(DEFAULT_SETTINGS)
    cursor.execute('SELECT key, value FROM workspace_settings WHERE workspace_id = ?', (workspace_id,))
    for key, value in cursor.fetchall():
        result[key] = value
    result['currency_symbol'] = CURRENCY_SYMBOLS.get(result.get('currency_code'), result.get('currency_symbol'))
    return result
