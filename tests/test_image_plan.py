import json

import pytest

from conftest import card, create, note
from xhs_context.access import restrict_access
from xhs_context.cli import main
from xhs_context.common import XhsError, dumps, encode_cursor
from xhs_context.images import image_plan
from xhs_context.jobs import receive_batch
from xhs_context.network import AnonymousFetcher
from xhs_context.processing import finish_body


def publish_body(store, number=1, body='正文已保存。'):
    saved = finish_body(dict(note(number, body=body), expected_images=None, images=[]))
    return saved, store.publish(saved)


def database_dump(store):
    with store.read() as db:
        return '\n'.join(db.iterdump())


def test_image_plan_works_under_restriction_and_cannot_write_or_fetch(store, monkeypatch):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    saved, revision = publish_body(store, body='每道题的答题思路和要点整理在后面的图里，可以先自己答一遍再对照看。')
    with store.write() as db:
        restrict_access(db)
    before = database_dump(store)
    monkeypatch.setattr(AnonymousFetcher, '__init__', lambda *a, **k: pytest.fail('Plan must not create a fetcher'))
    monkeypatch.setattr(store, 'initialize', lambda: pytest.fail('Plan must not initialize storage'))
    monkeypatch.setattr(store, 'write', lambda: pytest.fail('Plan must not mutate storage'))
    result = image_plan(store, {'collection_id': 'collection'})
    assert database_dump(store) == before
    data = result['data']
    assert not result['partial'] and data['acquisition_blocked']
    assert data['access_restriction']['error'] == 'ACCESS_RESTRICTED'
    assert data['fetch_candidates'] == data['explicit_reference_candidates'] == 1
    item = data['items'][0]
    assert item['revision_id'] == revision and item['priority'] == 'explicit_body_reference'
    assert item['media']['state'] == 'deferred' and item['media']['expected_images'] is None
    hint = item['body_image_hints'][0]
    assert hint['signal'] == 'explicit_content_reference'
    assert saved['body'][hint['location']['start']:hint['location']['end']] == hint['text']
    with store.read() as db:
        assert db.execute('SELECT count(*) FROM image_jobs').fetchone()[0] == 0


@pytest.mark.parametrize('body,signal', [
    ('见图', 'image_mention'),
    ('这是图像处理算法的介绍。', None),
    ('教程在图书馆借到。', None),
    ('答案在图论书里。', None),
    ('推荐菜：烤鱼。详细做法见图。', 'explicit_content_reference'),
    ('完整步骤见下图。', 'explicit_content_reference'),
    ('详细过程在图表中。', 'explicit_content_reference'),
    ('操作步骤见图1。', 'explicit_content_reference'),
    ('制作步骤：如图所示！', 'explicit_content_reference'),
    ('步骤在正文里，图片只作展示。', 'image_mention'),
    ('答案请看正文，图片只是封面。', 'image_mention'),
    ('答案在图里，正文不需要重复。', 'explicit_content_reference'),
    ('步骤在正文里;图片只作展示。', 'image_mention'),
    ('图里列出了安装步骤。', 'explicit_content_reference'),
    ('答案不在图片中，完整解释在正文。', 'image_mention'),
    ('没有答案在图片中。', 'image_mention'),
    ('答案在图片中吗？', 'image_mention'),
    ('#图片 #配图', None),
    ('短正文', None),
])
def test_body_hints_are_literal_references_not_quality_labels(store, body, signal):
    saved, _ = publish_body(store, body=body)
    result = image_plan(store, {'note_ids': [saved['note_id']]})['data']['items'][0]
    assert result['body_state'] == 'available'
    assert 'quality' not in result and 'answerable' not in result
    hints = result['body_image_hints']
    assert (hints[0]['signal'] if hints else None) == signal
    for hint in hints:
        assert saved['body'][hint['location']['start']:hint['location']['end']] == hint['text']


def test_long_body_hints_preserve_fixed_block_and_original_offsets(store):
    body = '背景文字。' * 100 + '详细步骤在图中。' + '其他资料。' * 90
    saved, revision = publish_body(store, body=body)
    item = image_plan(store, {'note_ids': [saved['note_id']]})['data']['items'][0]
    hint = item['body_image_hints'][0]
    assert hint['excerpt_only'] and len(hint['text']) <= 240
    assert '详细步骤在图中' in hint['text']
    with store.read() as db:
        block = db.execute('SELECT text FROM evidence_blocks WHERE revision_id=? AND block_id=?',
                           (revision, hint['block_id'])).fetchone()[0]
        assert hint['text'] in block
    assert body[hint['location']['start']:hint['location']['end']] == hint['text']


def test_image_plan_distinguishes_saved_missing_unknown_and_known_zero(store):
    obj = store.put_object(b'fixture bytes, existence check only', '.png')
    fixtures = [
        dict(note(1), images=[dict(obj, asset_id='img-1', state='downloaded')], expected_images=1),
        dict(note(2), images=[{'asset_id':'img-1','state':'failed'}], expected_images=2),
        dict(note(3), images=[], expected_images=None),
        note(4),
        dict(note(5), images=[dict(obj, asset_id='img-1', object_path='objects/missing.png')], expected_images=1),
        dict(note(6), images=[dict(obj, asset_id='img-1')], expected_images=None),
        dict(note(7), images=[dict(obj, asset_id='img-1')], expected_images=1, image_manifest_complete=False),
        dict(note(8), images=[dict(obj, asset_id='img-1', object_path='../outside.png')], expected_images=1),
    ]
    for n in fixtures:
        store.publish(n)
    result = image_plan(store, {'note_ids': [n['note_id'] for n in fixtures], 'max_chars':20000})['data']
    rows = {n['note_id']:n for n in result['items']}
    a,b,c,d,e,f,g,h = [rows[n['note_id']] for n in fixtures]
    assert a['media']['state'] == 'available' and a['media']['saved_images'] == 1 and not a['fetch_candidate']
    assert b['media']['state'] == 'partial' and b['media']['missing_expected_images'] == 2
    assert c['media']['state'] == 'unknown' and c['media']['missing_expected_images'] is None
    assert d['media']['state'] == 'none_expected' and not d['fetch_candidate']
    assert e['media']['missing_local_files'] == 1 and e['fetch_candidate']
    assert f['media']['saved_images'] == 1 and f['media']['state'] == 'partial' and f['fetch_candidate']
    assert g['media']['state'] == 'partial' and not g['media']['manifest_complete']
    assert h['media']['invalid_object_paths'] == 1 and h['media']['saved_images'] == 0


def test_collection_plan_is_paginated_budgeted_and_keeps_item_errors(store):
    create(store, 'body', 4)
    receive_batch(store, 'body', {'seq':0, 'items':[card(i) for i in range(1,5)]})
    for i in range(1,5):
        publish_body(store, i, '答案在图中。' if i==4 else '普通正文。')
    request = {'collection_id':'collection','limit':2,'max_chars':1800}
    ids=[]
    while True:
        result=image_plan(store, request)
        assert len(dumps(result)) <= 1800
        ids.extend(i['note_id'] for i in result['data']['items'])
        cursor=result['data']['next_cursor']
        if not cursor: break
        request={'cursor':cursor,'max_chars':1800}
    assert ids[0] == note(4)['note_id']
    assert len(ids)==len(set(ids))==4
    result=image_plan(store, {'note_ids':[ids[0],'unknown']})
    assert result['partial'] and result['data']['items'][0]['note_id']==ids[0]
    assert result['data']['errors']==[{'note_id':'unknown','code':'NOTE_NOT_FOUND'}]


def test_thumbnail_file_is_not_a_complete_gallery(store):
    obj=store.put_object(b'thumbnail fixture', '.png')
    saved=dict(note(),images=[dict(obj,asset_id='img-1',state='downloaded',quality='thumbnail')],expected_images=1)
    store.publish(saved)
    item=image_plan(store,{'note_ids':[saved['note_id']]})['data']['items'][0]
    assert item['media']['saved_images']==item['media']['thumbnail_images']==1
    assert item['media']['state']=='partial' and item['fetch_candidate']


def test_plan_cursor_rejects_changed_library_and_changed_image_files(store):
    first,_=publish_body(store,1)
    obj=store.put_object(b'image', '.png')
    second=dict(note(2), images=[dict(obj,asset_id='img-1')],expected_images=1)
    store.publish(second)
    cursor=image_plan(store,{'note_ids':[first['note_id'],second['note_id']], 'limit':1})['data']['next_cursor']
    store.object_path(obj['object_path']).unlink()
    with pytest.raises(XhsError) as exc:
        image_plan(store,{'cursor':cursor})
    assert exc.value.code=='CURSOR_STALE'
    cursor=image_plan(store,{'note_ids':[first['note_id'],second['note_id']], 'limit':1})['data']['next_cursor']
    store.publish(dict(first,body='改变后的正文'))
    with pytest.raises(XhsError) as exc:
        image_plan(store,{'cursor':cursor})
    assert exc.value.code=='CURSOR_STALE'


@pytest.mark.parametrize('value', [{}, {'note_ids':[]}, {'note_ids':'abc'}, {'note_ids':['bad.id']},
    {'note_ids':[True]}, {'collection_id':'collection','note_ids':['abc']}, {'note_ids':['abc'],'limit':0},
    {'note_ids':['abc'],'limit':True}, {'collection_id':None}, {'note_ids':['abc'],'unknown':1}])
def test_plan_rejects_invalid_scope(store, value):
    with pytest.raises(XhsError) as exc:
        image_plan(store, value)
    assert exc.value.code=='INVALID_ARGUMENT'


def test_cli_plan_supports_shortcuts_input_cursor_and_missing_notes(store, tmp_path, capsys):
    first,_=publish_body(store,1)
    second,_=publish_body(store,2)
    root=['--library',str(store.root),'images','plan']
    assert main([*root,'--note',first['note_id'],'--note',second['note_id'],'--limit','1'])==0
    page=json.loads(capsys.readouterr().out)
    assert main([*root,'--cursor',page['data']['next_cursor']])==0
    assert len(json.loads(capsys.readouterr().out)['data']['items'])==1
    request=tmp_path/'plan.json'
    request.write_text(dumps({'note_ids':[first['note_id'],'unknown']}),encoding='utf-8')
    assert main([*root,'--input',str(request)])==3
    assert json.loads(capsys.readouterr().out)['data']['errors'][0]['code']=='NOTE_NOT_FOUND'
    assert main([*root,'--input',str(request),'--limit','1'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='INVALID_ARGUMENT'
    assert main([*root,'--cursor',page['data']['next_cursor'],'--note',first['note_id']])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='INVALID_ARGUMENT'


def test_plan_reports_unknown_collection_and_rejects_foreign_cursor(store):
    with pytest.raises(XhsError) as exc:
        image_plan(store,{'collection_id':'missing'})
    assert exc.value.code=='COLLECTION_NOT_FOUND'
    with pytest.raises(XhsError) as exc:
        image_plan(store,{'cursor':encode_cursor({'v':1,'kind':'search'})})
    assert exc.value.code=='INVALID_ARGUMENT'
