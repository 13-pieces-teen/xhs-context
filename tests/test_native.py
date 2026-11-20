import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from xhs_context.common import XhsError, atomic_json, config_path, library_path
from xhs_context.native import read_message, write_message, serve_native, MAX_REQUEST
from xhs_context.setup import extension_id


def test_native_frames_preserve_utf8_and_request_ids():
    value = {"id": "request-one", "path": "/v1/jobs", "body": {"name": "中文收藏"}}
    stream = io.BytesIO()
    write_message(stream, value)
    stream.seek(0)
    assert read_message(stream) == value
    assert read_message(stream) is None


@pytest.mark.parametrize("raw", [b"\x01\x00", struct.pack("<I", MAX_REQUEST + 1),
                                   struct.pack("<I", 4) + b"nope", struct.pack("<I", 100) + b"{}"])
def test_native_rejects_truncated_or_oversize_frames(raw):
    with pytest.raises(XhsError):
        read_message(io.BytesIO(raw))


def test_real_native_entry_point_autostarts_library_and_stops_on_eof(tmp_path):
    request = io.BytesIO()
    write_message(request, {"id": "ready", "path": "/v1/status"})
    root = tmp_path / "本机资料"
    exe = Path(sys.executable).parent / "xhs-context-native.exe"
    result = subprocess.run([str(exe)] if exe.exists() else [sys.executable, "-m", "xhs_context.native"],
                            input=request.getvalue(), capture_output=True, timeout=30,
                            env={**os.environ, "XHS_CONTEXT_HOME": str(root)})
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    size = struct.unpack("<I", result.stdout[:4])[0]
    response = json.loads(result.stdout[4:4 + size])
    assert len(result.stdout) == size + 4
    assert response["id"] == "ready" and response["ok"]
    assert response["data"]["library"] == str(root.resolve())
    assert (root / "library.sqlite").exists()
    assert not (root / "pairing.json").exists()


def test_setup_default_is_shared_by_cli_and_native_host(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("XHS_CONTEXT_HOME", raising=False)
    chosen = tmp_path / "my-library"
    atomic_json(config_path(), {"library": str(chosen)})
    assert library_path() == chosen.resolve()
    assert library_path(tmp_path / "override") != chosen.resolve()
    monkeypatch.setenv("XHS_CONTEXT_HOME", str(tmp_path / "environment"))
    assert library_path().name == "environment"


def test_extension_identity_is_stable_across_install_paths():
    root = Path(__file__).resolve().parents[1]
    assert extension_id(root / "browser-extension") == "mehmgdlepmchnakhfmcbhdcgciphljpb"
