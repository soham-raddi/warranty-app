import os

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


@pages_bp.route('/privacy')
def privacy_page():
    return render_template('legal.html', page='privacy', contact_email=_contact_email())


@pages_bp.route('/terms')
def terms_page():
    return render_template('legal.html', page='terms', contact_email=_contact_email())


def _contact_email():
    # Shown on the legal pages; set CONTACT_EMAIL in the hosting environment.
    return os.getenv('CONTACT_EMAIL', '').strip() or os.getenv('OWNER_EMAIL', '').strip()
