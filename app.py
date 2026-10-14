import os
import secrets
import shutil
from dotenv import load_dotenv

# Load .env before anything else reads configuration. override=True makes .env win over stale
# variables left in the shell (python-dotenv otherwise silently keeps the shell's values).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)

from flask import Flask, g, jsonify, redirect, request, session, url_for

from datetime import timedelta

from database import (
    accept_pending_invites,
    get_all_settings,
    get_membership_role,
    get_user_by_id,
    get_user_workspaces,
    get_workspace,
    relocate_upload_paths,
)
from routes import auth_bp, chat_bp, init_oauth, pages_bp, receipts_bp, settings_bp, workspaces_bp

app = Flask(__name__)

secret_key = os.getenv('SECRET_KEY', '').strip()
if not secret_key:
    # A random per-process key keeps sessions secure; users are signed out on restart.
    secret_key = secrets.token_hex(32)
    print('WARNING: SECRET_KEY is not set. Using a random key; sessions will reset on restart.')
app.secret_key = secret_key

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
# On a hosted (https) install set SESSION_COOKIE_SECURE=true so the login cookie is never sent over http.
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() in {'1', 'true', 'yes'}

# Uploads live outside static/ so they are only served to signed-in users via /files/...
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['WARRANTY_UPLOAD_FOLDER'] = 'uploads/warranty_cards'
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB per request

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


os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['WARRANTY_UPLOAD_FOLDER'], exist_ok=True)
migrate_legacy_uploads()

# Initialize OAuth with Authlib
init_oauth(app)
if os.getenv('GOOGLE_CLIENT_ID', '').strip() and os.getenv('GOOGLE_CLIENT_SECRET', '').strip():
    print('Google sign-in: enabled')
else:
    print('Google sign-in: DISABLED (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not found in .env)')

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(receipts_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(workspaces_bp)

# Paths a signed-in person may use before they belong to any workspace.
NO_WORKSPACE_PATHS = ('/welcome', '/api/workspaces', '/api/auth/me')


@app.before_request
def enforce_authentication():
    """Protects all pages and APIs: requires a signed-in person and resolves their current workspace."""
    g.current_user = None
    g.workspace = None
    g.workspace_id = None
    g.role = None

    # Allow static files and auth endpoints
    if request.path == '/login' or request.path.startswith('/auth') or request.path.startswith('/static'):
        return None

    is_api = request.path.startswith('/api/') or request.path == '/upload'
    user_id = session.get('user_id')
    user = get_user_by_id(user_id) if user_id else None
    if user and not user.get('is_active'):
        user = None

    if not user:
        # Drop sessions belonging to removed or deactivated users
        session.clear()
        if is_api:
            return jsonify({'error': 'Unauthorized. Please sign in.'}), 401
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


@app.errorhandler(413)
def file_too_large(_error):
    limit_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
    return jsonify({'error': f'File is too large. The maximum upload size is {limit_mb} MB.'}), 413


@app.context_processor
def inject_current_user():
    """Makes the signed-in person, their workspace and role available across all Jinja2 templates."""
    user = g.get('current_user')
    workspace_id = g.get('workspace_id')

    # Rendered onto <body> so pages arrive with the right theme, accent and density (no flash of defaults).
    ui = {'theme_mode': 'system', 'accent_color': 'blue', 'density': 'comfortable', 'reduced_motion': '0'}
    if workspace_id:
        settings = get_all_settings(workspace_id)
        ui = {key: settings.get(key, default) for key, default in ui.items()}
    return {
        'current_user': user,
        'current_workspace': g.get('workspace'),
        'current_role': g.get('role'),
        'my_workspaces': get_user_workspaces(user['id']) if user else [],
        'ui': ui,
    }


if __name__ == '__main__':
    # Running app.py directly is local development: allow OAuth over plain http://127.0.0.1.
    os.environ.setdefault('AUTHLIB_INSECURE_TRANSPORT', '1')
    debug = os.getenv('FLASK_DEBUG', '0').lower() in {'1', 'true', 'yes'}
    app.run(debug=debug, port=int(os.getenv('PORT', '5000')))
