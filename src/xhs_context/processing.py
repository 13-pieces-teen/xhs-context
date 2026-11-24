from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

from .common import XhsError, digest
from .parser import parse_note

OCR_VERSION = "rapidocr-3.10.0-ppocrv6-cpu-tiles-v1"
MODEL_FILES = {"Det": "PP-OCRv6_det_small.onnx",
               "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
               "Rec": "PP-OCRv6_rec_small.onnx"}


def ocr_models():
    spec = importlib.util.find_spec("rapidocr")
    if not spec or not spec.origin:
        return {}
    root = Path(spec.origin).parent / "models"
    return {key: root / name for key, name in MODEL_FILES.items()}


def ocr_ready():
    models = ocr_models()
    return bool(models) and all(path.is_file() for path in models.values())


class LocalOCR:
    def __init__(self):
        self.engine = None

    def recognize(self, path):
        if not ocr_ready():
            raise XhsError("OCR_NOT_READY", "Install the OCR extra and prepare its local models", exit_code=5)
        from PIL import Image
        import numpy as np
        if self.engine is None:
            from rapidocr import RapidOCR
            params = {f"{key}.model_path": str(path) for key, path in ocr_models().items()}
            params.update({"Global.log_level": "error", "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                           "EngineConfig.onnxruntime.inter_op_num_threads": 1})
            with redirect_stdout(sys.stderr):
                self.engine = RapidOCR(params=params)
        lines = []
        with Image.open(path) as original:
            image = original.convert("RGB")
            width, height = image.size
            step, tile_height, overlap = 1660, 1800, 140
            starts = [0] if height <= tile_height else list(range(0, height, step))
            for top in starts:
                bottom = min(height, top + tile_height)
                # RapidOCR ndarray input uses OpenCV's BGR channel order.
                crop = np.array(image.crop((0, top, width, bottom)))[:, :, ::-1]
                with redirect_stdout(sys.stderr):
                    result = self.engine(crop)
                if not result.txts or result.boxes is None:
                    continue
                for text, score, box in zip(result.txts, result.scores, result.boxes):
                    coords = [[round(float(x), 2), round(float(y) + top, 2)] for x, y in box]
                    center_y = sum(point[1] for point in coords) / len(coords)
                    if top and center_y < top + overlap / 2:
                        continue
                    if bottom < height and center_y >= bottom - overlap / 2:
                        continue
                    lines.append({"text": text, "confidence": round(float(score), 4), "box": coords})
                if bottom == height:
                    break
        return lines


def store_image(store, data, asset, ocr=None):
    from PIL import Image, UnidentifiedImageError
    if len(data) > 25 * 1024 * 1024:
        raise XhsError("MEDIA_TOO_LARGE", "Image exceeds 25 MiB", exit_code=5)
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.width * picture.height > 40_000_000:
                raise XhsError("MEDIA_TOO_LARGE", "Image exceeds 40 million pixels", exit_code=5)
            width, height, format_name = picture.width, picture.height, picture.format
            picture.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise XhsError("INVALID_IMAGE", "Downloaded asset is not a supported image", exit_code=5) from None
    suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(format_name, ".bin")
    result = dict(asset, **store.put_object(data, suffix), width=width, height=height,
                  mime_type=Image.MIME.get(format_name, "application/octet-stream"), state="downloaded")
    result["ocr_state"] = "not_requested"
    if ocr:
        try:
            result["ocr_lines"] = ocr.recognize(store.object_path(result["object_path"]))
            result["ocr_state"] = "ready" if result["ocr_lines"] else "empty"
            result["processing_version"] = OCR_VERSION
        except Exception as exc:
            result["ocr_state"] = "failed"
            result["ocr_error"] = exc.code if isinstance(exc, XhsError) else type(exc).__name__
    return result


def finish_content(note):
    gaps = []
    if note.get("body_state") != "available":
        gaps.append("BODY_UNVERIFIED" if note.get("body_state") == "unverified" else "BODY_MISSING")
    if note.get("expected_images") is None:
        gaps.append("IMAGE_COUNT_UNKNOWN")
    images = note.get("images", [])
    if note.get("expected_images") is not None and note["expected_images"] != len(images):
        gaps.append("IMAGE_LIST_INCOMPLETE")
    for asset in images:
        if asset.get("state") != "downloaded":
            gaps.append(f"IMAGE_MISSING:{asset['asset_id']}")
        if asset.get("quality") == "thumbnail":
            gaps.append(f"THUMBNAIL_ONLY:{asset['asset_id']}")
        if asset.get("ocr_state") not in {"ready"}:
            gaps.append(f"OCR_{asset.get('ocr_state', 'pending').upper()}:{asset['asset_id']}")
    if note.get("note_type") == "video":
        gaps.append("VIDEO_NOT_TRANSCRIBED")
    source_gaps = [gap for gap in gaps if not gap.startswith("OCR_")]
    note["content_state"] = "complete" if not source_gaps else ("metadata_only" if note.get("body_state") != "available" and not images else "partial")
    note["processing_state"] = "ready" if not gaps else "partial"
    note["gaps"] = gaps
    return note


def finish_body(note):
    """Complete the current prototype's text scope without claiming media coverage."""
    note = dict(note, content_scope="body", media_state="deferred")
    if note.get("body_state") == "available" and not note.get("body", "").strip():
        note["body_state"] = "empty"
    body_ready = note.get("body_state") == "available"
    note["content_state"] = "body_only" if body_ready else "metadata_only"
    note["processing_state"] = "ready" if body_ready else "partial"
    note["gaps"] = ([] if body_ready else ["BODY_" + (
        "EMPTY" if note.get("body_state") == "empty" else
        "UNVERIFIED" if note.get("body_state") == "unverified" else "MISSING")]) + ["MEDIA_DEFERRED"]
    # Keep any already archived assets. Failed placeholders are intentionally
    # deferred, not failed work in the text prototype. No media request occurs.
    note["images"] = [dict(asset) if asset.get("state") == "downloaded" else
                      {**{k: v for k, v in asset.items() if k not in {"error", "ocr_error"}},
                       "state": "deferred", "ocr_state": "not_requested"}
                      for asset in note.get("images", [])]
    return note


def body_error(note):
    return ",".join(gap for gap in note.get("gaps", []) if gap.startswith("BODY_")) or None


def preserve_separate_images(note, prior):
    """A body refresh cannot discard independently acquired images."""
    if prior.get('content_scope') not in {'images', 'body_and_images'}:
        return note
    merged = dict(note)
    for key in ('images', 'expected_images', 'media_state', 'image_manifest_complete', 'images_source', 'images_raw_object', 'images_body_revision'):
        if key in prior:
            merged[key] = prior[key]
    if merged.get('note_type') in {None, 'unknown'}:
        merged['note_type'] = prior.get('note_type', 'unknown')
    merged = finish_content(merged)
    if prior.get('image_manifest_complete') is False and 'IMAGE_LIST_INCOMPLETE' not in merged['gaps']:
        merged['gaps'].append('IMAGE_LIST_UNVERIFIED')
        merged['content_state'] = 'partial'
    merged['content_scope'] = 'body_and_images' if merged.get('body_state') == 'available' else 'images'
    if merged.get('body') != prior.get('body') or 'IMAGES_CAPTURED_SEPARATELY' in prior.get('gaps', []):
        merged['gaps'].append('IMAGES_CAPTURED_SEPARATELY')
        merged['content_state'] = 'partial'
    return merged


def resolve_card(store, card, fetcher, ocr=None, stop=None, *, media=False):
    if card.get("browser_snapshot"):
        note = dict(card["browser_snapshot"])
    else:
        raw, mime, final_url = fetcher.get(card["source_url"])
        from .network import validate_note_response
        validate_note_response(final_url, card["note_id"])
        note = parse_note(raw.decode("utf-8", errors="replace"), card["note_id"], final_url)
        note["raw_object"] = store.put_object(raw, ".bin")
    if not media:
        return finish_body(note)
    previous = {}
    with store.read() as db:
        row = db.execute("SELECT r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=?",
                         (card["note_id"],)).fetchone()
        if row:
            old = json.loads(row[0])
            previous = {a.get("sha256"): a for a in old.get("images", []) if a.get("sha256")}
    downloaded = []
    for asset in note["images"]:
        if stop and stop():
            raise XhsError("SYNC_PAUSED", "Task paused before all media were processed", exit_code=5)
        try:
            data, _, _ = fetcher.get(asset["source_url"], media=True)
            cached = previous.get(digest(data))
            if cached and cached.get("processing_version") == OCR_VERSION and cached.get("ocr_state") == "ready" and ocr:
                result = store_image(store, data, asset)
                result.update({key: cached[key] for key in ("ocr_lines", "ocr_state", "processing_version")})
            else:
                result = store_image(store, data, asset, ocr)
            downloaded.append(result)
        except XhsError as exc:
            if exc.code in {"ACCESS_RESTRICTED", "RATE_LIMITED", "ACCESS_BLOCKED", "PROXY_ERROR", "PROXY_CONFIGURATION", "LOCAL_NETWORK_BLOCKED"}:
                raise
            downloaded.append(dict(asset, state="failed", error=exc.code, ocr_state="not_ready"))
    note["images"] = downloaded
    return finish_content(note)

