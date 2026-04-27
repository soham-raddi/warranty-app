import sqlite3
from datetime import datetime

conn = sqlite3.connect('inventory.db', check_same_thread=False)
cursor = conn.cursor()

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
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
''')
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
    
    for item in items:
        try:
            val = str(item['total_amount']).replace('₹', '').replace('$', '').replace(',', '').strip()
            total_spent += float(val)
        except: pass
            
        try:
            p_date = datetime.strptime(item['date_of_purchase'], '%Y-%m-%d')
            if (datetime.now() - p_date).days < 365:
                active_warranties += 1
                item['warranty_status'] = "Active"
            else:
                item['warranty_status'] = "Expired"
        except:
            item['warranty_status'] = "Unknown"
            
    return items, round(total_spent, 2), active_warranties


def save_chat_message(role, content):
    if not role or content is None:
        return False

    cursor.execute(
        'INSERT INTO chat_messages (role, content) VALUES (?, ?)',
        (str(role), str(content))
    )
    conn.commit()
    return True


def get_chat_messages(limit=100):
    safe_limit = max(1, min(int(limit), 500))
    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        ORDER BY id DESC
        LIMIT ?
        ''',
        (safe_limit,)
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


def search_chat_messages(query, limit=120):
    safe_limit = max(1, min(int(limit), 500))
    query_text = (query or '').strip()
    if not query_text:
        return get_chat_messages(limit=safe_limit)

    like_query = f"%{query_text}%"
    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE content LIKE ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (like_query, safe_limit)
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


def clear_chat_messages():
    cursor.execute('DELETE FROM chat_messages')
    conn.commit()
    return True