import pytest

from conftest import card, note
from test_browser_import import snapshot
from test_sync import page
from xhs_context.browser_import import accept_page, enable_browser, next_page
from xhs_context.access import access_restriction, refresh_access
from xhs_context.common import XhsError
from xhs_context.invalid_sources import invalid_report, invalid_source, mark_invalid, skip_invalid_items
from xhs_context.jobs import create_job, finish_scan, receive_batch, set_job_state
from xhs_context.storage import Store
from xhs_context.worker import Worker


def add_job(store, job_id, numbers, collection_id=None, token="first"):
    collection_id = collection_id or job_id
    create_job(store, {"job_id": job_id, "expected_count": len(numbers), "collection": {
        "id": collection_id, "name": collection_id,
        "source_url": "https://www.xiaohongshu.com/user/profile/" + collection_id}})
    receive_batch(store, job_id, {"seq": 0, "items": [
        dict(card(number), source_url=card(number)["source_url"] + "?xsec_token=" + token) for number in numbers]})


@pytest.mark.parametrize("code", ["SOURCE_UNAVAILABLE", "SOURCE_LINK_EXPIRED"])
def test_source_failure_is_permanent_across_jobs_collections_restarts_and_new_links(store, code):
    add_job(store, "first", [1], "collection")
    add_job(store, "other-queued", [1])
    old_revision = store.publish(note(1))
    enable_browser(store, "first")
    task = next_page(store)["task"]
    accept_page(store, "first", {"note_id": task["note_id"], "lease_id": task["lease_id"], "error": code})
    finish_scan(store, "first", {"batch_count": 1, "end_evidence": "page_end"})
    reopened = Store(store.root)
    reopened.initialize()
    add_job(reopened, "same-collection-update", [1], "collection", token="replacement")
    add_job(reopened, "other-collection", [1], token="another-new-token")
    class NeverFetch:
        def get(self, *args, **kwargs):
            pytest.fail("A recorded invalid note must never be fetched again")
    for job_id in ["first", "other-queued", "same-collection-update", "other-collection"]:
        set_job_state(reopened, job_id, "active", retry_failed=True)
        enable_browser(reopened, job_id)
    assert next_page(reopened)["task"] is None
    assert not Worker(reopened, fetcher=NeverFetch()).once()
    with reopened.read() as db:
        assert {row[0] for row in db.execute("SELECT state FROM job_items")} == {"skipped"}
        assert [row[0] for row in db.execute("SELECT attempts FROM job_items WHERE job_id<>'first'")] == [0, 0, 0]
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (task["note_id"],)).fetchone()[0] == old_revision
        report = invalid_report(db)
        assert report["total"] == 1 and not report["has_more"]
        assert report["items"][0]["source_url"] == task["source_url"]
        assert report["items"][0]["error"] == code and report["items"][0]["job_id"] == "first"


def test_read_only_legacy_report_and_upgrade_preserve_first_failure_and_saved_body(store):
    add_job(store, "old", [1, 2, 3])
    add_job(store, "later", [1])
    old_revision = store.publish(note(1))
    with store.write() as db:
        db.execute("UPDATE job_items SET state='skipped',error='SOURCE_UNAVAILABLE',updated_at='2026-01-01' WHERE job_id='old' AND note_id=?", (card(1)["note_id"],))
        db.execute("UPDATE job_items SET state='failed',error='SOURCE_LINK_EXPIRED',updated_at='2026-01-02' WHERE job_id='old' AND note_id=?", (card(2)["note_id"],))
        db.execute("UPDATE job_items SET state='failed',error='NOTE_NOT_FOUND' WHERE job_id='old' AND note_id=?", (card(3)["note_id"],))
        db.execute("UPDATE job_items SET state='failed',error='SOURCE_LINK_EXPIRED',updated_at='2026-01-03' WHERE job_id='later'")
        db.execute("DROP TABLE invalid_notes")
        generation = store.generation(db)
    with store.read() as db:
        before = invalid_report(db)
        assert before["total"] == 2
        assert invalid_source(db, card(1)["note_id"])["error"] == "SOURCE_UNAVAILABLE"
        assert invalid_source(db, card(3)["note_id"]) is None
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='invalid_notes'").fetchone()
    store.initialize()
    with store.read() as db:
        assert invalid_report(db) == before
        assert store.generation(db) == generation
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (card(1)["note_id"],)).fetchone()[0] == old_revision
        assert {row[0] for row in db.execute("SELECT state FROM job_items WHERE note_id=?", (card(1)["note_id"],))} == {"skipped"}
    store.initialize()
    with store.read() as db:
        assert invalid_report(db) == before and store.generation(db) == generation


@pytest.mark.parametrize("entry", ["worker", "browser", "resume", "receive"])
def test_direct_write_entry_upgrades_old_history_before_any_second_attempt(store, entry):
    add_job(store, "legacy", [1])
    add_job(store, "pending", [1])
    with store.write() as db:
        db.execute("UPDATE job_items SET state='failed',error='SOURCE_UNAVAILABLE' WHERE job_id='legacy'")
        db.execute("DROP TABLE invalid_notes")
        if entry == "browser":
            db.execute("UPDATE jobs SET content_mode='browser'")
    class NeverFetch:
        def get(self, *args, **kwargs):
            pytest.fail("Legacy source failures must block new jobs before a request")
    if entry == "worker":
        assert not Worker(store, fetcher=NeverFetch()).once()
    elif entry == "browser":
        assert next_page(store)["task"] is None
    elif entry == "resume":
        set_job_state(store, "pending", "active", retry_failed=True)
    else:
        receive_batch(store, "pending", {"seq": 1, "items": [card(1)]})
    with store.read() as db:
        assert invalid_report(db)["total"] == 1
        assert {row[0] for row in db.execute("SELECT state FROM job_items")} == {"skipped"}
        assert sum(row[0] for row in db.execute("SELECT attempts FROM job_items")) == 0


def test_invalid_registry_is_paginated_idempotent_and_rejects_ambiguous_errors(store):
    add_job(store, "notes", [1, 2, 3])
    with store.write() as db:
        first = mark_invalid(db, card(1)["note_id"], "SOURCE_UNAVAILABLE", card(1)["source_url"], "notes")
        mark_invalid(db, card(1)["note_id"], "SOURCE_LINK_EXPIRED", card(1)["source_url"] + "?xsec_token=new")
        assert invalid_source(db, card(1)["note_id"]) == first
        mark_invalid(db, card(2)["note_id"], "SOURCE_LINK_EXPIRED", card(2)["source_url"])
        skip_invalid_items(db)
    with store.read() as db:
        first_page = invalid_report(db, limit=1)
        second_page = invalid_report(db, limit=1, offset=1)
        assert first_page["total"] == second_page["total"] == 2
        assert first_page["has_more"] and not second_page["has_more"]
        assert {first_page["items"][0]["note_id"], second_page["items"][0]["note_id"]} == {card(1)["note_id"], card(2)["note_id"]}
        assert invalid_report(db, offset=2)["items"] == []
        for args in [{"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": 100001}]:
            with pytest.raises(XhsError):
                invalid_report(db, **args)
    for code in ["NOTE_NOT_FOUND", "ACCESS_BLOCKED", "ACCESS_RESTRICTED", "RATE_LIMITED", "IMAGE_NOT_FOUND"]:
        with store.write() as db:
            with pytest.raises(XhsError):
                mark_invalid(db, card(3)["note_id"], code, card(3)["source_url"])
            assert invalid_source(db, card(3)["note_id"]) is None


def test_late_browser_content_cannot_revive_a_recorded_invalid_id(store):
    add_job(store, "browser", [1])
    old_revision = store.publish(note(1))
    enable_browser(store, "browser")
    task = next_page(store)["task"]
    with store.write() as db:
        mark_invalid(db, task["note_id"], "SOURCE_UNAVAILABLE", task["source_url"], "another-job")
    result = accept_page(store, "browser", {"note_id": task["note_id"], "lease_id": task["lease_id"], "snapshot": snapshot(task)})
    assert result["skipped"]
    with store.read() as db:
        assert db.execute("SELECT state FROM job_items").fetchone()[0] == "skipped"
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (task["note_id"],)).fetchone()[0] == old_revision


def test_inflight_security_restriction_survives_invalid_mark_but_replay_does_not_relock(store):
    add_job(store, "browser", [1])
    add_job(store, "other", [2])
    enable_browser(store, "browser")
    task = next_page(store)["task"]
    with store.write() as db:
        mark_invalid(db, task["note_id"], "SOURCE_UNAVAILABLE", task["source_url"], "another-job")
    request = {"note_id": task["note_id"], "lease_id": task["lease_id"], "error": "ACCESS_RESTRICTED"}
    result = accept_page(store, "browser", request)
    assert result["restricted"] and result["skipped"]
    with store.read() as db:
        assert access_restriction(db)
        assert {row[0] for row in db.execute("SELECT state FROM jobs")} == {"paused"}
        assert db.execute("SELECT state,error FROM job_items WHERE job_id='browser'").fetchone()[:] == ("skipped", "SOURCE_UNAVAILABLE")
    refresh_access(store, {})
    assert accept_page(store, "browser", request)["duplicate"]
    with store.read() as db:
        assert access_restriction(db) is None


@pytest.mark.parametrize("late_error", [None, "NETWORK_ERROR"])
def test_inflight_worker_result_cannot_revive_a_recorded_invalid_id(store, late_error):
    add_job(store, "anonymous", [1])
    old_revision = store.publish(note(1))
    class ConcurrentFailure:
        def get(self, url, **kwargs):
            with store.write() as db:
                mark_invalid(db, card(1)["note_id"], "SOURCE_UNAVAILABLE", url, "other-job")
            if late_error:
                raise XhsError(late_error, "late result")
            return page(card(1)["note_id"], desc="new content").encode(), "text/html", url
    worker = Worker(store, fetcher=ConcurrentFailure())
    assert worker.once() and not worker.once()
    worker.recover()
    with store.read() as db:
        assert db.execute("SELECT state FROM job_items").fetchone()[0] == "skipped"
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (card(1)["note_id"],)).fetchone()[0] == old_revision
