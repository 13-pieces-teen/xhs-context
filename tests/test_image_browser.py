"""Browser gallery leases use synthetic archives and never contact XHS."""
import pytest

from conftest import card, create
from test_images import Fetcher, capture, current, setup_note
from xhs_context.access import access_restriction, refresh_access, restrict_access
from xhs_context.browser_import import enable_browser, next_page
from xhs_context.common import XhsError
from xhs_context.image_browser import (accept_image_page, check_image_page,
                                       enable_image_browser, next_image_page)
from xhs_context.image_jobs import create_image_job, image_report, set_image_state
from xhs_context.images import ImageWorker
from xhs_context.invalid_sources import invalid_source, mark_invalid
from xhs_context.jobs import receive_batch


def started(store, *, number=1):
    saved, revision = setup_note(store, number)
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    task = next_image_page(store)['task']
    assert task['kind'] == 'images'
    return saved, revision, job, task


def request(task, **result):
    return dict(note_id=task['note_id'], lease_id=task['lease_id'], **result)


def check(store, task):
    return check_image_page(store, {key: task[key] for key in ('job_id', 'note_id', 'lease_id')})['allowed']


def state(store, job_id):
    with store.read() as db:
        return image_report(db, job_id)


def test_browser_gallery_does_not_fetch_anonymous_note_and_is_idempotent(store):
    saved, revision = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    fetcher = Fetcher()
    worker = ImageWorker(store, fetcher=fetcher)
    assert not worker.once() and not fetcher.calls
    task = next_image_page(store)['task']
    assert check(store, task) and not next_image_page(store)['task']
    assert state(store, job['job_id'])['items'] == {'browser_running': 1}
    assert state(store, job['job_id'])['state'] == 'active'
    assert state(store, job['job_id'])['browser_mode']
    response = request(task, **capture(saved))
    assert accept_image_page(store, job['job_id'], response)['accepted']
    assert accept_image_page(store, job['job_id'], response)['duplicate']
    assert current(store, saved['note_id'])[0] == revision
    assert not check(store, task) and not next_image_page(store)['task']
    assert worker.once() and state(store, job['job_id'])['complete']
    assert len(fetcher.calls) == 2 and all(media for _, media in fetcher.calls)
    final_revision, final_note = current(store, saved['note_id'])
    assert final_revision != revision and final_note['body'] == saved['body']
    assert accept_image_page(store, job['job_id'], response)['duplicate']
    assert not worker.once() and len(fetcher.calls) == 2


def test_partial_gallery_downloads_once_and_only_explicit_resume_recaptures(store):
    saved, _, job, task = started(store)
    accept_image_page(store, job['job_id'], request(task, **capture(saved, count=1, expected=2)))
    first = Fetcher()
    assert ImageWorker(store, fetcher=first).once()
    assert state(store, job['job_id'])['items'] == {'partial': 1}
    assert not next_image_page(store)['task']
    report = enable_image_browser(store, job['job_id'])
    assert report['state'] == 'active' and report['items'] == {'queued': 1}
    second = Fetcher()
    assert not ImageWorker(store, fetcher=second).once()
    next_task = next_image_page(store)['task']
    assert next_task['lease_id'] != task['lease_id']
    accept_image_page(store, job['job_id'], request(next_task, **capture(saved)))
    assert ImageWorker(store, fetcher=second).once()
    assert second.calls == [('https://ci.xiaohongshu.com/1.png', True)]
    assert state(store, job['job_id'])['complete']


def test_repeated_active_resume_preserves_live_gallery_lease(store):
    saved, _, job, task = started(store)
    enable_image_browser(store, job['job_id'])
    set_image_state(store, job['job_id'], 'active')
    assert check(store, task)
    assert next_image_page(store)['task'] is None
    accept_image_page(store, job['job_id'], request(task, **capture(saved)))
    assert ImageWorker(store, fetcher=Fetcher()).once()


def test_paused_late_gallery_never_resumes_or_publishes(store):
    saved, revision, job, task = started(store)
    set_image_state(store, job['job_id'], 'paused')
    assert not check(store, task)
    assert accept_image_page(store, job['job_id'], request(task, **capture(saved)))['paused']
    assert not ImageWorker(store, fetcher=Fetcher()).once()
    assert state(store, job['job_id'])['state'] == 'paused'
    assert current(store, saved['note_id'])[0] == revision
    enable_image_browser(store, job['job_id'])
    newer = next_image_page(store)['task']
    with pytest.raises(XhsError) as exc:
        accept_image_page(store, job['job_id'], request(task, **capture(saved)))
    assert exc.value.code == 'CONFLICT' and check(store, newer)


@pytest.mark.parametrize('code', ['ACCESS_BLOCKED', 'NOTE_NOT_FOUND', 'BROWSER_PARSE_FAILED',
                                 'BROWSER_ERROR', 'IMAGE_LIST_UNAVAILABLE', 'IMAGE_LIST_TOO_LARGE'])
def test_unknown_or_login_page_pauses_without_invalidating_note(store, code):
    saved, revision, job, task = started(store)
    accept_image_page(store, job['job_id'], request(task, error=code))
    assert state(store, job['job_id'])['state'] == 'paused'
    with store.read() as db:
        assert not invalid_source(db, saved['note_id']) and not access_restriction(db)
    assert current(store, saved['note_id'])[0] == revision
    assert not next_image_page(store)['task']


@pytest.mark.parametrize('code', ['SOURCE_UNAVAILABLE', 'SOURCE_LINK_EXPIRED'])
def test_explicit_unavailable_gallery_persists_skip_and_preserves_archive(store, code):
    saved, revision, job, task = started(store)
    assert accept_image_page(store, job['job_id'], request(task, error=code))['skipped']
    assert state(store, job['job_id'])['items'] == {'skipped': 1}
    assert state(store, job['job_id'])['state'] == 'finished'
    with store.read() as db:
        assert invalid_source(db, saved['note_id'])['error'] == code
    enable_image_browser(store, job['job_id'])
    newer = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    assert newer['items'] == {'skipped': 1}
    assert not next_image_page(store)['task'] and current(store, saved['note_id'])[0] == revision


@pytest.mark.parametrize('invalid', [False, True])
def test_safety_precedes_invalid_and_old_receipt_cannot_relock_after_refresh(store, invalid):
    saved, revision, job, task = started(store)
    create(store, 'body', 1)
    if invalid:
        with store.write() as db:
            mark_invalid(db, saved['note_id'], 'SOURCE_UNAVAILABLE', saved['source_url'])
    response = request(task, error='ACCESS_RESTRICTED')
    assert accept_image_page(store, job['job_id'], response)['restricted']
    with store.read() as db:
        assert access_restriction(db) and db.execute("SELECT state FROM jobs WHERE id='body'").fetchone()[0] == 'paused'
    with pytest.raises(XhsError):
        enable_image_browser(store, job['job_id'])
    assert not next_image_page(store)['task'] and current(store, saved['note_id'])[0] == revision
    refresh_access(store, {})
    accept_image_page(store, job['job_id'], response)
    with store.read() as db:
        assert not access_restriction(db)


def test_expired_lease_is_reissued_and_old_result_cannot_override(store):
    saved, revision, job, task = started(store)
    with store.write() as db:
        db.execute("UPDATE image_items SET updated_at='2000-01-01T00:00:00.000+00:00'")
    assert not check(store, task)
    assert accept_image_page(store, job['job_id'], request(task, **capture(saved)))['expired']
    newer = next_image_page(store)['task']
    assert newer['lease_id'] != task['lease_id']
    with pytest.raises(XhsError):
        accept_image_page(store, job['job_id'], request(task, **capture(saved)))
    assert current(store, saved['note_id'])[0] == revision


def test_transient_gallery_errors_have_bounded_retries_then_explicit_fresh_budget(store):
    saved, _, job, task = started(store)
    for attempt in range(3):
        response = accept_image_page(store, job['job_id'], request(task, error='BROWSER_TIMEOUT'))
        if attempt < 2:
            assert response['browser_retry']['attempts'] == attempt + 1
            assert not next_image_page(store)['task']
            with store.write() as db:
                db.execute("UPDATE image_items SET payload=json_set(payload,'$.browser_retry_at',0)")
            task = next_image_page(store)['task']
    assert state(store, job['job_id'])['state'] == 'paused'
    enable_image_browser(store, job['job_id'])
    task = next_image_page(store)['task']
    accept_image_page(store, job['job_id'], request(task, **capture(saved)))
    assert ImageWorker(store, fetcher=Fetcher()).once()
    assert state(store, job['job_id'])['complete']


def test_active_body_lease_prevents_separate_image_lease(store):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    setup_note(store)
    enable_browser(store, 'body')
    body = next_page(store)['task']
    create_image_job(store, {'note_ids': [body['note_id']], 'browser': True})
    assert next_image_page(store)['task'] is None


def test_browser_gallery_rejects_other_note_identity(store):
    saved, revision, job, task = started(store)
    other, _ = setup_note(store, 2)
    with pytest.raises(XhsError) as exc:
        accept_image_page(store, job['job_id'], request(task, **capture(other)))
    assert exc.value.code == 'URL_NOTE_MISMATCH'
    assert current(store, saved['note_id'])[0] == revision


def test_explicit_zero_gallery_finishes_without_network(store):
    saved, _, job, task = started(store)
    accept_image_page(store, job['job_id'], request(task, **capture(saved, count=0, expected=0)))
    fetcher = Fetcher()
    assert ImageWorker(store, fetcher=fetcher).once()
    assert not fetcher.calls and state(store, job['job_id'])['complete']


def test_zero_gallery_cannot_remove_previously_saved_images(store):
    saved, _ = setup_note(store)
    first = create_image_job(store, capture(saved))
    assert ImageWorker(store, fetcher=Fetcher()).once()
    assert state(store, first['job_id'])['complete']
    revision, prior = current(store, saved['note_id'])
    # An explicit current-page capture can conflict with an existing archive;
    # ordinary missing-image jobs now reuse its complete images immediately.
    job = create_image_job(store, dict(capture(saved, count=0, expected=0), browser=True))
    fetcher = Fetcher()
    assert ImageWorker(store, fetcher=fetcher).once() and not fetcher.calls
    assert current(store, saved['note_id']) == (revision, prior)
    assert state(store, job['job_id'])['failures'][0]['error'] == 'INCOMPLETE_IMAGE_REFRESH'
    assert all(store.object_path(asset['object_path']).is_file() for asset in prior['images'])


def test_unknown_zero_gallery_is_not_success(store):
    saved, _, job, task = started(store)
    with pytest.raises(XhsError) as exc:
        accept_image_page(store, job['job_id'], request(task, **capture(saved, count=0, expected=None)))
    assert exc.value.code == 'INVALID_ARGUMENT'


def test_recover_keeps_live_browser_lease_for_its_expiry(store):
    _, _, job, task = started(store)
    ImageWorker(store, fetcher=Fetcher()).recover()
    assert check(store, task) and state(store, job['job_id'])['state'] == 'active'


def test_restrict_during_gallery_cannot_accept_late_success(store):
    saved, revision, job, task = started(store)
    with store.write() as db:
        restrict_access(db)
    assert accept_image_page(store, job['job_id'], request(task, **capture(saved)))['paused']
    assert current(store, saved['note_id'])[0] == revision


def test_browser_flag_requires_boolean(store):
    saved, _ = setup_note(store)
    with pytest.raises(XhsError) as exc:
        create_image_job(store, {'note_ids': [saved['note_id']], 'browser': 'true'})
    assert exc.value.code == 'INVALID_ARGUMENT'
