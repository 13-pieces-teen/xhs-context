import json
import pytest

from xhs_context.common import XhsError
from xhs_context.jobs import receive_batch, finish_scan, job_report
from xhs_context.parser import parse_note
from xhs_context.processing import finish_content
from xhs_context.worker import Worker, sync_lock
from conftest import card, create, note


def finish(store, job_id, evidence="page_end", batches=1):
    return finish_scan(store, job_id, {"batch_count": batches, "end_evidence": evidence})


def test_batch_ack_retry_conflict_and_order(store):
    create(store, "job", 2)
    batch = {"seq": 0, "items": [card(1), card(2)]}
    assert not receive_batch(store, "job", batch)["duplicate"]
    assert receive_batch(store, "job", batch)["duplicate"]
    with pytest.raises(XhsError) as caught:
        receive_batch(store, "job", {"seq": 0, "items": [card(1)]})
    assert caught.value.code == "CONFLICT"
    with pytest.raises(XhsError):
        finish(store, "job", batches=2)
    assert finish(store, "job")["scan_state"] == "complete"
    with store.read() as db:
        assert job_report(db, "job")["discovered"] == 2


def test_partial_scan_never_removes_and_two_complete_scans_confirm_absence(store):
    def run(job_id, ids, evidence="page_end"):
        create(store, job_id)
        receive_batch(store, job_id, {"seq": 0, "items": [card(i) for i in ids]})
        finish(store, job_id, evidence)
    def membership():
        with store.read() as db:
            return dict(db.execute("SELECT * FROM memberships WHERE note_id=?", (card(2)["note_id"],)).fetchone())
    run("first", [1, 2])
    run("partial", [1], "unknown")
    assert membership()["state"] == "present"
    run("complete-1", [1])
    assert membership()["state"] == "unconfirmed"
    finish(store, "complete-1")
    assert membership()["miss_count"] == 1
    run("complete-2", [1])
    assert membership()["state"] == "removed"


def test_expected_count_mismatch_and_unknown_end_remain_partial(store):
    create(store, "mismatch", 2)
    receive_batch(store, "mismatch", {"seq": 0, "items": [card(1)]})
    assert finish(store, "mismatch")["scan_state"] == "partial"
    create(store, "unknown")
    receive_batch(store, "unknown", {"seq": 0, "items": [card(1)]})
    assert finish(store, "unknown", "unknown")["scan_state"] == "partial"


def page(identifier, **extra):
    payload = {"noteId": identifier, "title": "马德里", "desc": "正文含有 undefined 这个普通词", "imageList": [], **extra}
    return '<script>window.__INITIAL_STATE__=' + json.dumps({"note": {"noteDetailMap": {identifier: {"note": payload}}}},
                                                              ensure_ascii=False) + ';</script>'


def test_parser_keeps_all_images_and_does_not_execute_js():
    identifier = card(1)["note_id"]
    images = [{"urlDefault": "https://sns-webpic-qc.xhscdn.com/" + str(i)} for i in range(25)]
    parsed = parse_note(page(identifier, imageList=images), identifier, card(1)["source_url"])
    assert parsed["expected_images"] == 25 and len(parsed["images"]) == 25
    assert "undefined" in parsed["body"]
    with pytest.raises(XhsError):
        parse_note('<script>window.__INITIAL_STATE__=evil();</script>', identifier, card(1)["source_url"])


def test_parser_does_not_use_author_bio_as_note_body():
    identifier = card(1)["note_id"]
    html = '<script>window.__INITIAL_STATE__=' + json.dumps({
        "noteId": identifier, "title": "只有标题", "imageList": [],
        "user": {"desc": "作者简介，不能变成笔记正文"}}) + ';</script>'
    parsed = parse_note(html, identifier, card(1)["source_url"])
    assert parsed["body_state"] == "missing"


def test_metadata_and_unknown_images_are_not_complete():
    result = finish_content({"body_state": "missing", "images": [], "expected_images": None})
    assert result["content_state"] == "metadata_only"
    assert "IMAGE_COUNT_UNKNOWN" in result["gaps"]


def test_feed_preview_does_not_become_verified_full_note():
    identifier = card(1)["note_id"]
    html = '<script>window.__INITIAL_STATE__=' + json.dumps({"feed": [{"noteId": identifier,
        "title": "卡片", "desc": "可能是截断的摘要", "imageList": []}]}) + ';</script>'
    result = finish_content(parse_note(html, identifier, card(1)["source_url"]))
    assert result["body_state"] == "unverified"
    assert result["content_state"] != "complete"


def test_worker_recovers_and_preserves_last_good_version_on_block(store):
    create(store, "job")
    receive_batch(store, "job", {"seq": 0, "items": [card(1)]})
    revision = store.publish(note())
    class Blocked:
        def get(self, *args, **kwargs):
            raise XhsError("ACCESS_BLOCKED", "blocked")
    worker = Worker(store, ocr=False, fetcher=Blocked())
    with store.write() as db:
        db.execute("UPDATE job_items SET state='running',attempts=1")
    worker.recover()
    assert worker.once()
    with store.read() as db:
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (card(1)["note_id"],)).fetchone()[0] == revision
        assert job_report(db, "job")["state"] == "paused"
        assert job_report(db, "job")["items"] == {"failed": 1}


def test_worker_resolves_anonymous_html_with_injected_transport(store):
    create(store, "job")
    receive_batch(store, "job", {"seq": 0, "items": [card(1)]})
    class FakeTransport:
        def get(self, url, **kwargs):
            return page(card(1)["note_id"]).encode(), "text/html", url
    assert Worker(store, ocr=False, fetcher=FakeTransport()).once()
    with store.read() as db:
        assert job_report(db, "job")["items"] == {"ready": 1}


@pytest.mark.parametrize("failure", ["media", "ocr"])
def test_refresh_failure_keeps_previous_searchable_image_evidence(store, failure):
    import io
    from PIL import Image
    from xhs_context.core import search
    from xhs_context.processing import store_image

    def picture(color):
        stream = io.BytesIO()
        Image.new("RGB", (100, 100), color).save(stream, "PNG")
        return stream.getvalue()

    class GoodOCR:
        def recognize(self, path):
            return [{"text": "保留证据关键词", "confidence": .99, "box": [[0, 0], [50, 0], [50, 20], [0, 20]]}]

    class FailedOCR:
        def recognize(self, path):
            raise RuntimeError("simulated OCR failure")

    asset = {"asset_id": "img-1", "source_url": "https://sns-webpic-qc.xhscdn.com/image", "quality": "page_default"}
    previous = note()
    previous.update(images=[store_image(store, picture("white"), asset, GoodOCR())], expected_images=1)
    revision = store.publish(finish_content(previous))
    create(store, "refresh")
    receive_batch(store, "refresh", {"seq": 0, "items": [card(1)]})

    class Transport:
        def get(self, url, *, media=False):
            if not media:
                return page(card(1)["note_id"], desc="已经更新的正文", imageList=[{"urlDefault": asset["source_url"]}]).encode(), "text/html", url
            if failure == "media":
                raise XhsError("NETWORK_ERROR", "simulated media failure")
            return picture("blue"), "image/png", url

    worker = Worker(store, ocr=False, fetcher=Transport(), media=True)
    worker.ocr = FailedOCR()
    assert worker.once()
    with store.read() as db:
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (card(1)["note_id"],)).fetchone()[0] == revision
        report = job_report(db, "refresh")
        assert report["items"] == {"failed": 1}
        assert report["failures"][0]["error"].startswith("INCOMPLETE_REFRESH:")
    assert search(store, {"queries": ["保留证据关键词"]})["data"]["items"][0]["revision_id"] == revision


def test_one_sync_worker_per_library(store):
    with sync_lock(store):
        with pytest.raises(XhsError):
            with sync_lock(store):
                pass


def test_collection_cannot_have_two_open_scans(store):
    create(store, "one")
    with pytest.raises(XhsError) as caught:
        create(store, "two")
    assert caught.value.code == "SCAN_ALREADY_RUNNING"
    assert caught.value.details["job_id"] == "one"


def test_stale_orphaned_scan_is_taken_over_without_removing_members(store):
    create(store, "orphan")
    receive_batch(store, "orphan", {"seq": 0, "items": [card(1), card(2)]})
    with store.write() as db:
        db.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00.000+00:00' WHERE id='orphan'")
    result = create(store, "fresh")
    assert result["superseded_job"] == "orphan"
    with store.read() as db:
        old = job_report(db, "orphan")
        assert old["scan_state"] == "partial" and old["end_evidence"] == "interrupted"
        states = {r[0] for r in db.execute("SELECT state FROM memberships")}
    assert states == {"present"}
    # The lost browser's late batch is rejected with a dedicated code it can act on.
    with pytest.raises(XhsError) as caught:
        receive_batch(store, "orphan", {"seq": 1, "items": [card(3)]})
    assert caught.value.code == "SCAN_FINALIZED"
    assert finish(store, "orphan", batches=1)["duplicate"]


def test_cli_abandon_closes_an_open_scan(store, capsys):
    from xhs_context.cli import main
    create(store, "open")
    assert main(["--library", str(store.root), "sync", "abandon", "--job", "open"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["changed"]
    assert main(["--library", str(store.root), "sync", "abandon", "--job", "open"]) == 0
    assert not json.loads(capsys.readouterr().out)["data"]["changed"]
    assert create(store, "next")["duplicate"] is False
    assert main(["--library", str(store.root), "sync", "abandon", "--job", "missing"]) == 4

