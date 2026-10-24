import uuid

from flask import Blueprint, g, jsonify, request
from werkzeug.utils import secure_filename

import storage
from database import (
    add_service_history_entry,
    apply_warranty_card_details,
    delete_appliance,
    find_duplicate_receipt,
    get_appliance,
    item_exists,
    get_appliance_file_paths,
    get_inventory_analytics,
    get_service_history,
    save_receipt_to_db,
    update_appliance,
    update_warranty_card,
)
from llm_extractor import parse_receipt_image_to_json, parse_warranty_card
from ratelimit import limit
from .auth import role_required

receipts_bp = Blueprint('receipts', __name__)

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'pdf'}

# Fields people may correct after a scan, with their maximum lengths.
EDITABLE_FIELDS = {
    'item_name': 200, 'brand': 100, 'model_number': 100, 'serial_number': 100, 'category': 100,
    'date_of_purchase': 10, 'total_amount': 40, 'store_name': 200, 'store_contact': 200,
    'invoice_number': 100, 'warranty_info': 500, 'return_policy': 500,
}


def _file_extension(filename):
    return filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''


def _validate_upload(file):
    """Returns an error message for unsupported uploads, or None if the file is acceptable."""
    if not file or file.filename == '':
        return 'No selected file'
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


@receipts_bp.route('/upload', methods=['POST'])
@limit('scan', per_workspace=True, hourly=30, daily=100)
def upload_file():
    if 'receipt_image' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['receipt_image']
    error = _validate_upload(file)
    if error:
        return jsonify({"error": error}), 400

    # Read the receipt first; only keep the file if it turns out to be a new, valid receipt.
    data = file.read()
    extracted_data = parse_receipt_image_to_json(data, file.filename)
    if "error" in extracted_data:
        return jsonify({"error": extracted_data["error"]}), 400

    duplicate_id = find_duplicate_receipt(
        g.workspace_id, extracted_data.get('invoice_number'), extracted_data.get('store_name'), extracted_data.get('item_name')
    )
    if duplicate_id:
        return jsonify({
            "error": f"This receipt is already in your inventory (invoice {extracted_data.get('invoice_number')}).",
            "duplicate_item_id": duplicate_id
        }), 409

    try:
        extracted_data['file_path'] = storage.save(data, _storage_key(file.filename), file.mimetype)
    except storage.StorageError:
        return jsonify({"error": "Could not store the receipt. Please try again."}), 502

    if save_receipt_to_db(g.workspace_id, extracted_data):
        return jsonify(extracted_data)
    storage.delete(extracted_data['file_path'])
    return jsonify({"error": "Database save failed"}), 500


@receipts_bp.route('/api/inventory', methods=['GET'])
def get_inventory():
    items, total, active, alerts = get_inventory_analytics(g.workspace_id)
    return jsonify({
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
        return jsonify({'error': 'Inventory item not found'}), 404
    return jsonify({'item': item})


@receipts_bp.route('/api/inventory/<int:item_id>', methods=['PATCH'])
def edit_item(item_id):
    """Corrects details the scan got wrong. Any member may edit; deleting stays with owners and admins."""
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'Inventory item not found'}), 404

    payload = request.json or {}
    changes = {}
    for field, max_length in EDITABLE_FIELDS.items():
        if field in payload:
            value = str(payload.get(field) or '').strip()[:max_length]
            changes[field] = value or None

    if 'item_name' in changes and not changes['item_name']:
        return jsonify({'error': 'Item name cannot be empty'}), 400
    if changes.get('date_of_purchase'):
        from datetime import datetime
        try:
            datetime.strptime(changes['date_of_purchase'], '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Purchase date must be a valid date'}), 400

    if 'warranty_months' in payload:
        raw = payload.get('warranty_months')
        if raw in (None, ''):
            changes['warranty_months'] = None  # fall back to the workspace's default period
        else:
            try:
                months = int(raw)
            except (TypeError, ValueError):
                return jsonify({'error': 'Warranty length must be a whole number of months'}), 400
            if not 1 <= months <= 360:
                return jsonify({'error': 'Warranty length must be between 1 and 360 months'}), 400
            changes['warranty_months'] = months

    update_appliance(g.workspace_id, item_id, changes)
    return jsonify({'message': 'Item updated', 'item': get_appliance(g.workspace_id, item_id)})


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
        return jsonify({'error': 'Inventory item not found'}), 404
    storage.delete(file_path)
    storage.delete(warranty_card_path)
    return jsonify({'message': 'Item and image deleted successfully'})


@receipts_bp.route('/api/inventory/<int:item_id>/attach-warranty-card', methods=['POST'])
@limit('card', per_workspace=True, hourly=30, daily=100)
def attach_warranty_card(item_id):
    if 'warranty_card' not in request.files:
        return jsonify({"error": "No warranty card file uploaded"}), 400

    file = request.files['warranty_card']
    error = _validate_upload(file)
    if error:
        return jsonify({"error": error}), 400

    if not item_exists(g.workspace_id, item_id):
        return jsonify({"error": "Inventory item not found"}), 404

    _, previous_card_path = get_appliance_file_paths(g.workspace_id, item_id)
    data = file.read()
    try:
        ref = storage.save(data, _storage_key(file.filename, 'warranty_cards'), file.mimetype)
    except storage.StorageError:
        return jsonify({"error": "Could not store the warranty card. Please try again."}), 502

    if not update_warranty_card(g.workspace_id, item_id, ref):
        storage.delete(ref)
        return jsonify({"error": "Inventory item not found"}), 404

    # Replacing a card should not leave the old file behind
    if previous_card_path and previous_card_path != ref:
        storage.delete(previous_card_path)

    # Read the card so the item uses its real warranty length instead of the workspace default.
    card = parse_warranty_card(data, file.filename)
    if card.get('error'):
        return jsonify({"message": "Warranty card attached, but it couldn't be read. "
                                   "You can set the warranty length with Edit.", "card_read": False})
    changes = apply_warranty_card_details(g.workspace_id, item_id, months=card['warranty_months'],
                                          start_date=card['warranty_start_date'], notes=card['coverage_notes'])
    if 'warranty_months' not in changes:
        return jsonify({"message": "Warranty card attached, but no warranty length was found on it. "
                                   "The default period is still used; you can change it with Edit.",
                        "card_read": False})

    item = next((i for i in get_inventory_analytics(g.workspace_id)[0] if i['id'] == item_id), {})
    months = changes['warranty_months']
    length = f"{months // 12}-year" if months % 12 == 0 else f"{months}-month"
    expiry = f", now valid until {item['warranty_expires_on']}" if item.get('warranty_expires_on') else ''
    return jsonify({"message": f"Warranty card read: {length} warranty{expiry}.", "card_read": True,
                    "warranty_months": months, "warranty_expires_on": item.get('warranty_expires_on')})


@receipts_bp.route('/api/inventory/<int:item_id>/service-history', methods=['GET'])
def service_history(item_id):
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'Inventory item not found'}), 404
    return jsonify({
        'item_id': item_id,
        'entries': get_service_history(item_id)
    })


@receipts_bp.route('/api/inventory/<int:item_id>/service-history', methods=['POST'])
def add_history_entry(item_id):
    if not item_exists(g.workspace_id, item_id):
        return jsonify({'error': 'Inventory item not found'}), 404

    payload = request.json or request.form or {}
    service_date = str(payload.get('service_date', '')).strip()[:10]
    service_type = str(payload.get('service_type', '')).strip()[:120]
    vendor = str(payload.get('vendor', '')).strip()[:120]
    cost = str(payload.get('cost', '')).strip()[:40]
    notes = str(payload.get('notes', '')).strip()[:1000]
    next_service_date = str(payload.get('next_service_date', '')).strip()[:10]

    if not service_date:
        return jsonify({'error': 'service_date is required'}), 400
    if not service_type:
        return jsonify({'error': 'service_type is required'}), 400

    entry_id = add_service_history_entry(
        item_id=item_id,
        service_date=service_date,
        service_type=service_type,
        vendor=vendor,
        cost=cost,
        notes=notes,
        next_service_date=next_service_date,
    )

    return jsonify({
        'message': 'Service history entry added successfully',
        'entry_id': entry_id,
        'entries': get_service_history(item_id)
    })
