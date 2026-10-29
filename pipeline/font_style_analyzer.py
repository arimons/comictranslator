#!/usr/bin/env python3
# CV 기반 획두께 분석으로 render_hint를 보정하고 integrated.json에 기록하는 모듈

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


# ──────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────

def _normalized_render_hint(value: Any) -> str:
    render_hint = str(value or "normal")
    if render_hint in {"sfx", "efx"}:
        render_hint = "serif_emphasis"
    if render_hint not in ("normal", "memory_serif", "semibold", "bold", "impact", "serif_emphasis"):
        render_hint = "normal"
    return render_hint


def _clip_rect(rect: list[int], width: int, height: int) -> list[int] | None:
    if not rect or len(rect) != 4:
        return None
    x, y, w, h = [int(v) for v in rect]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(width, x + w), min(height, y + h)
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


def _union_rects(rects: list[list[int]]) -> list[int] | None:
    valid = [r for r in rects if isinstance(r, list) and len(r) == 4 and r[2] > 0 and r[3] > 0]
    if not valid:
        return None
    x1 = min(int(r[0]) for r in valid)
    y1 = min(int(r[1]) for r in valid)
    x2 = max(int(r[0]) + int(r[2]) for r in valid)
    y2 = max(int(r[1]) + int(r[3]) for r in valid)
    return [x1, y1, x2 - x1, y2 - y1]


def _median(values: list[float], default: float = 0.0) -> float:
    return float(np.median(values)) if values else default


def _japanese_text_len(text: Any) -> int:
    return sum(1 for ch in str(text or "") if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")


# ──────────────────────────────────────────────
# Sheet crop 좌표 계산
# ──────────────────────────────────────────────

def _content_origin_in_sheet(region: dict) -> tuple[int, int, int, int] | None:
    indexed = region.get("indexed_sheet_rect")
    sheet_rect = region.get("sheet_rect")
    if indexed and len(indexed) == 4:
        ix, iy, iw, ih = [int(v) for v in indexed]
        if sheet_rect and len(sheet_rect) == 4:
            sw, sh = int(sheet_rect[2]), int(sheet_rect[3])
            label_w = max(0, iw - sw)
            return ix + label_w, iy, min(sw, iw), min(sh, ih)
        return ix, iy, iw, ih
    if sheet_rect and len(sheet_rect) == 4:
        return [int(v) for v in sheet_rect]
    return None


def _style_crop_rect_in_sheet(region: dict, image_width: int, image_height: int) -> list[int] | None:
    content = _content_origin_in_sheet(region)
    if content is None:
        return None
    cx, cy, cw, ch = content

    bubble_rect = region.get("bubble_rect") or region.get("unboxed_rect") or region.get("original_rect")
    text_rects = region.get("ocr_text_rects") or []
    text_union = _union_rects(text_rects)
    if bubble_rect and len(bubble_rect) == 4 and text_union:
        bx, by, _bw, _bh = [int(v) for v in bubble_rect]
        tx, ty, tw, th = text_union
        pad = max(4, int(min(tw, th) * 0.12))
        rel_x = max(0, tx - bx - pad)
        rel_y = max(0, ty - by - pad)
        rel_w = min(cw - rel_x, tw + pad * 2)
        rel_h = min(ch - rel_y, th + pad * 2)
        return _clip_rect([cx + rel_x, cy + rel_y, rel_w, rel_h], image_width, image_height)

    margin_x = max(2, int(cw * 0.08))
    margin_y = max(2, int(ch * 0.08))
    return _clip_rect([cx + margin_x, cy + margin_y, cw - margin_x * 2, ch - margin_y * 2], image_width, image_height)


def _map_original_rect_to_sheet(region: dict, original_rect: Any, image_width: int, image_height: int) -> list[int] | None:
    if not original_rect or len(original_rect) != 4:
        return None
    content = _content_origin_in_sheet(region)
    bubble_rect = region.get("bubble_rect") or region.get("unboxed_rect") or region.get("original_rect")
    if content is None or not bubble_rect or len(bubble_rect) != 4:
        return None
    cx, cy, _cw, _ch = content
    bx, by, _bw, _bh = [int(v) for v in bubble_rect]
    ox, oy, ow, oh = [int(v) for v in original_rect]
    pad = max(2, int(min(max(1, ow), max(1, oh)) * 0.12))
    return _clip_rect(
        [cx + ox - bx - pad, cy + oy - by - pad, ow + pad * 2, oh + pad * 2],
        image_width,
        image_height,
    )


# ──────────────────────────────────────────────
# CV 획두께 측정
# ──────────────────────────────────────────────

def _measure_style_features_for_crop(crop: np.ndarray) -> dict[str, float]:
    if crop.size == 0:
        return {"stroke_width_px": 0.0, "ink_density": 0.0, "shape_complexity": 0.0}

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    dark = (gray < 150).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(dark, 8)
    filtered = np.zeros_like(dark)
    complexities = []
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < 4 or w < 2 or h < 2:
            continue
        if area > dark.size * 0.55:
            continue
        component = (labels == label).astype(np.uint8)
        filtered[component > 0] = 1
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            contour_area = max(1.0, float(cv2.contourArea(contour)))
            perimeter = float(cv2.arcLength(contour, True))
            complexities.append((perimeter * perimeter) / contour_area)

    ys, xs = np.where(filtered > 0)
    if len(xs) == 0:
        return {"stroke_width_px": 0.0, "ink_density": 0.0, "shape_complexity": 0.0}

    x1, x2 = int(xs.min()), int(xs.max()) + 1
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    ink_area = float(filtered.sum())
    bbox_area = float(max(1, (x2 - x1) * (y2 - y1)))
    dist = cv2.distanceTransform((filtered * 255).astype(np.uint8), cv2.DIST_L2, 5)
    stroke_values = dist[filtered > 0] * 2.0
    stroke_width = float(np.percentile(stroke_values, 70)) if stroke_values.size else 0.0

    return {
        "stroke_width_px": round(stroke_width, 3),
        "ink_density": round(ink_area / bbox_area, 4),
        "shape_complexity": round(float(np.median(complexities)) if complexities else 0.0, 3),
    }


def _aggregate_line_style_features(
    line_features: list[dict[str, Any]],
    stroke_base: float,
    density_base: float,
    complexity_base: float,
    width_base: float,
) -> dict[str, Any]:
    strokes = [float(v["stroke_width_px"]) for v in line_features if v.get("stroke_width_px", 0.0) > 0]
    densities = [float(v["ink_density"]) for v in line_features if v.get("ink_density", 0.0) > 0]
    complexities = [float(v["shape_complexity"]) for v in line_features if v.get("shape_complexity", 0.0) > 0]
    widths = [float(v["line_width_px"]) for v in line_features if v.get("line_width_px", 0.0) > 0]
    chars = sum(int(v.get("char_count", 0)) for v in line_features)
    font_estimates = []
    weight_ratios = []
    for line_feature in line_features:
        line_width = float(line_feature.get("line_width_px", 0.0) or 0.0)
        line_rect = line_feature.get("line_rect") or []
        char_count = max(1, int(line_feature.get("char_count", 1) or 1))
        line_height = float(line_rect[3]) if isinstance(line_rect, list) and len(line_rect) == 4 else 0.0
        font_estimate = max(line_width, line_height / char_count)
        if font_estimate <= 0:
            continue
        stroke = float(line_feature.get("stroke_width_px", 0.0) or 0.0)
        line_feature["font_size_estimate"] = round(font_estimate, 3)
        line_feature["weight_ratio"] = round(stroke / font_estimate, 4)
        font_estimates.append(font_estimate)
        weight_ratios.append(stroke / font_estimate)

    feature = {
        "line_count": len(line_features),
        "char_count": chars,
        "line_stroke_width_px": round(_median(strokes), 3),
        "line_ink_density": round(_median(densities), 4),
        "line_shape_complexity": round(_median(complexities), 3),
        "line_width_px": round(_median(widths), 3),
        "font_size_estimate": round(_median(font_estimates), 3),
        "weight_ratio": round(_median(weight_ratios), 4),
        "line_features": line_features,
    }
    feature["line_stroke_width_rel"] = round(feature["line_stroke_width_px"] / max(0.001, stroke_base), 3)
    feature["line_ink_density_rel"] = round(feature["line_ink_density"] / max(0.001, density_base), 3)
    feature["line_shape_complexity_rel"] = round(feature["line_shape_complexity"] / max(0.001, complexity_base), 3)
    feature["line_width_rel"] = round(feature["line_width_px"] / max(0.001, width_base), 3)
    feature["short_label_candidate"] = bool(1 <= chars <= 8 and len(line_features) <= 3)
    return feature


def _measure_region_style_features(
    sheet_image_path: str,
    sheet_json_path: str,
    cv_clusters_path: str | None = None,
) -> dict[int, dict[str, Any]]:
    image = cv2.imread(sheet_image_path)
    if image is None:
        return {}
    try:
        with open(sheet_json_path, "r", encoding="utf-8") as f:
            sheet = json.load(f)
    except Exception:
        return {}

    measured: dict[int, dict[str, float]] = {}
    line_features_by_anchor: dict[int, list[dict[str, Any]]] = {}
    cv_cluster_map: dict[int, dict] = {}
    if cv_clusters_path:
        try:
            with open(cv_clusters_path, "r", encoding="utf-8") as f:
                cv_clusters = json.load(f)
            cv_cluster_map = {int(c.get("cluster_id")): c for c in cv_clusters if c.get("cluster_id") is not None}
        except Exception:
            cv_cluster_map = {}

    for region in sheet.get("regions", []):
        rid = region.get("region_id")
        if rid is None:
            continue
        rect = _style_crop_rect_in_sheet(region, image.shape[1], image.shape[0])
        if rect is None:
            continue
        x, y, w, h = rect
        features = _measure_style_features_for_crop(image[y:y + h, x:x + w])
        features["style_crop_rect"] = [int(x), int(y), int(w), int(h)]
        try:
            measured[int(rid)] = features
        except (TypeError, ValueError):
            continue

        for anchor_id in region.get("anchor_ids") or []:
            try:
                anchor_int = int(anchor_id)
            except (TypeError, ValueError):
                continue
            cluster = cv_cluster_map.get(anchor_int)
            if not cluster:
                continue
            for line in cluster.get("lines") or []:
                line_rect = line.get("bbox")
                line_sheet_rect = _map_original_rect_to_sheet(region, line_rect, image.shape[1], image.shape[0])
                if line_sheet_rect is None:
                    continue
                lx, ly, lw, lh = line_sheet_rect
                line_feature = _measure_style_features_for_crop(image[ly:ly + lh, lx:lx + lw])
                original_line_width = int(line_rect[2]) if line_rect and len(line_rect) == 4 else lw
                line_feature.update({
                    "line_text": line.get("text", ""),
                    "line_rect": [int(v) for v in line_rect[:4]] if line_rect and len(line_rect) == 4 else None,
                    "line_sheet_rect": [int(lx), int(ly), int(lw), int(lh)],
                    "line_width_px": float(original_line_width),
                    "char_count": _japanese_text_len(line.get("text", "")),
                })
                line_features_by_anchor.setdefault(anchor_int, []).append(line_feature)

    strokes = [v["stroke_width_px"] for v in measured.values() if v.get("stroke_width_px", 0.0) > 0]
    densities = [v["ink_density"] for v in measured.values() if v.get("ink_density", 0.0) > 0]
    complexities = [v["shape_complexity"] for v in measured.values() if v.get("shape_complexity", 0.0) > 0]
    line_strokes = [
        float(v["stroke_width_px"])
        for features in line_features_by_anchor.values()
        for v in features
        if v.get("stroke_width_px", 0.0) > 0
    ]
    line_densities = [
        float(v["ink_density"])
        for features in line_features_by_anchor.values()
        for v in features
        if v.get("ink_density", 0.0) > 0
    ]
    line_complexities = [
        float(v["shape_complexity"])
        for features in line_features_by_anchor.values()
        for v in features
        if v.get("shape_complexity", 0.0) > 0
    ]
    line_widths = [
        float(v["line_width_px"])
        for features in line_features_by_anchor.values()
        for v in features
        if v.get("line_width_px", 0.0) > 0
    ]
    # 굵은 강조 글자가 많으면 median이 올라가므로 normal 기준선은 하위 분위수 사용
    stroke_base = float(np.percentile(strokes, 35)) if strokes else 1.0
    density_base = float(np.median(densities)) if densities else 1.0
    complexity_base = float(np.median(complexities)) if complexities else 1.0
    line_stroke_base = float(np.percentile(line_strokes, 35)) if line_strokes else stroke_base
    line_density_base = float(np.median(line_densities)) if line_densities else density_base
    line_complexity_base = float(np.median(line_complexities)) if line_complexities else complexity_base
    line_width_base = float(np.median(line_widths)) if line_widths else 1.0

    for features in measured.values():
        features["stroke_width_rel"] = round(features.get("stroke_width_px", 0.0) / max(0.001, stroke_base), 3)
        features["ink_density_rel"] = round(features.get("ink_density", 0.0) / max(0.001, density_base), 3)
        features["shape_complexity_rel"] = round(features.get("shape_complexity", 0.0) / max(0.001, complexity_base), 3)

    for region in sheet.get("regions", []):
        rid = region.get("region_id")
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            continue
        anchor_features: dict[int, dict[str, Any]] = {}
        region_line_features = []
        for anchor_id in region.get("anchor_ids") or []:
            try:
                anchor_int = int(anchor_id)
            except (TypeError, ValueError):
                continue
            line_features = line_features_by_anchor.get(anchor_int, [])
            if not line_features:
                continue
            anchor_feature = _aggregate_line_style_features(
                line_features,
                line_stroke_base,
                line_density_base,
                line_complexity_base,
                line_width_base,
            )
            anchor_features[anchor_int] = anchor_feature
            region_line_features.extend(line_features)
        if rid_int in measured and anchor_features:
            measured[rid_int]["anchor_style_features"] = anchor_features
            measured[rid_int]["line_style_features"] = _aggregate_line_style_features(
                region_line_features,
                line_stroke_base,
                line_density_base,
                line_complexity_base,
                line_width_base,
            )
    return measured


def _style_features_for_anchor_ids(
    region_features: dict[str, Any],
    anchor_ids: list[int],
) -> dict[str, Any]:
    anchor_features = region_features.get("anchor_style_features") or {}
    selected = []
    for anchor_id in anchor_ids:
        try:
            feature = anchor_features.get(int(anchor_id))
        except (TypeError, ValueError):
            feature = None
        if feature:
            selected.append(feature)
    if not selected:
        return region_features

    line_features = [
        line_feature
        for feature in selected
        for line_feature in feature.get("line_features", [])
    ]
    merged = dict(region_features)
    if len(selected) == 1:
        merged["line_style_features"] = selected[0]
        return merged

    merged["line_style_features"] = {
        "line_count": sum(int(v.get("line_count", 0)) for v in selected),
        "char_count": sum(int(v.get("char_count", 0)) for v in selected),
        "line_stroke_width_rel": round(_median([float(v.get("line_stroke_width_rel", 0)) for v in selected if v.get("line_stroke_width_rel")], 1.0), 3),
        "line_ink_density_rel": round(_median([float(v.get("line_ink_density_rel", 0)) for v in selected if v.get("line_ink_density_rel")], 1.0), 3),
        "line_shape_complexity_rel": round(_median([float(v.get("line_shape_complexity_rel", 0)) for v in selected if v.get("line_shape_complexity_rel")], 1.0), 3),
        "line_width_rel": round(_median([float(v.get("line_width_rel", 0)) for v in selected if v.get("line_width_rel")], 1.0), 3),
        "font_size_estimate": round(_median([float(v.get("font_size_estimate", 0)) for v in selected if v.get("font_size_estimate")], 0.0), 3),
        "weight_ratio": round(_median([float(v.get("weight_ratio", 0)) for v in selected if v.get("weight_ratio")], 0.0), 4),
        "short_label_candidate": all(bool(v.get("short_label_candidate")) for v in selected),
        "line_features": line_features,
    }
    return merged


# ──────────────────────────────────────────────
# render_hint 최종 판정
# ──────────────────────────────────────────────

def _final_render_hint(raw_hint: Any, features: dict[str, Any] | None) -> str:
    raw = _normalized_render_hint(raw_hint)
    if not features:
        return raw

    stroke_rel = float(features.get("stroke_width_rel", 1.0) or 1.0)
    density_rel = float(features.get("ink_density_rel", 1.0) or 1.0)
    complexity_rel = float(features.get("shape_complexity_rel", 1.0) or 1.0)
    line_features = features.get("line_style_features") or features
    line_stroke_rel = float(line_features.get("line_stroke_width_rel", stroke_rel) or stroke_rel)
    line_density_rel = float(line_features.get("line_ink_density_rel", density_rel) or density_rel)
    line_complexity_rel = float(line_features.get("line_shape_complexity_rel", complexity_rel) or complexity_rel)
    line_width_rel = float(line_features.get("line_width_rel", 1.0) or 1.0)
    line_stroke_width_px = float(line_features.get("line_stroke_width_px", 0.0) or 0.0)
    line_density = float(line_features.get("line_ink_density", 0.0) or 0.0)
    weight_ratio = float(line_features.get("weight_ratio", 0.0) or 0.0)
    font_size_estimate = float(line_features.get("font_size_estimate", 0.0) or 0.0)
    char_count = int(line_features.get("char_count", 999) or 999)
    line_count = int(line_features.get("line_count", 1) or 1)
    short_label = bool(line_features.get("short_label_candidate")) or (char_count <= 8 and line_count <= 3)

    if short_label and font_size_estimate >= 80 and line_count <= 3 and char_count <= 10:
        return "serif_emphasis"

    serif_like = (
        raw == "serif_emphasis"
        and line_count == 1
        and char_count <= 6
        and line_width_rel >= 1.5
        and line_density_rel <= 1.2
    ) or (
        line_width_rel >= 1.65
        and line_complexity_rel >= 1.35
        and line_density_rel <= 1.2
        and char_count <= 6
    )
    if serif_like:
        return "serif_emphasis"

    memory_serif_like = (
        line_stroke_width_px <= 2.2
        and 24.0 <= font_size_estimate <= 58.0
        and weight_ratio <= 0.08
        and line_density <= 0.235
        and line_width_rel >= 0.8
        and line_density_rel <= 1.18
        and line_count >= 2
        and char_count >= 8
    )
    if raw == "memory_serif" or memory_serif_like:
        return "memory_serif"

    if (
        raw == "serif_emphasis"
        and line_stroke_rel >= 2.0
        and line_width_rel >= 2.0
        and line_complexity_rel >= 1.8
        and char_count <= 4
    ):
        return "impact"

    if raw == "impact" and weight_ratio >= 0.155:
        return "impact"
    if weight_ratio >= 0.18 and line_density_rel >= 1.35:
        return "impact"

    if raw in {"bold", "impact"} and weight_ratio >= 0.115:
        return "bold"
    if weight_ratio >= 0.125:
        return "bold"

    if raw == "semibold":
        return "semibold"

    if weight_ratio >= 0.105:
        return "semibold"

    return "normal"


def _render_hint_score(features: dict[str, Any]) -> dict[str, Any]:
    """디버그용: 판정에 사용된 핵심 수치를 요약한다."""
    line_f = features.get("line_style_features") or features
    return {
        "weight_ratio":         round(float(line_f.get("weight_ratio", 0.0) or 0.0), 4),
        "line_width_rel":       round(float(line_f.get("line_width_rel", 1.0) or 1.0), 3),
        "line_stroke_rel":      round(float(line_f.get("line_stroke_width_rel", 1.0) or 1.0), 3),
        "line_density_rel":     round(float(line_f.get("line_ink_density_rel", 1.0) or 1.0), 3),
        "line_complexity_rel":  round(float(line_f.get("line_shape_complexity_rel", 1.0) or 1.0), 3),
        "char_count":           int(line_f.get("char_count", 0) or 0),
        "line_count":           int(line_f.get("line_count", 1) or 1),
        "font_size_estimate":   round(float(line_f.get("font_size_estimate", 0.0) or 0.0), 1),
        "line_stroke_width_px": round(float(line_f.get("line_stroke_width_px", 0.0) or 0.0), 3),
        "line_ink_density":     round(float(line_f.get("line_ink_density", 0.0) or 0.0), 4),
    }


def _should_preserve_original_by_style(
    text: Any,
    features: dict[str, Any],
) -> tuple[bool, str | None]:
    """큰데 얇고 비어 보이는 단독 효과음은 번역 렌더 대신 원본 보존."""
    line_f = features.get("line_style_features") or features
    source_text = str(text or "").strip()
    compact = "".join(ch for ch in source_text if not ch.isspace())
    compact_len = len(compact)

    char_count = int(line_f.get("char_count", 0) or 0)
    line_count = int(line_f.get("line_count", 1) or 1)
    line_width_rel = float(line_f.get("line_width_rel", 1.0) or 1.0)
    weight_ratio = float(line_f.get("weight_ratio", 0.0) or 0.0)
    line_density_rel = float(line_f.get("line_ink_density_rel", 1.0) or 1.0)

    kana_like = bool(compact) and all(
        ("ぁ" <= ch <= "ゟ")
        or ("ァ" <= ch <= "ヿ")
        or ch in "ー〜～…!?！？ッっ"
        for ch in compact
    )
    has_small_tsu = any(ch in compact for ch in "ッっ")

    if (
        line_count == 1
        and 1 <= char_count <= 3
        and 1 <= compact_len <= 3
        and line_width_rel >= 3.0
        and weight_ratio <= 0.055
        and line_density_rel <= 0.65
    ):
        return True, "oversized_thin_sparse_single_efx"

    if (
        line_count == 1
        and 1 <= char_count <= 4
        and kana_like
        and has_small_tsu
        and line_width_rel >= 2.6
        and weight_ratio <= 0.065
        and line_density_rel <= 0.75
    ):
        return True, "small_tsu_oversized_thin_sparse_efx"

    return False, None


def _compact_style_features(features: dict[str, Any]) -> dict[str, Any]:
    """integrated.json 저장용: line_features 상세 배열을 제거한 경량 버전."""
    skip = {"line_features", "anchor_style_features"}
    out: dict[str, Any] = {}
    for k, v in features.items():
        if k in skip:
            continue
        if k == "line_style_features" and isinstance(v, dict):
            out[k] = {ik: iv for ik, iv in v.items() if ik != "line_features"}
        else:
            out[k] = v
    return out


# ──────────────────────────────────────────────
# integrated.json 패치
# ──────────────────────────────────────────────

def update_integrated_render_hints(
    integrated_path: str,
    sheet_image_path: str,
    regions_path: str,
    cv_clusters_path: str | None = None,
) -> int:
    """integrated.json의 render_hint_final / style_features / render_hint_score를 CV 재계산으로 갱신.

    스타일·사각형은 matched_cluster(anchor TG id) → 현재 regions.json anchor_ids 역탐색으로 현재 영역을 찾아 계산한다.
    source_region_id(번역 번호)와 source_region_ref(번역 시점 영역 참조)는 다시 쓰지 않는다. 번호를 현재 regions 번호로
    덮어쓰면 번역 번호 공간과 현재 번호 공간이 섞여 수동값이 다른 영역에 붙는다. 번호로는 영역을 찾지도 않는다.

    Returns:
        변경된 cluster 수
    """
    # 1. regions.json 로드 → anchor_id → region 역매핑 구성
    regions_path_obj = Path(regions_path)
    try:
        with open(regions_path_obj, "r", encoding="utf-8") as f:
            regions_data = json.load(f)
        anchor_to_region_id: dict[int, int] = {}
        region_by_id: dict[int, dict] = {}
        for r in regions_data.get("regions", []):
            rid = r.get("region_id")
            if rid is None:
                continue
            region_by_id[int(rid)] = r
            for a in (r.get("anchor_ids") or []):
                try:
                    anchor_to_region_id[int(a)] = int(rid)
                except (TypeError, ValueError):
                    pass
    except Exception:
        anchor_to_region_id = {}
        region_by_id = {}

    # 2. style feature 계산
    style_features_by_region = _measure_region_style_features(
        sheet_image_path, regions_path, cv_clusters_path
    )
    if not style_features_by_region:
        return 0

    # 3. integrated.json 로드
    integrated_path = Path(integrated_path)
    with open(integrated_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    clusters = data.get("clusters") or []
    changed = 0

    for cluster in clusters:
        anchor_ids = [int(a) for a in (cluster.get("matched_cluster") or []) if a is not None]

        # anchor → 현재 regions.json의 region_id 역탐색
        rids_found: set[int] = set()
        if anchor_ids and anchor_to_region_id:
            rids_found = {anchor_to_region_id[a] for a in anchor_ids if a in anchor_to_region_id}

        # 단일 region으로 1:1 매핑된 경우의 현재 영역 번호(스타일 계산용). source_region_id는 건드리지 않는다.
        remapped_rid: int | None = next(iter(rids_found)) if len(rids_found) == 1 else None

        # rect 갱신 — 복수 region일 때는 union rect 계산
        # (TG7+8+9 같이 하나의 OCR 클러스터가 여러 per-TG region에 걸친 경우)
        sub_rects = []
        for rid in rids_found:
            r = region_by_id.get(int(rid))
            if r:
                rect = r.get("unboxed_rect") or r.get("bubble_rect") or r.get("source_rect")
                if rect and len(rect) == 4:
                    sub_rects.append(rect)
        canonical_rect = _union_rects(sub_rects)
        if canonical_rect:
            for field in ("bounding_rect", "unboxed_rect", "source_region_rect", "union_bbox"):
                if cluster.get(field) != canonical_rect:
                    cluster[field] = list(canonical_rect)
                    changed += 1

        # style features 계산용 대표 region_id
        rid_int = remapped_rid if remapped_rid is not None else (min(rids_found) if rids_found else None)
        if rid_int is None:
            continue

        region_features = style_features_by_region.get(rid_int)
        if not region_features:
            continue

        features = _style_features_for_anchor_ids(region_features, anchor_ids)

        raw_hint = cluster.get("render_hint_raw") or cluster.get("render_hint", "normal")
        new_final = _final_render_hint(raw_hint, features)

        old_final = cluster.get("render_hint_final") or cluster.get("render_hint")
        if new_final != old_final:
            changed += 1

        cluster["render_hint_raw"]   = _normalized_render_hint(raw_hint)
        cluster["render_hint_final"] = new_final
        cluster["render_hint"]       = new_final   # render_text_overlay fallback 대응
        cluster["style_features"]    = _compact_style_features(features)
        cluster["render_hint_score"] = _render_hint_score(features)
        preserve_original, preserve_reason = _should_preserve_original_by_style(
            cluster.get("text"),
            features,
        )
        if preserve_original:
            cluster["preserve_original"] = True
            cluster["preserve_original_reason"] = preserve_reason
            changed += 1
        elif cluster.get("preserve_original_reason") in {
            "oversized_thin_sparse_single_efx",
            "small_tsu_oversized_thin_sparse_efx",
        }:
            cluster.pop("preserve_original", None)
            cluster.pop("preserve_original_reason", None)
            changed += 1

    with open(integrated_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return changed


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CV 기반 render_hint 재계산 후 integrated.json 갱신")
    parser.add_argument("integrated",    help="integrated.json 경로")
    parser.add_argument("sheet_image",   help="indexed_sheet.jpg 경로")
    parser.add_argument("regions_json",  help="regions.json 경로")
    parser.add_argument("--cv_clusters", default=None, help="text_groups.json 경로 (선택)")
    args = parser.parse_args()

    n = update_integrated_render_hints(
        args.integrated,
        args.sheet_image,
        args.regions_json,
        args.cv_clusters,
    )
    print(f"✅ render_hint 갱신 완료 — {n}개 변경")
