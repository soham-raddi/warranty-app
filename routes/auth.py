import os
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from authlib.integrations.flask_client import OAuth

from config import IS_PRODUCTION
from database import accept_pending_invites, get_user_by_email, get_user_workspaces, upsert_user
from ratelimit import limit

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
    """Dev login is opt-in via ALLOW_DEV_LOGIN, only works from this machine, and never on a hosted site."""
    if IS_PRODUCTION:
        return False
    enabled = os.getenv('ALLOW_DEV_LOGIN', 'false').lower() in {'1', 'true', 'yes'}
    return enabled and request.remote_addr in LOOPBACK_ADDRESSES


def role_required(*roles):
    """Restricts a route to members whose role in the current workspace is in roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.get('current_user'):
                return jsonify({'error': 'Unauthorized. Please sign in.'}), 401
            if g.get('role') not in roles:
                return jsonify({'error': 'You do not have permission to perform this action'}), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def start_session(user, next_url=None):
    """Signs the user in, picks their workspace and returns where to send them next."""
    session.clear()
    session.permanent = True
    session['user_id'] = user['id']

    accept_pending_invites(user)
    workspaces = get_user_workspaces(user['id'])
    if not workspaces:
        # Someone who signed in from an invite link goes back to it to accept; others pick or create a workspace.
        if next_url and next_url.startswith('/join/'):
            return next_url
        return url_for('workspaces.welcome_page')

    session['workspace_id'] = workspaces[0]['id']
    return next_url or url_for('pages.dashboard_page')


def is_google_configured():
    client_id = os.getenv('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
    return bool(client_id and client_secret and client_id != 'your-google-client-id')


def init_oauth(app):
    """Initializes Authlib OAuth with Google provider if credentials exist."""
    oauth.init_app(app)
    if is_google_configured():
        oauth.register(
            name='google',
            client_id=os.getenv('GOOGLE_CLIENT_ID', '').strip(),
            client_secret=os.getenv('GOOGLE_CLIENT_SECRET', '').strip(),
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'}
        )


@auth_bp.route('/login')
def login_page():
    next_url = safe_next_url(request.args.get('next'))
    if session.get('user_id'):
        return redirect(next_url or url_for('pages.dashboard_page'))

    return render_template(
        'login.html',
        google_configured=is_google_configured(),
        allow_dev_login=is_dev_login_allowed(),
        is_production=IS_PRODUCTION,
        error=request.args.get('error', ''),
        next_url=next_url or ''
    )


@auth_bp.route('/auth/google')
@limit('signin', per_ip=True, minute=20, hourly=100)
def google_login():
    if not is_google_configured():
        return redirect(url_for('auth.login_page', error='not_configured'))

    next_url = safe_next_url(request.args.get('next'))
    if next_url:
        session['next_url'] = next_url

    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/auth/callback')
@limit('callback', per_ip=True, minute=20, hourly=100)
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
    if not email or not user_info.get('email_verified', True):
        return redirect(url_for('auth.login_page', error='no_email'))

    # Anyone with a Google account may sign in; what they can see is decided by workspace membership.
    user = upsert_user(
        google_id=user_info.get('sub'),
        email=email,
        name=user_info.get('name') or email.split('@')[0],
        picture=user_info.get('picture') or '',
    )
    if not user.get('is_active'):
        return redirect(url_for('auth.login_page', error='unauthorized'))

    next_url = safe_next_url(session.pop('next_url', None))
    return redirect(start_session(user, next_url))


@auth_bp.route('/auth/dev-login')
def dev_login():
    """Local development login. ?email= signs in as another address so several families can be tested."""
    if not is_dev_login_allowed():
        return redirect(url_for('auth.login_page', error='dev_disabled'))

    email = (request.args.get('email') or os.getenv('OWNER_EMAIL', '') or 'owner@digitaltwin.local').strip().lower()
    if '@' not in email:
        return redirect(url_for('auth.login_page', error='dev_disabled'))

    existing = get_user_by_email(email)
    user = upsert_user(
        google_id=None,  # never overwrite a real linked Google account
        email=email,
        name=existing['name'] if existing else email.split('@')[0].replace('.', ' ').title(),
        picture=None if existing else '',
    )
    return redirect(start_session(user, safe_next_url(request.args.get('next'))))


@auth_bp.route('/auth/logout', methods=['POST'])
def logout():
    # POST only, so another site cannot sign you out with a plain link.
    session.clear()
    # ?next= lets "Use a different account" on an invite page come back to that invite after signing in.
    next_url = safe_next_url(request.args.get('next'))
    return redirect(url_for('auth.login_page', next=next_url) if next_url else url_for('auth.login_page'))


@auth_bp.route('/api/auth/me', methods=['GET'])
def get_current_user_api():
    user = g.get('current_user')
    if not user:
        return jsonify({'authenticated': False, 'user': None}), 401
    return jsonify({
        'authenticated': True,
        'user': user,
        'workspace': g.get('workspace'),
        'role': g.get('role'),
    })
