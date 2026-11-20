"""Explicit browser-assisted reading of normal note pages, with durable leases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from .common import XhsError, dumps, fields, integer, now, safe_id, text_field
from .network import validate_note_url, validate_url
from .processing import finish_body, body_error, preserve_separate_images
from .access import access_restriction, require_access, restrict_access


def enable_browser(store, job_id):
    with store.write() as db:
        require_access(db)
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise XhsError("JOB_NOT_FOUND", "Unknown job")
        if job["content_mode"] != "browser":
            # A different acquisition method gets a fresh retry budget.
            db.execute("UPDATE job_items SET state='queued',attempts=0,error=NULL WHERE job_id=? "
                       "AND state IN ('queued','failed','partial')", (job_id,))
        else:
            db.execute("UPDATE job_items SET payload=json_remove(payload,'$.browser_snapshot','$.browser_lease') "
                       "WHERE job_id=? AND state='partial' AND error LIKE 'BODY_%'", (job_id,))
            db.execute("UPDATE job_items SET state='queued',error=NULL WHERE job_id=? "
                       "AND state IN ('failed','partial') AND attempts<3", (job_id,))
        db.execute("UPDATE jobs SET content_mode='browser',state='active',error=NULL,updated_at=? WHERE id=?",
                   (now(), job_id))
    return {"job_id": job_id, "content_mode": "browser"}


def next_page(store):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="milliseconds")
    with store.write() as db:
        restriction = access_restriction(db)
        if restriction:
            return {"task": None, "access_restriction": restriction}
        db.execute("UPDATE job_items SET state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,"
                   "error='BROWSER_TIMEOUT' WHERE state='browser_running' AND updated_at<?", (cutoff,))
        if db.execute("SELECT 1 FROM job_items WHERE state='browser_running'").fetchone():
            return {"task": None}
        # Drain snapshots left by an interrupted older host before more pages.
        if db.execute("SELECT count(*) FROM job_items i JOIN jobs j ON j.id=i.job_id "
                      "WHERE j.state='active' AND i.state IN ('queued','running') "
                      "AND json_extract(i.payload,'$.browser_snapshot') IS NOT NULL").fetchone()[0] >= 3:
            return {"task": None}
        row = db.execute("SELECT i.* FROM job_items i JOIN jobs j ON j.id=i.job_id "
                         "WHERE j.state='active' AND j.content_mode='browser' AND i.state='queued' "
                         "AND i.attempts<3 AND json_extract(i.payload,'$.browser_snapshot') IS NULL "
                         "ORDER BY i.updated_at,i.note_id LIMIT 1").fetchone()
        if not row:
            return {"task": None}
        payload = json.loads(row["payload"])
        lease = uuid.uuid4().hex
        payload["browser_lease"] = lease
        db.execute("UPDATE job_items SET state='browser_running',attempts=attempts+1,payload=?,updated_at=? "
                   "WHERE job_id=? AND note_id=?", (dumps(payload), now(), row["job_id"], row["note_id"]))
        return {"task": {"job_id": row["job_id"], "note_id": row["note_id"],
                         "source_url": payload["source_url"], "lease_id": lease}}


def normalize_snapshot(snapshot, card):
    fields(snapshot, {"note_id", "page_url", "title", "body", "body_present", "images", "expected_images", "note_type"},
           {"note_id", "page_url", "title", "body", "body_present"})
    if snapshot["note_id"] != card["note_id"]:
        raise XhsError("URL_NOTE_MISMATCH", "Snapshot is for a different note")
    validate_note_url(snapshot["page_url"], card["note_id"])
    title = text_field(snapshot["title"], "title", 1000)
    body = text_field(snapshot["body"], "body", 100000)
    note_type = snapshot.get("note_type", "unknown")
    if not isinstance(snapshot["body_present"], bool) or note_type not in {"image", "video", "unknown"}:
        raise XhsError("INVALID_ARGUMENT", "Invalid browser note fields")
    urls = snapshot.get("images", [])
    if not isinstance(urls, list) or len(urls) > 100:
        raise XhsError("INVALID_ARGUMENT", "At most 100 page images are accepted")
    expected = snapshot.get("expected_images")
    if expected is not None:
        integer(expected, "expected_images", 0, 100)
        if expected < len(urls):
            raise XhsError("INVALID_ARGUMENT", "Image total cannot be smaller than the captured list")
    images = []
    for index, url in enumerate(urls, 1):
        text_field(url, "image URL", 10000)
        if url:
            validate_url(url, media=True)
        images.append({"asset_id": f"img-{index}", "source_url": url, "quality": "page_default", "state": "pending"})
    if not title and not body and not images:
        raise XhsError("BROWSER_PARSE_FAILED", "The page did not contain note content")
    return finish_body({"note_id": card["note_id"], "source_url": card["source_url"], "source_kind": "xiaohongshu",
            "canonical_url": "https://www.xiaohongshu.com/explore/" + card["note_id"],
            "title": title, "body": body, "body_state": "available" if snapshot["body_present"] else "missing",
            "images": images, "expected_images": expected, "note_type": note_type,
            "content_source": "browser_dom", "detail_context": "rendered_note", "fetched_at": now()})


def accept_page(store, job_id, request):
    fields(request, {"note_id", "lease_id", "snapshot", "error"}, {"note_id", "lease_id"})
    note_id = safe_id(request["note_id"], "note_id")
    lease = safe_id(request["lease_id"], "lease_id")
    if ("snapshot" in request) == ("error" in request):
        raise XhsError("INVALID_ARGUMENT", "Provide either a snapshot or an error")
    with store.write() as db:
        row = db.execute("SELECT i.*,j.state AS job_state FROM job_items i JOIN jobs j ON j.id=i.job_id "
                         "WHERE i.job_id=? AND i.note_id=?", (job_id, note_id)).fetchone()
        if not row:
            raise XhsError("JOB_NOT_FOUND", "Unknown browser task")
        payload = json.loads(row["payload"])
        if payload.get("browser_lease") != lease:
            raise XhsError("CONFLICT", "Browser task lease expired")
        if row["state"] != "browser_running":
            return {"accepted": True, "duplicate": True}
        # A genuine leased page can reveal a restriction while a pause races it.
        # Preserve that signal even when ordinary content results are ignored.
        if request.get("error") == "ACCESS_RESTRICTED":
            restrict_access(db)
            db.execute("UPDATE job_items SET state='failed',error='ACCESS_RESTRICTED',updated_at=? "
                       "WHERE job_id=? AND note_id=?", (now(), job_id, note_id))
            return {"accepted": True, "restricted": True}
        if row["job_state"] != "active":
            db.execute("UPDATE job_items SET state='queued',attempts=MAX(0,attempts-1),error=NULL,updated_at=? "
                       "WHERE job_id=? AND note_id=?", (now(), job_id, note_id))
            return {"accepted": True, "paused": True}
        if "snapshot" in request:
            note = normalize_snapshot(request["snapshot"], payload)
            note["raw_object"] = store.put_object(dumps(request["snapshot"]).encode("utf-8"), ".json")
            payload["browser_snapshot"] = note
            # A committed body is the completion point for this prototype.
            prior = db.execute("SELECT r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=?",
                               (note_id,)).fetchone()
            previous = json.loads(prior[0]) if prior else {}
            ready = note["body_state"] == "available"
            unchanged = previous.get("body_state") == "available" and all(
                previous.get(key) == note.get(key) for key in ("title", "body"))
            if not unchanged and (ready or previous.get("body_state") != "available"):
                store.publish(preserve_separate_images(note, previous), db=db)
            db.execute("UPDATE job_items SET payload=?,state=?,error=?,updated_at=? WHERE job_id=? AND note_id=?",
                       (dumps(payload), "ready" if ready else "partial", body_error(note), now(), job_id, note_id))
        else:
            code = request["error"]
            if code not in {"ACCESS_BLOCKED", "SOURCE_UNAVAILABLE", "SOURCE_LINK_EXPIRED", "BROWSER_TIMEOUT", "BROWSER_PARSE_FAILED", "BROWSER_CLOSED", "BROWSER_PAUSED"}:
                raise XhsError("INVALID_ARGUMENT", "Unknown browser error")
            db.execute("UPDATE job_items SET state=?,error=?,attempts=attempts-?,updated_at=? WHERE job_id=? AND note_id=?",
                       ("queued" if code == "BROWSER_PAUSED" else "failed", None if code == "BROWSER_PAUSED" else code,
                        int(code == "BROWSER_PAUSED"), now(), job_id, note_id))
            if code in {"ACCESS_BLOCKED", "BROWSER_CLOSED", "BROWSER_TIMEOUT"}:
                db.execute("UPDATE jobs SET state='paused',error=?,updated_at=? WHERE id=?", (code, now(), job_id))
    return {"accepted": True, "duplicate": False}
