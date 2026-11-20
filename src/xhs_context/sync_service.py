"""The same local operations behind native messaging and the optional HTTP receiver."""
from __future__ import annotations

import re
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from .common import XhsError, envelope
from .core import status
from .exports import export_snapshot
from .jobs import create_job, finish_scan, receive_batch, set_job_state
from .worker import Worker, sync_lock
from .browser_import import enable_browser, next_page, accept_page
from .access import report_restriction
from .image_jobs import create_image_job, image_report, set_image_state
from .images import ImageWorker


def dispatch_sync(store, path, body=None):
    if not isinstance(path, str) or len(path) > 300:
        raise XhsError("INVALID_ARGUMENT", "Invalid local operation", exit_code=2)
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        raise XhsError("INVALID_ARGUMENT", "Use a local operation path", exit_code=2)
    if body is None and parsed.path == "/v1/status":
        return status(store, parse_qs(parsed.query).get("job_id", [None])[0])
    image_route = re.fullmatch(r'/v1/image-jobs/([A-Za-z0-9_-]{1,100})(?:/(pause|resume))?', parsed.path)
    if body is None and image_route and not image_route[2]:
        with store.read() as db:
            return envelope(image_report(db, image_route[1]))
    if body is None:
        raise XhsError("NOT_FOUND", "Unknown local operation")
    if parsed.path == "/v1/access/restrict":
        return envelope(report_restriction(store, body))
    if parsed.path == '/v1/image-jobs':
        return envelope(create_image_job(store, body))
    if image_route and image_route[2]:
        return envelope(set_image_state(store, image_route[1], 'paused' if image_route[2] == 'pause' else 'active'))
    if parsed.path == "/v1/browser/next":
        return envelope(next_page(store))
    if parsed.path == "/v1/jobs":
        return envelope(create_job(store, body))
    match = re.fullmatch(r"/v1/jobs/([A-Za-z0-9_-]{1,100})/(batches|finish|pause|resume|browser|browser-result)", parsed.path)
    if not match:
        raise XhsError("NOT_FOUND", "Unknown local operation")
    job_id, action = match.groups()
    if action == "browser":
        result = enable_browser(store, job_id)
    elif action == "browser-result":
        result = accept_page(store, job_id, body)
    elif action == "batches":
        result = receive_batch(store, job_id, body)
    elif action == "finish":
        result = finish_scan(store, job_id, body)
    else:
        result = set_job_state(store, job_id, "paused" if action == "pause" else "active",
                               retry_failed=action == "resume")
    return envelope(result)


class SyncSession:
    def __init__(self, store, *, ocr=True, interval=2, worker_factory=Worker):
        self.store, self.interval = store, interval
        self.stop = threading.Event()
        self.worker = worker_factory(store, ocr=ocr, stop_event=self.stop)
        self.image_worker = ImageWorker(store, stop_event=self.stop)
        self.lock = sync_lock(store)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.lock.__enter__()
        try:
            self.worker.recover()
            self.image_worker.recover()
            self.thread.start()
        except BaseException:
            self.lock.__exit__(*sys.exc_info())
            raise
        return self

    def run(self):
        last_export = -1
        images_first = False
        while not self.stop.is_set():
            try:
                first, second = (self.image_worker, self.worker) if images_first else (self.worker, self.image_worker)
                worked = first.once() or second.once()
                images_first = not images_first
                if not worked:
                    with self.store.read() as db:
                        generation = self.store.generation(db)
                    if generation != last_export:
                        export_snapshot(self.store)
                        last_export = generation
                self.stop.wait(self.interval if worked else 1)
            except Exception as exc:
                print(f"sync worker error: {type(exc).__name__}", file=sys.stderr)
                self.stop.wait(2)

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=25)
        self.lock.__exit__(*args)
