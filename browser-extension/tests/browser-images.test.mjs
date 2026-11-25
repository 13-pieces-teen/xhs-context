import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserReader} from '../browser-reader.mjs';
import {Journal} from '../journal.mjs';

async function harness({error, expected = 2, lostReceipt = false, paused = false, bodyPaused = false, delayedGallery = false, resumed = false} = {}) {
  let stored = {}, time = 0, leased = false, restricted = false, firstReceipt = true, reads = 0;
  const calls = [], opened = [], pages = new Map(), results = [];
  const task = {kind: 'images', job_id: 'one', note_id: '000000000000000000000001', lease_id: 'lease', resumed,
    source_url: 'https://www.xiaohongshu.com/explore/000000000000000000000001'};
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async value => { stored = structuredClone({...stored, ...value}); }});
  if (bodyPaused) await journal.mutate(s => { s.sessions.one = {user_paused: true}; });
  if (paused) await journal.mutate(s => { s.image_paused = {one: true}; });
  const native = {request: async (path, body) => {
    calls.push({path, body});
    if (path === '/v1/browser/next') {
      if (leased || restricted) return {task: null};
      leased = true;
      return {task};
    }
    if (path === '/v1/browser/check') {
      assert.deepEqual(body, {kind: 'images', job_id: 'one', note_id: task.note_id, lease_id: 'lease'});
      return {allowed: true};
    }
    if (path === '/v1/access/restrict') { restricted = true; return {}; }
    if (path === '/v1/image-jobs/one/browser-result') {
      results.push(body);
      if (lostReceipt && firstReceipt) { firstReceipt = false; throw new Error('HOST_DISCONNECTED'); }
      return {accepted: true, duplicate: results.length > 1};
    }
    assert.fail('Unexpected request ' + path);
  }};
  const tabs = {
    create: async options => {
      opened.push(options);
      pages.set(1, {id: 1, url: options.url, active: false});
      return {id: 1};
    },
    get: async id => pages.get(id),
    remove: async id => { pages.delete(id); },
    sendMessage: async (_id, message) => {
      assert.deepEqual(message, {type: 'read-images', note_id: task.note_id, collect: true});
      if (delayedGallery && ++reads < 3) return {error: 'IMAGE_LIST_UNAVAILABLE'};
      return error ? {error} : {snapshot: {note_id: task.note_id, page_url: task.source_url,
        images: ['https://ci.xiaohongshu.com/one.png'], image_indices: [1], expected_images: expected, note_type: 'image'}};
    }
  };
  const reader = () => new BrowserReader(tabs, native, journal, async () => {},
    {now: () => time, sleep: async ms => { time += ms; }, pollInterval: 1, pageTimeout: 10});
  return {reader, native, journal, task, calls, opened, results, restricted: () => restricted, advance: () => { time += 30000; }};
}

test('image-only browser job reads its gallery without any body session', async () => {
  const h = await harness();
  await h.reader().pump();
  assert.equal(h.opened.length, 1);
  assert.equal(h.results.length, 1);
  assert.equal(h.results[0].snapshot.expected_images, 2);
  assert.deepEqual((await h.journal.load()).sessions, {});
});

test('paused body session with the same ID does not pause or strip an image snapshot', async () => {
  const h = await harness({bodyPaused: true});
  await h.reader().pump();
  assert.equal(h.opened.length, 1);
  assert.ok(h.results[0].snapshot);
});

test('unknown gallery total is delivered once and never automatically reopened', async () => {
  const h = await harness({expected: null});
  await h.reader().pump();
  await h.reader().pump();
  assert.equal(h.opened.length, 1);
  assert.equal(h.results[0].snapshot.expected_images, null);
});

test('asynchronously rendered gallery is awaited on the same tab and lease', async () => {
  const h = await harness({delayedGallery: true});
  await h.reader().pump();
  assert.equal(h.opened.length, 1);
  assert.ok(h.results[0].snapshot);
  assert.ok(h.calls.filter(c => c.path === '/v1/browser/check').length >= 4);
});

test('lost image-result acknowledgement replays its durable receipt without reopening', async () => {
  const h = await harness({lostReceipt: true});
  await h.reader().pump();
  assert.equal((await h.journal.load()).browser_outbox.path, '/v1/image-jobs/one/browser-result');
  h.advance();
  await h.reader().pump();
  assert.deepEqual(h.results[0], h.results[1]);
  assert.equal(h.opened.length, 1);
  assert.equal((await h.journal.load()).browser_outbox, null);
});

test('image safety response persists the global restriction before delivering its result', async () => {
  const h = await harness({error: 'ACCESS_RESTRICTED'});
  await h.reader().pump();
  await h.reader().pump();
  assert.equal(h.restricted(), true);
  assert.equal(h.opened.length, 1);
  assert.equal(h.results[0].error, 'ACCESS_RESTRICTED');
  assert.ok(h.calls.findIndex(c => c.path === '/v1/access/restrict') <
    h.calls.findIndex(c => c.path.endsWith('/browser-result')));
});

for (const error of ['IMAGE_LIST_UNAVAILABLE', 'IMAGE_LIST_TOO_LARGE', 'SOURCE_UNAVAILABLE', 'ACCESS_BLOCKED']) {
  test(`image diagnosis ${error} is delivered unchanged`, async () => {
    const h = await harness({error});
    await h.reader().pump();
    assert.equal(h.results[0].error, error);
    assert.equal(h.opened.length, 1);
  });
}

test('durable image pause prevents opening after restart even before host pause arrives', async () => {
  const h = await harness({paused: true});
  await h.reader().pump();
  assert.equal(h.opened.length, 0);
  assert.equal(h.results[0].error, 'BROWSER_PAUSED');
  assert.ok(!h.calls.some(c => c.path === '/v1/browser/check'));
});

test('image pause strips a pending snapshot; same-ID body pause does not', async () => {
  const h = await harness({paused: true});
  await h.journal.mutate(s => { s.browser_outbox = {job_id: 'one', path: '/v1/image-jobs/one/browser-result',
    body: {note_id: h.task.note_id, lease_id: 'lease', snapshot: {images: []}}}; });
  await h.reader().deliverResult();
  assert.equal(h.results[0].error, 'BROWSER_PAUSED');
  assert.equal(h.results[0].snapshot, undefined);
});

test('queued image pause is honored independently from same-ID body pause paths', async () => {
  const h = await harness();
  await h.journal.push('/v1/image-jobs/one/pause', {}, 'one');
  assert.equal(await h.reader().allowed(h.task), false);
  await h.journal.mutate(s => { s.queue[0].path = '/v1/jobs/one/pause'; });
  assert.equal(await h.reader().allowed(h.task), true);
});

test('host-confirmed external resume clears an acknowledged extension pause', async () => {
  const h = await harness({paused: true, resumed: true});
  await h.reader().pump();
  assert.equal(h.opened.length, 1);
  assert.ok(h.results[0].snapshot);
  assert.equal((await h.journal.load()).image_paused.one, undefined);
});

test('an undelivered image pause wins over an external resume', async () => {
  const h = await harness({paused: true, resumed: true});
  await h.journal.push('/v1/image-jobs/one/pause', {}, 'one');
  await h.reader().pump();
  assert.equal(h.opened.length, 0);
  assert.equal((await h.journal.load()).image_paused.one, true);
});

test('new local cancellation during resume verification keeps the pause', async () => {
  const h = await harness({paused: true, resumed: true});
  const request = h.native.request;
  h.native.request = async (path, body) => {
    const result = await request(path, body);
    if (path === '/v1/browser/check') await h.journal.mutate(s => {
      s.acquisition_epoch = (s.acquisition_epoch || 0) + 1;
      s.image_paused.one = true;
    });
    return result;
  };
  await h.reader().pump();
  assert.equal(h.opened.length, 0);
  assert.equal((await h.journal.load()).image_paused.one, true);
});

test('a revoked resumed lease retains the extension pause', async () => {
  const h = await harness({paused: true, resumed: true});
  const request = h.native.request;
  h.native.request = async (path, body) => path === '/v1/browser/check' ? {allowed: false} : request(path, body);
  await h.reader().pump();
  assert.equal(h.opened.length, 0);
  assert.equal((await h.journal.load()).image_paused.one, true);
});
