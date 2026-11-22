export class Journal {
  constructor(storage, maxBytes = 7 * 1024 * 1024) {
    this.storage = storage;
    this.maxBytes = maxBytes;
    this.serial = Promise.resolve();
  }
  async load() {
    return (await this.storage.get("journal")).journal ||
      {version: 2, sessions: {}, queue: [], last_error: null};
  }
  mutate(fn) {
    const operation = this.serial.then(async () => {
      const state = await this.load();
      const result = await fn(state);
      if (new TextEncoder().encode(JSON.stringify(state)).length > this.maxBytes)
        throw new Error("待保存内容已满。请重新连接本机组件后继续读取。");
      await this.storage.set({journal: state});
      return result;
    });
    this.serial = operation.catch(() => {});
    return operation;
  }
  async acknowledge(id) {
    return this.mutate(state => {
      if (state.queue[0]?.id === id) state.queue.shift();
      state.last_error = null;
    });
  }
  // The local host already closed this list scan (for example, another browser took it over).
  async dropFinalizedScan(jobId) {
    return this.mutate(state => {
      state.queue = state.queue.filter(q => !(q.job_id === jobId && /\/(batches|finish)$/.test(q.path)));
      const session = state.sessions[jobId];
      if (session && !session.finalized) {
        session.finalized = true;
        session.scan_state = "partial";
        session.scan_error = "SCAN_FINALIZED";
      }
      state.last_error = null;
      return session?.tab_id ?? null;
    });
  }
  async push(path, body, jobId) {
    return this.mutate(state => state.queue.push({
      id: crypto.randomUUID(), path, body, job_id: jobId
    }));
  }
}

