import test from "node:test";
import assert from "node:assert/strict";
import {Journal} from "../journal.mjs";
import {noteFromUrl, pageKey, countFromLabel} from "../shared.mjs";

function storage() {
  let data = {};
  return {
    get: async key => ({[key]: structuredClone(data[key])}),
    set: async values => { data = structuredClone({...data, ...values}); }
  };
}

test("unacknowledged requests survive service worker replacement", async () => {
  const backend = storage(), first = new Journal(backend);
  await first.push("/v1/jobs", {job_id: "one"}, "one");
  const pending = (await first.load()).queue[0];
  const restarted = new Journal(backend);
  assert.equal((await restarted.load()).queue[0].id, pending.id);
  await restarted.acknowledge("wrong-ack");
  assert.equal((await restarted.load()).queue.length, 1);
  await restarted.acknowledge(pending.id);
  assert.equal((await restarted.load()).queue.length, 0);
});

test("quota pressure leaves previously committed queue intact", async () => {
  const backend = storage(), journal = new Journal(backend, 400);
  await journal.push("/v1/jobs", {job_id: "one"}, "one");
  await assert.rejects(journal.push("/v1/jobs", {data: "x".repeat(500)}, "two"), /已满/);
  assert.equal((await new Journal(backend).load()).queue.length, 1);
});

test("concurrent durable mutations retain both requests", async () => {
  const journal = new Journal(storage());
  await Promise.all([journal.push("/v1/a", {}, "a"), journal.push("/v1/b", {}, "b")]);
  assert.equal((await journal.load()).queue.length, 2);
});

test("a scan closed by the host drops only its list requests", async () => {
  const journal = new Journal(storage());
  await journal.mutate(state => {
    state.sessions.old = {job_id: "old", tab_id: 7, finalized: false, scan_state: "scanning"};
    state.sessions.other = {job_id: "other", tab_id: 8, finalized: false, scan_state: "scanning"};
  });
  await journal.push("/v1/jobs/old/batches", {seq: 1}, "old");
  await journal.push("/v1/jobs/other/batches", {seq: 0}, "other");
  await journal.push("/v1/jobs/old/pause", {}, "old");
  await journal.push("/v1/jobs/old/finish", {batch_count: 2}, "old");
  assert.equal(await journal.dropFinalizedScan("old"), 7);
  const state = await journal.load();
  assert.deepEqual(state.queue.map(q => q.path), ["/v1/jobs/other/batches", "/v1/jobs/old/pause"]);
  assert.equal(state.sessions.old.finalized, true);
  assert.equal(state.sessions.old.scan_state, "partial");
  assert.equal(state.sessions.other.finalized, false);
});

test("page identity keeps folder query but excludes access token", () => {
  assert.equal(pageKey("https://www.xiaohongshu.com/user/profile/me?folder_id=3&xsec_token=secret"),
    "https://www.xiaohongshu.com/user/profile/me?folder_id=3");
  assert.throws(() => pageKey("https://www.xiaohongshu.com/explore/abc"));
});

test("real note URL parameters are preserved and unrelated hosts excluded", () => {
  const url = "https://www.xiaohongshu.com/explore/000000000000000000000001?xsec_token=example";
  assert.equal(noteFromUrl(url).source_url, url);
  assert.equal(noteFromUrl(url.replace("www.xiaohongshu.com", "www.xiaohongshu.com.evil.example")), null);
});

test("page cards exclude insecure or credential-bearing targets", () => {
  const path = "/explore/000000000000000000000001";
  assert.equal(noteFromUrl("http://www.xiaohongshu.com" + path), null);
  assert.equal(noteFromUrl("https://name:secret@www.xiaohongshu.com" + path), null);
});

test("profile note links retain the actual access URL and use the last path ID", () => {
  const user = "1".repeat(24), note = "2".repeat(24);
  const url = "https://www.xiaohongshu.com/user/profile/" + user + "/" + note + "?xsec_token=fixture-only&xsec_source=pc_user";
  assert.deepEqual(noteFromUrl(url), {note_id: note, source_url: url});
  assert.equal(noteFromUrl("https://www.xiaohongshu.com/user/profile/" + user), null);
  assert.throws(() => pageKey(url));
});

test("saved notes, albums, and files have distinct page identities", () => {
  const base = "https://www.xiaohongshu.com/user/profile/" + "1".repeat(24) + "?tab=fav";
  assert.notEqual(pageKey(base + "&subTab=note"), pageKey(base + "&subTab=album"));
  assert.notEqual(pageKey(base + "&subTab=note"), pageKey(base + "&subTab=file"));
  assert.equal(pageKey(base + "&subTab=note&xsec_token=unused"), base + "&subTab=note");
});

test("favorites note counts use the selected label without guessing abbreviated totals", () => {
  assert.equal(countFromLabel("笔记・969"), 969);
  assert.equal(countFromLabel("笔记・1,234"), 1234);
  assert.equal(countFromLabel("收藏（60）"), 60);
  assert.equal(countFromLabel("获赞与收藏 3369"), null);
  assert.equal(countFromLabel("笔记・1.2万"), null);
});

