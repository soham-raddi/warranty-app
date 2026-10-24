import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

# DATABASE_URL (Postgres, e.g. Supabase) is used when set; otherwise a local SQLite file, for development.
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
IS_POSTGRES = DATABASE_URL.startswith(('postgres://', 'postgresql://'))
DB_PATH = os.getenv('DATABASE_PATH', 'inventory.db')
_local = threading.local()

if IS_POSTGRES:
    import psycopg

    DatabaseError = psycopg.Error
else:
    DatabaseError = sqlite3.Error


def _to_postgres(sql):
    """Translates the SQLite-flavoured SQL used in this module into Postgres."""
    sql = sql.replace('?', '%s').replace(' LIKE ', ' ILIKE ')
    if sql.lstrip().upper().startswith('INSERT OR IGNORE INTO'):
        sql = re.sub(r'INSERT OR IGNORE INTO', 'INSERT INTO', sql, count=1, flags=re.IGNORECASE)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return sql


class _Cursor:
    """A cursor that accepts this module's SQL on both SQLite and Postgres."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        if IS_POSTGRES:
            sql = _to_postgres(sql)
        self._raw.execute(sql, tuple(params))
        return self

    def __getattr__(self, name):
        if name == 'lastrowid' and IS_POSTGRES:
            raise AttributeError('Use insert_returning_id() instead of cursor.lastrowid')
        return getattr(self._raw, name)


def _open_connection():
    if IS_POSTGRES:
        # prepare_threshold=None: Supabase's connection pooler (transaction mode) can't use prepared statements.
        return psycopg.connect(DATABASE_URL, prepare_threshold=None, connect_timeout=10)
    return sqlite3.connect(DB_PATH, timeout=10)


def _thread_connection():
    """Each server thread gets its own connection, so concurrent requests never share a cursor."""
    conn_obj = getattr(_local, 'conn', None)
    if conn_obj is None or (IS_POSTGRES and conn_obj.closed):
        conn_obj = _open_connection()
        _local.conn = conn_obj
        _local.cursor = _Cursor(conn_obj.cursor())
    return conn_obj


class _ThreadLocalProxy:
    """Forwards attribute access to this thread's connection or cursor."""

    def __init__(self, attribute):
        self._attribute = attribute

    def __getattr__(self, name):
        _thread_connection()
        return getattr(getattr(_local, self._attribute), name)


conn = _ThreadLocalProxy('conn')
cursor = _ThreadLocalProxy('cursor')


def end_request():
    """Called after every request: ends any open transaction so pooled Postgres connections are released."""
    conn_obj = getattr(_local, 'conn', None)
    if conn_obj is None:
        return
    try:
        conn_obj.rollback()  # writes are committed explicitly; this only closes read transactions
    except DatabaseError:
        # A broken connection is replaced on the next request.
        try:
            conn_obj.close()
        except DatabaseError:
            pass
        _local.conn = None


def insert_returning_id(sql, params=()):
    """Runs an INSERT and returns the new row's id on either database."""
    if IS_POSTGRES:
        cursor.execute(sql.rstrip().rstrip(';') + ' RETURNING id', params)
        return cursor.fetchone()[0]
    cursor.execute(sql, params)
    return cursor.lastrowid


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

WORKSPACE_ROLES = ('owner', 'admin', 'member')
INVITE_LIFETIME_DAYS = 7


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _schema_statements():
    """CREATE statements for the current schema. Timestamps are stored as UTC text on both databases."""
    if IS_POSTGRES:
        pk = 'SERIAL PRIMARY KEY'
        now = "TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
    else:
        pk = 'INTEGER PRIMARY KEY AUTOINCREMENT'
        now = 'CURRENT_TIMESTAMP'
    return [
        f'''CREATE TABLE IF NOT EXISTS appliances_v5 (
            id {pk},
            item_name TEXT, brand TEXT, model_number TEXT, serial_number TEXT,
            category TEXT, date_of_purchase TEXT, price TEXT, tax_amount TEXT,
            total_amount TEXT, payment_method TEXT, store_name TEXT,
            store_contact TEXT, warranty_info TEXT, return_policy TEXT,
            invoice_number TEXT, file_path TEXT,
            has_warranty_card INTEGER DEFAULT 0,
            warranty_card_path TEXT,
            workspace_id INTEGER,
            warranty_months INTEGER,
            currency TEXT
        )''',
        f'''CREATE TABLE IF NOT EXISTS chat_messages (
            id {pk},
            conversation_id TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({now}),
            workspace_id INTEGER,
            user_id INTEGER
        )''',
        f'''CREATE TABLE IF NOT EXISTS service_history (
            id {pk},
            item_id INTEGER NOT NULL,
            service_date TEXT NOT NULL,
            service_type TEXT NOT NULL,
            vendor TEXT,
            cost TEXT,
            notes TEXT,
            next_service_date TEXT,
            created_at TEXT NOT NULL DEFAULT ({now})
        )''',
        # Settings from before workspaces existed; kept so older versions of the app still work.
        '''CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''',
        f'''CREATE TABLE IF NOT EXISTS users (
            id {pk},
            google_id TEXT UNIQUE,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture TEXT,
            role TEXT NOT NULL DEFAULT 'owner',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT ({now}),
            last_login TEXT NOT NULL DEFAULT ({now})
        )''',
        # A workspace is one family's private space: its own items, settings, members and chats.
        f'''CREATE TABLE IF NOT EXISTS workspaces (
            id {pk},
            name TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT ({now})
        )''',
        f'''CREATE TABLE IF NOT EXISTS workspace_members (
            workspace_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL DEFAULT ({now}),
            PRIMARY KEY (workspace_id, user_id)
        )''',
        f'''CREATE TABLE IF NOT EXISTS workspace_invites (
            id {pk},
            workspace_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            invited_by INTEGER,
            created_at TEXT NOT NULL DEFAULT ({now}),
            token_hash TEXT,
            expires_at TEXT,
            accepted_at TEXT,
            accepted_by INTEGER,
            UNIQUE (workspace_id, email)
        )''',
        '''CREATE TABLE IF NOT EXISTS workspace_settings (
            workspace_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (workspace_id, key)
        )''',
        # Fixed-window counters for rate limits (shared by every server instance).
        '''CREATE TABLE IF NOT EXISTS rate_limits (
            bucket TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bucket, window_start)
        )''',
        # Which warranty reminder (e.g. "30 days before") was already emailed for which item.
        f'''CREATE TABLE IF NOT EXISTS reminder_log (
            item_id INTEGER NOT NULL,
            threshold_days INTEGER NOT NULL,
            sent_at TEXT NOT NULL DEFAULT ({now}),
            PRIMARY KEY (item_id, threshold_days)
        )''',
        # People who unsubscribed from a workspace's reminder emails.
        f'''CREATE TABLE IF NOT EXISTS email_optouts (
            workspace_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT ({now}),
            PRIMARY KEY (workspace_id, email)
        )''',
        # Purchases read from forwarded order emails, waiting for someone to review and save them.
        f'''CREATE TABLE IF NOT EXISTS pending_receipts (
            id {pk},
            workspace_id INTEGER NOT NULL,
            source_email_id TEXT NOT NULL UNIQUE,
            sender TEXT,
            subject TEXT,
            details TEXT NOT NULL,
            file_path TEXT,
            created_at TEXT NOT NULL DEFAULT ({now})
        )''',
        # Every email already imported, so a delivery Resend retries is never imported twice.
        f'''CREATE TABLE IF NOT EXISTS processed_emails (
            email_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT ({now})
        )''',
        '''CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''',
    ]


_INDEXES = [
    'CREATE INDEX IF NOT EXISTS idx_service_history_item_id ON service_history(item_id)',
    'CREATE INDEX IF NOT EXISTS idx_appliances_workspace ON appliances_v5(workspace_id)',
    'CREATE INDEX IF NOT EXISTS idx_chat_owner ON chat_messages(workspace_id, user_id, conversation_id)',
    'CREATE INDEX IF NOT EXISTS idx_members_user ON workspace_members(user_id)',
    'CREATE INDEX IF NOT EXISTS idx_invites_email ON workspace_invites(email)',
    'CREATE UNIQUE INDEX IF NOT EXISTS idx_invites_token ON workspace_invites(token_hash) WHERE token_hash IS NOT NULL',
]


def _add_sqlite_column(table, column_sql):
    try:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column_sql}')
    except sqlite3.OperationalError:
        pass  # column already exists


# Columns added after their table was first created. When adding one here, also add it to the CREATE TABLE
# above and bump SCHEMA_VERSION so existing databases (including the live Postgres one) get it on next start.
_ADDED_COLUMNS = [
    ('chat_messages', "conversation_id TEXT NOT NULL DEFAULT ''"),
    ('appliances_v5', 'has_warranty_card INTEGER DEFAULT 0'),
    ('appliances_v5', 'warranty_card_path TEXT'),
    ('appliances_v5', 'workspace_id INTEGER'),
    ('appliances_v5', 'warranty_months INTEGER'),
    ('chat_messages', 'workspace_id INTEGER'),
    ('chat_messages', 'user_id INTEGER'),
    ('workspace_invites', 'token_hash TEXT'),
    ('workspace_invites', 'expires_at TEXT'),
    ('workspace_invites', 'accepted_at TEXT'),
    ('workspace_invites', 'accepted_by INTEGER'),
    ('appliances_v5', 'currency TEXT'),
]
SCHEMA_VERSION = '5'


def _upgrade_old_sqlite_install():
    """Brings SQLite databases created by earlier versions of the app up to the current schema."""
    for table, column in _ADDED_COLUMNS:
        _add_sqlite_column(table, column)

    cursor.execute("UPDATE chat_messages SET conversation_id = 'legacy' WHERE conversation_id IS NULL OR conversation_id = ''")

    # Earlier extractions stored placeholders such as "not found"; store them as empty instead.
    marks = ', '.join('?' for _ in PLACEHOLDER_VALUES)
    for column in _TEXT_COLUMNS:
        cursor.execute(
            f'UPDATE appliances_v5 SET {column} = NULL WHERE LOWER(TRIM({column})) IN ({marks})',
            PLACEHOLDER_VALUES
        )

    # "Has warranty card" means a card file is attached, not that the receipt mentions a warranty.
    cursor.execute('''
        UPDATE appliances_v5
        SET has_warranty_card = CASE WHEN COALESCE(warranty_card_path, '') != '' THEN 1 ELSE 0 END
    ''')


PLACEHOLDER_VALUES = ('', 'n/a', 'na', 'none', 'null', 'unknown', 'not found', 'not available',
                      'not mentioned', 'not specified', 'not specified on receipt', '-')
_TEXT_COLUMNS = ('item_name', 'brand', 'model_number', 'serial_number', 'category', 'date_of_purchase',
                 'price', 'tax_amount', 'total_amount', 'payment_method', 'store_name', 'store_contact',
                 'warranty_info', 'return_policy', 'invoice_number')


def _postgres_schema_is_current():
    """One quick query instead of a dozen schema statements on every serverless cold start."""
    cursor.execute("SELECT to_regclass('schema_meta') IS NOT NULL")
    current = False
    if cursor.fetchone()[0]:
        cursor.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        current = bool(row) and row[0] == SCHEMA_VERSION
    conn.rollback()
    return current


def init_schema():
    if IS_POSTGRES and _postgres_schema_is_current():
        return
    if not IS_POSTGRES:
        # WAL lets readers proceed while another request is writing.
        _thread_connection().execute('PRAGMA journal_mode=WAL')
    for statement in _schema_statements():
        cursor.execute(statement)
    if IS_POSTGRES:
        for table, column in _ADDED_COLUMNS:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}')
    else:
        _upgrade_old_sqlite_install()
    for statement in _INDEXES:
        cursor.execute(statement)
    cursor.execute(
        '''INSERT INTO schema_meta (key, value) VALUES ('version', ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value''',
        (SCHEMA_VERSION,)
    )
    conn.commit()


init_schema()


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

    user_id = insert_returning_id('''
        INSERT INTO users (google_id, email, name, picture, role, is_active, created_at, last_login)
        VALUES (?, ?, ?, ?, 'member', 1, ?, ?)
    ''', (google_id, clean_email, name or clean_email, picture or '', now, now))
    conn.commit()
    return get_user_by_id(user_id)


# ---------------------------------------------------------------------------
# Workspaces, members and invites
# ---------------------------------------------------------------------------

def create_workspace(name, owner_user_id):
    """Creates a workspace with owner_user_id as its owner and returns its id."""
    cursor.execute('SELECT COUNT(*) FROM workspaces')
    is_first_workspace = cursor.fetchone()[0] == 0

    workspace_id = insert_returning_id(
        'INSERT INTO workspaces (name, created_by, created_at) VALUES (?, ?, ?)',
        (name.strip(), owner_user_id, datetime.now().isoformat())
    )
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
        invite_id = insert_returning_id(
            '''INSERT INTO workspace_invites (workspace_id, email, role, invited_by, created_at, token_hash, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (workspace_id, clean_email, role, invited_by, datetime.now().isoformat(), _hash_token(token), expires_at)
        )
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
            has_warranty_card, warranty_card_path, workspace_id, warranty_months, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get("item_name"), data.get("brand"), data.get("model_number"), data.get("serial_number"),
        data.get("category"), data.get("date_of_purchase"), data.get("price"),
        data.get("tax_amount"), data.get("total_amount"), data.get("payment_method"),
        data.get("store_name"), data.get("store_contact"), data.get("warranty_info"),
        data.get("return_policy"), data.get("invoice_number"), data.get("file_path"),
        has_warranty_card, data.get("warranty_card_path"), workspace_id, data.get("warranty_months"), data.get("currency")
    ))
    conn.commit()
    return True



def _add_months(start, months):
    """Same day N calendar months later (clamped to the month's last day, e.g. Jan 31 + 1 month = Feb 28/29)."""
    month_index = start.month - 1 + int(months)
    year, month = start.year + month_index // 12, month_index % 12 + 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return start.replace(year=year, month=month, day=min(start.day, days_in_month))


def get_appliance(workspace_id, item_id):
    cursor.execute('SELECT * FROM appliances_v5 WHERE id = ? AND workspace_id = ?', (item_id, workspace_id))
    row = cursor.fetchone()
    if not row:
        return None
    item = dict(zip([d[0] for d in cursor.description], row))
    item.pop('file_path', None)
    item.pop('warranty_card_path', None)
    return item


_UPDATABLE_COLUMNS = {
    'item_name', 'brand', 'model_number', 'serial_number', 'category', 'date_of_purchase', 'total_amount',
    'store_name', 'store_contact', 'invoice_number', 'warranty_info', 'return_policy', 'warranty_months', 'currency',
}


def update_appliance(workspace_id, item_id, changes):
    """Applies validated edits to an item in this workspace."""
    columns = [column for column in changes if column in _UPDATABLE_COLUMNS]
    if not columns:
        return False
    assignments = ', '.join(f'{column} = ?' for column in columns)
    cursor.execute(
        f'UPDATE appliances_v5 SET {assignments} WHERE id = ? AND workspace_id = ?',
        [changes[column] for column in columns] + [item_id, workspace_id]
    )
    conn.commit()
    return cursor.rowcount > 0


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
    cursor.execute('DELETE FROM reminder_log WHERE item_id = ?', (item_id,))
    conn.commit()
    return True, (None if _receipt_file_in_use(row[0]) else row[0]), row[1]


def _receipt_file_in_use(file_path):
    """True when another item saved from the same receipt still uses this file."""
    if not file_path:
        return False
    cursor.execute('SELECT 1 FROM appliances_v5 WHERE file_path = ? LIMIT 1', (file_path,))
    return cursor.fetchone() is not None


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
    home_currency = get_setting_value(workspace_id, 'currency_code', 'INR')
    rates = get_exchange_rates()

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
        currency = (item.get('currency') or home_currency).upper()
        item['currency'] = currency
        # What the item cost in the workspace's currency; None when no exchange rate is known yet.
        item['home_amount'] = convert_amount(amount, currency, home_currency, rates) if amount is not None else None
        if item['home_amount'] is not None:
            total_spent += item['home_amount']

        try:
            p_date = datetime.strptime(item['date_of_purchase'], '%Y-%m-%d')
        except (TypeError, ValueError):
            item['warranty_status'] = "Unknown"
            item['warranty_expires_on'] = None
            item['days_remaining'] = None
            continue

        months = item.get('warranty_months')
        expiry_date = _add_months(p_date, months) if months else p_date + timedelta(days=warranty_period_days)
        item['warranty_source'] = 'item' if months else 'default'
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
    entry_id = insert_returning_id(
        '''
        INSERT INTO service_history (
            item_id, service_date, service_type, vendor, cost, notes, next_service_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (item_id, service_date, service_type, vendor, cost, notes, next_service_date)
    )
    conn.commit()
    return entry_id


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
    search_filter = ''
    params = [workspace_id, user_id]
    if query_text:
        search_filter = '''WHERE EXISTS (
            SELECT 1 FROM mine m3 WHERE m3.conversation_id = c.conversation_id AND m3.content LIKE ?
        )'''
        params.append(f"%{query_text}%")
    params.append(safe_limit)

    cursor.execute(
        f'''
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
        {search_filter}
        ORDER BY c.updated_at DESC
        LIMIT ?
        ''',
        params
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


# ---------------------------------------------------------------------------
# Rate limits (stored in the database so every server instance shares them)
# ---------------------------------------------------------------------------

def hit_rate_limit(bucket, window_seconds):
    """Counts one hit for bucket in the current time window and returns the total so far."""
    window_start = int(datetime.now().timestamp()) // window_seconds * window_seconds
    cursor.execute(
        '''
        INSERT INTO rate_limits (bucket, window_start, hits) VALUES (?, ?, 1)
        ON CONFLICT (bucket, window_start) DO UPDATE SET hits = rate_limits.hits + 1
        RETURNING hits
        ''',
        (bucket, window_start)
    )
    hits = cursor.fetchone()[0]
    conn.commit()
    return hits


def prune_rate_limits(older_than_seconds=2 * 86400):
    cutoff = int(datetime.now().timestamp()) - older_than_seconds
    cursor.execute('DELETE FROM rate_limits WHERE window_start < ?', (cutoff,))
    conn.commit()
    return cursor.rowcount


def prune_old_invites(days=30):
    """Removes invites that expired or were used more than `days` ago."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec='seconds')
    cursor.execute(
        'DELETE FROM workspace_invites WHERE (expires_at IS NOT NULL AND expires_at < ?) OR (accepted_at IS NOT NULL AND accepted_at < ?)',
        (cutoff, cutoff)
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Ownership, deleting workspaces and accounts, export
# ---------------------------------------------------------------------------

def transfer_ownership(workspace_id, from_user_id, to_user_id):
    """Makes another member the owner; the previous owner becomes an admin. Returns an error or None."""
    if get_membership_role(workspace_id, from_user_id) != 'owner':
        return 'Only the owner can transfer ownership'
    if to_user_id == from_user_id:
        return 'You are already the owner'
    if not get_membership_role(workspace_id, to_user_id):
        return 'That person is not a member of this workspace'
    cursor.execute("UPDATE workspace_members SET role = 'admin' WHERE workspace_id = ? AND user_id = ?",
                   (workspace_id, from_user_id))
    cursor.execute("UPDATE workspace_members SET role = 'owner' WHERE workspace_id = ? AND user_id = ?",
                   (workspace_id, to_user_id))
    conn.commit()
    return None


def delete_workspace(workspace_id):
    """Deletes a workspace and everything in it. Returns the stored file references so callers can remove them."""
    cursor.execute('SELECT id, file_path, warranty_card_path FROM appliances_v5 WHERE workspace_id = ?', (workspace_id,))
    rows = cursor.fetchall()
    files = list(dict.fromkeys(ref for _id, file_path, card_path in rows for ref in (file_path, card_path) if ref))
    item_ids = [row[0] for row in rows]
    if item_ids:
        marks = ', '.join('?' for _ in item_ids)
        cursor.execute(f'DELETE FROM service_history WHERE item_id IN ({marks})', item_ids)
        cursor.execute(f'DELETE FROM reminder_log WHERE item_id IN ({marks})', item_ids)
    cursor.execute('SELECT file_path FROM pending_receipts WHERE workspace_id = ? AND file_path IS NOT NULL', (workspace_id,))
    files += [row[0] for row in cursor.fetchall()]
    for table in ('appliances_v5', 'chat_messages', 'workspace_settings', 'workspace_invites', 'workspace_members',
                  'email_optouts', 'pending_receipts'):
        cursor.execute(f'DELETE FROM {table} WHERE workspace_id = ?', (workspace_id,))
    cursor.execute('DELETE FROM workspaces WHERE id = ?', (workspace_id,))
    conn.commit()
    return files


def owned_workspaces_with_other_members(user_id):
    cursor.execute('''
        SELECT w.id, w.name FROM workspaces w
        JOIN workspace_members m ON m.workspace_id = w.id AND m.user_id = ? AND m.role = 'owner'
        WHERE EXISTS (SELECT 1 FROM workspace_members o WHERE o.workspace_id = w.id AND o.user_id != ?)
    ''', (user_id, user_id))
    return [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]


def delete_user_account(user_id):
    """Deletes an account: workspaces they own alone, their memberships, their chats and the user.

    Callers must first make sure the user owns no workspace that other people still use.
    Returns the stored file references of deleted workspaces.
    """
    files = []
    for workspace in get_user_workspaces(user_id):
        if workspace['role'] == 'owner':
            files += delete_workspace(workspace['id'])
    cursor.execute('DELETE FROM chat_messages WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM workspace_members WHERE user_id = ?', (user_id,))
    cursor.execute('UPDATE workspace_invites SET invited_by = NULL WHERE invited_by = ?', (user_id,))
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    return files


def export_workspace_rows(workspace_id):
    """Items and service history of a workspace, for the CSV export."""
    items, _total, _active, _alerts = get_inventory_analytics(workspace_id)
    cursor.execute('''
        SELECT s.item_id, a.item_name, s.service_date, s.service_type, s.vendor, s.cost, s.notes, s.next_service_date
        FROM service_history s JOIN appliances_v5 a ON a.id = s.item_id
        WHERE a.workspace_id = ?
        ORDER BY s.item_id, s.service_date
    ''', (workspace_id,))
    history = [dict(zip(['item_id', 'item_name', 'service_date', 'service_type', 'vendor', 'cost', 'notes',
                         'next_service_date'], row)) for row in cursor.fetchall()]
    return items, history


# ---------------------------------------------------------------------------
# Warranty cards and email reminders
# ---------------------------------------------------------------------------

def apply_warranty_card_details(workspace_id, item_id, months=None, start_date=None, notes=None):
    """Stores what was read from a warranty card. The card's warranty length replaces the default period;
    its start date is used only when the receipt had no purchase date. Returns the fields that changed."""
    item = get_appliance(workspace_id, item_id)
    if not item:
        return {}
    changes = {}
    if months:
        changes['warranty_months'] = int(months)
    if start_date and not item.get('date_of_purchase'):
        changes['date_of_purchase'] = start_date
    if notes:
        changes['warranty_info'] = notes[:500]
    if changes:
        update_appliance(workspace_id, item_id, changes)
    return changes


def workspace_member_emails(workspace_id):
    cursor.execute('''
        SELECT u.email FROM workspace_members m JOIN users u ON u.id = m.user_id
        WHERE m.workspace_id = ? AND u.is_active = 1
        ORDER BY m.joined_at
    ''', (workspace_id,))
    return [row[0] for row in cursor.fetchall()]


def workspaces_with_reminders_enabled():
    cursor.execute(
        "SELECT workspace_id FROM workspace_settings WHERE key = 'reminders_enabled' AND value = '1' ORDER BY workspace_id"
    )
    return [row[0] for row in cursor.fetchall()]


def reminder_already_sent(item_id, threshold_days):
    cursor.execute('SELECT 1 FROM reminder_log WHERE item_id = ? AND threshold_days = ?', (item_id, threshold_days))
    return cursor.fetchone() is not None


def record_reminders_sent(item_thresholds):
    """item_thresholds: (item_id, threshold_days) pairs that were just emailed."""
    now = datetime.now().isoformat(timespec='seconds')
    for item_id, threshold_days in item_thresholds:
        cursor.execute(
            'INSERT OR IGNORE INTO reminder_log (item_id, threshold_days, sent_at) VALUES (?, ?, ?)',
            (item_id, threshold_days, now)
        )
    conn.commit()


def opted_out_emails(workspace_id):
    cursor.execute('SELECT email FROM email_optouts WHERE workspace_id = ?', (workspace_id,))
    return {row[0] for row in cursor.fetchall()}


def set_reminder_optout(workspace_id, email, opted_out):
    """Unsubscribes (or resubscribes) one email address from a workspace's reminder emails."""
    clean_email = email.strip().lower()
    if opted_out:
        cursor.execute(
            'INSERT OR IGNORE INTO email_optouts (workspace_id, email, created_at) VALUES (?, ?, ?)',
            (workspace_id, clean_email, datetime.now().isoformat(timespec='seconds'))
        )
    else:
        cursor.execute('DELETE FROM email_optouts WHERE workspace_id = ? AND email = ?', (workspace_id, clean_email))
    conn.commit()


# ---------------------------------------------------------------------------
# Purchases imported from forwarded emails
# ---------------------------------------------------------------------------

def workspace_by_inbound_token(token):
    cursor.execute("SELECT workspace_id FROM workspace_settings WHERE key = 'inbound_token' AND value = ?", (token,))
    row = cursor.fetchone()
    return row[0] if row else None


def pending_receipt_exists(source_email_id):
    cursor.execute('SELECT 1 FROM processed_emails WHERE email_id = ?', (source_email_id,))
    return cursor.fetchone() is not None


def add_pending_receipt(workspace_id, source_email_id, sender, subject, details, file_path):
    """Stores a purchase read from an email. Returns its id, or None if that email was already imported."""
    if pending_receipt_exists(source_email_id):
        return None
    cursor.execute('INSERT OR IGNORE INTO processed_emails (email_id, created_at) VALUES (?, ?)',
                   (source_email_id, datetime.now().isoformat(timespec='seconds')))
    pending_id = insert_returning_id(
        '''INSERT INTO pending_receipts (workspace_id, source_email_id, sender, subject, details, file_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (workspace_id, source_email_id, sender, (subject or '')[:300], json.dumps(details), file_path,
         datetime.now().isoformat(timespec='seconds'))
    )
    conn.commit()
    return pending_id


def list_pending_receipts(workspace_id):
    cursor.execute(
        '''SELECT id, sender, subject, details, created_at FROM pending_receipts
           WHERE workspace_id = ? ORDER BY created_at DESC''',
        (workspace_id,)
    )
    return [{'id': row[0], 'sender': row[1], 'subject': row[2], 'created_at': row[4], **json.loads(row[3])}
            for row in cursor.fetchall()]


def get_pending_receipt(workspace_id, pending_id):
    cursor.execute(
        'SELECT id, details, file_path FROM pending_receipts WHERE id = ? AND workspace_id = ?',
        (pending_id, workspace_id)
    )
    row = cursor.fetchone()
    return {'id': row[0], 'details': json.loads(row[1]), 'file_path': row[2]} if row else None


def delete_pending_receipt(workspace_id, pending_id):
    """Removes a pending purchase. Returns its stored file reference (so the caller can delete it), or False."""
    cursor.execute('SELECT file_path FROM pending_receipts WHERE id = ? AND workspace_id = ?', (pending_id, workspace_id))
    row = cursor.fetchone()
    if not row:
        return False
    cursor.execute('DELETE FROM pending_receipts WHERE id = ? AND workspace_id = ?', (pending_id, workspace_id))
    conn.commit()
    return row[0]


def prune_old_pending_receipts(days=30):
    """Removes purchases nobody reviewed for `days` days. Returns their file references."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec='seconds')
    cursor.execute('SELECT file_path FROM pending_receipts WHERE created_at < ?', (cutoff,))
    files = [row[0] for row in cursor.fetchall() if row[0]]
    cursor.execute('DELETE FROM pending_receipts WHERE created_at < ?', (cutoff,))
    cursor.execute('DELETE FROM processed_emails WHERE created_at < ?', (cutoff,))
    conn.commit()
    return files


# ---------------------------------------------------------------------------
# Exchange rates (refreshed daily by fx.py; stored so pages never wait on the rate service)
# ---------------------------------------------------------------------------

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
