# 만화 대사 추출기 — Bubble Detector + Unboxed Text Eraser(LaMa) + OCR Sheet 생성
#!/usr/bin/env python3

import cv2
import json
import threading
import numpy as np
from pathlib import Path
from typing import Any, Dict, List, Tuple
from PIL import Image

# simple_lama_inpainting은 torch를 끌어온다. LaMa가 실제로 필요한 페이지에서만 import한다(_get_lama).
SimpleLama: Any | None = None

try:
    from pipeline.bubble_detector import build_ocr_region_sheet, _load_json, _is_dark_bg_text, _is_horizontal_text_group, _fill_mask_holes
    from pipeline.geometry import as_rect as _as_rect, union_rect as _union_rect
    from pipeline.unboxed_grouping import (
        cluster_adjacent_text_groups as _cluster_adjacent_tgs,
        is_noise_text as _is_noise,
        text_group_id as _tg_id,
        text_group_text as _tg_text,
    )
    from pipeline.text_style_metrics import (
        aggregate_style_decisions as _aggregate_style_decisions,
        classify_text_group_style as _classify_text_group_style,
        compute_page_style_baseline as _compute_page_style_baseline,
        compute_text_group_style as _compute_text_group_style,
    )
    from pipeline.run_masked_vis import (
        _extract_with_lane_retry, _polygon_to_mask, _expand_mask, _erode_mask,
    )
    from pipeline.unboxed_text_detector import _classify_inpaint_background
    from pipeline.flood_split import collect_inflection_candidates, split_mask_by_erosion_components, split_mask_by_inflection_line
except ImportError:
    from bubble_detector import build_ocr_region_sheet, _load_json, _is_dark_bg_text, _is_horizontal_text_group, _fill_mask_holes
    from geometry import as_rect as _as_rect, union_rect as _union_rect
    from unboxed_grouping import (
        cluster_adjacent_text_groups as _cluster_adjacent_tgs,
        is_noise_text as _is_noise,
        text_group_id as _tg_id,
        text_group_text as _tg_text,
    )
    from text_style_metrics import (
        aggregate_style_decisions as _aggregate_style_decisions,
        classify_text_group_style as _classify_text_group_style,
        compute_page_style_baseline as _compute_page_style_baseline,
        compute_text_group_style as _compute_text_group_style,
    )
    from run_masked_vis import (
        _extract_with_lane_retry, _polygon_to_mask, _expand_mask, _erode_mask,
    )
    from unboxed_text_detector import _classify_inpaint_background
    from flood_split import collect_inflection_candidates, split_mask_by_erosion_components, split_mask_by_inflection_line

_lama: Any | None = None
_lama_lock = threading.Lock()


def _compute_mask_centroid(mask: "np.ndarray | None") -> "list[int] | None":
    """flood/polygon mask 픽셀의 무게중심(centroid)을 계산한다.

    mask가 없거나 픽셀이 없으면 None을 반환 — overlay에서 bbox 중심을 그대로 사용.
    있으면 [cx, cy] (이미지 좌표계 기준) 를 반환.
    """
    if mask is None:
        return None
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    M = cv2.moments(binary)
    if M["m00"] < 1.0:
        return None
    cx = int(round(M["m10"] / M["m00"]))
    cy = int(round(M["m01"] / M["m00"]))
    return [cx, cy]


def _mask_bounding_rect(mask: "np.ndarray | None") -> "list[int] | None":
    """mask 픽셀의 이미지 좌표 외접 bbox를 반환한다."""
    if mask is None:
        return None
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    points = cv2.findNonZero(binary)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    return [int(x), int(y), int(w), int(h)]


def _rect_intersection_area(a: List[int], b: List[int]) -> int:
    ax, ay, aw, ah = _as_rect(a)
    bx, by, bw, bh = _as_rect(b)
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    return max(0, x2 - x1) * max(0, y2 - y1)


def _rect_center_in_rect(inner: List[int], outer: List[int]) -> bool:
    ix, iy, iw, ih = _as_rect(inner)
    ox, oy, ow, oh = _as_rect(outer)
    cx = ix + iw / 2.0
    cy = iy + ih / 2.0
    return ox <= cx <= ox + ow and oy <= cy <= oy + oh


def _rect_polygon(rect: List[int]) -> List[List[int]]:
    x, y, w, h = _as_rect(rect)
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _manual_region_anchor_ids(rect: List[int], groups: List[Dict[str, Any]]) -> List[int]:
    anchor_ids: List[int] = []
    for idx, group in enumerate(groups, start=1):
        group_rect = _as_rect(group.get("fallback_rect") or group.get("tight_rect") or group.get("bounding_rect") or [0, 0, 0, 0])
        if group_rect[2] <= 0 or group_rect[3] <= 0:
            continue
        group_area = max(1, group_rect[2] * group_rect[3])
        overlap_ratio = _rect_intersection_area(rect, group_rect) / group_area
        if _rect_center_in_rect(group_rect, rect) or overlap_ratio >= 0.35:
            anchor_ids.append(_tg_id(group, idx))
    return sorted(set(anchor_ids))


def _manual_seed_anchor_ids_from_result(
    rescue_rect: List[int],
    mask: "np.ndarray | None",
    groups: List[Dict[str, Any]],
) -> List[int]:
    anchor_ids: List[int] = []
    rx, ry, rw, rh = _as_rect(rescue_rect)
    if rw <= 0 or rh <= 0:
        return []

    for idx, group in enumerate(groups, start=1):
        group_rect = _as_rect(group.get("fallback_rect") or group.get("tight_rect") or group.get("bounding_rect") or [0, 0, 0, 0])
        gx, gy, gw, gh = group_rect
        if gw <= 0 or gh <= 0:
            continue
        group_area = max(1, gw * gh)
        rect_overlap_ratio = _rect_intersection_area(rescue_rect, group_rect) / group_area
        mask_overlap_ratio = 0.0
        if isinstance(mask, np.ndarray):
            x1 = max(0, gx)
            y1 = max(0, gy)
            x2 = min(mask.shape[1], gx + gw)
            y2 = min(mask.shape[0], gy + gh)
            if x2 > x1 and y2 > y1:
                mask_overlap_ratio = float(np.count_nonzero(mask[y1:y2, x1:x2] > 0)) / group_area
        if (
            _rect_center_in_rect(group_rect, rescue_rect)
            or rect_overlap_ratio >= 0.35
            or mask_overlap_ratio >= 0.12
        ):
            anchor_ids.append(_tg_id(group, idx))
    return sorted(set(anchor_ids))


def _manual_bubble_regions(manual_regions: List[Dict[str, Any]], groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    regions: List[Dict[str, Any]] = []
    for index, item in enumerate(manual_regions, start=1):
        if item.get("type") not in {None, "bubble"}:
            continue
        raw_rect = item.get("bubble_rect") or item.get("rect")
        if not isinstance(raw_rect, list) or len(raw_rect) < 4:
            continue
        rect = _as_rect(raw_rect)
        if rect[2] <= 0 or rect[3] <= 0:
            continue
        regions.append({
            "region_id": 10_000 + index,
            "anchor_ids": _manual_region_anchor_ids(rect, groups),
            "bubble_rect": rect,
            "polygon": _rect_polygon(rect),
            "is_valid": True,
            "method": "manual_bubble_guide",
            "region_type_guess": "bubble",
            "quality_flags": ["manual_bubble"],
            "review_tags": ["manual_bubble"],
        })
    return regions


def _manual_unboxed_crops(
    image: np.ndarray,
    manual_regions: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
    img_w: int,
    img_h: int,
) -> List[Dict[str, Any]]:
    crops: List[Dict[str, Any]] = []
    group_by_id = {_tg_id(group, idx): group for idx, group in enumerate(groups, start=1)}
    for index, item in enumerate(manual_regions, start=1):
        if item.get("type") != "unboxed":
            continue
        raw_rect = item.get("unboxed_rect") or item.get("rect")
        if not isinstance(raw_rect, list) or len(raw_rect) < 4:
            continue
        rx, ry, rw, rh = _as_rect(raw_rect)
        x1 = max(0, rx)
        y1 = max(0, ry)
        x2 = min(img_w, rx + rw)
        y2 = min(img_h, ry + rh)
        rect = [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]
        if rect[2] <= 0 or rect[3] <= 0:
            continue

        anchor_ids = _manual_region_anchor_ids(rect, groups)
        cluster = [(tid, group_by_id[tid]) for tid in anchor_ids if tid in group_by_id]
        consolidated = _consolidated_unboxed_group(cluster) if cluster else {
            "text_group_ids": [],
            "text": "",
            "tight_rect": rect,
            "bounding_rect": rect,
            "lines": [],
        }
        crops.append({
            "region_id": 0,
            "anchor_ids": anchor_ids,
            "source_rect": rect,
            "erase_rect": rect,
            "crop": image[y1:y2, x1:x2].copy(),
            "type": "unboxed",
            "sort_rect": rect,
            "split_order": 0,
            "source_order": 20_000 + index,
            "consolidated_text_group": consolidated,
            "inpaint_skipped": False,
            "source_method": item.get("source_method") or "manual_ubox_guide",
            "source_region_type": "manual_ubox",
            # 사용자가 엔진을 지정하면 배경 분류 결과보다 우선한다(현재는 "lama"만 의미가 있다).
            "inpaint_engine_override": item.get("inpaint_engine"),
            "quality_flags": ["manual_ubox"],
            "review_tags": ["manual_ubox"],
        })
    return crops


def _manual_ubox_text_mask(
    image: np.ndarray,
    rect: List[int],
    anchor_groups: List[Dict[str, Any]],
    dark_threshold: int = 225,
    bright_threshold: int = 175,
    group_pad: int = 8,
    dilate_px: int = 3,
    polarity: str = "dark",
) -> Tuple[List[int], np.ndarray | None]:
    """수동 Ubox 내부에서 인페인트에 넘길 텍스트 획 mask를 만든다."""
    x, y, w, h = _as_rect(rect)
    img_h, img_w = image.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)
    clipped = [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]
    if clipped[2] <= 0 or clipped[3] <= 0:
        return clipped, None

    roi = image[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    lane_mask = np.zeros((clipped[3], clipped[2]), dtype=np.uint8)

    for group in anchor_groups:
        gx, gy, gw, gh = _group_rect(group)
        if gw <= 0 or gh <= 0:
            continue
        lx1 = max(0, gx - x1 - group_pad)
        ly1 = max(0, gy - y1 - group_pad)
        lx2 = min(clipped[2], gx + gw - x1 + group_pad)
        ly2 = min(clipped[3], gy + gh - y1 + group_pad)
        if lx2 > lx1 and ly2 > ly1:
            lane_mask[ly1:ly2, lx1:lx2] = 255

    # 수동 Ubox는 사용자가 erase 대상 텍스트 영역을 직접 잡은 것이므로
    # TG lane이 좁더라도 Ubox 내부의 얇은 후리가나/루비까지 후보로 본다.
    lane_mask[:, :] = 255

    if polarity == "bright":
        text_mask = np.where((gray >= bright_threshold) & (lane_mask > 0), 255, 0).astype(np.uint8)
    else:
        text_mask = np.where((gray <= dark_threshold) & (lane_mask > 0), 255, 0).astype(np.uint8)
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(text_mask, connectivity=8)
    filtered = np.zeros_like(text_mask)
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= 2:
            filtered[labels == label] = 255
    text_mask = filtered
    if dilate_px > 0:
        kernel = np.ones((dilate_px * 2 + 1, dilate_px * 2 + 1), dtype=np.uint8)
        text_mask = cv2.dilate(text_mask, kernel, iterations=1)

    if int(np.count_nonzero(text_mask)) < 12:
        return clipped, None
    return clipped, text_mask


def _classify_manual_ubox_background(roi: np.ndarray, text_mask: np.ndarray) -> Dict[str, Any]:
    """수동 Ubox ROI의 배경을 분석해 마스크 극성과 엔진을 고른다."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    pixels = gray.reshape(-1)
    mean = float(np.mean(pixels)) if pixels.size else 0.0
    std = float(np.std(pixels)) if pixels.size else 0.0
    dark_ratio = float(np.count_nonzero(pixels < 80)) / max(1, pixels.size)
    light_ratio = float(np.count_nonzero(pixels > 220)) / max(1, pixels.size)

    if mean < 120 or dark_ratio > 0.35:
        return {
            "background_class": "dark_bg",
            "recommended_inpaint_engine": "lama",
            "manual_mask_polarity": "bright",
            "manual_bg_mean": round(mean, 2),
            "manual_bg_std": round(std, 2),
            "manual_bg_dark_ratio": round(dark_ratio, 4),
            "manual_bg_light_ratio": round(light_ratio, 4),
        }

    meta = _classify_inpaint_background(roi, text_mask)
    meta.update({
        "manual_mask_polarity": "dark",
        "manual_bg_mean": round(mean, 2),
        "manual_bg_std": round(std, 2),
        "manual_bg_dark_ratio": round(dark_ratio, 4),
        "manual_bg_light_ratio": round(light_ratio, 4),
    })
    return meta


def _padded_context_rect(base_rect: List[int], image_shape: Tuple[int, ...], padding: int = 24) -> List[int] | None:
    """수동 Ubox rect 바깥으로 padding을 더해 LaMa context rect를 만든다."""
    bx, by, bw, bh = _as_rect(base_rect)
    img_h, img_w = image_shape[:2]
    x1 = max(0, bx - padding)
    y1 = max(0, by - padding)
    x2 = min(img_w, bx + bw + padding)
    y2 = min(img_h, by + bh + padding)
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


def _apply_manual_ubox_lama(
    image: np.ndarray,
    masked_image: np.ndarray,
    item: Dict[str, Any],
    group_by_id: Dict[int, Dict[str, Any]],
    protected_mask: np.ndarray | None = None,
    context_padding: int = 24,
) -> bool:
    """수동 Ubox의 텍스트 획만 LaMa로 지운다."""
    rect = _as_rect(item.get("erase_rect") or item.get("source_rect") or [0, 0, 0, 0])
    anchor_groups = [
        group_by_id[int(tid)]
        for tid in item.get("anchor_ids", [])
        if str(tid).lstrip("-").isdigit() and int(tid) in group_by_id
    ]
    clipped, text_mask = _manual_ubox_text_mask(image, rect, anchor_groups)
    if text_mask is None:
        return False
    x, y, w, h = clipped
    clipped_roi = image[y:y + h, x:x + w]
    background_meta = _classify_manual_ubox_background(clipped_roi, text_mask)
    if background_meta.get("manual_mask_polarity") == "bright":
        # 흰 글자의 테두리·번진 가장자리가 마스크 밖에 남으면 LaMa가 글자 모양 얼룩을 되살린다. 6px까지 덮는다.
        clipped, text_mask = _manual_ubox_text_mask(image, rect, anchor_groups, polarity="bright", dilate_px=6)
        if text_mask is None:
            return False
        x, y, w, h = clipped

    safe_text_mask = text_mask
    if protected_mask is not None:
        roi_protected = protected_mask[y:y + h, x:x + w]
        safe_text_mask = np.where((text_mask > 0) & (roi_protected == 0), 255, 0).astype(np.uint8)
        if safe_text_mask.max() == 0:
            return False

    context_rect = _padded_context_rect(clipped, image.shape, padding=context_padding)
    if context_rect is None:
        return False
    cx, cy, cw, ch = context_rect
    context_mask = np.zeros((ch, cw), dtype=np.uint8)
    lx = x - cx
    ly = y - cy
    if lx < 0 or ly < 0 or lx + w > cw or ly + h > ch:
        return False
    context_mask[ly:ly + h, lx:lx + w] = safe_text_mask
    if context_mask.max() == 0:
        return False

    roi = image[cy:cy + ch, cx:cx + cw].copy()
    engine = str(item.get("inpaint_engine_override") or background_meta.get("recommended_inpaint_engine") or "lama")
    if engine == "telea":
        inpaint_roi = cv2.inpaint(roi, context_mask, 5, cv2.INPAINT_TELEA)
    else:
        lama = _get_lama()
        roi_pil = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))
        lama_out = lama(roi_pil, Image.fromarray(context_mask))
        inpaint_roi = cv2.cvtColor(np.array(lama_out), cv2.COLOR_RGB2BGR)
    lh, lw = inpaint_roi.shape[:2]
    apply_h, apply_w = min(ch, lh), min(cw, lw)
    target = masked_image[cy:cy + apply_h, cx:cx + apply_w]
    apply_mask = context_mask[:apply_h, :apply_w] > 0
    target[apply_mask] = inpaint_roi[:apply_h, :apply_w][apply_mask]
    item["inpaint_skipped"] = False
    item["inpaint_context_rect"] = context_rect
    item["manual_inpaint_background_class"] = background_meta.get("background_class")
    item["manual_inpaint_engine"] = engine
    item["manual_mask_polarity"] = background_meta.get("manual_mask_polarity")
    item["quality_flags"] = list(dict.fromkeys(
        [str(flag) for flag in item.get("quality_flags", [])] + [f"manual_ubox_{engine}"]
    ))
    return True


def _manual_wall_groups(virtual_walls: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    by_box: Dict[str, List[Dict[str, Any]]] = {}
    loose: List[List[Dict[str, Any]]] = []
    for item in virtual_walls or []:
        box_id = str(item.get("box_id") or "")
        if box_id:
            by_box.setdefault(box_id, []).append(item)
        else:
            loose.append([item])
    return list(by_box.values()) + loose


def _wall_group_rect(walls: List[Dict[str, Any]]) -> List[int]:
    points: List[Tuple[int, int]] = []
    for item in walls:
        raw = item.get("line") or item.get("polyline")
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        try:
            for point in (raw[0], raw[-1]):
                points.append((int(point[0]), int(point[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if not points:
        return [0, 0, 0, 0]
    x1 = min(point[0] for point in points)
    y1 = min(point[1] for point in points)
    x2 = max(point[0] for point in points)
    y2 = max(point[1] for point in points)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _clip_manual_edge_rescue_to_wall_rect(
    mask: np.ndarray,
    wall_rect: List[int],
    image_shape: Tuple[int, int],
) -> Tuple[np.ndarray, List[int]]:
    """Manual edge box는 사용자가 지정한 폐쇄 경계 밖으로 확장하지 않는다."""
    img_h, img_w = image_shape[:2]
    wx, wy, ww, wh = _as_rect(wall_rect)
    x1 = max(0, wx)
    y1 = max(0, wy)
    x2 = min(img_w, wx + ww)
    y2 = min(img_h, wy + wh)
    clipped = np.zeros((img_h, img_w), dtype=np.uint8)
    if x2 <= x1 or y2 <= y1:
        return clipped, [0, 0, 0, 0]

    clipped[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
    ys, xs = np.where(clipped > 0)
    if xs.size == 0 or ys.size == 0:
        return clipped, [0, 0, 0, 0]
    mx1 = int(xs.min())
    my1 = int(ys.min())
    mx2 = int(xs.max()) + 1
    my2 = int(ys.max()) + 1
    return clipped, [mx1, my1, mx2 - mx1, my2 - my1]


def _manual_edge_box_mask(
    wall_rect: List[int],
    image_shape: Tuple[int, int],
) -> Tuple[np.ndarray, List[int]]:
    """Edge rescue가 실패해도 사용자가 지정한 box 자체를 region으로 사용한다."""
    img_h, img_w = image_shape[:2]
    wx, wy, ww, wh = _as_rect(wall_rect)
    x1 = max(0, wx)
    y1 = max(0, wy)
    x2 = min(img_w, wx + ww)
    y2 = min(img_h, wy + wh)
    mask = np.zeros((img_h, img_w), dtype=np.uint8)
    if x2 <= x1 or y2 <= y1:
        return mask, [0, 0, 0, 0]
    mask[y1:y2, x1:x2] = 255
    return mask, [x1, y1, x2 - x1, y2 - y1]


def _wall_groups_touching_rect(
    virtual_walls: List[Dict[str, Any]],
    rect: List[int],
    pad: int = 32,
) -> List[Dict[str, Any]]:
    local: List[Dict[str, Any]] = []
    for walls in _manual_wall_groups(virtual_walls or []):
        if not any((_line_points(wall) and _line_touches_rect(*_line_points(wall), rect, pad=pad)) for wall in walls):
            continue
        local.extend(walls)
    return local


def _manual_edge_barrier_regions(
    image: np.ndarray,
    groups: List[Dict[str, Any]],
    virtual_walls: List[Dict[str, Any]],
    diagnostics: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    try:
        from pipeline.edge_barrier_flood_rescue import run_edge_barrier_rescue_auto
    except ImportError:
        from edge_barrier_flood_rescue import run_edge_barrier_rescue_auto

    regions: List[Dict[str, Any]] = []
    group_by_id = {_tg_id(group, idx): group for idx, group in enumerate(groups, start=1)}
    for index, walls in enumerate(_manual_wall_groups(virtual_walls), start=1):
        rect = _wall_group_rect(walls)
        if rect[2] <= 0 or rect[3] <= 0:
            if diagnostics is not None:
                diagnostics.append({
                    "box_index": index,
                    "box_id": str(walls[0].get("box_id") or f"edge_box_{index}") if walls else f"edge_box_{index}",
                    "ok": False,
                    "reason": "empty_edge_box",
                    "rect": rect,
                    "anchor_ids": [],
                    "used_box_fallback": False,
                })
            continue
        anchor_ids = _manual_region_anchor_ids(rect, groups)
        target_groups = [group_by_id[tid] for tid in anchor_ids if tid in group_by_id]
        rescue: Dict[str, Any] = {}
        rescue_mask: np.ndarray
        rescue_rect: List[int]
        used_box_fallback = True
        if target_groups:
            rescue = run_edge_barrier_rescue_auto(
                image,
                target_groups,
                rect,
                virtual_walls=walls,
            )
            raw_rescue_mask = rescue.get("mask")
            if rescue.get("success") and isinstance(raw_rescue_mask, np.ndarray):
                rescue_mask, rescue_rect = _clip_manual_edge_rescue_to_wall_rect(
                    raw_rescue_mask,
                    rect,
                    image.shape[:2],
                )
                used_box_fallback = rescue_rect[2] <= 0 or rescue_rect[3] <= 0
            else:
                rescue_mask, rescue_rect = _manual_edge_box_mask(rect, image.shape[:2])
        else:
            rescue_mask, rescue_rect = _manual_edge_box_mask(rect, image.shape[:2])
        if used_box_fallback:
            rescue_mask, rescue_rect = _manual_edge_box_mask(rect, image.shape[:2])
        if rescue_rect[2] <= 0 or rescue_rect[3] <= 0:
            if diagnostics is not None:
                diagnostics.append({
                    "box_index": index,
                    "box_id": str(walls[0].get("box_id") or f"edge_box_{index}") if walls else f"edge_box_{index}",
                    "ok": False,
                    "reason": str(rescue.get("reason") or "empty_edge_region"),
                    "rect": rect,
                    "anchor_ids": anchor_ids,
                    "used_box_fallback": used_box_fallback,
                })
            continue
        anchor_ids = _manual_seed_anchor_ids_from_result(rescue_rect, rescue_mask, groups) or anchor_ids
        score = rescue.get("score", {})
        quality_flags = [
            "manual_edge_barrier",
            f"edge_best={rescue.get('best_label')}",
            f"edge_score={round(float(score.get('rank_score', 0.0)), 3)}",
            f"edge_ratio={round(float(score.get('rect_anchor_ratio', 0.0)), 2)}",
            f"edge_page={round(float(score.get('mask_page_ratio', 0.0)), 4)}",
        ]
        if used_box_fallback:
            quality_flags.append(f"manual_edge_box_fallback={rescue.get('reason', 'no_target_group')}")
        if diagnostics is not None:
            diagnostics.append({
                "box_index": index,
                "box_id": str(walls[0].get("box_id") or f"edge_box_{index}") if walls else f"edge_box_{index}",
                "ok": True,
                "reason": str(rescue.get("reason") or ("no_target_group" if used_box_fallback else "edge_barrier_ok")),
                "rect": rect,
                "region_rect": rescue_rect,
                "anchor_ids": anchor_ids,
                "used_box_fallback": used_box_fallback,
                "region_id": 11_000 + index,
                "wall_count": len(walls),
            })
        regions.append({
            "region_id": 11_000 + index,
            "anchor_ids": anchor_ids,
            "seed_points": [rescue["seed"]] if rescue.get("seed") else [],
            "bubble_rect": rescue_rect,
            "mask": rescue_mask,
            "is_valid": True,
            "method": "manual_edge_barrier_rescue",
            "region_type_guess": "bubble",
            "bubble_page_ratio": float(score.get("mask_page_ratio", 0.0)),
            "bubble_tg_ratio": float(score.get("rect_anchor_ratio", 0.0)),
            "quality_flags": quality_flags,
            "review_tags": ["manual_edge_barrier"],
        })
    return regions


def _seed_point(item: Dict[str, Any]) -> List[int] | None:
    raw = item.get("point") or item.get("seed")
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    try:
        return [int(raw[0]), int(raw[1])]
    except (TypeError, ValueError):
        return None


def _point_source_rect(point: List[int], img_w: int, img_h: int, pad: int = 180) -> List[int]:
    x, y = point
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img_w, x + pad)
    y2 = min(img_h, y + pad)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _manual_seed_candidates(point: List[int], gray: np.ndarray) -> List[Tuple[int, int]]:
    x, y = int(point[0]), int(point[1])
    h, w = gray.shape[:2]
    candidates: List[Tuple[int, int]] = []
    for radius in (0, 4, 8, 12, 18):
        for dy in range(-radius, radius + 1, max(1, radius or 1)):
            for dx in range(-radius, radius + 1, max(1, radius or 1)):
                px, py = x + dx, y + dy
                if 0 <= px < w and 0 <= py < h and int(gray[py, px]) >= 160:
                    candidates.append((px, py))
        if candidates:
            break
    return list(dict.fromkeys(candidates or [(max(0, min(w - 1, x)), max(0, min(h - 1, y)))]))


def _seed_rescue_is_too_large(rect: List[int], page_ratio: float, image_shape: Tuple[int, ...], strict: bool = True) -> bool:
    """seed guidance는 seed 주변 bubble용이므로 패널/페이지급 확장은 버린다.

    strict=False(명시적 사용자 seed)이면 완화된 기준을 적용한다.
    페이지 전체에 가까운 비율은 strict 여부와 관계없이 거부한다.
    """
    x, y, w, h = _as_rect(rect)
    img_h, img_w = image_shape[:2]
    if w <= 0 or h <= 0 or img_w <= 0 or img_h <= 0:
        return True
    width_ratio = w / max(1, img_w)
    height_ratio = h / max(1, img_h)
    if strict:
        # 한 축이 거의 페이지 전체로 번지면 작은 bubble seed 의도에서 벗어난다.
        if width_ratio >= 0.86 or height_ratio >= 0.86:
            return True
        # 양축이 동시에 큰 경우는 패널 배경으로 샌 케이스가 대부분이다.
        if width_ratio >= 0.68 and height_ratio >= 0.68:
            return True
        if page_ratio > 0.38:
            return True
    else:
        # 명시적 사용자 seed: 페이지 전체 수준만 거부 (flood가 배경으로 샌 케이스)
        if width_ratio >= 0.95 or height_ratio >= 0.95:
            return True
        if width_ratio >= 0.85 and height_ratio >= 0.85:
            return True
        if page_ratio > 0.65:
            return True
    return False


def _manual_seed_bright_component(
    image: np.ndarray,
    point: List[int],
    virtual_walls: List[Dict[str, Any]] | None = None,
    bright_threshold: int = 190,
) -> Dict[str, Any] | None:
    """seed 주변의 흰 connected component를 검은 선 장벽 기준으로 먼저 찾는다."""
    img_h, img_w = image.shape[:2]
    guarded = _draw_seed_virtual_walls(image, virtual_walls or [])
    gray = cv2.cvtColor(guarded, cv2.COLOR_BGR2GRAY)
    bright = np.where(gray >= bright_threshold, 255, 0).astype(np.uint8)
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(bright, connectivity=8)
    image_area = max(1, img_h * img_w)
    best: Dict[str, Any] | None = None

    for seed in _manual_seed_candidates(point, gray):
        sx, sy = int(seed[0]), int(seed[1])
        if not (0 <= sx < img_w and 0 <= sy < img_h):
            continue
        label = int(labels[sy, sx])
        if label <= 0:
            continue
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < 80 or w <= 0 or h <= 0:
            continue
        component = np.where(labels == label, 255, 0).astype(np.uint8)
        mask = _fill_mask_holes(component)
        rect = _mask_bounding_rect(mask) or [x, y, w, h]
        rx, ry, rw, rh = _as_rect(rect)
        page_ratio = float(np.count_nonzero(mask) / image_area)
        touches_page = (
            np.any(mask[:2, :] > 0)
            or np.any(mask[-2:, :] > 0)
            or np.any(mask[:, :2] > 0)
            or np.any(mask[:, -2:] > 0)
        )
        if touches_page or _seed_rescue_is_too_large([rx, ry, rw, rh], page_ratio, image.shape):
            continue
        distance = float(np.hypot(sx - int(point[0]), sy - int(point[1])))
        score = float(np.count_nonzero(mask)) - page_ratio * image_area * 0.8 - distance * 25.0
        if best is None or score > float(best["rank_score"]):
            best = {
                "success": True,
                "reason": "manual_seed_bright_component_ok",
                "seed": [sx, sy],
                "rescue_rect": [int(rx), int(ry), int(rw), int(rh)],
                "mask": mask,
                "score": {
                    "mask_area": float(np.count_nonzero(mask)),
                    "mask_page_ratio": page_ratio,
                    "rect_anchor_ratio": 0.0,
                    "rank_score": score,
                    "bright_threshold": float(bright_threshold),
                },
                "best_label": f"bright_component{bright_threshold}",
                "rank_score": score,
            }
    return best


def _draw_seed_virtual_walls(image: np.ndarray, virtual_walls: List[Dict[str, Any]]) -> np.ndarray:
    guarded = image.copy()
    for item in virtual_walls or []:
        raw = item.get("line") or item.get("polyline")
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        try:
            p1 = (int(raw[0][0]), int(raw[0][1]))
            p2 = (int(raw[-1][0]), int(raw[-1][1]))
        except (TypeError, ValueError, IndexError):
            continue
        cv2.line(guarded, p1, p2, (0, 0, 0), 11, cv2.LINE_AA)
    return guarded


def _manual_seed_direct_flood(
    image: np.ndarray,
    point: List[int],
    virtual_walls: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    img_h, img_w = image.shape[:2]
    component_result = _manual_seed_bright_component(image, point, virtual_walls)
    if component_result is not None:
        return component_result

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    guarded = _draw_seed_virtual_walls(image, virtual_walls or [])
    image_area = max(1, img_h * img_w)
    best: Dict[str, Any] | None = None

    for seed in _manual_seed_candidates(point, gray):
        for diff in (8, 14, 22, 32):
            work = guarded.copy()
            mask_pad = np.zeros((img_h + 2, img_w + 2), dtype=np.uint8)
            flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
            area, _, _, rect = cv2.floodFill(
                work,
                mask_pad,
                seedPoint=seed,
                newVal=(0, 0, 0),
                loDiff=(diff, diff, diff),
                upDiff=(diff, diff, diff),
                flags=flags,
            )
            if area < 80:
                continue
            mask = _fill_mask_holes(mask_pad[1:-1, 1:-1])
            rect = _mask_bounding_rect(mask) or [0, 0, 0, 0]
            rx, ry, rw, rh = _as_rect(rect)
            if rw <= 0 or rh <= 0:
                continue
            touches_page = (
                np.any(mask[:2, :] > 0)
                or np.any(mask[-2:, :] > 0)
                or np.any(mask[:, :2] > 0)
                or np.any(mask[:, -2:] > 0)
            )
            page_ratio = float(np.count_nonzero(mask) / image_area)
            if touches_page or _seed_rescue_is_too_large([rx, ry, rw, rh], page_ratio, image.shape):
                continue
            score = (
                float(np.count_nonzero(mask))
                - float(np.count_nonzero(mask)) * page_ratio * 1.35
                - max(0.0, (rw / max(1, img_w)) - 0.65) * image_area * 0.18
                - max(0.0, (rh / max(1, img_h)) - 0.65) * image_area * 0.18
                - abs(seed[0] - int(point[0])) * 10
                - abs(seed[1] - int(point[1])) * 10
                - diff * 2
            )
            if best is None or score > float(best["rank_score"]):
                best = {
                    "success": True,
                    "reason": "manual_seed_direct_flood_ok",
                    "seed": [int(seed[0]), int(seed[1])],
                    "rescue_rect": [int(rx), int(ry), int(rw), int(rh)],
                    "mask": mask,
                    "score": {
                        "mask_area": float(np.count_nonzero(mask)),
                        "mask_page_ratio": page_ratio,
                        "rect_anchor_ratio": 0.0,
                        "rank_score": score,
                        "flood_diff": float(diff),
                    },
                    "best_label": f"direct_diff{diff}",
                    "rank_score": score,
                }

    return best or {
        "success": False,
        "reason": "manual_seed_direct_flood_failed",
        "seed": [int(point[0]), int(point[1])],
        "rescue_rect": [0, 0, 0, 0],
        "mask": np.zeros((img_h, img_w), dtype=np.uint8),
        "score": {},
        "best_label": None,
    }


def _manual_seed_bubble_regions(
    image: np.ndarray,
    groups: List[Dict[str, Any]],
    seed_points: List[Dict[str, Any]],
    virtual_walls: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    try:
        from pipeline.edge_barrier_flood_rescue import run_edge_barrier_rescue_auto
    except ImportError:
        from edge_barrier_flood_rescue import run_edge_barrier_rescue_auto

    img_h, img_w = image.shape[:2]
    regions: List[Dict[str, Any]] = []
    group_by_id = {_tg_id(group, idx): group for idx, group in enumerate(groups, start=1)}
    for index, item in enumerate(seed_points or [], start=1):
        point = _seed_point(item)
        if point is None:
            continue
        source_rect = _point_source_rect(point, img_w, img_h)
        anchor_ids = _manual_region_anchor_ids(source_rect, groups)
        target_groups = [group_by_id[tid] for tid in anchor_ids if tid in group_by_id]
        if not target_groups:
            target_groups = [{
                "text_group_id": -(20_000 + index),
                "tight_rect": [max(0, point[0] - 4), max(0, point[1] - 4), 8, 8],
                "bounding_rect": [max(0, point[0] - 4), max(0, point[1] - 4), 8, 8],
                "lines": [],
                "text": "",
            }]
        local_wall_rect = _point_source_rect(point, img_w, img_h, pad=96)
        local_walls = _wall_groups_touching_rect(virtual_walls or [], local_wall_rect)
        rescue = _manual_seed_direct_flood(image, point, local_walls)
        if not rescue.get("success"):
            rescue = run_edge_barrier_rescue_auto(
                image,
                target_groups,
                source_rect,
                virtual_walls=local_walls or None,
                seed_points=[(point[0], point[1])],
            )
        if not rescue.get("success"):
            continue
        rescue_rect = [int(v) for v in rescue.get("rescue_rect", [0, 0, 0, 0])[:4]]
        if rescue_rect[2] <= 0 or rescue_rect[3] <= 0:
            continue
        score = rescue.get("score", {})
        page_ratio = float(score.get("mask_page_ratio", 1.0))
        # 명시적 사용자 seed이므로 완화된 크기 기준 적용
        if _seed_rescue_is_too_large(rescue_rect, page_ratio, image.shape, strict=False):
            continue
        rescue_mask = rescue.get("mask")
        refined_anchor_ids = _manual_seed_anchor_ids_from_result(rescue_rect, rescue_mask, groups)
        regions.append({
            "region_id": 12_000 + index,
            "anchor_ids": refined_anchor_ids,
            "seed_points": [point],
            "bubble_rect": rescue_rect,
            "mask": rescue_mask,
            "is_valid": True,
            "method": "manual_bubble_seed_rescue",
            "region_type_guess": "bubble",
            "bubble_page_ratio": float(score.get("mask_page_ratio", 0.0)),
            "bubble_tg_ratio": float(score.get("rect_anchor_ratio", 0.0)),
            "quality_flags": [
                "manual_bubble_seed",
                str(rescue.get("reason") or "manual_seed_rescue"),
                "manual_relaxed_gate",
                f"seed_source_anchors={len(anchor_ids)}",
                f"seed_refined_anchors={len(refined_anchor_ids)}",
                f"edge_best={rescue.get('best_label')}",
                f"edge_score={round(float(score.get('rank_score', 0.0)), 3)}",
                f"edge_page={round(float(score.get('mask_page_ratio', 0.0)), 4)}",
            ],
            "review_tags": ["manual_bubble_seed"],
        })
    return regions


def _line_points(item: Dict[str, Any]) -> Tuple[Tuple[int, int], Tuple[int, int]] | None:
    raw = item.get("line") or item.get("polyline")
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    try:
        p1 = (int(raw[0][0]), int(raw[0][1]))
        p2 = (int(raw[-1][0]), int(raw[-1][1]))
    except (TypeError, ValueError, IndexError):
        return None
    if p1 == p2:
        return None
    return p1, p2


def _line_touches_rect(p1: Tuple[int, int], p2: Tuple[int, int], rect: List[int], pad: int = 16) -> bool:
    x, y, w, h = _as_rect(rect)
    lx1, lx2 = sorted([p1[0], p2[0]])
    ly1, ly2 = sorted([p1[1], p2[1]])
    return not (
        lx2 < x - pad
        or lx1 > x + w + pad
        or ly2 < y - pad
        or ly1 > y + h + pad
    )


def _line_angle_delta(a1: Tuple[int, int], a2: Tuple[int, int], b1: Tuple[int, int], b2: Tuple[int, int]) -> float:
    angle_a = float(np.degrees(np.arctan2(a2[1] - a1[1], a2[0] - a1[0])))
    angle_b = float(np.degrees(np.arctan2(b2[1] - b1[1], b2[0] - b1[0])))
    delta = abs((angle_a - angle_b + 90.0) % 180.0 - 90.0)
    return delta


def _segment_midpoint(p1: Tuple[int, int], p2: Tuple[int, int]) -> Tuple[float, float]:
    return ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)


def _point_to_segment_distance(pt: Tuple[float, float], p1: Tuple[int, int], p2: Tuple[int, int]) -> float:
    px, py = pt
    x1, y1 = p1
    x2, y2 = p2
    dx = float(x2 - x1)
    dy = float(y2 - y1)
    denom = dx * dx + dy * dy
    if denom <= 1e-6:
        return float(np.hypot(px - x1, py - y1))
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denom))
    return float(np.hypot(px - (x1 + dx * t), py - (y1 + dy * t)))


def _snap_manual_line_to_defects(
    binary: np.ndarray,
    anchor_centers: List[Tuple[int, int]],
    anchor_rects: List[List[int]],
    p1: Tuple[int, int],
    p2: Tuple[int, int],
) -> Tuple[Tuple[int, int], Tuple[int, int], str | None]:
    candidate_info = collect_inflection_candidates(
        binary,
        anchor_centers,
        min_defect_depth=5.0,
        anchor_rects=anchor_rects,
        allow_single_weak_turn=True,
        max_pair_points=12,
    )
    candidates = candidate_info.get("candidates") or []
    if not candidates:
        return p1, p2, None

    manual_mid = _segment_midpoint(p1, p2)
    manual_len = max(1.0, float(np.hypot(p2[0] - p1[0], p2[1] - p1[1])))
    best: Dict[str, Any] | None = None
    best_score = float("inf")
    max_mid_distance = max(36.0, manual_len * 1.6)

    for candidate in candidates:
        raw_p1 = candidate.get("p1")
        raw_p2 = candidate.get("p2")
        if not isinstance(raw_p1, list) or not isinstance(raw_p2, list):
            continue
        cp1 = (int(raw_p1[0]), int(raw_p1[1]))
        cp2 = (int(raw_p2[0]), int(raw_p2[1]))
        angle_delta = _line_angle_delta(p1, p2, cp1, cp2)
        if angle_delta > 38.0:
            continue
        candidate_mid = _segment_midpoint(cp1, cp2)
        mid_distance = float(np.hypot(candidate_mid[0] - manual_mid[0], candidate_mid[1] - manual_mid[1]))
        if mid_distance > max_mid_distance:
            continue
        endpoint_distance = min(
            _point_to_segment_distance(cp1, p1, p2) + _point_to_segment_distance(cp2, p1, p2),
            _point_to_segment_distance(cp2, p1, p2) + _point_to_segment_distance(cp1, p1, p2),
        )
        if endpoint_distance > max(60.0, manual_len * 1.8):
            continue
        line_length = float(candidate.get("split_line_length") or np.hypot(cp2[0] - cp1[0], cp2[1] - cp1[1]))
        score = angle_delta * 2.4 + mid_distance + endpoint_distance * 0.35 - min(60.0, line_length) * 0.08
        if candidate.get("component_separates"):
            score -= 18.0
        if candidate.get("bboxes_clear"):
            score -= 8.0
        if score < best_score:
            best = {"p1": cp1, "p2": cp2}
            best_score = score

    if best is None:
        return p1, p2, None
    return best["p1"], best["p2"], "manual_split_defect_snap"


def _split_mask_by_manual_line(
    mask: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Tuple[int, int]],
    anchor_rects: List[List[int]],
    p1: Tuple[int, int],
    p2: Tuple[int, int],
) -> List[Dict[str, Any]] | None:
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    if not np.any(binary > 0):
        return None
    p1, p2, snap_source = _snap_manual_line_to_defects(binary, anchor_centers, anchor_rects, p1, p2)
    h, w = binary.shape[:2]
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    if dx == 0 and dy == 0:
        return None

    ys, xs = np.mgrid[0:h, 0:w]
    side_map = (-dy) * (xs - p1[0]) + dx * (ys - p1[1])
    side_masks = [
        ((binary > 0) & (side_map >= 0)).astype(np.uint8) * 255,
        ((binary > 0) & (side_map < 0)).astype(np.uint8) * 255,
    ]
    total_area = int(np.count_nonzero(binary))
    min_area = max(80, min(900, int(total_area * 0.06)))
    if any(int(np.count_nonzero(sm)) < min_area for sm in side_masks):
        return None

    def side_for(point: Tuple[int, int]) -> int:
        return 0 if ((-dy) * (point[0] - p1[0]) + dx * (point[1] - p1[1])) >= 0 else 1

    split: List[Dict[str, Any]] = []
    for side_idx, sub_mask in enumerate(side_masks):
        ys_s, xs_s = np.where(sub_mask > 0)
        if not len(xs_s):
            return None
        bbox = [
            int(xs_s.min()),
            int(ys_s.min()),
            int(xs_s.max() - xs_s.min() + 1),
            int(ys_s.max() - ys_s.min() + 1),
        ]
        split.append({
            "anchor_ids": [
                int(aid)
                for aid, center in zip(anchor_ids, anchor_centers)
                if side_for(center) == side_idx
            ],
            "sub_mask": sub_mask,
            "bubble_rect": bbox,
            "split_line": [list(p1), list(p2)],
            "split_source": snap_source,
        })
    return split


def _split_rect_by_manual_line(
    shape: Tuple[int, int],
    parent_rect: List[int],
    anchor_ids: List[int],
    anchor_centers: List[Tuple[int, int]],
    anchor_rects: List[List[int]] | None,
    p1: Tuple[int, int],
    p2: Tuple[int, int],
) -> List[Dict[str, Any]] | None:
    h, w = shape[:2]
    x, y, rw, rh = _as_rect(parent_rect)
    if rw <= 1 or rh <= 1:
        return None
    x1 = max(0, min(w, x))
    y1 = max(0, min(h, y))
    x2 = max(0, min(w, x + rw))
    y2 = max(0, min(h, y + rh))
    if x2 - x1 <= 1 or y2 - y1 <= 1:
        return None
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    if dx == 0 and dy == 0:
        return None

    ys, xs = np.mgrid[0:h, 0:w]
    parent = np.zeros((h, w), dtype=bool)
    parent[y1:y2, x1:x2] = True
    side_map = (-dy) * (xs - p1[0]) + dx * (ys - p1[1])
    side_masks = [
        np.where(parent & (side_map >= 0), 255, 0).astype(np.uint8),
        np.where(parent & (side_map < 0), 255, 0).astype(np.uint8),
    ]
    if any(int(np.count_nonzero(side)) <= 0 for side in side_masks):
        return None

    def side_for(point: Tuple[int, int]) -> int:
        return 0 if ((-dy) * (point[0] - p1[0]) + dx * (point[1] - p1[1])) >= 0 else 1

    split: List[Dict[str, Any]] = []
    for side_idx, sub_mask in enumerate(side_masks):
        bbox = _mask_bounding_rect(sub_mask)
        if bbox is None:
            return None
        side_anchor_ids = [
            int(aid)
            for aid, center in zip(anchor_ids, anchor_centers)
            if side_for(center) == side_idx
        ]
        if side_anchor_ids:
            side_anchor_rects = [
                rect
                for aid, rect in zip(anchor_ids, anchor_rects or [])
                if int(aid) in side_anchor_ids
            ]
            if side_anchor_rects:
                bbox = _manual_split_anchor_bbox(side_anchor_rects, parent_rect, bbox)
        split.append({
            "anchor_ids": side_anchor_ids,
            "sub_mask": sub_mask,
            "bubble_rect": bbox,
            "split_line": [list(p1), list(p2)],
            "split_source": "manual_split_rect_fallback",
        })
    return split


def _manual_split_anchor_bbox(
    anchor_rects: List[List[int]],
    parent_rect: List[int],
    side_rect: List[int],
) -> List[int]:
    ax, ay, aw, ah = _union_rect([_as_rect(rect) for rect in anchor_rects])
    px, py, pw, ph = _as_rect(parent_rect)
    sx, sy, sw, sh = _as_rect(side_rect)
    pad_x = int(max(28, min(pw * 0.22, max(aw * 0.75, 1))))
    pad_y = int(max(36, min(ph * 0.22, max(ah * 0.42, 1))))
    x1 = max(px, sx, ax - pad_x)
    y1 = max(py, sy, ay - pad_y)
    x2 = min(px + pw, sx + sw, ax + aw + pad_x)
    y2 = min(py + ph, sy + sh, ay + ah + pad_y)
    if x2 <= x1 or y2 <= y1:
        return side_rect
    return [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]


def _augment_manual_split_anchors(
    anchor_ids: List[int],
    groups_by_id: Dict[int, Dict[str, Any]],
    parent_rect: List[int],
) -> List[int]:
    """수동 split parent rect 안에 들어온 누락 TG를 split anchor 후보로 보강한다."""
    px, py, pw, ph = _as_rect(parent_rect)
    if pw <= 0 or ph <= 0:
        return anchor_ids
    existing = {int(aid) for aid in anchor_ids}
    augmented = list(anchor_ids)
    for gid, group in groups_by_id.items():
        if int(gid) in existing:
            continue
        gx, gy, gw, gh = _group_rect(group)
        if gw <= 0 or gh <= 0:
            continue
        cx = gx + gw / 2.0
        cy = gy + gh / 2.0
        if px <= cx <= px + pw and py <= cy <= py + ph:
            augmented.append(int(gid))
            existing.add(int(gid))
    return augmented


def _split_mask_by_projection_neck(
    mask: np.ndarray,
    anchor_ids: List[int],
    anchor_centers: List[Tuple[int, int]],
) -> List[Dict[str, Any]] | None:
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    if not np.any(binary > 0):
        return None
    rect = _mask_bounding_rect(binary)
    if rect is None:
        return None
    x, y, w, h = _as_rect(rect)
    if w < 36 or h < 36:
        return None

    axis = 0 if w >= h * 0.72 else 1
    roi = binary[y:y + h, x:x + w] > 0
    projection = roi.sum(axis=0 if axis == 0 else 1).astype(np.float32)
    if len(projection) < 12:
        return None
    kernel = np.ones(7, dtype=np.float32) / 7.0
    smooth = np.convolve(projection, kernel, mode="same")
    start = max(3, int(len(smooth) * 0.22))
    end = min(len(smooth) - 3, int(len(smooth) * 0.78))
    if end <= start:
        return None
    cut_local = int(start + np.argmin(smooth[start:end]))
    max_projection = float(np.max(smooth))
    if max_projection <= 0 or float(smooth[cut_local]) > max_projection * 0.62:
        return None

    if axis == 0:
        cut = x + cut_local
        side_masks = [
            np.where((binary > 0) & (np.indices(binary.shape)[1] < cut), 255, 0).astype(np.uint8),
            np.where((binary > 0) & (np.indices(binary.shape)[1] >= cut), 255, 0).astype(np.uint8),
        ]
        split_line = [[cut, y], [cut, y + h]]
        side_for = lambda point: 0 if point[0] < cut else 1
    else:
        cut = y + cut_local
        side_masks = [
            np.where((binary > 0) & (np.indices(binary.shape)[0] < cut), 255, 0).astype(np.uint8),
            np.where((binary > 0) & (np.indices(binary.shape)[0] >= cut), 255, 0).astype(np.uint8),
        ]
        split_line = [[x, cut], [x + w, cut]]
        side_for = lambda point: 0 if point[1] < cut else 1

    total_area = int(np.count_nonzero(binary))
    min_area = max(700, int(total_area * 0.16))
    if any(int(np.count_nonzero(side)) < min_area for side in side_masks):
        return None

    result: List[Dict[str, Any]] = []
    for side_idx, sub_mask in enumerate(side_masks):
        sub_rect = _mask_bounding_rect(sub_mask)
        if sub_rect is None:
            return None
        result.append({
            "anchor_ids": [
                int(aid)
                for aid, center in zip(anchor_ids, anchor_centers)
                if side_for(center) == side_idx
            ],
            "sub_mask": sub_mask,
            "bubble_rect": sub_rect,
            "split_line": split_line,
        })
    if not any(item["anchor_ids"] for item in result):
        return None
    return result


def _split_region_variants(
    region: Dict[str, Any],
    groups_by_id: Dict[int, Dict[str, Any]],
    split_guides: List[Dict[str, Any]],
    image_shape: Tuple[int, int] | None = None,
) -> List[Dict[str, Any]]:
    if not region.get("is_valid"):
        return [region]
    mask = region.get("mask") if isinstance(region.get("mask"), np.ndarray) else None
    anchor_ids: List[int] = []
    for raw_aid in region.get("anchor_ids", []):
        try:
            aid = int(raw_aid)
        except (TypeError, ValueError):
            continue
        if aid in groups_by_id:
            anchor_ids.append(aid)
    anchor_rects = [_group_rect(groups_by_id[aid]) for aid in anchor_ids]
    anchor_centers = [
        (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
        for rect in anchor_rects
    ]
    rect = region.get("bubble_rect") or region.get("source_rect") or region.get("ocr_rect")
    if not rect and mask is not None:
        mask_rect = _mask_bounding_rect(mask)
        rect = mask_rect or [0, 0, 0, 0]
    if not rect:
        return [region]
    parent_rect = [int(v) for v in _as_rect(rect)]

    split_result = None
    split_source = None
    for guide in split_guides or []:
        points = _line_points(guide)
        if not points:
            continue
        p1, p2 = points
        if not _line_touches_rect(p1, p2, parent_rect):
            continue
        guide_anchor_ids = _augment_manual_split_anchors(anchor_ids, groups_by_id, parent_rect)
        guide_anchor_rects = [_group_rect(groups_by_id[aid]) for aid in guide_anchor_ids if aid in groups_by_id]
        guide_anchor_centers = [
            (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
            for rect in guide_anchor_rects
        ]
        if mask is not None:
            split_result = _split_mask_by_manual_line(mask, guide_anchor_ids, guide_anchor_centers, guide_anchor_rects, p1, p2)
            if split_result is not None:
                if sum(1 for item in split_result if item.get("anchor_ids")) >= 2:
                    split_source = next((str(item.get("split_source")) for item in split_result if item.get("split_source")), None) or "manual_split_guide"
                    break
                split_result = None
            split_shape = mask.shape[:2]
        elif image_shape is not None:
            split_shape = image_shape
        else:
            max_x = max([parent_rect[0] + parent_rect[2], p1[0], p2[0], *[r[0] + r[2] for r in guide_anchor_rects]], default=parent_rect[0] + parent_rect[2])
            max_y = max([parent_rect[1] + parent_rect[3], p1[1], p2[1], *[r[1] + r[3] for r in guide_anchor_rects]], default=parent_rect[1] + parent_rect[3])
            split_shape = (max_y + 4, max_x + 4)
        split_result = _split_rect_by_manual_line(split_shape, parent_rect, guide_anchor_ids, guide_anchor_centers, guide_anchor_rects, p1, p2)
        if split_result is not None:
            split_source = "manual_split_rect_fallback"
            break

    if split_result is None and mask is not None and region.get("method") == "manual_bubble_seed_rescue":
        if len(anchor_ids) >= 2:
            split_result = split_mask_by_inflection_line(mask, anchor_ids, anchor_centers, anchor_rects=anchor_rects)
            split_source = "manual_seed_auto_inflection_split" if split_result is not None else None
            if split_result is None:
                split_result = split_mask_by_erosion_components(mask, anchor_ids, anchor_centers, max_erosion=36)
                split_source = "manual_seed_auto_erosion_split" if split_result is not None else None
        if split_result is None and anchor_ids:
            split_result = _split_mask_by_projection_neck(mask, anchor_ids, anchor_centers)
            split_source = "manual_seed_projection_neck_split" if split_result is not None else None

    if not split_result:
        return [region]

    variants: List[Dict[str, Any]] = []
    for index, sub in enumerate(sorted(split_result, key=lambda item: (_as_rect(item.get("bubble_rect", [0, 0, 0, 0]))[1], -_as_rect(item.get("bubble_rect", [0, 0, 0, 0]))[0]))):
        child_rect = [int(v) for v in _as_rect(sub.get("bubble_rect", parent_rect))]
        child_anchor_ids = [int(aid) for aid in sub.get("anchor_ids", [])]
        if not child_anchor_ids:
            continue
        child_anchor_rects = [_group_rect(groups_by_id[aid]) for aid in child_anchor_ids if aid in groups_by_id]
        child = {
            **region,
            "anchor_ids": child_anchor_ids,
            "bubble_rect": child_rect,
            "mask": sub["sub_mask"],
            "split_parent_mask": mask if mask is not None else None,
            "split_line": sub.get("split_line"),
            "split_parent_rect": parent_rect,
            "split_order": index,
            "ocr_rect": child_rect,
            "ocr_text_rects": child_anchor_rects,
            "quality_flags": list(region.get("quality_flags", [])) + [split_source or "manual_seed_split"],
        }
        variants.append(child)
    return variants or [region]


def _split_manual_seed_regions(
    regions: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
    split_guides: List[Dict[str, Any]],
    image_shape: Tuple[int, int] | None = None,
) -> List[Dict[str, Any]]:
    groups_by_id = {_tg_id(group, idx): group for idx, group in enumerate(groups, start=1)}
    output: List[Dict[str, Any]] = []
    for region in regions:
        should_try = region.get("method") == "manual_bubble_seed_rescue" or split_guides
        output.extend(_split_region_variants(region, groups_by_id, split_guides, image_shape=image_shape) if should_try else [region])
    return output


def _invalidate_regions_replaced_by_manual_edge(regions: List[Dict[str, Any]]) -> None:
    manual_methods = {"manual_edge_barrier_rescue", "manual_bubble_seed_rescue"}
    manual_regions = [
        region for region in regions
        if region.get("is_valid") and region.get("method") in manual_methods
    ]
    for manual in manual_regions:
        manual_ids = {int(aid) for aid in manual.get("anchor_ids", [])}
        seed_points = manual.get("seed_points") or []
        manual_seed = seed_points[0] if seed_points else [None, None]
        manual_rect = manual.get("bubble_rect") or manual.get("unboxed_rect") or manual.get("erase_rect")
        manual_mask = manual.get("mask") if isinstance(manual.get("mask"), np.ndarray) else None
        for region in regions:
            if region is manual or not region.get("is_valid"):
                continue
            method = str(region.get("method") or "")
            if method in manual_methods or method.startswith("manual_"):
                continue
            region_ids = {int(aid) for aid in region.get("anchor_ids", [])}
            replace_by_anchor = bool(manual_ids and (manual_ids & region_ids))
            replace_by_seed = False
            if (
                not replace_by_anchor
                and isinstance(manual_seed, list)
                and len(manual_seed) >= 2
                and manual_seed[0] is not None
                and manual_seed[1] is not None
            ):
                rect = region.get("bubble_rect") or region.get("unboxed_rect") or region.get("erase_rect")
                if rect:
                    x, y, w, h = _as_rect(rect)
                    replace_by_seed = x <= int(manual_seed[0]) <= x + w and y <= int(manual_seed[1]) <= y + h
            replace_by_overlap = False
            if not (replace_by_anchor or replace_by_seed) and manual_rect:
                candidate_rects = [
                    region.get("bubble_rect"),
                    region.get("split_parent_rect"),
                    region.get("unboxed_rect"),
                    region.get("erase_rect"),
                ]
                for rect in candidate_rects:
                    if not rect:
                        continue
                    rx, ry, rw, rh = _as_rect(rect)
                    if rw <= 0 or rh <= 0:
                        continue
                    overlap = _rect_intersection_area(_as_rect(manual_rect), [rx, ry, rw, rh])
                    region_ratio = overlap / max(1, rw * rh)
                    if _rect_center_in_rect([rx, ry, rw, rh], _as_rect(manual_rect)) or region_ratio >= 0.65:
                        replace_by_overlap = True
                        break
            if not (replace_by_anchor or replace_by_seed or replace_by_overlap) and manual_mask is not None:
                region_mask = region.get("mask") if isinstance(region.get("mask"), np.ndarray) else None
                if region_mask is not None and region_mask.shape == manual_mask.shape:
                    region_area = int(np.count_nonzero(region_mask > 0))
                    if region_area > 0:
                        overlap = int(np.count_nonzero((region_mask > 0) & (manual_mask > 0)))
                        replace_by_overlap = (overlap / region_area) >= 0.35
            if replace_by_anchor or replace_by_seed or replace_by_overlap:
                region["is_valid"] = False
                region["region_type_guess"] = "bbox_fallback"
                region.setdefault("quality_flags", []).append("replaced_by_manual_rescue")


def _manual_override_rects(
    manual_regions: List[Dict[str, Any]],
    virtual_walls: List[Dict[str, Any]],
) -> List[Tuple[List[int], str]]:
    rects: List[Tuple[List[int], str]] = []
    for item in manual_regions or []:
        raw_rect = item.get("unboxed_rect") or item.get("bubble_rect") or item.get("rect")
        if not isinstance(raw_rect, list) or len(raw_rect) < 4:
            continue
        rect = _as_rect(raw_rect)
        if rect[2] > 0 and rect[3] > 0:
            rects.append((rect, f"manual_{item.get('type') or 'region'}_override"))
    return rects


def _rect_replaces_region(override_rect: List[int], region: Dict[str, Any]) -> bool:
    candidate_rects = [
        region.get("bubble_rect"),
        region.get("split_parent_rect"),
        region.get("unboxed_rect"),
        region.get("erase_rect"),
    ]
    ox, oy, ow, oh = _as_rect(override_rect)
    if ow <= 0 or oh <= 0:
        return False
    override_area = max(1, ow * oh)
    for raw_rect in candidate_rects:
        if not raw_rect:
            continue
        rx, ry, rw, rh = _as_rect(raw_rect)
        if rw <= 0 or rh <= 0:
            continue
        overlap = _rect_intersection_area([ox, oy, ow, oh], [rx, ry, rw, rh])
        region_ratio = overlap / max(1, rw * rh)
        if _rect_center_in_rect([rx, ry, rw, rh], [ox, oy, ow, oh]):
            return True
        if region_ratio >= 0.50:
            return True
    return False


def _invalidate_regions_replaced_by_manual_overrides(
    regions: List[Dict[str, Any]],
    manual_regions: List[Dict[str, Any]],
    virtual_walls: List[Dict[str, Any]],
) -> None:
    override_rects = _manual_override_rects(manual_regions, virtual_walls)
    if not override_rects:
        return
    for region in regions:
        if not region.get("is_valid"):
            continue
        method = str(region.get("method") or "")
        if method.startswith("manual_"):
            continue
        for rect, reason in override_rects:
            if _rect_replaces_region(rect, region):
                region["is_valid"] = False
                region["region_type_guess"] = "bbox_fallback"
                region.setdefault("quality_flags", []).append(reason)
                break


def _suppression_rect(item: Dict[str, Any]) -> List[int] | None:
    raw_rect = item.get("rect") or item.get("bubble_rect") or item.get("unboxed_rect") or item.get("source_rect")
    if not isinstance(raw_rect, list) or len(raw_rect) < 4:
        return None
    rect = _as_rect(raw_rect)
    return rect if rect[2] > 0 and rect[3] > 0 else None


def _suppression_matches_region(item: Dict[str, Any], region: Dict[str, Any]) -> bool:
    method = str(region.get("method") or "")
    if method.startswith("manual_"):
        return False

    rect = _suppression_rect(item)
    source_region_id = item.get("source_region_id", item.get("source_order"))
    if source_region_id is not None:
        try:
            if int(source_region_id) == int(region.get("region_id")):
                return True if rect is None else _rect_replaces_region(rect, region)
        except (TypeError, ValueError):
            pass

    if rect is None:
        return False
    return _rect_replaces_region(rect, region)


def _invalidate_suppressed_regions(
    regions: List[Dict[str, Any]],
    suppressed_regions: List[Dict[str, Any]],
) -> None:
    if not suppressed_regions:
        return
    for region in regions:
        if not region.get("is_valid"):
            continue
        for item in suppressed_regions:
            if not isinstance(item, dict):
                continue
            if _suppression_matches_region(item, region):
                region["is_valid"] = False
                region["region_type_guess"] = "bbox_fallback"
                region.setdefault("quality_flags", []).append("manual_region_suppressed")
                break


def _largest_rect_in_binary_mask(binary: np.ndarray) -> list[int] | None:
    """binary mask 내부의 최대 axis-aligned rect를 로컬 좌표로 찾는다."""
    if binary.size == 0:
        return None

    heights = [0] * binary.shape[1]
    best: tuple[int, int, int, int, int] | None = None  # area, x, y, w, h

    for y in range(binary.shape[0]):
        row = binary[y]
        for x, value in enumerate(row):
            heights[x] = heights[x] + 1 if value else 0

        stack: list[int] = []
        for x in range(binary.shape[1] + 1):
            cur_h = heights[x] if x < binary.shape[1] else 0
            while stack and cur_h < heights[stack[-1]]:
                top = stack.pop()
                h = heights[top]
                left = stack[-1] + 1 if stack else 0
                w = x - left
                area = w * h
                if area > 0 and (best is None or area > best[0]):
                    best = (area, left, y - h + 1, w, h)
            stack.append(x)

    if best is None:
        return None
    _area, x, y, w, h = best
    return [int(x), int(y), int(w), int(h)]


def _expanded_rect_inside_mask(
    binary: np.ndarray,
    seed_rect: list[int],
    min_fill_ratio: float = 0.94,
) -> list[int]:
    """seed rect를 mask 안에서 가능한 만큼 확장한다.

    완전 내접만 쓰면 삐죽한 말풍선에서 과소해질 수 있어, 높은 fill ratio를
    만족하는 범위 안에서 좌우상하로 조금씩 넓힌다.
    """
    x, y, w, h = _as_rect(seed_rect)
    if w <= 0 or h <= 0:
        return seed_rect
    img_h, img_w = binary.shape[:2]
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)
    x = max(0, x)
    y = max(0, y)

    def ok(nx1: int, ny1: int, nx2: int, ny2: int) -> bool:
        if nx2 <= nx1 or ny2 <= ny1:
            return False
        roi = binary[ny1:ny2, nx1:nx2]
        return float(np.count_nonzero(roi)) / max(1, roi.size) >= min_fill_ratio

    changed = True
    while changed:
        changed = False
        candidates = [
            (x - 1, y, x2, y2),
            (x, y - 1, x2, y2),
            (x, y, x2 + 1, y2),
            (x, y, x2, y2 + 1),
        ]
        for nx1, ny1, nx2, ny2 in candidates:
            nx1 = max(0, nx1)
            ny1 = max(0, ny1)
            nx2 = min(img_w, nx2)
            ny2 = min(img_h, ny2)
            if (nx1, ny1, nx2, ny2) == (x, y, x2, y2):
                continue
            if ok(nx1, ny1, nx2, ny2):
                x, y, x2, y2 = nx1, ny1, nx2, ny2
                changed = True
                break

    return [int(x), int(y), int(x2 - x), int(y2 - y)]


def _recenter_rect_on_mask_centroid(
    binary: np.ndarray,
    rect: list[int],
    centroid: list[int] | None,
    min_fill_ratio: float = 0.88,
) -> tuple[list[int], float]:
    """rect 크기를 유지하면서 mask centroid에 더 가까운 위치를 찾는다."""
    if centroid is None:
        return rect, 1.0
    x, y, w, h = _as_rect(rect)
    if w <= 0 or h <= 0:
        return rect, 1.0
    img_h, img_w = binary.shape[:2]
    if w > img_w or h > img_h:
        return rect, 0.0

    desired_x = int(round(float(centroid[0]) - w / 2))
    desired_y = int(round(float(centroid[1]) - h / 2))
    desired_x = int(max(0, min(desired_x, img_w - w)))
    desired_y = int(max(0, min(desired_y, img_h - h)))

    # desired 위치의 fill을 먼저 확인.
    # desired가 min_fill_ratio를 통과하면 score = dist²=0 + (1-fill)×100 ≤ 12.
    # 경쟁 후보가 이 score를 넘으려면 dist < √12 ≈ 3.46px 이어야 하므로
    # r=5 반경만 탐색해도 최적해가 보장된다 (전체 후보 수 ~121개로 고정).
    # desired가 fill threshold를 못 넘으면 원래 반경으로 전체 탐색.
    desired_roi = binary[desired_y:desired_y + h, desired_x:desired_x + w]
    desired_fill = float(np.count_nonzero(desired_roi)) / max(1, desired_roi.size)
    if desired_fill >= min_fill_ratio:
        radius = 5  # 수학적으로 충분 (위 증명 참고)
    else:
        radius = max(8, min(72, int(round(max(w, h) * 0.35))))

    candidates: set[tuple[int, int]] = {(x, y), (desired_x, desired_y)}
    for cy in range(max(0, desired_y - radius), min(img_h - h, desired_y + radius) + 1):
        for cx in range(max(0, desired_x - radius), min(img_w - w, desired_x + radius) + 1):
            candidates.add((cx, cy))

    best_rect = [x, y, w, h]
    best_fill = 0.0
    best_score: float | None = None
    target_cx = float(centroid[0])
    target_cy = float(centroid[1])
    for cx, cy in candidates:
        roi = binary[cy:cy + h, cx:cx + w]
        fill_ratio = float(np.count_nonzero(roi)) / max(1, roi.size)
        if fill_ratio < min_fill_ratio:
            continue
        rect_cx = cx + w / 2
        rect_cy = cy + h / 2
        score = (rect_cx - target_cx) ** 2 + (rect_cy - target_cy) ** 2 + (1.0 - fill_ratio) * 100.0
        if best_score is None or score < best_score:
            best_score = score
            best_rect = [int(cx), int(cy), int(w), int(h)]
            best_fill = fill_ratio

    return best_rect, best_fill


def _compute_safe_text_rect(
    mask: "np.ndarray | None",
    bubble_rect: list[int],
    ocr_text_rects: list[list[int]] | None = None,
) -> tuple[list[int] | None, dict[str, Any]]:
    """split 이후 bubble mask에서 overlay용 안전 rect clue를 계산한다."""
    if mask is None:
        return None, {"method": "no_mask"}

    bx, by, bw, bh = _as_rect(bubble_rect)
    if bw <= 0 or bh <= 0:
        return None, {"method": "empty_bubble_rect"}

    crop = np.where(mask[by:by + bh, bx:bx + bw] > 0, 255, 0).astype(np.uint8)
    if crop.size == 0 or int(np.count_nonzero(crop)) <= 0:
        return None, {"method": "empty_mask_crop"}
    centroid = _compute_mask_centroid(mask)
    local_centroid = [centroid[0] - bx, centroid[1] - by] if centroid else None

    short_side = min(bw, bh)
    erode_candidates = [
        max(1, int(round(short_side * 0.055))),
        max(1, int(round(short_side * 0.035))),
        0,
    ]
    best_rect: list[int] | None = None
    best_meta: dict[str, Any] = {}

    for erode_px in erode_candidates:
        if erode_px > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1, erode_px * 2 + 1))
            safe = cv2.erode(crop, kernel, iterations=1)
        else:
            safe = crop
        binary = safe > 0
        rect = _largest_rect_in_binary_mask(binary)
        if rect is None:
            continue
        rect = _expanded_rect_inside_mask(crop > 0, rect)
        _rx, _ry, rw, rh = rect
        area_ratio = (rw * rh) / max(1, bw * bh)
        if area_ratio < 0.025:
            continue
        rect, centroid_fill = _recenter_rect_on_mask_centroid(crop > 0, rect, local_centroid)
        best_rect = [bx + rect[0], by + rect[1], rect[2], rect[3]]
        best_meta = {
            "method": "mask_eroded_largest_rect",
            "erode_px": int(erode_px),
            "area_ratio_to_bubble": round(float(area_ratio), 4),
            "centroid_fill_ratio": round(float(centroid_fill), 4),
        }
        break

    if best_rect is None:
        return None, {"method": "no_stable_rect"}

    if ocr_text_rects:
        ocr_union = _union_rect([_as_rect(rect) for rect in ocr_text_rects])
        ox, oy, ow, oh = _as_rect(ocr_union)
        pad = max(4, int(round(min(bw, bh) * 0.04)))
        ox1 = max(bx, ox - pad)
        oy1 = max(by, oy - pad)
        ox2 = min(bx + bw, ox + ow + pad)
        oy2 = min(by + bh, oy + oh + pad)

        sx, sy, sw, sh = _as_rect(best_rect)
        sx2, sy2 = sx + sw, sy + sh
        contains = sx <= ox and sy <= oy and sx2 >= ox + ow and sy2 >= oy + oh

        if not contains:
            cx1 = max(bx, min(sx, ox1))
            cy1 = max(by, min(sy, oy1))
            cx2 = min(bx + bw, max(sx2, ox2))
            cy2 = min(by + bh, max(sy2, oy2))
            local = crop[cy1 - by:cy2 - by, cx1 - bx:cx2 - bx] > 0
            fill_ratio = float(np.count_nonzero(local)) / max(1, local.size)
            if fill_ratio >= 0.80:
                recentered, centroid_fill = _recenter_rect_on_mask_centroid(
                    crop > 0,
                    [int(cx1 - bx), int(cy1 - by), int(cx2 - cx1), int(cy2 - cy1)],
                    local_centroid,
                    min_fill_ratio=0.80,
                )
                best_rect = [bx + recentered[0], by + recentered[1], recentered[2], recentered[3]]
                best_meta["method"] = "mask_eroded_largest_rect_ocr_union"
                best_meta["ocr_union_fill_ratio"] = round(float(fill_ratio), 4)
                best_meta["centroid_fill_ratio"] = round(float(centroid_fill), 4)
                contains = True

        best_meta["contains_ocr_text"] = bool(contains)

    return best_rect, best_meta


def _is_cover_image_path(image_path: str) -> bool:
    """표지 이미지는 OCR/masking 후보를 만들지 않고 image-gen 재생성으로 넘긴다."""
    stem = Path(image_path).stem.lower()
    return stem == "cover" or stem == "00001"


def _write_cover_skip_outputs(
    image: np.ndarray,
    image_path: str,
    clusters_json: str,
    output_masked_path: str,
    output_json_path: str,
    indexed_output_sheet_path: str,
    output_vis_path: str | None,
) -> Dict[str, Any]:
    """커버 페이지용 빈 OCR 산출물을 저장한다."""
    cv2.imwrite(output_masked_path, image)
    blank_sheet = np.full((1, 1, 3), 255, dtype=np.uint8)
    cv2.imwrite(indexed_output_sheet_path, blank_sheet)

    if output_vis_path:
        vis = image.copy()
        cv2.rectangle(vis, (24, 24), (380, 82), (0, 0, 0), -1)
        cv2.putText(vis, "COVER_SKIP IMG_GEN", (36, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(output_vis_path, vis)

    output_data = {
        "image": image_path,
        "clusters": clusters_json,
        "page_type": "cover",
        "analysis_status": "complete",
        "ocr_region_count": 0,
        "skip_reason": "cover_image_gen_redraw",
        "sheet_size": [1, 1],
        "regions": [],
    }
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    return output_data


def _invalidate_containing_bubbles(regions: List[Dict[str, Any]]) -> None:
    """valid 버블 중 다른 valid 버블을 bubble_rect 기준으로 완전히 포함하는 버블을 무효화.

    포함하는 쪽(큰 쪽)을 is_valid=False로 마킹 → unboxed 경로로 재처리.
    manual 메서드 region은 어떤 경우에도 탈락 대상에서 제외한다.
    """
    valid = [r for r in regions if r.get("is_valid") and r.get("bubble_rect")]
    to_invalidate: set[int] = set()

    for i, ra in enumerate(valid):
        ax, ay, aw, ah = ra["bubble_rect"]
        for rb in valid[i + 1:]:
            bx, by, bw, bh = rb["bubble_rect"]
            a_contains_b = ax <= bx and ay <= by and ax + aw >= bx + bw and ay + ah >= by + bh
            b_contains_a = bx <= ax and by <= ay and bx + bw >= ax + aw and by + bh >= ay + ah
            a_is_manual = str(ra.get("method", "")).startswith("manual_")
            b_is_manual = str(rb.get("method", "")).startswith("manual_")
            if a_contains_b:
                if a_is_manual and not b_is_manual:
                    # manual(큰)이 auto(작은)를 포함 → auto 탈락
                    to_invalidate.add(id(rb))
                elif b_is_manual and not a_is_manual:
                    # auto(큰)이 manual(작은)를 포함 → auto 탈락
                    to_invalidate.add(id(ra))
                elif not a_is_manual and not b_is_manual:
                    # 둘 다 auto: anchor 있는 쪽 우선, 같으면 큰 쪽(ra) 탈락
                    a_has_anchor = bool(ra.get("anchor_ids"))
                    b_has_anchor = bool(rb.get("anchor_ids"))
                    if a_has_anchor != b_has_anchor:
                        to_invalidate.add(id(rb if a_has_anchor else ra))
                    else:
                        to_invalidate.add(id(ra))
                # 둘 다 manual이면 아무것도 안 함 (사용자 판단 우선)
            elif b_contains_a:
                if b_is_manual and not a_is_manual:
                    # manual(큰)이 auto(작은)를 포함 → auto 탈락
                    to_invalidate.add(id(ra))
                elif a_is_manual and not b_is_manual:
                    # auto(큰)이 manual(작은)를 포함 → auto 탈락
                    to_invalidate.add(id(rb))
                elif not a_is_manual and not b_is_manual:
                    # 둘 다 auto: anchor 있는 쪽 우선, 같으면 큰 쪽(rb) 탈락
                    a_has_anchor = bool(ra.get("anchor_ids"))
                    b_has_anchor = bool(rb.get("anchor_ids"))
                    if a_has_anchor != b_has_anchor:
                        to_invalidate.add(id(ra if b_has_anchor else rb))
                    else:
                        to_invalidate.add(id(rb))

    for r in regions:
        if id(r) in to_invalidate:
            r["is_valid"] = False
            r["region_type_guess"] = "bbox_fallback"  # unboxed 경로로 재처리


def _load_simple_lama_class() -> Any | None:
    global SimpleLama
    if SimpleLama is None:
        try:
            from simple_lama_inpainting import SimpleLama as simple_lama_class
        except ModuleNotFoundError:
            return None
        SimpleLama = simple_lama_class
    return SimpleLama


def _get_lama() -> Any:
    """LaMa 모델을 처음 필요할 때 한 번만 올린다. 여러 스레드가 동시에 불러도 한 번만 로드한다."""
    global _lama
    if _lama is not None:
        return _lama
    with _lama_lock:
        if _lama is None:
            lama_class = _load_simple_lama_class()
            if lama_class is None:
                raise RuntimeError("simple_lama_inpainting is not installed. Install requirements.txt in .venv.")
            _lama = lama_class()
    return _lama


def _rect_mask_in_roi(mask_shape: Tuple[int, int], roi_rect: List[int], rect: List[int], pad: int = 0) -> np.ndarray:
    """원본 좌표 rect를 ROI 내부 사각 마스크로 변환한다."""
    mask_h, mask_w = int(mask_shape[0]), int(mask_shape[1])
    rx, ry, _rw, _rh = _as_rect(roi_rect)
    x, y, w, h = _as_rect(rect)
    x1 = max(0, x - pad - rx)
    y1 = max(0, y - pad - ry)
    x2 = min(mask_w, x + w + pad - rx)
    y2 = min(mask_h, y + h + pad - ry)
    mask = np.zeros((mask_h, mask_w), dtype=np.uint8)
    if x2 > x1 and y2 > y1:
        mask[y1:y2, x1:x2] = 255
    return mask


def _select_unboxed_inpaint_mask(candidate: Dict[str, Any]) -> Tuple[np.ndarray, Dict[str, Any]]:
    """배경 복잡도에 따라 LaMa에 넘길 unboxed 마스크를 선택한다."""
    text_mask = candidate["text_mask"]
    background_class = str(candidate.get("background_class") or "")
    engine = str(candidate.get("recommended_inpaint_engine") or "lama")
    if int(candidate.get("furigana_added_pixels") or 0) > 0:
        rect_mask = _rect_mask_in_roi(
            text_mask.shape,
            _as_rect(candidate.get("roi_rect", [0, 0, 0, 0])),
            _as_rect(candidate.get("tg_rect", [0, 0, 0, 0])),
            pad=2,
        )
        if int(np.count_nonzero(rect_mask)) >= 20:
            return cv2.bitwise_or(rect_mask, text_mask), {
                "mask_strategy": "tg_rect_plus_furigana_lama",
                "recommended_inpaint_engine_override": "lama",
                "extra_dilate": 0,
                "extra_dilate_reason": "furigana_rect_mask",
            }

    if engine == "lama" and background_class in {"mixed_line_art", "dark_bg"}:
        rect_mask = _rect_mask_in_roi(
            text_mask.shape,
            _as_rect(candidate.get("roi_rect", [0, 0, 0, 0])),
            _as_rect(candidate.get("tg_rect", [0, 0, 0, 0])),
            pad=2,
        )
        if int(np.count_nonzero(rect_mask)) >= max(20, int(np.count_nonzero(text_mask)) // 2):
            return rect_mask, {
                "mask_strategy": "tg_rect_lama",
                "extra_dilate": 0,
                "extra_dilate_reason": "complex_background_rect_mask",
            }

    lane_bg_mask = (candidate["lane_mask"] > 0) & (text_mask == 0)
    bg_pixels_gray = cv2.cvtColor(candidate["roi"], cv2.COLOR_BGR2GRAY)[lane_bg_mask]
    bg_std = float(np.std(bg_pixels_gray)) if len(bg_pixels_gray) >= 20 else 0.0
    extra_dilate = min(int(bg_std / 6), 10) if bg_std > 30 else 0
    if extra_dilate > 0:
        k = np.ones((extra_dilate * 2 + 1, extra_dilate * 2 + 1), np.uint8)
        return cv2.dilate(text_mask, k, iterations=1), {
            "mask_strategy": "text_mask_dilated",
            "extra_dilate": extra_dilate,
            "bg_std_for_dilate": round(bg_std, 2),
        }
    return text_mask, {
        "mask_strategy": "text_mask",
        "extra_dilate": 0,
        "bg_std_for_dilate": round(bg_std, 2),
    }


def _erase_bright_text_on_dark(
    image: np.ndarray,
    masked_image: np.ndarray,
    region_rect: List[int],
    anchor_groups: List[Dict[str, Any]],
    lane_pad: int = 14,
    bright_threshold: int = 175,
    dilate_px: int = 2,
) -> bool:
    """검은 배경 말풍선 안의 밝은 글자 획만 LaMa로 지운다."""
    x, y, w, h = _as_rect(region_rect)
    if w <= 0 or h <= 0:
        return False

    roi = image[y:y + h, x:x + w].copy()
    if roi.size == 0:
        return False
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    lane_mask = np.zeros((h, w), dtype=np.uint8)
    for group in anchor_groups:
        gx, gy, gw, gh = _as_rect(group.get("fallback_rect") or group.get("tight_rect") or group.get("bounding_rect"))
        lx1 = max(0, gx - x - lane_pad)
        ly1 = max(0, gy - y - lane_pad)
        lx2 = min(w, gx + gw - x + lane_pad)
        ly2 = min(h, gy + gh - y + lane_pad)
        if lx2 > lx1 and ly2 > ly1:
            lane_mask[ly1:ly2, lx1:lx2] = 255

    bright = np.where((gray >= bright_threshold) & (lane_mask > 0), 255, 0).astype(np.uint8)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
    if dilate_px > 0:
        kernel = np.ones((dilate_px * 2 + 1, dilate_px * 2 + 1), dtype=np.uint8)
        bright = cv2.dilate(bright, kernel, iterations=1)

    if int(np.count_nonzero(bright)) < 20:
        return False

    lama = _get_lama()
    roi_pil = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))
    lama_out = lama(roi_pil, Image.fromarray(bright))
    lama_roi = cv2.cvtColor(np.array(lama_out), cv2.COLOR_RGB2BGR)
    lh, lw = lama_roi.shape[:2]
    apply_h, apply_w = min(h, lh), min(w, lw)
    target = masked_image[y:y + apply_h, x:x + apply_w]
    apply_mask = bright[:apply_h, :apply_w] > 0
    target[apply_mask] = lama_roi[:apply_h, :apply_w][apply_mask]
    return True


def _group_rect(group: Dict[str, Any]) -> List[int]:
    """TextGroup의 대표 rect를 반환한다."""
    return _as_rect(group.get("fallback_rect") or group.get("tight_rect") or group.get("bounding_rect") or [0, 0, 0, 0])


def _consolidated_unboxed_group(cluster: List[Tuple[int, Dict[str, Any]]]) -> Dict[str, Any]:
    """unboxed 후보로 묶인 TG들을 OCR/번역 단위 메타로 표현한다."""
    ordered = sorted(cluster, key=lambda item: (-_group_rect(item[1])[0], _group_rect(item[1])[1]))
    rect = _union_rect([_group_rect(group) for _, group in ordered])
    lines: List[Dict[str, Any]] = []

    for _tid, group in ordered:
        for line in group.get("lines", []):
            line_copy = dict(line)
            line_copy["source_text_group_id"] = _tg_id(group, _tid)
            lines.append(line_copy)

    return {
        "text_group_ids": [int(tid) for tid, _ in ordered],
        "text": "".join(_tg_text(group) for _, group in ordered),
        "tight_rect": rect,
        "bounding_rect": rect,
        "lines": lines,
    }


def _padded_union_crop_rect(
    rects: List[List[int]],
    img_w: int,
    img_h: int,
    pad: int = 12,
) -> List[int]:
    """OCR/overlay용 unboxed bbox를 원본 TG union 기준으로 넉넉하게 잡는다."""
    valid = [_as_rect(rect) for rect in rects if rect and len(rect) >= 4]
    valid = [rect for rect in valid if rect[2] > 0 and rect[3] > 0]
    if not valid:
        return [0, 0, 0, 0]
    ux, uy, uw, uh = _union_rect(valid)
    x1 = max(0, ux - pad)
    y1 = max(0, uy - pad)
    x2 = min(img_w, ux + uw + pad)
    y2 = min(img_h, uy + uh + pad)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _cluster_source_meta(
    cluster: List[Tuple[int, Dict[str, Any]]],
    source_meta_by_anchor: Dict[int, Dict[str, Any]],
) -> Tuple[set[str], set[str], List[str], List[str]]:
    """unboxed TG cluster의 원본 rescue 메타를 모은다."""
    source_methods: set[str] = set()
    source_types: set[str] = set()
    quality_flags: List[str] = []
    review_tags: List[str] = []
    for tid, _group in cluster:
        meta = source_meta_by_anchor.get(int(tid), {})
        if meta.get("method"):
            source_methods.add(str(meta.get("method")))
        if meta.get("region_type_guess"):
            source_types.add(str(meta.get("region_type_guess")))
        quality_flags.extend(str(v) for v in meta.get("quality_flags", []))
        review_tags.extend(str(v) for v in meta.get("review_tags", []))
    return source_methods, source_types, quality_flags, review_tags


def _make_consolidated_unboxed_crop(
    image: np.ndarray,
    cluster: List[Tuple[int, Dict[str, Any]]],
    img_w: int,
    img_h: int,
    source_meta_by_anchor: Dict[int, Dict[str, Any]],
    *,
    crop_rect: List[int] | None = None,
    erase_rect: List[int] | None = None,
    inpaint_skipped: bool = False,
    source_method: str | None = None,
    source_region_type: str | None = None,
    extra_quality_flags: List[str] | None = None,
    extra_review_tags: List[str] | None = None,
) -> Dict[str, Any]:
    """인접 unboxed TG cluster를 하나의 OCR/overlay region item으로 만든다."""
    sorted_cluster = sorted(cluster, key=lambda item: (-_group_rect(item[1])[0], _group_rect(item[1])[1]))
    tids = [int(t) for t, _ in sorted_cluster]
    consolidated_group = _consolidated_unboxed_group(sorted_cluster)
    cluster_rects = [_group_rect(group) for _, group in sorted_cluster]
    ux, uy, uw, uh = _as_rect(crop_rect or _padded_union_crop_rect(cluster_rects, img_w, img_h, pad=8))
    erase = erase_rect or _union_rect([
        _as_rect(group.get("tight_rect") or group.get("bounding_rect") or _group_rect(group))
        for _, group in sorted_cluster
    ])

    # 후리가나(ruby text) 공간 확보: 맨 오른쪽 열(첫 번째 line) width의 50%만큼 우측 확장.
    # vertical TG에서만 적용 (horizontal은 ruby가 위쪽에 붙으므로 해당 없음).
    # erase_rect가 외부에서 명시 제공된 경우엔 스킵.
    if not erase_rect and erase[2] > 0:
        first_group = sorted_cluster[0][1]
        if first_group.get("orientation") == "vertical":
            lines = first_group.get("lines", [])
            first_line_w = (
                _as_rect(lines[0].get("bbox", [0, 0, 0, 0]))[2]
                if lines
                else _as_rect(_group_rect(first_group))[2]
            )
            ruby_margin = int(first_line_w * 0.5)
            ex, ey, ew, eh = erase
            erase = [ex, ey, min(img_w - ex, ew + ruby_margin), eh]

    source_methods, source_types, quality_flags, review_tags = _cluster_source_meta(sorted_cluster, source_meta_by_anchor)
    if extra_quality_flags:
        quality_flags.extend(extra_quality_flags)
    if extra_review_tags:
        review_tags.extend(extra_review_tags)
    if len(sorted_cluster) > 1:
        quality_flags.append("unboxed_consolidated_tg_cluster")
    quality_flags = list(dict.fromkeys(quality_flags))
    review_tags = list(dict.fromkeys(review_tags))

    if source_method is None:
        source_method = "unboxed_consolidated" if len(sorted_cluster) > 1 else (next(iter(source_methods)) if len(source_methods) == 1 else "bbox_fallback")
    if source_region_type is None:
        source_region_type = "unboxed_consolidated" if len(sorted_cluster) > 1 else (next(iter(source_types)) if len(source_types) == 1 else "bbox_fallback")

    source_order = None
    if len(sorted_cluster) == 1 and tids:
        source_order = source_meta_by_anchor.get(tids[0], {}).get("region_id")

    result = {
        "region_id": 0,
        "anchor_ids": tids,
        "source_rect": [ux, uy, uw, uh],
        "erase_rect": list(erase),
        "crop": image[uy:uy + uh, ux:ux + uw].copy(),
        "type": "unboxed",
        "sort_rect": [ux, uy, uw, uh],
        "split_order": 0,
        "source_order": source_order,
        "consolidated_text_group": consolidated_group,
        "inpaint_skipped": inpaint_skipped,
        "source_method": source_method,
        "source_region_type": source_region_type,
        "quality_flags": quality_flags,
        "review_tags": review_tags,
    }
    if len(sorted_cluster) == 1 and tids:
        source_probe = source_meta_by_anchor.get(tids[0], {}).get("black_contour_probe")
        if source_probe:
            result["black_contour_probe"] = source_probe
    return result


def _manual_ubox_replaces_rect(manual_rect: List[int], other_rect: List[int], threshold: float = 0.90) -> bool:
    _mx, _my, mw, mh = _as_rect(manual_rect)
    _ox, _oy, ow, oh = _as_rect(other_rect)
    min_area = max(1, min(mw * mh, ow * oh))
    return (_rect_intersection_area(manual_rect, other_rect) / min_area) >= threshold


def _is_manual_ubox_crop(item: Dict[str, Any]) -> bool:
    method = str(item.get("source_method") or "")
    flags = " ".join(str(flag) for flag in item.get("quality_flags", []))
    tags = " ".join(str(tag) for tag in item.get("review_tags", []))
    return "manual_ubox" in method or "manual_ubox" in flags or "manual_ubox" in tags


def _apply_manual_unboxed_overrides(
    image: np.ndarray,
    crops: List[Dict[str, Any]],
    img_w: int,
    img_h: int,
) -> List[Dict[str, Any]]:
    unboxed_indexes = [idx for idx, item in enumerate(crops) if item.get("type") == "unboxed" and item.get("source_rect")]
    if len(unboxed_indexes) < 2:
        return crops

    consumed: set[int] = set()
    manual_indexes = [idx for idx in unboxed_indexes if _is_manual_ubox_crop(crops[idx])]
    if not manual_indexes:
        return crops

    for manual_idx in manual_indexes:
        manual = crops[manual_idx]
        manual_order = int(manual.get("source_order") or manual_idx)
        for other_idx in unboxed_indexes:
            if other_idx == manual_idx or other_idx in consumed:
                continue
            other = crops[other_idx]
            if _is_manual_ubox_crop(other) and int(other.get("source_order") or other_idx) > manual_order:
                continue
            if _manual_ubox_replaces_rect(manual["source_rect"], other["source_rect"]):
                consumed.add(other_idx)
                manual["quality_flags"] = list(dict.fromkeys(
                    [str(flag) for flag in manual.get("quality_flags", [])]
                    + ["manual_ubox_replaced_overlapped_u"]
                ))
                manual["review_tags"] = list(dict.fromkeys(
                    [str(tag) for tag in manual.get("review_tags", [])]
                    + ["manual_ubox_override"]
                ))

    return [item for idx, item in enumerate(crops) if idx not in consumed]


def _crop_anchor_ids(item: Dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for raw in item.get("anchor_ids", []):
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def _crop_rect(item: Dict[str, Any]) -> List[int]:
    return _as_rect(item.get("source_rect") or item.get("erase_rect") or [0, 0, 0, 0])


def _crop_text_len(item: Dict[str, Any]) -> int:
    consolidated = item.get("consolidated_text_group")
    if isinstance(consolidated, dict):
        text = str(consolidated.get("text") or "")
    else:
        text = str(item.get("text") or item.get("ocr_text") or "")
    return len(text.strip())


def _min_overlap_ratio(a: List[int], b: List[int]) -> float:
    ax, ay, aw, ah = _as_rect(a)
    bx, by, bw, bh = _as_rect(b)
    min_area = max(1, min(aw * ah, bw * bh))
    return _rect_intersection_area([ax, ay, aw, ah], [bx, by, bw, bh]) / min_area


def _is_manual_unboxed_crop(item: Dict[str, Any]) -> bool:
    method = str(item.get("source_method") or item.get("method") or "")
    return method.startswith("manual_")


def _bubble_priority(item: Dict[str, Any]) -> Tuple[int, int]:
    text = " ".join([
        str(item.get("source_method") or ""),
        str(item.get("source_region_type") or ""),
        " ".join(str(flag) for flag in item.get("quality_flags", [])),
    ])
    if "manual_bubble_seed" in text:
        base = 50
    elif "manual_edge_barrier" in text or "edge_barrier" in text:
        base = 45
    elif "dark_flood" in text:
        base = 35
    else:
        base = 20
    # 같은 우선순위라면 작은 crop이 중복 OCR을 덜 만든다.
    x, y, w, h = _crop_rect(item)
    return base, -(w * h)


def _same_split_parent(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_parent = left.get("sort_rect") or left.get("split_parent_rect")
    right_parent = right.get("sort_rect") or right.get("split_parent_rect")
    return bool(left_parent and right_parent and _as_rect(left_parent) == _as_rect(right_parent))


def _dedupe_dialogue_crops(crops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OCR sheet로 나가기 전 U/R 중복 후보를 제거한다."""
    consumed: set[int] = set()
    bubble_indexes = [idx for idx, item in enumerate(crops) if item.get("type") == "bubble"]
    unboxed_indexes = [idx for idx, item in enumerate(crops) if item.get("type") == "unboxed"]

    for uidx in unboxed_indexes:
        u_item = crops[uidx]
        u_rect = _crop_rect(u_item)
        u_ids = _crop_anchor_ids(u_item)
        for bidx in bubble_indexes:
            b_item = crops[bidx]
            b_rect = _crop_rect(b_item)
            b_ids = _crop_anchor_ids(b_item)
            shares_anchor = bool(u_ids and b_ids and (u_ids & b_ids))
            contained = _min_overlap_ratio(u_rect, b_rect) >= 0.75
            if shares_anchor or contained:
                consumed.add(uidx)
                break

    for pos, left_idx in enumerate(unboxed_indexes):
        if left_idx in consumed:
            continue
        left = crops[left_idx]
        if _is_manual_unboxed_crop(left):
            continue
        left_rect = _crop_rect(left)
        lx, ly, lw, lh = left_rect
        left_area = max(1, lw * lh)
        for right_idx in unboxed_indexes[pos + 1:]:
            if right_idx in consumed:
                continue
            right = crops[right_idx]
            if _is_manual_unboxed_crop(right):
                continue
            right_rect = _crop_rect(right)
            rx, ry, rw, rh = right_rect
            right_area = max(1, rw * rh)
            overlap = _rect_intersection_area(left_rect, right_rect)
            if overlap <= 0:
                continue
            left_in_right = overlap / left_area >= 0.92 and right_area >= left_area * 1.8
            right_in_left = overlap / right_area >= 0.92 and left_area >= right_area * 1.8
            if not left_in_right and not right_in_left:
                continue
            child_idx, parent_idx = (left_idx, right_idx) if left_in_right else (right_idx, left_idx)
            child = crops[child_idx]
            child_text_len = _crop_text_len(child)
            if child_text_len <= 2:
                consumed.add(child_idx)
                crops[parent_idx].setdefault("quality_flags", []).append("deduped_contained_unboxed_fragment")
                if child_idx == left_idx:
                    break

    for pos, left_idx in enumerate(bubble_indexes):
        if left_idx in consumed:
            continue
        left = crops[left_idx]
        left_rect = _crop_rect(left)
        left_ids = _crop_anchor_ids(left)
        for right_idx in bubble_indexes[pos + 1:]:
            if right_idx in consumed:
                continue
            right = crops[right_idx]
            right_rect = _crop_rect(right)
            right_ids = _crop_anchor_ids(right)
            shares_anchor = bool(left_ids and right_ids and (left_ids & right_ids))
            if _same_split_parent(left, right) and not shares_anchor:
                continue
            near_duplicate = _min_overlap_ratio(left_rect, right_rect) >= 0.72
            if not shares_anchor and not near_duplicate:
                continue
            keep, drop = (
                (left_idx, right_idx)
                if _bubble_priority(left) >= _bubble_priority(right)
                else (right_idx, left_idx)
            )
            consumed.add(drop)
            if keep == right_idx:
                break

    return [item for idx, item in enumerate(crops) if idx not in consumed]


def _draw_final_vis_label(vis: np.ndarray, rect: List[int], text: str, color: Tuple[int, int, int]) -> None:
    """최종 OCR region 라벨을 검은 배경 위에 그린다."""
    x, y, w, _h = _as_rect(rect)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.62
    thick = 2
    (tw, th), bl = cv2.getTextSize(text, font, scale, thick)
    ty = max(th + 6, y - 8)
    cv2.rectangle(vis, (x - 3, ty - th - 6), (x + tw + 6, ty + bl + 4), (0, 0, 0), -1)
    cv2.putText(vis, text, (x, ty), font, scale, color, thick, cv2.LINE_AA)


def _is_rescue_source_method(value: Any) -> bool:
    text = str(value or "")
    return "edge_barrier" in text


def _mask_for_bubble_erasure(region: Dict[str, Any], region_mask: np.ndarray | None) -> np.ndarray | None:
    """OCR crop용 split mask와 white masking용 bubble mask를 분리한다."""
    parent_mask = region.get("split_parent_mask")
    mask = parent_mask if isinstance(parent_mask, np.ndarray) else region_mask
    if not isinstance(mask, np.ndarray):
        return None

    result = _fill_mask_holes(mask)
    needs_gap_close = bool(region.get("split_parent_rect")) or _is_rescue_source_method(region.get("method"))
    if needs_gap_close:
        short_side = min(result.shape[:2]) if result.size else 0
        close_px = max(3, min(9, int(round(short_side * 0.004)))) if short_side else 3
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px * 2 + 1, close_px * 2 + 1))
        result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel, iterations=1)
        result = _fill_mask_holes(result)
    return np.where(result > 0, 255, 0).astype(np.uint8)


def _draw_final_vis_legend(vis: np.ndarray) -> None:
    """최종 dialogue 시각화 색상 의미를 상단에 표시한다."""
    entries = [
        ("bubble", (255, 215, 150)),
        ("edge", (130, 255, 80)),
        ("unboxed", (0, 255, 255)),
        ("TG-sub", (0, 210, 210)),
        ("safe", (210, 170, 70)),
        ("bubble-rect", (40, 170, 200)),
        ("parent", (150, 150, 150)),
    ]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.52
    thick = 1
    x = 10
    y = 24
    swatch = 16
    gap = 18
    cv2.rectangle(vis, (0, 0), (vis.shape[1], 34), (0, 0, 0), -1)
    for label, color in entries:
        (tw, _th), _bl = cv2.getTextSize(label, font, scale, thick)
        if x + swatch + 5 + tw + gap > vis.shape[1]:
            break
        cv2.rectangle(vis, (x, y - swatch + 3), (x + swatch, y + 3), color, -1)
        cv2.putText(vis, label, (x + swatch + 5, y), font, scale, color, thick, cv2.LINE_AA)
        x += swatch + 5 + tw + gap


def _scaled_erode_px(base_px: int, image_shape: Tuple[int, int, int] | Tuple[int, int]) -> int:
    """기준 해상도 대비 erode px를 비례 조정한다."""
    if base_px <= 0:
        return 0
    img_h, img_w = image_shape[:2]
    short_side = max(1, min(int(img_w), int(img_h)))
    return max(1, int(round(base_px * short_side / 1600.0)))


def _draw_final_tg_boxes(vis: np.ndarray, groups: List[Dict[str, Any]]) -> None:
    """최종 시각화 위에 TG bbox와 index 라벨을 다시 그린다."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.52
    thick = 1
    color = (0, 220, 255)
    bg = (0, 0, 0)
    img_h, img_w = vis.shape[:2]

    for idx, group in enumerate(groups, start=1):
        x, y, w, h = _group_rect(group)
        if w <= 0 or h <= 0:
            continue
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, 2, cv2.LINE_AA)
        label = f"TG{_tg_id(group, idx)}"
        (tw, th), bl = cv2.getTextSize(label, font, scale, thick)
        tx = max(0, min(img_w - tw - 4, x))
        ty = min(img_h - 4, max(th + 4, y + h + th + 8))
        cv2.rectangle(vis, (tx - 2, ty - th - 4), (tx + tw + 4, ty + bl + 2), bg, -1)
        cv2.putText(vis, label, (tx, ty), font, scale, color, thick, cv2.LINE_AA)


def _overlay_final_unboxed_regions(
    image: np.ndarray,
    output_vis_path: str | None,
    output_regions: List[Dict[str, Any]],
    text_groups: List[Dict[str, Any]] | None = None,
) -> None:
    """후보 시각화 위에 최종 U/R region 라벨을 최종 region_id 기준으로 덧그린다.

    build_ocr_region_sheet()가 bubble-only 중간 ID로 vis를 먼저 생성하므로,
    이후 U region 삽입 후 재인덱싱된 최종 ID를 bubble(R) 라벨에도 덮어써야
    vis의 R번호와 indexed sheet의 R번호가 일치한다.
    """
    if not output_vis_path:
        return

    vis_path = Path(output_vis_path)
    vis = image.copy()

    def paint_region_fill(target: np.ndarray, region: Dict[str, Any], color: Tuple[int, int, int]) -> None:
        mask_ref = region.get("mask_ref")
        if mask_ref:
            mask = cv2.imread(str(mask_ref), cv2.IMREAD_GRAYSCALE)
            if mask is not None and mask.shape[:2] == target.shape[:2] and np.any(mask > 0):
                target[mask > 0] = color
                return

        polygon = region.get("polygon") or region.get("contour")
        if polygon:
            poly = np.array(polygon, dtype=np.int32)
            if poly.ndim == 2 and poly.shape[0] >= 3:
                cv2.fillPoly(target, [poly], color)
                return

        rect = region.get("bubble_rect") or region.get("source_rect")
        if rect:
            x, y, w, h = _as_rect(rect)
            if w > 0 and h > 0:
                cv2.rectangle(target, (x, y), (x + w, y + h), color, -1)

    overlay = vis.copy()
    unboxed_fill_color = (0, 165, 255)
    bubble_fill_color = (255, 178, 94)
    rescue_fill_color = (170, 230, 80)
    dark_fill_color = (0, 220, 255)
    line_color = (0, 255, 255)
    label_color = (0, 255, 255)
    sub_tg_color = (0, 210, 210)

    for region in output_regions:
        if region.get("type") != "unboxed":
            continue
        rect = region.get("unboxed_rect") or region.get("erase_rect")
        if not rect:
            continue
        x, y, w, h = _as_rect(rect)
        if w <= 0 or h <= 0:
            continue
        cv2.rectangle(overlay, (x, y), (x + w, y + h), unboxed_fill_color, -1)

    for region in output_regions:
        if region.get("type") == "unboxed":
            continue
        if _is_rescue_source_method(region.get("source_method")):
            color = rescue_fill_color
        elif region.get("source_method") == "dark_flood":
            color = dark_fill_color
        else:
            color = bubble_fill_color
        paint_region_fill(overlay, region, color)

    vis = cv2.addWeighted(overlay, 0.38, vis, 0.62, 0)

    for region in output_regions:
        if region.get("type") != "unboxed":
            continue
        rect = region.get("unboxed_rect") or region.get("erase_rect")
        if not rect:
            continue
        x, y, w, h = _as_rect(rect)
        if w <= 0 or h <= 0:
            continue
        cv2.rectangle(vis, (x, y), (x + w, y + h), line_color, 5, cv2.LINE_AA)
        consolidated = region.get("consolidated_text_group") or {}
        for line in consolidated.get("lines") or []:
            line_rect = line.get("bbox")
            if not line_rect or len(line_rect) < 4:
                continue
            lx, ly, lw, lh = _as_rect(line_rect)
            if lw > 0 and lh > 0:
                cv2.rectangle(vis, (lx, ly), (lx + lw, ly + lh), sub_tg_color, 2, cv2.LINE_AA)
        _draw_final_vis_label(vis, rect, f"U{region['region_id']}", label_color)

    # bubble R 라벨을 최종 region_id로 덮어쓰기
    # (build_ocr_region_sheet의 중간 ID → 최종 ID로 교체)
    bubble_label_color = (255, 215, 150)
    edge_label_color = (130, 255, 80)
    safe_color = (210, 170, 70)  # 옅은 청록 계열 (BGR)
    bubble_rect_color = (40, 170, 200)
    split_parent_color = (150, 150, 150)
    split_lines: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for region in output_regions:
        if region.get("type") == "unboxed":
            continue
        split_parent_rect = region.get("split_parent_rect")
        if split_parent_rect:
            px, py, pw, ph = _as_rect(split_parent_rect)
            if pw > 0 and ph > 0:
                cv2.rectangle(vis, (px, py), (px + pw, py + ph), split_parent_color, 1, cv2.LINE_AA)
        split_line = region.get("split_line")
        if split_line and len(split_line) >= 2:
            p1 = (int(split_line[0][0]), int(split_line[0][1]))
            p2 = (int(split_line[1][0]), int(split_line[1][1]))
            split_lines.add((min(p1, p2), max(p1, p2)))
        rect = region.get("bubble_rect") or region.get("source_rect")
        if not rect:
            continue
        x, y, w, h = _as_rect(rect)
        if w <= 0 or h <= 0:
            continue
        is_rescue = _is_rescue_source_method(region.get("source_method"))
        rect_color = edge_label_color if is_rescue else bubble_rect_color
        rect_thickness = 3 if is_rescue else 1
        cv2.rectangle(vis, (x, y), (x + w, y + h), rect_color, rect_thickness, cv2.LINE_AA)
        safe_rect = region.get("safe_text_rect")
        if safe_rect:
            sx, sy, sw, sh = _as_rect(safe_rect)
            if sw > 0 and sh > 0:
                cv2.rectangle(vis, (sx, sy), (sx + sw, sy + sh), safe_color, 2, cv2.LINE_AA)
        centroid = region.get("mask_centroid")
        if centroid and len(centroid) >= 2:
            cv2.circle(vis, (int(centroid[0]), int(centroid[1])), 8, (255, 0, 0), -1, cv2.LINE_AA)
        label_color = edge_label_color if is_rescue else bubble_label_color
        _draw_final_vis_label(vis, rect, f"R{region['region_id']}", label_color)

    _draw_final_vis_legend(vis)
    for p1, p2 in sorted(split_lines):
        cv2.line(vis, p1, p2, (0, 0, 220), 4, cv2.LINE_AA)
    if text_groups:
        _draw_final_tg_boxes(vis, text_groups)
    cv2.imwrite(str(vis_path), vis)


def build_dialogue_sheet(
    image_path: str,
    clusters_json: str,
    output_masked_path: str,
    output_json_path: str,
    indexed_output_sheet_path: str,
    output_vis_path: str | None = None,
    manual_regions: List[Dict[str, Any]] | None = None,
    manual_seed_points: List[Dict[str, Any]] | None = None,
    manual_virtual_walls: List[Dict[str, Any]] | None = None,
    manual_split_guides: List[Dict[str, Any]] | None = None,
    suppressed_regions: List[Dict[str, Any]] | None = None,
    pad: int = 60,
    lane_pad: int = 20,
    dark_threshold: int = 214,
    min_mask_pixels: int = 80,
    bubble_protect_pad: int = 10,
    bubble_mask_erode: int = 2,
    indexed_label_gutter: int = 48,
    base_masked_path: str | None = None,
    reuse_existing_unboxed_inpaint: bool = False,
) -> Dict[str, Any]:
    """
    1. Bubble 영역 검출 → 흰색 마스킹
    2. Unboxed 텍스트 검출 → LaMa inpaint로 제거
    3. 두 종류 crop을 모아 indexed OCR sheet 생성

    Returns: regions JSON dict
    """
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(image_path)
    img_h, img_w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    if _is_cover_image_path(image_path):
        return _write_cover_skip_outputs(
            image,
            image_path,
            clusters_json,
            output_masked_path,
            output_json_path,
            indexed_output_sheet_path,
            output_vis_path,
        )

    groups = _load_json(Path(clusters_json))
    mask_artifact_dir = Path(output_json_path).parent.parent / "masks"
    manual_guidance_report: Dict[str, Any] = {"edge_boxes": []}
    style_baseline = _compute_page_style_baseline(image, groups)
    style_decisions_by_tg: Dict[int, Dict[str, Any]] = {}
    for idx, group in enumerate(groups, start=1):
        tid = _tg_id(group, idx)
        style = _compute_text_group_style(image, group)
        style_decisions_by_tg[tid] = _classify_text_group_style(style, style_baseline)

    # ── 1. Bubble Detection ──
    # image/gray를 미리 읽었으므로 build_ocr_region_sheet에 넘겨 재독 방지
    sheet = build_ocr_region_sheet(
        image_path=image_path,
        clusters_json=clusters_json,
        output_masked_path=None,
        output_sheet_path=None,
        output_json_path=None,
        enable_edge_barrier_rescue=True,
        output_vis_path=output_vis_path,
        image=image,
        gray=gray,
        manual_virtual_walls=[],
    )
    regions = sheet["regions"]
    regions.extend(_manual_bubble_regions(manual_regions or [], groups))
    regions.extend(_manual_seed_bubble_regions(image, groups, manual_seed_points or [], manual_virtual_walls or []))
    regions.extend(_manual_edge_barrier_regions(
        image,
        groups,
        manual_virtual_walls or [],
        diagnostics=manual_guidance_report["edge_boxes"],
    ))
    _invalidate_regions_replaced_by_manual_edge(regions)
    _invalidate_regions_replaced_by_manual_overrides(regions, manual_regions or [], manual_virtual_walls or [])
    _invalidate_suppressed_regions(regions, suppressed_regions or [])
    regions = _split_manual_seed_regions(regions, groups, manual_split_guides or [], image_shape=image.shape[:2])
    source_meta_by_anchor: Dict[int, Dict[str, Any]] = {}
    for source_region in sheet.get("regions", []):
        for aid in source_region.get("anchor_ids", []):
            try:
                source_meta_by_anchor[int(aid)] = source_region
            except (TypeError, ValueError):
                continue

    # 다른 valid bubble을 완전히 포함하는 bubble 무효화
    # (flood-fill이 패널 전체를 채우는 경우 발생 — 포함하는 쪽을 탈락시켜 unboxed로 재처리)
    _invalidate_containing_bubbles(regions)

    masked_image = image.copy()
    reused_unboxed_inpaint = False
    if reuse_existing_unboxed_inpaint and base_masked_path:
        base_masked = cv2.imread(base_masked_path)
        if base_masked is not None and base_masked.shape == image.shape:
            masked_image = base_masked.copy()
            reused_unboxed_inpaint = True
    bubble_base_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    protected_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    split_parent_masks: Dict[Tuple[int, int, int, int], np.ndarray] = {}

    valid_regions = [r for r in regions if r.get("is_valid")]
    crops: List[Dict[str, Any]] = []
    region_id_counter = 1

    for region in valid_regions:
        is_dark_flood_region = region.get("method") == "dark_flood"
        m = region.get("mask") if isinstance(region.get("mask"), np.ndarray) else _polygon_to_mask(region.get("polygon"), image.shape)
        masking_mask = _mask_for_bubble_erasure(region, m)
        mask_rect = _mask_bounding_rect(m)
        if mask_rect is not None and not region.get("split_parent_rect"):
            region["bubble_rect"] = mask_rect
        if masking_mask is not None and not is_dark_flood_region:
            bubble_base_mask[masking_mask > 0] = 255
            split_parent_rect = region.get("split_parent_rect")
            if split_parent_rect and len(split_parent_rect) == 4:
                parent_key = tuple(_as_rect(split_parent_rect))
                if parent_key not in split_parent_masks:
                    split_parent_masks[parent_key] = np.zeros(image.shape[:2], dtype=np.uint8)
                split_parent_masks[parent_key][masking_mask > 0] = 255
        else:
            rect = region.get("bubble_rect")
            if rect and not is_dark_flood_region:
                x, y, w, h = _as_rect(rect)
                cv2.rectangle(bubble_base_mask, (x, y), (x + w, y + h), 255, -1)

        rect = region.get("bubble_rect")
        if not rect:
            continue
        x, y, w, h = _as_rect(rect)
        crop_rect = region.get("ocr_rect") or rect
        cx, cy, cw, ch = _as_rect(crop_rect)
        ocr_text_rects = region.get("ocr_text_rects") or []
        safe_text_rect, safe_text_rect_quality = _compute_safe_text_rect(m, rect, ocr_text_rects)
        # bubble + mask 있으면 항상 full flood mask 사용
        # ocr_text_rects(TG bbox)는 split 결과용이며, mask가 있는 bubble에는 적용하지 않음
        if m is not None:
            crop_img = np.full((h, w, 3), 255, dtype=np.uint8)
            mask_crop = m[y:y+h, x:x+w]
            image_crop = image[y:y+h, x:x+w]
            crop_img[mask_crop > 0] = image_crop[mask_crop > 0]
        elif ocr_text_rects:
            # mask 없이 split ocr_text_rects만 있는 경우 (bbox_fallback split 결과)
            crop_img = np.full((ch, cw, 3), 255, dtype=np.uint8)
            for text_rect in ocr_text_rects:
                tx, ty, tw, th = _as_rect(text_rect)
                ix1 = max(cx, tx)
                iy1 = max(cy, ty)
                ix2 = min(cx + cw, tx + tw)
                iy2 = min(cy + ch, ty + th)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                crop_img[iy1-cy:iy2-cy, ix1-cx:ix2-cx] = image[iy1:iy2, ix1:ix2]
        elif region.get("ocr_rect"):
            crop_img = image[cy:cy+ch, cx:cx+cw].copy()
        else:
            crop_img = image[y:y+h, x:x+w].copy()

        if is_dark_flood_region:
            anchor_groups = [
                group for idx, group in enumerate(groups, start=1)
                if _tg_id(group, idx) in {int(aid) for aid in region.get("anchor_ids", [])}
            ]
            _erase_bright_text_on_dark(image, masked_image, rect, anchor_groups)

        crops.append({
            "region_id": region_id_counter,
            "anchor_ids": region.get("anchor_ids", []),
            "source_rect": rect,
            "ocr_rect": crop_rect if crop_rect != rect else None,
            "ocr_text_rects": ocr_text_rects,
            "crop": crop_img,
            "type": "bubble",
            "sort_rect": region.get("split_parent_rect") or rect,
            "split_order": region.get("split_order", 0),
            "split_line": region.get("split_line"),
            "source_order": region.get("region_id"),
            "source_method": region.get("method"),
            "source_region_type": region.get("region_type_guess"),
            "quality_flags": region.get("quality_flags", []),
            "review_tags": region.get("review_tags", []),
            "black_contour_probe": region.get("black_contour_probe"),
            "polygon": region.get("polygon"),
            "mask": m,
            "mask_centroid": _compute_mask_centroid(m),
            "safe_text_rect": safe_text_rect,
            "safe_text_rect_quality": safe_text_rect_quality,
        })
        region_id_counter += 1

    manual_unboxed_crops = _manual_unboxed_crops(image, manual_regions or [], groups, img_w, img_h)
    manual_unboxed_tg_ids: set[int] = set()
    group_by_id = {_tg_id(group, idx): group for idx, group in enumerate(groups, start=1)}
    for item in manual_unboxed_crops:
        crops.append(item)
        manual_unboxed_tg_ids.update(int(tid) for tid in item.get("anchor_ids", []))
        if not _apply_manual_ubox_lama(image, masked_image, item, group_by_id):
            item["inpaint_skipped"] = True
            item["quality_flags"] = list(dict.fromkeys(
                [str(flag) for flag in item.get("quality_flags", [])] + ["manual_ubox_inpaint_skipped"]
            ))
        region_id_counter += 1

    protected_mask[_expand_mask(bubble_base_mask, bubble_protect_pad) > 0] = 255

    # ── 2. Unboxed Text Erase (LaMa) ──
    # valid 버블에 속한 TG는 이미 bubble crop으로 처리됨 → unboxed 후보에서 제외
    valid_bubble_tg_ids: set[int] = set()
    for r in valid_regions:
        valid_bubble_tg_ids.update(int(t) for t in r.get("anchor_ids", []))
    non_ocr_skip_reasons = {
        "bottom_landscape_footer",
        "dark_bg_text",
        "edge_narrow_metadata",   # 페이지 가장자리 side/metadata 텍스트 — 마스킹도 제외
        "english_only_text",
        "large_single_glyph_sfx_like",
        "large_short_kana_sfx_like",
        "short_non_japanese_noise_text",
        "symbol_only_text",
        "tiny_landscape_text_noise",
    }
    non_ocr_tg_ids: set[int] = set()
    for skipped_item in sheet.get("skipped", []):
        if skipped_item.get("reason") in non_ocr_skip_reasons:
            try:
                non_ocr_tg_ids.add(int(skipped_item.get("cluster_id")))
            except (TypeError, ValueError):
                continue

    eligible: List[Tuple[int, Dict]] = []
    for idx, group in enumerate(groups, start=1):
        tid = _tg_id(group, idx)
        if tid in valid_bubble_tg_ids or tid in non_ocr_tg_ids or tid in manual_unboxed_tg_ids:
            continue
        if _is_noise(_tg_text(group)):
            continue
        style_decision = style_decisions_by_tg.get(tid, {})
        if style_decision.get("include_policy") == "exclude":
            continue
        eligible.append((tid, group))

    def _style_meta_for_cluster(cluster: List[Tuple[int, Dict]]) -> Dict[str, Any]:
        decisions = [
            style_decisions_by_tg[int(tid)]
            for tid, _group in cluster
            if int(tid) in style_decisions_by_tg
        ]
        return _aggregate_style_decisions(decisions)

    for cluster in _cluster_adjacent_tgs(eligible):
        tids = [t for t, _ in cluster]
        cand = _extract_with_lane_retry(
            image, [g for _, g in cluster], pad=pad, lane_pad=lane_pad, dark_threshold=dark_threshold
        )
        if cand["mask_pixels"] < min_mask_pixels or cand["erase_rect"][2] <= 0:
            dark_vertical_cluster: List[Tuple[int, Dict]] = []
            for _tid, group in cluster:
                rect = _as_rect(group.get("fallback_rect") or group.get("tight_rect") or group.get("bounding_rect"))
                if (
                    rect[2] > 0
                    and rect[3] > 0
                    and _is_dark_bg_text(gray, rect)
                    and not _is_horizontal_text_group(group, rect)
                ):
                    dark_vertical_cluster.append((_tid, group))

            if dark_vertical_cluster:
                crops.append(_make_consolidated_unboxed_crop(
                    image,
                    dark_vertical_cluster,
                    img_w,
                    img_h,
                    source_meta_by_anchor,
                    inpaint_skipped=True,
                    source_method="unboxed_consolidated" if len(dark_vertical_cluster) > 1 else "dark_bg_vertical_fallback",
                    source_region_type="dark_bg_text",
                    extra_quality_flags=[
                        "dark_background",
                        "dark_flood_candidate",
                        "vertical_dark_bg_allowed",
                        "inpaint_skipped_dark_bg",
                    ] + _style_meta_for_cluster(dark_vertical_cluster).get("quality_flags", []),
                    extra_review_tags=["dark_flood_candidate"] + _style_meta_for_cluster(dark_vertical_cluster).get("review_tags", []),
                ))
                region_id_counter += 1
            continue

        rx, ry, rw, rh = cand["roi_rect"]
        text_mask, mask_strategy_meta = _select_unboxed_inpaint_mask(cand)
        roi_protected = protected_mask[ry : ry + rh, rx : rx + rw]

        safe_text_mask = np.where((text_mask > 0) & (roi_protected == 0), 255, 0).astype(np.uint8)
        if safe_text_mask.max() > 0 and not reused_unboxed_inpaint:
            engine = str(mask_strategy_meta.get("recommended_inpaint_engine_override") or cand.get("recommended_inpaint_engine") or "lama")
            if engine == "telea":
                inpaint_roi = cv2.inpaint(
                    cand["roi"],
                    safe_text_mask,
                    int(cand.get("inpaint_radius", 5)),
                    cv2.INPAINT_TELEA,
                )
            else:
                lama = _get_lama()
                roi_pil = Image.fromarray(cv2.cvtColor(cand["roi"], cv2.COLOR_BGR2RGB))
                lama_out = lama(roi_pil, Image.fromarray(safe_text_mask))
                inpaint_roi = cv2.cvtColor(np.array(lama_out), cv2.COLOR_RGB2BGR)
            lh, lw = inpaint_roi.shape[:2]
            apply_h, apply_w = min(rh, lh), min(rw, lw)
            roi_slice = masked_image[ry : ry + apply_h, rx : rx + apply_w]
            roi_slice[safe_text_mask[:apply_h, :apply_w] > 0] = inpaint_roi[:apply_h, :apply_w][safe_text_mask[:apply_h, :apply_w] > 0]
        cand.update(mask_strategy_meta)

        ux, uy, uw, uh = _as_rect(cand["erase_rect"])

        # unboxed fallback은 과소 영역보다 과대 영역이 낫다.
        # 인접 TG cluster를 하나의 OCR/overlay region으로 유지해 번역 문맥과 rect를 함께 보존한다.
        crops.append(_make_consolidated_unboxed_crop(
            image,
            cluster,
            img_w,
            img_h,
            source_meta_by_anchor,
            inpaint_skipped=False,
            extra_quality_flags=_style_meta_for_cluster(cluster).get("quality_flags", []),
            extra_review_tags=_style_meta_for_cluster(cluster).get("review_tags", []),
        ))
        region_id_counter += 1

    for parent_rect, split_mask in split_parent_masks.items():
        x, y, w, h = parent_rect
        if w <= 0 or h <= 0:
            continue
        crop = split_mask[y:y + h, x:x + w]
        if crop.size == 0 or crop.max() == 0:
            continue
        close_px = int(round(min(w, h) * 0.025))
        close_px = max(3, min(9, close_px))
        kernel_size = close_px * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        closed = cv2.morphologyEx(crop, cv2.MORPH_CLOSE, kernel, iterations=1)
        seam_fill = np.where((closed > 0) & (crop == 0), 255, 0).astype(np.uint8)
        if close_px > 0:
            seam_fill[:close_px, :] = 0
            seam_fill[-close_px:, :] = 0
            seam_fill[:, :close_px] = 0
            seam_fill[:, -close_px:] = 0
        bubble_base_mask[y:y + h, x:x + w][seam_fill > 0] = 255

    if np.any(protected_mask):
        masked_image[protected_mask > 0] = image[protected_mask > 0]
    scaled_bubble_mask_erode = _scaled_erode_px(bubble_mask_erode, image.shape)
    bubble_overlay_mask = _erode_mask(bubble_base_mask, scaled_bubble_mask_erode)
    masked_image[bubble_overlay_mask > 0] = (255, 255, 255)

    cv2.imwrite(output_masked_path, masked_image)

    # ── 3. OCR Sheet 조립 ──
    crops = _apply_manual_unboxed_overrides(image, crops, img_w, img_h)
    crops = _dedupe_dialogue_crops(crops)

    # 패널 경계 y-값 감지: cy 분포에서 평균 gap의 gap_factor 배 이상인 지점을 경계로 판단
    def _detect_panel_y_cuts(items: list, gap_factor: float = 2.5) -> list:
        if len(items) < 2:
            return []
        cy_vals = sorted(
            (r.get("sort_rect") or r["source_rect"])[1]
            + (r.get("sort_rect") or r["source_rect"])[3] / 2
            for r in items
        )
        gaps = [
            (cy_vals[i + 1] - cy_vals[i], (cy_vals[i] + cy_vals[i + 1]) / 2)
            for i in range(len(cy_vals) - 1)
        ]
        if not gaps:
            return []
        avg_gap = sum(g for g, _ in gaps) / len(gaps)
        threshold = max(avg_gap * gap_factor, 150.0)
        return [mid for gap, mid in gaps if gap > threshold]

    _panel_y_cuts = _detect_panel_y_cuts(crops)
    # 가로로 긴(2p 스프레드) 이미지는 물리적으로 자르지 않는 대신, 오른쪽 절반(1p)
    # numbering을 모두 끝내고 왼쪽 절반(2p)으로 넘어가도록 읽기 순서만 페이지 단위로 나눈다.
    _is_wide_spread = img_h > 0 and (img_w / img_h) >= 1.25
    _page_mid_x = img_w / 2

    def _reading_order_key(item: Dict[str, Any]) -> Tuple[float, float, float, float]:
        rect = item.get("sort_rect") or item["source_rect"]
        cx = rect[0] + rect[2] / 2
        cy = rect[1] + rect[3] / 2
        # 페이지: 오른쪽 절반(1p, 0) → 왼쪽 절반(2p, 1). 스프레드가 아니면 항상 0.
        page = 1.0 if (_is_wide_spread and cx < _page_mid_x) else 0.0
        # 패널 밴드: 위→아래 (0, 1, 2...)
        band = float(sum(1 for cut in _panel_y_cuts if cy > cut))
        source_order = item.get("source_order")
        split_order = float(item.get("split_order") or 0)
        # 패널 내 읽기 순서: 오른쪽 → 왼쪽 (cx 내림차순)
        # source_order가 있는 분할 서브리전은 미세 offset으로 같은 부모끼리 인접 보장
        sub = (float(source_order) * 1e-4 + split_order * 1e-5) if source_order is not None else (split_order * 1e-5)
        return (page, band, 10000.0 - cx, sub)

    crops.sort(key=_reading_order_key)
    for i, item in enumerate(crops):
        item["region_id"] = i + 1

    _SHEET_GAP = 10

    n_crops = len(crops)
    max_height = max((c["crop"].shape[0] for c in crops), default=1)

    sheet_x = 0
    output_regions = []

    for i, item in enumerate(crops):
        c_img = item["crop"]
        h, w = c_img.shape[:2]

        region_data = {
            "region_id": item["region_id"],
            "anchor_ids": item["anchor_ids"],
            "sheet_rect": [sheet_x, 0, w, h],
            "type": item["type"],
        }
        if item["type"] == "unboxed":
            region_data["erase_rect"] = item["erase_rect"]
            region_data["unboxed_rect"] = item["source_rect"]
            region_data["consolidated_text_group"] = item.get("consolidated_text_group")
            region_data["inpaint_skipped"] = item.get("inpaint_skipped", False)
        else:
            region_data["bubble_rect"] = item["source_rect"]
            if item.get("polygon"):
                region_data["contour"] = item["polygon"]
                region_data["polygon"] = item["polygon"]
            if item.get("mask") is not None:
                mask_artifact_dir.mkdir(parents=True, exist_ok=True)
                mask_path = mask_artifact_dir / f"{Path(image_path).stem}_R{item['region_id']}_mask.png"
                cv2.imwrite(str(mask_path), np.where(item["mask"] > 0, 255, 0).astype(np.uint8))
                region_data["mask_ref"] = str(mask_path)
            # mask_centroid: polygon mask 있을 때만 존재. overlay ix/iy 중심 보정용
            if item.get("mask_centroid") is not None:
                region_data["mask_centroid"] = item["mask_centroid"]
            if item.get("safe_text_rect") is not None:
                region_data["safe_text_rect"] = item["safe_text_rect"]
            if item.get("safe_text_rect_quality"):
                region_data["safe_text_rect_quality"] = item["safe_text_rect_quality"]
            if item.get("ocr_rect"):
                region_data["ocr_rect"] = item["ocr_rect"]
            if item.get("ocr_text_rects"):
                region_data["ocr_text_rects"] = item["ocr_text_rects"]
        if item.get("source_method"):
            region_data["source_method"] = item["source_method"]
        if item.get("source_region_type"):
            region_data["source_region_type"] = item["source_region_type"]
        if item.get("quality_flags"):
            region_data["quality_flags"] = item["quality_flags"]
        if item.get("review_tags"):
            region_data["review_tags"] = item["review_tags"]
        if item.get("black_contour_probe"):
            region_data["black_contour_probe"] = item["black_contour_probe"]
        if item.get("sort_rect") and item.get("sort_rect") != item["source_rect"]:
            region_data["split_parent_rect"] = item["sort_rect"]
            region_data["split_order"] = item.get("split_order", 0)
        if item.get("split_line"):
            region_data["split_line"] = item["split_line"]

        output_regions.append(region_data)
        sheet_x += w + (_SHEET_GAP if i < n_crops - 1 else 0)

    # Indexed sheet — 각 crop 왼쪽에 라벨 컬럼 배치 (상단 gutter 제거)
    _LABEL_W = max(0, indexed_label_gutter)  # 좌측 라벨 열 너비 (기존 파라미터 재활용)
    idx_total_w = max(1, sum(_LABEL_W + c["crop"].shape[1] for c in crops) + _SHEET_GAP * max(0, n_crops - 1))
    idx_total_h = max(1, max_height)
    indexed = np.full((idx_total_h, idx_total_w, 3), 255, dtype=np.uint8)

    idx_x = 0
    for i, (item, region) in enumerate(zip(crops, output_regions)):
        c_img = item["crop"]
        h, w = c_img.shape[:2]
        img_x = idx_x + _LABEL_W

        # 이미지 배치
        indexed[0:h, img_x:img_x + w] = c_img

        # indexed_sheet_rect: 라벨+이미지 전체 영역
        region["indexed_sheet_rect"] = [idx_x, 0, _LABEL_W + w, h]

        # 이미지 테두리
        cv2.rectangle(indexed, (img_x, 0), (img_x + w - 1, h - 1), (225, 225, 225), 1)

        # 라벨-이미지 구분선
        if _LABEL_W > 0:
            cv2.line(indexed, (img_x - 1, 0), (img_x - 1, max(0, h - 1)), (180, 180, 180), 1)

        # 라벨 텍스트
        is_unboxed = region["type"] == "unboxed"
        prefix = "U" if is_unboxed else "R"
        label = f"{prefix}{region['region_id']}"
        aids = "TG" + ",".join(str(a) for a in region["anchor_ids"]) if region["anchor_ids"] else "TG-"

        color_id = (0, 90, 220) if is_unboxed else (0, 0, 220)
        color_tg = (130, 80, 0) if is_unboxed else (200, 60, 0)

        cv2.putText(indexed, label, (idx_x + 4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color_id, 2, cv2.LINE_AA)
        cv2.putText(indexed, aids, (idx_x + 4, 37), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color_tg, 1, cv2.LINE_AA)

        idx_x += _LABEL_W + w + (_SHEET_GAP if i < n_crops - 1 else 0)

    cv2.imwrite(indexed_output_sheet_path, indexed)
    _overlay_final_unboxed_regions(image, output_vis_path, output_regions, groups)

    output_data = {
        "image": image_path,
        "clusters": clusters_json,
        "analysis_status": "complete",
        "ocr_region_count": len(output_regions),
        "sheet_size": [int(idx_total_w), int(idx_total_h)],
        "bubble_protect_pad": bubble_protect_pad,
        "bubble_mask_erode": scaled_bubble_mask_erode,
        "bubble_mask_erode_base": bubble_mask_erode,
        "manual_guidance_report": manual_guidance_report,
        "reused_unboxed_inpaint": reused_unboxed_inpaint,
        "regions": output_regions,
    }
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    return output_data


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="만화 대사 추출 — Bubble Detect + LaMa + OCR Sheet 생성")
    parser.add_argument("image_path", help="원본 이미지 경로")
    parser.add_argument("clusters_json", help="text_groups JSON 경로")
    parser.add_argument("output_masked_path", help="마스킹 이미지 출력 경로")
    parser.add_argument("output_json_path", help="regions JSON 출력 경로")
    parser.add_argument("indexed_output_sheet_path", help="indexed OCR sheet 이미지 출력 경로")
    parser.add_argument("--output-vis-path", default=None, help="bubble vis 이미지 출력 경로 (선택)")
    args = parser.parse_args()

    result = build_dialogue_sheet(
        image_path=args.image_path,
        clusters_json=args.clusters_json,
        output_masked_path=args.output_masked_path,
        output_json_path=args.output_json_path,
        indexed_output_sheet_path=args.indexed_output_sheet_path,
        output_vis_path=args.output_vis_path,
    )
    n = len(result.get("regions", []))
    print(f"✅ 대사 추출 완료: {n}개 region → {args.output_json_path}")


if __name__ == "__main__":
    main()
