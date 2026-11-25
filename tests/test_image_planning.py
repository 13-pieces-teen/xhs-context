"""Missing-image planning, checkpoint reporting and bounded browser prefetch."""
import pytest

from conftest import card, create, note
from test_images import Fetcher, capture, current, setup_note
from xhs_context.common import XhsError
from xhs_context.core import read
from xhs_context.image_browser import accept_image_page, enable_image_browser, next_image_page
from xhs_context.image_jobs import create_image_job, image_report, set_image_state, validate_snapshot
from xhs_context.images import ImageWorker, image_plan
from xhs_context.invalid_sources import mark_invalid
from xhs_context.jobs import receive_batch


def report(library, job, **options):
    with library.read() as db:
        return image_report(db, job['job_id'], **options)


def save_images(store, number=1):
    saved, _ = setup_note(store, number)
    job = create_image_job(store, capture(saved))
    assert ImageWorker(store, fetcher=Fetcher()).once()
    return saved, job


def accept(store, job, task, saved):
    return accept_image_page(store, job['job_id'], dict(note_id=task['note_id'], lease_id=task['lease_id'], **capture(saved)))


@pytest.mark.parametrize('scope', ['note_ids', 'collection_id'])
def test_new_browser_job_only_acquires_missing_images(store, scope):
    create(store, 'body', 2)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1), card(2)]})
    complete, _ = save_images(store)
    incomplete, _ = setup_note(store, 2)
    revision, prior = current(store, complete['note_id'])
    request = {'note_ids': [complete['note_id'], incomplete['note_id']]} if scope == 'note_ids' else {'collection_id': 'collection'}
    job = create_image_job(store, dict(request, browser=True))
    assert job['items'] == {'ready': 1, 'queued': 1}
    assert job['images_saved'] == 2 and job['progress']['already_complete'] == 1
    assert job['progress']['waiting_gallery'] == 1
    assert next_image_page(store)['task']['note_id'] == incomplete['note_id']
    assert current(store, complete['note_id']) == (revision, prior)


def test_fully_saved_or_confirmed_zero_job_finishes_without_acquisition(store):
    complete, _ = save_images(store)
    zero = note(2)
    store.publish(zero)
    job = create_image_job(store, {'note_ids': [complete['note_id'], zero['note_id']], 'browser': True})
    assert job['state'] == 'finished' and job['complete']
    assert job['items'] == {'ready': 2} and job['progress']['already_complete'] == 2
    assert not next_image_page(store)['task']
    assert not ImageWorker(store, fetcher=Fetcher()).once()


def test_known_complete_directory_queues_only_cdn_downloads(store):
    saved, _ = setup_note(store)
    manifest = validate_snapshot(capture(saved)['snapshot'])
    store.publish(dict(saved, images=manifest['images'], expected_images=2, image_manifest_complete=True))
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    assert job['progress']['waiting_download'] == 1 and not job['progress']['waiting_gallery']
    assert not next_image_page(store)['task']
    fetcher = Fetcher()
    assert ImageWorker(store, fetcher=fetcher).once()
    assert len(fetcher.calls) == 2 and all(media for _, media in fetcher.calls)
    assert report(store, job, store=store)['complete']


@pytest.mark.parametrize('damage', ['missing', 'empty', 'truncated', 'same_size'])
def test_resume_rechecks_ready_files_and_repairs_from_saved_directory(store, damage):
    saved, job = save_images(store)
    revision, archived = current(store, saved['note_id'])
    request = {'mode': 'asset', 'items': [{'note_id': saved['note_id'], 'revision_id': revision}]}
    path = store.object_path(archived['images'][0]['object_path'])
    if damage == 'missing':
        path.unlink()
    elif damage == 'empty':
        path.write_bytes(b'')
    elif damage == 'same_size':
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    else:
        path.write_bytes(b'broken')
    planned = image_plan(store, {'note_ids': [saved['note_id']]})['data']['items'][0]
    assert planned['fetch_candidate'] and planned['media']['saved_images'] == 1
    error = 'ASSET_MISSING' if damage == 'missing' else 'ASSET_CORRUPT'
    assert planned['media']['missing_local_files' if damage == 'missing' else 'corrupt_local_files'] == 1
    checked = read(store, request)
    assert checked['partial'] and checked['data']['errors'] == [
        {'note_id': saved['note_id'], 'asset_id': 'img-1', 'code': error}]
    gallery = checked['data']['items'][0]
    assert gallery['media_state'] == planned['media']['state'] == 'partial'
    assert 'path' not in gallery['assets'][0] and gallery['assets'][1]['path']
    before = report(store, job, store=store)
    assert not before['complete'] and before['needs_repair'] == 1
    assert before['images_saved'] == 1
    resumed = enable_image_browser(store, job['job_id'])
    assert resumed['items'] == {'queued': 1} and resumed['progress']['waiting_download'] == 1
    assert not next_image_page(store)['task']
    fetcher = Fetcher()
    assert ImageWorker(store, fetcher=fetcher).once()
    assert fetcher.calls == [('https://ci.xiaohongshu.com/0.png', True)]
    assert report(store, job, store=store)['complete']
    repaired = read(store, request)
    assert not repaired['partial'] and all(a['path'] for a in repaired['data']['items'][0]['assets'])
    assert not image_plan(store, {'note_ids': [saved['note_id']]})['data']['items'][0]['fetch_candidate']


def test_asset_read_handles_legacy_metadata_and_invalid_paths_per_item(store):
    obj = store.put_object(b'legacy bytes', '.png')
    legacy = dict(asset_id='old', object_path=obj['object_path'], state='downloaded')
    broken = dict(asset_id='bad', object_path='../outside.png', state='downloaded')
    saved = dict(note(), images=[legacy, broken], expected_images=2)
    revision = store.publish(saved)
    result = read(store, {'mode': 'asset', 'items': [{'note_id': saved['note_id'], 'revision_id': revision}]})
    assert result['partial'] and result['data']['errors'][0]['code'] == 'INVALID_ASSET_PATH'
    old, bad = result['data']['items'][0]['assets']
    assert old['path'] and 'path' not in bad


def test_ready_missing_files_cannot_revive_persistently_invalid_note(store):
    saved, job = save_images(store)
    _, archived = current(store, saved['note_id'])
    store.object_path(archived['images'][0]['object_path']).unlink()
    with store.write() as db:
        mark_invalid(db, saved['note_id'], 'SOURCE_UNAVAILABLE', saved['source_url'])
    resumed = enable_image_browser(store, job['job_id'])
    assert resumed['items'] == {'skipped': 1} and resumed['state'] == 'finished'
    assert not next_image_page(store)['task']
    assert not ImageWorker(store, fetcher=Fetcher()).once()


def test_checkpoint_immediately_counts_saved_images_and_current_download(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, capture(saved))
    observed = []
    def pause_second(url, media):
        if url.endswith('1.png'):
            for with_files in (False, True):
                progress = report(store, job, **({'store': store} if with_files else {}))
                assert progress['images_saved'] == 1
                assert progress['progress']['downloading'] == 1
                assert progress['progress']['current_note_id'] == saved['note_id']
                assert progress['progress']['current_saved_images'] == 1
                assert progress['progress']['current_expected_images'] == 2
                observed.append(progress)
            set_image_state(store, job['job_id'], 'paused')
    assert ImageWorker(store, fetcher=Fetcher(pause_second)).once()
    assert len(observed) == 2 and report(store, job, store=store)['images_saved'] == 1
    set_image_state(store, job['job_id'], 'active')
    assert ImageWorker(store, fetcher=Fetcher()).once()
    # Published images and checkpoints reference the same two logical assets.
    assert report(store, job)['images_saved'] == 2
    assert report(store, job, store=store)['images_saved'] == 2


def test_browser_prefetch_stops_at_two_and_download_releases_capacity(store):
    notes = [setup_note(store, number)[0] for number in range(1, 5)]
    job = create_image_job(store, {'note_ids': [n['note_id'] for n in notes], 'browser': True})
    by_id = {n['note_id']: n for n in notes}
    for _ in range(2):
        task = next_image_page(store)['task']
        accept(store, job, task, by_id[task['note_id']])
    assert not next_image_page(store)['task']
    assert report(store, job)['progress']['waiting_download'] == 2
    assert report(store, job)['progress']['waiting_gallery'] == 2
    assert ImageWorker(store, fetcher=Fetcher()).once(job['job_id'])
    assert next_image_page(store)['task'] is not None


def test_one_jobs_backlog_does_not_starve_another_job(store):
    notes = [setup_note(store, number)[0] for number in range(1, 4)]
    first = create_image_job(store, {'note_ids': [n['note_id'] for n in notes], 'browser': True})
    for saved in notes[:2]:
        task = next_image_page(store)['task']
        accept(store, first, task, saved)
    other, _ = setup_note(store, 9)
    second = create_image_job(store, {'note_ids': [other['note_id']], 'browser': True})
    assert next_image_page(store)['task']['job_id'] == second['job_id']


def test_one_jobs_retry_delay_does_not_starve_another_job(store):
    first_note, _ = setup_note(store)
    first = create_image_job(store, {'note_ids': [first_note['note_id']], 'browser': True})
    task = next_image_page(store)['task']
    accept_image_page(store, first['job_id'], dict(note_id=task['note_id'], lease_id=task['lease_id'], error='BROWSER_TIMEOUT'))
    other, _ = setup_note(store, 2)
    second = create_image_job(store, {'note_ids': [other['note_id']], 'browser': True})
    assert next_image_page(store)['task']['job_id'] == second['job_id']


def test_resume_of_old_queued_item_reuses_complete_archive_directory(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    manifest = validate_snapshot(capture(saved)['snapshot'])
    store.publish(dict(saved, images=manifest['images'], expected_images=2, image_manifest_complete=True))
    resumed = enable_image_browser(store, job['job_id'])
    assert resumed['progress']['waiting_download'] == 1
    assert not next_image_page(store)['task']
    assert ImageWorker(store, fetcher=Fetcher()).once()
    assert report(store, job)['complete']


def test_thumbnail_directory_still_needs_browser_full_size_images(store):
    saved, _ = setup_note(store)
    manifest = validate_snapshot(capture(saved)['snapshot'])
    for asset in manifest['images']:
        asset['quality'] = 'thumbnail'
    store.publish(dict(saved, images=manifest['images'], expected_images=2, image_manifest_complete=True))
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    assert job['progress']['waiting_gallery'] == 1
    assert next_image_page(store)['task']['job_id'] == job['job_id']


@pytest.mark.parametrize('paused', [False, True])
def test_same_collection_request_reuses_unfinished_job_without_resetting_lease(store, paused):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    setup_note(store)
    first = create_image_job(store, {'job_id': 'image-first', 'collection_id': 'collection', 'browser': True})
    task = next_image_page(store)['task']
    if paused:
        set_image_state(store, first['job_id'], 'paused')
    result = create_image_job(store, {'job_id': 'image-second', 'collection_id': 'collection', 'browser': True})
    assert result['reused'] and result['job_id'] == first['job_id']
    assert result['state'] == ('paused' if paused else 'active')
    assert result['items'] == {'browser_running': 1}
    with store.read() as db:
        assert db.execute('SELECT count(*) FROM image_jobs').fetchone()[0] == 1
        assert db.execute("SELECT json_extract(payload,'$.browser_lease') FROM image_items WHERE job_id=?", (first['job_id'],)).fetchone()[0] == task['lease_id']


@pytest.mark.parametrize('browser', [None, False, True])
def test_collection_reuses_legacy_acquisition_mode_hash(store, browser):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    setup_note(store)
    initial = {'collection_id': 'collection'}
    if browser is not None:
        initial['browser'] = browser
    first = create_image_job(store, initial)
    result = create_image_job(store, {'collection_id': 'collection', 'browser': True})
    assert result['reused'] and result['job_id'] == first['job_id']
    assert result['browser_mode'] is bool(browser)
    # The caller explicitly upgrades the reused anonymous job when necessary.
    upgraded = enable_image_browser(store, result['job_id'])
    assert upgraded['browser_mode'] and next_image_page(store)['task']['job_id'] == first['job_id']


def test_collection_does_not_reuse_overlapping_note_id_scope(store):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    saved, _ = setup_note(store)
    create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    with pytest.raises(XhsError) as exc:
        create_image_job(store, {'collection_id': 'collection', 'browser': True})
    assert exc.value.code == 'IMAGE_ALREADY_QUEUED'
