import os
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
from llm_extractor import parse_receipt_image_to_json
from database import save_receipt_to_db, get_inventory_analytics, delete_appliance
from chatbot import ask_digital_twin

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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
    file_path = delete_appliance(item_id)
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
    return jsonify({'message': 'Item and image deleted successfully'})

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message')
    history = data.get('history', [])
    reply = ask_digital_twin(user_message, history)
    return jsonify({"reply": reply})

if __name__ == '__main__':
    app.run(debug=True)