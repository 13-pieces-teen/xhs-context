"""Capture the real popup UI with synthetic state only; no extension or network."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "panels"
VIEWPORT = {"width": 360, "height": 600}
SCALE = 2
PAGE = {
    "name": "收藏 · 笔记",
    "scope": "saved",
    "expected_count": 60,
    "page_key": "https://www.xiaohongshu.com/user/profile/fixture?tab=fav",
    "identity_marker": "fixture",
    "view_marker": "fav-note",
}
SESSION = {
    "job_id": "guide-fixture",
    "name": PAGE["name"],
    "finalized": False,
    "scan_state": "scanning",
}
JOB = {
    "state": "active",
    "discovered": 36,
    "body_available": 24,
    "expected_count": 60,
    "items": {"ready": 24, "queued": 11, "running": 1},
    "body_complete": False,
    "list_complete": False,
    "failure_summary": [],
    "content_mode": "anonymous",
}


def state(job=None, session=None):
    return copy.deepcopy({
        "page": PAGE,
        "page_error": "请在小红书打开收藏 → 笔记。",
        "page_session": session,
        "session": session,
        "server_status": {"job": job} if job else None,
        "connection_error": None,
    })


def scaled_box(box):
    return {key: round(value * SCALE, 3) for key, value in box.items()} if box else None


def main():
    assert OUT.resolve().is_relative_to((ROOT / "assets/guides").resolve())
    OUT.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "ready": state(),
        "running": state(JOB, SESSION),
        "paused": state({**JOB, "state": "paused"}, {**SESSION, "scan_state": "paused"}),
        "completed": state(
            {**JOB, "discovered": 60, "body_available": 60, "items": {"ready": 60},
             "body_complete": True, "read_complete": True, "list_complete": True},
            {**SESSION, "finalized": True, "scan_state": "finished"},
        ),
    }
    errors, blocked_requests = [], []
    metadata = {
        "schema_version": 1,
        "synthetic": True,
        "description": "Actual popup HTML rendered with synthetic state; not a live collection run.",
        "source": "browser-extension/popup.html",
        "viewport_css": VIEWPORT,
        "device_scale_factor": SCALE,
        "image_pixels": {"width": 720, "height": 1200},
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("browser-extension/popup.html", "browser-extension/popup.css", "browser-extension/popup.js")
        },
        "states": {},
    }
    with sync_playwright() as playwright:
        cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
        candidates = sorted(cache.glob("chromium-*/**/chrome.exe"), reverse=True)
        executable = os.environ.get("XHS_TEST_CHROMIUM") or (str(candidates[0]) if candidates else None)
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=executable,
            timeout=20000,
            args=["--host-resolver-rules=MAP * ~NOTFOUND"],
            ignore_default_args=["--hide-scrollbars"],
        )
        try:
            context = browser.new_context(
                viewport=VIEWPORT, device_scale_factor=SCALE,
                color_scheme="light", reduced_motion="reduce", service_workers="block",
            )

            def route_request(route):
                if route.request.url.startswith(("file:", "data:")):
                    route.continue_()
                else:
                    blocked_requests.append(route.request.url)
                    route.abort()

            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""
                window.__fixture = %s;
                window.__messages = [];
                window.chrome = window.chrome || {};
                window.chrome.runtime = {sendMessage: async message => {
                    window.__messages.push(message.type);
                    if (message.type === 'state') {
                        return {ok: true, data: structuredClone(window.__fixture)};
                    }
                    if (message.type === 'invalid-notes') {
                        return {ok: true, data: {total: 0, items: [], has_more: false}};
                    }
                    throw new Error('Guide capture must not invoke actions: ' + message.type);
                }};
            """ % json.dumps(fixtures["ready"], ensure_ascii=False))
            page.goto((ROOT / "browser-extension/popup.html").as_uri())
            expect(page.locator("#connection")).to_have_text("已连接")
            page.evaluate("document.fonts.ready")
            for name, fixture in fixtures.items():
                page.evaluate("""async value => {
                    window.__fixture = value;
                    await refresh();
                    document.querySelector('main').scrollTop = 0;
                    await document.fonts.ready;
                }""", fixture)
                if name == "ready":
                    expect(page.locator("#start")).to_have_text("开始读取")
                    expect(page.locator("#start")).to_be_enabled()
                elif name == "running":
                    expect(page.locator("#pause")).to_be_visible()
                    expect(page.locator("#pause")).to_be_enabled()
                elif name == "paused":
                    expect(page.locator("#status-title")).to_have_text("已暂停")
                    expect(page.locator("#resume")).to_be_enabled()
                else:
                    expect(page.locator("#status-title")).to_have_text("正文已保存")
                    expect(page.locator("#start")).to_have_text("更新收藏")
                controls = {}
                for identifier in ("start", "pause", "resume"):
                    control = page.locator("#" + identifier)
                    visible = control.is_visible()
                    box = control.bounding_box() if visible else None
                    if box:
                        assert box["x"] >= 0 and box["x"] + box["width"] <= VIEWPORT["width"]
                        assert box["y"] >= 0 and box["y"] + box["height"] <= VIEWPORT["height"]
                    controls["#" + identifier] = {
                        "visible": visible,
                        "enabled": control.is_enabled() if visible else False,
                        "label": control.inner_text().strip(),
                        "bbox_css": box,
                        "bbox_image": scaled_box(box),
                    }
                visible_text = page.evaluate("""() => {
                    const result = [];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const node = walker.currentNode, parent = node.parentElement;
                        const text = node.textContent.trim();
                        if (!text || parent.closest('script, style, [hidden], .sr-only, [aria-hidden="true"]')) continue;
                        const range = document.createRange(); range.selectNodeContents(node);
                        const rect = range.getBoundingClientRect();
                        if (rect.width && rect.height && rect.top < innerHeight && rect.bottom > 0 && rect.left < innerWidth && rect.right > 0) result.push(text);
                    }
                    return result;
                }""")
                text_fields = {}
                for identifier in ("connection", "folder", "scope", "task-name", "status-title",
                                   "list-value", "list-state", "body-value", "body-state", "action-note"):
                    locator = page.locator("#" + identifier)
                    if locator.is_visible():
                        text_fields[identifier] = locator.inner_text().strip()
                image_path = OUT / (name + ".png")
                page.screenshot(path=str(image_path), full_page=False, animations="disabled")
                metadata["states"][name] = {
                    "image": image_path.name,
                    "controls": controls,
                    "text_fields": text_fields,
                    "visible_text": visible_text,
                    "synthetic_state": fixture,
                }
                print("Captured " + name, flush=True)
            metadata["runtime_messages"] = sorted(set(page.evaluate("window.__messages")))
            metadata["blocked_remote_requests"] = blocked_requests
            metadata["page_errors"] = errors
            assert not errors, errors
            assert not blocked_requests, blocked_requests
            assert metadata["runtime_messages"] == ["state"], metadata["runtime_messages"]
            (OUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            browser.close()
    print(str(OUT / "metadata.json"), flush=True)


if __name__ == "__main__":
    main()
