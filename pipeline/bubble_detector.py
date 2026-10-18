#!/usr/bin/env python3
"""
말풍선 검출 코어 모듈 (구 floodfill_anchor_probe.py).

단순 FloodFill 추적기에서 Probe → Validation → Rescue → Finalize
4단계 파이프라인 구조로 고도화됐다.

---

파이프라인 개요
---------------
1. Probe      : TG(Text Group) 중심 주변의 흰 seed에서 FloodFill 수행.
                OpenCV contour를 직접 찾지 않고 magic-wand 방식으로 연결 흰 영역을 채운다.
                seed 탐색 우선순위: 좌우 바깥 여백 → 중심 근접 → bbox 가장자리 4방향.

2. Validation : bubble_candidate → bubble_region 승격 여부 판정 (is_valid).
                통과 조건:
                  - bimodality ≥ 0.85 (flood 내부 흑/백 이중 분포 — 배경+글자 구조)
                  - solidity    ≥ 0.75 (flood region이 충분히 채워진 볼록 형태)
                탈락 후보는 삭제하지 않고 bbox_fallback으로 강등해 Rescue 단계로 넘긴다.

3. Rescue     : 탈락 후보 구제.
                - cross-split / combined_anchor_rescue: 비율 초과 anchor를 flood group 합류/승격
                - high_ratio_bubble_rescued: solidity ≥ 0.65 AND bimodality ≥ 0.85 조건 구제
                - Edge Barrier: 구멍 난 말풍선·방사형 말풍선 보조 구제 (2026-05-17 동결)
                Edge Barrier는 특정 positive/negative 기준점을 유지하며 범위를 고정한다.

4. Finalize   : 검증 통과 region에만 region_type 부여.
                is_valid = True 확정 이후에만 타입 매겨 노이즈 오분류를 방지한다.
                bubble 판정·rescue 이후에도 bbox_fallback으로 남은 region은
                unboxed_text_region 후처리(narration·독백 배경 텍스트) 대상이 된다.

---

핵심 용어
---------
bubble_candidate    FloodFill로 1차 추출됐지만 검증 전 상태의 마스크 영역.
bubble_region       Validation 통과 후 OCR sheet 탑재가 확정된 영역.
is_valid            bubble_candidate가 bubble_region으로 승격 가능한지 나타내는 상태값.
bbox_fallback       is_valid = False 강등 상태. 삭제 대신 보존해 Rescue / unboxed_text 대상으로 유지.
bubble_tg_ratio     flood_rect 면적 / TextGroup bbox 면적. 배경 누수 검증 핵심 지표.
region_type         확정(is_valid=True) 이후에만 부여. bubble / narration_like 등.
review_tags         Rescue 이후에도 미확정인 후보에 붙는 후단 처리 힌트.
                    unboxed_text_candidate / image_gen_review_candidate 등.

---

출력
----
*_bubble_regions.json  regions 배열 (is_valid, region_type, method, review_tags, diagnostics 포함)
"""
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from pipeline.flood_split import (
        all_anchor_rects_are_disjoint,
        collect_inflection_candidates,
        split_mask_by_erosion_components,
        split_mask_by_inflection_line,
    )
except ImportError:
    from flood_split import (
        all_anchor_rects_are_disjoint,
        collect_inflection_candidates,
        split_mask_by_erosion_components,
        split_mask_by_inflection_line,
    )
try:
    from pipeline.bubble_text_filters import (
        cluster_text as _cluster_text,
        is_english_only_text as _is_english_only_text,
        is_japanese_char as _is_japanese_char,
        is_large_short_kana_sfx_like as _is_large_short_kana_sfx_like,
        is_large_single_glyph_sfx_like as _is_large_single_glyph_sfx_like,
        is_short_non_japanese_noise_text as _is_short_non_japanese_noise_text,
        is_symbol_only_text as _is_symbol_only_text,
        strip_and_stats as _strip_and_stats,
        text_stats as _text_stats,
    )
except ImportError:
    from bubble_text_filters import (
        cluster_text as _cluster_text,
        is_english_only_text as _is_english_only_text,
        is_japanese_char as _is_japanese_char,
        is_large_short_kana_sfx_like as _is_large_short_kana_sfx_like,
        is_large_single_glyph_sfx_like as _is_large_single_glyph_sfx_like,
        is_short_non_japanese_noise_text as _is_short_non_japanese_noise_text,
        is_symbol_only_text as _is_symbol_only_text,
        strip_and_stats as _strip_and_stats,
        text_stats as _text_stats,
    )


Point = Tuple[int, int]
MAX_SAFE_AREA_RATIO = 0.15
MIN_SAFE_INNER_AREA = 300
_WHITE_THRESHOLDS = [248, 242, 235, 225]
EDGE_NARROW_METADATA_MAX_WIDTH = 45
EDGE_NARROW_METADATA_MIN_HEIGHT = 200
EDGE_NARROW_METADATA_MARGIN = 100


def _should_demote_lone_edge_split_rescue(
    rescued_anchor_ids: List[int],
    rejected_reasons_by_anchor: Dict[int, str],
) -> bool:
    """같은 failed group에서 혼자 살아남은 edge split rescue를 sibling 합의로 강등할지 판단한다."""
    if len(rescued_anchor_ids) != 1:
        return False
    if len(rejected_reasons_by_anchor) < 2:
        return False

    sibling_rejects = list(rejected_reasons_by_anchor.values())
    thin_unboxed_rejects = sum(
        1
        for reason in sibling_rejects
        if reason == "edge_reject_all_guard_thin_unboxed_like"
    )
    return thin_unboxed_rejects == len(sibling_rejects)


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _unpack_rect(rect) -> Tuple[int, int, int, int]:
    """rect 리스트에서 (x, y, w, h)를 int 변환 후 반환한다."""
    return int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])


def _load_image_and_gray(image_path: str):
    """이미지 로드 + grayscale 변환 + 기본 메타 반환."""
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"이미지를 불러올 수 없습니다: {image_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    img_h, img_w = image.shape[:2]
    return image, gray, img_h, img_w


def _bbox_center(rect) -> Point:
    x, y, w, h = _unpack_rect(rect)
    return (int(round(x + w / 2)), int(round(y + h / 2)))


# NOTE: _find_white_seed, _find_side_white_seed 삭제됨 (2026-05-18)
# 이전 구현: center에서 방사형 탐색 / bbox 좌우 바깥 여백 탐색으로 흰 seed를 찾았으나,
# 말풍선은 텍스트 상하좌우에 반드시 여백이 있으므로 bbox 경계 4점 탐색으로 충분.
# FloodFill은 연결된 흰 영역 전체를 채우기 때문에 시작점만 확보하면 됨.

MIN_BUBBLE_AREA = 500  # flood area 이 이하이면 글자 획 안 고립 픽셀로 판단


def _flood_with_fallback(
    image: np.ndarray,
    gray: np.ndarray,
    rect,
) -> Tuple[Optional[Point], np.ndarray, int, List[int]]:
    """
    bbox 경계 4방향(상·하·좌·우)에서 흰 seed를 찾아 FloodFill을 수행한다.

    말풍선 내부 텍스트는 상하좌우에 여백이 있으므로,
    bbox 경계 안쪽 inset 지점을 순회하면 대부분의 말풍선 seed를 확보할 수 있다.
    여러 threshold를 순차 완화하여 JPEG 아티팩트나 약간 어두운 배경도 대응한다.
    """
    img_h, img_w = image.shape[:2]
    x, y, w, h = _unpack_rect(rect)
    empty_mask = np.zeros((img_h, img_w), dtype=np.uint8)

    margin = 2
    candidates: List[Point] = [
        (x + w // 2,                       max(0, y - margin)),               # 상단 바깥
        (x + w // 2,                       min(img_h - 1, y + h + margin)),   # 하단 바깥
        (max(0, x - margin),               y + h // 2),                       # 좌측 바깥
        (min(img_w - 1, x + w + margin),   y + h // 2),                       # 우측 바깥
    ]

    for threshold in _WHITE_THRESHOLDS:
        for pt in candidates:
            if gray[pt[1], pt[0]] < threshold:
                continue
            mask, area, frect = _flood_fill_mask(image, pt)
            if area >= MIN_BUBBLE_AREA:
                return pt, mask, area, frect

    # 모두 실패
    return None, empty_mask, 0, [0, 0, 0, 0]


def _flood_fill_mask(
    image: np.ndarray,
    seed: Point,
    lo_diff: int = 8,
    up_diff: int = 10,
    fixed_range: bool = False,
) -> Tuple[np.ndarray, int, List[int]]:
    """
    BGR 이미지에서 seed와 유사한 색상의 연결 영역을 flood fill로 추출한다.
    """
    img_h, img_w = image.shape[:2]
    work = image.copy()
    mask = np.zeros((img_h + 2, img_w + 2), dtype=np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    if fixed_range:
        flags |= cv2.FLOODFILL_FIXED_RANGE
    area, _, _, rect = cv2.floodFill(
        work,
        mask,
        seedPoint=seed,
        newVal=(0, 0, 0),
        loDiff=(lo_diff, lo_diff, lo_diff),
        upDiff=(up_diff, up_diff, up_diff),
        flags=flags,
    )
    return mask[1:-1, 1:-1], int(area), [int(v) for v in rect]


def _dark_flood_with_fallback(
    image: np.ndarray,
    gray: np.ndarray,
    rect,
    threshold: int = 110,
) -> Tuple[Optional[Point], np.ndarray, int, List[int]]:
    """검은 배경 말풍선 후보를 위해 bbox 주변의 어두운 seed에서 flood fill을 수행한다."""
    img_h, img_w = image.shape[:2]
    x, y, w, h = _unpack_rect(rect)
    empty_mask = np.zeros((img_h, img_w), dtype=np.uint8)

    margin = 2
    candidates: List[Point] = [
        (x + w // 2, max(0, y - margin)),
        (x + w // 2, min(img_h - 1, y + h + margin)),
        (max(0, x - margin), y + h // 2),
        (min(img_w - 1, x + w + margin), y + h // 2),
        (x + w // 2, y + h // 2),
    ]

    best: Optional[Tuple[Point, np.ndarray, int, List[int]]] = None
    for pt in candidates:
        if gray[pt[1], pt[0]] > threshold:
            continue
        mask, area, frect = _flood_fill_mask(image, pt, lo_diff=6, up_diff=8, fixed_range=True)
        if area < MIN_BUBBLE_AREA:
            continue
        if best is None or area > best[2]:
            best = (pt, mask, area, frect)

    if best is None:
        return None, empty_mask, 0, [0, 0, 0, 0]
    return best


def _mask_solidity_roundness(mask: np.ndarray) -> Tuple[float, float]:
    """mask의 대표 contour에 대한 solidity와 roundness를 반환한다."""
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0, 0.0
    cnt = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    hull_area = cv2.contourArea(cv2.convexHull(cnt))
    peri = cv2.arcLength(cnt, True)
    solidity = area / hull_area if hull_area > 0 else 0.0
    roundness = 4 * np.pi * area / (peri ** 2) if peri > 0 else 0.0
    return float(solidity), float(roundness)


def _rect_side_intrusion_reject_reason(
    mask: np.ndarray | None,
    quality_flags: List[str],
) -> Optional[str]:
    """rect 후보의 한쪽 세로변이 전장에 걸쳐 흔들리면 캐릭터/패널 경계 침범으로 강등한다."""
    if not isinstance(mask, np.ndarray):
        return None
    flag_text = " ".join(str(flag) for flag in quality_flags)
    rect_like_class = (
        "shape_class=rect" in flag_text
        or "shape_class=closed_boundary" in flag_text
    )
    if not rect_like_class or "large_bubble_tg_ratio" not in flag_text:
        return None

    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    _x, _y, w, h = cv2.boundingRect(cnt)
    if w <= 0 or h <= 0:
        return None
    roi = binary[_y:_y + h, _x:_x + w]

    def vertical_stats(side: str) -> Optional[Dict[str, float]]:
        values: List[int] = []
        for yy in range(h):
            xs = np.where(roi[yy] > 0)[0]
            if len(xs) == 0:
                continue
            values.append(int(xs.min() if side == "left" else xs.max()))
        if len(values) < max(12, int(h * 0.45)):
            return None
        arr = np.array(values, dtype=float)
        median = float(np.median(arr))
        dev = np.abs(arr - median)
        threshold = max(8.0, float(w) * 0.08)
        return {
            "span_ratio": float((arr.max() - arr.min()) / max(1, w)),
            "bad_frac": float(np.mean(dev > threshold)),
        }

    left = vertical_stats("left")
    right = vertical_stats("right")
    if left is None or right is None:
        return None

    min_bad_frac = 0.26 if "shape_class=closed_boundary" in flag_text else 0.35
    for side, stats, opposite in (("left", left, right), ("right", right, left)):
        if (
            stats["span_ratio"] >= 0.45
            and stats["bad_frac"] >= min_bad_frac
            and opposite["bad_frac"] <= 0.08
        ):
            return (
                "rect_side_intrusion_reject:"
                f"{side}(span={stats['span_ratio']:.2f},"
                f"bad={stats['bad_frac']:.2f},"
                f"opp={opposite['bad_frac']:.2f})"
            )
    return None


def _boundary_evidence(gray: np.ndarray, rect: List[int]) -> Tuple[int, Dict[str, float]]:
    """rect 주변에 말풍선 경계로 볼 만한 어두운 선이 몇 방향에 있는지 계산한다."""
    x, y, w, h = _unpack_rect(rect)
    img_h, img_w = gray.shape[:2]
    if w <= 0 or h <= 0:
        return 0, {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}

    band = 7
    pad = 2
    dark_threshold = 190
    side_threshold = 0.05
    crops = {
        "top": gray[max(0, y - pad):min(img_h, y + band), max(0, x):min(img_w, x + w)],
        "bottom": gray[max(0, y + h - band):min(img_h, y + h + pad), max(0, x):min(img_w, x + w)],
        "left": gray[max(0, y):min(img_h, y + h), max(0, x - pad):min(img_w, x + band)],
        "right": gray[max(0, y):min(img_h, y + h), max(0, x + w - band):min(img_w, x + w + pad)],
    }
    ratios: Dict[str, float] = {}
    for side, crop in crops.items():
        ratios[side] = float(np.count_nonzero(crop < dark_threshold) / crop.size) if crop.size else 0.0
    return sum(1 for value in ratios.values() if value >= side_threshold), ratios


def _profile_step_signal(mask: np.ndarray, axis: str) -> Dict[str, float]:
    """mask 폭/중심 profile에서 두 rect 덩어리가 계단식으로 이어지는 신호를 계산한다."""
    binary = np.where(mask > 0, 1, 0).astype(np.uint8)
    if binary.size == 0:
        return {"score": 0.0}
    img_h, img_w = binary.shape[:2]
    scan_len = img_h if axis == "vertical" else img_w
    cross_len = img_w if axis == "vertical" else img_h
    spans = np.zeros(scan_len, dtype=np.float32)
    centers = np.full(scan_len, np.nan, dtype=np.float32)

    # scan line별 첫/마지막 채움 위치를 한 번에 구한다(빈 line은 span 0, center NaN 유지).
    lines = binary if axis == "vertical" else binary.T
    has_fill = lines.any(axis=1)
    left = lines.argmax(axis=1)
    right = lines.shape[1] - 1 - lines[:, ::-1].argmax(axis=1)
    spans[has_fill] = (right - left + 1)[has_fill]
    centers[has_fill] = ((left + right) / 2.0)[has_fill]

    valid = np.where(spans > 0)[0]
    if len(valid) < 24:
        return {"score": 0.0}

    start = int(valid.min())
    end = int(valid.max())
    total_len = end - start + 1
    max_span = float(spans.max())
    if total_len < 40 or max_span < 24:
        return {"score": 0.0}

    window = max(7, min(31, (total_len // 18) * 2 + 1))
    kernel = np.ones(window, dtype=np.float32) / float(window)
    smooth_spans = np.convolve(spans, kernel, mode="same")

    valid_centers = np.where(~np.isnan(centers))[0]
    center_values = centers.copy()
    if len(valid_centers):
        missing = np.where(np.isnan(center_values))[0]
        if len(missing):
            center_values[missing] = np.interp(missing, valid_centers, centers[valid_centers])
    smooth_centers = np.convolve(center_values, kernel, mode="same")

    min_part = max(14, int(total_len * 0.18))
    transition = max(4, int(total_len * 0.04))
    best: Dict[str, float] = {"score": 0.0}

    for split in range(start + min_part, end - min_part + 1):
        a0, a1 = start, max(start, split - transition)
        b0, b1 = min(end, split + transition), end
        if a1 <= a0 or b1 <= b0:
            continue
        seg_a = np.arange(a0, a1 + 1)
        seg_b = np.arange(b0, b1 + 1)
        seg_a = seg_a[spans[seg_a] > 0]
        seg_b = seg_b[spans[seg_b] > 0]
        if len(seg_a) < min_part * 0.65 or len(seg_b) < min_part * 0.65:
            continue

        mean_a = float(np.mean(smooth_spans[seg_a]))
        mean_b = float(np.mean(smooth_spans[seg_b]))
        if mean_a <= 0 or mean_b <= 0:
            continue
        center_a = float(np.mean(smooth_centers[seg_a]))
        center_b = float(np.mean(smooth_centers[seg_b]))
        std_a = float(np.std(smooth_centers[seg_a]))
        std_b = float(np.std(smooth_centers[seg_b]))
        center_shift = abs(center_a - center_b) / max(1.0, max_span)
        width_floor = min(mean_a, mean_b) / max(1.0, max_span)
        balance = min(len(seg_a), len(seg_b)) / max(1.0, max(len(seg_a), len(seg_b)))
        neck_range = np.arange(max(start, split - transition), min(end, split + transition) + 1)
        neck_span = float(np.mean(smooth_spans[neck_range])) if len(neck_range) else max_span
        neck_ratio = neck_span / max(1.0, min(mean_a, mean_b))
        stability_penalty = (std_a + std_b) / max(1.0, cross_len)

        step_signal = center_shift >= 0.20
        neck_signal = neck_ratio <= 0.82
        score = (
            center_shift * 1.35
            + max(0.0, 1.0 - neck_ratio) * 0.65
            + width_floor * 0.35
            + balance * 0.20
            - stability_penalty * 0.70
        )

        if (step_signal or neck_signal) and width_floor >= 0.42 and balance >= 0.28:
            if score > best["score"]:
                best = {
                    "score": float(score),
                    "center_shift": float(center_shift),
                    "neck_ratio": float(neck_ratio),
                    "width_floor": float(width_floor),
                    "balance": float(balance),
                    "split": float(split),
                    "axis": 1.0 if axis == "vertical" else 2.0,
                }

    return best


def _dual_rect_profile_signal(mask: np.ndarray) -> Dict[str, Any]:
    """상하/좌우 어느 방향이든 두 직사각형 덩어리 profile 신호가 있는지 본다."""
    vertical = _profile_step_signal(mask, "vertical")
    horizontal = _profile_step_signal(mask, "horizontal")
    best = vertical if vertical.get("score", 0.0) >= horizontal.get("score", 0.0) else horizontal
    axis = "vertical" if best.get("axis") == 1.0 else "horizontal"
    score = float(best.get("score", 0.0))
    return {
        "ok": bool(score >= 0.58),
        "axis": axis,
        "score": score,
        "center_shift": float(best.get("center_shift", 0.0)),
        "neck_ratio": float(best.get("neck_ratio", 1.0)),
        "width_floor": float(best.get("width_floor", 0.0)),
        "balance": float(best.get("balance", 0.0)),
    }


def _clamp01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _band_score(value: float, low: float, ideal_low: float, ideal_high: float, high: float) -> float:
    """값이 정상 band 안에 들어오는 정도를 0~1로 환산한다."""
    if value < low or value > high:
        return 0.0
    if ideal_low <= value <= ideal_high:
        return 1.0
    if value < ideal_low:
        return _clamp01((value - low) / max(1e-6, ideal_low - low))
    return _clamp01((high - value) / max(1e-6, high - ideal_high))


def _dual_rect_validation_score(
    raw_solid: float,
    raw_round: float,
    shape_score: float,
    boundary_sides: int,
    contour_probe: Dict[str, Any],
    dual_rect_profile: Dict[str, Any],
    bubble_page_ratio: float,
    touches_border: bool,
) -> Tuple[bool, float, Dict[str, float]]:
    """dual rectangular bubble 전용 보완 점수를 계산한다."""
    profile_score = float(dual_rect_profile.get("score", 0.0))
    profile_component = _clamp01((profile_score - 0.58) / 0.40)
    boundary_component = _clamp01(boundary_sides / 4.0)

    contour_status = str(contour_probe.get("status", "weak"))
    contour_component = {
        "closed": 1.0,
        "partial": 0.66,
        "weak": 0.28,
        "one_sided": 0.0,
        "invalid": 0.0,
    }.get(contour_status, 0.0)

    # dual rect는 계단형 concavity가 정상 신호라 convex-hull solidity를 하한값이 아닌 band로 본다.
    solid_component = _band_score(raw_solid, 0.62, 0.68, 0.86, 0.93)
    round_component = _band_score(raw_round, 0.34, 0.40, 0.56, 0.66)
    shape_component = _clamp01((shape_score - 0.58) / 0.24)

    leak_penalty = 0.0
    if touches_border:
        leak_penalty += 0.12
    if bubble_page_ratio > 0.07:
        leak_penalty += _clamp01((bubble_page_ratio - 0.07) / 0.10) * 0.14
    if contour_status in {"weak", "one_sided", "invalid"}:
        leak_penalty += 0.10

    score = (
        profile_component * 0.32
        + contour_component * 0.22
        + boundary_component * 0.18
        + solid_component * 0.14
        + round_component * 0.10
        + shape_component * 0.04
        - leak_penalty
    )

    components = {
        "profile": profile_component,
        "contour": contour_component,
        "boundary": boundary_component,
        "solid": solid_component,
        "round": round_component,
        "shape": shape_component,
        "penalty": leak_penalty,
    }
    ok = (
        score >= 0.72
        and bool(dual_rect_profile.get("ok"))
        and boundary_sides >= 3
        and contour_status in {"closed", "partial"}
        and solid_component > 0.0
        and round_component > 0.0
        and shape_score >= 0.58
    )
    return ok, float(score), components


def _distance_peak_signal(mask: np.ndarray) -> Dict[str, float]:
    """mask 내부 distance peak가 둘 이상 분리되어 있는지 계산한다."""
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    if binary.size == 0 or np.count_nonzero(binary) < 300:
        return {"ok": 0.0, "count": 0.0}

    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    max_dist = float(dist.max())
    if max_dist <= 2.0:
        return {"ok": 0.0, "count": 0.0}

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"ok": 0.0, "count": 0.0}
    rect = cv2.boundingRect(max(contours, key=cv2.contourArea))
    diag = math.hypot(rect[2], rect[3])

    kernel_size = max(15, min(41, int(round(max(rect[2], rect[3]) * 0.10)) | 1))
    local_max = cv2.dilate(dist, np.ones((kernel_size, kernel_size), dtype=np.uint8))
    peak_mask = (dist >= local_max - 1e-4) & (dist >= max_dist * 0.48)
    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(peak_mask.astype(np.uint8), 8)

    peaks: List[Tuple[float, float, float]] = []
    for label in range(1, n_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area <= 0:
            continue
        cx, cy = centroids[label]
        px = int(round(cx))
        py = int(round(cy))
        if not (0 <= px < dist.shape[1] and 0 <= py < dist.shape[0]):
            continue
        peaks.append((float(dist[py, px]), float(cx), float(cy)))

    peaks.sort(reverse=True, key=lambda item: item[0])
    selected: List[Tuple[float, float, float]] = []
    min_sep = max(24.0, min(rect[2], rect[3]) * 0.28)
    for peak in peaks:
        if all(math.hypot(peak[1] - other[1], peak[2] - other[2]) >= min_sep for other in selected):
            selected.append(peak)
        if len(selected) >= 4:
            break

    if len(selected) < 2:
        return {"ok": 0.0, "count": float(len(selected))}

    best_pair: Optional[Tuple[float, float, float, float]] = None
    for i in range(len(selected)):
        for j in range(i + 1, len(selected)):
            a, b = selected[i], selected[j]
            sep = math.hypot(a[1] - b[1], a[2] - b[2])
            balance = min(a[0], b[0]) / max(a[0], b[0], 1e-6)
            sep_ratio = sep / max(1.0, diag)
            score = sep_ratio * 1.5 + balance * 0.5
            if best_pair is None or score > best_pair[0]:
                best_pair = (score, sep_ratio, balance, sep)

    if best_pair is None:
        return {"ok": 0.0, "count": float(len(selected))}

    score, sep_ratio, balance, sep = best_pair
    ok = sep_ratio >= 0.18 and balance >= 0.45
    return {
        "ok": 1.0 if ok else 0.0,
        "count": float(len(selected)),
        "score": float(score),
        "sep_ratio": float(sep_ratio),
        "balance": float(balance),
        "sep": float(sep),
        "max_dist": float(max_dist),
    }


def _text_center_offset_ratio(bubble_rect: List[int], text_rect: List[int]) -> float:
    """text rect 중심이 bubble rect 중심에서 얼마나 벗어났는지 0..1 비율로 계산한다."""
    bx, by, bw, bh = _unpack_rect(bubble_rect)
    tx, ty, tw, th = _unpack_rect(text_rect)
    if bw <= 0 or bh <= 0 or tw <= 0 or th <= 0:
        return 1.0
    bubble_cx = bx + bw / 2.0
    bubble_cy = by + bh / 2.0
    text_cx = tx + tw / 2.0
    text_cy = ty + th / 2.0
    dx = abs(text_cx - bubble_cx) / max(1.0, bw / 2.0)
    dy = abs(text_cy - bubble_cy) / max(1.0, bh / 2.0)
    return float(max(dx, dy))


def _mask_or_rect_centroid(mask: Optional[np.ndarray], rect: List[int]) -> Point:
    """mask 중심을 우선 사용하고, 비어 있으면 rect 중심을 반환한다."""
    if mask is not None and np.any(mask > 0):
        moments = cv2.moments(np.where(mask > 0, 255, 0).astype(np.uint8))
        if moments["m00"] > 0:
            return (int(round(moments["m10"] / moments["m00"])), int(round(moments["m01"] / moments["m00"])))
    return _bbox_center(rect)


def _ray_rect_distance(cx: float, cy: float, theta: float, rect: List[int]) -> float:
    """중심에서 theta 방향으로 rect 외곽에 닿는 거리."""
    x, y, w, h = _unpack_rect(rect)
    dx = float(np.cos(theta))
    dy = float(np.sin(theta))
    candidates: List[float] = []
    if abs(dx) > 1e-6:
        for bx in (x, x + w):
            t = (bx - cx) / dx
            py = cy + t * dy
            if t > 0 and y - 1 <= py <= y + h + 1:
                candidates.append(t)
    if abs(dy) > 1e-6:
        for by in (y, y + h):
            t = (by - cy) / dy
            px = cx + t * dx
            if t > 0 and x - 1 <= px <= x + w + 1:
                candidates.append(t)
    return min(candidates) if candidates else 0.0


def _max_false_gap_fraction(hit_flags: List[bool]) -> float:
    if not hit_flags:
        return 1.0
    if not any(hit_flags):
        return 1.0
    n = len(hit_flags)
    doubled = hit_flags + hit_flags
    best = current = 0
    for value in doubled:
        if value:
            current = 0
        else:
            current += 1
            best = max(best, current)
        if current >= n:
            return 1.0
    return min(best, n) / max(1, n)


def _black_contour_binary(gray: np.ndarray) -> np.ndarray:
    """black contour probe가 ray hit 판정에 쓰는 페이지 단위 검은 선 mask."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    black = np.where(blurred < 145, 255, 0).astype(np.uint8)
    return cv2.dilate(black, np.ones((3, 3), dtype=np.uint8), iterations=1)


def _black_contour_probe(
    gray: np.ndarray,
    rect: List[int],
    mask: Optional[np.ndarray] = None,
    ray_count: int = 72,
    black: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """후보 중심에서 방사형으로 검은 외곽선 hit의 폐곡선성을 추정한다.

    black: 같은 gray로 미리 만든 _black_contour_binary 결과. 없으면 여기서 계산한다.
    """
    x, y, w, h = _unpack_rect(rect)
    img_h, img_w = gray.shape[:2]
    if w <= 0 or h <= 0 or ray_count <= 0:
        return {
            "status": "invalid",
            "coverage": 0.0,
            "quadrants": 0,
            "max_gap": 1.0,
            "distance_cv": 0.0,
            "hit_points": [],
        }

    cx, cy = _mask_or_rect_centroid(mask, rect)
    cx = int(np.clip(cx, 0, img_w - 1))
    cy = int(np.clip(cy, 0, img_h - 1))
    if black is None:
        black = _black_contour_binary(gray)

    hit_flags: List[bool] = []
    hit_ratios: List[float] = []
    hit_points: List[List[int]] = []
    quadrant_hits = [False, False, False, False]

    for idx in range(ray_count):
        theta = (2.0 * np.pi * idx) / ray_count
        edge_dist = _ray_rect_distance(float(cx), float(cy), theta, rect)
        if edge_dist <= 2.0:
            hit_flags.append(False)
            continue

        start_r = max(3, int(edge_dist * 0.62))
        end_r = int(edge_dist * 1.28 + 8)
        found_point: Optional[List[int]] = None
        found_dist = 0
        cos_t = float(np.cos(theta))
        sin_t = float(np.sin(theta))
        for dist in range(start_r, max(start_r + 1, end_r + 1)):
            px = int(round(cx + cos_t * dist))
            py = int(round(cy + sin_t * dist))
            if px < 0 or py < 0 or px >= img_w or py >= img_h:
                break
            if black[py, px] > 0:
                found_point = [px, py]
                found_dist = dist
                break

        has_hit = found_point is not None
        hit_flags.append(has_hit)
        if has_hit:
            hit_points.append(found_point)
            hit_ratios.append(float(found_dist / max(edge_dist, 1.0)))
            quadrant_hits[int(idx / max(1, ray_count / 4)) % 4] = True

    coverage = float(sum(hit_flags) / max(1, len(hit_flags)))
    max_gap = _max_false_gap_fraction(hit_flags)
    quadrants = int(sum(1 for hit in quadrant_hits if hit))
    distance_cv = float(np.std(hit_ratios)) if len(hit_ratios) >= 2 else 0.0
    median_ratio = float(np.median(hit_ratios)) if hit_ratios else 0.0

    closed_like = (
        coverage >= 0.58
        and quadrants == 4
        and max_gap <= 0.35
        and 0.65 <= median_ratio <= 1.25
        and distance_cv <= 0.35
    )
    partial_like = (
        not closed_like
        and coverage >= 0.38
        and quadrants >= 3
        and max_gap <= 0.55
        and 0.60 <= median_ratio <= 1.35
    )
    one_sided = quadrants <= 2 or max_gap >= 0.62
    status = "closed" if closed_like else ("partial" if partial_like else ("one_sided" if one_sided else "weak"))

    return {
        "status": status,
        "center": [int(cx), int(cy)],
        "coverage": round(coverage, 3),
        "quadrants": quadrants,
        "max_gap": round(max_gap, 3),
        "distance_cv": round(distance_cv, 3),
        "median_ratio": round(median_ratio, 3),
        "hit_points": hit_points,
    }


def _erode_mask(mask: np.ndarray, pixels: int = 3) -> np.ndarray:
    if pixels <= 0:
        return mask.copy()
    kernel = np.ones((pixels * 2 + 1, pixels * 2 + 1), dtype=np.uint8)
    return cv2.erode(mask, kernel, iterations=1)


def _fill_mask_holes(mask: np.ndarray) -> np.ndarray:
    """
    floodFill로 얻은 흰 영역 mask 내부의 글자 구멍을 채운다.

    흰 말풍선 내부를 seed로 flood fill하면 검은 글자는 mask의 hole로 남는다.
    white masking에는 이 hole까지 지워야 하므로 외부 배경과 연결되지 않은 hole만 채운다.

    주의: 1px padding을 추가하지 않으면 flood mask가 이미지 코너를 둘러싸는 형태일 때
    원래 (0,0)이 작은 고립 섬이 되어 holes = NOT(binary) ≈ 전체 배경 → 이미지 전체 마스킹 버그 발생.
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    h, w = binary.shape[:2]

    # 1px 0-padding 추가 → 새 (0,0)은 항상 배경이므로 이미지 전체 외부와 연결 보장
    padded = np.zeros((h + 2, w + 2), dtype=np.uint8)
    padded[1:h + 1, 1:w + 1] = binary

    flood = padded.copy()
    ff_mask = np.zeros((h + 4, w + 4), dtype=np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)

    outside = flood[1:h + 1, 1:w + 1]
    holes = cv2.bitwise_not(outside)
    return cv2.bitwise_or(binary, holes)


MIN_SPLIT_SUBREGION_AREA = 2000  # 분할 후 유효한 sub-region으로 인정할 최소 픽셀 수


def _auto_split_flood_mask(
    mask: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_defect_depth: float = 15.0,
    min_sub_area: int = MIN_SPLIT_SUBREGION_AREA,
    _depth: int = 0,
) -> Optional[List[Dict[str, Any]]]:
    """
    Flood region mask에서 inflection point를 자동 탐지하여 분할한다.

    기존 _split_mask_by_inflection_line 과의 차이:
    - anchor 수 무관 (단일 anchor에서도 동작)
    - 분할 유효성을 'anchor가 양쪽에 있는가' 대신 '각 sub-region 면적이 충분한가'로 판단
    - anchor가 없는 쪽(CV 미인식 버블)도 OCR crop 생성 대상으로 포함
    - 직사각형 버블 오탐 방어: depth threshold 가 1차 방어 (직사각 코너는 depth 얕음)

    Returns:
        [{"anchor_ids": [...], "sub_mask": np.ndarray, "bubble_rect": [x,y,w,h]}, ...]
        분할 불가/불필요 시 None
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    total_area = cv2.contourArea(contour)

    # 전체 면적이 충분히 커야 의미 있는 분할
    if total_area < min_sub_area * 1.8:
        return None

    approx = cv2.approxPolyDP(contour, epsilon=2.0, closed=True)
    if len(approx) < 4:
        return None

    hull_idx = cv2.convexHull(approx, returnPoints=False)
    if hull_idx is None or len(hull_idx) < 3:
        return None

    try:
        defects = cv2.convexityDefects(approx, hull_idx)
    except cv2.error:
        return None

    if defects is None or len(defects) < 2:
        return None

    # depth 내림차순 정렬
    defect_list = sorted(
        [(int(d[0][2]), d[0][3] / 256.0) for d in defects],
        key=lambda x: x[1],
        reverse=True,
    )

    # 단일 앵커 region은 더 엄격한 depth 기준 적용
    # - 다중 앵커: 같은 flood 공유 = F-bubble 강한 증거 → 완화 기준 유지
    # - 단일 앵커: 버블 윤곽 불규칙성 오탐 + split crop 불균형으로 OCR 실패 가능
    #   → 두 defect 모두 300px 이상이어야 분할 (매우 강한 허리 신호만 허용)
    effective_min_depth = min_defect_depth if len(anchor_ids) >= 2 else max(min_defect_depth, 300.0)

    deep = [(idx, depth) for idx, depth in defect_list if depth >= effective_min_depth]
    if len(deep) < 2:
        return None

    p1 = tuple(approx[deep[0][0]][0])
    p2 = tuple(approx[deep[1][0]][0])

    if np.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 5:
        return None

    # 분할 실행
    h, w = mask.shape[:2]
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    ys_g, xs_g = np.mgrid[0:h, 0:w]
    side_map = (-dy) * (xs_g - p1[0]) + dx * (ys_g - p1[1])

    mask_pos = (binary > 0) & (side_map >= 0)
    mask_neg = (binary > 0) & (side_map <  0)

    def _side(pt: Point) -> bool:
        return ((-dy) * (pt[0] - p1[0]) + dx * (pt[1] - p1[1])) >= 0

    # anchor를 side별로 분배 (한쪽이 비어도 허용)
    groups = [
        {"anchor_ids": [], "sub_mask": mask_pos.astype(np.uint8) * 255},
        {"anchor_ids": [], "sub_mask": mask_neg.astype(np.uint8) * 255},
    ]
    for aid, ac in zip(anchor_ids, anchor_centers):
        groups[0 if _side(ac) else 1]["anchor_ids"].append(aid)

    # 각 sub-region 면적 검사 → 최소 면적 미달이면 분할 무효
    result: List[Dict[str, Any]] = []
    for g in groups:
        sm = g["sub_mask"]
        sub_area = int(np.count_nonzero(sm))
        if sub_area < min_sub_area:
            return None  # 한쪽이 너무 작으면 F-bubble 아닌 노이즈 → 분할 취소
        ys_s, xs_s = np.where(sm > 0)
        bbox = [
            int(xs_s.min()), int(ys_s.min()),
            int(xs_s.max() - xs_s.min() + 1),
            int(ys_s.max() - ys_s.min() + 1),
        ]
        result.append({
            "anchor_ids": g["anchor_ids"],  # 비어있어도 허용 (CV 미인식 버블)
            "sub_mask": sm,
            "bubble_rect": bbox,
        })

    # 재귀 분할: 변곡점 3개 이상 && 앵커 2개 이상인 서브 리전 존재 시
    # 최대 depth=2까지 재귀적으로 3분할 완성 시도
    if len(deep) >= 3 and _depth < 2:
        final_result: List[Dict[str, Any]] = []
        needs_recurse = False
        for sub in result:
            sub_ids = sub["anchor_ids"]
            if len(sub_ids) >= 2:
                sub_centers = [
                    ac for aid, ac in zip(anchor_ids, anchor_centers)
                    if aid in sub_ids
                ]
                sub_split = _auto_split_flood_mask(
                    sub["sub_mask"], sub_ids, sub_centers,
                    min_defect_depth, min_sub_area, _depth + 1,
                )
                if sub_split is not None:
                    final_result.extend(sub_split)
                    needs_recurse = True
                    continue
            final_result.append(sub)
        if needs_recurse:
            return final_result

    return result


_SINGLE_TG_SPLIT_MIN_SHAPE = 0.58
_SINGLE_TG_SPLIT_MIN_DEPTH_SUM = 20.0
_SINGLE_TG_SPLIT_MIN_AREA_RATIO = 0.10
# split line이 TG bbox 면적을 침범할 수 있는 최대 허용 비율 (8%)
# 이를 초과하면 텍스트를 실질적으로 가르는 것으로 판단하여 분할 기각
_SINGLE_TG_SPLIT_MAX_CUT_RATIO = 0.08
_SINGLE_TG_SPLIT_REQUIRE_DEFECT_ENDPOINTS = True


def _split_single_tg_bubble(
    mask: np.ndarray,
    anchor_id: int,
    anchor_center: Point,
    anchor_rect: List[int],
    quality_flags: List[str],
    min_sub_area: int = MIN_SPLIT_SUBREGION_AREA,
    allow_weak_turn: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """TG가 1개인 bubble region에서 CV 누락 텍스트 영역을 split으로 복구한다.

    조건:
    - shape_score ≥ 0.75 (명시적 값 필수, None이면 skip)
    - edge_barrier_rescue 아닌 flood 기반 bubble
    - split line이 TG bbox와 교차하지 않아야 함 (무한 직선 기준)
    - depth_sum ≥ 20, min_area_ratio ≥ 0.10

    Returns:
        [{"anchor_ids": [aid], ...}, {"anchor_ids": [], ...}]  — TG 있는 쪽 + orphan
        조건 불충족 시 None
    """
    # shape gate
    shape_score = None
    bubble_tg_ratio = None
    prefix = "shape="
    for flag in quality_flags:
        for part in str(flag).replace(",", " ").split():
            if part.startswith(prefix):
                try:
                    shape_score = float(part[len(prefix):])
                except ValueError:
                    pass
            if part.startswith("tg="):
                try:
                    bubble_tg_ratio = float(part[len("tg="):])
                except ValueError:
                    pass
    if shape_score is None or shape_score < _SINGLE_TG_SPLIT_MIN_SHAPE:
        return None
    # tg_ratio gate 폐기: cut_ratio(8%)가 TG bbox 침범 여부를 직접 판별하므로
    # 깔끔한 버블은 defect depth 조건에서 자연 실패, 부정형은 split 시도 허용

    # edge_barrier_rescue 제외
    flags_str = " ".join(str(f) for f in quality_flags)
    if "edge_barrier_rescue" in flags_str:
        return None

    result = collect_inflection_candidates(
        mask,
        anchor_centers=[anchor_center],
        min_defect_depth=15.0,
        anchor_rects=[anchor_rect],
        allow_single_weak_turn=allow_weak_turn,
        max_pair_points=10,
    )

    candidates = result.get("candidates", [])
    best = None
    best_score = -1.0
    # mask 무게중심은 후보와 무관하므로 처음 필요할 때 한 번만 구한다. (None: 아직 안 구함, (): 빈 mask)
    mask_centroid: Optional[Tuple[int, ...]] = None

    for c in candidates:
        if not c.get("bboxes_clear", True):
            continue
        p1 = tuple(int(v) for v in c["p1"])
        p2 = tuple(int(v) for v in c["p2"])
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        dist = max(1.0, (dx**2 + dy**2) ** 0.5)
        # split line이 TG bbox 면적을 실질적으로 침범하는 비율(cut_ratio) 계산
        # cut_ratio > _SINGLE_TG_SPLIT_MAX_CUT_RATIO(8%)면 텍스트를 가르는 것 → 기각
        _ax, _ay, _aw, _ah = (
            anchor_rect[0], anchor_rect[1], anchor_rect[2], anchor_rect[3]
        )
        _h_m, _w_m = mask.shape[:2]
        _x1c = max(0, _ax); _y1c = max(0, _ay)
        _x2c = min(_w_m, _ax + _aw); _y2c = min(_h_m, _ay + _ah)
        if _x1c < _x2c and _y1c < _y2c:
            _ys_b, _xs_b = np.mgrid[_y1c:_y2c, _x1c:_x2c]
            _side_local = (-dy) * (_xs_b - p1[0]) + dx * (_ys_b - p1[1])
            _anchor_sign = (-dy) * (anchor_center[0] - p1[0]) + dx * (anchor_center[1] - p1[1])
            _cut_pix = int(
                np.count_nonzero(_side_local < 0)
                if _anchor_sign >= 0
                else np.count_nonzero(_side_local >= 0)
            )
            _bbox_area = (_x2c - _x1c) * (_y2c - _y1c)
            _cr = _cut_pix / max(1, _bbox_area)
            if _cr > _SINGLE_TG_SPLIT_MAX_CUT_RATIO:
                continue
        _mar = c.get("min_area_ratio", 0)
        if _mar < _SINGLE_TG_SPLIT_MIN_AREA_RATIO:
            continue
        depths = c.get("depths", [0, 0])
        if sum(depths) < _SINGLE_TG_SPLIT_MIN_DEPTH_SUM:
            continue
        if _SINGLE_TG_SPLIT_REQUIRE_DEFECT_ENDPOINTS:
            point_sources = c.get("point_sources", [])
            if len(point_sources) != 2 or any(source != "defect" for source in point_sources):
                continue

        # centroid 근접도 기반 점수
        if mask_centroid is None:
            ys, xs = np.where(mask > 0)
            mask_centroid = (int(xs.mean()), int(ys.mean())) if len(xs) else ()
        if not mask_centroid:
            continue
        cx_r, cy_r = mask_centroid
        dist_centroid = abs((-dy) * (cx_r - p1[0]) + dx * (cy_r - p1[1])) / dist
        centroid_score = 1.0 / (1.0 + dist_centroid / 50.0)
        score = sum(depths) * 0.01 + c.get("area_balance", 0) * 0.5 + centroid_score * 0.5
        if score > best_score:
            best_score = score
            best = c

    if best is None:
        return None

    # split 실행
    p1 = tuple(int(v) for v in best["p1"])
    p2 = tuple(int(v) for v in best["p2"])
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    h, w = mask.shape[:2]
    ys_g, xs_g = np.mgrid[0:h, 0:w]
    side_map = (-dy) * (xs_g - p1[0]) + dx * (ys_g - p1[1])

    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    anchor_side = ((-dy) * (anchor_center[0] - p1[0]) + dx * (anchor_center[1] - p1[1])) >= 0
    mask_tg = (binary > 0) & ((side_map >= 0) == anchor_side)
    mask_orphan = (binary > 0) & ((side_map >= 0) != anchor_side)

    sub_result = []
    for aids, sm in [([ anchor_id], mask_tg), ([], mask_orphan)]:
        sm_u8 = sm.astype(np.uint8) * 255
        sub_area = int(np.count_nonzero(sm_u8))
        if sub_area < min_sub_area:
            return None
        ys_s, xs_s = np.where(sm_u8 > 0)
        bbox = [
            int(xs_s.min()), int(ys_s.min()),
            int(xs_s.max() - xs_s.min() + 1),
            int(ys_s.max() - ys_s.min() + 1),
        ]
        sub_result.append({
            "anchor_ids": aids,
            "sub_mask": sm_u8,
            "bubble_rect": bbox,
            "split_line": [list(p1), list(p2)],
        })

    return sub_result


def _split_same_column_continuation_bubble(
    mask: np.ndarray,
    anchor_ids: List[int],
    anchor_rects: List[List[int]],
    quality_flags: List[str],
) -> Optional[List[Dict[str, Any]]]:
    """같은 세로줄로 보이는 2-anchor를 하나의 anchor처럼 보고 orphan split을 재시도한다."""
    if len(anchor_ids) != 2 or len(anchor_rects) != 2:
        return None

    r1, r2 = anchor_rects
    x1, y1, w1, h1 = _unpack_rect(r1)
    x2, y2, w2, h2 = _unpack_rect(r2)
    if min(w1, w2, h1, h2) <= 0:
        return None

    overlap_x = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    x_overlap_ratio = overlap_x / max(1, min(w1, w2))
    cx1, cy1 = _bbox_center(r1)
    cx2, cy2 = _bbox_center(r2)
    center_x_delta = abs(cx1 - cx2)

    overlap_y = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    y_overlap_ratio = overlap_y / max(1, min(h1, h2))
    y_gap = max(0, max(y1, y2) - min(y1 + h1, y2 + h2))
    max_width = max(w1, w2)
    union_rect = _union_rect(anchor_rects)

    same_column = (
        x_overlap_ratio >= 0.80
        and center_x_delta <= max(6.0, max_width * 0.35)
        and y_overlap_ratio <= 0.10
        and y_gap <= max(48.0, max_width * 1.6)
        and union_rect[2] <= max_width * 1.35
    )
    if not same_column:
        return None

    split_result = _split_single_tg_bubble(
        mask,
        anchor_id=anchor_ids[0],
        anchor_center=_bbox_center(union_rect),
        anchor_rect=union_rect,
        quality_flags=quality_flags,
        allow_weak_turn=False,
    )
    if split_result is None:
        return None

    split_line = next(
        (sub.get("split_line") for sub in split_result if sub.get("split_line")),
        None,
    )
    if not split_line:
        return None
    p1, p2 = split_line
    dx = abs(int(p2[0]) - int(p1[0]))
    dy = abs(int(p2[1]) - int(p1[1]))
    if dy < 20 or dy < dx * 1.4:
        return None

    for sub in split_result:
        if sub.get("anchor_ids"):
            sub["anchor_ids"] = list(anchor_ids)
    return split_result


def _is_bubble_shaped(mask: np.ndarray, min_solidity: float = 0.85) -> bool:
    """flood fill inner mask가 말풍선(타원/직사각) 형태인지 solidity로 판단한다."""
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    cnt = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    if area < MIN_SAFE_INNER_AREA:
        return False
    hull_area = cv2.contourArea(cv2.convexHull(cnt))
    if hull_area <= 0:
        return False
    return (area / hull_area) >= min_solidity


def _extract_mask_polygon(mask: np.ndarray, simplify_epsilon: float = 2.0) -> Optional[List[List[int]]]:
    """mask에서 가장 큰 윤곽선을 다각형 좌표 리스트로 추출한다.

    향후 마스크 롤백 시 FloodFill 재실행 없이 cv2.fillPoly로 복원할 수 있도록,
    비정형 말풍선 윤곽선을 JSON에 저장하기 위한 헬퍼이다.
    simplify_epsilon이 클수록 꼭짓점 수가 줄어 JSON 크기가 줄어든다.
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) < MIN_SAFE_INNER_AREA:
        return None
    approx = cv2.approxPolyDP(cnt, simplify_epsilon, True)
    return [[int(pt[0][0]), int(pt[0][1])] for pt in approx]

def _compute_flood_bimodality(mask: np.ndarray, gray: np.ndarray) -> float:
    """flood mask 픽셀의 흑/백 bimodality를 반환한다 (0~1, 높을수록 흑백에 집중).

    말풍선 내부는 흰 배경 + 검은 텍스트 → 0.93 이상.
    글자 외곽선을 파고든 flood는 JPEG 노이즈/반안티앨리어싱으로 gray 픽셀 증가 → 0.85 미만.
    방사형·뾰족 말풍선은 내부 픽셀이 정상이므로 roundness와 달리 오탐하지 않는다.
    """
    pixels = gray[mask > 0]
    if len(pixels) == 0:
        return 1.0
    white_ratio = float(np.sum(pixels >= 220) / len(pixels))
    black_ratio = float(np.sum(pixels <= 40) / len(pixels))
    return white_ratio + black_ratio


def _is_dark_bg_text(gray: np.ndarray, rect, threshold: int = 120, expand: int = 20) -> bool:
    """bbox를 expand px 확장한 영역의 평균 밝기가 threshold 미만이면 검은 배경으로 판단한다.

    진짜 검정 배경은 확장해도 어둡고, 회색/흰 배경은 확장하면 밝아져 오분류를 줄인다.
    단, 흰 글자/주변 여백이 평균을 끌어올리는 가로 헤더형 DarkBG는
    bbox 내부 dark ratio와 median으로 보조 판정한다.
    """
    x, y, w, h = _unpack_rect(rect)
    img_h, img_w = gray.shape[:2]
    if w <= 0 or h <= 0:
        return False
    core = gray[y:min(img_h, y + h), x:min(img_w, x + w)]
    if core.size > 0:
        core_dark_ratio = float(np.mean(core < min(150, threshold + 20)))
        core_median = float(np.median(core))
        if core_dark_ratio >= 0.58 and core_median < threshold:
            return True

    x1, y1 = max(0, x - expand), max(0, y - expand)
    x2, y2 = min(img_w, x + w + expand), min(img_h, y + h + expand)
    if x2 <= x1 or y2 <= y1:
        return False
    return float(gray[y1:y2, x1:x2].mean()) < threshold


def _is_horizontal_text_group(cluster: Dict[str, Any], rect: List[int]) -> bool:
    """DarkBG 제외는 가로 챕터/효과 텍스트에만 적용하기 위한 방향 판정.

    Cloud Vision이 orientation='vertical'로 분류해도 실제 bbox가 가로로 긴 경우
    (하단 캡션/챕터/효과음 등 한 줄짜리 가로 배열 텍스트)는 horizontal로 판정한다.
    """
    orientation = str(cluster.get("orientation", "")).lower()
    _x, _y, w, h = _unpack_rect(rect)
    landscape_by_rect = w > 0 and h > 0 and (w / h) >= 1.15
    # 명시적 horizontal 태그
    if orientation == "horizontal":
        return True
    # orientation=vertical이어도 실제 bbox가 가로형이면 horizontal로 오버라이드
    if landscape_by_rect:
        return True
    # orientation=vertical이면 False, 그 외 미지정 방향은 1.15:1 이상이면 horizontal
    if orientation == "vertical":
        return False
    return w >= h * 1.15


def _is_edge_narrow_metadata_rect(rect: List[int], img_w: int) -> bool:
    x, _y, w, h = _unpack_rect(rect)
    return bool(
        w <= EDGE_NARROW_METADATA_MAX_WIDTH
        and h >= EDGE_NARROW_METADATA_MIN_HEIGHT
        and (x <= EDGE_NARROW_METADATA_MARGIN or (x + w) >= img_w - EDGE_NARROW_METADATA_MARGIN)
    )


def _touches_image_border(mask: np.ndarray, tolerance: int = 2) -> bool:
    if mask.size == 0:
        return False
    top = mask[:tolerance, :]
    bottom = mask[-tolerance:, :]
    left = mask[:, :tolerance]
    right = mask[:, -tolerance:]
    return bool(np.any(top > 0) or np.any(bottom > 0) or np.any(left > 0) or np.any(right > 0))


def _union_rect(rects: List[List[int]]) -> List[int]:
    if not rects:
        return [0, 0, 0, 0]
    xs1 = [int(r[0]) for r in rects if len(r) >= 4]
    ys1 = [int(r[1]) for r in rects if len(r) >= 4]
    xs2 = [int(r[0]) + int(r[2]) for r in rects if len(r) >= 4]
    ys2 = [int(r[1]) + int(r[3]) for r in rects if len(r) >= 4]
    if not xs1:
        return [0, 0, 0, 0]
    x1, y1 = min(xs1), min(ys1)
    x2, y2 = max(xs2), max(ys2)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _get_text_group_rect(cluster: Dict[str, Any], primary_key: str) -> List[int]:
    """cluster dict에서 primary_key → lines union → bounding_rect 순으로 rect를 추출한다."""
    if len(cluster.get(primary_key, [])) >= 4:
        return [int(v) for v in cluster[primary_key][:4]]
    if primary_key == "tight_rect":
        line_rects = [
            line.get("bbox", [])
            for line in cluster.get("lines", [])
            if len(line.get("bbox", [])) >= 4
        ]
        tight = _union_rect(line_rects)
        if tight[2] > 0 and tight[3] > 0:
            return tight
    return [int(v) for v in cluster.get("bounding_rect", [0, 0, 0, 0])[:4]]


def _tight_text_group_rect(cluster: Dict[str, Any]) -> List[int]:
    """판정용 TG bbox를 반환한다. Ruby 보정 확장은 포함하지 않는다."""
    return _get_text_group_rect(cluster, "tight_rect")


def _fallback_text_group_rect(cluster: Dict[str, Any]) -> List[int]:
    """최후 fallback crop용 TG bbox를 반환한다. 기존 ruby 보정 bbox를 유지한다."""
    return _get_text_group_rect(cluster, "fallback_rect")


def _text_group_id(cluster: Dict[str, Any], fallback: int) -> int:
    return int(cluster.get("text_group_id", cluster.get("cluster_id", fallback)))


def _is_symbol_only_bubble_candidate(
    mask: np.ndarray,
    bubble_tg_ratio: float,
    touches_border: bool,
    bubble_page_ratio: float,
    max_area_ratio: float = MAX_SAFE_AREA_RATIO,
) -> bool:
    """순수 기호 텍스트라도 명백한 말풍선이면 OCR 후보로 살릴지 판단한다."""
    if touches_border:
        return False
    if bubble_page_ratio <= 0 or bubble_page_ratio > max_area_ratio:
        return False
    if bubble_tg_ratio < 1.8 or bubble_tg_ratio > 10.0:
        return False
    if not _is_bubble_shaped(mask, min_solidity=0.9):
        return False
    return True


def _classify_anchor_region(
    cluster: Dict[str, Any],
    bubble_rect: List[int],
    inner_area: int,
    touches_border: bool,
    safe: bool,
) -> Tuple[str, List[str], float]:
    x, y, w, h = _tight_text_group_rect(cluster)
    fx, fy, fw, fh = _unpack_rect(bubble_rect)
    anchor_area = max(1, w * h)
    bubble_rect_ratio = (fw * fh) / anchor_area
    text = _cluster_text(cluster)
    stats = _text_stats(text)
    total_signal = max(1, stats["jp_chars"] + stats["alnum_chars"] + stats["symbol_chars"])
    symbol_ratio = stats["symbol_chars"] / total_signal

    flags: List[str] = []
    if inner_area == 0:
        flags.append("empty_inner_area")
    if bubble_rect_ratio < 0.2:
        flags.append("tiny_bubble_rect")
    if stats["jp_chars"] <= 1 and symbol_ratio >= 0.45:
        flags.append("symbol_heavy_low_japanese_text")
    if bubble_rect_ratio >= 12.0 and symbol_ratio >= 0.35:
        flags.append("oversized_symbol_heavy_flood")
    if touches_border:
        flags.append("touches_image_border")
    if bubble_rect_ratio >= 6.0:
        flags.append("large_bubble_tg_ratio")

    if (
        "empty_inner_area" in flags
        or "tiny_bubble_rect" in flags
        or "symbol_heavy_low_japanese_text" in flags
        or "oversized_symbol_heavy_flood" in flags
    ):
        return "noise_like", flags, bubble_rect_ratio

    if stats["jp_chars"] >= 4 and bubble_rect_ratio >= 8.0:
        return "narration_like", flags, bubble_rect_ratio

    if safe:
        return "bubble", flags, bubble_rect_ratio

    return "failed_candidate", flags, bubble_rect_ratio


def visualize_anchor_floodfill(
    image_path: str,
    clusters_json: str,
    output_json_path: str,
    output_image_path: str,
    dark_bg_mean_threshold: int = 130,
) -> Dict[str, Any]:
    image, gray, img_h, img_w = _load_image_and_gray(image_path)
    clusters = _load_json(clusters_json)
    image_area = img_w * img_h

    overlay = image.copy()
    debug = image.copy()
    results: List[Dict[str, Any]] = []

    palette = [
        (255, 120,  60),   # 오렌지
        ( 60, 180, 255),   # 스카이블루
        (100, 210,  80),   # 라임
        (255, 200,  50),   # 옐로우
        (180, 100, 255),   # 라벤더
        ( 50, 210, 180),   # 틸
        (255, 100, 140),   # 코럴
        ( 80, 200, 120),   # 민트
    ]

    for idx, cluster in enumerate(clusters, start=1):
        rect = _tight_text_group_rect(cluster)
        fallback_rect = _fallback_text_group_rect(cluster)
        if len(rect) < 4:
            continue

        cluster_id = _text_group_id(cluster, idx)
        center = _bbox_center(rect)
        x, y, w, h = _unpack_rect(rect)

        is_symbol_only = _is_symbol_only_text(_cluster_text(cluster))

        # 이미지 좌/우 끝 narrow 메타데이터 (면책 문구 등): 시각화에서도 스킵
        _vis_narrow_meta = _is_edge_narrow_metadata_rect(rect, img_w)
        if _vis_narrow_meta:
            results.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "anchor_center": list(center),
                "region_type_guess": "metadata",
                "quality_flags": ["edge_narrow_metadata"],
            })
            continue

        if _is_dark_bg_text(gray, rect, dark_bg_mean_threshold) and _is_horizontal_text_group(cluster, rect):
            cv2.rectangle(debug, (x, y), (x + w, y + h), (40, 40, 40), 2)
            label = f"A{cluster_id}"
            lx, ly = x, max(28, y - 8)
            font = cv2.FONT_HERSHEY_SIMPLEX
            (tw, th), bl = cv2.getTextSize(label, font, 0.9, 3)
            cv2.rectangle(debug, (max(0, lx - 4), max(0, ly - th - 8)),
                          (min(img_w - 1, lx + tw + 8), min(img_h - 1, ly + bl + 6)), (30, 30, 30), -1)
            cv2.putText(debug, label, (lx, ly), font, 0.9, (160, 160, 160), 3, cv2.LINE_AA)
            cv2.putText(debug, label, (lx, ly), font, 0.9, (80, 80, 80), 2, cv2.LINE_AA)
            results.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "anchor_center": list(center),
                "region_type_guess": "dark_bg_text",
                "quality_flags": ["dark_background", "horizontal_dark_bg_skip"],
            })
            continue

        cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 150, 255), 3)
        cv2.circle(debug, center, 5, (0, 0, 255), -1)

        seed, mask, area, bubble_rect = _flood_with_fallback(image, gray, rect)

        result = {
            "cluster_id": cluster_id,
            "anchor_bbox": rect,
            "anchor_center": list(center),
            "seed": list(seed) if seed else None,
            "flood_area": 0,
            "bubble_page_ratio": 0,
            "bubble_rect": [0, 0, 0, 0],
        }

        if seed is None:
            if is_symbol_only:
                result["region_type_guess"] = "symbol_only"
                result["quality_flags"] = ["symbol_only_text"]
            results.append(result)
            continue
        filled_mask = _fill_mask_holes(mask)
        inner_mask = _erode_mask(filled_mask, pixels=3)
        inner_area = int(np.count_nonzero(inner_mask))
        touches_border = _touches_image_border(mask)
        area_ratio = area / image_area
        safe = (
            area_ratio <= MAX_SAFE_AREA_RATIO
            and inner_area >= MIN_SAFE_INNER_AREA
        )
        if is_symbol_only:
            bubble_tg_ratio = (
                (bubble_rect[2] * bubble_rect[3])
                / max(1, int(rect[2]) * int(rect[3]))
            )
            if _is_symbol_only_bubble_candidate(
                inner_mask,
                bubble_tg_ratio,
                touches_border,
                area_ratio,
            ):
                region_type = "symbol_only_bubble"
                quality_flags = ["symbol_only_text", "symbol_only_bubble_candidate"]
            else:
                result.update({
                    "flood_area": int(area),
                    "bubble_page_ratio": round(float(area_ratio), 5),
                    "bubble_rect": [int(v) for v in bubble_rect[:4]],
                    "inner_area": inner_area,
                    "inner_area_ratio": round(float(inner_area / image_area), 5),
                    "bubble_tg_ratio": round(float(bubble_tg_ratio), 2),
                    "touches_image_border": touches_border,
                    "safe_region": False,
                    "region_type_guess": "symbol_only",
                    "quality_flags": ["symbol_only_text"],
                })
                results.append(result)
                cv2.rectangle(debug, (x, y), (x + w, y + h), (180, 180, 180), 2)
                label = f"A{cluster_id}"
                lx, ly = x, max(28, y - 8)
                font = cv2.FONT_HERSHEY_SIMPLEX
                (tw, th), bl = cv2.getTextSize(label, font, 0.9, 3)
                cv2.rectangle(debug, (max(0, lx - 4), max(0, ly - th - 8)),
                              (min(img_w - 1, lx + tw + 8), min(img_h - 1, ly + bl + 6)), (160, 160, 160), -1)
                cv2.putText(debug, label, (lx, ly), font, 0.9, (80, 80, 80), 3, cv2.LINE_AA)
                cv2.putText(debug, label, (lx, ly), font, 0.9, (40, 40, 40), 2, cv2.LINE_AA)
                continue
        else:
            region_type, quality_flags, bubble_tg_ratio = _classify_anchor_region(
                cluster,
                bubble_rect,
                inner_area,
                touches_border,
                safe,
            )
            # 글자 외곽선을 파고드는 flood: 내부 픽셀 bimodality 저하 → noise_like 강등
            if region_type == "bubble":
                _bm = _compute_flood_bimodality(filled_mask, gray)
                if _bm < 0.85:
                    region_type = "noise_like"
                    quality_flags = [*quality_flags, f"low_bimodal={round(_bm,4)}"]
        color = palette[(idx - 1) % len(palette)]

        if region_type == "bubble":
            mask_area = inner_mask > 0
            overlay[mask_area] = (
                overlay[mask_area].astype(np.float32) * 0.65
                + np.array(color, dtype=np.float32) * 0.35
            ).astype(np.uint8)
        elif region_type == "narration_like":
            anchor_area = np.zeros((img_h, img_w), dtype=bool)
            anchor_area[max(0, y):min(img_h, y + h), max(0, x):min(img_w, x + w)] = True
            narration_color = np.array((220, 120, 255), dtype=np.float32)  # 연한 퍼플
            overlay[anchor_area] = (
                overlay[anchor_area].astype(np.float32) * 0.60
                + narration_color * 0.40
            ).astype(np.uint8)
        elif region_type == "noise_like":
            cv2.rectangle(debug, (x, y), (x + w, y + h), (90, 90, 90), 4)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inner_contours, _ = cv2.findContours(inner_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        draw_color = color
        if region_type == "narration_like":
            draw_color = (200, 100, 240)  # 연한 퍼플
        elif region_type == "noise_like":
            draw_color = (90, 90, 90)
        cv2.drawContours(debug, contours, -1, draw_color, 1)
        if region_type == "bubble":
            cv2.drawContours(debug, inner_contours, -1, draw_color, 2)
        cv2.circle(debug, seed, 6, color, -1)

        fx, fy, fw, fh = bubble_rect
        if region_type == "narration_like":
            cv2.rectangle(debug, (x, y), (x + w, y + h), (200, 100, 240), 3)
            cv2.rectangle(debug, (fx, fy), (fx + fw, fy + fh), (120, 120, 120), 1)
        elif region_type == "noise_like":
            cv2.rectangle(debug, (x, y), (x + w, y + h), (90, 90, 90), 5)
        else:
            cv2.rectangle(debug, (fx, fy), (fx + fw, fy + fh), color, 2)
        label = f"A{cluster_id}"
        label_x, label_y = x, max(28, y - 8)
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.75
        thickness = 2
        (tw, th), baseline = cv2.getTextSize(label, font, scale, thickness)
        bg_tl = (max(0, label_x - 3), max(0, label_y - th - 6))
        bg_br = (min(img_w - 1, label_x + tw + 6), min(img_h - 1, label_y + baseline + 4))
        # 반투명 느낌을 위해 palette color로 배경 채우기 (완전 검정 대신)
        cv2.rectangle(debug, bg_tl, bg_br, (40, 40, 40), -1)
        cv2.rectangle(debug, bg_tl, bg_br, color, 2)
        cv2.putText(debug, label, (label_x, label_y), font, scale, (255, 255, 255), thickness + 2, cv2.LINE_AA)
        cv2.putText(debug, label, (label_x, label_y), font, scale, color, thickness, cv2.LINE_AA)

        result.update({
            "seed": list(seed),
            "flood_area": area,
            "bubble_page_ratio": round(area_ratio, 5),
            "bubble_rect": bubble_rect,
            "inner_area": inner_area,
            "inner_area_ratio": round(inner_area / image_area, 5),
            "erode_pixels": 3,
            "touches_image_border": touches_border,
            "safe_region": safe,
            "region_type_guess": region_type,
            "quality_flags": quality_flags,
            "bubble_tg_ratio": round(float(bubble_tg_ratio), 2),
        })
        results.append(result)

    final = cv2.addWeighted(overlay, 0.35, debug, 0.65, 0)
    cv2.imwrite(output_image_path, final)

    output = {
        "image": image_path,
        "clusters": clusters_json,
        "results": results,
    }
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output



def build_ocr_region_sheet(
    image_path: str,
    clusters_json: str,
    output_masked_path: str = None,
    output_sheet_path: str = None,
    output_json_path: str = None,
    indexed_output_sheet_path: Optional[str] = None,
    output_regions_only_path: Optional[str] = None,
    output_vis_path: Optional[str] = None,
    debug_mode: bool = False,
    erode_pixels: int = 3,
    max_area_ratio: float = MAX_SAFE_AREA_RATIO,
    min_bubble_tg_ratio: float = 1.0,
    max_bubble_tg_ratio: float = 20.0,
    indexed_label_gutter: int = 48,
    dark_bg_mean_threshold: int = 130,
    enable_edge_barrier_rescue: bool = True,
    image: Optional[np.ndarray] = None,
    gray: Optional[np.ndarray] = None,
    manual_virtual_walls: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    # 호출자가 이미지를 미리 읽어 넘겨준 경우 재독(disk I/O) 생략
    if image is None or gray is None:
        image, gray, img_h, img_w = _load_image_and_gray(image_path)
    else:
        img_h, img_w = image.shape[:2]
    clusters = _load_json(clusters_json)
    image_area = img_w * img_h

    grouped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    diagnostics: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    noise_like_anchors: List[Dict[str, Any]] = []

    # black contour probe용 페이지 mask는 이 호출 안에서 한 번만 만든다(호출 간 공유 없음).
    page_black_contour: List[np.ndarray] = []

    def _page_black_contour() -> np.ndarray:
        if not page_black_contour:
            page_black_contour.append(_black_contour_binary(gray))
        return page_black_contour[0]

    def _extract_quality_float(flags: List[str], name: str) -> Optional[float]:
        prefix = f"{name}="
        for flag in flags:
            for part in str(flag).replace(",", " ").split():
                if part.startswith(prefix):
                    try:
                        return float(part[len(prefix):])
                    except ValueError:
                        return None
        return None

    def _edge_anchor_text_reject_reason(
        anchor_groups: Optional[List[Dict[str, Any]]],
        raw_round: Optional[float],
    ) -> Optional[str]:
        if not anchor_groups or raw_round is None or raw_round >= 0.06:
            return None
        if len(anchor_groups) < 2:
            return None

        texts = [_cluster_text(group).replace(" ", "").replace("\n", "") for group in anchor_groups]
        total_chars = sum(len(text) for text in texts)
        max_lines = max((len(group.get("lines", [])) for group in anchor_groups), default=0)
        max_height = max((_tight_text_group_rect(group)[3] for group in anchor_groups), default=0)
        if total_chars <= 6 and max_lines <= 1 and max_height <= 140:
            return "edge_reject_short_handwritten_group_unboxed_like"
        return None

    def _edge_rescue_reject_reason(
        region: Dict[str, Any],
        rescue: Dict[str, Any],
        anchor_groups: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        flags = region.get("quality_flags", [])
        raw_solid = _extract_quality_float(flags, "raw_solid")
        raw_round = _extract_quality_float(flags, "raw_round")
        shape_score = _extract_quality_float(flags, "shape_score")
        if shape_score is None:
            shape_score = _extract_quality_float(flags, "shape")
        score = rescue.get("score", {})
        original_touches_border = any("border=Y" in str(flag) for flag in flags)
        failed_bounds = any("failed_bounds" in str(flag) for flag in flags)
        edge_page = float(score.get("mask_page_ratio", 0.0))
        edge_ratio = float(score.get("rect_anchor_ratio", 0.0))
        guard_mode = str(rescue.get("guard_mode", "none"))
        anchor_texts = [
            _cluster_text(group).replace(" ", "").replace("\n", "")
            for group in (anchor_groups or [])
        ]
        anchor_total_chars = sum(len(text) for text in anchor_texts)
        anchor_max_lines = max((len(group.get("lines", [])) for group in (anchor_groups or [])), default=0)
        anchor_reject = _edge_anchor_text_reject_reason(anchor_groups, raw_round)
        if anchor_reject is not None:
            return anchor_reject

        if (
            anchor_groups
            and anchor_total_chars <= 4
            and anchor_max_lines <= 1
            and guard_mode in {"vertical", "all"}
            and edge_ratio >= 4.0
            and edge_page <= 0.012
        ):
            return "edge_reject_short_virtual_guard_sfx_like"
        rescue_rect = [int(v) for v in rescue.get("rescue_rect", [0, 0, 0, 0])[:4]]
        boundary_sides, boundary_ratios = _boundary_evidence(gray, rescue_rect)
        if boundary_sides < 3:
            return (
                "edge_reject_weak_boundary_evidence"
                f"(sides={boundary_sides},"
                f"t={boundary_ratios['top']:.2f},b={boundary_ratios['bottom']:.2f},"
                f"l={boundary_ratios['left']:.2f},r={boundary_ratios['right']:.2f})"
            )
        if (
            raw_solid is not None
            and raw_round is not None
            and shape_score is not None
            and raw_solid < 0.82
            and raw_round < 0.34
            and shape_score < 0.70
        ):
            return "edge_reject_low_shape_class_none"
        if shape_score is not None and shape_score < 0.05:
            return "edge_reject_too_fragmented_original_shape"
        if failed_bounds and guard_mode == "all":
            return "edge_reject_all_guard_failed_bounds_unboxed_like"
        if edge_page > 0.08:
            return "edge_reject_large_unboxed_like"
        if failed_bounds and edge_page > 0.045:
            return "edge_reject_large_failed_bounds_unboxed_like"
        if failed_bounds and edge_ratio < 1.2:
            return "edge_reject_tight_failed_bounds_unboxed_like"
        # rescue 결과가 TG bbox 를 거의 벗어나지 않은 경우 — 실제 말풍선 없음
        if edge_ratio < 1.2:
            return "edge_reject_too_tight_rescue"
        # 극도로 얇은 세로 텍스트(낮은 roundness) + 적은 확장 비율은 나레이션 오탐 후보.
        # 다만 방사형 말풍선도 원본 flood roundness가 낮게 나오므로 rescue/contour 증거가 약할 때만 자른다.
        if (
            raw_round is not None
            and raw_round < 0.06
            and edge_ratio < 2.5
        ):
            contour_probe = _black_contour_probe(gray, rescue_rect, rescue.get("mask"), black=_page_black_contour())
            weak_evidence_count = sum([
                contour_probe.get("status") != "closed",
                boundary_sides < 4,
                float(score.get("shape_score", 0.0)) < 0.86,
                float(score.get("anchor_coverage", 0.0)) < 0.92,
                float(score.get("bimodality", 0.0)) < 0.92,
            ])
            if weak_evidence_count >= 3:
                return "edge_reject_thin_small_ratio_narration_like"
        if raw_solid is not None and raw_solid < 0.60 and float(score.get("shape_score", 0.0)) < 0.85:
            return "edge_reject_low_original_solidity"
        if (
            float(score.get("borderline_open_bubble", 0.0)) > 0.0
            and raw_solid is not None
            and raw_round is not None
            and raw_solid < 0.80
            and raw_round >= 0.18
        ):
            return "edge_reject_borderline_low_original_solidity"
        if (
            raw_solid is not None
            and raw_solid < 0.60
            and edge_page > 0.01
        ):
            return "edge_reject_low_original_solidity"
        if (
            raw_solid is not None
            and raw_round is not None
            and not original_touches_border
            and raw_solid < 0.66
            and raw_round < 0.055
        ):
            return "edge_reject_low_original_solidity"
        if (
            raw_solid is not None
            and raw_round is not None
            and not original_touches_border
            and raw_solid < 0.68
            and raw_round >= 0.06
        ):
            return "edge_reject_low_original_solidity"
        if raw_solid is not None and raw_round is not None and raw_solid >= 0.82 and 0.16 <= raw_round < 0.30:
            return "edge_reject_simple_mid_round_unboxed_like"
        if (
            raw_solid is not None
            and raw_round is not None
            and raw_solid >= 0.82
            and raw_round < 0.09
            and edge_ratio > 4.8
        ):
            return "edge_reject_thin_large_ratio_unboxed_like"
        if raw_round is not None and raw_round < 0.10 and guard_mode == "all":
            return "edge_reject_all_guard_thin_unboxed_like"
        if (
            raw_solid is not None
            and raw_round is not None
            and 0.80 <= raw_solid <= 0.86
            and raw_round >= 0.35
            and edge_page > 0.025
        ):
            return "edge_reject_mid_solid_large_unboxed_like"
        if (
            raw_solid is not None
            and raw_round is not None
            and 0.76 <= raw_solid <= 0.82
            and 0.10 <= raw_round <= 0.16
            and edge_ratio >= 4.5
            and edge_page >= 0.02
        ):
            return "edge_reject_rect_narration_like"
        if (
            raw_solid is not None
            and raw_round is not None
            and raw_solid < 0.70
            and raw_round < 0.10
            and edge_page > 0.03
        ):
            return "edge_reject_low_solid_large_thin_unboxed_like"
        if (
            raw_round is not None
            and raw_round < 0.10
            and edge_page > 0.055
        ):
            return "edge_reject_large_thin_unboxed_like"
        if (
            raw_solid is not None
            and raw_round is not None
            and original_touches_border
            and raw_solid <= 0.72
            and raw_round < 0.18
            and edge_page > 0.03
        ):
            return "edge_reject_border_large_low_shape_unboxed_like"
        if (
            raw_solid is not None
            and raw_round is not None
            and original_touches_border
            and shape_score is not None
            and shape_score < 0.45
            and raw_round < 0.08
            and edge_ratio >= 3.5
            and edge_page >= 0.012
            and guard_mode in {"both", "all", "vertical"}
        ):
            return "edge_reject_border_shape_none_thin_rescue"
        if (
            raw_solid is not None
            and raw_round is not None
            and original_touches_border
            and raw_solid >= 0.80
            and raw_solid <= 0.86
            and 0.08 <= raw_round <= 0.16
        ):
            return "edge_reject_border_low_round_unboxed_like"
        if (
            raw_round is not None
            and original_touches_border
            and guard_mode == "all"
            and raw_round >= 0.25
            and edge_page > 0.018
        ):
            return "edge_reject_all_guard_border_narration_like"
        if (
            raw_solid is not None
            and raw_round is not None
            and original_touches_border
            and raw_solid >= 0.90
            and 0.30 <= raw_round <= 0.45
            and edge_ratio < 3.0
            and edge_page > 0.02
        ):
            return "edge_reject_border_rect_unboxed_like"
        if raw_solid is not None and raw_round is not None and raw_solid > 0.90 and raw_round < 0.18:
            return "edge_reject_solid_low_round_unboxed_like"
        if float(score.get("anchor_coverage", 0.0)) < 0.65:
            return "edge_reject_low_anchor_coverage"
        if (
            raw_solid is not None
            and raw_round is not None
            and 0.82 <= raw_solid <= 0.90
            and 0.30 <= raw_round <= 0.36
        ):
            return "edge_reject_simple_mid_round_unboxed_like"
        return None

    def _derive_review_tags(region: Dict[str, Any]) -> List[str]:
        flags = [str(flag) for flag in region.get("quality_flags", [])]
        flag_text = " ".join(flags)
        tags: List[str] = []

        def add(tag: str) -> None:
            if tag not in tags:
                tags.append(tag)

        if region.get("method") == "edge_barrier_rescue":
            add("edge_rescue")

        if region.get("is_valid"):
            return tags

        if "edge_barrier_rescue_failed=" in flag_text:
            add("edge_no_safe_mask")
            add("image_gen_review_candidate")
        if "edge_reject_low_original_solidity" in flag_text:
            add("edge_rejected_low_solidity")
            add("unboxed_text_candidate")
        if "edge_reject_low_anchor_coverage" in flag_text:
            add("edge_rejected_low_anchor_coverage")
            add("image_gen_review_candidate")
        if "edge_reject_short_virtual_guard_sfx_like" in flag_text:
            add("edge_rejected_sfx_like")
            add("handwrite_or_sfx_candidate")
        if "edge_reject_short_handwritten_group_unboxed_like" in flag_text:
            add("handwrite_or_sfx_candidate")
        if "edge_reject_simple_mid_round_unboxed_like" in flag_text:
            add("edge_rejected_simple_mid_round")
            raw_solid = _extract_quality_float(flags, "raw_solid")
            raw_round = _extract_quality_float(flags, "raw_round")
            if raw_solid is not None and raw_round is not None and raw_solid >= 0.90 and raw_round >= 0.16:
                add("image_gen_review_candidate")
            else:
                add("unboxed_text_candidate")
        if "unboxed_like" in flag_text:
            add("edge_rejected_unboxed_like")
            add("unboxed_text_candidate")
        if "narration_like" in flag_text:
            add("edge_rejected_narration_like")
            add("unboxed_text_candidate")
        if "black_contour=closed" in flag_text:
            add("contour_probe_closed_candidate")
        elif "black_contour=partial" in flag_text:
            add("contour_probe_partial_candidate")

        return tags

    def _pad_rect_to_image(rect: List[int], pad: int) -> List[int]:
        x, y, w, h = [int(v) for v in rect[:4]]
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(img_w, x + w + pad)
        y2 = min(img_h, y + h + pad)
        return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]

    for idx, cluster in enumerate(clusters, start=1):
        rect = _tight_text_group_rect(cluster)
        fallback_rect = _fallback_text_group_rect(cluster)
        if len(rect) < 4:
            continue

        cluster_id = _text_group_id(cluster, idx)

        is_symbol_only = _is_symbol_only_text(_cluster_text(cluster))
        is_short_non_jp_noise = _is_short_non_japanese_noise_text(_cluster_text(cluster))
        is_english_only = _is_english_only_text(_cluster_text(cluster))
        is_large_single_glyph_sfx = _is_large_single_glyph_sfx_like(cluster, rect)
        is_large_short_kana_sfx = _is_large_short_kana_sfx_like(cluster, rect)

        is_dark_bg = _is_dark_bg_text(gray, rect, dark_bg_mean_threshold)
        is_horizontal_tg = _is_horizontal_text_group(cluster, rect)

        # 이미지 하단 근처(하위 8%) 가로 텍스트: 챕터 엔드 표기·페이지 캡션 등
        # 말풍선이 아닌 인쇄 레이아웃 텍스트이므로 bubble 감지 대상에서 제외
        # orientation 필드가 'vertical'이어도 AR이 명확히 가로형이면 landscape로 판단
        _rx, _ry, _rw, _rh = rect
        _is_landscape_by_ar = _rh > 0 and (_rw / _rh) >= 1.15
        if (is_horizontal_tg or _is_landscape_by_ar) and (_ry + _rh) > img_h * 0.92:
            diagnostics.append({"cluster_id": cluster_id, "method": "bottom_landscape_footer", "is_valid": False})
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "bottom_landscape_footer",
                "method": "noise_like",
                "region_type": "noise_like",
                "quality_flags": ["bottom_landscape_footer"],
            })
            continue

        _tiny_landscape_noise = (
            _is_landscape_by_ar
            and _rh <= max(14, int(img_h * 0.014))
            and (_rw * _rh) <= max(700, int(img_w * img_h * 0.00075))
        )
        if _tiny_landscape_noise:
            diagnostics.append({"cluster_id": cluster_id, "method": "tiny_landscape_text_noise", "is_valid": False})
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "tiny_landscape_text_noise",
                "method": "noise_like",
                "region_type": "noise_like",
                "quality_flags": ["tiny_landscape_text_noise"],
            })
            continue

        # 이미지 좌/우 끝에 바싹 붙은 매우 좁은 세로 텍스트: 면책 문구·저작권 표기 등 인쇄 메타데이터
        _narrow_meta = _is_edge_narrow_metadata_rect(rect, img_w)
        if _narrow_meta:
            diagnostics.append({"cluster_id": cluster_id, "method": "edge_narrow_metadata", "is_valid": False})
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "edge_narrow_metadata",
                "method": "metadata",
                "region_type": "metadata",
                "quality_flags": ["edge_narrow_metadata"],
            })
            continue

        if is_dark_bg and is_horizontal_tg:
            diagnostics.append({"cluster_id": cluster_id, "method": "dark_bg_text", "is_valid": False})
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "dark_bg_text",
                "method": "dark_bg_text",
                "region_type": "dark_bg_text",
                "quality_flags": ["dark_background", "horizontal_dark_bg_skip"],
            })
            continue
        if is_dark_bg:
            diagnostics.append({
                "cluster_id": cluster_id,
                "method": "dark_bg_vertical_allowed",
                "is_valid": True,
                "quality_flags": ["dark_background", "dark_flood_candidate", "vertical_dark_bg_allowed"],
            })

        if is_large_single_glyph_sfx:
            diagnostics.append({"cluster_id": cluster_id, "method": "large_single_glyph_sfx_like", "is_valid": False})
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "large_single_glyph_sfx_like",
                "method": "noise_like",
                "region_type": "noise_like",
            })
            noise_like_anchors.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "large_single_glyph_sfx_like",
            })
            continue

        if is_large_short_kana_sfx:
            diagnostics.append({"cluster_id": cluster_id, "method": "large_short_kana_sfx_like", "is_valid": False})
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "large_short_kana_sfx_like",
                "method": "noise_like",
                "region_type": "noise_like",
            })
            noise_like_anchors.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": "large_short_kana_sfx_like",
            })
            continue

        if is_symbol_only or is_short_non_jp_noise or is_english_only:
            reason = "symbol_only_text" if is_symbol_only else "short_non_japanese_noise_text" if is_short_non_jp_noise else "english_only_text"
            diagnostics.append({"cluster_id": cluster_id, "method": reason, "is_valid": False})
            if is_symbol_only:
                noise_like_anchors.append({
                    "cluster_id": cluster_id,
                    "anchor_bbox": rect,
                    "fallback_bbox": fallback_rect,
                    "reason": reason,
                })
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "reason": reason,
                "method": "noise_like",
                "region_type": "noise_like",
            })
            continue

        x, y, w, h = _unpack_rect(rect)
        seed: Optional[Point] = None
        mask = np.zeros((img_h, img_w), dtype=np.uint8)
        area = 0
        candidate_rect = [0, 0, 0, 0]

        is_valid = False
        method = "bbox_fallback"
        region_type = "bbox_fallback"
        bubble_mask = np.zeros((img_h, img_w), dtype=np.uint8)
        bubble_rect = list(_unpack_rect(fallback_rect))
        bubble_page_ratio = 0.0
        bubble_tg_ratio = 0.0
        touches_border = False
        quality_flags = (
            ["dark_background", "dark_flood_candidate", "vertical_dark_bg_allowed"]
            if is_dark_bg and not is_horizontal_tg
            else []
        )
        candidate_mask = np.zeros((img_h, img_w), dtype=np.uint8)
        is_bimodal = False
        is_solid = False
        is_shape_ok = False

        if is_dark_bg and not is_horizontal_tg:
            seed, mask, area, candidate_rect = _dark_flood_with_fallback(image, gray, rect)
            if seed is not None:
                bubble_page_ratio = area / image_area
                touches_border = _touches_image_border(mask)
                fx, fy, fw, fh = _unpack_rect(candidate_rect)
                bubble_rect = [fx, fy, fw, fh]
                anchor_area = max(1, w * h)
                bubble_tg_ratio = (fw * fh) / anchor_area
                center = _bbox_center(rect)
                center_inside = (fx <= center[0] <= fx + fw and fy <= center[1] <= fy + fh)
                solidity, roundness = _mask_solidity_roundness(mask)
                dark_shape_ok = solidity >= 0.88 and roundness >= 0.20
                dark_max_tg_ratio = min(max_bubble_tg_ratio, 8.0)
                dark_size_ok = (
                    bubble_page_ratio <= min(max_area_ratio, 0.07)
                    and 1.2 <= bubble_tg_ratio <= dark_max_tg_ratio
                    and center_inside
                    and not touches_border
                )
                quality_flags.append(
                    f"dark_flood solid={round(solidity,3)}, round={round(roundness,3)}, "
                    f"tg={round(bubble_tg_ratio,2)}, page={round(bubble_page_ratio,4)}, "
                    f"border={'Y' if touches_border else 'N'}"
                )
                if dark_shape_ok and dark_size_ok:
                    is_valid = True
                    candidate_mask = _fill_mask_holes(mask)
                    bubble_mask = candidate_mask
                    method = "dark_flood"
                    region_type = "bubble"
                    is_shape_ok = True
                    is_bimodal = True
                    is_solid = True
                    quality_flags.extend(["dark_flood_success", "skip_edge_barrier_for_dark_bg"])
                else:
                    quality_flags.append("dark_flood_failed_validation")

        # Step 1: Primary Probe
        if not is_valid:
            seed, mask, area, candidate_rect = _flood_with_fallback(image, gray, rect)

        if not is_valid and seed is not None:
            # Step 2: Strict Validation
            bubble_page_ratio = area / image_area
            touches_border = _touches_image_border(mask)
            fx, fy, fw, fh = _unpack_rect(candidate_rect)
            bubble_rect = [fx, fy, fw, fh]
            anchor_area = max(1, w * h)
            bubble_tg_ratio = (fw * fh) / anchor_area
            center = _bbox_center(rect)
            center_inside = (fx <= center[0] <= fx + fw and fy <= center[1] <= fy + fh)
            
            valid_area = (bubble_page_ratio <= max_area_ratio)
            valid_ratio = (bubble_tg_ratio >= min_bubble_tg_ratio)

            if valid_area and valid_ratio and center_inside:
                filled_mask = _fill_mask_holes(mask)
                candidate_mask = _erode_mask(filled_mask, pixels=erode_pixels)
                
                bimodality = _compute_flood_bimodality(filled_mask, gray)
                is_bimodal = (bimodality >= 0.88)
                
                # Solidity Check
                # _is_bubble_shaped internally checks solidity >= min_solidity
                # raw mask check
                raw_binary = np.where(mask > 0, 255, 0).astype(np.uint8)
                raw_contours, _ = cv2.findContours(raw_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                raw_solid = 0.0
                raw_round = 0.0
                if raw_contours:
                    rcnt = max(raw_contours, key=cv2.contourArea)
                    rarea = cv2.contourArea(rcnt)
                    rhull = cv2.contourArea(cv2.convexHull(rcnt))
                    if rhull > 0: raw_solid = rarea / rhull
                    rperi = cv2.arcLength(rcnt, True)
                    if rperi > 0: raw_round = 4 * np.pi * rarea / (rperi ** 2)
                    
                binary = np.where(candidate_mask > 0, 255, 0).astype(np.uint8)
                contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                solidity = 0.0
                roundness = 0.0
                if contours:
                    cnt = max(contours, key=cv2.contourArea)
                    area_cnt = cv2.contourArea(cnt)
                    hull_area = cv2.contourArea(cv2.convexHull(cnt))
                    if hull_area > 0:
                        solidity = area_cnt / hull_area
                    
                    peri = cv2.arcLength(cnt, True)
                    if peri > 0:
                        roundness = 4 * np.pi * area_cnt / (peri ** 2)
                
                _SOLID_THR = 0.82
                # aspect ratio 기반 roundness 정규화
                # 이상적 직사각형 roundness = π*ar/(ar+1)²로 정규화하면
                # 직사각형≈1.0, 타원>1.0으로 두 타입 점수가 비슷해짐


                _bw = int(candidate_rect[2]) if len(candidate_rect) >= 3 else 1
                _bh = int(candidate_rect[3]) if len(candidate_rect) >= 4 else 1
                _ar = max(_bw, _bh) / max(min(_bw, _bh), 1)
                _rect_round_ideal = math.pi * _ar / (_ar + 1) ** 2
                _rect_round_ideal = max(_rect_round_ideal, 0.10)
                # raw_round < rect_ideal 구간은 sqrt로 완화:
                # 직사각형 오버랩처럼 이상값 아래로 떨어지는 케이스를 선형보다 덜 페널티.
                # raw_round >= rect_ideal (타원/원)은 그대로 linear.
                _rr_ratio = raw_round / _rect_round_ideal
                type_round = math.sqrt(_rr_ratio) if _rr_ratio < 1.0 else _rr_ratio

                shape_score = (solidity / _SOLID_THR) * type_round

                # Gate 1: 모양 점수
                _SHAPE_THR = 0.58
                is_shape_gate = shape_score >= _SHAPE_THR

                # Gate 2: 크기 페널티 (flood 탈출 차단)
                # tg_ratio는 anchor bbox 크기에 따라 왜곡되므로 제외.
                _BASE_PAGE   = 0.07
                _BORDER_MULT = 0.80
                page_penalty   = _BASE_PAGE / max(bubble_page_ratio, _BASE_PAGE)
                border_penalty = _BORDER_MULT if touches_border else 1.0
                adjusted_score = shape_score * page_penalty * border_penalty
                is_size_gate = adjusted_score >= 0.50

                # border_rescue: page_penalty가 이미 적용 중인(page > BASE) 큰 region에서
                # border_penalty까지 중첩돼 탈락하는 경우만 구제.
                adj_no_border = shape_score * page_penalty
                is_border_rescue = (
                    is_shape_gate
                    and touches_border
                    and not is_size_gate
                    and bubble_page_ratio > _BASE_PAGE
                    and adj_no_border >= 0.50
                )

                is_shape_ok = is_shape_gate and (is_size_gate or is_border_rescue)
                boundary_sides, boundary_ratios = _boundary_evidence(gray, list(candidate_rect[:4]))
                has_boundary_evidence = boundary_sides >= 3
                center_offset = _text_center_offset_ratio(list(candidate_rect[:4]), _tight_text_group_rect(cluster))
                rect_like = raw_solid >= 0.85 and 0.38 <= raw_round <= 0.62 and shape_score >= 0.78
                ellipse_like = raw_solid >= 0.93 and raw_round >= 0.63 and shape_score >= 1.00
                dual_rect_profile = _dual_rect_profile_signal(filled_mask)
                closed_contour_probe = _black_contour_probe(
                    gray,
                    list(candidate_rect[:4]),
                    filled_mask,
                    black=_page_black_contour(),
                )
                closed_boundary_like = (
                    closed_contour_probe.get("status") == "closed"
                    and float(closed_contour_probe.get("coverage", 0.0)) >= 0.90
                    and int(closed_contour_probe.get("quadrants", 0)) == 4
                    and float(closed_contour_probe.get("max_gap", 1.0)) <= 0.12
                    and float(closed_contour_probe.get("distance_cv", 1.0)) <= 0.20
                    and boundary_sides == 4
                    and shape_score >= 0.58
                )
                dual_rect_like, dual_rect_score, dual_rect_parts = _dual_rect_validation_score(
                    raw_solid,
                    raw_round,
                    shape_score,
                    boundary_sides,
                    closed_contour_probe,
                    dual_rect_profile,
                    bubble_page_ratio,
                    touches_border,
                )
                distance_peaks = _distance_peak_signal(filled_mask)
                dual_ellipse_tail_like = (
                    raw_solid >= 0.78
                    and 0.32 <= raw_round <= 0.58
                    and shape_score >= 0.68
                    and distance_peaks["ok"] >= 1.0
                )
                direct_shape_class = (
                    "rect"
                    if rect_like
                    else (
                        "ellipse"
                        if ellipse_like
                        else (
                        "dual_rect"
                        if dual_rect_like
                            else (
                                "closed_boundary"
                                if closed_boundary_like
                                else ("dual_ellipse_tail" if dual_ellipse_tail_like else "none")
                            )
                        )
                    )
                )
                direct_shape_ok = has_boundary_evidence and direct_shape_class != "none"

                # 로그 추가
                quality_flags.append(
                    f"raw_solid={round(raw_solid,3)}, raw_round={round(raw_round,3)}, "
                    f"solid={round(solidity,3)}, round={round(roundness,3)}, "
                    f"shape={round(shape_score,3)}, adj={round(adjusted_score,3)}, "
                    f"tg={round(bubble_tg_ratio,2)}, page={round(bubble_page_ratio,4)}, "
                    f"border={'Y' if touches_border else 'N'}"
                )
                quality_flags.append(
                    "boundary="
                    f"sides={boundary_sides},"
                    f"t={boundary_ratios['top']:.2f},b={boundary_ratios['bottom']:.2f},"
                    f"l={boundary_ratios['left']:.2f},r={boundary_ratios['right']:.2f}"
                )
                quality_flags.append(f"shape_class={direct_shape_class}")
                if dual_rect_like:
                    quality_flags.append(
                        "dual_rect_profile="
                        f"axis={dual_rect_profile['axis']},"
                        f"score={dual_rect_profile['score']:.2f},"
                        f"shift={dual_rect_profile['center_shift']:.2f},"
                        f"neck={dual_rect_profile['neck_ratio']:.2f},"
                        f"floor={dual_rect_profile['width_floor']:.2f}"
                    )
                    quality_flags.append(
                        "dual_rect_score="
                        f"{dual_rect_score:.2f},"
                        f"p={dual_rect_parts['profile']:.2f},"
                        f"c={dual_rect_parts['contour']:.2f},"
                        f"b={dual_rect_parts['boundary']:.2f},"
                        f"s={dual_rect_parts['solid']:.2f},"
                        f"r={dual_rect_parts['round']:.2f},"
                        f"sh={dual_rect_parts['shape']:.2f},"
                        f"pen={dual_rect_parts['penalty']:.2f}"
                    )
                    quality_flags.append(
                        "dual_rect_contour="
                        f"{closed_contour_probe['status']},"
                        f"cov={closed_contour_probe['coverage']:.2f},"
                        f"quad={closed_contour_probe['quadrants']},"
                        f"gap={closed_contour_probe['max_gap']:.2f},"
                        f"cv={closed_contour_probe['distance_cv']:.2f}"
                    )
                if closed_boundary_like:
                    quality_flags.append(
                        "closed_boundary="
                        f"{closed_contour_probe['status']},"
                        f"cov={closed_contour_probe['coverage']:.2f},"
                        f"quad={closed_contour_probe['quadrants']},"
                        f"gap={closed_contour_probe['max_gap']:.2f},"
                        f"cv={closed_contour_probe['distance_cv']:.2f}"
                    )
                if dual_ellipse_tail_like:
                    quality_flags.append(
                        "dual_ellipse_tail="
                        f"peaks={int(distance_peaks['count'])},"
                        f"score={distance_peaks.get('score', 0.0):.2f},"
                        f"sep={distance_peaks.get('sep_ratio', 0.0):.2f},"
                        f"balance={distance_peaks.get('balance', 0.0):.2f}"
                    )
                quality_flags.append(f"center_offset={center_offset:.2f}")

                if direct_shape_ok:
                    is_valid = True
                    bubble_mask = candidate_mask
                    bubble_rect = [int(v) for v in candidate_rect[:4]]
                    method = "flood"

                    # Determine basic features for region_type
                    _ft, _ff, _ = _classify_anchor_region(
                        cluster, list(bubble_rect), int(np.count_nonzero(bubble_mask)),
                        touches_border, True
                    )
                    quality_flags.extend(_ff)

                    # valid flood는 후단에서 모두 bubble crop으로 처리한다.
                    # bubble 검증 실패 후보만 unboxed inpaint 대상으로 남긴다.
                    region_type = "bubble"
                else:
                    quality_flags.append("failed_validation")
                    if not has_boundary_evidence:
                        quality_flags.append("weak_boundary_evidence")
            else:
                quality_flags.append(f"failed_bounds: area={valid_area}, ratio={valid_ratio}(tg_ratio={round(bubble_tg_ratio,2)}), center={center_inside}")

            # Step 3: Rescue Probe
            if not is_valid:
                # To be implemented in next plan. For now, remains failed candidate.
                method = "failed_candidate"

        diagnostics.append({
            "cluster_id": cluster_id,
            "method": method,
            "is_valid": is_valid,
            "region_type": region_type,
            "bubble_page_ratio": round(float(bubble_page_ratio), 5),
            "bubble_tg_ratio": round(float(bubble_tg_ratio), 2),
            "touches_border": touches_border,
            # 이후 grouped region에 붙는 flag가 진단 기록으로 새지 않도록 복사본을 둔다.
            "quality_flags": list(quality_flags)
        })

        fail_tag = "failed"
        if not is_valid:
            qf_str = " ".join(quality_flags)
            if "tiny_text_large_ratio" in qf_str:
                fail_tag = "Tiny"
            elif "solid=" in qf_str:
                if not is_bimodal:
                    fail_tag = "Bimodal"
                elif not is_shape_ok:
                    fail_tag = "Shape"
                else:
                    fail_tag = "Val"
            elif "failed_bounds" in qf_str:
                if "area=False" in qf_str: fail_tag = "Area"
                elif "ratio=False" in qf_str: fail_tag = "Ratio"
                elif "center=False" in qf_str: fail_tag = "Center"
                else: fail_tag = "Bnds"
                
            skipped.append({
                "cluster_id": cluster_id,
                "anchor_bbox": rect,
                "fallback_bbox": fallback_rect,
                "bubble_rect": bubble_rect,
                "reason": "failed_candidate",
                "method": method,
                "region_type": region_type,
                "quality_flags": list(quality_flags)
            })
            # fallback to bbox crop for OCR
            rect_key = [int(v) for v in bubble_rect[:4]]
            key = ("fallback", *rect_key)
            if key not in grouped:
                grouped[key] = {
                    "anchor_ids": [cluster_id],
                    "seed_points": [],
                    "bubble_rect": rect_key,
                    "mask": candidate_mask,
                    "bubble_page_ratio": 0.0,
                    "bubble_tg_ratio": 0.0,
                    "touches_image_border": False,
                    "method": "bbox_fallback",
                    "region_type": "bbox_fallback",
                    "quality_flags": quality_flags,
                    "is_valid": False,
                    "fail_tag": fail_tag
                }
            else:
                grouped[key]["anchor_ids"].append(cluster_id)
            continue

        # Group valid bubbles
        rect_key = [int(v) for v in bubble_rect[:4]]
        key = ("valid", *rect_key)
        if key not in grouped:
            grouped[key] = {
                "anchor_ids": [cluster_id],
                "seed_points": [list(seed)],
                "bubble_rect": rect_key,
                "mask": bubble_mask,
                "bubble_page_ratio": bubble_page_ratio,
                "bubble_tg_ratio": bubble_tg_ratio,
                "touches_image_border": touches_border,
                "method": method,
                "region_type": region_type,
                "quality_flags": quality_flags,
                "is_valid": is_valid
            }
        else:
            grouped[key]["mask"] = cv2.bitwise_or(grouped[key]["mask"], bubble_mask)
            grouped[key]["anchor_ids"].append(cluster_id)
            grouped[key]["seed_points"].append(list(seed))
            grouped[key]["is_valid"] = True
            grouped[key]["method"] = method
            grouped[key]["region_type"] = region_type

    # 같은 bubble rect를 공유하는 fallback TG는 valid bubble group에 합류시킨다.
    # 그래야 한 말풍선 안의 여러 TG가 split 단계에서 함께 고려된다.
    valid_by_rect = {
        tuple(region["bubble_rect"]): key
        for key, region in grouped.items()
        if region.get("is_valid")
    }
    keys_to_remove: List[Tuple[Any, ...]] = []
    for key, region in list(grouped.items()):
        if region.get("is_valid"):
            continue
        valid_key = valid_by_rect.get(tuple(region["bubble_rect"]))
        if valid_key is None:
            continue
        valid_region = grouped[valid_key]
        for aid in region["anchor_ids"]:
            if aid not in valid_region["anchor_ids"]:
                valid_region["anchor_ids"].append(aid)
        valid_region["quality_flags"].append("merged_fallback_anchor_for_split")
        keys_to_remove.append(key)
    for key in keys_to_remove:
        grouped.pop(key, None)

    # 영어-only 텍스트는 독립 bubble 후보에서는 제외하지만, 이미 잡힌 bubble 안에 있으면
    # split anchor로 필요하다. 예: 일본어 설명 옆 영문 타이틀이 같은 bubble crop 안에 있는 경우.
    assigned_anchor_ids = {
        int(aid)
        for region in grouped.values()
        for aid in region.get("anchor_ids", [])
    }
    for skipped_item in skipped:
        if skipped_item.get("reason") != "english_only_text":
            continue
        aid = int(skipped_item.get("cluster_id", 0))
        if not aid or aid in assigned_anchor_ids:
            continue
        anchor_rect = [int(v) for v in skipped_item.get("anchor_bbox", [0, 0, 0, 0])[:4]]
        ax, ay, aw, ah = anchor_rect
        if aw <= 0 or ah <= 0:
            continue
        acx, acy = _bbox_center(anchor_rect)
        anchor_area = aw * ah
        best_region: Optional[Dict[str, Any]] = None
        best_overlap = 0.0
        for region in grouped.values():
            if not region.get("is_valid"):
                continue
            rx, ry, rw, rh = [int(v) for v in region.get("bubble_rect", [0, 0, 0, 0])[:4]]
            if not (rx <= acx <= rx + rw and ry <= acy <= ry + rh):
                continue
            ix1 = max(ax, rx)
            iy1 = max(ay, ry)
            ix2 = min(ax + aw, rx + rw)
            iy2 = min(ay + ah, ry + rh)
            overlap = max(0, ix2 - ix1) * max(0, iy2 - iy1) / max(1, anchor_area)
            if overlap > best_overlap:
                best_overlap = overlap
                best_region = region
        if best_region is not None and best_overlap >= 0.60:
            best_region["anchor_ids"].append(aid)
            best_region.setdefault("quality_flags", []).append(f"merged_english_anchor_for_split:A{aid}")
            assigned_anchor_ids.add(aid)

    clusters_by_id = {_text_group_id(c, i + 1): c for i, c in enumerate(clusters)}

    if enable_edge_barrier_rescue:
        try:
            try:
                from pipeline.edge_barrier_flood_rescue import run_edge_barrier_rescue_auto
            except ImportError:
                from edge_barrier_flood_rescue import run_edge_barrier_rescue_auto
        except Exception as exc:
            diagnostics.append({
                "method": "edge_barrier_rescue_import_failed",
                "is_valid": False,
                "reason": str(exc),
            })
            run_edge_barrier_rescue_auto = None

        if run_edge_barrier_rescue_auto is not None:
            def _edge_subset_source_rect(anchor_subset: List[int]) -> List[int]:
                subset_groups = [clusters_by_id[aid] for aid in anchor_subset]
                source_rect = _union_rect([
                    _fallback_text_group_rect(group)
                    for group in subset_groups
                ])
                pad = max(40, min(90, int(max(source_rect[2], source_rect[3]) * 0.30)))
                return _pad_rect_to_image(source_rect, pad)

            def _build_edge_rescued_region(
                base_region: Dict[str, Any],
                anchor_subset: List[int],
                rescue: Dict[str, Any],
                marker_flag: str,
            ) -> Optional[Dict[str, Any]]:
                score = rescue.get("score", {})
                rescue_rect = [int(v) for v in rescue.get("rescue_rect", [0, 0, 0, 0])[:4]]
                if rescue_rect[2] <= 0 or rescue_rect[3] <= 0:
                    return None

                rescue_mask = rescue["mask"]
                quality_flags = list(base_region.get("quality_flags", [])) + [
                    "edge_barrier_rescue",
                    marker_flag,
                    f"edge_best={rescue.get('best_label')}",
                    f"edge_guard={rescue.get('guard_mode')}_{rescue.get('guard_margin')}",
                    f"edge_score={round(float(score.get('rank_score', 0.0)), 3)}",
                    f"edge_ratio={round(float(score.get('rect_anchor_ratio', 0.0)), 2)}",
                    f"edge_page={round(float(score.get('mask_page_ratio', 0.0)), 4)}",
                ]
                if len(anchor_subset) >= 2:
                    quality_flags.append("edge_group_after_split")

                return {
                    **base_region,
                    "anchor_ids": list(anchor_subset),
                    "seed_points": [rescue["seed"]] if rescue.get("seed") else [],
                    "bubble_rect": rescue_rect,
                    "mask": rescue_mask,
                    "bubble_page_ratio": float(score.get("mask_page_ratio", 0.0)),
                    "bubble_tg_ratio": float(score.get("rect_anchor_ratio", 0.0)),
                    "touches_image_border": _touches_image_border(rescue_mask),
                    "method": "edge_barrier_rescue",
                    "region_type": "bubble",
                    "quality_flags": quality_flags,
                    "is_valid": True,
                }

            for region in grouped.values():
                if region.get("is_valid"):
                    continue
                if region.get("region_type") != "bbox_fallback":
                    continue
                _qf_str = " ".join(str(flag) for flag in region.get("quality_flags", []))
                if "dark_flood_candidate" in _qf_str and "dark_flood_failed_validation" not in _qf_str:
                    # dark_flood 가 성공했거나 아직 시도하지 않은 dark 후보는 edge_barrier 스킵
                    # dark_flood 가 명시적으로 실패한 경우에는 edge_barrier 허용
                    region["quality_flags"].append("edge_barrier_skipped_dark_flood_candidate")
                    continue

                anchor_ids = [aid for aid in region.get("anchor_ids", []) if aid in clusters_by_id]
                if not anchor_ids:
                    continue
                target_groups = [clusters_by_id[aid] for aid in anchor_ids]
                rescue = run_edge_barrier_rescue_auto(
                    image,
                    target_groups,
                    region["bubble_rect"],
                    virtual_walls=manual_virtual_walls or None,
                )
                score = rescue.get("score", {})
                reject_reason = None if rescue.get("manual_guided") else (
                    _edge_rescue_reject_reason(region, rescue, target_groups)
                    if rescue.get("success") else None
                )
                if not rescue.get("success"):
                    region["quality_flags"].append(
                        "edge_barrier_rescue_failed="
                        f"{rescue.get('reason')}, score={round(float(score.get('rank_score', -99.0)), 3)}"
                    )
                    continue
                if reject_reason is not None:
                    region["quality_flags"].append(reject_reason)
                    continue

                rescue_rect = [int(v) for v in rescue.get("rescue_rect", [0, 0, 0, 0])[:4]]
                if rescue_rect[2] <= 0 or rescue_rect[3] <= 0:
                    continue

                rescue_mask = rescue["mask"]
                region["is_valid"] = True
                region["method"] = "edge_barrier_rescue"
                region["region_type"] = "bubble"
                region["bubble_rect"] = rescue_rect
                region["mask"] = rescue_mask
                region["seed_points"] = [rescue["seed"]] if rescue.get("seed") else []
                region["bubble_page_ratio"] = float(score.get("mask_page_ratio", 0.0))
                region["bubble_tg_ratio"] = float(score.get("rect_anchor_ratio", 0.0))
                region["touches_image_border"] = _touches_image_border(rescue_mask)
                region["quality_flags"].extend([
                    "edge_barrier_rescue",
                    f"edge_best={rescue.get('best_label')}",
                    f"edge_guard={rescue.get('guard_mode')}_{rescue.get('guard_margin')}",
                    "edge_manual_guided" if rescue.get("manual_guided") else "edge_auto_guided",
                    f"edge_score={round(float(score.get('rank_score', 0.0)), 3)}",
                    f"edge_ratio={round(float(score.get('rect_anchor_ratio', 0.0)), 2)}",
                    f"edge_page={round(float(score.get('mask_page_ratio', 0.0)), 4)}",
                ])
                diagnostics.append({
                    "cluster_id": anchor_ids,
                    "method": "edge_barrier_rescue",
                    "is_valid": True,
                    "region_type": "bubble",
                    "bubble_page_ratio": round(float(score.get("mask_page_ratio", 0.0)), 5),
                    "bubble_tg_ratio": round(float(score.get("rect_anchor_ratio", 0.0)), 2),
                    "quality_flags": region["quality_flags"],
                })

            split_rescued: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
            split_remove_keys: List[Tuple[Any, ...]] = []
            for key, region in list(grouped.items()):
                if region.get("is_valid"):
                    continue
                anchor_ids = [aid for aid in region.get("anchor_ids", []) if aid in clusters_by_id]
                if len(anchor_ids) < 2:
                    continue

                if len(anchor_ids) >= 3:
                    best_partition: Optional[Tuple[float, List[Dict[str, Any]]]] = None
                    for single_aid in anchor_ids:
                        single_ids = [single_aid]
                        rest_ids = [aid for aid in anchor_ids if aid != single_aid]
                        partition_regions: List[Dict[str, Any]] = []
                        total_score = 0.0
                        partition_failed = False

                        for subset_ids, marker in (
                            (single_ids, "edge_partition_single"),
                            (rest_ids, "edge_partition_group"),
                        ):
                            subset_groups = [clusters_by_id[aid] for aid in subset_ids]
                            subset_source_rect = _edge_subset_source_rect(subset_ids)
                            subset_rescue = run_edge_barrier_rescue_auto(
                                image,
                                subset_groups,
                                subset_source_rect,
                                virtual_walls=manual_virtual_walls or None,
                            )
                            subset_score = subset_rescue.get("score", {})
                            subset_reject = None if subset_rescue.get("manual_guided") else (
                                _edge_rescue_reject_reason(region, subset_rescue, subset_groups)
                                if subset_rescue.get("success") else None
                            )
                            if not subset_rescue.get("success") or subset_reject is not None:
                                partition_failed = True
                                break
                            rescued_region = _build_edge_rescued_region(
                                region,
                                subset_ids,
                                subset_rescue,
                                marker,
                            )
                            if rescued_region is None:
                                partition_failed = True
                                break
                            partition_regions.append(rescued_region)
                            total_score += float(subset_score.get("rank_score", -99.0))

                        if partition_failed or len(partition_regions) != 2:
                            continue
                        if best_partition is None or total_score > best_partition[0]:
                            best_partition = (total_score, partition_regions)

                    if best_partition is not None:
                        for rescued_region in best_partition[1]:
                            rect = rescued_region["bubble_rect"]
                            ids = rescued_region["anchor_ids"]
                            split_rescued[("edge_partition", *ids, *rect)] = rescued_region
                        split_remove_keys.append(key)
                        continue

                remaining_anchor_ids: List[int] = []
                rejected_reasons_by_anchor: Dict[int, str] = {}
                single_rescued_keys: List[Tuple[Tuple[Any, ...], int]] = []
                current_rescued = 0
                _combined_has_border = any(
                    str(flag) == "border=Y" or str(flag).endswith("border=Y")
                    for flag in region.get("quality_flags", [])
                )
                _img_h, _img_w = image.shape[:2]
                _border_margin = 200  # px: 앵커가 이 거리 안쪽 엣지에 있어야 border 차단 적용
                for aid in anchor_ids:
                    # combined region 의 border=Y 는 merged 영역 기준 — 개별 앵커가 실제로
                    # 엣지에 가까운지 별도 확인해서 멀리 떨어진 앵커는 구제 시도 허용
                    if _combined_has_border:
                        _ar = _tight_text_group_rect(clusters_by_id[aid])
                        _ax, _ay, _aw, _ah = _ar
                        _anchor_near_border = (
                            _ax < _border_margin
                            or _ay < _border_margin
                            or (_ax + _aw) > (_img_w - _border_margin)
                            or (_ay + _ah) > (_img_h - _border_margin)
                        )
                        if _anchor_near_border:
                            reject_reason = "edge_reject_split_border_candidate"
                            region["quality_flags"].append(f"{reject_reason}:A{aid}")
                            rejected_reasons_by_anchor[aid] = reject_reason
                            remaining_anchor_ids.append(aid)
                            continue
                    rescue = run_edge_barrier_rescue_auto(
                        image,
                        [clusters_by_id[aid]],
                        region["bubble_rect"],
                        virtual_walls=manual_virtual_walls or None,
                    )
                    score = rescue.get("score", {})
                    reject_reason = None if rescue.get("manual_guided") else (
                        _edge_rescue_reject_reason(region, rescue, [clusters_by_id[aid]])
                        if rescue.get("success") else None
                    )
                    if not rescue.get("success"):
                        remaining_anchor_ids.append(aid)
                        continue
                    if reject_reason is not None:
                        region["quality_flags"].append(f"{reject_reason}:A{aid}")
                        rejected_reasons_by_anchor[aid] = reject_reason
                        remaining_anchor_ids.append(aid)
                        continue
                    rescue_rect = [int(v) for v in rescue.get("rescue_rect", [0, 0, 0, 0])[:4]]
                    if rescue_rect[2] <= 0 or rescue_rect[3] <= 0:
                        remaining_anchor_ids.append(aid)
                        continue

                    rescue_mask = rescue["mask"]
                    new_region = {
                        **region,
                        "anchor_ids": [aid],
                        "seed_points": [rescue["seed"]] if rescue.get("seed") else [],
                        "bubble_rect": rescue_rect,
                        "mask": rescue_mask,
                        "bubble_page_ratio": float(score.get("mask_page_ratio", 0.0)),
                        "bubble_tg_ratio": float(score.get("rect_anchor_ratio", 0.0)),
                        "touches_image_border": _touches_image_border(rescue_mask),
                        "method": "edge_barrier_rescue",
                        "region_type": "bubble",
                        "quality_flags": list(region.get("quality_flags", [])) + [
                            "edge_barrier_rescue",
                            "edge_split_from_failed_group",
                            f"edge_best={rescue.get('best_label')}",
                            f"edge_guard={rescue.get('guard_mode')}_{rescue.get('guard_margin')}",
                            f"edge_score={round(float(score.get('rank_score', 0.0)), 3)}",
                            f"edge_ratio={round(float(score.get('rect_anchor_ratio', 0.0)), 2)}",
                            f"edge_page={round(float(score.get('mask_page_ratio', 0.0)), 4)}",
                        ],
                        "is_valid": True,
                    }
                    single_key = ("edge_single", aid, *rescue_rect)
                    split_rescued[single_key] = new_region
                    single_rescued_keys.append((single_key, aid))
                    current_rescued += 1

                if _should_demote_lone_edge_split_rescue(
                    [aid for _key, aid in single_rescued_keys],
                    rejected_reasons_by_anchor,
                ):
                    demoted_ids = {aid for _key, aid in single_rescued_keys}
                    for single_key, aid in single_rescued_keys:
                        split_rescued.pop(single_key, None)
                        region["quality_flags"].append(
                            f"edge_demote_lone_split_rescue_sibling_unboxed_like:A{aid}"
                        )

                    split_remove_keys.append(key)
                    for aid in anchor_ids:
                        fallback_rect = _fallback_text_group_rect(clusters_by_id[aid])
                        child_region = {
                            **region,
                            "anchor_ids": [aid],
                            "bubble_rect": fallback_rect,
                            "mask": np.zeros((img_h, img_w), dtype=np.uint8),
                            "seed_points": [],
                            "bubble_page_ratio": 0.0,
                            "bubble_tg_ratio": 1.0,
                            "touches_image_border": False,
                            "method": "bbox_fallback",
                            "region_type": "bbox_fallback",
                            "quality_flags": list(region.get("quality_flags", [])) + [
                                f"edge_split_consensus_unboxed:A{aid}",
                            ],
                            "is_valid": False,
                        }
                        if aid in demoted_ids and aid not in rejected_reasons_by_anchor:
                            child_region["quality_flags"].append("edge_demoted_lone_rescue")
                        split_rescued[("edge_consensus_unboxed", aid, *fallback_rect)] = child_region
                    continue

                if current_rescued > 0:
                    if remaining_anchor_ids:
                        if len(remaining_anchor_ids) >= 2:
                            remaining_groups = [clusters_by_id[aid] for aid in remaining_anchor_ids]
                            remaining_rect = _union_rect([
                                _fallback_text_group_rect(group)
                                for group in remaining_groups
                            ])
                            pad = max(40, min(80, int(max(remaining_rect[2], remaining_rect[3]) * 0.25)))
                            remaining_source_rect = _pad_rect_to_image(remaining_rect, pad)
                            group_rescue = run_edge_barrier_rescue_auto(
                                image,
                                remaining_groups,
                                remaining_source_rect,
                                virtual_walls=manual_virtual_walls or None,
                            )
                            group_score = group_rescue.get("score", {})
                            group_reject_reason = None if group_rescue.get("manual_guided") else (
                                _edge_rescue_reject_reason(region, group_rescue, remaining_groups)
                                if group_rescue.get("success") else None
                            )
                            if group_rescue.get("success") and group_reject_reason is None:
                                group_rect = [
                                    int(v) for v in group_rescue.get("rescue_rect", [0, 0, 0, 0])[:4]
                                ]
                                group_mask = group_rescue["mask"]
                                region["anchor_ids"] = remaining_anchor_ids
                                region["seed_points"] = [group_rescue["seed"]] if group_rescue.get("seed") else []
                                region["bubble_rect"] = group_rect
                                region["mask"] = group_mask
                                region["bubble_page_ratio"] = float(group_score.get("mask_page_ratio", 0.0))
                                region["bubble_tg_ratio"] = float(group_score.get("rect_anchor_ratio", 0.0))
                                region["touches_image_border"] = _touches_image_border(group_mask)
                                region["method"] = "edge_barrier_rescue"
                                region["region_type"] = "bubble"
                                region["quality_flags"].extend([
                                    "edge_barrier_rescue",
                                    "edge_group_after_split",
                                    f"edge_best={group_rescue.get('best_label')}",
                                    f"edge_guard={group_rescue.get('guard_mode')}_{group_rescue.get('guard_margin')}",
                                    f"edge_score={round(float(group_score.get('rank_score', 0.0)), 3)}",
                                    f"edge_ratio={round(float(group_score.get('rect_anchor_ratio', 0.0)), 2)}",
                                    f"edge_page={round(float(group_score.get('mask_page_ratio', 0.0)), 4)}",
                                ])
                                region["is_valid"] = True
                            else:
                                region["anchor_ids"] = remaining_anchor_ids
                                if group_reject_reason is not None:
                                    region["quality_flags"].append(f"{group_reject_reason}:remaining_group")
                                region["quality_flags"].append("edge_split_partial_remaining")
                        else:
                            region["anchor_ids"] = remaining_anchor_ids
                            region["quality_flags"].append("edge_split_partial_remaining")
                    else:
                        split_remove_keys.append(key)

            for key in split_remove_keys:
                grouped.pop(key, None)
            grouped.update(split_rescued)

            for noise_anchor in noise_like_anchors:
                if noise_anchor.get("reason") in {"large_single_glyph_sfx_like", "large_short_kana_sfx_like"}:
                    continue
                aid = noise_anchor["cluster_id"]
                rect = noise_anchor["fallback_bbox"]
                cx, cy = _bbox_center(rect)
                for region in grouped.values():
                    if not region.get("is_valid"):
                        continue
                    if region.get("method") != "edge_barrier_rescue":
                        continue
                    rx, ry, rw, rh = [int(v) for v in region.get("bubble_rect", [0, 0, 0, 0])[:4]]
                    pad = 12
                    if rx - pad <= cx <= rx + rw + pad and ry - pad <= cy <= ry + rh + pad:
                        if aid not in region["anchor_ids"]:
                            region["anchor_ids"].append(aid)
                            region["quality_flags"].append(
                                f"merged_symbol_anchor_after_edge_rescue:A{aid}"
                            )
                        break

            for region in grouped.values():
                if not region.get("is_valid"):
                    continue
                if region.get("method") != "edge_barrier_rescue":
                    continue
                flags = region.get("quality_flags", [])
                if any("border=Y" in str(flag) for flag in flags):
                    continue
                if any(str(flag).startswith("merged_symbol_anchor_after_edge_rescue") for flag in flags):
                    continue
                anchor_groups = [
                    clusters_by_id[aid]
                    for aid in region.get("anchor_ids", [])
                    if aid in clusters_by_id
                ]
                raw_round = _extract_quality_float(flags, "raw_round")
                post_reject = _edge_anchor_text_reject_reason(anchor_groups, raw_round)
                if post_reject is None:
                    continue
                region["is_valid"] = False
                region["method"] = "bbox_fallback"
                region["region_type"] = "bbox_fallback"
                region["quality_flags"].append(post_reject)

    # edge_barrier_rescue 결과 중 인접한 단일-앵커 region을 합친다.
    # 동일 말풍선에서 시작점이 달라 flood_rect가 달랐던 TG들은 각자 rescue를 받아
    # 서로 근접한 bubble_rect를 갖게 된다. 앵커 center가 상대 bubble_rect 100px 확장 안에
    # 들어오면 동일 말풍선으로 판단하여 병합한다.
    _RESCUE_PROXIMITY_PAD = 100
    _rescue_regions = [
        (key, region)
        for key, region in grouped.items()
        if region.get("is_valid") and region.get("method") == "edge_barrier_rescue"
    ]
    _merged_into: Dict[Any, Any] = {}  # key → target_key
    for i, (ki, ri) in enumerate(_rescue_regions):
        if ki in _merged_into:
            continue
        br = ri["bubble_rect"]
        exp = [
            br[0] - _RESCUE_PROXIMITY_PAD,
            br[1] - _RESCUE_PROXIMITY_PAD,
            br[2] + _RESCUE_PROXIMITY_PAD * 2,
            br[3] + _RESCUE_PROXIMITY_PAD * 2,
        ]
        for kj, rj in _rescue_regions[i + 1:]:
            if kj in _merged_into:
                continue
            # rj 의 앵커 중 하나라도 ri 의 확장 bubble_rect 안에 있으면 병합
            for aid in rj.get("anchor_ids", []):
                if aid not in clusters_by_id:
                    continue
                ac = _bbox_center(_tight_text_group_rect(clusters_by_id[aid]))
                if (exp[0] <= ac[0] <= exp[0] + exp[2] and
                        exp[1] <= ac[1] <= exp[1] + exp[3]):
                    # rj → ri 로 병합
                    ri["mask"] = cv2.bitwise_or(ri["mask"], rj["mask"])
                    for aid2 in rj.get("anchor_ids", []):
                        if aid2 not in ri["anchor_ids"]:
                            ri["anchor_ids"].append(aid2)
                    for sp in rj.get("seed_points", []):
                        if sp not in ri.get("seed_points", []):
                            ri.setdefault("seed_points", []).append(sp)
                    # bubble_rect 를 합집합으로 갱신
                    bx1 = min(br[0], rj["bubble_rect"][0])
                    by1 = min(br[1], rj["bubble_rect"][1])
                    bx2 = max(br[0] + br[2], rj["bubble_rect"][0] + rj["bubble_rect"][2])
                    by2 = max(br[1] + br[3], rj["bubble_rect"][1] + rj["bubble_rect"][3])
                    ri["bubble_rect"] = [bx1, by1, bx2 - bx1, by2 - by1]
                    br = ri["bubble_rect"]
                    exp = [
                        br[0] - _RESCUE_PROXIMITY_PAD,
                        br[1] - _RESCUE_PROXIMITY_PAD,
                        br[2] + _RESCUE_PROXIMITY_PAD * 2,
                        br[3] + _RESCUE_PROXIMITY_PAD * 2,
                    ]
                    # 병합을 일으킨 rj의 anchor를 기록한다(aid2는 위 루프의 마지막 값이라 다를 수 있다).
                    ri["quality_flags"].append(f"rescue_proximity_merged:A{aid}")
                    _merged_into[kj] = ki
                    break
    # 병합된 region 제거
    for kj in _merged_into:
        grouped.pop(kj, None)

    # rescue 이후 같은 bubble rect로 수렴한 region은 다시 하나로 합친다.
    # 열린 말풍선은 TG별 flood 시작점이 달라도 같은 mask로 수렴하는 경우가 흔하다.
    merged_grouped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for key, region in grouped.items():
        merge_key = ("valid", *region["bubble_rect"]) if region.get("is_valid") else key
        if merge_key not in merged_grouped:
            merged_grouped[merge_key] = region
            continue

        target = merged_grouped[merge_key]
        target["mask"] = cv2.bitwise_or(target["mask"], region["mask"])
        for aid in region.get("anchor_ids", []):
            if aid not in target["anchor_ids"]:
                target["anchor_ids"].append(aid)
        for seed in region.get("seed_points", []):
            if seed not in target["seed_points"]:
                target["seed_points"].append(seed)
        for flag in region.get("quality_flags", []):
            if flag not in target["quality_flags"]:
                target["quality_flags"].append(flag)
        target["is_valid"] = bool(target.get("is_valid") or region.get("is_valid"))
        if target["is_valid"] and target.get("method") == "bbox_fallback":
            target["method"] = region.get("method", target["method"])
        if target["is_valid"] and target.get("region_type") == "bbox_fallback":
            target["region_type"] = region.get("region_type", target["region_type"])
    grouped = merged_grouped

    for region in grouped.values():
        if not region.get("is_valid") or region.get("method") != "flood":
            continue
        reject_reason = _rect_side_intrusion_reject_reason(
            region.get("mask"),
            region.get("quality_flags", []),
        )
        if reject_reason is None:
            continue
        region["is_valid"] = False
        region["method"] = "bbox_fallback"
        region["region_type"] = "bbox_fallback"
        region.setdefault("quality_flags", []).append(reject_reason)

    for region in grouped.values():
        if region.get("is_valid"):
            continue
        probe = _black_contour_probe(
            gray,
            [int(v) for v in region.get("bubble_rect", [0, 0, 0, 0])[:4]],
            region.get("mask"),
            black=_page_black_contour(),
        )
        region["black_contour_probe"] = probe
        region.setdefault("quality_flags", []).append(
            "black_contour="
            f"{probe['status']},cov={probe['coverage']:.2f},"
            f"quad={probe['quadrants']},gap={probe['max_gap']:.2f},"
            f"cv={probe['distance_cv']:.2f}"
        )

    # Cross-split and rescue merging is omitted or simplified for valid bubbles.
    # We just group overlapping valid bubbles directly by their identical bounding rects.

    def _pre_split_reading_key(region: Dict[str, Any]) -> Tuple[float, float]:
        rect = region["bubble_rect"]
        cx = rect[0] + rect[2] / 2
        cy = rect[1] + rect[3] / 2
        return (-cx, cy)

    regions = sorted(grouped.values(), key=_pre_split_reading_key)
    for sort_group_index, region in enumerate(regions):
        region["sort_group_index"] = sort_group_index

    def _split_child_reading_key(sub: Dict[str, Any]) -> Tuple[float, float]:
        rect = sub.get("bubble_rect", sub.get("flood_rect"))
        cx = rect[0] + rect[2] / 2
        cy = rect[1] + rect[3] / 2
        return (-cx, cy)

    def _expanded_split_ocr_rect(
        child_rect: List[int],
        parent_rect: List[int],
        anchor_rects: Optional[List[List[int]]] = None,
        pad: int = 2,
    ) -> List[int]:
        """split child OCR crop은 child bbox를 기본으로, 잘린 TG bbox만 최소 보정한다."""
        cx, cy, cw, ch = [int(v) for v in child_rect[:4]]
        px, py, pw, ph = [int(v) for v in parent_rect[:4]]
        x1, y1 = cx, cy
        x2, y2 = cx + cw, cy + ch
        for rect in anchor_rects or []:
            ax, ay, aw, ah = [int(v) for v in rect[:4]]
            x1 = min(x1, ax - pad)
            y1 = min(y1, ay - pad)
            x2 = max(x2, ax + aw + pad)
            y2 = max(y2, ay + ah + pad)
        x1 = max(px, x1)
        y1 = max(py, y1)
        x2 = min(px + pw, x2)
        y2 = min(py + ph, y2)
        return [x1, y1, max(1, x2 - x1), max(1, y2 - y1)]

    def _padded_text_restore_rects(
        anchor_rects: List[List[int]],
        parent_rect: List[int],
        pad: int = 2,
    ) -> List[List[int]]:
        """split OCR에서 복원할 TG 주변 원본 픽셀 영역만 반환한다."""
        px, py, pw, ph = [int(v) for v in parent_rect[:4]]
        out: List[List[int]] = []
        for rect in anchor_rects:
            ax, ay, aw, ah = [int(v) for v in rect[:4]]
            x1 = max(px, ax - pad)
            y1 = max(py, ay - pad)
            x2 = min(px + pw, ax + aw + pad)
            y2 = min(py + ph, ay + ah + pad)
            if x2 > x1 and y2 > y1:
                out.append([x1, y1, x2 - x1, y2 - y1])
        return out

    def _is_stable_edge_rescue_region(region: Dict[str, Any]) -> bool:
        """안정적인 edge barrier rescue bubble은 후단 split으로 다시 찢지 않는다."""
        if region.get("method") != "edge_barrier_rescue":
            return False
        flags = region.get("quality_flags", [])
        solid = _extract_quality_float(flags, "solid")
        edge_score = _extract_quality_float(flags, "edge_score")
        edge_ratio = _extract_quality_float(flags, "edge_ratio")
        if solid is None or edge_score is None or edge_ratio is None:
            return False
        return solid >= 0.88 and edge_score >= 4.0 and edge_ratio <= 2.3

    def _should_keep_parent_for_continuation_columns(
        anchor_ids: List[int],
        split_result: List[Dict[str, Any]],
    ) -> bool:
        """같은 말풍선 안의 우→좌 이어쓰기 칼럼이면 split을 폐기한다."""
        if len(anchor_ids) != 2 or len(split_result) != 2:
            return False
        split_anchor_ids = [
            [int(aid) for aid in sub.get("anchor_ids", []) if int(aid) in clusters_by_id]
            for sub in split_result
        ]
        if any(len(ids) != 1 for ids in split_anchor_ids):
            return False

        aid_a, aid_b = split_anchor_ids[0][0], split_anchor_ids[1][0]
        rect_a = _tight_text_group_rect(clusters_by_id[aid_a])
        rect_b = _tight_text_group_rect(clusters_by_id[aid_b])

        def _center_x(rect: List[int]) -> float:
            return rect[0] + rect[2] / 2

        right_id, right_rect, left_id, left_rect = (
            (aid_a, rect_a, aid_b, rect_b)
            if _center_x(rect_a) >= _center_x(rect_b)
            else (aid_b, rect_b, aid_a, rect_a)
        )

        _ = right_id
        ry1, ry2 = right_rect[1], right_rect[1] + right_rect[3]
        ly1, ly2 = left_rect[1], left_rect[1] + left_rect[3]
        overlap_y = max(0, min(ry2, ly2) - max(ry1, ly1))
        min_h = max(1, min(right_rect[3], left_rect[3]))
        overlap_ratio = overlap_y / min_h
        align_tol = max(45, int(min_h * 0.55))
        vertically_aligned = (
            abs(ry1 - ly1) <= align_tol
            or abs(ry2 - ly2) <= align_tol
            or (ry1 <= ly1 and ly2 <= ry2)
            or (ly1 <= ry1 and ry2 <= ly2)
        )
        if overlap_ratio < 0.65:
            return False
        if not vertically_aligned:
            return False

        right_x1, right_x2 = right_rect[0], right_rect[0] + right_rect[2]
        left_x1, left_x2 = left_rect[0], left_rect[0] + left_rect[2]
        x_gap = max(0, right_x1 - left_x2)
        if x_gap > max(80, int(max(right_rect[2], left_rect[2]) * 1.2)):
            return False

        left_text = "".join(ch for ch in _cluster_text(clusters_by_id[left_id]) if not ch.isspace())
        starts_like_continuation = bool(left_text) and left_text[0] in "-ー…‥っッ、。！？!?"
        left_is_short = len(left_text) <= 4 or left_rect[2] <= right_rect[2] * 0.85
        return bool(left_is_short or starts_like_continuation)

    def _edge_split_anchor_coverage_ok(split_result: List[Dict[str, Any]]) -> bool:
        """edge rescue split child가 배정된 TG bbox를 충분히 포함하는지 확인한다."""
        for sub in split_result:
            sub_mask = sub.get("sub_mask")
            if not isinstance(sub_mask, np.ndarray):
                return False
            for aid in sub.get("anchor_ids", []):
                if aid not in clusters_by_id:
                    return False
                ax, ay, aw, ah = _tight_text_group_rect(clusters_by_id[aid])
                if aw <= 0 or ah <= 0:
                    return False
                x1 = max(0, ax)
                y1 = max(0, ay)
                x2 = min(sub_mask.shape[1], ax + aw)
                y2 = min(sub_mask.shape[0], ay + ah)
                if x2 <= x1 or y2 <= y1:
                    return False
                coverage = float(np.count_nonzero(sub_mask[y1:y2, x1:x2] > 0)) / max(1, aw * ah)
                if coverage < 0.82:
                    return False
        return True

    crops: List[Dict[str, Any]] = []
    total_width = 0
    max_height = 0
    region_id_counter = 1

    for region in regions:
        anchor_ids = region["anchor_ids"]

        _has_proximity_merge = any(
            "rescue_proximity_merged" in str(f)
            for f in region.get("quality_flags", [])
        )
        allow_split = (
            "edge_group_after_split" not in region.get("quality_flags", [])
            and (
                # proximity_merge로 anchor가 추가된 region만 stable 체크 우회
                # 직접 구제된 다중 anchor 안정 region(같은 말풍선 다중 칼럼)은 stability 존중
                (len(anchor_ids) >= 3 and _has_proximity_merge)
                or not _is_stable_edge_rescue_region(region)
            )
        )
        if region["is_valid"] and len(anchor_ids) >= 2 and allow_split:
            anchor_rects = [
                _tight_text_group_rect(clusters_by_id[aid])
                for aid in anchor_ids
                if aid in clusters_by_id
            ]
            if len(anchor_rects) == len(anchor_ids):
                anchor_centers = [_bbox_center(r) for r in anchor_rects]
                split_result = split_mask_by_inflection_line(
                    region["mask"], anchor_ids, anchor_centers, anchor_rects=anchor_rects
                )
                if split_result is None and len(anchor_ids) == 2:
                    split_result = _split_same_column_continuation_bubble(
                        region["mask"],
                        anchor_ids,
                        anchor_rects,
                        region.get("quality_flags", []),
                    )
                    if split_result is not None:
                        region["quality_flags"].append("same_column_continuation_split_retry")
                if (
                    split_result is None
                    and region.get("method") == "edge_barrier_rescue"
                    and all_anchor_rects_are_disjoint(anchor_rects)
                ):
                    split_result = split_mask_by_erosion_components(
                        region["mask"], anchor_ids, anchor_centers
                    )
                    if split_result is None and len(anchor_ids) == 2:
                        shape_score = _extract_quality_float(region.get("quality_flags", []), "shape")
                        bubble_tg_ratio = float(region.get("bubble_tg_ratio", 0.0))
                        if bubble_tg_ratio >= 8.0 and (shape_score is None or shape_score >= 0.58):
                            split_result = split_mask_by_erosion_components(
                                region["mask"],
                                anchor_ids,
                                anchor_centers,
                                max_erosion=44,
                            )
                            if split_result is not None:
                                region["quality_flags"].append("deep_erosion_split_retry")

                # continuation_column_split_suppressed 비활성화:
                # split 후보 자체에서 line geometry를 검증한다.
                continuation_split_suppressed = False
                if (
                    split_result is not None
                    and region.get("method") == "edge_barrier_rescue"
                    and not _edge_split_anchor_coverage_ok(split_result)
                ):
                    split_result = None
                    region["quality_flags"].append("edge_split_rejected_low_anchor_coverage")

                if split_result is not None:
                    parent_rect = [int(v) for v in region["bubble_rect"]]
                    appended_split_child = False
                    for sub_index, sub in enumerate(sorted(split_result, key=_split_child_reading_key)):
                        if region.get("method") == "edge_barrier_rescue" and not sub.get("anchor_ids"):
                            continue
                        child_rect = [int(v) for v in sub.get("bubble_rect", sub.get("flood_rect"))]
                        sx, sy, sw, sh = child_rect
                        sub_anchor_rects = [
                            _tight_text_group_rect(clusters_by_id[aid])
                            for aid in sub["anchor_ids"]
                            if aid in clusters_by_id
                        ]
                        ocr_rect = _expanded_split_ocr_rect(child_rect, parent_rect, sub_anchor_rects)
                        ocr_text_rects = _padded_text_restore_rects(sub_anchor_rects, parent_rect)
                        sub_seeds = [
                            s for s, aid_s in zip(region["seed_points"], anchor_ids)
                            if aid_s in sub["anchor_ids"]
                        ]
                        sub_region = {
                            **region,
                            "anchor_ids": sub["anchor_ids"],
                            "bubble_rect": child_rect,
                            "ocr_rect": ocr_rect,
                            "ocr_text_rects": ocr_text_rects,
                            "mask": sub["sub_mask"],
                            "split_parent_mask": region.get("mask"),
                            "seed_points": sub_seeds,
                            "split_line": sub.get("split_line"),
                            "split_parent_rect": parent_rect,
                            "split_order": sub_index,
                        }
                        if region.get("method") == "edge_barrier_rescue":
                            sub_groups = [
                                clusters_by_id[aid]
                                for aid in sub["anchor_ids"]
                                if aid in clusters_by_id
                            ]
                            raw_round = _extract_quality_float(region.get("quality_flags", []), "raw_round")
                            split_reject = _edge_anchor_text_reject_reason(sub_groups, raw_round)
                            if split_reject is not None:
                                sub_region["is_valid"] = False
                                sub_region["method"] = "bbox_fallback"
                                sub_region["region_type"] = "bbox_fallback"
                                sub_region["quality_flags"] = list(region.get("quality_flags", [])) + [split_reject]

                        if sub_region["is_valid"]:
                            crop = np.full((sh, sw, 3), 255, dtype=np.uint8)
                            mask_crop = sub["sub_mask"][sy:sy + sh, sx:sx + sw]
                            image_crop = image[sy:sy + sh, sx:sx + sw]
                            crop[mask_crop > 0] = image_crop[mask_crop > 0]
                        else:
                            crop = image[sy:sy + sh, sx:sx + sw].copy()
                        crops.append({
                            "region_id": region_id_counter,
                            "crop": crop,
                            "region": sub_region,
                        })
                        appended_split_child = True
                        total_width += sw
                        max_height = max(max_height, sh)
                        region_id_counter += 1
                    if appended_split_child:
                        continue
                    if region.get("method") == "edge_barrier_rescue":
                        region["is_valid"] = False
                        region["method"] = "bbox_fallback"
                        region["region_type"] = "bbox_fallback"
                        region.setdefault("quality_flags", []).append("edge_split_all_children_anchorless")
                elif not continuation_split_suppressed:
                    region["quality_flags"].append("shared_flood_no_inflection_merge")

        # TG=1 bubble: CV 누락 텍스트 복구를 위한 single-TG split 시도
        if (
            region["is_valid"]
            and len(anchor_ids) == 1
            and region.get("region_type") in {"bubble", "narration_like"}
            and allow_split
        ):
            aid = anchor_ids[0]
            if aid in clusters_by_id:
                anchor_rect = _tight_text_group_rect(clusters_by_id[aid])
                anchor_center = _bbox_center(anchor_rect)
                split_result = _split_single_tg_bubble(
                    region["mask"],
                    anchor_id=aid,
                    anchor_center=anchor_center,
                    anchor_rect=anchor_rect,
                    quality_flags=region.get("quality_flags", []),
                )
                if split_result is not None:
                    parent_rect = [int(v) for v in region["bubble_rect"]]
                    for sub_index, sub in enumerate(sorted(split_result, key=_split_child_reading_key)):
                        child_rect = [int(v) for v in sub["bubble_rect"]]
                        sx, sy, sw, sh = child_rect
                        sub_anchor_ids = sub["anchor_ids"]
                        sub_anchor_rects = [anchor_rect] if sub_anchor_ids else []
                        ocr_rect = _expanded_split_ocr_rect(child_rect, parent_rect, sub_anchor_rects)
                        ocr_text_rects = _padded_text_restore_rects(sub_anchor_rects, parent_rect)
                        sub_seeds = [
                            s for s, aid_s in zip(region["seed_points"], anchor_ids)
                            if aid_s in sub_anchor_ids
                        ]
                        sub_region = {
                            **region,
                            "anchor_ids": sub_anchor_ids,
                            "bubble_rect": child_rect,
                            "ocr_rect": ocr_rect,
                            "ocr_text_rects": ocr_text_rects,
                            "mask": sub["sub_mask"],
                            "seed_points": sub_seeds,
                            "split_line": sub.get("split_line"),
                            "is_orphan_split": len(sub_anchor_ids) == 0,
                            "split_parent_rect": parent_rect,
                            "split_order": sub_index,
                        }
                        crop = np.full((sh, sw, 3), 255, dtype=np.uint8)
                        mask_crop = sub["sub_mask"][sy:sy + sh, sx:sx + sw]
                        image_crop = image[sy:sy + sh, sx:sx + sw]
                        crop[mask_crop > 0] = image_crop[mask_crop > 0]
                        crops.append({
                            "region_id": region_id_counter,
                            "crop": crop,
                            "region": sub_region,
                        })
                        total_width += sw
                        max_height = max(max_height, sh)
                        region_id_counter += 1
                    continue

        # Fallback to bbox if not valid or no split
        x, y, w, h = [int(v) for v in region["bubble_rect"]]
        crop = np.full((h, w, 3), 255, dtype=np.uint8)
        if region["is_valid"]:
            mask_crop = region["mask"][y:y + h, x:x + w]
            image_crop = image[y:y + h, x:x + w]
            crop[mask_crop > 0] = image_crop[mask_crop > 0]
        else:
            # For bbox fallback, we don't have a mask, just crop the raw image
            crop = image[y:y + h, x:x + w].copy()

        crops.append({"region_id": region_id_counter, "crop": crop, "region": region})
        total_width += w
        max_height = max(max_height, h)
        region_id_counter += 1

    # 원본 bubble 순서를 먼저 고정하고, split 자식만 그 자리에서 연속으로 펼친다.
    def _reading_order_key(item: Dict[str, Any]) -> Tuple[float, float, float]:
        region = item["region"]
        sort_group_index = float(region.get("sort_group_index", 0))
        return (sort_group_index, float(region.get("split_order", 0)), 0.0)

    crops.sort(key=_reading_order_key)
    for idx, item in enumerate(crops, start=1):
        item["region_id"] = idx
        item["region"]["review_tags"] = _derive_review_tags(item["region"])

    combined_mask = np.zeros((img_h, img_w), dtype=np.uint8)
    for item in crops:
        if item["region"]["is_valid"]:
            combined_mask = cv2.bitwise_or(combined_mask, item["region"]["mask"])

    masked = image.copy()
    masked[combined_mask > 0] = (255, 255, 255)
    
    if output_masked_path:
        cv2.imwrite(output_masked_path, masked)
        
    if output_vis_path:
        try:
            from pipeline.bubble_candidate_visualizer import render_candidate_review_vis
        except ImportError:
            from bubble_candidate_visualizer import render_candidate_review_vis

        render_candidate_review_vis(image, crops, output_vis_path, clusters=clusters, skipped=skipped)

    if output_regions_only_path:
        regions_only = np.full_like(image, 255)
        regions_only[combined_mask > 0] = image[combined_mask > 0]
        cv2.imwrite(output_regions_only_path, regions_only)

    sheet = np.full((max_height or 1, total_width or 1, 3), 255, dtype=np.uint8)
    sheet_x = 0
    output_regions: List[Dict[str, Any]] = []
    
    for item in crops:
        crop = item["crop"]
        h, w = crop.shape[:2]
        sheet[0:h, sheet_x:sheet_x + w] = crop
        region = item["region"]
        polygon = _extract_mask_polygon(region["mask"]) if region["is_valid"] else None
        region_data = {
            "region_id": item["region_id"],
            "anchor_ids": region["anchor_ids"],
            "method": region["method"],
            "region_type_guess": region["region_type"],
            "bubble_rect": region["bubble_rect"],
            "sheet_rect": [sheet_x, 0, w, h],
            "seed_points": region.get("seed_points", []),
            "polygon": polygon,
            "bubble_page_ratio": round(float(region["bubble_page_ratio"]), 5),
            "bubble_tg_ratio": round(float(region["bubble_tg_ratio"]), 2),
            "touches_image_border": region["touches_image_border"],
            "quality_flags": region["quality_flags"],
            "review_tags": region.get("review_tags", []),
            "is_valid": region["is_valid"],
        }
        if "sort_group_index" in region:
            region_data["sort_group_index"] = region["sort_group_index"]
        if region.get("split_parent_rect"):
            region_data["split_parent_rect"] = region["split_parent_rect"]
            region_data["split_order"] = region.get("split_order", 0)
        if region.get("split_line"):
            region_data["split_line"] = region["split_line"]
        if region.get("ocr_rect"):
            region_data["ocr_rect"] = region["ocr_rect"]
        if region.get("ocr_text_rects"):
            region_data["ocr_text_rects"] = region["ocr_text_rects"]
        if region.get("black_contour_probe"):
            region_data["black_contour_probe"] = region["black_contour_probe"]
        output_regions.append(region_data)
        sheet_x += w

    if output_sheet_path:
        cv2.imwrite(output_sheet_path, sheet)

    if indexed_output_sheet_path:
        gutter = max(0, indexed_label_gutter)
        indexed = np.full((sheet.shape[0] + gutter, sheet.shape[1], 3), 255, dtype=np.uint8)
        indexed[gutter:gutter + sheet.shape[0], 0:sheet.shape[1]] = sheet
        for region in output_regions:
            sx, sy, sw, sh = region["sheet_rect"]
            region["indexed_sheet_rect"] = [sx, sy + gutter, sw, sh]
            color = (0, 150, 0) if region["is_valid"] else (0, 0, 210)

            cv2.rectangle(indexed, (sx, gutter + sy), (sx + sw - 1, gutter + sy + sh - 1), (225, 225, 225), 1)
            if sx > 0:
                cv2.line(indexed, (sx, 0), (sx, indexed.shape[0] - 1), (238, 238, 238), 1)

            label = f"R{region['region_id']}"
            aids = "A" + ",".join(str(a) for a in region["anchor_ids"]) if region["anchor_ids"] else "A-"
            cv2.putText(indexed, label, (sx + 4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2, cv2.LINE_AA)
            cv2.putText(indexed, aids, (sx + 4, 39), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (130, 80, 0), 1, cv2.LINE_AA)
        cv2.imwrite(indexed_output_sheet_path, indexed)

    output = {
        "image": image_path,
        "clusters": clusters_json,
        "masked_output": output_masked_path,
        "sheet_output": output_sheet_path,
        "indexed_sheet_output": indexed_output_sheet_path,
        "original_size": [img_w, img_h],
        "sheet_size": [int(sheet.shape[1]), int(sheet.shape[0])],
        "summary": {
            "total_anchors": len(clusters),
            "skipped": len(skipped),
            "valid_bubbles": sum(1 for r in output_regions if r["is_valid"]),
        },
        "skipped": skipped,
        "regions": output_regions,
        "diagnostics": diagnostics,
    }
    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    return output

def main() -> None:
    # 모드 A (4 args): visualize  — flood_vis.json + debug 이미지 생성
    # 모드 B (6 args): mask       — flood pre-masking 이미지 + regions JSON 생성
    if len(sys.argv) == 5:
        result = visualize_anchor_floodfill(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
        matched = sum(1 for item in result["results"] if item.get("seed"))
        print(f"✅ Anchor flood fill 시각화 완료: {matched}/{len(result['results'])} seeded")
        print(f"   JSON: {sys.argv[3]}")
        print(f"   Debug: {sys.argv[4]}")

    elif len(sys.argv) in (6, 7):
        # argv: image clusters_json masked_out sheet_out regions_json [indexed_sheet_out]
        indexed_path = sys.argv[6] if len(sys.argv) == 7 else None
        result = build_ocr_region_sheet(
            image_path=sys.argv[1],
            clusters_json=sys.argv[2],
            output_masked_path=sys.argv[3],
            output_sheet_path=sys.argv[4],
            output_json_path=sys.argv[5],
            indexed_output_sheet_path=indexed_path,
        )
        s = result.get("summary", {})
        print(
            f"✅ Flood masking 완료: flood={s.get('flood', 0)}"
            f" symbol_skip={s.get('symbol_only_skipped', 0)}"
            f" dark_skip={s.get('dark_bg_skipped', 0)}"
        )
        print(f"   masked: {sys.argv[3]}")
        print(f"   json:   {sys.argv[5]}")
        if indexed_path:
            print(f"   indexed: {indexed_path}")

    else:
        print("Usage (visualize): python3 floodfill_anchor_probe.py <image> <clusters.json> <out.json> <debug.jpg>")
        print("Usage (mask):      python3 floodfill_anchor_probe.py <image> <clusters.json> <masked.jpg> <sheet.jpg> <regions.json> [indexed_sheet.jpg]")
        sys.exit(1)


if __name__ == "__main__":
    main()
