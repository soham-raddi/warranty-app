"""Scheduled jobs, triggered by Vercel Cron (see vercel.json).

Vercel calls these with "Authorization: Bearer <CRON_SECRET>" when the CRON_SECRET environment variable is
set, so nobody else can trigger them. Warranty reminder emails will be added here once an email provider
is configured.
"""
import hmac
import os

from flask import Blueprint, jsonify, request

from database import prune_old_invites, prune_rate_limits

cron_bp = Blueprint('cron', __name__)


def _authorized():
    secret = os.getenv('CRON_SECRET', '').strip()
    supplied = request.headers.get('Authorization', '')
    return bool(secret) and hmac.compare_digest(supplied, f'Bearer {secret}')


@cron_bp.route('/api/cron/daily', methods=['GET'])
def daily():
    if not _authorized():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'rate_limit_rows_removed': prune_rate_limits(),
        'old_invites_removed': prune_old_invites(),
    })
