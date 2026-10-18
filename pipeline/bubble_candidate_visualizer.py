# 말풍선 후보 검토용 시각화 이미지를 생성합니다.
from __future__ import annotations

from typing import Any, Dict, List, Optional

import cv2
import numpy as np


SOFT_BLUE = (255, 178, 94)
SOFT_BLUE_LINE = (255, 215, 150)
RESCUE_GREEN = (170, 230, 80)
RESCUE_GREEN_LINE = (130, 255, 80)
DARK_FLOOD_YELLOW = (0, 220, 255)
DARK_FLOOD_YELLOW_LINE = (0, 255, 255)
FAIL_RED = (0, 220, 255)
FAIL_RED_FILL = (180, 245, 255)
EDGE_FAIL_ORANGE = (0, 220, 255)
EDGE_FAIL_FILL = (180, 245, 255)
LABEL_BG = (0, 0, 0)
TG_BOX = (0, 220, 255)
TG_LINE_BOX = (0, 170, 255)
TG_FALLBACK_BOX = (0, 140, 255)
SAFE_TEXT_RECT = (210, 170, 70)
BUBBLE_RECT_BOX = (40, 170, 200)
SPLIT_PARENT_BOX = (150, 150, 150)
BLACK_CONTOUR_PROBE = (255, 0, 255)

# 조기 skip 케이스별 색상 (BGR)
_SKIP_COLORS: dict[str, tuple[int, int, int]] = {
    "dark_bg_text":                 (180,  60, 180),   # 보라
    "symbol_only_text":             (0,   150, 255),   # 주황
    "short_non_japanese_noise_text":(200,  80, 200),   # 핑크
    "english_only_text":            (180, 200,   0),   # 청록
    "large_short_kana_sfx_like":    (0,   220, 255),   # 노랑
    "large_single_glyph_sfx_like":  (0,   190, 255),   # 노랑
    "no_seed":                      (120, 120, 120),   # 회색
    "unsafe_region":                ( 80,  80,  80),   # 짙은 회색
}
_SKIP_LABELS: dict[str, str] = {
    "dark_bg_text":                  "DarkBG",
    "symbol_only_text":              "Symbol",
    "short_non_japanese_noise_text": "Noise",
    "english_only_text":             "EN",
    "large_short_kana_sfx_like":     "SFX",
    "large_single_glyph_sfx_like":   "SFX",
    "no_seed":                       "NoSeed",
    "unsafe_region":                 "Unsafe",
}


def _as_rect(rect: List[int]) -> List[int]:
    x, y, w, h = [int(v) for v in rect[:4]]
    return [x, y, max(0, w), max(0, h)]


def _union_rect(rects: List[List[int]]) -> List[int]:
    valid_rects = [r for r in rects if len(r) >= 4]
    if not valid_rects:
        return [0, 0, 0, 0]
    x1 = min(int(r[0]) for r in valid_rects)
    y1 = min(int(r[1]) for r in valid_rects)
    x2 = max(int(r[0]) + int(r[2]) for r in valid_rects)
    y2 = max(int(r[1]) + int(r[3]) for r in valid_rects)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _tight_text_group_rect(cluster: Dict[str, Any]) -> List[int]:
    if len(cluster.get("tight_rect", [])) >= 4:
        return _as_rect(cluster["tight_rect"])
    line_rects = [
        line.get("bbox", [])
        for line in cluster.get("lines", [])
        if len(line.get("bbox", [])) >= 4
    ]
    tight_rect = _union_rect(line_rects)
    if tight_rect[2] > 0 and tight_rect[3] > 0:
        return tight_rect
    return _as_rect(cluster.get("bounding_rect", [0, 0, 0, 0]))


def _rects_differ(a: List[int], b: List[int]) -> bool:
    return [int(v) for v in a[:4]] != [int(v) for v in b[:4]]


def _draw_region_fill(overlay: np.ndarray, region: Dict[str, Any], color: tuple[int, int, int]) -> None:
    mask = region.get("mask")
    polygon = region.get("polygon")
    rect = region.get("bubble_rect")
    if rect is None:
        return

    if mask is not None and np.any(mask > 0):
        overlay[mask > 0] = color
        return
    if polygon:
        poly = np.array(polygon, dtype=np.int32)
        if poly.ndim == 2 and poly.shape[0] >= 3:
            cv2.fillPoly(overlay, [poly], color)
            return

    x, y, w, h = _as_rect(rect)
    cv2.rectangle(overlay, (x, y), (x + w, y + h), color, -1)


def _fill_alpha_for_region(
    region: Dict[str, Any],
    image_shape: tuple[int, ...],
    default_alpha: float,
) -> float:
    """페이지 대부분을 덮는 area 계열 후보는 흐리게 표시한다."""
    fail_tag = str(region.get("fail_tag") or "").lower()
    review_tags = " ".join(str(tag).lower() for tag in region.get("review_tags", []))
    quality_flags = " ".join(str(flag).lower() for flag in region.get("quality_flags", []))
    area_like = "area" in fail_tag or "area" in review_tags or "area" in quality_flags

    rect = region.get("bubble_rect")
    rect_ratio = 0.0
    if rect is not None:
        _x, _y, w, h = _as_rect(rect)
        img_h, img_w = image_shape[:2]
        rect_ratio = (w * h) / max(1, img_w * img_h)

    if area_like or rect_ratio >= 0.35:
        return min(default_alpha, 0.12)
    if rect_ratio >= 0.22:
        return min(default_alpha, 0.18)
    return default_alpha


def _blend_region_fill(
    vis_img: np.ndarray,
    region: Dict[str, Any],
    color: tuple[int, int, int],
    alpha: float,
) -> np.ndarray:
    overlay = vis_img.copy()
    _draw_region_fill(overlay, region, color)
    region_alpha = _fill_alpha_for_region(region, vis_img.shape, alpha)
    return cv2.addWeighted(overlay, region_alpha, vis_img, 1 - region_alpha, 0)


def _draw_region_border(vis_img: np.ndarray, region: Dict[str, Any], color: tuple[int, int, int], thickness: int) -> None:
    mask = region.get("mask")
    polygon = region.get("polygon")
    rect = region.get("bubble_rect")
    if rect is None:
        return

    if mask is not None and np.any(mask > 0):
        binary = np.where(mask > 0, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            cv2.drawContours(vis_img, contours, -1, color, thickness, cv2.LINE_AA)
            return
    if polygon:
        poly = np.array(polygon, dtype=np.int32)
        if poly.ndim == 2 and poly.shape[0] >= 3:
            cv2.polylines(vis_img, [poly], True, color, thickness, cv2.LINE_AA)
            return

    x, y, w, h = _as_rect(rect)
    cv2.rectangle(vis_img, (x, y), (x + w, y + h), color, thickness, cv2.LINE_AA)


def _draw_overlay_clues(vis_img: np.ndarray, crops: List[Dict[str, Any]]) -> None:
    """최종 bubble_rect와 safe_text_rect clue를 함께 표시한다."""
    for item in crops:
        region = item["region"]
        if not region.get("is_valid"):
            continue
        split_parent_rect = region.get("split_parent_rect")
        if split_parent_rect:
            x, y, w, h = _as_rect(split_parent_rect)
            cv2.rectangle(vis_img, (x, y), (x + w, y + h), SPLIT_PARENT_BOX, 1, cv2.LINE_AA)

        bubble_rect = region.get("bubble_rect")
        if bubble_rect:
            x, y, w, h = _as_rect(bubble_rect)
            cv2.rectangle(vis_img, (x, y), (x + w, y + h), BUBBLE_RECT_BOX, 1, cv2.LINE_AA)

        safe_rect = region.get("safe_text_rect")
        if safe_rect:
            x, y, w, h = _as_rect(safe_rect)
            cv2.rectangle(vis_img, (x, y), (x + w, y + h), SAFE_TEXT_RECT, 2, cv2.LINE_AA)

        centroid = region.get("mask_centroid")
        if centroid and len(centroid) >= 2:
            cx, cy = int(centroid[0]), int(centroid[1])
            cv2.circle(vis_img, (cx, cy), 7, (255, 0, 0), -1, cv2.LINE_AA)


def _is_rescued_region(region: Dict[str, Any]) -> bool:
    method = str(region.get("method", ""))
    return "edge_barrier" in method


def _is_dark_flood_region(region: Dict[str, Any]) -> bool:
    return region.get("method") == "dark_flood"


def _is_edge_barrier_failed_region(region: Dict[str, Any]) -> bool:
    if region.get("is_valid"):
        return False
    flags = " ".join(str(flag) for flag in region.get("quality_flags", []))
    tags = " ".join(str(tag) for tag in region.get("review_tags", []))
    return (
        "edge_barrier_rescue_failed" in flags
        or "edge_reject_" in flags
        or "edge_" in tags
    )


def _draw_legend(vis_img: np.ndarray) -> None:
    """시각화 색상 의미를 이미지 상단에 한 번만 표시한다."""
    entries = [
        ("bubble", SOFT_BLUE_LINE),
        ("edge", RESCUE_GREEN_LINE),
        ("dark", DARK_FLOOD_YELLOW_LINE),
        ("fail", FAIL_RED),
        ("edge-fail", EDGE_FAIL_ORANGE),
        ("TG", TG_BOX),
        ("safe", SAFE_TEXT_RECT),
        ("parent", SPLIT_PARENT_BOX),
        ("probe", BLACK_CONTOUR_PROBE),
    ]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.52
    thick = 1
    pad_x = 10
    pad_y = 8
    swatch = 16
    gap = 18
    x = pad_x
    y = 24
    height = 34
    cv2.rectangle(vis_img, (0, 0), (vis_img.shape[1], height), (0, 0, 0), -1)
    for label, color in entries:
        (tw, th), _ = cv2.getTextSize(label, font, scale, thick)
        if x + swatch + 5 + tw + gap > vis_img.shape[1]:
            break
        cv2.rectangle(vis_img, (x, y - swatch + 3), (x + swatch, y + 3), color, -1)
        cv2.putText(vis_img, label, (x + swatch + 5, y), font, scale, color, thick, cv2.LINE_AA)
        x += swatch + 5 + tw + gap


def _draw_text_groups(vis_img: np.ndarray, clusters: List[Dict[str, Any]]) -> None:
    for idx, cluster in enumerate(clusters, start=1):
        rect = _tight_text_group_rect(cluster)
        fallback_rect = _as_rect(cluster.get("fallback_rect", cluster.get("bounding_rect", rect)))
        if not rect or len(rect) < 4:
            continue

        cluster_id = cluster.get("text_group_id", cluster.get("cluster_id", idx))
        x, y, w, h = _as_rect(rect)

        if _rects_differ(fallback_rect, rect):
            fx, fy, fw, fh = fallback_rect
            cv2.rectangle(vis_img, (fx, fy), (fx + fw, fy + fh), TG_FALLBACK_BOX, 1, cv2.LINE_AA)

        cv2.rectangle(vis_img, (x, y), (x + w, y + h), TG_BOX, 2, cv2.LINE_AA)


def _draw_skip_flags(
    vis_img: np.ndarray,
    skipped: List[Dict[str, Any]],
) -> None:
    """조기 skip된 anchor(DarkBG, Symbol, Noise 등)를 점선 박스 + 라벨로 표시합니다."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.52
    thick = 1

    for entry in skipped:
        reason = str(entry.get("reason", ""))
        if reason == "failed_candidate":
            continue  # crops에서 이미 처리

        color = _SKIP_COLORS.get(reason, (100, 100, 100))
        label = _SKIP_LABELS.get(reason, reason[:8])

        bbox = entry.get("fallback_bbox") or entry.get("anchor_bbox")
        if not bbox or len(bbox) < 4:
            continue
        x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        if w <= 0 or h <= 0:
            continue

        # 점선 효과: 짧은 세그먼트로 사각형 테두리 그리기
        seg = 8
        pts = []
        # top
        for i in range(x, x + w, seg * 2):
            pts.append(((i, y), (min(i + seg, x + w), y)))
        # bottom
        for i in range(x, x + w, seg * 2):
            pts.append(((i, y + h), (min(i + seg, x + w), y + h)))
        # left
        for i in range(y, y + h, seg * 2):
            pts.append(((x, i), (x, min(i + seg, y + h))))
        # right
        for i in range(y, y + h, seg * 2):
            pts.append(((x + w, i), (x + w, min(i + seg, y + h))))
        for p1, p2 in pts:
            cv2.line(vis_img, p1, p2, color, 2, cv2.LINE_AA)

        # 라벨 배지
        (tw, th), bl = cv2.getTextSize(label, font, scale, thick)
        lx, ly = x, max(th + 4, y - 2)
        cv2.rectangle(vis_img, (lx - 2, ly - th - 4), (lx + tw + 4, ly + bl + 2), LABEL_BG, -1)
        cv2.putText(vis_img, label, (lx, ly), font, scale, color, thick, cv2.LINE_AA)


def _draw_black_contour_probe(vis_img: np.ndarray, crops: List[Dict[str, Any]]) -> None:
    """탈락 후보의 black contour probe hit 지점을 표시한다."""
    for item in crops:
        region = item["region"]
        if region.get("is_valid"):
            continue
        probe = region.get("black_contour_probe") or {}
        points = probe.get("hit_points") or []
        if not points:
            continue
        center = probe.get("center")
        if center and len(center) >= 2:
            cv2.circle(vis_img, (int(center[0]), int(center[1])), 4, BLACK_CONTOUR_PROBE, -1, cv2.LINE_AA)
        for point in points[::2]:
            if len(point) < 2:
                continue
            cv2.circle(vis_img, (int(point[0]), int(point[1])), 2, BLACK_CONTOUR_PROBE, -1, cv2.LINE_AA)


def render_candidate_review_vis(
    image: np.ndarray,
    crops: List[Dict[str, Any]],
    output_path: str,
    clusters: Optional[List[Dict[str, Any]]] = None,
    skipped: Optional[List[Dict[str, Any]]] = None,
    alpha: float = 0.4,
) -> None:
    """성공 후보는 파랑, 실패 후보는 빨강으로 그린 검토용 이미지를 저장합니다."""
    failed_items = [
        item for item in crops
        if not item["region"].get("is_valid") and not _is_edge_barrier_failed_region(item["region"])
    ]
    edge_failed_items = [
        item for item in crops
        if not item["region"].get("is_valid") and _is_edge_barrier_failed_region(item["region"])
    ]
    rescue_items = [item for item in crops if item["region"].get("is_valid") and _is_rescued_region(item["region"])]
    dark_flood_items = [item for item in crops if item["region"].get("is_valid") and _is_dark_flood_region(item["region"])]
    valid_items = [
        item for item in crops
        if item["region"].get("is_valid")
        and not _is_rescued_region(item["region"])
        and not _is_dark_flood_region(item["region"])
    ]

    vis_img = image.copy()
    failed_alpha = min(alpha, 0.16)
    for item in failed_items:
        vis_img = _blend_region_fill(vis_img, item["region"], FAIL_RED_FILL, failed_alpha)
    for item in edge_failed_items:
        vis_img = _blend_region_fill(vis_img, item["region"], EDGE_FAIL_FILL, failed_alpha)
    for item in valid_items:
        vis_img = _blend_region_fill(vis_img, item["region"], SOFT_BLUE, alpha)
    for item in rescue_items:
        vis_img = _blend_region_fill(vis_img, item["region"], RESCUE_GREEN, alpha)
    for item in dark_flood_items:
        vis_img = _blend_region_fill(vis_img, item["region"], DARK_FLOOD_YELLOW, alpha)

    # 경계선도 같은 우선순위로 그립니다. rescue와 성공 후보 경계선이 마지막에 올라옵니다.
    for item in failed_items:
        _draw_region_border(vis_img, item["region"], FAIL_RED, 2)
    for item in edge_failed_items:
        _draw_region_border(vis_img, item["region"], EDGE_FAIL_ORANGE, 3)
    for item in valid_items:
        _draw_region_border(vis_img, item["region"], SOFT_BLUE_LINE, 3)
    for item in rescue_items:
        _draw_region_border(vis_img, item["region"], RESCUE_GREEN_LINE, 3)
    for item in dark_flood_items:
        _draw_region_border(vis_img, item["region"], DARK_FLOOD_YELLOW_LINE, 3)

    _draw_black_contour_probe(vis_img, crops)
    _draw_overlay_clues(vis_img, crops)

    if clusters:
        _draw_text_groups(vis_img, clusters)

    # 후보 중간 R 번호는 최종 OCR region id와 달라질 수 있어 표시하지 않는다.
    if skipped:
        _draw_skip_flags(vis_img, skipped)

    _draw_legend(vis_img)

    # split line은 모든 보조 레이어 위에 마지막으로 그린다.
    h, w = vis_img.shape[:2]
    drawn_lines: set = set()
    for item in crops:
        sl = item["region"].get("split_line")
        if sl is None:
            continue
        p1, p2 = tuple(sl[0]), tuple(sl[1])
        key = (min(p1, p2), max(p1, p2))
        if key in drawn_lines:
            continue
        drawn_lines.add(key)
        cv2.line(vis_img, p1, p2, (0, 0, 220), 4, cv2.LINE_AA)

    cv2.imwrite(output_path, vis_img)
