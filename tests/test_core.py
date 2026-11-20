import json
import socket
import sys
import pytest

from xhs_context.common import XhsError, dumps
from xhs_context.core import search, read, status
from xhs_context.jobs import create_job
from conftest import note, create


def test_chinese_short_names_aliases_and_accents(store, monkeypatch):
    store.publish(note(1, "马德里小吃推荐：Café Aurora 的土豆饼。"))
    store.publish(note(2, "容器部署、基础设施和可观测性，适合入门。"))
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("Query attempted network access"))
    for query in ["马德里 小吃", "小吃", "Cafe Aurora", "土豆饼"]:
        assert search(store, {"queries": [query]})["data"]["items"][0]["note_id"] == f"{1:024x}"
    assert search(store, {"queries": ["infra 入门"]})["data"]["items"][0]["note_id"] == f"{2:024x}"
    assert search(store, {"queries": ["并不存在的菜肴"]})["data"]["items"] == []
    assert "rapidocr" not in sys.modules


def test_identifier_is_not_reduced_to_generic_letter(store):
    store.publish(note(1, "C 语言入门"))
    store.publish(note(2, "C++ 入门"))
    result = search(store, {"queries": ["C++"]})
    assert [n["note_id"] for n in result["data"]["items"]] == [f"{2:024x}"]


def test_stable_versions_ignore_access_urls_and_raw_page_noise(store):
    first = note()
    first["raw_object"] = {"sha256": "first"}
    revision = store.publish(first)
    generation = status(store)["data"]["generation"]
    changed_access = dict(first, source_url=first["source_url"] + "?xsec_token=changed",
                          raw_object={"sha256": "different-advertisement"})
    assert store.publish(changed_access) == revision
    assert status(store)["data"]["generation"] == generation
    newer = dict(first, body="修改后的资料")
    assert store.publish(newer) != revision
    result = read(store, {"items": [{"note_id": first["note_id"], "revision_id": revision}], "mode": "full"})
    assert first["body"] in "".join(b["text"] for i in result["data"]["items"] for b in i["blocks"])


def test_search_page_and_stale_generation(store):
    for i in range(4):
        store.publish(note(i + 1))
    initial = search(store, {"queries": ["小吃"], "limit": 1})
    cursor = initial["data"]["next_cursor"]
    second = search(store, {"cursor": cursor})
    assert initial["data"]["items"][0]["note_id"] != second["data"]["items"][0]["note_id"]
    create(store, "new-collection")
    with pytest.raises(XhsError, match="index changed") as caught:
        search(store, {"cursor": cursor})
    assert caught.value.code == "CURSOR_STALE"


def test_read_pagination_preserves_evidence_offsets_and_revision(store):
    body = "\n".join("不可忽略前置条件。" + "测量内容。" * 80 for _ in range(5))
    n = note(body=body)
    revision = store.publish(n)
    request = {"items": [{"note_id": n["note_id"], "revision_id": revision}], "mode": "full", "max_chars": 2000}
    collected, iterations = [], 0
    while True:
        result = read(store, request)
        assert len(dumps(result)) <= 2000
        collected.extend(b for item in result["data"]["items"] for b in item["blocks"])
        iterations += 1
        assert iterations < 30
        if not result["data"]["next_cursor"]:
            break
        store.publish(note(99, body="另一篇持续更新的无关资料" + str(iterations)))
        request = {"cursor": result["data"]["next_cursor"], "max_chars": 2000}
    assert iterations > 1
    for block in collected:
        assert body[block["location"]["start"]:block["location"]["end"]] == block["text"]
    assert len({block["block_id"] for block in collected}) == len(collected)


def test_output_budget_and_missing_revisions(store):
    n = note(body="测试。" * 400)
    revision = store.publish(n)
    with pytest.raises(XhsError) as error:
        read(store, {"items": [{"note_id": n["note_id"], "revision_id": revision}], "mode": "full", "max_chars": 512})
    assert error.value.code == "OUTPUT_BUDGET_TOO_SMALL"
    result = read(store, {"items": [{"note_id": n["note_id"], "revision_id": "missing"}]})
    assert result["partial"]
    assert result["data"]["errors"][0]["code"] == "REVISION_NOT_FOUND"


def test_deleted_notes_are_excluded_from_search_and_read(store):
    n = note()
    revision = store.publish(n)
    store.set_deleted(n["note_id"], True)
    assert not search(store, {"queries": ["小吃"]})["data"]["items"]
    assert read(store, {"items": [{"note_id": n["note_id"], "revision_id": revision}]})["partial"]
    store.set_deleted(n["note_id"], False)
    assert search(store, {"queries": ["小吃"]})["data"]["items"]


@pytest.mark.parametrize("payload", [{}, {"queries": []}, {"queries": ["x"], "limit": True},
                                     {"queries": ["x"], "surprise": 1}, {"cursor": "%%%"}])
def test_invalid_search_inputs_are_structured_errors(store, payload):
    with pytest.raises(XhsError):
        search(store, payload)

