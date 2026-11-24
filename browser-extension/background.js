import {Journal} from "./journal.mjs";
import {noteFromUrl, pageKey} from "./shared.mjs";
import {NativeClient} from "./native.mjs";
import {BrowserReader} from "./browser-reader.mjs";

const journal = new Journal(chrome.storage.local);
const native = new NativeClient(chrome.runtime);
const browserReader = new BrowserReader(chrome.tabs, native, journal, stopScans);
let flushing = null;
let acquisitionEpoch = 0;
const ready = chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"}).then(async () => {
  acquisitionEpoch = (await journal.load()).acquisition_epoch || 0;
});

async function stopScans() {
  await ready;
  const epoch = ++acquisitionEpoch;
  const sessions = Object.values((await journal.load()).sessions).filter(s => !s.finalized && s.scan_state === "scanning");
  await journal.mutate(state => {
    state.acquisition_epoch = Math.max(state.acquisition_epoch || 0, epoch);
    for (const session of sessions) {
      state.sessions[session.job_id].scan_state = "paused";
      state.sessions[session.job_id].scan_error = "ACCESS_RESTRICTED";
    }
    // A previous click must not become a delayed resume after manual unlocking.
    state.queue = state.queue.filter(q => !/\/(resume|browser)$/.test(q.path));
  });
  await Promise.allSettled(sessions.map(s => chrome.tabs.sendMessage(s.tab_id, {type: "pause"})));
}

async function requireAcquisition(jobId, {scan = false} = {}) {
  if (browserReader.refreshing && !scan) throw new Error('正在刷新，请稍后继续读取。');
  if ((await journal.load()).restriction_pending) throw new Error("ACCESS_RESTRICTED");
  const status = await native.request("/v1/status" + (jobId ? "?job_id=" + jobId : ""));
  if (status.access_restriction) throw new Error("ACCESS_RESTRICTED");
  return status;
}

function checkIntent(epoch) {
  if (epoch !== acquisitionEpoch) throw new Error('读取状态已改变，请重新点击读取。');
}
async function acquisitionIntent(jobId) {
  const epoch = acquisitionEpoch;
  await requireAcquisition(jobId);
  checkIntent(epoch);
  return epoch;
}
function activate(epoch, path, body) {
  checkIntent(epoch);
  if (browserReader.refreshing) throw new Error('正在刷新，请稍后继续读取。');
  // NativeClient posts synchronously: no await may separate this check/send.
  return native.request(path, body);
}
function updateIntent(epoch, update) {
  return journal.mutate(state => { checkIntent(epoch); return update(state); });
}
function cancelPendingAcquisition() {
  const epoch = ++acquisitionEpoch;
  return journal.mutate(state => {
    state.acquisition_epoch = Math.max(state.acquisition_epoch || 0, epoch);
    state.queue = state.queue.filter(q => !/\/(resume|browser)$/.test(q.path));
  });
}

async function flush() {
  await ready;
  if (flushing) return flushing;
  flushing = (async () => {
    for (;;) {
      const pending = (await journal.load()).queue[0];
      if (!pending) break;
      try {
        await native.request(pending.path, pending.body);
        await journal.acknowledge(pending.id);
      } catch (error) {
        if (error.message === "SCAN_FINALIZED" && /\/batches$/.test(pending.path)) {
          const tabId = await journal.dropFinalizedScan(pending.job_id);
          if (tabId !== null) chrome.tabs.sendMessage(tabId, {type: "pause"}).catch(() => {});
          continue;
        }
        await journal.mutate(s => { s.last_error = String(error.message).slice(0, 200); });
        break;
      }
    }
  })().finally(() => { flushing = null; });
  return flushing;
}

async function resumeImages(jobId, epoch) {
  await flush();
  const pausePath = '/v1/image-jobs/' + jobId + '/pause';
  if ((await journal.load()).queue.some(q => q.path === pausePath))
    throw new Error('暂停操作尚未同步，请重新连接本机组件后继续补图。');
  const result = await activate(epoch, '/v1/image-jobs/' + jobId + '/browser', {});
  await updateIntent(epoch, s => {
    if (s.image_paused) delete s.image_paused[jobId];
    s.browser_error = null; s.browser_wait = null;
  });
  void browserReader.pump();
  return result;
}

async function inspectTab(tabId) {
  let info;
  try { info = await chrome.tabs.sendMessage(tabId, {type: "inspect"}); }
  catch { throw new Error("请刷新小红书页面，再打开扩展。"); }
  return {...info, tab_id: tabId, page_key: pageKey(info.url)};
}
async function currentPage() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !tab.url?.startsWith("https://www.xiaohongshu.com/"))
    throw new Error("请在小红书打开要读取的收藏夹。");
  if (noteFromUrl(tab.url)) throw new Error('当前是笔记页，可以单独保存图片。');
  return inspectTab(tab.id);
}
async function currentNote() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  const note = noteFromUrl(tab?.url);
  return note && tab?.id ? {...note, tab_id: tab.id} : null;
}

async function contentSession(message, sender, allowMoved = false) {
  if (!sender.tab || !sender.url?.startsWith("https://www.xiaohongshu.com/")) throw new Error("无效页面来源");
  const session = (await journal.load()).sessions[message.job_id];
  if (!session || session.tab_id !== sender.tab.id || (!allowMoved && pageKey(sender.url) !== session.page_key))
    throw new Error("当前页面与收藏夹不匹配，请回到原页面。");
  return session;
}

async function scan(session, page, epoch) {
  await updateIntent(epoch, s => {
    s.active_job_id = session.job_id;
    s.sessions[session.job_id].tab_id = page.tab_id;
    s.sessions[session.job_id].view_marker = page.view_marker;
    s.sessions[session.job_id].scan_error = "";
  });
  checkIntent(epoch);
  const result = await chrome.tabs.sendMessage(page.tab_id, {type: "start", job_id: session.job_id,
    page_key: session.page_key, view_marker: page.view_marker});
  if (result?.error) throw new Error(result.error);
}

async function handle(message, sender) {
  await ready;
  if (message.type === "scan-continue") {
    await contentSession(message, sender);
    const status = await requireAcquisition(message.job_id, {scan: true});
    if (status.job?.state !== "active") throw new Error("读取已暂停");
    return {};
  }
  if (message.type === "capture") {
    await contentSession(message, sender);
    if (!Array.isArray(message.items) || message.items.length < 1 || message.items.length > 25)
      throw new Error("批次应包含 1–25 条");
    const items = message.items.map(item => {
      const parsed = noteFromUrl(item.source_url);
      if (!parsed || parsed.note_id !== item.note_id) throw new Error("笔记链接不匹配");
      return {...parsed, title: String(item.title || "").slice(0, 1000)};
    });
    await journal.mutate(state => {
      const session = state.sessions[message.job_id];
      if (session.finalized) throw new Error("读取已结束，请更新收藏夹。");
      const seq = session.next_seq++;
      session.discovered += items.length;
      state.queue.push({id: crypto.randomUUID(), job_id: message.job_id,
        path: "/v1/jobs/" + message.job_id + "/batches", body: {seq, items}});
    });
    void flush();
    return {accepted: items.length};
  }
  if (message.type === "scan-state") {
    await contentSession(message, sender, ["error", "paused"].includes(message.state));
    if (!["scanning", "paused", "awaiting_confirmation", "page_end", "error"].includes(message.state))
      throw new Error("无效读取状态");
    await journal.mutate(state => {
      const session = state.sessions[message.job_id];
      // A scan closed by the local host stays closed; late page reports are ignored.
      if (session.finalized) return;
      session.scan_state = message.state;
      session.scan_error = String(message.error || "").slice(0, 200);
      if (message.error === 'ACCESS_RESTRICTED') state.restriction_pending = true;
    });
    // Respond to the page before asking it to pause: pause waits for scan.done.
    if (message.error === 'ACCESS_RESTRICTED') void browserReader.pump();
    if (message.state === "page_end") await finalize(message.job_id, "page_end");
    return {};
  }
  if (sender.url !== chrome.runtime.getURL("popup.html")) throw new Error("请从扩展面板操作。");

  if (message.type === 'refresh-access') return browserReader.refreshAccess();
  if (message.type === 'invalid-notes') {
    const offset = message.offset ?? 0;
    if (!Number.isInteger(offset) || offset < 0 || offset > 100000) throw new Error('无效的列表位置');
    return native.request('/v1/invalid-notes?limit=50&offset=' + offset);
  }

  if (message.type === "state") {
    let page = null, pageError = null;
    try { page = await currentPage(); } catch (error) { pageError = error.message; }
    const state = await journal.load();
    const sessions = Object.values(state.sessions).sort((a, b) => b.started_at.localeCompare(a.started_at));
    const pageSession = page ? sessions.find(s => s.page_key === page.page_key &&
      (!s.view_marker || s.view_marker === page.view_marker)) : null;
    const session = state.sessions[message.job_id] || state.sessions[state.active_job_id] || pageSession || sessions[0];
    let serverStatus = null, connectionError = null;
    try {
      serverStatus = await native.request("/v1/status" + (session ? "?job_id=" + session.job_id : ""));
      if (serverStatus.access_restriction && !browserReader.refreshing) await stopScans();
    }
    catch (error) { connectionError = error.message; }
    void flush();
    void browserReader.pump();
    return {page, note: await currentNote(), page_error: pageError, page_session: pageSession, session, queued: state.queue.length,
      last_error: state.last_error, browser_error: state.browser_error, browser_wait: state.browser_wait, restriction_pending: state.restriction_pending,
      connection_error: connectionError, server_status: serverStatus};
  }
  if (message.type === 'images-current') {
    const epoch = await acquisitionIntent();
    const note = await currentNote();
    if (!note) throw new Error('请打开已归档笔记的详情页。');
    const result = await chrome.tabs.sendMessage(note.tab_id, {type: 'read-images', note_id: note.note_id});
    if (result?.error === 'ACCESS_RESTRICTED') {
      await journal.mutate(s => { s.restriction_pending = true; });
      await stopScans();
      await native.request('/v1/access/restrict', {error: 'ACCESS_RESTRICTED'});
      await journal.mutate(s => { s.restriction_pending = false; });
    }
    if (result?.error) throw new Error(result.error);
    if (!result?.snapshot) throw new Error('图片区域尚未加载，请稍后再试。');
    return activate(epoch, '/v1/image-jobs', {job_id: crypto.randomUUID(), snapshot: result.snapshot});
  }
  if (message.type === 'images-collection') {
    const epoch = await acquisitionIntent();
    const session = (await journal.load()).sessions[message.job_id];
    if (!session) throw new Error('请先读取收藏列表。');
    const result = await activate(epoch, '/v1/image-jobs', {job_id: crypto.randomUUID(), collection_id: session.collection_id, browser: true});
    checkIntent(epoch);
    if (result.reused && (result.state === 'paused' || !result.browser_mode)) return resumeImages(result.job_id, epoch);
    void browserReader.pump();
    return result;
  }
  if (['images-pause', 'images-resume'].includes(message.type)) {
    if (message.type === 'images-resume') {
      const epoch = await acquisitionIntent();
      return resumeImages(message.job_id, epoch);
    }
    await cancelPendingAcquisition();
    await journal.mutate(s => {
      (s.image_paused ||= {})[message.job_id] = true;
      const path = '/v1/image-jobs/' + message.job_id + '/pause';
      if (!s.queue.some(q => q.path === path))
        s.queue.push({id: crypto.randomUUID(), job_id: message.job_id, path, body: {}});
    });
    await flush();
    return {};
  }

  if (message.type === "start") {
    const epoch = await acquisitionIntent();
    const page = await currentPage();
    if (page.scope === "wrong_tab") throw new Error("请先切换到“收藏”标签。");
    if (page.scope === "wrong_subtab") throw new Error("请切换到收藏里的“笔记”标签。");
    if (page.scope === "loading") throw new Error("收藏列表仍在加载，请稍后读取。");
    if (page.scope !== "saved" && message.confirmed !== true) throw new Error("请确认当前是要读取的收藏列表。");
    const name = String(message.name || page.name).trim();
    if (!name || name.length > 200) throw new Error("收藏夹名称应为 1–200 个字符。");
    const expected = message.expected_count === undefined ? page.expected_count : message.expected_count;
    if (expected !== null && (!Number.isInteger(expected) || expected < 0 || expected > 100000))
      throw new Error("数量应为整数，也可以留空。");
    await native.request("/v1/status");
    const idHash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(page.page_key + "\n" + page.identity_marker));
    const collectionId = "view-" + [...new Uint8Array(idHash)].map(x => x.toString(16).padStart(2, "0")).join("").slice(0, 24);
    const existing = Object.values((await journal.load()).sessions).find(s =>
      s.collection_id === collectionId && !s.finalized);
    if (existing) {
      await flush();
      await activate(epoch, "/v1/jobs/" + existing.job_id + "/resume", {});
      await updateIntent(epoch, s => { s.sessions[existing.job_id].user_paused = false; s.browser_error = null; s.browser_wait = null; });
      await scan(existing, page, epoch);
      void flush();
      return {job_id: existing.job_id, resumed: true};
    }
    const jobId = crypto.randomUUID();
    const session = {job_id: jobId, collection_id: collectionId, tab_id: page.tab_id,
      page_key: page.page_key, view_marker: page.view_marker, name,
      next_seq: 0, discovered: 0, scan_state: "scanning", finalized: false, started_at: new Date().toISOString()};
    // Deliver a pending finish first, so the previous scan of this view is closed.
    await flush();
    await activate(epoch, "/v1/jobs", {job_id: jobId, expected_count: expected,
      collection: {id: collectionId, name, source_url: page.page_key, identity_kind: "local_view"}});
    await journal.mutate(state => {
      for (const old of Object.keys(state.sessions)) {
        if (Object.keys(state.sessions).length <= 20) break;
        if (state.sessions[old].finalized && !state.queue.some(q => q.job_id === old)) delete state.sessions[old];
      }
      // A created job remains discoverable if refresh cancelled its delayed
      // response; its scan must wait for a new explicit user action.
      state.sessions[jobId] = epoch === acquisitionEpoch ? session :
        {...session, user_paused: true, scan_state: 'paused'};
      state.active_job_id = jobId;
    });
    checkIntent(epoch);
    void flush();
    try { await scan(session, page, epoch); }
    catch (error) {
      await updateIntent(epoch, state => { state.sessions[jobId].scan_state = "error"; state.sessions[jobId].scan_error = error.message; });
    }
    return {job_id: jobId};
  }

  if (message.type === "pause" || message.type === "resume") {
    if (message.type === 'pause') await cancelPendingAcquisition();
    const epoch = message.type === 'resume' ? await acquisitionIntent(message.job_id) : null;
    const session = (await journal.load()).sessions[message.job_id];
    if (!session) throw new Error("找不到这次读取记录。");
    const resume = async () => {
      await flush();
      await activate(epoch, "/v1/jobs/" + message.job_id + "/resume", {});
      await updateIntent(epoch, s => { s.sessions[message.job_id].user_paused = false; s.browser_error = null; s.browser_wait = null; });
      return epoch;
    };
    if (message.type === 'pause') await journal.mutate(s => { s.sessions[message.job_id].user_paused = true; });
    if (!session.finalized) {
      if (message.type === "pause") {
        try { await chrome.tabs.sendMessage(session.tab_id, {type: "pause"}); } catch {}
        await journal.mutate(s => { s.sessions[message.job_id].scan_state = "paused"; });
      } else {
        const page = await inspectTab(session.tab_id);
        if (page.page_key !== session.page_key || (session.view_marker && page.view_marker !== session.view_marker))
          throw new Error("请先回到同一个收藏夹。");
        const epoch = await resume();
        await scan(session, page, epoch);
      }
    } else if (message.type === "resume") await resume();
    if (message.type === "pause") await journal.push("/v1/jobs/" + message.job_id + "/pause", {}, message.job_id);
    void flush();
    void browserReader.pump();
    return {};
  }
  if (message.type === "browser-start") {
    const epoch = await acquisitionIntent(message.job_id);
    const session = (await journal.load()).sessions[message.job_id];
    if (!session) throw new Error("找不到这次读取记录。");
    await flush();
    await activate(epoch, "/v1/jobs/" + session.job_id + "/browser", {});
    await updateIntent(epoch, s => {
      s.sessions[session.job_id].browser_enabled = true; s.sessions[session.job_id].user_paused = false;
      s.browser_error = null; s.browser_wait = null; s.active_job_id = session.job_id;
    });
    void browserReader.pump();
    return {};
  }
  if (message.type === "open-source") {
    const session = (await journal.load()).sessions[message.job_id];
    if (!session) throw new Error("找不到这次读取记录。");
    try {
      const page = await inspectTab(session.tab_id);
      if (page.page_key !== session.page_key) throw new Error();
      await chrome.tabs.update(session.tab_id, {active: true});
    } catch {
      const tab = await chrome.tabs.create({url: session.page_key});
      await journal.mutate(s => { s.sessions[session.job_id].tab_id = tab.id; });
    }
    return {};
  }
  if (message.type === "finish") {
    const session = (await journal.load()).sessions[message.job_id];
    if (!session) throw new Error("找不到这次读取记录。");
    try { await chrome.tabs.sendMessage(session.tab_id, {type: "pause"}); } catch {}
    await finalize(message.job_id, message.verified ? "user_verified" : "interrupted");
    return {};
  }
  throw new Error("未知扩展操作");
}

async function finalize(jobId, evidence) {
  await journal.mutate(state => {
    const session = state.sessions[jobId];
    if (session.finalized) return;
    session.finalized = true;
    session.scan_state = evidence === "interrupted" ? "partial" : "finished";
    state.queue.push({id: crypto.randomUUID(), job_id: jobId, path: "/v1/jobs/" + jobId + "/finish",
      body: {batch_count: session.next_seq, end_evidence: evidence}});
  });
  void flush();
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  handle(message, sender).then(data => respond({ok: true, data}),
    error => respond({ok: false, error: String(error.message).slice(0, 200)}));
  return true;
});
chrome.alarms.create("flush", {periodInMinutes: .5});
chrome.alarms.onAlarm.addListener(() => { void flush(); void browserReader.pump(); });
chrome.runtime.onStartup.addListener(() => { void flush(); void browserReader.pump(); });
setInterval(() => { void browserReader.pump(); }, 5000);
void flush();

