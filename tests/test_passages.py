import pytest
from conftest import note
from xhs_context.core import search, read
from xhs_context.common import XhsError, dumps, encode_cursor


def test_explanation_breaks_tie_with_repeated_heading_without_losing_question(store):
    n=note(body='权限隔离\n权限隔离怎么做？\n权限隔离由操作系统检查路径与用户身份。\n#权限隔离', title='权限隔离')
    rev=store.publish(n)
    result=search(store,{'queries':['权限隔离']})['data']['items'][0]
    assert result['snippets'][0]['block_id']=='b00003'
    snippet=result['snippets'][0]; loc=snippet['location']
    assert n['body'][loc['start']:loc['end']]==snippet['text']
    response=read(store,{'items':[{'note_id':n['note_id'],'revision_id':rev,'block_ids':[snippet['block_id']]}],'context':0})
    assert [b['block_id'] for b in response['data']['items'][0]['blocks']]==['b00003']
    # Questions are valid content, and a question-plus-answer paragraph stays eligible.
    assert search(store,{'queries':['怎么做']})['data']['matched_count']==1


def test_cross_paragraph_candidates_are_not_discarded(store):
    store.publish(note(1,body='磁盘故障：检查硬盘接口。\n如何分析延迟？',title='记录'))
    store.publish(note(2,body='磁盘故障可能增加请求延迟。',title='记录'))
    response=search(store,{'queries':['磁盘 延迟']})['data']
    assert response['matched_count']==2
    assert {i['note_id'] for i in response['items']}=={f'{1:024x}',f'{2:024x}'}


def test_repeated_question_lists_remain_searchable_without_score_inflation(store):
    from xhs_context.retrieval import document_fields, rank_document
    from xhs_context.text import query_groups
    n=note(body='为什么磁盘延迟高？\n',title='排查问题')
    once=rank_document(document_fields(n,n),query_groups(['磁盘 延迟']),{})[0]
    many=dict(n,body=n['body']*100)
    assert rank_document(document_fields(many,many),query_groups(['磁盘 延迟']),{})[0]==once
    store.publish(n)
    assert search(store,{'queries':['磁盘 延迟']})['data']['items'][0]['snippets']


def test_numbered_question_is_not_mistaken_for_sentence_end(store):
    store.publish(note(body='1. 缓存故障是什么？\n缓存故障发生时先校验失效策略。',title='排查'))
    assert search(store,{'queries':['缓存 故障']})['data']['items'][0]['snippets'][0]['block_id']=='b00002'


def test_unpunctuated_explanation_stays_available(store):
    store.publish(note(body='磁盘读写慢先检查队列深度',title='笔记'))
    assert search(store,{'queries':['磁盘']})['data']['items'][0]['snippets'][0]['text']=='磁盘读写慢先检查队列深度'


def test_question_and_short_answer_are_not_filtered(store):
    store.publish(note(body='连接失败？重启客户端。\n连接失败的背景介绍'+'与问题无关的文字'*100,title='故障'))
    assert search(store,{'queries':['连接 失败']})['data']['items'][0]['snippets'][0]['block_id']=='b00001'


def test_context_can_expand_a_heading_without_inventing_text(store):
    n=note(body='读书步骤\n一、选书\n二、通读\n三、做笔记\n四、复盘',title='读书步骤')
    rev=store.publish(n); item={'note_id':n['note_id'],'revision_id':rev,'block_ids':['b00001']}
    small=read(store,{'items':[item],'context':0})['data']['items'][0]['blocks']
    assert len(small)==1
    expanded=read(store,{'items':[item],'context':4})['data']['items'][0]['blocks']
    assert ''.join(b['text'] for b in expanded)==n['body']


def test_context_survives_pagination_without_duplicate_blocks(store):
    n=note(body='\n'.join('原文'+str(i)+'文本'*150 for i in range(10)))
    rev=store.publish(n)
    request={'items':[{'note_id':n['note_id'],'revision_id':rev,'block_ids':['b00005']}], 'context':3,'max_chars':1500}
    found=[]
    while True:
        r=read(store,request)
        assert len(dumps(r))<=1500
        found.extend(b['block_id'] for n in r['data']['items'] for b in n['blocks'])
        if not r['data']['next_cursor']: break
        request={'cursor':r['data']['next_cursor'],'max_chars':1500}
    assert found==[f'b{i:05d}' for i in range(2,9)]


@pytest.mark.parametrize('context',[-1,5,True,'2',1.5])
def test_invalid_context_rejected(store, context):
    with pytest.raises(XhsError):
        read(store,{'items':[{'note_id':'x','revision_id':'r'}],'context':context})


def test_context_rejects_wrong_mode_and_old_cursor(store):
    with pytest.raises(XhsError):
        read(store,{'items':[{'note_id':'x','revision_id':'r'}],'mode':'asset','context':1})
    cursor=encode_cursor({'v':1,'kind':'read','read_version':'grouped-evidence-v3','request':{},'offset':0})
    with pytest.raises(XhsError) as error: read(store,{'cursor':cursor})
    assert error.value.code=='CURSOR_STALE'
