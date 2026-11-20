"""Isolated Chromium extension -> native image queue -> local image evidence. No live XHS."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

from PIL import Image
from playwright.sync_api import sync_playwright, expect

from browser_smoke import prepare_host, unregister_test_host
from xhs_context.access import report_restriction
from xhs_context.common import dumps
from xhs_context.core import read
from xhs_context.processing import finish_body
from xhs_context.setup import extension_id, register_host
from xhs_context.storage import Store

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'var/validation'


def main():
    if os.name != 'nt':
        raise SystemExit('Native host integration currently requires Windows')
    work = ROOT / '.tmp' / ('images-browser-' + uuid.uuid4().hex[:12])
    work.mkdir(parents=True)
    ext = work / 'extension'
    shutil.copytree(ROOT / 'browser-extension', ext)
    host = 'org.xhs_context.images_' + uuid.uuid4().hex[:12]
    native = ext / 'native.mjs'
    native.write_text(native.read_text(encoding='utf-8').replace('org.xhs_context.host', host), encoding='utf-8')
    Image.new('RGB', (32, 24), 'teal').save(work / 'fixture-image.png')
    manifest = prepare_host(work, host, ext, with_images=True)
    store = Store(work / 'library')
    store.initialize()
    note_id = '000000000000000000000123'
    url = 'https://www.xiaohongshu.com/explore/' + note_id
    note = finish_body({'note_id': note_id, 'source_url': url, 'canonical_url': url, 'source_kind': 'synthetic_fixture',
                        'title': '图片独立保存夹具', 'body': '已保存的原始正文', 'body_state': 'available',
                        'expected_images': None, 'images': []})
    revision = store.publish(note)
    html = '''<!doctype html><html><body><div id="noteContainer"><h1 id="detail-title">图片夹具</h1>
    <div id="detail-desc">页面中的新文字，不应覆盖旧正文</div>
    <img class="avatar" src="https://ci.xiaohongshu.com/avatar.png">
    <div data-xhs-gallery>
      <div class="swiper-slide" data-swiper-slide-index="1"><img src="https://ci.xiaohongshu.com/second.png"></div>
      <div class="swiper-slide" data-swiper-slide-index="0"><img src="https://ci.xiaohongshu.com/first.png"></div>
      <div class="swiper-slide" data-swiper-slide-index="0"><img src="https://ci.xiaohongshu.com/first.png"></div>
      <span data-xhs-image-total="2">2</span>
    </div><div class="comments"><img src="https://ci.xiaohongshu.com/comment.png"></div>
    </div></body></html>'''
    report, errors = {'dataset': 'synthetic_fixture', 'live_xhs': 'NOT_RUN'}, []
    cache = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'ms-playwright'
    candidates = sorted(cache.glob('chromium-*/**/chrome.exe'), reverse=True)
    executable = os.environ.get('XHS_TEST_CHROMIUM') or (str(candidates[0]) if candidates else None)
    try:
        register_host(manifest, host_name=host)
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(work / 'profile'), headless=True, executable_path=executable,
                args=['--host-resolver-rules=MAP * ~NOTFOUND', '--disable-extensions-except=' + str(ext), '--load-extension=' + str(ext)],
                ignore_default_args=['--disable-extensions'], viewport={'width': 1000, 'height': 900})
            try:
                def route(request):
                    if request.request.url.startswith('https://ci.xiaohongshu.com/'):
                        request.fulfill(content_type='image/png', path=str(work / 'fixture-image.png'))
                    else:
                        request.fulfill(content_type='text/html', body=html if request.request.url.startswith(url) else '<h1>另一页面</h1>')
                context.route('https://**/*', route)
                target = context.new_page()
                target.goto(url)
                popup = context.new_page()
                popup.on('pageerror', lambda e: errors.append(str(e)))
                popup.goto('chrome-extension://' + extension_id(ext) + '/popup.html')
                target.bring_to_front()
                popup.locator('#refresh').evaluate('(e)=>e.click()')
                expect(popup.locator('#images-current')).to_be_enabled(timeout=15000)
                snapshot = popup.evaluate('''async ({id, url}) => {
                    const tabs = await chrome.tabs.query({});
                    const tab = tabs.find(t => t.url === url);
                    return (await chrome.tabs.sendMessage(tab.id, {type: 'read-images', note_id: id})).snapshot;
                }''', {'id': note_id, 'url': url})
                assert snapshot['images'] == ['https://ci.xiaohongshu.com/first.png', 'https://ci.xiaohongshu.com/second.png']
                assert snapshot['image_indices'] == [1, 2] and snapshot['expected_images'] == 2
                popup.locator('#images-current').evaluate('(e)=>e.click()')
                expect(popup.locator('#image-progress')).to_contain_text('图片已保存 2 张', timeout=20000)
                with store.read() as db:
                    row = db.execute('SELECT r.id,r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=?', (note_id,)).fetchone()
                    updated = json.loads(row['payload'])
                    assert updated['body'] == note['body'] and row['id'] != revision
                assets = read(store, {'items': [{'note_id': note_id, 'revision_id': row['id']}], 'mode': 'asset'})
                assert not assets['partial'] and len(assets['data']['items'][0]['assets']) == 2
                assert all(Path(a['path']).is_file() for a in assets['data']['items'][0]['assets'])
                other = context.new_page()
                other.goto('https://example.test/')
                popup.close()
                popup = context.new_page()
                popup.on('pageerror', lambda e: errors.append(str(e)))
                popup.goto('chrome-extension://' + extension_id(ext) + '/popup.html')
                other.bring_to_front()
                popup.locator('#refresh').evaluate('(e)=>e.click()')
                expect(popup.locator('#image-progress')).to_contain_text('图片已保存 2 张')
                report_restriction(store, {'error': 'ACCESS_RESTRICTED'})
                target.bring_to_front()
                popup.locator('#refresh').evaluate('(e)=>e.click()')
                expect(popup.locator('#images-current')).to_be_disabled()
                rejected = popup.evaluate("async()=>await chrome.runtime.sendMessage({type:'images-current'})")
                assert not rejected['ok'] and rejected['error'] == 'ACCESS_RESTRICTED'
                requests = (work / 'media-requests.log').read_text(encoding='utf-8').splitlines()
                assert requests == snapshot['images'] and not errors
                OUT.mkdir(parents=True, exist_ok=True)
                popup.screenshot(path=str(OUT / 'images-popup.png'))
                report.update(status='PASS', body_preserved=True, image_files=2, gallery_order_and_duplicates='PASS',
                              cross_page_progress='PASS', restriction_guard='PASS', media_requests=requests)
            finally:
                context.close()
    except Exception as exc:
        report.update(status='FAIL', error=str(exc), page_errors=errors)
    finally:
        unregister_test_host(host, manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'images-smoke.json').write_text(dumps(report), encoding='utf-8')
    print(dumps(report))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
