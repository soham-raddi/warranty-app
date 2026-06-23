import os

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from database import (
    add_service_history_entry,
    delete_appliance,
    get_inventory_analytics,
    get_service_history,
    save_receipt_to_db,
    update_warranty_card,
)
from llm_extractor import parse_receipt_image_to_json

receipts_bp = Blueprint('receipts', __name__)


@receipts_bp.route('/upload', methods=['POST'])
def upload_file():
    if 'receipt_image' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['receipt_image']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(file.filename)
    filepath = os.path.join(upload_folder, filename)
    file.save(filepath)

    extracted_data = parse_receipt_image_to_json(filepath)

    if "error" in extracted_data:
        os.remove(filepath)
        return jsonify({"error": extracted_data["error"]}), 400

    extracted_data['file_path'] = filepath
    success = save_receipt_to_db(extracted_data)

    if success:
        return jsonify(extracted_data)
    return jsonify({"error": "Database save failed"}), 500


@receipts_bp.route('/api/inventory', methods=['GET'])
def get_inventory():
    items, total, active, alerts = get_inventory_analytics()
    return jsonify({
        'items': items,
        'total_spent': round(total, 2),
        'active_warranties': active,
        'alerts': alerts,
        'alert_count': len(alerts)
    })


@receipts_bp.route('/api/delete/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    file_path, warranty_card_path = delete_appliance(item_id)
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
    if file.filename == '':
        return jsonify({"error": "No selected warranty card file"}), 400

    upload_folder = current_app.config['WARRANTY_UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)

    filename = secure_filename(file.filename)
    filename = f"{item_id}_{filename}"
    filepath = os.path.join(upload_folder, filename)
    file.save(filepath)

    success = update_warranty_card(item_id, filepath)
    if not success:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"error": "Inventory item not found"}), 404

    return jsonify({
        "message": "Warranty card attached successfully",
        "warranty_card_path": filepath
    })


@receipts_bp.route('/api/inventory/<int:item_id>/service-history', methods=['GET'])
def service_history(item_id):
    return jsonify({
        'item_id': item_id,
        'entries': get_service_history(item_id)
    })


@receipts_bp.route('/api/inventory/<int:item_id>/service-history', methods=['POST'])
def add_history_entry(item_id):
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