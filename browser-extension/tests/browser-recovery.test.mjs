import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserReader} from '../browser-reader.mjs';
import {Journal} from '../journal.mjs';

const source = 'https://www.xiaohongshu.com/explore/000000000000000000000001';
const task = {job_id: 'one', note_id: '000000000000000000000001', lease_id: 'lease', source_url: source};
const transient = () => Object.assign(new Error('HOST_DISCONNECTED'), {transient: true});

async function harness() {
  let stored = {}, time = 1000;
  const s = {creates: 0, results: [], done: false, leased: false, active: true, retryAt: 0,
    busy: false, offlineResult: false, closeBusy: false, userActivated: false, removes: 0};
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async value => { stored = structuredClone({...stored, ...value}); }});
  await journal.mutate(v => { v.sessions.one = {browser_enabled: true}; });
  const native = {request: async (path, body) => {
    if (path.endsWith('/browser/next')) {
      if (s.done || s.leased || !s.active || time < s.retryAt) return {task: null};
      s.leased = true; return {task: {...task}};
    }
    if (path.startsWith('/v1/status')) return {job: {state: s.active ? 'active' : 'paused'}};
    if (path.endsWith('/browser-result')) {
      if (s.offlineResult) throw transient();
      s.results.push(body); s.leased = false;
      if (body.snapshot) s.done = true;
      else if (body.error === 'ACCESS_BLOCKED') s.active = false;
      else if (body.error === 'BROWSER_BUSY') s.retryAt = time + 5000;
      return {};
    }
    assert.fail(path);
  }};
  const tabs = {create: async () => {
    s.creates++;
    if (s.busy) throw new Error('Tabs cannot be edited right now (user may be dragging a tab).');
    return {id: 8};
  }, get: async () => ({id: 8, url: source, active: s.userActivated}),
  remove: async () => { if (s.closeBusy) throw new Error('Tabs cannot be edited right now'); s.removes++; },
  sendMessage: async () => ({snapshot: {body: 'saved'}})};
  const make = () => new BrowserReader(tabs, native, journal, async () => {},
    {now: () => time, sleep: async ms => { time += ms; }, pollInterval: 1});
  return {s, journal, tabs, native, make, advance: ms => { time += ms; }};
}

test('tab drag error is submitted as retryable and reading continues after backoff', async () => {
  const h = await harness(); h.s.busy = true;
  await h.make().pump();
  assert.equal(h.s.results[0].error, 'BROWSER_BUSY');
  assert.equal(h.s.active, true);
  await h.make().pump(); assert.equal(h.s.creates, 1);
  h.advance(5000); h.s.busy = false;
  await h.make().pump(); assert.equal(h.s.done, true);
  assert.equal(h.s.creates, 2);
});

test('worker replacement replays stored body before opening another page', async () => {
  const h = await harness(); h.s.offlineResult = true;
  await h.make().pump();
  assert.equal((await h.journal.load()).browser_outbox.body.snapshot.body, 'saved');
  h.s.offlineResult = false;
  await h.make().pump(); assert.equal(h.s.results.length, 0); // Durable wait survives replacement.
  h.advance(5000);
  await h.make().pump();
  assert.equal(h.s.done, true); assert.equal(h.s.creates, 1);
  assert.equal((await h.journal.load()).browser_outbox, null);
});

test('manual pause during reconnection does not publish or reopen a page', async () => {
  const h = await harness(); h.s.offlineResult = true;
  await h.make().pump();
  await h.journal.mutate(s => { s.sessions.one.user_paused = true; });
  h.s.offlineResult = false; h.advance(5000);
  await h.make().pump();
  assert.equal(h.s.results[0].error, 'BROWSER_PAUSED');
  assert.equal(h.s.done, false); assert.equal(h.s.creates, 1);
});

test('close failure retains ownership and cleanup waits without another page', async () => {
  const h = await harness(); h.s.closeBusy = true;
  await h.make().pump();
  assert.equal((await h.journal.load()).reader_tab.id, 8);
  await h.make().pump(); assert.equal(h.s.creates, 1);
  h.s.closeBusy = false; h.advance(5000);
  await h.make().pump();
  assert.equal((await h.journal.load()).reader_tab, null);
  assert.equal(h.s.removes, 1);
});

test('cleanup never closes a page that the user activated', async () => {
  const h = await harness(); h.s.userActivated = true;
  await h.make().pump(); assert.equal(h.s.removes, 0);
});

test('login wall stops without any automatic resume request', async () => {
  const h = await harness(); h.tabs.sendMessage = async () => ({error: 'ACCESS_BLOCKED'});
  await h.make().pump(); h.advance(120000); await h.make().pump();
  assert.equal(h.s.creates, 1); assert.equal(h.s.active, false);
});

test('expired outbox lease is discarded without submitting into its replacement', async () => {
  const h = await harness(); h.s.offlineResult = true;
  await h.make().pump();
  const request = h.native.request;
  h.native.request = async (path, body) => {
    if (path.endsWith('/browser-result')) throw new Error('CONFLICT');
    return request(path, body);
  };
  h.advance(5000); await h.make().pump();
  assert.equal((await h.journal.load()).browser_outbox, null);
  assert.equal(h.s.creates, 1);
});
