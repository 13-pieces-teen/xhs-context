"""Create a source-only prototype bundle; exclude libraries, credentials and environments."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

root = Path(__file__).resolve().parents[1]
version = json.loads((root / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))["version"]
destination = root / "dist" / f"xhs-context-prototype-{version}.zip"
destination.parent.mkdir(exist_ok=True)
files = [root / name for name in ("README.md", "AGENTS.md", "CHANGELOG.md", "安装.cmd", "pyproject.toml", "uv.lock", ".gitignore")]
# Keep local authoring files out of builds from both a Git checkout and an extracted source bundle.
local_paths = {root / name for name in (
    "docs/figma-panel-build.js",
    "docs/figma-panel-nodes.json",
    "examples/evaluation-questions.json",
    "assets/readme/generation.json",
    "assets/readme/panel-overview.png",
    "assets/guides/DESIGN.md",
    "assets/guides/README.md",
    "assets/guides/build_composition.py",
    "assets/guides/capture_panels.py",
    "assets/guides/export_guides.py",
    "assets/guides/hyperframes",
    "assets/guides/panels",
    "assets/guides/segments.json",
)}
for name in ("src", "browser-extension", "assets", "skills", "scripts", "examples", "tests", "docs", ".github"):
    files.extend(path for path in (root / name).rglob("*")
                 if path.is_file() and not {"__pycache__", ".hyperframes", "node_modules"}.intersection(path.parts)
                 and local_paths.isdisjoint((path, *path.parents))
                 and path.suffix not in {".pyc", ".log"})
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, path.relative_to(root))
checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
destination.with_suffix(destination.suffix + ".sha256").write_text(f"{checksum}  {destination.name}\n", encoding="utf-8")
print(destination)

