from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .common import SCHEMA_VERSION, XhsError, digest, dumps, now, safe_id
from .text import TOKENIZER_VERSION, evidence_blocks, indexed, normalize

_WRITE_LOCK = threading.RLock()
DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes(
 id TEXT PRIMARY KEY,source_url TEXT NOT NULL,title TEXT NOT NULL,
 current_revision TEXT,deleted INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(
 id TEXT PRIMARY KEY,note_id TEXT NOT NULL REFERENCES notes(id),
 payload TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_blocks(
 revision_id TEXT NOT NULL REFERENCES revisions(id),block_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL,kind TEXT NOT NULL,text TEXT NOT NULL,location TEXT NOT NULL,
 PRIMARY KEY(revision_id,block_id));
CREATE TABLE IF NOT EXISTS collections(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,source_url TEXT NOT NULL,identity_kind TEXT NOT NULL,
 last_job TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memberships(
 collection_id TEXT NOT NULL REFERENCES collections(id),note_id TEXT NOT NULL REFERENCES notes(id),
 state TEXT NOT NULL DEFAULT 'present',miss_count INTEGER NOT NULL DEFAULT 0,
 last_seen_job TEXT,PRIMARY KEY(collection_id,note_id));
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY,collection_id TEXT NOT NULL REFERENCES collections(id),
 state TEXT NOT NULL DEFAULT 'active',scan_state TEXT NOT NULL DEFAULT 'scanning',
 expected_count INTEGER,received_batches INTEGER NOT NULL DEFAULT 0,
 end_evidence TEXT,applied INTEGER NOT NULL DEFAULT 0,error TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,content_mode TEXT NOT NULL DEFAULT 'anonymous');
CREATE TABLE IF NOT EXISTS batches(
 job_id TEXT NOT NULL REFERENCES jobs(id),seq INTEGER NOT NULL,hash TEXT NOT NULL,
 item_count INTEGER NOT NULL,PRIMARY KEY(job_id,seq));
CREATE TABLE IF NOT EXISTS job_items(
 job_id TEXT NOT NULL REFERENCES jobs(id),note_id TEXT NOT NULL REFERENCES notes(id),
 payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,
 error TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(job_id,note_id));
CREATE TABLE IF NOT EXISTS search_docs(note_id TEXT PRIMARY KEY,normalized TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(
 note_id UNINDEXED,title,body,ocr,tokenize='unicode61 remove_diacritics 2');
CREATE INDEX IF NOT EXISTS item_queue ON job_items(state,updated_at);
CREATE INDEX IF NOT EXISTS revisions_note ON revisions(note_id);
"""


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.db_path = self.root / "library.sqlite"

    def connect(self, readonly=False):
        if not self.db_path.exists():
            raise XhsError("LIBRARY_NOT_INITIALIZED", "Run xhs-context init for this library")
        uri = self.db_path.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
        db = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not version or int(version[0]) != SCHEMA_VERSION:
            db.close()
            raise XhsError("SCHEMA_MISMATCH", "Unsupported database schema")
        return db

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            if self.db_path.exists():
                with self.write() as db:
                    if "content_mode" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
                        db.execute("ALTER TABLE jobs ADD COLUMN content_mode TEXT NOT NULL DEFAULT 'anonymous'")
                with self.read() as db:
                    return {"library": str(self.root), "created": False, "generation": self.generation(db)}
            db = sqlite3.connect(self.db_path)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(DDL)
                db.executemany("INSERT INTO meta VALUES (?,?)",
                               [("schema_version", str(SCHEMA_VERSION)), ("generation", "0"),
                                ("tokenizer_version", TOKENIZER_VERSION)])
                db.commit()
            finally:
                db.close()
        for name in ["objects", "staging", "exports", "notes"]:
            (self.root / name).mkdir(exist_ok=True)
        return {"library": str(self.root), "created": True, "generation": 0}

    @contextmanager
    def read(self):
        db = self.connect(readonly=True)
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.rollback()
            db.close()

    @contextmanager
    def write(self):
        with _WRITE_LOCK:
            db = self.connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def generation(db):
        return int(db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0])

    @staticmethod
    def bump(db):
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")

    def object_path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root / "objects"):
            raise XhsError("INVALID_ASSET_PATH", "Object path escapes the library")
        return path

    def put_object(self, data: bytes, suffix=".bin"):
        sha = digest(data)
        suffix = suffix if suffix in {".json", ".png", ".jpg", ".webp", ".gif", ".bin"} else ".bin"
        relative = f"objects/{sha[:2]}/{sha}{suffix}"
        path = self.object_path(relative)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.root / "staging" / f"{sha}-{threading.get_ident()}.tmp"
            with temp.open("wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        return {"sha256": sha, "object_path": relative, "bytes": len(data)}

    @staticmethod
    def note_row(db, note):
        db.execute("INSERT INTO notes(id,source_url,title,updated_at) VALUES (?,?,?,?) "
                   "ON CONFLICT(id) DO NOTHING",
                   (note["note_id"], note.get("source_url", ""), note.get("title", ""), now()))

    def publish(self, note, *, db=None):
        safe_id(note["note_id"], "note_id")
        if db is None:
            with self.write() as tx:
                return self.publish(note, db=tx)
        self.note_row(db, note)
        # Retrieval identity excludes attempt timestamps and diagnostics.
        payload = {k: v for k, v in note.items() if k not in {"fetched_at", "attempted_at"}}
        identity = {k: v for k, v in payload.items() if k not in {"source_url", "raw_object"}}
        identity["images"] = [{k: v for k, v in asset.items() if k != "source_url"}
                              for asset in payload.get("images", [])]
        revision = digest({"note": identity, "tokenizer": TOKENIZER_VERSION})[:32]
        blocks = evidence_blocks(payload)
        existing = db.execute("SELECT current_revision FROM notes WHERE id=?", (note["note_id"],)).fetchone()
        if existing[0] == revision:
            db.execute("UPDATE notes SET source_url=?,updated_at=? WHERE id=?",
                       (note.get("source_url", ""), now(), note["note_id"]))
            return revision
        db.execute("INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)",
                   (revision, note["note_id"], dumps(payload), now()))
        db.executemany("INSERT OR IGNORE INTO evidence_blocks VALUES (?,?,?,?,?,?)",
                       [(revision, b["block_id"], b["ordinal"], b["kind"], b["text"], dumps(b["location"]))
                        for b in blocks])
        ocr = "\n".join(b["text"] for b in blocks if b["kind"] == "ocr")
        db.execute("DELETE FROM note_fts WHERE note_id=?", (note["note_id"],))
        db.execute("INSERT INTO note_fts VALUES (?,?,?,?)",
                   (note["note_id"], indexed(note.get("title", "")), indexed(note.get("body", "")), indexed(ocr)))
        raw = "\n".join([note.get("title", ""), note.get("body", ""), ocr])
        db.execute("INSERT INTO search_docs VALUES (?,?) ON CONFLICT(note_id) DO UPDATE SET normalized=excluded.normalized",
                   (note["note_id"], normalize(raw)))
        db.execute("UPDATE notes SET source_url=?,title=?,current_revision=?,updated_at=? WHERE id=?",
                   (note.get("source_url", ""), note.get("title", ""), revision, now(), note["note_id"]))
        self.bump(db)
        return revision

    def set_deleted(self, note_id, deleted):
        with self.write() as db:
            row = db.execute("SELECT deleted FROM notes WHERE id=?", (note_id,)).fetchone()
            if not row:
                raise XhsError("NOTE_NOT_FOUND", "Unknown note")
            if row[0] != int(deleted):
                db.execute("UPDATE notes SET deleted=? WHERE id=?", (int(deleted), note_id))
                self.bump(db)
        return {"note_id": note_id, "deleted": deleted}

    def reindex(self):
        with self.write() as db:
            rows = db.execute("SELECT r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision").fetchall()
            for row in rows:
                note = json.loads(row[0])
                blocks = evidence_blocks(note)
                ocr = "\n".join(b["text"] for b in blocks if b["kind"] == "ocr")
                db.execute("DELETE FROM note_fts WHERE note_id=?", (note["note_id"],))
                db.execute("INSERT INTO note_fts VALUES (?,?,?,?)",
                           (note["note_id"], indexed(note.get("title", "")), indexed(note.get("body", "")), indexed(ocr)))
            db.execute("UPDATE meta SET value=? WHERE key='tokenizer_version'", (TOKENIZER_VERSION,))
            self.bump(db)
        return {"indexed_notes": len(rows)}

