"""Explicit image acquisition jobs; body jobs and their completion state are independent."""
from __future__ import annotations

import json
import uuid

from .access import require_access
from .common import (XhsError, decode_cursor, digest, dumps, encode_cursor, fields,
                     integer, now, safe_id, text_field)
from .image_files import local_image_file
from .network import validate_note_url, validate_url
from .invalid_sources import invalid_source, skip_invalid_items
from urllib.parse import urlsplit


def available(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_jobs'").fetchone())


def finish_idle_image_jobs(db):
    """Skipped notes have no work left, even when no worker item was leased."""
    if available(db):
        db.execute("UPDATE image_jobs SET state='finished',updated_at=? WHERE state='active' "
                   "AND NOT EXISTS (SELECT 1 FROM image_items i WHERE i.job_id=image_jobs.id "
                   "AND i.state IN ('queued','running','browser_running'))", (now(),))


def local_image_available(store, asset, *, cache=None):
    """Count confirmed downloads whose local object still passes verification."""
    if asset.get('state') != 'downloaded' or not asset.get('object_path'):
        return False
    if store is None:
        return True
    key = (asset['object_path'], asset.get('bytes'), asset.get('sha256'))
    if cache is not None and key in cache:
        return cache[key]
    valid = local_image_file(store, asset)[0] is not None
    if cache is not None:
        cache[key] = valid
    return valid


def image_note_state(store, note, *, cache=None):
    manifest = {'images': note.get('images', []), 'expected_images': note.get('expected_images'),
                'note_type': note.get('note_type', 'unknown'), 'source': 'saved_manifest',
                'image_manifest_complete': note.get('image_manifest_complete', True)}
    saved = sum(local_image_available(store, asset, cache=cache) for asset in manifest['images'])
    complete = complete_manifest(manifest) and saved == len(manifest['images']) and all(
        asset.get('quality') != 'thumbnail' for asset in manifest['images'])
    return {'manifest': manifest, 'complete': complete, 'saved': saved}


def _saved_images(store, note, payload, *, cache=None):
    # One logical gallery position may appear in the archive, its manifest and
    # a checkpoint. Count that position once, retaining any usable local copy.
    assets = [*note.get('images', []), *(payload.get('manifest') or {}).get('images', []),
              *payload.get('staged_images', [])]
    saved = set()
    for asset in assets:
        if local_image_available(store, asset, cache=cache):
            identifier = asset.get('asset_id', '')
            index = asset.get('image_index')
            if index is None and identifier.startswith('img-') and identifier[4:].isdigit():
                index = int(identifier[4:])
            key = ('index', index) if index is not None else \
                ('asset', identifier or asset.get('source_url') or asset['object_path'])
            saved.add(key)
    if not assets and store is None:
        return payload.get('result', {}).get('saved_images', 0)
    return len(saved)


def image_report(db, job_id, *, include_results=True, store=None, limit=50, cursor=None):
    integer(limit, 'limit', 1, 100)
    job = db.execute("SELECT * FROM image_jobs WHERE id=?", (job_id,)).fetchone() if available(db) else None
    if not job:
        raise XhsError("JOB_NOT_FOUND", "Unknown image job")
    after = ''
    if cursor is not None:
        page = decode_cursor(cursor, 'image-results')
        if page.get('job_id') != job_id or page.get('created_at') != job['created_at']:
            raise XhsError('CURSOR_STALE', 'Image results cursor belongs to another job')
        after = safe_id(page.get('after'), 'cursor note_id')
        if not db.execute('SELECT 1 FROM image_items WHERE job_id=? AND note_id=?', (job_id, after)).fetchone():
            raise XhsError('CURSOR_STALE', 'Image results cursor item is no longer available')
    counts = {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM image_items WHERE job_id=? GROUP BY state", (job_id,))}
    rows = db.execute("SELECT i.*,json_extract(r.payload,'$.images') AS note_images, "
                      "json_extract(r.payload,'$.expected_images') AS note_expected, "
                      "json_extract(r.payload,'$.image_manifest_complete') AS note_manifest_complete "
                      "FROM image_items i LEFT JOIN notes n ON n.id=i.note_id "
                      "LEFT JOIN revisions r ON r.id=n.current_revision WHERE i.job_id=? ORDER BY i.note_id", (job_id,)).fetchall()
    saved, repair = 0, 0
    progress = dict(waiting_gallery=0, reading_gallery=0, waiting_download=0, downloading=0,
                    partial=counts.get('partial', 0), failed=counts.get('failed', 0), skipped=counts.get('skipped', 0),
                    already_complete=0, current_note_id=None, current_saved_images=0, current_expected_images=None)
    results, current_priority, file_cache = [], 0, {}
    has_more = False
    for row in rows:
        payload = json.loads(row['payload'])
        note = dict(images=json.loads(row['note_images'] or '[]'), expected_images=row['note_expected'],
                    image_manifest_complete=row['note_manifest_complete'] != 0)
        count = _saved_images(store, note, payload, cache=file_cache)
        saved += count
        missing = bool(store is not None and row['state'] == 'ready' and not image_note_state(store, note, cache=file_cache)['complete'])
        repair += missing
        progress['already_complete'] += bool(row['state'] == 'ready' and payload.get('already_complete') and not missing)
        if row['state'] == 'browser_running':
            progress['reading_gallery'] += 1
        elif row['state'] == 'running':
            progress['downloading'] += 1
        elif row['state'] == 'queued':
            progress['waiting_gallery' if payload.get('browser_pending') else 'waiting_download'] += 1
        priority = {'running': 2, 'browser_running': 1}.get(row['state'], 0)
        if priority > current_priority:
            current_priority = priority
            progress.update(current_note_id=row['note_id'], current_saved_images=count,
                            current_expected_images=(payload.get('manifest') or {}).get('expected_images', note.get('expected_images')))
        if include_results and row['note_id'] > after:
            if len(results) < limit:
                results.append(dict(note_id=row['note_id'], state=row['state'], error=row['error'],
                                    revision_id=payload.get('result', {}).get('revision_id'), saved_images=count,
                                    needs_repair=missing))
            else:
                has_more = True
    progress['needs_repair'] = repair
    failures = [dict(r) for r in db.execute("SELECT error,count(*) AS count FROM image_items WHERE job_id=? AND error IS NOT NULL GROUP BY error ORDER BY count(*) DESC LIMIT 10", (job_id,))]
    browser_mode = bool(db.execute("SELECT 1 FROM image_items WHERE job_id=? AND json_extract(payload,'$.browser_mode')=1 LIMIT 1", (job_id,)).fetchone())
    report = {"job_id": job_id, "state": job['state'], "items": counts, "notes": sum(counts.values()), "browser_mode": browser_mode,
            "images_saved": saved, "progress": progress, "needs_repair": repair, "error": job['error'], "failures": failures,
            "created_at": job['created_at'], "updated_at": job['updated_at'], 'results': results,
            'results_limited': len(results) < sum(counts.values()),
            'next_cursor': encode_cursor({'v': 1, 'kind': 'image-results', 'job_id': job_id,
                                          'created_at': job['created_at'], 'after': results[-1]['note_id']}) if has_more else None,
            "complete": bool(counts) and not repair and counts.get('ready', 0) == sum(counts.values())}
    if not include_results:
        report.pop('results')
        report.pop('results_limited')
        report.pop('next_cursor')
    return report


def image_reports(db, *, store=None):
    if not available(db):
        return []
    return [image_report(db, r[0], include_results=False, store=store) for r in db.execute("SELECT id FROM image_jobs ORDER BY created_at DESC LIMIT 5")]


def validate_snapshot(snapshot):
    fields(snapshot, {'note_id', 'page_url', 'images', 'image_indices', 'expected_images', 'note_type'}, {'note_id', 'page_url', 'images'})
    safe_id(snapshot['note_id'], 'note_id')
    validate_note_url(snapshot['page_url'], snapshot['note_id'])
    urls = snapshot['images']
    if (not isinstance(urls, list) or len(urls) > 100
            or not urls and snapshot.get('expected_images') != 0):
        raise XhsError('INVALID_ARGUMENT', 'Capture up to 100 gallery images, or explicitly confirm zero', exit_code=2)
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


def create_image_job(store, request, *, browser_default=False):
    fields(request, {'job_id', 'note_ids', 'collection_id', 'snapshot', 'browser'})
    if 'browser' in request and not isinstance(request['browser'], bool):
        raise XhsError('INVALID_ARGUMENT', 'browser must be a boolean', exit_code=2)
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
        skip_invalid_items(db)
        finish_idle_image_jobs(db)
        existing = db.execute('SELECT request_hash FROM image_jobs WHERE id=?', (job_id,)).fetchone()
        if existing:
            if existing[0] != request_hash:
                raise XhsError('CONFLICT', 'Image job ID already belongs to another request')
            return image_report(db, job_id, store=store)
        if 'collection_id' in request:
            # Enabling the browser changes an old job's payload, not its original
            # request hash. The acquisition method does not change this scope.
            scope = {'collection_id': request['collection_id']}
            scope_hashes = (digest(scope), digest(dict(scope, browser=False)), digest(dict(scope, browser=True)))
            reusable = db.execute("SELECT id FROM image_jobs WHERE request_hash IN (?,?,?) AND state IN ('active','paused') "
                                  "ORDER BY created_at DESC LIMIT 1", scope_hashes).fetchone()
            if reusable:
                return dict(image_report(db, reusable['id'], store=store), reused=True)
        if note_ids is None:
            if not db.execute('SELECT 1 FROM collections WHERE id=?', (request['collection_id'],)).fetchone():
                raise XhsError('COLLECTION_NOT_FOUND', 'Unknown collection')
            note_ids = [r[0] for r in db.execute("SELECT m.note_id FROM memberships m JOIN notes n ON n.id=m.note_id WHERE collection_id=? AND m.state<>'removed' AND n.deleted=0 ORDER BY m.note_id", (request['collection_id'],))]
        if not note_ids:
            raise XhsError('NO_NOTES', 'No archived notes in this scope')
        rows = []
        for note_id in note_ids:
            row = db.execute('SELECT n.source_url,n.current_revision,r.payload FROM notes n LEFT JOIN revisions r ON r.id=n.current_revision WHERE n.id=? AND n.deleted=0', (note_id,)).fetchone()
            if not row or not row['current_revision']:
                raise XhsError('NOTE_NOT_ARCHIVED', 'Sync this note into the local library first', details={'note_id': note_id})
            invalid = invalid_source(db, note_id)
            local = image_note_state(store, json.loads(row['payload'] or '{}'))
            already_complete = manifest is None and local['complete']
            active = db.execute("SELECT i.job_id FROM image_items i JOIN image_jobs j ON j.id=i.job_id WHERE i.note_id=? AND j.state IN ('active','paused') AND i.state IN ('queued','running','browser_running')", (note_id,)).fetchone()
            if active and not invalid and not already_complete:
                raise XhsError('IMAGE_ALREADY_QUEUED', 'This note already has an unfinished image task', details={'job_id': active[0]})
            url = request['snapshot']['page_url'] if manifest else row['source_url']
            validate_note_url(url, note_id)
            payload = {'source_url': url}
            selected_manifest = manifest
            if selected_manifest is None and complete_manifest(local['manifest']):
                selected_manifest = local['manifest']
            if request.get('browser', browser_default and manifest is None):
                payload.update(browser_mode=True, browser_pending=selected_manifest is None)
            if selected_manifest is not None:
                payload['manifest'] = selected_manifest
            if manifest is not None:
                payload['explicit_snapshot'] = True
            if already_complete:
                payload['already_complete'] = True
                payload['result'] = {'revision_id': row['current_revision'], 'saved_images': local['saved'],
                                     'expected_images': local['manifest']['expected_images']}
            rows.append((job_id, note_id, dumps(payload), 'skipped' if invalid else 'ready' if already_complete else 'queued',
                         invalid['error'] if invalid else None, now()))
        db.execute('INSERT INTO image_jobs VALUES (?,?,?,?,?,?)', (job_id, request_hash, 'active', None, now(), now()))
        db.executemany('INSERT INTO image_items(job_id,note_id,payload,state,error,updated_at) VALUES (?,?,?,?,?,?)', rows)
        finish_idle_image_jobs(db)
        return image_report(db, job_id, store=store)


def start_image_job(store, request):
    """Public acquisition entry shared by the CLI and browser integration."""
    fields(request, {'job_id', 'note_ids', 'collection_id', 'snapshot', 'browser'})
    report = create_image_job(store, request, browser_default=True)
    if report.get('reused') and request.get('browser', True) and (report['state'] == 'paused' or not report['browser_mode']):
        report = dict(set_image_state(store, report['job_id'], 'active', browser=True), reused=True)
    return report


def complete_manifest(manifest):
    return bool(manifest is not None and manifest.get('expected_images') is not None
                and manifest.get('image_manifest_complete') is not False
                and all(asset.get('quality') != 'thumbnail' for asset in manifest.get('images', []))
                and len(manifest.get('images', [])) == manifest['expected_images'])


def resume_image_items(store, db, job_id, *, was_active, browser=False):
    """An explicit retry gets a fresh budget while keeping downloaded objects."""
    for row in db.execute("SELECT i.*,n.current_revision,r.payload AS note_payload FROM image_items i "
                          "LEFT JOIN notes n ON n.id=i.note_id LEFT JOIN revisions r ON r.id=n.current_revision "
                          "WHERE i.job_id=? AND i.state<>'skipped'", (job_id,)).fetchall():
        payload = json.loads(row['payload'])
        switching = browser and not payload.get('browser_mode')
        if browser:
            payload['browser_mode'] = True
            payload['resume_requested'] = True
        # Repeated clicks while the browser/worker is progressing are idempotent.
        if was_active and row['state'] == 'browser_running':
            continue
        if row['state'] == 'running' and not switching:
            continue
        local = image_note_state(store, json.loads(row['note_payload'] or '{}'))
        invalid = invalid_source(db, row['note_id'])
        if invalid:
            if row['state'] != 'ready' or not local['complete']:
                db.execute("UPDATE image_items SET state='skipped',error=?,updated_at=? WHERE job_id=? AND note_id=?",
                           (invalid['error'], now(), job_id, row['note_id']))
            continue
        # Recheck completed jobs: files may have been removed since publication.
        # Other jobs may also have filled these gaps while this task was paused.
        if local['complete'] and (row['state'] == 'ready' or not payload.get('explicit_snapshot')):
            payload.update(already_complete=True, browser_pending=False,
                           result={'revision_id': row['current_revision'], 'saved_images': local['saved'],
                                   'expected_images': local['manifest']['expected_images']})
            db.execute("UPDATE image_items SET state='ready',payload=?,error=NULL,updated_at=? WHERE job_id=? AND note_id=?",
                       (dumps(payload), now(), job_id, row['note_id']))
            continue
        repair_ready = row['state'] == 'ready'
        if repair_ready and complete_manifest(local['manifest']):
            payload['manifest'] = local['manifest']
            payload.pop('refresh_manifest', None)
        payload.pop('already_complete', None)
        retry = row['state'] in {'failed', 'partial', 'browser_running', 'ready'} or switching
        # Failed CDN URLs can expire even when the directory was complete.
        # A paused transfer with a good directory still reuses its checkpoint.
        stale_assets = row['state'] in {'failed', 'partial'} and (
            payload.get('browser_mode') or row['state'] == 'partial'
            or row['error'] in {'ACCESS_BLOCKED', 'INCOMPLETE_IMAGE_REFRESH'})
        if not stale_assets and payload.get('manifest') is None and complete_manifest(local['manifest']):
            payload['manifest'] = local['manifest']
            payload.pop('refresh_manifest', None)
        refresh = stale_assets or (retry or not was_active) and not complete_manifest(payload.get('manifest'))
        if refresh:
            payload.pop('manifest', None)
            payload['refresh_manifest'] = True
        if payload.get('browser_mode'):
            payload['browser_pending'] = payload.get('manifest') is None
        if retry or not was_active:
            payload.pop('browser_lease', None)
            payload.pop('browser_retry_at', None)
            payload.pop('worker_lease', None)
        db.execute("UPDATE image_items SET state='queued',payload=?,error=NULL,attempts=?,updated_at=? WHERE job_id=? AND note_id=?",
                   (dumps(payload), 0 if retry or not was_active else row['attempts'], now(), job_id, row['note_id']))


def set_image_state(store, job_id, state, *, browser=False):
    if state not in {'active', 'paused'}:
        raise XhsError('INVALID_ARGUMENT', 'Invalid image job state', exit_code=2)
    with store.write() as db:
        previous = image_report(db, job_id, store=store)
        skip_invalid_items(db)
        if state == 'active':
            require_access(db)
            resume_image_items(store, db, job_id, was_active=previous['state'] == 'active', browser=browser)
        db.execute('UPDATE image_jobs SET state=?,error=CASE WHEN ?=\'active\' THEN NULL ELSE error END,updated_at=? WHERE id=?', (state, state, now(), job_id))
        finish_idle_image_jobs(db)
        return image_report(db, job_id, store=store)
