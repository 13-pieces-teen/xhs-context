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
  const authenticationMessage = /登录后|请[^。！\n]{0,30}登录|扫码登录|登录账号|登录[^。！\n]{0,20}(?:查看|浏览|继续|重试)|需要登录|验证码|安全验证|完成验证/;
  const recoverableMessage = /网络|连接|加载|稍后|重试|刷新|暂时|临时|访问频繁/;
  function pageError(url, visibleText = "", readableNote = false) {
    let parsed;
    try { parsed = new URL(url); } catch { return null; }
    if (parsed.protocol !== "https:" || parsed.hostname !== "www.xiaohongshu.com") return null;
    // A page-level security message also wins on an unfamiliar error-page URL.
    if (!readableNote && /安全限制/.test(visibleText) && /检测到异常访问/.test(visibleText) && /\b300013\b/.test(visibleText))
      return "ACCESS_RESTRICTED";
    if (parsed.pathname.replace(/\/$/, "") === "/website-login/error") {
      const code = parsed.searchParams.get("error_code");
      if (code === "300013") return "ACCESS_RESTRICTED";
      if (!readableNote && authenticationMessage.test(visibleText)) return "ACCESS_BLOCKED";
      if (code === "300017") return "SOURCE_LINK_EXPIRED";
      return "ACCESS_BLOCKED";
    }
    if (/^\/(login|signin)\/?$/.test(parsed.pathname) || parsed.pathname.startsWith("/website-login/"))
      return "ACCESS_BLOCKED";
    return null;
  }
  function unavailableNote() {
    const visibleText = document.body?.innerText || '';
    if (authenticationMessage.test(visibleText) || recoverableMessage.test(visibleText)) return false;
    const standalone = /^(?:很?抱歉[，,！!：:]?\s*)?(?:(?:当前|该|这篇)?笔记(?:已被|已经|已)删除|(?:当前|该|这篇)?笔记不存在|找不到(?:对应的?)?笔记)[。.!！]?(?:\s*(?:这篇|该|当前|此)?笔记已无法访问[。.!！]?)?(?:\s*(?:返回首页|返回上一页|返回))*$/;
    // An empty note shell can contain the site's own error panel. The caller
    // already ruled out readable note content; exclude authored regions, not
    // the whole shell, or a deleted note waits forever for a body that cannot load.
    const excluded = '#detail-desc,#detail-title,[class*="comment"],.note-item,.feeds-container,blockquote,template';
    return [...document.querySelectorAll('.error-container,.error-page,.note-error,.error-wrapper,.error-message,.error-text,.error-title,.not-found,.not-found-container,.note-not-found,.note-unavailable,.empty-container,.empty-state,.empty-page,.reds-empty,[role="alert"],main,main h1,main h2')]
      .some(element => {
        if (!rendered(element) || element.closest(excluded)) return false;
        const text = (element.innerText || '').trim();
        // Ignore quoted/user-authored descendants, even inside an error wrapper.
        if (element.querySelector(excluded)) return false;
        const main = element.closest('main');
        if (main && !standalone.test((main.innerText || '').trim())) return false;
        return standalone.test(text);
      });
  }
  function readNote(noteId) {
    const root = document.querySelector("#noteContainer");
    const titleNode = root?.querySelector("#detail-title"), descNode = root?.querySelector("#detail-desc");
    const title = titleNode && rendered(titleNode) ? titleNode : null;
    const desc = descNode && rendered(descNode) ? descNode : null;
    const readable = hasReadableNote(root);
    const error = pageError(location.href, document.body?.innerText || "", readable);
    if (error) {
      // Unknown error codes remain access failures unless the rendered page
      // explicitly says this note is unavailable. Never infer a code's meaning.
      if (error === 'ACCESS_BLOCKED' && !readable &&
          new URL(location.href).pathname.replace(/\/$/, '') === '/website-login/error' && unavailableNote())
        return {error: 'SOURCE_UNAVAILABLE'};
      return {error};
    }
    const current = noteFromUrl(location.href);
    if (!current || current.note_id !== noteId) return {pending: true};
    if (!readable) {
      return unavailableNote() ? {error: "SOURCE_UNAVAILABLE"} : {pending: true};
    }
    return {snapshot: {note_id: noteId, page_url: location.href, title: title?.innerText.trim() || "",
      body: desc?.innerText || "", body_present: !!desc}};
  }
  function hasReadableNote(root) {
    return !!root && rendered(root) && [...root.querySelectorAll('#detail-title,#detail-desc')]
      .some(node => rendered(node) && /[^\s\u200b-\u200f\ufeff]/.test(node.innerText || ''));
  }
  function readImages(noteId) {
    const diagnosis = readNote(noteId);
    if (diagnosis.error) return diagnosis;
    const root = document.querySelector('#noteContainer');
    const error = pageError(location.href, document.body?.innerText || '', hasReadableNote(root));
    if (error) return {error};
    if (noteFromUrl(location.href)?.note_id !== noteId || !root || !rendered(root)) return {pending: true};
    // Read only gallery DOM. Avatars, comments and recommendation thumbnails are excluded.
    const gallery = root.querySelector('.media-container,.note-slider,.swiper-container,.swiper,[data-xhs-gallery]');
    const video = root.querySelector('video');
    if (!gallery && video && rendered(video)) return {snapshot: {
      note_id: noteId, page_url: location.href, images: [], image_indices: [], expected_images: 0, note_type: 'video'}};
    if (!gallery) return {error: 'IMAGE_LIST_UNAVAILABLE'};
    const indexed = new Map(), unordered = [];
    const imageUrl = value => {
      try {
        const url = new URL(value, location.href);
        if (url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443') &&
            (url.hostname === 'xhscdn.com' || url.hostname.endsWith('.xhscdn.com') || url.hostname === 'ci.xiaohongshu.com')) return url.href;
      } catch {}
      return null;
    };
    const imgUrl = img => (img?.complete && imageUrl(img.currentSrc)) || imageUrl(img?.src) || imageUrl(img?.getAttribute('data-src'));
    for (const slide of gallery.querySelectorAll('.swiper-slide,[data-xhs-image-index]')) {
      const index = slide.getAttribute('data-swiper-slide-index') ?? slide.getAttribute('data-xhs-image-index');
      const img = slide.querySelector('img');
      let url = imgUrl(img);
      if (!url) {
        const background = slide.querySelector('[style*="background-image"]') || slide;
        const match = getComputedStyle(background).backgroundImage?.match(/^url\(["']?(.*?)["']?\)$/);
        if (match) url = imageUrl(match[1]);
      }
      if (!url) continue;
      if (index !== null && /^\d+$/.test(index)) indexed.set(Number(index), url);
      else unordered.push(url);
    }
    if (!indexed.size && !unordered.length) {
      for (const img of gallery.querySelectorAll('img')) {
        const url = imgUrl(img);
        if (url) unordered.push(url);
      }
    }
    const images = [...new Set([...Array.from(indexed).sort((a, b) => a[0] - b[0]).map(entry => entry[1]), ...unordered])];
    if (images.length > 100) return {error: 'IMAGE_LIST_TOO_LARGE'};
    const paginationRendered = node => {
      if (!node?.getClientRects().length) return false;
      for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        if (ancestor.hidden || ancestor.getAttribute('aria-hidden') === 'true' ||
            style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)) return false;
      }
      return true;
    };
    const totalNode = [...gallery.querySelectorAll('.swiper-pagination-total,[data-xhs-image-total]')].find(paginationRendered);
    const totalLabel = totalNode?.getAttribute('data-xhs-image-total') || totalNode?.textContent?.trim() || '';
    // XHS uses a `1/17` fraction that fades out until hover. Its rendered
    // pagination still supplies a total; loaded URL count alone never does.
    const fraction = [...gallery.querySelectorAll('.fraction,.swiper-pagination-fraction')].find(paginationRendered);
    const fractionMatch = fraction?.textContent?.trim().match(/^(\d+)\s*\/\s*(\d+)$/);
    const expected = /^\d+$/.test(totalLabel) ? Number(totalLabel) :
      fractionMatch && Number(fractionMatch[1]) >= 1 && Number(fractionMatch[1]) <= Number(fractionMatch[2]) ? Number(fractionMatch[2]) : null;
    if ([...indexed.keys()].some(index => index >= (expected ?? 100))) return {error: 'IMAGE_LIST_UNAVAILABLE'};
    if (!images.length && expected !== 0) return {error: 'IMAGE_LIST_UNAVAILABLE'};
    return {snapshot: {note_id: noteId, page_url: location.href, images,
      image_indices: images.map(url => { const found = [...indexed].find(entry => entry[1] === url); return found ? found[0] + 1 : null; }),
      expected_images: expected !== null && expected >= images.length && expected <= 100 ? expected : null,
      note_type: gallery.querySelector('video') ? 'video' : 'image'}};
  }
  let galleryRead = null, galleryPromise = null;
  function collectImages(noteId) {
    // Content message retries can overlap if a page is slow. Reuse the work so
    // two callers never advance the same carousel independently.
    if (!galleryPromise) galleryPromise = collectGallery(noteId).finally(() => { galleryPromise = null; });
    return galleryPromise;
  }
  async function collectGallery(noteId) {
    if (!galleryRead || galleryRead.noteId !== noteId)
      galleryRead = {noteId, images: new Map(), expected: null, steps: 0, stalled: 0, waiting: 0, started: Date.now()};
    const state = galleryRead;
    // Only the extension's background reader opts into advancing the gallery.
    // Short bounded chunks keep pause and lease checks responsive between calls.
    for (let step = 0; step < 8; step++) {
      const result = readImages(noteId);
      if (!result.snapshot) return result;
      const snapshot = result.snapshot;
      if (state.expected !== null && snapshot.expected_images !== null && state.expected !== snapshot.expected_images)
        state.images.clear();
      if (snapshot.expected_images !== null) state.expected = snapshot.expected_images;
      const reusedUrl = snapshot.images.some((url, i) => snapshot.image_indices[i] !== null &&
        [...state.images.values()].some(old => old.index !== null && old.index !== snapshot.image_indices[i] && old.url === url));
      if (reusedUrl) {
        // Virtualized galleries can change a slide index before replacing its
        // image URL. Wait for that replacement instead of assigning the old
        // image to the new position or clicking ahead again.
        if (++state.waiting >= 24 || Date.now() - state.started > 35000) return {snapshot: state.lastSnapshot};
        await new Promise(resolve => setTimeout(resolve, 280));
        continue;
      }
      state.waiting = 0;
      snapshot.images.forEach((url, i) => {
        const index = snapshot.image_indices[i];
        if (index !== null) state.images.delete(url);
        state.images.set(index === null ? url : index, {url, index});
      });
      const entries = [...state.images.values()].sort((a, b) => (a.index ?? 101) - (b.index ?? 101));
      // Conflicting totals/positions must not turn old observations into an
      // invalid or falsely complete manifest. Keep the latest safe observation.
      if (entries.length > 100 || state.expected !== null && entries.length > state.expected ||
          new Set(entries.map(item => item.url)).size !== entries.length) return result;
      const merged = {...snapshot, images: entries.map(item => item.url), image_indices: entries.map(item => item.index),
        expected_images: state.expected};
      state.lastSnapshot = merged;
      const full = state.expected !== null && entries.length === state.expected &&
        (entries.every(item => item.index === null) || entries.every((item, i) => item.index === i + 1));
      if (full || state.expected === null || state.steps >= Math.min(100, state.expected + 2) ||
          state.stalled >= 3 || Date.now() - state.started > 35000) return {snapshot: merged};
      const gallery = document.querySelector('#noteContainer .media-container,#noteContainer .note-slider,#noteContainer [data-xhs-gallery]');
      const next = gallery?.querySelector('.arrow-controller.right,.swiper-button-next,[data-xhs-gallery-next]');
      if (!next || next.matches('.forbidden,.disabled,.swiper-button-disabled,[disabled],[aria-disabled="true"]'))
        return {snapshot: merged};
      // Never move a gallery after the user brings this reader tab to the front.
      if (document.visibilityState === 'visible') return {snapshot: merged};
      const active = () => gallery.querySelector('.swiper-slide-active')?.getAttribute('data-swiper-slide-index');
      const before = active(), beforeCount = state.images.size;
      next.click();
      state.steps++;
      await new Promise(resolve => setTimeout(resolve, 280));
      const after = readImages(noteId);
      if (after.error) return after;
      const newImages = after.snapshot?.images.some((url, i) => !state.images.has(after.snapshot.image_indices[i] ?? url));
      state.stalled = active() === before && !newImages && state.images.size === beforeCount ? state.stalled + 1 : 0;
    }
    return {pending: true};
  }
  globalThis.XhsContextPage = {noteFromUrl, pageKey, countFromLabel, rendered, inspect, cards, endMarker, scrollTarget, readNote, readImages, collectImages, pageError};
})();
