// Only opens normal note pages. No cookies, private APIs, or page-internal stores.
import {noteFromUrl, pageError} from "./shared.mjs";
export class BrowserReader {
  constructor(tabs, native, journal, onRestriction = async () => {}) {
    this.tabs = tabs;
    this.native = native;
    this.journal = journal;
    this.onRestriction = onRestriction;
    this.running = null;
  }
  pump() {
    if (this.running) return this.running;
    this.running = this.run().catch(async error => {
      await this.journal.mutate(s => { s.browser_error = String(error.message).slice(0, 200); });
    }).finally(() => { this.running = null; });
    return this.running;
  }
  async run() {
    const state = await this.journal.load();
    if (state.restriction_pending) {
      await this.onRestriction();
      await this.native.request("/v1/access/restrict", {error: "ACCESS_RESTRICTED"});
      await this.journal.mutate(s => { s.restriction_pending = false; });
    }
    if (!Object.values(state.sessions).some(s => s.browser_enabled)) return;
    for (;;) {
      const {task, access_restriction} = await this.native.request("/v1/browser/next", {});
      if (access_restriction) { await this.onRestriction(); return; }
      if (!task) return;
      let tabId = null, result = {error: "BROWSER_TIMEOUT"};
      try {
        // Only close a leftover tab that this reader created, never a user tab.
        const prior = (await this.journal.load()).reader_tab;
        if (prior?.id) await this.closeOwned(prior.id, prior.note_id);
        const tab = await this.tabs.create({url: task.source_url, active: false});
        tabId = tab.id;
        await this.journal.mutate(s => { s.reader_tab = {id: tabId, note_id: task.note_id}; s.browser_error = null; });
        const until = Date.now() + 45000;
        while (Date.now() < until) {
          await new Promise(resolve => setTimeout(resolve, 1000));
          const status = await this.native.request("/v1/status?job_id=" + task.job_id);
          if (status.job?.state !== "active") { result = {error: "BROWSER_PAUSED"}; break; }
          let tab;
          try { tab = await this.tabs.get(tabId); }
          catch { result = {error: "BROWSER_CLOSED"}; break; }
          const urlError = pageError(tab.url);
          if (urlError) { result = {error: urlError}; break; }
          try {
            const response = await Promise.race([
              this.tabs.sendMessage(tabId, {type: "read-note", note_id: task.note_id}),
              new Promise(resolve => setTimeout(() => resolve({pending: true}), 5000))
            ]);
            if (response?.snapshot) { result = {snapshot: response.snapshot}; break; }
            if (response?.error) {
              result = {error: ["ACCESS_RESTRICTED", "ACCESS_BLOCKED", "SOURCE_LINK_EXPIRED", "SOURCE_UNAVAILABLE"].includes(response.error) ? response.error : "BROWSER_PARSE_FAILED"};
              break;
            }
          } catch {} // Content script is not ready yet.
        }
        if (result.error === "ACCESS_RESTRICTED") {
          // A disconnected host must not lose the stop signal on worker restart.
          await this.journal.mutate(s => { s.restriction_pending = true; });
          await this.onRestriction();
          await this.native.request("/v1/access/restrict", {error: "ACCESS_RESTRICTED"});
          await this.journal.mutate(s => { s.restriction_pending = false; });
        }
        await this.native.request("/v1/jobs/" + task.job_id + "/browser-result", {
          note_id: task.note_id, lease_id: task.lease_id, ...result
        });
      } finally {
        if (tabId !== null) {
          await this.closeOwned(tabId, task.note_id);
          await this.journal.mutate(s => { if (s.reader_tab?.id === tabId) s.reader_tab = null; });
        }
      }
      if (["ACCESS_RESTRICTED", "ACCESS_BLOCKED", "BROWSER_CLOSED", "BROWSER_TIMEOUT", "BROWSER_PAUSED"].includes(result.error)) return;
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  }
  async closeOwned(id, noteId) {
    try {
      const tab = await this.tabs.get(id);
      if (!tab.active && noteFromUrl(tab.url)?.note_id === noteId) await this.tabs.remove(id);
    } catch {}
  }
}
