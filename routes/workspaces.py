import csv
import io
import os
import re
from datetime import datetime

from flask import Blueprint, Response, g, jsonify, redirect, render_template, request, session, url_for

import storage

from database import (
    accept_invite_token,
    create_invite,
    create_workspace,
    delete_invite,
    delete_user_account,
    delete_workspace,
    export_workspace_rows,
    owned_workspaces_with_other_members,
    transfer_ownership,
    get_invite_by_token,
    get_membership_role,
    get_user_workspaces,
    list_invites,
    list_workspace_members,
    regenerate_invite_token,
    remove_workspace_member,
    rename_workspace,
)
from ratelimit import limit
from .auth import is_dev_login_allowed, is_google_configured, role_required

workspaces_bp = Blueprint('workspaces', __name__)

EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MAX_NAME_LENGTH = 60
# Keeps one account from creating workspaces without limit on a public install.
MAX_OWNED_WORKSPACES = 5


def new_workspaces_allowed():
    """ALLOW_NEW_WORKSPACES=false makes the install invite-only (people can only join existing families)."""
    return os.getenv('ALLOW_NEW_WORKSPACES', 'true').lower() in {'1', 'true', 'yes'}


def _clean_name(raw):
    name = re.sub(r'\s+', ' ', str(raw or '')).strip()
    return name[:MAX_NAME_LENGTH]


@workspaces_bp.app_template_filter('nice_date')
def nice_date(value):
    """'2026-10-21T18:30:00' -> 'Oct 21, 2026', matching how dates appear elsewhere in the app."""
    try:
        return datetime.fromisoformat(str(value)).strftime('%b %d, %Y')
    except (TypeError, ValueError):
        return value or ''


def _invite_link(token):
    return url_for('workspaces.join_page', token=token, _external=True)


def _default_workspace_name(user):
    first_name = (user.get('name') or user['email'].split('@')[0]).split()[0]
    return f"{first_name}'s Family"


@workspaces_bp.route('/welcome')
def welcome_page():
    """Shown to signed-in people who do not belong to any workspace yet."""
    user = g.current_user
    if get_user_workspaces(user['id']):
        return redirect(url_for('pages.dashboard_page'))
    return render_template(
        'welcome.html',
        user=user,
        can_create=new_workspaces_allowed(),
        suggested_name=_default_workspace_name(user),
    )


@workspaces_bp.route('/api/workspaces', methods=['GET'])
def list_my_workspaces():
    return jsonify({
        'workspaces': get_user_workspaces(g.current_user['id']),
        'current_workspace_id': session.get('workspace_id'),
        'can_create': new_workspaces_allowed(),
    })


@workspaces_bp.route('/api/workspaces', methods=['POST'])
@limit('create_workspace', daily=10)
def create_my_workspace():
    if not new_workspaces_allowed():
        return jsonify({'error': 'New workspaces are disabled on this site. Ask a family owner to invite you.'}), 403

    user = g.current_user
    owned = [w for w in get_user_workspaces(user['id']) if w['role'] == 'owner']
    if len(owned) >= MAX_OWNED_WORKSPACES:
        return jsonify({'error': f'You can own at most {MAX_OWNED_WORKSPACES} workspaces'}), 400

    payload = request.json or {}
    name = _clean_name(payload.get('name')) or _default_workspace_name(user)
    workspace_id = create_workspace(name, user['id'])
    session['workspace_id'] = workspace_id
    return jsonify({'workspace': {'id': workspace_id, 'name': name, 'role': 'owner'}}), 201


@workspaces_bp.route('/api/workspaces/switch', methods=['POST'])
def switch_workspace():
    payload = request.json or {}
    try:
        workspace_id = int(payload.get('workspace_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'workspace_id is required'}), 400

    if not get_membership_role(workspace_id, g.current_user['id']):
        return jsonify({'error': 'You are not a member of that workspace'}), 403

    session['workspace_id'] = workspace_id
    return jsonify({'message': 'Switched workspace', 'workspace_id': workspace_id})


@workspaces_bp.route('/api/workspace', methods=['PATCH'])
@role_required('owner')
def rename_current_workspace():
    name = _clean_name((request.json or {}).get('name'))
    if not name:
        return jsonify({'error': 'Workspace name cannot be empty'}), 400
    rename_workspace(g.workspace_id, name)
    return jsonify({'message': 'Workspace renamed', 'workspace': {'id': g.workspace_id, 'name': name}})


@workspaces_bp.route('/api/workspace/members', methods=['GET'])
def workspace_members():
    can_manage = g.role in ('owner', 'admin')
    return jsonify({
        'workspace': g.workspace,
        'members': list_workspace_members(g.workspace_id),
        'invites': list_invites(g.workspace_id) if can_manage else [],
        'current_user_id': g.current_user['id'],
        'current_role': g.role,
        'can_manage': can_manage,
        'can_add_admins': g.role == 'owner',
    })


@workspaces_bp.route('/api/workspace/invites', methods=['POST'])
@role_required('owner', 'admin')
@limit('invite', per_workspace=True, hourly=30, daily=100)
def invite_member():
    payload = request.json or {}
    email = str(payload.get('email', '')).strip().lower()
    role = str(payload.get('role', 'member')).strip().lower()

    if not EMAIL_PATTERN.match(email):
        return jsonify({'error': 'Enter a valid email address'}), 400
    if role not in ('member', 'admin'):
        role = 'member'
    if role == 'admin' and g.role != 'owner':
        return jsonify({'error': 'Only the owner can invite admins'}), 403

    invite, error = create_invite(g.workspace_id, email, role, g.current_user['id'])
    if error:
        return jsonify({'error': error}), 400
    token = invite.pop('token')
    return jsonify({
        'message': f'Invite created for {email}. Send them the link below.',
        'invite': invite,
        'link': _invite_link(token),
    }), 201


@workspaces_bp.route('/api/workspace/invites/<int:invite_id>/link', methods=['POST'])
@role_required('owner', 'admin')
@limit('invite', per_workspace=True, hourly=30, daily=100)
def new_invite_link(invite_id):
    # Only a hash of each link is stored, so a lost link is replaced rather than shown again.
    result = regenerate_invite_token(g.workspace_id, invite_id)
    if not result:
        return jsonify({'error': 'Invite not found'}), 404
    token, expires_at = result
    return jsonify({'link': _invite_link(token), 'expires_at': expires_at,
                    'message': 'New invite link created. The previous link no longer works.'})


@workspaces_bp.route('/api/workspace/invites/<int:invite_id>', methods=['DELETE'])
@role_required('owner', 'admin')
def revoke_invite(invite_id):
    if not delete_invite(g.workspace_id, invite_id):
        return jsonify({'error': 'Invite not found'}), 404
    return jsonify({'message': 'Invite revoked'})


@workspaces_bp.route('/api/workspace/members/<int:user_id>', methods=['DELETE'])
@role_required('owner', 'admin')
def remove_member(user_id):
    if user_id == g.current_user['id']:
        return jsonify({'error': 'Use "Leave workspace" to remove yourself'}), 400

    target_role = get_membership_role(g.workspace_id, user_id)
    if target_role is None:
        return jsonify({'error': 'This person is not a member of the workspace'}), 404
    if g.role == 'admin' and target_role != 'member':
        return jsonify({'error': 'Admins can only remove members'}), 403

    removed, message = remove_workspace_member(g.workspace_id, user_id)
    if not removed:
        return jsonify({'error': message}), 400
    return jsonify({'message': message})


@workspaces_bp.route('/api/workspace/leave', methods=['POST'])
def leave_workspace():
    if g.role == 'owner':
        return jsonify({'error': 'The owner cannot leave their own workspace'}), 400

    remove_workspace_member(g.workspace_id, g.current_user['id'])
    remaining = get_user_workspaces(g.current_user['id'])
    session['workspace_id'] = remaining[0]['id'] if remaining else None
    return jsonify({'message': 'You left the workspace', 'redirect': url_for('pages.dashboard_page') if remaining else url_for('workspaces.welcome_page')})


@workspaces_bp.route('/join/<token>', methods=['GET'])
def join_page(token):
    """Landing page for an invite link. Viewing it never joins anyone; that needs the Join button (POST)."""
    invite = get_invite_by_token(token)
    user = g.get('current_user')
    already_member = bool(invite and user and get_membership_role(invite['workspace_id'], user['id']))
    if invite and invite['accepted'] and not already_member:
        invite = None  # a used link means nothing to anyone else

    return render_template(
        'join.html',
        invite=invite,
        user=user,
        token=token,
        already_member=already_member,
        google_configured=is_google_configured(),
        allow_dev_login=is_dev_login_allowed(),
    ), (200 if invite else 404)


@workspaces_bp.route('/join/<token>', methods=['POST'])
def accept_invite(token):
    user = g.get('current_user')
    if not user:
        return redirect(url_for('auth.login_page', next=url_for('workspaces.join_page', token=token)))

    workspace_id, error = accept_invite_token(token, user)
    if error:
        invite = get_invite_by_token(token)
        return render_template('join.html', invite=invite, user=user, token=token, error=error,
                               already_member=False, google_configured=is_google_configured(),
                               allow_dev_login=is_dev_login_allowed()), 400

    session['workspace_id'] = workspace_id
    return redirect(url_for('pages.dashboard_page'))


@workspaces_bp.route('/api/workspace/transfer', methods=['POST'])
@role_required('owner')
def transfer_workspace():
    try:
        new_owner_id = int((request.json or {}).get('user_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Choose a member to make owner'}), 400
    error = transfer_ownership(g.workspace_id, g.current_user['id'], new_owner_id)
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'message': 'Ownership transferred. You are now an admin of this workspace.'})


@workspaces_bp.route('/api/workspace', methods=['DELETE'])
@role_required('owner')
def delete_current_workspace():
    """Permanently deletes the workspace, its items, receipts, settings, invites and chats."""
    confirm = str((request.json or {}).get('confirm_name', '')).strip()
    if confirm != (g.workspace or {}).get('name'):
        return jsonify({'error': 'Type the workspace name exactly to confirm'}), 400
    for ref in delete_workspace(g.workspace_id):
        storage.delete(ref)
    session.pop('workspace_id', None)
    remaining = get_user_workspaces(g.current_user['id'])
    return jsonify({'message': 'Workspace deleted',
                    'redirect': url_for('pages.dashboard_page') if remaining else url_for('workspaces.welcome_page')})


@workspaces_bp.route('/api/workspace/export.csv', methods=['GET'])
def export_workspace():
    """Inventory and service history as one CSV file (receipt files stay downloadable from Inventory)."""
    items, history = export_workspace_rows(g.workspace_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    columns = ['id', 'item_name', 'brand', 'model_number', 'serial_number', 'category', 'date_of_purchase',
               'total_amount', 'store_name', 'invoice_number', 'warranty_months', 'warranty_expires_on',
               'warranty_status', 'warranty_info', 'return_policy', 'has_warranty_card']
    writer.writerow(['Inventory'])
    writer.writerow(columns)
    for item in items:
        writer.writerow([_csv_safe(item.get(column)) for column in columns])
    writer.writerow([])
    writer.writerow(['Service history'])
    history_columns = ['item_id', 'item_name', 'service_date', 'service_type', 'vendor', 'cost', 'notes', 'next_service_date']
    writer.writerow(history_columns)
    for entry in history:
        writer.writerow([_csv_safe(entry.get(column)) for column in history_columns])

    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '-', (g.workspace or {}).get('name', 'workspace')).strip('-') or 'workspace'
    filename = f"{safe_name}-{datetime.now().strftime('%Y-%m-%d')}.csv"
    return Response(chr(0xFEFF) + buffer.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def _csv_safe(value):
    """Stops spreadsheet apps treating text from receipts as formulas (CSV injection)."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', chr(9), chr(13)) else text


@workspaces_bp.route('/api/account', methods=['DELETE'])
def delete_my_account():
    """Deletes the signed-in person's account, workspaces they own alone, and their chats."""
    user = g.current_user
    if str((request.json or {}).get('confirm_email', '')).strip().lower() != user['email'].lower():
        return jsonify({'error': 'Type your email address exactly to confirm'}), 400
    shared = owned_workspaces_with_other_members(user['id'])
    if shared:
        names = ', '.join(w['name'] for w in shared)
        return jsonify({'error': f'You own workspaces other people still use ({names}). '
                                 'Transfer ownership or delete them first.'}), 400
    for ref in delete_user_account(user['id']):
        storage.delete(ref)
    session.clear()
    return jsonify({'message': 'Your account has been deleted', 'redirect': url_for('auth.login_page')})
