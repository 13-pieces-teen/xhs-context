import pytest

from conftest import card
from test_images import capture, current, run, setup_note
from xhs_context.browser_import import next_page
from xhs_context.image_jobs import create_image_job
from xhs_context.images import ImageWorker
from xhs_context.invalid_sources import invalid_report, invalid_source, mark_invalid, skip_invalid_items
from xhs_context.jobs import create_job, receive_batch
from xhs_context.storage import Store
from xhs_context.worker import Worker


def add_case_job(store, job_id, item):
    create_job(store, {'job_id': job_id, 'collection': {'id': job_id, 'name': job_id,
        'source_url': 'https://www.xiaohongshu.com/user/profile/' + job_id}})
    receive_batch(store, job_id, {'seq': 0, 'items': [item]})


class NeverFetch:
    def get(self, *args, **kwargs):
        pytest.fail('Unavailable source was requested again')


@pytest.mark.parametrize('code', ['SOURCE_UNAVAILABLE', 'SOURCE_LINK_EXPIRED'])
def test_legacy_note_page_image_failures_are_visible_read_only_and_migrate(store, code):
    saved, revision = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']]})
    with store.write() as db:
        db.execute("UPDATE image_items SET state='failed',error=?,updated_at='2026-01-01'", (code,))
        db.execute("UPDATE image_jobs SET state='finished'")
        db.execute('DROP TABLE invalid_notes')
        generation = store.generation(db)
    with store.read() as db:
        before = invalid_report(db)
        assert before['total'] == 1 and before['items'][0]['error'] == code
        assert invalid_source(db, saved['note_id'])['job_id'] == job['job_id']
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='invalid_notes'").fetchone()
    store.initialize()
    new_job = create_image_job(store, capture(saved))
    assert new_job['items'] == {'skipped': 1}
    assert not ImageWorker(store, fetcher=NeverFetch()).once()
    with store.read() as db:
        assert invalid_report(db) == before
        assert store.generation(db) == generation
        assert db.execute('SELECT current_revision FROM notes').fetchone()[0] == revision


def test_legacy_asset_failures_do_not_become_unavailable_notes(store):
    saved, _ = setup_note(store)
    create_image_job(store, capture(saved))
    with store.write() as db:
        db.execute("UPDATE image_items SET state='partial',error='IMAGE_DOWNLOAD_FAILED' ,"
                   "payload=json_set(payload,'$.staged_images',json(?))",
                   ('[{"asset_id":"img-1","state":"failed","error":"SOURCE_UNAVAILABLE"}]',))
        db.execute('DROP TABLE invalid_notes')
    with store.read() as db:
        assert invalid_report(db)['total'] == 0
    store.initialize()
    with store.read() as db:
        assert invalid_source(db, saved['note_id']) is None
        assert db.execute('SELECT state FROM image_items').fetchone()[0] == 'partial'


@pytest.mark.parametrize('first_upper', [False, True])
def test_equivalent_hex_id_cases_share_guard_without_rewriting_archive_keys(store, first_upper):
    lower = card(0xabcdef)
    upper = dict(lower, note_id=lower['note_id'].upper())
    first, other = (upper, lower) if first_upper else (lower, upper)
    add_case_job(store, 'first', first)
    add_case_job(store, 'already-queued', other)
    with store.read() as db:
        before = [tuple(r) for r in db.execute('SELECT id,current_revision FROM notes ORDER BY id')]
    with store.write() as db:
        initial = mark_invalid(db, first['note_id'], 'SOURCE_UNAVAILABLE', first['source_url'], 'first')
        assert mark_invalid(db, other['note_id'], 'SOURCE_LINK_EXPIRED', other['source_url']) == initial
    add_case_job(store, 'new-task', other)
    assert create_image_job(store, {'note_ids': [other['note_id']]})['items'] == {'skipped': 1}
    assert not Worker(store, fetcher=NeverFetch()).once()
    assert not ImageWorker(store, fetcher=NeverFetch()).once()
    assert next_page(store)['task'] is None
    with store.read() as db:
        assert invalid_source(db, other['note_id']) == initial
        assert invalid_report(db)['total'] == 1
        assert {r[0] for r in db.execute('SELECT state FROM job_items')} == {'skipped'}
        assert [tuple(r) for r in db.execute('SELECT id,current_revision FROM notes ORDER BY id')] == before


def test_legacy_case_variants_choose_earliest_body_or_image_failure(store):
    lower = card(0xabcdef)
    upper = dict(lower, note_id=lower['note_id'].upper())
    add_case_job(store, 'later-body', lower)
    add_case_job(store, 'old-image-note', upper)
    job = create_image_job(store, {'note_ids': [upper['note_id']]})
    with store.write() as db:
        db.execute("UPDATE job_items SET state='failed',error='SOURCE_UNAVAILABLE',updated_at='2026-01-02' WHERE job_id='later-body'")
        db.execute("UPDATE image_items SET state='failed',error='SOURCE_LINK_EXPIRED',updated_at='2026-01-01'")
        db.execute('DROP TABLE invalid_notes')
    with store.read() as db:
        report = invalid_report(db)
        assert report['total'] == 1
        assert invalid_source(db, lower['note_id'])['note_id'] == upper['note_id']
        assert report['items'][0]['job_id'] == job['job_id']
    store.initialize()
    with store.read() as db:
        assert invalid_report(db) == report
        assert {r[0] for r in db.execute('SELECT error FROM job_items')} == {'SOURCE_LINK_EXPIRED'}


def test_preexisting_registry_case_duplicates_report_once_and_preserve_first_error(store):
    lower = card(0xabcdef)
    upper = dict(lower, note_id=lower['note_id'].upper())
    add_case_job(store, 'lower', lower)
    add_case_job(store, 'upper', upper)
    with store.write() as db:
        db.execute('INSERT INTO invalid_notes VALUES (?,?,?,?,?)',
                   (upper['note_id'], 'SOURCE_LINK_EXPIRED', upper['source_url'], '2026-01-01', 'upper'))
        db.execute('INSERT INTO invalid_notes VALUES (?,?,?,?,?)',
                   (lower['note_id'], 'SOURCE_UNAVAILABLE', lower['source_url'], '2026-01-02', 'lower'))
        skip_invalid_items(db)
    with store.read() as db:
        assert invalid_report(db)['total'] == 1
        assert invalid_source(db, lower['note_id'])['error'] == 'SOURCE_LINK_EXPIRED'
        assert {r[0] for r in db.execute('SELECT state FROM job_items')} == {'skipped'}
        assert {r[0] for r in db.execute('SELECT error FROM job_items')} == {'SOURCE_LINK_EXPIRED'}


def test_old_library_without_image_tables_can_still_report_and_upgrade(store):
    add_case_job(store, 'old', card(1))
    with store.write() as db:
        db.execute("UPDATE job_items SET state='failed',error='SOURCE_UNAVAILABLE'")
        db.execute('DROP TABLE image_items')
        db.execute('DROP TABLE image_jobs')
        db.execute('DROP TABLE invalid_notes')
    with store.read() as db:
        assert invalid_report(db)['total'] == 1
    store.initialize()
    with store.read() as db:
        assert invalid_report(db)['total'] == 1


def test_backup_restore_preserves_registry_archive_history_and_downloaded_images(store, tmp_path):
    from xhs_context.exports import backup, restore
    saved, _ = setup_note(store)
    run(store, capture(saved))
    revision, archived = current(store, saved['note_id'])
    with store.write() as db:
        mark_invalid(db, saved['note_id'], 'SOURCE_UNAVAILABLE', saved['source_url'])
    with store.read() as db:
        registry = invalid_report(db)
        history = [tuple(row) for row in db.execute('SELECT id,payload FROM revisions ORDER BY id')]
    backup(store, tmp_path / 'backup-invalid')
    restore(tmp_path / 'backup-invalid', tmp_path / 'restored-invalid')
    restored = Store(tmp_path / 'restored-invalid')
    with restored.read() as db:
        assert invalid_report(db) == registry
        assert [tuple(row) for row in db.execute('SELECT id,payload FROM revisions ORDER BY id')] == history
    assert current(restored, saved['note_id']) == (revision, archived)
    for asset in archived['images']:
        assert restored.object_path(asset['object_path']).read_bytes() == store.object_path(asset['object_path']).read_bytes()
    assert create_image_job(restored, capture(saved))['items'] == {'skipped': 1}
    assert not ImageWorker(restored, fetcher=NeverFetch()).once()
