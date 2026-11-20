import json

import pytest

from conftest import card, create
from xhs_context.browser_import import enable_browser, next_page, accept_page
from xhs_context.common import XhsError
from xhs_context.jobs import receive_batch, job_report, set_job_state
from xhs_context.worker import Worker


def test_existing_library_upgrade_preserves_jobs_and_notes(store):
    create(store, "old", 1)
    receive_batch(store, "old", {"seq": 0, "items": [card(1)]})
    with store.write() as db:
        db.execute("ALTER TABLE jobs DROP COLUMN content_mode")
    assert store.initialize()["created"] is False
    with store.read() as db:
        report = job_report(db, "old")
        assert report["content_mode"] == "anonymous" and report["discovered"] == 1
    enable_browser(store, "old")
    assert next_page(store)["task"]["job_id"] == "old"


def prepare(store):
    create(store, "browser", 2)
    receive_batch(store, "browser", {"seq": 0, "items": [card(1), card(2)]})
    enable_browser(store, "browser")


def snapshot(task, **overrides):
    return {"note_id": task["note_id"], "page_url": task["source_url"], "title": "浏览器正文",
            "body": "只读取这篇笔记的正文，不包含评论。", "body_present": True,
            "images": [], "expected_images": 0, "note_type": "image", **overrides}


def submit(store, task, **overrides):
    return accept_page(store, task["job_id"], {"note_id": task["note_id"], "lease_id": task["lease_id"],
                                            "snapshot": snapshot(task, **overrides)})


def test_browser_lease_serializes_tasks_and_persists_searchable_body(store):
    prepare(store)
    task = next_page(store)["task"]
    assert next_page(store)["task"] is None
    assert not Worker(store, ocr=False).once()  # Do not request authenticated details anonymously.
    assert submit(store, task)["accepted"]
    assert submit(store, task)["duplicate"]
    with store.read() as db:
        assert job_report(db, "browser")["body_available"] == 1
    class NoNetwork:
        def get(self, *args, **kwargs):
            pytest.fail("Browser body must not be fetched again")
    assert not Worker(store, ocr=False, fetcher=NoNetwork()).once()
    with store.read() as db:
        assert job_report(db, "browser")["items"] == {"queued": 1, "ready": 1}


@pytest.mark.parametrize("override", [
    {"note_id": "f" * 24}, {"page_url": "https://evil.example/"},
    {"images": ["https://127.0.0.1/private"], "expected_images": 1},
    {"images": ["https://sns-webpic-qc.xhscdn.com/x"], "expected_images": 0},
])
def test_browser_snapshots_reject_mismatch_and_untrusted_media(store, override):
    prepare(store)
    task = next_page(store)["task"]
    with pytest.raises(XhsError):
        submit(store, task, **override)


def test_expired_lease_cannot_overwrite_reassigned_note(store):
    prepare(store)
    first = next_page(store)["task"]
    with store.write() as db:
        db.execute("UPDATE job_items SET updated_at='2000-01-01' WHERE state='browser_running'")
    second = next_page(store)["task"]
    assert second["note_id"] == first["note_id"] and second["lease_id"] != first["lease_id"]
    with pytest.raises(XhsError, match="expired"):
        submit(store, first)
    submit(store, second)


def test_pause_during_page_load_does_not_publish_or_consume_retry(store):
    prepare(store)
    task = next_page(store)["task"]
    set_job_state(store, "browser", "paused")
    assert submit(store, task)["paused"]
    with store.read() as db:
        assert job_report(db, "browser")["body_available"] == 0
        assert db.execute("SELECT attempts FROM job_items WHERE note_id=?", (task["note_id"],)).fetchone()[0] == 0


def test_browser_login_wall_pauses_instead_of_failing_remaining_queue(store):
    prepare(store)
    task = next_page(store)["task"]
    accept_page(store, "browser", {"note_id": task["note_id"], "lease_id": task["lease_id"], "error": "ACCESS_BLOCKED"})
    assert next_page(store)["task"] is None
    with store.read() as db:
        report = job_report(db, "browser")
        assert report["state"] == "paused" and report["items"] == {"failed": 1, "queued": 1}
        assert report["failure_summary"] == [{"error": "ACCESS_BLOCKED", "count": 1}]


def test_switch_acquisition_requeues_old_failures_with_fresh_attempts(store):
    prepare(store)
    with store.write() as db:
        db.execute("UPDATE jobs SET content_mode='anonymous',state='paused'")
        db.execute("UPDATE job_items SET state='failed',attempts=3,error='URL_NOT_ALLOWED'")
    enable_browser(store, "browser")
    assert next_page(store)["task"] is not None


def test_browser_body_finishes_without_downloading_images_or_running_ocr(store):
    prepare(store)
    task = next_page(store)["task"]
    submit(store, task, images=["https://sns-webpic-qc.xhscdn.com/fixture"], expected_images=1)
    class NoMedia:
        def get(self, *args, **kwargs):
            pytest.fail("The body prototype must not download media")
        def recognize(self, *args):
            pytest.fail("The body prototype must not run OCR")
    worker = Worker(store, fetcher=NoMedia())
    assert worker.ocr is None
    worker.ocr = NoMedia()
    assert not worker.once()
    with store.read() as db:
        payload = json.loads(db.execute("SELECT r.payload FROM revisions r JOIN notes n ON n.current_revision=r.id "
                                        "WHERE n.id=?", (task["note_id"],)).fetchone()[0])
        assert payload["images"][0]["state"] == "deferred"
        assert payload["images"][0]["ocr_state"] == "not_requested"
        assert payload["content_state"] == "body_only"
        assert payload["processing_state"] == "ready"
        assert payload["gaps"] == ["MEDIA_DEFERRED"]
        assert job_report(db, "browser")["failure_summary"] == []
