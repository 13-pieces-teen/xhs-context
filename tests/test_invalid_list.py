import pytest

from conftest import note
from xhs_context.access import report_restriction
from xhs_context.cli import dispatch, parser
from xhs_context.common import XhsError
from xhs_context.core import status
from xhs_context.invalid_sources import mark_invalid
from xhs_context.sync_service import dispatch_sync


def test_invalid_list_is_read_only_paginated_and_available_during_restriction(store):
    for number in range(1, 4):
        saved = note(number)
        store.publish(saved)
        with store.write() as db:
            mark_invalid(db, saved['note_id'], 'SOURCE_UNAVAILABLE', saved['source_url'])
    report_restriction(store, {'error': 'ACCESS_RESTRICTED'})
    before = status(store)['data']
    assert before['invalid_notes'] == {'total': 3}
    first = dispatch_sync(store, '/v1/invalid-notes?limit=2&offset=0')['data']
    second = dispatch_sync(store, '/v1/invalid-notes?limit=2&offset=2')['data']
    assert first['total'] == second['total'] == 3
    assert first['has_more'] and not second['has_more']
    assert len(first['items']) == 2 and len(second['items']) == 1
    assert len({item['note_id'] for item in first['items'] + second['items']}) == 3
    assert all(item['title'] and item['error'] == 'SOURCE_UNAVAILABLE' for item in first['items'])
    command = parser().parse_args(['invalid', 'list', '--library', str(store.root), '--limit', '2', '--offset', '2'])
    assert dispatch(command)['data'] == second
    after = status(store)['data']
    assert after['generation'] == before['generation']
    assert after['access_restriction'] == before['access_restriction']
    assert after['coverage']['body_available'] == 3


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'offset=-1', 'limit=abc', 'offset=',
                                 'limit=1&limit=2', 'reset=true'])
def test_invalid_list_rejects_bad_pagination(store, query):
    with pytest.raises(XhsError) as error:
        dispatch_sync(store, '/v1/invalid-notes?' + query)
    assert error.value.code == 'INVALID_ARGUMENT'


def test_invalid_list_has_no_write_or_clear_endpoint(store):
    with pytest.raises(XhsError) as error:
        dispatch_sync(store, '/v1/invalid-notes', {})
    assert error.value.code == 'NOT_FOUND'
