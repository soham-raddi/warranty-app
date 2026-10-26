import json
import re
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, request
from werkzeug.utils import secure_filename

import storage
from database import (
    add_service_history_entry,
    apply_warranty_card_details,
    delete_appliance,
    find_duplicate_receipt,
    delete_service_history_entry,
    get_all_settings,
    get_appliance,
    item_exists,
    get_appliance_file_paths,
    get_inventory_analytics,
    get_service_history,
    list_pending_receipts,
    parse_amount,
    save_receipt_to_db,
    update_appliance,
    update_service_history_entry,
    workspace_today,
    update_warranty_card,
)
import fx
from llm_extractor import parse_receipt_image_to_json, parse_warranty_card
from ratelimit import limit
from .auth import role_required

receipts_bp = Blueprint('receipts', __name__)

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'pdf'}

# Fields people may correct after a scan, with their maximum lengths.
EDITABLE_FIELDS = {
    'item_name': 200, 'brand': 100, 'model_number': 100, 'serial_number': 100, 'category': 100,
    'date_of_purchase': 10, 'total_amount': 40, 'store_name': 200, 'store_contact': 200,
    'invoice_number': 100, 'warranty_info': 500, 'return_policy': 500, 'currency': 3,
}
SAVE_FIELDS = {**EDITABLE_FIELDS, 'payment_method': 60, 'tax_amount': 40}
MAX_ITEMS_PER_RECEIPT = 20


def _file_extension(filename):
    return filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''


def _validate_upload(file):
    """Returns an error message for unsupported uploads, or None if the file is acceptable."""
    if not file or file.filename == '':
        return 'Choose a file to upload.'
    if _file_extension(file.filename) not in ALLOWED_EXTENSIONS:
        return 'Unsupported file type. Upload a JPG, PNG, WEBP or PDF file.'
    return None


def _storage_key(filename, *folders):
    """A unique name inside this workspace's folder, so files never collide or cross workspaces."""
    extension = _file_extension(filename)
    base_name = secure_filename(filename.rsplit('.', 1)[0])[:80] or 'upload'
    return '/'.join([str(g.workspace_id), *folders, f"{uuid.uuid4().hex[:12]}_{base_name}.{extension}"])


def _serve(ref):
    # Anything that is not a known receipt format is forced to download instead of rendering.
    return storage.serve(ref, as_attachment=_file_extension(ref or '') not in ALLOWED_EXTENSIONS)


def _clean_fields(payload, allowed):
    """Validates submitted item details. Returns (changes, error message)."""
    changes = {}
    for field, max_length in allowed.items():
        if field in payload:
            value = str(payload.get(field) or '').strip()[:max_length]
            changes[field] = value or None

    if 'item_name' in changes and not changes['item_name']:
        return None, 'Item name cannot be empty.'
    if changes.get('total_amount'):
        amount = parse_amount(changes['total_amount'])
        if amount is None or amount < 0:
            return None, 'Amount must be a number, like 1299 or 6.50.'
        changes['total_amount'] = format_amount(amount)
    if 'currency' in changes and not changes['currency']:
        changes.pop('currency')  # an item always has a currency; leaving it blank keeps the current one
    if changes.get('currency'):
        # Checked on the submitted text, before it is cut to the field's length.
        raw_currency = str(payload.get('currency') or '').strip().upper()
        if not re.fullmatch(r'[A-Z]{3}', raw_currency):
            return None, 'Currency must be a 3-letter code such as INR or MYR.'
        changes['currency'] = raw_currency
    if changes.get('date_of_purchase'):
        error = _date_error(changes['date_of_purchase'], 'Purchase date', allow_future=False)
        if error:
            return None, error

    if 'warranty_months' in payload:
        raw = payload.get('warranty_months')
        if raw in (None, ''):
            changes['warranty_months'] = None  # fall back to the workspace's default period
        else:
            try:
                months = int(raw)
            except (TypeError, ValueError):
                return None, 'Warranty length must be a whole number of months.'
            if not 1 <= months <= 360:
                return None, 'Warranty length must be between 1 and 360 months.'
            changes['warranty_months'] = months
    return changes, None


def format_amount(amount):
    """1299.0 -> '1299', 6.5 -> '6.5'."""
    return f'{amount:.2f}'.rstrip('0').rstrip('.')


def _date_error(value, label, allow_future=True):
    try:
        day = datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return f'{label} must be a valid date.'
    # A day of slack, since the family may be ahead of the server's clock.
    if not allow_future and day > workspace_today(g.workspace_id) + timedelta(days=1):
        return f"{label} can't be in the future."
    return None


def _ensure_rates(currencies):
    """Fetches exchange rates right away the first time an item is saved in a currency without one."""
    home = get_all_settings(g.workspace_id).get('currency_code')
    foreign = {c for c in currencies if c and c != home}
    if foreign:
        fx.ensure_rates_for(home, *foreign)


def _duplicate_of(receipt, item):
    return find_duplicate_receipt(g.workspace_id, receipt.get('invoice_number'), receipt.get('store_name'),
                                  item.get('item_name'))


@receipts_bp.route('/api/receipts/scan', methods=['POST'])
@limit('scan', per_workspace=True, hourly=30, daily=100)
def scan_receipt():
    """Reads a receipt for the person to review. Nothing is saved until they confirm."""
    if 'receipt_image' not in request.files:
        return jsonify({"error": "Choose a file to upload."}), 400
    file = request.files['receipt_image']
    error = _validate_upload(file)
    if error:
        return jsonify({"error": error}), 400

    result = parse_receipt_image_to_json(file.read(), file.filename)
    if "error" in result:
        return jsonify({"error": result["error"]}), 400
    for item in result['items']:
        item['duplicate_item_id'] = _duplicate_of(result['receipt'], item)
    return jsonify(result)


@receipts_bp.route('/api/receipts/save', methods=['POST'])
@limit('save', per_workspace=True, hourly=60, daily=300)
def save_receipt():
    """Saves the reviewed items. The receipt file is sent again so nothing is stored for scans never saved."""
    file = request.files.get('receipt_image')
    error = _validate_upload(file)
    if error:
        return jsonify({"error": error}), 400
    try:
        details = json.loads(request.form.get('details') or '{}')
    except ValueError:
        return jsonify({"error": "The receipt details could not be read. Please scan again."}), 400
    return save_reviewed_items(details, lambda: storage.save(file.read(), _storage_key(file.filename), file.mimetype))


def save_reviewed_items(details, store_file):
    """Validates reviewed receipt details and saves each item. store_file() returns the receipt's stored file
    reference; it is only called once something is actually going to be saved. Returns a Flask response."""
    try:
        receipt, items = dict(details.get('receipt') or {}), list(details.get('items') or [])
    except (TypeError, AttributeError, ValueError):
        return jsonify({"error": "The receipt details could not be read. Please scan again."}), 400
    if not items:
        return jsonify({"error": "Choose at least one item to save."}), 400
    if len(items) > MAX_ITEMS_PER_RECEIPT:
        return jsonify({"error": f"A receipt can have at most {MAX_ITEMS_PER_RECEIPT} items."}), 400

    rows, skipped = [], []
    for item in items:
        if not isinstance(item, dict):
            return jsonify({"error": "The receipt details could not be read. Please scan again."}), 400
        merged = {**receipt, **item, 'total_amount': item.get('amount') or None}
        row, error = _clean_fields(merged, SAVE_FIELDS)
        if error:
            return jsonify({"error": f"{item.get('item_name') or 'Item'}: {error}"}), 400
        if not row.get('item_name'):
            return jsonify({"error": "Every item needs a name."}), 400
        if _duplicate_of(row, row):
            skipped.append(row['item_name'])
            continue
        row['price'] = row['total_amount']
        row['currency'] = row.get('currency') or get_all_settings(g.workspace_id).get('currency_code')
        rows.append(row)

    if not rows:
        return jsonify({"error": "These items are already in your inventory.", "skipped": skipped}), 409

    try:
        file_path = store_file()
    except storage.StorageError:
        return jsonify({"error": "Could not store the receipt. Please try again."}), 502
    for row in rows:
        row['file_path'] = file_path
        save_receipt_to_db(g.workspace_id, row)
    _ensure_rates({row.get('currency') for row in rows})

    saved = len(rows)
    message = f"Saved {saved} item{'s' if saved != 1 else ''} to your inventory."
    if skipped:
        message += f" Skipped {len(skipped)} already saved: {', '.join(skipped)}."
    return jsonify({"message": message, "saved": saved, "skipped": skipped}), 201


@receipts_bp.route('/api/inventory', methods=['GET'])
def get_inventory():
    items, total, active, alerts = get_inventory_analytics(g.workspace_id)
    home = get_all_settings(g.workspace_id).get('currency_code')
    foreign = [i for i in items if i['currency'] != home and parse_amount(i.get('total_amount')) is not None]
    return jsonify({
        'pending_imports': len(list_pending_receipts(g.workspace_id)),
        # Items in other currencies are converted for the total; ones without a known rate are left out.
        'converted_items': sum(1 for i in foreign if i['home_amount'] is not None),
        'unconverted_items': sum(1 for i in foreign if i['home_amount'] is None),
        'items': items,
        'total_spent': round(total, 2),
        'active_warranties': active,
        'alerts': alerts,
        'alert_count': len(alerts)
    })


@receipts_bp.route('/api/inventory/<int:item_id>', methods=['GET'])
def get_item(item_id):
    item = get_appliance(g.workspace_id, item_id)
    if not item:
        return jsonify({'error': 'That item was not found. It may have been deleted.'}), 404
    return jsonify({'item': item})


@receipts_bp.route('/api/inventory/<int:item_id>', methods=['PATCH'])
def edit_item(item_id):
    """Corrects details the scan got wrong. Any member may edit; deleting stays with owners and admins."""
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'That item was not found. It may have been deleted.'}), 404

    changes, error = _clean_fields(request.json or {}, EDITABLE_FIELDS)
    if error:
        return jsonify({'error': error}), 400

    update_appliance(g.workspace_id, item_id, changes)
    _ensure_rates({changes.get('currency')})
    return jsonify({'message': 'Item updated.', 'item': get_appliance(g.workspace_id, item_id)})


@receipts_bp.route('/files/receipt/<int:item_id>')
def receipt_file(item_id):
    file_path, _ = get_appliance_file_paths(g.workspace_id, item_id)
    return _serve(file_path)


@receipts_bp.route('/files/warranty-card/<int:item_id>')
def warranty_card_file(item_id):
    _, warranty_card_path = get_appliance_file_paths(g.workspace_id, item_id)
    return _serve(warranty_card_path)


@receipts_bp.route('/api/delete/<int:item_id>', methods=['DELETE'])
@role_required('owner', 'admin')
def delete_item(item_id):
    found, file_path, warranty_card_path = delete_appliance(g.workspace_id, item_id)
    if not found:
        return jsonify({'error': 'That item was not found. It may have been deleted.'}), 404
    storage.delete(file_path)
    storage.delete(warranty_card_path)
    return jsonify({'message': 'Item deleted.'})


@receipts_bp.route('/api/inventory/<int:item_id>/attach-warranty-card', methods=['POST'])
@limit('card', per_workspace=True, hourly=30, daily=100)
def attach_warranty_card(item_id):
    if 'warranty_card' not in request.files:
        return jsonify({"error": "Choose a warranty card file to upload."}), 400

    file = request.files['warranty_card']
    error = _validate_upload(file)
    if error:
        return jsonify({"error": error}), 400

    if not item_exists(g.workspace_id, item_id):
        return jsonify({"error": "That item was not found. It may have been deleted."}), 404

    _, previous_card_path = get_appliance_file_paths(g.workspace_id, item_id)
    data = file.read()
    try:
        ref = storage.save(data, _storage_key(file.filename, 'warranty_cards'), file.mimetype)
    except storage.StorageError:
        return jsonify({"error": "Could not store the warranty card. Please try again."}), 502

    if not update_warranty_card(g.workspace_id, item_id, ref):
        storage.delete(ref)
        return jsonify({"error": "That item was not found. It may have been deleted."}), 404

    # Replacing a card should not leave the old file behind
    if previous_card_path and previous_card_path != ref:
        storage.delete(previous_card_path)

    return jsonify(_read_card_into_item(item_id, data, file.filename, attached=True))


@receipts_bp.route('/api/inventory/<int:item_id>/read-warranty-card', methods=['POST'])
@limit('card', per_workspace=True, hourly=30, daily=100)
def reread_warranty_card(item_id):
    """Reads an already attached card again, e.g. one attached before cards were read automatically."""
    _, card_path = get_appliance_file_paths(g.workspace_id, item_id)
    if not card_path:
        return jsonify({"error": "This item has no warranty card yet."}), 404
    try:
        data = storage.read(card_path)
    except (storage.StorageError, OSError, ValueError):
        return jsonify({"error": "Could not open the stored warranty card. Try replacing it."}), 502
    return jsonify(_read_card_into_item(item_id, data, card_path, attached=False))


def _read_card_into_item(item_id, data, filename, attached):
    """Reads a warranty card so the item uses its real warranty length instead of the workspace default."""
    prefix = "Warranty card attached, but" if attached else "The warranty card was read again, but"
    card = parse_warranty_card(data, filename)
    if card.get('error'):
        return {"message": f"{prefix} it couldn't be read. You can set the warranty length with Edit.",
                "card_read": False}
    changes = apply_warranty_card_details(g.workspace_id, item_id, months=card['warranty_months'],
                                          start_date=card['warranty_start_date'], notes=card['coverage_notes'])
    if 'warranty_months' not in changes:
        return {"message": f"{prefix} no warranty length was found on it. The default period is still used; "
                           "you can change it with Edit.", "card_read": False}

    item = next((i for i in get_inventory_analytics(g.workspace_id)[0] if i['id'] == item_id), {})
    months = changes['warranty_months']
    length = f"{months // 12}-year" if months % 12 == 0 else f"{months}-month"
    # The page adds the end date in the family's chosen date format.
    return {"message": f"Warranty card read: {length} warranty.", "card_read": True,
            "warranty_months": months, "warranty_expires_on": item.get('warranty_expires_on')}


@receipts_bp.route('/api/inventory/<int:item_id>/service-history', methods=['GET'])
def service_history(item_id):
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'That item was not found. It may have been deleted.'}), 404
    return jsonify({
        'item_id': item_id,
        'entries': get_service_history(item_id)
    })


def _clean_service_entry(payload):
    """Validates a service record. Returns (fields, error message)."""
    fields = {
        'service_date': str(payload.get('service_date', '')).strip()[:10],
        'service_type': str(payload.get('service_type', '')).strip()[:120],
        'vendor': str(payload.get('vendor', '')).strip()[:120],
        'cost': str(payload.get('cost', '')).strip()[:40],
        'notes': str(payload.get('notes', '')).strip()[:1000],
        'next_service_date': str(payload.get('next_service_date', '')).strip()[:10],
    }
    if not fields['service_date']:
        return None, 'Choose the date of the service.'
    error = _date_error(fields['service_date'], 'Service date', allow_future=False)
    if error:
        return None, error
    if not fields['service_type']:
        return None, 'Enter what kind of service it was.'
    if fields['cost']:
        cost = parse_amount(fields['cost'])
        if cost is None or cost < 0:
            return None, 'Cost must be a number, like 1500.'
        fields['cost'] = format_amount(cost)
    if fields['next_service_date']:
        error = _date_error(fields['next_service_date'], 'Next service date')
        if error:
            return None, error
        if fields['next_service_date'] < fields['service_date']:
            return None, 'The next service must be after this one.'
    return fields, None


@receipts_bp.route('/api/inventory/<int:item_id>/service-history', methods=['POST'])
def add_history_entry(item_id):
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'That item was not found. It may have been deleted.'}), 404
    fields, error = _clean_service_entry(request.json or request.form or {})
    if error:
        return jsonify({'error': error}), 400
    entry_id = add_service_history_entry(item_id=item_id, **fields)
    return jsonify({'message': 'Service record added.', 'entry_id': entry_id, 'entries': get_service_history(item_id)})


@receipts_bp.route('/api/inventory/<int:item_id>/service-history/<int:entry_id>', methods=['PATCH'])
def edit_history_entry(item_id, entry_id):
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'That item was not found. It may have been deleted.'}), 404
    fields, error = _clean_service_entry(request.json or {})
    if error:
        return jsonify({'error': error}), 400
    if not update_service_history_entry(item_id, entry_id, fields):
        return jsonify({'error': 'That service record was not found.'}), 404
    return jsonify({'message': 'Service record updated.', 'entries': get_service_history(item_id)})


@receipts_bp.route('/api/inventory/<int:item_id>/service-history/<int:entry_id>', methods=['DELETE'])
def delete_history_entry(item_id, entry_id):
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'That item was not found. It may have been deleted.'}), 404
    if not delete_service_history_entry(item_id, entry_id):
        return jsonify({'error': 'That service record was not found.'}), 404
    return jsonify({'message': 'Service record deleted.', 'entries': get_service_history(item_id)})
