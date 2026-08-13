# 표준 파이프라인 실행 엔진
#!/usr/bin/env python3

import json
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np

PIPELINE_DIR = Path(__file__).parent
DEFAULT_FONT = str(PIPELINE_DIR.parent / "Fonts" / "Freesentation-5Medium.ttf")
PYTHON = sys.executable
LogCallback = Callable[[str], None]

STEP_TIMEOUT = {
    "vision": 60,
    "cluster": 30,
    "extract": 300,   # dialogue_extractor (LaMa 포함)
    "gemini": 300,
    "overlay": 60,
}
OCR_SKIP_REASON_ANALYSIS_NOT_RUN = "analysis_not_run"
OCR_SKIP_REASON_NO_REGIONS = "no_ocr_regions"
OCR_SKIP_REASON_MISSING_SHEET = "missing_ocr_sheet"


class PipelineError(Exception):
    pass


def _append_log(log: list[str], message: str, log_callback: LogCallback | None = None) -> None:
    log.append(message)
    if log_callback is not None:
        log_callback(message)


def _pipeline_paths(work_dir: Path, name: str) -> dict[str, Path]:
    json_dir = work_dir / "json"
    masked_dir = work_dir / "masked"
    sheets_dir = work_dir / "sheets"
    merge_dir = work_dir / "merge"
    vis_dir = work_dir / "vis"

    for path in (json_dir, masked_dir, sheets_dir, merge_dir, vis_dir):
        path.mkdir(parents=True, exist_ok=True)

    return {
        "cv_json":           json_dir   / f"{name}_cv.json",
        "clusters_json":     json_dir   / f"{name}_text_groups.json",
        "regions_json":      json_dir   / f"{name}_regions.json",
        "integrated_json":   json_dir   / f"{name}_integrated.json",
        "masked_jpg":        masked_dir / f"{name}_masked.jpg",
        "indexed_sheet_jpg": sheets_dir / f"{name}_indexed_sheet.jpg",
        "bubble_vis_jpg":    vis_dir    / f"{name}_bubble_vis.jpg",
        "merge_jpg":         merge_dir  / f"{name}_merge.jpg",
    }


def _ocr_skip_message(reason: str) -> str:
    if reason == OCR_SKIP_REASON_ANALYSIS_NOT_RUN:
        return "말풍선 분석 산출물이 없어 OCR/번역을 건너뜁니다."
    if reason == OCR_SKIP_REASON_NO_REGIONS:
        return "말풍선 분석은 완료됐지만 OCR 대상 region이 없어 OCR/번역을 건너뜁니다."
    return "OCR sheet 산출물이 불완전해 Gemini OCR/번역을 건너뜁니다."


def _analysis_state(regions_json: Path, indexed_sheet_jpg: Path) -> dict[str, object]:
    if not regions_json.exists():
        return {
            "analysis_status": "not_run",
            "ocr_region_count": None,
            "reason": OCR_SKIP_REASON_ANALYSIS_NOT_RUN,
            "missing_inputs": ["regions_json"],
        }
    try:
        payload = json.loads(regions_json.read_text(encoding="utf-8"))
    except Exception:
        return {
            "analysis_status": "invalid",
            "ocr_region_count": None,
            "reason": OCR_SKIP_REASON_MISSING_SHEET,
            "missing_inputs": ["regions_json"],
        }
    regions = payload.get("regions") if isinstance(payload, dict) else []
    if not isinstance(regions, list):
        return {
            "analysis_status": "invalid",
            "ocr_region_count": None,
            "reason": OCR_SKIP_REASON_MISSING_SHEET,
            "missing_inputs": ["regions_json"],
        }
    if len(regions) == 0:
        return {
            "analysis_status": payload.get("analysis_status") or "complete",
            "ocr_region_count": 0,
            "reason": OCR_SKIP_REASON_NO_REGIONS,
            "missing_inputs": [],
        }
    if not indexed_sheet_jpg.exists():
        return {
            "analysis_status": payload.get("analysis_status") or "complete",
            "ocr_region_count": len(regions),
            "reason": OCR_SKIP_REASON_MISSING_SHEET,
            "missing_inputs": ["indexed_sheet_jpg"],
        }
    return {
        "analysis_status": payload.get("analysis_status") or "complete",
        "ocr_region_count": len(regions),
        "reason": None,
        "missing_inputs": [],
    }


def _write_ocr_skip_marker(integrated_json: Path, reason: str, missing: list[str], analysis_state: dict[str, object]) -> None:
    integrated_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "skipped",
        "skipped": True,
        "stage": "ocr_translation",
        "reason": reason,
        "message": _ocr_skip_message(reason),
        "missing_inputs": missing,
        "analysis_status": analysis_state.get("analysis_status"),
        "ocr_region_count": analysis_state.get("ocr_region_count"),
        "clusters": [],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    integrated_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _integrated_is_skipped(integrated_json: Path) -> bool:
    if not integrated_json.exists():
        return False
    try:
        payload = json.loads(integrated_json.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(payload, dict) and bool(payload.get("skipped"))


def _make_merge(left_path: Path, right_path: Path, out_path: Path) -> None:
    left = cv2.imread(str(left_path))
    right = cv2.imread(str(right_path))
    if left is None or right is None:
        return
    h = max(left.shape[0], right.shape[0])

    def _pad(img: np.ndarray) -> np.ndarray:
        pad = h - img.shape[0]
        return img if pad <= 0 else np.vstack([img, np.full((pad, img.shape[1], 3), 255, dtype=np.uint8)])

    gap = np.full((h, 18, 3), 245, dtype=np.uint8)
    cv2.imwrite(str(out_path), np.hstack([_pad(left), gap, _pad(right)]))


def _run_step(
    cmd: list,
    timeout: int,
    step_name: str,
    log_callback: LogCallback | None = None,
) -> str:
    started = time.time()
    if log_callback is not None:
        log_callback(f"  ▶ {step_name} subprocess 시작")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()

    def _read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line.rstrip("\n"))
        output_queue.put(None)

    reader = threading.Thread(target=_read_output, daemon=True)
    reader.start()

    lines: list[str] = []
    stream_done = False
    while True:
        if time.time() - started > timeout:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise subprocess.TimeoutExpired(cmd, timeout, output="\n".join(lines))

        try:
            line = output_queue.get(timeout=0.1)
        except queue.Empty:
            line = None
        else:
            if line is None:
                stream_done = True
            else:
                lines.append(line)
                if log_callback is not None:
                    log_callback(f"    {line}")

        returncode = process.poll()
        if returncode is not None and stream_done:
            break

    reader.join(timeout=1)
    stdout = "\n".join(lines)
    if process.returncode != 0:
        raise PipelineError(
            f"{step_name} 실패 (exit {process.returncode}):\n{stdout}"
        )
    if log_callback is not None:
        log_callback(f"  ◀ {step_name} subprocess 종료 ({time.time() - started:.1f}s)")
    return stdout


def run_single_image(
    image_path: str | Path,
    work_dir: str | Path,
    output_path: str | Path,
    extra_prompt: str = "",
    force: bool = False,
    font_path: str | None = None,
    _skip_step1: bool = False,
    tracking_offset: int | None = None,
    log_callback: LogCallback | None = None,
    stop_after: str = "overlay",
    ocr_model: str | None = None,
) -> tuple[bool, str]:
    image_path = Path(image_path)
    work_dir = Path(work_dir)
    output_path = Path(output_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    name = image_path.stem
    log = []
    t0 = time.time()

    def elapsed() -> str:
        return f"{time.time() - t0:.1f}s"

    paths = _pipeline_paths(work_dir, name)
    cv_json           = paths["cv_json"]
    clusters_json     = paths["clusters_json"]
    regions_json      = paths["regions_json"]
    integrated_json   = paths["integrated_json"]
    masked_jpg        = paths["masked_jpg"]
    indexed_sheet_jpg = paths["indexed_sheet_jpg"]
    bubble_vis_jpg    = paths["bubble_vis_jpg"]
    merge_jpg         = paths["merge_jpg"]

    _append_log(log, f"📸 {name} 처리 시작", log_callback)

    if not force and output_path.exists():
        _append_log(log, f"  ✅ 캐시: {output_path.name}", log_callback)
        return True, "\n".join(log)

    try:
        # Step 1: Cloud Vision
        if not _skip_step1:
            _append_log(log, "\n🔍 Step 1: Cloud Vision", log_callback)
            step_started = time.time()
            if not cv_json.exists() or force:
                _run_step(
                    [PYTHON, str(PIPELINE_DIR / "cloud_vision_api.py"), str(image_path), str(cv_json)],
                    STEP_TIMEOUT["vision"], "Cloud Vision", log_callback,
                )
                _append_log(log, "  ✅ 완료", log_callback)
            else:
                _append_log(log, "  ✅ 캐시 사용", log_callback)
            _append_log(log, f"  ⏱️ 단계 소요 {time.time() - step_started:.1f}s / 누적 {elapsed()}", log_callback)

        # Step 2: Text Grouping
        _append_log(log, "\n🔗 Step 2: Text Grouping", log_callback)
        step_started = time.time()
        if not clusters_json.exists() or force:
            img = cv2.imread(str(image_path))
            img_h = img.shape[0] if img is not None else 1920
            _run_step(
                [
                    PYTHON, "-m", "pipeline.text_group_builder_v2",
                    str(cv_json), str(clusters_json), str(img_h),
                ],
                STEP_TIMEOUT["cluster"], "Text Grouping", log_callback,
            )
            _append_log(log, "  ✅ 완료", log_callback)
        else:
            _append_log(log, "  ✅ 캐시 사용", log_callback)
        _append_log(log, f"  ⏱️ 단계 소요 {time.time() - step_started:.1f}s / 누적 {elapsed()}", log_callback)

        # Step 3: 만화 대사 추출 — Bubble Detect + Unboxed Erase(LaMa) + OCR Sheet
        _append_log(log, "\n🗨️ Step 3: 만화 대사 추출", log_callback)
        step_started = time.time()
        if not masked_jpg.exists() or not regions_json.exists() or force:
            _run_step(
                [
                    PYTHON, "-m", "pipeline.dialogue_extractor",
                    str(image_path), str(clusters_json),
                    str(masked_jpg), str(regions_json), str(indexed_sheet_jpg),
                    "--output-vis-path", str(bubble_vis_jpg),
                ],
                STEP_TIMEOUT["extract"], "Dialogue Extractor", log_callback,
            )
            _append_log(log, "  ✅ 완료", log_callback)
        else:
            _append_log(log, "  ✅ 캐시 사용", log_callback)
        _append_log(log, f"  ⏱️ 단계 소요 {time.time() - step_started:.1f}s / 누적 {elapsed()}", log_callback)

        if stop_after in {"extract", "pre_ocr"}:
            total = time.time() - t0
            _append_log(
                log,
                (
                    f"\n✅ OCR 전단계 완료: {name} (총 {total:.1f}s)\n"
                    f"  regions: {regions_json}\n"
                    f"  masked: {masked_jpg}\n"
                    f"  indexed sheet: {indexed_sheet_jpg}\n"
                    f"  vis: {bubble_vis_jpg}"
                ),
                log_callback,
            )
            return True, "\n".join(log)

        # Step 4: Gemini OCR + 번역
        _append_log(log, "\n🤖 Step 4: Gemini OCR/Translation", log_callback)
        step_started = time.time()
        _analysis = _analysis_state(regions_json, indexed_sheet_jpg)
        # Step 3 산출물이 바뀌면 region_id / sheet_rect / anchor_ids 매핑이 달라질 수 있으므로
        # 기존 integrated.json 은 무효화한다.
        _ocr_inputs = [regions_json, indexed_sheet_jpg, clusters_json]
        _stale_integrated = (
            integrated_json.exists()
            and any(
                path.exists() and path.stat().st_mtime > integrated_json.stat().st_mtime
                for path in _ocr_inputs
            )
        )
        if _analysis.get("reason"):
            _missing_ocr_inputs = list(_analysis.get("missing_inputs") or [])
            _write_ocr_skip_marker(integrated_json, str(_analysis["reason"]), _missing_ocr_inputs, _analysis)
            if output_path.exists():
                output_path.unlink()
            suffix = f"({', '.join(_missing_ocr_inputs)})" if _missing_ocr_inputs else ""
            _append_log(
                log,
                f"  ⚠️ {_ocr_skip_message(str(_analysis['reason']))} {suffix}".strip(),
                log_callback,
            )
        elif not integrated_json.exists() or force or _stale_integrated or _integrated_is_skipped(integrated_json):
            if _stale_integrated and not force:
                _append_log(log, "  ⚠️ OCR 입력 변경 감지 → integrated.json 재생성", log_callback)
            _run_step(
                [
                    PYTHON, "-m", "pipeline.gemini_ocr_translate_v5",
                    str(image_path), str(clusters_json), str(integrated_json),
                    "--ocr-sheet-image", str(indexed_sheet_jpg),
                    "--ocr-sheet-json", str(regions_json),
                    "--extra-prompt", extra_prompt,
                    *(["--model", ocr_model] if ocr_model else []),
                ],
                STEP_TIMEOUT["gemini"], "Gemini OCR", log_callback,
            )
            _append_log(log, "  ✅ 완료", log_callback)
        else:
            _append_log(log, "  ✅ 캐시 사용", log_callback)
        _append_log(log, f"  ⏱️ 단계 소요 {time.time() - step_started:.1f}s / 누적 {elapsed()}", log_callback)

        # Step 5: Text Overlay
        _append_log(log, "\n✍️ Step 5: Text Overlay", log_callback)
        step_started = time.time()
        if _integrated_is_skipped(integrated_json):
            _append_log(log, "  ⚠️ OCR/번역 스킵 상태라 Text Overlay도 건너뜀", log_callback)
            _append_log(log, f"  ⏱️ 단계 소요 {time.time() - step_started:.1f}s / 누적 {elapsed()}", log_callback)
            return True, "\n".join(log)
        _regions = paths["regions_json"] if paths["regions_json"].exists() else None
        overlay_cmd = [
            PYTHON, "-m", "pipeline.render_text_overlay",
            str(masked_jpg), str(integrated_json),
            str(clusters_json),  # 호환성 유지 (미사용)
            str(output_path),
        ]
        if font_path:
            overlay_cmd.append(str(font_path))
        if _regions:
            overlay_cmd += ["--regions_json", str(_regions), "--original_image", str(image_path)]
        if tracking_offset is not None:
            overlay_cmd += ["--tracking-offset", str(tracking_offset)]
        _run_step(overlay_cmd, STEP_TIMEOUT["overlay"], "Text Overlay", log_callback)
        _append_log(log, "  ✅ 완료", log_callback)
        _append_log(log, f"  ⏱️ 단계 소요 {time.time() - step_started:.1f}s / 누적 {elapsed()}", log_callback)

        left_for_merge = bubble_vis_jpg if bubble_vis_jpg.exists() else masked_jpg
        _make_merge(left_for_merge, output_path, merge_jpg)

        total = time.time() - t0
        _append_log(log, f"\n✅ 완료: {output_path.name} (총 {total:.1f}s)", log_callback)
        return True, "\n".join(log)

    except PipelineError as e:
        _append_log(log, f"\n❌ 실패: {e}", log_callback)
        return False, "\n".join(log)
    except subprocess.TimeoutExpired as e:
        _append_log(log, f"\n❌ 타임아웃: {e}", log_callback)
        return False, "\n".join(log)
    except Exception as e:
        _append_log(log, f"\n❌ 예외: {e}", log_callback)
        return False, "\n".join(log)


def run_batch_images(
    image_paths: list[Path],
    work_dir: Path,
    output_dir: Path,
    extra_prompt: str = "",
    force: bool = False,
    font_path: str | None = None,
    max_workers: int = 8,
    tracking_offset: int | None = None,
    log_callback: LogCallback | None = None,
    stop_after: str = "overlay",
    ocr_model: str | None = None,
) -> list[tuple[bool, str, Path]]:
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 이미지별 병렬 실행. Step 1 Cloud Vision도 각 worker에서 단일 이미지 단위로 수행한다.
    results: list[tuple[bool, str, Path]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(image_paths))) as executor:
        futures = {
            executor.submit(
                run_single_image,
                img,
                work_dir,
                output_dir / "final" / f"{img.stem}_final.jpg",
                extra_prompt,
                force,
                font_path,
                False,
                tracking_offset,
                log_callback,
                stop_after,
                ocr_model,
            ): img
            for img in image_paths
        }
        for future in as_completed(futures):
            img = futures[future]
            ok, log = future.result()
            results.append((ok, log, img))

    return results
