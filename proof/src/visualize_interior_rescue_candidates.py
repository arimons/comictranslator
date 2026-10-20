# 열린 말풍선 내부 추정 후보를 여러 OpenCV 방식으로 시각화하는 디버그 스크립트
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pipeline.bubble_detector import (
    _bbox_center,
    _extract_mask_polygon,
    _flood_with_fallback,
    _load_json,
    _tight_text_group_rect,
)


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


def _rect_from_mask(mask: np.ndarray) -> list[int] | None:
    points = cv2.findNonZero(np.where(mask > 0, 255, 0).astype(np.uint8))
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    return [int(x), int(y), int(w), int(h)]


def _component_for_seed(binary: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    binary = np.where(binary > 0, 255, 0).astype(np.uint8)
    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary)
    if n_labels <= 1:
        return np.zeros_like(binary)

    sx, sy = seed
    label = labels[sy, sx] if 0 <= sy < labels.shape[0] and 0 <= sx < labels.shape[1] else 0
    if label == 0:
        best_label = 1
        best_score = float("inf")
        for cand in range(1, n_labels):
            area = float(stats[cand, cv2.CC_STAT_AREA])
            cx, cy = centroids[cand]
            dist = (cx - sx) ** 2 + (cy - sy) ** 2
            score = dist / max(1.0, area ** 0.5)
            if score < best_score:
                best_score = score
                best_label = cand
        label = best_label

    return np.where(labels == label, 255, 0).astype(np.uint8)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    h, w = binary.shape[:2]
    padded = np.zeros((h + 2, w + 2), dtype=np.uint8)
    padded[1:h + 1, 1:w + 1] = binary
    flood = padded.copy()
    ff_mask = np.zeros((h + 4, w + 4), dtype=np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    outside = flood[1:h + 1, 1:w + 1]
    holes = cv2.bitwise_not(outside)
    return cv2.bitwise_or(binary, holes)


def _seed_near_text(gray: np.ndarray, rect: list[int]) -> tuple[int, int]:
    x, y, w, h = _as_rect(rect)
    img_h, img_w = gray.shape[:2]
    candidates = [
        (x + w // 2, max(0, y - 3)),
        (x + w // 2, min(img_h - 1, y + h + 3)),
        (max(0, x - 3), y + h // 2),
        (min(img_w - 1, x + w + 3), y + h // 2),
        _bbox_center(rect),
    ]
    return max(candidates, key=lambda p: int(gray[p[1], p[0]]) if 0 <= p[1] < img_h and 0 <= p[0] < img_w else -1)


def _local_to_full(local_mask: np.ndarray, roi_rect: list[int], full_shape: tuple[int, int]) -> np.ndarray:
    full = np.zeros(full_shape, dtype=np.uint8)
    x, y, w, h = roi_rect
    full[y:y + h, x:x + w] = local_mask
    return full


def _candidate_masks(image: np.ndarray, rect: list[int]) -> dict[str, np.ndarray]:
    img_h, img_w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    roi_rect = _pad_rect(rect, max(90, int(max(rect[2], rect[3]) * 0.55)), img_w, img_h)
    rx, ry, rw, rh = roi_rect
    roi = image[ry:ry + rh, rx:rx + rw]
    roi_gray = gray[ry:ry + rh, rx:rx + rw]
    local_rect = [rect[0] - rx, rect[1] - ry, rect[2], rect[3]]
    seed = _seed_near_text(roi_gray, local_rect)

    masks: dict[str, np.ndarray] = {}

    _seed, flood_mask, _area, _flood_rect = _flood_with_fallback(image, gray, rect)
    masks["01_seed_flood"] = flood_mask

    bright = np.where(roi_gray >= 168, 255, 0).astype(np.uint8)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((17, 17), np.uint8), iterations=1)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    masks["02_bright_close"] = _local_to_full(_fill_holes(_component_for_seed(bright, seed)), roi_rect, (img_h, img_w))

    edges = cv2.Canny(cv2.GaussianBlur(roi_gray, (3, 3), 0), 50, 150)
    edge_block = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)
    edge_suppressed = np.where((roi_gray >= 150) & (edge_block == 0), 255, 0).astype(np.uint8)
    edge_suppressed = cv2.morphologyEx(edge_suppressed, cv2.MORPH_CLOSE, np.ones((23, 23), np.uint8), iterations=1)
    masks["03_edge_suppressed"] = _local_to_full(_fill_holes(_component_for_seed(edge_suppressed, seed)), roi_rect, (img_h, img_w))

    dark = np.where(roi_gray < 120, 255, 0).astype(np.uint8)
    dark_density = cv2.boxFilter((dark > 0).astype(np.float32), -1, (31, 31), normalize=True)
    edge_density = cv2.boxFilter((edges > 0).astype(np.float32), -1, (31, 31), normalize=True)
    quiet = np.where((roi_gray >= 138) & (dark_density <= 0.20) & (edge_density <= 0.13), 255, 0).astype(np.uint8)
    quiet = cv2.morphologyEx(quiet, cv2.MORPH_CLOSE, np.ones((29, 29), np.uint8), iterations=1)
    quiet = cv2.morphologyEx(quiet, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=1)
    masks["04_quiet_texture"] = _local_to_full(_fill_holes(_component_for_seed(quiet, seed)), roi_rect, (img_h, img_w))

    forbidden = np.where((dark_density > 0.24) | (edge_density > 0.17), 255, 0).astype(np.uint8)
    allowed = np.where((roi_gray >= 135) & (forbidden == 0), 255, 0).astype(np.uint8)
    dist = cv2.distanceTransform(allowed, cv2.DIST_L2, 3)
    dist_mask = np.where(dist >= 3.0, 255, 0).astype(np.uint8)
    dist_mask = cv2.morphologyEx(dist_mask, cv2.MORPH_CLOSE, np.ones((19, 19), np.uint8), iterations=1)
    masks["05_distance_allowed"] = _local_to_full(_fill_holes(_component_for_seed(dist_mask, seed)), roi_rect, (img_h, img_w))

    rect_source = masks["04_quiet_texture"]
    rect_candidate = np.zeros((img_h, img_w), dtype=np.uint8)
    candidate_rect = _rect_from_mask(rect_source)
    if candidate_rect is not None:
        x, y, w, h = _pad_rect(candidate_rect, 4, img_w, img_h)
        cv2.rectangle(rect_candidate, (x, y), (x + w, y + h), 255, -1)
    masks["06_rect_from_quiet"] = rect_candidate
    return masks


def _overlay_mask(image: np.ndarray, mask: np.ndarray, color: Color) -> np.ndarray:
    vis = image.copy()
    overlay = vis.copy()
    overlay[mask > 0] = color
    vis = cv2.addWeighted(overlay, 0.42, vis, 0.58, 0)
    contours, _ = cv2.findContours(np.where(mask > 0, 255, 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(vis, contours, -1, color, 2, cv2.LINE_AA)
    return vis


def _put_title(img: np.ndarray, title: str) -> np.ndarray:
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 32), (0, 0, 0), -1)
    cv2.putText(out, title, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def _resize_cell(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def _make_board(cells: list[tuple[str, np.ndarray]], cols: int = 3, cell_w: int = 360, cell_h: int = 512) -> np.ndarray:
    rendered = [_put_title(_resize_cell(img, (cell_w, cell_h)), title) for title, img in cells]
    rows = []
    for i in range(0, len(rendered), cols):
        row = rendered[i:i + cols]
        if len(row) < cols:
            row.extend([np.full_like(rendered[0], 245) for _ in range(cols - len(row))])
        rows.append(np.hstack(row))
    return np.vstack(rows)


def _draw_rects(image: np.ndarray, groups: list[dict[str, Any]], regions: dict[str, Any] | None) -> np.ndarray:
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


def _crop_around(image: np.ndarray, rect: list[int], pad: int = 150) -> tuple[np.ndarray, list[int]]:
    img_h, img_w = image.shape[:2]
    crop_rect = _pad_rect(rect, pad, img_w, img_h)
    x, y, w, h = crop_rect
    return image[y:y + h, x:x + w].copy(), crop_rect


def _crop_mask_view(image: np.ndarray, mask: np.ndarray, rect: list[int], color: Color) -> np.ndarray:
    overlay = _overlay_mask(image, mask, color)
    crop, crop_rect = _crop_around(overlay, rect)
    x, y, w, h = rect
    cv2.rectangle(crop, (x - crop_rect[0], y - crop_rect[1]), (x + w - crop_rect[0], y + h - crop_rect[1]), (0, 220, 220), 2)
    return crop


def run(image_path: Path, groups_path: Path, regions_path: Path | None, out_dir: Path) -> list[Path]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    groups = _load_json(str(groups_path))
    regions = json.load(regions_path.open("r", encoding="utf-8")) if regions_path and regions_path.exists() else None

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    overview = _draw_rects(image, groups, regions)
    overview_path = out_dir / f"{image_path.stem}_00_overview.jpg"
    cv2.imwrite(str(overview_path), overview)
    written.append(overview_path)

    colors: dict[str, Color] = {
        "01_seed_flood": (255, 180, 0),
        "02_bright_close": (80, 230, 110),
        "03_edge_suppressed": (70, 210, 255),
        "04_quiet_texture": (255, 80, 180),
        "05_distance_allowed": (180, 120, 255),
        "06_rect_from_quiet": (0, 120, 255),
    }

    for idx, group in enumerate(groups, start=1):
        gid = int(group.get("text_group_id", group.get("cluster_id", idx)))
        rect = _tight_text_group_rect(group)
        if rect[2] <= 0 or rect[3] <= 0:
            continue
        masks = _candidate_masks(image, rect)
        original_crop, _ = _crop_around(image, rect)
        cells: list[tuple[str, np.ndarray]] = [("original", original_crop)]
        for name, mask in masks.items():
            cells.append((name, _crop_mask_view(image, mask, rect, colors[name])))
        board = _make_board(cells)
        board_path = out_dir / f"{image_path.stem}_TG{gid:02d}_candidate_board.jpg"
        cv2.imwrite(str(board_path), board)
        written.append(board_path)

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="열린 말풍선 내부 추정 후보 시각화")
    parser.add_argument("--image", default=str(DATA_HOME / 'samples/test_image1/v01/00024.jpg'))
    parser.add_argument("--groups", default="temp_output/Ore1/json/00024_text_groups.json")
    parser.add_argument("--regions", default="temp_output/Ore1/json/00024_regions.json")
    parser.add_argument("--out-dir", default=str(PROOF_DIR / 'misc/interior_rescue_00024'))
    args = parser.parse_args()

    written = run(
        Path(args.image),
        Path(args.groups),
        Path(args.regions) if args.regions else None,
        Path(args.out_dir),
    )
    print(json.dumps([str(path) for path in written], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
