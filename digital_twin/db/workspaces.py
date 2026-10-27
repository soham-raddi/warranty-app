"""Families (workspaces), their members and invites, ownership, and deleting workspaces and accounts."""
import hashlib
import secrets
from datetime import datetime, timedelta

from .connection import conn, cursor, insert_returning_id
from .users import _row_to_user, _USER_COLUMNS, get_user_by_email


WORKSPACE_ROLES = ('owner', 'admin', 'member')


INVITE_LIFETIME_DAYS = 7


def create_workspace(name, owner_user_id):
    """Creates a workspace with owner_user_id as its owner and returns its id."""
    cursor.execute('SELECT COUNT(*) FROM workspaces')
    is_first_workspace = cursor.fetchone()[0] == 0

    workspace_id = insert_returning_id(
        'INSERT INTO workspaces (name, created_by, created_at) VALUES (?, ?, ?)',
        (name.strip(), owner_user_id, datetime.now().isoformat())
    )
    cursor.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
        (workspace_id, owner_user_id, datetime.now().isoformat())
    )

    if is_first_workspace:
        # Data saved before workspaces existed belongs to the first workspace created on this install.
        _adopt_unassigned_data(workspace_id, owner_user_id)
    conn.commit()
    return workspace_id


def _adopt_unassigned_data(workspace_id, owner_user_id):
    cursor.execute('UPDATE appliances_v5 SET workspace_id = ? WHERE workspace_id IS NULL', (workspace_id,))
    cursor.execute(
        'UPDATE chat_messages SET workspace_id = ?, user_id = COALESCE(user_id, ?) WHERE workspace_id IS NULL',
        (workspace_id, owner_user_id)
    )
    cursor.execute('SELECT key, value FROM app_settings')
    for key, value in cursor.fetchall():
        cursor.execute(
            'INSERT OR IGNORE INTO workspace_settings (workspace_id, key, value) VALUES (?, ?, ?)',
            (workspace_id, key, value)
        )


def get_workspace(workspace_id):
    cursor.execute('SELECT id, name, created_by, created_at FROM workspaces WHERE id = ?', (workspace_id,))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1], 'created_by': row[2], 'created_at': row[3]} if row else None


def rename_workspace(workspace_id, name):
    cursor.execute('UPDATE workspaces SET name = ? WHERE id = ?', (name.strip(), workspace_id))
    conn.commit()
    return cursor.rowcount > 0


def get_user_workspaces(user_id):
    """Workspaces the user belongs to, oldest membership first."""
    cursor.execute('''
        SELECT w.id, w.name, m.role
        FROM workspace_members m
        JOIN workspaces w ON w.id = m.workspace_id
        WHERE m.user_id = ?
        ORDER BY m.joined_at ASC, w.id ASC
    ''', (user_id,))
    return [{'id': row[0], 'name': row[1], 'role': row[2]} for row in cursor.fetchall()]


def get_membership_role(workspace_id, user_id):
    cursor.execute(
        'SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?',
        (workspace_id, user_id)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def list_workspace_members(workspace_id):
    cursor.execute(f'''
        SELECT {', '.join('u.' + c.strip() for c in _USER_COLUMNS.split(','))}, m.role, m.joined_at
        FROM workspace_members m
        JOIN users u ON u.id = m.user_id
        WHERE m.workspace_id = ?
        ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, m.joined_at ASC
    ''', (workspace_id,))
    members = []
    for row in cursor.fetchall():
        user = _row_to_user(row[:9])
        user['role'] = row[9]
        user['joined_at'] = row[10]
        members.append(user)
    return members


def add_workspace_member(workspace_id, user_id, role='member'):
    cursor.execute(
        'INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)',
        (workspace_id, user_id, role, datetime.now().isoformat())
    )
    conn.commit()


def remove_workspace_member(workspace_id, user_id):
    role = get_membership_role(workspace_id, user_id)
    if role is None:
        return False, 'This person is not a member of the workspace'
    if role == 'owner':
        return False, 'The workspace owner cannot be removed'
    cursor.execute('DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?', (workspace_id, user_id))
    conn.commit()
    return True, 'Member removed'


def _hash_token(token):
    """Invite tokens are stored only as SHA-256 hashes, so a copy of the database cannot be used to join."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _new_token_and_expiry():
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(days=INVITE_LIFETIME_DAYS)).isoformat(timespec='seconds')
    return token, expires_at


def _is_expired(expires_at):
    # Invites created before links existed have no expiry date and stay valid until revoked.
    return bool(expires_at) and datetime.fromisoformat(expires_at) < datetime.now()


def create_invite(workspace_id, email, role, invited_by):
    """Invites an email address into a workspace. Returns (invite including its one-time token, error)."""
    clean_email = email.strip().lower()
    existing_user = get_user_by_email(clean_email)
    if existing_user and get_membership_role(workspace_id, existing_user['id']):
        return None, f'{clean_email} is already a member of this workspace.'

    cursor.execute(
        'SELECT id, expires_at, accepted_at FROM workspace_invites WHERE workspace_id = ? AND email = ?',
        (workspace_id, clean_email)
    )
    existing = cursor.fetchone()
    if existing and not existing[2] and not _is_expired(existing[1]):
        return None, f'{clean_email} has already been invited. Use "New link" on their pending invite to share it again.'

    token, expires_at = _new_token_and_expiry()
    if existing:
        # Re-inviting after the old link expired (or after a past invite was used) refreshes that invite.
        cursor.execute(
            '''UPDATE workspace_invites SET role = ?, invited_by = ?, token_hash = ?, expires_at = ?, created_at = ?,
                   accepted_at = NULL, accepted_by = NULL WHERE id = ?''',
            (role, invited_by, _hash_token(token), expires_at, datetime.now().isoformat(), existing[0])
        )
        invite_id = existing[0]
    else:
        invite_id = insert_returning_id(
            '''INSERT INTO workspace_invites (workspace_id, email, role, invited_by, created_at, token_hash, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (workspace_id, clean_email, role, invited_by, datetime.now().isoformat(), _hash_token(token), expires_at)
        )
    conn.commit()
    return {'id': invite_id, 'email': clean_email, 'role': role, 'token': token, 'expires_at': expires_at}, None


def regenerate_invite_token(workspace_id, invite_id):
    """Issues a fresh link for a pending invite (the old link stops working). Returns (token, expires_at) or None."""
    token, expires_at = _new_token_and_expiry()
    cursor.execute(
        'UPDATE workspace_invites SET token_hash = ?, expires_at = ? WHERE id = ? AND workspace_id = ? AND accepted_at IS NULL',
        (_hash_token(token), expires_at, invite_id, workspace_id)
    )
    conn.commit()
    return (token, expires_at) if cursor.rowcount else None


def list_invites(workspace_id):
    cursor.execute(
        '''SELECT id, email, role, created_at, expires_at FROM workspace_invites
           WHERE workspace_id = ? AND accepted_at IS NULL ORDER BY created_at ASC''',
        (workspace_id,)
    )
    return [
        {'id': row[0], 'email': row[1], 'role': row[2], 'created_at': row[3],
         'expires_at': row[4], 'expired': _is_expired(row[4])}
        for row in cursor.fetchall()
    ]


def delete_invite(workspace_id, invite_id):
    cursor.execute('DELETE FROM workspace_invites WHERE id = ? AND workspace_id = ? AND accepted_at IS NULL',
                   (invite_id, workspace_id))
    conn.commit()
    return cursor.rowcount > 0


def get_invite_by_token(token):
    """Looks up an invite link. Returns details for the join page, or None if the link is unknown."""
    if not token or len(token) > 200:
        return None
    cursor.execute('''
        SELECT i.id, i.workspace_id, i.email, i.role, i.expires_at, w.name, u.name, u.email, i.accepted_at
        FROM workspace_invites i
        JOIN workspaces w ON w.id = i.workspace_id
        LEFT JOIN users u ON u.id = i.invited_by
        WHERE i.token_hash = ?
    ''', (_hash_token(token),))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'id': row[0], 'workspace_id': row[1], 'email': row[2], 'role': row[3],
        'expires_at': row[4], 'expired': _is_expired(row[4]),
        'workspace_name': row[5], 'invited_by_name': row[6] or row[7] or 'Someone',
        'accepted': bool(row[8]),
    }


def accept_invite_token(token, user):
    """Joins the user to the invite's workspace and uses up the link. Returns (workspace_id, error)."""
    invite = get_invite_by_token(token)
    if not invite:
        return None, 'This invite link is not valid. It may have been used already or revoked.'
    if invite['accepted']:
        # Opening your own used link again (e.g. after joining via email at sign-in) just takes you there.
        if get_membership_role(invite['workspace_id'], user['id']):
            return invite['workspace_id'], None
        return None, 'This invite link has already been used. Ask for a new one.'
    if get_membership_role(invite['workspace_id'], user['id']):
        # Already a member (e.g. the inviter testing their own link): open the workspace but leave the
        # invite unused, because it is meant for someone else.
        return invite['workspace_id'], None
    if invite['expired']:
        return None, 'This invite link has expired. Ask for a new one.'

    cursor.execute(
        'INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)',
        (invite['workspace_id'], user['id'], invite['role'], datetime.now().isoformat())
    )
    _mark_invite_used(invite['id'], user['id'])
    conn.commit()
    return invite['workspace_id'], None


def _mark_invite_used(invite_id, user_id):
    # Used invites are kept (not deleted) so their link can still recognise the person who used it.
    cursor.execute(
        'UPDATE workspace_invites SET accepted_at = ?, accepted_by = ? WHERE id = ?',
        (datetime.now().isoformat(timespec='seconds'), user_id, invite_id)
    )


def accept_pending_invites(user):
    """Turns unexpired invites for the user's email into memberships. Returns the workspace ids joined."""
    cursor.execute(
        'SELECT id, workspace_id, role, expires_at FROM workspace_invites WHERE email = ? AND accepted_at IS NULL',
        (user['email'].lower(),)
    )
    invites = [row for row in cursor.fetchall() if not _is_expired(row[3])]
    joined = []
    for invite_id, workspace_id, role, _expires_at in invites:
        cursor.execute(
            'INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)',
            (workspace_id, user['id'], role, datetime.now().isoformat())
        )
        _mark_invite_used(invite_id, user['id'])
        joined.append(workspace_id)
    if invites:
        conn.commit()
    return joined


def prune_old_invites(days=30):
    """Removes invites that expired or were used more than `days` ago."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec='seconds')
    cursor.execute(
        'DELETE FROM workspace_invites WHERE (expires_at IS NOT NULL AND expires_at < ?) OR (accepted_at IS NOT NULL AND accepted_at < ?)',
        (cutoff, cutoff)
    )
    conn.commit()
    return cursor.rowcount


def transfer_ownership(workspace_id, from_user_id, to_user_id):
    """Makes another member the owner; the previous owner becomes an admin. Returns an error or None."""
    if get_membership_role(workspace_id, from_user_id) != 'owner':
        return 'Only the owner can transfer ownership'
    if to_user_id == from_user_id:
        return 'You are already the owner'
    if not get_membership_role(workspace_id, to_user_id):
        return 'That person is not a member of this workspace.'
    cursor.execute("UPDATE workspace_members SET role = 'admin' WHERE workspace_id = ? AND user_id = ?",
                   (workspace_id, from_user_id))
    cursor.execute("UPDATE workspace_members SET role = 'owner' WHERE workspace_id = ? AND user_id = ?",
                   (workspace_id, to_user_id))
    conn.commit()
    return None


def delete_workspace(workspace_id):
    """Deletes a workspace and everything in it. Returns the stored file references so callers can remove them."""
    cursor.execute('SELECT id, file_path, warranty_card_path FROM appliances_v5 WHERE workspace_id = ?', (workspace_id,))
    rows = cursor.fetchall()
    files = list(dict.fromkeys(ref for _id, file_path, card_path in rows for ref in (file_path, card_path) if ref))
    item_ids = [row[0] for row in rows]
    if item_ids:
        marks = ', '.join('?' for _ in item_ids)
        cursor.execute(f'DELETE FROM service_history WHERE item_id IN ({marks})', item_ids)
        cursor.execute(f'DELETE FROM reminder_log WHERE item_id IN ({marks})', item_ids)
    cursor.execute('SELECT file_path FROM pending_receipts WHERE workspace_id = ? AND file_path IS NOT NULL', (workspace_id,))
    files += [row[0] for row in cursor.fetchall()]
    for table in ('appliances_v5', 'chat_messages', 'workspace_settings', 'workspace_invites', 'workspace_members',
                  'email_optouts', 'pending_receipts'):
        cursor.execute(f'DELETE FROM {table} WHERE workspace_id = ?', (workspace_id,))
    cursor.execute('DELETE FROM workspaces WHERE id = ?', (workspace_id,))
    conn.commit()
    return files


def owned_workspaces_with_other_members(user_id):
    cursor.execute('''
        SELECT w.id, w.name FROM workspaces w
        JOIN workspace_members m ON m.workspace_id = w.id AND m.user_id = ? AND m.role = 'owner'
        WHERE EXISTS (SELECT 1 FROM workspace_members o WHERE o.workspace_id = w.id AND o.user_id != ?)
    ''', (user_id, user_id))
    return [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]


def delete_user_account(user_id):
    """Deletes an account: workspaces they own alone, their memberships, their chats and the user.

    Callers must first make sure the user owns no workspace that other people still use.
    Returns the stored file references of deleted workspaces.
    """
    files = []
    for workspace in get_user_workspaces(user_id):
        if workspace['role'] == 'owner':
            files += delete_workspace(workspace['id'])
    cursor.execute('DELETE FROM chat_messages WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM user_preferences WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM workspace_members WHERE user_id = ?', (user_id,))
    cursor.execute('UPDATE workspace_invites SET invited_by = NULL WHERE invited_by = ?', (user_id,))
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    return files


def workspace_member_emails(workspace_id):
    cursor.execute('''
        SELECT u.email FROM workspace_members m JOIN users u ON u.id = m.user_id
        WHERE m.workspace_id = ? AND u.is_active = 1
        ORDER BY m.joined_at
    ''', (workspace_id,))
    return [row[0] for row in cursor.fetchall()]
