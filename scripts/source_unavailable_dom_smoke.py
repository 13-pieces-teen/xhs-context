"""Exercise unavailable-note detection in real Chromium with local HTML only."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
NOTE_ID = "1" * 24
SOURCE = "https://www.xiaohongshu.com/explore/" + NOTE_ID
UNKNOWN_ERROR = "https://www.xiaohongshu.com/website-login/error?error_code=fixture_unknown"
SECURITY = "安全限制 检测到异常访问 300013"


def main():
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
    candidates = sorted(cache.glob("chromium-*/**/chrome.exe"), reverse=True)
    executable = os.environ.get("XHS_TEST_CHROMIUM") or (str(candidates[0]) if candidates else None)
    cases = []
    for message in ["笔记不存在", "笔记已删除", "找不到对应的笔记", "找不到对应笔记"]:
        cases.append((message, '<main><h1>' + message + '</h1><button>返回首页</button></main>', SOURCE, "SOURCE_UNAVAILABLE"))
    cases.extend([
        ("plain_main", '<main>找不到对应的笔记<button>返回首页</button></main>', SOURCE, "SOURCE_UNAVAILABLE"),
        ("error_inside_empty_note_shell", '<div id="noteContainer"><h1 id="detail-title"></h1><div id="detail-desc"></div><div class="error-page">找不到对应的笔记</div></div>', SOURCE, "SOURCE_UNAVAILABLE"),
        ("readable_note_wins_over_inner_error_panel", '<div id="noteContainer"><h1 id="detail-title">正常笔记</h1><div id="detail-desc">正常正文</div><div class="error-page">找不到对应的笔记</div></div>', SOURCE, "snapshot"),
        ("title_and_confirming_description", '<main><h1>找不到对应的笔记</h1><p>这篇笔记已无法访问</p><button>返回首页</button></main>', SOURCE, "SOURCE_UNAVAILABLE"),
        ("title_and_recovery_description", '<main><h1>找不到对应的笔记</h1><p>这篇笔记已无法访问，刷新后可继续</p><button>返回首页</button></main>', SOURCE, "pending"),
        ("unknown_error_explicit", '<div class="error-page">找不到对应的笔记</div>', UNKNOWN_ERROR, "SOURCE_UNAVAILABLE"),
        ("unknown_error_uncertain", '<div class="error-page">网页出错，请稍后再试</div>', UNKNOWN_ERROR, "ACCESS_BLOCKED"),
        ("unknown_error_security_first", '<main><h1>找不到对应的笔记</h1><p>' + SECURITY + '</p></main>', UNKNOWN_ERROR, "ACCESS_RESTRICTED"),
        ("security_url_first", '<main><h1>找不到对应的笔记</h1></main>', UNKNOWN_ERROR.replace("fixture_unknown", "300013"), "ACCESS_RESTRICTED"),
        ("expired_link", '<main><h1>找不到对应的笔记</h1></main>', UNKNOWN_ERROR.replace("fixture_unknown", "300017"), "SOURCE_LINK_EXPIRED"),
        ("login_page", '<main><h1>找不到对应的笔记</h1></main>', 'https://www.xiaohongshu.com/login', "ACCESS_BLOCKED"),
        ("login_required", '<div class="error-page">当前笔记暂时无法浏览，请先登录</div>', UNKNOWN_ERROR, "ACCESS_BLOCKED"),
        ("verification_required", '<div class="error-page">当前笔记暂时无法浏览，请完成安全验证</div>', UNKNOWN_ERROR, "ACCESS_BLOCKED"),
        ("hidden_attribute", '<div class="error-page" hidden>找不到对应的笔记</div>', SOURCE, "pending"),
        ("hidden_ancestor", '<div style="display:none"><div class="error-page">笔记已删除</div></div>', SOURCE, "pending"),
        ("hidden_stylesheet", '<style>.error-page { display:none; }</style><div class="error-page">笔记已删除</div>', SOURCE, "pending"),
        ("hidden_template", '<template><main><h1>笔记已删除</h1></main></template>', SOURCE, "pending"),
        ("comment", '<main><div class="comments"><h1>找不到对应的笔记</h1></div></main>', SOURCE, "pending"),
        ("quote", '<main><blockquote><h1>找不到对应的笔记</h1></blockquote></main>', SOURCE, "pending"),
        ("ordinary_article", '<main>这篇教程解释为什么会显示找不到对应的笔记。</main>', SOURCE, "pending"),
        ("help_heading", '<main><h1>遇到“笔记不存在”怎么办？</h1></main>', SOURCE, "pending"),
        ("quoted_heading_with_context", '<main><h1>笔记不存在</h1><p>上面是本文讨论的提示语。</p></main>', SOURCE, "pending"),
        ("negated_notice", '<div role="alert">并不是笔记已删除，请稍后重试。</div>', SOURCE, "pending"),
        ("temporary_notice", '<main><h1>当前笔记暂时无法浏览</h1></main>', SOURCE, "pending"),
        ("uncertain_notice", '<div class="error-page">笔记无法查看</div>', SOURCE, "pending"),
        ("network_recovery", '<div class="error-page">网络异常导致笔记暂时无法浏览，请刷新重试。</div>', SOURCE, "pending"),
        ("login_recovery", '<div class="error-page">当前笔记暂时无法浏览，请登录账号后重试。</div>', SOURCE, "pending"),
        ("expired_url_login", '<div class="error-page">请登录账号后重试。</div>', UNKNOWN_ERROR.replace("fixture_unknown", "300017"), "ACCESS_BLOCKED"),
        ("expired_url_verification", '<div class="error-page">请完成安全验证。</div>', UNKNOWN_ERROR.replace("fixture_unknown", "300017"), "ACCESS_BLOCKED"),
        ("body_quote", '<div id="noteContainer"><h1 id="detail-title">提示语讨论</h1><div id="detail-desc"><div class="error-page">找不到对应的笔记</div></div></div>', SOURCE, "snapshot"),
        ("body_security_quote", '<div id="noteContainer"><h1 id="detail-title">提示语讨论</h1><div id="detail-desc">' + SECURITY + '，找不到对应的笔记</div></div>', SOURCE, "snapshot"),
    ])
    for shell_name, shell in [
        ('empty_detail', '<div id="noteContainer"><h1 id="detail-title"></h1><div id="detail-desc"></div></div>'),
        ('whitespace_detail', '<div id="noteContainer"><h1 id="detail-title"> </h1><div id="detail-desc">\u200b</div></div>'),
    ]:
        cases.extend([
            (shell_name + '_security', shell + '<main>' + SECURITY + '</main>', SOURCE, 'ACCESS_RESTRICTED'),
            (shell_name + '_unavailable', shell + '<main><h1>笔记已删除</h1></main>', SOURCE, 'SOURCE_UNAVAILABLE'),
            (shell_name + '_loading', shell, SOURCE, 'pending'),
        ])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=executable,
            args=["--host-resolver-rules=MAP * ~NOTFOUND"])
        try:
            context = browser.new_context()
            fixture = {"html": ""}
            context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=fixture["html"]))
            page = context.new_page()
            for name, html, url, expected in cases:
                fixture["html"] = '<!doctype html><html><head><meta charset="utf-8"></head><body>' + html + '</body></html>'
                page.goto(url)
                page.add_script_tag(path=str(ROOT / "browser-extension/page-adapter.js"))
                result = page.evaluate("id => XhsContextPage.readNote(id)", NOTE_ID)
                outcome = result.get("error") or ("snapshot" if result.get("snapshot") else "pending")
                assert outcome == expected, (name, expected, result)
            context.close()
        finally:
            browser.close()
    print(json.dumps({"cases": len(cases), "passed": len(cases), "live_xhs": "NOT_RUN"}))


if __name__ == "__main__":
    main()
