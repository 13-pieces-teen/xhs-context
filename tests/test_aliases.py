import json

import pytest

from conftest import note
from xhs_context.aliases import add_aliases, list_aliases, remove_alias
from xhs_context.cli import main
from xhs_context.common import XhsError
from xhs_context.core import search


def ids(result):
    return [item["note_id"] for item in result["data"]["items"]]


def test_custom_aliases_expand_search_and_expire_cursors(store):
    for number in range(1, 4):
        store.publish(note(number, f"东京拉面第 {number} 家。", title="拉面"))
    store.publish(note(4, "Tokyo ramen guide.", title="ramen"))
    assert ids(search(store, {"queries": ["Tokyo"]})) == [f"{4:024x}"]
    first = search(store, {"queries": ["东京"], "limit": 1})
    result = add_aliases(store, ["Tokyo", "东京"])
    assert result["changed"] and result["custom_groups"] == 1
    with pytest.raises(XhsError) as caught:
        search(store, {"cursor": first["data"]["next_cursor"]})
    assert caught.value.code == "CURSOR_STALE"
    expanded = search(store, {"queries": ["Tokyo"]})
    assert set(ids(expanded)) == {f"{n:024x}" for n in range(1, 5)}
    assert expanded["data"]["effective_queries"] == ["tokyo", "东京"]
    assert set(ids(search(store, {"queries": ["拉面", "ramen"], "required_terms": ["tokyo"]}))) == {f"{n:024x}" for n in range(1, 5)}
    remove_alias(store, "东京")
    assert ids(search(store, {"queries": ["Tokyo"]})) == [f"{4:024x}"]
    assert list_aliases(store)["custom_groups"] == 0


def test_aliases_extend_builtin_groups_without_removing_them(store):
    store.publish(note(1, "Madrid 早餐", title="早餐"))
    store.publish(note(2, "马德里 早餐", title="早餐"))
    store.publish(note(3, "Madri 早餐", title="早餐"))
    add_aliases(store, ["马德里", "Madri"])
    groups = {tuple(g["names"]): g for g in list_aliases(store)["groups"]}
    extended = groups[("madrid", "马德里", "madri")]
    assert extended["source"] == "extended" and extended["removable"] == ["madri"]
    assert len(ids(search(store, {"queries": ["madrid 早餐"]}))) == 3
    with pytest.raises(XhsError) as caught:
        remove_alias(store, "马德里")
    assert caught.value.code == "CONFLICT"
    remove_alias(store, "Madri")
    assert list_aliases(store)["custom_groups"] == 0


def test_alias_validation_and_merging(store):
    with pytest.raises(XhsError):
        add_aliases(store, ["only-one"])
    with pytest.raises(XhsError):
        add_aliases(store, ["!!!", "???"])
    with pytest.raises(XhsError) as caught:
        add_aliases(store, ["madrid", "k8s"])
    assert caught.value.code == "CONFLICT"
    add_aliases(store, ["NYC", "纽约"])
    add_aliases(store, ["New York", "纽约"])
    groups = [g["names"] for g in list_aliases(store)["groups"] if g["source"] == "custom"]
    assert groups == [["nyc", "纽约", "new york"]]
    assert not add_aliases(store, ["纽约", "NYC"])["changed"]


def test_removing_the_first_name_keeps_the_rest_of_a_custom_group(store):
    for number, text in enumerate(["Tokyo 拉面", "东京 拉面", "TYO 拉面"], 1):
        store.publish(note(number, text, title="拉面"))
    add_aliases(store, ["Tokyo", "东京", "TYO"])
    remove_alias(store, "Tokyo")
    result = search(store, {"queries": ["东京"]})
    assert result["data"]["effective_queries"] == ["东京", "tyo"]
    assert set(ids(result)) == {f"{2:024x}", f"{3:024x}"}
    add_aliases(store, ["TYO", "Edo"])
    assert [g["names"] for g in list_aliases(store)["groups"] if g["source"] == "custom"] == [["东京", "tyo", "edo"]]


def test_cli_aliases(store, capsys):
    library = str(store.root)
    assert main(["--library", library, "aliases", "add", "Tokyo", "东京"]) == 0
    capsys.readouterr()
    assert main(["--library", library, "aliases", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)["data"]
    assert ["tokyo", "东京"] in [g["names"] for g in listed["groups"]]
    assert main(["--library", library, "aliases", "remove", "Tokyo", "东京"]) == 2
