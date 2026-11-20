from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
OUTPUT_SCHEMA_VERSION = 2


class XhsError(Exception):
    def __init__(self, code: str, message: str, *, details=None, exit_code=4):
        super().__init__(message)
        self.code, self.message, self.details, self.exit_code = code, message, details, exit_code


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else dumps(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def envelope(data=None, *, warnings=None, partial=False):
    return {"schema_version": OUTPUT_SCHEMA_VERSION, "ok": True, "partial": partial,
            "data": data, "warnings": warnings or []}


def error_envelope(exc: XhsError):
    return {"schema_version": OUTPUT_SCHEMA_VERSION, "ok": False, "partial": False,
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "warnings": []}


def config_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "xhs-context/config.json"


def library_path(value=None) -> Path:
    selected = value or os.environ.get("XHS_CONTEXT_HOME")
    if not selected and config_path().is_file():
        try:
            selected = json.loads(config_path().read_text(encoding="utf-8")).get("library")
        except (ValueError, OSError):
            raise XhsError("INVALID_CONFIG", "Re-run xhs-context setup to repair the library configuration") from None
    return Path(selected or config_path().parent / "library").expanduser().resolve()


def safe_id(value, label="ID") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise XhsError("INVALID_ARGUMENT", f"{label} must contain 1-100 letters, digits, _ or -", exit_code=2)
    return value


def integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise XhsError("INVALID_ARGUMENT", f"{label} must be an integer between {low} and {high}", exit_code=2)
    return value


def fields(value, allowed, required=()):
    if not isinstance(value, dict):
        raise XhsError("INVALID_ARGUMENT", "Expected a JSON object", exit_code=2)
    extra = set(value) - set(allowed)
    missing = set(required) - set(value)
    if extra or missing:
        raise XhsError("INVALID_ARGUMENT", "Unexpected or missing fields",
                       details={"unexpected": sorted(extra), "missing": sorted(missing)}, exit_code=2)


def text_field(value, label, max_length=10000, allow_empty=True):
    if not isinstance(value, str) or len(value) > max_length or (not allow_empty and not value.strip()):
        raise XhsError("INVALID_ARGUMENT", f"Invalid {label} (maximum {max_length} characters)", exit_code=2)
    return value


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(value))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def encode_cursor(value):
    return base64.urlsafe_b64encode(dumps(value).encode()).decode().rstrip("=")


def decode_cursor(value, kind):
    try:
        if not isinstance(value, str) or len(value) > 32768:
            raise ValueError()
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4),
                                          altchars=b"-_", validate=True))
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("kind") != kind:
            raise ValueError()
        return data
    except (ValueError, TypeError, UnicodeError):
        raise XhsError("INVALID_ARGUMENT", "Invalid cursor", exit_code=2) from None


def bounded_page(units, offset, make_response, max_chars, make_cursor):
    """Budget the complete serialized envelope, including the next-page cursor."""
    integer(max_chars, "max_chars", 512, 200000)
    selected = []
    for unit in units[offset:]:
        candidate = selected + [unit]
        next_offset = offset + len(candidate)
        cursor = make_cursor(next_offset) if next_offset < len(units) else None
        response = make_response(candidate, cursor)
        if len(dumps(response)) > max_chars:
            if not selected:
                raise XhsError("OUTPUT_BUDGET_TOO_SMALL", "Increase max_chars to fit one complete evidence unit",
                               details={"minimum_chars": len(dumps(response))}, exit_code=2)
            break
        selected = candidate
    cursor = make_cursor(offset + len(selected)) if offset + len(selected) < len(units) else None
    response = make_response(selected, cursor)
    if len(dumps(response)) > max_chars:
        raise XhsError("OUTPUT_BUDGET_TOO_SMALL", "Budget cannot fit response metadata", exit_code=2)
    return response

