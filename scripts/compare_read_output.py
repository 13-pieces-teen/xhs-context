"""Replay saved fixed-revision reads offline; verify evidence and measure JSON size."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from evaluate_archive import OFFLINE_CLI, inspect_archive, write_json
from xhs_context.common import library_path, now
from xhs_context.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Directory containing *-evidence.json baseline exports")
    parser.add_argument("--output", required=True)
    parser.add_argument("--library")
    args = parser.parse_args()
    source, out = Path(args.baseline), Path(args.output)
    if source.resolve() == out.resolve():
        parser.error("Keep the baseline in a separate directory")
    out.mkdir(parents=True, exist_ok=True)
    store = Store(Path(args.library) if args.library else library_path())
    before, manifest = inspect_archive(store)
    results = []
    for baseline_path in sorted(source.glob("*-evidence.json")):
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if "notes" not in baseline or "response_chars" not in baseline:
            continue
        expected = {(n["note_id"], n["revision_id"], b["block_id"]): {k: v for k, v in b.items() if k != "ordinal"}
                    for n in baseline["notes"] for b in n["blocks"]}
        request = {"items": [{k: n[k] for k in ("note_id", "revision_id")} for n in baseline["notes"]],
                   "mode": "full", "max_chars": 12000}
        records, actual, seen_cursors = [], {}, set()
        while True:
            request_path = out / (baseline_path.stem + "-request.json")
            write_json(request_path, request)
            process = subprocess.run([sys.executable, "-c", OFFLINE_CLI, "--library", str(store.root),
                                      "read", "--input", str(request_path.resolve())],
                                     capture_output=True, text=True, encoding="utf-8", timeout=60)
            if process.returncode != 0:
                raise RuntimeError(process.stdout or process.stderr)
            response = json.loads(process.stdout)
            size = len(process.stdout.strip())
            assert size <= request["max_chars"]
            records.append({"request": request, "response": response, "json_chars": size})
            for n in response["data"]["items"]:
                for block in n["blocks"]:
                    key = (n["note_id"], n["revision_id"], block["block_id"])
                    assert key not in actual, "Repeated evidence block across pages"
                    actual[key] = block
            cursor = response["data"]["next_cursor"]
            if not cursor:
                break
            assert cursor not in seen_cursors and len(records) < 100
            seen_cursors.add(cursor)
            request = {"cursor": cursor, "max_chars": 12000}
        assert actual == expected, "Evidence changed: compare IDs, versions, text and positions"
        write_json(out / (baseline_path.stem + "-read-pages.json"), records)
        results.append({"case": baseline_path.stem, "notes": len(baseline["notes"]), "blocks": len(actual),
                        "body_chars": sum(len(b["text"]) for b in actual.values()),
                        "baseline_chars": baseline["response_chars"], "current_chars": sum(r["json_chars"] for r in records),
                        "baseline_pages": baseline["page_count"], "current_pages": len(records),
                        "fixed_revision_evidence_identical": True})
    assert results, "No baseline evidence exports found"
    after, after_manifest = inspect_archive(store)
    assert before == after and manifest == after_manifest, "Archive changed during evaluation"
    summary = {"checked_at": now(), "generation": before["generation"], "network_denied": True,
               "archive_unchanged": True, "measurement": "serialized JSON characters, not tokens", "cases": results,
               "baseline_chars": sum(r["baseline_chars"] for r in results),
               "current_chars": sum(r["current_chars"] for r in results),
               "baseline_pages": sum(r["baseline_pages"] for r in results),
               "current_pages": sum(r["current_pages"] for r in results)}
    summary["character_reduction_percent"] = round(100 * (1 - summary["current_chars"] / summary["baseline_chars"]), 2)
    write_json(out / "read-output-comparison.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
