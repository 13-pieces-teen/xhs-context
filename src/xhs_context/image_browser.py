"""Read image directories in the signed-in browser, then download CDN assets locally."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from .access import access_restriction, require_access, restrict_access
from .browser_retry import RETRYABLE, DELAYS, clock_ms
from .common import XhsError, dumps, fields, now, safe_id
from .image_jobs import (available, finish_idle_image_jobs, image_report,
                         resume_image_items, validate_snapshot)
from .invalid_sources import invalid_source, mark_invalid, skip_invalid_items

SOURCE_ERRORS = {'SOURCE_UNAVAILABLE', 'SOURCE_LINK_EXPIRED'}
PAGE_ERRORS = RETRYABLE | SOURCE_ERRORS | {
    'ACCESS_RESTRICTED', 'ACCESS_BLOCKED', 'NOTE_NOT_FOUND', 'BROWSER_PARSE_FAILED',
    'BROWSER_PAUSED', 'BROWSER_ERROR', 'IMAGE_LIST_UNAVAILABLE', 'IMAGE_LIST_TOO_LARGE'}
MAX_PENDING_DIRECTORIES = 2


def enable_image_browser(store, job_id):
    with store.write() as db:
        require_access(db)
        previous = image_report(db, job_id, store=store)
        skip_invalid_items(db)
        resume_image_items(store, db, job_id, was_active=previous['state'] == 'active', browser=True)
        db.execute("UPDATE image_jobs SET state='active',error=NULL,updated_at=? WHERE id=?", (now(), job_id))
        finish_idle_image_jobs(db)
        return image_report(db, job_id, store=store)


def _cutoff():
    return (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec='milliseconds')


def _expire(db, item):
    paused = item['job_state'] != 'active'
    attempts = max(0, item['attempts'] - int(paused))
    exhausted = not paused and attempts >= 3
    payload = json.loads(item['payload'])
    payload['browser_retry_at'] = 0
    db.execute("UPDATE image_items SET state=?,attempts=?,error=?,payload=?,updated_at=? WHERE job_id=? AND note_id=?",
               ('failed' if exhausted else 'queued', attempts, None if paused else 'BROWSER_TIMEOUT',
                dumps(payload), now(), item['job_id'], item['note_id']))
    if exhausted:
        db.execute("UPDATE image_jobs SET state='paused',error='BROWSER_TIMEOUT',updated_at=? WHERE id=?", (now(), item['job_id']))


def _pending_retry(db):
    row = db.execute("SELECT i.job_id,i.note_id,i.error,i.attempts,json_extract(i.payload,'$.browser_retry_at') AS retry_at "
                     "FROM image_items i JOIN image_jobs j ON j.id=i.job_id WHERE j.state='active' AND i.state='queued' "
                     "AND json_extract(i.payload,'$.browser_pending')=1 "
                     "AND json_extract(i.payload,'$.browser_retry_at') IS NOT NULL ORDER BY retry_at,i.note_id LIMIT 1").fetchone()
    return dict(row, max_attempts=3) if row else None


def next_image_page(store):
    with store.write() as db:
        if not available(db):
            return {'task': None}
        skip_invalid_items(db)
        finish_idle_image_jobs(db)
        restriction = access_restriction(db)
        if restriction:
            return {'task': None, 'access_restriction': restriction}
        for item in db.execute("SELECT i.*,j.state AS job_state FROM image_items i JOIN image_jobs j ON j.id=i.job_id "
                               "WHERE i.state='browser_running' AND i.updated_at<?", (_cutoff(),)).fetchall():
            _expire(db, item)
        # Body and image reads share one browser tab and one acquisition cadence.
        if (db.execute("SELECT 1 FROM job_items WHERE state='browser_running'").fetchone()
                or db.execute("SELECT 1 FROM image_items WHERE state='browser_running'").fetchone()):
            return {'task': None}
        row = db.execute("WITH backlog AS (SELECT job_id,count(*) AS pending FROM image_items "
                         "WHERE state IN ('queued','running') AND json_extract(payload,'$.manifest') IS NOT NULL "
                         "AND coalesce(json_extract(payload,'$.browser_pending'),0)<>1 GROUP BY job_id) "
                         "SELECT i.* FROM image_items i JOIN image_jobs j ON j.id=i.job_id "
                         "LEFT JOIN backlog b ON b.job_id=i.job_id "
                         "WHERE j.state='active' AND i.state='queued' AND i.attempts<3 "
                         "AND json_extract(i.payload,'$.browser_mode')=1 AND json_extract(i.payload,'$.browser_pending')=1 "
                         "AND coalesce(json_extract(i.payload,'$.browser_retry_at'),0)<=? "
                         "AND coalesce(b.pending,0)<? "
                         "ORDER BY (json_extract(i.payload,'$.browser_retry_at') IS NULL),i.updated_at,i.note_id LIMIT 1",
                         (clock_ms(), MAX_PENDING_DIRECTORIES)).fetchone()
        if not row:
            retry = _pending_retry(db)
            return {'task': None, **({'browser_retry': retry} if retry and retry['retry_at'] > clock_ms() else {})}
        payload = json.loads(row['payload'])
        payload.pop('browser_retry_at', None)
        payload['browser_lease'] = lease = uuid.uuid4().hex
        db.execute("UPDATE image_items SET state='browser_running',attempts=attempts+1,error=NULL,payload=?,updated_at=? "
                   "WHERE job_id=? AND note_id=?", (dumps(payload), now(), row['job_id'], row['note_id']))
        return {'task': {'kind': 'images', 'job_id': row['job_id'], 'note_id': row['note_id'],
                         'source_url': payload['source_url'], 'lease_id': lease}}


def check_image_page(store, request):
    fields(request, {'job_id', 'note_id', 'lease_id'}, {'job_id', 'note_id', 'lease_id'})
    job_id, note_id, lease = (safe_id(request[key], key) for key in ('job_id', 'note_id', 'lease_id'))
    with store.read() as db:
        if not available(db) or access_restriction(db) or invalid_source(db, note_id):
            return {'allowed': False}
        row = db.execute("SELECT i.payload,i.updated_at FROM image_items i JOIN image_jobs j ON j.id=i.job_id "
                         "WHERE i.job_id=? AND i.note_id=? AND i.state='browser_running' AND j.state='active'",
                         (job_id, note_id)).fetchone()
        payload = json.loads(row['payload']) if row else {}
        return {'allowed': bool(row and row['updated_at'] >= _cutoff() and payload.get('browser_mode')
                                and payload.get('browser_pending') and payload.get('browser_lease') == lease)}


def accept_image_page(store, job_id, request):
    fields(request, {'note_id', 'lease_id', 'snapshot', 'error'}, {'note_id', 'lease_id'})
    safe_id(job_id, 'job_id')
    note_id, lease = (safe_id(request[key], key) for key in ('note_id', 'lease_id'))
    if ('snapshot' in request) == ('error' in request):
        raise XhsError('INVALID_ARGUMENT', 'Provide either a snapshot or an error')
    if 'error' in request and (not isinstance(request['error'], str) or request['error'] not in PAGE_ERRORS):
        raise XhsError('INVALID_ARGUMENT', 'Unknown browser image error')
    with store.write() as db:
        row = db.execute("SELECT i.*,j.state AS job_state FROM image_items i JOIN image_jobs j ON j.id=i.job_id "
                         "WHERE i.job_id=? AND i.note_id=?", (job_id, note_id)).fetchone()
        if not row:
            raise XhsError('JOB_NOT_FOUND', 'Unknown browser image task')
        payload = json.loads(row['payload'])
        if not payload.get('browser_mode') or payload.get('browser_lease') != lease:
            raise XhsError('CONFLICT', 'Browser image task lease expired')
        # A real safety response remains significant even if the note was just
        # marked invalid. Its durable receipt prevents an old outbox re-lock.
        if (request.get('error') == 'ACCESS_RESTRICTED' and row['state'] in {'browser_running', 'skipped'}
                and payload.get('browser_restriction_reported') != lease):
            restrict_access(db, source='images_browser')
            payload['browser_restriction_reported'] = lease
            db.execute('UPDATE image_items SET payload=? WHERE job_id=? AND note_id=?', (dumps(payload), job_id, note_id))
            if invalid_source(db, note_id):
                skip_invalid_items(db)
                return {'accepted': True, 'restricted': True, 'skipped': True}
            db.execute("UPDATE image_items SET state='failed',error='ACCESS_RESTRICTED',updated_at=? WHERE job_id=? AND note_id=?", (now(), job_id, note_id))
            return {'accepted': True, 'restricted': True}
        if invalid_source(db, note_id):
            skip_invalid_items(db)
            finish_idle_image_jobs(db)
            return {'accepted': True, 'skipped': True, 'duplicate': row['state'] != 'browser_running'}
        if row['state'] != 'browser_running':
            return {'accepted': True, 'duplicate': True}
        if request.get('error') in SOURCE_ERRORS:
            mark_invalid(db, note_id, request['error'], payload['source_url'], job_id=job_id)
            finish_idle_image_jobs(db)
            return {'accepted': True, 'skipped': True, 'duplicate': False}
        if row['job_state'] != 'active' or access_restriction(db):
            db.execute("UPDATE image_items SET state='queued',attempts=MAX(0,attempts-1),error=NULL,updated_at=? WHERE job_id=? AND note_id=?", (now(), job_id, note_id))
            return {'accepted': True, 'paused': True}
        if row['updated_at'] < _cutoff():
            _expire(db, row)
            return {'accepted': True, 'expired': True}
        if 'snapshot' in request:
            manifest = validate_snapshot(request['snapshot'])
            if request['snapshot']['note_id'] != note_id:
                raise XhsError('URL_NOTE_MISMATCH', 'Gallery is for a different note')
            payload.update(manifest=manifest, browser_pending=False)
            payload.pop('refresh_manifest', None)
            # Capturing a directory and downloading assets have separate budgets.
            db.execute("UPDATE image_items SET payload=?,state='queued',attempts=0,error=NULL,updated_at=? WHERE job_id=? AND note_id=?",
                       (dumps(payload), now(), job_id, note_id))
        else:
            code = request['error']
            if code in RETRYABLE and row['attempts'] < 3:
                payload['browser_retry_at'] = clock_ms() + DELAYS[max(0, row['attempts'] - 1)] * 1000
                db.execute("UPDATE image_items SET state='queued',payload=?,error=?,updated_at=? WHERE job_id=? AND note_id=?",
                           (dumps(payload), code, now(), job_id, note_id))
                return {'accepted': True, 'browser_retry': _pending_retry(db)}
            paused = code == 'BROWSER_PAUSED'
            db.execute("UPDATE image_items SET state=?,error=?,attempts=MAX(0,attempts-?),updated_at=? WHERE job_id=? AND note_id=?",
                       ('queued' if paused else 'failed', None if paused else code, int(paused), now(), job_id, note_id))
            db.execute("UPDATE image_jobs SET state='paused',error=?,updated_at=? WHERE id=?", (None if paused else code, now(), job_id))
        return {'accepted': True, 'duplicate': False}
