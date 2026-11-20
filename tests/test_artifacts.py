import io
import json
import pytest
from PIL import Image

from conftest import note
from xhs_context.common import XhsError
from xhs_context.core import read, status
from xhs_context.exports import export_snapshot, backup, restore
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

