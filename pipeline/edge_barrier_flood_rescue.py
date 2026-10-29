# 열린 말풍선 경계를 edge barrier 기반 flood로 구제합니다.
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import sys
from pathlib import Path
from pipeline.paths import DATA_HOME
from typing import Any, Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]

try:
    from pipeline.bubble_detector import (
        _bbox_center,
        _compute_flood_bimodality,
        _erode_mask,
        _fill_mask_holes,
        _flood_fill_mask,
    )
except ImportError:
    from bubble_detector import (
        _bbox_center,
        _compute_flood_bimodality,
        _erode_mask,
        _fill_mask_holes,
        _flood_fill_mask,
    )


Point = Tuple[int, int]


DEFAULT_RESCUE_PARAM_GRID = [
    {"roi_pad": 50, "guard_mode": "none", "guard_margin": 80, "guard_thickness": 5},
    {"roi_pad": 50, "guard_mode": "both", "guard_margin": 35, "guard_thickness": 7},
    {"roi_pad": 50, "guard_mode": "both", "guard_margin": 60, "guard_thickness": 7},
    {"roi_pad": 50, "guard_mode": "vertical", "guard_margin": 60, "guard_thickness": 7},
    {"roi_pad": 50, "guard_mode": "all", "guard_margin": 60, "guard_thickness": 7},
]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _as_rect(rect: Iterable[int]) -> List[int]:
    x, y, w, h = [int(v) for v in list(rect)[:4]]
    return [x, y, max(0, w), max(0, h)]


def _union_rect(rects: List[List[int]]) -> List[int]:
    valid = [r for r in rects if len(r) >= 4 and r[2] > 0 and r[3] > 0]
    if not valid:
        return [0, 0, 0, 0]
    x1 = min(r[0] for r in valid)
    y1 = min(r[1] for r in valid)
    x2 = max(r[0] + r[2] for r in valid)
    y2 = max(r[1] + r[3] for r in valid)
    return [x1, y1, x2 - x1, y2 - y1]


def _pad_rect(rect: List[int], pad: int, img_w: int, img_h: int) -> List[int]:
    x, y, w, h = _as_rect(rect)
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img_w, x + w + pad)
    y2 = min(img_h, y + h + pad)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _line_points(item: Dict[str, Any]) -> Optional[Tuple[Point, Point]]:
    raw = item.get("line") or item.get("polyline")
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    try:
        a = raw[0]
        b = raw[-1]
        return (int(a[0]), int(a[1])), (int(b[0]), int(b[1]))
    except (TypeError, ValueError, IndexError):
        return None


def _virtual_wall_rect(virtual_walls: Optional[List[Dict[str, Any]]]) -> List[int]:
    points: List[List[int]] = []
    for item in virtual_walls or []:
        line = _line_points(item)
        if line is None:
            continue
        points.extend([[line[0][0], line[0][1], 1, 1], [line[1][0], line[1][1], 1, 1]])
    return _union_rect(points)


def _seed_rects(seed_points: Optional[List[Point]]) -> List[List[int]]:
    rects: List[List[int]] = []
    for point in seed_points or []:
        try:
            x, y = int(point[0]), int(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        rects.append([x, y, 1, 1])
    return rects


def _draw_virtual_walls(
    edges: np.ndarray,
    virtual_walls: Optional[List[Dict[str, Any]]],
    offset: Point,
    thickness: int,
) -> bool:
    drew = False
    ox, oy = offset
    thick = max(3, int(thickness))
    half = thick // 2 + 1  # 안티에일리어싱으로 번지는 1px까지 박스 안쪽을 비워 둔다
    # 박스를 이루는 가로·세로 벽은 박스 바깥쪽으로 칠한다. 가운데로 칠하면 두께의 절반이 박스 안쪽을 덮어
    # 글자에 바짝 붙인 박스에서는 흰 여백과 채우기 시작점이 벽에 묻혀 검은 영역으로 새어 나간다.
    endpoints = [point for item in virtual_walls or [] for line in [_line_points(item)] if line for point in line]
    if endpoints:
        left, right = min(p[0] for p in endpoints), max(p[0] for p in endpoints)
        top, bottom = min(p[1] for p in endpoints), max(p[1] for p in endpoints)
    else:
        left = right = top = bottom = 0
    has_box = right > left and bottom > top
    for item in virtual_walls or []:
        line = _line_points(item)
        if line is None:
            continue
        (x1, y1), (x2, y2) = line
        if item.get("box_id") and has_box:
            if y1 == y2 and y1 in (top, bottom):
                shift = -half if y1 == top else half
                y1 += shift
                y2 += shift
                x1, x2 = (x1 - half, x2 + half) if x1 <= x2 else (x1 + half, x2 - half)
            elif x1 == x2 and x1 in (left, right):
                shift = -half if x1 == left else half
                x1 += shift
                x2 += shift
                y1, y2 = (y1 - half, y2 + half) if y1 <= y2 else (y1 + half, y2 - half)
        cv2.line(edges, (x1 - ox, y1 - oy), (x2 - ox, y2 - oy), 255, thick, cv2.LINE_AA)
        drew = True
    return drew


def _rect_area(rect: List[int]) -> int:
    return max(0, int(rect[2])) * max(0, int(rect[3]))


def _rect_intersection_area(a: List[int], b: List[int]) -> int:
    ax, ay, aw, ah = _as_rect(a)
    bx, by, bw, bh = _as_rect(b)
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    return max(0, x2 - x1) * max(0, y2 - y1)


def _mask_rect(mask: np.ndarray) -> List[int]:
    """mask > 0 픽셀의 외접 rect [x, y, w, h]. 빈 mask는 [0, 0, 0, 0]."""
    if mask.size == 0:
        return [0, 0, 0, 0]
    binary = mask if mask.dtype == np.uint8 else (mask > 0).astype(np.uint8)
    points = cv2.findNonZero(binary)
    if points is None:
        return [0, 0, 0, 0]
    x, y, w, h = cv2.boundingRect(points)
    return [int(x), int(y), int(w), int(h)]


def _mask_coverage(mask: np.ndarray, rect: List[int], pad: int = 0) -> float:
    img_h, img_w = mask.shape[:2]
    x, y, w, h = _pad_rect(_as_rect(rect), pad, img_w, img_h)
    if w <= 0 or h <= 0:
        return 0.0
    roi = mask[y:y + h, x:x + w]
    return float(np.count_nonzero(roi) / max(1, w * h))


def _center_inside_mask(mask: np.ndarray, rect: List[int]) -> bool:
    cx, cy = _bbox_center(rect)
    if not (0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1]):
        return False
    if mask[cy, cx] > 0:
        return True
    x1, y1 = max(0, cx - 2), max(0, cy - 2)
    x2, y2 = min(mask.shape[1], cx + 3), min(mask.shape[0], cy + 3)
    return bool(np.any(mask[y1:y2, x1:x2] > 0))


def _contour_features(mask: np.ndarray) -> Dict[str, float]:
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {
            "contour_count": 0.0,
            "solidity": 0.0,
            "roundness": 0.0,
            "extent": 0.0,
        }

    cnt = max(contours, key=cv2.contourArea)
    contour_area = float(cv2.contourArea(cnt))
    hull_area = float(cv2.contourArea(cv2.convexHull(cnt)))
    perimeter = float(cv2.arcLength(cnt, True))
    rect = cv2.boundingRect(cnt)
    rect_area = max(1.0, float(rect[2] * rect[3]))
    solidity = contour_area / hull_area if hull_area > 0 else 0.0
    roundness = 4.0 * np.pi * contour_area / (perimeter ** 2) if perimeter > 0 else 0.0
    return {
        "contour_count": float(len(contours)),
        "solidity": float(solidity),
        "roundness": float(roundness),
        "extent": float(contour_area / rect_area),
    }


def _edge_alignment(mask: np.ndarray, edges: np.ndarray) -> float:
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    boundary = np.zeros_like(binary)
    cv2.drawContours(boundary, contours, -1, 255, 2, cv2.LINE_AA)
    boundary = cv2.dilate(boundary, np.ones((5, 5), dtype=np.uint8), iterations=1)
    boundary_count = max(1, int(np.count_nonzero(boundary)))
    return float(np.count_nonzero((boundary > 0) & (edges > 0)) / boundary_count)


def _ratio_window_score(value: float, lo: float, hi: float, hard_hi: float) -> float:
    if value <= 0:
        return 0.0
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, value / lo)
    return max(0.0, 1.0 - ((value - hi) / max(0.001, hard_hi - hi)))


def _text_group_id(group: Dict[str, Any], fallback: int) -> int:
    return int(group.get("text_group_id", group.get("cluster_id", fallback)))


def _target_text_groups(groups: List[Dict[str, Any]], tg_ids: List[int]) -> List[Dict[str, Any]]:
    wanted = set(tg_ids)
    return [group for idx, group in enumerate(groups, start=1) if _text_group_id(group, idx) in wanted]


def _anchor_rect(groups: List[Dict[str, Any]]) -> List[int]:
    rects = [
        _as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0])))
        for group in groups
    ]
    return _union_rect(rects)


_RESCUE_MAX_OUTSIDE_RATIO = 0.20  # TG bbox가 bubble mask 바깥으로 삐져나갈 수 있는 최대 허용 비율 (20% 초과 시 fallback 기각)


def _score_rescue(
    mask: np.ndarray,
    anchor_rect: List[int],
    source_rect: List[int],
    image_area: int,
    edges: Optional[np.ndarray] = None,
    gray: Optional[np.ndarray] = None,
    guard_mode: str = "none",
    guard_margin: int = 80,
    touches_clip: bool = False,
    rect: Optional[List[int]] = None,
) -> Dict[str, float]:
    # 호출자가 이미 구한 mask rect가 있으면 재사용한다.
    if rect is None:
        rect = _mask_rect(mask)
    mask_area = int(np.count_nonzero(mask))
    rect_area = max(1, _rect_area(rect))
    anchor_area = max(1, _rect_area(anchor_rect))
    source_area = max(1, _rect_area(source_rect))
    source_anchor_ratio = float(source_area / anchor_area)
    page_ratio = float(mask_area / max(1, image_area))
    rect_anchor_ratio = float(rect_area / anchor_area)
    rect_source_ratio = float(rect_area / source_area)
    anchor_coverage = _mask_coverage(mask, anchor_rect, pad=3)
    center_inside = _center_inside_mask(mask, anchor_rect)
    features = _contour_features(mask)
    edge_score = _edge_alignment(mask, edges) if edges is not None else 0.0
    bimodality = _compute_flood_bimodality(mask, gray) if gray is not None else 1.0
    bimodality_score = max(0.0, min(1.0, (bimodality - 0.82) / 0.08))

    ratio_score = _ratio_window_score(rect_anchor_ratio, lo=1.15, hi=8.0, hard_hi=22.0)
    source_score = _ratio_window_score(rect_source_ratio, lo=0.05, hi=1.05, hard_hi=1.25)
    page_score = max(0.0, 1.0 - min(page_ratio / 0.12, 1.0))
    shape_score = (
        min(features["solidity"] / 0.88, 1.0) * 0.35
        + min(features["extent"] / 0.62, 1.0) * 0.25
        + min(features["roundness"] / 0.45, 1.0) * 0.20
        + min(edge_score / 0.12, 1.0) * 0.20
    )

    guard_penalty = 0.0
    if guard_mode == "both" and guard_margin <= 40:
        guard_penalty = 0.14
    elif guard_mode != "none":
        guard_penalty = 0.06

    source_is_region_candidate = source_anchor_ratio >= 1.2
    borderline_open_bubble = (
        mask_area >= 300
        and not touches_clip
        and page_ratio <= 0.05
        and 1.0 <= rect_anchor_ratio <= 3.2
        and rect_source_ratio <= 0.9
        and anchor_coverage >= 0.85
        and center_inside
        and features["solidity"] >= 0.86
        and features["extent"] >= 0.62
        and features["roundness"] >= 0.25
        and shape_score >= 0.83
        and bimodality >= 0.88
        and features["contour_count"] <= 1.0
    )

    hard_reject = (
        mask_area < 300
        or touches_clip
        or page_ratio > 0.12
        or rect_anchor_ratio < 0.8
        or rect_anchor_ratio > 8.0
        or (source_is_region_candidate and rect_source_ratio > 3.6)
        or (
            not borderline_open_bubble
            and (
                anchor_coverage < (1.0 - _RESCUE_MAX_OUTSIDE_RATIO)
                or not center_inside
                or features["solidity"] < 0.70
                or shape_score < 0.65
            )
        )
        or features["contour_count"] > 3.0
    )

    rank_score = (
        anchor_coverage * 1.7
        + ratio_score * 1.2
        + shape_score * 1.0
        + bimodality_score * 0.25
        + source_score * 0.7
        + page_score * 0.35
        - guard_penalty
    )
    if hard_reject:
        rank_score -= 10.0

    return {
        "mask_area": float(mask_area),
        "mask_page_ratio": page_ratio,
        "rect_area": float(rect_area),
        "rect_anchor_ratio": rect_anchor_ratio,
        "rect_source_ratio": rect_source_ratio,
        "source_anchor_ratio": source_anchor_ratio,
        "anchor_coverage": float(anchor_coverage),
        "center_inside": 1.0 if center_inside else 0.0,
        "source_overlap_ratio": float(_rect_intersection_area(rect, source_rect) / source_area),
        "edge_alignment": float(edge_score),
        "bimodality": float(bimodality),
        "bimodality_score": float(bimodality_score),
        "solidity": float(features["solidity"]),
        "roundness": float(features["roundness"]),
        "extent": float(features["extent"]),
        "contour_count": float(features["contour_count"]),
        "shape_score": float(shape_score),
        "borderline_open_bubble": 1.0 if borderline_open_bubble else 0.0,
        "ratio_score": float(ratio_score),
        "source_score": float(source_score),
        "page_score": float(page_score),
        "guard_penalty": float(guard_penalty),
        "rank_score": float(rank_score),
        "hard_reject": 1.0 if hard_reject else 0.0,
    }


def _touches_rect_border(mask: np.ndarray, rect: List[int], tolerance: int = 2) -> bool:
    x, y, w, h = _as_rect(rect)
    if w <= 0 or h <= 0:
        return False
    roi = mask[y:y + h, x:x + w]
    if roi.size == 0:
        return False
    return (
        np.any(roi[:tolerance, :] > 0)
        or np.any(roi[-tolerance:, :] > 0)
        or np.any(roi[:, :tolerance] > 0)
        or np.any(roi[:, -tolerance:] > 0)
    )


def _seed_for_rect(image: np.ndarray, gray: np.ndarray, rect: List[int]) -> Optional[Point]:
    """anchor rect 경계 4방향에서 밝은 seed를 탐색한다."""
    x, y, w, h = rect[0], rect[1], rect[2], rect[3]
    ih, iw = gray.shape[:2]
    margin = 2
    candidates: List[Point] = [
        (x + w // 2, max(0, y - margin)),
        (x + w // 2, min(ih - 1, y + h + margin)),
        (max(0, x - margin), y + h // 2),
        (min(iw - 1, x + w + margin), y + h // 2),
    ]
    for threshold in (240, 220, 200, 180):
        for pt in candidates:
            px, py = pt
            if 0 <= py < ih and 0 <= px < iw and gray[py, px] >= threshold:
                return pt
    # fallback: 가장 밝은 후보
    best = max(candidates, key=lambda pt: gray[pt[1], pt[0]] if 0 <= pt[1] < ih and 0 <= pt[0] < iw else 0)
    return best


CONFINED_ROI_PAD = 16


def _block_outside_rect(edges: np.ndarray, rect: List[int]) -> np.ndarray:
    """rect 바깥을 전부 장벽으로 만든다. 채우기가 한계선을 넘을 수 없게 한다."""
    x, y, w, h = _as_rect(rect)
    blocked = np.full_like(edges, 255)
    blocked[max(0, y):max(0, y + h), max(0, x):max(0, x + w)] = edges[max(0, y):max(0, y + h), max(0, x):max(0, x + w)]
    return blocked


def _free_seed_inside_rect(
    seed: Point,
    edges: np.ndarray,
    gray: np.ndarray,
    rect: List[int],
    max_radius: int = 60,
) -> Optional[Point]:
    """시작점이 박스 안의 밝은 비장벽 픽셀이 되게 한다. 없으면 None(박스로 대체한다)."""
    x, y, w, h = _as_rect(rect)
    ih, iw = edges.shape[:2]

    def usable(px: int, py: int, min_gray: int) -> bool:
        return x <= px < x + w and y <= py < y + h and 0 <= px < iw and 0 <= py < ih and edges[py, px] == 0 and gray[py, px] >= min_gray

    sx, sy = seed
    for min_gray in (230, 200, 170):
        if usable(sx, sy, min_gray):
            return (sx, sy)
        for radius in range(1, max_radius + 1):
            best: Optional[Point] = None
            best_dist = float("inf")
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius or not usable(sx + dx, sy + dy, min_gray):
                        continue
                    dist = dx * dx + dy * dy
                    if dist < best_dist:
                        best, best_dist = (sx + dx, sy + dy), dist
            if best is not None:
                return best
    return None


def _connected_component_at_seed(mask: np.ndarray, seed: Point, fallback_center: Point) -> np.ndarray:
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(np.where(mask > 0, 255, 0).astype(np.uint8))
    if n_labels <= 1:
        return np.zeros_like(mask)

    sx, sy = seed
    seed_label = labels[sy, sx] if 0 <= sy < labels.shape[0] and 0 <= sx < labels.shape[1] else 0
    if seed_label == 0:
        best_label = 1
        best_dist = float("inf")
        cx, cy = fallback_center
        for label in range(1, n_labels):
            lx = stats[label, cv2.CC_STAT_LEFT] + stats[label, cv2.CC_STAT_WIDTH] / 2
            ly = stats[label, cv2.CC_STAT_TOP] + stats[label, cv2.CC_STAT_HEIGHT] / 2
            dist = (lx - cx) ** 2 + (ly - cy) ** 2
            if dist < best_dist:
                best_dist = dist
                best_label = label
        seed_label = best_label

    return np.where(labels == seed_label, 255, 0).astype(np.uint8)


def _barrier_edges(gray: np.ndarray, anchor_rect: List[int], cfg: Dict[str, Any]) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, cfg["canny_lo"], cfg["canny_hi"])

    x, y, w, h = _as_rect(anchor_rect)
    pad = int(cfg.get("clear_anchor_pad", 5))
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(gray.shape[1], x + w + pad), min(gray.shape[0], y + h + pad)
    edges[y1:y2, x1:x2] = 0

    dilate_px = int(cfg.get("dilate_px", 1))
    if dilate_px > 0:
        kernel = np.ones((dilate_px * 2 + 1, dilate_px * 2 + 1), dtype=np.uint8)
        edges = cv2.dilate(edges, kernel, iterations=1)
    return edges


def _add_synthetic_guards(
    edges: np.ndarray,
    anchor_rect: List[int],
    clip_rect: List[int],
    mode: str,
    margin: int,
    thickness: int,
) -> np.ndarray:
    """TG 주변에 가상의 barrier를 추가합니다."""
    if mode == "none":
        return edges

    guarded = edges.copy()
    ax, ay, aw, ah = _as_rect(anchor_rect)
    cx, cy, cw, ch = _as_rect(clip_rect)
    y1, y2 = cy, cy + ch
    x1, x2 = cx, cx + cw

    if mode in {"left", "both", "all"}:
        lx = max(0, ax - margin)
        cv2.line(guarded, (lx, y1), (lx, y2), 255, thickness)
    if mode in {"right", "both", "all"}:
        rx = min(edges.shape[1] - 1, ax + aw + margin)
        cv2.line(guarded, (rx, y1), (rx, y2), 255, thickness)
    if mode in {"top", "vertical", "all"}:
        ty = max(0, ay - margin)
        cv2.line(guarded, (x1, ty), (x2, ty), 255, thickness)
    if mode in {"bottom", "vertical", "all"}:
        by = min(edges.shape[0] - 1, ay + ah + margin)
        cv2.line(guarded, (x1, by), (x2, by), 255, thickness)
    return guarded


def run_edge_barrier_rescue(
    image: np.ndarray,
    groups: List[Dict[str, Any]],
    source_region_rect: List[int],
    roi_pad: int = 20,
    max_page_ratio: float = 0.12,
    max_source_ratio: float = 3.6,
    guard_mode: str = "none",
    guard_margin: int = 80,
    guard_thickness: int = 5,
    extra_dilate_px: Optional[int] = None,
    virtual_walls: Optional[List[Dict[str, Any]]] = None,
    seed_points: Optional[List[Point]] = None,
    manual_guided: bool = False,
    confine_to_walls: bool = False,
) -> Dict[str, Any]:
    """bbox fallback region을 edge barrier flood로 다시 시도합니다.

    confine_to_walls: 수동 Edge box는 사용자가 그은 한계선이다. 박스 바깥을 전부 장벽으로 막아 채우기가 박스를
    넘지 못하게 하고, 안쪽으로 줄이는 침식도 하지 않는다(설계: 새는 길에 벽을 세워 거기까지만 채운다).
    """
    img_h, img_w = image.shape[:2]
    image_area = img_h * img_w
    anchor_rect = _anchor_rect(groups)
    wall_rect = _virtual_wall_rect(virtual_walls)
    seed_rect = _union_rect(_seed_rects(seed_points))
    if _rect_area(source_region_rect) > 0:
        clip_base_rect = _union_rect([source_region_rect, anchor_rect, wall_rect, seed_rect])
    else:
        clip_base_rect = _union_rect([anchor_rect, wall_rect, seed_rect])
    has_virtual_walls = _rect_area(wall_rect) > 0
    confine_to_walls = bool(confine_to_walls and has_virtual_walls)
    # 박스에 가둘 때는 바깥쪽으로 칠하는 벽이 들어갈 만큼만 ROI를 잡는다
    clip_rect = _pad_rect(wall_rect if confine_to_walls else clip_base_rect, CONFINED_ROI_PAD if confine_to_walls else roi_pad, img_w, img_h)
    manual_guided = bool(manual_guided or has_virtual_walls or seed_points)

    # ROI 크롭 후 모든 연산을 ROI 공간에서 수행한다.
    cx, cy, cw, ch = _as_rect(clip_rect)
    if cw <= 0 or ch <= 0:
        return {
            "success": False,
            "reason": "empty_roi",
            "anchor_rect": anchor_rect,
            "source_region_rect": source_region_rect,
            "clip_rect": clip_rect,
        }
    roi_img = image[cy:cy + ch, cx:cx + cw]
    roi_gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)

    def to_local(rect: List[int]) -> List[int]:
        x, y, w, h = _as_rect(rect)
        return [x - cx, y - cy, w, h]

    anchor_local = to_local(anchor_rect)
    source_local = to_local(source_region_rect)
    clip_local = [0, 0, cw, ch]

    seed_local = None
    for seed in seed_points or []:
        try:
            sx, sy = int(seed[0]) - cx, int(seed[1]) - cy
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= sx < cw and 0 <= sy < ch:
            seed_local = (sx, sy)
            break
    if seed_local is None:
        seed_local = _seed_for_rect(roi_img, roi_gray, anchor_local)
    if seed_local is None:
        return {"success": False, "reason": "no_seed", "anchor_rect": anchor_rect, "source_region_rect": source_region_rect}
    seed_full = (seed_local[0] + cx, seed_local[1] + cy)
    seed_local_pt = seed_local
    if not (0 <= seed_local_pt[0] < cw and 0 <= seed_local_pt[1] < ch):
        return {
            "success": False,
            "reason": "seed_outside_roi",
            "anchor_rect": anchor_rect,
            "source_region_rect": source_region_rect,
            "clip_rect": clip_rect,
            "seed": list(seed_full),
        }
    center_local = _bbox_center(anchor_local)

    configs = [
        {"label": "soft", "canny_lo": 30, "canny_hi": 100, "dilate_px": 2, "clear_anchor_pad": 5},
        {"label": "mid", "canny_lo": 50, "canny_hi": 150, "dilate_px": 2, "clear_anchor_pad": 5},
        {"label": "hard", "canny_lo": 80, "canny_hi": 200, "dilate_px": 1, "clear_anchor_pad": 5},
    ]
    if extra_dilate_px is not None:
        configs.append({
            "label": f"guard_dilate{extra_dilate_px}",
            "canny_lo": 35,
            "canny_hi": 110,
            "dilate_px": extra_dilate_px,
            "clear_anchor_pad": 5,
        })

    attempts: List[Dict[str, Any]] = []
    best: Optional[Dict[str, Any]] = None
    for cfg in configs:
        # ROI 안에서 Canny + guard — 전체 이미지 대신 ROI gray만 사용
        base_edges = _barrier_edges(roi_gray, anchor_local, cfg)
        edges = _add_synthetic_guards(
            base_edges,
            anchor_local,
            clip_local,
            mode=guard_mode,
            margin=guard_margin,
            thickness=guard_thickness,
        )
        if has_virtual_walls:
            _draw_virtual_walls(edges, virtual_walls, (cx, cy), max(guard_thickness, 9))
        flood_seed = seed_local_pt
        if confine_to_walls:
            box_local = to_local(wall_rect)
            edges = _block_outside_rect(edges, box_local)
            flood_seed = _free_seed_inside_rect(seed_local_pt, edges, roi_gray, box_local)
        barrier = roi_img.copy()
        barrier[edges > 0] = (0, 0, 0)
        if flood_seed is None:
            mask = np.zeros(roi_gray.shape, dtype=np.uint8)
            flood_seed = seed_local_pt
        else:
            mask, _, _ = _flood_fill_mask(barrier, flood_seed)
            mask = _connected_component_at_seed(mask, flood_seed, center_local)
        filled = _fill_mask_holes(mask)
        inner = _erode_mask(filled, pixels=0 if confine_to_walls else 3)
        inner = _connected_component_at_seed(inner, flood_seed, center_local)

        touches_clip = _touches_rect_border(inner, clip_local)
        local_rescue_rect = _mask_rect(inner)
        score = _score_rescue(
            inner,
            anchor_local,
            source_local,
            image_area,
            edges=base_edges,
            gray=roi_gray,
            guard_mode=guard_mode,
            guard_margin=guard_margin,
            touches_clip=touches_clip,
            rect=local_rescue_rect,
        )
        if manual_guided:
            ok = (
                int(score.get("mask_area", 0.0)) > 64
                and float(score.get("anchor_coverage", 0.0)) >= 0.20
                and float(score.get("mask_page_ratio", 1.0)) <= max(max_page_ratio, 0.24)
            )
        else:
            ok = (
                score["hard_reject"] == 0.0
                and score["mask_page_ratio"] <= max_page_ratio
                and score["rect_source_ratio"] <= max_source_ratio
            )

        # 로컬 rescue_rect를 full-image 좌표로 복원
        rescue_rect_full = [
            local_rescue_rect[0] + cx,
            local_rescue_rect[1] + cy,
            local_rescue_rect[2],
            local_rescue_rect[3],
        ]

        # mask/edges는 ROI 로컬 배열로 들고 있다가, 최종 best만 full-image 크기로 복원한다.
        attempt = {
            "label": cfg["label"],
            "ok": ok,
            "seed": list(seed_full),
            "rescue_rect": rescue_rect_full,
            "clip_rect": clip_rect,
            "anchor_rect": anchor_rect,
            "score": score,
            "touches_clip": touches_clip,
            "manual_guided": manual_guided,
            "mask": inner,
            "edges": edges,
        }
        attempts.append(attempt)
        if ok and (best is None or score["rank_score"] > best["score"]["rank_score"]):
            best = attempt

    if best is None:
        best = max(
            attempts,
            key=lambda item: item["score"]["rank_score"],
        ) if attempts else None

    def _to_full_image(local: np.ndarray) -> np.ndarray:
        full = np.zeros((img_h, img_w), dtype=np.uint8)
        full[cy:cy + ch, cx:cx + cw] = local
        return full

    return {
        "success": bool(best and best["ok"]),
        "reason": "edge_barrier_ok" if best and best["ok"] else "edge_barrier_no_safe_mask",
        "best_label": best["label"] if best else None,
        "anchor_rect": anchor_rect,
        "source_region_rect": source_region_rect,
        "clip_rect": clip_rect,
        "guard_mode": guard_mode,
        "guard_margin": guard_margin,
        "guard_thickness": guard_thickness,
        "roi_pad": roi_pad,
        "manual_guided": manual_guided,
        "virtual_wall_count": len(virtual_walls or []),
        "seed": best["seed"] if best else None,
        "rescue_rect": best["rescue_rect"] if best else [0, 0, 0, 0],
        "score": best["score"] if best else {},
        "attempts": [
            {k: v for k, v in attempt.items() if k not in {"mask", "edges"}}
            for attempt in attempts
        ],
        "mask": _to_full_image(best["mask"]) if best else np.zeros(image.shape[:2], dtype=np.uint8),
        "edges": _to_full_image(best["edges"]) if best else np.zeros(image.shape[:2], dtype=np.uint8),
    }


def _run_edge_barrier_rescue_auto_strict(
    image: np.ndarray,
    groups: List[Dict[str, Any]],
    source_region_rect: List[int],
    param_grid: Optional[List[Dict[str, Any]]] = None,
    virtual_walls: Optional[List[Dict[str, Any]]] = None,
    seed_points: Optional[List[Point]] = None,
) -> Dict[str, Any]:
    """관찰 케이스 기준의 파라미터 후보군을 돌려 가장 안정적인 rescue를 고릅니다."""
    candidates: List[Dict[str, Any]] = []
    confine = _virtual_wall_rect(virtual_walls)[2] > 0 and _virtual_wall_rect(virtual_walls)[3] > 0
    # 수동 Edge box는 박스가 한계선이다. 앵커 주변에 가상 가드를 더하거나 ROI를 바꿔 보는 후보는 쓰지 않는다
    grid = [{"guard_mode": "none"}] if confine else (param_grid or DEFAULT_RESCUE_PARAM_GRID)
    for params in grid:
        result = run_edge_barrier_rescue(
            image,
            groups,
            source_region_rect,
            roi_pad=int(params.get("roi_pad", 20)),
            guard_mode=str(params.get("guard_mode", "none")),
            guard_margin=int(params.get("guard_margin", 80)),
            guard_thickness=int(params.get("guard_thickness", 5)),
            extra_dilate_px=params.get("extra_dilate_px"),
            virtual_walls=virtual_walls,
            seed_points=seed_points,
            manual_guided=bool(virtual_walls or seed_points),
            confine_to_walls=confine,
            max_page_ratio=0.24 if (virtual_walls or seed_points) else 0.12,
            max_source_ratio=12.0 if (virtual_walls or seed_points) else 3.6,
        )
        candidates.append(result)

    successful = [item for item in candidates if item.get("success")]
    pool = successful or candidates
    best = max(
        pool,
        key=lambda item: (
            float(item.get("score", {}).get("rank_score", -99.0)),
            1 if item.get("guard_mode") == "none" else 0,
        ),
    ) if pool else {
        "success": False,
        "reason": "edge_barrier_no_candidate",
        "mask": np.zeros(image.shape[:2], dtype=np.uint8),
        "edges": np.zeros(image.shape[:2], dtype=np.uint8),
        "score": {},
        "rescue_rect": [0, 0, 0, 0],
    }

    result = dict(best)
    result["success"] = bool(successful and best.get("success"))
    result["reason"] = "edge_barrier_auto_ok" if result["success"] else "edge_barrier_auto_no_safe_mask"
    result["candidates"] = [
        {
            k: v for k, v in candidate.items()
            if k not in {"mask", "edges", "attempts"}
        }
        for candidate in candidates
    ]
    return result


def _is_open_overlap_bubble_rescue(result: Dict[str, Any], group_count: int) -> bool:
    """효과음/열린 경계 때문에 기존 hard gate가 버린 multi-TG 말풍선을 구제한다."""
    if result.get("success") or group_count < 2:
        return False
    score = result.get("score", {})
    if not score or float(score.get("hard_reject", 0.0)) == 0.0:
        return False
    return (
        float(score.get("mask_page_ratio", 1.0)) <= 0.055
        and 1.2 <= float(score.get("rect_anchor_ratio", 0.0)) <= 4.2
        and float(score.get("rect_source_ratio", 1.0)) <= 0.70
        and float(score.get("anchor_coverage", 0.0)) >= 0.82
        and float(score.get("center_inside", 0.0)) >= 1.0
        and float(score.get("bimodality", 0.0)) >= 0.92
        and float(score.get("solidity", 0.0)) >= 0.74
        and float(score.get("extent", 0.0)) >= 0.52
        and float(score.get("roundness", 0.0)) >= 0.30
        and float(score.get("shape_score", 0.0)) >= 0.76
    )


def run_edge_barrier_rescue_auto(
    image: np.ndarray,
    groups: List[Dict[str, Any]],
    source_region_rect: List[int],
    param_grid: Optional[List[Dict[str, Any]]] = None,
    virtual_walls: Optional[List[Dict[str, Any]]] = None,
    seed_points: Optional[List[Point]] = None,
) -> Dict[str, Any]:
    result = _run_edge_barrier_rescue_auto_strict(
        image,
        groups,
        source_region_rect,
        param_grid=param_grid,
        virtual_walls=virtual_walls,
        seed_points=seed_points,
    )
    if _is_open_overlap_bubble_rescue(result, len(groups)):
        result = dict(result)
        result["success"] = True
        result["reason"] = "edge_barrier_auto_open_overlap_ok"
        result["open_overlap_relaxed"] = True
    return result


def _draw_rescue_debug(image: np.ndarray, result: Dict[str, Any]) -> np.ndarray:
    vis = image.copy()
    mask = result["mask"]
    if np.any(mask > 0):
        overlay = vis.copy()
        overlay[mask > 0] = (70, 210, 255)
        vis = cv2.addWeighted(overlay, 0.45, vis, 0.55, 0)
        contours, _ = cv2.findContours(np.where(mask > 0, 255, 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(vis, contours, -1, (0, 120, 255), 2, cv2.LINE_AA)

    for rect, color, label in [
        (result.get("source_region_rect", [0, 0, 0, 0]), (0, 0, 255), "fallback"),
        (result.get("clip_rect", [0, 0, 0, 0]), (255, 120, 0), "clip"),
        (result.get("anchor_rect", [0, 0, 0, 0]), (0, 220, 255), "TG"),
        (result.get("rescue_rect", [0, 0, 0, 0]), (0, 180, 0), "rescue"),
    ]:
        x, y, w, h = _as_rect(rect)
        if w <= 0 or h <= 0:
            continue
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, 2, cv2.LINE_AA)
        cv2.putText(vis, label, (x, max(16, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    if result.get("seed"):
        cv2.circle(vis, tuple(result["seed"]), 5, (255, 0, 0), -1)
    title = f"{result.get('reason')} best={result.get('best_label')}"
    cv2.putText(vis, title, (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(vis, title, (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    return vis


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def run_case(
    img_num: str,
    tg_ids: List[int],
    region_id: Optional[int],
    out_dir: Path,
    roi_pad: int = 80,
    guard_mode: str = "none",
    guard_margin: int = 80,
    guard_thickness: int = 5,
    extra_dilate_px: Optional[int] = None,
) -> Dict[str, Any]:
    image_path = DATA_HOME / "samples/test_image" / f"{img_num}.jpg"
    groups_path = ROOT_DIR / "experiment" / "proof" / "json" / f"{img_num}_text_groups.json"
    regions_path = ROOT_DIR / "experiment" / "proof" / "json" / f"{img_num}_bubble_regions.json"

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    groups = _target_text_groups(_load_json(groups_path), tg_ids)
    if not groups:
        raise ValueError(f"대상 TextGroup이 없습니다. {tg_ids}")

    source_region_rect = _anchor_rect(groups)
    if region_id is not None and regions_path.exists():
        regions = _load_json(regions_path)
        for region in regions.get("regions", []):
            if int(region.get("region_id", -1)) == region_id:
                source_region_rect = _as_rect(region.get("bubble_rect", source_region_rect))
                break

    result = run_edge_barrier_rescue(
        image,
        groups,
        source_region_rect,
        roi_pad=roi_pad,
        guard_mode=guard_mode,
        guard_margin=guard_margin,
        guard_thickness=guard_thickness,
        extra_dilate_px=extra_dilate_px,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = (
        f"{img_num}_R{region_id if region_id is not None else 0:02d}_"
        f"TG{'_'.join(str(i) for i in tg_ids)}_pad{roi_pad}_g{guard_mode}_m{guard_margin}"
    )
    debug_path = out_dir / f"{suffix}_edge_barrier_rescue.jpg"
    edge_path = out_dir / f"{suffix}_edges.jpg"
    cv2.imwrite(str(debug_path), _draw_rescue_debug(image, result))
    cv2.imwrite(str(edge_path), result["edges"])

    serializable = _json_safe({k: v for k, v in result.items() if k not in {"mask", "edges"}})
    serializable["image"] = str(image_path)
    serializable["text_groups"] = tg_ids
    serializable["source_region_id"] = region_id
    serializable["debug_output"] = str(debug_path)
    serializable["edge_output"] = str(edge_path)
    json_path = out_dir / f"{suffix}_edge_barrier_rescue.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(json.dumps(serializable, ensure_ascii=False, indent=2))
    return serializable


def main() -> None:
    parser = argparse.ArgumentParser(description="edge barrier flood rescue 실험을 실행합니다.")
    parser.add_argument("img_num")
    parser.add_argument("--tg-id", type=int, action="append", required=True)
    parser.add_argument("--region-id", type=int)
    parser.add_argument("--out-dir", default="experiment/proof/edge_barrier_rescue")
    parser.add_argument("--roi-pad", type=int, default=80)
    parser.add_argument("--guard-mode", choices=["none", "left", "right", "both", "top", "bottom", "vertical", "all"], default="none")
    parser.add_argument("--guard-margin", type=int, default=80)
    parser.add_argument("--guard-thickness", type=int, default=5)
    parser.add_argument("--extra-dilate-px", type=int)
    args = parser.parse_args()
    run_case(
        args.img_num,
        args.tg_id,
        args.region_id,
        Path(args.out_dir),
        roi_pad=args.roi_pad,
        guard_mode=args.guard_mode,
        guard_margin=args.guard_margin,
        guard_thickness=args.guard_thickness,
        extra_dilate_px=args.extra_dilate_px,
    )


if __name__ == "__main__":
    main()
