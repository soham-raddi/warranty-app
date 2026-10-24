"""Scheduled jobs, triggered by Vercel Cron (see vercel.json).

Vercel calls these with "Authorization: Bearer <CRON_SECRET>" when the CRON_SECRET environment variable is
set, so nobody else can trigger them. The daily job also emails warranty reminders for every workspace that
turned them on, when RESEND_API_KEY is set.
"""
import hmac
import os

from flask import Blueprint, jsonify, request

import emails
import reminders
import storage
from database import prune_old_invites, prune_old_pending_receipts, prune_rate_limits, workspaces_with_reminders_enabled

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
        'old_email_imports_removed': _prune_pending_imports(),
        'reminders': _send_due_reminders(),
    })


def _send_due_reminders():
    summary = {'emails_sent': 0, 'workspaces_failed': 0}
    if not emails.EMAIL_ENABLED:
        return summary
    for workspace_id in workspaces_with_reminders_enabled():
        try:
            if reminders.send_workspace_reminders(workspace_id, request.url_root)['sent']:
                summary['emails_sent'] += 1
        except emails.EmailError as error:
            # One family's bad address must not stop reminders for everyone else.
            print(f'Reminder email failed for workspace {workspace_id}: {error}')
            summary['workspaces_failed'] += 1
    return summary


def _prune_pending_imports():
    files = prune_old_pending_receipts()
    for ref in files:
        storage.delete(ref)
    return len(files)
