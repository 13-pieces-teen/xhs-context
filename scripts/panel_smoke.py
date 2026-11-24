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
    remote_requests = []
    checks = []
    screenshots = {}
    with sync_playwright() as p:
        cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
        candidates = sorted(cache.glob("chromium-*/**/chrome.exe"), reverse=True)
        executable = os.environ.get("XHS_TEST_CHROMIUM") or (str(candidates[0]) if candidates else None)
        browser = p.chromium.launch(headless=True, executable_path=executable, ignore_default_args=["--hide-scrollbars"])
        page = browser.new_page(viewport={"width": 360, "height": 600}, device_scale_factor=2)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: remote_requests.append(request.url) if request.url.startswith(("http://", "https://")) else None)
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
            if (message.type === 'invalid-notes') {
              const items = window.__invalidItems || [];
              const offset = message.offset || 0;
              return {ok: true, data: {total: items.length, items: items.slice(offset, offset + 50), has_more: offset + 50 < items.length}};
            }
            if (window.__holdAction) return new Promise(resolve => window.__resolveAction = resolve);
            if (window.__nextActionState?.[message.type]) {
              window.__fixture = structuredClone(window.__nextActionState[message.type]);
              delete window.__nextActionState[message.type];
            }
            return {ok: true, data: {job_id: "job-1"}};
          }};
        """ % json.dumps(state(), ensure_ascii=False))
        page.goto((ROOT / "browser-extension/popup.html").as_uri())
        page.wait_for_load_state("networkidle")
        def settle_sheet():
            page.evaluate("async () => { await Promise.allSettled(document.getElementById('sheet').getAnimations().map(a => a.finished)); }")

        def close_sheet():
            page.keyboard.press("Escape")
            expect(page.locator("#sheet-layer")).to_be_hidden()
            assert not page.locator("#app-content").evaluate("e => e.inert")

        def show(name, value):
            page.evaluate("async value => {window.__fixture=value; await refresh(); document.querySelector('main').scrollTop=0;}", value)
            if page.locator("#message.info").is_visible():
                expect(page.locator("#message.info")).to_be_hidden(timeout=5000)
            page.evaluate("document.fonts.ready")
            page.locator("body").screenshot(path=str(OUT / (name + ".png")), animations="disabled")
            bounds = page.evaluate("""() => ({
              width: document.body.getBoundingClientRect().width,
              height: document.body.getBoundingClientRect().height,
              overflow: document.querySelector('main').scrollWidth > document.querySelector('main').clientWidth
            })""")
            assert bounds["width"] == 360 and bounds["height"] <= 600 and not bounds["overflow"], (name, bounds)
            screenshots[name] = bounds
            print("Rendered " + name, flush=True)

        def first_screen(*ids):
            """Check the unobscured area above the floating toolbar."""
            page.evaluate("document.querySelector('main').scrollTop=0")
            bounds = page.locator("main").bounding_box()
            dock = page.locator("footer").bounding_box()
            bottom = min(bounds["y"] + bounds["height"], dock["y"] - 4)
            for id in ids:
                control = page.locator("#" + id)
                expect(control).to_be_visible()
                box = control.bounding_box()
                assert box["y"] >= bounds["y"] - 1 and box["y"] + box["height"] <= bottom + 1, (id, box, bounds, dock)

        def reachable(*ids):
            """Secondary task controls remain reachable when dense content scrolls."""
            for id in ids:
                control = page.locator("#" + id)
                expect(control).to_be_visible()
                control.evaluate("e => e.scrollIntoView({block:'center'})")
                box, dock = control.bounding_box(), page.locator("footer").bounding_box()
                bounds = page.locator("main").bounding_box()
                assert box["y"] >= bounds["y"] - 1 and box["y"] + box["height"] <= dock["y"] - 3, (id, box, dock)

        def rings_in_one_row(*ids):
            boxes = [page.locator("#" + id + "-meter").bounding_box() for id in ids]
            assert all(box is not None for box in boxes), boxes
            assert max(box["y"] for box in boxes) - min(box["y"] for box in boxes) <= 1, boxes
            for left, right in zip(boxes, boxes[1:]):
                assert left["x"] + left["width"] <= right["x"], boxes

        def add_layout_metric():
            """Synthetic future metric: tests space only, never claims real image coverage."""
            page.evaluate("""() => {
              document.getElementById('extra-metric')?.remove();
              const metric = document.getElementById('list-meter').closest('.metric').cloneNode(true);
              metric.id = 'extra-metric';
              for (const node of metric.querySelectorAll('[id]')) node.id = node.id.replace('list-', 'extra-');
              metric.querySelector('.metric-label').textContent = '测试指标';
              metric.querySelector('#extra-meter').setAttribute('aria-label', '布局测试指标');
              document.querySelector('.progress-card').append(metric);
            }""")

        show("ready", state())
        assert page.locator("#start").evaluate("e => !!getComputedStyle(e).getPropertyValue('--btn-bg')"), "daisyUI button stylesheet did not load"
        expect(page.locator("#start")).to_be_enabled()
        first_screen("start")
        page.locator("#settings-toggle").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#sheet-close")).to_be_focused()
        assert page.locator("#app-content").evaluate("e => e.inert")
        page.keyboard.press("Tab")
        expect(page.locator("#name")).to_be_focused()
        expect(page.locator("#name")).to_be_visible()
        assert page.locator("#name").evaluate("e => getComputedStyle(e).appearance === 'none' && !!getComputedStyle(e).getPropertyValue('--input-color')"), "daisyUI input stylesheet did not load"
        page.locator("#name").fill("我修正的收藏")
        page.locator("#expected").fill("65")
        page.evaluate("refresh()")
        expect(page.locator("#name")).to_have_value("我修正的收藏")
        expect(page.locator("#expected")).to_have_value("65")
        settle_sheet()
        page.locator("body").screenshot(path=str(OUT / "options.png"), animations="disabled")
        page.locator("#sheet-done").click()
        expect(page.locator("#sheet-layer")).to_be_hidden()
        page.locator("#start").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='start')") == {
            "type": "start", "name": "我修正的收藏", "confirmed": False, "expected_count": 65}
        page.locator("#settings-toggle").click()
        page.locator("#expected").fill("1.5")
        before = page.evaluate("window.__messages.filter(m=>m.type==='start').length")
        page.locator("#sheet-done").click()
        expect(page.locator("#advanced")).to_be_visible()
        expect(page.locator("#expected")).to_be_focused()
        expect(page.locator("#expected")).to_have_attribute("aria-invalid", "true")
        expect(page.locator("#sheet-error")).to_contain_text("整数")
        assert page.evaluate("window.__messages.filter(m=>m.type==='start').length") == before
        checks += ["keyboard_disclosure", "preserve_typed_options", "start_payload", "reject_fractional_total"]
        page.locator("#expected").fill("")
        close_sheet()
        expect(page.locator("#advanced")).to_be_hidden()
        expect(page.locator("#settings-toggle")).to_be_focused()
        expect(page.locator("#settings-toggle")).to_have_attribute("aria-expanded", "false")
        page.locator("#refresh").focus()
        for _ in range(6):
            page.keyboard.press("Tab")
            assert not page.locator("#advanced").evaluate("e => e.contains(document.activeElement)"), "Closed settings must not receive keyboard focus"
        checks.append("closing_settings_returns_focus_and_excludes_hidden_fields_from_tab_order")
        page.locator("#refresh").click()
        page.locator("#help-toggle").click()
        expect(page.locator("#help-panel")).to_be_visible()
        expect(page.locator("#help-panel")).to_contain_text("OCR")
        settle_sheet()
        page.locator("body").screenshot(path=str(OUT / "help.png"), animations="disabled")
        close_sheet()
        expect(page.locator("#help-panel")).to_be_hidden()
        checks.append("footer_settings_and_help_are_keyboard_accessible")

        page.locator("#settings-toggle").click()
        expect(page.locator("#sheet-close")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(page.locator("#sheet-done")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator("#sheet-close")).to_be_focused()
        close_sheet()
        page.locator("#help-toggle").click()
        page.evaluate("() => { popupView.close(); popupView.open('help-panel', {trigger:document.getElementById('help-toggle')}); }")
        settle_sheet()
        expect(page.locator("#sheet-layer")).to_be_visible()
        expect(page.locator("#help-panel")).to_be_visible()
        close_sheet()
        expect(page.locator("#help-toggle")).to_be_focused()
        checks.append("sheet_focus_trap_and_rapid_reopen")

        page.locator("#help-toggle").click()
        page.locator('[data-appearance-choice="dark"]').click()
        expect(page.locator("body")).to_have_attribute("data-appearance", "dark")
        page.locator("#glass-opacity").evaluate("e => {e.value=95; e.dispatchEvent(new Event('input',{bubbles:true}));}")
        page.locator("#reduce-effects").check()
        expect(page.locator("body")).to_have_attribute("data-reduce-effects", "true")
        close_sheet()
        show("dark-ready", state())
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.locator("body")).to_have_attribute("data-appearance", "dark")
        expect(page.locator("body")).to_have_attribute("data-reduce-effects", "true")
        page.locator("#help-toggle").click()
        expect(page.locator("#glass-opacity")).to_have_value("95")
        expect(page.locator("#reduce-effects")).to_be_checked()
        page.locator('[data-appearance-choice="system"]').click()
        page.locator("#reduce-effects").uncheck()
        page.locator("#glass-opacity").evaluate("e => {e.value=72; e.dispatchEvent(new Event('input',{bubbles:true}));}")
        close_sheet()
        checks.append("appearance_glass_and_motion_preferences_persist")

        page.locator("#settings-toggle").click()
        page.evaluate("value => {window.__fixture=value; return refresh();}", state(job=JOB, session=SESSION))
        expect(page.locator("#sheet-layer")).to_be_hidden()
        assert not page.locator("#app-content").evaluate("e => e.inert")
        show("ready-after-task-transition", state())
        page.locator("#settings-toggle").click()
        page.evaluate("value => {window.__fixture=value; return refresh();}", state(page={**PAGE, "page_key":"another-collection"}))
        expect(page.locator("#sheet-layer")).to_be_hidden()
        assert not page.locator("#app-content").evaluate("e => e.inert")
        checks.append("settings_close_when_task_or_source_changes")

        page.evaluate("value => {window.__fixture=value; return refresh();}", state(job=JOB, session=SESSION))
        page.locator("#details-toggle").click()
        page.evaluate("value => {window.__fixture=value; return refresh();}", state())
        expect(page.locator("#sheet-layer")).to_be_hidden()
        assert page.evaluate("!document.activeElement.disabled && !document.activeElement.closest('[inert]') && document.activeElement.getClientRects().length > 0"), "A vanished task must return focus to an available control"
        checks.append("vanished_task_sheet_restores_available_focus")

        unknown = state(page={**PAGE, "scope": "unknown"})
        show("unknown-scope", unknown)
        expect(page.locator("#start")).to_be_disabled()
        assert page.locator("#confirmed").evaluate("e => getComputedStyle(e).appearance === 'none' && getComputedStyle(e, '::before').clipPath !== 'none'"), "daisyUI checkbox stylesheet did not load"
        page.locator("#confirmed").focus()
        page.keyboard.press("Space")
        expect(page.locator("#confirmed")).to_be_checked()
        expect(page.locator("#start")).to_be_enabled()
        page.keyboard.press("Space")
        expect(page.locator("#confirmed")).not_to_be_checked()
        expect(page.locator("#start")).to_be_disabled()
        page.keyboard.press("Space")
        expect(page.locator("#start")).to_be_enabled()
        checks.append("confirmation_checkbox_retains_native_keyboard_toggle")
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
        expect(page.locator("#list-percent")).to_have_text("60%")
        expect(page.locator("#body-percent")).to_have_text("67%")
        assert float(page.locator("#list-meter").evaluate("node => node.style.getPropertyValue('--value')")) == 60
        rings_in_one_row("list", "body")
        assert page.locator("#list-meter").evaluate("e => getComputedStyle(e, '::before').maskImage !== 'none'"), "daisyUI radial progress stylesheet did not load"
        first_screen("pause", "open-source", "browser-start", "images-current", "images-collection")
        expect(page.locator("#browser-start")).to_be_disabled()
        assert page.locator("#browser-start").evaluate("e => getComputedStyle(e).pointerEvents") == "auto", "Disabled controls must retain their reason tooltip"
        expect(page.locator("#images-current")).to_be_disabled()
        page.locator("#details-toggle").click()
        expect(page.locator("#detail")).to_be_visible()
        close_sheet()
        expect(page.locator("#detail")).to_be_hidden()
        checks.append("separate_list_and_body_progress")
        checks.append("parallel_rings_and_main_actions_fit_first_screen")

        unknown_total = state(job={**JOB, "expected_count": None}, session=SESSION)
        show("unknown-total", unknown_total)
        assert page.locator("#list-meter").get_attribute("aria-valuenow") is None
        expect(page.locator("#list-state")).to_contain_text("总数未知")
        expect(page.locator("#list-percent")).to_have_text("—")
        expect(page.locator("#list-value")).to_have_text("36 篇")
        checks.append("unknown_total_has_no_percentage")

        unconfirmed_full_count = state(job={**JOB, "discovered": 60, "body_available": 60,
                                           "items": {"ready": 60}}, session=SESSION)
        show("full-count-still-unconfirmed", unconfirmed_full_count)
        expect(page.locator("#list-percent")).to_have_text("100%")
        expect(page.locator("#list-state")).to_contain_text("尚未确认")
        expect(page.locator("#body-state")).to_have_text("已发现正文已保存")
        checks.append("a_full_count_ring_does_not_claim_the_collection_was_verified")

        image_job = {"job_id": "images-1", "state": "active", "images_saved": 24, "notes": 12,
                     "items": {"ready": 3, "queued": 9}, "complete": False, "failures": []}
        with_images = state(job=JOB, session=SESSION)
        with_images["server_status"]["image_jobs"] = [image_job]
        with_images["note"] = {"note_id": "000000000000000000000001"}
        show("independent-image-progress", with_images)
        rings_in_one_row("list", "body")
        expect(page.locator("#image-progress")).to_contain_text("24 张")
        first_screen("pause", "open-source", "images-current", "images-collection")
        reachable("images-pause")
        page.locator("#image-details summary").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#image-detail")).to_be_visible()
        page.locator("#image-details").evaluate("async e => { await Promise.all(e.getAnimations({subtree:true}).map(a => a.finished.catch(() => {}))); }")
        page.locator("#image-details").scroll_into_view_if_needed()
        page.evaluate("() => { const main = document.querySelector('main'); main.scrollTop = main.scrollHeight; }")
        page.locator("body").screenshot(path=str(OUT / "image-details-expanded.png"), animations="disabled")
        page.keyboard.press("Space")
        expect(page.locator("#image-detail")).to_be_hidden()
        expect(page.locator("#image-details summary")).to_be_focused()
        checks.append("image_details_retains_native_keyboard_disclosure")
        image_paused = copy.deepcopy(with_images)
        image_paused["server_status"]["image_jobs"][0]["state"] = "paused"
        page.evaluate("value => {window.__nextActionState = {'images-pause': value};}", image_paused)
        page.locator("#images-pause").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='images-pause')")["job_id"] == "images-1"
        expect(page.locator("#images-resume")).to_be_focused()
        show("image-paused", image_paused)
        page.evaluate("value => {window.__nextActionState = {'images-resume': value};}", with_images)
        page.locator("#images-resume").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='images-resume')")["job_id"] == "images-1"
        expect(page.locator("#images-pause")).to_be_focused()
        image_incomplete = copy.deepcopy(with_images)
        image_incomplete["server_status"]["image_jobs"][0].update(state="finished", notes=3, items={"ready": 3})
        show("image-awaiting-completeness", image_incomplete)
        expect(page.locator("#image-progress")).to_contain_text("待补全")
        expect(page.locator("#images-resume")).to_be_visible()
        checks.append("image_progress_and_controls_remain_independent_of_body_task")

        image_downloading = copy.deepcopy(with_images)
        image_downloading['server_status']['image_jobs'][0].update(images_saved=1, notes=1, items={'running': 1},
            progress={'downloading': 1, 'current_saved_images': 1, 'current_expected_images': 2})
        show('image-downloading-checkpoint', image_downloading)
        expect(page.locator('#image-progress')).to_contain_text('已保存 1 张')
        expect(page.locator('#image-stage')).to_contain_text('当前笔记已保存 1 / 2 张')
        checks.append('image_checkpoint_is_visible_before_note_completion')

        image_repair = copy.deepcopy(image_incomplete)
        image_repair['server_status']['image_jobs'][0].update(needs_repair=1, progress={'already_complete': 2})
        show('image-local-file-repair', image_repair)
        expect(page.locator('#images-resume')).to_be_visible()
        page.locator('#image-details summary').click()
        expect(page.locator('#image-detail')).to_contain_text('2 / 3 篇图片齐全')
        expect(page.locator('#image-detail')).to_contain_text('1 篇本地图片缺失')
        checks.append('missing_local_image_exposes_repair_action')
        page.locator('#image-details summary').click()
        expect(page.locator('#image-detail')).to_be_hidden()

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
        expect(page.locator('#status-title')).to_have_text('等待自动重试')
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
        first_screen("resume", "open-source", "browser-start", "images-current", "images-collection")
        expect(page.locator("#recovery-title")).to_have_text("网页要求登录或验证")
        page.locator("#details-toggle").click()
        expect(page.locator("#failure-reason")).to_contain_text("登录或验证")
        close_sheet()
        browser_reading = state(job={**JOB, "content_mode": "browser", "discovered": 60, "body_available": 48,
                                     "items": {"ready": 48, "browser_running": 1, "queued": 11}},
                                session={**SESSION, "finalized": True, "scan_state": "finished"})
        page.evaluate("value => {window.__nextActionState = {'browser-start': value};}", browser_reading)
        page.locator("#browser-start").click()
        assert page.evaluate("window.__messages.findLast(m=>m.type==='browser-start')")["job_id"] == "job-1"
        expect(page.locator("#browser-start")).to_be_disabled()
        expect(page.locator("#pause")).to_be_focused()
        checks.append("browser_recovery_and_failure_reason")
        checks.append("focus_follows_the_same_task_when_an_action_becomes_unavailable")

        awaiting_with_failure = state(job={**blocked_job, "list_complete": False},
                                      session={**SESSION, "scan_state": "awaiting_confirmation"})
        show("awaiting-with-body-failure", awaiting_with_failure)
        first_screen("finish", "partial", "browser-start")
        expect(page.locator("#finish")).to_be_enabled()
        expect(page.locator("#browser-start")).to_be_enabled()
        primary_buttons = page.locator("main button.btn-primary:visible:not(:disabled)").evaluate_all(
            "nodes => nodes.map(node => node.id)")
        assert primary_buttons == ["finish"], primary_buttons
        checks.append("end_confirmation_has_one_primary_action_when_bodies_need_recovery")

        restricted = state(job=blocked_job, session={**SESSION, "finalized": True, "scan_state": "finished"})
        restricted["server_status"].update(access_restriction={"error": "ACCESS_RESTRICTED", "site_code": "300013"},
                                          image_jobs=[{**image_job, "state": "paused"}])
        restricted["note"] = with_images["note"]
        show("restricted", restricted)
        expect(page.locator("#message")).to_be_visible()
        first_screen("message")
        expect(page.locator("#refresh")).to_be_in_viewport(ratio=1)
        for id in ["start", "resume", "browser-start", "images-current", "images-collection", "images-resume"]:
            control = page.locator("#" + id)
            assert not control.is_visible() or not control.is_enabled(), id
        before = len(page.evaluate("window.__messages"))
        page.locator("#refresh").click()
        sent = page.evaluate("window.__messages")[before:]
        assert any(message["type"] == "refresh-access" for message in sent)
        assert not any(message["type"] in ["resume", "start", "browser-start", "images-resume"] for message in sent)
        checks.append("access_recheck_does_not_automatically_resume_any_task")

        complete = state(job={**JOB, "discovered":60,"body_available":60,"items":{"ready":60},"list_complete":True,"body_complete":True},
                         session={**SESSION,"finalized":True,"scan_state":"finished"})
        show("complete", complete)
        expect(page.locator("#status-title")).to_have_text("正文已保存")
        expect(page.locator("#list-state")).to_have_text("列表已收齐")
        expect(page.locator("#start")).to_have_text("更新收藏")
        skipped_job = {**JOB, "state": "active", "discovered": 3, "expected_count": 3,
                       "body_available": 2, "items": {"ready": 2, "skipped": 1}, "list_complete": True,
                       "body_complete": False, "read_complete": True, "skipped_count": 1,
                       "skipped_summary": [{"error": "SOURCE_UNAVAILABLE", "count": 1}]}
        finished_session = {**SESSION, "finalized": True, "scan_state": "finished"}
        show("complete-with-skipped", state(job=skipped_job, session=finished_session))
        expect(page.locator("#status-title")).to_have_text("已结束 · 跳过 1 篇")
        expect(page.locator("#body-value")).to_have_text("2 / 3 篇")
        expect(page.locator("#body-state")).to_contain_text("已跳过 1 篇")
        expect(page.locator("#resume")).to_be_hidden()
        expect(page.locator("#browser-help")).to_be_hidden()
        expect(page.locator("#failure-reason")).to_be_hidden()
        expect(page.locator("#detail-summary")).to_contain_text("1 篇已跳过")
        checks.append("skipped_notes_finish_without_claiming_saved_bodies_or_offering_retry")

        show("all-skipped", state(job={**skipped_job, "body_available": 0, "items": {"skipped": 3},
            "skipped_count": 3, "skipped_summary": [{"error": "SOURCE_UNAVAILABLE", "count": 3}]}, session=finished_session))
        expect(page.locator("#status-title")).to_have_text("已结束 · 跳过 3 篇")
        expect(page.locator("#body-value")).to_have_text("0 / 3 篇")
        expect(page.locator("#browser-help")).to_be_hidden()
        checks.append("all_skipped_is_finished_without_saved_body_claim")

        show("reading-with-skipped", state(job={**skipped_job, "read_complete": False, "body_available": 1,
            "content_mode": "browser", "items": {"ready": 1, "skipped": 1, "browser_running": 1}}, session=finished_session))
        expect(page.locator("#status-title")).to_have_text("读取正文中")
        expect(page.locator("#body-state")).to_contain_text("已跳过 1 篇")
        expect(page.locator("#pause")).to_be_visible()
        checks.append("skipping_one_note_does_not_interrupt_remaining_reading")
        invalid_state = state(job=skipped_job, session=finished_session)
        invalid_state['server_status']['invalid_notes'] = {'total': 51}
        page.evaluate("""() => { window.__invalidItems = Array.from({length:51}, (_, i) => ({
          note_id: String(i).padStart(24, '0'), title: i === 0 ? '<img src=x onerror=alert(1)>' : '失效笔记 ' + i,
          error: i % 2 ? 'SOURCE_LINK_EXPIRED' : 'SOURCE_UNAVAILABLE'
        })); }""")
        show('invalid-list-collapsed', invalid_state)
        expect(page.locator('#invalid-summary')).to_have_text('失效列表（51）')
        page.locator('#invalid-summary').click()
        expect(page.locator('#invalid-items li')).to_have_count(50)
        expect(page.locator('#invalid-items li').first).to_contain_text('<img src=x onerror=alert(1)>')
        expect(page.locator('#invalid-items img')).to_have_count(0)
        expect(page.locator('#invalid-more')).to_be_visible()
        page.locator('#invalid-more').click()
        expect(page.locator('#invalid-items li')).to_have_count(51)
        expect(page.locator('#invalid-status')).to_have_text('已显示 51 / 51 篇')
        expect(page.locator('#invalid-more')).to_be_hidden()
        assert [message['offset'] for message in page.evaluate('window.__messages') if message['type'] == 'invalid-notes'] == [0, 50]
        # A new failure arrives after page 1, before polling discovers it. An
        # offset page now belongs to a different list and must be restarted.
        page.evaluate("async () => { await loadInvalidNotes(true); window.__invalidItems.unshift({note_id:'new',title:'新增失效',error:'SOURCE_UNAVAILABLE'}); }")
        page.locator('#invalid-more').click()
        expect(page.locator('#invalid-items li')).to_have_count(50)
        expect(page.locator('#invalid-items li').first).to_contain_text('新增失效')
        expect(page.locator('#invalid-status')).to_have_text('已显示 50 / 52 篇')
        page.evaluate("window.__fixture.server_status.invalid_notes.total = 52")
        page.locator('#invalid-more').click()
        expect(page.locator('#invalid-items li')).to_have_count(52)
        expect(page.locator('#invalid-status')).to_have_text('已显示 52 / 52 篇')
        titles = page.locator('#invalid-items strong').all_text_contents()
        assert len(set(titles)) == 52
        invalid_state['server_status']['invalid_notes']['total'] = 52
        checks.append('invalid_list_restarts_when_failures_arrive_between_pages')
        show('invalid-list-expanded', invalid_state)
        page.locator('#invalid-summary').click()
        checks.append('persistent_invalid_list_pages_without_retrying_sources_or_rendering_title_html')
        partial = copy.deepcopy(awaiting)
        partial["session"].update(finalized=True, scan_state="partial")
        partial["page_session"] = partial["session"]
        show("partial", partial)
        expect(page.locator("#status-title")).to_have_text("已读正文已保存")
        expect(page.locator("#list-state")).to_contain_text("待确认")
        checks.append("partial_save_never_claims_collection_complete")

        show("disconnected", state(job=JOB, session=SESSION, connection_error="NETWORK_ERROR"))
        expect(page.locator("#status-title")).to_have_text("连接中断")
        expect(page.locator("#body-value")).to_have_text("—")
        expect(page.locator("#list-percent")).to_have_text("—")
        expect(page.locator("#body-percent")).to_have_text("—")
        assert page.locator("#list-meter").get_attribute("aria-valuenow") is None
        assert page.locator("#body-meter").get_attribute("aria-valuenow") is None
        expect(page.locator("#message")).to_be_visible()
        show("ready", state())
        expect(page.locator("#message")).to_be_hidden()
        checks.append("disconnect_does_not_show_stale_completion")

        show("setup", state(connection_error="HOST_NOT_INSTALLED"))
        expect(page.locator("#setup")).to_be_visible()
        expect(page.locator("#target-card")).to_be_hidden()
        show("empty", state(page=None))
        expect(page.locator("#inspect")).to_be_visible()
        note_only = state(page=None)
        note_only["note"] = with_images["note"]
        show("note-only", note_only)
        expect(page.locator("#images-current")).to_be_enabled()
        expect(page.locator("#images-collection")).to_be_disabled()
        first_screen("inspect", "images-current", "images-collection")
        checks.append("image_action_scope_stays_explicit_without_a_collection_task")
        show("long-name", state(page={**PAGE,"name":"很长的收藏夹名称" * 28,"expected_count":100000}))
        first_screen("start")
        checks.append("long_name_without_horizontal_overflow")

        long_running = copy.deepcopy(with_images)
        long_running["session"]["name"] = "很长的收藏夹名称" * 28
        long_running["page_session"] = long_running["session"]
        long_running["server_status"]["job"].update(discovered=100000, expected_count=100000, body_available=99999)
        # The action popup owns a 360px viewport; don't force it to shrink to
        # the bootstrap viewport that Chrome uses while deciding popup size.
        for width in [360, 400]:
            page.set_viewport_size({"width": width, "height": 600})
            add_layout_metric()
            show("three-rings-long-name-" + str(width), long_running)
            rings_in_one_row("list", "body", "extra")
            first_screen("pause")
            reachable("images-current", "images-collection", "images-pause")
            expect(page.locator("#list-value")).to_have_text("100000 / 100000 篇")
            expect(page.locator("#body-value")).to_have_text("99999 / 100000 篇")
            expect(page.locator("#body-percent")).to_have_text("99%")
            expect(page.locator("#body-meter")).to_have_attribute("aria-valuenow", "99")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.evaluate("document.getElementById('extra-metric').remove()")
        page.set_viewport_size({"width": 360, "height": 600})
        checks.append("three_rings_and_long_counts_fit_fixed_popup_width")

        show("paused", paused)
        page.locator("#help-toggle").click()
        page.locator('[data-appearance-choice="dark"]').click()
        close_sheet()
        show("dark-paused", paused)
        assert page.locator("body").evaluate("e => getComputedStyle(e).colorScheme") == "dark"
        page.locator("#help-toggle").click()
        page.locator('[data-appearance-choice="system"]').click()
        close_sheet()
        checks.append("dark_task_appearance")

        page.emulate_media(reduced_motion="reduce")
        show("reduced-motion", running)
        assert page.locator("#body-meter").evaluate("(e)=>getComputedStyle(e, '::after').transitionDuration") == "0s"
        show("reduced-motion-paused", paused)
        assert page.locator("#status-title").evaluate("(e)=>e.getAnimations().length") == 0
        page.evaluate("value => {window.__fixture=value; return refresh();}", with_images)
        closed_arrow = page.locator("#image-details summary").evaluate("e => getComputedStyle(e, '::after').transform")
        page.locator("#image-details summary").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#image-detail")).to_be_visible()
        assert page.locator("#image-details").evaluate("e => getComputedStyle(e, '::details-content').transitionDuration") == "0s"
        assert page.locator("#image-details summary").evaluate("e => getComputedStyle(e, '::after').transform") != closed_arrow, "Expanded disclosure must remain distinguishable with reduced motion"
        page.keyboard.press("Space")
        expect(page.locator("#image-detail")).to_be_hidden()
        checks.append("reduced_motion_disclosure_remains_static_and_shows_expanded_state")
        checks.append("reduced_motion")
        assert not remote_requests, remote_requests
        checks.append("vendored_components_load_without_remote_requests")
        assert not errors, errors
        preview = OUT / "preview.html"
        preview.write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <title>收藏上下文 · 面板预览</title><style>
        *{box-sizing:border-box}body{margin:0;padding:32px;width:1192px;background:#eeeff1;color:#242529;font-family:"Microsoft YaHei",sans-serif}
        header{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:28px}h1{font-size:26px;margin:0 0 8px}p{font-size:13px;color:#74615b;margin:0}.tag{padding:8px 12px;background:#fcf7f5;border-radius:8px;font-size:12px}
        main{display:grid;grid-template-columns:repeat(3,360px);gap:24px}h2{font-size:14px;font-weight:500;margin:0 0 12px}h2 span{font-size:11px;color:#74615b;margin-right:10px}img{display:block;width:360px;height:auto;border:1px solid #eadbd4;border-radius:12px}footer{margin-top:20px;font-size:11px;color:#74615b}
        </style><header><div><h1>收藏上下文 · Chrome 面板</h1><p>清楚知道读到了哪里，以及下一步可以做什么。</p></div><div class="tag">实际 HTML 界面 · 合成数据</div></header>
        <main><section><h2><span>01</span>浅色 · 已暂停</h2><img src="paused.png" alt="浅色暂停状态"></section><section><h2><span>02</span>深色 · 已暂停</h2><img src="dark-paused.png" alt="深色暂停状态"></section><section><h2><span>03</span>名称与总数</h2><img src="options.png" alt="设置面板"></section></main>
        <footer>360px 面板 · 并排圆环 · 列表与正文分别报告 · 低频选项折叠 · 支持减少动态效果。此预览不代表真实收藏读取结果，也不是 Figma 截图。</footer></html>''', encoding="utf-8")
        gallery = browser.new_page(viewport={"width":1192,"height":800}, device_scale_factor=1)
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
