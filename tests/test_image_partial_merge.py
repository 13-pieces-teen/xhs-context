"""Partial galleries combine only verified positions in one observed version."""
import pytest

from test_images import Fetcher, current, picture, run, setup_note
from xhs_context.common import XhsError, digest
from xhs_context.image_browser import accept_image_page, enable_image_browser, next_image_page
from xhs_context.image_jobs import create_image_job, image_report
from xhs_context.images import ImageWorker
from xhs_context.processing import store_image
from xhs_context.storage import Store


def capture_positions(saved, positions, expected=2, *, urls=None):
    return {'snapshot': {'note_id': saved['note_id'], 'page_url': saved['source_url'],
                         'images': urls or [f'https://ci.xiaohongshu.com/{i - 1 if i else "unknown"}.png' for i in positions],
                         'image_indices': positions, 'expected_images': expected, 'note_type': 'image'}}


def test_two_partial_captures_complete_after_store_reopen(store):
    saved, _ = setup_note(store)
    first, second = Fetcher(), Fetcher()
    assert run(store, capture_positions(saved, [1]), first)['items'] == {'partial': 1}
    reopened = Store(store.root)
    reopened.initialize()
    result = run(reopened, capture_positions(saved, [2]), second)
    _, updated = current(reopened, saved['note_id'])
    assert result['complete'] and result['items'] == {'ready': 1}
    assert updated['image_manifest_complete'] and updated['expected_images'] == 2
    assert [asset['image_index'] for asset in updated['images']] == [1, 2]
    assert 'IMAGE_LIST_UNVERIFIED' not in updated['gaps']
    assert len(first.calls) == len(second.calls) == 1
    assert updated['body'] == saved['body'] and updated['raw_object'] == saved['raw_object']
    assert all(digest(reopened.object_path(asset['object_path']).read_bytes()) == asset['sha256'] for asset in updated['images'])


def test_browser_resume_merges_partial_positions_without_refetching_saved_image(store):
    saved, _ = setup_note(store)
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    calls = []
    for index in [1, 2]:
        if index == 2:
            enable_image_browser(store, job['job_id'])
        task = next_image_page(store)['task']
        accept_image_page(store, job['job_id'], dict(note_id=task['note_id'], lease_id=task['lease_id'],
                                                    **capture_positions(saved, [index])))
        fetcher = Fetcher()
        assert ImageWorker(store, fetcher=fetcher).once(job['job_id'])
        calls.extend(fetcher.calls)
    with store.read() as db:
        assert image_report(db, job['job_id'])['complete']
    assert calls == [('https://ci.xiaohongshu.com/0.png', True), ('https://ci.xiaohongshu.com/1.png', True)]


def test_legacy_partial_uses_unchanged_stored_body_revision(store):
    saved, _ = setup_note(store)
    run(store, capture_positions(saved, [1]))
    _, old = current(store, saved['note_id'])
    old.pop('images_capture_context')
    for asset in old['images']:
        asset.pop('gallery_context')
    store.publish(old)
    assert run(store, capture_positions(saved, [2]))['complete']


@pytest.mark.parametrize('first_positions,second_positions,first_total,second_total', [
    ([None], [2], 2, 2), ([1], [None], 2, 2), ([1], [1], 2, 2),
    ([1], [2], None, None), ([1], [2], 3, 2),
])
def test_unreliable_or_incompatible_captures_stay_partial(store, first_positions, second_positions, first_total, second_total):
    saved, _ = setup_note(store)
    run(store, capture_positions(saved, first_positions, first_total))
    assert not run(store, capture_positions(saved, second_positions, second_total))['complete']
    assert not current(store, saved['note_id'])[1]['image_manifest_complete']


@pytest.mark.parametrize('change', ['body', 'title', 'corrupt', 'thumbnail'])
def test_old_evidence_cannot_fill_new_or_unverified_gallery(store, change):
    saved, _ = setup_note(store)
    run(store, capture_positions(saved, [1]))
    _, old = current(store, saved['note_id'])
    path = store.object_path(old['images'][0]['object_path'])
    if change == 'corrupt':
        path.write_bytes(b'corrupt fixture')
    elif change == 'thumbnail':
        old['images'][0]['quality'] = 'thumbnail'
        store.publish(old)
    else:
        old[change] = 'changed fixture'
        store.publish(old)
    assert not run(store, capture_positions(saved, [2]))['complete']
    assert not current(store, saved['note_id'])[1]['image_manifest_complete']
    assert path.is_file()


class ColorFetcher(Fetcher):
    def get(self, url, *, media=False):
        self.calls.append((url, media))
        assert media
        return picture('green' if 'changed' in url else 'blue' if url.endswith('1.png') else 'red'), 'image/png', url


def test_changed_position_starts_new_gallery_generation_and_retains_old_files(store):
    saved, _ = setup_note(store)
    run(store, capture_positions(saved, [1, 2], 3), ColorFetcher())
    _, old = current(store, saved['note_id'])
    old_paths = [store.object_path(asset['object_path']) for asset in old['images']]
    new_urls = ['https://ci.xiaohongshu.com/changed.png', 'https://ci.xiaohongshu.com/2.png']
    changed = run(store, capture_positions(saved, [2, 3], 3, urls=new_urls), ColorFetcher())
    assert not changed['complete']
    _, partial = current(store, saved['note_id'])
    assert partial['images_capture_context']['id'] != old['images_capture_context']['id']
    # A later observation of the missing position completes this new generation.
    assert run(store, capture_positions(saved, [1], 3), ColorFetcher())['complete']
    _, updated = current(store, saved['note_id'])
    assert [asset['image_index'] for asset in updated['images']] == [1, 2, 3]
    assert updated['images'][1]['source_url'] == new_urls[0]
    assert all(path.is_file() for path in old_paths)


def test_identical_pixels_at_distinct_positions_are_distinct_gallery_images(store):
    saved, _ = setup_note(store)
    run(store, capture_positions(saved, [1]))
    result = run(store, capture_positions(saved, [2], urls=['https://ci.xiaohongshu.com/also-red.png']))
    _, updated = current(store, saved['note_id'])
    assert result['complete'] and len(updated['images']) == 2
    assert updated['images'][0]['sha256'] == updated['images'][1]['sha256']


def test_interrupted_new_version_prefers_checkpoint_to_older_saved_position(store):
    saved, _ = setup_note(store)
    run(store, capture_positions(saved, [1, 2], 3), ColorFetcher())
    job = create_image_job(store, {'note_ids': [saved['note_id']], 'browser': True})
    task = next_image_page(store)['task']
    urls = ['https://ci.xiaohongshu.com/changed.png', 'https://ci.xiaohongshu.com/2.png']
    accept_image_page(store, job['job_id'], dict(note_id=task['note_id'], lease_id=task['lease_id'],
                                                **capture_positions(saved, [2, 3], 3, urls=urls)))

    class Interrupted(ColorFetcher):
        def get(self, url, *, media=False):
            if url == urls[1]:
                raise XhsError('ACCESS_BLOCKED', 'synthetic interrupted transfer')
            return super().get(url, media=media)

    assert ImageWorker(store, fetcher=Interrupted()).once(job['job_id'])
    enable_image_browser(store, job['job_id'])
    resumed = next_image_page(store)['task']
    accept_image_page(store, job['job_id'], dict(note_id=resumed['note_id'], lease_id=resumed['lease_id'],
                                                **capture_positions(saved, [1, 3], 3)))
    fetcher = ColorFetcher()
    assert ImageWorker(store, fetcher=fetcher).once(job['job_id'])
    with store.read() as db:
        assert image_report(db, job['job_id'])['complete']
    _, updated = current(store, saved['note_id'])
    assert updated['images'][1]['source_url'] == urls[0]
    assert fetcher.calls == [(urls[1], True)]


def test_conflicting_stored_duplicate_indices_cannot_prove_completeness(store):
    saved, _ = setup_note(store)
    run(store, capture_positions(saved, [1]))
    _, old = current(store, saved['note_id'])
    duplicate = dict(old['images'][0], asset_id='alternate', source_url='https://ci.xiaohongshu.com/alternate.png')
    duplicate = store_image(store, picture('green'), duplicate, ocr=None)
    old['images'].append(duplicate)
    store.publish(old)
    assert not run(store, capture_positions(saved, [2]))['complete']
    assert not current(store, saved['note_id'])[1]['image_manifest_complete']
