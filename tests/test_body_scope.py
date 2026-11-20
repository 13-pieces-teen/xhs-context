import json

import pytest

from conftest import card, create, note
from test_sync import page, finish
from test_browser_import import prepare, submit
from xhs_context.browser_import import next_page, enable_browser
from xhs_context.core import search, read, status
from xhs_context.jobs import receive_batch, job_report, set_job_state
from xhs_context.processing import finish_content
from xhs_context.worker import Worker


def test_anonymous_body_pipeline_never_requests_media_or_ocr(store):
    create(store, "body", 1)
    receive_batch(store, "body", {"seq": 0, "items": [card(1)]})
    finish(store, "body")
    class TextTransport:
        def get(self, url, *, media=False):
            assert not media
            return page(card(1)["note_id"], imageList=[{"urlDefault": "https://sns-webpic-qc.xhscdn.com/image"}]).encode(), "text/html", url
    class NoOCR:
        def recognize(self, *args):
            pytest.fail("OCR must not run for body scope")
    worker = Worker(store, fetcher=TextTransport())
    assert worker.ocr is None
    worker.ocr = NoOCR()
    assert worker.once()
    with store.read() as db:
        report = job_report(db, "body")
        assert report["items"] == {"ready": 1} and report["body_complete"]
        assert not report["content_complete"] and not report["failure_summary"]
    result = search(store, {"queries": ["undefined"]})["data"]["items"][0]
    assert result["content_scope"] == "body" and result["gaps"] == ["MEDIA_DEFERRED"]
    evidence = read(store, {"items": [{k: result[k] for k in ("note_id", "revision_id")}], "mode": "full"})
    assert any("undefined" in b["text"] for item in evidence["data"]["items"] for b in item["blocks"])
    asset = read(store, {"items": [{k: result[k] for k in ("note_id", "revision_id")}], "mode": "asset"})
    assert asset["partial"] and asset["data"]["errors"][0]["code"] == "MEDIA_DEFERRED"
    assert status(store)["data"]["coverage"]["body_available"] == 1
    assert status(store)["data"]["coverage"]["content_complete"] == 0


def test_upgrade_reuses_existing_body_and_preserves_revision_history(store):
    create(store, "old", 1)
    receive_batch(store, "old", {"seq": 0, "items": [card(1)]})
    finish(store, "old")
    previous = note()
    previous.update(images=[{"asset_id": "img-1", "source_url": "", "state": "failed",
                             "error": "URL_NOT_ALLOWED", "ocr_state": "not_ready"}], expected_images=1)
    previous = finish_content(previous)
    revision = store.publish(previous)
    with store.write() as db:
        db.execute("UPDATE job_items SET state='partial',attempts=3,error=?", (",".join(previous["gaps"]),))
    worker = Worker(store)
    worker.recover()
    with store.read() as db:
        report = job_report(db, "old")
        assert report["items"] == {"ready": 1} and report["body_complete"]
        assert not report["failure_summary"]
        assert json.loads(db.execute("SELECT payload FROM revisions WHERE id=?", (revision,)).fetchone()[0]) == previous
        generation = store.generation(db)
    worker.recover()
    with store.read() as db:
        assert store.generation(db) == generation
    assert search(store, {"queries": ["马德里"]})["data"]["items"]


@pytest.mark.parametrize("override,expected", [({"body": ""}, "BODY_EMPTY"),
                                              ({"body_present": False}, "BODY_MISSING")])
@pytest.mark.parametrize("retry", ["resume", "browser"])
def test_no_text_is_not_completed_and_retry_rereads_page(store, override, expected, retry):
    prepare(store)
    task = next_page(store)["task"]
    submit(store, task, **override)
    with store.read() as db:
        report = job_report(db, "browser")
        assert report["items"] == {"partial": 1, "queued": 1}
        assert report["body_available"] == 0
        assert report["failure_summary"] == [{"error": expected, "count": 1}]
    if retry == "resume":
        set_job_state(store, "browser", "active", retry_failed=True)
    else:
        enable_browser(store, "browser")
    with store.read() as db:
        payload = json.loads(db.execute("SELECT payload FROM job_items WHERE note_id=?", (task["note_id"],)).fetchone()[0])
        assert "browser_snapshot" not in payload and "browser_lease" not in payload


def test_fresh_browser_body_updates_existing_text_and_keeps_old_revision(store):
    prepare(store)
    task = next_page(store)["task"]
    previous = note(int(task["note_id"], 16))
    old_revision = store.publish(previous)
    submit(store, task, body="新正文版本关键词")
    result = search(store, {"queries": ["新正文版本关键词"]})["data"]["items"][0]
    assert result["revision_id"] != old_revision
    old = read(store, {"items": [{"note_id": task["note_id"], "revision_id": old_revision}], "mode": "full"})
    assert any("马德里" in b["text"] for i in old["data"]["items"] for b in i["blocks"])


def test_missing_browser_body_keeps_previous_good_evidence(store):
    prepare(store)
    task = next_page(store)["task"]
    old_revision = store.publish(note(int(task["note_id"], 16)))
    submit(store, task, body="", body_present=False)
    with store.read() as db:
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (task["note_id"],)).fetchone()[0] == old_revision
        assert job_report(db, "browser")["failure_summary"] == [{"error": "BODY_MISSING", "count": 1}]
