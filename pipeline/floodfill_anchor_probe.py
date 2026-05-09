#!/usr/bin/env python3
"""
Anchor Text Group 중심점 기반 flood fill 말풍선 후보 시각화.

OpenCV contour를 직접 찾지 않고, Anchor bbox 중심 근처의 흰 픽셀을 seed로 잡아
magic-wand 방식으로 연결된 흰 영역을 채운다. 현재는 pipeline에 연결하지 않고
debug 이미지와 JSON으로만 검증한다.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


Point = Tuple[int, int]
MAX_SAFE_AREA_RATIO = 0.12


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _bbox_center(rect: List[int]) -> Point:
    x, y, w, h = [int(v) for v in rect[:4]]
    return (int(round(x + w / 2)), int(round(y + h / 2)))


def _clip_point(x: int, y: int, img_w: int, img_h: int) -> Point:
    return (max(0, min(img_w - 1, x)), max(0, min(img_h - 1, y)))


def _find_white_seed(gray: np.ndarray, center: Point, rect: List[int]) -> Optional[Point]:
    """
    Anchor 중심이 글자 위에 놓일 수 있으므로, 중심 주변에서 가장 가까운 흰 픽셀을 찾는다.
    """
    img_h, img_w = gray.shape[:2]
    cx, cy = _clip_point(center[0], center[1], img_w, img_h)
    x, y, w, h = [int(v) for v in rect[:4]]
    left, top = max(0, x), max(0, y)
    right, bottom = min(img_w - 1, x + w), min(img_h - 1, y + h)

    max_radius = int(max(w, h) * 0.75) + 20
    thresholds = [248, 242, 235, 225]

    for threshold in thresholds:
        best = None
        best_dist = None

        for radius in range(0, max_radius + 1, 3):
            x1, x2 = max(left, cx - radius), min(right, cx + radius)
            y1, y2 = max(top, cy - radius), min(bottom, cy + radius)
            if x1 > x2 or y1 > y2:
                continue

            roi = gray[y1:y2 + 1, x1:x2 + 1]
            ys, xs = np.where(roi >= threshold)
            if len(xs) == 0:
                continue

            for ox, oy in zip(xs, ys):
                px, py = x1 + int(ox), y1 + int(oy)
                dist = (px - cx) ** 2 + (py - cy) ** 2
                if best_dist is None or dist < best_dist:
                    best = (px, py)
                    best_dist = dist

            if best is not None:
                return best

    return None


def _flood_fill_mask(image: np.ndarray, seed: Point, lo_diff: int = 8, up_diff: int = 10) -> Tuple[np.ndarray, int, List[int]]:
    """
    BGR 이미지에서 seed와 유사한 색상의 연결 영역을 flood fill로 추출한다.
    """
    img_h, img_w = image.shape[:2]
    work = image.copy()
    mask = np.zeros((img_h + 2, img_w + 2), dtype=np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
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
    """
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    h, w = binary.shape[:2]
    flood = binary.copy()
    ff_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    outside = flood
    holes = cv2.bitwise_not(outside)
    return cv2.bitwise_or(binary, holes)


def _touches_image_border(mask: np.ndarray, tolerance: int = 2) -> bool:
    if mask.size == 0:
        return False
    top = mask[:tolerance, :]
    bottom = mask[-tolerance:, :]
    left = mask[:, :tolerance]
    right = mask[:, -tolerance:]
    return bool(np.any(top > 0) or np.any(bottom > 0) or np.any(left > 0) or np.any(right > 0))


def visualize_anchor_floodfill(
    image_path: str,
    clusters_json: str,
    output_json_path: str,
    output_image_path: str,
) -> Dict[str, Any]:
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"이미지를 불러올 수 없습니다: {image_path}")

    clusters = _load_json(clusters_json)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    img_h, img_w = image.shape[:2]
    image_area = img_w * img_h

    overlay = image.copy()
    debug = image.copy()
    results: List[Dict[str, Any]] = []

    palette = [
        (0, 200, 255),
        (0, 255, 0),
        (255, 0, 180),
        (255, 120, 0),
        (0, 160, 255),
        (180, 0, 255),
        (255, 255, 0),
        (0, 90, 255),
    ]

    for idx, cluster in enumerate(clusters, start=1):
        rect = cluster.get("bounding_rect", [])
        if len(rect) < 4:
            continue

        cluster_id = cluster.get("cluster_id", idx)
        center = _bbox_center(rect)
        seed = _find_white_seed(gray, center, rect)
        x, y, w, h = [int(v) for v in rect[:4]]

        cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 150, 255), 3)
        cv2.circle(debug, center, 5, (0, 0, 255), -1)

        result = {
            "cluster_id": cluster_id,
            "anchor_bbox": rect,
            "anchor_center": list(center),
            "seed": list(seed) if seed else None,
            "flood_area": 0,
            "flood_area_ratio": 0,
            "flood_rect": [0, 0, 0, 0],
        }

        if seed is None:
            results.append(result)
            continue

        mask, area, flood_rect = _flood_fill_mask(image, seed)
        filled_mask = _fill_mask_holes(mask)
        inner_mask = _erode_mask(filled_mask, pixels=3)
        inner_area = int(np.count_nonzero(inner_mask))
        touches_border = _touches_image_border(mask)
        area_ratio = area / image_area
        safe = area_ratio <= MAX_SAFE_AREA_RATIO and not touches_border
        color = palette[(idx - 1) % len(palette)]
        mask_area = inner_mask > 0
        overlay[mask_area] = (
            overlay[mask_area].astype(np.float32) * 0.45
            + np.array(color, dtype=np.float32) * 0.55
        ).astype(np.uint8)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inner_contours, _ = cv2.findContours(inner_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(debug, contours, -1, color, 1)
        cv2.drawContours(debug, inner_contours, -1, color, 5)
        cv2.circle(debug, seed, 7, color, -1)

        fx, fy, fw, fh = flood_rect
        cv2.rectangle(debug, (fx, fy), (fx + fw, fy + fh), color, 2)
        cv2.putText(
            debug,
            f"A{cluster_id}",
            (x, max(18, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )

        result.update({
            "seed": list(seed),
            "flood_area": area,
            "flood_area_ratio": round(area_ratio, 5),
            "flood_rect": flood_rect,
            "inner_area": inner_area,
            "inner_area_ratio": round(inner_area / image_area, 5),
            "erode_pixels": 3,
            "touches_image_border": touches_border,
            "safe_region": safe,
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


def apply_floodfill_white_mask(
    image_path: str,
    clusters_json: str,
    output_image_path: str,
    region_output_path: Optional[str] = None,
    erode_pixels: int = 3,
    max_area_ratio: float = MAX_SAFE_AREA_RATIO,
) -> Dict[str, Any]:
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"이미지를 불러올 수 없습니다: {image_path}")

    clusters = _load_json(clusters_json)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    img_h, img_w = image.shape[:2]
    image_area = img_w * img_h

    result_image = image.copy()
    region_image = np.full_like(image, 255)
    combined_mask = np.zeros((img_h, img_w), dtype=np.uint8)
    applied: List[Dict[str, Any]] = []

    for idx, cluster in enumerate(clusters, start=1):
        rect = cluster.get("bounding_rect", [])
        if len(rect) < 4:
            continue

        cluster_id = cluster.get("cluster_id", idx)
        center = _bbox_center(rect)
        seed = _find_white_seed(gray, center, rect)
        if seed is None:
            applied.append({
                "cluster_id": cluster_id,
                "applied": False,
                "reason": "no_seed",
            })
            continue

        mask, area, flood_rect = _flood_fill_mask(image, seed)
        area_ratio = area / image_area
        touches_border = _touches_image_border(mask)
        if area_ratio > max_area_ratio or touches_border:
            applied.append({
                "cluster_id": cluster_id,
                "applied": False,
                "reason": "unsafe_region",
                "seed": list(seed),
                "flood_area_ratio": round(area_ratio, 5),
                "touches_image_border": touches_border,
                "flood_rect": flood_rect,
            })
            continue

        filled_mask = _fill_mask_holes(mask)
        inner_mask = _erode_mask(filled_mask, pixels=erode_pixels)
        combined_mask = cv2.bitwise_or(combined_mask, inner_mask)
        result_image[inner_mask > 0] = (255, 255, 255)
        applied.append({
            "cluster_id": cluster_id,
            "applied": True,
            "seed": list(seed),
            "flood_area_ratio": round(area_ratio, 5),
            "inner_area_ratio": round(int(np.count_nonzero(inner_mask)) / image_area, 5),
            "flood_rect": flood_rect,
            "erode_pixels": erode_pixels,
        })

    cv2.imwrite(output_image_path, result_image)
    region_image[combined_mask > 0] = image[combined_mask > 0]
    if region_output_path:
        cv2.imwrite(region_output_path, region_image)
    return {
        "image": image_path,
        "clusters": clusters_json,
        "output": output_image_path,
        "region_output": region_output_path,
        "applied": applied,
    }


def create_packed_ocr_sheet(
    image_path: str,
    clusters_json: str,
    output_image_path: str,
    output_json_path: str,
    indexed_output_image_path: Optional[str] = None,
    erode_pixels: int = 3,
    max_area_ratio: float = MAX_SAFE_AREA_RATIO,
    indexed_label_gutter: int = 32,
) -> Dict[str, Any]:
    """
    안전한 flood region crop만 모아 OCR용 compact sheet를 만든다.

    같은 flood_rect를 공유하는 Anchor는 같은 Speech Bubble Region으로 병합한다.
    clean sheet는 OCR 순도를 보기 위한 버전이고, indexed sheet는 region 매칭 실험용이다.
    """
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"이미지를 불러올 수 없습니다: {image_path}")

    clusters = _load_json(clusters_json)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    img_h, img_w = image.shape[:2]
    image_area = img_w * img_h

    merged: Dict[Tuple[int, int, int, int], Dict[str, Any]] = {}
    skipped: List[Dict[str, Any]] = []

    for idx, cluster in enumerate(clusters, start=1):
        rect = cluster.get("bounding_rect", [])
        if len(rect) < 4:
            continue

        cluster_id = int(cluster.get("cluster_id", idx))
        center = _bbox_center(rect)
        seed = _find_white_seed(gray, center, rect)
        if seed is None:
            skipped.append({"cluster_id": cluster_id, "reason": "no_seed"})
            continue

        mask, area, flood_rect = _flood_fill_mask(image, seed)
        area_ratio = area / image_area
        touches_border = _touches_image_border(mask)
        if area_ratio > max_area_ratio or touches_border:
            skipped.append({
                "cluster_id": cluster_id,
                "reason": "unsafe_region",
                "seed": list(seed),
                "flood_area_ratio": round(area_ratio, 5),
                "touches_image_border": touches_border,
                "flood_rect": flood_rect,
            })
            continue

        filled_mask = _fill_mask_holes(mask)
        inner_mask = _erode_mask(filled_mask, pixels=erode_pixels)
        key = tuple(int(v) for v in flood_rect[:4])
        if key not in merged:
            merged[key] = {
                "anchor_ids": [],
                "seeds": [],
                "flood_rect": list(key),
                "mask": inner_mask,
                "flood_area_ratio": area_ratio,
                "inner_area": int(np.count_nonzero(inner_mask)),
            }
        else:
            merged[key]["mask"] = cv2.bitwise_or(merged[key]["mask"], inner_mask)
            merged[key]["inner_area"] = int(np.count_nonzero(merged[key]["mask"]))

        merged[key]["anchor_ids"].append(cluster_id)
        merged[key]["seeds"].append(list(seed))

    regions = sorted(
        merged.values(),
        key=lambda item: (item["flood_rect"][0], item["flood_rect"][1]),
        reverse=True,
    )

    crops: List[Dict[str, Any]] = []
    total_width = 0
    max_height = 0
    for region_id, region in enumerate(regions, start=1):
        x, y, w, h = [int(v) for v in region["flood_rect"]]
        crop = np.full((h, w, 3), 255, dtype=np.uint8)
        mask_crop = region["mask"][y:y + h, x:x + w]
        image_crop = image[y:y + h, x:x + w]
        crop[mask_crop > 0] = image_crop[mask_crop > 0]
        crops.append({
            "region_id": region_id,
            "region": region,
            "crop": crop,
        })
        total_width += w
        max_height = max(max_height, h)

    if total_width == 0 or max_height == 0:
        sheet = np.full((1, 1, 3), 255, dtype=np.uint8)
    else:
        sheet = np.full((max_height, total_width, 3), 255, dtype=np.uint8)

    sheet_x = 0
    output_regions: List[Dict[str, Any]] = []
    for item in crops:
        crop = item["crop"]
        h, w = crop.shape[:2]
        sheet[0:h, sheet_x:sheet_x + w] = crop
        region = item["region"]
        output_regions.append({
            "region_id": item["region_id"],
            "anchor_ids": region["anchor_ids"],
            "original_flood_rect": region["flood_rect"],
            "sheet_rect": [sheet_x, 0, w, h],
            "seed_points": region["seeds"],
            "flood_area_ratio": round(float(region["flood_area_ratio"]), 5),
            "inner_area": int(region["inner_area"]),
            "erode_pixels": erode_pixels,
            "safe_region": True,
        })
        sheet_x += w

    cv2.imwrite(output_image_path, sheet)

    if indexed_output_image_path:
        gutter = max(0, indexed_label_gutter)
        indexed = np.full((sheet.shape[0] + gutter, sheet.shape[1], 3), 255, dtype=np.uint8)
        indexed[gutter:gutter + sheet.shape[0], 0:sheet.shape[1]] = sheet
        for region in output_regions:
            sx, sy, sw, _ = region["sheet_rect"]
            region["indexed_sheet_rect"] = [sx, sy + gutter, sw, region["sheet_rect"][3]]
            label = f"R{region['region_id']}"
            cv2.putText(indexed, label, (sx + 4, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.imwrite(indexed_output_image_path, indexed)

    output = {
        "image": image_path,
        "clusters": clusters_json,
        "output": output_image_path,
        "indexed_output": indexed_output_image_path,
        "sheet_size": [int(sheet.shape[1]), int(sheet.shape[0])],
        "original_size": [img_w, img_h],
        "regions": output_regions,
        "skipped": skipped,
    }
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output


def create_floodfill_fallback_outputs(
    image_path: str,
    clusters_json: str,
    output_masked_path: str,
    output_regions_only_path: str,
    output_sheet_path: str,
    output_json_path: str,
    indexed_output_sheet_path: Optional[str] = None,
    erode_pixels: int = 3,
    max_area_ratio: float = MAX_SAFE_AREA_RATIO,
    min_flood_rect_anchor_ratio: float = 1.0,
    max_flood_rect_anchor_ratio: float = 12.0,
    indexed_label_gutter: int = 32,
) -> Dict[str, Any]:
    """
    floodFill을 우선 적용하고, 실패/위험 region은 union bbox로 fallback한다.

    - flood: 말풍선 내부 inner mask 사용
    - union_bbox_fallback: Anchor Text Group bounding_rect를 그대로 사용
    """
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"이미지를 불러올 수 없습니다: {image_path}")

    clusters = _load_json(clusters_json)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    img_h, img_w = image.shape[:2]
    image_area = img_w * img_h

    grouped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    diagnostics: List[Dict[str, Any]] = []

    for idx, cluster in enumerate(clusters, start=1):
        rect = cluster.get("bounding_rect", [])
        if len(rect) < 4:
            continue

        cluster_id = int(cluster.get("cluster_id", idx))
        x, y, w, h = [int(v) for v in rect[:4]]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(img_w, x + w), min(img_h, y + h)
        center = _bbox_center(rect)
        seed = _find_white_seed(gray, center, rect)

        method = "union_bbox_fallback"
        fallback_reason = "no_seed"
        flood_rect = [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]
        region_mask = np.zeros((img_h, img_w), dtype=np.uint8)
        flood_area_ratio = 0.0
        inner_area_ratio = 0.0
        touches_border = False
        flood_rect_anchor_ratio = 0.0
        quality_flags: List[str] = []

        if seed is not None:
            mask, area, detected_flood_rect = _flood_fill_mask(image, seed)
            flood_area_ratio = area / image_area
            touches_border = _touches_image_border(mask)
            fx, fy, fw, fh = [int(v) for v in detected_flood_rect[:4]]
            anchor_area = max(1, (x2 - x1) * (y2 - y1))
            flood_rect_anchor_ratio = (fw * fh) / anchor_area
            ratio_is_safe = min_flood_rect_anchor_ratio <= flood_rect_anchor_ratio <= max_flood_rect_anchor_ratio
            if flood_area_ratio <= max_area_ratio and not touches_border and ratio_is_safe:
                filled_mask = _fill_mask_holes(mask)
                region_mask = _erode_mask(filled_mask, pixels=erode_pixels)
                method = "flood"
                fallback_reason = ""
                flood_rect = detected_flood_rect
                inner_area_ratio = int(np.count_nonzero(region_mask)) / image_area
            else:
                fallback_reason = "unsafe_region"
                if not ratio_is_safe:
                    fallback_reason = "unsafe_flood_rect_anchor_ratio"
                    quality_flags.append(fallback_reason)
                if touches_border:
                    quality_flags.append("touches_image_border")
                if flood_area_ratio > max_area_ratio:
                    quality_flags.append("excessive_flood_area")

        if method == "union_bbox_fallback" and x2 > x1 and y2 > y1:
            region_mask[y1:y2, x1:x2] = 255
            inner_area_ratio = int(np.count_nonzero(region_mask)) / image_area
            if fallback_reason and fallback_reason not in quality_flags:
                quality_flags.append(fallback_reason)

        key = (method, *[int(v) for v in flood_rect[:4]])
        if key not in grouped:
            grouped[key] = {
                "method": method,
                "fallback_reason": fallback_reason,
                "anchor_ids": [],
                "seed_points": [],
                "flood_rect": [int(v) for v in flood_rect[:4]],
                "mask": region_mask,
                "flood_area_ratio": flood_area_ratio,
                "inner_area_ratio": inner_area_ratio,
                "touches_image_border": touches_border,
                "flood_rect_anchor_ratio": flood_rect_anchor_ratio,
                "quality_flags": quality_flags,
            }
        else:
            grouped[key]["mask"] = cv2.bitwise_or(grouped[key]["mask"], region_mask)
            grouped[key]["inner_area_ratio"] = int(np.count_nonzero(grouped[key]["mask"])) / image_area

        grouped[key]["anchor_ids"].append(cluster_id)
        if seed is not None:
            grouped[key]["seed_points"].append(list(seed))

        diagnostics.append({
            "cluster_id": cluster_id,
            "method": method,
            "fallback_reason": fallback_reason,
            "anchor_bbox": rect,
            "seed": list(seed) if seed else None,
            "flood_rect": [int(v) for v in flood_rect[:4]],
            "flood_area_ratio": round(float(flood_area_ratio), 5),
            "inner_area_ratio": round(float(inner_area_ratio), 5),
            "flood_rect_anchor_ratio": round(float(flood_rect_anchor_ratio), 2),
            "touches_image_border": touches_border,
            "quality_flags": quality_flags,
        })

    regions = sorted(
        grouped.values(),
        key=lambda item: (item["flood_rect"][0], item["flood_rect"][1]),
        reverse=True,
    )

    combined_mask = np.zeros((img_h, img_w), dtype=np.uint8)
    for region in regions:
        combined_mask = cv2.bitwise_or(combined_mask, region["mask"])

    masked = image.copy()
    masked[combined_mask > 0] = (255, 255, 255)
    regions_only = np.full_like(image, 255)
    regions_only[combined_mask > 0] = image[combined_mask > 0]
    cv2.imwrite(output_masked_path, masked)
    cv2.imwrite(output_regions_only_path, regions_only)

    crops: List[Dict[str, Any]] = []
    total_width = 0
    max_height = 0
    output_regions: List[Dict[str, Any]] = []

    for region_id, region in enumerate(regions, start=1):
        x, y, w, h = [int(v) for v in region["flood_rect"]]
        crop = np.full((h, w, 3), 255, dtype=np.uint8)
        mask_crop = region["mask"][y:y + h, x:x + w]
        image_crop = image[y:y + h, x:x + w]
        crop[mask_crop > 0] = image_crop[mask_crop > 0]
        crops.append({"region_id": region_id, "crop": crop, "region": region})
        total_width += w
        max_height = max(max_height, h)

    sheet = np.full((max_height or 1, total_width or 1, 3), 255, dtype=np.uint8)
    sheet_x = 0
    for item in crops:
        crop = item["crop"]
        h, w = crop.shape[:2]
        sheet[0:h, sheet_x:sheet_x + w] = crop
        region = item["region"]
        output_regions.append({
            "region_id": item["region_id"],
            "anchor_ids": region["anchor_ids"],
            "method": region["method"],
            "fallback_reason": region["fallback_reason"],
            "original_rect": region["flood_rect"],
            "sheet_rect": [sheet_x, 0, w, h],
            "seed_points": region["seed_points"],
            "flood_area_ratio": round(float(region["flood_area_ratio"]), 5),
            "inner_area_ratio": round(float(region["inner_area_ratio"]), 5),
            "flood_rect_anchor_ratio": round(float(region["flood_rect_anchor_ratio"]), 2),
            "touches_image_border": region["touches_image_border"],
            "quality_flags": region["quality_flags"],
            "safe_region": region["method"] == "flood",
        })
        sheet_x += w

    cv2.imwrite(output_sheet_path, sheet)

    if indexed_output_sheet_path:
        gutter = max(0, indexed_label_gutter)
        indexed = np.full((sheet.shape[0] + gutter, sheet.shape[1], 3), 255, dtype=np.uint8)
        indexed[gutter:gutter + sheet.shape[0], 0:sheet.shape[1]] = sheet
        for region in output_regions:
            sx, sy, sw, _ = region["sheet_rect"]
            region["indexed_sheet_rect"] = [sx, sy + gutter, sw, region["sheet_rect"][3]]
            label = f"R{region['region_id']}"
            color = (0, 160, 0) if region["method"] == "flood" else (0, 0, 255)
            cv2.putText(indexed, label, (sx + 4, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
        cv2.imwrite(indexed_output_sheet_path, indexed)

    output = {
        "image": image_path,
        "clusters": clusters_json,
        "masked_output": output_masked_path,
        "regions_only_output": output_regions_only_path,
        "sheet_output": output_sheet_path,
        "indexed_sheet_output": indexed_output_sheet_path,
        "original_size": [img_w, img_h],
        "sheet_size": [int(sheet.shape[1]), int(sheet.shape[0])],
        "regions": output_regions,
        "diagnostics": diagnostics,
    }
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output


def main() -> None:
    if len(sys.argv) != 5:
        print("Usage: python3 pipeline/floodfill_anchor_probe.py <image> <cv_clusters.json> <output.json> <debug.jpg>")
        sys.exit(1)

    result = visualize_anchor_floodfill(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    matched = sum(1 for item in result["results"] if item["seed"])
    print(f"✅ Anchor flood fill 시각화 완료: {matched}/{len(result['results'])} seeded")
    print(f"   JSON: {sys.argv[3]}")
    print(f"   Debug: {sys.argv[4]}")


if __name__ == "__main__":
    main()
