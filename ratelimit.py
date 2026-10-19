"""Simple rate limits stored in the database, so they hold across every server instance on Vercel.

Limits protect the Groq bill and stop abuse: receipt scans and chat messages per workspace or person,
and sign-in attempts per IP address. Set RATE_LIMITS_ENABLED=false to switch them off (e.g. in tests).
"""
import os
from functools import wraps

from flask import g, jsonify, request

from database import hit_rate_limit

WINDOWS = {'minute': 60, 'hourly': 3600, 'daily': 86400}


def enabled():
    return os.getenv('RATE_LIMITS_ENABLED', 'true').lower() in {'1', 'true', 'yes'}


def client_ip():
    # ProxyFix (enabled on Vercel) puts the visitor's real address in remote_addr.
    return request.remote_addr or 'unknown'


def check(name, key, **limits):
    """Counts a hit for each window (minute/hourly/daily=N). Returns an error message if any limit is exceeded."""
    if not enabled():
        return None
    for window, maximum in limits.items():
        if maximum and hit_rate_limit(f'{name}:{key}:{window}', WINDOWS[window]) > maximum:
            label = {'minute': 'a minute', 'hourly': 'an hour', 'daily': 'a day'}[window]
            return f'Too many requests. You can do this {maximum} times in {label}; please try again later.'
    return None


def limit(name, per_workspace=False, per_ip=False, **limits):
    """Decorator: limits a route per signed-in person (default), per workspace, or per IP address."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if per_ip:
                key = f'ip:{client_ip()}'
            elif per_workspace and g.get('workspace_id'):
                key = f'ws:{g.workspace_id}'
            elif g.get('current_user'):
                key = f'user:{g.current_user["id"]}'
            else:
                key = f'ip:{client_ip()}'
            error = check(name, key, **limits)
            if error:
                return jsonify({'error': error}), 429
            return view(*args, **kwargs)
        return wrapped
    return decorator
