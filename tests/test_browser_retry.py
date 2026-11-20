import pytest

from conftest import card, create
from xhs_context.browser_import import enable_browser, next_page, accept_page
from xhs_context.jobs import receive_batch, job_report, set_job_state
from xhs_context.worker import Worker
from xhs_context.access import restrict_access
from xhs_context.common import XhsError


def prepare(store):
    create(store, 'retry', 2)
    receive_batch(store, 'retry', {'seq': 0, 'items': [card(1), card(2)]})
    enable_browser(store, 'retry')


def failed(store, task, code):
    return accept_page(store, task['job_id'], {'note_id': task['note_id'], 'lease_id': task['lease_id'], 'error': code})


@pytest.mark.parametrize('code', ['BROWSER_BUSY', 'BROWSER_TIMEOUT', 'BROWSER_CLOSED', 'BROWSER_INTERRUPTED'])
def test_transient_error_waits_then_retries_same_note_without_pausing(store, monkeypatch, code):
    prepare(store)
    clock = [100000]
    monkeypatch.setattr('xhs_context.browser_import.clock_ms', lambda: clock[0])
    task = next_page(store)['task']
    receipt = failed(store, task, code)
    assert receipt['browser_retry']['retry_at'] == 105000
    assert next_page(store)['task'] is None  # Do not skip ahead to the rest of the collection.
    with store.read() as db:
        report = job_report(db, 'retry')
        assert report['state'] == 'active' and report['browser_retry']['error'] == code
        assert report['items'] == {'queued': 2}
    clock[0] = 105000
    second = next_page(store)['task']
    assert second['note_id'] == task['note_id'] and second['lease_id'] != task['lease_id']
    with pytest.raises(XhsError):
        failed(store, task, code)  # Old page cannot modify the new lease.


def test_retry_budget_is_finite_and_explicit_continue_can_retry_again(store, monkeypatch):
    prepare(store)
    clock = [100000]
    monkeypatch.setattr('xhs_context.browser_import.clock_ms', lambda: clock[0])
    for attempt, delay in [(1, 5000), (2, 15000), (3, None)]:
        task = next_page(store)['task']
        failed(store, task, 'BROWSER_BUSY')
        assert next_page(store)['task'] is None
        if delay:
            clock[0] += delay
    with store.read() as db:
        report = job_report(db, 'retry')
        assert report['state'] == 'paused' and report['error'] == 'BROWSER_BUSY'
        assert report['browser_retry'] is None and report['items'] == {'failed': 1, 'queued': 1}
    set_job_state(store, 'retry', 'active', retry_failed=True)
    assert next_page(store)['task']['note_id'] == task['note_id']


def test_manual_pause_and_restriction_override_due_retry(store, monkeypatch):
    prepare(store)
    monkeypatch.setattr('xhs_context.browser_import.clock_ms', lambda: 100000)
    failed(store, next_page(store)['task'], 'BROWSER_TIMEOUT')
    set_job_state(store, 'retry', 'paused')
    monkeypatch.setattr('xhs_context.browser_import.clock_ms', lambda: 200000)
    assert next_page(store)['task'] is None
    set_job_state(store, 'retry', 'active', retry_failed=True)
    with store.write() as db:
        restrict_access(db)
    assert next_page(store)['access_restriction']


def test_host_restart_preserves_unacknowledged_page_lease(store):
    prepare(store)
    task = next_page(store)['task']
    Worker(store, ocr=False).recover()
    assert next_page(store)['task'] is None
    result = {'note_id': task['note_id'], 'lease_id': task['lease_id'], 'snapshot': {
        'note_id': task['note_id'], 'page_url': task['source_url'], 'title': '恢复夹具',
        'body': '断线前已经读取的正文', 'body_present': True}}
    accept_page(store, 'retry', result)
    assert accept_page(store, 'retry', result)['duplicate']
    with store.read() as db:
        assert job_report(db, 'retry')['body_available'] == 1


def test_explicit_resume_releases_page_abandoned_by_old_extension(store):
    prepare(store)
    task = next_page(store)['task']
    set_job_state(store, 'retry', 'paused')
    set_job_state(store, 'retry', 'active', retry_failed=True)
    second = next_page(store)['task']
    assert second['note_id'] == task['note_id'] and second['lease_id'] != task['lease_id']
    with store.read() as db:
        assert db.execute("SELECT attempts FROM job_items WHERE note_id=?", (task['note_id'],)).fetchone()[0] == 1


def test_repeated_abandoned_leases_cannot_bypass_retry_limit(store):
    prepare(store)
    task = next_page(store)['task']
    note_id = task['note_id']
    for attempt in range(3):
        assert task['note_id'] == note_id
        with store.write() as db:
            db.execute("UPDATE job_items SET updated_at='2000-01-01' WHERE state='browser_running'")
        task = next_page(store)['task']
    assert task is None
    with store.read() as db:
        report = job_report(db, 'retry')
        assert report['state'] == 'paused' and report['error'] == 'BROWSER_TIMEOUT'
        assert report['items'] == {'failed': 1, 'queued': 1}


def test_abandoned_lease_after_manual_pause_refunds_attempt(store):
    prepare(store)
    task = next_page(store)['task']
    set_job_state(store, 'retry', 'paused')
    with store.write() as db:
        db.execute("UPDATE job_items SET updated_at='2000-01-01' WHERE state='browser_running'")
    assert next_page(store)['task'] is None
    with store.read() as db:
        item = db.execute("SELECT state,attempts,error FROM job_items WHERE note_id=?", (task['note_id'],)).fetchone()
        assert tuple(item) == ('queued', 0, None)
