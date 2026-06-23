from flask import Blueprint, render_template

pages_bp = Blueprint('pages', __name__)


@pages_bp.route('/')
def dashboard_page():
    return render_template('index.html', current_page='dashboard')


@pages_bp.route('/upload-center')
def upload_page():
    return render_template('upload.html', current_page='upload')


@pages_bp.route('/inventory')
def inventory_page():
    return render_template('inventory.html', current_page='inventory')


@pages_bp.route('/assistant')
def assistant_page():
    return render_template('assistant.html', current_page='assistant')


@pages_bp.route('/settings')
def settings_page():
    return render_template('settings.html', current_page='settings')