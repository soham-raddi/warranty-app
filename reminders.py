"""Warranty expiry reminder emails, sent through emails.py.

Each item is reminded about at most once per stage: when it enters the workspace's reminder window
(e.g. 30 days before), then 7 days before and 1 day before. reminder_log remembers what was sent, so the
daily cron job never repeats itself.
"""
from datetime import date

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

import emails
from database import (
    get_all_settings,
    get_inventory_analytics,
    get_workspace,
    opted_out_emails,
    record_reminders_sent,
    reminder_already_sent,
    workspace_member_emails,
)

EXTRA_STAGES = (7, 1)
MAX_RECIPIENTS = 10


def parse_recipients(raw_value):
    values = str(raw_value or '').replace(';', ',').replace('\n', ',').split(',')
    return [value.strip().lower() for value in values if value.strip()]


def _window_days(settings):
    try:
        return max(0, int(settings.get('reminder_days_before', '30')))
    except (TypeError, ValueError):
        return 30


def _stage_for(days_remaining, window):
    """The tightest stage an item is in, e.g. 5 days left with a 30-day window -> the 7-day stage."""
    stages = sorted({window, *(stage for stage in EXTRA_STAGES if stage < window)})
    return next((stage for stage in stages if days_remaining <= stage), None)


def recipients_for(workspace_id, settings):
    """The workspace's chosen recipients, or every member when none are set, minus anyone who unsubscribed."""
    chosen = parse_recipients(settings.get('reminder_recipients'))
    unsubscribed = opted_out_emails(workspace_id)
    people = [email.lower() for email in (chosen or workspace_member_emails(workspace_id))]
    return [email for email in dict.fromkeys(people) if email not in unsubscribed][:MAX_RECIPIENTS]


def _serializer():
    return URLSafeSerializer(current_app.secret_key, salt='reminder-unsubscribe')


def unsubscribe_token(workspace_id, email):
    return _serializer().dumps([workspace_id, email.lower()])


def read_unsubscribe_token(token):
    """Returns (workspace_id, email) from an unsubscribe link, or None if the link was tampered with."""
    try:
        workspace_id, email = _serializer().loads(token)
        return int(workspace_id), str(email)
    except (BadSignature, TypeError, ValueError):
        return None


def send_workspace_reminders(workspace_id, app_url, only_new=True):
    """Emails one summary of warranties ending soon. only_new=False (the "Send now" button) includes every
    warranty in the window, not just those that reached a new stage. Returns a summary dict; raises
    emails.EmailError when sending fails."""
    settings = get_all_settings(workspace_id)
    window = _window_days(settings)
    items, _, _, _ = get_inventory_analytics(workspace_id)

    due, stages = [], []
    for item in items:
        days = item.get('days_remaining')
        if not isinstance(days, int) or days < 0:
            continue
        stage = _stage_for(days, window)
        if stage is None or (only_new and reminder_already_sent(item['id'], stage)):
            continue
        due.append(item)
        stages.append((item['id'], stage))

    if not due:
        return {'sent': 0, 'items': 0, 'window': window}
    recipients = recipients_for(workspace_id, settings)
    if not recipients:
        return {'sent': 0, 'items': len(due), 'window': window}

    due.sort(key=lambda item: item['days_remaining'])
    workspace = get_workspace(workspace_id) or {'name': 'your workspace'}
    # One email per person, each with its own unsubscribe link (also offered as one-click in mail apps).
    messages = []
    for email in recipients:
        unsubscribe_url = f"{app_url.rstrip('/')}/unsubscribe/{unsubscribe_token(workspace_id, email)}"
        subject, html_body, text_body = emails.reminder_email(workspace['name'], due, app_url, unsubscribe_url)
        messages.append({'to': [email], 'subject': subject, 'html': html_body, 'text': text_body, 'headers': {
            'List-Unsubscribe': f'<{unsubscribe_url}>',
            'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
        }})
    key = None
    if only_new:
        # The same reminders for the same day are never sent twice, even if the cron job is retried.
        key = f"reminders-{workspace_id}-{date.today().isoformat()}-" + '-'.join(f'{i}.{s}' for i, s in stages)
    emails.send_batch(messages, idempotency_key=key)
    record_reminders_sent(stages)
    return {'sent': len(recipients), 'items': len(due), 'window': window}
