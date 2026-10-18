#!/usr/bin/env python3
"""Comic Translator review UI.

This is a local-only web app for reviewing TG anchors and Step 3 regions before
Gemini OCR. It intentionally lives beside the Gradio wrapper instead of
replacing it.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import filecmp
import hashlib
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from pipeline.manual_guidance import build_effective_text_groups, load_json, write_json
from pipeline.openai_image_redraw import redraw_image_with_openai
from pipeline.pipeline_runner import DEFAULT_FONT, run_single_image, stream_subprocess
from pipeline.render_text_overlay import apply_text_overlay

load_dotenv()

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui_v2"
SESSION_ROOT = ROOT / "temp_output" / "v2_sessions"
INPUT_CACHE_DIR = ROOT / "temp_output" / "v2_input_cache"
CV_CACHE_DIR = ROOT / "temp_output" / "v2_cv_cache"
ANALYSIS_CACHE_DIR = ROOT / "temp_output" / "v2_analysis_cache"
ANALYSIS_CACHE_VERSION = "2026-05-29-region-suppress-v1"
ANALYSIS_CACHE_COMPATIBLE_VERSIONS = {
    ANALYSIS_CACHE_VERSION,
    "2026-05-29-seed-bright-component-v1",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
PYTHON = sys.executable
DEFAULT_ANALYZE_WORKERS = 4
LOAD_CACHE_SYNC_LIMIT = 10
IMPORT_CACHE_SYNC_LIMIT = 0
OCR_SKIP_REASON_ANALYSIS_NOT_RUN = "analysis_not_run"
OCR_SKIP_REASON_NO_REGIONS = "no_ocr_regions"
OCR_SKIP_REASON_MISSING_SHEET = "missing_ocr_sheet"
_LOAD_HYDRATE_LOCK = threading.Lock()
_LOAD_HYDRATE_RUNNING: set[tuple[str, str]] = set()
ANALYSIS_CACHE_ARTIFACTS = {
    "cv_json": "cv.json",
    "text_groups_json": "text_groups.json",
    "regions_json": "regions.json",
    "indexed_sheet_jpg": "indexed_sheet.jpg",
    "masked_jpg": "masked.jpg",
    "bubble_vis_jpg": "bubble_vis.jpg",
}
ANALYSIS_CACHE_LINK_ARTIFACTS = {
    "indexed_sheet_jpg",
    "masked_jpg",
    "bubble_vis_jpg",
}
ANALYSIS_CACHE_REQUIRED = (
    "text_groups_json",
    "regions_json",
    "indexed_sheet_jpg",
    "masked_jpg",
    "bubble_vis_jpg",
)
GUIDANCE_OUTPUT_ARTIFACTS = {
    "effective_text_groups_json": "text_groups_effective.json",
    "guidance_report_json": "manual_guidance_report.json",
    "regions_json": "regions.json",
    "indexed_sheet_jpg": "indexed_sheet.jpg",
    "masked_jpg": "masked.jpg",
    "bubble_vis_jpg": "bubble_vis.jpg",
}
GUIDANCE_OUTPUT_REQUIRED = (
    "effective_text_groups_json",
    "guidance_report_json",
    "regions_json",
    "indexed_sheet_jpg",
    "masked_jpg",
    "bubble_vis_jpg",
)


class ImportPathRequest(BaseModel):
    path: str
    session_id: str | None = None
    recursive: bool = True


class SessionMetaRequest(BaseModel):
    session_id: str
    project_name: str = ""


class SessionDeleteRequest(BaseModel):
    session_id: str


class AnalyzeRequest(BaseModel):
    session_id: str
    image_id: str = "all"
    force: bool = False
    max_workers: int = DEFAULT_ANALYZE_WORKERS


class GuidanceRequest(BaseModel):
    session_id: str
    image_id: str
    guidance: dict[str, Any]


class ClearGuidanceRequest(BaseModel):
    session_id: str
    image_id: str


class RemoveImageRequest(BaseModel):
    session_id: str
    image_id: str


class RunFinalRequest(BaseModel):
    session_id: str
    image_id: str
    extra_prompt: str = ""
    font_path: str | None = None
    tracking_offset: int | None = None
    overlay_guidance: dict[str, Any] | None = None
    max_workers: int = 1
    force: bool = False
    ocr_model: str | None = None


class OverlayRequest(BaseModel):
    session_id: str
    image_id: str
    overlay_guidance: dict[str, Any]
    font_path: str | None = None
    tracking_offset: int | None = None


class GenerateMissingFinalRequest(BaseModel):
    session_id: str
    image_ids: list[str] = []
    model: str = "gpt-image-2"
    prompt_guide: str = ""
    analyze_first: bool = True
    text_model: str = "gpt-5.4-mini"
    quality: str = "medium"
    size: str = "match_input"
    output_format: str = "jpeg"
    output_compression: int | None = 92
    max_workers: int = DEFAULT_ANALYZE_WORKERS
    force: bool = False


def now_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def session_dir(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in {"_", "-"})
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid session id")
    return SESSION_ROOT / safe


def session_file(session_id: str) -> Path:
    return session_dir(session_id) / "session.json"


def ensure_session(session_id: str | None = None) -> dict[str, Any]:
    sid = session_id or now_id()
    base = session_dir(sid)
    base.mkdir(parents=True, exist_ok=True)
    for child in ("input", "work", "output", "output/final"):
        (base / child).mkdir(parents=True, exist_ok=True)
    path = session_file(sid)
    if path.exists():
        data = load_json(path, default={})
    else:
        data = {"session_id": sid, "created_at": dt.datetime.now().isoformat(timespec="seconds"), "images": []}
        write_json(path, data)
    return data


def save_session(data: dict[str, Any]) -> None:
    data["images"] = sorted_image_records(data.get("images", []))
    write_json(session_file(data["session_id"]), data)


def delete_session(session_id: str) -> None:
    base = session_dir(session_id)
    if base.exists():
        shutil.rmtree(base)


def list_recent_sessions(limit: int = 10) -> list[dict[str, Any]]:
    raw_sessions: list[dict[str, Any]] = []
    for session_json in SESSION_ROOT.glob("*/session.json"):
        data = load_json(session_json, default={})
        images = sorted_image_records(data.get("images", []))
        if not images:
            continue
        mtime = session_json.stat().st_mtime
        image_hashes = [item.get("image_sha256") for item in images if item.get("image_sha256")]
        image_hash_set = set(image_hashes)
        first_input = Path(images[0].get("input_path", ""))
        raw_sessions.append({
            "session_id": data.get("session_id") or session_json.parent.name,
            "project_name": data.get("project_name", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            "image_count": len(images),
            "first_image": images[0].get("name", ""),
            "thumbnail_url": file_url(first_input),
            "_updated_ts": mtime,
            "_image_hashes": image_hash_set,
        })
    raw_sessions.sort(key=lambda item: item.get("_updated_ts", 0), reverse=True)

    grouped: list[dict[str, Any]] = []
    for session in raw_sessions:
        hashes = session.get("_image_hashes") or set()
        if not hashes:
            grouped.append(session)
            continue
        should_add = True
        for existing in list(grouped):
            existing_hashes = existing.get("_image_hashes") or set()
            if not existing_hashes:
                continue
            if hashes == existing_hashes or hashes < existing_hashes:
                should_add = False
                break
            if hashes > existing_hashes:
                grouped.remove(existing)
        if should_add:
            grouped.append(session)

    grouped.sort(key=lambda item: item.get("_updated_ts", 0), reverse=True)
    sessions: list[dict[str, Any]] = []
    for item in grouped[:limit]:
        item = dict(item)
        item.pop("_updated_ts", None)
        item.pop("_image_hashes", None)
        sessions.append(item)
    return sessions


def natural_name_key(value: str) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", Path(value).name.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def sorted_image_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: natural_name_key(item.get("name") or item.get("input_path") or item.get("id") or ""))


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def estimate_horizontal_margins(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            gray = image.convert("L")
            gray.thumbnail((512, 512))
            threshold = gray.point(lambda pixel: 255 if pixel < 245 else 0)
            bbox = threshold.getbbox()
            if not bbox:
                return {"left_margin_ratio": 0.0, "right_margin_ratio": 0.0, "spread_layout": "blank"}
            width = gray.width
            left_ratio = bbox[0] / max(1, width)
            right_ratio = (width - bbox[2]) / max(1, width)
            if left_ratio >= 0.18 and right_ratio >= 0.18:
                layout = "center_content"
            elif left_ratio >= 0.38 or right_ratio >= 0.38:
                layout = "half_blank"
            elif left_ratio <= 0.08 and right_ratio <= 0.08:
                layout = "double_page"
            else:
                layout = "wide_page"
            return {
                "left_margin_ratio": round(left_ratio, 4),
                "right_margin_ratio": round(right_ratio, 4),
                "spread_layout": layout,
            }
    except Exception:
        return {"left_margin_ratio": None, "right_margin_ratio": None, "spread_layout": "unknown"}


def spread_candidate_from_dimensions(width: int | None, height: int | None, path: Path | None = None) -> dict[str, Any]:
    if not width or not height:
        return {"wide_spread_candidate": False}
    ratio = width / max(1, height)
    margins = estimate_horizontal_margins(path) if path else {}
    return {
        "width": width,
        "height": height,
        "aspect_ratio": round(ratio, 4),
        "wide_spread_candidate": ratio >= 1.25,
        **margins,
    }


def spread_candidate_from_manifest(item: dict[str, Any]) -> dict[str, Any] | None:
    width = item.get("width")
    height = item.get("height")
    if not width or not height:
        return None
    ratio = width / max(1, height)
    return {
        "width": width,
        "height": height,
        "aspect_ratio": item.get("aspect_ratio", round(ratio, 4)),
        "wide_spread_candidate": item.get("wide_spread_candidate", ratio >= 1.25),
        **{
            key: item[key]
            for key in ("left_margin_ratio", "right_margin_ratio", "spread_layout")
            if key in item
        },
    }


def image_manifest_item(path: Path, image_hash: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    width, height = image_dimensions(path)
    item = {
        "id": path.stem,
        "name": path.name,
        "input_path": str(path),
        "image_sha256": image_hash,
        **spread_candidate_from_dimensions(width, height, path),
    }
    if extra:
        item.update(extra)
    return item


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_record_hash(item: dict[str, Any]) -> str:
    image_hash = item.get("image_sha256")
    if image_hash:
        return image_hash
    image_hash = file_sha256(Path(item["input_path"]))
    item["image_sha256"] = image_hash
    return image_hash


def input_cache_path(image_hash: str, suffix: str) -> Path:
    clean_suffix = suffix.lower() if suffix else ".img"
    return INPUT_CACHE_DIR / image_hash[:2] / f"{image_hash}{clean_suffix}"


def cache_input_file(src: Path, image_hash: str | None = None) -> tuple[str, Path]:
    image_hash = image_hash or file_sha256(src)
    cache_path = input_cache_path(image_hash, src.suffix)
    if not cache_path.exists():
        _copy_file(src, cache_path)
    return image_hash, cache_path


def link_cached_input(src: Path, dest: Path, image_hash: str | None = None) -> tuple[str, Path]:
    image_hash, cache_path = cache_input_file(src, image_hash)
    _link_or_copy_file(cache_path, dest)
    return image_hash, cache_path


def cv_cache_path(image_hash: str) -> Path:
    return CV_CACHE_DIR / image_hash[:2] / f"{image_hash}_cv.json"


def analysis_cache_dir(image_hash: str) -> Path:
    return ANALYSIS_CACHE_DIR / image_hash[:2] / image_hash


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists() and src.samefile(dest):
            return
    except OSError:
        pass
    tmp = dest.with_name(f".{dest.name}.{threading.get_ident()}.tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        shutil.copy2(src, tmp)
        tmp.replace(dest)
    finally:
        if tmp.exists() or tmp.is_symlink():
            try:
                tmp.unlink()
            except OSError:
                pass


def _link_or_copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists() and src.samefile(dest):
            return
    except OSError:
        pass
    try:
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.symlink_to(src.resolve())
    except OSError:
        try:
            if dest.exists() and src.samefile(dest):
                return
        except OSError:
            pass
        _copy_file(src, dest)


def _unlink_if_symlink(path: Path) -> None:
    if path.is_symlink():
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _unlink_symlinked_analysis_outputs(record: dict[str, Any]) -> None:
    for key in ANALYSIS_CACHE_ARTIFACTS:
        _unlink_if_symlink(Path(record[key]))
    mask_dir = Path(record["work_dir"]) / "masks"
    if mask_dir.exists():
        # masks/는 세션 전체 페이지가 같이 쓴다. 이 페이지 mask만 지워야 다른 페이지 mask_ref가 깨지지 않는다.
        for mask_path in mask_dir.glob(f"{Path(record['input_path']).stem}_R*_mask.png"):
            _unlink_if_symlink(mask_path)


def _normalize_empty_region_visual(record: dict[str, Any]) -> bool:
    """OCR region이 없는 분석 결과는 오래된 박스 시각화를 원본으로 되돌린다."""
    regions_path = Path(record["regions_json"])
    input_path = Path(record["input_path"])
    bubble_vis_path = Path(record["bubble_vis_jpg"])
    if not regions_path.exists() or not input_path.exists():
        return False
    data = load_json(regions_path, default={})
    regions = data.get("regions") if isinstance(data, dict) else None
    if not isinstance(regions, list) or regions:
        return False
    try:
        # 복사본은 samefile이 아니므로 내용(stat 서명)까지 비교해야 GET마다 다시 복사하지 않는다.
        if bubble_vis_path.exists() and (
            input_path.samefile(bubble_vis_path) or filecmp.cmp(input_path, bubble_vis_path, shallow=True)
        ):
            return False
    except OSError:
        pass
    _copy_file(input_path, bubble_vis_path)
    return True


def analysis_outputs_are_cache_links(record: dict[str, Any]) -> bool:
    return any(Path(record[key]).is_symlink() for key in ANALYSIS_CACHE_LINK_ARTIFACTS)


def guidance_clear_marker(record: dict[str, Any]) -> Path:
    return Path(record["guidance_clear_json"])


def mark_guidance_cleared(record: dict[str, Any]) -> None:
    write_json(guidance_clear_marker(record), {"cleared_at": dt.datetime.now().isoformat(timespec="seconds")})


def clear_guidance_cleared_marker(record: dict[str, Any]) -> None:
    marker = guidance_clear_marker(record)
    if marker.exists():
        marker.unlink()


def _cache_artifact_path(image_hash: str, key: str) -> Path:
    return analysis_cache_dir(image_hash) / ANALYSIS_CACHE_ARTIFACTS[key]


def guidance_cache_path(image_hash: str) -> Path:
    return analysis_cache_dir(image_hash) / "manual_guidance.json"


def guidance_cache_tombstone_path(image_hash: str) -> Path:
    return analysis_cache_dir(image_hash) / "manual_guidance_cleared.json"


def guidance_digest(guidance: dict[str, Any]) -> str:
    payload = json.dumps(guidance or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def guidance_output_cache_dir(image_hash: str, guidance: dict[str, Any]) -> Path:
    return analysis_cache_dir(image_hash) / "guidance_outputs" / guidance_digest(guidance)


def _rewrite_region_mask_refs(regions_path: Path, cache_dir: Path, target_masks_dir: Path) -> None:
    data = load_json(regions_path, default=None)
    if not isinstance(data, dict):
        return
    changed = False
    for region in data.get("regions", []):
        mask_ref = region.get("mask_ref") if isinstance(region, dict) else None
        if not mask_ref:
            continue
        mask_name = Path(mask_ref).name
        cached_mask = cache_dir / "masks" / mask_name
        if not cached_mask.exists():
            continue
        target = target_masks_dir / mask_name
        _link_or_copy_file(cached_mask, target)
        # 이미 같은 경로를 가리키면 다시 쓰지 않는다(GET 반복 시 쓰기 0회).
        if region.get("mask_ref") != str(target):
            region["mask_ref"] = str(target)
            changed = True
    if changed:
        write_json(regions_path, data)


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def _visual_matches_input(record: dict[str, Any]) -> bool:
    """_normalize_empty_region_visual이 이미 원본으로 맞춰 둔 상태인지."""
    input_path = Path(record["input_path"])
    bubble_vis_path = Path(record["bubble_vis_jpg"])
    try:
        return bubble_vis_path.exists() and (
            input_path.samefile(bubble_vis_path) or filecmp.cmp(input_path, bubble_vis_path, shallow=True)
        )
    except OSError:
        return False


def _guidance_outputs_in_place(record: dict[str, Any], cache_dir: Path) -> bool:
    """guidance 산출물 캐시가 이미 세션에 그대로 연결돼 있으면 True. 이때 hydrate는 아무것도 쓰지 않는다."""
    expected_regions = load_json(cache_dir / GUIDANCE_OUTPUT_ARTIFACTS["regions_json"], default=None)
    if not isinstance(expected_regions, dict):
        return False
    target_masks_dir = Path(record["work_dir"]) / "masks"
    for region in expected_regions.get("regions", []):
        mask_ref = region.get("mask_ref") if isinstance(region, dict) else None
        if not mask_ref:
            continue
        mask_name = Path(mask_ref).name
        cached_mask = cache_dir / "masks" / mask_name
        if not cached_mask.exists():
            continue
        target = target_masks_dir / mask_name
        if not _same_file(cached_mask, target):
            return False
        region["mask_ref"] = str(target)
    if load_json(record["regions_json"], default=None) != expected_regions:
        return False
    regions_empty = expected_regions.get("regions") == []
    for key, name in GUIDANCE_OUTPUT_ARTIFACTS.items():
        if key == "regions_json":
            continue
        src = cache_dir / name
        dest = Path(record[key])
        if key in ANALYSIS_CACHE_LINK_ARTIFACTS:
            if key == "bubble_vis_jpg" and regions_empty and not dest.is_symlink() and _visual_matches_input(record):
                continue
            if not dest.is_symlink() or not _same_file(src, dest):
                return False
        else:
            try:
                if dest.is_symlink() or not filecmp.cmp(src, dest, shallow=False):
                    return False
            except OSError:
                return False
    return True


def backfill_analysis_cache_from_sessions(image_hash: str) -> bool:
    if not SESSION_ROOT.exists():
        return False
    for session_json in SESSION_ROOT.glob("*/session.json"):
        data = load_json(session_json, default={})
        session_id = data.get("session_id") or session_json.parent.name
        for item in data.get("images", []):
            if item.get("image_sha256") != image_hash:
                continue
            input_path = item.get("input_path")
            if not input_path:
                continue
            record = image_record(session_id, Path(input_path), image_hash)
            if store_analysis_cache(record, image_hash):
                return True
    return False


def hydrate_analysis_cache(record: dict[str, Any], image_hash: str, force: bool, log_callback, allow_backfill: bool = False) -> bool:
    if force:
        return False
    if guidance_clear_marker(record).exists():
        return False
    cache_dir = analysis_cache_dir(image_hash)
    if not all(_cache_artifact_path(image_hash, key).exists() for key in ANALYSIS_CACHE_REQUIRED):
        if not allow_backfill or not backfill_analysis_cache_from_sessions(image_hash):
            return False
    meta = load_json(cache_dir / "meta.json", default={})
    if meta.get("analysis_cache_version") not in ANALYSIS_CACHE_COMPATIBLE_VERSIONS:
        return False
    if all(Path(record[key]).exists() for key in ANALYSIS_CACHE_REQUIRED):
        return False

    for key in ANALYSIS_CACHE_ARTIFACTS:
        src = _cache_artifact_path(image_hash, key)
        if src.exists():
            if key in ANALYSIS_CACHE_LINK_ARTIFACTS:
                _link_or_copy_file(src, Path(record[key]))
            else:
                _copy_file(src, Path(record[key]))
    _rewrite_region_mask_refs(
        Path(record["regions_json"]),
        cache_dir,
        Path(record["work_dir"]) / "masks",
    )
    _normalize_empty_region_visual(record)
    log_callback("  ♻️ OCR 전단계 분석 캐시 사용")
    return True


def store_analysis_cache(record: dict[str, Any], image_hash: str) -> bool:
    if not all(Path(record[key]).exists() for key in ANALYSIS_CACHE_REQUIRED):
        return False
    _normalize_empty_region_visual(record)
    cache_dir = analysis_cache_dir(image_hash)
    changed = False
    for key in ANALYSIS_CACHE_ARTIFACTS:
        src = Path(record[key])
        if not src.exists():
            continue
        dest = cache_dir / ANALYSIS_CACHE_ARTIFACTS[key]
        if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
            _copy_file(src, dest)
            changed = True

    regions = load_json(record["regions_json"], default={})
    for region in regions.get("regions", []) if isinstance(regions, dict) else []:
        mask_ref = region.get("mask_ref") if isinstance(region, dict) else None
        if mask_ref and Path(mask_ref).exists():
            _copy_file(Path(mask_ref), cache_dir / "masks" / Path(mask_ref).name)
            changed = True
    write_json(cache_dir / "meta.json", {"analysis_cache_version": ANALYSIS_CACHE_VERSION})
    return changed


def backfill_guidance_cache_from_sessions(image_hash: str) -> bool:
    if not SESSION_ROOT.exists():
        return False
    tombstone = guidance_cache_tombstone_path(image_hash)
    tombstone_mtime = tombstone.stat().st_mtime_ns if tombstone.exists() else 0
    candidates: list[Path] = []
    for session_json in SESSION_ROOT.glob("*/session.json"):
        data = load_json(session_json, default={})
        session_id = data.get("session_id") or session_json.parent.name
        for item in data.get("images", []):
            if item.get("image_sha256") != image_hash:
                continue
            input_path = item.get("input_path")
            if not input_path:
                continue
            record = image_record(session_id, Path(input_path), image_hash)
            guidance_path = Path(record["guidance_json"])
            if guidance_path.exists() and guidance_has_edits(load_json(guidance_path, default={})):
                if guidance_path.stat().st_mtime_ns <= tombstone_mtime:
                    continue
                candidates.append(guidance_path)
    if not candidates:
        return False
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    _copy_file(latest, guidance_cache_path(image_hash))
    return True


def store_guidance_cache(record: dict[str, Any], image_hash: str) -> bool:
    guidance_path = Path(record["guidance_json"])
    if not guidance_path.exists():
        return False
    guidance = load_json(guidance_path, default={})
    if not guidance_has_edits(guidance):
        return False
    tombstone = guidance_cache_tombstone_path(image_hash)
    if tombstone.exists():
        tombstone.unlink()
    _copy_file(guidance_path, guidance_cache_path(image_hash))
    return True


def store_guidance_output_cache(record: dict[str, Any], image_hash: str, guidance: dict[str, Any]) -> bool:
    if not guidance_has_edits(guidance):
        return False
    if not all(Path(record[key]).exists() for key in GUIDANCE_OUTPUT_REQUIRED):
        return False
    cache_dir = guidance_output_cache_dir(image_hash, guidance)
    changed = False
    for key, name in GUIDANCE_OUTPUT_ARTIFACTS.items():
        src = Path(record[key])
        if not src.exists():
            continue
        dest = cache_dir / name
        if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
            _copy_file(src, dest)
            changed = True

    regions = load_json(record["regions_json"], default={})
    for region in regions.get("regions", []) if isinstance(regions, dict) else []:
        mask_ref = region.get("mask_ref") if isinstance(region, dict) else None
        if mask_ref and Path(mask_ref).exists():
            _copy_file(Path(mask_ref), cache_dir / "masks" / Path(mask_ref).name)
            changed = True
    write_json(
        cache_dir / "meta.json",
        {
            "analysis_cache_version": ANALYSIS_CACHE_VERSION,
            "guidance_digest": guidance_digest(guidance),
        },
    )
    return changed


def hydrate_guidance_output_cache(record: dict[str, Any], image_hash: str, guidance: dict[str, Any], log_callback) -> bool:
    if not guidance_has_edits(guidance):
        return False
    cache_dir = guidance_output_cache_dir(image_hash, guidance)
    meta = load_json(cache_dir / "meta.json", default={})
    if meta.get("analysis_cache_version") not in ANALYSIS_CACHE_COMPATIBLE_VERSIONS:
        return False
    if meta.get("guidance_digest") != guidance_digest(guidance):
        return False
    if not all((cache_dir / GUIDANCE_OUTPUT_ARTIFACTS[key]).exists() for key in GUIDANCE_OUTPUT_REQUIRED):
        return False
    if _guidance_outputs_in_place(record, cache_dir):
        log_callback("  ✍️ guidance 산출물 캐시 사용")
        return True

    _unlink_symlinked_analysis_outputs(record)
    for key, name in GUIDANCE_OUTPUT_ARTIFACTS.items():
        src = cache_dir / name
        if not src.exists():
            continue
        if key in ANALYSIS_CACHE_LINK_ARTIFACTS:
            _link_or_copy_file(src, Path(record[key]))
        else:
            _copy_file(src, Path(record[key]))
    _rewrite_region_mask_refs(
        Path(record["regions_json"]),
        cache_dir,
        Path(record["work_dir"]) / "masks",
    )
    _normalize_empty_region_visual(record)
    log_callback("  ✍️ guidance 산출물 캐시 사용")
    return True


def hydrate_guidance_cache(record: dict[str, Any], image_hash: str, log_callback, allow_backfill: bool = False) -> bool:
    target = Path(record["guidance_json"])
    if target.exists():
        return False
    cache = guidance_cache_path(image_hash)
    if not cache.exists() and (not allow_backfill or not backfill_guidance_cache_from_sessions(image_hash)):
        return False
    if not cache.exists():
        return False
    _copy_file(cache, target)
    log_callback("  ✍️ guidance 캐시 사용")
    return True


def clear_guidance_cache(image_hash: str) -> None:
    cache = guidance_cache_path(image_hash)
    if cache.exists():
        cache.unlink()
    write_json(guidance_cache_tombstone_path(image_hash), {"cleared_at": dt.datetime.now().isoformat(timespec="seconds")})


def hydrate_cv_cache(record: dict[str, Any], image_hash: str, force: bool, log_callback) -> bool:
    if force:
        return False
    cache = cv_cache_path(image_hash)
    cv_json = Path(record["cv_json"])
    if cv_json.exists() or not cache.exists():
        return False
    cv_json.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cache, cv_json)
    log_callback("  ♻️ CV 전역 캐시 사용")
    return True


def store_cv_cache(record: dict[str, Any], image_hash: str) -> bool:
    cv_json = Path(record["cv_json"])
    if not cv_json.exists():
        return False
    cache = cv_cache_path(image_hash)
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists() or cv_json.stat().st_mtime > cache.stat().st_mtime:
        shutil.copy2(cv_json, cache)
        return True
    return False


def hydrate_load_cache_for_item(session_id: str, item: dict[str, Any], allow_backfill: bool) -> None:
    image_hash = item.get("image_sha256") or ensure_record_hash(item)
    record = image_record(session_id, Path(item["input_path"]), image_hash)
    if hydrate_analysis_cache(record, image_hash, False, lambda _message: None, allow_backfill=allow_backfill):
        record = image_record(session_id, Path(item["input_path"]), image_hash)
    if hydrate_guidance_cache(record, image_hash, lambda _message: None, allow_backfill=allow_backfill):
        record = image_record(session_id, Path(item["input_path"]), image_hash)
    if guidance_has_edits(record.get("guidance", {})):
        if hydrate_guidance_output_cache(record, image_hash, record["guidance"], lambda _message: None):
            return
        if analysis_outputs_are_cache_links(record):
            reapply_guidance_if_present(session_id, record, lambda _message: None)


def hydrate_load_cache_for_records(
    session_id: str,
    records: list[dict[str, Any]],
    sync_limit: int | None = None,
) -> None:
    if not records:
        return
    sync_limit = LOAD_CACHE_SYNC_LIMIT if sync_limit is None else sync_limit
    for item in records[:sync_limit]:
        hydrate_load_cache_for_item(session_id, item, allow_backfill=True)
    remaining = records[sync_limit:]
    if remaining:
        start_background_load_cache_hydration(session_id, remaining)


def start_background_load_cache_hydration(session_id: str, records: list[dict[str, Any]]) -> None:
    items = [dict(item) for item in records if item.get("id") and item.get("input_path")]
    if not items:
        return
    key = (session_id, ",".join(item["id"] for item in items))
    with _LOAD_HYDRATE_LOCK:
        if key in _LOAD_HYDRATE_RUNNING:
            return
        _LOAD_HYDRATE_RUNNING.add(key)

    def run() -> None:
        try:
            data = ensure_session(session_id)
            by_id = {item.get("id"): item for item in data.get("images", [])}
            changed = False
            for item in items:
                current = by_id.get(item["id"], item)
                hydrate_load_cache_for_item(session_id, current, allow_backfill=True)
                if item.get("image_sha256") and not current.get("image_sha256"):
                    current["image_sha256"] = item["image_sha256"]
                    changed = True
            if changed:
                save_session(data)
        finally:
            with _LOAD_HYDRATE_LOCK:
                _LOAD_HYDRATE_RUNNING.discard(key)

    threading.Thread(target=run, daemon=True, name=f"load-cache-hydrate-{session_id}").start()


def safe_filename(name: str) -> str:
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    clean = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in stem)
    return f"{clean[:80] or 'image'}{suffix}"


def allocate_input_path(input_dir: Path, base_name: str, index: int, taken_ids: set[str]) -> Path:
    """세션 input 폴더에서 이미지 id(stem)와 파일이 모두 겹치지 않는 경로를 고른다.

    input 파일은 전역 input cache를 가리키는 symlink라서, 기존 경로에 덮어쓰면
    다른 이미지(다른 세션 포함)의 원본이 바뀐다. 고른 stem은 taken_ids에 추가한다.
    """
    stem_names = [base_name, f"{index:03d}_{base_name}"]
    attempt = 2
    while True:
        for name in stem_names:
            candidate = input_dir / name
            if candidate.stem not in taken_ids and not candidate.exists() and not candidate.is_symlink():
                taken_ids.add(candidate.stem)
                return candidate
        stem_names = [f"{index:03d}_{attempt}_{base_name}"]
        attempt += 1


def export_zip_filename(project_name: str, session_id: str) -> str:
    raw_name = (project_name or "").strip() or session_id
    clean = "".join(ch if ch.isalnum() or ch in {" ", "_", "-", "."} else "_" for ch in raw_name)
    clean = re.sub(r"\s+", " ", clean).strip(" ._")[:100] or session_id
    if clean.lower().endswith(".zip"):
        return clean
    return f"{clean}.zip"


def image_record(
    session_id: str,
    path: Path,
    image_hash: str | None = None,
    manifest_item: dict[str, Any] | None = None,
    probe_image: bool = True,
    include_detail: bool = True,
) -> dict[str, Any]:
    """페이지 레코드를 만든다.

    include_detail=False면 text_groups/regions/integrated 같은 큰 JSON을 읽지 않는 가벼운 레코드다.
    목록(세션 요약)에는 가벼운 레코드를, 선택한 페이지에는 전체 레코드를 쓴다.
    """
    stem = path.stem
    spread_metadata = spread_candidate_from_manifest(manifest_item or {})
    if spread_metadata is None:
        if probe_image:
            width, height = image_dimensions(path)
            spread_metadata = spread_candidate_from_dimensions(width, height, path)
        else:
            spread_metadata = {"wide_spread_candidate": False}
    base = session_dir(session_id)
    work = base / "work"
    output = base / "output"
    json_dir = work / "json"
    vis_dir = work / "vis"
    sheets_dir = work / "sheets"
    masked_dir = work / "masked"
    final_path = output / "final" / f"{stem}_final.jpg"
    export_recovery_path = json_dir / f"{stem}_export_recovery.json"
    export_recovery = load_json(export_recovery_path, default={})
    has_image_generation_fallback = isinstance(export_recovery, dict) and export_recovery.get("status") == "generated"
    record = {
        "id": stem,
        "name": path.name,
        "image_sha256": image_hash,
        "input_path": str(path),
        "input_url": file_url(path),
        **spread_metadata,
        "work_dir": str(work),
        "output_dir": str(output),
        "cv_json": str(json_dir / f"{stem}_cv.json"),
        "text_groups_json": str(json_dir / f"{stem}_text_groups.json"),
        "effective_text_groups_json": str(json_dir / f"{stem}_text_groups_effective.json"),
        "guidance_json": str(json_dir / f"{stem}_manual_guidance.json"),
        "guidance_clear_json": str(json_dir / f"{stem}_manual_guidance_cleared.json"),
        "guidance_report_json": str(json_dir / f"{stem}_manual_guidance_report.json"),
        "overlay_guidance_json": str(json_dir / f"{stem}_overlay_guidance.json"),
        "overlay_layout_json": str(json_dir / f"{stem}_overlay_layout.json"),
        "regions_json": str(json_dir / f"{stem}_regions.json"),
        "integrated_json": str(json_dir / f"{stem}_integrated.json"),
        "ocr_status_json": str(json_dir / f"{stem}_ocr_status.json"),
        "export_recovery_json": str(export_recovery_path),
        "indexed_sheet_jpg": str(sheets_dir / f"{stem}_indexed_sheet.jpg"),
        "masked_jpg": str(masked_dir / f"{stem}_masked.jpg"),
        "bubble_vis_jpg": str(vis_dir / f"{stem}_bubble_vis.jpg"),
        "final_jpg": str(final_path),
        "bubble_vis_url": file_url(vis_dir / f"{stem}_bubble_vis.jpg"),
        "indexed_sheet_url": file_url(sheets_dir / f"{stem}_indexed_sheet.jpg"),
        "masked_url": file_url(masked_dir / f"{stem}_masked.jpg"),
        "final_url": file_url(final_path),
        "has_effective_text_groups": (json_dir / f"{stem}_text_groups_effective.json").exists(),
        "has_integrated_json": (json_dir / f"{stem}_integrated.json").exists(),
        "ocr_status": load_json(json_dir / f"{stem}_ocr_status.json", default={}),
        "export_recovery": export_recovery,
        "has_image_generation_fallback": has_image_generation_fallback,
        "detail_rev": page_detail_rev(json_dir, stem),
        "detail_loaded": include_detail,
    }
    if include_detail:
        regions = load_json(json_dir / f"{stem}_regions.json", default={})
        integrated = load_json(json_dir / f"{stem}_integrated.json", default=[])
        record.update({
            "text_groups": load_json(json_dir / f"{stem}_text_groups.json", default=[]),
            "effective_text_groups": load_json(json_dir / f"{stem}_text_groups_effective.json", default=[]),
            "regions": regions,
            "guidance": load_json(json_dir / f"{stem}_manual_guidance.json", default={}),
            "guidance_report": load_json(json_dir / f"{stem}_manual_guidance_report.json", default={}),
            "overlay_guidance": load_json(json_dir / f"{stem}_overlay_guidance.json", default={}),
            "overlay_layout": load_json(json_dir / f"{stem}_overlay_layout.json", default={}),
            "integrated": integrated,
        })
        record.update(_regions_flags(regions))
        record.update(_integrated_flags(integrated))
    else:
        record.update(_cached_json_flags(json_dir / f"{stem}_regions.json", _regions_flags, default={}))
        record.update(_cached_json_flags(json_dir / f"{stem}_integrated.json", _integrated_flags, default=[]))
    return record


# 세션 요약 목록에 들어가는 키. 전체 레코드도 같은 키를 같은 의미로 가진다(프론트에서 병합).
IMAGE_SUMMARY_KEYS = (
    "id",
    "name",
    "image_sha256",
    "input_path",
    "input_url",
    "width",
    "height",
    "aspect_ratio",
    "wide_spread_candidate",
    "left_margin_ratio",
    "right_margin_ratio",
    "spread_layout",
    "parent_id",
    "parent_name",
    "parent_input_path",
    "source_image_sha256",
    "crop_rect",
    "page_side",
    "reading_order",
    "split_policy",
    "split_x_ratio",
    "integrated_json",
    "overlay_guidance_json",
    "bubble_vis_url",
    "indexed_sheet_url",
    "masked_url",
    "final_url",
    "has_effective_text_groups",
    "has_integrated_json",
    "has_analysis",
    "ocr_region_count",
    "integrated_skipped",
    "integrated_message",
    "ocr_status",
    "export_recovery",
    "has_image_generation_fallback",
    "detail_rev",
)
PAGE_DETAIL_FILES = (
    "text_groups",
    "text_groups_effective",
    "regions",
    "manual_guidance",
    "manual_guidance_report",
    "overlay_guidance",
    "overlay_layout",
    "integrated",
)
_JSON_FLAGS_CACHE: dict[tuple[str, str], tuple[tuple[int, int], dict[str, Any]]] = {}
_JSON_FLAGS_LOCK = threading.Lock()


def page_detail_rev(json_dir: Path, stem: str) -> str:
    """페이지 상세 JSON들의 (mtime, size) 서명. 바뀌면 프론트가 상세를 다시 받는다."""
    parts = []
    for name in PAGE_DETAIL_FILES:
        try:
            st = (json_dir / f"{stem}_{name}.json").stat()
            parts.append(f"{st.st_mtime_ns:x}.{st.st_size:x}")
        except OSError:
            parts.append("-")
    return hashlib.sha1("|".join(parts).encode("ascii")).hexdigest()[:16]


def _regions_flags(data: Any) -> dict[str, Any]:
    regions = data.get("regions") if isinstance(data, dict) else None
    has_analysis = isinstance(regions, list)
    return {"has_analysis": has_analysis, "ocr_region_count": len(regions) if has_analysis else None}


def _integrated_flags(data: Any) -> dict[str, Any]:
    skipped = bool(isinstance(data, dict) and data.get("skipped"))
    return {
        "integrated_skipped": skipped,
        "integrated_message": (data.get("message") or "") if skipped else "",
    }


def _cached_json_flags(path: Path, flags_fn, default: Any) -> dict[str, Any]:
    """큰 JSON에서 뽑는 요약 플래그를 (mtime, size) 기준으로 캐시한다. 요약 목록이 매번 전체 JSON을 파싱하지 않게."""
    try:
        st = path.stat()
        signature = (st.st_mtime_ns, st.st_size)
    except OSError:
        return flags_fn(default)
    key = (str(path), flags_fn.__name__)
    with _JSON_FLAGS_LOCK:
        cached = _JSON_FLAGS_CACHE.get(key)
    if cached and cached[0] == signature:
        return dict(cached[1])
    flags = flags_fn(load_json(path, default=default))
    with _JSON_FLAGS_LOCK:
        if len(_JSON_FLAGS_CACHE) > 20000:
            _JSON_FLAGS_CACHE.clear()
        _JSON_FLAGS_CACHE[key] = (signature, flags)
    return dict(flags)


def image_summary(record: dict[str, Any]) -> dict[str, Any]:
    summary = {key: record[key] for key in IMAGE_SUMMARY_KEYS if key in record}
    summary["detail_loaded"] = False
    return summary


def write_export_recovery_generated(record: dict[str, Any], req: GenerateMissingFinalRequest) -> None:
    existing = load_json(record["export_recovery_json"], default={})
    if not isinstance(existing, dict):
        existing = {}
    try:
        attempts = int(existing.get("attempts", 0)) + 1
    except (TypeError, ValueError):
        attempts = 1
    write_json(record["export_recovery_json"], {
        "status": "generated",
        "source": "image_generation_fallback",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "attempts": attempts,
        "model": req.model or "gpt-image-2",
        "text_model": req.text_model or "gpt-5.4-mini",
        "analyze_first": bool(req.analyze_first),
        "quality": req.quality or "medium",
        "size": req.size or "match_input",
        "output_format": req.output_format or "jpeg",
        "output_compression": req.output_compression,
        "prompt_guide": req.prompt_guide or "",
        "final_path": record["final_jpg"],
    })


def clear_export_recovery_metadata(record: dict[str, Any]) -> None:
    raw_path = record.get("export_recovery_json")
    if not raw_path:
        return
    path = Path(raw_path)
    if path.exists() or path.is_symlink():
        path.unlink()


def merge_manifest_metadata(record: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    passthrough_keys = (
        "parent_id",
        "parent_name",
        "parent_input_path",
        "source_image_sha256",
        "crop_rect",
        "page_side",
        "reading_order",
        "split_policy",
        "split_x_ratio",
        "wide_spread_candidate",
        "left_margin_ratio",
        "right_margin_ratio",
        "spread_layout",
        "aspect_ratio",
        "width",
        "height",
    )
    for key in passthrough_keys:
        if key in item:
            record[key] = item[key]
    return record


def analysis_state_from_record(record: dict[str, Any]) -> dict[str, Any]:
    regions_path = Path(record["regions_json"])
    if not regions_path.exists():
        return {
            "analysis_status": "not_run",
            "ocr_region_count": None,
            "reason": OCR_SKIP_REASON_ANALYSIS_NOT_RUN,
            "missing_inputs": ["regions_json"],
        }

    data = load_json(regions_path, default={})
    regions = data.get("regions") if isinstance(data, dict) else []
    if not isinstance(regions, list):
        return {
            "analysis_status": "invalid",
            "ocr_region_count": None,
            "reason": OCR_SKIP_REASON_MISSING_SHEET,
            "missing_inputs": ["regions_json"],
        }
    if len(regions) == 0:
        return {
            "analysis_status": data.get("analysis_status") or "complete",
            "ocr_region_count": 0,
            "reason": OCR_SKIP_REASON_NO_REGIONS,
            "missing_inputs": [],
        }
    if not Path(record["indexed_sheet_jpg"]).exists():
        return {
            "analysis_status": data.get("analysis_status") or "complete",
            "ocr_region_count": len(regions),
            "reason": OCR_SKIP_REASON_MISSING_SHEET,
            "missing_inputs": ["indexed_sheet_jpg"],
        }
    return {
        "analysis_status": data.get("analysis_status") or "complete",
        "ocr_region_count": len(regions),
        "reason": None,
        "missing_inputs": [],
    }


def ocr_skip_message(reason: str) -> str:
    if reason == OCR_SKIP_REASON_ANALYSIS_NOT_RUN:
        return "말풍선 분석 산출물이 없어 OCR/번역을 건너뜁니다."
    if reason == OCR_SKIP_REASON_NO_REGIONS:
        return "말풍선 분석은 완료됐지만 OCR 대상 region이 없어 OCR/번역을 건너뜁니다."
    return "OCR sheet 산출물이 불완전해 Gemini OCR/번역을 건너뜁니다."


def ocr_skip_payload(reason: str, missing: list[str] | None = None, analysis_state: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "skipped",
        "skipped": True,
        "stage": "ocr_translation",
        "reason": reason,
        "message": ocr_skip_message(reason),
        "missing_inputs": missing or [],
        "analysis_status": (analysis_state or {}).get("analysis_status"),
        "ocr_region_count": (analysis_state or {}).get("ocr_region_count"),
        "clusters": [],
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def integrated_skip_detail(integrated: Any) -> str | None:
    if isinstance(integrated, dict) and integrated.get("skipped"):
        return integrated.get("message") or integrated.get("reason") or "OCR/번역 스킵"
    return None


def mark_ocr_status(record: dict[str, Any], status: str, detail: str = "", message: str = "") -> None:
    write_json(
        Path(record["ocr_status_json"]),
        {
            "status": status,
            "detail": detail,
            "message": message or detail,
            "has_integrated_json": Path(record["integrated_json"]).exists(),
            "has_final": Path(record["final_jpg"]).exists(),
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
    )


def ocr_completed_for_record(record: dict[str, Any]) -> bool:
    status = load_json(Path(record["ocr_status_json"]), default={})
    if isinstance(status, dict) and status.get("status") in {"done", "skipped"}:
        return True
    integrated_path = Path(record["integrated_json"])
    if not integrated_path.exists():
        return False
    integrated = load_json(integrated_path, default=None)
    return not (isinstance(integrated, dict) and integrated.get("skipped"))


def mark_ocr_skipped(record: dict[str, Any], reason: str, missing: list[str], analysis_state: dict[str, Any]) -> None:
    write_json(Path(record["integrated_json"]), ocr_skip_payload(reason, missing, analysis_state))
    for key in ("final_jpg", "overlay_layout_json"):
        path = Path(record[key])
        if path.exists() or path.is_symlink():
            path.unlink()
    mark_ocr_status(record, "skipped", ocr_skip_message(reason), reason)


def file_url(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    return f"/api/file?path={quote(path.absolute().as_posix())}&v={path.stat().st_mtime_ns}"


def copy_images_into_session(paths: list[Path], session_id: str) -> list[dict[str, Any]]:
    data = ensure_session(session_id)
    input_dir = session_dir(session_id) / "input"
    existing_ids = {item["id"] for item in data.get("images", [])}
    records: list[dict[str, Any]] = []
    for index, src in enumerate(sorted(paths, key=lambda path: natural_name_key(path.name)), start=1):
        if src.suffix.lower() not in IMAGE_EXTS or not src.exists():
            continue
        candidate = allocate_input_path(input_dir, safe_filename(src.name), index, existing_ids)
        image_hash, _cache_path = link_cached_input(src, candidate)
        record = image_manifest_item(candidate, image_hash)
        data.setdefault("images", []).append(record)
        records.append(record)
    save_session(data)
    hydrate_load_cache_for_records(session_id, records, sync_limit=IMPORT_CACHE_SYNC_LIMIT)
    return records


def scan_images(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in IMAGE_EXTS else []
    if not path.is_dir():
        return []
    iterator = path.rglob("*") if recursive else path.glob("*")
    return sorted(
        (p for p in iterator if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=lambda item: natural_name_key(item.name),
    )


def session_images(session_id: str, include_detail: bool = True) -> list[dict[str, Any]]:
    """세션 페이지 목록을 만든다. include_detail=False면 목록용 요약만 돌려준다.

    캐시 연결(hydrate)은 멱등이라 이미 연결된 정상 상태에서는 아무것도 쓰지 않는다.
    guidance 재적용(Step 3 재실행)이 필요한 페이지는 여기서 돌리지 않고 백그라운드 hydration으로 넘긴다.
    """
    data = ensure_session(session_id)
    changed = False
    images = []
    pending_reapply: list[dict[str, Any]] = []

    def noop(_message: str) -> None:
        return None

    for item in sorted_image_records(data.get("images", [])):
        if not item.get("image_sha256"):
            ensure_record_hash(item)
            changed = True
        image_hash = item.get("image_sha256")
        input_path = Path(item["input_path"])
        # hydrate 함수들은 경로만 쓰므로 큰 JSON을 읽지 않는 가벼운 레코드로 충분하다.
        record = image_record(session_id, input_path, image_hash, item, probe_image=False, include_detail=False)
        if image_hash:
            hydrate_analysis_cache(record, image_hash, False, noop, allow_backfill=False)
            hydrate_guidance_cache(record, image_hash, noop, allow_backfill=False)
        guidance = load_json(record["guidance_json"], default={})
        if guidance_has_edits(guidance):
            output_ready = bool(image_hash) and hydrate_guidance_output_cache(record, image_hash, guidance, noop)
            if not output_ready and analysis_outputs_are_cache_links(record) and _claim_guidance_reapply(session_id, record):
                pending_reapply.append(item)
        _normalize_empty_region_visual(record)
        record = merge_manifest_metadata(
            image_record(session_id, input_path, image_hash, item, probe_image=False, include_detail=include_detail),
            item,
        )
        images.append(record if include_detail else image_summary(record))
    if changed:
        save_session(data)
    if pending_reapply:
        start_background_load_cache_hydration(session_id, pending_reapply)
    return images


def session_image_summaries(session_id: str) -> list[dict[str, Any]]:
    return session_images(session_id, include_detail=False)


_GUIDANCE_REAPPLY_CLAIMED: set[tuple[str, str, int]] = set()


def _claim_guidance_reapply(session_id: str, record: dict[str, Any]) -> bool:
    """같은 guidance 파일 상태로는 프로세스당 한 번만 백그라운드 재적용을 건다(목록 폴링 때 반복 실행 방지)."""
    try:
        guidance_mtime = Path(record["guidance_json"]).stat().st_mtime_ns
    except OSError:
        guidance_mtime = 0
    key = (session_id, record["id"], guidance_mtime)
    with _LOAD_HYDRATE_LOCK:
        if key in _GUIDANCE_REAPPLY_CLAIMED:
            return False
        _GUIDANCE_REAPPLY_CLAIMED.add(key)
    return True


def load_hydration_pending(session_id: str) -> bool:
    with _LOAD_HYDRATE_LOCK:
        return any(key[0] == session_id for key in _LOAD_HYDRATE_RUNNING)


def page_record(session_id: str, image_id: str) -> dict[str, Any]:
    """선택한 페이지 하나의 전체 레코드. 읽기 전용."""
    item = get_image(session_id, image_id)
    return merge_manifest_metadata(
        image_record(session_id, Path(item["input_path"]), item.get("image_sha256"), item, probe_image=False),
        item,
    )


def analyze_one_image(session_id: str, item: dict[str, Any], index: int, total: int, force: bool, emit) -> bool:
    image_hash = ensure_record_hash(item)
    record = image_record(session_id, Path(item["input_path"]), image_hash)

    def image_emit(status: str, detail: str = "") -> None:
        emit({"type": "image_status", "image_id": record["id"], "name": record["name"], "status": status, "detail": detail})

    def log_emit(message: str) -> None:
        emit({"type": "log", "image_id": record["id"], "message": f"[{record['name']}] {message}"})

    image_emit("running", "분석 시작")
    emit({"type": "log", "image_id": record["id"], "message": f"[{index}/{total}] {record['name']} 분석 시작"})
    if hydrate_analysis_cache(record, image_hash, force, log_emit, allow_backfill=True):
        refreshed = reapply_guidance_if_present(session_id, record, log_emit) or image_record(session_id, Path(item["input_path"]), image_hash)
        emit({
            "type": "image_status",
            "image_id": record["id"],
            "name": record["name"],
            "status": "done",
            "detail": "Guidance 재적용" if guidance_has_edits(refreshed.get("guidance", {})) else "분석 캐시 복원",
            "image": refreshed,
        })
        return True
    hydrate_cv_cache(record, image_hash, force, log_emit)
    _unlink_symlinked_analysis_outputs(record)
    ok, log = run_single_image(
        record["input_path"],
        record["work_dir"],
        record["final_jpg"],
        force=force,
        stop_after="pre_ocr",
        log_callback=log_emit,
    )
    if ok:
        if store_cv_cache(record, image_hash):
            log_emit("  ♻️ CV 전역 캐시 저장")
        if store_analysis_cache(record, image_hash):
            log_emit("  ♻️ OCR 전단계 분석 캐시 저장")
        clear_guidance_cleared_marker(record)
        refreshed = reapply_guidance_if_present(session_id, record, log_emit) or image_record(session_id, Path(item["input_path"]), image_hash)
        emit({
            "type": "image_status",
            "image_id": record["id"],
            "name": record["name"],
            "status": "done",
            "detail": "Guidance 재적용" if guidance_has_edits(refreshed.get("guidance", {})) else "OCR 전단계 완료",
            "image": refreshed,
        })
        return True
    image_emit("failed", "실패")
    emit({"type": "error", "image_id": record["id"], "message": log})
    return False


def stream_analyze(req: AnalyzeRequest):
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def emit(payload: dict[str, Any]) -> None:
        event_queue.put(payload)

    def run() -> None:
        try:
            data = ensure_session(req.session_id)
            if req.image_id == "all":
                targets = sorted_image_records(data.get("images", []))
            elif req.image_id == "failed":
                targets = [
                    item for item in sorted_image_records(data.get("images", []))
                    if (image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256")).get("ocr_status") or {}).get("status") == "failed"
                ]
            else:
                targets = [get_image(req.session_id, req.image_id)]
            total = len(targets)
            if total == 0:
                emit({"type": "log", "message": "분석할 이미지가 없습니다."})
                emit({"type": "complete", "ok": True, "images": []})
                return
            workers = max(1, min(req.max_workers, total))
            emit({"type": "log", "message": f"Analyze worker {workers}개로 실행"})
            for item in targets:
                ensure_record_hash(item)
                emit({"type": "image_status", "image_id": item["id"], "name": item["name"], "status": "queued", "detail": "대기"})
            save_session(data)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(analyze_one_image, req.session_id, item, index, total, req.force, emit): item
                    for index, item in enumerate(targets, start=1)
                }
                # 한 장이 실패해도 나머지는 계속 처리되므로, 끝까지 기다린 뒤 complete로 결과를 알린다.
                ok = True
                for future in as_completed(futures):
                    if not future.result():
                        ok = False
            emit({"type": "complete", "ok": ok, "images": session_image_summaries(req.session_id)})
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
        finally:
            event_queue.put(None)

    threading.Thread(target=run, daemon=True).start()

    while True:
        event = event_queue.get()
        if event is None:
            break
        yield json.dumps(event, ensure_ascii=False) + "\n"


def get_image(session_id: str, image_id: str) -> dict[str, Any]:
    data = ensure_session(session_id)
    for item in data.get("images", []):
        if item.get("id") == image_id:
            return item
    raise HTTPException(status_code=404, detail="Image not found")


def remove_session_image(session_id: str, image_id: str) -> list[dict[str, Any]]:
    data = ensure_session(session_id)
    images = data.get("images", [])
    target = next((item for item in images if item.get("id") == image_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Image not found")
    data["images"] = [item for item in images if item.get("id") != image_id]
    save_session(data)
    input_path = Path(target.get("input_path", ""))
    session_input = (session_dir(session_id) / "input").resolve()
    try:
        resolved_parent = input_path.resolve().parent
    except OSError:
        resolved_parent = input_path.parent
    if input_path.exists() and (input_path.parent.resolve() == session_input or resolved_parent == session_input):
        input_path.unlink()
    return session_image_summaries(session_id)


def current_clusters_path(record: dict[str, Any], image_id: str) -> Path:
    effective = Path(record["effective_text_groups_json"])
    if effective.exists():
        return effective
    return Path(record["text_groups_json"])


def guidance_has_edits(guidance: dict[str, Any]) -> bool:
    if not isinstance(guidance, dict):
        return False
    keys = (
        "disabled_tg_ids",
        "tg_splits",
        "virtual_walls",
        "split_guides",
        "manual_regions",
        "manual_seed_points",
        "suppressed_regions",
    )
    return any(bool(guidance.get(key)) for key in keys)


def _canonical_guidance_items(items: Any) -> set[str]:
    if not isinstance(items, list):
        return set()
    return {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in items}


def guidance_allows_unboxed_mask_reuse(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if not guidance_has_edits(previous):
        return False
    stable_keys = ("disabled_tg_ids", "tg_splits", "suppressed_regions")
    for key in stable_keys:
        if previous.get(key, []) != current.get(key, []):
            return False
    additive_keys = ("manual_regions", "manual_seed_points", "virtual_walls", "split_guides")
    for key in additive_keys:
        old_items = _canonical_guidance_items(previous.get(key, []))
        new_items = _canonical_guidance_items(current.get(key, []))
        if not old_items.issubset(new_items):
            return False
    return True


def run_step3_with_effective_text_groups(session_id: str, image_id: str, guidance: dict[str, Any]) -> dict[str, Any]:
    try:
        from pipeline.dialogue_extractor import build_dialogue_sheet
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Step 3 dependency is missing: {exc}. Install requirements.txt in .venv.",
        ) from exc

    record = image_record(session_id, Path(get_image(session_id, image_id)["input_path"]))
    clear_guidance_cleared_marker(record)
    guidance_path = Path(record["guidance_json"])
    previous_guidance = load_json(guidance_path, default={})
    can_reuse_unboxed_mask = (
        Path(record["masked_jpg"]).exists()
        and Path(record["regions_json"]).exists()
        and guidance_allows_unboxed_mask_reuse(previous_guidance, guidance)
    )
    write_json(guidance_path, guidance)

    report = build_effective_text_groups(
        record["text_groups_json"],
        guidance_path,
        record["effective_text_groups_json"],
        record["guidance_report_json"],
    )

    _unlink_symlinked_analysis_outputs(record)
    sheet_result = build_dialogue_sheet(
        image_path=record["input_path"],
        clusters_json=record["effective_text_groups_json"],
        output_masked_path=record["masked_jpg"],
        output_json_path=record["regions_json"],
        indexed_output_sheet_path=record["indexed_sheet_jpg"],
        output_vis_path=record["bubble_vis_jpg"],
        manual_regions=guidance.get("manual_regions", []),
        manual_seed_points=guidance.get("manual_seed_points", []),
        manual_virtual_walls=guidance.get("virtual_walls", []),
        manual_split_guides=guidance.get("split_guides", []),
        suppressed_regions=guidance.get("suppressed_regions", []),
        base_masked_path=record["masked_jpg"] if can_reuse_unboxed_mask else None,
        reuse_existing_unboxed_inpaint=can_reuse_unboxed_mask,
    )
    if sheet_result.get("reused_unboxed_inpaint"):
        report["fast_path"] = {
            "reused_unboxed_inpaint": True,
            "reason": "additive_guidance_only",
        }
    if isinstance(sheet_result, dict) and isinstance(sheet_result.get("manual_guidance_report"), dict):
        report["manual_guidance"] = sheet_result["manual_guidance_report"]
        write_json(record["guidance_report_json"], report)
    refreshed = image_record(session_id, Path(record["input_path"]))
    refreshed["guidance_report"] = report
    return refreshed


def reapply_guidance_if_present(session_id: str, record: dict[str, Any], log_callback) -> dict[str, Any] | None:
    guidance = load_json(record["guidance_json"], default={})
    if not guidance_has_edits(guidance):
        return None
    if not Path(record["text_groups_json"]).exists():
        return None
    image_hash = record.get("image_sha256")
    if image_hash and hydrate_guidance_output_cache(record, image_hash, guidance, log_callback):
        return image_record(session_id, Path(record["input_path"]), image_hash)
    log_callback("  ✍️ 기존 guidance 재적용")
    refreshed = run_step3_with_effective_text_groups(session_id, record["id"], guidance)
    if image_hash:
        store_guidance_output_cache(refreshed, image_hash, guidance)
    return refreshed


def render_overlay_for_record(record: dict[str, Any], font_path: str | None, tracking_offset: int | None = None, log_callback=None) -> None:
    if not Path(record["integrated_json"]).exists():
        raise HTTPException(status_code=400, detail="OCR/translation result is missing. Run OCR/Final first.")
    skip_detail = integrated_skip_detail(load_json(record["integrated_json"], default={}))
    if skip_detail:
        raise HTTPException(status_code=400, detail=skip_detail)
    if not Path(record["masked_jpg"]).exists():
        raise HTTPException(status_code=400, detail="Masked image is missing. Apply analysis first.")
    font = font_path or DEFAULT_FONT
    if log_callback is not None:
        log_callback("  ✍️ overlay 직접 렌더")
    count = apply_text_overlay(
        Path(record["masked_jpg"]),
        Path(record["integrated_json"]),
        Path(record["final_jpg"]),
        regions_json=Path(record["regions_json"]) if Path(record["regions_json"]).exists() else None,
        font_path=Path(font),
        original_image_path=Path(record["input_path"]) if Path(record["input_path"]).exists() else None,
        tracking_offset=tracking_offset,
        overlay_guidance_path=Path(record["overlay_guidance_json"]) if Path(record["overlay_guidance_json"]).exists() else None,
        overlay_layout_output=Path(record["overlay_layout_json"]),
    )
    if log_callback is not None:
        log_callback(f"  ✅ overlay 렌더 완료: {count}개")
    clear_export_recovery_metadata(record)


def merge_overlay_guidance(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {**(existing or {}), **(incoming or {})}
    existing_regions = existing.get("regions", {}) if isinstance(existing, dict) else {}
    incoming_regions = incoming.get("regions", {}) if isinstance(incoming, dict) else {}
    regions: dict[str, Any] = {}
    if isinstance(existing_regions, dict):
        regions.update(existing_regions)
    if isinstance(incoming_regions, dict):
        for key, value in incoming_regions.items():
            previous = regions.get(key, {})
            if isinstance(previous, dict) and isinstance(value, dict):
                regions[key] = {**previous, **value}
            else:
                regions[key] = value
    merged["regions"] = regions
    return merged


def run_final_for_item(
    session_id: str,
    item: dict[str, Any],
    extra_prompt: str,
    font_path: str | None,
    tracking_offset: int | None = None,
    overlay_guidance: dict[str, Any] | None = None,
    force: bool = False,
    log_callback=None,
    ocr_model: str | None = None,
) -> dict[str, Any]:
    record = image_record(session_id, Path(item["input_path"]), item.get("image_sha256"))
    clusters = current_clusters_path(record, record["id"])
    font = font_path or DEFAULT_FONT
    if overlay_guidance is not None:
        existing_overlay_guidance = load_json(Path(record["overlay_guidance_json"]), default={})
        write_json(Path(record["overlay_guidance_json"]), merge_overlay_guidance(existing_overlay_guidance, overlay_guidance))
    if force:
        for key in ("integrated_json", "final_jpg", "overlay_layout_json", "ocr_status_json", "export_recovery_json"):
            path = Path(record[key])
            if path.exists() or path.is_symlink():
                path.unlink()

    analysis_state = analysis_state_from_record(record)
    skip_reason = analysis_state.get("reason")
    if skip_reason:
        missing = analysis_state.get("missing_inputs") or []
        mark_ocr_skipped(record, skip_reason, missing, analysis_state)
        if log_callback is not None:
            suffix = f"({', '.join(missing)})" if missing else ""
            log_callback(f"{ocr_skip_message(skip_reason)} {suffix}".strip())
        return image_record(session_id, Path(item["input_path"]), record.get("image_sha256"))

    ocr_cmd = [
        PYTHON,
        "-m",
        "pipeline.gemini_ocr_translate_v5",
        record["input_path"],
        str(clusters),
        record["integrated_json"],
        "--ocr-sheet-image",
        record["indexed_sheet_jpg"],
        "--ocr-sheet-json",
        record["regions_json"],
        # '-반말로'처럼 '-'로 시작하는 프롬프트도 값으로 넘어가도록 '=' 형식을 쓴다.
        f"--extra-prompt={extra_prompt}",
        *(["--model", ocr_model] if ocr_model else []),
    ]
    if log_callback is None:
        run_subprocess(ocr_cmd, timeout=360)
    else:
        log_callback("Step 4 Gemini OCR/번역 시작")
        run_subprocess_stream(ocr_cmd, timeout=360, log_callback=log_callback)
        log_callback("Step 6 Text Overlay 시작")
    render_overlay_for_record(record, font, tracking_offset, log_callback=log_callback)
    mark_ocr_status(record, "done", "OCR/번역 완료", "integrated.json 생성 완료")
    return image_record(session_id, Path(item["input_path"]))


def stream_run_final(req: RunFinalRequest):
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def emit(payload: dict[str, Any]) -> None:
        event_queue.put(payload)

    def run_one(item: dict[str, Any], index: int, total: int) -> bool:
        image_id = item["id"]
        name = item.get("name") or image_id
        def log_emit(message: str) -> None:
            detail = str(message).strip()
            emit({"type": "log", "image_id": image_id, "message": f"[{name}] {detail}"})
            if detail:
                emit({"type": "image_status", "image_id": image_id, "name": name, "status": "running", "detail": detail[:120]})

        try:
            emit({"type": "image_status", "image_id": image_id, "name": name, "status": "running", "detail": "OCR/번역 실행"})
            emit({"type": "log", "image_id": image_id, "message": f"[{index}/{total}] {name} OCR/번역 시작"})
            record = image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256"))
            refreshed = run_final_for_item(
                req.session_id,
                item,
                req.extra_prompt,
                req.font_path,
                req.tracking_offset,
                req.overlay_guidance if req.image_id != "all" else None,
                force=req.force,
                log_callback=log_emit,
                ocr_model=req.ocr_model,
            )
            skip_detail = integrated_skip_detail(refreshed.get("integrated"))
            if skip_detail:
                emit({
                    "type": "image_status",
                    "image_id": image_id,
                    "name": name,
                    "status": "skipped",
                    "detail": skip_detail,
                    "image": refreshed,
                })
                emit({"type": "log", "image_id": image_id, "message": f"[{name}] {skip_detail}"})
                return True
            emit({
                "type": "image_status",
                "image_id": image_id,
                "name": name,
                "status": "done",
                "detail": "final ready",
                "image": refreshed,
            })
            emit({"type": "log", "image_id": image_id, "message": f"[{name}] OCR/번역 완료"})
            return True
        except Exception as exc:
            record = image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256"))
            mark_ocr_status(record, "failed", "실패", str(exc))
            emit({"type": "image_status", "image_id": image_id, "name": name, "status": "failed", "detail": "실패"})
            emit({"type": "final_error", "image_id": image_id, "message": f"[{name}] {exc}"})
            return False

    def run() -> None:
        try:
            data = ensure_session(req.session_id)
            if req.image_id == "all":
                targets = sorted_image_records(data.get("images", []))
            elif req.image_id == "failed":
                targets = [
                    item for item in sorted_image_records(data.get("images", []))
                    if (image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256")).get("ocr_status") or {}).get("status") == "failed"
                ]
            else:
                targets = [get_image(req.session_id, req.image_id)]
            total = len(targets)
            if req.image_id == "all" and not req.force:
                pending_targets = []
                skipped_complete = 0
                for item in targets:
                    record = image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256"))
                    if ocr_completed_for_record(record):
                        skipped_complete += 1
                    else:
                        pending_targets.append(item)
                if skipped_complete:
                    emit({"type": "log", "message": f"이미 OCR 결과가 있는 {skipped_complete}개 파일은 건너뜁니다."})
                targets = pending_targets
                total = len(targets)
            if total == 0:
                emit({"type": "log", "message": "OCR/번역할 이미지가 없습니다."})
                emit({"type": "complete", "ok": True, "images": session_image_summaries(req.session_id)})
                return
            workers = max(1, min(req.max_workers, total))
            emit({"type": "log", "message": f"OCR/Translation worker {workers}개로 실행"})
            for item in targets:
                emit({"type": "image_status", "image_id": item["id"], "name": item["name"], "status": "queued", "detail": "대기"})
            ok = True
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(run_one, item, index, total): item
                    for index, item in enumerate(targets, start=1)
                }
                for future in as_completed(futures):
                    ok = future.result() and ok
            emit({"type": "complete", "ok": ok, "images": session_image_summaries(req.session_id)})
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
        finally:
            event_queue.put(None)

    threading.Thread(target=run, daemon=True).start()

    while True:
        event = event_queue.get()
        if event is None:
            break
        yield json.dumps(event, ensure_ascii=False) + "\n"


def clear_guidance_outputs(session_id: str, image_id: str) -> dict[str, Any]:
    source = get_image(session_id, image_id)
    image_hash = source.get("image_sha256") or ensure_record_hash(source)
    record = image_record(session_id, Path(source["input_path"]), image_hash)
    clear_guidance_cache(image_hash)
    mark_guidance_cleared(record)

    paths_to_delete = [
        record["guidance_json"],
        record["guidance_report_json"],
        record["effective_text_groups_json"],
        record["regions_json"],
        record["indexed_sheet_jpg"],
        record["masked_jpg"],
        record["bubble_vis_jpg"],
        record["integrated_json"],
        record["ocr_status_json"],
        record["final_jpg"],
        record["overlay_layout_json"],
        record["export_recovery_json"],
    ]
    for raw_path in paths_to_delete:
        path = Path(raw_path)
        if path.exists():
            path.unlink()

    mask_dir = Path(record["work_dir"]) / "masks"
    if mask_dir.exists():
        for mask_path in mask_dir.glob(f"{Path(record['input_path']).stem}_R*_mask.png"):
            mask_path.unlink()

    return image_record(session_id, Path(source["input_path"]), image_hash)


def build_session_export_zip(session_id: str) -> Path:
    data = ensure_session(session_id)
    base = session_dir(data["session_id"])
    export_dir = base / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    zip_path = export_dir / export_zip_filename(data.get("project_name", ""), data["session_id"])
    used_names: set[str] = set()

    def export_name_for(item: dict[str, Any], source_path: Path) -> str:
        base_name = safe_filename(item.get("name") or source_path.name)
        archive_path = Path("final") / f"{Path(base_name).stem}{source_path.suffix.lower() or Path(base_name).suffix.lower()}"
        candidate = archive_path.as_posix()
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index = 2
        while True:
            renamed = archive_path.with_name(f"{archive_path.stem}_{index}{archive_path.suffix}").as_posix()
            if renamed not in used_names:
                used_names.add(renamed)
                return renamed
            index += 1

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in sorted_image_records(data.get("images", [])):
            input_path = Path(item["input_path"])
            record = image_record(data["session_id"], input_path, item.get("image_sha256"))
            final_path = Path(record["final_jpg"])
            source_path = final_path if final_path.exists() else input_path
            if not source_path.exists():
                continue
            zf.write(source_path, export_name_for(item, source_path))
    return zip_path


def generate_missing_finals_with_openai(req: GenerateMissingFinalRequest) -> dict[str, Any]:
    data = ensure_session(req.session_id)
    requested_ids = set(req.image_ids or [])
    targets = [
        item for item in sorted_image_records(data.get("images", []))
        if (not requested_ids or item.get("id") in requested_ids)
    ]
    logs: list[str] = []
    generated: list[str] = []
    failed: list[dict[str, str]] = []
    lock = threading.Lock()

    def process_item(item: dict[str, Any]) -> tuple[str, str]:
        record = image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256"))
        if Path(record["final_jpg"]).exists() and not req.force:
            with lock:
                logs.append(f"[{record['name']}] final이 이미 있어 건너뜁니다.")
            return record["id"], "skipped"

        def emit(message: str, name: str = record["name"]) -> None:
            with lock:
                logs.append(f"[{name}] {message}")

        ok = redraw_image_with_openai(
            record["input_path"],
            record["final_jpg"],
            model_name=req.model or "gpt-image-2",
            prompt_guide=req.prompt_guide or "",
            text_model_name=req.text_model or "gpt-5.4-mini",
            analyze_first=req.analyze_first,
            quality=req.quality or "medium",
            size=req.size or "match_input",
            output_format=req.output_format or "jpeg",
            output_compression=req.output_compression,
            log_callback=emit,
        )
        if ok:
            write_export_recovery_generated(record, req)
            with lock:
                generated.append(record["id"])
            return record["id"], "generated"
        with lock:
            failed.append({"id": record["id"], "name": record["name"]})
        return record["id"], "failed"

    if targets:
        workers = max(1, min(req.max_workers, len(targets)))
        logs.append(f"이미지 생성 worker {workers}개로 실행")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_item, item) for item in targets]
            for future in as_completed(futures):
                future.result()
    return {
        "ok": not failed,
        "generated": generated,
        "failed": failed,
        "logs": "\n".join(logs),
        "images": session_image_summaries(req.session_id),
    }


def stream_generate_missing_finals(req: GenerateMissingFinalRequest):
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def emit(payload: dict[str, Any]) -> None:
        event_queue.put(payload)

    def run() -> None:
        try:
            data = ensure_session(req.session_id)
            requested_ids = set(req.image_ids or [])
            targets = [
                item for item in sorted_image_records(data.get("images", []))
                if (not requested_ids or item.get("id") in requested_ids)
            ]
            generated: list[str] = []
            failed: list[dict[str, str]] = []
            total = len(targets)
            if total == 0:
                emit({"type": "log", "message": "이미지 생성 대상이 없습니다."})
                emit({"type": "complete", "ok": True, "generated": [], "failed": [], "images": session_image_summaries(req.session_id)})
                return

            emit({"type": "log", "message": f"이미지 생성 후보 {total}개 처리 시작"})
            workers = max(1, min(req.max_workers, total))
            emit({"type": "log", "message": f"이미지 생성 worker {workers}개로 실행"})

            def process_item(index: int, item: dict[str, Any]) -> tuple[str, str, str]:
                record = image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256"))
                name = record["name"]
                if Path(record["final_jpg"]).exists() and not req.force:
                    emit({"type": "log", "image_id": record["id"], "message": f"[{name}] final이 이미 있어 건너뜁니다."})
                    return record["id"], name, "skipped"

                def log_callback(message: str, item_name: str = name, image_id: str = record["id"]) -> None:
                    emit({"type": "log", "image_id": image_id, "message": f"[{item_name}] {message}"})

                emit({"type": "log", "image_id": record["id"], "message": f"[{index}/{total}] {name} 이미지 생성 시작"})
                ok = redraw_image_with_openai(
                    record["input_path"],
                    record["final_jpg"],
                    model_name=req.model or "gpt-image-2",
                    prompt_guide=req.prompt_guide or "",
                    text_model_name=req.text_model or "gpt-5.4-mini",
                    analyze_first=req.analyze_first,
                    quality=req.quality or "medium",
                    size=req.size or "match_input",
                    output_format=req.output_format or "jpeg",
                    output_compression=req.output_compression,
                    log_callback=log_callback,
                )
                if ok:
                    write_export_recovery_generated(record, req)
                    emit({"type": "log", "image_id": record["id"], "message": f"[{name}] 이미지 생성 완료"})
                    return record["id"], name, "generated"
                emit({"type": "error", "image_id": record["id"], "message": f"[{name}] 이미지 생성 실패"})
                return record["id"], name, "failed"

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(process_item, index, item) for index, item in enumerate(targets, start=1)]
                for future in as_completed(futures):
                    image_id, name, state = future.result()
                    if state == "generated":
                        generated.append(image_id)
                    elif state == "failed":
                        failed.append({"id": image_id, "name": name})
            emit({
                "type": "complete",
                "ok": not failed,
                "generated": generated,
                "failed": failed,
                "images": session_image_summaries(req.session_id),
            })
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
        finally:
            event_queue.put(None)

    threading.Thread(target=run, daemon=True).start()

    while True:
        event = event_queue.get()
        if event is None:
            break
        yield json.dumps(event, ensure_ascii=False) + "\n"


def run_subprocess(cmd: list[str], timeout: int = 300) -> str:
    return run_subprocess_stream(cmd, timeout=timeout)


def run_subprocess_stream(cmd: list[str], timeout: int = 300, log_callback=None) -> str:
    # pipeline_runner와 같은 reader-thread 방식이라 Windows에서도 동작하고 timeout이 지켜진다.
    def on_line(line: str) -> None:
        message = line.rstrip()
        if message and log_callback is not None:
            log_callback(message)

    try:
        returncode, lines = stream_subprocess(cmd, timeout, on_line)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail=f"subprocess timeout after {timeout}s")
    combined = "\n".join(lines)
    if returncode != 0:
        raise HTTPException(status_code=500, detail=combined)
    return combined
class RevalidatingStaticFiles(StaticFiles):
    """UI 자산은 매 요청마다 재검증한다(바뀌지 않았으면 304).

    index.html이 버전 없는 /app.bundle.js를 참조하므로, 기본 휴리스틱 캐시를 두면
    번들을 다시 빌드한 뒤에도 브라우저가 옛 JS를 계속 실행한다.
    """

    def file_response(self, *args: Any, **kwargs: Any):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="Comic Translator Review UI")

    @app.post("/api/sessions/new")
    def create_session() -> dict[str, Any]:
        data = ensure_session()
        return {"session": data}

    @app.get("/api/sessions/recent")
    def recent_sessions() -> dict[str, Any]:
        return {"sessions": list_recent_sessions()}

    @app.get("/api/session/{session_id}")
    def read_session(session_id: str) -> dict[str, Any]:
        # 목록용 요약만 돌려준다. 페이지 상세(regions, integrated 등)는 /page/{image_id}로 받는다.
        data = ensure_session(session_id)
        images = session_image_summaries(session_id)
        return {"session": data, "images": images, "hydration_pending": load_hydration_pending(session_id)}

    @app.get("/api/session/{session_id}/page/{image_id}")
    def read_session_page(session_id: str, image_id: str) -> dict[str, Any]:
        return {"image": page_record(session_id, image_id)}

    @app.post("/api/session/meta")
    def update_session_meta(req: SessionMetaRequest) -> dict[str, Any]:
        data = ensure_session(req.session_id)
        data["project_name"] = req.project_name.strip()[:120]
        save_session(data)
        return {"session": data}

    @app.post("/api/session/delete")
    def delete_session_endpoint(req: SessionDeleteRequest) -> dict[str, Any]:
        delete_session(req.session_id)
        return {"ok": True, "sessions": list_recent_sessions()}

    @app.post("/api/import-path")
    def import_path(req: ImportPathRequest) -> dict[str, Any]:
        data = ensure_session(req.session_id)
        src = Path(req.path).expanduser()
        paths = scan_images(src, req.recursive)
        records = copy_images_into_session(paths, data["session_id"])
        return {"session_id": data["session_id"], "imported": len(records), "images": records}

    @app.post("/api/upload")
    def upload_files(session_id: str | None = None, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        # 동기 함수로 둬서 해시·이미지 처리 동안 이벤트 루프를 막지 않고 threadpool에서 실행되게 한다.
        data = ensure_session(session_id)
        input_dir = session_dir(data["session_id"]) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        taken_ids = {item["id"] for item in data.get("images", [])}
        copied: list[Path] = []
        sorted_uploads = sorted(files, key=lambda upload: natural_name_key(upload.filename or ""))
        for index, upload in enumerate(sorted_uploads, start=1):
            name = safe_filename(upload.filename or f"upload_{index}.jpg")
            if Path(name).suffix.lower() not in IMAGE_EXTS:
                continue
            target = allocate_input_path(input_dir, name, index, taken_ids)
            # "xb": 기존 파일이나 symlink를 따라가 덮어쓰지 않는다.
            with target.open("xb") as f:
                shutil.copyfileobj(upload.file, f)
            copied.append(target)
        records = []
        current = ensure_session(data["session_id"])
        known = {item["id"] for item in current.get("images", [])}
        for path in copied:
            if path.stem in known:
                continue
            image_hash, _cache_path = link_cached_input(path, path)
            item = image_manifest_item(path, image_hash)
            current.setdefault("images", []).append(item)
            records.append(item)
        save_session(current)
        hydrate_load_cache_for_records(data["session_id"], records, sync_limit=IMPORT_CACHE_SYNC_LIMIT)
        return {"session_id": data["session_id"], "imported": len(records), "images": sorted_image_records(records)}

    @app.post("/api/image/remove")
    def remove_image(req: RemoveImageRequest) -> dict[str, Any]:
        images = remove_session_image(req.session_id, req.image_id)
        return {"ok": True, "session": ensure_session(req.session_id), "images": images, "sessions": list_recent_sessions()}

    @app.post("/api/analyze")
    def analyze(req: AnalyzeRequest) -> dict[str, Any]:
        data = ensure_session(req.session_id)
        targets = sorted_image_records(data.get("images", [])) if req.image_id == "all" else [get_image(req.session_id, req.image_id)]
        logs: list[str] = []
        for item in targets:
            image_hash = ensure_record_hash(item)
            record = image_record(req.session_id, Path(item["input_path"]), image_hash)
            if hydrate_analysis_cache(record, image_hash, req.force, logs.append, allow_backfill=True):
                reapply_guidance_if_present(req.session_id, record, logs.append)
                continue
            hydrate_cv_cache(record, image_hash, req.force, logs.append)
            _unlink_symlinked_analysis_outputs(record)
            ok, log = run_single_image(
                record["input_path"],
                record["work_dir"],
                record["final_jpg"],
                force=req.force,
                stop_after="pre_ocr",
            )
            logs.append(log)
            if not ok:
                raise HTTPException(status_code=500, detail=log)
            store_cv_cache(record, image_hash)
            store_analysis_cache(record, image_hash)
            clear_guidance_cleared_marker(record)
            reapply_guidance_if_present(req.session_id, record, logs.append)
        save_session(data)
        images = session_image_summaries(req.session_id)
        return {"ok": True, "logs": "\n\n".join(logs), "images": images}

    @app.post("/api/analyze-stream")
    def analyze_stream(req: AnalyzeRequest) -> StreamingResponse:
        return StreamingResponse(stream_analyze(req), media_type="application/x-ndjson")

    @app.post("/api/guidance")
    def apply_guidance(req: GuidanceRequest) -> dict[str, Any]:
        refreshed = run_step3_with_effective_text_groups(req.session_id, req.image_id, req.guidance)
        source = get_image(req.session_id, req.image_id)
        image_hash = source.get("image_sha256") or ensure_record_hash(source)
        store_guidance_cache(refreshed, image_hash)
        store_guidance_output_cache(refreshed, image_hash, req.guidance)
        return {"ok": True, "image": refreshed}

    @app.post("/api/guidance/clear")
    def clear_guidance(req: ClearGuidanceRequest) -> dict[str, Any]:
        refreshed = clear_guidance_outputs(req.session_id, req.image_id)
        return {"ok": True, "image": refreshed}

    @app.post("/api/run-final")
    def run_final(req: RunFinalRequest) -> dict[str, Any]:
        source = get_image(req.session_id, req.image_id)
        try:
            refreshed = run_final_for_item(
                req.session_id,
                source,
                req.extra_prompt,
                req.font_path,
                req.tracking_offset,
                req.overlay_guidance,
                req.force,
                ocr_model=req.ocr_model,
            )
        except Exception as exc:
            record = image_record(req.session_id, Path(source["input_path"]), source.get("image_sha256"))
            mark_ocr_status(record, "failed", "실패", str(exc))
            raise
        return {"ok": True, "image": refreshed}

    @app.post("/api/run-final-stream")
    def run_final_stream(req: RunFinalRequest) -> StreamingResponse:
        return StreamingResponse(stream_run_final(req), media_type="application/x-ndjson")

    @app.post("/api/overlay/apply")
    def apply_overlay(req: OverlayRequest) -> dict[str, Any]:
        source = get_image(req.session_id, req.image_id)
        # 렌더에는 경로만 필요하다. 원본 전체를 여는 여백 probe는 하지 않고 manifest 크기 정보를 쓴다.
        record = image_record(req.session_id, Path(source["input_path"]), source.get("image_sha256"), source, probe_image=False, include_detail=False)
        existing_overlay_guidance = load_json(Path(record["overlay_guidance_json"]), default={})
        write_json(Path(record["overlay_guidance_json"]), merge_overlay_guidance(existing_overlay_guidance, req.overlay_guidance))
        render_overlay_for_record(record, req.font_path, req.tracking_offset)
        return {"ok": True, "image": page_record(req.session_id, req.image_id)}

    @app.get("/api/session/{session_id}/export")
    def export_session(session_id: str) -> FileResponse:
        zip_path = build_session_export_zip(session_id)
        return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)

    @app.post("/api/export/generate-missing")
    def generate_missing_final_images(req: GenerateMissingFinalRequest) -> dict[str, Any]:
        return generate_missing_finals_with_openai(req)

    @app.post("/api/export/generate-missing/stream")
    def generate_missing_final_images_stream(req: GenerateMissingFinalRequest) -> StreamingResponse:
        return StreamingResponse(stream_generate_missing_finals(req), media_type="application/x-ndjson")

    @app.get("/api/file")
    def get_file(path: str) -> FileResponse:
        # 세션·캐시 폴더 아래의 이미지만 내려준다. 저장소 루트 전체를 열면 .env나 인증서 파일까지 읽힌다.
        resolved = Path(path).resolve()
        servable_roots = (SESSION_ROOT, INPUT_CACHE_DIR, ANALYSIS_CACHE_DIR)
        if resolved.suffix.lower() not in IMAGE_EXTS or not any(
            resolved.is_relative_to(root.resolve()) for root in servable_roots
        ):
            raise HTTPException(status_code=403, detail="File outside workspace is not served")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(resolved)

    @app.get("/api/ocr-models")
    def list_ocr_models() -> dict[str, Any]:
        """Step 4에서 선택 가능한 OCR/번역 모델 목록."""
        from pipeline.gemini_ocr_translate_v5 import MODEL_NAME, SUPPORTED_OCR_MODELS

        return {"default": MODEL_NAME, "models": SUPPORTED_OCR_MODELS}

    @app.get("/api/fonts")
    def list_fonts() -> dict[str, Any]:
        fonts_dir = ROOT / "Fonts"
        fonts = sorted([*fonts_dir.glob("*.ttf"), *fonts_dir.glob("*.otf")]) if fonts_dir.exists() else []
        return {
            "default": DEFAULT_FONT,
            "fonts": [{"name": path.stem, "path": str(path)} for path in fonts],
        }

    fonts_dir = ROOT / "Fonts"
    if fonts_dir.exists():
        app.mount("/fonts", StaticFiles(directory=str(fonts_dir)), name="fonts")
    app.mount("/", RevalidatingStaticFiles(directory=str(UI_DIR), html=True), name="ui")
    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Comic Translator Review UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7870)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
