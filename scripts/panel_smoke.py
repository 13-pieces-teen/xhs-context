"""Popup rendering and interaction checks with synthetic state, no user account."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var/panel-design"
OUT.mkdir(parents=True, exist_ok=True)
PAGE = {"name": "收藏 · 笔记", "scope": "saved", "expected_count": 60,
        "page_key": "https://www.xiaohongshu.com/user/profile/fixture?tab=fav",
        "identity_marker": "fixture", "view_marker": "fav-note"}
SESSION = {"job_id": "job-1", "name": "收藏 · 笔记", "finalized": False, "scan_state": "scanning"}
JOB = {"state": "running", "discovered": 36, "body_available": 24, "expected_count": 60,
       "items": {"ready": 24, "queued": 11, "running": 1}, "body_complete": False,
       "list_complete": False, "failure_summary": [], "content_mode": "anonymous"}

def state(*, job=None, session=None, page=PAGE, connection_error=None):
    return copy.deepcopy({"page": page, "page_error": "请在小红书打开收藏 → 笔记。",
        "page_session": session if page else None, "session": session,
        "server_status": {"job": job} if job else None, "connection_error": connection_error})

def main():
    errors = []
    checks = []
    screenshots = {}
    with sync_playwright() as p:
        cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
        candidates = sorted(cache.glob("chromium-*/**/chrome.exe"), reverse=True)
        executable = os.environ.get("XHS_TEST_CHROMIUM") or (str(candidates[0]) if candidates else None)
        browser = p.chromium.launch(headless=True, executable_path=executable, ignore_default_args=["--hide-scrollbars"])
        page = browser.new_page(viewport={"width": 400, "height": 600}, device_scale_factor=2)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
          window.__fixture = %s;
          window.__messages = [];
          window.chrome = window.chrome || {};
          window.chrome.runtime = {sendMessage: async (message) => {
            window.__messages.push(message);
            if (message.type === "state") {
              if (window.__delayNextState) {
                window.__delayNextState = false;
                return new Promise(resolve => window.__resolveOldState = resolve);
              }
              return {ok: true, data: structuredClone(window.__fixture)};
            }
            if (window.__holdAction) return new Promise(resolve => window.__resolveAction = resolve);
            return {ok: true, data: {job_id: "job-1"}};
          }};
        """ % json.dumps(state(), ensure_ascii=False))
        page.goto((ROOT / "browser-extension/popup.html").as_uri())
        page.wait_for_load_state("networkidle")
        def show(name, value):
            page.evaluate("async value => {window.__fixture=value; await refresh(); document.querySelector('main').scrollTop=0;}", value)
            page.evaluate("document.fonts.ready")
            page.locator("body").screenshot(path=str(OUT / (name + ".png")), animations="disabled")
            bounds = page.evaluate("""() => ({
              width: document.body.getBoundingClientRect().width,
              height: document.body.getBoundingClientRect().height,
              overflow: document.querySelector('main').scrollWidth > document.querySelector('main').clientWidth
            })""")
            assert bounds["width"] == 400 and bounds["height"] <= 600 and not bounds["overflow"], (name, bounds)
            screenshots[name] = bounds
        show("ready", state())
        expect(page.locator("#start")).to_be_enabled()
        page.locator("#advanced > summary").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#name")).to_be_visible()
        page.locator("#name").fill("我修正的收藏")
        page.locator("#expected").fill("65")
        page.evaluate("refresh()")
        expect(page.locator("#name")).to_have_value("我修正的收藏")
        expect(page.locator("#expected")).to_have_value("65")
        page.locator("body").screenshot(path=str(OUT / "options.png"), animations="disabled")
        page.locator("#start").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='start')") == {
            "type": "start", "name": "我修正的收藏", "confirmed": False, "expected_count": 65}
        page.locator("#expected").fill("1.5")
        before = page.evaluate("window.__messages.filter(m=>m.type==='start').length")
        page.locator("#start").click()
        assert page.evaluate("window.__messages.filter(m=>m.type==='start').length") == before
        checks += ["keyboard_disclosure", "preserve_typed_options", "start_payload", "reject_fractional_total"]
        page.locator("#expected").fill("")
        page.locator("#advanced > summary").click()

        unknown = state(page={**PAGE, "scope": "unknown"})
        show("unknown-scope", unknown)
        expect(page.locator("#start")).to_be_disabled()
        page.locator("#confirmed").check()
        expect(page.locator("#start")).to_be_enabled()
        show("other-unknown-scope", state(page={**PAGE, "scope": "unknown", "page_key": "https://www.xiaohongshu.com/other"}))
        expect(page.locator("#confirmed")).not_to_be_checked()
        expect(page.locator("#start")).to_be_disabled()
        checks.append("confirmation_cannot_leak_between_sources")

        running = state(job=JOB, session=SESSION)
        show("running", running)
        expect(page.locator("#target-card")).to_be_hidden()
        expect(page.locator("#browser-help")).to_be_hidden()
        expect(page.locator("#pause")).to_be_visible()
        expect(page.locator("#pause")).to_be_in_viewport(ratio=1)
        expect(page.locator("#list-meter")).to_have_attribute("aria-valuenow", "60")
        expect(page.locator("#body-meter")).to_have_attribute("aria-valuenow", "67")
        expect(page.locator("#list-state")).to_contain_text("尚未确认")
        checks.append("separate_list_and_body_progress")

        unknown_total = state(job={**JOB, "expected_count": None}, session=SESSION)
        show("unknown-total", unknown_total)
        assert page.locator("#list-meter").get_attribute("aria-valuenow") is None
        expect(page.locator("#list-state")).to_contain_text("总数未知")
        checks.append("unknown_total_has_no_percentage")

        paused = state(job={**JOB, "state": "paused"}, session={**SESSION, "scan_state": "paused"})
        show("paused", paused)
        expect(page.locator("#pause")).to_be_hidden()
        expect(page.locator("#resume")).to_be_visible()
        expect(page.locator("#end-check")).to_be_hidden()
        expect(page.locator("#browser-help")).to_be_hidden()
        expect(page.locator("#resume")).to_be_in_viewport(ratio=1)
        show("running", running)
        page.evaluate("() => {window.__delayNextState=true; void refresh();}")
        page.wait_for_function("typeof window.__resolveOldState === 'function'")
        page.evaluate("() => {window.__holdAction=true;}")
        page.locator("#pause").click()
        expect(page.locator("#pause")).to_be_disabled()
        page.evaluate("value => {window.__fixture=value; window.__resolveOldState({ok:true,data:value});}", paused)
        expect(page.locator("#status-title")).not_to_have_text("已暂停")
        page.evaluate("() => {window.__holdAction=false; window.__resolveAction({ok:true,data:{}});}")
        expect(page.locator("#status-title")).to_have_text("已暂停")
        checks.append("stale_poll_does_not_overwrite_in_flight_action")

        retrying = state(job={**JOB, 'state': 'active', 'content_mode': 'browser',
                             'browser_retry': {'error': 'BROWSER_BUSY', 'retry_at': 9999999999999, 'attempts': 1, 'max_attempts': 3}},
                         session={**SESSION, 'finalized': True, 'scan_state': 'partial'})
        show('automatic-retry', retrying)
        expect(page.locator('#status-title')).to_have_text('暂时中断，将自动继续')
        expect(page.locator('#action-note')).to_contain_text('无需点击继续')
        expect(page.locator('#pause')).to_be_visible()
        retrying['server_status']['job']['state'] = 'paused'
        show('paused-during-retry', retrying)
        expect(page.locator('#status-title')).to_have_text('已暂停')
        expect(page.locator('#action-note')).not_to_contain_text('自动重试')
        checks.append('auto_retry_message_never_overrides_manual_pause')

        awaiting = state(job={**JOB, "discovered": 48, "body_available": 48, "items": {"ready": 48}, "expected_count": None},
                         session={**SESSION, "scan_state": "awaiting_confirmation"})
        show("awaiting-confirmation", awaiting)
        expect(page.locator("#body-meter")).to_have_attribute("aria-valuenow", "100")
        expect(page.locator("#list-state")).to_contain_text("待确认")
        page.locator("#finish").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='finish')")["verified"] is True
        page.locator("#partial").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='finish')")["verified"] is False
        checks.append("end_confirmation_and_partial_save_are_distinct")

        blocked_job = {**JOB, "state": "paused", "discovered": 60, "body_available": 48,
                       "items": {"ready": 48, "partial": 12}, "list_complete": True,
                       "error": "ACCESS_BLOCKED", "failure_summary": [{"count":12, "error":"ACCESS_BLOCKED"}]}
        show("blocked", state(job=blocked_job, session={**SESSION, "finalized": True, "scan_state": "finished"}))
        expect(page.locator("#browser-start")).to_be_visible()
        expect(page.locator("#browser-start")).to_be_in_viewport(ratio=1)
        expect(page.locator("#recovery-title")).to_have_text("网页要求登录或验证")
        page.locator("#browser-start").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='browser-start')")["job_id"] == "job-1"
        page.locator("#detail-summary").click()
        expect(page.locator("#failure-reason")).to_contain_text("登录或验证")
        page.locator("#detail-summary").click()
        checks.append("browser_recovery_and_failure_reason")

        complete = state(job={**JOB, "discovered":60,"body_available":60,"items":{"ready":60},"list_complete":True,"body_complete":True},
                         session={**SESSION,"finalized":True,"scan_state":"finished"})
        show("complete", complete)
        expect(page.locator("#status-title")).to_have_text("正文已读完，可供 Agent 使用")
        expect(page.locator("#list-state")).to_have_text("列表已收齐")
        expect(page.locator("#start")).to_have_text("更新当前收藏夹")
        partial = copy.deepcopy(awaiting)
        partial["session"].update(finalized=True, scan_state="partial")
        partial["page_session"] = partial["session"]
        show("partial", partial)
        expect(page.locator("#status-title")).to_have_text("已读笔记正文已保存")
        expect(page.locator("#list-state")).to_contain_text("待确认")
        checks.append("partial_save_never_claims_collection_complete")

        show("disconnected", state(session=SESSION, connection_error="NETWORK_ERROR"))
        expect(page.locator("#status-title")).to_have_text("本机连接中断")
        expect(page.locator("#body-value")).to_have_text("—")
        expect(page.locator("#message")).to_be_visible()
        show("ready", state())
        expect(page.locator("#message")).to_be_hidden()
        checks.append("disconnect_does_not_show_stale_completion")

        show("setup", state(connection_error="HOST_NOT_INSTALLED"))
        expect(page.locator("#setup")).to_be_visible()
        expect(page.locator("#target-card")).to_be_hidden()
        show("empty", state(page=None))
        expect(page.locator("#inspect")).to_be_visible()
        show("long-name", state(page={**PAGE,"name":"很长的收藏夹名称" * 28,"expected_count":100000}))
        checks.append("long_name_without_horizontal_overflow")

        page.emulate_media(reduced_motion="reduce")
        show("reduced-motion", running)
        assert page.locator("#body-fill").evaluate("(e)=>getComputedStyle(e).transitionDuration") == "0s"
        show("reduced-motion-paused", paused)
        assert page.locator("#status-title").evaluate("(e)=>e.getAnimations().length") == 0
        checks.append("reduced_motion")
        assert not errors, errors
        preview = OUT / "preview.html"
        preview.write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <title>收藏上下文 · 面板预览</title><style>
        *{box-sizing:border-box}body{margin:0;padding:32px;width:1312px;background:#f1e8e3;color:#352a27;font-family:"Microsoft YaHei",sans-serif}
        header{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:28px}h1{font-size:26px;margin:0 0 8px}p{font-size:13px;color:#74615b;margin:0}.tag{padding:8px 12px;background:#fcf7f5;border-radius:8px;font-size:12px}
        main{display:grid;grid-template-columns:repeat(3,400px);gap:24px}h2{font-size:14px;font-weight:500;margin:0 0 12px}h2 span{font-size:11px;color:#74615b;margin-right:10px}img{display:block;width:400px;height:auto;border:1px solid #eadbd4;border-radius:12px}footer{margin-top:20px;font-size:11px;color:#74615b}
        </style><header><div><h1>收藏上下文 · Chrome 面板</h1><p>清楚知道读到了哪里，以及下一步可以做什么。</p></div><div class="tag">实际 HTML 界面 · 合成数据</div></header>
        <main><section><h2><span>01</span>准备读取</h2><img src="ready.png" alt="准备读取"></section><section><h2><span>02</span>后台读取</h2><img src="running.png" alt="后台读取"></section><section><h2><span>03</span>登录与补全</h2><img src="blocked.png" alt="登录与补全"></section></main>
        <footer>400px 面板 · 列表与正文分别报告 · 低频选项折叠 · 支持减少动态效果。此预览不代表真实收藏读取结果，也不是 Figma 截图。</footer></html>''', encoding="utf-8")
        gallery = browser.new_page(viewport={"width":1312,"height":800}, device_scale_factor=1)
        gallery.goto(preview.as_uri())
        gallery.wait_for_load_state("networkidle")
        gallery.screenshot(path=str(OUT / "overview.png"), full_page=True, animations="disabled")
        browser.close()
    report = {"checks": checks, "screenshots": screenshots, "errors":errors,
              "dataset":"synthetic_popup_states", "live_xhs":"NOT_RUN"}
    (OUT / "report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":len(checks),"screenshots":len(screenshots),"errors":errors},ensure_ascii=False))
if __name__ == "__main__":
    main()
