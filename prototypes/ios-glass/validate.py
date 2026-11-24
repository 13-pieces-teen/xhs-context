"""Validate the isolated iOS-inspired study with synthetic interactions only."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/ios-glass"
STATES = ("paused", "running", "ready", "restricted", "unknown", "complete", "confirm", "disconnected")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks, issues, errors, requests = [], [], [], []
    measurements = {}
    report = {"dataset": "synthetic_design_study", "live_xhs": "NOT_RUN", "checks": checks,
              "issues": issues, "page_errors": errors, "remote_requests": requests, "measurements": measurements}
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
    candidates = sorted(cache.glob("chromium-*/**/chrome.exe"), reverse=True)
    executable = os.environ.get("XHS_TEST_CHROMIUM") or (str(candidates[0]) if candidates else None)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=executable,
                                    ignore_default_args=["--hide-scrollbars"])
        page = browser.new_page(viewport={"width": 1280, "height": 920}, device_scale_factor=2)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url)
                if request.url.startswith(("http://", "https://")) else None)
        page.route("http://**/*", lambda route: route.abort())
        page.route("https://**/*", lambda route: route.abort())

        def settled():
            page.evaluate("document.fonts.ready")
            page.evaluate("async () => { await Promise.allSettled(document.getElementById('panel').getAnimations({subtree:true}).map(a => a.finished)); }")

        def choose(state):
            page.locator(f'[data-state="{state}"]').click()
            expect(page.locator("#sheet-layer")).to_be_hidden()
            settled()

        def capture(name):
            settled()
            expect(page.locator("#toast")).to_be_hidden(timeout=4000)
            page.locator("#panel").screenshot(path=str(OUT / (name + ".png")), animations="disabled")

        def visible_above_dock(selector):
            return page.locator(selector).evaluate("""e => {
                const r=e.getBoundingClientRect(), dock=document.querySelector('.dock').getBoundingClientRect();
                const area=document.getElementById('scroll-area').getBoundingClientRect();
                return {visible:e.checkVisibility(), top:r.top-area.top, bottom:r.bottom-dock.top};
            }""")

        try:
            page.goto((ROOT / "prototypes/ios-glass/index.html").as_uri())
            page.wait_for_load_state("networkidle")
            settled()
            expect(page.locator("#refresh svg")).to_be_visible()
            assert page.locator("#panel .icon").evaluate_all("nodes => nodes.filter(e => e.checkVisibility()).every(e => e.querySelector('svg')?.childElementCount > 0)"), "Visible icons must render locally on file URLs"
            checks.append("local_svg_icons_render_without_file_mask_requests")
            for state in STATES:
                choose(state)
                metric = page.locator("#panel").evaluate("""panel => {
                    const r=panel.getBoundingClientRect(), area=document.getElementById('scroll-area');
                    return {width:r.width,height:r.height,panelOverflow:panel.scrollWidth>panel.clientWidth,
                        contentOverflow:area.scrollWidth>area.clientWidth, scrollTop:area.scrollTop};
                }""")
                measurements[state] = metric
                if metric["width"] != 360 or metric["height"] != 600 or metric["panelOverflow"] or metric["contentOverflow"]:
                    issues.append({"state": state, "problem": "Panel geometry", "measurement": metric})
                main = "#confirm-end" if state == "confirm" else "#primary"
                visibility = visible_above_dock(main)
                if not visibility["visible"] or visibility["top"] < 0 or visibility["bottom"] > -4:
                    issues.append({"state": state, "problem": "Main action clipped by dock", "measurement": visibility})
                if state in ("paused", "running", "ready", "unknown", "complete", "disconnected"):
                    visibility = visible_above_dock('[data-action="collection-images"]')
                    if visibility["bottom"] > -4:
                        issues.append({"state": state, "problem": "Quick actions clipped by dock", "measurement": visibility})
            checks.append("eight_states_fixed_360x600_and_main_actions_visible")

            choose("paused")
            expect(page.locator("#list-percent")).to_have_text("88%")
            expect(page.locator("#body-percent")).to_have_text("99%")
            capture("light-paused")
            page.screenshot(path=str(OUT / "studio.png"), full_page=True, animations="disabled")
            choose("running")
            page.locator("#dark-mode").click()
            capture("dark-running")
            page.locator("#light-mode").click()
            choose("paused")
            page.locator("#primary").click()
            expect(page.locator("#state-label")).to_have_text("保存正文中")
            page.locator("#primary").click()
            expect(page.locator("#state-label")).to_have_text("已暂停")
            checks.append("pause_resume_retains_state_and_progress_meaning")

            choose("restricted")
            expect(page.locator("#primary")).to_be_disabled()
            expect(page.locator("#recover")).to_be_disabled()
            page.locator("#refresh").click()
            expect(page.locator("#state-label")).to_have_text("已暂停")
            expect(page.locator("#primary")).to_be_enabled()
            expect(page.locator("#primary")).to_contain_text("继续读取")
            checks.append("access_check_requires_manual_resume")

            choose("unknown")
            expect(page.locator("#list-percent")).to_have_text("—")
            assert page.locator("#list-meter").get_attribute("aria-valuenow") is None
            choose("complete")
            expect(page.locator("#body-percent")).to_have_text("99%")
            expect(page.locator("#body-caption")).to_contain_text("3 篇已失效")
            checks.append("unknown_and_skipped_progress_do_not_claim_full_archive")

            choose("ready")
            page.locator("#settings-button").click()
            expect(page.locator("#close-sheet")).to_be_focused()
            page.keyboard.press("Shift+Tab")
            expect(page.locator('#settings-form button[type="submit"]')).to_be_focused()
            page.keyboard.press("Tab")
            expect(page.locator("#close-sheet")).to_be_focused()
            page.keyboard.press("Tab")
            expect(page.locator("#demo-name")).to_be_focused()
            capture("settings")
            page.locator("#demo-name").fill("示例 · 技术收藏")
            page.locator("#demo-total").fill("1.5")
            page.locator('#settings-form button[type="submit"]').click()
            expect(page.locator("#sheet-layer")).to_be_visible()
            assert not page.locator("#demo-total").evaluate("e => e.checkValidity()")
            page.locator("#demo-total").fill("969")
            page.locator('#settings-form button[type="submit"]').click()
            expect(page.locator("#sheet-layer")).to_be_hidden()
            expect(page.locator("#collection-name")).to_have_text("示例 · 技术收藏")
            expect(page.locator("#settings-button")).to_be_focused()
            checks.append("settings_validation_save_and_modal_focus_cycle")

            page.locator("#settings-button").click()
            page.keyboard.press("Escape")
            expect(page.locator("#sheet-layer")).to_be_hidden()
            expect(page.locator("#settings-button")).to_be_focused()
            assert not page.locator("#app-content").evaluate("e => e.inert")
            checks.append("escape_closes_sheet_and_restores_trigger_focus")

            # This race cannot be generated through inert background controls;
            # use the study's public API to exercise animation interruption directly.
            page.evaluate("""() => {
                const d=window.designStudy;
                d.openSheet('help',document.getElementById('help-button'));
                d.closeSheet();
                d.openSheet('settings',document.getElementById('settings-button'));
            }""")
            settled()
            expect(page.locator("#sheet-layer")).to_be_visible()
            expect(page.locator("#demo-name")).to_be_visible()
            expect(page.locator("#sheet-title")).to_have_text("名称与总数")
            assert page.locator("#app-content").evaluate("e => e.inert")
            page.keyboard.press("Escape")
            expect(page.locator("#sheet-layer")).to_be_hidden()
            checks.append("rapid_close_reopen_cancels_stale_exit")

            choose("paused")
            page.locator('[data-action="collection-images"]').click()
            page.locator("#image-toggle").click()
            expect(page.locator("#image-state")).to_have_text("已暂停")
            page.locator("#image-toggle").click()
            expect(page.locator("#image-state")).to_have_text("补图中")
            page.keyboard.press("Escape")
            expect(page.locator("#sheet-layer")).to_be_hidden()
            expect(page.locator("#state-label")).to_have_text("已暂停")
            checks.append("image_task_controls_independent_from_body_task")

            page.locator("#reduce-motion").check()
            page.locator("#help-button").click()
            assert page.locator("#panel").evaluate("e => e.getAnimations({subtree:true}).filter(a => a.playState==='running').length") == 0
            assert page.locator("#primary").evaluate("e => getComputedStyle(e).transitionDuration") == "0s"
            page.keyboard.press("Escape")
            expect(page.locator("#sheet-layer")).to_be_hidden()
            page.locator("#reduce-motion").uncheck()
            page.emulate_media(reduced_motion="reduce")
            page.locator("#help-button").click()
            assert page.locator("#panel").evaluate("e => e.getAnimations({subtree:true}).filter(a => a.playState==='running').length") == 0
            page.keyboard.press("Escape")
            expect(page.locator("#sheet-layer")).to_be_hidden()
            page.emulate_media(reduced_motion="no-preference")
            checks.append("manual_and_system_reduced_motion_disable_animations")

            choose("running")
            page.locator("#dark-mode").click()
            expect(page.locator("#panel")).to_have_attribute("data-appearance", "dark")
            checks.append("dark_appearance")

            assert not errors, errors
            assert not requests, requests
            checks.append("offline_assets_and_zero_javascript_errors")

            overview = OUT / "overview.html"
            overview.write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>iOS 风格设计稿</title>
            <style>*{box-sizing:border-box}body{margin:0;padding:38px 40px;width:1248px;background:#eeeff1;color:#303139;font-family:"Microsoft YaHei",sans-serif}header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:30px}h1{font-size:25px;font-weight:600;margin:0}header p,footer{font-size:12px;color:#82848e}main{display:grid;grid-template-columns:repeat(3,360px);gap:44px}h2{font-size:13px;font-weight:400;color:#73757e;margin:0 0 12px}img{width:360px;height:600px;display:block;border-radius:28px;box-shadow:0 14px 30px -18px #32333c66}footer{margin-top:28px}</style>
            <header><h1>收藏上下文 · iOS 风格交互设计稿</h1><p>360 × 600 · 示例数据</p></header>
            <main><section><h2>01 / 浅色 · 已暂停</h2><img src="light-paused.png" alt="浅色暂停状态"></section><section><h2>02 / 深色 · 读取中</h2><img src="dark-running.png" alt="深色读取状态"></section><section><h2>03 / 设置面板</h2><img src="settings.png" alt="名称与总数设置"></section></main>
            <footer>基于实际 HTML 渲染 · 所有内容与操作仅用于设计演示 · 未连接真实收藏</footer></html>''', encoding="utf-8")
            gallery = browser.new_page(viewport={"width": 1248, "height": 820}, device_scale_factor=1)
            gallery.goto(overview.as_uri())
            gallery.wait_for_load_state("networkidle")
            gallery.screenshot(path=str(OUT / "overview.png"), full_page=True, animations="disabled")
            report["passed"] = not issues
        finally:
            (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
    print(json.dumps({"checks": len(checks), "passed": report.get("passed", False), "issues": issues,
                      "page_errors": errors, "remote_requests": requests}, ensure_ascii=False))
    if issues:
        raise AssertionError(issues)


if __name__ == "__main__":
    main()
