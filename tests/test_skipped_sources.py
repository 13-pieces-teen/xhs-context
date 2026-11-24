import pytest

from conftest import card, create, note
from test_browser_import import submit
from test_sync import finish, page
from xhs_context.browser_import import accept_page, enable_browser, next_page
from xhs_context.common import XhsError
from xhs_context.core import status
from xhs_context.jobs import receive_batch, set_job_state
from xhs_context.worker import Worker


def prepare(store):
    create(store, "reading", 2)
    receive_batch(store, "reading", {"seq": 0, "items": [card(1), card(2)]})


@pytest.mark.parametrize("code", ["SOURCE_UNAVAILABLE", "SOURCE_LINK_EXPIRED"])
@pytest.mark.parametrize("archived", [False, True])
def test_browser_skips_unavailable_sources_and_keeps_reading(store, code, archived):
    prepare(store)
    previous = store.publish(note(1)) if archived else None
    enable_browser(store, "reading")
    missing = next_page(store)["task"]
    result = {"note_id": missing["note_id"], "lease_id": missing["lease_id"], "error": code}
    accept_page(store, "reading", result)
    assert accept_page(store, "reading", result)["duplicate"]
    report = status(store, "reading")["data"]["job"]
    assert report["state"] == "active" and report["error"] is None
    assert report["items"] == {"queued": 1, "skipped": 1}
    assert report["skipped_count"] == 1
    assert report["skipped_summary"] == [{"error": code, "count": 1}]
    assert not report["failure_summary"] and not report["read_complete"]

    following = next_page(store)["task"]
    assert following["note_id"] != missing["note_id"]
    submit(store, following)
    assert not status(store, "reading")["data"]["job"]["read_complete"]  # List still unconfirmed.
    finish(store, "reading")
    report = status(store, "reading")["data"]["job"]
    assert report["read_complete"] and not report["body_complete"]
    assert report["body_available"] == 1 + int(archived)
    for _ in range(2):
        set_job_state(store, "reading", "paused")
        set_job_state(store, "reading", "active", retry_failed=True)
        enable_browser(store, "reading")
        assert next_page(store)["task"] is None
    if archived:
        with store.read() as db:
            assert db.execute("SELECT current_revision FROM notes WHERE id=?", (missing["note_id"],)).fetchone()[0] == previous


@pytest.mark.parametrize("code", ["SOURCE_UNAVAILABLE", "SOURCE_LINK_EXPIRED"])
def test_anonymous_worker_skips_source_errors_without_overwriting_archive(store, code):
    prepare(store)
    previous = store.publish(note(1))
    finish(store, "reading")
    calls = []
    class Fetcher:
        def get(self, url, **kwargs):
            calls.append(url)
            if url == card(1)["source_url"]:
                raise XhsError(code, "fixture")
            return page(card(2)["note_id"]).encode(), "text/html", url
    worker = Worker(store, fetcher=Fetcher())
    assert worker.once() and worker.once() and not worker.once()
    report = status(store, "reading")["data"]["job"]
    assert report["state"] == "active" and report["error"] is None
    assert report["read_complete"] and not report["body_complete"]
    assert report["items"] == {"ready": 1, "skipped": 1}
    assert report["skipped_summary"] == [{"error": code, "count": 1}]
    set_job_state(store, "reading", "active", retry_failed=True)
    assert not worker.once() and len(calls) == 2
    with store.read() as db:
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (card(1)["note_id"],)).fetchone()[0] == previous


@pytest.mark.parametrize("mode", ["anonymous", "browser"])
def test_missing_parsed_target_is_a_failure_not_a_confirmed_deleted_source(store, mode):
    prepare(store)
    if mode == "browser":
        enable_browser(store, "reading")
        task = next_page(store)["task"]
        accept_page(store, "reading", {"note_id": task["note_id"], "lease_id": task["lease_id"], "error": "NOTE_NOT_FOUND"})
    else:
        class MissingTarget:
            def get(self, *args, **kwargs):
                raise XhsError("NOTE_NOT_FOUND", "fixture")
        assert Worker(store, fetcher=MissingTarget()).once()
    report = status(store, "reading")["data"]["job"]
    assert report["items"] == {"failed": 1, "queued": 1}
    assert report["skipped_count"] == 0 and report["skipped_summary"] == []
    assert report["failure_summary"] == [{"error": "NOTE_NOT_FOUND", "count": 1}]
    assert not report["read_complete"]


@pytest.mark.parametrize("action", ["resume", "switch_to_browser", "enable_browser_again"])
def test_explicit_continuation_migrates_old_source_failures_without_retrying(store, action):
    create(store, "reading", 3)
    receive_batch(store, "reading", {"seq": 0, "items": [card(1), card(2), card(3)]})
    with store.write() as db:
        db.execute("UPDATE jobs SET state='paused',content_mode=? WHERE id='reading'",
                   ("browser" if action == "enable_browser_again" else "anonymous",))
        for number, code in enumerate(["SOURCE_UNAVAILABLE", "SOURCE_LINK_EXPIRED", "NOTE_NOT_FOUND"], 1):
            db.execute("UPDATE job_items SET state='failed',error=?,attempts=1 WHERE note_id=?",
                       (code, card(number)["note_id"]))
    assert status(store, "reading")["data"]["job"]["skipped_count"] == 0
    if action == "resume":
        set_job_state(store, "reading", "active", retry_failed=True)
    else:
        enable_browser(store, "reading")
    report = status(store, "reading")["data"]["job"]
    assert report["items"] == {"queued": 1, "skipped": 2}
    assert report["skipped_count"] == 2 and not report["read_complete"]
    assert {row["error"] for row in report["skipped_summary"]} == {"SOURCE_UNAVAILABLE", "SOURCE_LINK_EXPIRED"}
    enable_browser(store, "reading")
    assert next_page(store)["task"]["note_id"] == card(3)["note_id"]
