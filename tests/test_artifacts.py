import io
import json
import pytest
from PIL import Image

from conftest import note
from xhs_context.common import XhsError
from xhs_context.core import read, status
from xhs_context.exports import ExportScheduler, export_snapshot, backup, prune_snapshots, restore
from xhs_context.processing import store_image, finish_content
from xhs_context.storage import Store


def test_snapshot_has_one_revision_and_explicit_lag(store):
    n = note()
    old_revision = store.publish(n)
    snapshot = export_snapshot(store)
    exported = json.loads((store.root / "exports/current.json").read_text(encoding="utf-8"))
    assert exported["snapshot_id"] == snapshot["snapshot_id"]
    assert not status(store)["data"]["readable_snapshot"]["behind"]
    new_revision = store.publish(dict(n, body="更新后的正文"))
    assert new_revision != old_revision
    assert status(store)["data"]["readable_snapshot"]["behind"]
    manifest = json.loads((store.root / "exports" / snapshot["snapshot_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["notes"] == [{"note_id": n["note_id"], "revision_id": old_revision,
                                  "path": f"notes/{n['note_id']}.md"}]


def test_exports_keep_current_and_recent_snapshots(store):
    store.publish(note())
    created = [export_snapshot(store)["snapshot_id"] for _ in range(5)]
    remaining = sorted(p.name for p in (store.root / "exports").glob("snapshot-*"))
    assert remaining == sorted(created[-3:])
    pointer = json.loads((store.root / "exports/current.json").read_text(encoding="utf-8"))
    assert pointer["snapshot_id"] == created[-1]
    # The snapshot named by current.json survives even if a newer orphan exists.
    orphan = store.root / "exports" / "snapshot-zzzzorphan"
    orphan.mkdir()
    (orphan / "manifest.json").write_text(json.dumps({"created_at": "9999-01-01T00:00:00+00:00"}), encoding="utf-8")
    prune_snapshots(store, keep=1)
    assert (store.root / "exports" / created[-1]).is_dir() and not orphan.exists()


def test_export_scheduler_waits_for_quiet_and_bounds_delay(store):
    clock = [0.0]
    calls = []

    def export(target):
        calls.append(clock[0])
        with target.read() as db:
            return {"generation": target.generation(db)}

    scheduler = ExportScheduler(store, quiet=30, max_delay=100, clock=lambda: clock[0], export=export)
    store.publish(note())
    assert not scheduler.idle()
    clock[0] = 29
    assert not scheduler.idle()
    clock[0] = 31
    assert scheduler.idle() and calls == [31]
    clock[0] = 40
    assert not scheduler.idle()  # Unchanged generation is never re-exported.
    # Continuous changes still refresh the snapshot after max_delay.
    for second in range(41, 160, 10):
        clock[0] = second
        store.publish(note(body=f"正文 {second}"))
        scheduler.idle()
    assert calls == [31, 141]


def test_export_scheduler_reuses_existing_snapshot_after_restart(store):
    store.publish(note())
    export_snapshot(store)
    calls = []
    scheduler = ExportScheduler(store, quiet=0, clock=lambda: 0.0, export=lambda s: calls.append(s))
    assert not scheduler.idle() and calls == []


def test_backup_restore_keeps_media_and_old_citations(store, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 30), "white").save(buffer, format="PNG")
    asset = store_image(store, buffer.getvalue(), {"asset_id": "img-1", "quality": "local_original"})
    n = finish_content(dict(note(), images=[asset], expected_images=1))
    old_revision = store.publish(n)
    store.publish(dict(n, body="后来修改了文字"))
    backup_path = tmp_path / "backup"
    backup(store, backup_path)
    restored_path = tmp_path / "restored"
    restore(backup_path, restored_path)
    restored = Store(restored_path)
    result = read(restored, {"items": [{"note_id": n["note_id"], "revision_id": old_revision,
                                       "asset_ids": ["img-1"]}], "mode": "asset"})
    actual = result["data"]["items"][0]["assets"][0]
    assert actual["width"] == 20 and actual["height"] == 30
    assert str(restored_path) in actual["path"]
    assert restored.object_path(asset["object_path"]).read_bytes() == buffer.getvalue()
    (backup_path / asset["object_path"]).write_bytes(b"corrupt")
    with pytest.raises(XhsError) as caught:
        restore(backup_path, tmp_path / "bad-restore")
    assert caught.value.code == "BACKUP_HASH_MISMATCH"


def test_asset_path_cannot_escape_library(store):
    with pytest.raises(XhsError):
        store.object_path("../private-file")


def test_restore_rejects_parent_traversal_before_writing(store, tmp_path):
    store.publish(note())
    target = tmp_path / "backup-path-test"
    backup(store, target)
    manifest = json.loads((target / "backup.json").read_text(encoding="utf-8"))
    manifest["files"].append({"path": "objects/../../backup-path-test/library.sqlite", "sha256": "unused"})
    (target / "backup.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(XhsError) as caught:
        restore(target, tmp_path / "restore-path-test")
    assert caught.value.code == "INVALID_BACKUP"

