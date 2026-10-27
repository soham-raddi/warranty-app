"""Creating the tables and upgrading databases made by older versions of the app."""
import os
import sqlite3

from .connection import _thread_connection, conn, cursor, IS_POSTGRES
from .workspaces import add_workspace_member, create_workspace


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
            created_at TEXT NOT NULL DEFAULT ({now}),
            currency TEXT
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
        # Each person's own look (theme, accent, spacing, motion), whichever family they're viewing.
        '''CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )''',
        # Each day's exchange rates (units per 1 EUR), fetched once, so items convert at their purchase-date rate.
        '''CREATE TABLE IF NOT EXISTS historical_rates (
            rate_date TEXT PRIMARY KEY,
            rates TEXT NOT NULL
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
    ('service_history', 'currency TEXT'),
]


SCHEMA_VERSION = '9'


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
    _give_items_a_currency()
    cursor.execute(
        '''INSERT INTO schema_meta (key, value) VALUES ('version', ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value''',
        (SCHEMA_VERSION,)
    )
    conn.commit()


def _give_items_a_currency(workspace_id=None, currency=None):
    """Items saved before currencies were tracked get the workspace currency they were entered in, so changing the
    workspace currency later converts them instead of relabelling them."""
    if workspace_id is not None:
        cursor.execute(
            "UPDATE appliances_v5 SET currency = ? WHERE workspace_id = ? AND (currency IS NULL OR currency = '')",
            (currency, workspace_id)
        )
        cursor.execute(
            '''UPDATE service_history SET currency = ? WHERE (currency IS NULL OR currency = '')
               AND item_id IN (SELECT id FROM appliances_v5 WHERE workspace_id = ?)''',
            (currency, workspace_id)
        )
        return
    cursor.execute('''
        UPDATE appliances_v5 SET currency = COALESCE(
            (SELECT value FROM workspace_settings s WHERE s.workspace_id = appliances_v5.workspace_id AND s.key = 'currency_code'),
            'INR')
        WHERE currency IS NULL OR currency = ''
    ''')
    cursor.execute('''
        UPDATE service_history SET currency = COALESCE(
            (SELECT s.value FROM appliances_v5 a JOIN workspace_settings s ON s.workspace_id = a.workspace_id
             WHERE a.id = service_history.item_id AND s.key = 'currency_code'),
            'INR')
        WHERE currency IS NULL OR currency = ''
    ''')


def pin_item_currencies(workspace_id, currency):
    _give_items_a_currency(workspace_id, currency)
    conn.commit()


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
