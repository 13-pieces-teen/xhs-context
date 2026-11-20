export const HOST_NAME = "org.xhs_context.host";

export class NativeClient {
  constructor(runtime, timeout = 20000) {
    this.runtime = runtime;
    this.timeout = timeout;
    this.port = null;
    this.pending = new Map();
  }
  connect() {
    if (this.port) return this.port;
    const port = this.runtime.connectNative(HOST_NAME);
    this.port = port;
    port.onMessage.addListener(message => {
      if (message.id === null && message.error) {
        this.fail(new Error(message.error.code));
        port.disconnect();
        return;
      }
      const waiting = this.pending.get(message.id);
      if (!waiting) return;
      clearTimeout(waiting.timer);
      this.pending.delete(message.id);
      if (message.ok) waiting.resolve(message.data);
      else waiting.reject(new Error(message.error?.code || "LOCAL_OPERATION_FAILED"));
    });
    port.onDisconnect.addListener(() => {
      const reason = this.runtime.lastError?.message || "本机组件已断开，点击重试即可重新连接。";
      if (this.port === port) this.port = null;
      this.fail(new Error(/not found|not registered|forbidden|Access.*denied/i.test(reason) ? "HOST_NOT_INSTALLED" : reason));
    });
    return port;
  }
  fail(error) {
    for (const waiting of this.pending.values()) {
      clearTimeout(waiting.timer);
      waiting.reject(error);
    }
    this.pending.clear();
    this.port = null;
  }
  request(path, body) {
    return new Promise((resolve, reject) => {
      const id = crypto.randomUUID();
      try {
        const port = this.connect();
        const timer = setTimeout(() => {
          this.pending.delete(id);
          reject(new Error("本机组件响应超时，已发送的数据会保留，稍后重试。"));
        }, this.timeout);
        this.pending.set(id, {resolve, reject, timer});
        port.postMessage({id, path, ...(body === undefined ? {} : {body})});
      } catch (error) {
        const waiting = this.pending.get(id);
        if (waiting) clearTimeout(waiting.timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }
}
