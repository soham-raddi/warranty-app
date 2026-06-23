import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('inventory.db', check_same_thread=False)
cursor = conn.cursor()

DEFAULT_SETTINGS = {
    'timezone': 'UTC',
    'currency_symbol': 'Rs',
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
conn.commit()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
''')
conn.commit()

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
conn.commit()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
''')
conn.commit()

for _key, _value in DEFAULT_SETTINGS.items():
    cursor.execute(
        'INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)',
        (_key, _value)
    )
conn.commit()

try:
    cursor.execute('ALTER TABLE chat_messages ADD COLUMN conversation_id TEXT NOT NULL DEFAULT ""')
except sqlite3.OperationalError:
    pass

cursor.execute("UPDATE chat_messages SET conversation_id = 'legacy' WHERE conversation_id IS NULL OR conversation_id = ''")
conn.commit()

# Lightweight migration for existing inventories created before warranty-card columns were introduced.
try:
    cursor.execute('ALTER TABLE appliances_v5 ADD COLUMN has_warranty_card INTEGER DEFAULT 0')
except sqlite3.OperationalError:
    pass

try:
    cursor.execute('ALTER TABLE appliances_v5 ADD COLUMN warranty_card_path TEXT')
except sqlite3.OperationalError:
    pass

conn.commit()

try:
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_service_history_item_id ON service_history(item_id)')
except sqlite3.OperationalError:
    pass

conn.commit()


def _has_warranty_card(data):
    warranty_text = str(data.get("warranty_info") or "").strip().lower()
    unknown_markers = {
        "",
        "n/a",
        "na",
        "none",
        "unknown",
        "not available",
        "not mentioned",
        "not specified",
        "not specified on receipt"
    }
    return 0 if warranty_text in unknown_markers else 1

def save_receipt_to_db(data):
    if "error" in data: return False
    has_warranty_card = _has_warranty_card(data)

    cursor.execute('''
        INSERT INTO appliances_v5 (
            item_name, brand, model_number, serial_number, category, date_of_purchase, 
            price, tax_amount, total_amount, payment_method, 
            store_name, store_contact, warranty_info, return_policy, invoice_number, file_path,
            has_warranty_card, warranty_card_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get("item_name"), data.get("brand"), data.get("model_number"), data.get("serial_number"),
        data.get("category"), data.get("date_of_purchase"), data.get("price"),
        data.get("tax_amount"), data.get("total_amount"), data.get("payment_method"),
        data.get("store_name"), data.get("store_contact"), data.get("warranty_info"), 
        data.get("return_policy"), data.get("invoice_number"), data.get("file_path"),
        has_warranty_card, data.get("warranty_card_path")
    ))
    conn.commit()
    return True

def get_all_appliances():
    cursor.execute('SELECT * FROM appliances_v5')
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return columns, rows

def delete_appliance(item_id):
    cursor.execute('SELECT file_path, warranty_card_path FROM appliances_v5 WHERE id = ?', (item_id,))
    row = cursor.fetchone()
    file_path = row[0] if row else None
    warranty_card_path = row[1] if row else None
    cursor.execute('DELETE FROM appliances_v5 WHERE id = ?', (item_id,))
    conn.commit()
    return file_path, warranty_card_path


def update_warranty_card(item_id, warranty_card_path):
    cursor.execute(
        '''
        UPDATE appliances_v5
        SET has_warranty_card = 1,
            warranty_card_path = ?
        WHERE id = ?
        ''',
        (warranty_card_path, item_id)
    )
    conn.commit()
    return cursor.rowcount > 0

def get_inventory_analytics():
    columns, rows = get_all_appliances()
    items = [dict(zip(columns, row)) for row in rows]
    total_spent = 0.0
    active_warranties = 0
    warranty_period_days = 365
    alert_window_days = 30

    try:
        warranty_period_days = max(1, int(get_setting_value('warranty_period_days', '365')))
    except Exception:
        warranty_period_days = 365

    try:
        alert_window_days = max(0, int(get_setting_value('expiry_alert_days', '30')))
    except Exception:
        alert_window_days = 30

    alerts = []
    
    for item in items:
        try:
            val = str(item['total_amount']).replace('₹', '').replace('$', '').replace(',', '').strip()
            total_spent += float(val)
        except: pass
            
        try:
            p_date = datetime.strptime(item['date_of_purchase'], '%Y-%m-%d')
            expiry_date = p_date + timedelta(days=warranty_period_days)
            days_remaining = (expiry_date.date() - datetime.now().date()).days
            item['warranty_expires_on'] = expiry_date.strftime('%Y-%m-%d')
            item['days_remaining'] = days_remaining

            if days_remaining >= 0 and days_remaining <= alert_window_days:
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
                alerts.append({
                    'id': item.get('id'),
                    'item_name': item.get('item_name') or 'Item',
                    'status': 'expired',
                    'days_remaining': days_remaining,
                    'expiry_date': item['warranty_expires_on'],
                    'category': item.get('category') or 'N/A',
                    'brand': item.get('brand') or 'N/A'
                })
            elif (datetime.now() - p_date).days < warranty_period_days:
                active_warranties += 1
                item['warranty_status'] = "Active"
            else:
                item['warranty_status'] = "Expired"
        except:
            item['warranty_status'] = "Unknown"
            item['warranty_expires_on'] = None
            item['days_remaining'] = None
            
    return items, round(total_spent, 2), active_warranties, alerts


def add_service_history_entry(item_id, service_date, service_type, vendor='', cost='', notes='', next_service_date=''):
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


def get_service_history_summary(item_id):
    history = get_service_history(item_id)
    last_service = history[0] if history else None
    return {
        'item_id': item_id,
        'entries': history,
        'count': len(history),
        'last_service': last_service
    }


def _normalize_conversation_id(conversation_id):
    normalized = str(conversation_id or '').strip()
    return normalized if normalized else 'legacy'


def save_chat_message(role, content, conversation_id='legacy'):
    if not role or content is None:
        return False

    cursor.execute(
        'INSERT INTO chat_messages (conversation_id, role, content) VALUES (?, ?, ?)',
        (_normalize_conversation_id(conversation_id), str(role), str(content))
    )
    conn.commit()
    return True


def get_chat_messages(conversation_id='legacy', limit=100):
    safe_limit = max(1, min(int(limit), 500))
    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (_normalize_conversation_id(conversation_id), safe_limit)
    )
    rows = cursor.fetchall()

    messages = [
        {
            'id': row[0],
            'role': row[1],
            'content': row[2],
            'created_at': row[3]
        }
        for row in rows
    ]
    messages.reverse()
    return messages


def search_chat_messages(query, conversation_id='legacy', limit=120):
    safe_limit = max(1, min(int(limit), 500))
    query_text = (query or '').strip()
    if not query_text:
        return get_chat_messages(conversation_id=conversation_id, limit=safe_limit)

    like_query = f"%{query_text}%"
    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE conversation_id = ? AND content LIKE ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (_normalize_conversation_id(conversation_id), like_query, safe_limit)
    )
    rows = cursor.fetchall()

    messages = [
        {
            'id': row[0],
            'role': row[1],
            'content': row[2],
            'created_at': row[3]
        }
        for row in rows
    ]
    messages.reverse()
    return messages


def get_chat_conversations(query='', limit=100):
    safe_limit = max(1, min(int(limit), 500))
    query_text = (query or '').strip()
    like_query = f"%{query_text}%" if query_text else None

    cursor.execute(
        '''
        WITH conversation_summaries AS (
            SELECT
                COALESCE(NULLIF(conversation_id, ''), 'legacy') AS conversation_id,
                MIN(id) AS first_message_id,
                MIN(created_at) AS created_at,
                MAX(created_at) AS updated_at,
                COUNT(*) AS message_count
            FROM chat_messages
            GROUP BY COALESCE(NULLIF(conversation_id, ''), 'legacy')
        )
        SELECT
            c.conversation_id,
            c.created_at,
            c.updated_at,
            c.message_count,
            (
                SELECT content
                FROM chat_messages m1
                WHERE COALESCE(NULLIF(m1.conversation_id, ''), 'legacy') = c.conversation_id
                  AND m1.role = 'user'
                ORDER BY m1.id ASC
                LIMIT 1
            ) AS title,
            (
                SELECT content
                FROM chat_messages m2
                WHERE COALESCE(NULLIF(m2.conversation_id, ''), 'legacy') = c.conversation_id
                ORDER BY m2.id DESC
                LIMIT 1
            ) AS preview
        FROM conversation_summaries c
        WHERE (? IS NULL OR EXISTS (
            SELECT 1
            FROM chat_messages m3
            WHERE COALESCE(NULLIF(m3.conversation_id, ''), 'legacy') = c.conversation_id
              AND m3.content LIKE ?
        ))
        ORDER BY c.updated_at DESC
        LIMIT ?
        ''',
        (like_query, like_query, safe_limit)
    )

    rows = cursor.fetchall()
    return [
        {
            'conversation_id': row[0],
            'created_at': row[1],
            'updated_at': row[2],
            'message_count': row[3],
            'title': row[4],
            'preview': row[5]
        }
        for row in rows
    ]


def clear_chat_messages(conversation_id=None):
    if conversation_id is None:
        cursor.execute('DELETE FROM chat_messages')
    else:
        cursor.execute(
            'DELETE FROM chat_messages WHERE COALESCE(NULLIF(conversation_id, ""), "legacy") = ?',
            (_normalize_conversation_id(conversation_id),)
        )
    conn.commit()
    return True


def get_setting_value(key, default_value=None):
    cursor.execute('SELECT value FROM app_settings WHERE key = ?', (str(key),))
    row = cursor.fetchone()
    if row is None:
        return default_value
    return row[0]


def set_setting_value(key, value):
    cursor.execute(
        '''
        INSERT INTO app_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''',
        (str(key), str(value))
    )
    conn.commit()
    return True


def get_all_settings():
    result = dict(DEFAULT_SETTINGS)
    cursor.execute('SELECT key, value FROM app_settings')
    rows = cursor.fetchall()
    for row in rows:
        result[row[0]] = row[1]
    return result