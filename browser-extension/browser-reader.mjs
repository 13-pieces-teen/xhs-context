// Normal note pages only. Leased results are durable before their acknowledgement.
import {noteFromUrl, pageError} from "./shared.mjs";

const busyTabs = error => /Tabs cannot be edited right now|user may be dragging a tab/i.test(error?.message || '');
const disconnected = error => error?.transient || ['HOST_DISCONNECTED', 'NATIVE_TIMEOUT'].includes(error?.message);
const stopErrors = new Set(['ACCESS_RESTRICTED', 'ACCESS_BLOCKED', 'BROWSER_CLOSED', 'BROWSER_TIMEOUT',
  'BROWSER_PAUSED', 'BROWSER_BUSY', 'BROWSER_INTERRUPTED', 'BROWSER_ERROR']);

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
  }
  pump() {
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
  async syncRestriction() {
    if (!(await this.journal.load()).restriction_pending) return;
    await this.onRestriction();
    await this.native.request('/v1/access/restrict', {error: 'ACCESS_RESTRICTED'});
    await this.journal.mutate(s => { s.restriction_pending = false; });
  }
  async locallyPaused(jobId) {
    const state = await this.journal.load();
    return state.sessions[jobId]?.user_paused || state.queue.some(q => q.path === '/v1/jobs/' + jobId + '/pause');
  }
  async allowed(task) {
    if (await this.locallyPaused(task.job_id)) return false;
    const status = await this.native.request('/v1/status?job_id=' + task.job_id);
    return !status.access_restriction && status.job?.state === 'active';
  }
  async deliverResult() {
    const pending = (await this.journal.load()).browser_outbox;
    if (!pending) return;
    const body = {...pending.body};
    // A local pause wins even when its native message is still waiting to flush.
    if (body.snapshot && await this.locallyPaused(pending.job_id)) {
      delete body.snapshot;
      body.error = 'BROWSER_PAUSED';
    }
    try { await this.native.request(pending.path, body); }
    catch (error) {
      if (!['CONFLICT', 'JOB_NOT_FOUND'].includes(error.message)) throw error;
      // Reassigned or removed lease: the current lease remains authoritative.
    }
    await this.journal.mutate(s => {
      if (s.browser_outbox?.body.lease_id === pending.body.lease_id) s.browser_outbox = null;
      s.browser_error = null;
      s.browser_wait = null;
    });
  }
  async run() {
    await this.syncRestriction();
    const state = await this.journal.load();
    if (state.browser_wait?.retry_at > this.now()) return;
    await this.deliverResult();
    if (!await this.cleanup()) return;
    if (!Object.values(state.sessions).some(s => s.browser_enabled && !s.user_paused)) return;
    for (;;) {
      const {task, access_restriction} = await this.native.request('/v1/browser/next', {});
      if (access_restriction) { await this.onRestriction(); return; }
      await this.journal.mutate(s => { s.browser_wait = null; s.browser_error = null; });
      if (!task) return;
      const result = await this.readTask(task);
      await this.journal.mutate(s => {
        s.browser_outbox = {job_id: task.job_id, path: '/v1/jobs/' + task.job_id + '/browser-result',
          body: {note_id: task.note_id, lease_id: task.lease_id, ...result.value}};
        if (result.value.error === 'ACCESS_RESTRICTED') s.restriction_pending = true;
      });
      try {
        if (result.connectionError) throw result.connectionError;
        await this.syncRestriction();
        await this.deliverResult();
      } finally { await this.cleanup(); }
      if (stopErrors.has(result.value.error) || (await this.journal.load()).reader_tab) return;
      await this.sleep(1500);
    }
  }
  async readTask(task) {
    let value = {error: 'BROWSER_TIMEOUT'};
    try {
      if (!await this.allowed(task)) return {value: {error: 'BROWSER_PAUSED'}};
      const tab = await this.tabs.create({url: task.source_url, active: false});
      await this.journal.mutate(s => { s.reader_tab = {id: tab.id, note_id: task.note_id}; });
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
        if (urlError) { value = {error: urlError}; break; }
        try {
          let timer;
          const response = await Promise.race([
            this.tabs.sendMessage(tab.id, {type: 'read-note', note_id: task.note_id}),
            new Promise(resolve => { timer = setTimeout(() => resolve({pending: true}), 5000); })
          ]).finally(() => clearTimeout(timer));
          if (response?.snapshot) { value = {snapshot: response.snapshot}; break; }
          if (response?.error) {
            value = {error: ['ACCESS_RESTRICTED', 'ACCESS_BLOCKED', 'SOURCE_LINK_EXPIRED', 'SOURCE_UNAVAILABLE'].includes(response.error) ? response.error : 'BROWSER_PARSE_FAILED'};
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
  async cleanup() {
    const prior = (await this.journal.load()).reader_tab;
    if (!prior?.id) return true;
    if (!await this.closeOwned(prior.id, prior.note_id)) {
      await this.journal.mutate(s => { s.browser_wait = {error: 'BROWSER_BUSY', retry_at: this.now() + 5000}; });
      return false;
    }
    await this.journal.mutate(s => { if (s.reader_tab?.id === prior.id) s.reader_tab = null; });
    return true;
  }
  async closeOwned(id, noteId) {
    try {
      const tab = await this.tabs.get(id);
      if (!tab.active && noteFromUrl(tab.url)?.note_id === noteId) await this.tabs.remove(id);
      return true; // Leave activated or navigated tabs with the user.
    } catch (error) { return !busyTabs(error); }
  }
}
