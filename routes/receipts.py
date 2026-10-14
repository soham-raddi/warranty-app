import os
import uuid

from flask import Blueprint, abort, current_app, g, jsonify, request, send_file
from werkzeug.utils import secure_filename

from database import (
    add_service_history_entry,
    delete_appliance,
    find_duplicate_receipt,
    item_exists,
    get_appliance_file_paths,
    get_inventory_analytics,
    get_service_history,
    save_receipt_to_db,
    update_warranty_card,
)
from llm_extractor import parse_receipt_image_to_json
from .auth import role_required

receipts_bp = Blueprint('receipts', __name__)

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'pdf'}


def _file_extension(filename):
    return filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''


def _validate_upload(file):
    """Returns an error message for unsupported uploads, or None if the file is acceptable."""
    if not file or file.filename == '':
        return 'No selected file'
    if _file_extension(file.filename) not in ALLOWED_EXTENSIONS:
        return 'Unsupported file type. Upload a JPG, PNG, WEBP or PDF file.'
    return None


def _save_upload(file, folder):
    """Saves an upload under a unique name so files with the same original name never collide."""
    os.makedirs(folder, exist_ok=True)
    extension = _file_extension(file.filename)
    base_name = secure_filename(file.filename.rsplit('.', 1)[0])[:80] or 'upload'
    filepath = os.path.join(folder, f"{uuid.uuid4().hex[:12]}_{base_name}.{extension}")
    file.save(filepath)
    return filepath


def _workspace_folder(*parts):
    """Each workspace keeps its files in its own folder: uploads/<workspace id>/..."""
    return os.path.join(current_app.config['UPLOAD_FOLDER'], str(g.workspace_id), *parts)


def _serve_upload(path):
    """Sends a stored upload, refusing anything outside the uploads folder."""
    if not path:
        abort(404)
    upload_root = os.path.realpath(current_app.config['UPLOAD_FOLDER'])
    real_path = os.path.realpath(path)
    if os.path.commonpath([upload_root, real_path]) != upload_root or not os.path.isfile(real_path):
        abort(404)
    # Anything that is not a known receipt format is forced to download instead of rendering.
    as_attachment = _file_extension(real_path) not in ALLOWED_EXTENSIONS
    return send_file(real_path, as_attachment=as_attachment)


@receipts_bp.route('/upload', methods=['POST'])
def upload_file():
    if 'receipt_image' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['receipt_image']
    error = _validate_upload(file)
    if error:
        return jsonify({"error": error}), 400

    filepath = _save_upload(file, _workspace_folder())

    extracted_data = parse_receipt_image_to_json(filepath)

    if "error" in extracted_data:
        os.remove(filepath)
        return jsonify({"error": extracted_data["error"]}), 400

    duplicate_id = find_duplicate_receipt(
        g.workspace_id, extracted_data.get('invoice_number'), extracted_data.get('store_name'), extracted_data.get('item_name')
    )
    if duplicate_id:
        os.remove(filepath)
        return jsonify({
            "error": f"This receipt is already in your inventory (invoice {extracted_data.get('invoice_number')}).",
            "duplicate_item_id": duplicate_id
        }), 409

    extracted_data['file_path'] = filepath
    success = save_receipt_to_db(g.workspace_id, extracted_data)

    if success:
        return jsonify(extracted_data)
    os.remove(filepath)
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


@receipts_bp.route('/files/receipt/<int:item_id>')
def receipt_file(item_id):
    file_path, _ = get_appliance_file_paths(g.workspace_id, item_id)
    return _serve_upload(file_path)


@receipts_bp.route('/files/warranty-card/<int:item_id>')
def warranty_card_file(item_id):
    _, warranty_card_path = get_appliance_file_paths(g.workspace_id, item_id)
    return _serve_upload(warranty_card_path)


@receipts_bp.route('/api/delete/<int:item_id>', methods=['DELETE'])
@role_required('owner', 'admin')
def delete_item(item_id):
    found, file_path, warranty_card_path = delete_appliance(g.workspace_id, item_id)
    if not found:
        return jsonify({'error': 'Inventory item not found'}), 404
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
    if warranty_card_path and os.path.exists(warranty_card_path):
        os.remove(warranty_card_path)
    return jsonify({'message': 'Item and image deleted successfully'})


@receipts_bp.route('/api/inventory/<int:item_id>/attach-warranty-card', methods=['POST'])
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
    filepath = _save_upload(file, _workspace_folder('warranty_cards'))

    success = update_warranty_card(g.workspace_id, item_id, filepath)
    if not success:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"error": "Inventory item not found"}), 404

    # Replacing a card should not leave the old file behind
    if previous_card_path and previous_card_path != filepath and os.path.exists(previous_card_path):
        os.remove(previous_card_path)

    return jsonify({
        "message": "Warranty card attached successfully",
        "warranty_card_path": filepath
    })


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
    service_date = str(payload.get('service_date', '')).strip()
    service_type = str(payload.get('service_type', '')).strip()
    vendor = str(payload.get('vendor', '')).strip()
    cost = str(payload.get('cost', '')).strip()
    notes = str(payload.get('notes', '')).strip()
    next_service_date = str(payload.get('next_service_date', '')).strip()

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