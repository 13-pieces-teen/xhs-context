(() => {
  let run = null;

  const {pageKey, inspect, cards, endMarker, scrollTarget} = globalThis.XhsContextPage;
  async function send(value) {
    const result = await chrome.runtime.sendMessage(value);
    if (!result.ok) throw new Error(result.error);
    return result.data;
  }
  async function state(session, value, error) {
    await send({type: "scan-state", job_id: session.job_id, state: value, error});
  }
  async function scan(session) {
    try {
      let stalls = 0;
      await send({type: "scan-continue", job_id: session.job_id});
      await state(session, "scanning");
      scrollTarget().scrollTo({top: 0, behavior: "instant"});
      await new Promise(resolve => setTimeout(resolve, 600));
      while (!session.paused && run === session) {
        await send({type: "scan-continue", job_id: session.job_id});
        const current = inspect();
        if (!["saved", "unknown"].includes(current.scope) || pageKey(location.href) !== session.page_key ||
            (session.view_marker && current.view_marker !== session.view_marker))
          throw new Error("页面已切换，请回到目标收藏夹再继续");
        const fresh = cards().filter(card => !session.seen.has(card.note_id));
        for (let i = 0; i < fresh.length; i += 25) {
          const batch = fresh.slice(i, i + 25);
          await send({type: "capture", job_id: session.job_id, items: batch});
          batch.forEach(card => session.seen.add(card.note_id));
        }
        if (session.paused) break;
        if (endMarker()) {
          await state(session, "page_end");
          return;
        }
        stalls = fresh.length ? 0 : stalls + 1;
        if (stalls >= 8) {
          await state(session, "awaiting_confirmation");
          return;
        }
        const target = scrollTarget();
        target.scrollBy({top: Math.max(400, target.clientHeight * .8), behavior: "instant"});
        await new Promise(resolve => setTimeout(resolve, 1200));
      }
      await state(session, "paused");
    } catch (error) {
      try { await state(session, "error", error.message); } catch {}
    }
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    (async () => {
      if (message.type === 'read-images')
        return globalThis.XhsContextPage.readImages(message.note_id);
      if (message.type === "inspect")
        return inspect();
      if (message.type === "read-note")
        return globalThis.XhsContextPage.readNote(message.note_id);
      if (message.type === "pause") {
        if (run) { run.paused = true; await run.done; }
        return {};
      }
      if (message.type === "start") {
        if (run) { run.paused = true; await run.done; }
        const session = {job_id: message.job_id, page_key: message.page_key, view_marker: message.view_marker,
          seen: new Set(), paused: false};
        run = session;
        session.done = scan(session);
        return {started: true};
      }
      throw new Error("Unsupported page command");
    })().then(respond, error => respond({error: error.message}));
    return true;
  });
})();

