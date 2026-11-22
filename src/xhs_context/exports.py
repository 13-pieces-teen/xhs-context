from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path, PurePosixPath

from .common import XhsError, atomic_json, digest, dumps, now
from .storage import Store

KEEP_SNAPSHOTS = 3
STALE_STAGING_SECONDS = 3600


def _snapshot_time(path):
    try:
        manifest = path / "manifest.json"
        return json.loads(manifest.read_text(encoding="utf-8"))["created_at"], manifest.stat().st_mtime_ns
    except (OSError, ValueError, KeyError, TypeError):
        # Incomplete or damaged snapshots sort first, so they are removed first.
        return "", 0


def prune_snapshots(store, keep=KEEP_SNAPSHOTS):
    """Keep the snapshot named by current.json plus the newest others; snapshots are derived data."""
    exports = store.root / "exports"
    current = None
    try:
        current = json.loads((exports / "current.json").read_text(encoding="utf-8")).get("snapshot_id")
    except (OSError, ValueError, AttributeError):
        pass
    snapshots = sorted((p for p in exports.glob("snapshot-*") if p.is_dir()),
                       key=lambda p: (_snapshot_time(p), p.name), reverse=True)
    retained = {current} if current else set()
    for path in snapshots:
        if len(retained) >= keep:
            break
        retained.add(path.name)
    removed = 0
    for path in snapshots:
        if path.name in retained:
            continue
        # A reader may hold a file open on Windows; a remainder is retried next time.
        shutil.rmtree(path, ignore_errors=True)
        removed += not path.exists()
    cutoff = time.time() - STALE_STAGING_SECONDS
    for path in (store.root / "staging").glob("snapshot-*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
    return removed


class ExportScheduler:
    """Refresh the readable snapshot after changes settle, not after every published note."""

    def __init__(self, store, *, quiet=30.0, max_delay=600.0, clock=time.monotonic, export=None):
        self.store, self.quiet, self.max_delay, self.clock = store, quiet, max_delay, clock
        self.export = export or export_snapshot
        self.exported = None
        try:
            pointer = json.loads((store.root / "exports/current.json").read_text(encoding="utf-8"))
            if (store.root / "exports" / pointer["snapshot_id"] / "manifest.json").is_file():
                self.exported = int(pointer["generation"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.seen = self.changed_at = self.pending_since = None

    def idle(self):
        with self.store.read() as db:
            generation = self.store.generation(db)
        moment = self.clock()
        if generation != self.seen:
            self.seen, self.changed_at = generation, moment
        if generation == self.exported:
            self.pending_since = None
            return False
        if self.pending_since is None:
            self.pending_since = moment
        if moment - self.changed_at < self.quiet and moment - self.pending_since < self.max_delay:
            return False
        result = self.export(self.store)
        self.exported = result.get("generation", generation) if isinstance(result, dict) else generation
        self.pending_since = None
        return True


def export_snapshot(store):
    snapshot_id = "snapshot-" + uuid.uuid4().hex[:16]
    staging = store.root / "staging" / snapshot_id
    staging.mkdir()
    (staging / "notes").mkdir()
    manifest = {"snapshot_id": snapshot_id, "created_at": now(), "notes": []}
    with store.read() as db:
        manifest["generation"] = store.generation(db)
        records = db.execute("SELECT n.id,n.current_revision,r.payload FROM notes n "
                             "JOIN revisions r ON r.id=n.current_revision WHERE n.deleted=0 ORDER BY n.id").fetchall()
        for row in records:
            note = json.loads(row["payload"])
            blocks = [dict(b) for b in db.execute(
                "SELECT block_id,kind,text,location FROM evidence_blocks WHERE revision_id=? ORDER BY ordinal",
                (row["current_revision"],))]
            for b in blocks:
                b["location"] = json.loads(b["location"])
            memberships = [dict(m) for m in db.execute(
                "SELECT collection_id,state FROM memberships WHERE note_id=?", (row["id"],))]
            output = dict(note, revision_id=row["current_revision"], evidence_blocks=blocks, memberships=memberships)
            # Export canonical public links; retain access URLs only in the internal archive.
            output["source_url"] = note.get("canonical_url", note.get("source_url", "").split("?")[0])
            for asset in output.get("images", []):
                asset.pop("source_url", None)
            (staging / "notes" / (row["id"] + ".json")).write_text(dumps(output), encoding="utf-8")
            lines = [f"# {note.get('title', '')}", "", f"note_id: {row['id']}",
                     f"revision_id: {row['current_revision']}", f"source_kind: {note.get('source_kind', '')}",
                     f"source: {output['source_url']}", f"gaps: {', '.join(note.get('gaps', []))}", ""]
            for block in blocks:
                lines.extend([f"## {block['block_id']} · {block['kind']}", "", block["text"], "",
                              "location: " + dumps(block["location"]), ""])
            for asset in note.get("images", []):
                if asset.get("object_path"):
                    lines.extend([f"![{asset['asset_id']}](../../../{asset['object_path']})", ""])
            (staging / "notes" / (row["id"] + ".md")).write_text("\n".join(lines), encoding="utf-8")
            manifest["notes"].append({"note_id": row["id"], "revision_id": row["current_revision"],
                                      "path": f"notes/{row['id']}.md"})
    atomic_json(staging / "manifest.json", manifest)
    index = ["# xhs-context 阅读快照", "", f"生成时间：{manifest['created_at']}",
             f"检索代次：{manifest['generation']}", ""]
    index.extend(f"- [{n['note_id']}]({n['path']})" for n in manifest["notes"])
    (staging / "index.md").write_text("\n".join(index), encoding="utf-8")
    final = store.root / "exports" / snapshot_id
    os.replace(staging, final)
    pointer = {"snapshot_id": snapshot_id, "generation": manifest["generation"],
               "created_at": manifest["created_at"], "path": str(final), "index": str(final / "index.md")}
    atomic_json(store.root / "exports/current.json", pointer)
    return dict(pointer, notes=len(manifest["notes"]), pruned_snapshots=prune_snapshots(store))


def backup(store, destination):
    target = Path(destination).resolve()
    if target.exists():
        raise XhsError("DESTINATION_EXISTS", "Backup destination must not already exist", exit_code=2)
    if target.is_relative_to(store.root):
        raise XhsError("INVALID_ARGUMENT", "Keep backups outside the live library", exit_code=2)
    target.mkdir(parents=True)
    source = store.connect(readonly=True)
    copy = sqlite3.connect(target / "library.sqlite")
    try:
        source.backup(copy)
    finally:
        source.close()
        copy.close()
    snapshot = sqlite3.connect(target / "library.sqlite")
    objects = set()
    try:
        for (payload,) in snapshot.execute("SELECT payload FROM revisions"):
            note = json.loads(payload)
            for key in ('raw_object', 'images_raw_object'):
                if (note.get(key) or {}).get('object_path'):
                    objects.add(note[key]['object_path'])
            objects.update(a["object_path"] for a in note.get("images", []) if a.get("object_path"))
        if snapshot.execute("SELECT 1 FROM sqlite_master WHERE name='image_items' AND type='table'").fetchone():
            for (payload,) in snapshot.execute('SELECT payload FROM image_items'):
                objects.update(a['object_path'] for a in json.loads(payload).get('staged_images', []) if a.get('object_path'))
    finally:
        snapshot.close()
    entries = []
    for relative in sorted(objects):
        original = store.object_path(relative)
        if digest(original.read_bytes()) != original.stem:
            raise XhsError("OBJECT_HASH_MISMATCH", "An archived object failed its content-hash check",
                           details={"path": relative})
        destination_file = target / relative
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination_file)
        entries.append({"path": relative, "sha256": digest(destination_file.read_bytes())})
    entries.append({"path": "library.sqlite", "sha256": digest((target / "library.sqlite").read_bytes())})
    atomic_json(target / "backup.json", {"schema_version": 1, "created_at": now(), "files": entries})
    return {"path": str(target), "files": len(entries), "verified": True}


def restore(source, destination):
    source, target = Path(source).resolve(), Path(destination).resolve()
    if target.exists():
        raise XhsError("DESTINATION_EXISTS", "Restore into a new library directory", exit_code=2)
    manifest = json.loads((source / "backup.json").read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    if not files or not any(e.get("path") == "library.sqlite" for e in files):
        raise XhsError("INVALID_BACKUP", "Backup manifest is incomplete", exit_code=2)
    seen = set()
    for entry in files:
        relative = entry["path"]
        if (not isinstance(relative, str) or "\\" in relative or
            PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts or
            relative != PurePosixPath(relative).as_posix() or relative in seen):
            raise XhsError("INVALID_BACKUP", "Backup contains a noncanonical or duplicate path", exit_code=2)
        seen.add(relative)
        origin = (source / relative).resolve()
        if not origin.is_relative_to(source) or (relative != "library.sqlite" and not relative.startswith("objects/")):
            raise XhsError("INVALID_BACKUP", "Backup contains an invalid path", exit_code=2)
        if digest(origin.read_bytes()) != entry["sha256"]:
            raise XhsError("BACKUP_HASH_MISMATCH", "Backup verification failed", details={"path": relative})
    staging = target.with_name(target.name + ".restoring-" + uuid.uuid4().hex[:8])
    staging.mkdir(parents=True)
    for entry in files:
        output = staging / entry["path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / entry["path"], output)
    for name in ["staging", "exports", "notes"]:
        (staging / name).mkdir(exist_ok=True)
    check = Store(staging)
    with check.read() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise XhsError("INVALID_BACKUP", "Restored database failed integrity check")
        for (payload,) in db.execute("SELECT payload FROM revisions"):
            note = json.loads(payload)
            references = [a["object_path"] for a in note.get("images", []) if a.get("object_path")]
            if note.get("raw_object", {}).get("object_path"):
                references.append(note["raw_object"]["object_path"])
            if any(relative not in seen for relative in references):
                raise XhsError("INVALID_BACKUP", "Backup omits an object referenced by the database")
    os.replace(staging, target)
    return {"library": str(target), "files": len(files), "verified": True}

