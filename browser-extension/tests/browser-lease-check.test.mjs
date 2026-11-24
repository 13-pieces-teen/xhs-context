import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserReader} from '../browser-reader.mjs';
import {Journal} from '../journal.mjs';

const task = {job_id: 'one', note_id: '000000000000000000000001', lease_id: 'lease',
  source_url: 'https://www.xiaohongshu.com/explore/000000000000000000000001'};

async function harness({checks = [true], error = null, url = task.source_url, active = false, statuses = []} = {}) {
  let stored = {}, time = 0;
  const state = {creates: 0, reads: 0, gets: 0, lastStatus: undefined, removes: [], checks: [], reports: [], done: false};
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async value => { stored = structuredClone({...stored, ...value}); }});
  await journal.mutate(s => { s.sessions.one = {browser_enabled: true}; });
  const native = {request: async (path, body) => {
    if (path === '/v1/browser/next') return {task: state.done ? null : task};
    if (path === '/v1/browser/check') {
      assert.deepEqual(body, {job_id: task.job_id, note_id: task.note_id, lease_id: task.lease_id});
      state.checks.push(body);
      return {allowed: checks[Math.min(state.checks.length - 1, checks.length - 1)]};
    }
    if (path === '/v1/access/restrict') return {};
    if (path.endsWith('/browser-result')) { state.reports.push(body); state.done = true; return {}; }
    assert.fail(path);
  }};
  const tabs = {create: async () => { state.creates++; return {id: 8}; },
    get: async () => {
      state.lastStatus = statuses[Math.min(state.gets++, statuses.length - 1)];
      return {id: 8, url, active, status: state.lastStatus};
    },
    remove: async id => { state.removes.push(id); },
    sendMessage: async () => {
      state.reads++;
      if (state.lastStatus === 'loading') return {error: 'SOURCE_LINK_EXPIRED'};
      return error ? {error} : {snapshot: {body: 'saved'}};
    }};
  const reader = new BrowserReader(tabs, native, journal, async () => {},
    {now: () => time, sleep: async ms => { time += ms; }, pollInterval: 1});
  return {reader, state, journal};
}

test('a revoked or invalidated lease opens no page even if its job remains active', async () => {
  const h = await harness({checks: [false]});
  await h.reader.pump();
  assert.equal(h.state.creates, 0);
  assert.equal(h.state.reads, 0);
  assert.equal(h.state.reports[0].error, 'BROWSER_PAUSED');
});

test('a lease invalidated after opening is stopped before reading the page', async () => {
  const h = await harness({checks: [true, false]});
  await h.reader.pump();
  assert.equal(h.state.creates, 1);
  assert.equal(h.state.reads, 0);
  assert.deepEqual(h.state.removes, [8]);
  assert.equal(h.state.reports[0].error, 'BROWSER_PAUSED');
});

for (const error of ['ACCESS_RESTRICTED', 'ACCESS_BLOCKED', 'SOURCE_LINK_EXPIRED']) {
  test(`a 300017 URL obeys the rendered ${error} diagnosis and cleans its inactive tab`, async () => {
    const h = await harness({url: 'https://www.xiaohongshu.com/website-login/error?error_code=300017', error});
    await h.reader.pump();
    assert.equal(h.state.reads, 1);
    assert.equal(h.state.reports[0].error, error);
    assert.deepEqual(h.state.removes, [8]);
    assert.equal((await h.journal.load()).reader_tab, null);
  });
}

test('an inactive 300013 error redirect is closed after recording the restriction', async () => {
  const h = await harness({url: 'https://www.xiaohongshu.com/website-login/error?error_code=300013'});
  await h.reader.pump();
  assert.equal(h.state.reads, 0);
  assert.equal(h.state.reports[0].error, 'ACCESS_RESTRICTED');
  assert.deepEqual(h.state.removes, [8]);
});

test('a loading 300017 redirect waits for the completed security-page diagnosis', async () => {
  const h = await harness({url: 'https://www.xiaohongshu.com/website-login/error?error_code=300017',
    statuses: ['loading', 'complete'], error: 'ACCESS_RESTRICTED'});
  await h.reader.pump();
  assert.equal(h.state.reads, 1);
  assert.equal(h.state.reports[0].error, 'ACCESS_RESTRICTED');
  assert.deepEqual(h.state.removes, [8]);
});

test('cleanup leaves activated error tabs and navigation to other notes or sites alone', async () => {
  for (const options of [{active: true, url: 'https://www.xiaohongshu.com/website-login/error?error_code=300017'},
    {url: 'https://www.xiaohongshu.com/explore/000000000000000000000002'},
    {url: 'https://example.com/website-login/error?error_code=300017'}]) {
    const h = await harness(options);
    await h.journal.mutate(s => { s.reader_tab = {id: 8, note_id: task.note_id}; });
    assert.equal(await h.reader.cleanup(), true);
    assert.deepEqual(h.state.removes, []);
  }
});

test('cleanup compares legacy uppercase note IDs without leaking its own normal page', async () => {
  const h = await harness({url: 'https://www.xiaohongshu.com/explore/abcdefabcdefabcdefabcdef'});
  await h.journal.mutate(s => { s.reader_tab = {id: 8, note_id: 'ABCDEFABCDEFABCDEFABCDEF'}; });
  assert.equal(await h.reader.cleanup(), true);
  assert.deepEqual(h.state.removes, [8]);
});
