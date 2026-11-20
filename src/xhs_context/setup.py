"""One-time per-user registration; no admin rights, cookies or browser profile edits."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from .common import XhsError, atomic_json, config_path, library_path
from .native import HOST_NAME
from .storage import Store

REGISTRY_ROOTS = (r"Software\Google\Chrome\NativeMessagingHosts",
                  r"Software\Microsoft\Edge\NativeMessagingHosts")


def extension_id(extension_dir):
    manifest = json.loads((Path(extension_dir) / "manifest.json").read_text(encoding="utf-8"))
    raw = base64.b64decode(manifest["key"], validate=True)
    return "".join(chr(97 + int(n, 16)) for n in hashlib.sha256(raw).hexdigest()[:32])


def register_host(manifest_path, *, host_name=HOST_NAME, registry=None):
    if registry is None:
        if os.name != "nt":
            raise XhsError("UNSUPPORTED_PLATFORM", "Automatic host setup currently supports Windows", exit_code=2)
        import winreg as registry
    targets = [root + "\\" + host_name for root in REGISTRY_ROOTS]
    # Check both before writing. Never replace another checkout silently.
    for key in targets:
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, key) as handle:
                existing, _ = registry.QueryValueEx(handle, "")
            if Path(existing).resolve() != Path(manifest_path).resolve():
                raise XhsError("HOST_ALREADY_REGISTERED", "Another installation owns this native host",
                               details={"manifest": existing})
        except FileNotFoundError:
            pass
    for key in targets:
        with registry.CreateKey(registry.HKEY_CURRENT_USER, key) as handle:
            registry.SetValueEx(handle, "", 0, registry.REG_SZ, str(manifest_path))
    return targets


def install_host(extension_dir, selected_library=None):
    extension_dir = Path(extension_dir).resolve()
    executable = Path(sys.executable).parent / "xhs-context-native.exe"
    if not executable.is_file():
        raise XhsError("HOST_NOT_INSTALLED", "Run scripts/install.ps1 to install the native entry point")
    selected_library = library_path(selected_library)
    # Preserve the first prototype's library if no default has been configured.
    legacy = extension_dir.parent / "var/library"
    if not config_path().exists() and not selected_library.exists() and (legacy / "library.sqlite").is_file():
        selected_library = legacy.resolve()
    manifest_path = config_path().parent / "native-host.json"
    value = {"name": HOST_NAME, "description": "xhs-context local archive",
             "path": str(executable), "type": "stdio",
             "allowed_origins": [f"chrome-extension://{extension_id(extension_dir)}/"]}
    # Detect a different installation before overwriting its manifest.
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if Path(previous.get("path", "")).resolve() != executable.resolve():
            raise XhsError("HOST_ALREADY_REGISTERED", "Another installation owns this native host")
    Store(selected_library).initialize()
    atomic_json(manifest_path, value)
    registered = register_host(manifest_path)
    atomic_json(config_path(), {"library": str(selected_library), "extension_dir": str(extension_dir),
                                "cli_path": str(executable.with_name('xhs-context.exe'))})
    return {"installed": True, "extension_dir": str(extension_dir), "library": str(selected_library),
            "native_host": str(manifest_path), "browsers": ["Chrome", "Edge"], "registry_keys": registered}
