import json
import pytest

from conftest import note
from xhs_context.cli import main
from xhs_context.common import XhsError, dumps, encode_cursor
from xhs_context.core import read, search


def ids(result):
    return [item["note_id"] for item in result["data"]["items"]]


def test_body_filter_keeps_unread_title_matches_visible_as_coverage(store):
    metadata = note(1, body="", title="马德里美食指南")
    metadata.update(body_state="missing", content_state="metadata_only", gaps=["BODY_MISSING"])
    store.publish(metadata)
    store.publish(note(2, body="马德里的可颂店和地址", title="早餐体验"))
    result = search(store, {"queries": ["马德里"]})
    assert ids(result) == [f"{2:024x}"]
    assert result["data"]["unavailable_body_matches"] == 1
    assert result["data"]["coverage"]["notes"] == 2
    assert set(ids(search(store, {"queries": ["马德里"], "content": "all"}))) == {f"{1:024x}", f"{2:024x}"}


def test_content_filter_runs_before_pagination_and_preserves_explicit_leads(store):
    saved = [note(1, "", title="MCP"),
             note(2, "\ufeff#MCP\ufeff\u00a0 #工具", title="MCP"),
             note(3, "MCP 工具接入的实践笔记。", title="实际经验"),
             note(4, "先核对传入参数，再连接工具。", title="MCP 排查")]
    saved[0].update(body_state="missing", content_state="metadata_only", gaps=["BODY_MISSING"])
    revisions = [store.publish(n) for n in saved]
    first = search(store, {"queries": ["MCP"], "limit": 1})
    second = search(store, {"cursor": first["data"]["next_cursor"]})
    assert set(ids(first) + ids(second)) == {saved[2]["note_id"], saved[3]["note_id"]}
    assert not second["data"]["next_cursor"]
    for page in [first, second]:
        assert page["data"]["matched_count"] == 2
        assert page["data"]["unavailable_body_matches"] == 1
        assert page["data"]["noncontent_body_matches"] == 1
        assert page["data"]["coverage"]["notes"] == 4
        assert page["data"]["coverage"]["body_available"] == 3
    all_results = search(store, {"queries": ["MCP"], "content": "all"})
    indexed = {item["note_id"]: item for item in all_results["data"]["items"]}
    assert set(indexed) == {n["note_id"] for n in saved}
    assert indexed[saved[0]["note_id"]]["body_filter_reason"] == "unavailable"
    assert indexed[saved[1]["note_id"]]["body_filter_reason"] == "no_text"
    assert indexed[saved[1]["note_id"]]["snippets"] == []
    assert "body_filter_reason" not in indexed[saved[2]["note_id"]]
    # Search filtering must not remove the archived source or fixed-version reads.
    original = read(store, {"items": [{"note_id": saved[1]["note_id"], "revision_id": revisions[1]}], "mode": "full"})
    assert "".join(b["text"] for n in original["data"]["items"] for b in n["blocks"]) == saved[1]["body"]


@pytest.mark.parametrize("body", ["#MCP #工具", "\ufeff#MCP\ufeff\u200b #工具\u00a0",
                                  "＃MCP ＃工具", "#MCP 🎉 — …", "\u200b\ufeff", "✨！！"])
def test_default_search_excludes_tags_and_decoration_without_text(store, body):
    store.publish(note(body=body, title="MCP 入门"))
    result = search(store, {"queries": ["MCP"]})
    assert result["data"]["items"] == []
    assert result["data"]["matched_count"] == 0
    assert result["data"]["noncontent_body_matches"] == 1
    assert result["data"]["unavailable_body_matches"] == 0


@pytest.mark.parametrize("body", ["C++", "C#", "# Markdown 标题", "见图", "推荐菜：烤鱼。详细做法见图。\n#美食"])
def test_content_filter_keeps_short_text_code_and_image_dependent_text(store, body):
    saved = note(body=body, title="学习资料")
    store.publish(saved)
    result = search(store, {"queries": ["学习资料"]})
    assert ids(result) == [saved["note_id"]]
    assert result["data"]["noncontent_body_matches"] == 0
    for snippet in result["data"]["items"][0]["snippets"]:
        location = snippet["location"]
        assert body[location["start"]:location["end"]] == snippet["text"]


def test_content_filter_preserves_ocr_leads_only_in_all_mode(store):
    saved = note(body="#资料", title="收藏图片")
    saved["images"] = [{"asset_id": "img-1", "ocr_lines": [{"text": "图中的银河土豆饼"}]}]
    store.publish(saved)
    default = search(store, {"queries": ["银河土豆饼"]})
    assert default["data"]["items"] == []
    assert default["data"]["noncontent_body_matches"] == 1
    all_results = search(store, {"queries": ["银河土豆饼"], "content": "all"})
    assert ids(all_results) == [saved["note_id"]]
    assert all_results["data"]["items"][0]["snippets"][0]["kind"] == "ocr"


def test_previous_body_filter_cursor_is_stale(store):
    cursor = encode_cursor({"v": 1, "kind": "search", "search_version": "lexical-body-v2",
                            "request": {"queries": ["MCP"]}, "offset": 1, "generation": 0})
    with pytest.raises(XhsError) as caught:
        search(store, {"cursor": cursor})
    assert caught.value.code == "CURSOR_STALE"


def test_required_city_survives_batched_queries_and_aliases(store):
    store.publish(note(1, "马德里。早餐推荐可颂。", title="可颂"))
    store.publish(note(2, "Madrid tapas bar", title="tapas"))
    store.publish(note(3, "Barcelona tapas bar", title="tapas"))
    store.publish(note(4, "Barcelona tapas bar\n#Madrid", title="tapas"))
    query = {"queries": ["可颂", "tapas"], "required_terms": ["Madrid"], "limit": 1}
    first = search(store, query)
    second = search(store, {"cursor": first["data"]["next_cursor"]})
    assert set(ids(first) + ids(second)) == {f"{1:024x}", f"{2:024x}"}
    assert not second["data"]["next_cursor"]
    with pytest.raises(XhsError):
        search(store, {"cursor": first["data"]["next_cursor"], "content": "all"})


def test_related_topic_is_not_silently_treated_as_an_alias(store):
    store.publish(note(1, "容器工具清单。\n#入门", title="软件列表"))
    assert not ids(search(store, {"queries": ["infra 入门"]}))
    # The Agent may deliberately request the related topic in a later round.
    assert ids(search(store, {"queries": ["容器"]})) == [f"{1:024x}"]


def test_hashtag_hits_cannot_outrank_a_substantive_explanation(store):
    store.publish(note(1, "工具名字列表。\n#infra #入门 " * 8, title="入门"))
    store.publish(note(2, "基础设施入门：先理解服务进程、资源隔离和请求链路，再做练习。", title="学习路线"))
    result = search(store, {"queries": ["infra 入门"]})
    assert ids(result)[0] == f"{2:024x}"
    assert "tags" in result["data"]["items"][1]["matched_fields"]
    assert not any(s["text"].lstrip().startswith("#") for s in result["data"]["items"][1]["snippets"])


def test_equivalent_queries_do_not_inflate_rank(store):
    store.publish(note(1, "Madrid 可颂", title="早餐"))
    store.publish(note(2, "马德里早餐", title="马德里"))
    first = search(store, {"queries": ["Madrid"]})
    repeated = search(store, {"queries": ["Madrid", "马德里", "Madrid"]})
    assert first["data"]["items"] == repeated["data"]["items"]
    assert first["data"]["effective_queries"] == repeated["data"]["effective_queries"]


@pytest.mark.parametrize("name,query", [("Café Aurora", "Cafe Aurora"), ("Ｃａｆé Aurora", "Cafe Aurora"), ("C++", "C++")])
def test_snippet_finds_late_match_and_preserves_original_offsets(store, name, query):
    body = "背景介绍。" * 120 + name + " 的实用笔记。" + "后续内容。" * 60
    saved = note(1, body=body, title="长文")
    store.publish(saved)
    result = search(store, {"queries": [query]})["data"]["items"][0]
    snippet = result["snippets"][0]
    assert name in snippet["text"] and snippet["matched"] and snippet["excerpt_only"]
    assert snippet["location"]["start"] > 0
    assert body[snippet["location"]["start"]:snippet["location"]["end"]] == snippet["text"]
    evidence = read(store, {"items": [{"note_id": result["note_id"], "revision_id": result["revision_id"],
                                      "block_ids": [snippet["block_id"]]}]})
    assert any(snippet["text"] in b["text"] for n in evidence["data"]["items"] for b in n["blocks"])


def test_grouped_read_paginates_without_losing_blocks_and_retains_errors(store):
    originals, requests = {}, []
    for number in (1, 2):
        saved = note(number, body="\n".join(("原文段落" + str(i) + "。") * 25 for i in range(6)))
        revision = store.publish(saved)
        originals[saved["note_id"]] = saved["body"]
        requests.append({"note_id": saved["note_id"], "revision_id": revision})
    requests.append({"note_id": f"{3:024x}", "revision_id": "missing"})
    request = {"items": requests, "mode": "full", "max_chars": 2200}
    collected, flags, pages = {}, [], 0
    while True:
        result = read(store, request)
        assert len(dumps(result)) <= 2200 and result["partial"]
        assert result["data"]["errors"][0]["code"] == "REVISION_NOT_FOUND"
        for item in result["data"]["items"]:
            assert "block" not in item and item["blocks"]
            flags.append((item["continued"], item["has_more"]))
            for block in item["blocks"]:
                key = (item["note_id"], item["revision_id"], block["block_id"])
                assert key not in collected
                collected[key] = block["text"]
                assert originals[item["note_id"]][block["location"]["start"]:block["location"]["end"]] == block["text"]
        pages += 1
        assert pages < 30
        if not result["data"]["next_cursor"]:
            break
        request = {"cursor": result["data"]["next_cursor"], "max_chars": 2200}
    assert len(collected) == 12 and pages > 1 and any(a for a, b in flags) and any(b for a, b in flags)
    for note_id, body in originals.items():
        assert "".join(text for key, text in collected.items() if key[0] == note_id) == body


def test_short_paragraphs_share_metadata_and_keep_block_ids(store):
    saved = note(body="\n".join("这是一个独立段落。" for _ in range(30)))
    revision = store.publish(saved)
    result = read(store, {"items": [{"note_id": saved["note_id"], "revision_id": revision}], "mode": "full"})
    assert result["schema_version"] == 2
    assert len(result["data"]["items"]) == 1
    assert len(result["data"]["items"][0]["blocks"]) == 30
    assert not result["data"]["next_cursor"]
    assert dumps(result).count('"revision_id"') == 1


def test_cli_supports_query_batches_and_explicit_required_terms(store, capsys):
    store.publish(note(1, "马德里可颂", title="店铺"))
    store.publish(note(2, "巴塞罗那 tapas", title="店铺"))
    assert main(["--library", str(store.root), "search", "--query", "可颂", "--query", "tapas", "--require", "Madrid"]) == 0
    assert ids(json.loads(capsys.readouterr().out)) == [f"{1:024x}"]


@pytest.mark.parametrize("extra", [{"content": []}, {"content": "unknown"}, {"required_terms": "Madrid"},
                                    {"required_terms": [""]}, {"required_terms": ["!!!"]}])
def test_new_search_filters_reject_invalid_input(store, extra):
    with pytest.raises(XhsError) as caught:
        search(store, {"queries": ["x"], **extra})
    assert caught.value.code == "INVALID_ARGUMENT"


def test_old_strategy_cursors_are_rejected_explicitly(store):
    for kind, method in [("search", search), ("read", read)]:
        cursor = encode_cursor({"v": 1, "kind": kind, "request": {}, "offset": 0})
        with pytest.raises(XhsError) as caught:
            method(store, {"cursor": cursor})
        assert caught.value.code == "CURSOR_STALE"
