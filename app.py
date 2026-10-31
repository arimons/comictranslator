#!/usr/bin/env python3
"""Comic Translator review UI.

This is a local-only web app for reviewing TG anchors and Step 3 regions before
OpenAI OCR. It intentionally lives beside the Gradio wrapper instead of
replacing it.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import filecmp
import hashlib
import json
import os
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
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from pipeline.project_paths import manifest_files, manifest_path, project_base
from pipeline.manual_guidance import build_effective_text_groups, load_json, write_json
from pipeline.region_refs import (
    SCHEME as REGION_REF_SCHEME,
    annotate_regions,
    dual_write_guidance,
    legacy_key_map,
    segment_index_by_ref,
    normalize_overlay_guidance,
    project_page,
    reconcile_overlay_guidance,
    reconcile_speakers,
    tg_digest,
)
from pipeline.region_types import region_binding_key, regions_with_processing_basis
from pipeline.source_library import SourceLibrary, atomic_link, digest as source_digest
from pipeline.page_preparation import build_plan, is_split_page, split_in_place
from pipeline.image_generation_bridge import ImageGenerationStore
from pipeline.openai_image_redraw import redraw_image_with_openai
from pipeline.openai_tasks import IMAGE_MODEL
from pipeline.pipeline_runner import DEFAULT_FONT, run_single_image, stream_subprocess
from pipeline.render_text_overlay import apply_text_overlay
from pipeline.work_glossary import (NARRATION, GlossaryStore, character_map, cv_page_text, glossary_snapshot, match_works,
                                    page_region_contexts, page_speakers, region_context_prompt, speaker_prompt,
                                    REGION_CONTEXT_LIMIT)

load_dotenv()

from pipeline.paths import (REPO_ROOT as ROOT, WEB_DIR as UI_DIR, PROJECTS_DIR as SESSION_ROOT,
                            INPUT_CACHE_DIR, CV_CACHE_DIR, ANALYSIS_CACHE_DIR, GLOSSARY_DIR, EXPORTS_DIR, DATA_HOME, export_directory)
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
_GLOSSARY_STORES: dict[str, GlossaryStore] = {}
_GLOSSARY_STORE_LOCK = threading.Lock()


def glossary_store() -> GlossaryStore:
    root = GLOSSARY_DIR
    with _GLOSSARY_STORE_LOCK:
        key = str(root)
        if key not in _GLOSSARY_STORES:
            _GLOSSARY_STORES[key] = GlossaryStore(root)
        return _GLOSSARY_STORES[key]


def glossary_binding_path(session_id: str) -> Path:
    return session_dir(session_id) / "work" / "glossary_binding.json"


def glossary_payload(session_id: str) -> dict:
    # 조회가 빈 프로젝트를 만들지 않도록 ensure_session을 쓰지 않는다.
    if not session_file(session_id).is_file():
        raise ValueError("프로젝트를 찾을 수 없습니다.")
    binding = load_json(glossary_binding_path(session_id), default={})
    glossary = session_glossary(session_id)
    # 저장된 CV만으로 후보를 계산한다(연결 후에도 '변경'에서 보여 준다). 연결은 편집기가 POST로 한다.
    session = load_json(session_file(session_id), default={})
    return {"glossary": glossary, "works": glossary_store().list_works(),
            "binding": {key: binding.get(key) for key in ("matched_by", "hits")} if binding else None,
            "match": match_works(cv_page_text(session, session_dir(session_id) / "work"), glossary_store().all_memos())}


def speakers_path(session_id: str, image_id: str) -> Path:
    if not re.fullmatch(r"[\w.-]+", image_id):
        raise ValueError("잘못된 페이지 ID입니다.")
    return session_dir(session_id) / "work" / "speakers" / f"{image_id}.json"


def page_speaker_map(session_id: str, image_id: str) -> dict[str, str]:
    return load_json(speakers_path(session_id, image_id), default={}).get("by_tg", {})


def _annotated_page_regions(record_or_regions_json: Any, guidance: dict | None = None) -> list[dict[str, Any]]:
    """regions.json의 영역에 region_ref 등을 붙여 돌려준다(읽기 전용)."""
    payload = load_json(Path(record_or_regions_json), default={})
    raw = payload.get("regions") if isinstance(payload, dict) else None
    return annotate_regions(raw if isinstance(raw, list) else [], guidance)


def reconciled_speaker_store(record: dict[str, Any], stored: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """화자·번역 메모 저장소를 현재 영역 기준으로 맞춘 사본과 보고, 영역 참조가 붙은 현재 영역을 돌려준다. 파일은 쓰지 않는다."""
    guidance = load_json(Path(record["guidance_json"]), default={}) if record.get("guidance_json") else {}
    regions = _annotated_page_regions(record["regions_json"], guidance if isinstance(guidance, dict) else None)
    if stored is None:
        stored = load_json(speakers_path(record["session_id"], record["id"]), default={}) if record.get("session_id") else {}
    store, report = reconcile_speakers(stored, regions)
    return store, report, regions


def _speaker_stash(stored: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    """합침 때 보관된 서로 다른 번역 메모. 저장 경로가 `{**stored}`로 쓰므로 reconcile 결과의 보관분을 함께 실어 준다."""
    merged = {**(stored.get("unmatched_context_by_region_ref") or {}), **(store.get("unmatched_context_by_region_ref") or {})}
    return {"unmatched_context_by_region_ref": merged} if merged else {}


def session_glossary(session_id: str) -> dict | None:
    binding = load_json(glossary_binding_path(session_id), default={})
    return glossary_store().read(binding["work_id"]) if binding.get("work_id") else None


def session_glossary_snapshot(session_id: str) -> dict:
    data = session_glossary(session_id)
    return glossary_snapshot(data)


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


class PagePreparationRequest(BaseModel):
    session_id: str
    revision: str | None = None


class PageSplitOverrideRequest(BaseModel):
    session_id: str
    image_id: str
    # auto: 자동 판정, keep: 가로 그대로, split: split_x에서 직접 나누기
    mode: str
    split_x: int | None = None


class PageSplitRequest(BaseModel):
    session_id: str
    # 분석·편집·번역 흔적이 있는 가로 페이지도 나눈다. 나누면 그 흔적은 원래 페이지에 남고 새 페이지에는 이어지지 않는다.
    include_worked: bool = False


def page_has_work(session_id: str, item: dict[str, Any]) -> bool:
    """가이드 편집·번역 결과가 있거나 처리 제외한 페이지. 나누면 그 작업이 새 페이지로 이어지지 않는다.

    자동 분석 결과만 있는 페이지는 작업 흔적으로 보지 않는다. 조각은 원본 CV를 잘라 다시 분석하므로 잃는 것이 없다.
    """
    record = image_record(session_id, Path(item["input_path"]), item.get("image_sha256"))
    return page_processing_excluded(session_id, item["id"]) or any(Path(record[key]).exists() for key in ("guidance_json", "integrated_json"))


def split_wide_pages_in_session(session_id: str, include_worked: bool = False) -> dict[str, Any]:
    """같은 프로젝트 안에서 가로 펼침을 오른쪽 → 왼쪽 두 쪽으로 나눈다(표지처럼 가운데 한 장은 가운데만 남긴다).

    원본 파일은 그대로 두고 프로젝트에 나눈 PNG를 저장한다. 작업 흔적이 있는 페이지는 기본으로 건너뛴다.
    """
    data = ensure_session(session_id)
    candidates = [item for item in sorted_image_records(data.get("images", [])) if not is_split_page(item)]
    plan = build_plan(candidates, numbered=False)
    by_id = {item["id"]: item for item in candidates}
    to_split: list[dict[str, Any]] = []
    skipped_worked: list[str] = []
    center_kept = 0
    for page in plan["pages"]:
        if page["mode"] == "keep":
            continue
        item = by_id[page["image_id"]]
        if not include_worked and page_has_work(session_id, item):
            skipped_worked.append(item.get("name") or item["id"])
            continue
        to_split.append(item)
        center_kept += page["mode"] == "center_crop"
    created: list[dict[str, Any]] = []
    if to_split:
        records = split_in_place(to_split, session_dir(session_id) / "input")
        try:
            split_ids = {item["id"] for item in to_split}
            created = [
                image_manifest_item(record["path"], file_sha256(record["path"]), {key: value for key, value in record.items() if key != "path"})
                for record in records
            ]
            data["images"] = [item for item in data.get("images", []) if item["id"] not in split_ids] + created
            data["page_preparation"] = {"reading_order": "rtl", "split_source_count": len(to_split), "output_count": len(created)}
            save_session(data)
        except Exception:
            for record in records:
                record["path"].unlink(missing_ok=True)
            raise
        hydrate_load_cache_for_records(session_id, created, sync_limit=IMPORT_CACHE_SYNC_LIMIT)
    return {
        "ok": True,
        "split_pages": len(to_split),
        "created": len(created),
        "center_kept": center_kept,
        "skipped_worked": skipped_worked,
        "images": session_image_summaries(session_id),
    }


class ImportPathRequest(BaseModel):
    path: str
    session_id: str | None = None
    recursive: bool = True


class SessionMetaRequest(BaseModel):
    session_id: str
    project_name: str = ""


class GlossaryBindRequest(BaseModel):
    session_id: str
    work_id: str | None = None
    title: str = ""
    matched_by: str = "user"
    hits: list[str] = []


class GlossarySaveRequest(BaseModel):
    session_id: str
    work_id: str
    title: str | None = None
    characters: list[dict] = []
    terms: list[dict] = []
    notes: str = ""


class GlossaryDraftRequest(BaseModel):
    session_id: str
    work_id: str
    url: str
    text: str = ""


class SpeakerRequest(BaseModel):
    session_id: str
    image_id: str
    tg_ids: list[int] = []
    region_id: int | None = None
    region_key: str | None = None
    region_ref: str | None = None
    character_id: str | None = None


class RegionContextRequest(BaseModel):
    session_id: str
    image_id: str
    region_id: int | None = None
    region_key: str | None = None
    region_ref: str | None = None
    text: str = ""


class PageRefRequest(BaseModel):
    session_id: str
    image_id: str


class SessionDeleteRequest(BaseModel):
    session_id: str


class AnalyzeRequest(BaseModel):
    session_id: str
    image_id: str = "all"
    force: bool = False
    max_workers: int = DEFAULT_ANALYZE_WORKERS
    # "text_groups"면 Step 1-2(CV·글자 묶음)까지만 한다. Step 3(LaMa 지우기)는 /api/guidance가 영역 확정 뒤에 돌린다.
    stop_after: str = "pre_ocr"


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
    # True면 overlay_guidance를 기존 파일과 병합하지 않고 그대로 저장한다(OverlayRequest.replace와 같은 의미).
    replace: bool = False
    # image_id가 "batch"일 때 처리할 페이지 목록. 확인한 페이지를 모아 백그라운드로 번역할 때 쓴다.
    image_ids: list[str] = []


class OverlayRequest(BaseModel):
    session_id: str
    image_id: str
    overlay_guidance: dict[str, Any]
    font_path: str | None = None
    tracking_offset: int | None = None
    # True면 기존 파일과 병합하지 않고 그대로 저장한다. 편집기가 전체 상태를 보낼 때 쓰며,
    # 실행 취소·수동값 초기화로 지운 키가 병합 때문에 되살아나지 않게 한다.
    replace: bool = False


class RestoreAutoMaskRequest(BaseModel):
    session_id: str
    image_id: str
    dilate_px: int = 4
    polarity: str = "auto"
    threshold: int | None = None
    min_area: int = 10
    use_region_seeds: bool = True
    extra_seed_rects: list[list[int]] = []
    save: bool = True  # False면 저장하지 않고 PNG만 돌려준다(편집 중인 마스크에 합칠 때)


class RestoreMaskRequest(BaseModel):
    session_id: str
    image_id: str
    mask_png: str


class RestoreRunRequest(BaseModel):
    session_id: str
    image_id: str
    engine: str = "lama"
    context_rect: list[int]
    mask_png: str | None = None
    resolution: str = "original"
    feather_px: int = 0
    source: str = "masked"


class RestoreAcceptRequest(BaseModel):
    session_id: str
    image_id: str
    job_id: str


class AiEraseRequest(BaseModel):
    session_id: str
    image_id: str
    quality: str | None = None  # 비우면 모델별 기본값: gpt-image medium, Gemini low(1K가 반투명 말풍선을 주변 색에 더 잘 맞춤)
    ocr_model: str | None = None  # 세션의 OCR·번역 모델. gemini-*이면 Gemini 이미지 모델로 지운다


class RestorePatchRemoveRequest(BaseModel):
    session_id: str
    image_id: str
    patch_id: str


class GenerateMissingFinalRequest(BaseModel):
    session_id: str
    image_ids: list[str] = []
    model: str = IMAGE_MODEL
    prompt_guide: str = ""
    analyze_first: bool = True
    text_model: str = "gpt-6.1-terra"
    quality: str = "medium"
    size: str = "match_input"
    output_format: str = "jpeg"
    output_compression: int | None = 92
    max_workers: int = DEFAULT_ANALYZE_WORKERS
    force: bool = False


class SourceFolderRequest(BaseModel):
    session_id: str
    folder: str
    revision: str | None = None


def now_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def session_dir(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in {"_", "-"})
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid session id")
    return SESSION_ROOT / safe


def source_library():
    return SourceLibrary(SESSION_ROOT.parent)


def session_file(session_id: str) -> Path:
    return manifest_path(session_dir(session_id))


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
    return source_library().refresh(data, base)


def save_session(data: dict[str, Any]) -> None:
    data["images"] = sorted_image_records(data.get("images", []))
    portable = {**data, "images": [{k:v for k,v in p.items() if k != "source_status"} for p in data["images"]]}
    write_json(session_dir(data["session_id"]) / "project.json", portable)


def delete_session(session_id: str) -> None:
    base = session_dir(session_id)
    if base.exists():
        shutil.rmtree(base)


def session_home_progress(base: Path, images: list[dict[str, Any]], updated_ts: float) -> dict[str, Any]:
    """홈 요약은 파일 존재·시각만 읽는다. 결과 생성과 수동 검토 완료를 혼동하지 않는다."""
    analysis_count = final_count = 0
    for image in images:
        stem = Path(image.get("input_path") or image.get("name") or image["id"]).stem
        json_dir = base / "work" / "json"
        tracked = {
            "analysis": json_dir / f"{stem}_regions.json",
            "final": base / "output" / "final" / f"{stem}_final.jpg",
            "integrated": json_dir / f"{stem}_integrated.json",
            "guidance": json_dir / f"{stem}_manual_guidance.json",
            "overlay": json_dir / f"{stem}_overlay_guidance.json",
            "restore": base / "work" / "restore" / stem / "state.json",
        }
        for kind, path in tracked.items():
            try:
                stat = path.stat()
                if not path.is_file():
                    continue
            except OSError:
                continue
            updated_ts = max(updated_ts, stat.st_mtime)
            if kind == "analysis":
                analysis_count += 1
            elif kind == "final":
                final_count += 1
    return {"analysis_count": analysis_count, "final_count": final_count, "_updated_ts": updated_ts}


def list_recent_sessions(limit: int = 10) -> list[dict[str, Any]]:
    raw_sessions: list[dict[str, Any]] = []
    for session_json in manifest_files(SESSION_ROOT):
        data = load_json(session_json, default={})
        images = sorted_image_records(data.get("images", []))
        if not images:
            continue
        progress = session_home_progress(session_json.parent, images, session_json.stat().st_mtime)
        mtime = progress["_updated_ts"]
        image_hashes = [item.get("image_sha256") for item in images if item.get("image_sha256")]
        image_hash_set = set(image_hashes)
        first_input = Path(images[0].get("input_path", ""))
        raw_sessions.append({
            "session_id": data.get("session_id") or session_json.parent.name,
            "project_name": data.get("project_name", ""),
            "source_folder_name": data.get("source_folder_name", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            "image_count": len(images),
            "first_image": images[0].get("name", ""),
            "thumbnail_url": file_url(first_input),
            "_updated_ts": mtime,
            "_image_hashes": image_hash_set,
            **progress,
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
    if item.get("source_status") == "missing":raise HTTPException(status_code=409,detail="원본 폴더를 다시 연결하세요")
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
    # 임시 작업 폴더에서 완성한 JSON도 프로젝트 저장 경계에서 상대 경로로 바꾼다.
    # byte 복사만 하면 임시 출력 단계의 절대 경로가 프로젝트에 다시 유입된다.
    if src.suffix == ".json" and (project_base(src) is not None or project_base(dest) is not None):
        sentinel = object()
        payload = load_json(src, sentinel)
        if payload is not sentinel:
            write_json(dest, payload)
            return
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
        dest.symlink_to(os.path.relpath(src.resolve(), dest.parent.resolve()))
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
                if dest.is_symlink():
                    return False
                if src.suffix == ".json":
                    # 저장 표현(상대/절대)과 들여쓰기는 달라도 실행 내용이 같으면 재복사하지 않는다.
                    if load_json(src) != load_json(dest):
                        return False
                elif not filecmp.cmp(src, dest, shallow=False):
                    return False
            except OSError:
                return False
    return True


def backfill_analysis_cache_from_sessions(image_hash: str) -> bool:
    if not SESSION_ROOT.exists():
        return False
    for session_json in manifest_files(SESSION_ROOT):
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
    if record.get("analysis_route") == "standard":
        return False
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
    if record.get("analysis_route") == "standard":
        return False
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
    for session_json in manifest_files(SESSION_ROOT):
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
    if record.get("analysis_route") == "standard":
        return False
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
    if record.get("analysis_route") == "standard":
        return False
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
            "region_suppression_version": regions.get("region_suppression_version", 0) if isinstance(regions, dict) else 0,
        },
    )
    return changed


def hydrate_guidance_output_cache(record: dict[str, Any], image_hash: str, guidance: dict[str, Any], log_callback) -> bool:
    if record.get("analysis_route") == "standard":
        return False
    if not guidance_has_edits(guidance):
        return False
    cache_dir = guidance_output_cache_dir(image_hash, guidance)
    meta = load_json(cache_dir / "meta.json", default={})
    if meta.get("analysis_cache_version") not in ANALYSIS_CACHE_COMPATIBLE_VERSIONS:
        return False
    if meta.get("guidance_digest") != guidance_digest(guidance):
        return False
    if guidance.get("suppressed_regions") and meta.get("region_suppression_version") != 1:
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


def crop_parent_cv_rows(rows: list[dict[str, Any]], crop_rect: list[int], size: tuple[int, int] | None = None) -> list[dict[str, Any]]:
    """원본(분할 전) CV 결과를 조각 좌표로 옮긴다. box_2d는 [ymin, xmin, ymax, xmax] 픽셀이다.

    글자 상자의 중심이 조각 안에 있으면 그 조각 것으로 보고, 조각 경계에서 자른다.
    조각 이미지가 원본 crop과 다른 크기로 저장됐으면 비율대로 맞춘다."""
    x0, y0, cw, ch = (int(v) for v in crop_rect)
    sx = size[0] / cw if size and cw else 1.0
    sy = size[1] / ch if size and ch else 1.0
    out = []
    for row in rows:
        box = row.get("box_2d") if isinstance(row, dict) else None
        if not isinstance(box, list) or len(box) != 4:
            continue
        ymin, xmin, ymax, xmax = (float(v) for v in box)
        cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
        if not (x0 <= cx < x0 + cw and y0 <= cy < y0 + ch):
            continue
        nx1, ny1 = max(0.0, xmin - x0), max(0.0, ymin - y0)
        nx2, ny2 = min(float(cw), xmax - x0), min(float(ch), ymax - y0)
        if nx2 <= nx1 or ny2 <= ny1:
            continue
        out.append({**row, "box_2d": [round(ny1 * sy), round(nx1 * sx), round(ny2 * sy), round(nx2 * sx)]})
    return out


def hydrate_cv_cache(record: dict[str, Any], image_hash: str, force: bool, log_callback, item: dict[str, Any] | None = None) -> bool:
    cache = cv_cache_path(image_hash)
    cv_json = Path(record["cv_json"])
    if force:
        # 다시 분석이어도 분할 조각은 원본 CV를 잘라 쓴다(같은 글자를 다시 사는 셈이라). 조각 자신의 캐시만 건너뛴다.
        if cv_json.exists() or cv_json.is_symlink():
            cv_json.unlink()
    elif cv_json.exists():
        return False
    if not force and cache.exists():
        cv_json.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache, cv_json)
        log_callback("  ♻️ CV 전역 캐시 사용")
        return True
    # 가로 이미지를 나눈 조각: 분할 전 원본의 CV가 있으면 잘라 쓴다(새 Cloud Vision 요청 없음).
    # 일반 분석에서는 TG가 아직 없는 페이지에만 넣는다. 이미 만든 TG가 있으면 Step 2가 그것을 재사용하므로
    # CV만 바꾸면 결과가 어긋난다. 그런 페이지는 '처음부터 다시 분석'(force)이 Step 2-3까지 다시 만든다.
    if not force and Path(record["text_groups_json"]).exists():
        return False
    meta = item or record
    parent_hash, crop_rect = meta.get("source_image_sha256"), meta.get("crop_rect")
    if not parent_hash or parent_hash == image_hash or not isinstance(crop_rect, list) or len(crop_rect) != 4:
        return False
    parent_cache = cv_cache_path(str(parent_hash))
    rows = load_json(parent_cache, default=None) if parent_cache.exists() else None
    if not isinstance(rows, list):
        return False
    size = (int(meta["width"]), int(meta["height"])) if meta.get("width") and meta.get("height") else None
    cropped = crop_parent_cv_rows(rows, crop_rect, size)
    write_json(cv_json, cropped)
    log_callback(f"  ♻️ 분할 전 원본 CV 재사용 (글자 {len(cropped)}/{len(rows)}개)")
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
    if page_processing_excluded(session_id, item["id"]):
        return
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


def page_processing_excluded(session_id: str, image_id: str) -> bool:
    if not image_id or any(ch in image_id for ch in ("/", chr(92), chr(0))) or image_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid image id")
    policy = load_json(session_dir(session_id) / "work/page_policy" / f"{image_id}.json", default={})
    return policy.get("excluded") is True


def page_analysis_route(session_id: str, image_id: str) -> str:
    # 페이지 제외와 같은 프로젝트 로컬 정책. 기존 JSON에는 키가 없어도 된다.
    page_processing_excluded(session_id, image_id)
    policy = load_json(session_dir(session_id) / "work/page_policy" / f"{image_id}.json", default={})
    return "standard" if policy.get("analysis_route") == "standard" else "auto"


class PageAnalysisRouteRequest(BaseModel):
    session_id: str
    image_id: str
    route: Literal["standard", "auto"]


class PageProcessingRequest(BaseModel):
    session_id: str
    image_id: str
    excluded: bool


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
    excluded = page_processing_excluded(session_id, stem)
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
    restore_dir = work / "restore" / stem
    final_path = output / "final" / f"{stem}_final.jpg"
    export_recovery_path = json_dir / f"{stem}_export_recovery.json"
    export_recovery = load_json(export_recovery_path, default={})
    has_image_generation_fallback = isinstance(export_recovery, dict) and export_recovery.get("status") == "generated"
    record = {
        "id": stem,
        "processing_excluded": excluded,
        "analysis_route": page_analysis_route(session_id, stem),
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
        "final_url": file_url(path if excluded else final_path),
        "restore_dir": str(restore_dir),
        "restore_state_json": str(restore_dir / "state.json"),
        "restore_mask_png": str(restore_dir / "mask.png"),
        "restore_mask_url": file_url(restore_dir / "mask.png"),
        "restore_patch_count": len(load_json(restore_dir / "state.json", default={}).get("patches", [])),
        # 확정 패치를 얹은 현재 지우기 결과. 분석 탭 "마스크" 미리보기가 masked.jpg 대신 이것을 보여 준다.
        "restore_preview_url": file_url(restore_dir / "preview.png"),
        "has_effective_text_groups": (json_dir / f"{stem}_text_groups_effective.json").exists(),
        "has_integrated_json": not excluded and (json_dir / f"{stem}_integrated.json").exists(),
        "ocr_status": load_json(json_dir / f"{stem}_ocr_status.json", default={}),
        "image_generation_recommendation": load_json(work / "image_generation" / stem / "recommendation.json", default={}),
        "export_recovery": export_recovery,
        "has_image_generation_fallback": has_image_generation_fallback,
        "detail_rev": page_detail_rev(json_dir, stem),
        "detail_loaded": include_detail,
    }
    if include_detail:
        regions = regions_with_processing_basis(load_json(json_dir / f"{stem}_regions.json", default={}))
        integrated = load_json(json_dir / f"{stem}_integrated.json", default=[])
        effective_text_groups = load_json(json_dir / f"{stem}_text_groups_effective.json", default=[])
        raw_text_groups = load_json(json_dir / f"{stem}_text_groups.json", default=[])
        manual_guidance = load_json(json_dir / f"{stem}_manual_guidance.json", default={})
        overlay_guidance = load_json(json_dir / f"{stem}_overlay_guidance.json", default={})
        # 영역 참조: 저장 파일은 그대로 두고 읽을 때 메모리로만 region_ref 등을 붙인다.
        projection = project_page(
            regions, integrated, overlay_guidance, manual_guidance, raw_text_groups,
            _overlay_is_newer_than_integrated(json_dir / f"{stem}_overlay_guidance.json", json_dir / f"{stem}_integrated.json"),
            _overlay_is_newer_than_integrated(json_dir / f"{stem}_overlay_guidance.json", json_dir / f"{stem}_regions.json") and (json_dir / f"{stem}_regions.json").exists(),
        )
        regions, integrated = projection["regions"], projection["integrated"]
        record.update({
            "text_groups": load_json(json_dir / f"{stem}_text_groups.json", default=[]),
            "effective_text_groups": effective_text_groups,
            "regions": regions,
            "guidance": manual_guidance,
            "guidance_report": load_json(json_dir / f"{stem}_manual_guidance_report.json", default={}),
            "overlay_guidance": overlay_guidance,
            "overlay_guidance_v1": projection["overlay_guidance_v1"],
            "region_ref_report": projection["report"],
            "overlay_layout": load_json(json_dir / f"{stem}_overlay_layout.json", default={}),
            "integrated": integrated,
        })
        record.update(_regions_flags(regions))
        record.update(_integrated_flags(integrated))
    else:
        record.update(_cached_json_flags(json_dir / f"{stem}_regions.json", _regions_flags, default={}))
        record.update(_cached_json_flags(json_dir / f"{stem}_integrated.json", _integrated_flags, default=[]))
    # 번역 뒤 영역이 바뀌었는지. 번역 시점 서명이 없는 예전 결과는 판정하지 않는다.
    translated_sig = (record.get("ocr_status") or {}).get("regions_signature")
    record["ocr_stale"] = bool(
        record["has_integrated_json"] and translated_sig and record.get("regions_signature")
        and translated_sig != record["regions_signature"]
    )
    return record


# 세션 요약 목록에 들어가는 키. 전체 레코드도 같은 키를 같은 의미로 가진다(프론트에서 병합).
IMAGE_SUMMARY_KEYS = (
    "input_storage",
    "source_ref",
    "source_status",
    "processing_excluded",
    "analysis_route",
    "analysis_skip_reason",
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
    "split_override",
    "integrated_json",
    "overlay_guidance_json",
    "bubble_vis_url",
    "indexed_sheet_url",
    "masked_url",
    "final_url",
    "restore_mask_url",
    "restore_patch_count",
    "restore_preview_url",
    "has_effective_text_groups",
    "has_integrated_json",
    "has_analysis",
    "ocr_region_count",
    "regions_signature",
    "ocr_stale",
    "integrated_skipped",
    "integrated_message",
    "ocr_status",
    "export_recovery",
    "has_image_generation_fallback",
    "image_generation_recommendation",
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


def regions_signature(data: Any) -> str | None:
    """번역이 만들어진 시점의 영역 구성을 식별한다. 영역이 바뀌면 기존 번역이 현재 영역과 어긋난다."""
    regions = data.get("regions") if isinstance(data, dict) else None
    if not isinstance(regions, list):
        return None
    keys = sorted(region_binding_key(r) for r in regions if isinstance(r, dict))
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:16]


def _regions_flags(data: Any) -> dict[str, Any]:
    regions = data.get("regions") if isinstance(data, dict) else None
    has_analysis = isinstance(regions, list)
    return {"has_analysis": has_analysis, "ocr_region_count": len(regions) if has_analysis else None,
            "regions_signature": regions_signature(data),
            "analysis_skip_reason": data.get("skip_reason") if isinstance(data, dict) else None}


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
        "model": req.model or IMAGE_MODEL,
        "text_model": req.text_model or "gpt-6.1-terra",
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
        "input_storage", "source_ref", "source_status",
        "parent_id",
        "parent_name",
        "parent_input_path",
        "source_image_sha256",
        "crop_rect",
        "page_side",
        "reading_order",
        "split_policy",
        "split_x_ratio",
        "split_override",
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
    return "OCR sheet 산출물이 불완전해 OpenAI OCR/번역을 건너뜁니다."


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
            "regions_signature": regions_signature(load_json(Path(record["regions_json"]), default={})) if status == "done" else None,
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
    root = Path(os.path.commonpath([str(p.resolve().parent) for p in paths])) if paths else None
    root_id = source_library().register(root) if root else None
    for index, src in enumerate(sorted(paths, key=lambda path: natural_name_key(path.name)), start=1):
        if src.suffix.lower() not in IMAGE_EXTS or not src.exists():
            continue
        candidate = allocate_input_path(input_dir, safe_filename(src.name), index, existing_ids)
        image_hash = source_digest(src)
        atomic_link(src, candidate)
        record = image_manifest_item(candidate, image_hash, {"input_storage":"source",
            "source_ref":{"root_id":root_id,"relative_path":str(src.resolve().relative_to(root))}})
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


def server_folder_listing(path: str = "") -> dict[str, Any]:
    """서버(이 앱이 도는 컴퓨터)의 폴더를 고르는 창용 목록. 하위 폴더 이름과 이 폴더의 이미지 수만 돌려준다."""
    home = Path.home()
    current = Path(path).expanduser() if path.strip() else home
    try:
        current = current.resolve()
    except OSError:
        current = home
    if not current.is_dir():
        raise HTTPException(status_code=404, detail="폴더를 찾을 수 없습니다")
    dirs: list[dict[str, str]] = []
    images = 0
    try:
        for entry in os.scandir(current):
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(Path(entry.path))})
                elif entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTS:
                    images += 1
            except OSError:
                continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="이 폴더를 읽을 권한이 없습니다") from exc
    dirs.sort(key=lambda item: natural_name_key(item["name"]))
    # 외장 디스크는 macOS에서 /Volumes 아래에 붙는다
    roots = [{"name": "홈", "path": str(home)}]
    volumes = Path("/Volumes")
    if volumes.is_dir():
        for entry in sorted(volumes.iterdir(), key=lambda item: item.name):
            # 시동 디스크 바로가기(심볼릭 링크)와 복구 볼륨은 사진 폴더가 아니므로 뺀다
            if not entry.name.startswith(".") and entry.name != "Recovery" and not entry.is_symlink() and entry.is_dir():
                roots.append({"name": entry.name, "path": str(entry)})
    parent = str(current.parent) if current.parent != current else ""
    return {"path": str(current), "parent": parent, "dirs": dirs, "image_count": images, "roots": roots}


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
        if image_hash and input_path.exists() and not record["processing_excluded"]:
            hydrate_analysis_cache(record, image_hash, False, noop, allow_backfill=False)
            hydrate_guidance_cache(record, image_hash, noop, allow_backfill=False)
        guidance = load_json(record["guidance_json"], default={})
        if input_path.exists() and guidance_has_edits(guidance) and not record["processing_excluded"]:
            output_ready = bool(image_hash) and hydrate_guidance_output_cache(record, image_hash, guidance, noop)
            if not output_ready and analysis_outputs_are_cache_links(record) and _claim_guidance_reapply(session_id, record):
                pending_reapply.append(item)
        if not record["processing_excluded"]:
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


def _overlay_is_newer_than_integrated(overlay_path: Path, integrated_path: Path) -> bool:
    """수동값이 현재 integrated 번호 기준으로 저장됐는지(그 integrated보다 나중에 저장됐는지)."""
    try:
        return overlay_path.stat().st_mtime >= integrated_path.stat().st_mtime
    except OSError:
        return True


def page_record(session_id: str, image_id: str) -> dict[str, Any]:
    """선택한 페이지 하나의 전체 레코드. 읽기 전용."""
    item = get_image(session_id, image_id)
    return merge_manifest_metadata(
        image_record(session_id, Path(item["input_path"]), item.get("image_sha256"), item, probe_image=False),
        item,
    )


def analyze_one_image(session_id: str, item: dict[str, Any], index: int, total: int, force: bool, emit) -> bool:
    if item.get("source_status") == "missing":
        emit({"type":"image_status","image_id":item["id"],"status":"failed","detail":"원본 폴더를 다시 연결하세요"})
        return False
    if page_processing_excluded(session_id, item["id"]):
        emit({"type":"image_status", "image_id":item["id"], "status":"skipped", "detail":"페이지 처리 제외 · 원본 출력"})
        return True
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
    cv_ready = hydrate_cv_cache(record, image_hash, force, log_emit, item)
    _unlink_symlinked_analysis_outputs(record)
    ok, log = run_single_image(
        record["input_path"],
        record["work_dir"],
        record["final_jpg"],
        force=force,
        _skip_step1=cv_ready,
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
        "inpaint_engine_overrides",
        "custom_masks",
    )
    return any(bool(guidance.get(key)) for key in keys)


def _canonical_guidance_items(items: Any) -> set[str]:
    if not isinstance(items, list):
        return set()
    return {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in items}


def guidance_allows_unboxed_mask_reuse(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if not guidance_has_edits(previous):
        return False
    # 지우기 엔진 고정이나 지우기 보정이 바뀌면 이전 지우기 결과를 재사용하지 않고 해당 영역을 다시 지운다
    stable_keys = ("disabled_tg_ids", "tg_splits", "suppressed_regions", "inpaint_engine_overrides", "custom_masks")
    for key in stable_keys:
        if previous.get(key, []) != current.get(key, []):
            return False
    additive_keys = ("manual_regions", "manual_seed_points", "virtual_walls", "split_guides")
    for key in additive_keys:
        old_items = _canonical_guidance_items(previous.get(key, []))
        new_items = _canonical_guidance_items(current.get(key, []))
        if not old_items.issubset(new_items):
            return False
        # 기존 영역의 크기를 바꾼 가이드는 덧붙임이 아니다. 줄인 경우 이전에 지운 자리가 남지 않게 다시 지운다
        if any('"resize_source_key"' in item for item in new_items - old_items):
            return False
    return True


def run_step3_with_effective_text_groups(session_id: str, image_id: str, guidance: dict[str, Any]) -> dict[str, Any]:
    if page_processing_excluded(session_id, image_id):
        raise HTTPException(status_code=409, detail="처리 제외 페이지입니다. 처리 대상으로 복원한 뒤 편집하세요.")
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
        inpaint_engine_overrides=guidance.get("inpaint_engine_overrides", []),
        custom_masks=guidance.get("custom_masks", []),
        base_masked_path=record["masked_jpg"] if can_reuse_unboxed_mask else None,
        reuse_existing_unboxed_inpaint=can_reuse_unboxed_mask,
        force_standard=record.get("analysis_route") == "standard",
    )
    if sheet_result.get("reused_unboxed_inpaint"):
        report["fast_path"] = {
            "reused_unboxed_inpaint": True,
            "reason": "additive_guidance_only",
        }
    if isinstance(sheet_result, dict) and isinstance(sheet_result.get("manual_guidance_report"), dict):
        report["manual_guidance"] = sheet_result["manual_guidance_report"]
        write_json(record["guidance_report_json"], report)
    sync_overlay_guidance_for_record(record)
    refreshed = image_record(session_id, Path(record["input_path"]))
    refreshed["guidance_report"] = report
    return refreshed


def reapply_guidance_if_present(session_id: str, record: dict[str, Any], log_callback) -> dict[str, Any] | None:
    if page_processing_excluded(session_id, record["id"]):
        return None
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
    if record.get("processing_excluded"):
        return
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
        restore_state_path=Path(record["restore_state_json"]) if Path(record["restore_state_json"]).exists() else None,
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


def _overlay_page_inputs(record: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """overlay 수동값을 영역 참조로 해석하는 데 쓰는 현재 integrated와 영역(region_ref 부착)."""
    integrated = load_json(Path(record["integrated_json"]), default={})
    payload = load_json(Path(record["regions_json"]), default={})
    raw_regions = payload.get("regions") if isinstance(payload, dict) else None
    manual = load_json(Path(record["guidance_json"]), default={}) if record.get("guidance_json") else {}
    return integrated, annotate_regions(raw_regions if isinstance(raw_regions, list) else [], manual if isinstance(manual, dict) else None)


def _current_tg_digest(record: dict[str, Any]) -> str | None:
    """원본 TG(text_groups.json, CV/Step 2 산출)의 해시. TG 끄기·분할로는 변하지 않고 TG가 새로 매겨졌을 때만 변한다(설계 A7).

    원본 파일이 없으면(예: 일부 페이지) None이라 이 보호는 꺼진다."""
    path = record.get("text_groups_json")
    groups = load_json(Path(path), default=None) if path and Path(path).exists() else None
    return tg_digest(groups) if isinstance(groups, list) and groups else None


def _file_is_newer(path: Path, other: Path) -> bool:
    """path가 other보다 나중에 저장됐는지. 비교할 수 없으면 False(증거 없음)."""
    try:
        return path.stat().st_mtime >= other.stat().st_mtime
    except OSError:
        return False


def store_overlay_guidance(record: dict[str, Any], incoming: dict[str, Any] | None, replace: bool) -> dict[str, Any]:
    """편집기가 보낸 overlay 수동값을 저장한다. 영역 참조(regions_by_ref)와 구형 번호 키(regions)를 함께 쓴다.

    구버전 번들이 번호 키만 보내도 현재 integrated 번호 기준으로 영역 참조로 바꿔 받는다(replace여도 참조 값을 지우지 않는다).
    보관함(unmatched_regions)은 incoming이 따로 주지 않으면 그대로 둔다.
    """
    path = Path(record["overlay_guidance_json"])
    integrated, regions = _overlay_page_inputs(record)
    stored = load_json(path, default={}) if path.exists() else {}
    incoming_keys = list(((incoming or {}).get("regions") or {}).keys()) if isinstance(incoming, dict) else []
    # 새 번들은 key_scheme을 달아 보낸다. 빈 regions로 replace해도 구 번들과 구별된다.
    incoming_has_refs = bool(isinstance(incoming, dict) and (incoming.get("regions_by_ref") or incoming.get("key_scheme") == REGION_REF_SCHEME)) or any(
        str(key).startswith(("tg:", "guide:", "geo:")) for key in incoming_keys
    )
    if not (isinstance(integrated, dict) and integrated.get("clusters")) and not incoming_has_refs and incoming_keys:
        # 번역이 아직 없으면 번호 키가 어느 영역을 가리키는지 알 수 없다. 이 경우만 예전처럼 번호 키 그대로 저장한다.
        legacy = incoming if replace else merge_overlay_guidance(stored if isinstance(stored, dict) else {}, incoming or {})
        write_json(path, legacy)
        return legacy
    stored_newer = True
    if path.exists() and Path(record["integrated_json"]).exists():
        stored_newer = path.stat().st_mtime >= Path(record["integrated_json"]).stat().st_mtime
    stored_newer_regions = _file_is_newer(path, Path(record["regions_json"]))
    existing_v1, _ = normalize_overlay_guidance(stored if isinstance(stored, dict) else {}, integrated, regions, stored_newer, stored_newer_regions)
    incoming_v1, _ = normalize_overlay_guidance(incoming if isinstance(incoming, dict) else {}, integrated, regions, True, True)
    if replace:
        result = incoming_v1
        if not incoming_has_refs:
            # 구 번들은 번호 키만 안다. 구형으로 표현되지 않던 참조 전용 값(번역 없는 영역의 직접 입력 등)은 지우지 않는다.
            expressible = legacy_key_map(existing_v1, integrated, regions)
            for key, value in (existing_v1.get("regions_by_ref") or {}).items():
                if key not in expressible and key not in result["regions_by_ref"]:
                    result["regions_by_ref"][key] = value
        incoming_stash = (incoming or {}).get("unmatched_regions") if isinstance(incoming, dict) else None
        if not isinstance(incoming_stash, dict) or not incoming_stash:
            result["unmatched_regions"] = existing_v1.get("unmatched_regions") or {}
    else:
        result = {**existing_v1, **{k: v for k, v in incoming_v1.items() if k not in {"regions_by_ref", "unmatched_regions"}}}
        merged_refs: dict[str, Any] = dict(existing_v1.get("regions_by_ref") or {})
        for key, value in (incoming_v1.get("regions_by_ref") or {}).items():
            previous = merged_refs.get(key)
            merged_refs[key] = {**previous, **value} if isinstance(previous, dict) and isinstance(value, dict) else value
        result["regions_by_ref"] = merged_refs
        result["unmatched_regions"] = {**(existing_v1.get("unmatched_regions") or {}), **(incoming_v1.get("unmatched_regions") or {})}
    digest = _current_tg_digest(record)
    result, _report = reconcile_overlay_guidance(result, regions, digest, segments=segment_index_by_ref(integrated, regions, digest))
    saved = dual_write_guidance(result, integrated, regions)
    write_json(path, saved)
    return saved


def sync_overlay_guidance_for_record(record: dict[str, Any], log_callback=None) -> dict[str, Any]:
    """저장된 overlay 수동값을 현재 영역·integrated 기준으로 맞춰 다시 쓴다.

    OCR/번역 직전에 불러 번호가 바뀌기 전에 영역 참조로 고정하고, Step 3·OCR 직후에도 불러
    합쳐지거나 갈라지거나 사라진 영역의 수동값을 옮기거나 보관한다. 번호는 보지 않는다.
    번역(integrated)도 없고 영역 참조 기록도 없으면 판단할 근거가 없어 건드리지 않는다.
    """
    path = Path(record["overlay_guidance_json"])
    if not path.exists():
        return {}
    stored = load_json(path, default={})
    if not isinstance(stored, dict):
        return {}
    integrated, regions = _overlay_page_inputs(record)
    has_refs = stored.get("key_scheme") == REGION_REF_SCHEME and isinstance(stored.get("regions_by_ref"), dict)
    clusters = integrated.get("clusters") if isinstance(integrated, dict) else None
    if not regions or (not has_refs and not clusters):
        return {}
    stored_newer = True
    if Path(record["integrated_json"]).exists():
        stored_newer = path.stat().st_mtime >= Path(record["integrated_json"]).stat().st_mtime
    v1, normalize_report = normalize_overlay_guidance(stored, integrated, regions, stored_newer, _file_is_newer(path, Path(record["regions_json"])))
    digest = _current_tg_digest(record)
    v1, report = reconcile_overlay_guidance(v1, regions, digest, segments=segment_index_by_ref(integrated, regions, digest))
    saved = dual_write_guidance(v1, integrated, regions)
    if saved != stored:
        write_json(path, saved)
        stashed = {**normalize_report.get("stashed", {}), **report.get("stashed", {})}
        message = f"글자 입히기 수동값 영역 참조 맞춤: 이동 {report['moved'] or '-'}"
        if stashed:
            message += f", 짝 없음(보관) {sorted(stashed)}"
        print(f"[overlay] {record['id']} {message}", flush=True)
        if log_callback is not None:
            log_callback(message)
    report["stashed"] = {**normalize_report.get("stashed", {}), **report.get("stashed", {})}
    return report


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
    replace_overlay: bool = False,
    glossary_context: dict | None = None,
) -> dict[str, Any]:
    if page_processing_excluded(session_id, item["id"]):
        return page_record(session_id, item["id"])
    context = glossary_context if glossary_context is not None else session_glossary_snapshot(session_id)
    record = image_record(session_id, Path(item["input_path"]), item.get("image_sha256"))
    stored_speakers = load_json(speakers_path(session_id, record["id"]), default={})
    speaker_store, _speaker_report, page_regions = reconciled_speaker_store({**record, "session_id": session_id}, stored_speakers)
    if speaker_store != stored_speakers and (speaker_store.get("by_region_ref") or speaker_store.get("context_by_region_ref")):
        write_json(speakers_path(session_id, record["id"]), speaker_store)
    speakers = page_speakers(page_regions, page_speaker_map(session_id, record["id"]), context, speaker_store.get("by_region", {}), speaker_store.get("by_region_ref", {}))
    # 작품 메모 → 화자 → 영역별 번역 가이드 → 추가 프롬프트 순서로 붙인다.
    region_contexts = page_region_contexts(page_regions, speaker_store.get("context_by_region", {}), speaker_store.get("context_by_region_ref", {}))
    translation_prompt = "\n\n".join(part for part in (context.get("prompt", ""), speaker_prompt(speakers), region_context_prompt(region_contexts), extra_prompt) if part)
    clusters = current_clusters_path(record, record["id"])
    font = font_path or DEFAULT_FONT
    if overlay_guidance is not None:
        store_overlay_guidance(record, overlay_guidance, replace_overlay)
    else:
        # OCR이 번역 번호를 새로 매기기 전에, 지금 영역 기준으로 수동값을 영역 참조로 고정해 둔다(force가 integrated를 지우기 전).
        sync_overlay_guidance_for_record(record)
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
        f"--extra-prompt={translation_prompt}",
        *(["--model", ocr_model] if ocr_model else []),
    ]
    if log_callback is None:
        run_subprocess(ocr_cmd, timeout=360)
    else:
        log_callback("Step 4 OpenAI OCR/번역 시작")
        run_subprocess_stream(ocr_cmd, timeout=360, log_callback=log_callback)
        log_callback("Step 6 Text Overlay 시작")
    write_json(Path(record["work_dir"]) / "json" / f"{record['id']}_glossary_snapshot.json", {**context, "speakers": speakers, "region_contexts": region_contexts})
    integrated = load_json(record["integrated_json"], default={})
    if integrated:
        integrated["glossary"] = {"work_id": context.get("work_id"), "version": context.get("version"), "speakers": speakers, "region_contexts": region_contexts}
        write_json(record["integrated_json"], integrated)
    sync_overlay_guidance_for_record(record, log_callback=log_callback)
    render_overlay_for_record(record, font, tracking_offset, log_callback=log_callback)
    mark_ocr_status(record, "done", "OCR/번역 완료", "integrated.json 생성 완료")
    return image_record(session_id, Path(item["input_path"]))


def stream_run_final(req: RunFinalRequest):
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    glossary_context: dict = {}
    # 여러 페이지를 한 번에 처리하는 요청(전체 또는 지정한 목록)은 페이지별 overlay 편집값을 받지 않는다.
    multi_target = req.image_id in ("all", "batch")

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
                req.overlay_guidance if not multi_target else None,
                force=req.force,
                log_callback=log_emit,
                ocr_model=req.ocr_model,
                replace_overlay=req.replace and not multi_target,
                glossary_context=glossary_context,
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
        nonlocal glossary_context
        try:
            # 같은 배치 안에서는 조사/검토 시점에 따라 페이지별 용어가 달라지지 않는다.
            data = ensure_session(req.session_id)
            if req.image_id == "all":
                targets = sorted_image_records(data.get("images", []))
            elif req.image_id == "batch":
                wanted = set(req.image_ids)
                targets = [item for item in sorted_image_records(data.get("images", [])) if item["id"] in wanted]
            elif req.image_id == "failed":
                targets = [
                    item for item in sorted_image_records(data.get("images", []))
                    if (image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256")).get("ocr_status") or {}).get("status") == "failed"
                ]
            else:
                targets = [get_image(req.session_id, req.image_id)]
            targets = [item for item in targets if not page_processing_excluded(req.session_id, item["id"])]
            if targets:
                glossary_context = session_glossary_snapshot(req.session_id)
            total = len(targets)
            if multi_target and not req.force:
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
    export_dir = export_directory() / data["session_id"]
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
            source_path = final_path if final_path.exists() and not record["processing_excluded"] else input_path
            if not source_path.exists():
                continue
            zf.write(source_path, export_name_for(item, source_path))
    return zip_path


def generate_missing_finals_with_openai(req: GenerateMissingFinalRequest) -> dict[str, Any]:
    data = ensure_session(req.session_id)
    requested_ids = set(req.image_ids or [])
    targets = [
        item for item in sorted_image_records(data.get("images", []))
        if (not requested_ids or item.get("id") in requested_ids) and not page_processing_excluded(req.session_id, item["id"])
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
            model_name=req.model or IMAGE_MODEL,
            prompt_guide=req.prompt_guide or "",
            text_model_name=req.text_model or "gpt-6.1-terra",
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
                if (not requested_ids or item.get("id") in requested_ids) and not page_processing_excluded(req.session_id, item["id"])
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
                    model_name=req.model or IMAGE_MODEL,
                    prompt_guide=req.prompt_guide or "",
                    text_model_name=req.text_model or "gpt-6.1-terra",
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


# ── 배경 글자 복원(LaMa) ───────────────────────────────────────────
# 복원은 서버 프로세스 안의 단일 작업 스레드에서 순서대로 돈다.
_RESTORE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="restore")
_RESTORE_JOBS: dict[str, dict[str, Any]] = {}
_RESTORE_JOBS_LOCK = threading.Lock()
_RESTORE_STATE_LOCK = threading.Lock()


def _restore_record(session_id: str, image_id: str) -> dict[str, Any]:
    if page_processing_excluded(session_id, image_id):
        raise HTTPException(status_code=409, detail="처리 제외 페이지입니다. 처리 대상으로 복원한 뒤 편집하세요.")
    source = get_image(session_id, image_id)
    return image_record(session_id, Path(source["input_path"]), source.get("image_sha256"))


def _read_bgr(path: str | Path):
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail=f"이미지를 읽을 수 없습니다: {Path(path).name}")
    return image


def _restore_state(record: dict[str, Any]) -> dict[str, Any]:
    state = load_json(record["restore_state_json"], default={})
    return state if isinstance(state, dict) else {}


def _update_restore_state(record: dict[str, Any], **changes: Any) -> dict[str, Any]:
    with _RESTORE_STATE_LOCK:
        state = _restore_state(record)
        state.update(changes)
        state.setdefault("patches", [])
        write_json(record["restore_state_json"], state)
        return state


def restore_source_image(record: dict[str, Any], source: str):
    """복원 입력 페이지. masked: Step 3 결과에 확정 패치를 얹은 현재 상태, original: 원본 입력."""
    from pipeline.background_restore import apply_restore_patches

    if source == "masked" and Path(record["masked_jpg"]).exists():
        image = _read_bgr(record["masked_jpg"])
        apply_restore_patches(image, Path(record["restore_state_json"]))
        return image, "masked"
    return _read_bgr(record["input_path"]), "original"


def write_restore_preview(record: dict[str, Any]) -> None:
    """확정 패치를 얹은 현재 페이지 미리보기(PNG). 최종 출력 경로와 무관한 확인용이다."""
    import cv2

    preview = Path(record["restore_dir"]) / "preview.png"
    if not _restore_state(record).get("patches"):
        preview.unlink(missing_ok=True)
        return
    image, _ = restore_source_image(record, "masked")
    cv2.imwrite(str(preview), image)


def restore_info(record: dict[str, Any]) -> dict[str, Any]:
    from pipeline.background_restore import is_grayscale_image

    original = _read_bgr(record["input_path"])
    state = _restore_state(record)
    return {
        "image_id": record["id"],
        "width": int(original.shape[1]),
        "height": int(original.shape[0]),
        "is_grayscale": is_grayscale_image(original),
        "state": state,
        "mask_url": record.get("restore_mask_url"),
        "has_masked": Path(record["masked_jpg"]).exists(),
        "preview_url": file_url(Path(record["restore_dir"]) / "preview.png"),
        "masked_url": record.get("masked_url"),
        "input_url": record.get("input_url"),
        "patches": [
            {
                **patch,
                "patch_url": file_url(Path(record["restore_dir"]) / patch["patch_png"]),
                "mask_url": file_url(Path(record["restore_dir"]) / patch["mask_png"]),
            }
            for patch in state.get("patches", [])
        ],
    }


def _restore_job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def submit_restore_job(req: RestoreRunRequest) -> dict[str, Any]:
    import cv2

    from pipeline.background_restore import ENGINES, RESOLUTION_CHOICES, RestoreParams, decode_mask_png

    if req.engine not in ENGINES:
        raise HTTPException(status_code=400, detail=f"engine은 {ENGINES} 중 하나여야 합니다")
    if req.resolution not in RESOLUTION_CHOICES:
        raise HTTPException(status_code=400, detail=f"resolution은 {list(RESOLUTION_CHOICES)} 중 하나여야 합니다")
    record = _restore_record(req.session_id, req.image_id)
    page, source_kind = restore_source_image(record, req.source)
    h, w = page.shape[:2]
    if req.mask_png:
        mask = decode_mask_png(req.mask_png, w, h)
        Path(record["restore_dir"]).mkdir(parents=True, exist_ok=True)
        cv2.imwrite(record["restore_mask_png"], mask)
    elif Path(record["restore_mask_png"]).exists():
        mask = cv2.imread(record["restore_mask_png"], cv2.IMREAD_GRAYSCALE)
        mask = (mask > 127).astype("uint8") * 255
    else:
        raise HTTPException(status_code=400, detail="제거 마스크가 없습니다. 자동 마스크를 만들거나 직접 칠하세요.")
    _update_restore_state(record, context_rect=[int(v) for v in req.context_rect[:4]])

    job_id = f"{dt.datetime.now().strftime('%H%M%S')}_{req.engine}_{hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:6]}"
    job_dir = Path(record["restore_dir"]) / "jobs" / job_id
    params = RestoreParams(
        engine=req.engine,
        resolution=req.resolution,
        feather_px=max(0, int(req.feather_px)),
    )
    job: dict[str, Any] = {
        "job_id": job_id,
        "session_id": req.session_id,
        "image_id": req.image_id,
        "engine": req.engine,
        "source": source_kind,
        "status": "queued",
        "progress": {"step": 0, "total": 1},
        "created_at": time.time(),
        "_job_dir": str(job_dir),
    }
    with _RESTORE_JOBS_LOCK:
        _RESTORE_JOBS[job_id] = job

    def work() -> None:
        from pipeline.background_restore import run_restore

        job["status"] = "running"
        job["started_at"] = time.time()
        try:
            result = run_restore(page, mask, req.context_rect, params, job_dir)
            job["meta"] = result["meta"]
            job["result"] = {
                "crop_before_url": file_url(job_dir / "crop_before.png"),
                "crop_after_url": file_url(job_dir / "crop_after.png"),
                "crop_mask_url": file_url(job_dir / "crop_mask.png"),
                "crop_model_raw_url": file_url(job_dir / "crop_model_raw.png"),
            }
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - 실패는 작업 상태로 돌려주고 기존 결과는 건드리지 않는다
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            job["finished_at"] = time.time()

    _RESTORE_EXECUTOR.submit(work)
    return _restore_job_public(job)


AI_ERASE_ENGINE = "ai-erase"


def submit_ai_erase_job(req: AiEraseRequest) -> dict[str, Any]:
    """원본 페이지를 이미지 편집 모델로 보내 글자를 지운 배경을 만든다. 결과는 미리보기 후보이고,
    확정(/api/restore/accept)하면 페이지 전체 배경 복원 패치로 저장되어 렌더 때 합성된다."""
    import cv2
    import numpy as np

    from pipeline.gemini_image import image_model_for, is_gemini_model

    model = image_model_for(req.ocr_model, IMAGE_MODEL)
    quality = req.quality or ("low" if is_gemini_model(model) else "medium")
    if quality not in {"low", "medium", "high"}:
        raise HTTPException(status_code=400, detail="quality는 low, medium, high 중 하나여야 합니다")
    record = _restore_record(req.session_id, req.image_id)
    page = _read_bgr(record["input_path"])  # 이전 지우기 결과가 아니라 원본에서 지운다
    h, w = page.shape[:2]
    job_id = f"{dt.datetime.now().strftime('%H%M%S')}_{AI_ERASE_ENGINE}_{hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:6]}"
    job_dir = Path(record["restore_dir"]) / "jobs" / job_id
    job: dict[str, Any] = {
        "job_id": job_id,
        "session_id": req.session_id,
        "image_id": req.image_id,
        "engine": AI_ERASE_ENGINE,
        "source": "original",
        "status": "queued",
        "progress": {"step": 0, "total": 1},
        "created_at": time.time(),
        "_job_dir": str(job_dir),
    }
    with _RESTORE_JOBS_LOCK:
        _RESTORE_JOBS[job_id] = job

    def work() -> None:
        from pipeline.ai_text_erase import erase_page_text

        job["status"] = "running"
        job["started_at"] = time.time()
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            erased, meta = erase_page_text(page, job_dir, model=model, quality=quality)
            # 배경 복원 확정 흐름이 그대로 쓰도록 같은 파일 이름으로 남긴다(페이지 전체 = rect, 마스크 전부 255)
            cv2.imwrite(str(job_dir / "crop_before.png"), page)
            cv2.imwrite(str(job_dir / "crop_after.png"), erased)
            cv2.imwrite(str(job_dir / "crop_mask.png"), np.full((h, w), 255, dtype=np.uint8))
            job["meta"] = {
                "context_rect": [0, 0, w, h],
                "params": {"engine": AI_ERASE_ENGINE, "model": meta["model"], "quality": meta["quality"]},
                "timing": {"total_sec": meta["elapsed_sec"]},
                "usage": {k: meta[k] for k in ("input_tokens", "output_tokens", "estimated_cost_usd", "request_size")},
            }
            job["result"] = {
                "crop_before_url": file_url(job_dir / "crop_before.png"),
                "crop_after_url": file_url(job_dir / "crop_after.png"),
            }
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - 실패는 작업 상태로 돌려주고 기존 결과는 건드리지 않는다
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            job["finished_at"] = time.time()

    _RESTORE_EXECUTOR.submit(work)
    return _restore_job_public(job)


def accept_restore_job(req: RestoreAcceptRequest) -> dict[str, Any]:
    with _RESTORE_JOBS_LOCK:
        job = _RESTORE_JOBS.get(req.job_id)
    if job is None or job.get("session_id") != req.session_id or job.get("image_id") != req.image_id:
        raise HTTPException(status_code=404, detail="복원 작업을 찾을 수 없습니다")
    if job.get("status") != "done":
        raise HTTPException(status_code=400, detail="완료된 복원 결과만 확정할 수 있습니다")
    record = _restore_record(req.session_id, req.image_id)
    job_dir = Path(job["_job_dir"])
    patches_dir = Path(record["restore_dir"]) / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    patch_id = req.job_id
    shutil.copy2(job_dir / "crop_after.png", patches_dir / f"{patch_id}_patch.png")
    shutil.copy2(job_dir / "crop_mask.png", patches_dir / f"{patch_id}_mask.png")
    meta = job.get("meta", {})
    patch = {
        "id": patch_id,
        "engine": job["engine"],
        "rect": meta.get("context_rect"),
        "patch_png": f"patches/{patch_id}_patch.png",
        "mask_png": f"patches/{patch_id}_mask.png",
        "params": meta.get("params"),
        "timing": meta.get("timing"),
        "usage": meta.get("usage"),
        "source": job.get("source"),
        "accepted_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    with _RESTORE_STATE_LOCK:
        state = _restore_state(record)
        state["patches"] = [p for p in state.get("patches", []) if p.get("id") != patch_id] + [patch]
        write_json(record["restore_state_json"], state)
    write_restore_preview(record)
    return restore_info(record)


def remove_restore_patch(req: RestorePatchRemoveRequest) -> dict[str, Any]:
    record = _restore_record(req.session_id, req.image_id)
    with _RESTORE_STATE_LOCK:
        state = _restore_state(record)
        state["patches"] = [p for p in state.get("patches", []) if p.get("id") != req.patch_id]
        write_json(record["restore_state_json"], state)
    write_restore_preview(record)
    return restore_info(record)


def build_restore_auto_mask(req: RestoreAutoMaskRequest) -> dict[str, Any]:
    import cv2

    from pipeline.background_restore import MaskParams, build_text_stroke_mask, seeds_from_regions, suggest_context_rect

    record = _restore_record(req.session_id, req.image_id)
    # 글자 획은 원본에서 찾는다. masked.jpg는 이미 LaMa가 지운 상태일 수 있다.
    original = _read_bgr(record["input_path"])
    seeds: list[dict[str, Any]] = []
    if req.use_region_seeds:
        groups_path = record["effective_text_groups_json"] if Path(record["effective_text_groups_json"]).exists() else record["text_groups_json"]
        seeds = seeds_from_regions(load_json(record["regions_json"], default={}), load_json(groups_path, default=[]))
    seeds += [{"rect": [int(v) for v in rect[:4]], "fragments": [], "source": "user"} for rect in req.extra_seed_rects if len(rect) >= 4]
    params = MaskParams(polarity=req.polarity, threshold=req.threshold, dilate_px=max(0, min(32, int(req.dilate_px))), min_area=max(1, int(req.min_area)))
    mask = build_text_stroke_mask(original, seeds, params)
    if not req.save:
        ok, buf = cv2.imencode(".png", mask)
        return {
            "mask_png": "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode() if ok else None,
            "seeds": [{"rect": s["rect"], "source": s.get("source"), "region_id": s.get("region_id")} for s in seeds],
            "mask_pixels": int((mask > 0).sum()),
        }
    Path(record["restore_dir"]).mkdir(parents=True, exist_ok=True)
    cv2.imwrite(record["restore_mask_png"], mask)
    _update_restore_state(record, mask_params={
        "dilate_px": params.dilate_px, "polarity": params.polarity, "threshold": params.threshold, "min_area": params.min_area,
    }, extra_seed_rects=[s["rect"] for s in seeds if s.get("source") == "user"])
    refreshed = _restore_record(req.session_id, req.image_id)
    return {
        **restore_info(refreshed),
        "seeds": [{"rect": s["rect"], "source": s.get("source"), "region_id": s.get("region_id")} for s in seeds],
        "suggested_context_rect": suggest_context_rect(mask),
        "mask_pixels": int((mask > 0).sum()),
    }


class RevalidatingStaticFiles(StaticFiles):
    """UI 자산은 매 요청마다 재검증한다(바뀌지 않았으면 304).

    index.html이 버전 없는 /app.bundle.js를 참조하므로, 기본 휴리스틱 캐시를 두면
    번들을 다시 빌드한 뒤에도 브라우저가 옛 JS를 계속 실행한다.
    """

    def file_response(self, *args: Any, **kwargs: Any):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# 서버가 불러온 프로젝트 Python 파일의 수정 시각. 실행 중에 바뀌면 재시작이 필요하다.
_CODE_MTIMES: dict[str, int] = {}


_PROJECT_MODULES: dict[str, Any] = {"count": -1, "root": None, "files": []}


def project_module_files() -> list[tuple[str, Path]]:
    # 모듈 수가 바뀔 때만 다시 훑는다. 상태등이 5초마다 부르므로 매번 수천 개 경로를 풀지 않는다.
    if _PROJECT_MODULES["count"] != len(sys.modules) or _PROJECT_MODULES["root"] != ROOT:
        files = []
        for module in list(sys.modules.values()):
            path = getattr(module, "__file__", None)
            if not path or not path.endswith(".py"):
                continue
            file = Path(path).resolve()
            if not file.is_relative_to(ROOT) or any(part.startswith(".venv") for part in file.relative_to(ROOT).parts):
                continue
            files.append((str(file.relative_to(ROOT)), file))
        _PROJECT_MODULES.update(count=len(sys.modules), root=ROOT, files=files)
    return _PROJECT_MODULES["files"]


def loaded_code_mtimes() -> dict[str, int]:
    result = {}
    for name, file in project_module_files():
        try:
            result[name] = file.stat().st_mtime_ns
        except OSError:
            continue
    return result


def stale_code_files() -> list[str]:
    changed = []
    for name, mtime in loaded_code_mtimes().items():
        # 나중에 지연 import된 모듈은 처음 본 시각을 기준으로 삼는다.
        if _CODE_MTIMES.setdefault(name, mtime) != mtime:
            changed.append(name)
    return sorted(changed)


def bundle_version() -> str:
    # 문자열로 보낸다. 나노초 정수는 JS 숫자 정밀도를 넘는다.
    try:
        return str((UI_DIR / "app.bundle.js").stat().st_mtime_ns)
    except OSError:
        return ""


# 처리 중인 /api 요청 수. 스트리밍 응답은 마지막 body를 보낼 때까지 센다. 서버 재시작 전 확인용.
_INFLIGHT = {"count": 0}
_INFLIGHT_LOCK = threading.Lock()
_UNCOUNTED_PATHS = {"/api/health", "/api/server/restart"}


class InflightCounter:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path in _UNCOUNTED_PATHS:
            await self.app(scope, receive, send)
            return
        with _INFLIGHT_LOCK:
            _INFLIGHT["count"] += 1
        finished = False

        def finish():
            nonlocal finished
            if not finished:
                finished = True
                with _INFLIGHT_LOCK:
                    _INFLIGHT["count"] -= 1

        async def counted_send(message):
            if message["type"] == "http.response.body" and not message.get("more_body"):
                finish()
            await send(message)

        try:
            await self.app(scope, receive, counted_send)
        finally:
            finish()


def busy_job_count() -> int:
    with _INFLIGHT_LOCK:
        requests = _INFLIGHT["count"]
    return requests + len(_LOAD_HYDRATE_RUNNING)


def restart_current_process(delay: float = 0.4) -> None:
    """같은 명령으로 프로세스를 갈아 끼운다. PID·포트·작업 폴더는 유지된다."""
    time.sleep(delay)  # 재시작 요청의 응답이 브라우저에 도착한 뒤 교체한다.
    os.environ["COMIC_TRANSLATOR_RESTARTED"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.argv])


def create_app() -> FastAPI:
    app = FastAPI(title="Comic Translator Review UI")
    app.add_middleware(InflightCounter)

    @app.post("/api/page-processing")
    def set_page_processing(req: PageProcessingRequest) -> dict:
        get_image(req.session_id, req.image_id)
        path = session_dir(req.session_id) / "work/page_policy" / f"{req.image_id}.json"
        policy = load_json(path, default={})
        write_json(path, {**policy, "excluded": req.excluded, "updated_at": dt.datetime.now().isoformat()})
        return {"image": page_record(req.session_id, req.image_id)}

    @app.post("/api/page-analysis-route")
    def set_page_analysis_route(req: PageAnalysisRouteRequest) -> dict:
        get_image(req.session_id, req.image_id)
        if page_processing_excluded(req.session_id, req.image_id):
            raise HTTPException(status_code=409, detail="처리 대상으로 복원한 뒤 전환하세요.")
        path = session_dir(req.session_id) / "work/page_policy" / f"{req.image_id}.json"
        policy = load_json(path, default={})
        write_json(path, {**policy, "analysis_route": req.route, "updated_at": dt.datetime.now().isoformat()})
        return {"image": page_record(req.session_id, req.image_id)}

    @app.get("/api/image-generation/{session_id}/{image_id}")
    def read_image_generation(session_id: str, image_id: str) -> dict:
        try:
            result = ImageGenerationStore(ROOT).status(session_id, image_id)
            for item in result["results"]:
                item["preview_url"] = file_url(item["preview_path"])
            return result
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/glossary/{session_id}")
    def read_glossary(session_id: str) -> dict:
        try:
            return glossary_payload(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/glossary/bind")
    def bind_glossary(req: GlossaryBindRequest) -> dict:
        try:
            if not session_file(req.session_id).is_file():
                raise ValueError("프로젝트를 찾을 수 없습니다.")
            store = glossary_store()
            wid = store.read(req.work_id)["work_id"] if req.work_id else store.create(req.title)
            auto = req.matched_by == "cv"
            write_json(glossary_binding_path(req.session_id), {"work_id": wid, "matched_by": "cv" if auto else "user",
                                                               "hits": [str(hit)[:40] for hit in req.hits[:12]] if auto else []})
            return glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/glossary/unbind")
    def unbind_glossary(req: GlossaryBindRequest) -> dict:
        try:
            if not session_file(req.session_id).is_file():
                raise ValueError("프로젝트를 찾을 수 없습니다.")
            # 작품 없이 진행. 'none'을 남겨 CV 자동 연결이 다시 붙지 않게 한다.
            write_json(glossary_binding_path(req.session_id), {"work_id": None, "matched_by": "none", "hits": []})
            return glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def bound_work(session_id: str, work_id: str) -> None:
        data = session_glossary(session_id)
        if not data or data["work_id"] != work_id:
            raise ValueError("연결된 작품이 바뀌었습니다. 작품 메모를 다시 여세요.")

    @app.post("/api/glossary/save")
    def save_glossary(req: GlossarySaveRequest) -> dict:
        try:
            bound_work(req.session_id, req.work_id)
            glossary_store().save(req.work_id, req.model_dump())
            return glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/namu-lookup")
    def lookup_namu(q: str = "") -> dict:
        from pipeline.work_sources import namu_lookup
        try:
            return namu_lookup(q)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError:
            raise HTTPException(status_code=502, detail="나무위키에 연결하지 못했습니다. 직접 검색해 주소를 붙여 넣으세요.") from None

    @app.post("/api/glossary/draft")
    def draft_glossary(req: GlossaryDraftRequest) -> dict:
        try:
            bound_work(req.session_id, req.work_id)
            if len(req.text) > 16000:
                raise ValueError("붙여 넣는 자료는 16000자 이내로 입력하세요.")
            glossary_store().draft(req.work_id, req.url, req.text)
            return glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            # SDK 예외에는 키/요청 정보가 들어갈 수 있어 원문을 노출하지 않는다.
            raise HTTPException(status_code=400, detail="초안 생성에 실패했습니다. URL 접근이 막히면 본문을 직접 붙여 넣고, OpenAI 키·한도를 확인하세요.") from None

    @app.get("/api/speakers/{session_id}/{image_id}")
    def read_speakers(session_id: str, image_id: str) -> dict:
        try:
            item = get_image(session_id, image_id)
            record = {**image_record(session_id, Path(item["input_path"]), item.get("image_sha256"), item, probe_image=False, include_detail=False), "session_id": session_id}
            stored = load_json(speakers_path(session_id, image_id), default={})
            store, _report, _regions = reconciled_speaker_store(record, stored)
            return {"by_tg": page_speaker_map(session_id, image_id), "by_region": stored.get("by_region", {}),
                    "context_by_region": stored.get("context_by_region", {}),
                    "by_region_ref": store.get("by_region_ref", {}),
                    "context_by_region_ref": store.get("context_by_region_ref", {})}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _speaker_target_region(req_session: str, req_image: str, region_ref: str | None, region_id: int | None, region_key: str | None):
        """요청이 가리키는 현재 영역. region_ref가 있으면 그것으로, 없으면 구형 region_id + region_key로 찾는다."""
        from pipeline.region_types import region_binding_key
        item = get_image(req_session, req_image)
        record = {**image_record(req_session, Path(item["input_path"]), item.get("image_sha256"), item, probe_image=False, include_detail=False), "session_id": req_session}
        stored = load_json(speakers_path(req_session, req_image), default={})
        store, _report, regions = reconciled_speaker_store(record, stored)
        if region_ref:
            region = next((r for r in regions if r.get("region_ref") == region_ref), None)
        elif region_id is not None:
            region = next((r for r in regions if r.get("region_id") == region_id), None)
        else:
            return stored, store, regions, None
        if region is None:
            raise ValueError("현재 페이지에 없는 처리 영역입니다.")
        if not region_ref and region_key is not None and region_key != region_binding_key(region):
            raise ValueError("처리 영역이 변경됐습니다. 다시 선택하세요.")
        return stored, store, regions, region

    @app.post("/api/speakers")
    def assign_speaker(req: SpeakerRequest) -> dict:
        try:
            get_image(req.session_id, req.image_id)
            cid = req.character_id
            if cid and cid != NARRATION and cid not in character_map(session_glossary(req.session_id)):
                raise ValueError("작품 메모에 없는 인물입니다.")
            from pipeline.region_types import region_binding_key
            stored, store, regions, region = _speaker_target_region(req.session_id, req.image_id, req.region_ref, req.region_id, req.region_key)
            by_tg, by_region = stored.get("by_tg", {}), dict(stored.get("by_region", {}))
            by_region_ref = dict(store.get("by_region_ref", {}))
            tg_ids = req.tg_ids
            if region is not None:
                # null은 기존 TG 지정으로 되돌아가지 않는 명시적인 R 지정 해제다.
                by_region[region_binding_key(region)] = cid
                by_region_ref[region["region_ref"]] = cid
                tg_ids = []
            else:
                # 기존 TG 전용 클라이언트의 새 지정이 이전 R 직접 지정보다 우선한다.
                for current in regions:
                    if set(current.get("anchor_ids") or []) & set(tg_ids):
                        by_region.pop(region_binding_key(current), None)
                        by_region_ref.pop(current["region_ref"], None)
            for tg in tg_ids:
                if cid:
                    by_tg[str(int(tg))] = cid
                else:
                    by_tg.pop(str(int(tg)), None)
            context_ref = store.get("context_by_region_ref", {})
            write_json(speakers_path(req.session_id, req.image_id), {**stored, "by_tg": by_tg, "by_region": by_region,
                                                                    "by_region_ref": by_region_ref, "context_by_region_ref": context_ref,
                                                                    **_speaker_stash(stored, store)})
            return {"by_tg": by_tg, "by_region": by_region, "by_region_ref": by_region_ref,
                    "context_by_region": stored.get("context_by_region", {}), "context_by_region_ref": context_ref}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/ocr-stale/keep")
    def keep_stale_translation(req: PageRefRequest) -> dict:
        # 영역이 바뀐 뒤에도 기존 번역을 그대로 쓰기로 한 페이지. 번역 시점 서명을 현재 영역으로 갱신해 알림을 끈다.
        try:
            item = get_image(req.session_id, req.image_id)
            record = image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256"))
            status = load_json(Path(record["ocr_status_json"]), default={})
            if status.get("status") == "done":
                status["regions_signature"] = regions_signature(load_json(Path(record["regions_json"]), default={}))
                write_json(Path(record["ocr_status_json"]), status)
            return {"ok": True, "image": page_record(req.session_id, req.image_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/region-context")
    def save_region_context(req: RegionContextRequest) -> dict:
        # R별 번역 메모. 저장만 하고 번역은 실행하지 않는다. 다음 OCR/번역 때 프롬프트에 붙는다.
        try:
            get_image(req.session_id, req.image_id)
            from pipeline.region_types import region_binding_key
            if not req.region_ref and req.region_id is None:
                raise ValueError("메모를 붙일 영역이 없습니다.")
            stored, store, _regions, region = _speaker_target_region(req.session_id, req.image_id, req.region_ref, req.region_id, req.region_key)
            key = region_binding_key(region)
            text = req.text.strip()
            if len(text) > REGION_CONTEXT_LIMIT:
                raise ValueError(f"번역 메모는 {REGION_CONTEXT_LIMIT}자 이내로 적어 주세요.")
            contexts = dict(stored.get("context_by_region", {}))
            contexts_ref = dict(store.get("context_by_region_ref", {}))
            if text:
                contexts[key] = text
                contexts_ref[region["region_ref"]] = text
            else:
                contexts.pop(key, None)
                contexts_ref.pop(region["region_ref"], None)
            write_json(speakers_path(req.session_id, req.image_id), {**stored, "context_by_region": contexts,
                                                                    "by_region_ref": store.get("by_region_ref", {}), "context_by_region_ref": contexts_ref,
                                                                    **_speaker_stash(stored, store)})
            return {"context_by_region": contexts, "context_by_region_ref": contexts_ref}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/sessions/new")
    def create_session() -> dict[str, Any]:
        data = ensure_session()
        return {"session": data}

    @app.get("/api/sessions/recent")
    def recent_sessions() -> dict[str, Any]:
        return {"sessions": list_recent_sessions()}

    _CODE_MTIMES.update(loaded_code_mtimes())

    @app.post("/api/server/restart")
    def restart_server(force: bool = False) -> dict[str, Any]:
        busy = busy_job_count()
        if busy and not force:
            raise HTTPException(status_code=409, detail=f"진행 중인 작업 {busy}개가 있습니다. 재시작하면 중단됩니다.")
        threading.Thread(target=restart_current_process, daemon=True, name="server-restart").start()
        return {"ok": True, "interrupted": busy}

    @app.get("/api/health")
    def server_health() -> dict[str, Any]:
        # stale_code: 서버 시작 뒤 바뀐 Python 파일(재시작 필요). bundle: 화면 번들 버전(바뀌면 새로고침 필요).
        return {"status": "ok", "stale_code": stale_code_files(), "bundle": bundle_version()}

    def preparation_source(session_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id) or not session_file(session_id).is_file():
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
        data = load_json(session_file(session_id), default={})
        if not data.get("images"):
            raise HTTPException(status_code=400, detail="불러온 이미지가 없습니다.")
        return data

    @app.post("/api/pages/preview")
    def preview_pages(req: PagePreparationRequest) -> dict[str, Any]:
        source = preparation_source(req.session_id)
        try:
            plan = build_plan([item for item in sorted_image_records(source["images"]) if not is_split_page(item)], numbered=False)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        by_id = {item["id"]: item for item in source["images"]}
        for page in plan["pages"]:
            page["source_url"] = file_url(page["source_path"])
            page["has_work"] = page["mode"] != "keep" and page_has_work(req.session_id, by_id[page["image_id"]])
        return plan

    @app.post("/api/pages/override")
    def override_page_split(req: PageSplitOverrideRequest) -> dict[str, Any]:
        """페이지별 나누기 방식을 프로젝트에 저장한다. 미리보기와 분석 시작 때의 자동 나누기 모두 이 값을 따른다."""
        preparation_source(req.session_id)
        if req.mode not in {"auto", "keep", "split"} or (req.mode == "split" and req.split_x is None):
            raise HTTPException(status_code=400, detail="나누기 방식이 올바르지 않습니다.")
        data = ensure_session(req.session_id)
        item = next((image for image in data.get("images", []) if image["id"] == req.image_id), None)
        if item is None or is_split_page(item):
            raise HTTPException(status_code=404, detail="나눌 수 있는 원본 페이지가 아닙니다.")
        if req.mode == "auto":
            item.pop("split_override", None)
        else:
            item["split_override"] = {"mode": "keep"} if req.mode == "keep" else {"mode": "split", "split_x": int(req.split_x)}
        save_session(data)
        try:
            page = build_plan([item], numbered=False)["pages"][0]
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        page["source_url"] = file_url(page["source_path"])
        page["has_work"] = page["mode"] != "keep" and page_has_work(req.session_id, item)
        return {"page": page}

    @app.post("/api/pages/split")
    def split_pages(req: PageSplitRequest) -> dict[str, Any]:
        preparation_source(req.session_id)
        try:
            return split_wide_pages_in_session(req.session_id, include_worked=req.include_worked)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"같은 이름의 파일이 이미 있어 나눌 수 없습니다: {Path(str(exc)).name}") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    @app.get("/api/sources/{session_id}")
    def input_sources(session_id: str):
        if not session_file(session_id).is_file():raise HTTPException(status_code=404,detail="프로젝트가 없습니다")
        data=ensure_session(session_id)
        pages=data.get("images",[])
        return {"session_id":session_id,"total":len(pages),
            "connected":sum(p.get("source_status")=="connected" for p in pages),
            "fallback":sum(p.get("source_status")=="fallback" for p in pages),
            "missing":sum(p.get("source_status")=="missing" for p in pages),
            "owned":sum(not p.get("source_ref") for p in pages)}

    @app.post("/api/sources/preview")
    def preview_input_sources(req: SourceFolderRequest):
        req=req.model_dump()
        if not session_file(req["session_id"]).is_file():raise HTTPException(status_code=404,detail="프로젝트가 없습니다")
        try:
            return source_library().preview(ensure_session(req["session_id"]), req["folder"])
        except (OSError,ValueError,KeyError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @app.post("/api/sources/relink")
    def relink_input_sources(req: SourceFolderRequest):
        req=req.model_dump()
        if not session_file(req["session_id"]).is_file():raise HTTPException(status_code=404,detail="프로젝트가 없습니다")
        try:
            data=ensure_session(req["session_id"])
            plan=source_library().preview(data, req["folder"])
            if not req.get("revision") or req["revision"]!=plan["revision"]:
                raise HTTPException(status_code=409,detail="원본 또는 프로젝트가 변경됐습니다. 다시 확인하세요")
            result=source_library().apply(data,session_dir(req["session_id"]),plan)
            return {"matched":result["matched"],"unmatched":result["unmatched"],"status":input_sources(req["session_id"])}
        except (OSError,ValueError,KeyError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @app.get("/api/fs/list")
    def list_server_folder(path: str = "") -> dict[str, Any]:
        return server_folder_listing(path)

    @app.post("/api/import-path")
    def import_path(req: ImportPathRequest) -> dict[str, Any]:
        data = ensure_session(req.session_id)
        src = Path(req.path).expanduser()
        paths = scan_images(src, req.recursive)
        records = copy_images_into_session(paths, data["session_id"])
        if records:
            data = ensure_session(data["session_id"])
            if src.is_dir():
                data["source_folder_name"] = src.name
            if not data.get("project_name"):
                data["project_name"] = (src.name if src.is_dir() else src.stem)[:120]
            save_session(data)
        return {"session_id": data["session_id"], "imported": len(records), "images": records}

    @app.post("/api/upload")
    def upload_files(session_id: str | None = None, files: list[UploadFile] = File(...), source_folder_name: str = "") -> dict[str, Any]:
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
        if copied:
            folder_name = source_folder_name.strip()[:120]
            if folder_name:
                current["source_folder_name"] = folder_name
            if not current.get("project_name"):
                current["project_name"] = folder_name or copied[0].stem[:120]
        known = {item["id"] for item in current.get("images", [])}
        for path in copied:
            if path.stem in known:
                continue
            image_hash = file_sha256(path)
            item = image_manifest_item(path, image_hash, {"input_storage":"upload"})
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
            if page_processing_excluded(req.session_id, item["id"]):
                logs.append(f"[{item['id']}] 페이지 처리 제외 · 원본 출력")
                continue
            image_hash = ensure_record_hash(item)
            record = image_record(req.session_id, Path(item["input_path"]), image_hash)
            if hydrate_analysis_cache(record, image_hash, req.force, logs.append, allow_backfill=True):
                reapply_guidance_if_present(req.session_id, record, logs.append)
                continue
            cv_ready = hydrate_cv_cache(record, image_hash, req.force, logs.append, item)
            _unlink_symlinked_analysis_outputs(record)
            stop_after = req.stop_after if req.stop_after in {"pre_ocr", "text_groups"} else "pre_ocr"
            ok, log = run_single_image(
                record["input_path"],
                record["work_dir"],
                record["final_jpg"],
                force=req.force,
                _skip_step1=cv_ready,
                stop_after=stop_after,
            )
            logs.append(log)
            if not ok:
                raise HTTPException(status_code=500, detail=log)
            store_cv_cache(record, image_hash)
            if stop_after == "pre_ocr":
                # Step 3 산출물이 없는 부분 분석은 분석 캐시에 넣지 않는다(다음 분석이 빈 결과를 재사용하지 않게).
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
        if page_processing_excluded(req.session_id, req.image_id):
            return {"ok": True, "image": page_record(req.session_id, req.image_id)}
        try:
            context = session_glossary_snapshot(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
                replace_overlay=req.replace,
                glossary_context=context,
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
        if page_processing_excluded(req.session_id, req.image_id):
            raise HTTPException(status_code=409, detail="처리 제외 페이지입니다. 처리 대상으로 복원한 뒤 편집하세요.")
        source = get_image(req.session_id, req.image_id)
        # 렌더에는 경로만 필요하다. 원본 전체를 여는 여백 probe는 하지 않고 manifest 크기 정보를 쓴다.
        record = image_record(req.session_id, Path(source["input_path"]), source.get("image_sha256"), source, probe_image=False, include_detail=False)
        store_overlay_guidance(record, req.overlay_guidance, req.replace)
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
        if resolved.suffix.lower() not in IMAGE_EXTS or not (any(
            resolved.is_relative_to(root.resolve()) for root in servable_roots
        ) or source_library().allowed_source(resolved, SESSION_ROOT)):
            raise HTTPException(status_code=403, detail="File outside workspace is not served")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(resolved)

    @app.get("/api/restore/status")
    def restore_status() -> dict[str, Any]:
        from pipeline.background_restore import ENGINES, RESOLUTION_CHOICES

        return {
            "engines": list(ENGINES),
            "resolutions": list(RESOLUTION_CHOICES),
        }

    @app.get("/api/restore/info")
    def restore_info_endpoint(session_id: str, image_id: str) -> dict[str, Any]:
        return restore_info(_restore_record(session_id, image_id))

    @app.post("/api/restore/auto-mask")
    def restore_auto_mask(req: RestoreAutoMaskRequest) -> dict[str, Any]:
        return build_restore_auto_mask(req)

    @app.post("/api/restore/mask")
    def restore_save_mask(req: RestoreMaskRequest) -> dict[str, Any]:
        import cv2

        from pipeline.background_restore import decode_mask_png

        record = _restore_record(req.session_id, req.image_id)
        original = _read_bgr(record["input_path"])
        mask = decode_mask_png(req.mask_png, original.shape[1], original.shape[0])
        Path(record["restore_dir"]).mkdir(parents=True, exist_ok=True)
        cv2.imwrite(record["restore_mask_png"], mask)
        return restore_info(_restore_record(req.session_id, req.image_id))

    @app.post("/api/restore/ai-erase")
    def restore_ai_erase(req: AiEraseRequest) -> dict[str, Any]:
        return submit_ai_erase_job(req)

    @app.post("/api/restore/run")
    def restore_run(req: RestoreRunRequest) -> dict[str, Any]:
        return submit_restore_job(req)

    @app.get("/api/restore/job/{job_id}")
    def restore_job(job_id: str) -> dict[str, Any]:
        with _RESTORE_JOBS_LOCK:
            job = _RESTORE_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="복원 작업을 찾을 수 없습니다")
        return _restore_job_public(job)

    @app.post("/api/restore/accept")
    def restore_accept(req: RestoreAcceptRequest) -> dict[str, Any]:
        return accept_restore_job(req)

    @app.post("/api/restore/patch/remove")
    def restore_patch_remove(req: RestorePatchRemoveRequest) -> dict[str, Any]:
        return remove_restore_patch(req)

    @app.get("/api/ocr-models")
    def list_ocr_models() -> dict[str, Any]:
        """Step 4에서 선택 가능한 OCR/번역 모델 목록."""
        from pipeline.gemini_image import GEMINI_IMAGE_MODEL
        from pipeline.gemini_ocr_translate_v5 import MODEL_NAME, SUPPORTED_OCR_MODELS

        # 이미지 작업(글자 지우기·통번역 재생성)은 OCR·번역 모델의 제공자를 따라간다
        return {"default": MODEL_NAME, "models": SUPPORTED_OCR_MODELS,
                "image_models": {"openai": IMAGE_MODEL, "gemini": GEMINI_IMAGE_MODEL}}

    @app.get("/api/fonts")
    def list_fonts() -> dict[str, Any]:
        fonts_dir = ROOT / "Fonts"
        fonts = sorted([*fonts_dir.glob("*.ttf"), *fonts_dir.glob("*.otf")]) if fonts_dir.exists() else []
        return {
            "default": DEFAULT_FONT,
            "fonts": [{"name": path.stem, "path": str(path)} for path in fonts],
        }

    @app.get("/manual")
    def editor_manual() -> FileResponse:
        # 사용자 매뉴얼은 docs/editor_manual.html 하나를 정본으로 두고 그대로 보여 준다.
        return FileResponse(ROOT / "docs" / "editor_manual.html", media_type="text/html")

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
    # 앱 안에서 재시작한 경우에는 브라우저 탭을 새로 열지 않는다.
    if not args.no_browser and not os.environ.get("COMIC_TRANSLATOR_RESTARTED"):
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
