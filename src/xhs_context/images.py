"""Save note images separately, preserving the stored body and historical citations."""
from __future__ import annotations

import json

from .access import access_restriction, require_access, restrict_access
from .common import XhsError, digest, dumps, now
from .image_jobs import available, image_report
from .network import AnonymousFetcher, validate_url
from .parser import parse_note
from .processing import finish_content, store_image

STOP_ERRORS = {'ACCESS_RESTRICTED', 'RATE_LIMITED', 'ACCESS_BLOCKED', 'NETWORK_ERROR',
               'PROXY_ERROR', 'PROXY_CONFIGURATION', 'LOCAL_NETWORK_BLOCKED'}


class ImageWorker:
    def __init__(self, store, *, fetcher=None, stop_event=None):
        self.store, self.fetcher, self.stop_event = store, fetcher or AnonymousFetcher(), stop_event

    def recover(self):
        with self.store.write() as db:
            if available(db):
                db.execute("UPDATE image_items SET state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,error='INTERRUPTED',updated_at=? WHERE state='running'", (now(),))

    def check(self, job_id):
        if self.stop_event and self.stop_event.is_set():
            raise XhsError('IMAGE_PAUSED', 'Image acquisition stopped')
        with self.store.read() as db:
            require_access(db)
            if db.execute('SELECT state FROM image_jobs WHERE id=?', (job_id,)).fetchone()[0] != 'active':
                raise XhsError('IMAGE_PAUSED', 'Image acquisition paused')

    def checkpoint(self, job_id, note_id, payload):
        with self.store.write() as db:
            db.execute('UPDATE image_items SET payload=?,updated_at=? WHERE job_id=? AND note_id=?', (dumps(payload), now(), job_id, note_id))

    def collect(self, job_id, note_id, payload, prior):
        manifest = payload.get('manifest')
        if not manifest:
            # Reuse a known manifest without revisiting the note page.
            if prior.get('image_manifest_complete', True) and prior.get('expected_images') is not None and len(prior.get('images', [])) == prior['expected_images']:
                manifest = {'images': prior.get('images', []), 'expected_images': prior['expected_images'],
                            'note_type': prior.get('note_type', 'unknown'), 'source': 'saved_manifest'}
            else:
                self.check(job_id)
                raw, _, _ = self.fetcher.get(payload['source_url'])
                parsed = parse_note(raw.decode('utf-8', errors='replace'), note_id, payload['source_url'])
                if parsed.get('body_state') == 'unverified':
                    raise XhsError('IMAGE_LIST_UNVERIFIED', 'Only a feed preview was returned; open the note and save its gallery')
                manifest = {k: parsed.get(k) for k in ('images', 'expected_images', 'note_type')}
                manifest['source'] = 'anonymous_page'
            if len(manifest.get('images', [])) > 100:
                raise XhsError('IMAGE_LIST_TOO_LARGE', 'A note may contain at most 100 images')
            payload['manifest'] = manifest
            self.checkpoint(job_id, note_id, payload)
        known = {a.get('source_url'): a for a in prior.get('images', []) if a.get('source_url')}
        known.update({a.get('source_url'): a for a in payload.get('staged_images', []) if a.get('source_url')})
        downloaded = []
        for asset in manifest['images']:
            self.check(job_id)
            url = asset.get('source_url', '')
            old = known.get(url, asset)
            path = self.store.object_path(old['object_path']) if old.get('object_path') else None
            if path and path.is_file() and digest(path.read_bytes()) == old.get('sha256'):
                result = dict(old, asset_id=asset['asset_id'])
            else:
                try:
                    validate_url(url, media=True)
                    data, _, _ = self.fetcher.get(url, media=True)
                    self.check(job_id)
                    result = store_image(self.store, data, asset, ocr=None)
                except XhsError as exc:
                    if exc.code in STOP_ERRORS or exc.code == 'IMAGE_PAUSED':
                        raise
                    result = dict(asset, state='failed', error=exc.code, ocr_state='not_requested')
            downloaded.append(result)
            payload['staged_images'] = downloaded
            self.checkpoint(job_id, note_id, payload)
        manifest_complete = manifest.get('expected_images') is not None and len(manifest['images']) == manifest['expected_images']
        if not manifest_complete:
            # Partial gallery captures can add images, never erase previously saved files.
            seen_ids = {a['asset_id'] for a in downloaded}
            seen_objects = {a.get('sha256') for a in downloaded if a.get('sha256')}
            downloaded.extend(a for a in prior.get('images', []) if a.get('state') == 'downloaded'
                              and a['asset_id'] not in seen_ids and a.get('sha256') not in seen_objects)
        updated = dict(prior, images=downloaded, expected_images=manifest.get('expected_images'), image_manifest_complete=manifest_complete)
        old_available = {a['asset_id'] for a in prior.get('images', []) if a.get('state') == 'downloaded'
                         and a.get('object_path') and self.store.object_path(a['object_path']).is_file()}
        if any(a['asset_id'] in old_available and a.get('state') != 'downloaded' for a in downloaded):
            raise XhsError('INCOMPLETE_IMAGE_REFRESH', 'Previous images retained after an unsuccessful refresh')
        updated['note_type'] = manifest.get('note_type') or prior.get('note_type', 'unknown')
        updated = finish_content(updated)
        if not manifest_complete and 'IMAGE_LIST_INCOMPLETE' not in updated['gaps']:
            updated['gaps'].append('IMAGE_LIST_UNVERIFIED')
            updated['content_state'] = 'partial'
        updated['content_scope'] = 'body_and_images' if prior.get('body_state') == 'available' else 'images'
        image_gaps = [g for g in updated['gaps'] if g.startswith(('IMAGE_', 'THUMBNAIL_'))]
        complete = not image_gaps
        updated['media_state'] = ('available' if downloaded else 'none') if complete else 'partial'
        updated['images_source'] = prior.get('images_source', manifest['source']) if manifest['source'] == 'saved_manifest' else manifest['source']
        updated['images_raw_object'] = prior.get('images_raw_object') if manifest['source'] == 'saved_manifest' else None
        if not updated['images_raw_object']:
            updated['images_raw_object'] = self.store.put_object(dumps(manifest).encode('utf-8'), '.json')
        if 'IMAGES_CAPTURED_SEPARATELY' in prior.get('gaps', []):
            updated['gaps'].append('IMAGES_CAPTURED_SEPARATELY')
            updated['content_state'] = 'partial'
        updated['processing_state'] = 'ready' if complete else 'partial'
        return updated, image_gaps

    def once(self, job_id=None):
        with self.store.write() as db:
            if not available(db) or access_restriction(db):
                return False
            clause, args = (' AND i.job_id=?', [job_id]) if job_id else ('', [])
            row = db.execute("SELECT i.* FROM image_items i JOIN image_jobs j ON j.id=i.job_id WHERE j.state='active' AND i.state='queued' AND i.attempts<3" + clause + ' ORDER BY i.updated_at,i.note_id LIMIT 1', args).fetchone()
            if not row:
                return False
            db.execute("UPDATE image_items SET state='running',attempts=attempts+1,updated_at=? WHERE job_id=? AND note_id=?", (now(), row['job_id'], row['note_id']))
        job_id, note_id, payload = row['job_id'], row['note_id'], json.loads(row['payload'])
        try:
            self.check(job_id)
            with self.store.read() as db:
                note = db.execute('SELECT n.current_revision,r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=? AND n.deleted=0', (note_id,)).fetchone()
                if not note:
                    raise XhsError('NOTE_NOT_FOUND', 'Note was removed before image acquisition')
                revision, prior = note['current_revision'], json.loads(note['payload'])
            updated, gaps = self.collect(job_id, note_id, payload, prior)
            updated['images_body_revision'] = prior.get('images_body_revision', revision)
            self.check(job_id)
            with self.store.write() as db:
                require_access(db)
                if db.execute('SELECT state FROM image_jobs WHERE id=?', (job_id,)).fetchone()[0] != 'active':
                    raise XhsError('IMAGE_PAUSED', 'Image job paused before publishing')
                current = db.execute('SELECT current_revision,deleted FROM notes WHERE id=?', (note_id,)).fetchone()
                if current['deleted'] or current['current_revision'] != revision:
                    raise XhsError('REVISION_CONFLICT', 'Note changed during image acquisition; stored body was retained')
                new_revision = self.store.publish(updated, db=db)
                payload['result'] = {'revision_id': new_revision, 'saved_images': sum(a.get('state') == 'downloaded' for a in updated['images']), 'expected_images': updated.get('expected_images')}
                db.execute('UPDATE image_items SET payload=?,state=?,error=?,updated_at=? WHERE job_id=? AND note_id=?',
                           (dumps(payload), 'partial' if gaps else 'ready', ','.join(gaps) or None, now(), job_id, note_id))
        except Exception as exc:
            code = exc.code if isinstance(exc, XhsError) else type(exc).__name__
            with self.store.write() as db:
                paused = code == 'IMAGE_PAUSED'
                db.execute('UPDATE image_items SET state=?,error=?,attempts=attempts-?,updated_at=? WHERE job_id=? AND note_id=?', ('queued' if paused else 'failed', code, int(paused), now(), job_id, note_id))
                if code in {'ACCESS_RESTRICTED', 'RATE_LIMITED'}:
                    restrict_access(db, error=code, source='images')
                elif code in STOP_ERRORS:
                    db.execute("UPDATE image_jobs SET state='paused',error=?,updated_at=? WHERE id=?", (code, now(), job_id))
        with self.store.write() as db:
            report = image_report(db, job_id)
            if not any(report['items'].get(s) for s in ('queued', 'running')) and report['state'] == 'active':
                db.execute("UPDATE image_jobs SET state='finished',updated_at=? WHERE id=?", (now(), job_id))
        return True
