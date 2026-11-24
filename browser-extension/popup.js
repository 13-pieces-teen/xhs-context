let activeJob = null, busy = false, lastResult = null, refreshVersion = 0;
let actionError = "", actionNotice = "", loadError = "", scopeKey = null;
let noticeTimer = null, visibleNotice = "";
let activeImageJob = null;
let invalidOffset = 0, invalidLoaded = false, invalidLoading = false, invalidTotal = 0;
let invalidPageTotal = null;
const el = id => document.getElementById(id);
const errors = {
  HOST_NOT_INSTALLED: "请先双击项目里的“安装.cmd”，安装本机组件。",
  HOST_DISCONNECTED: "本机组件暂时断开，正在自动重连，未确认保存的正文会保留并重新提交。",
  NATIVE_TIMEOUT: "本机组件响应超时，正在重新连接并重交未确认的结果。",
  SYNC_ALREADY_RUNNING: "另一个同步进程正在运行。关闭旧的 start-sync 窗口或另一浏览器后重试。",
  SCAN_ALREADY_RUNNING: "另一个浏览器或旧的扩展记录仍在读取这个收藏夹。它 30 分钟没有进展后可在这里重新开始，也可以用命令 sync abandon 结束旧的读取。",
  SCAN_FINALIZED: "这次列表读取已在别处结束，已保存的条目都保留；可以重新读取当前收藏夹。",
  ACCESS_BLOCKED: "网页拦截了访问，可能要求登录或验证，内容处理已暂停。",
  ACCESS_RESTRICTED: "访问受限，读取已暂停。处理平台提示后，点击“重新检查”，再手动继续。",
  RATE_LIMITED: "请求过于频繁，读取已暂停。稍后点击“重新检查”，再手动继续。",
  LOCAL_NETWORK_BLOCKED: "本机 DNS / 代理配置阻止了连接。",
  URL_NOT_ALLOWED: "旧版网络检查拦截了请求，可使用浏览器补全。",
  PROXY_CONFIGURATION: "本机代理配置不受支持，需要 HTTP 或 HTTPS 代理。",
  PROXY_ERROR: "本机代理暂时无法连接。",
  NETWORK_ERROR: "网络连接失败或超时。",
  BROWSER_CLOSED: "读取用的后台标签页已关闭。",
  BROWSER_BUSY: "浏览器暂时无法操作标签页，可能正在拖动标签页。",
  BROWSER_INTERRUPTED: "读取期间本机连接中断。",
  BROWSER_ERROR: "浏览器操作失败，请检查浏览器后继续。",
  BROWSER_TIMEOUT: "笔记页未在等待时间内加载完成。",
  BROWSER_PARSE_FAILED: "未识别到笔记正文。",
  BODY_MISSING: "未取得笔记正文，可稍后重试。",
  BODY_UNVERIFIED: "只取得了列表摘要，尚未取得正文。",
  BODY_EMPTY: "该笔记没有可保存的文字正文。",
  NOTE_NOT_FOUND: "返回的页面不包含这篇笔记，可能需要登录。",
  SOURCE_UNAVAILABLE: "笔记失效或已不可访问，已加入失效列表，不再自动读取。",
  SOURCE_LINK_EXPIRED: "笔记访问链接已失效（300017），已加入失效列表，不再自动读取。",
  MEDIA_DEFERRED: "图片尚未保存，可以单独补充。",
  IMAGE_LIST_UNAVAILABLE: "当前页面没有已加载的图片区域，请打开笔记详情并查看图片。",
  IMAGE_ALREADY_QUEUED: "这篇笔记已有图片任务，请查看图片进度。",
  IMAGE_COUNT_UNKNOWN: "图片总数尚未确认，已保存的图片可以使用。",
  IMAGE_LIST_INCOMPLETE: "图片列表尚未收齐，已保存的图片可以使用。",
  IMAGE_LIST_UNVERIFIED: "仅保存了已显示的部分图片，尚未确认完整列表。",
  NOTE_NOT_ARCHIVED: "请先让这篇笔记进入本地收藏列表，再补充图片。",
  INCOMPLETE_IMAGE_REFRESH: "本次补图未成功，已保留先前保存的图片。",
  REVISION_CONFLICT: "补图期间正文版本发生变化，已保留正文；请重新发起补图。"
};
function explain(value) {
  return [...new Set(String(value).split(",").map(part => errors[part.split(":")[0]] || part))].join(" ");
}
async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response.ok) throw new Error(explain(response.error));
  return response.data;
}

const count = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
function setText(id, value) {
  const text = String(value ?? "");
  const target = el(id).querySelector('[data-button-label]') || el(id);
  if (target.textContent !== text) target.textContent = text;
}
function showMessage() {
  const connectionError = lastResult?.connection_error;
  const message = actionError || loadError ||
    (lastResult?.server_status?.access_restriction ? explain(lastResult.server_status.access_restriction.error) :
      lastResult?.restriction_pending ? explain("ACCESS_RESTRICTED") : "") ||
    (connectionError && connectionError !== "HOST_NOT_INSTALLED" ? explain(connectionError) : "") || actionNotice;
  setText("message", message);
  const isNotice = !!message && message === actionNotice;
  if (isNotice && visibleNotice !== message) {
    clearTimeout(noticeTimer);
    visibleNotice = message;
    noticeTimer = setTimeout(() => {
      if (actionNotice === visibleNotice) actionNotice = "";
      visibleNotice = "";
      showMessage();
    }, 3500);
  } else if (!isNotice) {
    clearTimeout(noticeTimer);
    visibleNotice = "";
  }
  el("message").hidden = !message;
  el("message").setAttribute('role', isNotice ? 'status' : 'alert');
  el("message").classList.toggle("info", !!message && message === actionNotice);
  el("message").classList.toggle("alert-success", !!message && message === actionNotice);
  el("message").classList.toggle("alert-error", !message || message !== actionNotice);
  window.popupView.messageChanged(message === actionNotice ? '' : message);
  // A global error takes the place of the normal contextual hint.
  if (message && !isNotice) el("action-note").hidden = true;
}
function meter(id, numerator, denominator, description) {
  const known = Number.isFinite(denominator) && denominator > 0;
  const percent = known ? Math.min(100, Math.max(0, numerator / denominator * 100)) : null;
  // Rounding must not turn a nearly finished task into a visible 100%.
  const displayPercent = percent === null ? null : percent < 100 ? Math.min(99, Math.round(percent)) : 100;
  const node = el(id + "-meter");
  node.dataset.indeterminate = String(!known);
  node.setAttribute("aria-valuetext", description);
  if (known) {
    node.setAttribute("aria-valuemin", "0");
    node.setAttribute("aria-valuemax", "100");
    node.setAttribute("aria-valuenow", String(displayPercent));
  } else {
    for (const attr of ["aria-valuenow", "aria-valuemin", "aria-valuemax"]) node.removeAttribute(attr);
  }
  node.style.setProperty("--value", String(percent ?? 0));
  const label = el(id + "-percent");
  const text = displayPercent === null ? "—" : displayPercent + "%";
  if (label.textContent !== text) {
    label.replaceChildren(document.createTextNode(displayPercent === null ? "—" : String(displayPercent)));
    if (displayPercent !== null) {
      const unit = document.createElement('span');
      unit.className = 'ring-unit';
      unit.textContent = '%';
      label.append(unit);
    }
  }
}
function render(result) {
  lastResult = result;
  const totalInvalid = count(result.server_status?.invalid_notes?.total);
  el('invalid-notes').hidden = !totalInvalid;
  setText('invalid-summary', '失效列表（' + totalInvalid + '）');
  if (totalInvalid !== invalidTotal) {
    invalidTotal = totalInvalid;
    invalidLoaded = false;
    if (el('invalid-notes').open) void loadInvalidNotes(true);
  }
  const page = result.page, session = result.session, job = result.server_status?.job;
  const pageSession = result.page_session, connected = !result.connection_error;
  const restricted = !!result.server_status?.access_restriction || !!result.restriction_pending;
  const imageJobs = result.server_status?.image_jobs || [];
  const imageJob = imageJobs.find(job => job.job_id === activeImageJob) || imageJobs[0];
  activeImageJob = imageJob?.job_id || null;
  el('image-card').hidden = !connected || !imageJob;
  el('images-current').hidden = !result.note && !session;
  el('images-current').disabled = busy || !connected || restricted || !result.note;
  el('images-current').title = result.note ? '保存当前笔记已加载的图片' : '打开笔记页后可保存图片';
  el('images-collection').hidden = !session && !result.note;
  el('images-collection').disabled = busy || !connected || restricted || !session;
  el('images-collection').title = session ? '补充「' + session.name + '」的图片' : '先读取收藏，再补充收藏夹图片';
  el('images-pause').hidden = !imageJob || imageJob.state !== 'active';
  el('images-pause').disabled = busy || !connected;
  const imagesSettled = imageJob && count(imageJob.items.skipped) > 0 &&
    !['queued', 'running', 'browser_running', 'failed', 'partial'].some(state => count(imageJob.items[state])) && !count(imageJob.needs_repair);
  el('images-resume').hidden = !imageJob || imageJob.state === 'active' || imageJob.complete || imagesSettled;
  el('images-resume').disabled = busy || !connected || restricted;
  setText('images-resume', imageJob?.state === 'paused' ? '继续补图' : '重试补图');
  setText('image-progress', imageJob ?
    '已保存 ' + count(imageJob.images_saved) + ' 张 · ' +
      (imageJob.complete ? '已齐全' : imagesSettled ? '已结束 · 有跳过项' : imageJob.state === 'paused' ? '已暂停' :
        count(imageJob.items.browser_running) ? '读取图片列表中' : imageJob.state === 'active' ? '保存图片中' : '待补全') : '');
  const imageProgress = imageJob?.progress || {};
  const downloadProgress = count(imageProgress.downloading) ? '当前笔记已保存 ' + count(imageProgress.current_saved_images) +
    (imageProgress.current_expected_images == null ? ' 张（总数未确认）' : ' / ' + count(imageProgress.current_expected_images) + ' 张') + '。' : '';
  setText('image-stage', imageJob ? downloadProgress +
    (count(imageProgress.reading_gallery) ? '正在后台读取图片列表，读完会自动关闭页面。' :
      imageJob.state === 'active' && count(imageProgress.waiting_download) ? '正在保存已读取的图片。' :
      imageJob.state === 'paused' ? '已保存的图片保留，继续时只补缺失项。' :
      imageJob.complete ? '本地图片已齐全。' : '先读取图片列表，再下载缺失图片；失效笔记自动跳过。') : '');
  setText('image-detail', imageJob ?
    Math.max(0, count(imageJob.items?.ready) - count(imageJob.needs_repair)) + ' / ' + count(imageJob.notes) + ' 篇图片齐全。' +
    (count(imageProgress.already_complete) ? ' ' + count(imageProgress.already_complete) + ' 篇本地已有完整图片，无需重新读取。' : '') +
    (count(imageJob.needs_repair) ? ' ' + count(imageJob.needs_repair) + ' 篇本地图片缺失，可继续补图。' : '') +
    ' 补图会先核对本地文件；缺少图片列表时打开后台笔记页，读取后关闭，再下载缺失图片。数量未确认的笔记不会显示为齐全。' +
    (imageJob.error ? ' ' + explain(imageJob.error) : '') +
    (imageJob.failures || []).map(item => ' ' + item.count + ' 篇：' + explain(item.error)).join('') : '');
  const hostMissing = result.connection_error === "HOST_NOT_INSTALLED";
  const sameTask = !!session && pageSession?.job_id === session.job_id;
  const pendingBodies = job && !job.read_complete && !job.body_complete &&
    ['queued', 'running', 'browser_running', 'partial', 'failed'].some(state => count(job.items?.[state]));
  const pageScanning = pageSession && !pageSession.finalized && pageSession.scan_state === "scanning";
  const invalidScope = !page || ["wrong_tab", "wrong_subtab", "loading"].includes(page.scope);
  const key = page ? [page.page_key, page.identity_marker, page.view_marker, page.scope].join("|") : "";
  if (scopeKey !== null && key !== scopeKey) {
    window.popupView.closeForPanel('advanced');
    actionError = "";
    el("confirmed").checked = false;
    el("name").value = "";
    el("expected").value = "";
  }
  scopeKey = key;
  if (activeJob && activeJob !== session?.job_id) window.popupView.closeForPanel('task-details');
  activeJob = session?.job_id || null;
  setText("connection", connected ? "已连接" : "未连接");
  el("connection").classList.toggle("offline", !connected);
  el("connection").classList.toggle("badge-success", connected);
  el("connection").classList.toggle("badge-warning", !connected);
  el("setup").hidden = !hostMissing;
  setText("folder", page?.name || (result.note ? "保存笔记图片" : "从收藏页开始"));
  setText("target-label", result.note && !page ? "当前笔记" : "当前收藏夹");
  el("folder").title = page?.name || "";
  el("target-card").hidden = hostMissing || (!page && !!session) || (sameTask && (!session.finalized || pendingBodies));
  el("target-card").classList.toggle("compact", sameTask && !!session.finalized && !invalidScope);
  const canConfigure = !!page && !el("target-card").hidden && !invalidScope;
  if (!canConfigure) el("advanced").open = false;
  el("advanced").hidden = !canConfigure || !el("advanced").open;
  el("settings-toggle").disabled = !canConfigure;
  el("settings-toggle").title = canConfigure ? "修正当前页面的名称与总数" : "识别到可读取的收藏页后可设置";
  const expectedPage = page?.expected_count;
  setText("scope", page ? (page.scope === "wrong_tab" ? "请先切换到小红书的“收藏”标签。" :
    page.scope === "wrong_subtab" ? "请切换到收藏里的“笔记”标签。" :
    page.scope === "loading" ? "正在等待收藏列表加载…" :
    expectedPage !== null && expectedPage !== undefined ? expectedPage + " 篇笔记" : "总数未知") :
    result.note ? "保存当前笔记已加载的图片。" : result.page_error || "请在小红书打开“收藏 → 笔记”，再刷新页面识别。");
  el("target-footnote").hidden = invalidScope;
  el("confirmation").hidden = !page || page.scope !== "unknown";
  el("confirmed").disabled = busy;
  el("inspect").hidden = !invalidScope || hostMissing;
  el("inspect").disabled = busy;
  el("start").hidden = invalidScope;
  const resumable = pageSession && !pageSession.finalized && !pageScanning;
  setText("start", pageScanning ? "正在读取…" : resumable ? "继续读取" :
    pageSession?.finalized ? "更新收藏" : "开始读取");
  el("start").classList.toggle("secondary", !!session && !sameTask);
  el("start").disabled = busy || !connected || restricted || invalidScope || pageScanning ||
    (page?.scope === "unknown" && !el("confirmed").checked);
  el("reconnect").disabled = busy;
  el("refresh").disabled = busy;
  setText("refresh", restricted ? "重新检查" : "刷新");
  el("refresh").title = "检查访问状态并刷新进度";
  el("activity").hidden = !session;
  el("open-source").hidden = !session;
  el("open-source").title = session ? '打开任务「' + session.name + '」的收藏页' : '';
  el("browser-start").hidden = !session;
  el("browser-start").disabled = true;
  el("browser-start").title = "有未完成正文时可补全";
  el("details-toggle").disabled = !session;
  if (!session) el("task-details").open = false;
  el("task-details").hidden = !session || !el("task-details").open;
  for (const id of ["pause", "resume", "end-check", "browser-help"]) el(id).hidden = true;
  if (session) {
    setText("task-name", session.name);
    el("task-name").title = session.name;
    const scanning = !session.finalized && session.scan_state === "scanning";
    const counts = job?.items || {};
    const total = count(job?.discovered), saved = count(job?.body_available);
    const queued = count(counts.queued), running = count(counts.running), browserRunning = count(counts.browser_running);
    const unfinished = queued + running + browserRunning;
    const failed = count(counts.partial) + count(counts.failed);
    const skipped = count(job?.skipped_count ?? counts.skipped);
    const paused = job?.state === "paused" || (!session.finalized && session.scan_state === "paused");
    const retry = !paused && !restricted && job?.state === 'active' ? job.browser_retry || result.browser_wait : null;
    const retrySeconds = retry ? Math.max(0, Math.ceil((retry.retry_at - Date.now()) / 1000)) : 0;
    const awaiting = !session.finalized && session.scan_state === "awaiting_confirmation";
    const complete = !!job?.body_complete;
    const readComplete = !!job?.read_complete || complete;
    const title = !connected ? "连接中断" : !job ? "获取进度中" :
      restricted ? "访问受限" : complete ? (total ? "正文已保存" : "列表为空") :
      readComplete && skipped ? "已结束 · 跳过 " + skipped + " 篇" :
      paused ? "已暂停" : retry ? "等待自动重试" : awaiting ? "待核对末尾" :
      session.scan_state === "error" ? "需要处理" : session.finalized && !unfinished ?
      (failed ? "正文待补全" : skipped ? "已结束 · 有失效笔记" : "已读正文已保存") :
      browserRunning ? "读取正文中" : running ? "保存正文中" : scanning ? "读取列表中" : "等待正文";
    const previousTitle = el("status-title").textContent;
    setText("status-title", title);
    if (previousTitle !== title && previousTitle !== "正在获取读取进度" && !window.popupView.reduceEffects()) {
      el("status-title").getAnimations().forEach(animation => animation.cancel());
      el("status-title").animate([{opacity: .5, transform: "translateY(3px)"}, {opacity: 1, transform: "translateY(0)"}],
        {duration: 180, easing: "ease-out"});
    }
    setText("progress", job ? (total ? "已发现 " + total + " 篇 · 正文已保存 " + saved + " 篇" +
      (skipped ? " · 已跳过 " + skipped + " 篇失效笔记" : "") : "尚未发现笔记") : "正在接收收藏列表…");
    const expected = job?.expected_count;
    const knownTotal = typeof expected === "number" && Number.isFinite(expected) && expected >= 0;
    setText("list-value", connected && job ? total + (knownTotal ? " / " + expected : "") + " 篇" : "—");
    setText("body-value", connected && job ? saved + " / " + total + " 篇" : "—");
    const listCaption = !connected ? "等待连接" : !job ? "获取中" :
      job.list_complete ? "列表已收齐" : knownTotal && total > expected ? "超出总数 · 待核对" :
      knownTotal ? "尚未确认收齐" : "总数未知 · 待确认";
    setText("list-state", listCaption);
    el("list-state").classList.toggle("pending", !!job && !job.list_complete);
    setText("body-state", !connected ? "等待连接" : !job ? "获取中" :
      skipped ? "已跳过 " + skipped + " 篇" :
      total ? (saved >= total ? "已发现正文已保存" : "占已发现笔记") : "暂无笔记");
    meter("list", total, connected && job && knownTotal ? expected : null, listCaption + (connected && job ? "，已发现 " + total + " 篇" : ""));
    meter("body", saved, connected && job ? total : null, connected && job ? "已发现 " + total + " 篇，正文已保存 " + saved + " 篇" : "状态待获取");
    const detail = [];
    if (job && !job.list_complete) detail.push(knownTotal ? "列表尚未收齐（" + total + "/" + expected + "）" : "列表完整性尚未确认");
    if (counts.failed) detail.push(counts.failed + " 篇失败");
    if (counts.partial) detail.push(counts.partial + " 篇未取得正文");
    for (const reason of job?.skipped_summary || []) detail.push(reason.count + " 篇：" + explain(reason.error));
    if (queued) detail.push(queued + " 篇排队中");
    if (running) detail.push(running + " 篇处理中");
    if (browserRunning) detail.push(browserRunning + " 篇正在读取网页");
    if (result.queued) detail.push("正在保存新发现的内容");
    if (job?.error) detail.push(explain(job.error));
    if (session.scan_error) detail.push(explain(session.scan_error));
    if (result.last_error) detail.push(explain(result.last_error));
    if (result.browser_error) detail.push(explain(result.browser_error));
    if (retry) detail.push('等待自动重试' + (retry.max_attempts ? '（已尝试 ' + retry.attempts + '/' + retry.max_attempts + ' 次）' : ''));
    setText("detail", detail.join("；") || (job ? "笔记正文已保存到本机，可以搜索和引用。" : "正在等待本机返回最新状态。"));
    const reasons = job?.failure_summary || [];
    el("failure-reason").hidden = !reasons.length;
    setText("failure-reason", reasons.map(r => r.count + " 篇：" + explain(r.error)).join("\n"));
    setText("detail-summary", failed ? failed + " 篇未完成 · 查看原因" : skipped ? skipped + " 篇已跳过 · 查看原因" : "读取详情");
    el("browser-help").hidden = restricted || !connected || !job || readComplete || (!unfinished && !failed) || job.content_mode === "browser" || saved >= total ||
      !(failed || (paused && job.error) || (session.finalized && !unfinished));
    const accessBlocked = [job?.error, ...reasons.map(reason => reason.error)].some(error => String(error).includes("ACCESS_BLOCKED"));
    setText("recovery-title", accessBlocked ? "网页要求登录或验证" : (failed ? failed + " 篇正文待补全" : "补全未完成正文"));
    el("browser-start").disabled = busy || el("browser-help").hidden;
    el("browser-start").title = el("browser-help").hidden ? "有未完成正文且允许读取时可补全" : "逐篇打开后台笔记页，读取后关闭";
    el("open-source").disabled = busy;
    el("pause").hidden = paused || (!scanning && !unfinished);
    el("pause").disabled = busy || !connected;
    el("resume").hidden = readComplete || (!paused && (scanning || !(failed || (!session.finalized && !scanning))));
    el("resume").disabled = busy || !connected || restricted;
    setText("resume", paused ? "继续读取" : session.finalized ? "重试正文" : "继续读取");
    el("end-check").hidden = restricted || !connected || !sameTask || session.finalized ||
      !["awaiting_confirmation", "error"].includes(session.scan_state);
    for (const id of ["finish", "partial"]) el(id).disabled = busy || !connected;
    el("resume").classList.toggle("secondary", !el("end-check").hidden);
    el("pause").classList.toggle("secondary", !el("end-check").hidden);
    // Keep continuing the scan available beside partial save, without stacking
    // a second full-height action under the end-of-list confirmation.
    const checkingEnd = !el("end-check").hidden;
    el("resume").classList.toggle("quiet", checkingEnd);
    const resumeParent = el(checkingEnd ? "verification-options" : "task-actions");
    if (el("resume").parentElement !== resumeParent) resumeParent.append(el("resume"));
    // Resolve list verification first; keep recovery available without stacking two notices.
    if (!el("end-check").hidden) el("browser-help").hidden = true;
    const attention = session.scan_error || result.browser_error || job?.error || result.last_error;
    const note = !connected ? "已保存内容保留，正在重连。" : !job ? "正在获取进度…" :
      retry ? (retrySeconds ? '约 ' + retrySeconds + ' 秒后自动重试，无需点击继续。' : '等待自动重试，无需点击继续。') :
      paused && ['BROWSER_BUSY', 'BROWSER_TIMEOUT', 'BROWSER_CLOSED', 'BROWSER_INTERRUPTED'].includes(job.error) ?
      '重试未成功，处理后可继续。原因见详情。' :
      attention ? explain(attention) : complete ? (total ? "正文可供 Agent 搜索和引用。" : "可返回收藏页核对。") :
      readComplete && skipped ? "失效项已跳过，已保存正文仍可使用。" :
      paused ? "已保存正文保留，可随时继续。" : job.content_mode === "browser" ? "后台逐篇打开笔记，读取后关闭。" :
      session.finalized && !unfinished ? "已保存正文可用，列表范围见上方。" :
      scanning ? "关闭面板可继续，请保留收藏页。" : "后台保存中，关闭面板可继续。";
    setText("action-note", note);
    el("action-note").hidden = !el("end-check").hidden || !el("browser-help").hidden;
  }
  el("quick-actions").hidden = !["open-source", "browser-start", "images-current", "images-collection"].some(id => !el(id).hidden);
  syncComponentVariants();
  window.popupView.sync();
  showMessage();
}
function syncComponentVariants() {
  for (const button of document.querySelectorAll('button.btn')) {
    const quiet = button.classList.contains('quiet');
    const secondary = button.classList.contains('secondary') ||
      (button.id === 'start' && el('target-card').classList.contains('compact'));
    button.classList.toggle('btn-primary', !quiet && !secondary);
    button.classList.toggle('btn-ghost', quiet);
  }
}
async function loadInvalidNotes(reset = false) {
  if (invalidLoading) return;
  invalidLoading = true;
  el('invalid-more').disabled = true;
  setText('invalid-status', '正在读取失效列表…');
  const requestedTotal = invalidTotal;
  let reload = false;
  try {
    const offset = reset ? 0 : invalidOffset;
    const result = await send({type: 'invalid-notes', offset});
    // New failures sort before older rows. Never append an offset page from a
    // different list snapshot, which would duplicate rows and omit new ones.
    if (requestedTotal !== invalidTotal && result.total !== invalidTotal) {
      invalidLoaded = false;
      reload = true;
      return;
    }
    invalidTotal = result.total;
    setText('invalid-summary', '失效列表（' + invalidTotal + '）');
    if (offset && result.total !== invalidPageTotal) {
      invalidLoaded = false;
      reload = true;
      return;
    }
    if (!offset) el('invalid-items').replaceChildren();
    for (const item of result.items) {
      const row = document.createElement('li');
      row.className = 'list-row';
      const content = document.createElement('div');
      content.className = 'list-col-grow';
      const title = document.createElement('strong');
      title.textContent = item.title || item.note_id;
      const reason = document.createElement('p');
      reason.className = 'hint';
      reason.textContent = (item.error === 'SOURCE_LINK_EXPIRED' ? '访问链接失效' : '笔记不存在或已不可访问') +
        ' · ' + item.note_id;
      content.append(title, reason);
      row.append(content);
      el('invalid-items').append(row);
    }
    invalidOffset = offset + result.items.length;
    invalidPageTotal = result.total;
    invalidLoaded = true;
    el('invalid-more').hidden = !result.has_more;
    setText('invalid-status', '已显示 ' + invalidOffset + ' / ' + result.total + ' 篇');
  } catch (error) {
    setText('invalid-status', error.message);
    el('invalid-more').hidden = false;
  } finally {
    invalidLoading = false;
    el('invalid-more').disabled = false;
    if (reload && el('invalid-notes').open) void loadInvalidNotes(true);
  }
}
el('invalid-notes').addEventListener('toggle', () => {
  if (el('invalid-notes').open && !invalidLoaded) void loadInvalidNotes(true);
});
el('invalid-more').addEventListener('click', () => { void loadInvalidNotes(!invalidLoaded); });
async function refresh() {
  const version = ++refreshVersion;
  const result = await send({type: "state", job_id: activeJob});
  if (version !== refreshVersion) return;
  loadError = "";
  render(result);
}
function refreshFailed(error) {
  loadError = error.message;
  showMessage();
}
function bind(id, action) {
  el(id).addEventListener("click", async () => {
    if (busy) return;
    const hadFocus = document.activeElement === el(id);
    busy = true;
    ++refreshVersion; // An earlier poll must not overwrite the pending action.
    actionError = "";
    actionNotice = "";
    loadError = "";
    if (lastResult) render(lastResult);
    el(id).disabled = true;
    try { await action(); }
    catch (error) { actionError = error.message; showMessage(); }
    finally {
      busy = false;
      // Re-enable controls without replacing the job just returned by start.
      const requestedJob = activeJob;
      const requestedImageJob = activeImageJob;
      if (lastResult) render(lastResult);
      else el(id).disabled = false;
      activeJob = requestedJob;
      activeImageJob = requestedImageJob;
      await refresh().catch(refreshFailed);
      if (!actionError && ['images-current', 'images-collection', 'images-resume'].includes(id) && !el('image-card').hidden)
        el('image-card').scrollIntoView({block: 'nearest', behavior: 'instant'});
      if (hadFocus && (document.activeElement === el(id) || document.activeElement === document.body) &&
          (!el(id).getClientRects().length || el(id).disabled)) {
        const candidates = id.startsWith('images-') ?
          ["images-pause", "images-resume", "images-current", "images-collection", "open-source", "inspect", "help-toggle"] :
          ["pause", "resume", "finish", "browser-start", "open-source", "start", "inspect", "refresh"];
        const next = candidates
          .map(el).find(node => !node.disabled && node.getClientRects().length);
        next?.focus({preventScroll: true});
      }
    }
  });
}
bind("start", async () => {
  if (!el("expected").checkValidity()) {
    window.popupView.open('advanced', {force:true});
    window.popupView.validateSettings();
    throw new Error("已核对总数应为 0–100000 的整数，也可以留空。");
  }
  const request = {type: "start", name: el("name").value, confirmed: el("confirmed").checked};
  if (el("expected").value.trim()) request.expected_count = Number(el("expected").value);
  activeJob = (await send(request)).job_id;
});
bind("refresh", async () => {
  const result = await send({type: 'refresh-access'});
  if (result.unlocked) actionNotice = '可手动继续读取；若仍受限会再次暂停。';
});
bind("inspect", async () => {});
bind("reconnect", async () => {});
bind("pause", () => send({type: "pause", job_id: activeJob}));
bind("resume", () => send({type: "resume", job_id: activeJob}));
bind("browser-start", () => send({type: "browser-start", job_id: activeJob}));
bind("open-source", () => send({type: "open-source", job_id: activeJob}));
bind("finish", () => send({type: "finish", job_id: activeJob, verified: true}));
bind("partial", () => send({type: "finish", job_id: activeJob, verified: false}));
bind('images-current', async () => { activeImageJob = (await send({type: 'images-current'})).job_id; });
function imageActionResult(result) {
  activeImageJob = result.job_id;
  actionNotice = result.complete ? '图片已齐全。' : result.state === 'finished' ? '本轮补图已结束，请查看图片进度。' :
    '已开始补图，读取图片列表后保存图片。';
}
bind('images-collection', async () => { imageActionResult(await send({type: 'images-collection', job_id: activeJob})); });
bind('images-pause', () => send({type: 'images-pause', job_id: activeImageJob}));
bind('images-resume', async () => { imageActionResult(await send({type: 'images-resume', job_id: activeImageJob})); });
window.popupView.onSettingsDone = () => {
  if (actionError === "已核对总数应为 0–100000 的整数，也可以留空。") actionError = "";
  showMessage();
};
el("confirmed").addEventListener("change", () => { if (lastResult) render(lastResult); });
refresh().catch(refreshFailed);
setInterval(() => { if (!busy) refresh().catch(refreshFailed); }, 2500);

