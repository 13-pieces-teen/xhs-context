"""Persistent acquisition stop; reading the local archive remains available."""
from __future__ import annotations

import json

from .common import XhsError, dumps, fields, now


def access_restriction(db):
    row = db.execute("SELECT value FROM meta WHERE key='access_restriction'").fetchone()
    return json.loads(row[0]) if row else None


def require_access(db):
    restriction = access_restriction(db)
    if restriction:
        raise XhsError("ACCESS_RESTRICTED", "Automatic reading is locked after a platform restriction",
                       details=restriction, exit_code=5)


def restrict_access(db, *, error="ACCESS_RESTRICTED", source="browser"):
    restriction = access_restriction(db) or {
        "error": error, "site_code": "429" if error == "RATE_LIMITED" else "300013",
        "source": source, "observed_at": now(),
    }
    db.execute("INSERT INTO meta VALUES ('access_restriction',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (dumps(restriction),))
    db.execute("UPDATE jobs SET state='paused',error=?,updated_at=? WHERE state IN ('active','paused')",
               (restriction["error"], now()))
    return restriction


def unlock_access(store, *, confirmed=False):
    if confirmed is not True:
        raise XhsError("CONFIRM_ACCESS_RESTORED", "Manually verify normal access, then use --confirm-access-restored",
                       exit_code=2)
    with store.write() as db:
        previous = access_restriction(db)
        if previous is None:
            return {"unlocked": False, "jobs_resumed": False}
        # Unlocking only changes the local guard; it neither probes XHS nor resumes jobs.
        db.execute("UPDATE jobs SET state='paused',updated_at=? WHERE state='active'", (now(),))
        db.execute("UPDATE jobs SET error=NULL WHERE error IN ('ACCESS_RESTRICTED','RATE_LIMITED')")
        db.execute("DELETE FROM meta WHERE key='access_restriction'")
    return {"unlocked": previous is not None, "jobs_resumed": False}


def report_restriction(store, request):
    fields(request, {"error"}, {"error"})
    if request["error"] != "ACCESS_RESTRICTED":
        raise XhsError("INVALID_ARGUMENT", "Only an observed security restriction can be reported", exit_code=2)
    with store.write() as db:
        return {"access_restriction": restrict_access(db)}
