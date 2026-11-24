import test from "node:test";
import assert from "node:assert/strict";
import {pageError} from "../shared.mjs";
import {BrowserReader} from "../browser-reader.mjs";
import {Journal} from "../journal.mjs";

const source = "https://www.xiaohongshu.com/explore/000000000000000000000001";
const restrictedUrl = "https://www.xiaohongshu.com/website-login/error?error_code=300013";
const message = "安全限制 检测到异常访问，为保障账号安全本次访问已被限制 300013";

test("security restriction, expired link and login have different diagnoses", () => {
  assert.equal(pageError(restrictedUrl), "ACCESS_RESTRICTED");
  assert.equal(pageError(restrictedUrl.replace("300013", "300017")), "SOURCE_LINK_EXPIRED");
  assert.equal(pageError("https://www.xiaohongshu.com/login"), "ACCESS_BLOCKED");
  assert.equal(pageError(source, message), "ACCESS_RESTRICTED");
  assert.equal(pageError(source, message, true), null);
  assert.equal(pageError(source + "?error_code=300013"), null);
  assert.equal(pageError("https://example.com/website-login/error?error_code=300013", message), null);
});

async function harness({redirect = false, disconnected = false} = {}) {
  let stored = {}, restricted = false, creates = 0, pauses = 0, online = !disconnected;
  const journal = new Journal({get: async key => ({[key]: structuredClone(stored[key])}),
    set: async value => { stored = structuredClone({...stored, ...value}); }});
  await journal.mutate(s => { s.sessions.one = {browser_enabled: true}; });
  const task = {job_id: "one", note_id: "000000000000000000000001", lease_id: "lease", source_url: source};
  const submitted = [];
  const native = {request: async (path, body) => {
    if (path === "/v1/browser/next") return restricted ? {task: null, access_restriction: {site_code: "300013"}} : {task};
    if (path === '/v1/browser/check') return {allowed: !restricted};
    if (path === "/v1/access/restrict") {
      if (!online) throw new Error("HOST_DISCONNECTED");
      restricted = true;
      return {};
    }
    if (path.endsWith("/browser-result")) { submitted.push(body); return {}; }
    assert.fail("Unexpected operation: " + path);
  }};
  const tabs = {
    create: async options => { assert.equal(options.active, false); creates++; return {id: 8}; },
    get: async () => ({id: 8, active: false, url: redirect ? restrictedUrl : source}),
    sendMessage: async () => ({error: "ACCESS_RESTRICTED"}),
    remove: async () => {},
  };
  const makeReader = () => new BrowserReader(tabs, native, journal, async () => { pauses++; });
  return {makeReader, journal, submitted, reconnect: () => { online = true; },
    stats: () => ({creates, pauses, restricted})};
}

for (const redirect of [true, false]) {
  test(`reader stops after ${redirect ? "redirect" : "DOM restriction"} and never opens a second note`, async () => {
    const h = await harness({redirect});
    await h.makeReader().pump();
    assert.equal(h.stats().creates, 1);
    assert.equal(h.stats().restricted, true);
    assert.equal(h.submitted[0].error, "ACCESS_RESTRICTED");
    await h.makeReader().pump(); // A new extension worker must obey the native guard.
    assert.equal(h.stats().creates, 1);
    assert.ok(h.stats().pauses >= 2);
  });
}

test("lost native connection preserves the restriction before any next page", async () => {
  const h = await harness({redirect: true, disconnected: true});
  await h.makeReader().pump();
  assert.equal((await h.journal.load()).restriction_pending, true);
  await h.makeReader().pump();
  assert.equal(h.stats().creates, 1);
  h.reconnect();
  await h.makeReader().pump();
  assert.equal((await h.journal.load()).restriction_pending, false);
  assert.equal(h.stats().restricted, true);
  assert.equal(h.stats().creates, 1);
});
