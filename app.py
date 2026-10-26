import os
import secrets
import shutil
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load .env before anything else reads configuration. override=True makes .env win over stale
# variables left in the shell (python-dotenv otherwise silently keeps the shell's values).
# On Vercel there is no .env file; settings come from the project's environment variables.
# Tests set SKIP_DOTENV so they can never pick up real credentials (such as the live database) from .env.
if not os.getenv('SKIP_DOTENV'):
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)

from datetime import datetime, timedelta

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import emails
import inbound
from config import IS_PRODUCTION, MAX_UPLOAD_BYTES
from database import (
    get_appearance,
    accept_pending_invites,
    end_request,
    get_all_settings,
    get_membership_role,
    get_user_by_id,
    get_user_workspaces,
    get_workspace,
    relocate_upload_paths,
)
from routes import auth_bp, chat_bp, cron_bp, inbound_bp, init_oauth, pages_bp, receipts_bp, settings_bp, workspaces_bp
from routes.pages import contact_email

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, 'public')

# Static files live in public/ so Vercel can serve them from its CDN; locally Flask serves the same folder.
app = Flask(__name__, static_folder=os.path.join(PUBLIC_DIR, 'static'), static_url_path='/static')

secret_key = os.getenv('SECRET_KEY', '').strip()
if not secret_key:
    if IS_PRODUCTION:
        # Every server instance would invent a different key and sign people out at random.
        raise RuntimeError('SECRET_KEY must be set in the hosting environment variables.')
    secret_key = secrets.token_hex(32)
    print('WARNING: SECRET_KEY is not set. Using a random key; sessions will reset on restart.')
app.secret_key = secret_key

if IS_PRODUCTION or os.getenv('TRUST_PROXY', '').lower() in {'1', 'true', 'yes'}:
    # Vercel terminates HTTPS in front of the app; trust its forwarded headers so Flask sees the real
    # https:// address (for Google sign-in and invite links) and the visitor's IP (for rate limits).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config['PREFERRED_URL_SCHEME'] = 'https'

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
# The login cookie is only ever sent over https in production (override with SESSION_COOKIE_SECURE).
app.config['SESSION_COOKIE_SECURE'] = os.getenv(
    'SESSION_COOKIE_SECURE', 'true' if IS_PRODUCTION else 'false'
).lower() in {'1', 'true', 'yes'}

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES + 256 * 1024  # room for form fields around the file

LEGACY_UPLOAD_FOLDER = 'static/uploads'


def migrate_legacy_uploads():
    """Moves files saved under static/uploads by older versions into the private uploads folder."""
    if not os.path.isdir(LEGACY_UPLOAD_FOLDER):
        return

    for root, _, files in os.walk(LEGACY_UPLOAD_FOLDER):
        relative_dir = os.path.relpath(root, LEGACY_UPLOAD_FOLDER)
        target_dir = os.path.normpath(os.path.join(app.config['UPLOAD_FOLDER'], relative_dir))
        os.makedirs(target_dir, exist_ok=True)
        for name in files:
            target = os.path.join(target_dir, name)
            if not os.path.exists(target):
                shutil.move(os.path.join(root, name), target)

    relocate_upload_paths(LEGACY_UPLOAD_FOLDER, app.config['UPLOAD_FOLDER'])

    # Remove the legacy folder tree only if everything was moved out of it.
    for root, _, _ in os.walk(LEGACY_UPLOAD_FOLDER, topdown=False):
        if not os.listdir(root):
            os.rmdir(root)


if not IS_PRODUCTION:
    # Local development keeps files on disk; production uses Supabase Storage (see storage.py).
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    migrate_legacy_uploads()

# Initialize OAuth with Authlib
init_oauth(app)
if os.getenv('GOOGLE_CLIENT_ID', '').strip() and os.getenv('GOOGLE_CLIENT_SECRET', '').strip():
    print('Google sign-in: enabled')
else:
    print('Google sign-in: DISABLED (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set)')

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(receipts_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(workspaces_bp)
app.register_blueprint(cron_bp)
app.register_blueprint(inbound_bp)

# Paths anyone may open without signing in.
PUBLIC_PATHS = ('/login', '/auth', '/static', '/privacy', '/terms', '/manifest.webmanifest', '/api/cron/',
                '/unsubscribe/', '/api/inbound/email')
# Paths a signed-in person may use before they belong to any workspace.
NO_WORKSPACE_PATHS = ('/welcome', '/api/workspaces', '/api/auth/me', '/api/account')
UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


@app.before_request
def make_csp_nonce():
    g.csp_nonce = secrets.token_urlsafe(16)


@app.before_request
def reject_cross_site_requests():
    """CSRF protection: browsers send Origin (or Referer) on form posts and fetch calls; changes must come from
    this site. Requests without either header can't carry someone's cookie from another site."""
    if request.method not in UNSAFE_METHODS:
        return None
    source = request.headers.get('Origin') or request.headers.get('Referer')
    if not source:
        return None
    if urlparse(source).netloc != request.host:  # also rejects "Origin: null" from sandboxed pages
        return jsonify({'error': 'Cross-site request blocked'}), 403
    return None


@app.before_request
def enforce_authentication():
    """Protects all pages and APIs: requires a signed-in person and resolves their current workspace."""
    g.current_user = None
    g.workspace = None
    g.workspace_id = None
    g.role = None

    if request.path.startswith(PUBLIC_PATHS):
        return None

    is_api = request.path.startswith('/api/')
    user_id = session.get('user_id')
    user = get_user_by_id(user_id) if user_id else None
    if user and not user.get('is_active'):
        user = None

    if request.path.startswith('/join/'):
        # Invite links are public so people can see who invited them before signing in.
        g.current_user = user
        return None

    if not user:
        # Drop sessions belonging to removed or deactivated users
        session.clear()
        if is_api:
            return jsonify({'error': 'Please sign in to continue.'}), 401
        next_target = request.full_path if request.query_string else request.path
        return redirect(url_for('auth.login_page', next=next_target))

    g.current_user = user

    # Invites sent while someone is already signed in take effect on their next request.
    accept_pending_invites(user)

    workspace_id = session.get('workspace_id')
    role = get_membership_role(workspace_id, user['id']) if workspace_id else None
    if role is None:
        # Not (or no longer) a member of the remembered workspace: fall back to another one.
        workspaces = get_user_workspaces(user['id'])
        if workspaces:
            workspace_id, role = workspaces[0]['id'], workspaces[0]['role']
            session['workspace_id'] = workspace_id
        else:
            workspace_id = None
            session.pop('workspace_id', None)

    if workspace_id is None:
        if request.path.startswith(NO_WORKSPACE_PATHS):
            return None
        if is_api:
            return jsonify({'error': 'Create or join a workspace first.', 'code': 'no_workspace'}), 403
        return redirect(url_for('workspaces.welcome_page'))

    g.workspace_id = workspace_id
    g.workspace = get_workspace(workspace_id)
    g.role = role
    return None


# Only our own code plus the jsdelivr CDN (Bootstrap, icons, marked, DOMPurify) may run or load.
CONTENT_SECURITY_POLICY = '; '.join([
    "default-src 'self'",
    # Inline <script> blocks must carry this request's nonce, so injected scripts can never run.
    "script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "font-src 'self' data: https://cdn.jsdelivr.net",
    "img-src 'self' data: blob: https:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self' https://accounts.google.com",
    "object-src 'none'",
])


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Permissions-Policy', 'camera=(self), microphone=(), geolocation=()')
    if response.mimetype == 'text/html':
        # Receipts (PDFs, images) are left without a policy so the browser's viewer keeps working.
        response.headers.setdefault('Content-Security-Policy',
                                    CONTENT_SECURITY_POLICY.replace('{nonce}', g.get('csp_nonce') or 'none'))
    if IS_PRODUCTION:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    if request.path.startswith(('/api/', '/files/')) or g.get('current_user'):
        # Personal data must never be stored by shared caches.
        response.headers.setdefault('Cache-Control', 'private, no-store')
    return response


@app.teardown_appcontext
def release_database(_error=None):
    end_request()


@app.errorhandler(413)
def file_too_large(_error):
    limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    return jsonify({'error': f'File is too large. The maximum upload size is {limit_mb} MB.'}), 413


def _error_page(status, title, text):
    if request.path.startswith('/api/'):
        return jsonify({'error': text}), status
    return render_template('message.html', title=title, text=text, status=status), status


@app.errorhandler(404)
def not_found(_error):
    return _error_page(404, 'Page not found', "This page doesn't exist, or the file was deleted.")


@app.errorhandler(405)
def method_not_allowed(_error):
    return _error_page(405, 'Not available', "That address can't be opened directly.")


@app.errorhandler(500)
def server_error(_error):
    return _error_page(500, 'Something went wrong', 'Please try again in a moment.')


@app.route('/manifest.webmanifest')
def web_manifest():
    # Vercel serves this from public/ directly; this route covers local development.
    return send_from_directory(PUBLIC_DIR, 'manifest.webmanifest', mimetype='application/manifest+json')


@app.context_processor
def inject_current_user():
    """Makes the signed-in person, their workspace and role available across all Jinja2 templates."""
    user = g.get('current_user')
    workspace_id = g.get('workspace_id')

    # Rendered onto <body> so pages arrive with the right theme, accent and density (no flash of defaults).
    ui = get_appearance(user['id'] if user else None, workspace_id)
    return {
        'current_user': user,
        'current_workspace': g.get('workspace'),
        'current_role': g.get('role'),
        'my_workspaces': get_user_workspaces(user['id']) if user else [],
        'max_upload_bytes': MAX_UPLOAD_BYTES,
        'email_enabled': emails.EMAIL_ENABLED,
        'email_import_enabled': inbound.is_enabled(),
        'contact_email': contact_email(),
        'current_year': datetime.now().year,
        'ui': ui,
        'csp_nonce': g.get('csp_nonce', ''),
    }


if __name__ == '__main__':
    # Running app.py directly is local development: allow OAuth over plain http://127.0.0.1.
    os.environ.setdefault('AUTHLIB_INSECURE_TRANSPORT', '1')
    debug = os.getenv('FLASK_DEBUG', '0').lower() in {'1', 'true', 'yes'}
    app.run(debug=debug, port=int(os.getenv('PORT', '5000')))
