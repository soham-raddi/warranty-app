"""Order emails forwarded to a workspace's private address, and reviewing what was read from them."""
import json

import requests
from flask import Blueprint, g, jsonify, request

from ..db import (
    delete_pending_receipt,
    get_all_settings,
    get_pending_receipt,
    list_pending_receipts,
    set_setting_value,
)
from ..services import inbound, storage
from ..services.ratelimit import limit
from .auth import role_required
from .receipts import _serve, save_reviewed_items

inbound_bp = Blueprint('inbound', __name__)


@inbound_bp.route('/api/inbound/email', methods=['POST'])
@limit('inbound', per_ip=True, minute=60, hourly=600)
def receive_email():
    """Resend's webhook for received mail. Public, but only signed requests from Resend are accepted."""
    if not inbound.is_enabled():
        return jsonify({'error': 'Email import is not set up'}), 404
    body = request.get_data()
    if not inbound.signature_is_valid(request.headers, body):
        return jsonify({'error': 'Invalid signature'}), 401
    try:
        event = json.loads(body)
    except ValueError:
        return jsonify({'error': 'Invalid JSON'}), 400
    if event.get('type') not in ('email.received', 'inbox.email.received'):
        return jsonify({'status': 'ignored: not a received email'})
    try:
        status = inbound.handle_received(event)
    except (requests.RequestException, storage.StorageError) as error:
        # A temporary problem: a non-2xx reply makes Resend try again later.
        print(f'Email import failed, Resend will retry: {error}')
        return jsonify({'error': 'Temporary failure'}), 503
    print(f'Email import: {status}')
    return jsonify({'status': status})


@inbound_bp.route('/api/inbound/address', methods=['GET'])
def forwarding_address():
    if not inbound.is_enabled():
        return jsonify({'enabled': False})
    settings = get_all_settings(g.workspace_id)
    return jsonify({
        'enabled': True,
        'address': inbound.address_for(g.workspace_id),
        'gmail_confirmation': settings.get('inbound_gmail_confirmation') or None,
        'can_manage': g.role in ('owner', 'admin'),
    })


@inbound_bp.route('/api/inbound/address', methods=['POST'])
@role_required('owner', 'admin')
def replace_forwarding_address():
    if not inbound.is_enabled():
        return jsonify({'error': "Email import isn't set up on this site."}), 404
    inbound.new_address_token(g.workspace_id)
    set_setting_value(g.workspace_id, 'inbound_gmail_confirmation', '')
    return jsonify({'address': inbound.address_for(g.workspace_id),
                    'message': 'New forwarding address created. The old one no longer works.'})


@inbound_bp.route('/api/inbound/gmail-confirmation', methods=['DELETE'])
def dismiss_gmail_confirmation():
    set_setting_value(g.workspace_id, 'inbound_gmail_confirmation', '')
    return jsonify({'message': 'Dismissed.'})


@inbound_bp.route('/api/pending', methods=['GET'])
def pending_receipts():
    return jsonify({'pending': list_pending_receipts(g.workspace_id)})


@inbound_bp.route('/api/pending/<int:pending_id>/save', methods=['POST'])
@limit('save', per_workspace=True, hourly=60, daily=300)
def save_pending(pending_id):
    pending = get_pending_receipt(g.workspace_id, pending_id)
    if not pending:
        return jsonify({'error': 'This purchase was already saved or dismissed.'}), 404
    response, status = save_reviewed_items(request.json or {}, lambda: pending['file_path'])
    if status == 201:
        delete_pending_receipt(g.workspace_id, pending_id)  # the file now belongs to the saved items
    return response, status


@inbound_bp.route('/api/pending/<int:pending_id>', methods=['DELETE'])
def dismiss_pending(pending_id):
    file_path = delete_pending_receipt(g.workspace_id, pending_id)
    if file_path is False:
        return jsonify({'error': 'This purchase was already saved or dismissed.'}), 404
    storage.delete(file_path)
    return jsonify({'message': 'Dismissed.'})


@inbound_bp.route('/files/pending/<int:pending_id>')
def pending_file(pending_id):
    pending = get_pending_receipt(g.workspace_id, pending_id)
    return _serve(pending['file_path'] if pending else None)
