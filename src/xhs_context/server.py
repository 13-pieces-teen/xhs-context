from __future__ import annotations

import json
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .common import XhsError, atomic_json, dumps, envelope, error_envelope
from .exports import ExportScheduler
from .sync_service import dispatch_sync
from .worker import Worker, sync_lock

MAX_BODY = 2 * 1024 * 1024
EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}$")


def make_server(store, token, port=4319):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value):
            data = dumps(value).encode("utf-8")
            self.send_response(code)
            origin = self.headers.get("Origin", "")
            if EXTENSION_ORIGIN.fullmatch(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def boundary(self, auth=True):
            self.connection.settimeout(10)
            host = self.headers.get("Host", "")
            if host != f"127.0.0.1:{self.server.server_port}":
                raise XhsError("FORBIDDEN", "Invalid local host")
            origin = self.headers.get("Origin")
            if origin is not None and not EXTENSION_ORIGIN.fullmatch(origin):
                raise XhsError("FORBIDDEN", "Only the paired extension can send browser requests")
            if auth and not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                raise XhsError("UNAUTHORIZED", "Pair the extension using the local pairing file")

        def do_OPTIONS(self):
            try:
                self.boundary(auth=False)
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Content-Length", "0")
                self.end_headers()
            except XhsError as exc:
                self.reply(403, error_envelope(exc))

        def dispatch(self):
            self.boundary()
            parsed = urlsplit(self.path)
            if self.command == "GET" and parsed.path == "/v1/status":
                return dispatch_sync(store, self.path)
            if self.command != "POST":
                raise XhsError("NOT_FOUND", "Unknown endpoint")
            try:
                size = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                size = -1
            if not 0 <= size <= MAX_BODY:
                raise XhsError("INVALID_ARGUMENT", "Content-Length must be 0-2 MiB", exit_code=2)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise XhsError("INVALID_ARGUMENT", "Use application/json", exit_code=2)
            try:
                payload = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeError):
                raise XhsError("INVALID_ARGUMENT", "Invalid UTF-8 JSON", exit_code=2) from None
            return dispatch_sync(store, self.path, payload)

        def handle_request(self):
            try:
                self.reply(200, self.dispatch())
            except XhsError as exc:
                code = 400 if exc.exit_code == 2 else (
                    401 if exc.code == "UNAUTHORIZED" else 403 if exc.code == "FORBIDDEN" else
                    404 if exc.code in {"NOT_FOUND", "JOB_NOT_FOUND"} else 409)
                self.reply(code, error_envelope(exc))
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                print(f"sync receiver error: {type(exc).__name__}", file=sys.stderr)
                self.reply(500, error_envelope(XhsError("INTERNAL_ERROR", "Local receiver failed; retry the same batch", exit_code=5)))

        do_GET = handle_request
        do_POST = handle_request

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(store, port=4319, ocr=True, interval=2):
    pairing = store.root / "pairing.json"
    if pairing.exists():
        token = json.loads(pairing.read_text(encoding="utf-8"))["token"]
    else:
        token = secrets.token_urlsafe(32)
    stop = threading.Event()
    with sync_lock(store):
        server = make_server(store, token, port)
        atomic_json(pairing, {"server": f"http://127.0.0.1:{server.server_port}", "token": token})
        worker = Worker(store, ocr=ocr, stop_event=stop)
        worker.recover()
        def run():
            exports = ExportScheduler(store)
            while not stop.is_set():
                try:
                    worked = worker.once()
                    if not worked:
                        exports.idle()
                    stop.wait(interval if worked else 1)
                except Exception as exc:
                    print(f"sync worker error: {type(exc).__name__}", file=sys.stderr)
                    stop.wait(2)
        thread = threading.Thread(target=run, daemon=True)
        print(dumps(envelope({"server": f"http://127.0.0.1:{server.server_port}",
                             "pairing_file": str(pairing), "ocr": ocr})), flush=True)
        thread.start()
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            thread.join(timeout=25)

