"""Explicit image acquisition jobs; body jobs and their completion state are independent."""
from __future__ import annotations

import json
import uuid

from .access import require_access
from .common import XhsError, digest, dumps, fields, integer, now, safe_id, text_field
from .network import validate_note_url, validate_url
from urllib.parse import urlsplit


def available(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_jobs'").fetchone())


def image_report(db, job_id, *, include_results=True):
    job = db.execute("SELECT * FROM image_jobs WHERE id=?", (job_id,)).fetchone() if available(db) else None
    if not job:
        raise XhsError("JOB_NOT_FOUND", "Unknown image job")
    counts = {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM image_items WHERE job_id=? GROUP BY state", (job_id,))}
    saved = db.execute("SELECT coalesce(sum(json_extract(payload,'$.result.saved_images')),0) FROM image_items WHERE job_id=?", (job_id,)).fetchone()[0]
    failures = [dict(r) for r in db.execute("SELECT error,count(*) AS count FROM image_items WHERE job_id=? AND error IS NOT NULL GROUP BY error ORDER BY count(*) DESC LIMIT 10", (job_id,))]
    results = [dict(r) for r in db.execute("SELECT note_id,state,error,json_extract(payload,'$.result.revision_id') AS revision_id,json_extract(payload,'$.result.saved_images') AS saved_images FROM image_items WHERE job_id=? ORDER BY note_id LIMIT 50", (job_id,))]
    report = {"job_id": job_id, "state": job['state'], "items": counts, "notes": sum(counts.values()),
            "images_saved": saved, "error": job['error'], "failures": failures,
            "created_at": job['created_at'], "updated_at": job['updated_at'], 'results': results, 'results_limited': sum(counts.values()) > 50,
            "complete": bool(counts) and counts.get('ready', 0) == sum(counts.values())}
    if not include_results:
        report.pop('results')
        report.pop('results_limited')
    return report


def image_reports(db):
    if not available(db):
        return []
    return [image_report(db, r[0], include_results=False) for r in db.execute("SELECT id FROM image_jobs ORDER BY created_at DESC LIMIT 5")]


def validate_snapshot(snapshot):
    fields(snapshot, {'note_id', 'page_url', 'images', 'image_indices', 'expected_images', 'note_type'}, {'note_id', 'page_url', 'images'})
    safe_id(snapshot['note_id'], 'note_id')
    validate_note_url(snapshot['page_url'], snapshot['note_id'])
    urls = snapshot['images']
    if not isinstance(urls, list) or not 1 <= len(urls) <= 100:
        raise XhsError('INVALID_ARGUMENT', 'Capture 1-100 images from the note gallery', exit_code=2)
    for url in urls:
        text_field(url, 'image URL', 10000, False)
        validate_url(url, media=True)
    if len(set(urls)) != len(urls):
        raise XhsError('INVALID_ARGUMENT', 'Duplicate gallery image URLs', exit_code=2)
    expected = snapshot.get('expected_images')
    if expected is not None:
        integer(expected, 'expected_images', len(urls), 100)
    if snapshot.get('note_type', 'unknown') not in {'image', 'video', 'unknown'}:
        raise XhsError('INVALID_ARGUMENT', 'Invalid note type', exit_code=2)
    indices = snapshot.get('image_indices', [None] * len(urls))
    if not isinstance(indices, list) or len(indices) != len(urls):
        raise XhsError('INVALID_ARGUMENT', 'Image indices must align with captured URLs', exit_code=2)
    for index in indices:
        if index is not None:
            integer(index, 'image_index', 1, expected or 100)
    actual = [i for i in indices if i is not None]
    if len(set(actual)) != len(actual):
        raise XhsError('INVALID_ARGUMENT', 'Duplicate image positions', exit_code=2)
    return {'images': [{'asset_id': f'img-{index}' if index is not None else 'img-' + digest(urlsplit(url).path)[:12],
                        'image_index': index, 'source_url': url, 'quality': 'page_default'}
                       for index, url in zip(indices, urls)], 'expected_images': expected,
            'note_type': snapshot.get('note_type', 'unknown'), 'source': 'browser_gallery'}


def create_image_job(store, request):
    fields(request, {'job_id', 'note_ids', 'collection_id', 'snapshot'})
    choices = [key for key in ('note_ids', 'collection_id', 'snapshot') if key in request]
    if len(choices) != 1:
        raise XhsError('INVALID_ARGUMENT', 'Choose note_ids, collection_id, or a gallery snapshot', exit_code=2)
    job_id = request.get('job_id', uuid.uuid4().hex)
    safe_id(job_id, 'job_id')
    manifest = validate_snapshot(request['snapshot']) if 'snapshot' in request else None
    note_ids = request.get('note_ids')
    if manifest:
        note_ids = [request['snapshot']['note_id']]
    if note_ids is not None:
        if not isinstance(note_ids, list) or not 1 <= len(note_ids) <= 1000:
            raise XhsError('INVALID_ARGUMENT', 'Provide 1-1000 note IDs', exit_code=2)
        for note_id in note_ids:
            safe_id(note_id, 'note_id')
        note_ids = list(dict.fromkeys(note_ids))
    else:
        safe_id(request['collection_id'], 'collection_id')
    request_hash = digest({k: v for k, v in request.items() if k != 'job_id'})
    store.initialize()
    with store.write() as db:
        require_access(db)
        existing = db.execute('SELECT request_hash FROM image_jobs WHERE id=?', (job_id,)).fetchone()
        if existing:
            if existing[0] != request_hash:
                raise XhsError('CONFLICT', 'Image job ID already belongs to another request')
            return image_report(db, job_id)
        if note_ids is None:
            if not db.execute('SELECT 1 FROM collections WHERE id=?', (request['collection_id'],)).fetchone():
                raise XhsError('COLLECTION_NOT_FOUND', 'Unknown collection')
            note_ids = [r[0] for r in db.execute("SELECT m.note_id FROM memberships m JOIN notes n ON n.id=m.note_id WHERE collection_id=? AND m.state<>'removed' AND n.deleted=0 ORDER BY m.note_id", (request['collection_id'],))]
        if not note_ids:
            raise XhsError('NO_NOTES', 'No archived notes in this scope')
        rows = []
        for note_id in note_ids:
            row = db.execute('SELECT source_url,current_revision FROM notes WHERE id=? AND deleted=0', (note_id,)).fetchone()
            if not row or not row['current_revision']:
                raise XhsError('NOTE_NOT_ARCHIVED', 'Sync this note into the local library first', details={'note_id': note_id})
            active = db.execute("SELECT i.job_id FROM image_items i JOIN image_jobs j ON j.id=i.job_id WHERE i.note_id=? AND j.state IN ('active','paused') AND i.state IN ('queued','running')", (note_id,)).fetchone()
            if active:
                raise XhsError('IMAGE_ALREADY_QUEUED', 'This note already has an unfinished image task', details={'job_id': active[0]})
            url = request['snapshot']['page_url'] if manifest else row['source_url']
            validate_note_url(url, note_id)
            payload = {'source_url': url}
            if manifest:
                payload['manifest'] = manifest
            rows.append((job_id, note_id, dumps(payload), now()))
        db.execute('INSERT INTO image_jobs VALUES (?,?,?,?,?,?)', (job_id, request_hash, 'active', None, now(), now()))
        db.executemany('INSERT INTO image_items(job_id,note_id,payload,updated_at) VALUES (?,?,?,?)', rows)
        return image_report(db, job_id)


def set_image_state(store, job_id, state):
    if state not in {'active', 'paused'}:
        raise XhsError('INVALID_ARGUMENT', 'Invalid image job state', exit_code=2)
    with store.write() as db:
        image_report(db, job_id)
        if state == 'active':
            require_access(db)
            db.execute("UPDATE image_items SET state='queued',error=NULL WHERE job_id=? AND state IN ('failed','partial') AND attempts<3", (job_id,))
        db.execute('UPDATE image_jobs SET state=?,error=CASE WHEN ?=\'active\' THEN NULL ELSE error END,updated_at=? WHERE id=?', (state, state, now(), job_id))
        return image_report(db, job_id)
