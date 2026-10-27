"""Reminder emails (what was sent, who unsubscribed) and purchases imported from forwarded emails."""
import json
from datetime import datetime, timedelta

from .connection import conn, cursor, insert_returning_id


def workspaces_with_reminders_enabled():
    cursor.execute(
        "SELECT workspace_id FROM workspace_settings WHERE key = 'reminders_enabled' AND value = '1' ORDER BY workspace_id"
    )
    return [row[0] for row in cursor.fetchall()]


def reminder_already_sent(item_id, threshold_days):
    cursor.execute('SELECT 1 FROM reminder_log WHERE item_id = ? AND threshold_days = ?', (item_id, threshold_days))
    return cursor.fetchone() is not None


def record_reminders_sent(item_thresholds):
    """item_thresholds: (item_id, threshold_days) pairs that were just emailed."""
    now = datetime.now().isoformat(timespec='seconds')
    for item_id, threshold_days in item_thresholds:
        cursor.execute(
            'INSERT OR IGNORE INTO reminder_log (item_id, threshold_days, sent_at) VALUES (?, ?, ?)',
            (item_id, threshold_days, now)
        )
    conn.commit()


def opted_out_emails(workspace_id):
    cursor.execute('SELECT email FROM email_optouts WHERE workspace_id = ?', (workspace_id,))
    return {row[0] for row in cursor.fetchall()}


def set_reminder_optout(workspace_id, email, opted_out):
    """Unsubscribes (or resubscribes) one email address from a workspace's reminder emails."""
    clean_email = email.strip().lower()
    if opted_out:
        cursor.execute(
            'INSERT OR IGNORE INTO email_optouts (workspace_id, email, created_at) VALUES (?, ?, ?)',
            (workspace_id, clean_email, datetime.now().isoformat(timespec='seconds'))
        )
    else:
        cursor.execute('DELETE FROM email_optouts WHERE workspace_id = ? AND email = ?', (workspace_id, clean_email))
    conn.commit()


def workspace_by_inbound_token(token):
    cursor.execute("SELECT workspace_id FROM workspace_settings WHERE key = 'inbound_token' AND value = ?", (token,))
    row = cursor.fetchone()
    return row[0] if row else None


def pending_receipt_exists(source_email_id):
    cursor.execute('SELECT 1 FROM processed_emails WHERE email_id = ?', (source_email_id,))
    return cursor.fetchone() is not None


def add_pending_receipt(workspace_id, source_email_id, sender, subject, details, file_path):
    """Stores a purchase read from an email. Returns its id, or None if that email was already imported."""
    if pending_receipt_exists(source_email_id):
        return None
    cursor.execute('INSERT OR IGNORE INTO processed_emails (email_id, created_at) VALUES (?, ?)',
                   (source_email_id, datetime.now().isoformat(timespec='seconds')))
    pending_id = insert_returning_id(
        '''INSERT INTO pending_receipts (workspace_id, source_email_id, sender, subject, details, file_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (workspace_id, source_email_id, sender, (subject or '')[:300], json.dumps(details), file_path,
         datetime.now().isoformat(timespec='seconds'))
    )
    conn.commit()
    return pending_id


def list_pending_receipts(workspace_id):
    cursor.execute(
        '''SELECT id, sender, subject, details, created_at FROM pending_receipts
           WHERE workspace_id = ? ORDER BY created_at DESC''',
        (workspace_id,)
    )
    return [{'id': row[0], 'sender': row[1], 'subject': row[2], 'created_at': row[4], **json.loads(row[3])}
            for row in cursor.fetchall()]


def get_pending_receipt(workspace_id, pending_id):
    cursor.execute(
        'SELECT id, details, file_path FROM pending_receipts WHERE id = ? AND workspace_id = ?',
        (pending_id, workspace_id)
    )
    row = cursor.fetchone()
    return {'id': row[0], 'details': json.loads(row[1]), 'file_path': row[2]} if row else None


def delete_pending_receipt(workspace_id, pending_id):
    """Removes a pending purchase. Returns its stored file reference (so the caller can delete it), or False."""
    cursor.execute('SELECT file_path FROM pending_receipts WHERE id = ? AND workspace_id = ?', (pending_id, workspace_id))
    row = cursor.fetchone()
    if not row:
        return False
    cursor.execute('DELETE FROM pending_receipts WHERE id = ? AND workspace_id = ?', (pending_id, workspace_id))
    conn.commit()
    return row[0]


def prune_old_pending_receipts(days=30):
    """Removes purchases nobody reviewed for `days` days. Returns their file references."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec='seconds')
    cursor.execute('SELECT file_path FROM pending_receipts WHERE created_at < ?', (cutoff,))
    files = [row[0] for row in cursor.fetchall() if row[0]]
    cursor.execute('DELETE FROM pending_receipts WHERE created_at < ?', (cutoff,))
    cursor.execute('DELETE FROM processed_emails WHERE created_at < ?', (cutoff,))
    conn.commit()
    return files
