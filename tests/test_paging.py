import pytest

from conftest import note
from xhs_context import common, core, images
from xhs_context.common import XhsError, dumps


def greedy_page(units, offset, make_response, max_chars, make_cursor):
    """The original one-unit-at-a-time implementation, kept as the reference."""
    common.integer(max_chars, "max_chars", 512, 200000)
    selected = []
    for unit in units[offset:]:
        candidate = selected + [unit]
        next_offset = offset + len(candidate)
        cursor = make_cursor(next_offset) if next_offset < len(units) else None
        response = make_response(candidate, cursor)
        if len(dumps(response)) > max_chars:
            if not selected:
                raise XhsError("OUTPUT_BUDGET_TOO_SMALL", "", details={"minimum_chars": len(dumps(response))})
            break
        selected = candidate
    cursor = make_cursor(offset + len(selected)) if offset + len(selected) < len(units) else None
    return make_response(selected, cursor)


def outcome(call):
    try:
        return call()
    except XhsError as error:
        return ("error", error.code, error.details)


def walk(method, request):
    pages = []
    while True:
        result = outcome(lambda: method(request))
        pages.append(result)
        if isinstance(result, tuple) or not result["data"]["next_cursor"]:
            return pages
        request = {"cursor": result["data"]["next_cursor"], "max_chars": request["max_chars"]}


@pytest.fixture
def library(store):
    revisions = []
    for number in range(1, 5):
        body = "\n".join(f"段落{i} " + "磁盘延迟排查。" * (3 + (i * number) % 17) for i in range(25))
        saved = note(number, body, title=f"磁盘 {number}")
        revisions.append({"note_id": saved["note_id"], "revision_id": store.publish(saved)})
    for number in range(5, 30):
        store.publish(note(number, "磁盘延迟的简短记录。", title="短"))
    return revisions


@pytest.mark.parametrize("budget", [512, 900, 1500, 2203, 4000, 9000, 30000, 200000])
def test_paging_matches_reference_for_read_search_and_image_plan(store, library, monkeypatch, budget):
    calls = {
        "read": lambda: walk(lambda r: core.read(store, r), {"items": library, "mode": "full", "max_chars": budget}),
        "evidence": lambda: walk(lambda r: core.read(store, r), {"items": [dict(i, block_ids=["b00003", "b00020"]) for i in library],
                                                               "context": 2, "max_chars": budget}),
        "search": lambda: walk(lambda r: core.search(store, r), {"queries": ["磁盘 延迟"], "limit": 50, "max_chars": budget}),
        "plan": lambda: walk(lambda r: images.image_plan(store, r),
                             {"note_ids": [i["note_id"] for i in library], "limit": 4, "max_chars": budget}),
    }
    fast = {name: call() for name, call in calls.items()}
    for module in (core, images):
        monkeypatch.setattr(module, "bounded_page", greedy_page)
    reference = {name: call() for name, call in calls.items()}
    for name in calls:
        assert fast[name] == reference[name], name
    for page in fast["read"]:
        if not isinstance(page, tuple):
            assert len(dumps(page)) <= budget
