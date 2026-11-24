import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserReader} from '../browser-reader.mjs';
import {Journal} from '../journal.mjs';

async function harness({restricted = true, pending = false, outbox = false} = {}) {
  let stored = {};
  const s = {restricted, active: !restricted, calls: [], creates: 0, offline: false};
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async value => { stored = structuredClone({...stored, ...value}); }});
  await journal.mutate(state => {
    state.sessions.one = {browser_enabled: true, scan_state: 'paused', scan_error: 'ACCESS_RESTRICTED'};
    state.restriction_pending = pending;
    if (outbox) state.browser_outbox = {job_id: 'one', path: '/v1/jobs/one/browser-result',
      body: {note_id: 'note', lease_id: 'lease', error: 'ACCESS_RESTRICTED'}};
    state.browser_error = 'ACCESS_RESTRICTED';
  });
  const native = {request: async (path, body) => {
    s.calls.push(path);
    if (s.offline) throw new Error('HOST_DISCONNECTED');
    if (path.startsWith('/v1/status')) return {access_restriction: s.restricted ? {error: 'ACCESS_RESTRICTED'} : null,
      job: {state: s.active ? 'active' : 'paused'}};
    if (path === '/v1/access/restrict' || body?.error === 'ACCESS_RESTRICTED') {
      s.restricted = true; s.active = false; return {};
    }
    if (path === '/v1/access/refresh') {
      const unlocked = s.restricted; s.restricted = false;
      return {unlocked, jobs_resumed: false};
    }
    if (path === '/v1/browser/next') return {task: null,
      access_restriction: s.restricted ? {error: 'ACCESS_RESTRICTED'} : null};
    assert.fail(path);
  }};
  const reader = new BrowserReader({create: async () => { s.creates++; }}, native, journal);
  return {s, journal, native, reader};
}

test('explicit refresh clears old guard but never resumes or opens a page', async () => {
  const {s, journal, reader} = await harness();
  assert.deepEqual(await reader.refreshAccess(), {unlocked: true, jobs_resumed: false});
  const state = await journal.load();
  assert.equal(state.sessions.one.user_paused, true);
  assert.equal(state.sessions.one.scan_error, '');
  assert.equal(state.browser_error, null);
  await reader.pump();
  assert.equal(s.active, false);
  assert.equal(s.creates, 0);
  assert.deepEqual(s.calls, ['/v1/status', '/v1/access/refresh', '/v1/browser/next']);
});

test('pending stop and old outbox are acknowledged before clearing the guard', async () => {
  const {s, journal, reader} = await harness({pending: true, outbox: true});
  await reader.refreshAccess();
  assert.deepEqual(s.calls, ['/v1/status', '/v1/access/restrict', '/v1/jobs/one/browser-result', '/v1/access/refresh']);
  assert.equal((await journal.load()).browser_outbox, null);
  assert.equal((await journal.load()).restriction_pending, false);
  await reader.pump();
  assert.equal(s.restricted, false);
});

test('a lost outbox acknowledgement prevents refresh until delivery succeeds', async () => {
  const {s, journal, reader, native} = await harness({outbox: true});
  const request = native.request;
  native.request = async (path, body) => {
    if (path.endsWith('/browser-result')) throw new Error('HOST_DISCONNECTED');
    return request(path, body);
  };
  await assert.rejects(reader.refreshAccess(), /HOST_DISCONNECTED/);
  assert.equal(s.restricted, true);
  assert.ok((await journal.load()).browser_outbox);
  assert.ok(!s.calls.includes('/v1/access/refresh'));
  native.request = request;
  await reader.refreshAccess();
  assert.equal(s.restricted, false);
});

test('refresh waits for an in-flight result and blocks concurrent pumps', async () => {
  const {s, journal, reader} = await harness();
  let finish;
  reader.running = new Promise(resolve => { finish = resolve; });
  const refresh = reader.refreshAccess();
  const pump = reader.pump();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(s.calls, ['/v1/status']);
  await journal.mutate(state => {
    state.restriction_pending = true;
    state.browser_outbox = {job_id: 'one', path: '/v1/jobs/one/browser-result',
      body: {lease_id: 'lease', error: 'ACCESS_RESTRICTED'}};
  });
  finish();
  await Promise.all([refresh, pump]);
  assert.equal(s.restricted, false);
  assert.equal(s.creates, 0);
  assert.equal(s.calls.at(-1), '/v1/access/refresh');
});

test('unrestricted refresh leaves a running task alone', async () => {
  const {s, journal, reader} = await harness({restricted: false});
  reader.running = new Promise(() => {}); // Normal refresh must not wait for a note.
  assert.deepEqual(await reader.refreshAccess(), {unlocked: false, jobs_resumed: false});
  assert.equal(s.active, true);
  assert.equal((await journal.load()).sessions.one.user_paused, undefined);
  assert.deepEqual(s.calls, ['/v1/status']);
});

test('ordinary polling preserves the guard and later real restrictions still stop', async () => {
  const {s, journal, reader} = await harness();
  await reader.pump();
  assert.equal(s.restricted, true);
  assert.ok(!s.calls.includes('/v1/access/refresh'));
  await reader.refreshAccess();
  await journal.mutate(state => { state.restriction_pending = true; });
  await reader.pump();
  assert.equal(s.restricted, true);
  assert.equal(s.creates, 0);
});
