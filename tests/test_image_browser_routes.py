import pytest

from conftest import card, create
from test_browser_import import submit
from test_images import Fetcher, capture, current, setup_note
from xhs_context.browser_import import enable_browser
from xhs_context.common import XhsError
from xhs_context.image_jobs import create_image_job, image_report
from xhs_context.images import ImageWorker
from xhs_context.jobs import receive_batch
from xhs_context.sync_service import dispatch_sync


def call(store, path, body=None):
    return dispatch_sync(store, path, body)['data']


def next_task(store):
    return call(store, '/v1/browser/next', {})['task']


def test_body_and_gallery_share_one_tab_without_starving_either_queue(store):
    create(store, 'body', 2)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1), card(2)]})
    enable_browser(store, 'body')
    saved, _ = setup_note(store, 3)
    image_job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})

    body = next_task(store)
    assert body['job_id'] == 'body'
    assert next_task(store) is None
    submit(store, body)

    image = next_task(store)
    assert image['kind'] == 'images' and image['job_id'] == image_job['job_id']
    assert next_task(store) is None  # The second body cannot overlap the gallery tab.
    lease = {key: image[key] for key in ('kind', 'job_id', 'note_id', 'lease_id')}
    assert call(store, '/v1/browser/check', lease)['allowed']
    assert not call(store, '/v1/browser/check', {key: value for key, value in lease.items() if key != 'kind'})['allowed']
    assert not ImageWorker(store, fetcher=Fetcher()).once()  # No anonymous note-page fallback.

    call(store, f"/v1/image-jobs/{image['job_id']}/browser-result", {
        'note_id': image['note_id'], 'lease_id': image['lease_id'], 'snapshot': capture(saved)['snapshot']})
    assert next_task(store)['job_id'] == 'body'
    assert ImageWorker(store, fetcher=Fetcher()).once()
    with store.read() as db:
        assert image_report(db, image['job_id'])['complete']
    assert current(store, saved['note_id'])[1]['body'] == saved['body']


def test_browser_resume_route_recovers_exhausted_anonymous_login_failure(store):
    saved, original = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']]})

    class LoginWall:
        def get(self, *args, **kwargs):
            raise XhsError('ACCESS_BLOCKED', 'Anonymous viewing refused')

    assert ImageWorker(store, fetcher=LoginWall()).once()
    with store.write() as db:
        db.execute("UPDATE image_items SET attempts=3 WHERE job_id=?", (job['job_id'],))
    resumed = call(store, f"/v1/image-jobs/{job['job_id']}/browser", {})
    assert resumed['state'] == 'active'
    assert current(store, saved['note_id'])[0] == original
    task = next_task(store)
    assert task['kind'] == 'images'
    call(store, f"/v1/image-jobs/{job['job_id']}/browser-result", {
        'note_id': task['note_id'], 'lease_id': task['lease_id'], 'snapshot': capture(saved)['snapshot']})
    assert ImageWorker(store, fetcher=Fetcher()).once()
    with store.read() as db:
        assert image_report(db, job['job_id'])['images_saved'] == 2
        assert image_report(db, job['job_id'])['complete']


def test_browser_check_kind_and_enable_payload_are_validated(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    task = next_task(store)
    lease = {key: task[key] for key in ('job_id', 'note_id', 'lease_id')}
    for kind in [None, 'other', True]:
        with pytest.raises(XhsError):
            call(store, '/v1/browser/check', {**lease, 'kind': kind})
    with pytest.raises(XhsError):
        call(store, f"/v1/image-jobs/{job['job_id']}/browser", {'surprise': True})
