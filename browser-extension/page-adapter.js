/* Shared by the content script and extension worker; only DOM helpers need a page. */
(() => {
  function noteFromUrl(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "www.xiaohongshu.com" ||
          url.username || url.password || (url.port && url.port !== "443")) return null;
      const match = url.pathname.match(/^\/(?:explore|discovery\/item|user\/profile\/[a-f0-9]{24})\/([a-f0-9]{24})\/?$/i);
      return match ? {note_id: match[1].toLowerCase(), source_url: url.href} : null;
    } catch { return null; }
  }
  function pageKey(value) {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "www.xiaohongshu.com" || url.username || url.password)
      throw new Error("仅支持小红书网页");
    if (!/^\/(user\/profile|collection|board)\/[^/]+\/?$/.test(url.pathname))
      throw new Error("请先打开收藏列表页");
    const keep = new URLSearchParams();
    for (const key of ["collection_id", "folder_id", "board_id", "tab", "subTab"])
      if (url.searchParams.has(key)) keep.set(key, url.searchParams.get(key));
    return url.origin + url.pathname + (keep.size ? "?" + keep.toString() : "");
  }
  function countFromLabel(text) {
    const match = text.trim().match(/^[\d,]+$|^(?:笔记|收藏|专辑|文件|Saved|Favorites)\s*[・·:：]?\s*([\d,]+)\s*$|[（(]\s*([\d,]+)\s*[)）]|([\d,]+)\s*篇/i);
    const count = match ? Number((match[1] || match[2] || match[3] || match[0]).replaceAll(",", "")) : null;
    return Number.isInteger(count) && count >= 0 && count <= 100000 ? count : null;
  }
  function rendered(element) {
    if (!element?.getClientRects().length) return false;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (node.hidden || node.getAttribute("aria-hidden") === "true" || style.display === "none" ||
          style.visibility === "hidden" || style.opacity === "0") return false;
      // Collapsed XHS panes keep padding (and sometimes borders), so their
      // outer rectangle can be nonzero while there is no visible content area.
      const rect = node.getBoundingClientRect();
      const contentHeight = node.clientHeight - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0);
      const contentWidth = node.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
      if ((/(hidden|clip)/.test(style.overflowY) && (rect.height < 1 || contentHeight < 1)) ||
          (/(hidden|clip)/.test(style.overflowX) && (rect.width < 1 || contentWidth < 1))) return false;
    }
    return true;
  }
  function layout() {
    const root = document.querySelector("#userPageContainer") || document;
    const panes = [...root.querySelectorAll(".tab-content-item")];
    const active = panes.filter(p => rendered(p) && p.querySelector(".feeds-container"));
    const pane = active.length === 1 ? active[0] : null;
    const feed = panes.length ? pane?.querySelector(".feeds-container") || null : root;
    const candidates = [...root.querySelectorAll('[role="tab"][aria-selected="true"],.reds-tab-item.active,.tab-item.active,.tab.active,[data-tab].active')]
      .filter(rendered);
    const primary = root.querySelector(".xhs-user-page-primary-tabs .reds-tab-item.active") ||
      candidates.find(e => !e.closest(".tab-content-item") && /收藏|笔记|点赞|赞过|浏览记录|Saved|Favorites|Posts|Likes/i.test(e.textContent));
    const secondary = pane?.querySelector(".reds-tab-item.active");
    return {root, pane, feed, primary, secondary, hasPanes: panes.length > 0};
  }
  function inspect() {
    const url = new URL(location.href), view = layout();
    const primary = view.primary?.textContent.trim() || "";
    const secondary = view.secondary?.textContent.trim() || "";
    const tab = url.searchParams.get("tab"), subTab = url.searchParams.get("subTab");
    const folderRoute = /^\/(collection|board)\//.test(url.pathname) ||
      ["collection_id", "folder_id", "board_id"].some(key => url.searchParams.has(key));
    const primarySaved = /^(收藏|Saved|Favorites)/i.test(primary);
    const primaryOther = /^(笔记|点赞|赞过|浏览记录|Posts|Likes)/i.test(primary);
    const profileSaved = /^\/user\/profile\//.test(url.pathname) && (primarySaved || tab === "fav");
    let scope = primaryOther ? "wrong_tab" : (primarySaved || tab === "fav" || folderRoute) ? "saved" :
      ["posts", "posted", "note", "like", "likes", "history"].includes(tab) ? "wrong_tab" : "unknown";
    if (scope === "saved" && profileSaved && (/^(专辑|文件)/.test(secondary) ||
        (!secondary && subTab && subTab !== "note"))) scope = "wrong_subtab";
    if (scope === "saved" && view.hasPanes && !view.feed) scope = "loading";
    const folder = view.root.querySelector('[data-collection-name],.collection-title,.folder-title');
    const labelNode = folder?.cloneNode(true);
    labelNode?.querySelectorAll('[data-note-count],.count').forEach(node => node.remove());
    const folderLabel = (folder?.getAttribute("data-collection-name") || labelNode?.textContent.trim() || "")
      .replace(/\s*[\d,]+\s*篇(?:笔记)?\s*$/, "").replace(/\s*[（(]\s*[\d,]+\s*[)）]\s*$/, "").trim();
    const name = (folderLabel || (profileSaved ? "收藏 · 笔记" :
      view.root.querySelector("h1")?.textContent.trim() || "我的收藏")).slice(0, 200);
    const totalNode = folder?.querySelector('[data-note-count],.count') ||
      (!view.hasPanes ? view.root.querySelector('[data-note-count]') : null);
    const countText = profileSaved && view.secondary ? secondary :
      totalNode?.getAttribute("data-note-count") || totalNode?.textContent.trim() || primary;
    const marker = profileSaved ? "fav:" + (/^(专辑)/.test(secondary) ? "album" : /^(文件)/.test(secondary) ? "file" : subTab || "note") :
      (folderLabel || primary || name).replace(/\s*[（(]\s*[\d,]+\s*[)）]\s*$/, "").replace(/^(收藏|Saved|Favorites)\s*[\d,]+\s*$/i, "$1").trim();
    return {url: location.href, title: document.title, name, expected_count: countFromLabel(countText), scope,
      visible_cards: cards(view).length, view_marker: marker,
      identity_marker: profileSaved ? marker : folderRoute ? "saved" : marker};
  }
  function cards(view = layout()) {
    const result = new Map();
    if (!view.feed) return [];
    const roots = [...view.feed.querySelectorAll('section.note-item,.note-item,[data-note-id]')].filter(rendered);
    for (const root of roots) {
      const links = [...root.querySelectorAll("a[href]")].filter(rendered);
      // Prefer the actual signed cover/title link over the hidden canonical link.
      const link = links.find(a => a.matches("a.cover,a.title") && noteFromUrl(a.href)) || links.find(a => noteFromUrl(a.href));
      if (!link) continue;
      const parsed = noteFromUrl(link.href);
      const title = root.querySelector(".title,[class*='title']")?.textContent || root.querySelector("img")?.alt || link.textContent || "";
      result.set(parsed.note_id, {...parsed, title: title.trim().slice(0, 1000)});
    }
    return [...result.values()];
  }
  function endMarker() {
    const view = layout(), root = view.pane || view.feed;
    if (!root) return false;
    return [...root.querySelectorAll(".end-container,.feeds-end,.no-more,.list-end,[class*='end-tip'],[data-xhs-context-end]")]
      .some(node => rendered(node) && /^[-\s]*(?:THE END|没有更多了|没有更多|到底了)[-\s]*$/i.test(node.textContent.trim()));
  }
  function scrollTarget() {
    let element = layout().feed;
    while (element && element !== document.body && element.nodeType === 1) {
      if (/(auto|scroll)/.test(getComputedStyle(element).overflowY) && element.scrollHeight > element.clientHeight + 10) return element;
      element = element.parentElement;
    }
    // Generic collection layouts may put their scroller inside the selected root.
    const card = layout().feed?.querySelector(".note-item");
    for (let parent = card?.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      if (/(auto|scroll)/.test(getComputedStyle(parent).overflowY) && parent.scrollHeight > parent.clientHeight + 10) return parent;
    }
    return document.scrollingElement;
  }
  function pageError(url, visibleText = "", readableNote = false) {
    let parsed;
    try { parsed = new URL(url); } catch { return null; }
    if (parsed.protocol !== "https:" || parsed.hostname !== "www.xiaohongshu.com") return null;
    if (parsed.pathname.replace(/\/$/, "") === "/website-login/error") {
      const code = parsed.searchParams.get("error_code");
      if (code === "300013") return "ACCESS_RESTRICTED";
      if (code === "300017") return "SOURCE_LINK_EXPIRED";
      return "ACCESS_BLOCKED";
    }
    // Only page-level error content counts. A normal note may discuss these words.
    if (!readableNote && /安全限制/.test(visibleText) && /检测到异常访问/.test(visibleText) && /\b300013\b/.test(visibleText))
      return "ACCESS_RESTRICTED";
    if (/^\/(login|signin)\/?$/.test(parsed.pathname) || parsed.pathname.startsWith("/website-login/"))
      return "ACCESS_BLOCKED";
    return null;
  }
  function readNote(noteId) {
    const root = document.querySelector("#noteContainer"), title = root?.querySelector("#detail-title");
    const desc = root?.querySelector("#detail-desc");
    const readable = !!root && rendered(root) && !!(title || desc);
    const error = pageError(location.href, document.body?.innerText || "", readable);
    if (error) return {error};
    const current = noteFromUrl(location.href);
    if (!current || current.note_id !== noteId) return {pending: true};
    if (!root || !rendered(root) || (!title && !desc)) {
      const unavailable = [...document.querySelectorAll('.error-container,.error-page,.note-error')]
        .some(e => rendered(e) && /不存在|无法查看|已删除/.test(e.textContent));
      return unavailable ? {error: "SOURCE_UNAVAILABLE"} : {pending: true};
    }
    return {snapshot: {note_id: noteId, page_url: location.href, title: title?.innerText.trim() || "",
      body: desc?.innerText || "", body_present: !!desc}};
  }
  globalThis.XhsContextPage = {noteFromUrl, pageKey, countFromLabel, rendered, inspect, cards, endMarker, scrollTarget, readNote, pageError};
})();
