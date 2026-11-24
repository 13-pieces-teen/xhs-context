import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserReader} from '../browser-reader.mjs';
import {Journal} from '../journal.mjs';

const source = 'https://www.xiaohongshu.com/explore/000000000000000000000001';
const noteId = '000000000000000000000001';
const task = {job_id: 'one', note_id: noteId, lease_id: 'lease', source_url: source};
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };

async function harness(options = {}) {
  let stored = {}, time = 1000, cursor = 0;
  const s = {creates: 0, removes: [], removeAttempts: 0, reads: 0, calls: [],
    offline: false, closeFailure: null, getFailure: null, failOutbox: false, failOwnership: false};
  const entered = deferred(), release = deferred();
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async update => {
      if (s.failOutbox && update.journal.browser_outbox) { s.failOutbox = false; throw new Error('QUOTA_BYTES quota exceeded'); }
      if (s.failOwnership && update.journal.reader_tab) { s.failOwnership = false; throw new Error('Storage write failed'); }
      stored = structuredClone({...stored, ...update});
    }});
  const native = {request: async (path, body) => {
    s.calls.push({path, body});
    if (s.offline) throw new Error('HOST_DISCONNECTED');
    if (path === '/v1/browser/next') return {task: cursor++ === 0 && !options.recoveryOnly ? task : null};
    if (path === '/v1/browser/check') return {allowed: true};
    if (path === '/v1/access/restrict') return {};
    if (path.endsWith('/browser-result')) {
      entered.resolve();
      if (options.holdReceipt) await release.promise;
      return {accepted: true};
    }
    assert.fail(path);
  }};
  const tabs = {
    create: async options => { assert.equal(options.active, false); s.creates++; return {id: 8}; },
    get: async () => {
      if (s.getFailure) throw new Error(s.getFailure);
      return {id: 8, active: !!options.active,
        url: s.reads && options.afterReadUrl ? options.afterReadUrl : options.url || source};
    },
    remove: async id => {
      s.removeAttempts++;
      if (s.closeFailure) throw new Error(s.closeFailure);
      s.removes.push(id);
    },
    sendMessage: async () => {
      s.reads++;
      return options.response || {snapshot: {note_id: noteId, page_url: source, body: 'saved body'}};
    }
  };
  const make = () => new BrowserReader(tabs, native, journal, async () => {},
    {now: () => time, sleep: async ms => { time += ms; }, pollInterval: 1, pageTimeout: 5});
  const own = () => journal.mutate(state => { state.reader_tab = {id: 8, note_id: noteId}; });
  const outbox = () => journal.mutate(state => { state.browser_outbox = {job_id: 'one',
    path: '/v1/jobs/one/browser-result', body: {note_id: noteId, lease_id: 'lease', error: 'SOURCE_UNAVAILABLE'}}; });
  return {s, journal, make, own, outbox, entered, release, advance: ms => { time += ms; }};
}

for (const response of [{error: 'SOURCE_UNAVAILABLE'}, {snapshot: {body: 'saved body'}}]) {
  test(`${response.error || 'snapshot'} closes its tab after durable staging, before native acknowledgement`, async () => {
    const h = await harness({holdReceipt: true, response});
    const running = h.make().pump();
    await h.entered.promise;
    assert.deepEqual(h.s.removes, [8]);
    const state = await h.journal.load();
    assert.equal(state.reader_tab, null);
    assert.deepEqual(state.browser_outbox.body, {note_id: noteId, lease_id: 'lease', ...response});
    h.release.resolve();
    await running;
    assert.equal((await h.journal.load()).browser_outbox, null);
  });
}

test('restart closes a leftover tab even while its pending result cannot reach native', async () => {
  const h = await harness({recoveryOnly: true});
  await h.own(); await h.outbox(); h.s.offline = true;
  await h.make().pump();
  assert.deepEqual(h.s.removes, [8]);
  assert.equal((await h.journal.load()).reader_tab, null);
  assert.equal((await h.journal.load()).browser_outbox.body.error, 'SOURCE_UNAVAILABLE');
  assert.equal(h.s.creates, 0);
});

test('restart closes a leftover tab before retrying an offline security report', async () => {
  const h = await harness({recoveryOnly: true});
  await h.own();
  await h.journal.mutate(state => { state.restriction_pending = true; });
  h.s.offline = true;
  await h.make().pump();
  assert.deepEqual(h.s.removes, [8]);
  assert.equal((await h.journal.load()).restriction_pending, true);
  assert.equal(h.s.calls[0].path, '/v1/access/restrict');
});

test('manual refresh cleans a leftover tab even if its initial status request fails', async () => {
  const h = await harness({recoveryOnly: true});
  await h.own(); h.s.offline = true;
  await assert.rejects(h.make().refreshAccess(), /HOST_DISCONNECTED/);
  assert.deepEqual(h.s.removes, [8]);
  assert.equal((await h.journal.load()).reader_tab, null);
});

test('native backoff never delays closing an already-read background page', async () => {
  const h = await harness({recoveryOnly: true});
  await h.own(); await h.outbox();
  await h.journal.mutate(state => { state.browser_wait = {error: 'HOST_DISCONNECTED', retry_at: 30000}; });
  await h.make().pump();
  assert.deepEqual(h.s.removes, [8]);
  assert.equal(h.s.calls.length, 0);
  assert.ok((await h.journal.load()).browser_outbox);
});

test('failed outbox persistence still closes the tab and retains the result for a later pump', async () => {
  const h = await harness(); h.s.failOutbox = true;
  const reader = h.make();
  await reader.pump();
  assert.deepEqual(h.s.removes, [8]);
  assert.equal((await h.journal.load()).browser_outbox, undefined);
  assert.equal(reader.pendingResult.body.snapshot.body, 'saved body');
  assert.ok(!h.s.calls.some(call => call.path.endsWith('/browser-result')));
  await reader.pump();
  assert.equal(h.s.creates, 1);
  assert.equal(h.s.calls.find(call => call.path.endsWith('/browser-result')).body.snapshot.body, 'saved body');
  assert.equal(reader.pendingResult, null);
});

test('failed initial ownership persistence does not leak a newly created tab', async () => {
  const h = await harness(); h.s.failOwnership = true;
  await h.make().pump();
  assert.deepEqual(h.s.removes, [8]);
  assert.equal(h.s.reads, 0);
  assert.equal(h.s.calls.find(call => call.path.endsWith('/browser-result')).body.error, 'BROWSER_ERROR');
});

test('non-busy remove failure retains ownership and retries without opening another page', async () => {
  const h = await harness(); h.s.closeFailure = 'Transient tab service failure';
  await h.make().pump();
  assert.equal((await h.journal.load()).reader_tab.id, 8);
  assert.equal(h.s.creates, 1);
  const restarted = h.make();
  await restarted.pump();
  assert.equal((await h.journal.load()).reader_tab.id, 8);
  assert.equal(h.s.creates, 1);
  assert.equal(h.s.removeAttempts, 2);
  h.s.closeFailure = null; h.advance(5000);
  await restarted.pump();
  assert.deepEqual(h.s.removes, [8]);
  assert.equal((await h.journal.load()).reader_tab, null);
});

test('an unknown tabs.get error preserves ownership; a confirmed missing tab clears it', async () => {
  const h = await harness({recoveryOnly: true}); await h.own();
  h.s.getFailure = 'Transient tab service failure';
  assert.equal(await h.make().cleanup(), false);
  assert.equal((await h.journal.load()).reader_tab.id, 8);
  h.s.getFailure = 'No tab with id: 8.';
  assert.equal(await h.make().cleanup(), true);
  assert.equal((await h.journal.load()).reader_tab, null);
});

test('a tab closed between get and remove is treated as already cleaned', async () => {
  const h = await harness({recoveryOnly: true}); await h.own();
  h.s.closeFailure = 'No tab with id: 8.';
  assert.equal(await h.make().cleanup(), true);
  assert.equal((await h.journal.load()).reader_tab, null);
});

test('an explicitly diagnosed new site error path can close only the exact observed URL', async () => {
  const url = 'https://www.xiaohongshu.com/404?fixture=missing';
  const h = await harness({url, response: {error: 'SOURCE_UNAVAILABLE'}});
  await h.make().pump();
  assert.deepEqual(h.s.removes, [8]);
  const moved = await harness({url, afterReadUrl: 'https://www.xiaohongshu.com/404?fixture=other',
    response: {error: 'SOURCE_UNAVAILABLE'}});
  await moved.make().pump();
  assert.deepEqual(moved.s.removes, []);
});

test('diagnosed error URL remains available for cleanup after worker replacement', async () => {
  const h = await harness({url: 'https://www.xiaohongshu.com/404', response: {error: 'SOURCE_UNAVAILABLE'}});
  h.s.closeFailure = 'Transient tab service failure';
  await h.make().pump();
  assert.equal((await h.journal.load()).reader_tab.error_url, 'https://www.xiaohongshu.com/404');
  h.s.closeFailure = null; h.advance(5000);
  await h.make().pump();
  assert.deepEqual(h.s.removes, [8]);
});

for (const options of [
  {active: true, response: {error: 'SOURCE_UNAVAILABLE'}},
  {url: 'https://www.xiaohongshu.com/404', response: {pending: true}},
  {url: 'https://www.xiaohongshu.com/explore/000000000000000000000002', response: {error: 'SOURCE_UNAVAILABLE'}},
  {url: 'https://example.test/404', response: {error: 'SOURCE_UNAVAILABLE'}},
]) {
  test(`cleanup protects ${options.active ? 'user-activated tab' : options.url}`, async () => {
    const h = await harness(options);
    await h.make().pump();
    assert.deepEqual(h.s.removes, []);
  });
}
