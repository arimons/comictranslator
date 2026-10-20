# anchor ROI 안에서 열린 말풍선 contour close 후보를 시각화하는 디버그 스크립트
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pipeline.bubble_detector import _bbox_center, _load_json, _tight_text_group_rect


Color = tuple[int, int, int]


def _as_rect(rect: list[int] | tuple[int, ...] | None) -> list[int]:
    if not rect or len(rect) < 4:
        return [0, 0, 0, 0]
    x, y, w, h = [int(v) for v in rect[:4]]
    return [x, y, max(0, w), max(0, h)]


def _pad_rect(rect: list[int], pad: int, img_w: int, img_h: int) -> list[int]:
    x, y, w, h = _as_rect(rect)
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img_w, x + w + pad)
    y2 = min(img_h, y + h + pad)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _rect_intersection_area(a: list[int], b: list[int]) -> int:
    ax, ay, aw, ah = _as_rect(a)
    bx, by, bw, bh = _as_rect(b)
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    return max(0, x2 - x1) * max(0, y2 - y1)


def _touches_roi_border(mask: np.ndarray, tolerance: int = 2) -> bool:
    if mask.size == 0:
        return False
    return bool(
        np.any(mask[:tolerance, :] > 0)
        or np.any(mask[-tolerance:, :] > 0)
        or np.any(mask[:, :tolerance] > 0)
        or np.any(mask[:, -tolerance:] > 0)
    )


def _fill_contour_mask(shape: tuple[int, int], contour: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    return mask


def _contour_features(contour: np.ndarray, rect: list[int], anchor_rect: list[int], mask: np.ndarray, gray: np.ndarray) -> dict[str, float]:
    area = float(cv2.contourArea(contour))
    x, y, w, h = cv2.boundingRect(contour)
    bbox = [x, y, w, h]
    rect_area = max(1.0, float(w * h))
    anchor_area = max(1.0, float(anchor_rect[2] * anchor_rect[3]))
    hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
    peri = float(cv2.arcLength(contour, True))
    center = _bbox_center(rect)
    center_inside = cv2.pointPolygonTest(contour, center, False) >= 0
    anchor_overlap = _rect_intersection_area(bbox, anchor_rect) / anchor_area
    roi_pixels = gray[mask > 0]
    mean_gray = float(np.mean(roi_pixels)) if len(roi_pixels) else 0.0
    dark_ratio = float(np.mean(roi_pixels < 120)) if len(roi_pixels) else 1.0
    return {
        "area": area,
        "bbox_area": rect_area,
        "anchor_ratio": rect_area / anchor_area,
        "anchor_overlap": anchor_overlap,
        "solidity": area / hull_area if hull_area > 0 else 0.0,
        "roundness": 4.0 * math.pi * area / (peri * peri) if peri > 0 else 0.0,
        "extent": area / rect_area,
        "center_inside": 1.0 if center_inside else 0.0,
        "touches_roi": 1.0 if _touches_roi_border(mask) else 0.0,
        "mean_gray": mean_gray,
        "dark_ratio": dark_ratio,
    }


def _score_candidate(features: dict[str, float]) -> float:
    ratio = features["anchor_ratio"]
    ratio_score = 1.0 if 1.15 <= ratio <= 10.0 else max(0.0, min(ratio / 1.15, 1.0 - (ratio - 10.0) / 18.0))
    score = (
        features["center_inside"] * 2.0
        + min(features["anchor_overlap"], 1.0) * 2.0
        + ratio_score * 1.2
        + min(features["solidity"] / 0.72, 1.0) * 0.8
        + min(features["extent"] / 0.45, 1.0) * 0.7
        + max(0.0, min((features["mean_gray"] - 130.0) / 80.0, 1.0)) * 0.5
        - features["touches_roi"] * 1.1
        - max(0.0, features["dark_ratio"] - 0.20) * 2.5
    )
    return float(score)


def _best_contour_mask(binary: np.ndarray, anchor_rect: list[int], tg_rect: list[int], gray: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_mask = np.zeros_like(binary)
    best_meta: dict[str, float] = {"score": -999.0}
    min_area = max(300.0, float(anchor_rect[2] * anchor_rect[3]) * 0.9)
    for contour in contours:
        if cv2.contourArea(contour) < min_area:
            continue
        mask = _fill_contour_mask(binary.shape, contour)
        features = _contour_features(contour, tg_rect, anchor_rect, mask, gray)
        score = _score_candidate(features)
        if score > best_meta["score"]:
            best_mask = mask
            best_meta = {**features, "score": score}
    return best_mask, best_meta


def _local_to_full(local_mask: np.ndarray, roi_rect: list[int], full_shape: tuple[int, int]) -> np.ndarray:
    full = np.zeros(full_shape, dtype=np.uint8)
    x, y, w, h = roi_rect
    full[y:y + h, x:x + w] = local_mask
    return full


def _overlay_mask(image: np.ndarray, mask: np.ndarray, color: Color, rect: list[int] | None = None) -> np.ndarray:
    vis = image.copy()
    overlay = vis.copy()
    overlay[mask > 0] = color
    vis = cv2.addWeighted(overlay, 0.44, vis, 0.56, 0)
    contours, _ = cv2.findContours(np.where(mask > 0, 255, 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(vis, contours, -1, color, 2, cv2.LINE_AA)
    if rect is not None:
        x, y, w, h = rect
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 220, 220), 2)
    return vis


def _crop_around(image: np.ndarray, rect: list[int], pad: int = 150) -> tuple[np.ndarray, list[int]]:
    img_h, img_w = image.shape[:2]
    crop_rect = _pad_rect(rect, pad, img_w, img_h)
    x, y, w, h = crop_rect
    return image[y:y + h, x:x + w].copy(), crop_rect


def _crop_view(image: np.ndarray, rect: list[int], mask: np.ndarray | None = None, color: Color = (80, 220, 90)) -> np.ndarray:
    view = _overlay_mask(image, mask, color, rect) if mask is not None else image.copy()
    crop, crop_rect = _crop_around(view, rect)
    x, y, w, h = rect
    cv2.rectangle(crop, (x - crop_rect[0], y - crop_rect[1]), (x + w - crop_rect[0], y + h - crop_rect[1]), (0, 220, 220), 2)
    return crop


def _put_title(img: np.ndarray, title: str) -> np.ndarray:
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(out, title, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def _resize_cell(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def _make_board(cells: list[tuple[str, np.ndarray]], cols: int = 3, cell_w: int = 360, cell_h: int = 512) -> np.ndarray:
    rendered = [_put_title(_resize_cell(img, (cell_w, cell_h)), title) for title, img in cells]
    blank = np.full_like(rendered[0], 245)
    rows = []
    for i in range(0, len(rendered), cols):
        row = rendered[i:i + cols]
        row.extend([blank.copy() for _ in range(cols - len(row))])
        rows.append(np.hstack(row))
    return np.vstack(rows)


def _binary_sources(roi_gray: np.ndarray, local_tg_rect: list[int]) -> dict[str, np.ndarray]:
    blur = cv2.GaussianBlur(roi_gray, (3, 3), 0)
    edges = cv2.Canny(blur, 45, 145)
    adaptive = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        8,
    )
    dark = np.where(roi_gray < 150, 255, 0).astype(np.uint8)
    gradient = cv2.morphologyEx(blur, cv2.MORPH_GRADIENT, np.ones((5, 5), np.uint8))
    gradient = np.where(gradient > 24, 255, 0).astype(np.uint8)

    # 글자 획은 anchor 내부 contour를 오염시키므로 기본 boundary source에서 제거한다.
    x, y, w, h = local_tg_rect
    erase_pad = 4
    x1, y1 = max(0, x - erase_pad), max(0, y - erase_pad)
    x2, y2 = min(roi_gray.shape[1], x + w + erase_pad), min(roi_gray.shape[0], y + h + erase_pad)
    for mask in (edges, adaptive, dark, gradient):
        mask[y1:y2, x1:x2] = 0

    return {
        "edge": edges,
        "adaptive": adaptive,
        "dark": dark,
        "gradient": gradient,
    }


def _closing_variants(binary: np.ndarray) -> dict[str, np.ndarray]:
    variants: dict[str, np.ndarray] = {}
    kernels = {
        "h_close_17x5": cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5)),
        "h_close_35x7": cv2.getStructuringElement(cv2.MORPH_RECT, (35, 7)),
        "v_close_5x17": cv2.getStructuringElement(cv2.MORPH_RECT, (5, 17)),
        "v_close_7x35": cv2.getStructuringElement(cv2.MORPH_RECT, (7, 35)),
        "ellipse_15": cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)),
        "ellipse_29": cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (29, 29)),
    }
    for name, kernel in kernels.items():
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
        closed = cv2.dilate(closed, np.ones((3, 3), np.uint8), iterations=1)
        variants[name] = closed
    return variants


def _candidate_masks(image: np.ndarray, rect: list[int]) -> tuple[dict[str, np.ndarray], dict[str, dict[str, float]]]:
    img_h, img_w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    roi_rect = _pad_rect(rect, max(110, int(max(rect[2], rect[3]) * 0.65)), img_w, img_h)
    rx, ry, rw, rh = roi_rect
    roi_gray = gray[ry:ry + rh, rx:rx + rw]
    local_rect = [rect[0] - rx, rect[1] - ry, rect[2], rect[3]]
    masks: dict[str, np.ndarray] = {}
    metas: dict[str, dict[str, float]] = {}
    sources = _binary_sources(roi_gray, local_rect)

    for source_name, source in sources.items():
        for close_name, closed in _closing_variants(source).items():
            local_mask, meta = _best_contour_mask(closed, local_rect, local_rect, roi_gray)
            key = f"{source_name}_{close_name}"
            masks[key] = _local_to_full(local_mask, roi_rect, (img_h, img_w))
            metas[key] = meta

    # 점수 높은 상위 후보만 caller가 쓰기 쉽게 유지한다.
    ranked = sorted(metas, key=lambda key: metas[key].get("score", -999.0), reverse=True)
    return {key: masks[key] for key in ranked}, {key: metas[key] for key in ranked}


def _draw_overview(image: np.ndarray, groups: list[dict[str, Any]], regions: dict[str, Any] | None) -> np.ndarray:
    vis = image.copy()
    for idx, group in enumerate(groups, start=1):
        rect = _tight_text_group_rect(group)
        x, y, w, h = _as_rect(rect)
        gid = int(group.get("text_group_id", group.get("cluster_id", idx)))
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 220, 220), 2)
        cv2.putText(vis, f"TG{gid}", (x, max(18, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 180, 220), 2)
    if regions:
        for region in regions.get("regions", []):
            rect = _as_rect(region.get("bubble_rect"))
            if rect[2] <= 0 or rect[3] <= 0:
                continue
            x, y, w, h = rect
            color = (80, 220, 90) if region.get("is_valid") else (0, 0, 220)
            cv2.rectangle(vis, (x, y), (x + w, y + h), color, 2)
            cv2.putText(vis, f"R{region.get('region_id')}", (x, max(18, y - 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    return vis


def run(image_path: Path, groups_path: Path, regions_path: Path | None, out_dir: Path, top_k: int = 8) -> list[Path]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    groups = _load_json(str(groups_path))
    regions = json.load(regions_path.open("r", encoding="utf-8")) if regions_path and regions_path.exists() else None

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    overview_path = out_dir / f"{image_path.stem}_00_overview.jpg"
    cv2.imwrite(str(overview_path), _draw_overview(image, groups, regions))
    written.append(overview_path)

    meta_summary: dict[str, Any] = {}
    colors: list[Color] = [
        (60, 210, 255),
        (80, 230, 110),
        (255, 120, 180),
        (180, 120, 255),
        (255, 190, 70),
        (0, 150, 255),
        (120, 220, 220),
        (220, 160, 80),
    ]

    for idx, group in enumerate(groups, start=1):
        gid = int(group.get("text_group_id", group.get("cluster_id", idx)))
        rect = _tight_text_group_rect(group)
        if rect[2] <= 0 or rect[3] <= 0:
            continue
        masks, metas = _candidate_masks(image, rect)
        cells: list[tuple[str, np.ndarray]] = [("original", _crop_view(image, rect))]
        top_keys = list(masks)[:top_k]
        meta_summary[f"TG{gid}"] = {key: metas[key] for key in top_keys}
        for i, key in enumerate(top_keys):
            score = metas[key].get("score", -999.0)
            title = f"{i + 1:02d}_{key} s={score:.2f}"
            cells.append((title, _crop_view(image, rect, masks[key], colors[i % len(colors)])))
        board = _make_board(cells)
        board_path = out_dir / f"{image_path.stem}_TG{gid:02d}_contour_close_board.jpg"
        cv2.imwrite(str(board_path), board)
        written.append(board_path)

    meta_path = out_dir / f"{image_path.stem}_contour_close_scores.json"
    meta_path.write_text(json.dumps(meta_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(meta_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="anchor ROI contour close rescue 후보 시각화")
    parser.add_argument("--image", default=str(DATA_HOME / 'samples/test_image1/v01/00024.jpg'))
    parser.add_argument("--groups", default="temp_output/Ore1/json/00024_text_groups.json")
    parser.add_argument("--regions", default="temp_output/Ore1/json/00024_regions.json")
    parser.add_argument("--out-dir", default=str(PROOF_DIR / 'misc/contour_rescue_00024'))
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    written = run(
        Path(args.image),
        Path(args.groups),
        Path(args.regions) if args.regions else None,
        Path(args.out_dir),
        top_k=args.top_k,
    )
    print(json.dumps([str(path) for path in written], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
