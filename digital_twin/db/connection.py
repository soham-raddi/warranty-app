"""Connections to SQLite (local) or Postgres (Supabase, when DATABASE_URL is set), one per server thread.

SQL in this package is written for SQLite; _Cursor translates it for Postgres."""
import os
import re
import sqlite3
import threading


# DATABASE_URL (Postgres, e.g. Supabase) is used when set; otherwise a local SQLite file, for development.
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()


IS_POSTGRES = DATABASE_URL.startswith(('postgres://', 'postgresql://'))


DB_PATH = os.getenv('DATABASE_PATH', 'inventory.db')


_local = threading.local()


if IS_POSTGRES:
    import psycopg

    DatabaseError = psycopg.Error
else:
    DatabaseError = sqlite3.Error


def _to_postgres(sql):
    """Translates the SQLite-flavoured SQL used in this module into Postgres."""
    sql = sql.replace('?', '%s').replace(' LIKE ', ' ILIKE ')
    if sql.lstrip().upper().startswith('INSERT OR IGNORE INTO'):
        sql = re.sub(r'INSERT OR IGNORE INTO', 'INSERT INTO', sql, count=1, flags=re.IGNORECASE)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return sql


class _Cursor:
    """A cursor that accepts this module's SQL on both SQLite and Postgres."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        if IS_POSTGRES:
            sql = _to_postgres(sql)
        self._raw.execute(sql, tuple(params))
        return self

    def __getattr__(self, name):
        if name == 'lastrowid' and IS_POSTGRES:
            raise AttributeError('Use insert_returning_id() instead of cursor.lastrowid')
        return getattr(self._raw, name)


def _open_connection():
    if IS_POSTGRES:
        # prepare_threshold=None: Supabase's connection pooler (transaction mode) can't use prepared statements.
        return psycopg.connect(DATABASE_URL, prepare_threshold=None, connect_timeout=10)
    return sqlite3.connect(DB_PATH, timeout=10)


def _thread_connection():
    """Each server thread gets its own connection, so concurrent requests never share a cursor."""
    conn_obj = getattr(_local, 'conn', None)
    if conn_obj is None or (IS_POSTGRES and conn_obj.closed):
        conn_obj = _open_connection()
        _local.conn = conn_obj
        _local.cursor = _Cursor(conn_obj.cursor())
    return conn_obj


class _ThreadLocalProxy:
    """Forwards attribute access to this thread's connection or cursor."""

    def __init__(self, attribute):
        self._attribute = attribute

    def __getattr__(self, name):
        _thread_connection()
        return getattr(getattr(_local, self._attribute), name)


conn = _ThreadLocalProxy('conn')


cursor = _ThreadLocalProxy('cursor')


def end_request():
    """Called after every request: ends any open transaction so pooled Postgres connections are released."""
    conn_obj = getattr(_local, 'conn', None)
    if conn_obj is None:
        return
    try:
        conn_obj.rollback()  # writes are committed explicitly; this only closes read transactions
    except DatabaseError:
        # A broken connection is replaced on the next request.
        try:
            conn_obj.close()
        except DatabaseError:
            pass
        _local.conn = None


def close_connection():
    """Closes this thread's connection (tests use it so the SQLite file can be deleted on Windows)."""
    conn_obj = getattr(_local, 'conn', None)
    if conn_obj is not None:
        try:
            conn_obj.close()
        except DatabaseError:
            pass
        _local.conn = None


def insert_returning_id(sql, params=()):
    """Runs an INSERT and returns the new row's id on either database."""
    if IS_POSTGRES:
        cursor.execute(sql.rstrip().rstrip(';') + ' RETURNING id', params)
        return cursor.fetchone()[0]
    cursor.execute(sql, params)
    return cursor.lastrowid
