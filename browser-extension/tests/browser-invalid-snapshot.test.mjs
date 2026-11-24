import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserReader} from '../browser-reader.mjs';
import {Journal} from '../journal.mjs';

async function harness({images = false, loseFailureReceipt = false, rejectError = false, rejectTransport = false} = {}) {
  let stored = {}, leased = false, time = 1000, lost = false;
  const calls = [], pages = new Map();
  let creates = 0;
  const task = {job_id: 'one', note_id: '000000000000000000000001', lease_id: 'lease',
    source_url: 'https://www.xiaohongshu.com/explore/000000000000000000000001', ...(images ? {kind: 'images'} : {})};
  const path = (images ? '/v1/image-jobs/' : '/v1/jobs/') + 'one/browser-result';
  const failure = images ? 'IMAGE_LIST_UNAVAILABLE' : 'BROWSER_PARSE_FAILED';
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async value => { stored = structuredClone({...stored, ...value}); }});
  const native = {request: async (operation, body) => {
    if (operation === '/v1/browser/next') {
      if (leased) return {task: null}; leased = true; return {task};
    }
    if (operation === '/v1/browser/check') return {allowed: true};
    assert.equal(operation, path);
    calls.push(body);
    if (body.snapshot) throw new Error(rejectTransport ? 'HOST_DISCONNECTED' : 'INVALID_ARGUMENT');
    assert.equal(body.error, failure);
    assert.deepEqual((await journal.load()).browser_outbox.body, body);
    if (rejectError) throw new Error('INVALID_ARGUMENT');
    if (loseFailureReceipt && !lost) { lost = true; throw new Error('HOST_DISCONNECTED'); }
    return {accepted: true};
  }};
  const tabs = {
    create: async () => { creates++; pages.set(8, {id: 8, active: false, url: task.source_url}); return {id: 8}; },
    get: async id => pages.get(id),
    remove: async id => { pages.delete(id); },
    sendMessage: async () => ({snapshot: {note_id: task.note_id, page_url: task.source_url,
      images: ['https://ci.xiaohongshu.com/one.png'], image_indices: [100], expected_images: 2}})
  };
  const make = () => new BrowserReader(tabs, native, journal, async () => {},
    {now: () => time, sleep: async ms => { time += ms; }, pollInterval: 1});
  return {make, journal, calls, task, path, failure, creates: () => creates, open: () => pages.size,
    advance: () => { time += 30000; }};
}

for (const images of [false, true]) {
  test(`invalid ${images ? 'gallery' : 'body'} snapshot becomes one durable failed result for its original lease`, async () => {
    const h = await harness({images});
    await h.make().pump();
    await h.make().pump();
    assert.equal(h.calls.length, 2);
    assert.ok(h.calls[0].snapshot);
    assert.deepEqual(h.calls[1], {note_id: h.task.note_id, lease_id: 'lease', error: h.failure});
    assert.equal((await h.journal.load()).browser_outbox, null);
    assert.equal(h.creates(), 1);
    assert.equal(h.open(), 0);
  });
}

test('lost failure acknowledgement replays only the replacement error after restart', async () => {
  const h = await harness({images: true, loseFailureReceipt: true});
  await h.make().pump();
  assert.equal((await h.journal.load()).browser_outbox.body.error, 'IMAGE_LIST_UNAVAILABLE');
  h.advance();
  await h.make().pump();
  assert.equal(h.calls.filter(body => body.snapshot).length, 1);
  assert.deepEqual(h.calls[1], h.calls[2]);
  assert.equal((await h.journal.load()).browser_outbox, null);
  assert.equal(h.creates(), 1);
});

test('an invalid error report is surfaced without recursive error-to-error conversion', async () => {
  const h = await harness({rejectError: true});
  await h.make().pump();
  assert.equal(h.calls.length, 2);
  assert.equal((await h.journal.load()).browser_error, 'INVALID_ARGUMENT');
  assert.equal((await h.journal.load()).browser_outbox.body.error, 'BROWSER_PARSE_FAILED');
});

test('a transport failure preserves the original snapshot for acknowledgement retry', async () => {
  const h = await harness({rejectTransport: true});
  await h.make().pump();
  assert.equal(h.calls.length, 1);
  assert.ok((await h.journal.load()).browser_outbox.body.snapshot);
  assert.equal(h.open(), 0);
});
