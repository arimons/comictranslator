# Flood region 분리 후보를 평가하고 split mask를 생성하는 모듈
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


Point = Tuple[int, int]
MIN_SPLIT_SUBREGION_AREA = 900
# split line이 anchor bbox 면적을 침범할 수 있는 최대 허용 비율 (8%)
_SPLIT_MAX_CUT_RATIO = 0.08
_GEOMETRY_SPLIT_AREA_RATIO = 0.0004
_GEOMETRY_SPLIT_MIN_AREA_FLOOR = 900
_GEOMETRY_SPLIT_MIN_AREA_CEIL = 2500
_GEOMETRY_SPLIT_MAX_LINE_SHORT_SIDE_RATIO = 0.85
_GEOMETRY_SPLIT_CENTROID_REJECT_RATIO = 0.08
_GEOMETRY_SPLIT_CENTROID_LONG_LINE_RATIO = 0.65
# depth는 미세 노이즈만 거르고, 실제 후보 품질은 turn angle과 split geometry로 판단한다.
_MIN_DEFECT_DEPTH_FLOOR = 5.0
_MIN_DEFECT_TURN_ANGLE = 35.0
_MIN_SINGLE_TG_WEAK_TURN_ANGLE = 30.0
_MAX_DEFECT_PAIR_POINTS = 10
_MAX_RECURSIVE_DEFECT_PAIR_POINTS = 10
_MAX_RECURSIVE_NODE_CANDIDATES = 12
_MAX_SPLIT_RECURSION_DEPTH = 3
_LOCAL_TURN_NMS_DISTANCE = 18.0
_RECURSIVE_USED_POINT_DISTANCE = 14.0
# mask bounding rect 경계로부터 이 거리 이내의 defect point는 자동 탈락 (직사각형 모서리 필터)
_BOUNDARY_FILTER_TOL = 6


def _preferred_anchor_partition(
    anchor_centers: List[Point],
) -> Optional[Tuple[frozenset, frozenset]]:
    """anchor center 간 거리 기반으로 가장 자연스러운 2-그룹 분할을 반환한다.

    가장 가까운 anchor 쌍을 같은 그룹으로 묶고 나머지를 반대 그룹으로 분류한다.
    anchor가 2개이면 None 반환 (trivial split, 선호 파티션 불필요).
    """
    n = len(anchor_centers)
    if n < 3:
        return None

    min_dist = float("inf")
    ci, cj = 0, 1
    for i in range(n):
        for j in range(i + 1, n):
            dx = anchor_centers[i][0] - anchor_centers[j][0]
            dy = anchor_centers[i][1] - anchor_centers[j][1]
            d = dx * dx + dy * dy
            if d < min_dist:
                min_dist = d
                ci, cj = i, j

    group_a = frozenset([ci, cj])
    group_b = frozenset(range(n)) - group_a
    return (group_a, group_b)


def bbox_intersection_area(a: List[int], b: List[int]) -> int:
    ax, ay, aw, ah = [int(v) for v in a[:4]]
    bx, by, bw, bh = [int(v) for v in b[:4]]
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    return max(0, x2 - x1) * max(0, y2 - y1)


def all_anchor_rects_are_disjoint(anchor_rects: List[List[int]]) -> bool:
    for i, rect_a in enumerate(anchor_rects):
        for rect_b in anchor_rects[i + 1:]:
            if bbox_intersection_area(rect_a, rect_b) > 0:
                return False
    return True


def _mask_contains_points(
    candidate_mask: np.ndarray,
    points: List[Point],
    radius: int = 2,
) -> bool:
    h, w = candidate_mask.shape[:2]
    for px, py in points:
        x1, x2 = max(0, px - radius), min(w, px + radius + 1)
        y1, y2 = max(0, py - radius), min(h, py + radius + 1)
        if x1 >= x2 or y1 >= y2:
            return False
        if not np.any(candidate_mask[y1:y2, x1:x2]):
            return False
    return True


def _line_side(pt: Point, p1: Point, p2: Point) -> float:
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    return float((-dy) * (pt[0] - p1[0]) + dx * (pt[1] - p1[1]))


def _mask_centroid(binary_mask: np.ndarray) -> Optional[Tuple[float, float]]:
    moments = cv2.moments(np.where(binary_mask > 0, 255, 0).astype(np.uint8))
    if moments["m00"] <= 0:
        return None
    return (float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"]))


def _mask_bbox(binary_mask: np.ndarray) -> Optional[List[int]]:
    ys, xs = np.where(binary_mask > 0)
    if len(xs) == 0:
        return None
    return [
        int(xs.min()),
        int(ys.min()),
        int(xs.max() - xs.min() + 1),
        int(ys.max() - ys.min() + 1),
    ]


def _adaptive_min_split_area(image_area: int) -> int:
    return int(
        np.clip(
            image_area * _GEOMETRY_SPLIT_AREA_RATIO,
            _GEOMETRY_SPLIT_MIN_AREA_FLOOR,
            _GEOMETRY_SPLIT_MIN_AREA_CEIL,
        )
    )


def _point_segment_distance(pt: Tuple[float, float], p1: Point, p2: Point) -> float:
    px, py = pt
    x1, y1 = p1
    x2, y2 = p2
    dx = float(x2 - x1)
    dy = float(y2 - y1)
    denom = dx * dx + dy * dy
    if denom <= 1e-6:
        return float(np.hypot(px - x1, py - y1))
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denom))
    qx = x1 + t * dx
    qy = y1 + t * dy
    return float(np.hypot(px - qx, py - qy))


def _nearest_contour_index(contour_points: np.ndarray, pt: Point) -> int:
    deltas = contour_points.astype(np.float32) - np.array(pt, dtype=np.float32)
    distances = deltas[:, 0] * deltas[:, 0] + deltas[:, 1] * deltas[:, 1]
    return int(np.argmin(distances))


def _contour_turn_angle(contour: np.ndarray, pt: Point, step: int = 8) -> Optional[float]:
    """contour의 N-step, N, N+step 방향 변화 각도를 계산한다."""
    contour_points = contour[:, 0, :]
    n_points = len(contour_points)
    if n_points < step * 2 + 1:
        return None

    idx = _nearest_contour_index(contour_points, pt)
    p0 = contour_points[(idx - step) % n_points].astype(np.float32)
    p1 = contour_points[idx].astype(np.float32)
    p2 = contour_points[(idx + step) % n_points].astype(np.float32)
    v1 = p1 - p0
    v2 = p2 - p1
    norm1 = float(np.linalg.norm(v1))
    norm2 = float(np.linalg.norm(v2))
    if norm1 < 1e-6 or norm2 < 1e-6:
        return None

    cosv = float(np.clip(np.dot(v1, v2) / (norm1 * norm2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosv)))


def _extract_defect_points(
    contour: np.ndarray,
    anchor_count: int,
    min_defect_depth: float,
    allow_single_weak_turn: bool = True,
) -> tuple[np.ndarray, list[tuple[int, float, Point, float]]]:
    approx = cv2.approxPolyDP(contour, epsilon=2.0, closed=True)
    if len(approx) < 4:
        return approx, []

    hull_idx = cv2.convexHull(approx, returnPoints=False)
    if hull_idx is None or len(hull_idx) < 3:
        return approx, []

    try:
        defects = cv2.convexityDefects(approx, hull_idx)
    except cv2.error:
        return approx, []

    if defects is None:
        return approx, []

    effective_min_depth = min(_MIN_DEFECT_DEPTH_FLOOR, min_defect_depth)
    min_turn_angle = (
        _MIN_SINGLE_TG_WEAK_TURN_ANGLE
        if anchor_count == 1 and allow_single_weak_turn
        else _MIN_DEFECT_TURN_ANGLE
    )
    defect_points: list[tuple[int, float, Point, float]] = []
    defect_far_indices: set[int] = set()
    for d in defects:
        far_idx = int(d[0][2])
        depth = float(d[0][3]) / 256.0
        if depth < effective_min_depth:
            continue
        pt = tuple(int(v) for v in approx[far_idx][0])
        turn_angle = _contour_turn_angle(contour, pt, step=8)
        if turn_angle is None or turn_angle < min_turn_angle:
            continue
        defect_points.append((far_idx, depth, pt, turn_angle))
        defect_far_indices.add(far_idx)

    contour_points = contour[:, 0, :]
    if len(contour_points) >= 20:
        local_turn_points: list[tuple[int, float, Point, float]] = []
        for idx, raw_pt in enumerate(contour_points):
            if idx in defect_far_indices:
                continue
            pt = tuple(int(v) for v in raw_pt)
            turn_angle = _contour_turn_angle(contour, pt, step=8)
            if turn_angle is None or turn_angle < min_turn_angle:
                continue
            local_turn_points.append((-idx - 1, turn_angle, pt, turn_angle))

        local_turn_points.sort(key=lambda item: item[3], reverse=True)
        kept_local_turns: list[tuple[int, float, Point, float]] = []
        existing_points = [pt for _, _, pt, _ in defect_points]
        for item in local_turn_points:
            _, _, pt, _ = item
            if any(float(np.hypot(pt[0] - ex[0], pt[1] - ex[1])) < _LOCAL_TURN_NMS_DISTANCE for ex in existing_points):
                continue
            kept_local_turns.append(item)
            existing_points.append(pt)
            if len(kept_local_turns) >= _MAX_RECURSIVE_DEFECT_PAIR_POINTS:
                break

        defect_points.extend(kept_local_turns)
    defect_points.sort(key=lambda item: max(item[1], item[3]), reverse=True)
    return approx, defect_points


def _defect_point_source(point_index: int) -> str:
    return "local_turn" if point_index < 0 else "defect"


def collect_inflection_candidates(
    mask: np.ndarray,
    anchor_centers: List[Point],
    min_defect_depth: float = 15.0,
    anchor_rects: Optional[List[List[int]]] = None,
    allow_single_weak_turn: bool = True,
    max_pair_points: int = _MAX_DEFECT_PAIR_POINTS,
) -> Dict[str, Any]:
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return {"candidates": [], "selected": None, "defect_points": []}

    contour = max(contours, key=cv2.contourArea)
    _, defect_points = _extract_defect_points(
        contour,
        len(anchor_centers),
        min_defect_depth,
        allow_single_weak_turn=allow_single_weak_turn,
    )
    # boundary filter: mask bounding rect 경계 위의 defect point 자동 탈락
    # 직사각형 bubble 모서리에 찍히는 hull defect 제거용
    ys_nz, xs_nz = np.where(binary > 0)
    if len(xs_nz) > 0:
        bx_min_f, bx_max_f = int(xs_nz.min()), int(xs_nz.max())
        by_min_f, by_max_f = int(ys_nz.min()), int(ys_nz.max())
        tol = _BOUNDARY_FILTER_TOL
        defect_points = [
            item for item in defect_points
            if not (
                item[2][0] <= bx_min_f + tol
                or item[2][0] >= bx_max_f - tol
                or item[2][1] <= by_min_f + tol
                or item[2][1] >= by_max_f - tol
            )
        ]

    if len(defect_points) < 2:
        return {
            "candidates": [],
            "selected": None,
            "defect_points": [
                {"pt": list(pt), "depth": round(depth, 2), "turn_angle": round(turn_angle, 2)}
                for _, depth, pt, turn_angle in defect_points
            ],
        }

    h, w = binary.shape[:2]
    ys_g, xs_g = np.mgrid[0:h, 0:w]
    total_area = max(1, int(np.count_nonzero(binary)))
    candidates: list[dict[str, Any]] = []
    selected: Optional[dict[str, Any]] = None
    preferred_selected: Optional[dict[str, Any]] = None  # 선호 파티션과 일치하는 최고 후보

    preferred_partition = _preferred_anchor_partition(anchor_centers)

    pair_points = defect_points[:max_pair_points]
    for i, (idx1, depth1, p1, turn1) in enumerate(pair_points):
        for idx2, depth2, p2, turn2 in pair_points[i + 1:]:
            if (
                len(anchor_centers) == 1
                and allow_single_weak_turn
                and max(turn1, turn2) < _MIN_DEFECT_TURN_ANGLE
            ):
                continue
            length = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
            if length < 5:
                continue

            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            sides = [_line_side(ac, p1, p2) for ac in anchor_centers]
            pos_count = sum(1 for side in sides if side >= 0)
            neg_count = len(sides) - pos_count
            separates = pos_count > 0 and neg_count > 0
            pos_indices = [idx for idx, side in enumerate(sides) if side >= 0]
            neg_indices = [idx for idx, side in enumerate(sides) if side < 0]

            # 선호 파티션 일치 여부 (3개 이상 anchor일 때만 유효)
            partition_preferred = False
            if preferred_partition is not None and separates:
                pos_set = frozenset(pos_indices)
                neg_set = frozenset(neg_indices)
                partition_preferred = (
                    (pos_set == preferred_partition[0] and neg_set == preferred_partition[1]) or
                    (pos_set == preferred_partition[1] and neg_set == preferred_partition[0])
                )

            side_map = (-dy) * (xs_g - p1[0]) + dx * (ys_g - p1[1])
            mask_pos = (binary > 0) & (side_map >= 0)
            mask_neg = (binary > 0) & (side_map < 0)
            pos_centers = [ac for ac, side in zip(anchor_centers, sides) if side >= 0]
            neg_centers = [ac for ac, side in zip(anchor_centers, sides) if side < 0]
            centers_inside = (
                separates
                and _mask_contains_points(mask_pos, pos_centers)
                and _mask_contains_points(mask_neg, neg_centers)
            )

            area_pos = int(np.count_nonzero(mask_pos))
            area_neg = int(np.count_nonzero(mask_neg))
            min_area_ratio = min(area_pos, area_neg) / total_area
            area_balance = min(area_pos, area_neg) / max(area_pos, area_neg, 1)
            cut_component = _line_cut_component_stats(binary, p1, p2, anchor_centers)
            component_separates = bool(cut_component.get("separates"))
            component_min_area = int(cut_component.get("min_area", 0))
            component_min_area_ratio = float(cut_component.get("min_area_ratio", 0.0))

            line_mask = np.zeros_like(binary)
            cv2.line(line_mask, p1, p2, 255, 3)
            cut_cost = int(np.count_nonzero((line_mask > 0) & (binary > 0))) / length
            separation = min(abs(side) for side in sides) / length if separates else 0.0
            min_anchor_line_distance = separation if separates else 0.0
            mean_anchor_line_distance = (
                sum(abs(side) / length for side in sides) / max(1, len(sides))
                if separates
                else 0.0
            )

            # 각 anchor bbox가 할당된 sub-mask에 얼마나 포함되는지 측정 (주 선택 기준)
            anchor_coverage = 0.0
            if anchor_rects:
                total_cov = 0.0
                for rect, side in zip(anchor_rects, sides):
                    sub = mask_pos if side >= 0 else mask_neg
                    rx, ry, rw, rh = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
                    x1c = max(0, rx); x2c = min(w, rx + rw)
                    y1c = max(0, ry); y2c = min(h, ry + rh)
                    if x1c < x2c and y1c < y2c:
                        bbox_area = (x2c - x1c) * (y2c - y1c)
                        covered = int(np.count_nonzero(sub[y1c:y2c, x1c:x2c]))
                        total_cov += covered / max(1, bbox_area)
                anchor_coverage = total_cov / max(1, len(anchor_rects))

            # anchor_rects 있으면 coverage 주도, depth 보조 (separation/cut_cost 스케일이 너무 커서 coverage를 압도함)
            if anchor_rects:
                score = anchor_coverage + (depth1 + depth2) * 0.001
            else:
                score = separation - cut_cost * 0.55 + (depth1 + depth2) * 0.01 + area_balance * 0.8

            # split line이 anchor bbox 면적을 실질적으로 침범하는 비율(cut_ratio)로 평가
            # cut_ratio > _SPLIT_MAX_CUT_RATIO(8%)이면 텍스트를 가르는 것 → bboxes_clear=False
            bboxes_clear = True
            if anchor_rects:
                cut_ratios = []
                for rect, ac in zip(anchor_rects, anchor_centers):
                    rx, ry, rw, rh = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
                    x1c = max(0, rx); y1c = max(0, ry)
                    x2c = min(w, rx + rw); y2c = min(h, ry + rh)
                    if x1c >= x2c or y1c >= y2c:
                        cut_ratios.append(0.0)
                        continue
                    ys_b, xs_b = np.mgrid[y1c:y2c, x1c:x2c]
                    side_local = (-dy) * (xs_b - p1[0]) + dx * (ys_b - p1[1])
                    anchor_sign = (-dy) * (ac[0] - p1[0]) + dx * (ac[1] - p1[1])
                    cut_pix = int(
                        np.count_nonzero(side_local < 0)
                        if anchor_sign >= 0
                        else np.count_nonzero(side_local >= 0)
                    )
                    cut_ratio = cut_pix / max(1, (x2c - x1c) * (y2c - y1c))
                    cut_ratios.append(cut_ratio)
                    if cut_ratio > _SPLIT_MAX_CUT_RATIO:
                        bboxes_clear = False
                        break
            else:
                cut_ratios = []

            candidate = {
                "source": "defect_pair",
                "p1": list(p1),
                "p2": list(p2),
                "depths": [round(depth1, 2), round(depth2, 2)],
                "turn_angles": [round(turn1, 2), round(turn2, 2)],
                "point_sources": [_defect_point_source(idx1), _defect_point_source(idx2)],
                "score": round(score, 3),
                "score_raw": score,
                "separates": separates,
                "centers_inside": centers_inside,
                "bboxes_clear": bboxes_clear,
                "area_balance": round(area_balance, 3),
                "anchor_coverage": round(anchor_coverage, 3),
                "min_area_ratio": round(min_area_ratio, 3),
                "component_separates": component_separates,
                "component_min_area": component_min_area,
                "component_min_area_ratio": round(component_min_area_ratio, 3),
                "component_count": int(cut_component.get("component_count", 0)),
                "cut_cost": round(cut_cost, 3),
                "split_line_length": round(length, 2),
                "max_cut_ratio": round(max(cut_ratios) if cut_ratios else 0.0, 3),
                "min_anchor_line_distance": round(min_anchor_line_distance, 2),
                "mean_anchor_line_distance": round(mean_anchor_line_distance, 2),
                "pos_indices": pos_indices,
                "neg_indices": neg_indices,
            }
            candidate["partition_preferred"] = partition_preferred
            candidates.append(candidate)

            if (
                component_separates
                and component_min_area >= MIN_SPLIT_SUBREGION_AREA
                and bboxes_clear
            ):
                if partition_preferred:
                    if preferred_selected is None or score > preferred_selected["score_raw"]:
                        preferred_selected = candidate
                if selected is None or score > selected["score_raw"]:
                    selected = candidate

    # 선호 파티션 일치 후보가 있으면 우선 사용, 없으면 일반 최고 후보
    final_selected = preferred_selected if preferred_selected is not None else selected

    return {
        "candidates": candidates,
        "selected": final_selected,
        "defect_points": [
            {"pt": list(pt), "depth": round(depth, 2), "turn_angle": round(turn_angle, 2)}
            for _, depth, pt, turn_angle in defect_points
        ],
    }


def _line_cut_component_stats(
    binary: np.ndarray,
    p1: Point,
    p2: Point,
    anchor_centers: List[Point],
    line_thickness: int = 5,
) -> Dict[str, Any]:
    """split line을 실제 barrier로 지웠을 때 anchor들이 별도 component로 갈라지는지 평가한다."""
    if not anchor_centers:
        return {"separates": False, "min_area": 0, "component_count": 0}

    cut = np.where(binary > 0, 255, 0).astype(np.uint8)
    cv2.line(cut, p1, p2, 0, line_thickness, cv2.LINE_AA)
    n_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(cut)
    total_area = max(1, int(np.count_nonzero(binary)))

    assigned: List[int] = []
    h, w = cut.shape[:2]
    for ax, ay in anchor_centers:
        x1, x2 = max(0, ax - 2), min(w, ax + 3)
        y1, y2 = max(0, ay - 2), min(h, ay + 3)
        roi = labels[y1:y2, x1:x2]
        roi = roi[roi > 0]
        if roi.size == 0:
            assigned.append(0)
            continue
        vals, counts = np.unique(roi, return_counts=True)
        assigned.append(int(vals[int(np.argmax(counts))]))

    assigned_nonzero = [label for label in assigned if label > 0]
    component_ids = sorted(set(assigned_nonzero))
    if len(assigned_nonzero) != len(anchor_centers) or len(component_ids) < 2:
        return {
            "separates": False,
            "min_area": 0,
            "component_count": len(component_ids),
            "anchor_components": assigned,
        }

    component_areas = [int(stats[label, cv2.CC_STAT_AREA]) for label in component_ids]
    min_area = min(component_areas) if component_areas else 0
    min_area_ratio = min_area / total_area if component_areas else 0.0
    return {
        "separates": True,
        "min_area": int(min_area),
        "min_area_ratio": float(min_area_ratio),
        "component_count": len(component_ids),
        "anchor_components": assigned,
    }


def _split_binary_by_segment_components(
    binary: np.ndarray,
    p1: Point,
    p2: Point,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_sub_area: int = MIN_SPLIT_SUBREGION_AREA,
    line_thickness: int = 5,
) -> Optional[List[Dict[str, Any]]]:
    """finite split segment로 실제 분리된 connected component를 split 결과로 반환한다."""
    if len(anchor_ids) != len(anchor_centers):
        return None

    cut = np.where(binary > 0, 255, 0).astype(np.uint8)
    cv2.line(cut, p1, p2, 0, line_thickness, cv2.LINE_AA)
    n_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(cut)
    if n_labels <= 2:
        return None

    h, w = cut.shape[:2]
    label_to_anchor_ids: Dict[int, List[int]] = {}
    for aid, (ax, ay) in zip(anchor_ids, anchor_centers):
        x1, x2 = max(0, ax - 2), min(w, ax + 3)
        y1, y2 = max(0, ay - 2), min(h, ay + 3)
        roi = labels[y1:y2, x1:x2]
        roi = roi[roi > 0]
        if roi.size == 0:
            return None
        vals, counts = np.unique(roi, return_counts=True)
        label = int(vals[int(np.argmax(counts))])
        label_to_anchor_ids.setdefault(label, []).append(aid)

    if len(label_to_anchor_ids) < 2:
        return None

    result: List[Dict[str, Any]] = []
    for label, group_anchor_ids in label_to_anchor_ids.items():
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_sub_area:
            return None
        sm = np.where(labels == label, 255, 0).astype(np.uint8)
        bbox = _bbox_from_mask(sm)
        if bbox is None:
            return None
        result.append({
            "anchor_ids": group_anchor_ids,
            "sub_mask": sm,
            "flood_rect": bbox,
            "split_line": [list(p1), list(p2)],
        })

    return result


def _split_binary_by_geometry_components(
    binary: np.ndarray,
    p1: Point,
    p2: Point,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_sub_area: int,
    line_thickness: int = 5,
) -> Optional[List[Dict[str, Any]]]:
    """anchor 분리 여부와 무관하게 finite segment가 실제 mask component를 나누는지 본다."""
    bbox = _mask_bbox(binary)
    if bbox is None:
        return None

    _bx, _by, bw, bh = bbox
    short_side = max(1, min(bw, bh))
    length = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
    if length < 5:
        return None
    if length > short_side * _GEOMETRY_SPLIT_MAX_LINE_SHORT_SIDE_RATIO:
        return None

    centroid = _mask_centroid(binary)
    if centroid is not None:
        centroid_dist = _point_segment_distance(centroid, p1, p2)
        near_centroid = centroid_dist <= max(4.0, short_side * _GEOMETRY_SPLIT_CENTROID_REJECT_RATIO)
        long_line = length > short_side * _GEOMETRY_SPLIT_CENTROID_LONG_LINE_RATIO
        if near_centroid and long_line:
            return None

    cut = np.where(binary > 0, 255, 0).astype(np.uint8)
    cv2.line(cut, p1, p2, 0, line_thickness, cv2.LINE_AA)
    n_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(cut)
    component_labels = [label for label in range(1, n_labels)]
    if len(component_labels) < 2:
        return None

    component_areas = [int(stats[label, cv2.CC_STAT_AREA]) for label in component_labels]
    if min(component_areas) < min_sub_area:
        return None

    label_to_anchor_ids: Dict[int, List[int]] = {label: [] for label in component_labels}
    h, w = cut.shape[:2]
    for aid, (ax, ay) in zip(anchor_ids, anchor_centers):
        x1, x2 = max(0, ax - 2), min(w, ax + 3)
        y1, y2 = max(0, ay - 2), min(h, ay + 3)
        roi = labels[y1:y2, x1:x2]
        roi = roi[roi > 0]
        if roi.size == 0:
            continue
        vals, counts = np.unique(roi, return_counts=True)
        label = int(vals[int(np.argmax(counts))])
        if label in label_to_anchor_ids:
            label_to_anchor_ids[label].append(aid)

    result: List[Dict[str, Any]] = []
    for label in component_labels:
        sm = np.where(labels == label, 255, 0).astype(np.uint8)
        child_bbox = _bbox_from_mask(sm)
        if child_bbox is None:
            return None
        result.append({
            "anchor_ids": label_to_anchor_ids.get(label, []),
            "sub_mask": sm,
            "flood_rect": child_bbox,
            "split_line": [list(p1), list(p2)],
            "split_source": "geometry_fallback",
        })

    return result


def collect_geometry_split_candidates(
    mask: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_defect_depth: float = 15.0,
    max_pair_points: int = _MAX_DEFECT_PAIR_POINTS,
    image_area: Optional[int] = None,
) -> Dict[str, Any]:
    """anchor-aware split 실패 후 사용하는 geometry-only split 후보를 수집한다.

    image_area: 최소 분할 면적 기준이 되는 페이지 면적. 잘라낸 mask를 넘길 때 원래 페이지 면적을 준다.
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return {"candidates": [], "selected": None, "defect_points": []}

    contour = max(contours, key=cv2.contourArea)
    _, defect_points = _extract_defect_points(
        contour,
        max(2, len(anchor_centers)),
        min_defect_depth,
        allow_single_weak_turn=False,
    )

    bbox = _mask_bbox(binary)
    if bbox is None:
        return {"candidates": [], "selected": None, "defect_points": []}
    bx, by, bw, bh = bbox
    tol = _BOUNDARY_FILTER_TOL
    defect_points = [
        item for item in defect_points
        if not (
            item[2][0] <= bx + tol
            or item[2][0] >= bx + bw - 1 - tol
            or item[2][1] <= by + tol
            or item[2][1] >= by + bh - 1 - tol
        )
    ]

    if len(defect_points) < 2:
        return {
            "candidates": [],
            "selected": None,
            "defect_points": [
                {"pt": list(pt), "depth": round(depth, 2), "turn_angle": round(turn_angle, 2)}
                for _, depth, pt, turn_angle in defect_points
            ],
        }

    min_sub_area = _adaptive_min_split_area(image_area or binary.shape[0] * binary.shape[1])
    short_side = max(1, min(bw, bh))
    candidates: List[Dict[str, Any]] = []
    selected: Optional[Dict[str, Any]] = None

    pair_points = defect_points[:max_pair_points]
    for i, (idx1, depth1, p1, turn1) in enumerate(pair_points):
        for idx2, depth2, p2, turn2 in pair_points[i + 1:]:
            split_result = _split_binary_by_geometry_components(
                binary,
                p1,
                p2,
                anchor_ids,
                anchor_centers,
                min_sub_area=min_sub_area,
            )
            length = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
            candidate = {
                "source": "geometry_fallback",
                "p1": list(p1),
                "p2": list(p2),
                "depths": [round(depth1, 2), round(depth2, 2)],
                "turn_angles": [round(turn1, 2), round(turn2, 2)],
                "point_sources": [_defect_point_source(idx1), _defect_point_source(idx2)],
                "split_line_length": round(length, 2),
                "min_sub_area": min_sub_area,
                "component_count": len(split_result or []),
                "score_raw": -1.0,
                "score": -1.0,
            }
            if split_result is not None:
                child_areas = [int(np.count_nonzero(sub["sub_mask"] > 0)) for sub in split_result]
                min_child_area = min(child_areas)
                length_score = max(0.0, 1.0 - length / max(1.0, short_side * _GEOMETRY_SPLIT_MAX_LINE_SHORT_SIDE_RATIO))
                score = (
                    length_score * 2.0
                    + min(1.0, min_child_area / max(1, min_sub_area * 2)) * 0.8
                    + (depth1 + depth2) * 0.004
                    + (turn1 + turn2) * 0.01
                )
                candidate.update({
                    "score_raw": score,
                    "score": round(score, 3),
                    "min_child_area": min_child_area,
                    "child_count": len(split_result),
                })
                if selected is None or score > selected["score_raw"]:
                    selected = candidate
            candidates.append(candidate)

    return {
        "candidates": candidates,
        "selected": selected,
        "defect_points": [
            {"pt": list(pt), "depth": round(depth, 2), "turn_angle": round(turn_angle, 2)}
            for _, depth, pt, turn_angle in defect_points
        ],
    }


def _split_mask_by_geometry_fallback(
    binary: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_defect_depth: float,
    image_area: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    candidate_info = collect_geometry_split_candidates(
        binary,
        anchor_ids,
        anchor_centers,
        min_defect_depth=min_defect_depth,
        image_area=image_area,
    )
    selected = candidate_info.get("selected")
    if selected is None:
        return None
    p1 = tuple(int(v) for v in selected["p1"])
    p2 = tuple(int(v) for v in selected["p2"])
    return _split_binary_by_geometry_components(
        binary,
        p1,
        p2,
        anchor_ids,
        anchor_centers,
        min_sub_area=_adaptive_min_split_area(image_area or binary.shape[0] * binary.shape[1]),
    )


def _bbox_from_mask(mask: np.ndarray) -> Optional[List[int]]:
    ys_m, xs_m = np.where(mask > 0)
    if len(xs_m) == 0:
        return None
    return [
        int(xs_m.min()), int(ys_m.min()),
        int(xs_m.max() - xs_m.min() + 1),
        int(ys_m.max() - ys_m.min() + 1),
    ]


def _valid_split_candidate(candidate: Dict[str, Any]) -> bool:
    return bool(
        candidate.get("component_separates", True)
        and int(candidate.get("component_min_area", MIN_SPLIT_SUBREGION_AREA)) >= MIN_SPLIT_SUBREGION_AREA
        and candidate.get("bboxes_clear", True)
    )


def _point_near_used_points(candidate: Dict[str, Any], used_points: List[Point]) -> bool:
    for key in ("p1", "p2"):
        pt = candidate.get(key)
        if pt is None:
            continue
        px, py = int(pt[0]), int(pt[1])
        for ux, uy in used_points:
            if float(np.hypot(px - ux, py - uy)) < _RECURSIVE_USED_POINT_DISTANCE:
                return True
    return False


def _recursive_candidate_rank(candidate: Dict[str, Any]) -> float:
    score = float(candidate.get("score_raw", candidate.get("score", 0.0)))
    score += float(candidate.get("anchor_coverage", 0.0)) * 1.0
    max_cut_ratio = float(candidate.get("max_cut_ratio", 0.0))
    split_line_length = float(candidate.get("split_line_length", 999.0))
    point_sources = candidate.get("point_sources", [])
    score += min(80.0, float(candidate.get("min_anchor_line_distance", 0.0))) / 48.0
    score += min(140.0, float(candidate.get("mean_anchor_line_distance", 0.0))) / 240.0
    score += max(0.0, 1.0 - (max_cut_ratio / _SPLIT_MAX_CUT_RATIO)) * 0.45
    score += max(0.0, 1.0 - (split_line_length / 180.0)) * 1.2
    score += sum(1 for source in point_sources if source == "defect") * 0.25
    if candidate.get("partition_preferred"):
        score += 1.0
    score -= max_cut_ratio * 10.0
    return score


def _score_split_tree(
    candidate: Dict[str, Any],
    children: List[Dict[str, Any]],
    child_scores: List[float],
) -> float:
    singleton_count = sum(1 for child in children if len(child.get("anchor_ids", [])) == 1)
    extra_leaf_count = max(0, len(children) - 2)
    multi_anchor_penalty = sum(max(0, len(child.get("anchor_ids", [])) - 1) for child in children)

    score = _recursive_candidate_rank(candidate)
    score += sum(child_scores) * 0.82
    score += singleton_count * 1.4
    score += extra_leaf_count * 0.7
    score -= multi_anchor_penalty * 1.1
    return score


def _search_best_recursive_split(
    binary: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_defect_depth: float,
    anchor_rects: Optional[List[List[int]]],
    depth: int,
    used_points: Optional[List[Point]] = None,
) -> Optional[tuple[float, List[Dict[str, Any]]]]:
    if len(anchor_ids) < 2 or depth >= _MAX_SPLIT_RECURSION_DEPTH:
        return None

    candidate_info = collect_inflection_candidates(
        binary,
        anchor_centers,
        min_defect_depth,
        anchor_rects,
        max_pair_points=_MAX_RECURSIVE_DEFECT_PAIR_POINTS,
    )
    candidates = [
        c for c in candidate_info.get("candidates", [])
        if _valid_split_candidate(c)
        and not _point_near_used_points(c, used_points or [])
    ]
    candidates.sort(key=_recursive_candidate_rank, reverse=True)
    candidates = candidates[:_MAX_RECURSIVE_NODE_CANDIDATES]

    best: Optional[tuple[float, List[Dict[str, Any]]]] = None
    for candidate in candidates:
        p1 = tuple(int(v) for v in candidate["p1"])
        p2 = tuple(int(v) for v in candidate["p2"])
        segment_groups = _split_binary_by_segment_components(
            binary,
            p1,
            p2,
            anchor_ids,
            anchor_centers,
        )
        if segment_groups is None:
            continue

        groups: List[Dict[str, Any]] = [
            {
                **group,
                "anchor_centers": [
                    ac for aid, ac in zip(anchor_ids, anchor_centers)
                    if aid in group["anchor_ids"]
                ],
                "anchor_rects": [
                    rect for aid, rect in zip(anchor_ids, anchor_rects or [])
                    if aid in group["anchor_ids"]
                ],
            }
            for group in segment_groups
        ]

        children: List[Dict[str, Any]] = []
        child_scores: List[float] = []
        valid = True
        for group in groups:
            sub_mask = group["sub_mask"]
            centers = group["anchor_centers"]
            if not _mask_contains_points(sub_mask > 0, centers):
                valid = False
                break

            if len(group["anchor_ids"]) >= 2:
                nested = _search_best_recursive_split(
                    sub_mask,
                    group["anchor_ids"],
                    centers,
                    min_defect_depth,
                    group["anchor_rects"] if anchor_rects is not None else None,
                    depth + 1,
                    [*(used_points or []), p1, p2],
                )
                if nested is not None:
                    nested_score, nested_children = nested
                    children.extend(nested_children)
                    child_scores.append(nested_score)
                    continue

            bbox = _bbox_from_mask(sub_mask)
            if bbox is None:
                valid = False
                break
            children.append({
                "anchor_ids": group["anchor_ids"],
                "sub_mask": sub_mask,
                "flood_rect": bbox,
                "split_line": [list(p1), list(p2)],
            })
            child_scores.append(0.0)

        if not valid:
            continue

        tree_score = _score_split_tree(candidate, children, child_scores)
        if best is None or tree_score > best[0]:
            best = (tree_score, children)

    return best


def _finalize_split_result_with_optional_recursion(
    result: List[Dict[str, Any]],
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_defect_depth: float,
    anchor_rects: Optional[List[List[int]]],
    image_area: Optional[int] = None,
) -> List[Dict[str, Any]]:
    final_result: List[Dict[str, Any]] = []
    needs_recurse = False
    for sub in result:
        sub_ids = sub["anchor_ids"]
        if len(sub_ids) >= 2:
            sub_centers = [
                ac for aid, ac in zip(anchor_ids, anchor_centers)
                if aid in sub_ids
            ]
            sub_rects = None
            if anchor_rects is not None:
                sub_rects = [
                    r for aid, r in zip(anchor_ids, anchor_rects)
                    if aid in sub_ids
                ]
            sub_split = split_mask_by_inflection_line(
                sub["sub_mask"], sub_ids, sub_centers,
                min_defect_depth=min_defect_depth,
                anchor_rects=sub_rects,
                image_area=image_area,
            )
            if sub_split is not None:
                final_result.extend(sub_split)
                needs_recurse = True
                continue
        final_result.append(sub)

    return final_result if needs_recurse else result


_SPLIT_CROP_MARGIN = 8


def _split_crop_box(
    binary: np.ndarray,
    anchor_centers: List[Point],
    anchor_rects: Optional[List[List[int]]],
) -> Optional[Tuple[int, int, int, int]]:
    """mask·anchor 중심·anchor rect를 모두 담는 계산 영역 (x1, y1, x2, y2). 페이지 밖으로는 넘지 않는다."""
    h, w = binary.shape[:2]
    bbox = _mask_bbox(binary)
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]
    for cx, cy in anchor_centers:
        x1, y1, x2, y2 = min(x1, int(cx)), min(y1, int(cy)), max(x2, int(cx) + 1), max(y2, int(cy) + 1)
    for rect in anchor_rects or []:
        rx, ry, rw, rh = (int(v) for v in rect[:4])
        x1, y1, x2, y2 = min(x1, rx), min(y1, ry), max(x2, rx + rw), max(y2, ry + rh)
    m = _SPLIT_CROP_MARGIN
    return max(0, x1 - m), max(0, y1 - m), min(w, x2 + m), min(h, y2 + m)


def _uncrop_split_item(item: Dict[str, Any], shape: Tuple[int, ...], x0: int, y0: int) -> Dict[str, Any]:
    """잘라낸 영역 좌표의 split 결과를 원래 페이지 좌표로 되돌린다."""
    out = dict(item)
    sub = item["sub_mask"]
    full = np.zeros(shape[:2], dtype=sub.dtype)
    full[y0:y0 + sub.shape[0], x0:x0 + sub.shape[1]] = sub
    out["sub_mask"] = full
    if item.get("flood_rect") is not None:
        fx, fy, fw, fh = item["flood_rect"]
        out["flood_rect"] = [fx + x0, fy + y0, fw, fh]
    if item.get("split_line") is not None:
        out["split_line"] = [[int(px) + x0, int(py) + y0] for px, py in item["split_line"]]
    return out


def split_mask_by_inflection_line(
    mask: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_defect_depth: float = 15.0,
    anchor_rects: Optional[List[List[int]]] = None,
    image_area: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    flood region mask를 contour inflection points 기반으로 분할한다.

    여러 defect pair 중 TG를 실제로 양쪽 sub-mask 안에 포함하고,
    최소 면적을 만족하는 후보를 점수화해 선택한다.

    후보 평가가 페이지 전체 크기 배열로 돌면 무거운 페이지에서 Step 3 시간 대부분을 쓴다.
    그래서 mask·anchor를 담는 영역만 잘라 계산하고 결과 좌표를 되돌린다. 최소 분할 면적은
    image_area(기본: 넘겨받은 mask 면적)로 정해 자르기 전과 같은 기준을 쓴다.
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    image_area = image_area or binary.shape[0] * binary.shape[1]
    box = _split_crop_box(binary, anchor_centers, anchor_rects)
    if box is None or box == (0, 0, binary.shape[1], binary.shape[0]):
        return _split_mask_by_inflection_line_core(
            binary, anchor_ids, anchor_centers, min_defect_depth, anchor_rects, image_area,
        )
    x0, y0, x1, y1 = box
    crop_centers = [(int(cx) - x0, int(cy) - y0) for cx, cy in anchor_centers]
    crop_rects = None
    if anchor_rects is not None:
        crop_rects = [[int(r[0]) - x0, int(r[1]) - y0, *(int(v) for v in r[2:4])] for r in anchor_rects]
    result = _split_mask_by_inflection_line_core(
        binary[y0:y1, x0:x1], anchor_ids, crop_centers, min_defect_depth, crop_rects, image_area,
    )
    if result is None:
        return None
    return [_uncrop_split_item(item, binary.shape, x0, y0) for item in result]


def _split_mask_by_inflection_line_core(
    binary: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_defect_depth: float,
    anchor_rects: Optional[List[List[int]]],
    image_area: int,
) -> Optional[List[Dict[str, Any]]]:
    recursive_result = _search_best_recursive_split(
        binary,
        anchor_ids,
        anchor_centers,
        min_defect_depth,
        anchor_rects,
        depth=0,
    )
    if recursive_result is not None:
        return recursive_result[1]

    result: Optional[List[Dict[str, Any]]] = None
    candidate_info = collect_inflection_candidates(binary, anchor_centers, min_defect_depth, anchor_rects)
    selected = candidate_info.get("selected")
    if result is None:
        if selected is not None:
            p1 = tuple(int(v) for v in selected["p1"])
            p2 = tuple(int(v) for v in selected["p2"])
            result = _split_binary_by_segment_components(
                binary,
                p1,
                p2,
                anchor_ids,
                anchor_centers,
            )
        if result is None:
            result = _split_mask_by_geometry_fallback(
                binary,
                anchor_ids,
                anchor_centers,
                min_defect_depth,
                image_area=image_area,
            )
        if result is None:
            return None

    return _finalize_split_result_with_optional_recursion(
        result,
        anchor_ids,
        anchor_centers,
        min_defect_depth,
        anchor_rects,
        image_area=image_area,
    )


def split_mask_by_erosion_components(
    mask: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Point],
    min_sub_area: int = MIN_SPLIT_SUBREGION_AREA,
    max_erosion: int = 24,
) -> Optional[List[Dict[str, Any]]]:
    """
    점진적 침식으로 얇은 연결부를 끊어 flood region을 분할한다.
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    h, w = binary.shape[:2]

    for erosion_px in range(3, max_erosion + 1, 2):
        kernel = np.ones((erosion_px * 2 + 1, erosion_px * 2 + 1), np.uint8)
        eroded = cv2.erode(binary, kernel)
        n_labels, _, stats, centroids = cv2.connectedComponentsWithStats(eroded)

        valid_labels = [
            i for i in range(1, n_labels)
            if stats[i, cv2.CC_STAT_AREA] > 50
        ]
        if len(valid_labels) < 2:
            continue

        comp_centers = [
            (float(centroids[lbl][0]), float(centroids[lbl][1]))
            for lbl in valid_labels
        ]
        ys_g, xs_g = np.mgrid[0:h, 0:w]

        groups: List[Dict[str, Any]] = [
            {"anchor_ids": [], "comp_idx": i}
            for i in range(len(valid_labels))
        ]
        for aid, ac in zip(anchor_ids, anchor_centers):
            dists = [
                (ac[0] - cx) ** 2 + (ac[1] - cy) ** 2
                for cx, cy in comp_centers
            ]
            groups[int(np.argmin(dists))]["anchor_ids"].append(aid)

        groups = [g for g in groups if g["anchor_ids"]]
        if len(groups) < 2:
            continue

        anchor_comp_indices = [g["comp_idx"] for g in groups]
        anchor_comp_centers = [comp_centers[i] for i in anchor_comp_indices]
        dist_maps = np.stack([
            (xs_g - cx) ** 2 + (ys_g - cy) ** 2
            for cx, cy in anchor_comp_centers
        ], axis=0)
        nearest_anchor_comp = np.argmin(dist_maps, axis=0)

        result: List[Dict[str, Any]] = []
        valid = True
        for ci, g in enumerate(groups):
            sub = (binary > 0) & (nearest_anchor_comp == ci)
            sub_area = int(np.count_nonzero(sub))
            if sub_area < min_sub_area:
                valid = False
                break
            sm = sub.astype(np.uint8) * 255
            ys_s, xs_s = np.where(sm > 0)
            bbox = [
                int(xs_s.min()), int(ys_s.min()),
                int(xs_s.max() - xs_s.min() + 1),
                int(ys_s.max() - ys_s.min() + 1),
            ]
            result.append({
                "anchor_ids": g["anchor_ids"],
                "sub_mask": sm,
                "flood_rect": bbox,
                "erosion_px": erosion_px,
            })

        if valid:
            return result

    return None
