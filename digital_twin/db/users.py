"""People who sign in with Google."""
from datetime import datetime

from .connection import conn, cursor, insert_returning_id


_USER_COLUMNS = 'id, google_id, email, name, picture, role, is_active, created_at, last_login'


def _row_to_user(row):
    if not row:
        return None
    return {
        'id': row[0],
        'google_id': row[1],
        'email': row[2],
        'name': row[3],
        'picture': row[4],
        'is_active': bool(row[6]),
        'created_at': row[7],
        'last_login': row[8],
    }


def get_user_by_id(user_id):
    cursor.execute(f'SELECT {_USER_COLUMNS} FROM users WHERE id = ?', (user_id,))
    return _row_to_user(cursor.fetchone())


def get_user_by_email(email):
    if not email:
        return None
    cursor.execute(f'SELECT {_USER_COLUMNS} FROM users WHERE LOWER(email) = LOWER(?)', (email.strip(),))
    return _row_to_user(cursor.fetchone())


def upsert_user(google_id, email, name=None, picture=None):
    clean_email = email.strip().lower()
    user = get_user_by_email(clean_email)
    now = datetime.now().isoformat()
    if user:
        cursor.execute('''
            UPDATE users
            SET google_id = COALESCE(?, google_id),
                name = COALESCE(?, name),
                picture = COALESCE(?, picture),
                last_login = ?
            WHERE id = ?
        ''', (google_id, name, picture, now, user['id']))
        conn.commit()
        return get_user_by_id(user['id'])

    user_id = insert_returning_id('''
        INSERT INTO users (google_id, email, name, picture, role, is_active, created_at, last_login)
        VALUES (?, ?, ?, ?, 'member', 1, ?, ?)
    ''', (google_id, clean_email, name or clean_email, picture or '', now, now))
    conn.commit()
    return get_user_by_id(user_id)
