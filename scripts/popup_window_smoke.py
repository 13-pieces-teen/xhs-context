"""Exercise Chrome's action popup autosizing with an isolated synthetic extension."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var/popup-width"

MEASURE = """() => {
  const box = selector => {
    const node = document.querySelector(selector);
    const r = node.getBoundingClientRect();
    return {x:r.x, y:r.y, width:r.width, height:r.height};
  };
  const lines = selector => {
    const range = document.createRange();
    range.selectNodeContents(document.querySelector(selector));
    return range.getClientRects().length;
  };
  return {
    innerWidth, innerHeight, dpr:devicePixelRatio,
    screenWidth:screen.width, screenHeight:screen.height,
    visualScale:visualViewport.scale,
    html:box('html'), body:box('body'),
    htmlCssWidth:getComputedStyle(document.documentElement).width,
    bodyCssWidth:getComputedStyle(document.body).width,
    bodyMaxWidth:getComputedStyle(document.body).maxWidth,
    bodyFont:getComputedStyle(document.body).fontFamily,
    rootFont:getComputedStyle(document.documentElement).fontFamily,
    componentButtonLoaded:!!getComputedStyle(document.querySelector('#start')).getPropertyValue('--btn-bg'),
    viewScriptLoaded:typeof window.popupView?.open === 'function',
    iconsLoaded:document.querySelectorAll('[data-icon] svg').length > 0,
    appearance:document.body.dataset.resolvedAppearance,
    sheetVisible:!document.getElementById('sheet-layer').hidden,
    backgroundInert:document.getElementById('app-content').inert,
    fieldFont:getComputedStyle(document.getElementById('name')).fontFamily,
    actionPopup:chrome.extension.getViews({type:'popup'}).includes(window),
    titleLines:lines('h1'),
    footerLines:['#details-toggle [data-button-label]','#settings-toggle [data-button-label]','#help-toggle [data-button-label]'].map(lines),
    list:box('#list-meter'), bodyProgress:box('#body-meter'),
    progressVisible:document.getElementById('list-meter').checkVisibility(),
    listLabelLines:lines('.metric-label'),
    pageOverflow:document.documentElement.scrollWidth > innerWidth
  };
}"""


class PopupTarget:
    """Action popups are CDP targets but not Playwright BrowserContext pages."""
    def __init__(self, browser_session, pump_page, target):
        self.cdp, self.pump_page, self.target = browser_session, pump_page, target
        self.session = self.cdp.send('Target.attachToTarget', {'targetId':target, 'flatten':False})['sessionId']
        self.replies = {}
        self.next_id = 0
        self.cdp.on('Target.receivedMessageFromTarget', self.receive)

    def receive(self, event):
        if event['sessionId'] == self.session:
            message = json.loads(event['message'])
            if 'id' in message:
                self.replies[message['id']] = message

    def command(self, method, params=None):
        self.next_id += 1
        request_id = self.next_id
        self.cdp.send('Target.sendMessageToTarget', {'sessionId':self.session,
            'message':json.dumps({'id':request_id,'method':method,'params':params or {}})})
        deadline = time.monotonic() + 10
        while request_id not in self.replies:
            if time.monotonic() > deadline:
                raise TimeoutError('Popup CDP command timed out: ' + method)
            self.pump_page.wait_for_timeout(20)
        reply = self.replies.pop(request_id)
        if 'error' in reply:
            raise RuntimeError(reply['error'])
        return reply.get('result', {})

    def evaluate(self, expression):
        result = self.command('Runtime.evaluate', {'expression':expression, 'returnByValue':True, 'awaitPromise':True})
        if result.get('exceptionDetails'):
            raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')

    def measure(self, output):
        self.evaluate('document.fonts.ready.then(() => true)')
        self.pump_page.wait_for_timeout(200)
        measurement = self.evaluate('(' + MEASURE + ')()')
        screenshot = self.command('Page.captureScreenshot', {'format':'png'})
        output.write_bytes(base64.b64decode(screenshot['data']))
        return measurement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", action="store_true", help="Capture the local var/popup-width/before.css baseline without enforcing fixed-width assertions")
    options = parser.parse_args()
    label = "before" if options.before else "after"
    work = OUT / (label + "-" + uuid.uuid4().hex[:10])
    extension = work / "extension"
    extension.mkdir(parents=True)
    for name in ["popup.html", "popup.css", "popup-view.js", "popup.js"]:
        shutil.copy2(ROOT / "browser-extension" / name, extension / name)
    for name in ["fonts", "icons", "vendor"]:
        shutil.copytree(ROOT / "browser-extension" / name, extension / name)
    if options.before:
        shutil.copy2(OUT / "before.css", extension / "popup.css")
    manifest = {"manifest_version": 3, "name": "Isolated popup sizing fixture", "version": "1.0",
                "background": {"service_worker": "fixture.js"}, "action": {"default_popup": "popup.html"}}
    (extension / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    page = {"name": "收藏 · 笔记", "scope": "saved", "expected_count": 969,
            "page_key": "fixture", "identity_marker": "fixture", "view_marker": "fav-note"}
    session = {"job_id": "fixture-job", "name": page["name"], "finalized": False, "scan_state": "paused"}
    fixture = {"page": page, "session": session, "page_session": session, "server_status": {"job": {
        "state": "paused", "discovered": 851, "body_available": 840, "expected_count": 969,
        "items": {"ready": 840, "queued": 11}, "body_complete": False, "list_complete": False,
        "failure_summary": [], "content_mode": "anonymous"}}}
    (extension / "fixture.js").write_text(
        "let fixture = " + json.dumps(fixture, ensure_ascii=False) + ";\n"
        "chrome.runtime.onMessage.addListener((message, sender, respond) => {\n"
        "  if (message.type === 'state') { setTimeout(() => respond({ok:true,data:fixture}), 200); return true; }\n"
        "  respond({ok:false,error:'FIXTURE_READ_ONLY'});\n"
        "});\n", encoding="utf-8")
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
    if not cache.exists() and os.name == "nt":
        cache = Path.home() / "AppData/Local/ms-playwright"
    candidates = sorted(cache.glob("chromium-*/**/chrome.exe"), reverse=True)
    executable = os.environ.get("XHS_TEST_CHROMIUM") or (str(candidates[0]) if candidates else None)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(work / "profile"), headless=True,
            executable_path=executable, no_viewport=True,
            args=["--screen-info={1600x1200}", "--window-size=1000,900", "--host-resolver-rules=MAP * ~NOTFOUND", "--disable-extensions-except=" + str(extension),
                  "--load-extension=" + str(extension)], ignore_default_args=["--disable-extensions"])
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        print("Service worker: " + worker.url, flush=True)
        browser_session = context.browser.new_browser_cdp_session()
        pump_page = context.pages[0]
        # Enlarge only the enclosing browser window, never the auto-sized popup.
        worker.evaluate("async () => { const windows = await chrome.windows.getAll(); await chrome.windows.update(windows[0].id, {width:1000,height:900}); }")

        def open_popup():
            worker.evaluate("async () => { const windows = await chrome.windows.getAll(); await chrome.action.openPopup({windowId:windows[0].id}); }")
            pump_page.wait_for_timeout(750)
            targets = browser_session.send("Target.getTargets")['targetInfos']
            target = next(target for target in targets if target['url'].endswith('/popup.html'))
            return PopupTarget(browser_session, pump_page, target['targetId'])

        def close_popup(popup):
            popup.evaluate('setTimeout(() => window.close(), 0); true')
            pump_page.wait_for_timeout(400)

        report = {'live_xhs':'NOT_RUN', 'dataset':'synthetic', 'viewport_override':False, 'passed':False,
                  'transport':'chrome.action.openPopup + CDP target', 'measurements':{}}
        try:
            popup = open_popup()
            report['measurements']['cold-paused'] = popup.measure(OUT / (label + '-cold-paused.png'))
            close_popup(popup)
            ready = {'page':page, 'session':None, 'page_session':None, 'server_status':None}
            worker.evaluate('value => { fixture = value; }', ready)
            popup = open_popup()
            report['measurements']['ready'] = popup.measure(OUT / (label + '-ready.png'))
            if not options.before:
                popup.evaluate("document.getElementById('settings-toggle').click(); true")
                pump_page.wait_for_timeout(300)
                report['measurements']['ready-settings'] = popup.measure(OUT / (label + '-ready-settings.png'))
                settings_measurement = report['measurements']['ready-settings']
                assert settings_measurement['sheetVisible'] and settings_measurement['backgroundInert'], settings_measurement
                assert settings_measurement['fieldFont'] == settings_measurement['bodyFont'], settings_measurement
                popup.evaluate("document.getElementById('sheet-done').click(); true")
                pump_page.wait_for_timeout(250)
                assert popup.evaluate("document.getElementById('sheet-layer').hidden && !document.getElementById('app-content').inert")
                popup.evaluate("document.getElementById('help-toggle').click(); true")
                pump_page.wait_for_timeout(300)
                popup.evaluate("document.querySelector('[data-appearance-choice=dark]').click(); document.getElementById('sheet-close').click(); true")
                pump_page.wait_for_timeout(250)
                report['measurements']['ready-dark'] = popup.measure(OUT / (label + '-ready-dark.png'))
                assert report['measurements']['ready-dark']['appearance'] == 'dark'
            worker.evaluate('value => { fixture = value; }', fixture)
            popup.evaluate('refresh().then(() => true)')
            report['measurements']['ready-to-paused'] = popup.measure(OUT / (label + '-ready-to-paused.png'))
            close_popup(popup)
            popup = open_popup()
            report['measurements']['reopened-paused'] = popup.measure(OUT / (label + '-reopened-paused.png'))
            for name, measurement in report['measurements'].items():
                assert measurement['actionPopup'], (name, measurement)
                if not options.before:
                    assert measurement['innerWidth'] == measurement['html']['width'] == measurement['body']['width'] == 360, (name, measurement)
                    assert measurement['titleLines'] == 1 and measurement['footerLines'] == [1,1,1], (name, measurement)
                    assert not measurement['pageOverflow'], (name, measurement)
                    assert measurement['componentButtonLoaded'], (name, measurement)
                    assert measurement['viewScriptLoaded'] and measurement['iconsLoaded'], (name, measurement)
                    assert measurement['bodyFont'] == measurement['rootFont'] and 'MiSans' in measurement['bodyFont'], (name, measurement)
                    if measurement['progressVisible']:
                        assert measurement['list']['x'] + measurement['list']['width'] <= measurement['bodyProgress']['x'], (name, measurement)
                        assert measurement['listLabelLines'] == 1, (name, measurement)
            report['passed'] = True
            if options.before:
                report['reproduced_compaction'] = any(item['body']['width'] < 360 for item in report['measurements'].values())
        finally:
            (OUT / (label + '-report.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'passed':report['passed'], 'popup_widths':{
                name:item['innerWidth'] for name,item in report['measurements'].items()},
                'report':str(OUT / (label + '-report.json'))}, ensure_ascii=False), flush=True)
            context.close()


if __name__ == "__main__":
    main()
