"""Run the real CLI against a synthetic fixture; not a live XHS acceptance test."""
from pathlib import Path
import json
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
library = root / "var" / "demo"
report_dir = root / "var" / "validation"
report_dir.mkdir(parents=True, exist_ok=True)
steps = []


def call(name, *args):
    start = time.perf_counter()
    result = subprocess.run([sys.executable, "-m", "xhs_context", "--library", str(library), *args],
                            cwd=root, capture_output=True, text=True, encoding="utf-8")
    payload = json.loads(result.stdout)
    step = {"name": name, "seconds": round(time.perf_counter() - start, 4),
            "exit_code": result.returncode, "response": payload}
    if result.stderr:
        step["diagnostics"] = result.stderr[-2000:]
    steps.append(step)
    if result.returncode not in {0, 3} or not payload.get("ok"):
        raise RuntimeError(name + ": " + result.stdout)
    return payload["data"]


def main():
    subprocess.run([sys.executable, str(root / "examples/make_fixture.py")], check=True)
    call("initialize", "init")
    call("ingest_with_real_ocr", "ingest", "--input", str(root / "examples/demo-batch.json"))
    call("status", "status")
    found = call("image_only_search", "search", "--query", "银河土豆饼", "--content", "all")
    if not found["items"]:
        raise RuntimeError("Image-only phrase was not retrieved; inspect OCR output")
    chosen = found["items"][0]
    if not any(snippet["kind"] == "ocr" for snippet in chosen["snippets"]):
        raise RuntimeError("Expected image OCR evidence")
    request = {"items": [{"note_id": chosen["note_id"], "revision_id": chosen["revision_id"],
                           "block_ids": [chosen["snippets"][0]["block_id"]]}], "mode": "evidence"}
    request_path = report_dir / "read-evidence.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    call("read_ocr_evidence", "read", "--input", str(request_path))
    request["mode"] = "asset"
    request["items"][0].pop("block_ids")
    request["items"][0]["asset_ids"] = ["img-1"]
    request_path = report_dir / "read-asset.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    asset = call("read_original_image", "read", "--input", str(request_path))
    if not Path(asset["items"][0]["assets"][0]["path"]).is_file():
        raise RuntimeError("Original image path not available")
    call("madrid_search", "search", "--query", "马德里 小吃")
    call("infra_search", "search", "--query", "infra 入门")
    call("export", "export")
    return {"dataset": "synthetic_fixture", "live_xhs": "NOT_RUN", "steps": steps}


if __name__ == "__main__":
    try:
        report = main()
        report["status"] = "PASS"
    except Exception as exc:
        report = {"status": "FAIL", "dataset": "synthetic_fixture", "error": str(exc), "steps": steps}
    (report_dir / "smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(report_dir / "smoke.json"),
                      "steps": [{"name": s["name"], "seconds": s["seconds"], "exit_code": s["exit_code"]} for s in steps],
                      "error": report.get("error")}, ensure_ascii=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)

