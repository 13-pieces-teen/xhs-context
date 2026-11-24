import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {pageKey, pageError} from '../shared.mjs';

const source = await readFile(new URL('../content.js', import.meta.url), 'utf8');
const collection = 'https://www.xiaohongshu.com/collection/one';
const restriction = '安全限制 检测到异常访问，为保障账号安全本次访问已被限制 300013';

async function scan({text = '', url = collection, readable = false, afterScroll = false, afterRedirect = false} = {}) {
  const messages = [], scrolls = [];
  let listener, finish;
  const done = new Promise(resolve => { finish = resolve; });
  const body = {innerText: text};
  const location = {href: url};
  const context = vm.createContext({
    location,
    document: {body, querySelector: () => readable ? {querySelector: () => ({})} : null},
    setTimeout: callback => { callback(); },
    XhsContextPage: {pageKey, pageError, rendered: () => true,
      inspect: () => ({scope: 'saved', view_marker: 'one'}),
      cards: () => [{note_id: 'note-' + scrolls.length}],
      endMarker: () => !afterScroll && !afterRedirect,
      scrollTarget: () => ({scrollTo: () => { scrolls.push('top'); }, scrollBy: () => {
        scrolls.push('next');
        if (afterScroll) body.innerText = restriction;
        if (afterRedirect) location.href = 'https://www.xiaohongshu.com/website-login/error?error_code=300013';
      }})},
    chrome: {runtime: {
      onMessage: {addListener: callback => { listener = callback; }},
      sendMessage: async message => {
        messages.push(message);
        // Background validates the collection URL before granting scan-continue.
        if (message.type === 'scan-continue' && pageKey(location.href) !== collection)
          return {ok: false, error: '当前页面与收藏夹不匹配，请回到原页面。'};
        if (message.type === 'scan-state' && ['error', 'page_end'].includes(message.state)) finish();
        return {ok: true, data: {}};
      }
    }}
  });
  vm.runInContext(source, context);
  const response = await new Promise(resolve => listener({type: 'start', job_id: 'one',
    page_key: collection, view_marker: 'one'}, {}, resolve));
  assert.equal(response.started, true);
  await done;
  return {messages, scrolls};
}

test('an existing DOM restriction stops the list before its first scroll or capture', async () => {
  const result = await scan({text: restriction});
  assert.deepEqual(result.scrolls, []);
  assert.equal(result.messages.filter(message => message.type === 'capture').length, 0);
  assert.equal(result.messages.at(-1).error, 'ACCESS_RESTRICTED');
});

test('a restriction appearing during list scrolling stops before the next capture', async () => {
  const result = await scan({afterScroll: true});
  assert.deepEqual(result.scrolls, ['top', 'next']);
  assert.equal(result.messages.filter(message => message.type === 'capture').length, 1);
  assert.equal(result.messages.at(-1).error, 'ACCESS_RESTRICTED');
});

test('a security redirect is reported before scan-continue can reject the changed page identity', async () => {
  const result = await scan({url: 'https://www.xiaohongshu.com/website-login/error?error_code=300013'});
  assert.deepEqual(result.scrolls, []);
  assert.equal(result.messages.filter(message => message.type === 'scan-continue').length, 0);
  assert.equal(result.messages.at(-1).error, 'ACCESS_RESTRICTED');
});

test('a redirect during list scrolling is diagnosed before the next collection identity check', async () => {
  const result = await scan({afterRedirect: true});
  assert.deepEqual(result.scrolls, ['top', 'next']);
  assert.equal(result.messages.filter(message => message.type === 'scan-continue').length, 2);
  assert.equal(result.messages.filter(message => message.type === 'capture').length, 1);
  assert.equal(result.messages.at(-1).error, 'ACCESS_RESTRICTED');
});

test('restriction text inside a readable note is not mistaken for a platform block', async () => {
  const result = await scan({text: restriction, readable: true});
  assert.equal(result.messages.at(-1).state, 'page_end');
  assert.equal(result.messages.filter(message => message.type === 'capture').length, 1);
});

test('an ordinary mention of the error code does not stop the list', async () => {
  const result = await scan({text: '今天的学习笔记：错误码 300013'});
  assert.equal(result.messages.at(-1).state, 'page_end');
});
