from __future__ import annotations

import json

from .common import XhsError, digest, fields, integer, now, safe_id, text_field, dumps
from .network import validate_note_url, validate_page_url
from .access import access_restriction, require_access


def validate_collection(value):
    fields(value, {"id", "name", "source_url", "identity_kind"}, {"id", "name", "source_url"})
    safe_id(value["id"], "collection.id")
    text_field(value["name"], "collection.name", 200, False)
    validate_page_url(value["source_url"])
    if value.get("identity_kind", "local_view") not in {"platform", "local_view", "fixture"}:
        raise XhsError("INVALID_ARGUMENT", "Invalid collection identity_kind", exit_code=2)
    return dict(value, identity_kind=value.get("identity_kind", "local_view"))


def create_job(store, request):
    fields(request, {"job_id", "collection", "expected_count"}, {"job_id", "collection"})
    job_id = safe_id(request["job_id"], "job_id")
    collection = validate_collection(request["collection"])
    expected = request.get("expected_count")
    if expected is not None:
        integer(expected, "expected_count", 0, 100000)
    with store.write() as db:
        require_access(db)
        existing = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if existing:
            if existing["collection_id"] != collection["id"] or existing["expected_count"] != expected:
                raise XhsError("CONFLICT", "job_id already belongs to another request")
            return {"job_id": job_id, "duplicate": True}
        prior = db.execute("SELECT * FROM collections WHERE id=?", (collection["id"],)).fetchone()
        if prior and prior["source_url"] != collection["source_url"]:
            raise XhsError("CONFLICT", "Collection identity belongs to a different page")
        if db.execute("SELECT id FROM jobs WHERE collection_id=? AND scan_state='scanning'",
                      (collection["id"],)).fetchone():
            raise XhsError("SCAN_ALREADY_RUNNING", "Finish or resume the existing scan for this collection")
        db.execute("INSERT INTO collections VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "name=excluded.name,last_job=excluded.last_job,updated_at=excluded.updated_at",
                   (collection["id"], collection["name"], collection["source_url"],
                    collection["identity_kind"], job_id, now()))
        db.execute("INSERT INTO jobs(id,collection_id,expected_count,created_at,updated_at) VALUES (?,?,?,?,?)",
                   (job_id, collection["id"], expected, now(), now()))
        store.bump(db)
    return {"job_id": job_id, "duplicate": False}


def validate_card(card):
    fields(card, {"note_id", "source_url", "title"}, {"note_id", "source_url"})
    safe_id(card["note_id"], "note_id")
    validate_note_url(card["source_url"], card["note_id"])
    text_field(card.get("title", ""), "title", 1000)
    return {"note_id": card["note_id"], "source_url": card["source_url"], "title": card.get("title", "")}


def receive_batch(store, job_id, request):
    fields(request, {"seq", "items"}, {"seq", "items"})
    seq = integer(request["seq"], "seq", 0, 100000)
    if not isinstance(request["items"], list) or not 1 <= len(request["items"]) <= 50:
        raise XhsError("INVALID_ARGUMENT", "A batch must contain 1-50 cards", exit_code=2)
    items = [validate_card(item) for item in request["items"]]
    batch_hash = digest(items)
    with store.write() as db:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise XhsError("JOB_NOT_FOUND", "Create the job before sending batches")
        existing = db.execute("SELECT * FROM batches WHERE job_id=? AND seq=?", (job_id, seq)).fetchone()
        if existing:
            if existing["hash"] != batch_hash:
                raise XhsError("CONFLICT", "Batch sequence reused with different content")
            return {"job_id": job_id, "seq": seq, "accepted": existing["item_count"], "duplicate": True}
        if job["scan_state"] != "scanning":
            raise XhsError("CONFLICT", "This scan is finalized; start a new job")
        membership_changed = False
        for item in items:
            store.note_row(db, item)
            current = db.execute("SELECT current_revision FROM notes WHERE id=?", (item["note_id"],)).fetchone()
            if not current[0]:
                store.publish(dict(item, source_kind="xiaohongshu", body="", body_state="missing",
                                   images=[], expected_images=None, content_state="metadata_only",
                                   processing_state="pending", gaps=["BODY_MISSING", "IMAGE_COUNT_UNKNOWN"]), db=db)
            old = db.execute("SELECT state FROM memberships WHERE collection_id=? AND note_id=?",
                             (job["collection_id"], item["note_id"])).fetchone()
            membership_changed |= old is None or old[0] != "present"
            db.execute("INSERT INTO memberships VALUES (?,?,'present',0,?) ON CONFLICT(collection_id,note_id) "
                       "DO UPDATE SET state='present',miss_count=0,last_seen_job=excluded.last_seen_job",
                       (job["collection_id"], item["note_id"], job_id))
            db.execute("INSERT INTO job_items(job_id,note_id,payload,updated_at) VALUES (?,?,?,?) "
                       "ON CONFLICT(job_id,note_id) DO NOTHING",
                       (job_id, item["note_id"], dumps(item), now()))
        db.execute("INSERT INTO batches VALUES (?,?,?,?)", (job_id, seq, batch_hash, len(items)))
        db.execute("UPDATE jobs SET received_batches=received_batches+1,updated_at=? WHERE id=?", (now(), job_id))
        if membership_changed:
            store.bump(db)
    return {"job_id": job_id, "seq": seq, "accepted": len(items), "duplicate": False}


def finish_scan(store, job_id, request):
    fields(request, {"batch_count", "end_evidence"}, {"batch_count", "end_evidence"})
    count = integer(request["batch_count"], "batch_count", 0, 100000)
    evidence = request["end_evidence"]
    if evidence not in {"page_end", "user_verified", "unknown", "interrupted"}:
        raise XhsError("INVALID_ARGUMENT", "Invalid end_evidence", exit_code=2)
    with store.write() as db:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise XhsError("JOB_NOT_FOUND", "Unknown job")
        if job["scan_state"] != "scanning":
            return {"job_id": job_id, "scan_state": job["scan_state"], "duplicate": True}
        sequences = [r[0] for r in db.execute("SELECT seq FROM batches WHERE job_id=? ORDER BY seq", (job_id,))]
        if sequences != list(range(count)):
            raise XhsError("BATCHES_PENDING", "All numbered batches must be committed before finalizing")
        discovered = db.execute("SELECT count(*) FROM job_items WHERE job_id=?", (job_id,)).fetchone()[0]
        complete = evidence in {"page_end", "user_verified"} and (
            job["expected_count"] is None or job["expected_count"] == discovered)
        scan_state = "complete" if complete else "partial"
        db.execute("UPDATE jobs SET scan_state=?,end_evidence=?,applied=1,updated_at=? WHERE id=?",
                   (scan_state, evidence, now(), job_id))
        if complete:
            # Two independent complete scans, never two delivery retries, confirm absence.
            db.execute("UPDATE memberships SET miss_count=miss_count+1,"
                       "state=CASE WHEN miss_count+1>=2 THEN 'removed' ELSE 'unconfirmed' END "
                       "WHERE collection_id=? AND last_seen_job<>? AND state<>'removed'",
                       (job["collection_id"], job_id))
        store.bump(db)
    return {"job_id": job_id, "scan_state": scan_state, "discovered": discovered,
            "expected_count": job["expected_count"], "end_evidence": evidence}


def set_job_state(store, job_id, state, retry_failed=False):
    with store.write() as db:
        if state == "active":
            require_access(db)
        if not db.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
            raise XhsError("JOB_NOT_FOUND", "Unknown job")
        db.execute("UPDATE jobs SET state=?,error=CASE WHEN ?='active' THEN NULL ELSE error END,updated_at=? WHERE id=?",
                   (state, state, now(), job_id))
        if retry_failed:
            # A missing body needs a fresh normal page read, not the same incomplete snapshot.
            db.execute("UPDATE job_items SET payload=json_remove(payload,'$.browser_snapshot','$.browser_lease') "
                       "WHERE job_id=? AND state='partial' AND error LIKE 'BODY_%'", (job_id,))
            db.execute("UPDATE job_items SET state='queued',error=NULL WHERE job_id=? "
                       "AND state IN ('failed','partial') AND attempts<3", (job_id,))
    return {"job_id": job_id, "state": state}


def job_report(db, job_id):
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise XhsError("JOB_NOT_FOUND", "Unknown job")
    result = dict(row)
    result["access_restriction"] = access_restriction(db)
    counts = {r[0]: r[1] for r in db.execute(
        "SELECT state,count(*) FROM job_items WHERE job_id=? GROUP BY state", (job_id,))}
    result["items"] = counts
    result["discovered"] = sum(counts.values())
    result["list_complete"] = row["scan_state"] == "complete"
    result["content_scope"] = "body"
    result["body_complete"] = result["list_complete"] and counts.get("ready", 0) == result["discovered"]
    # Whole-note coverage still includes media. Body-only success must not claim it.
    archived = db.execute("SELECT count(*) FROM job_items i JOIN notes n ON n.id=i.note_id "
                          "JOIN revisions r ON r.id=n.current_revision WHERE i.job_id=? "
                          "AND json_extract(r.payload,'$.content_state')='complete'", (job_id,)).fetchone()[0]
    result["content_complete"] = result["list_complete"] and archived == result["discovered"]
    result["body_available"] = db.execute(
        "SELECT count(*) FROM job_items i JOIN notes n ON n.id=i.note_id "
        "JOIN revisions r ON r.id=n.current_revision WHERE i.job_id=? "
        "AND json_extract(r.payload,'$.body_state')='available'", (job_id,)).fetchone()[0]
    result["failure_summary"] = [dict(r) for r in db.execute(
        "SELECT error,count(*) AS count FROM job_items WHERE job_id=? AND state IN ('failed','partial') "
        "AND error IS NOT NULL GROUP BY error ORDER BY count(*) DESC LIMIT 8", (job_id,))]
    result["failures"] = [dict(r) for r in db.execute(
        "SELECT note_id,state,attempts,error FROM job_items WHERE job_id=? AND error IS NOT NULL LIMIT 50",
        (job_id,))]
    return result

