"""Read-only evaluation of an existing archive using a frozen set of queries.

The CLI subprocesses deny Python socket/DNS access. No sync or remote checks run.
Cases and outputs can contain personal archive references: keep them under var/.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

from xhs_context.access import access_restriction
from xhs_context.common import library_path, now
from xhs_context.storage import Store

OFFLINE_CLI = """
import socket, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
def denied(*args, **kwargs):
    raise AssertionError('Evaluation forbids network access')
socket.socket = socket.create_connection = socket.getaddrinfo = denied
from xhs_context.cli import main
raise SystemExit(main(sys.argv[1:]))
"""


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def fingerprint(entries):
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def inspect_archive(store):
    with store.read() as db:
        rows = db.execute("SELECT n.id,n.current_revision,r.payload FROM notes n JOIN revisions r "
                          "ON r.id=n.current_revision WHERE n.deleted=0 ORDER BY n.id").fetchall()
        manifest = [{"note_id": row["id"], "revision_id": row["current_revision"]} for row in rows]
        lengths, issues, sources, scope = [], [], Counter(), Counter()
        duplicate_bodies, media_references = Counter(), 0
        checked_blocks, checked_raw = 0, 0
        for row in rows:
            note = json.loads(row["payload"])
            scope[note.get("content_state", "unknown")] += 1
            if note.get("body_state") != "available":
                continue
            body = note.get("body", "")
            lengths.append(len(body.strip()))
            sources[note.get("content_source", "unspecified")] += 1
            if not body.strip():
                issues.append({"note_id": row["id"], "code": "EMPTY_AVAILABLE_BODY"})
            duplicate_bodies[hashlib.sha256(body.strip().encode()).hexdigest()] += 1
            media_references += bool(re.search(r"图[一二三四五六七八九十0-9]|看图|见图|如图|图中|视频", body))
            blocks = db.execute("SELECT block_id,text,location FROM evidence_blocks "
                                "WHERE revision_id=? AND kind='body' ORDER BY ordinal", (row["current_revision"],)).fetchall()
            covered = set()
            for block in blocks:
                checked_blocks += 1
                location = json.loads(block["location"])
                start, end = location["start"], location["end"]
                if body[start:end] != block["text"]:
                    issues.append({"note_id": row["id"], "block_id": block["block_id"], "code": "OFFSET_MISMATCH"})
                covered.update(range(start, end))
            if any(not char.isspace() and i not in covered for i, char in enumerate(body)):
                issues.append({"note_id": row["id"], "code": "BODY_CHARACTERS_NOT_INDEXED"})
            raw = note.get("raw_object")
            if raw:
                data = store.object_path(raw["object_path"]).read_bytes()
                checked_raw += 1
                if hashlib.sha256(data).hexdigest() != raw["sha256"]:
                    issues.append({"note_id": row["id"], "code": "RAW_OBJECT_HASH_MISMATCH"})
            else:
                issues.append({"note_id": row["id"], "code": "RAW_OBJECT_MISSING"})
        audit = {"generation": store.generation(db), "notes": len(rows), "body_available": len(lengths),
                 "content_states": dict(scope), "content_sources": dict(sources), "body_characters": sum(lengths),
                 "body_length": {"min": min(lengths, default=0), "median": statistics.median(lengths) if lengths else 0,
                                 "max": max(lengths, default=0), "under_50": sum(n < 50 for n in lengths)},
                 "notes_mentioning_images_or_video_heuristic": media_references,
                 "duplicate_body_groups": sum(n > 1 for n in duplicate_bodies.values()),
                 "checked_body_blocks": checked_blocks, "checked_raw_objects": checked_raw,
                 "integrity_issues": issues, "access_restriction": access_restriction(db),
                 "jobs": [dict(row) for row in db.execute("SELECT state,scan_state,expected_count FROM jobs")],
                 "manifest_sha256": fingerprint(manifest)}
    return audit, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    store = Store(Path(args.library) if args.library else library_path())
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_path = Path(args.cases)
    cases = json.loads(case_path.read_text(encoding="utf-8"))
    audit, manifest = inspect_archive(store)
    write_json(out / "audit.json", audit)
    write_json(out / "manifest.json", manifest)
    cases_sha = hashlib.sha256(case_path.read_bytes()).hexdigest()
    results = []
    for case in cases["cases"]:
        request = {"queries": case["queries"], "limit": 8, "max_chars": 12000}
        request.update({key: case[key] for key in ("content", "required_terms", "match") if key in case})
        request_path = out / (case["id"] + "-request.json")
        write_json(request_path, request)
        start = time.perf_counter()
        process = subprocess.run([sys.executable, "-c", OFFLINE_CLI, "--library", str(store.root),
                                  "search", "--input", str(request_path.resolve())],
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if process.returncode not in {0, 3}:
            raise RuntimeError(process.stdout or process.stderr)
        response = json.loads(process.stdout)
        write_json(out / (case["id"] + "-response.json"), response)
        items = response["data"]["items"]
        expected = case.get("expected_note_ids", [])
        ranks = {note_id: next((i + 1 for i, item in enumerate(items) if item["note_id"] == note_id), None)
                 for note_id in expected}
        result = {"id": case["id"], "queries": case["queries"], "matched_count": response["data"]["matched_count"],
                  "returned": len(items), "metadata_only_in_page": sum(i["content_state"] == "metadata_only" for i in items),
                  "expected_ranks_in_returned_page": ranks,
                  "known_targets_found": sum(rank is not None for rank in ranks.values()), "known_targets": len(expected),
                  "all_known_targets_in_returned_page": all(rank is not None for rank in ranks.values()) if expected else None,
                  "cold_cli_ms": elapsed, "response_chars": len(process.stdout.strip()),
                  "has_next_page": bool(response["data"]["next_cursor"]), "partial": response["partial"]}
        result["unavailable_body_matches"] = response["data"].get("unavailable_body_matches", 0)
        result["noncontent_body_matches"] = response["data"].get("noncontent_body_matches", 0)
        if case.get("expect_empty"):
            result["empty_result_correct"] = response["data"]["matched_count"] == 0
        results.append(result)
    after, after_manifest = inspect_archive(store)
    assert audit["generation"] == after["generation"] and manifest == after_manifest, "Archive changed during evaluation"
    assert audit["access_restriction"] == after["access_restriction"] and audit["jobs"] == after["jobs"]
    summary = {"checked_at": now(), "dataset": "existing_real_archive", "generation": audit["generation"],
               "manifest_sha256": audit["manifest_sha256"], "cases_sha256": cases_sha,
               "network_denied_in_cli_subprocesses": True, "live_xhs": "NOT_RUN", "maps": "NOT_RUN",
               "archive_unchanged": True, "case_count": len(results),
               "cold_cli_ms": {"min": min(r["cold_cli_ms"] for r in results),
                               "median": statistics.median(r["cold_cli_ms"] for r in results),
                               "max": max(r["cold_cli_ms"] for r in results)},
               "case_selection": cases.get("selection", "source_selected_known_targets"),
               "limits": "Small local sample; without exhaustive human labels this is not overall relevance or recall accuracy. Character counts are not tokens.",
               "results": results}
    write_json(out / "retrieval-evaluation.json", summary)
    print(json.dumps({"output": str(out), "notes": audit["notes"], "body_available": audit["body_available"],
                      "integrity_issues": len(audit["integrity_issues"]), "cases": len(results),
                      "cold_cli_ms": summary["cold_cli_ms"], "archive_unchanged": True}))
    return 0 if not audit["integrity_issues"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
