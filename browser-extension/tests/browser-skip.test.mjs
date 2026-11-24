import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserReader} from '../browser-reader.mjs';
import {Journal} from '../journal.mjs';

async function harness(firstError, {redirect = false, errorCode = '300017'} = {}) {
  let stored = {}, time = 0, cursor = 0, restricted = false, paused = false;
  const opened = [], results = [], pages = new Map();
  const tasks = [1, 2, 3].map(value => {
    const note_id = value.toString(16).padStart(24, '0');
    return {job_id: 'one', note_id, lease_id: 'lease-' + value,
      source_url: 'https://www.xiaohongshu.com/explore/' + note_id};
  });
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async value => { stored = structuredClone({...stored, ...value}); }});
  await journal.mutate(s => { s.sessions.one = {browser_enabled: true}; });
  const native = {request: async (path, body) => {
    if (path === '/v1/browser/next') return {
      task: paused || restricted ? null : tasks[cursor] || null,
      access_restriction: restricted ? {error: 'ACCESS_RESTRICTED'} : null};
    if (path === '/v1/browser/check') return {allowed: !paused && !restricted};
    if (path === '/v1/access/restrict') { restricted = true; paused = true; return {}; }
    if (path.endsWith('/browser-result')) {
      assert.equal(body.lease_id, tasks[cursor].lease_id);
      results.push(body); cursor++;
      if (body.error === 'ACCESS_BLOCKED') paused = true;
      return {};
    }
    assert.fail('Unexpected operation: ' + path);
  }};
  const tabs = {
    create: async options => {
      assert.equal(options.active, false);
      const id = opened.length + 1;
      opened.push(options.url);
      pages.set(id, {id, active: false, url: redirect && id === 1
        ? 'https://www.xiaohongshu.com/website-login/error?error_code=' + errorCode : options.url});
      return {id};
    },
    get: async id => pages.get(id),
    remove: async id => { pages.delete(id); },
    sendMessage: async id => id === 1 ? {error: firstError} : {snapshot: {body: 'saved note ' + id}},
  };
  const reader = new BrowserReader(tabs, native, journal, async () => {},
    {now: () => time, sleep: async ms => { time += ms; }, pollInterval: 1});
  return {reader, opened, results, tasks, state: () => ({paused, restricted})};
}

for (const error of ['SOURCE_UNAVAILABLE', 'SOURCE_LINK_EXPIRED']) {
  test(`${error} is recorded once and the same pump reads the following notes`, async () => {
    const h = await harness(error);
    await h.reader.pump();
    assert.deepEqual(h.opened, h.tasks.map(task => task.source_url));
    assert.equal(h.results[0].error, error);
    assert.ok(h.results.slice(1).every(result => result.snapshot));
    assert.equal(h.results.length, 3);
    assert.deepEqual(h.state(), {paused: false, restricted: false});
    await h.reader.pump();
    assert.equal(h.opened.length, 3);
  });
}

test('300017 redirect skips the expired link and continues reading', async () => {
  const h = await harness('SOURCE_LINK_EXPIRED', {redirect: true});
  await h.reader.pump();
  assert.equal(h.results[0].error, 'SOURCE_LINK_EXPIRED');
  assert.equal(h.opened.length, 3);
  assert.equal(h.results.filter(result => result.snapshot).length, 2);
});

test('unknown website error code uses the visible missing-note diagnosis and continues', async () => {
  const h = await harness('SOURCE_UNAVAILABLE', {redirect: true, errorCode: '300099'});
  await h.reader.pump();
  assert.equal(h.results[0].error, 'SOURCE_UNAVAILABLE');
  assert.equal(h.opened.length, 3);
  assert.equal(h.results.filter(result => result.snapshot).length, 2);
  assert.equal(h.state().paused, false);
});

test('unknown website error code still stops when its DOM diagnoses an access block', async () => {
  const h = await harness('ACCESS_BLOCKED', {redirect: true, errorCode: '300099'});
  await h.reader.pump();
  assert.equal(h.results[0].error, 'ACCESS_BLOCKED');
  assert.equal(h.opened.length, 1);
  assert.equal(h.state().paused, true);
});

for (const error of ['ACCESS_RESTRICTED', 'ACCESS_BLOCKED']) {
  test(`${error} still pauses the queue before a second note`, async () => {
    const h = await harness(error);
    await h.reader.pump();
    assert.equal(h.results[0].error, error);
    assert.equal(h.results.length, 1);
    assert.equal(h.opened.length, 1);
    assert.equal(h.state().paused, true);
    await h.reader.pump();
    assert.equal(h.opened.length, 1);
  });
}
