"""Inventory items, their receipts and warranty cards, service history, totals and export rows."""
import re
from datetime import datetime, timedelta

from .connection import conn, cursor, insert_returning_id
from .currency import convert_amount, get_exchange_rates, get_historical_rates
from .settings import get_setting_value, workspace_today


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
    today = workspace_today(workspace_id)
    day_rates = get_historical_rates({i.get('date_of_purchase') for i in items
                                      if (i.get('currency') or home_currency).upper() != home_currency})

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
        # What the item cost in the workspace's currency: at the rate on the purchase date when known, otherwise
        # today's; None when no rate is known yet.
        item['home_amount'], item['rate_date'] = None, None
        if amount is not None:
            on_the_day = day_rates.get(item.get('date_of_purchase'))
            converted = convert_amount(amount, currency, home_currency, on_the_day) if on_the_day else None
            if converted is not None and currency != home_currency:
                item['home_amount'], item['rate_date'] = converted, item['date_of_purchase']
            else:
                item['home_amount'] = convert_amount(amount, currency, home_currency, rates)
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
        days_remaining = (expiry_date.date() - today).days
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


def add_service_history_entry(item_id, service_date, service_type, vendor='', cost='', notes='', next_service_date='',
                              currency=None):
    """Callers must check the item belongs to their workspace first (item_exists)."""
    entry_id = insert_returning_id(
        '''
        INSERT INTO service_history (
            item_id, service_date, service_type, vendor, cost, notes, next_service_date, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (item_id, service_date, service_type, vendor, cost, notes, next_service_date, currency)
    )
    conn.commit()
    return entry_id


def get_service_history(item_id):
    """Callers must check the item belongs to their workspace first (item_exists)."""
    cursor.execute(
        '''
        SELECT id, item_id, service_date, service_type, vendor, cost, notes, next_service_date, created_at, currency
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
            'created_at': row[8],
            'currency': row[9]
        }
        for row in rows
    ]


def export_workspace_rows(workspace_id):
    """Items and service history of a workspace, for the CSV export."""
    items, _total, _active, _alerts = get_inventory_analytics(workspace_id)
    cursor.execute('''
        SELECT s.item_id, a.item_name, s.service_date, s.service_type, s.vendor, s.cost, s.currency, s.notes,
               s.next_service_date
        FROM service_history s JOIN appliances_v5 a ON a.id = s.item_id
        WHERE a.workspace_id = ?
        ORDER BY s.item_id, s.service_date
    ''', (workspace_id,))
    history = [dict(zip(['item_id', 'item_name', 'service_date', 'service_type', 'vendor', 'cost', 'currency', 'notes',
                         'next_service_date'], row)) for row in cursor.fetchall()]
    return items, history


def update_service_history_entry(item_id, entry_id, fields):
    """Callers must check the item belongs to their workspace first (item_exists)."""
    columns = [c for c in fields
               if c in ('service_date', 'service_type', 'vendor', 'cost', 'currency', 'notes', 'next_service_date')]
    if not columns:
        return False
    cursor.execute(
        f"UPDATE service_history SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ? AND item_id = ?",
        [fields[c] for c in columns] + [entry_id, item_id]
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_service_history_entry(item_id, entry_id):
    cursor.execute('DELETE FROM service_history WHERE id = ? AND item_id = ?', (entry_id, item_id))
    conn.commit()
    return cursor.rowcount > 0


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
