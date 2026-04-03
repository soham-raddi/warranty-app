from flask import Flask, render_template, request, jsonify
import os
from werkzeug.utils import secure_filename
from llm_extractor import parse_receipt_image_to_json
from database import save_receipt_to_db, get_all_appliances
from chatbot import ask_digital_twin

app = Flask(__name__)

# Configure the upload folder to be inside 'static' so the browser can display the images
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/')
def index():
    """Serves the main HTML interface."""
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'receipt_image' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
        
    file = request.files['receipt_image']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        extracted_data = parse_receipt_image_to_json(filepath)
        
        # --- NEW: Catch the Mountain Photo! ---
        if extracted_data.get("error") == "NOT_A_RECEIPT":
            os.remove(filepath) # Delete the bad image
            return jsonify({'error': 'This image does not look like a valid receipt or bill.'}), 400
            
        # Catch any other extraction errors
        if "error" in extracted_data:
            os.remove(filepath)
            return jsonify({'error': 'Failed to extract data from the receipt.'}), 500
        
        extracted_data['file_path'] = filepath.replace('\\', '/')
        save_receipt_to_db(extracted_data)
        
        return jsonify(extracted_data)

@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    """Fetches all digitized appliances for the 'My Inventory' tab."""
    columns, rows = get_all_appliances()
    # Convert the raw database rows into a list of dictionaries for the frontend
    data = [dict(zip(columns, row)) for row in rows]
    return jsonify(data)

@app.route('/api/chat', methods=['POST'])
def chat():
    """Handles messages sent to the Digital Twin chatbot."""
    req = request.get_json()
    user_msg = req.get('message')
    history = req.get('history', [])
    
    # Pass the message and history to our AI engine
    reply = ask_digital_twin(user_msg, history)
    return jsonify({'reply': reply})

# This is the crucial block that tells Flask to actively run the server!
if __name__ == '__main__':
    app.run(debug=True)