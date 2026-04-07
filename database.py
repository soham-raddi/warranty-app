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
        invoice_number TEXT, file_path TEXT
    )
''')
conn.commit()

def save_receipt_to_db(data):
    if "error" in data: return False
    cursor.execute('''
        INSERT INTO appliances_v5 (
            item_name, brand, model_number, serial_number, category, date_of_purchase, 
            price, tax_amount, total_amount, payment_method, 
            store_name, store_contact, warranty_info, return_policy, invoice_number, file_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get("item_name"), data.get("brand"), data.get("model_number"), data.get("serial_number"),
        data.get("category"), data.get("date_of_purchase"), data.get("price"),
        data.get("tax_amount"), data.get("total_amount"), data.get("payment_method"),
        data.get("store_name"), data.get("store_contact"), data.get("warranty_info"), 
        data.get("return_policy"), data.get("invoice_number"), data.get("file_path")
    ))
    conn.commit()
    return True

def get_all_appliances():
    cursor.execute('SELECT * FROM appliances_v5')
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return columns, rows

def delete_appliance(item_id):
    cursor.execute('SELECT file_path FROM appliances_v5 WHERE id = ?', (item_id,))
    row = cursor.fetchone()
    file_path = row[0] if row else None
    cursor.execute('DELETE FROM appliances_v5 WHERE id = ?', (item_id,))
    conn.commit()
    return file_path

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