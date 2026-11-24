import pytest

from conftest import card, create, note
from test_images import Fetcher, capture, current, run, setup_note
from xhs_context.common import XhsError
from xhs_context.core import read
from xhs_context.image_jobs import create_image_job, image_report, set_image_state
from xhs_context.images import ImageWorker, image_plan
from xhs_context.invalid_sources import invalid_source, mark_invalid
from xhs_context.jobs import receive_batch
from xhs_context.storage import Store


def invalidate(store, saved, code='SOURCE_UNAVAILABLE'):
    with store.write() as db:
        return mark_invalid(db, saved['note_id'], code, saved['source_url'])


def never_fetch():
    return Fetcher(lambda *args: pytest.fail('A known unavailable note must not fetch a page or an asset'))


@pytest.mark.parametrize('scope', ['note_ids', 'collection_id', 'snapshot'])
def test_new_image_jobs_skip_persistent_invalid_notes_in_every_scope(store, scope):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    saved, revision = setup_note(store)
    invalidate(store, saved)
    reopened = Store(store.root)
    request = {'note_ids': [saved['note_id']]} if scope == 'note_ids' else \
        {'collection_id': 'collection'} if scope == 'collection_id' else capture(saved)
    job = create_image_job(reopened, request)
    assert job['items'] == {'skipped': 1} and job['state'] == 'finished'
    assert job['results'][0]['error'] == 'SOURCE_UNAVAILABLE'
    worker = ImageWorker(reopened, fetcher=never_fetch())
    assert not worker.once()
    assert set_image_state(reopened, job['job_id'], 'active')['state'] == 'finished'
    worker.recover()
    assert not worker.once()
    assert current(reopened, saved['note_id'])[0] == revision
    archived = read(reopened, {'items': [{'note_id': saved['note_id'], 'revision_id': revision}], 'mode': 'full'})
    assert archived['data']['items'][0]['note_id'] == saved['note_id']


@pytest.mark.parametrize('state,action', [('queued', 'once'), ('running', 'recover'), ('failed', 'resume'), ('partial', 'resume')])
def test_existing_image_work_is_suppressed_without_revival_or_network(store, state, action):
    saved, revision = setup_note(store)
    job = create_image_job(store, capture(saved))
    invalidate(store, saved, 'SOURCE_LINK_EXPIRED')
    # Simulate a stale worker or older installation leaving an item unfinished.
    with store.write() as db:
        db.execute("UPDATE image_items SET state=?,error='INTERRUPTED' WHERE job_id=?", (state, job['job_id']))
    worker = ImageWorker(store, fetcher=never_fetch())
    if action == 'recover':
        worker.recover()
    elif action == 'resume':
        set_image_state(store, job['job_id'], 'active')
    assert not worker.once()
    with store.read() as db:
        report = image_report(db, job['job_id'])
    assert report['items'] == {'skipped': 1}
    assert report['state'] == 'finished'
    assert report['results'][0]['error'] == 'SOURCE_LINK_EXPIRED'
    assert current(store, saved['note_id'])[0] == revision


def test_mixed_collection_continues_valid_notes_and_never_fetches_invalid_ones(store):
    create(store, 'body', 2)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1), card(2)]})
    unavailable, _ = setup_note(store)
    store.publish(note(2))  # A valid local manifest with zero expected images.
    invalidate(store, unavailable)
    job = create_image_job(store, {'collection_id': 'collection'})
    assert job['items'] == {'ready': 1, 'skipped': 1}
    worker = ImageWorker(store, fetcher=never_fetch())
    assert not worker.once()
    with store.read() as db:
        report = image_report(db, job['job_id'])
    assert report['state'] == 'finished'
    assert report['items'] == {'ready': 1, 'skipped': 1}


@pytest.mark.parametrize('code', ['SOURCE_UNAVAILABLE', 'SOURCE_LINK_EXPIRED'])
def test_note_page_error_persists_invalid_source_and_suppresses_other_jobs(store, code):
    create(store, 'body', 1)
    receive_batch(store, 'body', {'seq': 0, 'items': [card(1)]})
    saved, revision = setup_note(store)
    def unavailable(url, media):
        assert not media
        raise XhsError(code, 'Explicit unavailable note page')
    fetcher = Fetcher(unavailable)
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    assert ImageWorker(store, fetcher=fetcher).once()
    assert fetcher.calls == [(saved['source_url'], False)]
    with store.read() as db:
        assert invalid_source(db, saved['note_id'])['error'] == code
        assert image_report(db, job['job_id'])['items'] == {'skipped': 1}
        assert db.execute("SELECT state FROM job_items WHERE job_id='body'").fetchone()[0] == 'skipped'
    assert current(store, saved['note_id'])[0] == revision
    assert create_image_job(store, capture(saved))['items'] == {'skipped': 1}
    assert not ImageWorker(store, fetcher=never_fetch()).once()


def test_explicit_unavailable_html_from_image_page_fetch_is_persisted(store):
    saved, revision = setup_note(store)
    class PageFetcher:
        def get(self, url, *, media=False):
            assert not media
            return '<main><h1>找不到对应的笔记</h1></main>'.encode(), 'text/html', url
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    assert ImageWorker(store, fetcher=PageFetcher()).once()
    with store.read() as db:
        assert invalid_source(db, saved['note_id'])['error'] == 'SOURCE_UNAVAILABLE'
        assert image_report(db, job['job_id'])['items'] == {'skipped': 1}
    assert current(store, saved['note_id'])[0] == revision


@pytest.mark.parametrize('code', ['SOURCE_UNAVAILABLE', 'SOURCE_LINK_EXPIRED'])
def test_an_unavailable_image_asset_does_not_blacklist_the_note(store, code):
    saved, _ = setup_note(store)
    def missing_asset(url, media):
        assert media
        raise XhsError(code, 'A missing or expired CDN asset')
    result = run(store, capture(saved, count=1, expected=1), Fetcher(missing_asset))
    assert result['items'] == {'partial': 1}
    with store.read() as db:
        assert invalid_source(db, saved['note_id']) is None
    assert current(store, saved['note_id'])[1]['images'][0]['error'] == code
    assert create_image_job(store, capture(saved))['items'] == {'queued': 1}


def test_invalidating_during_an_asset_fetch_preserves_old_body_and_images(store):
    saved, _ = setup_note(store)
    run(store, capture(saved))
    revision, archived = current(store, saved['note_id'])
    request = capture(saved)
    request['snapshot']['images'][0] = 'https://ci.xiaohongshu.com/new.png'
    job = create_image_job(store, request)
    fetcher = Fetcher(lambda *_: invalidate(store, saved))
    assert ImageWorker(store, fetcher=fetcher).once()
    assert len(fetcher.calls) == 1
    assert current(store, saved['note_id']) == (revision, archived)
    assert all(store.object_path(asset['object_path']).is_file() for asset in archived['images'])
    with store.read() as db:
        assert image_report(db, job['job_id'])['items'] == {'skipped': 1}
    assert not ImageWorker(store, fetcher=never_fetch()).once()


def test_invalidating_after_lease_but_before_fetch_prevents_network(store):
    saved, revision = setup_note(store)
    job = create_image_job(store, capture(saved))
    class RacingWorker(ImageWorker):
        def collect(self, job_id, note_id, payload, prior):
            invalidate(self.store, saved)
            return super().collect(job_id, note_id, payload, prior)
    assert RacingWorker(store, fetcher=never_fetch()).once()
    with store.read() as db:
        assert image_report(db, job['job_id'])['items'] == {'skipped': 1}
    assert current(store, saved['note_id'])[0] == revision


def test_invalidating_between_final_check_and_publish_cannot_replace_archive(store):
    saved, revision = setup_note(store)
    job = create_image_job(store, capture(saved, count=1, expected=1))
    class RacingWorker(ImageWorker):
        collected = False

        def collect(self, *args):
            result = super().collect(*args)
            self.collected = True
            return result

        def check(self, *args):
            super().check(*args)
            if self.collected:
                invalidate(self.store, saved)
    assert RacingWorker(store, fetcher=Fetcher()).once()
    assert current(store, saved['note_id'])[0] == revision
    with store.read() as db:
        assert image_report(db, job['job_id'])['items'] == {'skipped': 1}


@pytest.mark.parametrize('code', ['NOTE_NOT_FOUND', 'PARSE_FAILED', 'ACCESS_BLOCKED'])
def test_uncertain_note_page_errors_do_not_establish_persistent_unavailability(store, code):
    saved, revision = setup_note(store)
    def uncertain(url, media):
        assert not media
        raise XhsError(code, 'Insufficient evidence of note unavailability')
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    assert ImageWorker(store, fetcher=Fetcher(uncertain)).once()
    with store.read() as db:
        assert invalid_source(db, saved['note_id']) is None
        assert image_report(db, job['job_id'])['items'] == {'failed': 1}
    assert current(store, saved['note_id'])[0] == revision


def test_image_plan_keeps_local_evidence_but_excludes_invalid_fetch_candidates(store, monkeypatch):
    saved, revision = setup_note(store)
    invalid = invalidate(store, saved)
    monkeypatch.setattr(store, 'write', lambda: pytest.fail('The plan must stay read-only'))
    result = image_plan(store, {'note_ids': [saved['note_id']]})['data']
    assert result['fetch_candidates'] == 0
    item = result['items'][0]
    assert item['revision_id'] == revision and item['body_state'] == 'available'
    assert item['fetch_candidate'] is False and item['priority'] == 'source_unavailable'
    assert item['invalid_source'] == invalid


def test_image_plan_cursor_expires_when_invalid_sources_change_without_archive_generation(store):
    first, _ = setup_note(store, 1)
    second, _ = setup_note(store, 2)
    original = image_plan(store, {'note_ids': [first['note_id'], second['note_id']], 'limit': 1})['data']
    assert original['next_cursor']
    invalidate(store, second)
    with store.read() as db:
        assert store.generation(db) == original['generation']
    with pytest.raises(XhsError) as exc:
        image_plan(store, {'cursor': original['next_cursor']})
    assert exc.value.code == 'CURSOR_STALE'
