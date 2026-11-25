// Normal note pages only. Leased results are durable before their acknowledgement.
import {noteFromUrl, pageError} from "./shared.mjs";

const busyTabs = error => /Tabs cannot be edited right now|user may be dragging a tab/i.test(error?.message || '');
const disconnected = error => error?.transient || ['HOST_DISCONNECTED', 'NATIVE_TIMEOUT'].includes(error?.message);
const missingTab = error => /No tab with id\b|Invalid tab ID\b|The tab was closed\b/i.test(error?.message || '');
const pageErrors = new Set(['ACCESS_RESTRICTED', 'ACCESS_BLOCKED', 'SOURCE_LINK_EXPIRED', 'SOURCE_UNAVAILABLE']);
const stopErrors = new Set(['ACCESS_RESTRICTED', 'ACCESS_BLOCKED', 'BROWSER_CLOSED', 'BROWSER_TIMEOUT',
  'BROWSER_PAUSED', 'BROWSER_BUSY', 'BROWSER_INTERRUPTED', 'BROWSER_ERROR']);
const errorPage = value => {
  try {
    const url = new URL(value);
    return url.origin === 'https://www.xiaohongshu.com' && !url.username && !url.password &&
      url.pathname.replace(/\/$/, '') === '/website-login/error';
  } catch { return false; }
};

export class BrowserReader {
  constructor(tabs, native, journal, onRestriction = async () => {}, options = {}) {
    this.tabs = tabs;
    this.native = native;
    this.journal = journal;
    this.onRestriction = onRestriction;
    this.now = options.now || Date.now;
    this.sleep = options.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
    this.pageTimeout = options.pageTimeout ?? 45000;
    this.pollInterval = options.pollInterval ?? 1000;
    this.running = null;
    this.refreshing = null;
    this.readerTab = null;
    this.pendingResult = null;
  }
  pump() {
    if (this.refreshing) return this.refreshing.catch(() => {});
    if (this.running) return this.running;
    this.running = this.run().catch(async error => {
      await this.journal.mutate(s => {
        s.browser_error = String(error.message).slice(0, 200);
        if (disconnected(error)) {
          const attempts = (s.browser_wait?.attempts || 0) + 1;
          s.browser_wait = {error: error.message, attempts,
            retry_at: this.now() + Math.min(30000, 5000 * 2 ** Math.min(attempts - 1, 3))};
        }
      });
    }).finally(() => { this.running = null; });
    return this.running;
  }
  refreshAccess() {
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      if (!this.running) await this.cleanup();
      const state = await this.journal.load();
      const status = await this.native.request('/v1/status');
      if (!status.access_restriction && !state.restriction_pending &&
          state.browser_outbox?.body.error !== 'ACCESS_RESTRICTED' &&
          this.pendingResult?.body.error !== 'ACCESS_RESTRICTED')
        return {unlocked: false, jobs_resumed: false};
      await this.onRestriction();
      // Finish the old attempt before clearing its guard. Otherwise its durable
      // error could be replayed after refresh and lock the archive again.
      await this.running;
      await this.cleanup();
      await this.persistResult();
      await this.syncRestriction();
      await this.deliverResult();
      const result = await this.native.request('/v1/access/refresh', {});
      await this.journal.mutate(s => {
        for (const session of Object.values(s.sessions)) {
          session.user_paused = true;
          if (!session.finalized) session.scan_state = 'paused';
          if (['ACCESS_RESTRICTED', 'RATE_LIMITED'].includes(session.scan_error)) session.scan_error = '';
        }
        for (const key of ['last_error', 'browser_error'])
          if (['ACCESS_RESTRICTED', 'RATE_LIMITED'].includes(s[key])) s[key] = null;
      });
      return result;
    })().finally(() => { this.refreshing = null; });
    return this.refreshing;
  }
  async syncRestriction() {
    if (!(await this.journal.load()).restriction_pending) return;
    await this.onRestriction();
    await this.native.request('/v1/access/restrict', {error: 'ACCESS_RESTRICTED'});
    await this.journal.mutate(s => { s.restriction_pending = false; });
  }
  async locallyPaused(jobId, images = false) {
    const state = await this.journal.load();
    if (images) return state.image_paused?.[jobId] ||
      state.queue.some(q => q.path === '/v1/image-jobs/' + jobId + '/pause');
    return state.sessions[jobId]?.user_paused || state.queue.some(q => q.path === '/v1/jobs/' + jobId + '/pause');
  }
  async allowed(task) {
    const state = await this.journal.load();
    if (state.restriction_pending) return false;
    const paused = await this.locallyPaused(task.job_id, task.kind === 'images');
    const pausePath = '/v1/image-jobs/' + task.job_id + '/pause';
    if (paused && (task.kind !== 'images' || task.resumed !== true || state.queue.some(q => q.path === pausePath)))
      return false;
    const result = await this.native.request('/v1/browser/check', {
      job_id: task.job_id, note_id: task.note_id, lease_id: task.lease_id,
      ...(task.kind === 'images' ? {kind: 'images'} : {})});
    if (result.allowed !== true) return false;
    if (!paused) return true;
    // A confirmed CLI/API resume can supersede an acknowledged extension pause.
    // Pending pauses and any newer local cancellation retain priority.
    return this.journal.mutate(s => {
      if (s.restriction_pending || (s.acquisition_epoch || 0) !== (state.acquisition_epoch || 0) ||
          s.queue.some(q => q.path === pausePath)) return false;
      if (s.image_paused) delete s.image_paused[task.job_id];
      return true;
    });
  }
  async deliverResult() {
    const pending = (await this.journal.load()).browser_outbox;
    if (!pending) return;
    const body = {...pending.body};
    // A local pause wins even when its native message is still waiting to flush.
    if (body.snapshot && await this.locallyPaused(pending.job_id, pending.path.startsWith('/v1/image-jobs/'))) {
      delete body.snapshot;
      body.error = 'BROWSER_PAUSED';
    }
    try { await this.native.request(pending.path, body); }
    catch (error) {
      if (error.message === 'INVALID_ARGUMENT' && body.snapshot) {
        // A malformed page observation cannot become valid by replaying it.
        // Persist one failure for the same lease before attempting delivery;
        // lost acknowledgements then replay the failure, never the bad snapshot.
        await this.journal.mutate(s => {
          if (s.browser_outbox?.path !== pending.path ||
              s.browser_outbox?.body.lease_id !== pending.body.lease_id) return;
          s.browser_outbox = {...pending, body: {note_id: body.note_id, lease_id: body.lease_id,
            error: pending.path.startsWith('/v1/image-jobs/') ? 'IMAGE_LIST_UNAVAILABLE' : 'BROWSER_PARSE_FAILED'}};
        });
        return this.deliverResult();
      }
      if (!['CONFLICT', 'JOB_NOT_FOUND'].includes(error.message)) throw error;
      // Reassigned or removed lease: the current lease remains authoritative.
    }
    await this.journal.mutate(s => {
      if (s.browser_outbox?.body.lease_id === pending.body.lease_id) s.browser_outbox = null;
      s.browser_error = null;
      s.browser_wait = null;
    });
  }
  async persistResult() {
    const pending = this.pendingResult;
    if (!pending) return;
    await this.journal.mutate(s => {
      s.browser_outbox = pending;
      if (pending.body.error === 'ACCESS_RESTRICTED') s.restriction_pending = true;
      if (this.readerTab?.error_url && s.reader_tab?.id === this.readerTab.id)
        s.reader_tab.error_url = this.readerTab.error_url;
    });
    if (this.pendingResult === pending) this.pendingResult = null;
  }
  async run() {
    // Closing an already-read tab never depends on a live native connection or
    // a result acknowledgement, including after service-worker replacement.
    const cleaned = await this.cleanup();
    await this.persistResult();
    await this.syncRestriction();
    const state = await this.journal.load();
    if (state.browser_wait?.retry_at > this.now()) return;
    await this.deliverResult();
    if (!cleaned) return;
    // The host only leases explicitly browser-enabled jobs, including image
    // jobs that have no body-reading session in this extension.
    for (;;) {
      const {task, access_restriction} = await this.native.request('/v1/browser/next', {});
      if (access_restriction) { await this.onRestriction(); return; }
      await this.journal.mutate(s => { s.browser_wait = null; s.browser_error = null; });
      if (!task) return;
      const result = await this.readTask(task);
      this.pendingResult = {job_id: task.job_id,
        path: (task.kind === 'images' ? '/v1/image-jobs/' : '/v1/jobs/') + task.job_id + '/browser-result',
        body: {note_id: task.note_id, lease_id: task.lease_id, ...result.value}};
      // Normally the result is durable before closing. If storage fails, retain
      // it in this reader for the next pump, and still clean up its background tab.
      try { await this.persistResult(); }
      finally { await this.cleanup(); }
      if (result.connectionError) throw result.connectionError;
      await this.syncRestriction();
      await this.deliverResult();
      if (stopErrors.has(result.value.error) || this.readerTab || (await this.journal.load()).reader_tab) return;
      await this.sleep(1500);
    }
  }
  async readTask(task) {
    let value = {error: 'BROWSER_TIMEOUT'};
    try {
      if (!await this.allowed(task)) return {value: {error: 'BROWSER_PAUSED'}};
      const tab = await this.tabs.create({url: task.source_url, active: false});
      this.readerTab = {id: tab.id, note_id: task.note_id};
      await this.journal.mutate(s => { s.reader_tab = this.readerTab; });
      const until = this.now() + this.pageTimeout;
      while (this.now() < until) {
        await this.sleep(this.pollInterval);
        if (!await this.allowed(task)) { value = {error: 'BROWSER_PAUSED'}; break; }
        let page;
        try { page = await this.tabs.get(tab.id); }
        catch (error) {
          value = {error: busyTabs(error) ? 'BROWSER_BUSY' : 'BROWSER_CLOSED'};
          break;
        }
        const urlError = pageError(page.url);
        // Even 300017 can accompany a visible security/login challenge. Read
        // the page diagnosis before permanently recording a source failure.
        const inspectErrorPage = errorPage(page.url) && urlError !== 'ACCESS_RESTRICTED';
        if (urlError && !inspectErrorPage) {
          this.rememberErrorPage(page.url, urlError);
          value = {error: urlError}; break;
        }
        if (inspectErrorPage && page.status === 'loading') continue;
        try {
          let timer;
          const response = await Promise.race([
            this.tabs.sendMessage(tab.id, {type: task.kind === 'images' ? 'read-images' : 'read-note', note_id: task.note_id,
              ...(task.kind === 'images' ? {collect: true} : {})}),
            new Promise(resolve => { timer = setTimeout(() => resolve({pending: true}), 5000); })
          ]).finally(() => clearTimeout(timer));
          if (response?.snapshot) { value = {snapshot: response.snapshot}; break; }
          if (response?.error) {
            if (task.kind === 'images' && response.error === 'IMAGE_LIST_UNAVAILABLE') {
              // The note shell often renders before its gallery. Keep the same
              // lease and tab for this bounded wait; never reopen it in a loop.
              value = {error: 'IMAGE_LIST_UNAVAILABLE'};
              continue;
            }
            const knownErrors = ['ACCESS_RESTRICTED', 'ACCESS_BLOCKED', 'SOURCE_LINK_EXPIRED', 'SOURCE_UNAVAILABLE'];
            if (task.kind === 'images') knownErrors.push('IMAGE_LIST_UNAVAILABLE', 'IMAGE_LIST_TOO_LARGE');
            value = {error: knownErrors.includes(response.error) ? response.error : 'BROWSER_PARSE_FAILED'};
            this.rememberErrorPage(page.url, value.error);
            break;
          }
        } catch {} // The content script may not be ready yet; keep waiting on this page.
      }
      return {value};
    } catch (error) {
      if (disconnected(error)) return {value: {error: 'BROWSER_INTERRUPTED'}, connectionError: error};
      return {value: {error: busyTabs(error) ? 'BROWSER_BUSY' : 'BROWSER_ERROR'}};
    }
  }
  rememberErrorPage(url, error) {
    if (!this.readerTab || !pageErrors.has(error) || noteFromUrl(url)) return;
    try {
      const parsed = new URL(url);
      if (parsed.origin === 'https://www.xiaohongshu.com' && !parsed.username && !parsed.password)
        this.readerTab.error_url = parsed.href;
    } catch {}
  }
  async cleanup() {
    const prior = this.readerTab || (await this.journal.load()).reader_tab;
    if (!prior?.id) return true;
    this.readerTab = prior;
    let closed = false, error = 'BROWSER_BUSY';
    try { closed = await this.closeOwned(prior.id, prior.note_id, prior.error_url); }
    catch (failure) { error = busyTabs(failure) ? 'BROWSER_BUSY' : 'BROWSER_ERROR'; }
    if (!closed) {
      await this.journal.mutate(s => { s.browser_wait = {error, retry_at: this.now() + 5000}; });
      return false;
    }
    await this.journal.mutate(s => { if (s.reader_tab?.id === prior.id) s.reader_tab = null; });
    if (this.readerTab?.id === prior.id) this.readerTab = null;
    return true;
  }
  async closeOwned(id, noteId, diagnosedErrorUrl) {
    try {
      const tab = await this.tabs.get(id);
      // Known site error redirects still belong to this inactive reader tab.
      // Other note IDs and unrelated navigation remain with the user.
      if (!tab.active && (noteFromUrl(tab.url)?.note_id === noteId.toLowerCase() || errorPage(tab.url) ||
          diagnosedErrorUrl && tab.url === diagnosedErrorUrl)) await this.tabs.remove(id);
      return true; // Leave activated or navigated tabs with the user.
    } catch (error) {
      if (missingTab(error)) return true;
      throw error;
    }
  }
}
