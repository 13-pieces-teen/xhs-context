"""Explicit browser-assisted reading of normal note pages, with durable leases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from .common import XhsError, dumps, fields, integer, now, safe_id, text_field
from .network import validate_note_url, validate_url
from .processing import finish_body, body_error, preserve_separate_images
from .access import access_restriction, require_access, restrict_access
from .browser_retry import RETRYABLE, DELAYS, clock_ms, pending_retry
from .jobs import SKIPPED_SOURCE_ERRORS, skip_unavailable_items
from .invalid_sources import invalid_source, mark_invalid, skip_invalid_items


def enable_browser(store, job_id):
    with store.write() as db:
        require_access(db)
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise XhsError("JOB_NOT_FOUND", "Unknown job")
        skip_unavailable_items(db, job_id)
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
    """One browser tab serves explicit body and image-gallery work."""
    from .image_browser import next_image_page
    with store.read() as db:
        previous = db.execute("SELECT value FROM meta WHERE key='browser_last_kind'").fetchone()
    # A long body queue must not starve a user-requested gallery task.
    order = [('body', _next_body_page), ('images', next_image_page)]
    if previous and previous[0] == 'body':
        order.reverse()
    pending = {'task': None}
    for kind, select in order:
        result = select(store)
        if result.get('access_restriction'):
            return result
        if result.get('task'):
            with store.write() as db:
                db.execute("INSERT INTO meta(key,value) VALUES('browser_last_kind',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (kind,))
            return result
        if result.get('browser_retry'):
            pending = result
    return pending


def _next_body_page(store):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="milliseconds")
    with store.write() as db:
        skip_invalid_items(db)
        restriction = access_restriction(db)
        if restriction:
            return {"task": None, "access_restriction": restriction}
        expired = db.execute("SELECT i.*,j.state AS job_state FROM job_items i JOIN jobs j ON j.id=i.job_id "
                             "WHERE i.state='browser_running' AND i.updated_at<?", (cutoff,)).fetchall()
        for item in expired:
            paused = item['job_state'] != 'active'
            attempts = max(0, item['attempts'] - int(paused))
            exhausted = not paused and attempts >= 3
            # The lease has already waited 90 seconds. Retry this item first;
            # worker replacement must not bypass the same three-attempt limit.
            payload = json.loads(item['payload'])
            payload['browser_retry_at'] = 0
            db.execute("UPDATE job_items SET state=?,attempts=?,error=?,payload=?,updated_at=? WHERE job_id=? AND note_id=?",
                       ('failed' if exhausted else 'queued', attempts, None if paused else 'BROWSER_TIMEOUT',
                        dumps(payload), now(), item['job_id'], item['note_id']))
            if exhausted:
                db.execute("UPDATE jobs SET state='paused',error='BROWSER_TIMEOUT',updated_at=? WHERE id=?",
                           (now(), item['job_id']))
        if db.execute("SELECT 1 FROM job_items WHERE state='browser_running'").fetchone():
            return {"task": None}
        if (db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_items'").fetchone()
                and db.execute("SELECT 1 FROM image_items WHERE state='browser_running'").fetchone()):
            return {"task": None}
        retry = pending_retry(db)
        if retry and retry['retry_at'] > clock_ms():
            return {'task': None, 'browser_retry': retry}
        # Drain snapshots left by an interrupted older host before more pages.
        if db.execute("SELECT count(*) FROM job_items i JOIN jobs j ON j.id=i.job_id "
                      "WHERE j.state='active' AND i.state IN ('queued','running') "
                      "AND json_extract(i.payload,'$.browser_snapshot') IS NOT NULL").fetchone()[0] >= 3:
            return {"task": None}
        row = db.execute("SELECT i.* FROM job_items i JOIN jobs j ON j.id=i.job_id "
                         "WHERE j.state='active' AND j.content_mode='browser' AND i.state='queued' "
                         "AND i.attempts<3 AND json_extract(i.payload,'$.browser_snapshot') IS NULL "
                         "ORDER BY (json_extract(i.payload,'$.browser_retry_at') IS NULL),i.updated_at,i.note_id LIMIT 1").fetchone()
        if not row:
            return {"task": None}
        payload = json.loads(row["payload"])
        payload.pop('browser_retry_at', None)
        lease = uuid.uuid4().hex
        payload["browser_lease"] = lease
        db.execute("UPDATE job_items SET state='browser_running',attempts=attempts+1,error=NULL,payload=?,updated_at=? "
                   "WHERE job_id=? AND note_id=?", (dumps(payload), now(), row["job_id"], row["note_id"]))
        return {"task": {"job_id": row["job_id"], "note_id": row["note_id"],
                         "source_url": payload["source_url"], "lease_id": lease}}


def check_page(store, request):
    """Recheck a particular lease before the extension opens or reads its page."""
    fields(request, {"job_id", "note_id", "lease_id"}, {"job_id", "note_id", "lease_id"})
    job_id = safe_id(request["job_id"], "job_id")
    note_id = safe_id(request["note_id"], "note_id")
    lease_id = safe_id(request["lease_id"], "lease_id")
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="milliseconds")
    with store.read() as db:
        if access_restriction(db) or invalid_source(db, note_id):
            return {"allowed": False}
        row = db.execute("SELECT i.payload,i.updated_at FROM job_items i JOIN jobs j ON j.id=i.job_id "
                         "WHERE i.job_id=? AND i.note_id=? AND i.state='browser_running' "
                         "AND j.state='active' AND j.content_mode='browser'", (job_id, note_id)).fetchone()
        return {"allowed": bool(row and row["updated_at"] >= cutoff and
                                json.loads(row["payload"]).get("browser_lease") == lease_id)}


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
        # A valid in-flight page can report a real restriction even after another
        # task marks this ID invalid. Record the lease receipt so replaying the
        # same error after an explicit refresh cannot reapply the old restriction.
        if (request.get("error") == "ACCESS_RESTRICTED" and row["state"] in {"browser_running", "skipped"}
                and payload.get("browser_restriction_reported") != lease):
            restrict_access(db)
            payload["browser_restriction_reported"] = lease
            db.execute("UPDATE job_items SET payload=? WHERE job_id=? AND note_id=?", (dumps(payload), job_id, note_id))
            if invalid_source(db, note_id):
                skip_invalid_items(db)
                return {"accepted": True, "restricted": True, "skipped": True}
            db.execute("UPDATE job_items SET state='failed',error='ACCESS_RESTRICTED',updated_at=? "
                       "WHERE job_id=? AND note_id=?", (now(), job_id, note_id))
            return {"accepted": True, "restricted": True}
        if invalid_source(db, note_id):
            skip_invalid_items(db)
            return {"accepted": True, "skipped": True, "duplicate": row["state"] != "browser_running"}
        if row["state"] != "browser_running":
            return {"accepted": True, "duplicate": True}
        if request.get("error") in SKIPPED_SOURCE_ERRORS:
            mark_invalid(db, note_id, request["error"], payload["source_url"], job_id)
            return {"accepted": True, "skipped": True, "duplicate": False}
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
            if code not in RETRYABLE | SKIPPED_SOURCE_ERRORS | {"ACCESS_BLOCKED", "NOTE_NOT_FOUND", "BROWSER_PARSE_FAILED", "BROWSER_PAUSED", "BROWSER_ERROR"}:
                raise XhsError("INVALID_ARGUMENT", "Unknown browser error")
            if code in RETRYABLE and row['attempts'] < 3:
                payload['browser_retry_at'] = clock_ms() + DELAYS[max(0, row['attempts'] - 1)] * 1000
                db.execute("UPDATE job_items SET state='queued',payload=?,error=?,updated_at=? WHERE job_id=? AND note_id=?",
                           (dumps(payload), code, now(), job_id, note_id))
                return {'accepted': True, 'browser_retry': pending_retry(db, job_id)}
            db.execute("UPDATE job_items SET state=?,error=?,attempts=attempts-?,updated_at=? WHERE job_id=? AND note_id=?",
                       ("queued" if code == "BROWSER_PAUSED" else "skipped" if code in SKIPPED_SOURCE_ERRORS else "failed",
                        None if code == "BROWSER_PAUSED" else code,
                        int(code == "BROWSER_PAUSED"), now(), job_id, note_id))
            if code in RETRYABLE | {"ACCESS_BLOCKED", "BROWSER_ERROR"}:
                db.execute("UPDATE jobs SET state='paused',error=?,updated_at=? WHERE id=?", (code, now(), job_id))
    return {"accepted": True, "duplicate": False}
