"""Explicit local snapshot import, also used by the clearly labelled demo fixture."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .common import XhsError, fields, now, text_field
from .jobs import create_job, receive_batch, finish_scan, validate_card
from .processing import LocalOCR, finish_content, store_image


def ingest_file(store, source, ocr=True):
    source = Path(source).resolve()
    if source.stat().st_size > 20 * 1024 * 1024:
        raise XhsError("INVALID_ARGUMENT", "Local import JSON exceeds 20 MiB", exit_code=2)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    fields(data, {"collection", "notes", "dataset_kind"}, {"collection", "notes"})
    notes = data["notes"]
    if not isinstance(notes, list) or not 1 <= len(notes) <= 10000:
        raise XhsError("INVALID_ARGUMENT", "Local import requires 1-10000 notes", exit_code=2)
    collection = data["collection"]
    prepared = []
    engine = LocalOCR() if ocr else None
    for original in notes:
        fields(original, {"note_id", "source_url", "title", "body", "images", "note_type"},
               {"note_id", "source_url", "title", "body"})
        text_field(original["body"], "body", 1000000)
        validate_card({k: original[k] for k in ("note_id", "source_url", "title")})
        note = dict(original, source_kind="synthetic_fixture" if data.get("dataset_kind") == "synthetic" else "local_import",
                    canonical_url=original["source_url"].split("?")[0], body_state="available",
                    expected_images=len(original.get("images", [])), images=[])
        for index, image in enumerate(original.get("images", []), 1):
            fields(image, {"path"}, {"path"})
            asset = {"asset_id": f"img-{index}", "quality": "local_original"}
            path = (source.parent / image["path"]).resolve()
            note["images"].append(store_image(store, path.read_bytes(), asset, engine))
        prepared.append(finish_content(note))
    job_id = "import-" + uuid.uuid4().hex
    create_job(store, {"job_id": job_id, "collection": collection, "expected_count": len(prepared)})
    for seq, start in enumerate(range(0, len(prepared), 50)):
        batch = prepared[start:start + 50]
        receive_batch(store, job_id, {"seq": seq, "items": [
            {k: note[k] for k in ("note_id", "source_url", "title")} for note in batch]})
        with store.write() as db:
            for note in batch:
                store.publish(note, db=db)
                ready = note["processing_state"] == "ready"
                db.execute("UPDATE job_items SET state=?,error=?,updated_at=? WHERE job_id=? AND note_id=?",
                           ("ready" if ready else "partial", None if ready else ",".join(note["gaps"]),
                            now(), job_id, note["note_id"]))
    finish_scan(store, job_id, {"batch_count": (len(prepared) + 49) // 50, "end_evidence": "user_verified"})
    return {"job_id": job_id, "imported": len(prepared), "dataset_kind": data.get("dataset_kind", "local_import"),
            "partial": any(n["processing_state"] != "ready" for n in prepared)}

