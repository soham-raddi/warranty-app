"""One-time copy of a local install (inventory.db + uploads/) into Supabase (Postgres + Storage).

Usage, from the project folder, with DATABASE_URL, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY set
(for example in .env):

    python scripts/migrate_to_supabase.py            # dry run: shows what would be copied
    python scripts/migrate_to_supabase.py --apply    # copies everything

Row ids are kept, so workspaces, members, items and chats stay linked. Receipt files are uploaded to the
private bucket and their references updated. It refuses to run if the Supabase database already has
users, so it can't overwrite live data.
"""
import argparse
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, '.env'), override=True)

if not os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')):
    sys.exit('DATABASE_URL must point to your Supabase Postgres database.')

import database  # noqa: E402  (creates the schema in Postgres)
import storage  # noqa: E402

if not storage.USE_SUPABASE:
    sys.exit('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to upload receipt files.')

# Copied in this order so rows they refer to already exist.
TABLES = ['users', 'workspaces', 'workspace_members', 'workspace_invites', 'workspace_settings',
          'appliances_v5', 'service_history', 'chat_messages']
TABLES_WITH_ID = {'users', 'workspaces', 'workspace_invites', 'appliances_v5', 'service_history', 'chat_messages'}


def sqlite_columns(source, table):
    return [row[1] for row in source.execute(f'PRAGMA table_info({table})')]


def postgres_columns(table):
    database.cursor.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_name = ? AND table_schema = current_schema()',
        (table,)
    )
    return {row[0] for row in database.cursor.fetchall()}


def upload_file(ref, workspace_id):
    """Uploads a local file and returns its new 'supabase:' reference (or None if the file is missing)."""
    if not ref or ref.startswith(storage.SUPABASE_PREFIX):
        return ref
    local_path = os.path.join(PROJECT_ROOT, ref)
    if not os.path.isfile(local_path):
        print(f'  ! missing file, reference cleared: {ref}')
        return None
    relative = ref.replace('\\', '/').split('uploads/', 1)[-1]
    if not relative.split('/', 1)[0].isdigit():
        relative = f'{workspace_id}/{relative}'  # files saved before workspaces existed
    with open(local_path, 'rb') as handle:
        return storage.save(handle.read(), relative)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='actually copy (default is a dry run)')
    parser.add_argument('--sqlite', default=os.getenv('DATABASE_PATH', 'inventory.db'), help='local database file')
    args = parser.parse_args()

    if not os.path.isfile(args.sqlite):
        sys.exit(f'Local database not found: {args.sqlite}')
    source = sqlite3.connect(f'file:{args.sqlite}?mode=ro', uri=True)

    database.cursor.execute('SELECT COUNT(*) FROM users')
    if database.cursor.fetchone()[0]:
        sys.exit('The Supabase database already has users. Refusing to copy over live data.')

    print(f'{"Copying" if args.apply else "Dry run: would copy"} from {args.sqlite} to Supabase\n')
    for table in TABLES:
        columns = [c for c in sqlite_columns(source, table) if c in postgres_columns(table)]
        rows = source.execute(f'SELECT {", ".join(columns)} FROM {table}').fetchall()
        print(f'{table}: {len(rows)} rows')
        if not args.apply or not rows:
            continue

        if table == 'appliances_v5':
            file_index = {c: i for i, c in enumerate(columns)}
            converted = []
            for row in rows:
                row = list(row)
                for column in ('file_path', 'warranty_card_path'):
                    if column in file_index:
                        row[file_index[column]] = upload_file(row[file_index[column]], row[file_index['workspace_id']])
                converted.append(row)
            rows = converted

        marks = ', '.join('?' for _ in columns)
        for row in rows:
            database.cursor.execute(f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({marks})', row)

        if table in TABLES_WITH_ID:
            # Continue numbering after the copied ids.
            database.cursor.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1))"
            )
        database.conn.commit()

    print('\nDone.' if args.apply else '\nNothing was changed. Run again with --apply to copy.')


if __name__ == '__main__':
    main()
