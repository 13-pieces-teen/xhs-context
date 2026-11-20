import json
import pytest

from conftest import note
from xhs_context.cli import main
from xhs_context.common import XhsError
from xhs_context.core import search


def test_phrase_rejects_scattered_words_across_lines_and_fields(store):
    store.publish(note(1, '数据结构是什么？\n多智能体如何并行？', title='面试题'))
    store.publish(note(2, '数据并行复制模型，通过同步梯度训练。', title='训练笔记'))
    store.publish(note(3, '并行处理的经验', title='数据'))
    store.publish(note(4, '仅有标签线索\n#数据并行', title='收藏'))
    broad = search(store, {'queries': ['数据并行']})
    assert {i['note_id'] for i in broad['data']['items']} >= {note(1)['note_id'], note(2)['note_id']}
    exact = search(store, {'queries': ['数据并行'], 'match': 'phrase'})
    assert [i['note_id'] for i in exact['data']['items']] == [note(2)['note_id']]
    assert exact['data']['items'][0]['matched_fields'] == ['body']


def test_phrase_unicode_spaces_word_boundaries_and_aliases(store):
    store.publish(note(1, 'ＫＶ\tCache uses memory. Madrid tapas', title='部署'))
    store.publish(note(2, 'KV\nCache must not span paragraphs; KV cachex is another name.', title='其他'))
    found = search(store, {'queries': ['KV Cache'], 'match': 'phrase'})
    assert [i['note_id'] for i in found['data']['items']] == [note(1)['note_id']]
    aliases = search(store, {'queries': ['马德里 tapas'], 'match': 'phrase'})
    assert [i['note_id'] for i in aliases['data']['items']] == [note(1)['note_id']]


def test_phrase_mode_persists_across_cursor_and_checks_input(store):
    for i in range(3):
        store.publish(note(i, '数据并行训练的说明'))
    first = search(store, {'queries': ['数据并行'], 'match': 'phrase', 'limit': 1})
    second = search(store, {'cursor': first['data']['next_cursor']})
    assert second['data']['match'] == 'phrase'
    assert first['data']['items'][0]['note_id'] != second['data']['items'][0]['note_id']
    with pytest.raises(XhsError):
        search(store, {'cursor': first['data']['next_cursor'], 'match': 'terms'})
    for bad in ['semantic', [], None]:
        with pytest.raises(XhsError):
            search(store, {'queries': ['数据并行'], 'match': bad})


def test_phrase_keeps_title_only_as_explicit_lead_and_does_not_change_source(store):
    item = note(body='', title='数据并行')
    item['body_state'] = 'missing'
    revision = store.publish(item)
    assert not search(store, {'queries': ['数据并行'], 'match': 'phrase'})['data']['items']
    found = search(store, {'queries': ['数据并行'], 'match': 'phrase', 'content': 'all'})['data']['items'][0]
    assert found['revision_id'] == revision and found['body_filter_reason'] == 'unavailable'


def test_cli_phrase_shortcut(store, capsys):
    store.publish(note(body='张量并行按张量维度切分计算。'))
    assert main(['--library', str(store.root), 'search', '--query', '张量并行', '--match', 'phrase']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['data']['match'] == 'phrase' and result['data']['matched_count'] == 1
