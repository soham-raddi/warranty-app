"""Where uploaded receipts and warranty cards are kept.

Locally files go in the uploads/ folder. When SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set
(e.g. on Vercel, whose disk is temporary), they go to a private Supabase Storage bucket instead and are
handed out through short-lived signed links.

Stored file references look like 'uploads/12/abc_receipt.jpg' (local) or 'supabase:12/abc_receipt.jpg'.
"""
import mimetypes
import re
import os
from urllib.parse import quote

import requests
from flask import abort, redirect, send_file

# Accept the URL as copied from the dashboard, e.g. with a trailing "/rest/v1/" or stray quotes.
SUPABASE_URL = re.sub(r'/rest/v1/?$', '', os.getenv('SUPABASE_URL', '').strip().strip('"\'').rstrip('/')).rstrip('/')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip().strip('"\'')
SUPABASE_BUCKET = os.getenv('SUPABASE_BUCKET', 'receipts').strip() or 'receipts'
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

LOCAL_ROOT = 'uploads'
SUPABASE_PREFIX = 'supabase:'
SIGNED_URL_SECONDS = 60
TIMEOUT_SECONDS = 30


class StorageError(Exception):
    pass


def _headers(extra=None):
    headers = {'apikey': SUPABASE_KEY}
    if not SUPABASE_KEY.startswith('sb_'):
        # Legacy service_role keys are JWTs and also go in Authorization. New "sb_secret_..." keys are not
        # JWTs and must only be sent as apikey (Supabase rejects them in the Authorization header).
        headers['Authorization'] = f'Bearer {SUPABASE_KEY}'
    headers.update(extra or {})
    return headers


def _object_url(kind, key):
    return f'{SUPABASE_URL}/storage/v1/object/{kind}{SUPABASE_BUCKET}/{quote(key)}'


def save(data, key, content_type=None):
    """Stores bytes under key (e.g. '12/abc_receipt.jpg') and returns the reference to keep in the database."""
    content_type = content_type or mimetypes.guess_type(key)[0] or 'application/octet-stream'
    if USE_SUPABASE:
        response = requests.post(
            _object_url('', key), data=data, timeout=TIMEOUT_SECONDS,
            headers=_headers({'Content-Type': content_type, 'x-upsert': 'true'}),
        )
        if response.status_code >= 300:
            raise StorageError(f'Upload to storage failed ({response.status_code})')
        return SUPABASE_PREFIX + key

    path = os.path.join(LOCAL_ROOT, *key.split('/'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(data)
    return path.replace('\\', '/')


def read(ref):
    """Returns the bytes of a stored file."""
    if ref.startswith(SUPABASE_PREFIX):
        response = requests.get(_object_url('authenticated/', ref[len(SUPABASE_PREFIX):]),
                                headers=_headers(), timeout=TIMEOUT_SECONDS)
        if response.status_code >= 300:
            raise StorageError(f'Could not read file from storage ({response.status_code})')
        return response.content
    with open(_safe_local_path(ref), 'rb') as handle:
        return handle.read()


def delete(ref):
    """Removes a stored file; missing files are ignored."""
    if not ref:
        return
    if ref.startswith(SUPABASE_PREFIX):
        requests.delete(f'{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}', timeout=TIMEOUT_SECONDS,
                        headers=_headers({'Content-Type': 'application/json'}),
                        json={'prefixes': [ref[len(SUPABASE_PREFIX):]]})
        return
    try:
        os.remove(_safe_local_path(ref))
    except (FileNotFoundError, PermissionError, ValueError):
        pass


def serve(ref, as_attachment=False):
    """A Flask response that shows the file: a short-lived signed link for Supabase, the file itself locally."""
    if not ref:
        abort(404)
    if ref.startswith(SUPABASE_PREFIX):
        key = ref[len(SUPABASE_PREFIX):]
        body = {'expiresIn': SIGNED_URL_SECONDS}
        if as_attachment:
            body['download'] = True
        response = requests.post(_object_url('sign/', key), json=body, headers=_headers(), timeout=TIMEOUT_SECONDS)
        if response.status_code >= 300:
            abort(404)
        signed = response.json().get('signedURL') or response.json().get('signedUrl') or ''
        if not signed.startswith('/storage/v1'):
            signed = '/storage/v1' + signed
        url = SUPABASE_URL + signed
        if as_attachment and 'download=' not in url:
            url += ('&' if '?' in url else '?') + 'download='
        return redirect(url)

    try:
        path = _safe_local_path(ref)
    except ValueError:
        abort(404)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=as_attachment)


def _safe_local_path(ref):
    """Resolves a local reference, refusing anything outside the uploads folder."""
    root = os.path.realpath(LOCAL_ROOT)
    path = os.path.realpath(ref)
    if os.path.commonpath([root, path]) != root:
        raise ValueError('Path outside the uploads folder')
    return path
