"""Real Chromium, isolated DOM fixtures for gallery totals and bounded lazy loading."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
NOTE = '1' * 24
URL = 'https://www.xiaohongshu.com/explore/' + NOTE


def main():
    cache = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'ms-playwright'
    candidates = sorted(cache.glob('chromium-*/**/chrome.exe'), reverse=True)
    executable = os.environ.get('XHS_TEST_CHROMIUM') or (str(candidates[0]) if candidates else None)
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable,
                                    args=['--host-resolver-rules=MAP * ~NOTFOUND'])
        try:
            context = browser.new_context()
            context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<html><body></body></html>'))
            page = context.new_page()

            def fixture(gallery, *, visible=False):
                page.goto(URL)
                page.set_content('<div id="noteContainer"><h1 id="detail-title">Fixture</h1>'
                                 '<div class="media-container">' + gallery + '</div></div>')
                page.evaluate('(visible) => Object.defineProperty(document,"visibilityState",{get:()=>visible?"visible":"hidden",configurable:true})', visible)
                page.add_script_tag(path=str(ROOT / 'browser-extension/page-adapter.js'))

            def slide(index):
                return f'<div class="swiper-slide" data-swiper-slide-index="{index}"><img src="https://ci.xiaohongshu.com/{index}.png"></div>'

            fixture(slide(16) + ''.join(slide(i) for i in range(17)) + slide(0) + '<div class="fraction" style="opacity:0">1/17</div>')
            snapshot = page.evaluate('id=>XhsContextPage.readImages(id)', NOTE)['snapshot']
            assert snapshot['expected_images'] == 17 and snapshot['image_indices'] == list(range(1, 18)), snapshot
            checks.append('real_fraction_and_duplicate_slides')

            lazy = slide(0).replace('swiper-slide"', 'swiper-slide swiper-slide-active"') + '<div class="fraction">1/12</div><button class="arrow-controller right">Next</button>'

            def install_lazy(*, blocked=False, restricted=False):
                page.evaluate('''({blocked,restricted})=>{
                    window.clicks=0;
                    document.querySelector('button').onclick=()=>{
                        window.clicks++;
                        if(restricted){document.querySelector('#noteContainer').remove();document.body.innerHTML='<main>安全限制 检测到异常访问 300013</main>';return;}
                        if(blocked)return;
                        const s=document.querySelector('.swiper-slide');const i=Number(s.dataset.swiperSlideIndex)+1;
                        s.dataset.swiperSlideIndex=String(i);s.querySelector('img').src='https://ci.xiaohongshu.com/'+i+'.png';
                        document.querySelector('.fraction').textContent=(i+1)+'/12';
                        if(i===11)document.querySelector('button').disabled=true;
                    };
                }''', {'blocked': blocked, 'restricted': restricted})

            fixture(lazy)
            install_lazy()
            result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
            assert result == {'pending': True}, result
            result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
            assert result['snapshot']['image_indices'] == list(range(1, 13)), result
            assert page.evaluate('clicks') == 11
            checks.append('virtualized_lazy_gallery_accumulates_in_chunks')

            fixture(lazy)
            install_lazy(blocked=True)
            result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
            assert len(result['snapshot']['images']) == 1 and page.evaluate('clicks') == 3, result
            checks.append('stuck_arrow_stops_with_partial_result')

            fixture(lazy, visible=True)
            install_lazy()
            result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
            assert len(result['snapshot']['images']) == 1 and page.evaluate('clicks') == 0
            checks.append('foreground_user_page_is_not_advanced')

            fixture(lazy)
            install_lazy(restricted=True)
            result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
            assert result == {'error': 'ACCESS_RESTRICTED'} and page.evaluate('clicks') == 1, result
            checks.append('restriction_stops_gallery_immediately')

            fixture(lazy.replace('<div class="fraction">1/12</div>', ''))
            install_lazy()
            result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
            assert result['snapshot']['expected_images'] is None and page.evaluate('clicks') == 0
            checks.append('unknown_total_stays_unconfirmed')

            fixture(slide(0) + '<div hidden><div class="fraction">1/1</div></div>')
            result = page.evaluate('id=>XhsContextPage.readImages(id)', NOTE)
            assert result['snapshot']['expected_images'] is None, result
            checks.append('hidden_template_total_is_ignored')

            fixture(slide(99) + '<div class="fraction">1/2</div>')
            assert page.evaluate('id=>XhsContextPage.readImages(id)', NOTE) == {'error': 'IMAGE_LIST_UNAVAILABLE'}
            checks.append('out_of_range_positions_never_reach_native_snapshot')

            fixture(lazy.replace('1/12', '1/3'))
            page.evaluate('''()=>{
                window.clicks=0;
                document.querySelector('button').onclick=()=>{
                    clicks++; const s=document.querySelector('.swiper-slide');const i=Number(s.dataset.swiperSlideIndex)+1;
                    s.dataset.swiperSlideIndex=String(i);document.querySelector('.fraction').textContent=(i+1)+'/3';
                    setTimeout(()=>{s.querySelector('img').src='https://ci.xiaohongshu.com/'+i+'.png'},1200);
                };
            }''')
            for _ in range(5):
                result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
                if 'snapshot' in result:
                    break
            assert result['snapshot']['images'] == [f'https://ci.xiaohongshu.com/{i}.png' for i in range(3)], result
            assert result['snapshot']['image_indices'] == [1, 2, 3] and page.evaluate('clicks') == 2
            checks.append('delayed_image_url_keeps_original_positions')

            fixture(lazy)
            install_lazy()
            results = page.evaluate('id=>Promise.all([XhsContextPage.collectImages(id),XhsContextPage.collectImages(id)])', NOTE)
            assert results == [{'pending': True}, {'pending': True}] and page.evaluate('clicks') == 8, results
            result = page.evaluate('id=>XhsContextPage.collectImages(id)', NOTE)
            assert len(result['snapshot']['images']) == 12 and page.evaluate('clicks') == 11
            checks.append('overlapping_messages_share_one_gallery_reader')
        finally:
            browser.close()
    report = {'status': 'PASS', 'checks': checks, 'live_acquisition': 'NOT_RUN'}
    out = ROOT / 'var/validation/gallery-dom-smoke.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
