import json

import pytest

from xhs_context.common import XhsError
from xhs_context.parser import parse_note

NOTE_ID = "1" * 24
SOURCE = "https://www.xiaohongshu.com/explore/" + NOTE_ID
MESSAGES = ["笔记不存在", "笔记已删除", "找不到对应的笔记", "找不到对应笔记"]


def parse_error(html):
    with pytest.raises(XhsError) as error:
        parse_note(html, NOTE_ID, SOURCE)
    return error.value.code


@pytest.mark.parametrize("message", MESSAGES)
@pytest.mark.parametrize("state", ["", '<script>window.__INITIAL_STATE__={"note": {}}</script>'])
def test_explicit_error_page_is_unavailable_with_or_without_page_state(message, state):
    assert parse_error('<main><h1>' + message + '</h1><button>返回首页</button></main>' + state) == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("state", ["", '<script>window.__INITIAL_STATE__={}</script>'])
def test_explicit_unavailable_title_allows_a_bounded_confirming_description(state):
    html = '<main><h1>找不到对应的笔记</h1><p>这篇笔记已无法访问</p><button>返回首页</button></main>'
    assert parse_error(html + state) == "SOURCE_UNAVAILABLE"
    assert parse_error(html.replace("这篇笔记已无法访问", "上面是本文讨论的提示语") + state) != "SOURCE_UNAVAILABLE"
    assert parse_error(html.replace("这篇笔记已无法访问", "这篇笔记已无法访问，刷新后可继续") + state) != "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("html", [
    '<div class="error-page" hidden>笔记已删除</div>',
    '<div style="display:none"><div class="error-page">笔记已删除</div></div>',
    '<div class="error-page" aria-hidden="true">笔记已删除</div>',
    '<template><main><h1>笔记已删除</h1></main></template>',
    '<div class="comments"><div class="error-page">笔记已删除</div></div>',
    '<main><blockquote><h1>笔记已删除</h1></blockquote></main>',
    '<div id="noteContainer"><div id="detail-desc"><div class="error-page">笔记已删除</div></div></div>',
    '<main>这篇教程解释为什么会显示找不到对应的笔记。</main>',
    '<main><h1>登录后查看</h1><div class="error-page">当前笔记暂时无法浏览</div></main>',
    '<div class="error-page">请完成安全验证，当前笔记暂时无法浏览</div>',
    '<div role="alert">并不是笔记已删除，请稍后重试。</div>',
    '<main><h1>遇到“笔记不存在”怎么办？</h1></main>',
    '<main><h1>笔记不存在</h1><p>上面是本文讨论的提示语。</p></main>',
    '<div class="error-page">当前笔记暂时无法浏览，请登录账号后重试。</div>',
    '<div class="error-page">网络异常导致笔记暂时无法浏览，请刷新重试。</div>',
    '<main><h1>当前笔记暂时无法浏览</h1></main>',
    '<main><h1>笔记无法查看</h1></main>',
    '<style>.error-page{display:none}</style><div class="error-page">笔记已删除</div>',
    '<link rel="stylesheet" href="/theme.css"><div class="error-page">笔记已删除</div>',
    '<style>.error-page{display:none}</style><div class="error-page">笔记已删除</div>'
    '<div id="noteContainer"><h1 id="detail-title">正常标题</h1><div id="detail-desc">正文</div></div>',
    '<div class="error-page">笔记已删除</div><div id="noteContainer"><div id="detail-desc">正文</div></div>',
])
def test_hidden_user_authored_and_login_text_does_not_establish_unavailability(html):
    assert parse_error(html + '<script>window.__INITIAL_STATE__={}</script>') == "NOTE_NOT_FOUND"


def test_note_missing_from_state_remains_uncertain():
    assert parse_error('<script>window.__INITIAL_STATE__={}</script>') == "NOTE_NOT_FOUND"
    assert parse_error('<main>页面尚未加载完成</main>') == "PARSE_FAILED"


def test_a_real_note_about_deleted_notes_remains_readable():
    state = {"note": {"noteId": NOTE_ID, "title": "笔记已删除", "desc": "找不到对应的笔记是本文研究的提示语"}}
    html = '<main><h1>笔记已删除</h1></main><script>window.__INITIAL_STATE__=' + json.dumps(state) + '</script>'
    assert parse_note(html, NOTE_ID, SOURCE)["body"] == state["note"]["desc"]


def test_security_restriction_wins_over_unavailability_even_with_an_empty_note_shell():
    html = '<div id="noteContainer"></div><main><h1>笔记已删除</h1><p>安全限制 检测到异常访问 300013</p></main>'
    assert parse_error(html) == "ACCESS_RESTRICTED"


@pytest.mark.parametrize("shell", [
    '<div id="noteContainer"><h1 id="detail-title"></h1><div id="detail-desc"></div></div>',
    '<div id="noteContainer"><h1 id="detail-title">  </h1><div id="detail-desc">\u200b</div></div>',
])
def test_empty_detail_elements_do_not_hide_security_or_deleted_note_notices(shell):
    assert parse_error(shell + '<main><h1>笔记已删除</h1></main>') == "SOURCE_UNAVAILABLE"
    assert parse_error(shell + '<main>安全限制 检测到异常访问 300013</main>') == "ACCESS_RESTRICTED"


def test_plain_page_level_message_is_sufficient_but_an_ordinary_article_is_not():
    assert parse_error('<main>找不到对应的笔记<button>返回首页</button></main>') == "SOURCE_UNAVAILABLE"
    assert parse_error('<article>笔记已删除</article>') == "PARSE_FAILED"
