"""Permanent, library-wide records of explicitly unavailable note sources."""
from __future__ import annotations

from .common import XhsError, integer, now, safe_id

INVALID_SOURCE_ERRORS = frozenset({"SOURCE_UNAVAILABLE", "SOURCE_LINK_EXPIRED"})
INVALID_DDL = """CREATE TABLE IF NOT EXISTS invalid_notes(
 note_id TEXT PRIMARY KEY REFERENCES notes(id),error TEXT NOT NULL,
 source_url TEXT NOT NULL,observed_at TEXT NOT NULL,job_id TEXT)"""
def _available(db, table='invalid_notes'):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _first_sources(source):
    # XHS note IDs are hexadecimal and case-insensitive. Preserve the original
    # archived key rather than rewriting notes, references, or historical URLs.
    return ("SELECT note_id,error,source_url,observed_at,job_id FROM (SELECT *,"
            "ROW_NUMBER() OVER (PARTITION BY lower(note_id) ORDER BY observed_at,job_id,note_id) AS occurrence "
            "FROM (" + source + ")) WHERE occurrence=1")


def _legacy(db, job_id=None):
    queries, params = [], []
    for table in ('job_items', 'image_items'):
        if not _available(db, table):
            continue
        # In image_items, a pure SOURCE_* failure came from fetching the note
        # page. CDN failures are stored per asset and yield partial IMAGE_* gaps.
        query = ("SELECT i.note_id,i.error,COALESCE(json_extract(i.payload,'$.source_url'),n.source_url) AS source_url,"
                 "i.updated_at AS observed_at,i.job_id FROM " + table + " i JOIN notes n ON n.id=i.note_id "
                 "WHERE i.state IN ('failed','skipped') AND i.error IN ('SOURCE_UNAVAILABLE','SOURCE_LINK_EXPIRED')")
        if job_id:
            query += " AND i.job_id=?"
            params.append(job_id)
        queries.append(query)
    return _first_sources(" UNION ALL ".join(queries)), tuple(params)


def _backfill(db, job_id=None):
    legacy, params = _legacy(db, job_id)
    db.execute("INSERT OR IGNORE INTO invalid_notes(note_id,error,source_url,observed_at,job_id) "
               "SELECT note_id,error,source_url,observed_at,job_id FROM (" + legacy + ") candidate "
               "WHERE NOT EXISTS (SELECT 1 FROM invalid_notes known WHERE lower(known.note_id)=lower(candidate.note_id))", params)


def _ensure(db):
    if not _available(db):
        db.execute(INVALID_DDL)
        _backfill(db)
    db.execute("CREATE INDEX IF NOT EXISTS invalid_source_key ON invalid_notes(lower(note_id),observed_at,note_id)")
    for table in ('job_items', 'image_items'):
        if _available(db, table):
            db.execute(f"CREATE INDEX IF NOT EXISTS {table}_source_key ON {table}(lower(note_id),state)")


def initialize_invalid_sources(db):
    """Upgrade old acquisition history; never alter saved notes or revisions."""
    _ensure(db)
    _backfill(db)
    skip_invalid_items(db)


def backfill_invalid_items(db, job_id=None):
    _ensure(db)
    _backfill(db, job_id)
    skip_invalid_items(db)


def invalid_source(db, note_id):
    if _available(db):
        row = db.execute("SELECT * FROM invalid_notes WHERE lower(note_id)=lower(?) "
                         "ORDER BY observed_at,job_id,note_id LIMIT 1", (note_id,)).fetchone()
    else:
        # A read-only status/query on an older library must not perform a migration.
        legacy, params = _legacy(db)
        row = db.execute("SELECT * FROM (" + legacy + ") WHERE lower(note_id)=lower(?)", (*params, note_id)).fetchone()
    return dict(row) if row else None


def mark_invalid(db, note_id, error, source_url, job_id=None):
    if error not in INVALID_SOURCE_ERRORS:
        raise XhsError("INVALID_ARGUMENT", "Only an explicit note-source failure may be retained", exit_code=2)
    safe_id(note_id, "note_id")
    _ensure(db)
    if invalid_source(db, note_id) is None:
        db.execute("INSERT INTO invalid_notes VALUES (?,?,?,?,?)", (note_id, error, source_url, now(), job_id))
    skip_invalid_items(db)
    return invalid_source(db, note_id)


def skip_invalid_items(db):
    """Suppress queued and in-flight work for known IDs across all collections."""
    _ensure(db)
    for table, states in (("job_items", "'queued','running','browser_running','failed','partial'"),
                          ("image_items", "'queued','running','browser_running','failed','partial'")):
        if not _available(db, table):
            continue
        db.execute(f"UPDATE {table} SET state='skipped',error=(SELECT error FROM invalid_notes "
                   f"WHERE lower(note_id)=lower({table}.note_id) ORDER BY observed_at,job_id,note_id LIMIT 1),"
                   f"updated_at=? WHERE lower(note_id) IN (SELECT lower(note_id) FROM invalid_notes) AND state IN ({states})", (now(),))


def invalid_report(db, limit=50, offset=0):
    integer(limit, "limit", 1, 100)
    integer(offset, "offset", 0, 100000)
    source = "(" + (_first_sources("SELECT * FROM invalid_notes") if _available(db) else _legacy(db)[0]) + ")"
    total = db.execute("SELECT count(*) FROM " + source).fetchone()[0]
    rows = db.execute("SELECT i.note_id,COALESCE(n.title,'') AS title,i.error,i.source_url,i.observed_at,i.job_id "
                      "FROM " + source + " i LEFT JOIN notes n ON n.id=i.note_id "
                      "ORDER BY i.observed_at DESC,i.note_id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return {"total": total, "items": [dict(row) for row in rows], "has_more": offset + len(rows) < total}
