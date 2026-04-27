import os
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
from llm_extractor import parse_receipt_image_to_json
from database import (
    save_receipt_to_db,
    get_inventory_analytics,
    delete_appliance,
    update_warranty_card,
    save_chat_message,
    get_chat_messages,
    search_chat_messages,
    clear_chat_messages
)
from chatbot import ask_digital_twin

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['WARRANTY_UPLOAD_FOLDER'] = 'static/uploads/warranty_cards'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['WARRANTY_UPLOAD_FOLDER'], exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'receipt_image' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['receipt_image']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    extracted_data = parse_receipt_image_to_json(filepath)
    
    if "error" in extracted_data:
        os.remove(filepath)
        return jsonify({"error": extracted_data["error"]}), 400
        
    extracted_data['file_path'] = filepath
    success = save_receipt_to_db(extracted_data)
    
    if success:
        return jsonify(extracted_data)
    else:
        return jsonify({"error": "Database save failed"}), 500

@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    items, total, active = get_inventory_analytics()
    return jsonify({
        'items': items,
        'total_spent': round(total, 2),
        'active_warranties': active
    })

@app.route('/api/delete/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    file_path, warranty_card_path = delete_appliance(item_id)
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
    if warranty_card_path and os.path.exists(warranty_card_path):
        os.remove(warranty_card_path)
    return jsonify({'message': 'Item and image deleted successfully'})


@app.route('/api/inventory/<int:item_id>/attach-warranty-card', methods=['POST'])
def attach_warranty_card(item_id):
    if 'warranty_card' not in request.files:
        return jsonify({"error": "No warranty card file uploaded"}), 400

    file = request.files['warranty_card']
    if file.filename == '':
        return jsonify({"error": "No selected warranty card file"}), 400

    filename = secure_filename(file.filename)
    filename = f"{item_id}_{filename}"
    filepath = os.path.join(app.config['WARRANTY_UPLOAD_FOLDER'], filename)
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

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message')
    history = data.get('history', [])

    if user_message:
        save_chat_message('user', user_message)

    result = ask_digital_twin(user_message, history)

    if isinstance(result, dict):
        assistant_reply = result.get("reply", "")
        if assistant_reply:
            save_chat_message('assistant', assistant_reply)
        return jsonify({
            "reply": assistant_reply,
            "action": result.get("action")
        })

    assistant_reply = str(result)
    if assistant_reply:
        save_chat_message('assistant', assistant_reply)
    return jsonify({"reply": assistant_reply, "action": None})


@app.route('/api/chat/history', methods=['GET'])
def chat_history():
    limit = request.args.get('limit', default=100, type=int)
    query = request.args.get('query', default='', type=str)

    if query and query.strip():
        messages = search_chat_messages(query=query, limit=limit)
    else:
        messages = get_chat_messages(limit=limit)

    return jsonify({"messages": messages})


@app.route('/api/chat/history', methods=['DELETE'])
def clear_chat_history():
    clear_chat_messages()
    return jsonify({"message": "Chat history cleared successfully"})

if __name__ == '__main__':
    app.run(debug=True)