"""Durable, bounded retries for temporary browser failures; never resume a paused job."""
import time

RETRYABLE = frozenset({'BROWSER_BUSY', 'BROWSER_TIMEOUT', 'BROWSER_CLOSED', 'BROWSER_INTERRUPTED'})
DELAYS = (5, 15)


def clock_ms():
    return int(time.time() * 1000)


def pending_retry(db, job_id=None):
    clause, params = (' AND i.job_id=?', [job_id]) if job_id else ('', [])
    row = db.execute("SELECT i.job_id,i.note_id,i.error,i.attempts,json_extract(i.payload,'$.browser_retry_at') AS retry_at "
                     "FROM job_items i JOIN jobs j ON j.id=i.job_id WHERE j.state='active' AND i.state='queued' AND i.error IS NOT NULL "
                     "AND json_extract(i.payload,'$.browser_retry_at') IS NOT NULL" + clause +
                     " ORDER BY retry_at,i.note_id LIMIT 1", params).fetchone()
    return dict(row, max_attempts=3) if row else None
