import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {Journal} from '../journal.mjs';
import {BrowserReader} from '../browser-reader.mjs';
import {noteFromUrl, pageKey} from '../shared.mjs';

// Run the real background handler with in-memory Chrome and native transports.
const source = (await readFile(new URL('../background.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\r?\n/gm, '');
const collection = 'https://www.xiaohongshu.com/collection/one';
const popup = {url: 'chrome-extension://fixture/popup.html'};
const content = {url: collection, tab: {id: 11}};
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function harness({request, pause = async () => {}, secondSession = false} = {}) {
  let listener;
  const requests = [], pauses = [];
  let stored = {journal: {version: 2, sessions: {
    one: {job_id: 'one', tab_id: 11, page_key: collection, scan_state: 'scanning', finalized: false},
    ...(secondSession ? {two: {job_id: 'two', tab_id: 12, page_key: collection,
      scan_state: 'scanning', finalized: false}} : {})
  }, queue: [], last_error: null}};
  const context = vm.createContext({Journal, BrowserReader, noteFromUrl, pageKey,
    NativeClient: class {
      async request(path, body) {
        requests.push({path, body});
        if (request) return request(path, body);
        assert.fail('Unexpected native request: ' + path);
      }
    },
    setInterval: () => {},
    chrome: {
      storage: {local: {
        setAccessLevel: async () => {},
        get: async key => ({[key]: structuredClone(stored[key])}),
        set: async value => { stored = structuredClone({...stored, ...value}); }
      }},
      runtime: {
        getURL: path => 'chrome-extension://fixture/' + path,
        onMessage: {addListener: callback => { listener = callback; }},
        onStartup: {addListener: () => {}}
      },
      alarms: {create: () => {}, onAlarm: {addListener: () => {}}},
      tabs: {sendMessage: async (id, message) => {
        assert.equal(message.type, 'pause');
        pauses.push(id);
        return pause(id);
      }}
    }
  });
  vm.runInContext(source, context);
  return {
    send: (message, sender = popup) => new Promise(resolve => listener(message, sender, resolve)),
    state: () => structuredClone(stored.journal), requests, pauses,
    readerIdle: () => vm.runInContext('browserReader.running', context)
  };
}

test('a normal refresh concurrent with scan-continue leaves the collection scan running', {timeout: 2000}, async () => {
  const entered = deferred(), release = deferred();
  const h = harness({request: async path => {
    if (path === '/v1/status') { entered.resolve(); await release.promise; return {}; }
    if (path === '/v1/status?job_id=one') return {job: {state: 'active'}};
    assert.fail(path);
  }});
  const refresh = h.send({type: 'refresh-access'});
  await entered.promise;
  const continued = await h.send({type: 'scan-continue', job_id: 'one'}, content);
  assert.equal(continued.ok, true);
  release.resolve();
  const result = await refresh;
  assert.equal(result.ok, true);
  assert.equal(result.data.unlocked, false);
  assert.equal(h.state().sessions.one.scan_state, 'scanning');
  assert.equal(h.state().sessions.one.user_paused, undefined);
  assert.deepEqual(h.pauses, []);
  assert.equal(h.requests.some(call => call.path === '/v1/access/refresh'), false);
});

test('a list restriction is persisted then submitted without waiting for its own page acknowledgement', {timeout: 2000}, async () => {
  const pageAcknowledged = deferred(), entered = deferred(), release = deferred();
  let serverRestricted = false;
  const h = harness({secondSession: true,
    // Model a content pause that cannot finish before the error message returns.
    pause: async () => { await pageAcknowledged.promise; return {}; },
    request: async (path, body) => {
      if (path === '/v1/browser/next') return {task: null, access_restriction: {error: 'ACCESS_RESTRICTED'}};
      assert.equal(path, '/v1/access/restrict');
      assert.equal(body.error, 'ACCESS_RESTRICTED');
      entered.resolve();
      await release.promise;
      serverRestricted = true;
      return {access_restriction: {error: 'ACCESS_RESTRICTED'}};
    }
  });
  const reported = await h.send({type: 'scan-state', job_id: 'one', state: 'error',
    error: 'ACCESS_RESTRICTED'}, content);
  assert.equal(reported.ok, true);
  assert.equal(h.state().restriction_pending, true);
  pageAcknowledged.resolve();
  await entered.promise;
  const denied = await h.send({type: 'scan-continue', job_id: 'two'}, {...content, tab: {id: 12}});
  assert.equal(denied.ok, false);
  assert.equal(denied.error, 'ACCESS_RESTRICTED');
  assert.equal(h.state().restriction_pending, true);
  release.resolve();
  await h.readerIdle();
  assert.equal(serverRestricted, true);
  assert.equal(h.state().restriction_pending, false);
  assert.equal(h.state().sessions.two.scan_state, 'paused');
  assert.deepEqual(h.pauses, [12]);
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].path, '/v1/browser/next');
});

test('a content-script sender cannot clear the local restriction through refresh-access', async () => {
  const h = harness();
  const denied = await h.send({type: 'refresh-access'}, content);
  assert.equal(denied.ok, false);
  assert.equal(denied.error, '请从扩展面板操作。');
  assert.deepEqual(h.requests, []);
  assert.equal(h.state().sessions.one.scan_state, 'scanning');
});
