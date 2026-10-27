"""Fixed-window counters for rate limits, stored in the database so every server instance shares them."""
from datetime import datetime

from .connection import conn, cursor


def hit_rate_limit(bucket, window_seconds):
    """Counts one hit for bucket in the current time window and returns the total so far."""
    window_start = int(datetime.now().timestamp()) // window_seconds * window_seconds
    cursor.execute(
        '''
        INSERT INTO rate_limits (bucket, window_start, hits) VALUES (?, ?, 1)
        ON CONFLICT (bucket, window_start) DO UPDATE SET hits = rate_limits.hits + 1
        RETURNING hits
        ''',
        (bucket, window_start)
    )
    hits = cursor.fetchone()[0]
    conn.commit()
    return hits


def prune_rate_limits(older_than_seconds=2 * 86400):
    cutoff = int(datetime.now().timestamp()) - older_than_seconds
    cursor.execute('DELETE FROM rate_limits WHERE window_start < ?', (cutoff,))
    conn.commit()
    return cursor.rowcount
