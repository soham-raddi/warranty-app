import sqlite3

conn = sqlite3.connect('inventory.db', check_same_thread=False)
cursor = conn.cursor()

# V4 Table adds 'file_path' at the very end
cursor.execute('''
    CREATE TABLE IF NOT EXISTS appliances_v4 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT, brand TEXT, model_number TEXT, serial_number TEXT,
        category TEXT, date_of_purchase TEXT, price TEXT, tax_amount TEXT,
        total_amount TEXT, payment_method TEXT, store_name TEXT,
        store_contact TEXT, warranty_info TEXT, return_policy TEXT,
        file_path TEXT
    )
''')
conn.commit()

def save_receipt_to_db(data):
    if "error" in data: return False
    cursor.execute('''
        INSERT INTO appliances_v4 (
            item_name, brand, model_number, serial_number, category, date_of_purchase, 
            price, tax_amount, total_amount, payment_method, 
            store_name, store_contact, warranty_info, return_policy, file_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get("item_name"), data.get("brand"), data.get("model_number"), data.get("serial_number"),
        data.get("category"), data.get("date_of_purchase"), data.get("price"),
        data.get("tax_amount"), data.get("total_amount"), data.get("payment_method"),
        data.get("store_name"), data.get("store_contact"), data.get("warranty_info"), 
        data.get("return_policy"), data.get("file_path") # <-- NEW FIELD
    ))
    conn.commit()
    return True

def get_all_appliances():
    cursor.execute('SELECT * FROM appliances_v4')
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return columns, rows