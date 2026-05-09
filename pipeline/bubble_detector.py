#!/usr/bin/env python3
"""
Tight Text Line Group 기반 Speech Bubble contour 후보 탐색.

현재 단계에서는 masking/overlay에 연결하지 않고, TG별 local ROI에서 contour 후보를 찾은 뒤
JSON과 debug 이미지로만 검증한다.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


Rect = Tuple[int, int, int, int]
Point = Tuple[float, float]
RoiScale = Tuple[float, float]
RoiMode = str

MIN_COMPACTNESS = 0.18
MAX_PERIMETER_AREA_RATIO = 0.22
MAX_BUBBLE_AREA_RATIO = 0.08
MAX_CONTOUR_TO_GROUP_AREA_RATIO = 45.0
MAX_CONTOUR_TO_GROUP_WIDTH_RATIO = 12.0
MAX_CONTOUR_TO_GROUP_HEIGHT_RATIO = 5.0
MAX_PERIMETER_COMPLEXITY = 1.8
DEBUG_CONTOUR_LIMIT_PER_CATEGORY = 8


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _bbox_center(bbox: List[int]) -> Point:
    x, y, w, h = bbox
    return (x + w / 2.0, y + h / 2.0)


def _bbox_area(bbox: List[int]) -> int:
    return max(int(bbox[2]), 0) * max(int(bbox[3]), 0)


def _group_id(group: Dict) -> Any:
    return group.get("text_group_id", group.get("cluster_id", "?"))


def _group_label(group: Dict) -> str:
    return "text_group" if "text_group_id" in group else "anchor_cluster"


def _group_text(group: Dict) -> str:
    return "".join(line.get("text", "") for line in group.get("lines", []))


def _bbox_overlap_ratio(a: List[int], b: List[int]) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    return (iw * ih) / max(_bbox_area(a), 1)


def _clip_rect(x1: float, y1: float, x2: float, y2: float, img_w: int, img_h: int) -> Rect:
    left = max(0, int(round(x1)))
    top = max(0, int(round(y1)))
    right = min(img_w, int(round(x2)))
    bottom = min(img_h, int(round(y2)))
    return (left, top, max(1, right - left), max(1, bottom - top))


def _roi_for_group(group: Dict, img_w: int, img_h: int, scale: RoiScale, roi_mode: RoiMode) -> Rect:
    x, y, w, h = group["bounding_rect"]
    cx, cy = _bbox_center(group["bounding_rect"])
    scale_x, scale_y = scale

    if roi_mode == "bbox_scale":
        half_w = max((w * scale_x) / 2.0, w / 2.0)
        half_h = max((h * scale_y) / 2.0, h / 2.0)
        return _clip_rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h, img_w, img_h)

    # scale은 전체 이미지 비율 기반 half-range다.
    # 너무 넓으면 패널/배경 contour가 이기므로 1차 ROI는 작게 시작한다.
    half_w = max(w * 2.0, img_w * scale_x)
    half_h = max(h * 0.75, img_h * scale_y)

    return _clip_rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h, img_w, img_h)


def _group_points(group: Dict) -> List[Point]:
    points: List[Point] = []
    for line in group.get("lines", []):
        bbox = line.get("bbox", [])
        if len(bbox) == 4:
            points.append(_bbox_center(bbox))
        for fragment in line.get("fragments", []):
            fb = fragment.get("bbox", [])
            if len(fb) == 4:
                points.append(_bbox_center(fb))

    if not points and len(group.get("bounding_rect", [])) == 4:
        points.append(_bbox_center(group["bounding_rect"]))

    return points


def _preprocess_roi(roi: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """
    말풍선 외곽선은 대체로 촘촘히 이어져 있으므로, 강한 close/dilate로 끊긴 선을
    억지 연결하면 글자/톤/효과선까지 하나의 contour로 붙는 문제가 생긴다.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi.copy()
    bilateral = cv2.bilateralFilter(gray, d=5, sigmaColor=75, sigmaSpace=75)

    adaptive = cv2.adaptiveThreshold(
        bilateral,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        21,
        4,
    )

    canny = cv2.Canny(bilateral, 40, 140)

    kernel_close = np.ones((3, 3), np.uint8)
    adaptive_closed = cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, kernel_close, iterations=1)
    canny_closed = cv2.morphologyEx(canny, cv2.MORPH_CLOSE, kernel_close, iterations=1)

    _, otsu_dark = cv2.threshold(bilateral, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    otsu_dark = cv2.morphologyEx(otsu_dark, cv2.MORPH_CLOSE, kernel_close, iterations=1)

    # 말풍선 내부는 대체로 높은 명도의 흰 영역이다. 다만 white component 자체의 contour는
    # ROI crop 경계, 글자 구멍, 패널선 때문에 가짜 폐곡선이 되기 쉬우므로 후보로 채택하지 않는다.
    # 이후 outline 검증용 보조 마스크로만 쓰는 것이 맞다.
    _, white_mask = cv2.threshold(bilateral, 238, 255, cv2.THRESH_BINARY)

    return [
        ("adaptive_dark", adaptive_closed),
        ("canny", canny_closed),
        ("otsu_dark", otsu_dark),
        ("white_region_support", white_mask),
    ]


def _touches_roi_border(contour: np.ndarray, roi_w: int, roi_h: int, tolerance: int = 3) -> bool:
    x, y, w, h = cv2.boundingRect(contour)
    return (
        x <= tolerance
        or y <= tolerance
        or x + w >= roi_w - tolerance
        or y + h >= roi_h - tolerance
    )


def _contour_shape_metrics(contour: np.ndarray, area: float) -> Dict[str, float]:
    perimeter = max(cv2.arcLength(contour, True), 1.0)
    roundness = min((4.0 * np.pi * area) / (perimeter * perimeter), 1.0)
    perimeter_area_ratio = perimeter / max(area, 1.0)

    x, y, w, h = cv2.boundingRect(contour)
    rect_area = max(w * h, 1)
    extent = area / rect_area
    aspect_ratio = w / max(h, 1)
    ideal_rect_perimeter = max(2 * (w + h), 1)
    perimeter_complexity = perimeter / ideal_rect_perimeter

    hull = cv2.convexHull(contour)
    hull_area = max(cv2.contourArea(hull), 1.0)
    convexity = min(area / hull_area, 1.0)

    return {
        "perimeter": float(perimeter),
        "roundness": float(roundness),
        "perimeter_area_ratio": float(perimeter_area_ratio),
        "perimeter_complexity": float(perimeter_complexity),
        "extent": float(extent),
        "aspect_ratio": float(aspect_ratio),
        "convexity": float(convexity),
    }


def _is_simple_bubble_shape(metrics: Dict[str, float]) -> bool:
    """
    면적 대비 둘레가 과도하게 긴 복잡 contour를 제거한다.

    roundness는 4πA/P² 값이다. 원은 1.0, 정사각형은 약 0.785,
    긴 직사각형/복잡한 디자인 contour는 낮아진다.
    """
    if metrics["roundness"] < MIN_COMPACTNESS:
        return False
    if metrics["perimeter_area_ratio"] > MAX_PERIMETER_AREA_RATIO:
        return False
    if metrics["perimeter_complexity"] > MAX_PERIMETER_COMPLEXITY:
        return False
    if metrics["convexity"] < 0.45:
        return False
    if metrics["aspect_ratio"] > 8.0 or metrics["aspect_ratio"] < 0.12:
        return False
    return True


def _is_anchor_local_bubble_shape(metrics: Dict[str, float]) -> bool:
    """
    Anchor bbox 주변 local ROI에서만 쓰는 완화 기준.

    말풍선 꼬리, 겹친 말풍선, 효과선 접촉이 있으면 roundness/complexity가 나빠지므로
    전역 탐색 기준보다 느슨하게 본다. 이 기준은 넓은 image-scale ROI에는 적용하지 않는다.
    """
    if metrics["perimeter_area_ratio"] > 0.09:
        return False
    if metrics["perimeter_complexity"] > 4.3:
        return False
    if metrics["convexity"] < 0.40:
        return False
    if metrics["extent"] < 0.35:
        return False
    if metrics["aspect_ratio"] > 8.0 or metrics["aspect_ratio"] < 0.12:
        return False
    return True


def _all_points_inside(contour: np.ndarray, points: List[Point], offset_x: int, offset_y: int) -> bool:
    if not points:
        return False

    for px, py in points:
        local_point = (float(px - offset_x), float(py - offset_y))
        if cv2.pointPolygonTest(contour, local_point, False) < 0:
            return False

    return True


def _dedupe_candidates(candidates: List[Dict]) -> List[Dict]:
    deduped: List[Dict] = []
    seen = set()
    for candidate in sorted(candidates, key=lambda c: c["score"], reverse=True):
        rect = candidate["bounding_rect"]
        key = tuple(round(v / 8) for v in rect)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _add_debug_contour(debug_contours: Dict[str, List[Dict]], category: str, item: Dict) -> None:
    bucket = debug_contours.setdefault(category, [])
    if len(bucket) >= DEBUG_CONTOUR_LIMIT_PER_CATEGORY:
        return
    bucket.append(item)


def _is_reasonable_size_for_group(
    area: float,
    image_area: int,
    contour_bbox: List[int],
    group_bbox: List[int],
    group_area: int,
) -> bool:
    """패널처럼 TG 대비 과도하게 큰 후보를 제거한다."""
    if area / image_area > MAX_BUBBLE_AREA_RATIO:
        return False

    cw, ch = max(contour_bbox[2], 1), max(contour_bbox[3], 1)
    gw, gh = max(group_bbox[2], 1), max(group_bbox[3], 1)

    if area / group_area > MAX_CONTOUR_TO_GROUP_AREA_RATIO:
        return False
    if cw / gw > MAX_CONTOUR_TO_GROUP_WIDTH_RATIO:
        return False
    if ch / gh > MAX_CONTOUR_TO_GROUP_HEIGHT_RATIO:
        return False

    return True


def _candidate_score(area: float, group_area: int, metrics: Dict[str, float], border_touch: bool, retry_idx: int) -> float:
    return (
        1.0
        + min(area / group_area, 12.0) * 0.05
        + metrics["roundness"] * 0.25
        + metrics["convexity"] * 0.15
        + metrics["extent"] * 0.1
        - (0.45 if border_touch else 0.0)
        - retry_idx * 0.03
    )


def _size_rejection_reason(
    area: float,
    image_area: int,
    contour_bbox: List[int],
    group_bbox: List[int],
    group_area: int,
) -> Optional[str]:
    if area / image_area > MAX_BUBBLE_AREA_RATIO:
        return "panel_area_ratio"

    cw, ch = max(contour_bbox[2], 1), max(contour_bbox[3], 1)
    gw, gh = max(group_bbox[2], 1), max(group_bbox[3], 1)

    if area / group_area > MAX_CONTOUR_TO_GROUP_AREA_RATIO:
        return "too_large_for_group_area"
    if cw / gw > MAX_CONTOUR_TO_GROUP_WIDTH_RATIO:
        return "too_wide_for_group"
    if ch / gh > MAX_CONTOUR_TO_GROUP_HEIGHT_RATIO:
        return "too_tall_for_group"

    return None


def _build_candidate(
    contour: np.ndarray,
    rx: int,
    ry: int,
    rw: int,
    rh: int,
    area: float,
    image_area: int,
    group_bbox: List[int],
    group_area: int,
    metrics: Dict[str, float],
    retry_idx: int,
    method: Any,
    score_bonus: float = 0.0,
) -> Dict:
    x, y, w, h = cv2.boundingRect(contour)
    area_ratio = area / image_area
    border_touch = _touches_roi_border(contour, rw, rh)
    score = _candidate_score(area, group_area, metrics, border_touch, retry_idx) + score_bonus
    contour_global = contour.reshape(-1, 2) + np.array([rx, ry])

    return {
        "bounding_rect": [int(x + rx), int(y + ry), int(w), int(h)],
        "area": round(area, 2),
        "area_ratio": round(area_ratio, 5),
        "perimeter": round(metrics["perimeter"], 2),
        "roundness": round(metrics["roundness"], 4),
        "perimeter_area_ratio": round(metrics["perimeter_area_ratio"], 5),
        "perimeter_complexity": round(metrics["perimeter_complexity"], 4),
        "extent": round(metrics["extent"], 4),
        "aspect_ratio": round(metrics["aspect_ratio"], 4),
        "convexity": round(metrics["convexity"], 4),
        "contour_to_group_area_ratio": round(area / group_area, 3),
        "contour_to_group_width_ratio": round(w / max(group_bbox[2], 1), 3),
        "contour_to_group_height_ratio": round(h / max(group_bbox[3], 1), 3),
        "roi": [rx, ry, rw, rh],
        "retry": retry_idx,
        "method": method,
        "touches_roi_border": border_touch,
        "score": round(float(score), 4),
        "contour": contour_global.astype(int).tolist(),
    }


def _find_panel_canvas_candidates(
    roi: np.ndarray,
    panel_contour: np.ndarray,
    rx: int,
    ry: int,
    group_bbox: List[int],
    group_area: int,
    group_points: List[Point],
    image_area: int,
    retry_idx: int,
    method_idx: int,
) -> Tuple[List[Dict], List[Dict]]:
    """
    과대 contour 내부만 흰 캔버스에 옮겨 하위 bubble contour를 다시 찾는다.

    패널/배경 contour 자체는 흰 선으로 지워서 2차 findContours에서 다시 이기지 않게 한다.
    """
    rh, rw = roi.shape[:2]
    canvas = np.full_like(roi, 255)
    mask = np.zeros((rh, rw), dtype=np.uint8)
    cv2.drawContours(mask, [panel_contour], -1, 255, thickness=-1)
    canvas[mask == 255] = roi[mask == 255]
    cv2.drawContours(canvas, [panel_contour], -1, (255, 255, 255), thickness=8)

    candidates: List[Dict] = []
    debug_items: Dict[str, List[Dict]] = {}
    retrieval_modes = [
        ("external", cv2.RETR_EXTERNAL),
        ("tree", cv2.RETR_TREE),
    ]

    for inner_method_idx, (inner_method_name, binary) in enumerate(_preprocess_roi(canvas), start=1):
        for retrieval_name, retrieval_mode in retrieval_modes:
            contours, _ = cv2.findContours(binary, retrieval_mode, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = float(cv2.contourArea(contour))
                if area <= group_area * 1.15:
                    continue

                x, y, w, h = cv2.boundingRect(contour)
                global_rect = [x + rx, y + ry, w, h]
                contour_global = contour.reshape(-1, 2) + np.array([rx, ry])
                border_touch = _touches_roi_border(contour, rw, rh)
                metrics = _contour_shape_metrics(contour, area)

                if _size_rejection_reason(area, image_area, global_rect, group_bbox, group_area):
                    continue

                if w < group_bbox[2] or h < group_bbox[3]:
                    continue
                if (
                    global_rect[0] > group_bbox[0]
                    or global_rect[1] > group_bbox[1]
                    or global_rect[0] + global_rect[2] < group_bbox[0] + group_bbox[2]
                    or global_rect[1] + global_rect[3] < group_bbox[1] + group_bbox[3]
                ):
                    continue
                if not _all_points_inside(contour, group_points, rx, ry):
                    continue

                if not _is_simple_bubble_shape(metrics):
                    _add_debug_contour(debug_items, "panel_canvas_rejected", {
                        "reason": "shape_filter",
                        "bounding_rect": global_rect,
                        "area": round(area, 2),
                        "roundness": round(metrics["roundness"], 4),
                        "perimeter_complexity": round(metrics["perimeter_complexity"], 4),
                        "roi": [rx, ry, rw, rh],
                        "retry": retry_idx,
                        "method": f"panel_canvas:{method_idx}:{inner_method_name}:{retrieval_name}",
                        "touches_roi_border": border_touch,
                        "contour": contour_global.astype(int).tolist(),
                    })
                    continue

                candidates.append(_build_candidate(
                    contour,
                    rx,
                    ry,
                    rw,
                    rh,
                    area,
                    image_area,
                    group_bbox,
                    group_area,
                    metrics,
                    retry_idx,
                    f"panel_canvas:{method_idx}:{inner_method_name}:{retrieval_name}",
                    score_bonus=0.18,
                ))

    return candidates, debug_items.get("panel_canvas_rejected", [])


def _find_candidates_for_group(
    image: np.ndarray,
    group: Dict,
    image_area: int,
    scales: List[RoiScale],
    roi_mode: RoiMode,
) -> Tuple[Optional[Dict], List[Dict]]:
    img_h, img_w = image.shape[:2]
    group_bbox = group["bounding_rect"]
    group_area = max(_bbox_area(group_bbox), 1)
    group_points = _group_points(group)
    all_candidates: List[Dict] = []
    scale_summaries: List[Dict] = []
    debug_contours: Dict[str, List[Dict]] = {}

    for retry_idx, scale in enumerate(scales, start=1):
        rx, ry, rw, rh = _roi_for_group(group, img_w, img_h, scale, roi_mode)
        roi = image[ry : ry + rh, rx : rx + rw]
        raw_contours = 0
        accepted_before_dedupe = 0

        for method_idx, (method_name, binary) in enumerate(_preprocess_roi(roi), start=1):
            if method_name == "white_region_support":
                continue

            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            raw_contours += len(contours)

            for contour in contours:
                area = float(cv2.contourArea(contour))
                if area <= group_area * 1.15:
                    continue

                x, y, w, h = cv2.boundingRect(contour)
                global_rect = [x + rx, y + ry, w, h]
                contour_global = contour.reshape(-1, 2) + np.array([rx, ry])
                border_touch = _touches_roi_border(contour, rw, rh)
                metrics = _contour_shape_metrics(contour, area)
                size_rejection = _size_rejection_reason(area, image_area, global_rect, group_bbox, group_area)
                overlaps_group = _bbox_overlap_ratio(group_bbox, global_rect) >= 0.5

                if size_rejection and overlaps_group:
                    _add_debug_contour(debug_contours, "panel_or_too_large", {
                        "reason": size_rejection,
                        "bounding_rect": global_rect,
                        "area": round(area, 2),
                        "area_ratio": round(area / image_area, 5),
                        "contour_to_group_area_ratio": round(area / group_area, 3),
                        "contour_to_group_width_ratio": round(w / max(group_bbox[2], 1), 3),
                        "contour_to_group_height_ratio": round(h / max(group_bbox[3], 1), 3),
                        "roi": [rx, ry, rw, rh],
                        "retry": retry_idx,
                        "method": method_name,
                        "touches_roi_border": border_touch,
                        "contour": contour_global.astype(int).tolist(),
                    })
                    panel_candidates, panel_rejected = _find_panel_canvas_candidates(
                        roi,
                        contour,
                        rx,
                        ry,
                        group_bbox,
                        group_area,
                        group_points,
                        image_area,
                        retry_idx,
                        method_name,
                    )
                    all_candidates.extend(panel_candidates)
                    for rejected in panel_rejected:
                        _add_debug_contour(debug_contours, "panel_canvas_rejected", rejected)
                    continue

                if w < group_bbox[2] or h < group_bbox[3]:
                    _add_debug_contour(debug_contours, "too_small_bbox", {
                        "reason": "bbox_smaller_than_group",
                        "bounding_rect": global_rect,
                        "area": round(area, 2),
                        "roi": [rx, ry, rw, rh],
                        "retry": retry_idx,
                        "method": method_name,
                        "touches_roi_border": border_touch,
                        "contour": contour_global.astype(int).tolist(),
                    })
                    continue

                if (
                    global_rect[0] > group_bbox[0]
                    or global_rect[1] > group_bbox[1]
                    or global_rect[0] + global_rect[2] < group_bbox[0] + group_bbox[2]
                    or global_rect[1] + global_rect[3] < group_bbox[1] + group_bbox[3]
                ):
                    if overlaps_group:
                        _add_debug_contour(debug_contours, "partial_overlap", {
                            "reason": "does_not_contain_group_bbox",
                            "bounding_rect": global_rect,
                            "area": round(area, 2),
                            "roi": [rx, ry, rw, rh],
                            "retry": retry_idx,
                            "method": method_name,
                            "touches_roi_border": border_touch,
                            "contour": contour_global.astype(int).tolist(),
                        })
                    continue

                points_inside = _all_points_inside(contour, group_points, rx, ry)
                if not points_inside:
                    _add_debug_contour(debug_contours, "points_outside", {
                        "reason": "group_points_outside_contour",
                        "bounding_rect": global_rect,
                        "area": round(area, 2),
                        "roi": [rx, ry, rw, rh],
                        "retry": retry_idx,
                        "method": method_name,
                        "touches_roi_border": border_touch,
                        "contour": contour_global.astype(int).tolist(),
                    })
                    continue

                shape_ok = _is_simple_bubble_shape(metrics)
                method = method_name
                score_bonus = 0.0
                if not shape_ok and roi_mode == "bbox_scale" and _is_anchor_local_bubble_shape(metrics):
                    shape_ok = True
                    method = f"anchor_local_relaxed:{method_name}"
                    score_bonus = -0.05

                if not shape_ok:
                    _add_debug_contour(debug_contours, "complex_or_nonbubble", {
                        "reason": "shape_filter",
                        "bounding_rect": global_rect,
                        "area": round(area, 2),
                        "roundness": round(metrics["roundness"], 4),
                        "perimeter_area_ratio": round(metrics["perimeter_area_ratio"], 5),
                        "perimeter_complexity": round(metrics["perimeter_complexity"], 4),
                        "convexity": round(metrics["convexity"], 4),
                        "roi": [rx, ry, rw, rh],
                        "retry": retry_idx,
                        "method": method_name,
                        "touches_roi_border": border_touch,
                        "contour": contour_global.astype(int).tolist(),
                    })
                    continue

                all_candidates.append(_build_candidate(
                    contour,
                    rx,
                    ry,
                    rw,
                    rh,
                    area,
                    image_area,
                    group_bbox,
                    group_area,
                    metrics,
                    retry_idx,
                    method,
                    score_bonus=score_bonus,
                ))
                accepted_before_dedupe += 1

        scale_summaries.append({
            "retry": retry_idx,
            "scale_x": scale[0],
            "scale_y": scale[1],
            "roi_mode": roi_mode,
            "roi": [rx, ry, rw, rh],
            "raw_contours": raw_contours,
            "accepted_before_dedupe": accepted_before_dedupe,
        })

    candidates = _dedupe_candidates(all_candidates)
    best = candidates[0] if candidates else None
    if best is not None:
        best["scale_summaries"] = scale_summaries
    return best, candidates[:5], scale_summaries, debug_contours


def detect_bubble_candidates(
    image_path: str,
    groups_path: str,
    output_json_path: str,
    debug_output_path: Optional[str] = None,
    roi_scales: Optional[List[RoiScale]] = None,
    roi_mode: RoiMode = "image_scale",
) -> Dict[str, Any]:
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"이미지를 불러올 수 없습니다: {image_path}")

    groups = _load_json(groups_path)
    img_h, img_w = image.shape[:2]
    image_area = img_w * img_h
    scales = roi_scales or [(0.08, 0.05), (0.14, 0.09), (0.22, 0.14), (0.32, 0.2)]

    results = []
    for group in groups:
        best, candidates, scale_summaries, debug_contours = _find_candidates_for_group(
            image,
            group,
            image_area,
            scales,
            roi_mode,
        )
        results.append({
            "group_id": _group_id(group),
            "group_label": _group_label(group),
            "text_group_id": group.get("text_group_id"),
            "cluster_id": group.get("cluster_id"),
            "text_group_bbox": group.get("bounding_rect"),
            "text": _group_text(group),
            "best_candidate": best,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "scale_summaries": scale_summaries,
            "debug_contours": debug_contours,
        })

    output = {
        "image": image_path,
        "groups": groups_path,
        "roi_mode": roi_mode,
        "roi_scales": scales,
        "bubble_candidates": results,
    }

    if debug_output_path:
        draw_bubble_candidates_debug(image, results, debug_output_path)

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output


def draw_bubble_candidates_debug(image: np.ndarray, results: List[Dict], output_path: str) -> None:
    debug = image.copy()
    overlay = debug.copy()
    contour_index = 1

    category_styles = {
        "panel_or_too_large": ((255, 60, 0), 4),
        "complex_or_nonbubble": ((0, 0, 255), 4),
        "points_outside": ((0, 255, 255), 4),
        "partial_overlap": ((0, 120, 255), 4),
        "too_small_bbox": ((0, 0, 0), 3),
        "panel_canvas_rejected": ((255, 0, 255), 4),
    }

    def draw_index_label(target: np.ndarray, text: str, x: int, y: int, color: Tuple[int, int, int]) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.72
        thickness = 2
        (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
        pad_x, pad_y = 5, 4
        left = max(0, min(x, target.shape[1] - tw - pad_x * 2 - 1))
        top = max(th + pad_y * 2, min(y, target.shape[0] - baseline - 1))
        cv2.rectangle(
            target,
            (left, top - th - pad_y * 2),
            (left + tw + pad_x * 2, top + baseline),
            color,
            -1,
        )
        cv2.putText(
            target,
            text,
            (left + pad_x, top - pad_y),
            font,
            scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )

    for result in results:
        for category, (color, thickness) in category_styles.items():
            for item in result.get("debug_contours", {}).get(category, []):
                contour = np.array(item["contour"], dtype=np.int32).reshape(-1, 1, 2)
                cv2.drawContours(overlay, [contour], -1, color, max(5, thickness))
                x, y, _, _ = item["bounding_rect"]
                item["debug_index"] = contour_index
                draw_index_label(overlay, str(contour_index), int(x), int(y), color)
                contour_index += 1

        tg_id = result.get("group_id", "?")

        candidate = result.get("best_candidate")
        if not candidate:
            continue

        contour = np.array(candidate["contour"], dtype=np.int32).reshape(-1, 1, 2)
        color = (0, 180, 0) if not candidate.get("touches_roi_border") else (0, 140, 255)
        cv2.drawContours(debug, [contour], -1, color, 5)
        x, y, _, _ = candidate["bounding_rect"]
        candidate["debug_index"] = contour_index
        draw_index_label(debug, str(contour_index), int(x), int(y), color)
        contour_index += 1

        rx, ry, rw, rh = candidate["roi"]
        cv2.rectangle(debug, (rx, ry), (rx + rw, ry + rh), (180, 180, 180), 1)

        bx, by, bw, bh = candidate["bounding_rect"]
        label = f"TG{tg_id} r{candidate['retry']} s={candidate['score']:.2f}"
        cv2.putText(debug, label, (bx, max(15, by - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    debug = cv2.addWeighted(overlay, 0.55, debug, 0.45, 0)

    legend = [
        ("bubble", (0, 180, 0)),
        ("bubble_border", (0, 140, 255)),
        ("panel_or_too_large", (255, 60, 0)),
        ("complex_or_nonbubble", (0, 0, 255)),
        ("points_outside", (0, 255, 255)),
        ("partial_overlap", (0, 120, 255)),
        ("too_small_bbox", (0, 0, 0)),
        ("panel_canvas_rejected", (255, 0, 255)),
    ]
    for idx, (label, color) in enumerate(legend):
        y = 24 + idx * 22
        cv2.rectangle(debug, (12, y - 12), (28, y + 4), color, -1)
        cv2.putText(debug, label, (36, y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    cv2.imwrite(output_path, debug)


def main() -> None:
    if len(sys.argv) not in (4, 5, 7, 8):
        print("Usage: python3 pipeline/bubble_detector.py <image> <groups.json> <output.json> [debug.jpg]")
        print("       python3 pipeline/bubble_detector.py <image> <groups.json> <output.json> <debug.jpg> <scale_x> <scale_y> [image_scale|bbox_scale]")
        sys.exit(1)

    image_path = sys.argv[1]
    groups_path = sys.argv[2]
    output_json_path = sys.argv[3]
    debug_output_path = sys.argv[4] if len(sys.argv) == 5 else None
    roi_scales = None
    roi_mode = "image_scale"
    if len(sys.argv) in (7, 8):
        debug_output_path = sys.argv[4]
        roi_scales = [(float(sys.argv[5]), float(sys.argv[6]))]
    if len(sys.argv) == 8:
        roi_mode = sys.argv[7]
        if roi_mode not in ("image_scale", "bbox_scale"):
            raise ValueError("roi_mode은 image_scale 또는 bbox_scale이어야 합니다.")

    result = detect_bubble_candidates(image_path, groups_path, output_json_path, debug_output_path, roi_scales, roi_mode)
    matched = sum(1 for item in result["bubble_candidates"] if item["best_candidate"])
    total = len(result["bubble_candidates"])
    print(f"✅ Bubble contour 후보 탐색 완료: {matched}/{total} matched")
    print(f"   JSON: {output_json_path}")
    if debug_output_path:
        print(f"   Debug: {debug_output_path}")


if __name__ == "__main__":
    main()
