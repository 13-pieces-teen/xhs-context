"""Real MV3 extension -> browser-launched native host; synthetic XHS pages only."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import shutil
import time
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from playwright.sync_api import sync_playwright, expect
from xhs_context.storage import Store
from xhs_context.core import status, search
from xhs_context.setup import extension_id, register_host, REGISTRY_ROOTS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var/validation"
OUT.mkdir(parents=True, exist_ok=True)


def prepare_host(work, host_name, extension, *, with_images=False):
    # Reuse the installed console launcher, replacing only its Python entry script.
    # This fixture transport exists only inside this isolated test installation.
    template = ROOT / ".venv/Scripts/xhs-context-native.exe"
    with ZipFile(template) as archive:
        prefix_size = min(info.header_offset for info in archive.infolist())
    executable = work / "fixture-host.exe"
    launcher = template.read_bytes()
    executable.write_bytes(launcher[:prefix_size])
    bootstrap = """
import json, os, sys
from pathlib import Path
from xhs_context.native import serve_native
from xhs_context.common import XhsError
from xhs_context.storage import Store
from xhs_context.sync_service import SyncSession
from xhs_context.worker import Worker
from xhs_context.images import ImageWorker
work = Path(WORK_PATH)
class FixtureTransport:
    def get(self, url, **kwargs):
        if kwargs.get('media'):
            if not IMAGES_ENABLED:
                raise AssertionError('The text prototype must not download media')
            with (work / 'media-requests.log').open('a', encoding='utf-8') as f:
                f.write(url + '\\n')
            return (work / 'fixture-image.png').read_bytes(), 'image/png', url
        identifier = url.split('?')[0].rstrip('/').split('/')[-1]
        if int(identifier, 16) >= 9000:
            raise XhsError('ACCESS_BLOCKED', 'Synthetic login wall')
        note = {'noteId': identifier, 'title': '虚拟列表合成收藏',
                'desc': '合成数据：容器部署与基础设施学习。不是实际小红书收藏。', 'imageList': []}
        html = '<script>window.__INITIAL_STATE__=' + json.dumps({'note': note}, ensure_ascii=False) + ';</script>'
        return html.encode(), 'text/html', url
def fixture_worker(store, **kwargs):
    worker = Worker(store, fetcher=FixtureTransport(), **kwargs)
    class FixtureOCR:
        def recognize(self, path):
            raise AssertionError('The text prototype must not run OCR')
    worker.ocr = FixtureOCR()
    return worker
def session(store):
    session = SyncSession(store, ocr=False, interval=.02, worker_factory=fixture_worker)
    session.image_worker = ImageWorker(store, fetcher=FixtureTransport(), stop_event=session.stop)
    return session
if os.name == 'nt':
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
source, sink = sys.stdin.buffer, sys.stdout.buffer
log = (work / 'host.log').open('a', encoding='utf-8')
sys.stdout = sys.stderr = log
(work / 'host-started.json').write_text(json.dumps({'pid': os.getpid()}))
result = serve_native(Store(work / 'library'), source, sink, session_factory=session)
(work / 'host-finished.json').write_text(json.dumps({'exit_code': result}))
log.close()
""".replace("WORK_PATH", repr(str(work))).replace('IMAGES_ENABLED', repr(with_images))
    with ZipFile(executable, "a", ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", bootstrap.encode("utf-8"))
    # uv launchers also keep the interpreter path after the ZIP end record.
    if launcher[-4:] in {b"UVSC", b"UVPY"}:
        trailer_size = int.from_bytes(launcher[-8:-4], "little") + 8
        with executable.open("ab") as stream:
            stream.write(launcher[-trailer_size:])
    manifest = work / "native-host.json"
    manifest.write_text(json.dumps({"name": host_name, "description": "Isolated synthetic test host",
        "path": str(executable), "type": "stdio",
        "allowed_origins": ["chrome-extension://" + extension_id(extension) + "/"]}), encoding="utf-8")
    return manifest


def unregister_test_host(host_name, manifest):
    import winreg
    for root in REGISTRY_ROOTS:
        key = root + "\\" + host_name
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                existing, _ = winreg.QueryValueEx(handle, "")
            # Only this run's unique registration may be removed.
            if Path(existing).resolve() != manifest.resolve():
                raise RuntimeError("Test host registration unexpectedly changed")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def main():
    if os.name != "nt":
        raise SystemExit("This native-host integration test currently requires Windows.")
    run_id = uuid.uuid4().hex[:12]
    work = ROOT / ".tmp" / ("native-browser-" + run_id)
    work.mkdir(parents=True)
    extension = work / "extension"
    shutil.copytree(ROOT / "browser-extension", extension)
    host_name = "org.xhs_context.test_" + run_id
    native_file = extension / "native.mjs"
    native_file.write_text(native_file.read_text(encoding="utf-8").replace("org.xhs_context.host", host_name), encoding="utf-8")
    manifest = prepare_host(work, host_name, extension)
    store = Store(work / "library")
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
    candidates = sorted(cache.glob("chromium-*/**/chrome.exe"), reverse=True)
    executable = os.environ.get("XHS_TEST_CHROMIUM") or (str(candidates[0]) if candidates else None)
    errors = []
    restriction_requests = []
    report = {"live_xhs": "NOT_RUN", "dataset": "synthetic_profile_favorites",
              "transport": "real_native_messaging", "expected_count": 60, "manual_pairing": False}
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(work / "browser-profile"), headless=True,
                executable_path=executable, args=["--host-resolver-rules=MAP * ~NOTFOUND", "--disable-extensions-except=" + str(extension),
                "--load-extension=" + str(extension)], ignore_default_args=["--disable-extensions"],
                viewport={"width": 1000, "height": 800})
            try:
                fixture = (ROOT / "tests/fixtures/profile-favorites.html").read_text(encoding="utf-8")
                def route_request(route):
                    url = route.request.url
                    if url.startswith("https://www.xiaohongshu.com/"):
                        if len(urlsplit(url).path.split('/')) == 5 and '/user/profile/' in url:
                            identifier = int(urlsplit(url).path.split('/')[-1], 16)
                            if identifier >= 9100:
                                restriction_requests.append(identifier)
                                route.fulfill(status=200, content_type="text/html",
                                              path=str(ROOT / "tests/fixtures/access-restricted.html"))
                                return
                            note_html = '''<!doctype html><html><body>
                                <div id="noteContainer"><h1 id="detail-title">合成浏览器笔记</h1>
                                <div id="detail-desc">浏览器正文：容器与基础设施入门。合成测试数据。</div>
                                <div class="note-slider"><div class="swiper-slide"><div class="note-slider-img">
                                <img src="https://sns-webpic-qc.xhscdn.com/fixture.png"></div></div></div>
                                <div class="comments">不应进入正文的评论</div></div></body></html>'''
                            if urlsplit(url).path.endswith(f"{9000:024x}"):
                                note_html = note_html.replace('<h1 id="detail-title">合成浏览器笔记</h1>', '')
                            route.fulfill(status=200, content_type="text/html", body=note_html)
                            return
                        body = fixture.replace("if(end===60){", "if(false){") if "folder_id=unknown" in url else fixture
                        if "folder_id=browser" in url:
                            body = body.replace('笔记・60', '笔记・3').replace('Math.min(60,', 'Math.min(3,')
                            body = body.replace('end===60', 'end===3').replace('(i+1000)', '(i+9000)')
                        if "folder_id=restricted" in url:
                            body = body.replace('笔记・60', '笔记・3').replace('Math.min(60,', 'Math.min(3,')
                            body = body.replace('end===60', 'end===3').replace('(i+1000)', '(i+9100)')
                        if "tab=posts" in url:
                            body = body.replace('class="reds-tab-item active sub-tab-list" data-fixture="saved"', 'class="reds-tab-item sub-tab-list" data-fixture="saved"')
                            body = body.replace('class="reds-tab-item sub-tab-list" data-fixture="posts"', 'class="reds-tab-item active sub-tab-list" data-fixture="posts"')
                        if "subTab=album" in url:
                            body = body.replace('class="reds-tab-item active" data-fixture="notes"', 'class="reds-tab-item" data-fixture="notes"')
                            body = body.replace('class="reds-tab-item" data-fixture="albums"', 'class="reds-tab-item active" data-fixture="albums"')
                        route.fulfill(status=200, content_type="text/html", body=body)
                    elif url.startswith("chrome-extension://"):
                        route.continue_()
                    elif url == "https://example.com/":
                        route.fulfill(status=200, content_type="text/html", body="<h1>Unrelated page</h1>")
                    elif url == "https://sns-webpic-qc.xhscdn.com/fixture.png":
                        route.fulfill(path=str(ROOT / "examples/ocr-card.png"), content_type="image/png")
                    else:
                        route.abort()
                context.route("**/*", route_request)
                sw = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                ext_id = sw.url.split("/")[2]
                target = context.new_page()
                target.goto("https://www.xiaohongshu.com/user/profile/111111111111111111111111?tab=fav&subTab=note")
                target.wait_for_load_state("networkidle")

                # Real XHS collapsed panes retain one pixel of padding. Their outer
                # rectangle is nonzero even though their content height is zero.
                collapsed_heights = target.locator('.tab-content-item[style*="height:0px"]').evaluate_all(
                    "panes => panes.map(p => p.getBoundingClientRect().height)")
                assert collapsed_heights and all(height == 1 for height in collapsed_heights)

                def open_popup(front=None):
                    popup = context.new_page()
                    popup.on("pageerror", lambda error: errors.append(str(error)))
                    popup.goto("chrome-extension://" + ext_id + "/popup.html")
                    popup.wait_for_load_state("networkidle")
                    (front or target).bring_to_front()
                    popup.locator("#refresh").evaluate("(e)=>e.click()")
                    return popup

                popup = open_popup()
                expect(popup.locator("#setup")).to_be_visible(timeout=15000)
                popup.locator("body").screenshot(path=str(OUT / "extension-setup.png"))
                report["missing_host_guidance"] = "PASS"
                register_host(manifest, host_name=host_name)
                popup.locator("#reconnect").evaluate("(e)=>e.click()")
                expect(popup.locator("#start")).to_be_enabled(timeout=20000)
                expect(popup.locator("#folder")).to_have_text("收藏 · 笔记")
                expect(popup.locator("#scope")).to_have_text("页面显示 60 篇笔记")
                expect(popup.locator("#name")).to_have_value("")
                expect(popup.locator("#expected")).to_have_value("")
                expect(popup.locator("#confirmation")).to_be_hidden()
                assert popup.locator("#advanced").get_attribute("open") is None
                assert (work / "host-started.json").exists()
                assert not (work / "library/pairing.json").exists()
                report["automatic_launch_and_metadata"] = "PASS"
                report["collapsed_panes_with_padding"] = "PASS"
                before = sw.evaluate("async () => {const [tab]=await chrome.tabs.query({active:true,currentWindow:true}); return chrome.tabs.sendMessage(tab.id,{type:'inspect'});}")
                target.locator(".profile-note-count").evaluate("e=>e.textContent='笔记・61'")
                after = sw.evaluate("async () => {const [tab]=await chrome.tabs.query({active:true,currentWindow:true}); return chrome.tabs.sendMessage(tab.id,{type:'inspect'});}")
                assert before["view_marker"] == after["view_marker"] and before["identity_marker"] == after["identity_marker"]
                target.locator(".profile-note-count").evaluate("e=>e.textContent='笔记・60'")
                report["count_change_preserves_identity"] = "PASS"
                popup.locator("body").screenshot(path=str(OUT / "extension-ready.png"))
                popup.locator("#start").evaluate("(e)=>e.click()")
                deadline = time.monotonic() + 25
                job_id = None
                while time.monotonic() < deadline:
                    jobs = status(store)["data"]["recent_jobs"]
                    if jobs and jobs[0]["discovered"] >= 18:
                        job_id = jobs[0]["id"]
                        break
                    target.wait_for_timeout(100)
                assert job_id, popup.locator("#message").inner_text()
                other = context.new_page()
                other.goto("https://example.com/")
                popup.close()
                popup = open_popup(other)
                expect(popup.locator("#activity")).to_be_visible()
                expect(popup.locator("#target-card")).to_be_hidden()
                expect(popup.locator("#pause")).to_be_visible(timeout=10000)
                popup.locator("#pause").evaluate("(e)=>e.click()")
                expect(popup.locator("#status-title")).to_have_text("已暂停", timeout=15000)
                popup.locator("#resume").evaluate("(e)=>e.click()")
                expect(popup.locator("#status-title")).not_to_have_text("已暂停", timeout=15000)
                popup.close()
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    report["job"] = status(store, job_id)["data"]["job"]
                    if report["job"]["body_complete"]:
                        break
                    target.wait_for_timeout(150)
                assert report["job"]["body_complete"] and not report["job"]["content_complete"], report["job"]
                assert report["job"]["discovered"] == 60 and report["job"]["expected_count"] == 60
                assert report["job"]["received_batches"] > 1
                with store.read() as db:
                    rows = db.execute("SELECT id,source_url FROM notes").fetchall()
                    assert len(rows) == 60
                    assert all("/user/profile/" in row["source_url"] and "xsec_token=fixture-only" in row["source_url"] for row in rows)
                    assert all(row["id"] not in {"f" * 24, "e" * 24} for row in rows)
                report["profile_links_and_active_feed_only"] = "PASS"
                report["pause_resume_and_popup_close"] = "PASS"
                report["progress_pause_resume_from_other_page"] = "PASS"
                popup = open_popup(other)
                expect(popup.locator("#status-title")).to_have_text("正文已读完，可供 Agent 使用", timeout=15000)
                popup.locator("#open-source").evaluate("(e)=>e.click()")
                expect(popup.locator("#target-card")).to_be_visible()
                expect(popup.locator("#start")).to_have_text("更新当前收藏夹")
                report["popup_text"] = popup.locator("#activity").inner_text()
                popup.locator("body").screenshot(path=str(OUT / "extension-popup.png"))

                target.goto("https://www.xiaohongshu.com/user/profile/fixture?tab=posts")
                target.wait_for_load_state("networkidle")
                target.bring_to_front()
                popup.locator("#refresh").evaluate("(e)=>e.click()")
                expect(popup.locator("#scope")).to_contain_text("请先切换")
                expect(popup.locator("#start")).to_be_disabled()
                report["wrong_tab_guard"] = "PASS"

                target.goto("https://www.xiaohongshu.com/user/profile/111111111111111111111111?tab=fav&subTab=album")
                target.wait_for_load_state("networkidle")
                target.bring_to_front()
                popup.locator("#refresh").evaluate("(e)=>e.click()")
                expect(popup.locator("#scope")).to_contain_text("收藏里的“笔记”")
                expect(popup.locator("#start")).to_be_disabled()
                report["album_scope_guard"] = "PASS"

                target.goto("https://www.xiaohongshu.com/user/profile/111111111111111111111111?tab=fav&subTab=note&folder_id=unknown")
                target.wait_for_load_state("networkidle")
                target.bring_to_front()
                popup.locator("#refresh").evaluate("(e)=>e.click()")
                expect(popup.locator("#start")).to_be_enabled()
                popup.locator("#start").evaluate("(e)=>e.click()")
                expect(popup.locator("#end-check")).to_be_visible(timeout=40000)
                unknown = status(store)["data"]["recent_jobs"][0]
                assert unknown["discovered"] == 60 and not unknown["list_complete"]
                popup.locator("body").screenshot(path=str(OUT / "extension-unconfirmed.png"))
                popup.locator("#partial").evaluate("(e)=>e.click()")
                expect(popup.locator("#status-title")).to_have_text("已读笔记正文已保存", timeout=15000)
                assert status(store, unknown["id"])["data"]["job"]["scan_state"] == "partial"
                report["unknown_end_keeps_partial"] = "PASS"

                target.goto("https://www.xiaohongshu.com/user/profile/111111111111111111111111?tab=fav&subTab=note&folder_id=browser")
                target.wait_for_load_state("networkidle")
                target.bring_to_front()
                popup.locator("#refresh").evaluate("(e)=>e.click()")
                expect(popup.locator("#start")).to_be_enabled()
                popup.locator("#start").evaluate("(e)=>e.click()")
                expect(popup.locator("#status-title")).to_have_text("已暂停", timeout=15000)
                expect(popup.locator("#failure-reason")).to_contain_text("登录或验证")
                popup.close()
                popup = open_popup(other)
                expect(popup.locator("#browser-start")).to_be_visible()
                # tabs.create's first navigation can precede Playwright's target
                # attachment. Allocate a real inactive tab, then navigate through
                # the attached Page so all test traffic stays inside our routes.
                sw.evaluate("""() => {
                    globalThis.fixtureNavigations = [];
                    const create = chrome.tabs.create.bind(chrome.tabs);
                    chrome.tabs.create = async options => {
                        fixtureNavigations.push(options.url);
                        return create({...options, url:'about:blank'});
                    };
                }""")
                def navigate_fixture(page):
                    url = sw.evaluate("fixtureNavigations.shift()")
                    assert url and '/user/profile/' in url
                    page.goto(url)
                context.on("page", navigate_fixture)
                popup.locator("#browser-start").evaluate("(e)=>e.click()")
                expect(popup.locator("#browser-help")).to_be_hidden()
                expect(popup.locator("#status-title")).to_have_text("正文已读完，可供 Agent 使用", timeout=30000)
                expect(popup.locator("#progress")).to_contain_text("正文已保存 3 篇")
                expect(popup.locator("#failure-reason")).to_be_hidden()
                assert "完整归档" not in popup.locator("#progress").inner_text()
                assert other.url == "https://example.com/" and not other.is_closed()
                with store.read() as db:
                    for number in range(9000, 9003):
                        payload = json.loads(db.execute("SELECT r.payload FROM revisions r JOIN notes n ON n.current_revision=r.id WHERE n.id=?",
                                                        (f"{number:024x}",)).fetchone()[0])
                        assert payload["content_source"] == "browser_dom"
                        assert "不应进入正文的评论" not in payload["body"]
                        assert payload["images"] == []
                        assert payload["content_scope"] == "body" and payload["processing_state"] == "ready"
                        assert payload["content_state"] == "body_only" and payload["gaps"] == ["MEDIA_DEFERRED"]
                report["browser_dom_fallback_from_other_page"] = "PASS"
                report["body_only_no_media_or_ocr"] = "PASS"
                report["browser_navigation"] = "real_inactive_tabs_with_fixture_navigation_after_attachment"
                popup.locator("body").screenshot(path=str(OUT / "extension-background-task.png"))
                report["query_results"] = len(search(store, {"queries": ["容器"], "limit": 50, "max_chars": 100000})["data"]["items"])
                assert report["query_results"] == 50
                # Simulated 300013 page: no real XHS request or retry is made.
                target.goto("https://www.xiaohongshu.com/user/profile/111111111111111111111111?tab=fav&subTab=note&folder_id=restricted")
                target.wait_for_load_state("networkidle")
                target.bring_to_front()
                popup.locator("#refresh").evaluate("(e)=>e.click()")
                expect(popup.locator("#start")).to_be_enabled()
                popup.locator("#start").evaluate("(e)=>e.click()")
                expect(popup.locator("#status-title")).to_have_text("已暂停", timeout=15000)
                expect(popup.locator("#browser-start")).to_be_visible()
                other.bring_to_front()
                popup.locator("#browser-start").evaluate("(e)=>e.click()")
                expect(popup.locator("#status-title")).to_have_text("访问受限，自动读取已停止", timeout=15000)
                expect(popup.locator("#resume")).to_be_disabled()
                expect(popup.locator("#message")).to_contain_text("300013")
                # A forged control message cannot bypass the disabled UI.
                rejected = popup.evaluate("""async () => {
                    const state = await chrome.runtime.sendMessage({type:'state'});
                    return chrome.runtime.sendMessage({type:'resume',job_id:state.data.session.job_id});
                }""")
                assert not rejected["ok"] and "ACCESS_RESTRICTED" in rejected["error"]
                popup.wait_for_timeout(5500)  # Allow another background pump, which must open no page.
                assert len(restriction_requests) == 1 and restriction_requests[0] in range(9100, 9103), restriction_requests
                locked_status = status(store)["data"]
                assert locked_status["access_restriction"]["site_code"] == "300013"
                assert all(job["state"] == "paused" for job in locked_status["recent_jobs"])
                popup.locator("body").screenshot(path=str(OUT / "extension-security-restriction.png"))
                report["security_restriction_stops_all_jobs_and_disables_resume"] = "PASS"
                report["restriction_fixture_note_requests"] = restriction_requests
                assert not errors
                report["page_errors"] = errors
                report["browser_version"] = context.browser.version
                report["status"] = "PASS"
            except Exception:
                if "popup" in locals() and not popup.is_closed():
                    report["failure_ui"] = popup.locator("body").inner_text()
                    popup.locator("body").screenshot(path=str(OUT / "extension-failure.png"))
                report["failure_tabs"] = sw.evaluate("""async () => {
                    const results = [];
                    for (const tab of await chrome.tabs.query({})) {
                        let response;
                        try { response = await chrome.tabs.sendMessage(tab.id, {type:'read-note', note_id:tab.url?.split('?')[0].split('/').pop()}); }
                        catch(error) { response = String(error); }
                        results.push({url:tab.url, status:tab.status, response});
                    }
                    return results;
                }""")
                raise
            finally:
                context.close()
        deadline = time.monotonic() + 30
        while not (work / "host-finished.json").exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert (work / "host-finished.json").exists(), "Native host did not stop after browser exit"
        report["host_stops_with_browser"] = "PASS"
        host_log = (work / "host.log").read_text(encoding="utf-8")
        assert "sync worker error:" not in host_log, host_log
    except Exception as exc:
        report["status"], report["error"] = "FAIL", str(exc)
    finally:
        unregister_test_host(host_name, manifest)
    (OUT / "browser-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(OUT / "browser-smoke.json"),
                      "error": report.get("error"), "discovered": report.get("job", {}).get("discovered")}, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

