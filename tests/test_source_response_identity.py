import httpx
import pytest

from conftest import card, create
from test_images import setup_note
from test_network import client_factory, proxied
from test_sync import page
from xhs_context.common import XhsError
from xhs_context.image_jobs import create_image_job
from xhs_context.images import ImageWorker
from xhs_context.invalid_sources import invalid_report
from xhs_context.jobs import receive_batch
from xhs_context.network import AnonymousFetcher
from xhs_context.worker import Worker


@pytest.mark.parametrize("target", [
    "/user/profile/unrelated", "/explore/" + "2" * 24, "/missing-route",
])
def test_redirect_away_from_note_is_not_a_permanent_note_failure(proxied, monkeypatch, store, target):
    create(store, "job", 1)
    receive_batch(store, "job", {"seq": 0, "items": [card(1)]})
    requests, proxies = [], []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": target}) if len(requests) == 1 else httpx.Response(404)
    client_factory(monkeypatch, handler, proxies)
    assert Worker(store, fetcher=AnonymousFetcher()).once()
    assert len(requests) == 1  # The unrelated redirect is rejected before requesting it.
    with store.read() as db:
        assert invalid_report(db)["total"] == 0
        assert db.execute("SELECT state,error FROM job_items").fetchone()[:] == ("failed", "NOTE_NOT_FOUND")


def test_same_note_redirect_keeps_supported_canonical_paths(proxied, monkeypatch):
    source = card(1)["source_url"]
    target = "https://www.xiaohongshu.com/discovery/item/" + card(1)["note_id"] + "?xsec_token=new"
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": target}) if len(seen) == 1 else httpx.Response(200, text="content")
    client_factory(monkeypatch, handler, [])
    assert AnonymousFetcher().get(source) == (b"content", "text/plain; charset=utf-8", target)
    assert seen == [source, target]


@pytest.mark.parametrize("status_code", [404, 410])
def test_error_status_from_the_target_note_still_establishes_unavailability(proxied, monkeypatch, status_code):
    client_factory(monkeypatch, lambda request: httpx.Response(status_code), [])
    with pytest.raises(XhsError) as exc:
        AnonymousFetcher().get(card(1)["source_url"])
    assert exc.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("kind", ["body", "images"])
@pytest.mark.parametrize("html", ["<main><h1>笔记不存在</h1></main>", page(card(1)["note_id"])])
def test_final_url_is_checked_before_parsing_or_marking_a_note(store, kind, html):
    saved, revision = setup_note(store)
    class WrongPage:
        def get(self, url, **kwargs):
            return html.encode(), "text/html", card(2)["source_url"]
    if kind == "body":
        create(store, "job", 1)
        receive_batch(store, "job", {"seq": 0, "items": [card(1)]})
        assert Worker(store, fetcher=WrongPage()).once()
        table = "job_items"
    else:
        create_image_job(store, {"note_ids": [saved["note_id"]]})
        assert ImageWorker(store, fetcher=WrongPage()).once()
        table = "image_items"
    with store.read() as db:
        assert invalid_report(db)["total"] == 0
        assert db.execute("SELECT current_revision FROM notes WHERE id=?", (saved["note_id"],)).fetchone()[0] == revision
        assert db.execute("SELECT state,error FROM " + table).fetchone()[:] == ("failed", "NOTE_NOT_FOUND")
