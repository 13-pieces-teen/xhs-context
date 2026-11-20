from __future__ import annotations

import json
import os
from contextlib import contextmanager

from .common import XhsError, now
from .network import AnonymousFetcher
from .processing import LocalOCR, resolve_card, finish_body, body_error, preserve_separate_images
from .access import access_restriction, restrict_access


@contextmanager
def sync_lock(store):
    """An OS lock releases on process exit, including crashes; query processes need no lock."""
    path = store.root / "sync.lock"
    handle = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise XhsError("SYNC_ALREADY_RUNNING", "Another sync worker is using this library") from None
        yield
    finally:
        handle.close()


class Worker:
    def __init__(self, store, *, ocr=True, fetcher=None, stop_event=None, media=False):
        self.store = store
        self.media = media
        self.ocr = LocalOCR() if media and ocr else None
        self.fetcher = fetcher or AnonymousFetcher()
        self.stop_event = stop_event

    def recover(self):
        with self.store.write() as db:
            db.execute("UPDATE job_items SET state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,"
                       "error='INTERRUPTED',updated_at=? WHERE state='running'", (now(),))
            # The extension may still own a page or an unacknowledged result after
            # a host restart. Keep its lease; next_page expires abandoned leases.
        if not self.media:
            self.adopt_body_scope()

    def adopt_body_scope(self):
        """Reuse verified bodies from old media-only failures, keeping history intact."""
        with self.store.write() as db:
            rows = db.execute("SELECT i.job_id,i.note_id,i.error,r.payload FROM job_items i "
                              "JOIN notes n ON n.id=i.note_id JOIN revisions r ON r.id=n.current_revision "
                              "WHERE i.state='partial' AND json_extract(r.payload,'$.body_state')='available'").fetchall()
            for row in rows:
                gaps = (row["error"] or "").split(",")
                if not all(gap.startswith(("IMAGE_", "OCR_", "THUMBNAIL_", "VIDEO_", "MEDIA_")) for gap in gaps):
                    continue
                prior = json.loads(row['payload'])
                note = preserve_separate_images(finish_body(prior), prior)
                self.store.publish(note, db=db)
                db.execute("UPDATE job_items SET state=?,error=?,updated_at=? WHERE job_id=? AND note_id=?",
                           ("ready" if note["body_state"] == "available" else "partial", body_error(note),
                            now(), row["job_id"], row["note_id"]))

    def stopped(self, job_id):
        if self.stop_event and self.stop_event.is_set():
            return True
        with self.store.read() as db:
            return bool(access_restriction(db)) or db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0] != "active"

    def once(self, job_id=None):
        with self.store.write() as db:
            if access_restriction(db):
                return False
            clause = " AND i.job_id=?" if job_id else ""
            row = db.execute("SELECT i.* FROM job_items i JOIN jobs j ON j.id=i.job_id "
                             "WHERE i.state='queued' AND i.attempts<3 AND j.state='active' "
                             "AND (j.content_mode='anonymous' OR json_extract(i.payload,'$.browser_snapshot') IS NOT NULL)" + clause +
                             " ORDER BY i.updated_at,i.note_id LIMIT 1", [job_id] if job_id else []).fetchone()
            if not row:
                return False
            db.execute("UPDATE job_items SET state='running',attempts=attempts+1,updated_at=? "
                       "WHERE job_id=? AND note_id=?", (now(), row["job_id"], row["note_id"]))
        job_id, note_id = row["job_id"], row["note_id"]
        try:
            note = resolve_card(self.store, json.loads(row["payload"]), self.fetcher, self.ocr,
                                lambda: self.stopped(job_id), media=self.media)
            if self.stopped(job_id):
                raise XhsError("SYNC_PAUSED", "Sync paused before publishing")
            with self.store.write() as db:
                current = db.execute("SELECT r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=?",
                                     (note_id,)).fetchone()
                prior = json.loads(current[0]) if current else {}
                if note["body_state"] != "available" and prior.get("body_state") == "available":
                    raise XhsError("BODY_MISSING", "The new fetch is incomplete; previous version retained")
                usable_prior = prior.get("body_state") == "available" or any(
                    asset.get("state") == "downloaded" for asset in prior.get("images", []))
                new_gaps = sorted(set(note.get("gaps", [])) - set(prior.get("gaps", [])))
                acquisition_gaps = [gap for gap in new_gaps if gap.startswith(
                    ("BODY_", "IMAGE_", "THUMBNAIL_", "OCR_")) and not (
                        gap.startswith("OCR_") and any(old.startswith("OCR_") and
                            old.partition(":")[2] == gap.partition(":")[2] for old in prior.get("gaps", [])))]
                if usable_prior and acquisition_gaps:
                    # Do not combine a new body with stale images, or replace indexed OCR
                    # with a failed attempt. Retry the whole revision and retain evidence.
                    raise XhsError("INCOMPLETE_REFRESH", "Previous version retained after an incomplete refresh",
                                   details={"gaps": acquisition_gaps})
                if not self.media:
                    note = preserve_separate_images(note, prior)
                self.store.publish(note, db=db)
                ready = (note["processing_state"] == "ready" and note["content_state"] == "complete") if self.media else note["body_state"] == "available"
                db.execute("UPDATE job_items SET state=?,error=?,updated_at=? WHERE job_id=? AND note_id=?",
                           ("ready" if ready else "partial", None if ready else
                            (",".join(note["gaps"])[:1000] if self.media else body_error(note)),
                            now(), job_id, note_id))
        except Exception as exc:
            code = exc.code if isinstance(exc, XhsError) else type(exc).__name__
            failure = code
            if isinstance(exc, XhsError) and exc.code == "INCOMPLETE_REFRESH":
                failure += ":" + ",".join(exc.details["gaps"])
            with self.store.write() as db:
                paused = code == "SYNC_PAUSED"
                db.execute("UPDATE job_items SET state=?,error=?,attempts=attempts-?,updated_at=? WHERE job_id=? AND note_id=?",
                           ("queued" if paused else "failed", failure[:1000], int(paused), now(), job_id, note_id))
                if code in {"ACCESS_RESTRICTED", "RATE_LIMITED"}:
                    restrict_access(db, error=code, source="anonymous")
                elif code in {"ACCESS_BLOCKED", "LOCAL_NETWORK_BLOCKED", "PROXY_ERROR", "PROXY_CONFIGURATION", "NETWORK_ERROR"}:
                    db.execute("UPDATE jobs SET state='paused',error=?,updated_at=? WHERE id=?", (code, now(), job_id))
        return True

