"""Local checks shared by pinned reads, image planning and job progress."""
from __future__ import annotations

import hashlib
import stat
from collections import Counter
from functools import lru_cache

from .common import XhsError


@lru_cache(maxsize=4096)
def _file_digest(path, fingerprint):
    # The fingerprint changes on a normal edit or atomic replacement. Frequent
    # progress polls can reuse the digest without caching missing/unreadable files.
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def local_image_file(store, asset):
    """Return the usable path or a per-asset error, including for older archives."""
    relative = asset.get('object_path')
    if not relative:
        return None, 'ASSET_MISSING'
    if not isinstance(relative, str):
        return None, 'INVALID_ASSET_PATH'
    try:
        path = store.object_path(relative)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            return None, 'ASSET_MISSING'
        expected_size = asset.get('bytes')
        if not info.st_size or (expected_size is not None and info.st_size != expected_size):
            return None, 'ASSET_CORRUPT'
        if asset.get('sha256'):
            fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if _file_digest(path, fingerprint) != asset['sha256']:
                return None, 'ASSET_CORRUPT'
        else:
            with path.open('rb') as stream:
                if not stream.read(1):
                    return None, 'ASSET_CORRUPT'
        return path, None
    except XhsError as exc:
        return None, exc.code
    except (FileNotFoundError, NotADirectoryError):
        return None, 'ASSET_MISSING'
    except OSError:
        return None, 'ASSET_UNREADABLE'


def inspect_gallery(store, note):
    assets = note.get('images', [])
    expected = note.get('expected_images')
    files = [local_image_file(store, asset) for asset in assets]
    saved_assets = [asset for asset, (path, _) in zip(assets, files) if path is not None]
    errors = Counter(error for asset, (_, error) in zip(assets, files) if asset.get('object_path') and error)
    saved = len(saved_assets)
    thumbnails = sum(asset.get('quality') == 'thumbnail' for asset in saved_assets)
    unverified = sum(asset.get('state') != 'downloaded' for asset in saved_assets)
    complete = expected is not None and len(assets) == expected and note.get('image_manifest_complete') is not False
    if complete and not expected:
        state = 'none_expected'
    elif complete and saved == expected and not thumbnails and not unverified:
        state = 'available'
    elif note.get('media_state') == 'deferred' and not saved:
        state = 'deferred'
    elif expected is None and not assets:
        state = 'unknown'
    else:
        state = 'partial'
    return {'state': state, 'expected_images': expected, 'listed_images': len(assets),
            'saved_images': saved, 'unsaved_listed_images': len(assets) - saved,
            'missing_expected_images': max(0, expected - saved) if expected is not None else None,
            'missing_local_files': errors['ASSET_MISSING'], 'invalid_object_paths': errors['INVALID_ASSET_PATH'],
            'corrupt_local_files': errors['ASSET_CORRUPT'], 'unreadable_local_files': errors['ASSET_UNREADABLE'],
            'thumbnail_images': thumbnails, 'unverified_local_images': unverified,
            'manifest_complete': complete, 'local_file_check': 'size_and_sha256_when_known'}, files
