import unicodedata

import pytest

from conftest import note
from xhs_context.core import search
from xhs_context.retrieval import fold, terms_for
from xhs_context.text import normalize


def ids(result):
    return [item["note_id"] for item in result["data"]["items"]]


def test_partial_matches_rank_after_every_full_match(store):
    store.publish(note(1, "马德里小吃：土豆饼和炸鱿鱼。", title="马德里"))
    store.publish(note(2, "马德里早餐推荐可颂店。", title="马德里早餐"))
    store.publish(note(3, "北京小吃合集。", title="北京"))
    result = search(store, {"queries": ["马德里 小吃"]})
    data = result["data"]
    assert ids(result)[0] == f"{1:024x}"
    assert set(ids(result)[1:]) == {f"{2:024x}", f"{3:024x}"}
    assert data["matched_count"] == 1 and data["partial_term_matches"] == 2
    assert "partial_match" not in data["items"][0]
    for item in data["items"][1:]:
        missing = item["partial_match"][0]["missing_terms"]
        assert missing == (["小吃"] if item["note_id"] == f"{2:024x}" else ["马德里"])
        assert item["snippets"]
    assert not result["partial"] and result["warnings"] == []


def test_partial_tier_respects_required_terms_and_body_filter(store):
    store.publish(note(1, "马德里早餐推荐可颂店。", title="早餐"))
    store.publish(note(2, "北京小吃合集。", title="北京"))
    missing_body = note(3, "", title="马德里 攻略")
    missing_body.update(body_state="missing", content_state="metadata_only", gaps=["BODY_MISSING"])
    store.publish(missing_body)
    result = search(store, {"queries": ["马德里 小吃"], "required_terms": ["马德里"]})
    assert ids(result) == [f"{1:024x}"]
    # Body-filter counters keep describing full matches only.
    assert result["data"]["unavailable_body_matches"] == 0
    assert f"{3:024x}" in ids(search(store, {"queries": ["马德里 小吃"], "content": "all"}))


def test_partial_tier_ignores_tags_single_characters_and_phrase_mode(store):
    store.publish(note(1, "容器工具清单。\n#入门", title="软件列表"))
    store.publish(note(2, "今天吃了鱼。", title="日记"))
    store.publish(note(3, "Kubernetes 调度原理。", title="调度"))
    assert ids(search(store, {"queries": ["infra 入门"]})) == []
    assert ids(search(store, {"queries": ["鱼 做法"]})) == []
    assert ids(search(store, {"queries": ["k8s 网络 调度"]})) == [f"{3:024x}"]
    phrase = search(store, {"queries": ["k8s 网络"], "match": "phrase"})
    assert phrase["data"]["items"] == [] and phrase["data"]["partial_term_matches"] == 0


def test_cursor_pages_continue_from_full_into_partial_tier(store):
    for number in range(1, 4):
        store.publish(note(number, f"磁盘延迟排查 {number}。", title="完整"))
    for number in range(4, 7):
        store.publish(note(number, f"磁盘容量规划 {number}。", title="部分"))
    seen, request = [], {"queries": ["磁盘 延迟"], "limit": 2}
    while True:
        page = search(store, request)
        seen.extend(item["note_id"] for item in page["data"]["items"])
        if not page["data"]["next_cursor"]:
            break
        request = {"cursor": page["data"]["next_cursor"]}
    assert seen[:3] == [f"{n:024x}" for n in range(1, 4)]
    assert sorted(seen[3:]) == [f"{n:024x}" for n in range(4, 7)] and len(seen) == 6


@pytest.mark.parametrize("query,body", [("learn k8s.", "k8s 入门笔记"), ("Node.js.", "Node.js 服务端"),
                                        ("C++.", "C++ 内存模型"), ("（C#）", "C# 委托"), ("“马德里”", "马德里早餐")])
def test_surrounding_punctuation_does_not_hide_identifiers(store, query, body):
    store.publish(note(1, body, title="资料"))
    assert ids(search(store, {"queries": [query]})) == [f"{1:024x}"]


def test_trailing_punctuation_is_removed_from_terms_only():
    assert terms_for("learn k8s.") == ["learn", "k8s"]
    assert terms_for("node.js") == ["node.js"]
    assert terms_for("c++") == ["c++"]


@pytest.mark.parametrize("value", ["Café Ｃａｆé", "が パ ㋐", "〪〭漢字", "Ångström ñ ü", "ﾊﾟﾝ", "e\u0301\u20dd", "普通中文 ASCII 123"])
def test_fast_fold_matches_reference(value):
    reference = "".join(c for c in unicodedata.normalize("NFKD", normalize(value)) if not unicodedata.combining(c))
    assert fold(value) == reference
