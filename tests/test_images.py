import io
import json

import pytest
from PIL import Image

from conftest import note, card, create
from xhs_context.access import restrict_access, unlock_access
from xhs_context.browser_import import enable_browser, next_page, accept_page
from xhs_context.cli import main
from xhs_context.common import XhsError, dumps
from xhs_context.core import read, status
from xhs_context.image_jobs import create_image_job, image_report, set_image_state
from xhs_context.images import ImageWorker
from xhs_context.jobs import receive_batch
from xhs_context.processing import finish_body
from xhs_context.sync_service import dispatch_sync


def picture(color='red'):
    data = io.BytesIO()
    Image.new('RGB', (12, 8), color).save(data, 'PNG')
    return data.getvalue()


def setup_note(store, number=1):
    saved = finish_body(dict(note(number), expected_images=None, images=[]))
    saved['raw_object'] = store.put_object(dumps({'body': saved['body']}).encode(), '.json')
    revision = store.publish(saved)
    return saved, revision


def capture(saved, count=2, expected=2):
    return {'snapshot': {'note_id': saved['note_id'], 'page_url': saved['source_url'],
                         'images': [f'https://ci.xiaohongshu.com/{i}.png' for i in range(count)],
                         'image_indices': list(range(1, count + 1)), 'expected_images': expected, 'note_type': 'image'}}


def current(store, note_id):
    with store.read() as db:
        row = db.execute('SELECT r.id,r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=?', (note_id,)).fetchone()
        return row[0], json.loads(row[1])


class Fetcher:
    def __init__(self, callback=None):
        self.calls, self.callback = [], callback

    def get(self, url, *, media=False):
        self.calls.append((url, media))
        if self.callback:
            self.callback(url, media)
        assert media, 'Gallery capture must not fetch the note again'
        return picture('blue' if url.endswith('1.png') else 'red'), 'image/png', url


def run(store, request, fetcher=None):
    job = create_image_job(store, request)
    worker = ImageWorker(store, fetcher=fetcher or Fetcher())
    assert worker.once(job['job_id'])
    with store.read() as db:
        return image_report(db, job['job_id'])


def test_unknown_media_is_not_a_successful_empty_response(store):
    saved = note(body='')
    saved.update(body_state='missing', expected_images=None, gaps=['BODY_MISSING', 'IMAGE_COUNT_UNKNOWN'])
    revision = store.publish(saved)
    result = read(store, {'items': [{'note_id': saved['note_id'], 'revision_id': revision}], 'mode': 'asset'})
    assert result['partial'] and result['data']['errors'][0]['code'] == 'IMAGE_COUNT_UNKNOWN'
    assert result['data']['items'][0]['assets'] == []
    assert 'BODY_MISSING' in result['data']['items'][0]['gaps']


def test_known_zero_images_preserves_the_note_without_error(store):
    saved = note()
    revision = store.publish(saved)
    result = read(store, {'items': [{'note_id': saved['note_id'], 'revision_id': revision}], 'mode': 'asset'})
    assert not result['partial'] and result['data']['items'][0]['assets'] == []


def test_images_preserve_body_history_and_do_not_run_ocr(store, monkeypatch):
    from xhs_context.processing import LocalOCR
    monkeypatch.setattr(LocalOCR, '__init__', lambda self: pytest.fail('OCR must be opt-in'))
    saved, revision = setup_note(store)
    fetcher = Fetcher()
    job = run(store, capture(saved), fetcher)
    assert job['complete'] and job['images_saved'] == 2
    new_revision, updated = current(store, saved['note_id'])
    assert new_revision != revision and updated['body'] == saved['body']
    assert updated['raw_object'] == saved['raw_object'] and updated['images_body_revision'] == revision
    assert all(a['ocr_state'] == 'not_requested' for a in updated['images'])
    old = read(store, {'items': [{'note_id': saved['note_id'], 'revision_id': revision}], 'mode': 'asset'})
    assert old['data']['errors'][0]['code'] == 'MEDIA_DEFERRED'
    assets = read(store, {'items': [{'note_id': saved['note_id'], 'revision_id': new_revision}], 'mode': 'asset'})
    assert len(assets['data']['items'][0]['assets']) == 2 and not assets['partial']
    assert all(store.object_path(a['object_path']).is_file() for a in updated['images'])
    cached = Fetcher(lambda *args: pytest.fail('Already saved images must be reused'))
    assert run(store, {'note_ids': [saved['note_id']]}, cached)['complete']
    assert current(store, saved['note_id'])[0] == new_revision


def test_partial_capture_cannot_erase_images_or_claim_completeness(store):
    saved, _ = setup_note(store)
    run(store, capture(saved))
    result = run(store, capture(saved, count=1, expected=None))
    _, updated = current(store, saved['note_id'])
    assert result['items'] == {'partial': 1} and result['images_saved'] == 2
    assert updated['body'] == saved['body'] and not updated['image_manifest_complete']
    assert 'IMAGE_COUNT_UNKNOWN' in updated['gaps'] and 'IMAGE_LIST_UNVERIFIED' in updated['gaps']


def test_body_refresh_retains_separate_images_and_body_job_success(store):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    saved, _ = setup_note(store)
    run(store, capture(saved))
    _, before = current(store, saved['note_id'])
    enable_browser(store, 'body')
    task = next_page(store)['task']
    accept_page(store, 'body', {'note_id': task['note_id'], 'lease_id': task['lease_id'],
                              'snapshot': {'note_id': task['note_id'], 'page_url': task['source_url'],
                                           'title': '更新', 'body': '更新的文字正文', 'body_present': True}})
    _, after = current(store, saved['note_id'])
    assert after['images'] == before['images'] and after['body'] == '更新的文字正文'
    assert 'IMAGES_CAPTURED_SEPARATELY' in after['gaps']
    assert status(store, 'body')['data']['job']['items'] == {'ready': 1}


def test_restriction_locks_images_and_body_without_further_requests(store):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    saved, revision = setup_note(store)
    def limited(url, media):
        if url.endswith('1.png'):
            raise XhsError('RATE_LIMITED', 'fixture')
    job = run(store, capture(saved), Fetcher(limited))
    assert job['state'] == 'paused' and job['error'] == 'RATE_LIMITED'
    assert current(store, saved['note_id'])[0] == revision
    assert status(store, 'body')['data']['job']['state'] == 'paused'
    with pytest.raises(XhsError, match='locked'):
        create_image_job(store, capture(saved))
    with pytest.raises(XhsError, match='locked'):
        set_image_state(store, job['job_id'], 'active')
    never = Fetcher(lambda *args: pytest.fail('Restricted acquisition must not fetch'))
    assert not ImageWorker(store, fetcher=never).once()
    unlock_access(store, confirmed=True)
    with store.read() as db:
        assert image_report(db, job['job_id'])['state'] == 'paused'


def test_pause_resume_reuses_download_checkpoint(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, capture(saved))
    def pause(url, media):
        if url.endswith('1.png'):
            set_image_state(store, job['job_id'], 'paused')
    assert ImageWorker(store, fetcher=Fetcher(pause)).once()
    set_image_state(store, job['job_id'], 'active')
    resumed = Fetcher()
    worker = ImageWorker(store, fetcher=resumed)
    worker.recover()
    assert worker.once()
    assert resumed.calls == [('https://ci.xiaohongshu.com/1.png', True)]
    with store.read() as db:
        assert image_report(db, job['job_id'])['complete']


def test_concurrent_body_change_is_not_overwritten(store):
    saved, _ = setup_note(store)
    def change(url, media):
        store.publish(dict(saved, body='在补图时更新的正文'))
    result = run(store, capture(saved), Fetcher(change))
    assert result['failures'][0]['error'] == 'REVISION_CONFLICT'
    assert current(store, saved['note_id'])[1]['body'] == '在补图时更新的正文'


def test_image_queue_idempotence_and_duplicate_note_guard(store):
    saved, _ = setup_note(store)
    request = dict(capture(saved), job_id='fixed')
    first = create_image_job(store, request)
    assert create_image_job(store, request)['job_id'] == first['job_id']
    with pytest.raises(XhsError) as exc:
        create_image_job(store, capture(saved))
    assert exc.value.code == 'IMAGE_ALREADY_QUEUED'
    with pytest.raises(XhsError) as exc:
        create_image_job(store, {'job_id': 'fixed', 'note_ids': [saved['note_id']]})
    assert exc.value.code == 'CONFLICT'


def test_native_image_jobs_and_cli_are_separate_from_read(store, monkeypatch, capsys):
    from xhs_context.network import AnonymousFetcher
    saved, _ = setup_note(store)
    monkeypatch.setattr(AnonymousFetcher, 'get', Fetcher().get)
    job = dispatch_sync(store, '/v1/image-jobs', capture(saved))['data']
    assert main(['--library', str(store.root), 'images', 'process', '--job', job['job_id']]) == 0
    assert json.loads(capsys.readouterr().out)['data']['jobs'][0]['complete']
    assert dispatch_sync(store, '/v1/image-jobs/' + job['job_id'])['data']['complete']


@pytest.mark.parametrize('url', ['http://ci.xiaohongshu.com/x', 'https://localhost/x', 'https://example.org/x'])
def test_gallery_rejects_unrelated_and_insecure_urls(store, url):
    saved, _ = setup_note(store)
    request = capture(saved, count=1, expected=1)
    request['snapshot']['images'] = [url]
    with pytest.raises(XhsError) as exc:
        create_image_job(store, request)
    assert exc.value.code == 'URL_NOT_ALLOWED'


def test_collection_images_queue_does_not_change_body_jobs(store):
    create(store, 'body', 2)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1), card(2)]})
    before = status(store, 'body')['data']['job']
    job = create_image_job(store, {'collection_id': 'collection'})
    assert job['notes'] == 2 and job['items'] == {'queued': 2}
    assert status(store, 'body')['data']['job'] == before


def test_failed_image_refresh_retains_previous_images(store):
    saved, _ = setup_note(store)
    run(store, capture(saved))
    revision, prior = current(store, saved['note_id'])
    request = capture(saved)
    request['snapshot']['images'][0] = 'https://ci.xiaohongshu.com/changed.png'
    def fail(url, media):
        raise XhsError('HTTP_ERROR', 'fixture')
    job = run(store, request, Fetcher(fail))
    assert job['failures'][0]['error'] == 'INCOMPLETE_IMAGE_REFRESH'
    assert current(store, saved['note_id']) == (revision, prior)


def test_image_only_acquisition_from_page_preserves_missing_body(store):
    saved, _ = setup_note(store)
    saved.update(body='', body_state='missing')
    store.publish(saved)
    class PageFetcher(Fetcher):
        def get(self, url, *, media=False):
            if media:
                return super().get(url, media=True)
            self.calls.append((url, media))
            state = {'note': {'noteId': saved['note_id'], 'title': 'new title', 'desc': 'Do not import this new text',
                              'imageList': [{'urlDefault': 'https://ci.xiaohongshu.com/0.png'}]}}
            return ('<script>window.__INITIAL_STATE__=' + json.dumps(state) + '</script>').encode(), 'text/html', url
    result = run(store, {'note_ids': [saved['note_id']]}, PageFetcher())
    assert result['complete'] and result['images_saved'] == 1
    revision, updated = current(store, saved['note_id'])
    assert updated['body'] == '' and updated['body_state'] == 'missing' and updated['title'] == saved['title']
    assert updated['content_scope'] == 'images'
    assert not read(store, {'items': [{'note_id': saved['note_id'], 'revision_id': revision}], 'mode': 'asset'})['partial']


def test_unknown_gallery_total_stays_partial_in_asset_read(store):
    saved, _ = setup_note(store)
    run(store, capture(saved, count=1, expected=None))
    revision, _ = current(store, saved['note_id'])
    result = read(store, {'items': [{'note_id': saved['note_id'], 'revision_id': revision}], 'mode': 'asset'})
    assert result['partial'] and result['data']['errors'][0]['code'] == 'IMAGE_COUNT_UNKNOWN'
    assert len(result['data']['items'][0]['assets']) == 1


def test_backup_restores_image_provenance_and_interrupted_checkpoint(store, tmp_path):
    from xhs_context.exports import backup, restore
    from xhs_context.storage import Store
    saved, _ = setup_note(store)
    run(store, capture(saved))
    _, published = current(store, saved['note_id'])
    second, _ = setup_note(store, 2)
    job = create_image_job(store, capture(second))
    def pause(url, media):
        if url.endswith('1.png'):
            set_image_state(store, job['job_id'], 'paused')
    ImageWorker(store, fetcher=Fetcher(pause)).once()
    backed = backup(store, tmp_path / 'backup')
    assert backed['verified']
    restore(tmp_path / 'backup', tmp_path / 'restored')
    restored = Store(tmp_path / 'restored')
    raw = published['images_raw_object']['object_path']
    assert restored.object_path(raw).read_bytes() == store.object_path(raw).read_bytes()
    set_image_state(restored, job['job_id'], 'active')
    fetcher = Fetcher()
    assert ImageWorker(restored, fetcher=fetcher).once()
    assert fetcher.calls == [('https://ci.xiaohongshu.com/1.png', True)]
