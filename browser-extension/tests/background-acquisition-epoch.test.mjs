import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {createHash, webcrypto} from 'node:crypto';
import {Journal} from '../journal.mjs';
import {BrowserReader} from '../browser-reader.mjs';
import {noteFromUrl, pageKey} from '../shared.mjs';

const source = (await readFile(new URL('../background.js', import.meta.url), 'utf8')).replace(/^import .*;\r?\n/gm, '');
const collection = 'https://www.xiaohongshu.com/collection/one';
const collectionId = 'view-' + createHash('sha256').update(collection + '\nsaved').digest('hex').slice(0, 24);
const noteId = '000000000000000000000001';
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
const activating = path => path === '/v1/jobs' || path === '/v1/image-jobs' || /\/(resume|browser)$/.test(path);

function harness({existing = false, backlog = 30, holdRead = false, holdTab = false,
  lateActivation = false, initialEpoch = 0} = {}) {
  let listener, serial = Promise.resolve(), firstStatus = true, blockNextRead = false;
  let restricted = false, active = false, offline = false;
  const entered = deferred(), held = deferred(), release = deferred();
  const calls = [], pageStarts = [];
  let stored = {journal: {version: 2, acquisition_epoch: initialEpoch, sessions: {
    one: {job_id: 'one', collection_id: collectionId, tab_id: 11, page_key: collection,
      scan_state: 'paused', finalized: !existing, user_paused: true}
  }, queue: Array.from({length: backlog}, (_, i) => ({id: String(i), job_id: 'other',
    path: '/v1/jobs/other/batches', body: {seq: i}})), last_error: null}};
  const context = vm.createContext({Journal, BrowserReader, noteFromUrl, pageKey,
    crypto: webcrypto, TextEncoder, setInterval: () => {},
    NativeClient: class {
      request(path, body) {
        // Execute native operations in FIFO order. A separate delayed receipt
        // models a response whose backend effect preceded a new user action.
        const executed = serial.then(async () => {
          await new Promise(resolve => setImmediate(resolve));
          calls.push({path, body});
          if (offline) throw new Error('HOST_DISCONNECTED');
          if (path.startsWith('/v1/status')) {
            if (firstStatus) { firstStatus = false; blockNextRead = holdRead; entered.resolve(); }
            return {access_restriction: restricted ? {error: 'ACCESS_RESTRICTED'} : null,
              job: {state: active ? 'active' : 'paused'}};
          }
          if (path === '/v1/access/refresh') { restricted = false; return {unlocked: true, jobs_resumed: false}; }
          if (path === '/v1/access/restrict') { restricted = true; active = false; return {}; }
          if (activating(path)) {
            if (restricted) throw new Error('ACCESS_RESTRICTED');
            active = true;
            if (lateActivation) held.resolve();
            return {job_id: body?.job_id || 'one'};
          }
          if (path.endsWith('/pause')) active = false;
          if (path === '/v1/browser/next') return {task: null};
          if (path === '/v1/browser/check') return {allowed: active};
          return {};
        });
        serial = executed.catch(() => {});
        return executed.then(async value => {
          if (lateActivation && activating(path)) await release.promise;
          return value;
        });
      }
    },
    chrome: {storage: {local: {
      setAccessLevel: async () => {},
      get: async key => {
        if (blockNextRead) { blockNextRead = false; held.resolve(); await release.promise; }
        return {[key]: structuredClone(stored[key])};
      },
      set: async value => { stored = structuredClone({...stored, ...value}); }
    }}, runtime: {getURL: path => 'chrome-extension://fixture/' + path,
      onMessage: {addListener: fn => { listener = fn; }}, onStartup: {addListener: () => {}}},
    alarms: {create: () => {}, onAlarm: {addListener: () => {}}},
    tabs: {
      query: async () => {
        if (holdTab) { held.resolve(); await release.promise; }
        return [{id: 11, url: holdTab ? 'https://www.xiaohongshu.com/explore/' + noteId : collection}];
      },
      sendMessage: async (id, message) => {
        if (message.type === 'inspect') return {url: collection, name: 'one', scope: 'saved',
          expected_count: 1, identity_marker: 'saved', view_marker: 'saved'};
        if (message.type === 'start') { pageStarts.push(message); return {started: true}; }
        if (message.type === 'read-images') return {snapshot: {note_id: noteId, images: []}};
        return {};
      }
    }}
  });
  vm.runInContext(source, context);
  return {
    send: message => new Promise(resolve => listener(message, {url: 'chrome-extension://fixture/popup.html'}, resolve)),
    state: () => structuredClone(stored.journal),
    restrict: () => { restricted = true; active = false; },
    offline: value => { offline = value; },
    allowed: task => { context.testTask = task; return vm.runInContext('browserReader.allowed(testTask)', context); },
    active: () => active, entered, held, release, calls, pageStarts
  };
}

test('offline image pause survives reconnect and only explicit resume clears its own namespace', {timeout: 3000}, async () => {
  const h = harness({backlog: 0});
  assert.equal((await h.send({type: 'images-resume', job_id: 'one'})).ok, true);
  h.offline(true);
  assert.equal((await h.send({type: 'images-pause', job_id: 'one'})).ok, true);
  assert.equal(h.state().image_paused.one, true);
  assert.ok(h.state().queue.some(q => q.path === '/v1/image-jobs/one/pause'));
  h.offline(false);
  const task = {kind: 'images', job_id: 'one', note_id: noteId, lease_id: 'lease'};
  assert.equal(await h.allowed(task), false);
  assert.equal((await h.send({type: 'images-resume', job_id: 'one'})).ok, true);
  assert.equal(h.state().image_paused.one, undefined);
  assert.equal(h.state().sessions.one.user_paused, true);
  assert.equal(await h.allowed(task), true);
  const pause = h.calls.findLastIndex(c => c.path === '/v1/image-jobs/one/pause');
  const resume = h.calls.findLastIndex(c => c.path === '/v1/image-jobs/one/browser');
  assert.ok(pause < resume);
});

for (const [type, existing] of [['resume', false], ['browser-start', false], ['start', false], ['start', true]]) {
  test(`${type}${existing ? ' existing scan' : ''} waiting on real batch flush cannot activate after refresh`, {timeout: 3000}, async () => {
    const h = harness({existing, initialEpoch: 7});
    const old = h.send({type, job_id: 'one'});
    await h.entered.promise;
    h.restrict();
    const refresh = await h.send({type: 'refresh-access'});
    assert.equal(refresh.ok, true);
    assert.equal((await old).ok, false);
    assert.equal(h.active(), false);
    assert.ok(!h.calls.some(call => activating(call.path)));
    assert.equal(h.state().sessions.one.user_paused, true);
    assert.ok(h.state().acquisition_epoch > 7);
    assert.equal(h.pageStarts.length, 0);
  });
}

for (const type of ['images-current', 'images-collection']) {
  test(`${type} asynchronous preparation cannot activate after refresh`, {timeout: 3000}, async () => {
    const h = harness({backlog: 0, holdTab: type === 'images-current', holdRead: type === 'images-collection'});
    const old = h.send({type, job_id: 'one'});
    await h.held.promise;
    h.restrict();
    assert.equal((await h.send({type: 'refresh-access'})).ok, true);
    h.release.resolve();
    assert.equal((await old).ok, false);
    assert.ok(!h.calls.some(call => activating(call.path)));
    assert.equal(h.active(), false);
  });
}

for (const type of ['resume', 'browser-start', 'start', 'images-resume']) {
  test(`${type} delayed activation receipt cannot overwrite the newer pause`, {timeout: 3000}, async () => {
    const h = harness({backlog: 0, lateActivation: true});
    const old = h.send({type, job_id: 'one'});
    await h.held.promise;
    h.restrict();
    assert.equal((await h.send({type: 'refresh-access'})).ok, true);
    h.release.resolve();
    await old;
    assert.equal(h.active(), false);
    assert.ok(Object.values(h.state().sessions).every(session => session.user_paused));
    assert.ok(Object.values(h.state().sessions).every(session => session.scan_state === 'paused' || session.finalized));
    assert.equal(h.pageStarts.length, 0);
  });
}

for (const pauseType of ['pause', 'images-pause']) {
  test(`${pauseType} cancels an older resume waiting for pending batches`, {timeout: 3000}, async () => {
    const h = harness();
    const old = h.send({type: 'resume', job_id: 'one'});
    await h.entered.promise;
    assert.equal((await h.send({type: pauseType, job_id: 'one'})).ok, true);
    assert.equal((await old).ok, false);
    assert.ok(!h.calls.some(call => call.path === '/v1/jobs/one/resume'));
    assert.equal(h.state().sessions.one.user_paused, true);
    assert.equal(h.active(), false);
  });
}
