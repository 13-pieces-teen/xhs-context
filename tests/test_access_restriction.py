import json

import pytest

from conftest import card
from xhs_context.access import access_restriction, unlock_access
from xhs_context.browser_import import accept_page, enable_browser, next_page
from xhs_context.common import XhsError
from xhs_context.core import search, status
from xhs_context.jobs import create_job, job_report, receive_batch, set_job_state
from xhs_context.parser import parse_note
from xhs_context.storage import Store
from xhs_context.sync_service import dispatch_sync
from xhs_context.worker import Worker


def add_job(store, name, numbers):
    create_job(store, {"job_id": name, "collection": {"id": name, "name": name,
               "source_url": "https://www.xiaohongshu.com/user/profile/fixture"}})
    receive_batch(store, name, {"seq": 0, "items": [card(n) for n in numbers]})


def test_security_stop_survives_restart_blocks_all_acquisition_and_keeps_archive(store):
    add_job(store, "browser", [1, 2, 3])
    add_job(store, "anonymous", [4])
    enable_browser(store, "browser")
    saved = next_page(store)["task"]
    accept_page(store, "browser", {"note_id": saved["note_id"], "lease_id": saved["lease_id"], "snapshot": {
        "note_id": saved["note_id"], "page_url": saved["source_url"], "title": "马德里", "body": "马德里小吃正文",
        "body_present": True}})
    blocked = next_page(store)["task"]
    # A user pause may race the error page; its restriction must still be retained.
    set_job_state(store, "browser", "paused")
    assert accept_page(store, "browser", {"note_id": blocked["note_id"], "lease_id": blocked["lease_id"],
                                          "error": "ACCESS_RESTRICTED"})["restricted"]
    reopened = Store(store.root)
    class NoNetwork:
        def get(self, *args, **kwargs):
            pytest.fail("A locked archive must not access any source")
    worker = Worker(reopened, fetcher=NoNetwork())
    worker.recover()
    assert not worker.once()
    assert next_page(reopened)["task"] is None
    assert status(reopened)["data"]["access_restriction"]["site_code"] == "300013"
    assert search(reopened, {"queries": ["小吃"]})["data"]["items"]
    for action in [lambda: set_job_state(reopened, "browser", "active", retry_failed=True),
                   lambda: enable_browser(reopened, "anonymous"), lambda: add_job(reopened, "new", [5])]:
        with pytest.raises(XhsError) as error:
            action()
        assert error.value.code == "ACCESS_RESTRICTED"
    set_job_state(reopened, "browser", "paused")
    with reopened.read() as db:
        assert all(row[0] == "paused" for row in db.execute("SELECT state FROM jobs"))
        report = job_report(db, "browser")
        assert report["error"] == "ACCESS_RESTRICTED" and report["body_available"] == 1
        assert report["items"] == {"ready": 1, "failed": 1, "queued": 1}
        assert db.execute("SELECT attempts FROM job_items WHERE state='queued'").fetchone()[0] == 0
    with pytest.raises(XhsError, match="confirm-access-restored"):
        unlock_access(reopened)
    assert unlock_access(reopened, confirmed=True) == {"unlocked": True, "jobs_resumed": False}
    assert next_page(reopened)["task"] is None and not worker.once()
    set_job_state(reopened, "browser", "active")
    assert next_page(reopened)["task"] is not None


def test_stale_browser_result_cannot_set_a_restriction(store):
    add_job(store, "browser", [1])
    enable_browser(store, "browser")
    task = next_page(store)["task"]
    with pytest.raises(XhsError) as error:
        accept_page(store, "browser", {"note_id": task["note_id"], "lease_id": "stale", "error": "ACCESS_RESTRICTED"})
    assert error.value.code == "CONFLICT"
    with store.read() as db:
        assert access_restriction(db) is None


def test_restriction_report_survives_a_lost_lease_and_cannot_unlock(store):
    add_job(store, "browser", [1])
    dispatch_sync(store, "/v1/access/restrict", {"error": "ACCESS_RESTRICTED"})
    assert next_page(store)["access_restriction"]["site_code"] == "300013"
    with pytest.raises(XhsError):
        dispatch_sync(store, "/v1/access/restrict", {"error": "clear"})
    assert status(store)["data"]["access_restriction"]


def test_explicit_refresh_allows_a_new_browser_attempt_without_auto_resuming(store):
    add_job(store, "browser", [1])
    enable_browser(store, "browser")
    for _ in range(4):
        task = next_page(store)["task"]
        assert task is not None
        accept_page(store, "browser", {"note_id": task["note_id"], "lease_id": task["lease_id"],
                                      "error": "ACCESS_RESTRICTED"})
        # Reopening and ordinary status/resume must not clear the stop.
        reopened = Store(store.root)
        assert dispatch_sync(reopened, "/v1/status")["data"]["access_restriction"]
        with pytest.raises(XhsError) as error:
            dispatch_sync(reopened, "/v1/jobs/browser/resume", {})
        assert error.value.code == "ACCESS_RESTRICTED"
        assert dispatch_sync(reopened, "/v1/access/refresh", {})["data"] == {
            "unlocked": True, "jobs_resumed": False}
        report = status(reopened, "browser")["data"]
        assert report["access_restriction"] is None
        assert report["job"]["state"] == "paused" and report["job"]["error"] is None
        assert next_page(reopened)["task"] is None
        dispatch_sync(reopened, "/v1/jobs/browser/resume", {})
    task = next_page(store)["task"]
    accept_page(store, "browser", {"note_id": task["note_id"], "lease_id": task["lease_id"], "snapshot": {
        "note_id": task["note_id"], "page_url": task["source_url"], "title": "恢复后正文", "body": "正常内容",
        "body_present": True}})
    assert status(store, "browser")["data"]["job"]["body_available"] == 1


def test_refresh_is_explicit_validated_and_does_not_pause_an_unrestricted_job(store):
    add_job(store, "browser", [1])
    assert dispatch_sync(store, "/v1/access/refresh", {})["data"] == {
        "unlocked": False, "jobs_resumed": False}
    assert status(store, "browser")["data"]["job"]["state"] == "active"
    dispatch_sync(store, "/v1/access/restrict", {"error": "ACCESS_RESTRICTED"})
    for body in [None, {"resume": True}, []]:
        with pytest.raises(XhsError):
            dispatch_sync(store, "/v1/access/refresh", body)
        assert status(store)["data"]["access_restriction"]


@pytest.mark.parametrize("code", ["RATE_LIMITED", "ACCESS_RESTRICTED"])
def test_anonymous_restriction_stops_other_jobs(store, code):
    add_job(store, "one", [1])
    add_job(store, "two", [2])
    class Blocked:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            raise XhsError(code, "fixture")
    fetcher = Blocked()
    worker = Worker(store, fetcher=fetcher)
    assert worker.once() and not worker.once() and fetcher.calls == 1
    with store.read() as db:
        assert access_restriction(db)["error"] == code
        assert all(row[0] == "paused" for row in db.execute("SELECT state FROM jobs"))

    # Even an old exhausted restriction may be retried explicitly after refresh.
    with store.write() as db:
        db.execute("UPDATE job_items SET attempts=3 WHERE error=?", (code,))
    dispatch_sync(store, "/v1/access/refresh", {})
    assert not worker.once() and fetcher.calls == 1
    dispatch_sync(store, "/v1/jobs/one/resume", {})
    assert worker.once() and fetcher.calls == 2
    assert status(store)["data"]["access_restriction"]["error"] == code


def test_http_200_security_page_is_not_content_but_a_note_about_it_is_valid():
    text = "安全限制 检测到异常访问，为保障账号安全本次访问已被限制 300013"
    note_id = "1" * 24
    for state in ["", "<script>window.__INITIAL_STATE__={}</script>"]:
        with pytest.raises(XhsError) as error:
            parse_note("<main>" + text + "</main>" + state, note_id, "fixture")
        assert error.value.code == "ACCESS_RESTRICTED"
    state = {"note": {"noteId": note_id, "title": "错误页面讨论", "desc": text}}
    html = "<script>window.__INITIAL_STATE__=" + json.dumps(state, ensure_ascii=False) + "</script>"
    assert parse_note(html, note_id, "fixture")["body"] == text


def test_legacy_media_fetch_security_restriction_stops_the_entire_library(store):
    from test_sync import page
    add_job(store, "media", [1, 2])
    add_job(store, "other", [3])
    calls = []
    class RestrictedMedia:
        def get(self, url, *, media=False):
            calls.append((url, media))
            if media:
                raise XhsError("ACCESS_RESTRICTED", "fixture security redirect")
            html = page(card(1)["note_id"], imageList=[
                {"urlDefault": "https://sns-webpic-qc.xhscdn.com/first"},
                {"urlDefault": "https://sns-webpic-qc.xhscdn.com/second"}])
            return html.encode(), "text/html", url
    worker = Worker(store, fetcher=RestrictedMedia(), media=True, ocr=False)
    assert worker.once("media") and not worker.once()
    assert len(calls) == 2 and calls[-1][1] is True
    with store.read() as db:
        assert access_restriction(db)["error"] == "ACCESS_RESTRICTED"
        assert {row[0] for row in db.execute("SELECT state FROM jobs")} == {"paused"}


@pytest.mark.parametrize("pause", ["restriction", "user", "shutdown"])
def test_pause_committed_between_last_check_and_publish_keeps_saved_revision(store, pause):
    from threading import Event
    from conftest import note
    from test_sync import page
    from xhs_context.access import restrict_access

    add_job(store, "one", [1])
    previous = store.publish(note(1))
    stop = Event()

    class Fetcher:
        def get(self, url, **kwargs):
            return page(card(1)["note_id"], desc="new content").encode(), "text/html", url

    worker = Worker(store, fetcher=Fetcher(), stop_event=stop)
    stopped = worker.stopped
    checks = 0

    def concurrent_pause(*args):
        nonlocal checks
        result = stopped(*args)
        checks += 1
        if checks == 2:
            if pause == "restriction":
                with store.write() as db:
                    restrict_access(db)
            elif pause == "user":
                set_job_state(store, "one", "paused")
            else:
                stop.set()
        return result

    worker.stopped = concurrent_pause
    assert worker.once() and checks == 2
    with store.read() as db:
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (card(1)["note_id"],)).fetchone()[0] == previous
        assert db.execute("SELECT state,attempts FROM job_items").fetchone()[:] == ("queued", 0)
