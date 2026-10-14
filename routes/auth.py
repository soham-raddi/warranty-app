import os
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from authlib.integrations.flask_client import OAuth

from database import (
    add_authorized_user,
    count_users,
    delete_user,
    get_owner_user,
    get_user_by_email,
    get_user_by_id,
    list_users,
    make_sole_owner,
    upsert_user,
)

auth_bp = Blueprint('auth', __name__)
oauth = OAuth()

LOOPBACK_ADDRESSES = {'127.0.0.1', '::1'}


def safe_next_url(target):
    """Returns target only if it is a same-site relative path, else None (prevents open redirects)."""
    if not target:
        return None
    target = str(target).strip()
    if not target.startswith('/') or target.startswith('//') or '\\' in target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target


def is_dev_login_allowed():
    """Dev login is opt-in via ALLOW_DEV_LOGIN and only ever works from this machine."""
    enabled = os.getenv('ALLOW_DEV_LOGIN', 'false').lower() in {'1', 'true', 'yes'}
    return enabled and request.remote_addr in LOOPBACK_ADDRESSES


def role_required(*roles):
    """Restricts a route to signed-in users whose role is in roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = g.get('current_user')
            if not user:
                return jsonify({'error': 'Unauthorized. Please sign in.'}), 401
            if user.get('role') not in roles:
                return jsonify({'error': 'You do not have permission to perform this action'}), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _start_session(user):
    session.clear()
    session['user_id'] = user['id']
    session['user_email'] = user['email']
    session['user_name'] = user['name']
    session['user_role'] = user['role']
    session['user_picture'] = user['picture']


def is_google_configured():
    client_id = os.getenv('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
    return bool(client_id and client_secret and client_id != 'your-google-client-id')


def init_oauth(app):
    """Initializes Authlib OAuth with Google provider if credentials exist."""
    oauth.init_app(app)
    client_id = os.getenv('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()

    if is_google_configured():
        oauth.register(
            name='google',
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'}
        )


@auth_bp.route('/login')
def login_page():
    next_url = safe_next_url(request.args.get('next'))
    if session.get('user_id'):
        return redirect(next_url or url_for('pages.dashboard_page'))

    owner = get_owner_user()
    google_configured = is_google_configured()
    allow_dev_login = is_dev_login_allowed()
    error = request.args.get('error', '')

    return render_template(
        'login.html',
        google_configured=google_configured,
        allow_dev_login=allow_dev_login,
        owner=owner,
        error=error,
        next_url=next_url or ''
    )


@auth_bp.route('/auth/google')
def google_login():
    if not is_google_configured():
        return redirect(url_for('auth.login_page', error='not_configured'))

    next_url = safe_next_url(request.args.get('next'))
    if next_url:
        session['next_url'] = next_url

    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/auth/callback')
def google_callback():
    if not is_google_configured():
        return redirect(url_for('auth.login_page', error='not_configured'))

    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            user_info = oauth.google.userinfo()
    except Exception as e:
        current_app.logger.error(f"Google OAuth callback error: {e}")
        return redirect(url_for('auth.login_page', error='auth_failed'))

    email = (user_info.get('email') or '').strip().lower()
    if not email:
        return redirect(url_for('auth.login_page', error='no_email'))

    google_id = user_info.get('sub')
    name = user_info.get('name') or email.split('@')[0]
    picture = user_info.get('picture') or ''

    # Authorization logic
    owner_email_env = os.getenv('OWNER_EMAIL', '').strip().lower()
    owner_user = get_owner_user()
    existing_user = get_user_by_email(email)

    is_authorized = False
    assigned_role = 'member'

    if owner_email_env:
        if email == owner_email_env:
            is_authorized = True
            assigned_role = 'owner'
        elif existing_user and existing_user.get('is_active', 1):
            is_authorized = True
            assigned_role = existing_user.get('role', 'member')
    else:
        # If no owner exists yet, the first user to log in claims ownership!
        if not owner_user:
            is_authorized = True
            assigned_role = 'owner'
        elif owner_user['email'].lower() == email:
            is_authorized = True
            assigned_role = 'owner'
        elif existing_user and existing_user.get('is_active', 1):
            is_authorized = True
            assigned_role = existing_user.get('role', 'member')

    if not is_authorized:
        return redirect(url_for('auth.login_page', error='unauthorized', unauth_email=email))

    user = upsert_user(
        google_id=google_id,
        email=email,
        name=name,
        picture=picture,
        default_role=assigned_role
    )

    # OWNER_EMAIL is authoritative: its account becomes the single owner, even if it existed before as a member
    # or a dev-login placeholder owner was created first.
    if assigned_role == 'owner' and (user['role'] != 'owner' or get_owner_user()['id'] != user['id']):
        user = make_sole_owner(user['id'])

    if not user.get('is_active'):
        return redirect(url_for('auth.login_page', error='unauthorized', unauth_email=email))

    next_url = safe_next_url(session.pop('next_url', None))
    _start_session(user)
    return redirect(next_url or url_for('pages.dashboard_page'))


@auth_bp.route('/auth/dev-login')
def dev_login():
    """Local development login helper when Google OAuth keys are not yet configured."""
    if not is_dev_login_allowed():
        return redirect(url_for('auth.login_page', error='dev_disabled'))

    owner_email_env = os.getenv('OWNER_EMAIL', '').strip().lower() or 'owner@digitaltwin.local'
    owner_user = get_owner_user()

    if owner_user:
        target_email = owner_user['email']
        target_name = owner_user['name']
        role = owner_user['role']
    else:
        target_email = owner_email_env
        target_name = 'Local Twin Owner'
        role = 'owner'

    user = upsert_user(
        google_id=None,  # never overwrite a real linked Google account
        email=target_email,
        name=target_name,
        picture='https://api.dicebear.com/7.x/identicon/svg?seed=owner',
        default_role=role
    )

    _start_session(user)
    next_url = safe_next_url(request.args.get('next'))
    return redirect(next_url or url_for('pages.dashboard_page'))


@auth_bp.route('/auth/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login_page'))


@auth_bp.route('/api/auth/me', methods=['GET'])
def get_current_user_api():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'authenticated': False, 'user': None}), 401

    user = get_user_by_id(user_id)
    if not user:
        session.clear()
        return jsonify({'authenticated': False, 'user': None}), 401

    return jsonify({'authenticated': True, 'user': user})


@auth_bp.route('/api/auth/users', methods=['GET'])
def get_workspace_users():
    user_id = session.get('user_id')
    current_user = get_user_by_id(user_id) if user_id else None
    if not current_user:
        return jsonify({'error': 'Unauthorized'}), 401

    users = list_users()
    return jsonify({
        'users': users,
        'current_user_role': current_user['role'],
        'can_manage': current_user['role'] == 'owner'
    })


@auth_bp.route('/api/auth/users', methods=['POST'])
def add_workspace_user():
    user_id = session.get('user_id')
    current_user = get_user_by_id(user_id) if user_id else None
    if not current_user or current_user['role'] != 'owner':
        return jsonify({'error': 'Only the workspace owner can invite family members'}), 403

    payload = request.json or {}
    email = str(payload.get('email', '')).strip().lower()
    name = str(payload.get('name', '')).strip()
    role = str(payload.get('role', 'member')).strip().lower()

    if not email or '@' not in email:
        return jsonify({'error': 'A valid email address is required'}), 400

    if role not in {'member', 'admin'}:
        role = 'member'

    user, created = add_authorized_user(email=email, name=name, role=role)
    if not created:
        return jsonify({'error': f'User {email} is already authorized in this workspace'}), 400

    return jsonify({
        'message': f'Authorized {email} for workspace collaboration',
        'user': user
    }), 201


@auth_bp.route('/api/auth/users/<int:target_user_id>', methods=['DELETE'])
def remove_workspace_user(target_user_id):
    user_id = session.get('user_id')
    current_user = get_user_by_id(user_id) if user_id else None
    if not current_user or current_user['role'] != 'owner':
        return jsonify({'error': 'Only the workspace owner can remove members'}), 403

    if current_user['id'] == target_user_id:
        return jsonify({'error': 'You cannot delete yourself from the workspace'}), 400

    success, message = delete_user(target_user_id)
    if not success:
        return jsonify({'error': message}), 400

    return jsonify({'message': message})
