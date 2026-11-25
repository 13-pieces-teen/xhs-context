import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
from PIL import Image

from conftest import card, create, note
from test_images import Fetcher, capture, current, setup_note
from xhs_context.access import restrict_access
from xhs_context.cli import main
from xhs_context.common import XhsError
from xhs_context.core import read, search
from xhs_context.image_browser import next_image_page
from xhs_context.image_jobs import create_image_job, image_report, set_image_state, validate_snapshot
from xhs_context.images import ImageWorker
from xhs_context.jobs import receive_batch
from xhs_context.sync_service import dispatch_sync


def cli(store, capsys, *args):
    assert main(['--library', str(store.root), *args]) == 0
    return json.loads(capsys.readouterr().out)['data']


@pytest.mark.parametrize('background', [False, True])
def test_cli_fetch_queues_browser_gallery_without_anonymous_page_requests(store, capsys, monkeypatch, background):
    saved, original = setup_note(store)
    fetcher = Fetcher()
    worker = ImageWorker(store, fetcher=fetcher)
    monkeypatch.setattr('xhs_context.images.ImageWorker', lambda value: worker)
    result = cli(store, capsys, 'images', 'fetch', '--note', saved['note_id'],
                 *(['--background'] if background else []))
    report = result['job'] if background else result['jobs'][0]
    assert report['browser_mode'] and report['progress']['waiting_gallery'] == 1
    assert not report['complete'] and not fetcher.calls
    assert current(store, saved['note_id'])[0] == original
    assert next_image_page(store)['task']['job_id'] == report['job_id']


@pytest.mark.parametrize('entry', ['cli', 'api'])
def test_public_resume_converts_exhausted_legacy_job_and_preserves_active_lease(store, capsys, entry):
    saved, original = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    with store.write() as db:
        db.execute("UPDATE image_items SET state='failed',error='ACCESS_BLOCKED',attempts=3 WHERE job_id=?", (job['job_id'],))
    set_image_state(store, job['job_id'], 'paused')

    def resume():
        if entry == 'cli':
            return cli(store, capsys, 'images', 'resume', '--job', job['job_id'])
        return dispatch_sync(store, f"/v1/image-jobs/{job['job_id']}/resume", {})['data']

    report = resume()
    assert report['job_id'] == job['job_id'] and report['state'] == 'active' and report['browser_mode']
    task = next_image_page(store)['task']
    assert resume()['items'] == {'browser_running': 1}
    with store.read() as db:
        row = db.execute('SELECT attempts,payload FROM image_items WHERE job_id=?', (job['job_id'],)).fetchone()
        assert row['attempts'] == 1 and json.loads(row['payload'])['browser_lease'] == task['lease_id']
    dispatch_sync(store, f"/v1/image-jobs/{job['job_id']}/browser-result", {
        'note_id': task['note_id'], 'lease_id': task['lease_id'], 'snapshot': capture(saved)['snapshot']})
    assert ImageWorker(store, fetcher=Fetcher()).once()
    result = cli(store, capsys, 'images', 'status', '--job', job['job_id'])
    assert result['complete'] and result['results'][0]['revision_id'] != original


@pytest.mark.parametrize('entry', ['cli', 'api'])
def test_public_collection_fetch_reuses_and_resumes_old_job(store, capsys, entry):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    setup_note(store)
    old = create_image_job(store, {'collection_id': 'collection'})
    set_image_state(store, old['job_id'], 'paused')
    if entry == 'cli':
        report = cli(store, capsys, 'images', 'fetch', '--collection', 'collection', '--background')['job']
    else:
        report = dispatch_sync(store, '/v1/image-jobs', {'collection_id': 'collection'})['data']
    assert report['reused'] and report['job_id'] == old['job_id']
    assert report['state'] == 'active' and report['browser_mode']
    task = next_image_page(store)['task']
    again = dispatch_sync(store, '/v1/image-jobs', {'collection_id': 'collection'})['data']
    assert again['job_id'] == old['job_id'] and again['items'] == {'browser_running': 1}
    with store.read() as db:
        assert db.execute('SELECT count(*) FROM image_jobs').fetchone()[0] == 1
        payload = json.loads(db.execute('SELECT payload FROM image_items').fetchone()[0])
        assert payload['browser_lease'] == task['lease_id']


def test_public_fetch_reuses_known_directory_for_downloads(store):
    saved, _ = setup_note(store)
    manifest = validate_snapshot(capture(saved)['snapshot'])
    store.publish(dict(saved, images=manifest['images'], expected_images=2))
    job = dispatch_sync(store, '/v1/image-jobs', {'note_ids': [saved['note_id']]})['data']
    assert job['progress']['waiting_download'] == 1 and not job['progress']['waiting_gallery']
    assert next_image_page(store)['task'] is None
    fetcher = Fetcher()
    assert ImageWorker(store, fetcher=fetcher).once()
    assert len(fetcher.calls) == 2 and all(media for _, media in fetcher.calls)


@pytest.mark.parametrize('snapshot', [False, True])
def test_public_request_replay_keeps_legacy_hash_and_pause(store, snapshot):
    saved, _ = setup_note(store)
    request = dict(capture(saved) if snapshot else {'note_ids': [saved['note_id']]}, job_id='legacy')
    create_image_job(store, request)
    set_image_state(store, 'legacy', 'paused')
    report = dispatch_sync(store, '/v1/image-jobs', request)['data']
    assert report['job_id'] == 'legacy' and report['state'] == 'paused'


def test_explicit_anonymous_request_and_access_restrictions_are_preserved(store, capsys, tmp_path):
    saved, _ = setup_note(store)
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'note_ids': [saved['note_id']], 'browser': False}), encoding='utf-8')
    report = cli(store, capsys, 'images', 'fetch', '--input', str(request), '--background')['job']
    assert not report['browser_mode'] and next_image_page(store)['task'] is None
    with store.write() as db:
        restrict_access(db)
    for path, body in [('/v1/image-jobs', {'note_ids': [saved['note_id']]}),
                       (f"/v1/image-jobs/{report['job_id']}/resume", {})]:
        with pytest.raises(XhsError) as error:
            dispatch_sync(store, path, body)
        assert error.value.code == 'ACCESS_RESTRICTED'


def test_results_page_through_large_job_while_progress_changes(store, capsys):
    identifiers = [note(number)['note_id'] for number in range(1, 104)]
    for number in range(1, 104):
        store.publish(note(number))
    job = create_image_job(store, {'job_id': 'paged-' + 'x' * 85, 'note_ids': identifiers})
    assert len(job['results']) == 50 and job['next_cursor']
    first = cli(store, capsys, 'images', 'status', '--job', job['job_id'], '--limit', '40')
    seen = first['results'][:]
    with store.write() as db:
        db.execute("UPDATE image_items SET state='failed',error='NETWORK_ERROR' WHERE job_id=? AND note_id=?",
                   (job['job_id'], identifiers[0]))
    cursor = first['next_cursor']
    while cursor:
        path = f"/v1/image-jobs/{job['job_id']}?" + urlencode({'limit': 17, 'cursor': cursor})
        page = dispatch_sync(store, path)['data']
        assert page['notes'] == 103 and page['items'] == {'failed': 1, 'ready': 102}
        seen.extend(page['results'])
        cursor = page['next_cursor']
    assert [item['note_id'] for item in seen] == identifiers
    assert all(item['revision_id'] for item in seen)
    last = seen[-1]
    evidence = read(store, {'items': [{'note_id': last['note_id'], 'revision_id': last['revision_id']}], 'mode': 'full'})
    assert evidence['data']['items'][0]['blocks']
    repeated = cli(store, capsys, 'images', 'status', '--job', job['job_id'], '--cursor', first['next_cursor'])
    assert repeated['results'][0]['note_id'] == identifiers[40]
    other = create_image_job(store, {'note_ids': [identifiers[-1]]})
    with store.read() as db, pytest.raises(XhsError, match='another job'):
        image_report(db, other['job_id'], cursor=first['next_cursor'])


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'limit=bad', 'limit=1&limit=2',
                                 'cursor=', 'cursor=bad', 'offset=1', 'cursor=x&cursor=y'])
def test_result_route_rejects_invalid_pagination(store, query):
    store.publish(note())
    job = create_image_job(store, {'note_ids': [note()['note_id']]})
    with pytest.raises(XhsError) as error:
        dispatch_sync(store, f"/v1/image-jobs/{job['job_id']}?{query}")
    assert error.value.code == 'INVALID_ARGUMENT'


@pytest.mark.parametrize('args', [('--limit', '1'), ('--cursor', 'bad')])
def test_cli_pagination_requires_a_job(store, capsys, args):
    assert main(['--library', str(store.root), 'images', 'status', *args]) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'INVALID_ARGUMENT'


def test_tag_only_note_can_be_found_then_read_as_images(store, capsys):
    saved = dict(note(body='#纸艺', title='折纸图解'), expected_images=None,
                 media_state='deferred', gaps=['MEDIA_DEFERRED'])
    store.publish(saved)
    hit, = search(store, {'queries': ['纸艺'], 'content': 'all'})['data']['items']
    assert 'tags' in hit['matched_fields']
    job = cli(store, capsys, 'images', 'fetch', '--note', hit['note_id'], '--background')['job']
    task = next_image_page(store)['task']
    dispatch_sync(store, f"/v1/image-jobs/{job['job_id']}/browser-result", {
        'note_id': task['note_id'], 'lease_id': task['lease_id'], 'snapshot': capture(saved)['snapshot']})
    assert ImageWorker(store, fetcher=Fetcher()).once()
    finished = cli(store, capsys, 'images', 'status', '--job', job['job_id'])
    result = finished['results'][0]
    assets = read(store, {'items': [{'note_id': result['note_id'], 'revision_id': result['revision_id']}], 'mode': 'asset'})
    gallery = assets['data']['items'][0]
    assert len(gallery['assets']) == 2
    for asset in gallery['assets']:
        assert Path(asset['path']).is_file()
        with Image.open(asset['path']) as image:
            assert image.size == (12, 8)
