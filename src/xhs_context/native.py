"""Browser-launched local host. stdin/stdout contain only framed native messages."""
from __future__ import annotations

import json
import os
import struct
import sys

from .common import XhsError, dumps, error_envelope, fields, library_path
from .storage import Store
from .sync_service import SyncSession, dispatch_sync

HOST_NAME = "org.xhs_context.host"
MAX_REQUEST = 2 * 1024 * 1024
MAX_RESPONSE = 1024 * 1024


def read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            if not chunks:
                return None
            raise XhsError("INVALID_FRAME", "Incomplete native message", exit_code=2)
        chunks.extend(chunk)
    return bytes(chunks)


def read_message(stream):
    header = read_exact(stream, 4)
    if header is None:
        return None
    size = struct.unpack("<I", header)[0]
    if not 0 < size <= MAX_REQUEST:
        raise XhsError("INVALID_FRAME", "Native message exceeds the size limit", exit_code=2)
    raw = read_exact(stream, size)
    if raw is None:
        raise XhsError("INVALID_FRAME", "Missing native message body", exit_code=2)
    try:
        message = json.loads(raw)
        fields(message, {"id", "path", "body"}, {"id", "path"})
        if not isinstance(message["id"], str) or len(message["id"]) > 100:
            raise ValueError()
        return message
    except (ValueError, UnicodeError):
        raise XhsError("INVALID_FRAME", "Invalid native JSON message", exit_code=2) from None


def write_message(stream, value):
    raw = dumps(value).encode("utf-8")
    if len(raw) >= MAX_RESPONSE:
        raw = dumps({"id": value.get("id"), **error_envelope(
            XhsError("OUTPUT_TOO_LARGE", "Use the CLI for detailed diagnostics"))}).encode("utf-8")
    stream.write(struct.pack("<I", len(raw)) + raw)
    stream.flush()


def serve_native(store, input_stream, output_stream, *, session_factory=SyncSession):
    store.initialize()
    try:
        with session_factory(store):
            while True:
                message = read_message(input_stream)
                if message is None:
                    return 0
                try:
                    response = dispatch_sync(store, message["path"], message.get("body"))
                except XhsError as exc:
                    response = error_envelope(exc)
                except Exception as exc:
                    print(f"native operation error: {type(exc).__name__}", file=sys.stderr)
                    response = error_envelope(XhsError("LOCAL_OPERATION_FAILED", "The local operation failed", exit_code=5))
                write_message(output_stream, {"id": message["id"], **response})
    except XhsError as exc:
        write_message(output_stream, {"id": None, **error_envelope(exc)})
        return exc.exit_code


def main():
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    # Libraries occasionally print while loading models. Keep the protocol separate.
    sys.stdout = sys.stderr
    try:
        return serve_native(Store(library_path()), source, sink)
    except (OSError, ValueError) as exc:
        print(f"native host error: {type(exc).__name__}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
