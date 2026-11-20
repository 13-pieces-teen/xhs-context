import test from "node:test";
import assert from "node:assert/strict";
import {NativeClient} from "../native.mjs";

function runtime() {
  const runtime = {ports: [], lastError: null};
  runtime.connectNative = () => {
    const listeners = {}, sent = [];
    const port = {sent, onMessage: {addListener: fn => listeners.message = fn},
      onDisconnect: {addListener: fn => listeners.disconnect = fn},
      postMessage: value => sent.push(value),
      receive: value => listeners.message(value), disconnect: () => listeners.disconnect()};
    runtime.ports.push(port);
    return port;
  };
  return runtime;
}

test("native responses map to the right concurrent request", async () => {
  const chrome = runtime(), client = new NativeClient(chrome);
  const first = client.request("/v1/status"), second = client.request("/v1/jobs", {});
  const port = chrome.ports[0];
  assert.equal(chrome.ports.length, 1);
  port.receive({id: port.sent[1].id, ok: true, data: {job_id: "two"}});
  port.receive({id: port.sent[0].id, ok: true, data: {notes: 1}});
  assert.deepEqual(await first, {notes: 1});
  assert.deepEqual(await second, {job_id: "two"});
});

test("disconnect rejects pending work and the next request relaunches the host", async () => {
  const chrome = runtime(), client = new NativeClient(chrome);
  const first = client.request("/v1/status");
  chrome.lastError = {message: "Specified native messaging host not found."};
  chrome.ports[0].disconnect();
  await assert.rejects(first, /HOST_NOT_INSTALLED/);
  chrome.lastError = null;
  const retry = client.request("/v1/status");
  assert.equal(chrome.ports.length, 2);
  const port = chrome.ports[1];
  port.receive({id: port.sent[0].id, ok: true, data: {ready: true}});
  assert.equal((await retry).ready, true);
});

test("native operation errors cannot acknowledge a durable batch", async () => {
  const chrome = runtime(), client = new NativeClient(chrome);
  const pending = client.request("/v1/jobs/one/batches", {seq: 0, items: []});
  const port = chrome.ports[0];
  port.receive({id: port.sent[0].id, ok: false, error: {code: "CONFLICT"}});
  await assert.rejects(pending, /CONFLICT/);
});

test('timeout closes a stuck native port and a late old disconnect cannot break its replacement', async () => {
  const chrome = runtime(), client = new NativeClient(chrome, 5);
  const first = client.request('/v1/status');
  await assert.rejects(first, error => error.message === 'NATIVE_TIMEOUT' && error.transient);
  const retry = client.request('/v1/status');
  chrome.ports[0].disconnect();
  const port = chrome.ports[1];
  port.receive({id: port.sent[0].id, ok: true, data: {connected: true}});
  assert.equal((await retry).connected, true);
});
