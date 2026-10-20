# CV anchor 없이 흰 말풍선 후보를 찾아 비교 시각화하는 스크립트
#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


Rect = list[int]


@dataclass
class Candidate:
    candidate_id: int
    rect: Rect
    area: int
    area_ratio: float
    fill_ratio: float
    solidity: float
    circularity: float
    dark_ratio: float
    known_text_overlap: float
    known_text_center_count: int
    known_region_overlap: float
    score: float
    decision: str
    reject_reasons: list[str]


def _load_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _as_rect(rect: Any) -> Rect:
    if not rect:
        return [0, 0, 0, 0]
    x, y, w, h = [int(v) for v in rect[:4]]
    return [x, y, max(0, w), max(0, h)]


def _xyxy_to_rect(box: list[int]) -> Rect:
    ymin, xmin, ymax, xmax = [int(v) for v in box[:4]]
    return [xmin, ymin, max(0, xmax - xmin), max(0, ymax - ymin)]


def _rect_iou(a: Rect, b: Rect) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _rect_overlap_ratio(a: Rect, b: Rect) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    return inter / max(1, aw * ah)


def _rect_contains_center(rect: Rect, target: Rect) -> bool:
    x, y, w, h = rect
    tx, ty, tw, th = target
    cx = tx + tw / 2
    cy = ty + th / 2
    return x <= cx <= x + w and y <= cy <= y + h


def _known_text_rects(cv_json: Path | None, text_groups_json: Path | None) -> list[Rect]:
    rects: list[Rect] = []
    cv_data = _load_json(cv_json)
    if isinstance(cv_data, list):
        for item in cv_data:
            if item.get("box_2d"):
                rects.append(_xyxy_to_rect(item["box_2d"]))

    groups = _load_json(text_groups_json)
    if isinstance(groups, list):
        for group in groups:
            rect = group.get("tight_rect") or group.get("fallback_rect") or group.get("bounding_rect")
            if rect:
                rects.append(_as_rect(rect))
    return rects


def _known_region_rects(regions_json: Path | None) -> list[Rect]:
    data = _load_json(regions_json)
    if not isinstance(data, dict):
        return []
    rects = []
    for region in data.get("regions", []):
        rect = region.get("source_rect") or region.get("bubble_rect") or region.get("ocr_rect")
        if rect:
            rects.append(_as_rect(rect))
    return rects


def _remove_border_connected(mask: np.ndarray) -> np.ndarray:
    flood = mask.copy()
    h, w = flood.shape[:2]
    ff_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    for x in range(w):
        if flood[0, x]:
            cv2.floodFill(flood, ff_mask, (x, 0), 0)
        if flood[h - 1, x]:
            cv2.floodFill(flood, ff_mask, (x, h - 1), 0)
    for y in range(h):
        if flood[y, 0]:
            cv2.floodFill(flood, ff_mask, (0, y), 0)
        if flood[y, w - 1]:
            cv2.floodFill(flood, ff_mask, (w - 1, y), 0)
    return flood


def _build_white_region_mask(gray: np.ndarray, threshold: int) -> np.ndarray:
    _, white = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8), iterations=1)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8), iterations=1)
    return _remove_border_connected(white)


def _candidate_score(
    area_ratio: float,
    fill_ratio: float,
    solidity: float,
    circularity: float,
    dark_ratio: float,
    known_text_overlap: float,
    known_text_center_count: int,
    known_region_overlap: float,
) -> float:
    size_score = 1.0 if 0.004 <= area_ratio <= 0.08 else 0.45
    dark_score = min(dark_ratio / 0.08, 1.0)
    shape_score = min(max(solidity, circularity), 1.0)
    missing_bonus = 0.0 if known_text_center_count > 0 else 1.0 - min(known_text_overlap / 0.25, 1.0)
    region_bonus = 1.0 - min(known_region_overlap / 0.45, 1.0)
    return round(
        0.22 * size_score
        + 0.22 * min(fill_ratio / 0.75, 1.0)
        + 0.20 * shape_score
        + 0.18 * dark_score
        + 0.12 * missing_bonus
        + 0.06 * region_bonus,
        4,
    )


def find_candidates(
    image: np.ndarray,
    known_text_rects: list[Rect],
    known_region_rects: list[Rect],
    white_threshold: int,
    dark_threshold: int,
    min_area_ratio: float,
    max_area_ratio: float,
) -> tuple[np.ndarray, list[Candidate]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    page_area = gray.shape[0] * gray.shape[1]
    white_mask = _build_white_region_mask(gray, white_threshold)
    contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[Candidate] = []
    for contour in contours:
        area = int(cv2.contourArea(contour))
        if area <= 0:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        rect = [int(x), int(y), int(w), int(h)]
        rect_area = max(1, w * h)
        area_ratio = area / page_area
        fill_ratio = area / rect_area
        hull = cv2.convexHull(contour)
        hull_area = max(1.0, float(cv2.contourArea(hull)))
        solidity = float(area) / hull_area
        perimeter = max(1.0, float(cv2.arcLength(contour, True)))
        circularity = min(1.0, float(4.0 * np.pi * area / (perimeter * perimeter)))

        component_mask = np.zeros_like(gray)
        cv2.drawContours(component_mask, [contour], -1, 255, -1)
        inner = cv2.erode(component_mask, np.ones((5, 5), dtype=np.uint8), iterations=1)
        inner_pixels = max(1, int(np.count_nonzero(inner)))
        dark_pixels = int(np.count_nonzero((gray < dark_threshold) & (inner > 0)))
        dark_ratio = dark_pixels / inner_pixels

        known_text_overlap = max((_rect_overlap_ratio(rect, known) for known in known_text_rects), default=0.0)
        known_text_center_count = sum(1 for known in known_text_rects if _rect_contains_center(rect, known))
        known_region_overlap = max((_rect_iou(rect, known) for known in known_region_rects), default=0.0)

        reject_reasons: list[str] = []
        if area_ratio < min_area_ratio:
            reject_reasons.append("too_small")
        if area_ratio > max_area_ratio:
            reject_reasons.append("too_large")
        if fill_ratio < 0.28:
            reject_reasons.append("low_fill")
        if solidity < 0.55:
            reject_reasons.append("low_solidity")
        if dark_ratio < 0.003:
            reject_reasons.append("low_dark_content")
        if known_region_overlap >= 0.65:
            reject_reasons.append("already_region")

        score = _candidate_score(
            area_ratio,
            fill_ratio,
            solidity,
            circularity,
            dark_ratio,
            known_text_overlap,
            known_text_center_count,
            known_region_overlap,
        )
        if not reject_reasons and known_text_center_count == 0 and known_text_overlap < 0.18:
            decision = "anchorless_candidate"
        elif not reject_reasons:
            decision = "covered_candidate"
        else:
            decision = "rejected"

        candidates.append(
            Candidate(
                candidate_id=0,
                rect=rect,
                area=area,
                area_ratio=round(area_ratio, 5),
                fill_ratio=round(fill_ratio, 4),
                solidity=round(solidity, 4),
                circularity=round(circularity, 4),
                dark_ratio=round(dark_ratio, 4),
                known_text_overlap=round(known_text_overlap, 4),
                known_text_center_count=known_text_center_count,
                known_region_overlap=round(known_region_overlap, 4),
                score=score,
                decision=decision,
                reject_reasons=reject_reasons,
            )
        )

    candidates.sort(key=lambda c: (c.decision != "anchorless_candidate", -c.score, c.rect[1], c.rect[0]))
    for idx, candidate in enumerate(candidates, start=1):
        candidate.candidate_id = idx
    return white_mask, candidates


def _draw_candidates(
    image: np.ndarray,
    candidates: list[Candidate],
    output_path: Path,
    title: str,
    include_rejected: bool,
) -> None:
    canvas = image.copy()
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 36), (255, 255, 255), -1)
    cv2.putText(canvas, title, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (0, 0, 0), 2, cv2.LINE_AA)

    for candidate in candidates:
        if candidate.decision == "rejected" and not include_rejected:
            continue
        x, y, w, h = candidate.rect
        if candidate.decision == "anchorless_candidate":
            color = (0, 190, 0)
        elif candidate.decision == "covered_candidate":
            color = (255, 120, 0)
        else:
            color = (0, 0, 220)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)
        label = f"A{candidate.candidate_id} {candidate.score:.2f}"
        cv2.rectangle(canvas, (x, max(0, y - 20)), (x + 78, y), color, -1)
        cv2.putText(canvas, label, (x + 3, max(13, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), canvas)


def _decision_color(candidate: Candidate) -> tuple[int, int, int]:
    if candidate.decision == "anchorless_candidate":
        return (0, 190, 0)
    if candidate.decision == "covered_candidate":
        return (255, 120, 0)
    return (0, 0, 220)


def _draw_decision_layers(
    image: np.ndarray,
    white_mask: np.ndarray,
    candidates: list[Candidate],
    known_text_rects: list[Rect],
    known_region_rects: list[Rect],
    output_path: Path,
) -> None:
    canvas = image.copy()
    overlay = canvas.copy()
    overlay[white_mask > 0] = (255, 230, 80)
    canvas = cv2.addWeighted(overlay, 0.28, canvas, 0.72, 0)

    for rect in known_region_rects:
        x, y, w, h = rect
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (210, 0, 210), 2)

    for rect in known_text_rects:
        x, y, w, h = rect
        cx = int(x + w / 2)
        cy = int(y + h / 2)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (255, 120, 0), 1)
        cv2.circle(canvas, (cx, cy), 3, (255, 0, 0), -1)

    for candidate in candidates:
        if candidate.decision == "rejected":
            continue
        x, y, w, h = candidate.rect
        color = _decision_color(candidate)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 3)
        label = (
            f"A{candidate.candidate_id} {candidate.decision} "
            f"s={candidate.score:.2f} dark={candidate.dark_ratio:.2f} "
            f"txt={candidate.known_text_center_count}"
        )
        cv2.rectangle(canvas, (x, max(0, y - 24)), (min(canvas.shape[1] - 1, x + 310), y), color, -1)
        cv2.putText(canvas, label, (x + 4, max(15, y - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (255, 255, 255), 1, cv2.LINE_AA)

    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 56), (255, 255, 255), -1)
    cv2.putText(canvas, "yellow=white components  blue=known text  purple=known regions", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(canvas, "green=anchorless candidate  orange=already covered candidate", (8, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 0, 0), 1, cv2.LINE_AA)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), canvas)


def _write_candidate_review_board(
    image: np.ndarray,
    white_mask: np.ndarray,
    candidates: list[Candidate],
    output_path: Path,
) -> None:
    selected = [candidate for candidate in candidates if candidate.decision != "rejected"]
    if not selected:
        return

    tile_w, tile_h = 280, 210
    rows = len(selected)
    board = np.full((rows * tile_h, tile_w * 4, 3), 255, dtype=np.uint8)

    for row, candidate in enumerate(selected):
        x, y, w, h = candidate.rect
        pad = 20
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(image.shape[1], x + w + pad)
        y2 = min(image.shape[0], y + h + pad)
        crop = image[y1:y2, x1:x2]
        mask_crop = white_mask[y1:y2, x1:x2]
        mask_vis = cv2.cvtColor(mask_crop, cv2.COLOR_GRAY2BGR)
        mask_overlay = crop.copy()
        mask_overlay[mask_crop > 0] = (255, 230, 80)
        mask_overlay = cv2.addWeighted(mask_overlay, 0.45, crop, 0.55, 0)

        def fit(tile: np.ndarray) -> np.ndarray:
            out = np.full((tile_h, tile_w, 3), 255, dtype=np.uint8)
            th, tw = tile.shape[:2]
            scale = min((tile_w - 8) / max(1, tw), (tile_h - 8) / max(1, th))
            resized = cv2.resize(tile, (max(1, int(tw * scale)), max(1, int(th * scale))), interpolation=cv2.INTER_AREA)
            out[4 : 4 + resized.shape[0], 4 : 4 + resized.shape[1]] = resized
            return out

        board[row * tile_h : (row + 1) * tile_h, 0:tile_w] = fit(crop)
        board[row * tile_h : (row + 1) * tile_h, tile_w : tile_w * 2] = fit(mask_vis)
        board[row * tile_h : (row + 1) * tile_h, tile_w * 2 : tile_w * 3] = fit(mask_overlay)

        info_x = tile_w * 3 + 10
        info_y = row * tile_h + 24
        color = _decision_color(candidate)
        cv2.rectangle(
            board,
            (tile_w * 3, row * tile_h),
            (tile_w * 4 - 1, (row + 1) * tile_h - 1),
            (245, 245, 245),
            -1,
        )
        lines = [
            f"A{candidate.candidate_id} {candidate.decision}",
            f"score {candidate.score:.2f}",
            f"area {candidate.area_ratio:.4f}",
            f"fill {candidate.fill_ratio:.2f}",
            f"solid {candidate.solidity:.2f}",
            f"dark {candidate.dark_ratio:.2f}",
            f"text centers {candidate.known_text_center_count}",
            f"region iou {candidate.known_region_overlap:.2f}",
        ]
        for idx, line in enumerate(lines):
            cv2.putText(board, line, (info_x, info_y + idx * 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color if idx == 0 else (0, 0, 0), 1, cv2.LINE_AA)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), board)


def _write_candidate_crops(image: np.ndarray, candidates: list[Candidate], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        if candidate.decision == "rejected":
            continue
        x, y, w, h = candidate.rect
        pad = 20
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(image.shape[1], x + w + pad)
        y2 = min(image.shape[0], y + h + pad)
        crop = image[y1:y2, x1:x2].copy()
        cv2.putText(
            crop,
            f"A{candidate.candidate_id} {candidate.decision}",
            (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (0, 0, 220),
            2,
            cv2.LINE_AA,
        )
        cv2.imwrite(str(out_dir / f"A{candidate.candidate_id:02d}_{candidate.decision}.jpg"), crop)


def _make_contact_sheet(crop_dir: Path, output_path: Path) -> None:
    files = sorted(crop_dir.glob("*.jpg"))
    if not files:
        return
    thumb_w, thumb_h = 240, 180
    cols = 3
    rows = int(np.ceil(len(files) / cols))
    sheet = np.full((rows * (thumb_h + 26), cols * thumb_w, 3), 255, dtype=np.uint8)
    for idx, path in enumerate(files):
        image = cv2.imread(str(path))
        if image is None:
            continue
        h, w = image.shape[:2]
        scale = min(thumb_w / max(1, w), thumb_h / max(1, h))
        resized = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        x = (idx % cols) * thumb_w
        y = (idx // cols) * (thumb_h + 26)
        sheet[y + 26 : y + 26 + resized.shape[0], x : x + resized.shape[1]] = resized
        cv2.putText(sheet, path.stem[:30], (x + 4, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 180), 1, cv2.LINE_AA)
    cv2.imwrite(str(output_path), sheet)


def run_one(
    image_path: Path,
    cv_json: Path | None,
    text_groups_json: Path | None,
    regions_json: Path | None,
    output_root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    known_text = _known_text_rects(cv_json, text_groups_json)
    known_regions = _known_region_rects(regions_json)

    started = time.time()
    white_mask, candidates = find_candidates(
        image,
        known_text,
        known_regions,
        white_threshold=args.white_threshold,
        dark_threshold=args.dark_threshold,
        min_area_ratio=args.min_area_ratio,
        max_area_ratio=args.max_area_ratio,
    )
    elapsed = time.time() - started

    out_dir = output_root / image_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "white_components_mask.jpg"), white_mask)
    _draw_candidates(image, candidates, out_dir / "candidates_all.jpg", f"{image_path.stem} anchorless all", include_rejected=True)
    _draw_candidates(image, candidates, out_dir / "candidates_selected.jpg", f"{image_path.stem} anchorless selected", include_rejected=False)
    _draw_decision_layers(image, white_mask, candidates, known_text, known_regions, out_dir / "decision_layers.jpg")
    _write_candidate_review_board(image, white_mask, candidates, out_dir / "candidate_review_board.jpg")
    _write_candidate_crops(image, candidates, out_dir / "crops")
    _make_contact_sheet(out_dir / "crops", out_dir / "crop_contact_sheet.jpg")

    payload = {
        "image": str(image_path),
        "cv_json": str(cv_json) if cv_json else None,
        "text_groups_json": str(text_groups_json) if text_groups_json else None,
        "regions_json": str(regions_json) if regions_json else None,
        "elapsed_sec": round(elapsed, 4),
        "known_text_rect_count": len(known_text),
        "known_region_rect_count": len(known_regions),
        "candidates": [candidate.__dict__ for candidate in candidates],
    }
    (out_dir / "candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "image": image_path.stem,
        "elapsed_sec": round(elapsed, 4),
        "total": len(candidates),
        "anchorless": sum(1 for c in candidates if c.decision == "anchorless_candidate"),
        "covered": sum(1 for c in candidates if c.decision == "covered_candidate"),
        "rejected": sum(1 for c in candidates if c.decision == "rejected"),
    }


def _default_json_path(kind: str, image_path: Path) -> Path:
    name = image_path.stem
    if kind == "cv":
        return (DATA_HOME / "legacy/temp_output") / "Ore1" / "json" / f"{name}_cv.json"
    if kind == "groups":
        return (DATA_HOME / "legacy/temp_output") / "Ore1" / "json" / f"{name}_text_groups.json"
    if kind == "regions":
        return (DATA_HOME / "legacy/temp_output") / "Ore1" / "json" / f"{name}_regions.json"
    raise ValueError(kind)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="anchorless bubble 후보 검출 실험")
    parser.add_argument("images", nargs="+")
    parser.add_argument("--output-root", default=str(PROOF_DIR / 'anchorless_bubble_experiments'))
    parser.add_argument("--cv-json")
    parser.add_argument("--text-groups-json")
    parser.add_argument("--regions-json")
    parser.add_argument("--white-threshold", type=int, default=242)
    parser.add_argument("--dark-threshold", type=int, default=170)
    parser.add_argument("--min-area-ratio", type=float, default=0.0012)
    parser.add_argument("--max-area-ratio", type=float, default=0.18)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_root = ROOT_DIR / args.output_root
    output_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for image_arg in args.images:
        image_path = Path(image_arg).resolve()
        cv_json = Path(args.cv_json).resolve() if args.cv_json else _default_json_path("cv", image_path)
        text_groups_json = Path(args.text_groups_json).resolve() if args.text_groups_json else _default_json_path("groups", image_path)
        regions_json = Path(args.regions_json).resolve() if args.regions_json else _default_json_path("regions", image_path)
        rows.append(run_one(image_path, cv_json, text_groups_json, regions_json, output_root, args))

    summary = {
        "output_root": str(output_root),
        "rows": rows,
    }
    (output_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Anchorless bubble experiment summary", ""]
    for row in rows:
        lines.append(
            f"- {row['image']}: anchorless={row['anchorless']}, "
            f"covered={row['covered']}, rejected={row['rejected']}, elapsed={row['elapsed_sec']}s"
        )
    (output_root / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"완료: {output_root}")


if __name__ == "__main__":
    main()
