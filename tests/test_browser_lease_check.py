import pytest

from conftest import card, create
from xhs_context.access import restrict_access
from xhs_context.browser_import import accept_page, enable_browser, next_page
from xhs_context.common import XhsError
from xhs_context.invalid_sources import mark_invalid
from xhs_context.jobs import receive_batch, set_job_state
from xhs_context.sync_service import dispatch_sync


def leased(store):
    create(store, "browser", 1)
    receive_batch(store, "browser", {"seq": 0, "items": [card(1)]})
    enable_browser(store, "browser")
    task = next_page(store)["task"]
    return {key: task[key] for key in ("job_id", "note_id", "lease_id")}


def allowed(store, request):
    return dispatch_sync(store, "/v1/browser/check", request)["data"]["allowed"]


@pytest.mark.parametrize("change", ["invalid", "paused", "expired", "reassigned", "completed", "restricted"])
def test_specific_browser_lease_is_rechecked_without_mutation(store, change):
    request = leased(store)
    assert allowed(store, request) is True
    if change == "invalid":
        with store.write() as db:
            mark_invalid(db, card(1)["note_id"], "SOURCE_UNAVAILABLE", card(1)["source_url"], "other")
    elif change == "paused":
        set_job_state(store, "browser", "paused")
    elif change == "expired":
        with store.write() as db:
            db.execute("UPDATE job_items SET updated_at='2000-01-01' WHERE job_id='browser'")
    elif change == "reassigned":
        set_job_state(store, "browser", "paused")
        set_job_state(store, "browser", "active", retry_failed=True)
        replacement = next_page(store)["task"]
        assert replacement["lease_id"] != request["lease_id"]
    elif change == "completed":
        accept_page(store, "browser", {"note_id": request["note_id"], "lease_id": request["lease_id"],
                                       "error": "BROWSER_PAUSED"})
    elif change == "restricted":
        with store.write() as db:
            restrict_access(db)
    with store.read() as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM job_items")]
    assert allowed(store, request) is False
    with store.read() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM job_items")] == before


def test_lease_check_rejects_another_note_job_and_malformed_requests(store):
    request = leased(store)
    for key, value in [("job_id", "unknown"), ("note_id", "f" * 24), ("lease_id", "unknown")]:
        assert allowed(store, {**request, key: value}) is False
    for bad in [{}, {**request, "unexpected": True}, {**request, "lease_id": None}]:
        with pytest.raises(XhsError):
            allowed(store, bad)


def test_cancelled_invalid_lease_stays_skipped_and_next_note_is_available(store):
    request = leased(store)
    receive_batch(store, "browser", {"seq": 1, "items": [card(2)]})
    with store.write() as db:
        mark_invalid(db, card(1)["note_id"], "SOURCE_UNAVAILABLE", card(1)["source_url"], "other")
    assert not allowed(store, request)
    result = accept_page(store, "browser", {"note_id": request["note_id"], "lease_id": request["lease_id"],
                                           "error": "BROWSER_PAUSED"})
    assert result["skipped"]
    with store.read() as db:
        assert db.execute("SELECT state,error FROM job_items WHERE note_id=?", (card(1)["note_id"],)).fetchone()[:] == (
            "skipped", "SOURCE_UNAVAILABLE")
    assert next_page(store)["task"]["note_id"] == card(2)["note_id"]


def test_cancelled_old_lease_cannot_change_its_replacement(store):
    request = leased(store)
    set_job_state(store, "browser", "paused")
    set_job_state(store, "browser", "active", retry_failed=True)
    replacement = next_page(store)["task"]
    assert not allowed(store, request)
    with store.read() as db:
        before = tuple(db.execute("SELECT * FROM job_items").fetchone())
    with pytest.raises(XhsError) as exc:
        accept_page(store, "browser", {"note_id": request["note_id"], "lease_id": request["lease_id"],
                                       "error": "BROWSER_PAUSED"})
    assert exc.value.code == "CONFLICT"
    assert allowed(store, {key: replacement[key] for key in request})
    with store.read() as db:
        assert tuple(db.execute("SELECT * FROM job_items").fetchone()) == before
