let activeJob = null, busy = false, lastResult = null, refreshVersion = 0;
let actionError = "", loadError = "", scopeKey = null;
let activeImageJob = null;
const el = id => document.getElementById(id);
const errors = {
  HOST_NOT_INSTALLED: "请先双击项目里的“安装.cmd”，安装本机组件。",
  HOST_DISCONNECTED: "本机组件暂时断开，正在自动重连，未确认保存的正文会保留并重新提交。",
  NATIVE_TIMEOUT: "本机组件响应超时，正在重新连接并重交未确认的结果。",
  SYNC_ALREADY_RUNNING: "另一个同步进程正在运行。关闭旧的 start-sync 窗口或另一浏览器后重试。",
  SCAN_ALREADY_RUNNING: "另一个浏览器或旧的扩展记录仍在读取这个收藏夹。它 30 分钟没有进展后可在这里重新开始，也可以用命令 sync abandon 结束旧的读取。",
  SCAN_FINALIZED: "这次列表读取已在别处结束，已保存的条目都保留；可以重新读取当前收藏夹。",
  ACCESS_BLOCKED: "网页拦截了访问，可能要求登录或验证，内容处理已暂停。",
  ACCESS_RESTRICTED: "小红书安全限制（300013）：自动读取已锁定。请停止重试，按平台提示处理；已保存正文仍可离线使用。",
  RATE_LIMITED: "小红书限制了请求频率，自动读取已锁定。请停止重试，先按平台提示处理。",
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
  SOURCE_UNAVAILABLE: "原笔记已删除或不可访问。",
  SOURCE_LINK_EXPIRED: "笔记访问链接已失效（300017）。",
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

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const count = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
function setText(id, value) {
  const text = String(value ?? "");
  if (el(id).textContent !== text) el(id).textContent = text;
}
function showMessage() {
  const connectionError = lastResult?.connection_error;
  const message = actionError || loadError ||
    (lastResult?.server_status?.access_restriction ? explain(lastResult.server_status.access_restriction.error) :
      lastResult?.restriction_pending ? explain("ACCESS_RESTRICTED") : "") ||
    (connectionError && connectionError !== "HOST_NOT_INSTALLED" ? explain(connectionError) : "");
  setText("message", message);
  el("message").hidden = !message;
}
function meter(id, numerator, denominator, description) {
  const known = Number.isFinite(denominator) && denominator > 0;
  const percent = known ? Math.min(100, Math.max(0, numerator / denominator * 100)) : null;
  const node = el(id + "-meter");
  node.dataset.indeterminate = String(!known);
  node.setAttribute("aria-valuetext", description);
  if (known) {
    node.setAttribute("aria-valuemin", "0");
    node.setAttribute("aria-valuemax", "100");
    node.setAttribute("aria-valuenow", String(Math.round(percent)));
  } else {
    for (const attr of ["aria-valuenow", "aria-valuemin", "aria-valuemax"]) node.removeAttribute(attr);
  }
  el(id + "-fill").style.transform = "scaleX(" + (percent === null ? 0 : percent / 100) + ")";
}
function render(result) {
  lastResult = result;
  const page = result.page, session = result.session, job = result.server_status?.job;
  const pageSession = result.page_session, connected = !result.connection_error;
  const restricted = !!result.server_status?.access_restriction || !!result.restriction_pending;
  const imageJobs = result.server_status?.image_jobs || [];
  const imageJob = imageJobs.find(job => job.job_id === activeImageJob) || imageJobs[0];
  activeImageJob = imageJob?.job_id || null;
  el('image-card').hidden = !connected || (!result.note && !session && !imageJob);
  el('images-current').hidden = !result.note;
  el('images-current').disabled = busy || restricted;
  el('images-collection').hidden = !session;
  el('images-collection').disabled = busy || restricted;
  el('images-pause').hidden = !imageJob || imageJob.state !== 'active';
  el('images-pause').disabled = busy;
  el('images-resume').hidden = !imageJob || imageJob.state === 'active' || imageJob.complete;
  el('images-resume').disabled = busy || restricted;
  setText('image-progress', imageJob ?
    '图片已保存 ' + imageJob.images_saved + ' 张 · ' + (imageJob.items.ready || 0) + ' / ' + imageJob.notes + ' 篇图片齐全' :
    '补图单独排队，不改变正文读取进度。');
  setText('image-detail', imageJob ?
    (imageJob.state === 'paused' ? '已暂停。' : imageJob.state === 'active' ? '正在后台补图，关闭面板也会继续。' : '本轮补图已结束。') +
    (imageJob.error ? ' ' + explain(imageJob.error) : '') +
    imageJob.failures.map(item => ' ' + item.count + ' 篇：' + explain(item.error)).join('') :
    (session ? '收藏夹：' + session.name : '读取当前笔记已加载的图片区域。'));
  const hostMissing = result.connection_error === "HOST_NOT_INSTALLED";
  const sameTask = !!session && pageSession?.job_id === session.job_id;
  const pageScanning = pageSession && !pageSession.finalized && pageSession.scan_state === "scanning";
  const invalidScope = !page || ["wrong_tab", "wrong_subtab", "loading"].includes(page.scope);
  const key = page ? [page.page_key, page.identity_marker, page.view_marker, page.scope].join("|") : "";
  if (scopeKey !== null && key !== scopeKey) {
    actionError = "";
    el("confirmed").checked = false;
    el("name").value = "";
    el("expected").value = "";
  }
  scopeKey = key;
  activeJob = session?.job_id || null;
  setText("connection", connected ? "本机已就绪" : "未连接");
  el("connection").classList.toggle("offline", !connected);
  el("setup").hidden = !hostMissing;
  setText("folder", page?.name || "从收藏页开始");
  el("target-card").hidden = hostMissing || (!page && !!session) || (sameTask && !session.finalized);
  el("target-card").classList.toggle("compact", sameTask && !!session.finalized && !invalidScope);
  el("advanced").hidden = !page || el("target-card").hidden || invalidScope;
  const expectedPage = page?.expected_count;
  setText("scope", page ? (page.scope === "wrong_tab" ? "请先切换到小红书的“收藏”标签。" :
    page.scope === "wrong_subtab" ? "请切换到收藏里的“笔记”标签。" :
    page.scope === "loading" ? "正在等待收藏列表加载…" :
    expectedPage !== null && expectedPage !== undefined ? "页面显示 " + expectedPage + " 篇笔记" : "将读取当前收藏列表 · 总数未知") :
    result.page_error || "请在小红书打开“收藏 → 笔记”，再刷新页面识别。");
  el("confirmation").hidden = !page || page.scope !== "unknown";
  el("confirmed").disabled = busy;
  el("ready-guide").hidden = invalidScope;
  el("inspect").hidden = !invalidScope || hostMissing;
  el("inspect").disabled = busy;
  el("start").hidden = invalidScope;
  const resumable = pageSession && !pageSession.finalized && !pageScanning;
  setText("start", pageScanning ? "正在读取…" : resumable ? "继续读取收藏夹" :
    pageSession?.finalized ? "更新当前收藏夹" : "读取当前收藏夹");
  el("start").disabled = busy || !connected || restricted || invalidScope || pageScanning ||
    (page?.scope === "unknown" && !el("confirmed").checked);
  el("reconnect").disabled = busy;
  el("refresh").disabled = busy;
  el("activity").hidden = !session;
  for (const id of ["pause", "resume", "end-check", "browser-help"]) el(id).hidden = true;
  if (session) {
    setText("task-name", session.name + " · 后台任务");
    el("task-name").title = session.name;
    const scanning = !session.finalized && session.scan_state === "scanning";
    const counts = job?.items || {};
    const total = count(job?.discovered), saved = count(job?.body_available);
    const queued = count(counts.queued), running = count(counts.running), browserRunning = count(counts.browser_running);
    const unfinished = queued + running + browserRunning;
    const failed = count(counts.partial) + count(counts.failed);
    const paused = job?.state === "paused" || (!session.finalized && session.scan_state === "paused");
    const retry = !paused && !restricted && job?.state === 'active' ? job.browser_retry || result.browser_wait : null;
    const retrySeconds = retry ? Math.max(0, Math.ceil((retry.retry_at - Date.now()) / 1000)) : 0;
    const awaiting = !session.finalized && session.scan_state === "awaiting_confirmation";
    const complete = !!job?.body_complete;
    const title = !connected ? "本机连接中断" : !job ? "正在获取读取进度" :
      restricted ? "访问受限，自动读取已停止" : complete ? (total ? "正文已读完，可供 Agent 使用" : "收藏列表为空") :
      paused ? "已暂停" : retry ? "暂时中断，将自动继续" : awaiting ? "等待核对列表末尾" :
      session.scan_state === "error" ? "读取需要处理" : session.finalized && !unfinished ?
      (failed ? "部分正文需要处理" : "已读笔记正文已保存") :
      browserRunning ? "正在读取笔记正文" : running ? "正在保存正文" : scanning ? "正在读取收藏列表" : "等待读取正文";
    const previousTitle = el("status-title").textContent;
    setText("status-title", title);
    if (previousTitle !== title && previousTitle !== "正在获取读取进度" && !reducedMotion.matches) {
      el("status-title").getAnimations().forEach(animation => animation.cancel());
      el("status-title").animate([{opacity: .5, transform: "translateY(3px)"}, {opacity: 1, transform: "translateY(0)"}],
        {duration: 160, easing: "ease-out"});
    }
    setText("progress", job ? (total ? "已发现 " + total + " 篇 · 正文已保存 " + saved + " 篇" : "尚未发现笔记") : "正在接收收藏列表…");
    const expected = job?.expected_count;
    const knownTotal = typeof expected === "number" && Number.isFinite(expected) && expected >= 0;
    setText("list-value", job ? total + (knownTotal ? " / " + expected : "") + " 篇" : "—");
    setText("body-value", job ? saved + " / " + total + " 篇" : "—");
    const listCaption = !connected ? "连接恢复后更新进度" : !job ? "正在获取状态" :
      job.list_complete ? "列表已收齐" : knownTotal && total > expected ? "数量超过页面总数，请核对" :
      knownTotal ? "列表尚未确认收齐" : "总数未知 · 列表完整性待确认";
    setText("list-state", listCaption);
    el("list-state").classList.toggle("pending", !!job && !job.list_complete);
    setText("body-state", !connected ? "当前无法确认正文状态" : !job ? "正在获取状态" :
      total ? (saved >= total ? "已发现的正文已保存" : "以已发现笔记为基准") : "暂无需要保存的笔记");
    meter("list", total, job && knownTotal ? expected : null, listCaption + (job ? "，已发现 " + total + " 篇" : ""));
    meter("body", saved, job ? total : null, job ? "已发现 " + total + " 篇，正文已保存 " + saved + " 篇" : "状态待获取");
    const detail = [];
    if (job && !job.list_complete) detail.push(knownTotal ? "列表尚未收齐（" + total + "/" + expected + "）" : "列表完整性尚未确认");
    if (counts.failed) detail.push(counts.failed + " 篇失败");
    if (counts.partial) detail.push(counts.partial + " 篇未取得正文");
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
    setText("detail-summary", failed ? failed + " 篇未完成 · 查看原因" : "读取详情");
    el("browser-help").hidden = restricted || !connected || !job || job.content_mode === "browser" || saved >= total ||
      !(failed || (paused && job.error) || (session.finalized && !unfinished));
    const accessBlocked = [job?.error, ...reasons.map(reason => reason.error)].some(error => String(error).includes("ACCESS_BLOCKED"));
    setText("recovery-title", accessBlocked ? "网页要求登录或验证" : (failed ? failed + " 篇正文待补全" : "补全未完成正文"));
    el("browser-start").disabled = busy || !connected || restricted;
    el("open-source").disabled = busy;
    el("pause").hidden = paused || (!scanning && !unfinished);
    el("pause").disabled = busy || !connected;
    el("resume").hidden = !paused && (scanning || !(failed || (!session.finalized && !scanning)));
    el("resume").disabled = busy || !connected || restricted;
    setText("resume", paused ? "继续读取正文" : session.finalized ? "重试未完成正文" : "继续读取");
    el("end-check").hidden = !connected || !sameTask || session.finalized ||
      !["awaiting_confirmation", "error"].includes(session.scan_state);
    for (const id of ["finish", "partial"]) el(id).disabled = busy || !connected;
    el("resume").classList.toggle("secondary", !el("browser-help").hidden || !el("end-check").hidden);
    const attention = session.scan_error || result.browser_error || job?.error || result.last_error;
    const note = !connected ? "已保存的内容仍在本机，扩展会自动重连。" : !job ? "任务已保留，正在等待本机返回进度。" :
      retry ? explain(retry.error) + (retrySeconds ? ' 约 ' + retrySeconds + ' 秒后自动重试，无需点击继续。' : ' 正在等待后台调度自动重试。') :
      paused && ['BROWSER_BUSY', 'BROWSER_TIMEOUT', 'BROWSER_CLOSED', 'BROWSER_INTERRUPTED'].includes(job.error) ?
      explain(job.error) + ' 连续尝试未成功，已暂停；处理后可点击继续。' :
      attention ? explain(attention) : complete ? (total ? "现在可以让 Agent 搜索、追读和引用。图片内容尚未处理。" : "当前列表没有笔记。可以返回收藏页核对。") :
      paused ? "已保存的正文会保留，可以随时继续读取。" : job.content_mode === "browser" ? "扩展正在逐篇打开后台笔记页，读取后关闭。" :
      session.finalized && !unfinished ? "已保存的正文可以使用；列表完整性请以上方状态为准。" :
      scanning ? "关闭面板也会继续。列表读取时，请保留原收藏标签页。" : "正文在后台保存，关闭面板也能继续。";
    setText("action-note", note);
    el("action-note").hidden = !el("end-check").hidden || !el("browser-help").hidden;
  }
  showMessage();
}
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
    busy = true;
    ++refreshVersion; // An earlier poll must not overwrite the pending action.
    actionError = "";
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
      if (!el(id).getClientRects().length) {
        const next = ["pause", "resume", "finish", "open-source", "start", "inspect"]
          .map(el).find(node => !node.disabled && node.getClientRects().length);
        next?.focus({preventScroll: true});
      }
    }
  });
}
bind("start", async () => {
  if (!el("expected").checkValidity()) {
    el("advanced").open = true;
    el("expected").reportValidity();
    throw new Error("已核对总数应为 0–100000 的整数，也可以留空。");
  }
  const request = {type: "start", name: el("name").value, confirmed: el("confirmed").checked};
  if (el("expected").value.trim()) request.expected_count = Number(el("expected").value);
  activeJob = (await send(request)).job_id;
});
bind("refresh", async () => {});
bind("inspect", async () => {});
bind("reconnect", async () => {});
bind("pause", () => send({type: "pause", job_id: activeJob}));
bind("resume", () => send({type: "resume", job_id: activeJob}));
bind("browser-start", () => send({type: "browser-start", job_id: activeJob}));
bind("open-source", () => send({type: "open-source", job_id: activeJob}));
bind("finish", () => send({type: "finish", job_id: activeJob, verified: true}));
bind("partial", () => send({type: "finish", job_id: activeJob, verified: false}));
bind('images-current', async () => { activeImageJob = (await send({type: 'images-current'})).job_id; });
bind('images-collection', async () => { activeImageJob = (await send({type: 'images-collection', job_id: activeJob})).job_id; });
bind('images-pause', () => send({type: 'images-pause', job_id: activeImageJob}));
bind('images-resume', () => send({type: 'images-resume', job_id: activeImageJob}));
el("confirmed").addEventListener("change", () => { if (lastResult) render(lastResult); });
refresh().catch(refreshFailed);
setInterval(() => { if (!busy) refresh().catch(refreshFailed); }, 2500);

