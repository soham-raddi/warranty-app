import os

from flask import Flask

from routes import chat_bp, pages_bp, receipts_bp, settings_bp

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['WARRANTY_UPLOAD_FOLDER'] = 'static/uploads/warranty_cards'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['WARRANTY_UPLOAD_FOLDER'], exist_ok=True)

app.register_blueprint(pages_bp)
app.register_blueprint(receipts_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(settings_bp)

if __name__ == '__main__':
    app.run(debug=True)