"""Create a source-only prototype bundle; exclude libraries, credentials and environments."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

root = Path(__file__).resolve().parents[1]
version = json.loads((root / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))["version"]
destination = root / "dist" / f"xhs-context-prototype-{version}.zip"
destination.parent.mkdir(exist_ok=True)
files = [root / name for name in ["README.md", "AGENTS.md", "安装.cmd", "pyproject.toml", "uv.lock", ".gitignore",
                                  "小红书收藏上下文项目计划书.md"]]
for name in ["src", "browser-extension", "assets", "skills", "scripts", "examples", "tests", "docs", "research"]:
    files.extend(path for path in (root / name).rglob("*")
                 if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".log"})
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, path.relative_to(root))
checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
destination.with_suffix(destination.suffix + ".sha256").write_text(f"{checksum}  {destination.name}\n", encoding="utf-8")
print(destination)

