"""Independent, non-executing parser for a note page's serialized initial state."""
from __future__ import annotations

import json
import re
from collections import deque
from html.parser import HTMLParser

from .common import XhsError

UNAVAILABLE_NOTE = re.compile(r"(?:当前|该|这篇)?笔记(?:已被|已经|已)删除|(?:当前|该|这篇)?笔记不存在|找不到(?:对应的?)?笔记")
STANDALONE_UNAVAILABLE = re.compile(
    r"^(?:很?抱歉[，,！!：:]?\s*)?(?:" + UNAVAILABLE_NOTE.pattern +
    r")[。.!！]?(?:\s*(?:这篇|该|当前|此)?笔记已无法访问[。.!！]?)?"
    r"(?:\s*(?:返回首页|返回上一页|返回))*$")
RECOVERABLE_MESSAGE = re.compile(r"登录后|请[^。！\n]{0,30}登录|扫码登录|登录账号|登录[^。！\n]{0,20}(?:查看|浏览|继续|重试)|"
                                 r"验证码|安全验证|完成验证|网络|连接|加载|稍后|重试|刷新|暂时|临时|访问频繁")
ERROR_CLASSES = {"error-container", "error-page", "note-error", "error-wrapper", "error-message", "error-text",
                 "error-title", "not-found", "not-found-container", "note-not-found", "note-unavailable",
                 "empty-container", "empty-state", "empty-page", "reds-empty"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.in_script = False
        self.current, self.scripts = [], []
        self.page_text, self.in_style, self.has_note_content = [], False, False
        self.has_stylesheet = False
        self.elements, self.error_regions = [], []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.has_stylesheet |= tag == "style" or (tag == "link" and "stylesheet" in attributes.get("rel", "").lower().split())
        classes = set(attributes.get("class", "").split())
        hidden = any(frame["hidden"] for frame in self.elements) or "hidden" in attributes or \
            attributes.get("aria-hidden") == "true" or bool(re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|opacity\s*:\s*0(?:\s|;|$))",
                attributes.get("style", ""), re.I)) or tag in {"template", "noscript"}
        excluded = any(frame["excluded"] for frame in self.elements) or hidden or \
            attributes.get("id") in {"noteContainer", "detail-title", "detail-desc"} or \
            any("comment" in name for name in classes) or bool(classes & {"note-item", "feeds-container"}) or tag == "blockquote"
        note_content = not hidden and attributes.get("id") in {"detail-title", "detail-desc"} and \
            any(frame["note_root"] for frame in self.elements)
        region = ("main" if tag == "main" else "error" if classes & ERROR_CLASSES or attributes.get("role") == "alert" or
                  (tag in {"h1", "h2"} and any(frame["tag"] == "main" for frame in self.elements)) else None)
        if tag not in VOID_TAGS:
            self.elements.append({"tag": tag, "hidden": hidden, "excluded": excluded, "region": region, "text": [],
                                  "note_root": attributes.get("id") == "noteContainer", "note_content": note_content})
        if tag == "style":
            self.in_style = True
        if tag == "script":
            self.in_script = True
            self.current = []

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
        if tag == "script":
            self.in_script = False
            self.scripts.append("".join(self.current))
        for index in range(len(self.elements) - 1, -1, -1):
            if self.elements[index]["tag"] == tag:
                for frame in self.elements[index:]:
                    if frame["region"] and not frame["excluded"]:
                        self.error_regions.append(frame)
                del self.elements[index:]
                break

    def handle_data(self, data):
        if self.in_script:
            self.current.append(data)
        elif not self.in_style and not any(frame["hidden"] for frame in self.elements):
            self.page_text.append(data)
            if any(frame["note_content"] for frame in self.elements) and re.search(r"[^\s\u200b-\u200f\ufeff]", data):
                self.has_note_content = True
            if not any(frame["excluded"] for frame in self.elements):
                for frame in self.elements:
                    if frame["region"]:
                        frame["text"].append(data)

    def check_restriction(self):
        text = " ".join(self.page_text)
        if not self.has_note_content and "安全限制" in text and "检测到异常访问" in text and re.search(r"\b300013\b", text):
            raise XhsError("ACCESS_RESTRICTED", "Source displays security restriction 300013", exit_code=5)

    def check_unavailable(self):
        # Raw HTML cannot establish whether a stylesheet hides an error template.
        # A readable note DOM also rules out treating an unrelated notice as its loss.
        if self.has_note_content or self.has_stylesheet:
            return
        visible_text = re.sub(r"[\u200b-\u200f\ufeff]", "", " ".join(self.page_text))
        if RECOVERABLE_MESSAGE.search(visible_text) or "需要登录" in visible_text:
            return
        for frame in self.error_regions + self.elements:
            if frame["excluded"] or not frame["region"]:
                continue
            text = " ".join(frame["text"]).strip()
            # Headings/alerts must be affirmative standalone notices too, not
            # articles, negations or discussions containing an error phrase.
            if STANDALONE_UNAVAILABLE.fullmatch(text) and STANDALONE_UNAVAILABLE.fullmatch(visible_text.strip()):
                raise XhsError("SOURCE_UNAVAILABLE", "Source explicitly reports this note is unavailable", exit_code=5)


def json_object(source, start):
    depth, quoted, escape = 0, False, False
    for index in range(start, len(source)):
        char = source[index]
        if quoted:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise XhsError("PARSE_FAILED", "Incomplete serialized page state", exit_code=5)


def replace_undefined(source):
    """Replace JS undefined outside JSON strings, leaving user text untouched."""
    return re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',
                  lambda match: "null" if match.group() == "undefined" else match.group(), source)


def parse_note(html, note_id, source_url):
    collector = Scripts()
    collector.feed(html)
    states = []
    for script in collector.scripts:
        match = re.search(r"(?:window\.)?__INITIAL_STATE__\s*=\s*({)", script)
        if match:
            try:
                states.append(json.loads(replace_undefined(json_object(script, match.start(1)))))
            except (ValueError, RecursionError):
                continue
    if not states:
        collector.check_restriction()
        collector.check_unavailable()
        if re.search(r"验证码|安全验证|访问频繁|登录后查看", html):
            raise XhsError("ACCESS_BLOCKED", "Page requires verification or authenticated viewing", exit_code=5)
        raise XhsError("PARSE_FAILED", "No supported serialized note state found", exit_code=5)
    queue = deque((state, False, ()) for state in states)
    candidates, visited = [], 0
    while queue and visited < 30000:
        node, inherited, path = queue.popleft()
        visited += 1
        if isinstance(node, dict):
            explicit = node.get("noteId", node.get("note_id", node.get("id")))
            matches = (str(explicit).lower() == note_id.lower()) if explicit is not None else inherited
            if matches and any(k in node for k in ("desc", "content", "imageList", "image_list", "title")):
                detail_context = "noteDetailMap" in path or (path == ("note",) and explicit is not None)
                weight = 10 * ("desc" in node or "content" in node) + 5 * ("imageList" in node or "image_list" in node) + ("title" in node)
                candidates.append((weight + 100 * detail_context, node, detail_context))
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    inherited_id = key.lower() == note_id.lower() or (
                        matches and key in {"note", "noteInfo", "noteCard", "note_card"})
                    queue.append((value, inherited_id, path + (key,)))
        elif isinstance(node, list):
            queue.extend((value, inherited, path) for value in node if isinstance(value, (dict, list)))
    if not candidates:
        collector.check_restriction()
        collector.check_unavailable()
        raise XhsError("NOTE_NOT_FOUND", "Page state does not contain the requested note", exit_code=5)
    _, node, detail_context = max(candidates, key=lambda item: item[0])
    body = node.get("desc", node.get("content"))
    raw_images = node.get("imageList", node.get("image_list"))
    if raw_images is not None and not isinstance(raw_images, list):
        raw_images = None
    images = []
    for index, raw in enumerate(raw_images or [], 1):
        raw = raw if isinstance(raw, dict) else {}
        url = raw.get("urlDefault") or raw.get("url")
        quality = "page_default"
        if not url:
            for info in raw.get("infoList", []) or []:
                if isinstance(info, dict) and info.get("imageScene") == "WB_DFT":
                    url = info.get("url")
                    break
        if not url:
            url, quality = raw.get("urlPre"), "thumbnail"
        url = url if isinstance(url, str) else ""
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("http://"):
            url = "https://" + url[7:]
        images.append({"asset_id": f"img-{index}", "source_url": url or "",
                       "quality": quality if url else "unknown", "state": "pending"})
    return {"note_id": note_id, "source_url": source_url,
            "canonical_url": f"https://www.xiaohongshu.com/explore/{note_id}",
            "source_kind": "xiaohongshu", "title": str(node.get("title") or node.get("displayTitle") or ""),
            "body": body if isinstance(body, str) else "",
            "body_state": ("available" if detail_context else "unverified") if isinstance(body, str) else "missing",
            "note_type": str(node.get("type", "image")), "images": images,
            "expected_images": len(raw_images) if raw_images is not None and detail_context else None,
            "detail_context": "recognized" if detail_context else "unverified",
            "author": str((node.get("user") or {}).get("nickname", "")) if isinstance(node.get("user"), dict) else ""}

