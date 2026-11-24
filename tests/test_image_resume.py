"""Explicit retry replaces stale directories and preserves completed downloads."""
import pytest

from test_images import Fetcher, capture, current, setup_note
from xhs_context.common import XhsError, dumps
from xhs_context.image_browser import accept_image_page, enable_image_browser, next_image_page
from xhs_context.image_jobs import create_image_job, image_report, set_image_state
from xhs_context.images import ImageWorker
from xhs_context.invalid_sources import mark_invalid


def report(store, job_id):
    with store.read() as db:
        return image_report(db, job_id)


def respond(store, job, task, snapshot):
    return accept_image_page(store, job['job_id'], dict(note_id=task['note_id'], lease_id=task['lease_id'], **snapshot))


def test_explicit_resume_resets_exhausted_regular_failure_budget(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, capture(saved))
    with store.write() as db:
        db.execute("UPDATE image_items SET state='failed',attempts=3,error='NETWORK_ERROR'")
        db.execute("UPDATE image_jobs SET state='paused',error='NETWORK_ERROR'")
    set_image_state(store, job['job_id'], 'active')
    assert ImageWorker(store, fetcher=Fetcher()).once()
    assert report(store, job['job_id'])['complete']


def test_anonymous_incomplete_manifest_is_refetched_on_explicit_resume(store):
    saved, _ = setup_note(store)
    class ChangingPage(Fetcher):
        page_calls = 0

        def get(self, url, *, media=False):
            if media:
                return super().get(url, media=True)
            self.page_calls += 1
            state = {'note': {'noteId': saved['note_id'], 'title': 'fixture', 'desc': 'fixture body'}}
            if self.page_calls > 1:
                state['note']['imageList'] = [{'urlDefault': 'https://ci.xiaohongshu.com/0.png'}]
            return ('<script>window.__INITIAL_STATE__=' + dumps(state) + '</script>').encode(), 'text/html', url
    page = ChangingPage()
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    worker = ImageWorker(store, fetcher=page)
    assert worker.once() and report(store, job['job_id'])['items'] == {'partial': 1}
    assert page.page_calls == 1
    set_image_state(store, job['job_id'], 'active')
    assert worker.once() and page.page_calls == 2
    assert report(store, job['job_id'])['complete']


def test_browser_resume_refreshes_complete_directory_with_expired_asset_url(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, dict(capture(saved), browser=True))
    def fail_second(url, media):
        if url.endswith('1.png'):
            raise XhsError('ACCESS_BLOCKED', 'Fixture expired CDN URL')
    first = Fetcher(fail_second)
    assert ImageWorker(store, fetcher=first).once()
    assert report(store, job['job_id'])['state'] == 'paused'
    enable_image_browser(store, job['job_id'])
    second = Fetcher()
    assert not ImageWorker(store, fetcher=second).once()
    task = next_image_page(store)['task']
    fresh = capture(saved)
    fresh['snapshot']['images'][1] = 'https://ci.xiaohongshu.com/fresh.png'
    respond(store, job, task, fresh)
    assert ImageWorker(store, fetcher=second).once()
    assert second.calls == [('https://ci.xiaohongshu.com/fresh.png', True)]
    assert report(store, job['job_id'])['complete']


@pytest.mark.parametrize('failure', [None, 'ACCESS_BLOCKED'])
def test_switching_running_anonymous_job_rejects_stale_worker_result(store, failure):
    saved, revision = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    class SwitchingPage(Fetcher):
        def get(self, url, *, media=False):
            assert not media
            enable_image_browser(store, job['job_id'])
            if failure:
                raise XhsError(failure, 'Obsolete anonymous login response')
            state = {'note': {'noteId': saved['note_id'], 'title': 'fixture', 'desc': 'fixture body',
                              'imageList': [{'urlDefault': 'https://ci.xiaohongshu.com/0.png'}]}}
            return ('<script>window.__INITIAL_STATE__=' + dumps(state) + '</script>').encode(), 'text/html', url
    assert ImageWorker(store, fetcher=SwitchingPage()).once()
    assert report(store, job['job_id'])['state'] == 'active'
    assert report(store, job['job_id'])['items'] == {'queued': 1}
    assert current(store, saved['note_id'])[0] == revision
    task = next_image_page(store)['task']
    respond(store, job, task, capture(saved))
    assert ImageWorker(store, fetcher=Fetcher()).once()
    assert report(store, job['job_id'])['complete']


def test_browser_download_pause_reuses_complete_gallery_without_reopening(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, dict(capture(saved), browser=True))
    def pause_second(url, media):
        if url.endswith('1.png'):
            set_image_state(store, job['job_id'], 'paused')
    assert ImageWorker(store, fetcher=Fetcher(pause_second)).once()
    enable_image_browser(store, job['job_id'])
    assert not next_image_page(store)['task']
    second = Fetcher()
    assert ImageWorker(store, fetcher=second).once()
    assert second.calls == [('https://ci.xiaohongshu.com/1.png', True)]
    assert report(store, job['job_id'])['complete']


def test_explicit_retry_cannot_reset_known_invalid_item(store):
    saved, revision = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    with store.write() as db:
        mark_invalid(db, saved['note_id'], 'SOURCE_LINK_EXPIRED', saved['source_url'])
    enable_image_browser(store, job['job_id'])
    set_image_state(store, job['job_id'], 'active')
    assert report(store, job['job_id'])['items'] == {'skipped': 1}
    assert not next_image_page(store)['task']
    assert not ImageWorker(store, fetcher=Fetcher()).once()
    assert current(store, saved['note_id'])[0] == revision


def test_partial_snapshot_survives_restart_and_downloads_before_recapture(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    task = next_image_page(store)['task']
    respond(store, job, task, capture(saved, count=1, expected=2))
    worker = ImageWorker(store, fetcher=Fetcher())
    worker.recover()
    assert not next_image_page(store)['task']
    assert worker.once()
    assert report(store, job['job_id'])['items'] == {'partial': 1}


def test_repeated_active_resume_does_not_revoke_native_worker_lease(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, dict(capture(saved), browser=True))
    def resume_each(url, media):
        enable_image_browser(store, job['job_id'])
    assert ImageWorker(store, fetcher=Fetcher(resume_each)).once()
    assert report(store, job['job_id'])['complete']
