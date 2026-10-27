"""Each person's private assistant conversations."""

from .connection import conn, cursor


def _normalize_conversation_id(conversation_id):
    normalized = str(conversation_id or '').strip()
    return normalized if normalized else 'legacy'


def _rows_to_messages(rows):
    messages = [{'id': row[0], 'role': row[1], 'content': row[2], 'created_at': row[3]} for row in rows]
    messages.reverse()
    return messages


def save_chat_message(workspace_id, user_id, role, content, conversation_id='legacy'):
    if not role or content is None:
        return False

    cursor.execute(
        'INSERT INTO chat_messages (workspace_id, user_id, conversation_id, role, content) VALUES (?, ?, ?, ?, ?)',
        (workspace_id, user_id, _normalize_conversation_id(conversation_id), str(role), str(content))
    )
    conn.commit()
    return True


def get_chat_messages(workspace_id, user_id, conversation_id='legacy', limit=100):
    safe_limit = max(1, min(int(limit), 500))
    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE workspace_id = ? AND user_id = ? AND conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (workspace_id, user_id, _normalize_conversation_id(conversation_id), safe_limit)
    )
    return _rows_to_messages(cursor.fetchall())


def search_chat_messages(workspace_id, user_id, query, conversation_id='legacy', limit=120):
    safe_limit = max(1, min(int(limit), 500))
    query_text = (query or '').strip()
    if not query_text:
        return get_chat_messages(workspace_id, user_id, conversation_id=conversation_id, limit=safe_limit)

    cursor.execute(
        '''
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE workspace_id = ? AND user_id = ? AND conversation_id = ? AND content LIKE ?
        ORDER BY id DESC
        LIMIT ?
        ''',
        (workspace_id, user_id, _normalize_conversation_id(conversation_id), f"%{query_text}%", safe_limit)
    )
    return _rows_to_messages(cursor.fetchall())


def get_chat_conversations(workspace_id, user_id, query='', limit=100):
    safe_limit = max(1, min(int(limit), 500))
    query_text = (query or '').strip()
    search_filter = ''
    params = [workspace_id, user_id]
    if query_text:
        search_filter = '''WHERE EXISTS (
            SELECT 1 FROM mine m3 WHERE m3.conversation_id = c.conversation_id AND m3.content LIKE ?
        )'''
        params.append(f"%{query_text}%")
    params.append(safe_limit)

    cursor.execute(
        f'''
        WITH mine AS (
            SELECT * FROM chat_messages WHERE workspace_id = ? AND user_id = ?
        ),
        conversation_summaries AS (
            SELECT conversation_id, MIN(created_at) AS created_at, MAX(created_at) AS updated_at, COUNT(*) AS message_count
            FROM mine
            GROUP BY conversation_id
        )
        SELECT
            c.conversation_id,
            c.created_at,
            c.updated_at,
            c.message_count,
            (SELECT content FROM mine m1 WHERE m1.conversation_id = c.conversation_id AND m1.role = 'user'
             ORDER BY m1.id ASC LIMIT 1) AS title,
            (SELECT content FROM mine m2 WHERE m2.conversation_id = c.conversation_id
             ORDER BY m2.id DESC LIMIT 1) AS preview
        FROM conversation_summaries c
        {search_filter}
        ORDER BY c.updated_at DESC
        LIMIT ?
        ''',
        params
    )

    return [
        {
            'conversation_id': row[0],
            'created_at': row[1],
            'updated_at': row[2],
            'message_count': row[3],
            'title': row[4],
            'preview': row[5]
        }
        for row in cursor.fetchall()
    ]


def clear_chat_messages(workspace_id, user_id):
    """Deletes this person's chat history in this workspace (never anyone else's)."""
    cursor.execute('DELETE FROM chat_messages WHERE workspace_id = ? AND user_id = ?', (workspace_id, user_id))
    conn.commit()
    return True


def delete_chat_conversation(workspace_id, user_id, conversation_id):
    """Deletes one of this person's conversations (never anyone else's)."""
    cursor.execute(
        'DELETE FROM chat_messages WHERE workspace_id = ? AND user_id = ? AND conversation_id = ?',
        (workspace_id, user_id, conversation_id)
    )
    conn.commit()
    return cursor.rowcount > 0
