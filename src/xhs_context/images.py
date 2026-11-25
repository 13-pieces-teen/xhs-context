"""Save note images separately, preserving the stored body and historical citations."""
from __future__ import annotations

import json
import re
import uuid

from .access import access_restriction, require_access, restrict_access
from .common import (XhsError, bounded_page, decode_cursor, digest, dumps, encode_cursor,
                     envelope, fields, integer, now, safe_id)
from .image_files import inspect_gallery
from .image_jobs import available, finish_idle_image_jobs
from .invalid_sources import invalid_source, mark_invalid, skip_invalid_items
from .network import AnonymousFetcher, validate_url, validate_note_response
from .parser import parse_note
from .processing import finish_content, store_image

STOP_ERRORS = {'ACCESS_RESTRICTED', 'RATE_LIMITED', 'ACCESS_BLOCKED', 'NETWORK_ERROR',
               'PROXY_ERROR', 'PROXY_CONFIGURATION', 'LOCAL_NETWORK_BLOCKED'}

IMAGE_PLAN_VERSION = 'local-image-plan-v2'
_CONTENT = r'(?:答案|答题思路|解答|步骤|做法|教程|操作方法|公式|代码|详解|详细过程)'
_IMAGE = r'(?:图中|图里|图上|图内|图片|配图|长图|下图|上图|图表|图所示|图[一二三四五六七八九十0-9]+|图(?=$|[\s。，！？?!：:；;、]))'
_CLAUSE_END = r'。！？?!\n，,；;'
_EXPLICIT_IMAGE_REFERENCE = re.compile(
    _CONTENT + r'[^' + _CLAUSE_END + r']{0,32}?(?:见|看|在|放|整理|如)[^' + _CLAUSE_END + r']{0,16}?' + _IMAGE +
    r'|' + _IMAGE + r'[^' + _CLAUSE_END + r']{0,16}?' + _CONTENT)
_IMAGE_MENTION = re.compile(_IMAGE)
_NEGATED_REFERENCE = re.compile(r'不在|不是|没有|不需要|无需|不用|没在')


def _image_hints(blocks):
    """Author text pointers, never a claim about unseen images or body quality."""
    from .retrieval import HASHTAG
    hints = []
    for block in blocks:
        original = block['text']
        # Mask tags without shifting the original character positions.
        text = HASHTAG.sub(lambda m: ' ' * len(m.group()), original)
        def asserted_reference(match):
            # A literal question or negation is a mention, not a positive pointer.
            before = re.split('[' + _CLAUSE_END + ']', text[:match.start()])[-1][-16:]
            after = re.match('[^' + _CLAUSE_END + ']*[' + _CLAUSE_END + ']?', text[match.end():]).group()
            clause = before + match.group() + after
            return not _NEGATED_REFERENCE.search(clause) and not clause.rstrip().endswith(('?', '？'))
        explicit = next((m for m in _EXPLICIT_IMAGE_REFERENCE.finditer(text)
                         if asserted_reference(m)), None)
        match = explicit or _IMAGE_MENTION.search(text)
        if not match:
            continue
        start = max(0, match.start() - 40)
        end = min(len(original), max(start + 240, match.end()))
        location = json.loads(block['location'])
        hints.append({'block_id': block['block_id'], 'text': original[start:end],
                      'location': {'start': location['start'] + start, 'end': location['start'] + end},
                      'signal': 'explicit_content_reference' if explicit else 'image_mention',
                      'excerpt_only': start > 0 or end < len(original)})
    hints.sort(key=lambda h: (h['signal'] != 'explicit_content_reference', h['location']['start']))
    return hints[:2]


def _validate_image_plan(request):
    fields(request, {'note_ids', 'collection_id', 'limit', 'max_chars', 'cursor'})
    if 'cursor' in request:
        fields(request, {'cursor', 'max_chars'})
        cursor = decode_cursor(request['cursor'], 'image-plan')
        if cursor.get('plan_version') != IMAGE_PLAN_VERSION:
            raise XhsError('CURSOR_STALE', 'Image planning changed; repeat the original request')
        original = cursor.get('request')
        if not isinstance(original, dict) or 'cursor' in original:
            raise XhsError('INVALID_ARGUMENT', 'Invalid image plan cursor', exit_code=2)
        base, _, _ = _validate_image_plan(original)
        integer(cursor.get('offset'), 'cursor offset', 0, 1000000)
        integer(cursor.get('generation'), 'cursor generation', 0, 2**63 - 1)
        return base, cursor, request.get('max_chars', 6000)
    if ('note_ids' in request) == ('collection_id' in request):
        raise XhsError('INVALID_ARGUMENT', 'Choose note_ids or collection_id', exit_code=2)
    base = {'limit': integer(request.get('limit', 8), 'limit', 1, 50)}
    if 'note_ids' in request:
        ids = request['note_ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise XhsError('INVALID_ARGUMENT', 'Provide 1-100 note IDs', exit_code=2)
        base['note_ids'] = list(dict.fromkeys(safe_id(n, 'note_id') for n in ids))
    else:
        base['collection_id'] = safe_id(request['collection_id'], 'collection_id')
    return base, None, request.get('max_chars', 6000)


def image_plan(store, request):
    """Inspect local image gaps under the existing guard; never queue or fetch."""
    base, cursor, budget = _validate_image_plan(request)
    errors, items = [], []
    with store.read() as db:
        generation = store.generation(db)
        if cursor and cursor['generation'] != generation:
            raise XhsError('CURSOR_STALE', 'Library changed; repeat the original image plan')
        restriction = access_restriction(db)
        ids = base.get('note_ids')
        if ids is None:
            if not db.execute('SELECT 1 FROM collections WHERE id=?', (base['collection_id'],)).fetchone():
                raise XhsError('COLLECTION_NOT_FOUND', 'Unknown collection')
            ids = [r[0] for r in db.execute("SELECT m.note_id FROM memberships m JOIN notes n ON n.id=m.note_id "
                                          "WHERE m.collection_id=? AND m.state<>'removed' AND n.deleted=0 ORDER BY m.note_id",
                                          (base['collection_id'],))]
        for note_id in ids:
            row = db.execute('SELECT n.current_revision,r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision '
                             'WHERE n.id=? AND n.deleted=0', (note_id,)).fetchone()
            if not row:
                errors.append({'note_id': note_id, 'code': 'NOTE_NOT_FOUND'})
                continue
            note = json.loads(row['payload'])
            media, _ = inspect_gallery(store, note)
            hints = _image_hints(db.execute("SELECT block_id,text,location FROM evidence_blocks WHERE revision_id=? "
                                           "AND kind='body' ORDER BY ordinal", (row['current_revision'],)))
            invalid = invalid_source(db, note_id)
            candidate = not invalid and media['state'] not in {'available', 'none_expected'}
            explicit = any(h['signal'] == 'explicit_content_reference' for h in hints)
            priority = 'source_unavailable' if invalid else 'explicit_body_reference' if candidate and explicit else 'image_gap' if candidate else 'already_resolved'
            items.append({'note_id': note_id, 'revision_id': row['current_revision'], 'title': note.get('title', ''),
                          'source_url': note.get('canonical_url', note.get('source_url', '').split('?')[0]),
                          'body_state': note.get('body_state', 'unknown'), 'media': media, 'body_image_hints': hints,
                          'fetch_candidate': bool(candidate), 'priority': priority, 'gaps': note.get('gaps', []),
                          **({'invalid_source': invalid} if invalid else {})})
    priorities = {'explicit_body_reference': 0, 'image_gap': 1, 'already_resolved': 2, 'source_unavailable': 3}
    items.sort(key=lambda i: (priorities[i['priority']], i['note_id']))
    # A deleted image file can change candidate order without a database write.
    state_hash = digest(items)
    if cursor and cursor.get('state_hash') != state_hash:
        raise XhsError('CURSOR_STALE', 'Local image state changed; repeat the original image plan')
    offset = cursor['offset'] if cursor else 0
    integer(offset, 'cursor offset', 0, len(items))
    def cursor_at(index):
        return encode_cursor({'v': 1, 'kind': 'image-plan', 'plan_version': IMAGE_PLAN_VERSION,
                              'request': base, 'offset': index, 'generation': generation, 'state_hash': state_hash})
    def response(selected, next_cursor):
        next_index = offset + len(selected)
        return envelope({'generation': generation, 'scope': {k: v for k, v in base.items() if k != 'limit'},
                         'notes': len(items), 'fetch_candidates': sum(i['fetch_candidate'] for i in items),
                         'explicit_reference_candidates': sum(i['priority'] == 'explicit_body_reference' for i in items),
                         'access_restriction': restriction, 'acquisition_blocked': restriction is not None,
                         'items': selected, 'errors': errors,
                         'next_cursor': next_cursor or (cursor_at(next_index) if next_index < len(items) else None)},
                        partial=bool(errors))
    return bounded_page(items[:offset + base['limit']], offset, response, budget, cursor_at)


def _body_image_context(note):
    return digest({key: note.get(key) for key in ('note_id', 'title', 'body', 'body_state', 'raw_object')})


def _verified_image(store, asset):
    if asset.get('state') != 'downloaded' or not asset.get('sha256') or not asset.get('object_path'):
        return False
    try:
        path = store.object_path(asset['object_path'])
        return path.is_file() and digest(path.read_bytes()) == asset['sha256']
    except (OSError, XhsError):
        return False


def _reliable_index(asset, expected):
    index = asset.get('image_index')
    return isinstance(index, int) and not isinstance(index, bool) and isinstance(expected, int) and 1 <= index <= expected


def _capture_context(store, prior, payload, manifest):
    """Keep partial captures within one observed body, total and gallery generation."""
    body = _body_image_context(prior)
    expected = manifest.get('expected_images')
    prior_images = [dict(asset) for asset in prior.get('images', [])]
    for candidate in (payload.get('image_capture_context'), prior.get('images_capture_context')):
        if isinstance(candidate, dict) and candidate.get('body') == body and candidate.get('expected') == expected:
            return dict(candidate), prior_images
    # Older captures did not carry a context token. Their stored body revision
    # can establish that their indexed images still belong to this body version.
    if (not prior.get('images_capture_context') and prior.get('images_source') == 'browser_gallery'
            and expected is not None and prior.get('expected_images') == expected and prior.get('images_body_revision')):
        with store.read() as db:
            original = db.execute('SELECT payload FROM revisions WHERE id=?', (prior['images_body_revision'],)).fetchone()
        if original and _body_image_context(json.loads(original[0])) == body:
            context = {'id': uuid.uuid4().hex, 'body': body, 'expected': expected}
            for asset in prior_images:
                asset['gallery_context'] = context['id']
            return context, prior_images
    return {'id': uuid.uuid4().hex, 'body': body, 'expected': expected}, prior_images


class ImageWorker:
    def __init__(self, store, *, fetcher=None, stop_event=None):
        self.store, self.fetcher, self.stop_event = store, fetcher or AnonymousFetcher(), stop_event

    def recover(self):
        with self.store.write() as db:
            if available(db):
                skip_invalid_items(db)
                db.execute("UPDATE image_items SET state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,error='INTERRUPTED',updated_at=? WHERE state='running'", (now(),))
                finish_idle_image_jobs(db)

    def require_source(self, db, note_id):
        invalid = invalid_source(db, note_id)
        if invalid:
            raise XhsError('IMAGE_SOURCE_INVALID', 'Known unavailable note must not be fetched', details=invalid)

    def check(self, job_id, note_id, lease=None):
        if self.stop_event and self.stop_event.is_set():
            raise XhsError('IMAGE_PAUSED', 'Image acquisition stopped')
        with self.store.read() as db:
            self.require_source(db, note_id)
            require_access(db)
            if db.execute('SELECT state FROM image_jobs WHERE id=?', (job_id,)).fetchone()[0] != 'active':
                raise XhsError('IMAGE_PAUSED', 'Image acquisition paused')
            if lease and not db.execute("SELECT 1 FROM image_items WHERE job_id=? AND note_id=? AND state='running' "
                                        "AND json_extract(payload,'$.worker_lease')=?", (job_id, note_id, lease)).fetchone():
                raise XhsError('IMAGE_PAUSED', 'Image acquisition lease replaced')

    def checkpoint(self, job_id, note_id, payload):
        with self.store.write() as db:
            self.require_source(db, note_id)
            if not db.execute("SELECT 1 FROM image_items WHERE job_id=? AND note_id=? AND state='running' "
                              "AND json_extract(payload,'$.worker_lease')=?", (job_id, note_id, payload['worker_lease'])).fetchone():
                raise XhsError('IMAGE_PAUSED', 'Image acquisition lease replaced')
            db.execute('UPDATE image_items SET payload=?,updated_at=? WHERE job_id=? AND note_id=?', (dumps(payload), now(), job_id, note_id))

    def collect(self, job_id, note_id, payload, prior):
        manifest = payload.get('manifest')
        if not manifest:
            if payload.get('browser_mode'):
                raise XhsError('IMAGE_LIST_UNAVAILABLE', 'Browser gallery capture is required before downloading')
            # Reuse a known manifest without revisiting the note page.
            if not payload.get('refresh_manifest') and prior.get('image_manifest_complete', True) and prior.get('expected_images') is not None and len(prior.get('images', [])) == prior['expected_images']:
                manifest = {'images': prior.get('images', []), 'expected_images': prior['expected_images'],
                            'note_type': prior.get('note_type', 'unknown'), 'source': 'saved_manifest'}
            else:
                self.check(job_id, note_id, payload['worker_lease'])
                try:
                    raw, _, final_url = self.fetcher.get(payload['source_url'])
                    validate_note_response(final_url, note_id)
                    parsed = parse_note(raw.decode('utf-8', errors='replace'), note_id, final_url)
                except XhsError as exc:
                    # Only a note-page failure establishes note unavailability.
                    # A missing CDN asset below remains an image-level failure.
                    if exc.code in {'SOURCE_UNAVAILABLE', 'SOURCE_LINK_EXPIRED'}:
                        with self.store.write() as db:
                            mark_invalid(db, note_id, exc.code, payload['source_url'], job_id=job_id)
                    raise
                if parsed.get('body_state') == 'unverified':
                    raise XhsError('IMAGE_LIST_UNVERIFIED', 'Only a feed preview was returned; open the note and save its gallery')
                manifest = {k: parsed.get(k) for k in ('images', 'expected_images', 'note_type')}
                manifest['source'] = 'anonymous_page'
            if len(manifest.get('images', [])) > 100:
                raise XhsError('IMAGE_LIST_TOO_LARGE', 'A note may contain at most 100 images')
            payload['manifest'] = manifest
            payload.pop('refresh_manifest', None)
            self.checkpoint(job_id, note_id, payload)
        context, prior_images = _capture_context(self.store, prior, payload, manifest)
        browser_capture = manifest.get('source') == 'browser_gallery'
        expected = manifest.get('expected_images')
        known = {a.get('source_url'): a for a in prior_images if a.get('source_url')}
        known.update({a.get('source_url'): a for a in payload.get('staged_images', []) if a.get('source_url')})
        staged = {a.get('source_url'): a for a in payload.get('staged_images', []) if a.get('source_url')}
        downloaded = []
        for asset in manifest['images']:
            self.check(job_id, note_id, payload['worker_lease'])
            url = asset.get('source_url', '')
            old = known.get(url, asset)
            path = self.store.object_path(old['object_path']) if old.get('object_path') else None
            if path and path.is_file() and digest(path.read_bytes()) == old.get('sha256'):
                result = dict(old, asset_id=asset['asset_id'], image_index=asset.get('image_index'))
            else:
                try:
                    validate_url(url, media=True)
                    data, _, _ = self.fetcher.get(url, media=True)
                    self.check(job_id, note_id, payload['worker_lease'])
                    result = store_image(self.store, data, asset, ocr=None)
                except XhsError as exc:
                    if exc.code in STOP_ERRORS or exc.code in {'IMAGE_PAUSED', 'IMAGE_SOURCE_INVALID'}:
                        raise
                    result = dict(asset, state='failed', error=exc.code, ocr_state='not_requested')
            if browser_capture:
                if _reliable_index(result, expected) and result.get('state') == 'downloaded':
                    conflict = any(previous.get('gallery_context') == context['id']
                                   and previous.get('image_index') == result['image_index']
                                   and previous.get('sha256') != result.get('sha256')
                                   and _verified_image(self.store, previous)
                                   for previous in [*prior_images, *staged.values()])
                    if conflict:
                        # A changed position reveals a new gallery version. Old
                        # positions remain saved, but cannot fill this version's gaps.
                        context['id'] = uuid.uuid4().hex
                        for current in downloaded:
                            current['gallery_context'] = context['id']
                result['gallery_context'] = context['id']
                payload['image_capture_context'] = dict(context)
            downloaded.append(result)
            staged[url] = result
            payload['staged_images'] = list(staged.values())
            self.checkpoint(job_id, note_id, payload)
        manifest_complete = expected is not None and len(manifest['images']) == expected
        if not manifest_complete:
            # Partial gallery captures can add images, never erase previously saved files.
            seen_ids = {a['asset_id'] for a in downloaded}
            # A checkpoint may contain a newer observation of an existing
            # position after an interrupted download; prefer it to the archive.
            for previous in [*staged.values(), *prior_images]:
                if previous['asset_id'] not in seen_ids and _verified_image(self.store, previous):
                    downloaded.append(dict(previous))
                    seen_ids.add(previous['asset_id'])
            # Counting files alone cannot prove a complete gallery: all positions
            # must be reliable, locally verified and from the same capture context.
            if (browser_capture and expected and manifest['images']
                    and all(_reliable_index(asset, expected) for asset in manifest['images'])):
                positions, ambiguous = {}, False
                for image in downloaded:
                    if image.get('gallery_context') != context['id'] or not _reliable_index(image, expected):
                        continue
                    if not _verified_image(self.store, image) or image.get('quality') == 'thumbnail':
                        continue
                    index = image['image_index']
                    if index in positions and positions[index].get('sha256') != image.get('sha256'):
                        ambiguous = True
                    positions[index] = image
                if not ambiguous and set(positions) == set(range(1, expected + 1)):
                    downloaded = [positions[index] for index in range(1, expected + 1)]
                    manifest_complete = True
        updated = dict(prior, images=downloaded, expected_images=manifest.get('expected_images'), image_manifest_complete=manifest_complete)
        if browser_capture:
            updated['images_capture_context'] = dict(context)
        elif manifest.get('source') != 'saved_manifest':
            updated.pop('images_capture_context', None)
        old_available = {a['asset_id'] for a in prior.get('images', []) if a.get('state') == 'downloaded'
                         and a.get('object_path') and self.store.object_path(a['object_path']).is_file()}
        if old_available and not downloaded or any(a['asset_id'] in old_available and a.get('state') != 'downloaded' for a in downloaded):
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
            if not available(db):
                return False
            skip_invalid_items(db)
            finish_idle_image_jobs(db)
            if access_restriction(db):
                return False
            clause, args = (' AND i.job_id=?', [job_id]) if job_id else ('', [])
            row = db.execute("SELECT i.* FROM image_items i JOIN image_jobs j ON j.id=i.job_id WHERE j.state='active' AND i.state='queued' AND i.attempts<3 "
                             "AND (coalesce(json_extract(i.payload,'$.browser_mode'),0)<>1 OR "
                             "(coalesce(json_extract(i.payload,'$.browser_pending'),0)<>1 AND json_extract(i.payload,'$.manifest') IS NOT NULL))"
                             + clause + ' ORDER BY i.updated_at,i.note_id LIMIT 1', args).fetchone()
            if not row:
                return False
            payload = json.loads(row['payload'])
            payload['worker_lease'] = uuid.uuid4().hex
            db.execute("UPDATE image_items SET state='running',attempts=attempts+1,payload=?,updated_at=? WHERE job_id=? AND note_id=?", (dumps(payload), now(), row['job_id'], row['note_id']))
        job_id, note_id = row['job_id'], row['note_id']
        try:
            self.check(job_id, note_id, payload['worker_lease'])
            with self.store.read() as db:
                note = db.execute('SELECT n.current_revision,r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=? AND n.deleted=0', (note_id,)).fetchone()
                if not note:
                    raise XhsError('NOTE_NOT_FOUND', 'Note was removed before image acquisition')
                revision, prior = note['current_revision'], json.loads(note['payload'])
            updated, gaps = self.collect(job_id, note_id, payload, prior)
            updated['images_body_revision'] = prior.get('images_body_revision', revision)
            self.check(job_id, note_id, payload['worker_lease'])
            with self.store.write() as db:
                self.require_source(db, note_id)
                require_access(db)
                if db.execute('SELECT state FROM image_jobs WHERE id=?', (job_id,)).fetchone()[0] != 'active':
                    raise XhsError('IMAGE_PAUSED', 'Image job paused before publishing')
                if not db.execute("SELECT 1 FROM image_items WHERE job_id=? AND note_id=? AND state='running' "
                                  "AND json_extract(payload,'$.worker_lease')=?", (job_id, note_id, payload['worker_lease'])).fetchone():
                    raise XhsError('IMAGE_PAUSED', 'Image acquisition lease replaced')
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
                owned = db.execute("SELECT 1 FROM image_items WHERE job_id=? AND note_id=? AND state='running' "
                                   "AND json_extract(payload,'$.worker_lease')=?", (job_id, note_id, payload['worker_lease'])).fetchone()
                if invalid_source(db, note_id):
                    skip_invalid_items(db)
                else:
                    db.execute("UPDATE image_items SET state=?,error=?,attempts=MAX(0,attempts-?),updated_at=? WHERE job_id=? AND note_id=? "
                               "AND state='running' AND json_extract(payload,'$.worker_lease')=?",
                               ('queued' if paused else 'failed', code, int(paused), now(), job_id, note_id, payload['worker_lease']))
                if code in {'ACCESS_RESTRICTED', 'RATE_LIMITED'}:
                    restrict_access(db, error=code, source='images')
                elif code in STOP_ERRORS and owned:
                    db.execute("UPDATE image_jobs SET state='paused',error=?,updated_at=? WHERE id=?", (code, now(), job_id))
        with self.store.write() as db:
            finish_idle_image_jobs(db)
        return True
