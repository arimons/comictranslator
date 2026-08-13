# 말풍선 밖 배경 텍스트 후보를 TextGroup 기반 local mask로 검증합니다.
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np

try:
    from pipeline.bubble_detector import _compute_flood_bimodality
    from pipeline.geometry import as_rect as _as_rect, union_rect as _union_rect
except ImportError:
    from bubble_detector import _compute_flood_bimodality
    from geometry import as_rect as _as_rect, union_rect as _union_rect


ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _pad_rect(rect: List[int], pad: int, img_w: int, img_h: int) -> List[int]:
    x, y, w, h = _as_rect(rect)
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img_w, x + w + pad)
    y2 = min(img_h, y + h + pad)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _rect_to_local(rect: List[int], roi_rect: List[int]) -> List[int]:
    return [rect[0] - roi_rect[0], rect[1] - roi_rect[1], rect[2], rect[3]]


def _target_text_groups(groups: List[Dict[str, Any]], tg_ids: List[int]) -> List[Dict[str, Any]]:
    wanted = set(tg_ids)
    return [g for g in groups if int(g.get("text_group_id", g.get("cluster_id", -1))) in wanted]


def _region_by_id(regions: Dict[str, Any], region_id: int) -> Dict[str, Any] | None:
    for region in regions.get("regions", []):
        if int(region.get("region_id", -1)) == region_id:
            return region
    return None


def _fragment_rects(groups: List[Dict[str, Any]]) -> List[List[int]]:
    rects: List[List[int]] = []
    for group in groups:
        for line in group.get("lines", []):
            fragments = line.get("fragments") or []
            if not fragments:
                rects.append(_as_rect(line.get("bbox", [0, 0, 0, 0])))
                continue
            for fragment in fragments:
                rects.append(_as_rect(fragment.get("bbox", [0, 0, 0, 0])))
    return rects


def _looks_vertical(groups: List[Dict[str, Any]]) -> bool:
    """세로 텍스트 그룹인지 보수적으로 판단합니다."""
    if any(str(g.get("orientation", "")).lower() == "vertical" for g in groups):
        return True
    rects = [_as_rect(g.get("tight_rect", g.get("bounding_rect", [0, 0, 0, 0]))) for g in groups]
    rects = [r for r in rects if r[2] > 0 and r[3] > 0]
    if not rects:
        return False
    union = _union_rect(rects)
    return union[3] >= max(1, union[2]) * 1.35


def _build_lane_mask(shape: tuple[int, int], groups: List[Dict[str, Any]], roi_rect: List[int], pad: int) -> np.ndarray:
    h, w = shape
    mask = np.zeros((h, w), dtype=np.uint8)
    for rect in _fragment_rects(groups):
        lx, ly, lw, lh = _rect_to_local(rect, roi_rect)
        x1 = max(0, lx - pad)
        y1 = max(0, ly - pad)
        x2 = min(w, lx + lw + pad)
        y2 = min(h, ly + lh + pad)
        if x2 > x1 and y2 > y1:
            cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    return mask


def _build_vertical_furigana_band(
    shape: tuple[int, int],
    groups: List[Dict[str, Any]],
    roi_rect: List[int],
    *,
    side_gap: int = 1,
    band_width: int = 32,
    y_pad: int = 4,
) -> np.ndarray:
    """세로 본문 오른쪽 후리가나 후보 영역을 만듭니다."""
    h, w = shape
    band = np.zeros((h, w), dtype=np.uint8)
    for rect in _fragment_rects(groups):
        lx, ly, lw, lh = _rect_to_local(rect, roi_rect)
        if lw <= 0 or lh <= 0:
            continue
        x1 = max(0, lx + lw + side_gap)
        x2 = min(w, lx + lw + band_width)
        y1 = max(0, ly - y_pad)
        y2 = min(h, ly + lh + y_pad)
        if x2 > x1 and y2 > y1:
            cv2.rectangle(band, (x1, y1), (x2, y2), 255, -1)
    return band


def _build_furigana_side_mask(
    roi: np.ndarray,
    groups: List[Dict[str, Any]],
    roi_rect: List[int],
    text_mask: np.ndarray,
    dark_threshold: int,
) -> tuple[np.ndarray, Dict[str, Any]]:
    """TG에서 제외된 오른쪽 후리가나를 픽셀 기반으로 보조 검출합니다."""
    if not _looks_vertical(groups):
        return np.zeros(text_mask.shape, dtype=np.uint8), {
            "furigana_added_pixels": 0,
            "furigana_components": 0,
            "furigana_pass": "skipped_non_vertical",
        }

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    band = _build_vertical_furigana_band(gray.shape, groups, roi_rect)
    if not np.any(band):
        return np.zeros(text_mask.shape, dtype=np.uint8), {
            "furigana_added_pixels": 0,
            "furigana_components": 0,
            "furigana_pass": "empty_band",
        }

    threshold = min(int(dark_threshold), 190)
    dark = np.where((gray <= threshold) & (band > 0), 255, 0).astype(np.uint8)
    # 작은 후리가나 획을 살리기 위해 open은 쓰지 않고 component 필터만 적용합니다.
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(dark, connectivity=8)
    filtered = np.zeros_like(dark)
    components = 0
    for label in range(1, num_labels):
        x, y, w, h, area = [int(v) for v in stats[label]]
        if area < 6 or area > 180:
            continue
        if w < 1 or h < 2 or w > 24 or h > 32:
            continue
        fill_ratio = area / max(1, w * h)
        if fill_ratio > 0.88 and w > 3:
            continue
        px1 = max(0, x - 12)
        py1 = max(0, y - 12)
        px2 = min(dark.shape[1], x + w + 12)
        py2 = min(dark.shape[0], y + h + 12)
        patch = dark[py1:py2, px1:px2]
        local_dark_density = float(np.count_nonzero(patch)) / max(1, patch.size)
        if local_dark_density > 0.22:
            continue
        filtered[labels == label] = 255
        components += 1

    if components == 0:
        return filtered, {
            "furigana_added_pixels": 0,
            "furigana_components": 0,
            "furigana_pass": "no_components",
        }

    filtered = cv2.dilate(filtered, np.ones((3, 3), dtype=np.uint8), iterations=1)
    new_pixels = int(np.count_nonzero((filtered > 0) & (text_mask == 0)))
    return filtered, {
        "furigana_added_pixels": new_pixels,
        "furigana_components": components,
        "furigana_pass": "right_side_band",
        "furigana_threshold": threshold,
    }


def _lane_bimodality(roi: np.ndarray, lane_mask: np.ndarray) -> float:
    """lane 영역이 흰 배경+검은 글자 구조인지 점수화합니다."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return float(_compute_flood_bimodality(np.where(lane_mask > 0, 255, 0).astype(np.uint8), gray))


def _classify_inpaint_background(roi: np.ndarray, text_mask: np.ndarray) -> Dict[str, Any]:
    """마스크 주변 배경 통계로 unboxed inpaint 엔진을 추천합니다."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    outer = cv2.dilate(text_mask, np.ones((25, 25), dtype=np.uint8), iterations=1)
    inner = cv2.dilate(text_mask, np.ones((7, 7), dtype=np.uint8), iterations=1)
    ring = (outer > 0) & (inner == 0)
    if int(np.count_nonzero(ring)) < 20:
        ring = (outer > 0) & (text_mask == 0)

    pixels = gray[ring]
    if pixels.size == 0:
        return {
            "background_class": "unknown",
            "recommended_inpaint_engine": "telea",
            "bg_mean": 0.0,
            "bg_std": 0.0,
            "bg_dark_ratio": 0.0,
            "bg_light_ratio": 0.0,
            "bg_edge_density": 0.0,
        }

    edges = cv2.Canny(gray, 80, 160)
    edge_density = float(np.count_nonzero(edges[ring])) / max(1, int(np.count_nonzero(ring)))
    bg_mean = float(np.mean(pixels))
    bg_std = float(np.std(pixels))
    dark_ratio = float(np.count_nonzero(pixels < 80)) / pixels.size
    light_ratio = float(np.count_nonzero(pixels > 220)) / pixels.size

    if bg_mean >= 235 and bg_std < 18:
        background_class = "white_flat"
        engine = "telea"
    elif (
        bg_mean >= 220
        and dark_ratio < 0.03
        and light_ratio >= 0.75
        and (edge_density >= 0.06 or bg_std >= 18)
    ):
        background_class = "screentone_light"
        engine = "lama"
    elif bg_mean >= 210 and bg_std < 35:
        background_class = "light_flat"
        engine = "telea"
    elif bg_mean >= 180 and bg_std < 60 and edge_density < 0.12 and dark_ratio < 0.15:
        background_class = "screentone_light"
        engine = "lama"
    elif bg_mean < 110 or dark_ratio > 0.35:
        background_class = "dark_bg"
        engine = "lama"
    elif edge_density > 0.12 or bg_std >= 60:
        background_class = "mixed_line_art"
        engine = "lama"
    else:
        background_class = "fallback_lightish"
        engine = "telea"

    return {
        "background_class": background_class,
        "recommended_inpaint_engine": engine,
        "bg_mean": round(bg_mean, 2),
        "bg_std": round(bg_std, 2),
        "bg_dark_ratio": round(dark_ratio, 4),
        "bg_light_ratio": round(light_ratio, 4),
        "bg_edge_density": round(edge_density, 4),
    }


def _conservative_dark_threshold(bimodality: float) -> int:
    """배경이 복잡할수록 보수적인 dark-only threshold를 사용합니다."""
    if bimodality <= 0.65:
        return 150
    if bimodality >= 0.75:
        return 170
    t = (bimodality - 0.65) / 0.10
    return int(round(150 + 20 * t))


def _build_conservative_dark_mask(
    gray: np.ndarray,
    lane_mask: np.ndarray,
    bimodality: float,
    *,
    reason: str,
) -> tuple[np.ndarray, Dict[str, Any]]:
    threshold = _conservative_dark_threshold(bimodality)
    _, dark = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
    mask = cv2.bitwise_and(dark, lane_mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
    mask = cv2.dilate(mask, np.ones((3, 3), dtype=np.uint8), iterations=1)
    lane_pixels = max(1, int(np.count_nonzero(lane_mask)))
    return mask, {
        "mask_mode": "conservative_dark",
        "mask_reason": reason,
        "lane_bimodality": round(bimodality, 4),
        "effective_dark_threshold": int(threshold),
        "dilate_kernel": 3,
        "inpaint_radius": 5,
        "pre_inpaint_mask_ratio": round(float(np.count_nonzero(mask)) / lane_pixels, 4),
    }


def _build_text_mask(
    roi: np.ndarray,
    lane_mask: np.ndarray,
    dark_threshold: int,
    *,
    mask_mode: str = "auto",
) -> tuple[np.ndarray, Dict[str, Any]]:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bimodality = _lane_bimodality(roi, lane_mask)
    use_conservative = mask_mode == "conservative" or (mask_mode == "auto" and bimodality < 0.75)

    if use_conservative:
        reason = "requested" if mask_mode == "conservative" else "low_bimodality"
        return _build_conservative_dark_mask(gray, lane_mask, bimodality, reason=reason)

    denoised = cv2.bilateralFilter(gray, 5, 35, 35)
    adaptive = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        12,
    )
    _, dark = cv2.threshold(denoised, dark_threshold, 255, cv2.THRESH_BINARY_INV)
    local_bg = cv2.medianBlur(denoised, 31)
    bright_on_dark = np.zeros_like(denoised)
    bright_on_dark[(denoised > 235) & (local_bg < 205) & ((denoised.astype(np.int16) - local_bg.astype(np.int16)) > 28)] = 255

    mask = cv2.bitwise_or(adaptive, dark)
    mask = cv2.bitwise_or(mask, bright_on_dark)
    mask = cv2.bitwise_and(mask, lane_mask)

    k_open = np.ones((2, 2), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filtered = np.zeros_like(mask)
    max_area = max(30, roi.shape[0] * roi.shape[1] * 0.18)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 3 or area > max_area:
            continue
        cv2.drawContours(filtered, [cnt], -1, 255, cv2.FILLED)

    k_dilate = np.ones((7, 7), dtype=np.uint8)
    mask = cv2.dilate(filtered, k_dilate, iterations=1)
    lane_pixels = max(1, int(np.count_nonzero(lane_mask)))
    adaptive_mask_ratio = float(np.count_nonzero(mask)) / lane_pixels
    if mask_mode == "auto" and adaptive_mask_ratio >= 0.55 and bimodality < 0.90:
        fallback_mask, fallback_meta = _build_conservative_dark_mask(
            gray,
            lane_mask,
            bimodality,
            reason="adaptive_overreach",
        )
        fallback_meta["adaptive_mask_ratio"] = round(adaptive_mask_ratio, 4)
        return fallback_mask, fallback_meta

    return mask, {
        "mask_mode": "adaptive",
        "mask_reason": "adaptive",
        "lane_bimodality": round(bimodality, 4),
        "effective_dark_threshold": int(dark_threshold),
        "dilate_kernel": 7,
        "inpaint_radius": 7,
        "pre_inpaint_mask_ratio": round(adaptive_mask_ratio, 4),
    }


def _find_panel_boundaries(image: np.ndarray, tg_rect: List[int]) -> Dict[str, int | None]:
    """TG를 둘러싼 가장 가까운 패널 경계선 좌표를 반환합니다.

    수평/수직 방향으로 이미지 폭(높이)의 40% 이상을 덮는 어두운 선을 패널 경계로 판단합니다.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    img_h, img_w = gray.shape
    _, dark = cv2.threshold(gray, 40, 255, cv2.THRESH_BINARY_INV)

    tx, ty, tw, th = _as_rect(tg_rect)
    cx = tx + tw // 2
    cy = ty + th // 2

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
    h_closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, h_kernel)
    h_lines = np.where(np.count_nonzero(h_closed, axis=1) / img_w > 0.40)[0]

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
    v_closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, v_kernel)
    v_lines = np.where(np.count_nonzero(v_closed, axis=0) / img_h > 0.40)[0]

    top_cands = h_lines[h_lines < cy]
    bottom_cands = h_lines[h_lines > cy]
    left_cands = v_lines[v_lines < cx]
    right_cands = v_lines[v_lines > cx]

    return {
        "top":    int(top_cands[-1])    if len(top_cands)    else None,
        "bottom": int(bottom_cands[0])  if len(bottom_cands) else None,
        "left":   int(left_cands[-1])   if len(left_cands)   else None,
        "right":  int(right_cands[0])   if len(right_cands)  else None,
    }


_LAMA_CONTEXT_MARGIN = 15  # LaMa symmetric 패딩이 텍스트를 재현하지 않도록 TG 경계 위아래 최소 여백
_VERTICAL_FURIGANA_CONTEXT_MARGIN = 36  # 세로 본문 오른쪽 후리가나가 panel clamp로 잘리지 않도록 보존


def _clip_roi_to_panel(
    roi_rect: List[int],
    boundaries: Dict[str, int | None],
    tg_rect: List[int] | None = None,
    margin: int = 2,
) -> List[int]:
    """ROI를 패널 경계 안쪽으로 clamp합니다.

    tg_rect를 지정하면 패널 경계가 TG 내부를 지나더라도 TG 자체 영역은 보존합니다.
    TG 상하 _LAMA_CONTEXT_MARGIN px는 패널 경계와 무관하게 항상 확보해
    LaMa symmetric 패딩이 TG 첫/끝 글자를 재현하는 현상을 방지합니다.
    너무 작아지면 원본 유지.
    """
    x1, y1, w, h = roi_rect
    x2, y2 = x1 + w, y1 + h

    # TG 실제 텍스트 영역은 항상 포함해야 함
    if tg_rect is not None:
        tg_x1, tg_y1, tg_w, tg_h = tg_rect
        tg_x2, tg_y2 = tg_x1 + tg_w, tg_y1 + tg_h
    else:
        tg_x1 = tg_y1 = tg_x2 = tg_y2 = None

    top = boundaries.get("top")
    bottom = boundaries.get("bottom")
    left = boundaries.get("left")
    right = boundaries.get("right")

    if top is not None:
        clip = top + margin
        tg_top_limit = None if tg_y1 is None else tg_y1 - _LAMA_CONTEXT_MARGIN
        y1 = max(y1, clip if tg_top_limit is None else min(clip, tg_top_limit))
    if bottom is not None:
        clip = bottom - margin
        tg_bot_limit = None if tg_y2 is None else tg_y2 + _LAMA_CONTEXT_MARGIN
        y2 = min(y2, clip if tg_bot_limit is None else max(clip, tg_bot_limit))
    if left is not None:
        clip = left + margin
        x1 = max(x1, clip if tg_x1 is None else min(clip, tg_x1))
    if right is not None:
        clip = right - margin
        if tg_x2 is None:
            x2 = min(x2, clip)
        else:
            vertical_side_limit = tg_x2
            if tg_w > 0 and tg_h >= tg_w * 1.35:
                vertical_side_limit = tg_x2 + _VERTICAL_FURIGANA_CONTEXT_MARGIN
            x2 = min(x2, max(clip, vertical_side_limit))

    new_w, new_h = x2 - x1, y2 - y1
    if new_w < 10 or new_h < 10:
        return roi_rect  # 너무 좁아지면 원본 유지
    return [x1, y1, new_w, new_h]


def _mask_rect(mask: np.ndarray, roi_rect: List[int], pad: int, img_w: int, img_h: int) -> List[int]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return [0, 0, 0, 0]
    x1 = int(xs.min()) + roi_rect[0]
    y1 = int(ys.min()) + roi_rect[1]
    x2 = int(xs.max()) + 1 + roi_rect[0]
    y2 = int(ys.max()) + 1 + roi_rect[1]
    return _pad_rect([x1, y1, x2 - x1, y2 - y1], pad, img_w, img_h)


def _draw_rect(img: np.ndarray, rect: List[int], color: tuple[int, int, int], label: str) -> None:
    x, y, w, h = _as_rect(rect)
    cv2.rectangle(img, (x, y), (x + w, y + h), color, 2, cv2.LINE_AA)
    cv2.putText(img, label, (x, max(16, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)


def _stack_debug(cells: List[np.ndarray], cols: int = 3) -> np.ndarray:
    max_h = max(cell.shape[0] for cell in cells)
    max_w = max(cell.shape[1] for cell in cells)
    padded = []
    for cell in cells:
        if len(cell.shape) == 2:
            cell = cv2.cvtColor(cell, cv2.COLOR_GRAY2BGR)
        canvas = np.full((max_h, max_w, 3), 255, dtype=np.uint8)
        canvas[:cell.shape[0], :cell.shape[1]] = cell
        padded.append(canvas)
    rows = []
    for i in range(0, len(padded), cols):
        rows.append(np.hstack(padded[i:i + cols]))
    return np.vstack(rows)


def extract_unboxed_text_candidate(
    image: np.ndarray,
    groups: List[Dict[str, Any]],
    region_rect: List[int] | None = None,
    pad: int = 60,
    lane_pad: int = 20,
    dark_threshold: int = 214,
) -> Dict[str, Any]:
    """TG 주변 local ROI에서 unboxed text mask와 외접 rect를 추출합니다."""
    img_h, img_w = image.shape[:2]
    tg_rect = _union_rect([_as_rect(g.get("tight_rect", g.get("bounding_rect", [0, 0, 0, 0]))) for g in groups])
    roi_rect = _pad_rect(tg_rect, pad, img_w, img_h)
    boundaries = _find_panel_boundaries(image, tg_rect)
    roi_rect = _clip_roi_to_panel(roi_rect, boundaries, tg_rect=tg_rect)
    rx, ry, rw, rh = roi_rect
    roi = image[ry:ry + rh, rx:rx + rw].copy()

    lane_mask = _build_lane_mask((rh, rw), groups, roi_rect, pad=lane_pad)
    text_mask, mask_meta = _build_text_mask(roi, lane_mask, dark_threshold=dark_threshold)
    furigana_mask, furigana_meta = _build_furigana_side_mask(
        roi,
        groups,
        roi_rect,
        text_mask,
        dark_threshold,
    )
    if furigana_meta.get("furigana_added_pixels", 0) > 0:
        text_mask = cv2.bitwise_or(text_mask, furigana_mask)
        mask_meta.update(furigana_meta)
    else:
        mask_meta.update(furigana_meta)
    mask_meta.update(_classify_inpaint_background(roi, text_mask))
    inpainted = cv2.inpaint(roi, text_mask, int(mask_meta["inpaint_radius"]), cv2.INPAINT_TELEA)
    diff = cv2.absdiff(roi, inpainted)
    diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, diff_mask = cv2.threshold(diff_gray, 8, 255, cv2.THRESH_BINARY)
    erase_rect = _mask_rect(text_mask, roi_rect, pad=8, img_w=img_w, img_h=img_h)

    return {
        "tg_rect": tg_rect,
        "roi_rect": roi_rect,
        "region_rect": region_rect or [0, 0, 0, 0],
        "erase_rect": erase_rect,
        "lane_pad": lane_pad,
        "dark_threshold": dark_threshold,
        "mask_pixels": int(np.count_nonzero(text_mask)),
        "diff_pixels": int(np.count_nonzero(diff_mask)),
        **mask_meta,
        "roi": roi,
        "lane_mask": lane_mask,
        "text_mask": text_mask,
        "inpainted": inpainted,
        "diff_mask": diff_mask,
    }


def write_unboxed_text_debug(
    image: np.ndarray,
    groups: List[Dict[str, Any]],
    candidate: Dict[str, Any],
    out_dir: Path,
    suffix: str,
) -> Dict[str, str]:
    """unboxed_text_candidate 디버그 이미지를 저장합니다."""
    roi_rect = candidate["roi_rect"]
    region_rect = candidate["region_rect"]
    erase_rect = candidate["erase_rect"]
    roi = candidate["roi"]
    text_mask = candidate["text_mask"]
    inpainted = candidate["inpainted"]
    lane_mask = candidate["lane_mask"]
    diff_mask = candidate["diff_mask"]

    roi_annotated = roi.copy()
    for group in groups:
        gid = int(group.get("text_group_id", group.get("cluster_id", -1)))
        local = _rect_to_local(_as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0]))), roi_rect)
        _draw_rect(roi_annotated, local, (0, 220, 255), f"TG{gid}")

    full_preview = image.copy()
    _draw_rect(full_preview, roi_rect, (255, 120, 0), "ROI")
    if region_rect[2] > 0 and region_rect[3] > 0:
        _draw_rect(full_preview, region_rect, (0, 0, 255), "fallback")
    if erase_rect[2] > 0 and erase_rect[3] > 0:
        _draw_rect(full_preview, erase_rect, (0, 180, 0), "erase_rect")

    mask_overlay = roi.copy()
    mask_overlay[text_mask > 0] = (40, 40, 230)
    mask_overlay = cv2.addWeighted(mask_overlay, 0.55, roi, 0.45, 0)

    inpaint_full = image.copy()
    rx, ry, rw, rh = roi_rect
    inpaint_full[ry:ry + rh, rx:rx + rw] = inpainted

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "roi": str(out_dir / f"{suffix}_roi.jpg"),
        "lane_mask": str(out_dir / f"{suffix}_lane_mask.jpg"),
        "text_mask": str(out_dir / f"{suffix}_text_mask.jpg"),
        "inpaint": str(out_dir / f"{suffix}_inpaint.jpg"),
        "diff_mask": str(out_dir / f"{suffix}_diff_mask.jpg"),
        "preview": str(out_dir / f"{suffix}_preview.jpg"),
        "full_inpaint": str(out_dir / f"{suffix}_full_inpaint.jpg"),
        "board": str(out_dir / f"{suffix}_board.jpg"),
    }
    cv2.imwrite(paths["roi"], roi_annotated)
    cv2.imwrite(paths["lane_mask"], lane_mask)
    cv2.imwrite(paths["text_mask"], text_mask)
    cv2.imwrite(paths["inpaint"], inpainted)
    cv2.imwrite(paths["diff_mask"], diff_mask)
    cv2.imwrite(paths["preview"], full_preview)
    cv2.imwrite(paths["full_inpaint"], inpaint_full)
    board = _stack_debug([roi_annotated, lane_mask, text_mask, mask_overlay, inpainted, diff_mask])
    cv2.imwrite(paths["board"], board)
    return paths


def run(
    img_num: str,
    tg_ids: List[int],
    region_id: int,
    pad: int,
    lane_pad: int,
    dark_threshold: int,
    out_dir: Path,
) -> None:
    image_path = ROOT_DIR / "test_image" / f"{img_num}.jpg"
    groups_path = ROOT_DIR / "experiment" / "proof" / "json" / f"{img_num}_text_groups.json"
    regions_path = ROOT_DIR / "experiment" / "proof" / "json" / f"{img_num}_bubble_regions.json"

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    img_h, img_w = image.shape[:2]

    groups = _target_text_groups(_load_json(groups_path), tg_ids)
    if not groups:
        raise ValueError(f"대상 TextGroup이 없습니다. {tg_ids}")

    regions = _load_json(regions_path)
    region = _region_by_id(regions, region_id)
    region_rect = _as_rect(region.get("bubble_rect", [0, 0, 0, 0])) if region else [0, 0, 0, 0]

    candidate = extract_unboxed_text_candidate(
        image,
        groups,
        region_rect=region_rect,
        pad=pad,
        lane_pad=lane_pad,
        dark_threshold=dark_threshold,
    )
    tg_rect = candidate["tg_rect"]
    roi_rect = candidate["roi_rect"]
    erase_rect = candidate["erase_rect"]

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{img_num}_R{region_id:02d}_TG{'_'.join(str(i) for i in tg_ids)}_lp{lane_pad}_th{dark_threshold}"
    paths = write_unboxed_text_debug(image, groups, candidate, out_dir, suffix)

    meta = {
        "image": str(image_path),
        "text_groups": tg_ids,
        "region_id": region_id,
        "region_rect": region_rect,
        "tg_rect": tg_rect,
        "roi_rect": roi_rect,
        "erase_rect": erase_rect,
        "lane_pad": lane_pad,
        "dark_threshold": dark_threshold,
        "mask_pixels": candidate["mask_pixels"],
        "diff_pixels": candidate["diff_pixels"],
        "outputs": {
            "board": paths["board"],
            "preview": paths["preview"],
            "full_inpaint": paths["full_inpaint"],
        },
    }
    with (out_dir / f"{suffix}_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="TextGroup local unboxed text 후보를 검증합니다.")
    parser.add_argument("img_num")
    parser.add_argument("--region-id", type=int, required=True)
    parser.add_argument("--tg-id", type=int, action="append", required=True)
    parser.add_argument("--pad", type=int, default=60)
    parser.add_argument("--lane-pad", type=int, default=20)
    parser.add_argument("--dark-threshold", type=int, default=214)
    parser.add_argument("--out-dir", default="experiment/proof/unboxed_text_probe")
    args = parser.parse_args()
    run(
        args.img_num,
        args.tg_id,
        args.region_id,
        args.pad,
        args.lane_pad,
        args.dark_threshold,
        Path(args.out_dir),
    )


if __name__ == "__main__":
    main()
