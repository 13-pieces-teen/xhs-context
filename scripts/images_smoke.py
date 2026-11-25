"""Synthetic UI -> browser gallery -> native CDN queue. No live XHS."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright, expect

from browser_smoke import prepare_host, unregister_test_host
from xhs_context.common import dumps
from xhs_context.core import read, status
from xhs_context.image_jobs import create_image_job, image_report
from xhs_context.jobs import create_job, receive_batch, finish_scan
from xhs_context.processing import finish_body
from xhs_context.setup import extension_id, register_host
from xhs_context.storage import Store

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'var/validation'
ORIGIN = 'https://www.xiaohongshu.com'
MEDIA = ['https://ci.xiaohongshu.com/first.png', 'https://ci.xiaohongshu.com/second.png']


@contextmanager
def isolated_browser(playwright, work, extension, executable, headed):
    """Use the stable extension/native harness; keep production visibility guards."""
    context = playwright.chromium.launch_persistent_context(str(work / 'profile'), headless=not headed,
        executable_path=executable,
        args=['--host-resolver-rules=MAP * ~NOTFOUND', '--disable-extensions-except=' + str(extension),
              '--load-extension=' + str(extension)] + (['--window-position=-32000,-32000'] if headed else []),
        ignore_default_args=['--disable-extensions'], viewport={'width': 1000, 'height': 900})
    try:
        yield context
    finally:
        context.close()


def page_html(mode='normal'):
    head = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>'
    if mode == 'restricted':
        return (ROOT / 'tests/fixtures/access-restricted.html').read_text(encoding='utf-8')
    if mode == 'unavailable':
        return head + '<main><h1>找不到对应的笔记</h1><p>这篇笔记已无法访问</p></main></body></html>'
    gallery = '''<div data-xhs-gallery>
      <div class="swiper-slide" data-swiper-slide-index="1"><img src="https://ci.xiaohongshu.com/second.png"></div>
      <div class="swiper-slide" data-swiper-slide-index="0"><img src="https://ci.xiaohongshu.com/first.png"></div>
      <div class="swiper-slide" data-swiper-slide-index="0"><img src="https://ci.xiaohongshu.com/first.png"></div>
      <div class="fraction" style="opacity:0">1/2</div></div>'''
    if mode == 'partial':
        gallery = gallery.replace('<div class="fraction" style="opacity:0">1/2</div>', '')
    if mode == 'zero':
        gallery = '<div data-xhs-gallery><span data-xhs-image-total="0">0</span></div>'
    if mode == 'video':
        gallery = '<video controls width="320" height="240"></video>'
    if mode == 'empty':
        gallery = ''
    content = '<h1 id="detail-title">合成图片夹具</h1><div id="detail-desc">页面新文字不能覆盖原正文</div>'
    if mode == 'gallery_only':
        content = ''
    if mode == 'delayed':
        return head + '<div id="noteContainer">' + content + '</div><script>setTimeout(()=>{document.querySelector("#noteContainer").insertAdjacentHTML("beforeend",' + json.dumps(gallery) + ')},2500)</script></body></html>'
    if mode == 'lazy':
        return head + '<div id="noteContainer">' + content + '''<div data-xhs-gallery>
          <div class="swiper-slide swiper-slide-active" data-swiper-slide-index="0"><img src="https://ci.xiaohongshu.com/first.png"></div>
          <div class="fraction" style="opacity:0">1/2</div><button class="arrow-controller right">Next</button>
        </div></div><script>
          window.galleryClicks=0;
          document.querySelector('button').onclick=()=>{
            window.galleryClicks++;
            fetch('/fixture-gallery-click?note='+location.pathname.split('/').pop());
            const slide=document.querySelector('.swiper-slide');
            slide.dataset.swiperSlideIndex='1';
            document.querySelector('.fraction').textContent='2/2';
            document.querySelector('button').disabled=true;
            setTimeout(()=>{slide.querySelector('img').src='https://ci.xiaohongshu.com/second.png'},1200);
          };
        </script></body></html>'''
    return head + '<div id="noteContainer">' + content + gallery + '''
      <img class="avatar" src="https://ci.xiaohongshu.com/avatar.png">
      <div class="comments"><img src="https://ci.xiaohongshu.com/comment.png"></div></div></body></html>'''


def seed_note(store, identifier):
    note_id = f'{identifier:024x}'
    url = ORIGIN + '/explore/' + note_id
    note = finish_body({'note_id': note_id, 'source_url': url, 'canonical_url': url, 'source_kind': 'synthetic_fixture',
                        'title': '合成图片夹具', 'body': '已保存的原始正文', 'body_state': 'available',
                        'expected_images': None, 'images': []})
    return note_id, store.publish(note)


def seed_collection(store, name, identifiers):
    create_job(store, {'job_id': name, 'expected_count': len(identifiers), 'collection': {
        'id': name, 'name': name, 'source_url': ORIGIN + '/collection/' + name}})
    ids = [seed_note(store, identifier)[0] for identifier in identifiers]
    receive_batch(store, name, {'seq': 0, 'items': [{'note_id': note_id,
        'source_url': ORIGIN + '/explore/' + note_id, 'title': '合成图片夹具'} for note_id in ids]})
    finish_scan(store, name, {'batch_count': 1, 'end_evidence': 'page_end'})
    with store.write() as db:
        db.execute("UPDATE job_items SET state='ready' WHERE job_id=?", (name,))
    return ids


def main():
    if os.name != 'nt':
        raise SystemExit('Native host integration currently requires Windows')
    # Playwright reports these tabs as visible, so the default run verifies the
    # foreground guard and explicitly leaves full background lazy loading NOT_RUN.
    headed = os.environ.get('XHS_TEST_HEADED', '0') == '1'
    work = ROOT / '.tmp' / ('images-browser-' + uuid.uuid4().hex[:12])
    work.mkdir(parents=True)
    ext = work / 'extension'
    shutil.copytree(ROOT / 'browser-extension', ext)
    host = 'org.xhs_context.images_' + uuid.uuid4().hex[:12]
    native = ext / 'native.mjs'
    native.write_text(native.read_text(encoding='utf-8').replace('org.xhs_context.host', host), encoding='utf-8')
    background = ext / 'background.js'
    background.write_text(background.read_text(encoding='utf-8') + '''
globalThis.fixtureCalls = [];
const fixtureRequest = NativeClient.prototype.request;
NativeClient.prototype.request = function(path, body) {
  fixtureCalls.push({path,body}); return fixtureRequest.call(this,path,body);
};
''', encoding='utf-8')
    Image.new('RGB', (32, 24), 'teal').save(work / 'fixture-image.png')
    manifest = prepare_host(work, host, ext, with_images=True)
    store = Store(work / 'library')
    store.initialize()
    current_id, revision = seed_note(store, 123)
    collections = {name: seed_collection(store, name, ids) for name, ids in {
        'new-gallery': [9001], 'legacy-gallery': [9002], 'partial-gallery': [9003],
        'restricted-gallery': [9004, 9005], 'lazy-gallery': [9006], 'missing-gallery': [9007, 9008, 9009],
        'cli-gallery': [9010], 'cli-resume-gallery': [9011]}.items()}
    modes = {f'{9001:024x}': 'delayed', f'{9003:024x}': 'partial', f'{9004:024x}': 'restricted',
             f'{9006:024x}': 'lazy', f'{9007:024x}': 'unavailable', f'{9011:024x}': 'partial'}
    opened, closed, gallery_clicks, errors = [], [], [], []
    report = {'dataset': 'synthetic_fixture', 'live_xhs': 'NOT_RUN', 'work': str(work),
              'browser_mode': 'headed_offscreen' if headed else 'headless', 'checks': {}}
    cache = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'ms-playwright'
    candidates = sorted(cache.glob('chromium-*/**/chrome.exe'), reverse=True)
    executable = os.environ.get('XHS_TEST_CHROMIUM') or (str(candidates[0]) if candidates else None)
    try:
        register_host(manifest, host_name=host)
        with sync_playwright() as p:
            with isolated_browser(p, work, ext, executable, headed) as context:
                def route(request):
                    url = request.request.url
                    if url.startswith(ORIGIN + '/fixture-gallery-click?note='):
                        gallery_clicks.append(url.split('note=', 1)[1])
                        request.fulfill(content_type='application/json', body='{}')
                        return
                    if url.startswith('https://ci.xiaohongshu.com/'):
                        request.fulfill(content_type='image/png', path=str(work / 'fixture-image.png'))
                        return
                    note_id = urlsplit(url).path.split('/')[-1]
                    if url.startswith(ORIGIN + '/explore/'):
                        if request.request.is_navigation_request():
                            opened.append(note_id)
                        html = page_html(modes.get(note_id, 'normal'))
                    else:
                        html = '<meta charset="utf-8"><h1>合成离线页面</h1>'
                    request.fulfill(content_type='text/html; charset=utf-8', body=html)
                context.route('https://**/*', route)
                extension_origin = 'chrome-extension://' + extension_id(ext) + '/'
                target = context.new_page()
                target.goto(ORIGIN + '/explore/' + current_id)
                popup = context.new_page()
                popup.on('pageerror', lambda e: errors.append(str(e)))
                popup.goto('chrome-extension://' + extension_id(ext) + '/popup.html')
                sw = next((worker for worker in context.service_workers if worker.url.startswith(extension_origin)), None)
                if sw is None:
                    sw = context.wait_for_event('serviceworker', predicate=lambda worker: worker.url.startswith(extension_origin))
                report['test_worker'] = sw.url
                target.bring_to_front()
                popup.evaluate('refresh()')
                expect(popup.locator('#images-current')).to_be_enabled(timeout=15000)

                def capture():
                    return popup.evaluate('''async ({id}) => {
                        const tab = (await chrome.tabs.query({})).find(t => t.url?.includes('/explore/' + id));
                        return chrome.tabs.sendMessage(tab.id, {type:'read-images', note_id:id});
                    }''', {'id': current_id})

                snapshot = capture()['snapshot']
                assert snapshot['images'] == MEDIA and snapshot['image_indices'] == [1, 2] and snapshot['expected_images'] == 2
                popup.locator('#images-current').evaluate('(e)=>e.click()')
                expect(popup.locator('#image-progress')).to_contain_text('已保存 2 张 · 已齐全', timeout=20000)
                with store.read() as db:
                    row = db.execute('SELECT r.id,r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=?', (current_id,)).fetchone()
                    assert json.loads(row['payload'])['body'] == '已保存的原始正文' and row['id'] != revision
                assets = read(store, {'items': [{'note_id': current_id, 'revision_id': row['id']}], 'mode': 'asset'})
                assert not assets['partial'] and len(assets['data']['items'][0]['assets']) == 2
                assert all(Path(a['path']).is_file() for a in assets['data']['items'][0]['assets'])
                report['checks']['current_gallery_order_body_preservation'] = 'PASS'

                for mode in ['gallery_only', 'video', 'zero', 'empty', 'unavailable', 'restricted']:
                    modes[current_id] = mode
                    target.goto(ORIGIN + '/explore/' + current_id + '?fixture=' + mode)
                    result = capture()
                    if mode == 'gallery_only':
                        assert result['snapshot']['images'] == MEDIA, result
                    elif mode in {'video', 'zero'}:
                        assert result['snapshot']['expected_images'] == 0 and result['snapshot']['images'] == [], result
                    else:
                        assert result.get('error') == {'empty': 'IMAGE_LIST_UNAVAILABLE', 'unavailable': 'SOURCE_UNAVAILABLE',
                            'restricted': 'ACCESS_RESTRICTED'}[mode], result
                report['checks']['gallery_only_explicit_zero_unknown_empty_and_diagnoses'] = 'PASS'
                target.goto('https://example.test/')
                target.bring_to_front()
                popup.evaluate('refresh()')
                expect(popup.locator('#image-progress')).to_contain_text('已保存 2 张 · 已齐全')
                report['checks']['cross_page_progress'] = 'PASS'

                # Attach routing before the real extension-created tab navigates.
                sw.evaluate('''() => {
                    globalThis.fixtureNavigations=[];
                    globalThis.fixturePageReads=[];
                    const send=chrome.tabs.sendMessage.bind(chrome.tabs);
                    chrome.tabs.sendMessage=(id,message)=>{
                        fixturePageReads.push({id,...message});return send(id,message);
                    };
                    const create=chrome.tabs.create.bind(chrome.tabs);
                    chrome.tabs.create=async options=>{
                        fixtureNavigations.push(options.url); return create({...options,url:'about:blank'});
                    };
                }''')
                def navigate_fixture(page):
                    url = sw.evaluate('fixtureNavigations.shift()')
                    assert url and url.startswith(ORIGIN + '/explore/'), url
                    page.on('close', lambda: closed.append(urlsplit(url).path.split('/')[-1]))
                    page.goto(url)
                    # Playwright's attached page navigation can select its target.
                    # Keep the user's fixture tab in front, as tabs.create(active:false)
                    # does in the installed extension; do not override DOM visibility.
                    target.bring_to_front()
                    if modes.get(urlsplit(url).path.split('/')[-1]) == 'lazy':
                        visibility = page.evaluate('document.visibilityState')
                        report['lazy_background_visibility'] = visibility
                        report['lazy_browser_tabs'] = sw.evaluate("chrome.tabs.query({}).then(tabs=>tabs.map(({id,active,windowId,url})=>({id,active,windowId,url})))")
                context.on('page', navigate_fixture)

                def select_collection(name):
                    session = {'job_id': name, 'collection_id': name, 'name': name, 'page_key': ORIGIN + '/collection/' + name,
                        'started_at': '2026-11-24T00:00:00Z', 'scan_state': 'finished', 'finalized': True}
                    sw.evaluate('''async session=>{
                        const {journal={version:2,queue:[],sessions:{}}}=await chrome.storage.local.get('journal');
                        journal.sessions={[session.job_id]:session}; journal.active_job_id=session.job_id;
                        await chrome.storage.local.set({journal});
                    }''', session)
                    popup.reload()
                    target.bring_to_front()
                    expect(popup.locator('#images-collection')).to_be_enabled(timeout=15000)

                def wait_job(job_id, predicate, timeout=25):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        with store.read() as db:
                            result = image_report(db, job_id)
                        if predicate(result):
                            return result
                        popup.wait_for_timeout(100)
                    raise AssertionError(result)

                def create_from_ui(name):
                    select_collection(name)
                    before = sw.evaluate("fixtureCalls.filter(c=>c.path==='/v1/image-jobs'&&c.body.collection_id).length")
                    popup.locator('#images-collection').click()
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        calls = sw.evaluate("fixtureCalls.filter(c=>c.path==='/v1/image-jobs'&&c.body.collection_id)")
                        if len(calls) > before and calls[-1]['body']['collection_id'] == name:
                            assert calls[-1]['body']['browser'] is True
                            return calls[-1]['body']['job_id']
                        popup.wait_for_timeout(50)
                    raise AssertionError('No UI image collection request')

                fresh = create_from_ui('new-gallery')
                expect(popup.locator('#image-progress')).to_contain_text('读取图片列表中', timeout=10000)
                fresh_report = wait_job(fresh, lambda r: r['complete'])
                assert fresh_report['browser_mode'] and fresh_report['images_saved'] == 2
                assert opened.count(collections['new-gallery'][0]) == 1
                report['checks']['collection_browser_gallery_delayed_render'] = 'PASS'

                lazy = create_from_ui('lazy-gallery')
                lazy_report = wait_job(lazy, lambda r: r['state'] == 'finished')
                lazy_id = collections['lazy-gallery'][0]
                reads = sw.evaluate('fixturePageReads')
                assert any(item.get('note_id') == lazy_id and item.get('collect') is True for item in reads)
                assert opened.count(lazy_id) == closed.count(lazy_id) == 1
                if report['lazy_background_visibility'] == 'hidden':
                    assert lazy_report['complete'] and lazy_report['images_saved'] == 2
                    assert gallery_clicks.count(lazy_id) == 1
                    with store.read() as db:
                        payload = json.loads(db.execute('SELECT payload FROM image_items WHERE job_id=?', (lazy,)).fetchone()[0])
                        assert [item['source_url'] for item in payload['manifest']['images']] == MEDIA
                        assert [item['image_index'] for item in payload['manifest']['images']] == [1, 2]
                    report['checks']['lazy_fraction_gallery_collects_two_and_closes_background_tab'] = 'PASS'
                else:
                    assert report['lazy_background_visibility'] == 'visible'
                    assert lazy_report['items'] == {'partial': 1} and not gallery_clicks
                    report['checks']['headless_foreground_guard'] = 'PASS'
                    report['checks']['lazy_fraction_gallery_collects_two_and_closes_background_tab'] = 'NOT_RUN'
                    report['not_run_reason'] = 'Playwright reports the inactive reader tab as visible; production foreground protection remains enabled. Background lazy loading is covered by gallery_dom_smoke, not this extension/native run.'

                # Reproduce the original paused state, including exhausted anonymous attempts.
                create_image_job(store, {'job_id': 'legacy-image', 'collection_id': 'legacy-gallery'})
                with store.write() as db:
                    db.execute("UPDATE image_jobs SET state='paused',error='ACCESS_BLOCKED' WHERE id='legacy-image'")
                    db.execute("UPDATE image_items SET state='failed',attempts=3,error='ACCESS_BLOCKED' WHERE job_id='legacy-image'")
                select_collection('legacy-gallery')
                expect(popup.locator('#images-resume')).to_have_text('继续补图')
                popup.locator('#images-resume').click()
                legacy_report = wait_job('legacy-image', lambda r: r['complete'])
                assert legacy_report['browser_mode'] and legacy_report['images_saved'] == 2
                assert sw.evaluate("fixtureCalls.some(c=>c.path==='/v1/image-jobs/legacy-image/browser')")
                report['checks']['resume_legacy_login_wall_and_exhausted_attempts'] = 'PASS'

                partial = create_from_ui('partial-gallery')
                partial_report = wait_job(partial, lambda r: r['state'] == 'finished')
                assert partial_report['items'] == {'partial': 1} and not partial_report['complete']
                before = opened.count(collections['partial-gallery'][0])
                popup.wait_for_timeout(6500)
                assert opened.count(collections['partial-gallery'][0]) == before == 1
                modes[collections['partial-gallery'][0]] = 'normal'
                expect(popup.locator('#images-resume')).to_be_visible()
                popup.locator('#images-resume').click()
                assert wait_job(partial, lambda r: r['complete'])['images_saved'] == 2
                assert opened.count(collections['partial-gallery'][0]) == 2
                report['checks']['partial_gallery_waits_for_explicit_resume'] = 'PASS'

                def image_cli(*args):
                    result = subprocess.run([sys.executable, '-m', 'xhs_context', '--library', str(store.root),
                                             'images', *args], capture_output=True, text=True, encoding='utf-8', timeout=15)
                    assert result.returncode == 0, result.stdout + result.stderr
                    return json.loads(result.stdout)['data']

                cli_note = collections['cli-gallery'][0]
                cli_job = image_cli('fetch', '--note', cli_note)['job']
                assert cli_job['browser_mode']
                assert wait_job(cli_job['job_id'], lambda r: r['complete'])['images_saved'] == 2
                assert opened.count(cli_note) == closed.count(cli_note) == 1
                report['checks']['cli_fetch_uses_running_extension_gallery_reader'] = 'PASS'

                cli_partial = create_from_ui('cli-resume-gallery')
                wait_job(cli_partial, lambda r: r['state'] == 'finished')
                paused = popup.evaluate("job_id=>chrome.runtime.sendMessage({type:'images-pause',job_id})", cli_partial)
                assert paused['ok'] and wait_job(cli_partial, lambda r: r['state'] == 'paused')
                assert sw.evaluate("async id=>(await chrome.storage.local.get('journal')).journal.image_paused[id]", cli_partial)
                modes[collections['cli-resume-gallery'][0]] = 'normal'
                image_cli('resume', '--job', cli_partial)
                assert wait_job(cli_partial, lambda r: r['complete'])['images_saved'] == 2
                assert not sw.evaluate("async id=>(await chrome.storage.local.get('journal')).journal.image_paused[id]", cli_partial)
                report['checks']['cli_resume_supersedes_acknowledged_extension_pause'] = 'PASS'

                restricted_job = create_from_ui('restricted-gallery')
                wait_job(restricted_job, lambda r: r['error'] == 'ACCESS_RESTRICTED')
                restricted_count = len(opened)
                popup.wait_for_timeout(3500)
                expect(popup.locator('#images-resume')).to_be_disabled()
                assert status(store)['data']['access_restriction'] and len(opened) == restricted_count
                assert opened.count(collections['restricted-gallery'][1]) == 0
                popup.locator('#refresh').click()
                expect(popup.locator('#images-resume')).to_be_enabled(timeout=10000)
                popup.wait_for_timeout(2000)
                assert not status(store)['data']['access_restriction'] and len(opened) == restricted_count
                modes[collections['restricted-gallery'][0]] = 'normal'
                popup.locator('#images-resume').click()
                assert wait_job(restricted_job, lambda r: r['complete'])['images_saved'] == 4
                report['checks']['image_security_stop_poll_refresh_manual_resume'] = 'PASS'

                missing = create_from_ui('missing-gallery')
                missing_report = wait_job(missing, lambda r: r['state'] == 'finished')
                invalid_id = collections['missing-gallery'][0]
                assert missing_report['items'] == {'ready': 2, 'skipped': 1} and missing_report['images_saved'] == 4
                assert not missing_report['error']
                assert all(opened.count(note_id) == closed.count(note_id) == 1 for note_id in collections['missing-gallery'])
                with store.read() as db:
                    item = db.execute('SELECT state,attempts,error FROM image_items WHERE job_id=? AND note_id=?', (missing, invalid_id)).fetchone()
                    assert dict(item) == {'state': 'skipped', 'attempts': 1, 'error': 'SOURCE_UNAVAILABLE'}
                    assert db.execute('SELECT count(*) FROM invalid_notes WHERE note_id=?', (invalid_id,)).fetchone()[0] == 1
                error_results = sw.evaluate("fixtureCalls.filter(c=>c.path.endsWith('/browser-result')&&c.body.error==='SOURCE_UNAVAILABLE')")
                assert len([item for item in error_results if item['body']['note_id'] == invalid_id]) == 1
                expect(popup.locator('#invalid-summary')).to_contain_text('失效列表（1）', timeout=10000)
                report['checks']['invalid_gallery_closes_and_skips_once_then_reads_following_notes'] = 'PASS'

                opened_before_repeat = len(opened)
                repeated = create_from_ui('missing-gallery')
                assert repeated != missing
                repeated_report = wait_job(repeated, lambda r: r['state'] == 'finished')
                assert repeated_report['items'] == {'ready': 2, 'skipped': 1}
                assert repeated_report['progress']['already_complete'] == 2
                with store.read() as db:
                    item = db.execute('SELECT state,attempts FROM image_items WHERE job_id=? AND note_id=?', (repeated, invalid_id)).fetchone()
                    assert dict(item) == {'state': 'skipped', 'attempts': 0}
                popup.wait_for_timeout(1500)
                assert len(opened) == opened_before_repeat and opened.count(invalid_id) == 1
                remaining = sw.evaluate("chrome.tabs.query({url:'https://www.xiaohongshu.com/*'})")
                assert not remaining, remaining
                report['checks']['same_scope_reuses_local_images_and_never_reopens_invalid_note'] = 'PASS'

                note_log = work / 'note-requests.log'
                assert not note_log.exists() or not note_log.read_text(encoding='utf-8').strip()
                with store.read() as db:
                    for row in db.execute('SELECT r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision'):
                        assert json.loads(row['payload'])['body'] == '已保存的原始正文'
                requests = (work / 'media-requests.log').read_text(encoding='utf-8').splitlines()
                assert all(url in MEDIA for url in requests)
                assert not errors, errors
                OUT.mkdir(parents=True, exist_ok=True)
                popup.evaluate('refresh()')
                popup.screenshot(path=str(OUT / 'images-popup.png'))
                report.update(status='PASS_WITH_NOT_RUN' if report.get('not_run_reason') else 'PASS', page_errors=errors, anonymous_note_requests=0,
                    media_requests=len(requests), browser_note_requests=len(opened), background_tabs_closed=len(closed),
                    lazy_gallery_clicks=len(gallery_clicks), invalid_note_attempts=1, repeated_invalid_note_attempts=0,
                    image_native_calls=sw.evaluate("fixtureCalls.filter(c=>c.path.startsWith('/v1/image-jobs'))"))
    except Exception as exc:
        report.update(status='FAIL', error=str(exc), page_errors=errors)
    finally:
        unregister_test_host(host, manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'images-smoke.json').write_text(dumps(report), encoding='utf-8')
    print(dumps({key: value for key, value in report.items() if key != 'image_native_calls'}))
    return 0 if report['status'] in {'PASS', 'PASS_WITH_NOT_RUN'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
