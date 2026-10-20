# Cloud Vision 전처리 조합별 OCR 결과를 비교 시각화하는 스크립트
#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path

import argparse
import csv
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

from pipeline.cloud_vision_api import CloudVisionAPI  # noqa: E402


Rect = list[int]


@dataclass(frozen=True)
class Variant:
    name: str
    scale: float
    image: np.ndarray


FEATURES = {
    "document": "DOCUMENT_TEXT_DETECTION",
    "text": "TEXT_DETECTION",
}

HINTS = {
    "ja_en": ["ja", "en"],
    "ja": ["ja"],
}


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def _load_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(path)
    return image


def _make_variants(image: np.ndarray) -> list[Variant]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    up2 = cv2.resize(image, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    clahe_bgr = cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)

    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    otsu_bgr = cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR)

    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        8,
    )
    adaptive_bgr = cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR)

    close_kernel = np.ones((2, 2), dtype=np.uint8)
    mild_close = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    mild_close = cv2.dilate(mild_close, close_kernel, iterations=1)
    mild_close_bgr = cv2.cvtColor(mild_close, cv2.COLOR_GRAY2BGR)

    # 흑백 만화는 축소/압축 과정에서 가는 획이 끊기는 경우가 있어,
    # 2배 확대 후 대비 보정을 따로 둔다.
    up2_gray = cv2.cvtColor(up2, cv2.COLOR_BGR2GRAY)
    up2_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(up2_gray)
    up2_clahe_bgr = cv2.cvtColor(up2_clahe, cv2.COLOR_GRAY2BGR)

    return [
        Variant("original", 1.0, image),
        Variant("upscale2x", 2.0, up2),
        Variant("clahe", 1.0, clahe_bgr),
        Variant("otsu", 1.0, otsu_bgr),
        Variant("adaptive", 1.0, adaptive_bgr),
        Variant("mild_close", 1.0, mild_close_bgr),
        Variant("upscale2x_clahe", 2.0, up2_clahe_bgr),
    ]


def _normalize_box(box: list[int], scale: float) -> Rect:
    if scale == 1.0:
        return [int(v) for v in box]
    ymin, xmin, ymax, xmax = box
    return [
        int(round(ymin / scale)),
        int(round(xmin / scale)),
        int(round(ymax / scale)),
        int(round(xmax / scale)),
    ]


def _rect_iou(a: Rect, b: Rect) -> float:
    ay1, ax1, ay2, ax2 = a
    by1, bx1, by2, bx2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def _dedupe_boxes(boxes: list[dict[str, Any]], iou_threshold: float = 0.72) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for box in sorted(boxes, key=lambda item: _box_area(item["box_2d"]), reverse=True):
        if any(_rect_iou(box["box_2d"], kept["box_2d"]) >= iou_threshold for kept in result):
            continue
        result.append(box)
    return result


def _box_area(box: Rect) -> int:
    ymin, xmin, ymax, xmax = box
    return max(0, ymax - ymin) * max(0, xmax - xmin)


def _draw_boxes(
    image: np.ndarray,
    boxes: list[dict[str, Any]],
    title: str,
    output_path: Path,
    color: tuple[int, int, int] = (0, 0, 255),
) -> None:
    canvas = image.copy()
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 34), (255, 255, 255), -1)
    cv2.putText(
        canvas,
        f"{title} boxes={len(boxes)}",
        (8, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    for idx, item in enumerate(boxes, start=1):
        ymin, xmin, ymax, xmax = item["box_2d"]
        cv2.rectangle(canvas, (xmin, ymin), (xmax, ymax), color, 2)
        label = str(idx)
        cv2.rectangle(canvas, (xmin, max(0, ymin - 18)), (xmin + 34, ymin), color, -1)
        cv2.putText(
            canvas,
            label,
            (xmin + 3, max(12, ymin - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), canvas)


def _draw_comparison(
    image: np.ndarray,
    baseline_boxes: list[dict[str, Any]],
    boxes: list[dict[str, Any]],
    title: str,
    output_path: Path,
) -> None:
    canvas = image.copy()
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 42), (255, 255, 255), -1)

    new_count = 0
    matched_count = 0
    for base in baseline_boxes:
        ymin, xmin, ymax, xmax = base["box_2d"]
        cv2.rectangle(canvas, (xmin, ymin), (xmax, ymax), (180, 180, 180), 1)

    for idx, item in enumerate(boxes, start=1):
        best = max((_rect_iou(item["box_2d"], base["box_2d"]) for base in baseline_boxes), default=0.0)
        if best >= 0.5:
            color = (255, 120, 0)
            matched_count += 1
        else:
            color = (0, 180, 0)
            new_count += 1
        ymin, xmin, ymax, xmax = item["box_2d"]
        cv2.rectangle(canvas, (xmin, ymin), (xmax, ymax), color, 2)
        if best < 0.5:
            cv2.rectangle(canvas, (xmin, max(0, ymin - 18)), (xmin + 38, ymin), color, -1)
            cv2.putText(
                canvas,
                f"N{idx}",
                (xmin + 3, max(12, ymin - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    cv2.putText(
        canvas,
        f"{title} matched={matched_count} new={new_count}",
        (8, 27),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), canvas)


def _make_contact_sheet(image_paths: list[Path], output_path: Path) -> None:
    thumbs: list[tuple[str, np.ndarray]] = []
    for path in image_paths:
        image = cv2.imread(str(path))
        if image is None:
            continue
        thumbs.append((path.stem, image))
    if not thumbs:
        return

    thumb_w, thumb_h = 340, 240
    cols = 2
    rows = int(np.ceil(len(thumbs) / cols))
    sheet = np.full((rows * (thumb_h + 28), cols * thumb_w, 3), 255, dtype=np.uint8)
    for idx, (label, image) in enumerate(thumbs):
        h, w = image.shape[:2]
        scale = min(thumb_w / max(1, w), thumb_h / max(1, h))
        resized = cv2.resize(
            image,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
        x = (idx % cols) * thumb_w
        y = (idx // cols) * (thumb_h + 28)
        sheet[y + 28 : y + 28 + resized.shape[0], x : x + resized.shape[1]] = resized
        cv2.putText(sheet, label[:38], (x + 6, y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 180), 1, cv2.LINE_AA)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), sheet)


def _run_detection(
    api: CloudVisionAPI,
    feature_key: str,
    image_path: Path,
) -> list[dict[str, Any]]:
    if feature_key == "document":
        result = api.detect_document_text(str(image_path))
    elif feature_key == "text":
        result = api.detect_text(str(image_path))
    else:
        raise ValueError(feature_key)
    return result or []


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_cached_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_image(
    image_path: Path,
    output_root: Path,
    variant_filter: set[str] | None,
    features: list[str],
    hints: list[str],
    force: bool,
) -> list[dict[str, Any]]:
    image = _load_image(image_path)
    variants = _make_variants(image)
    if variant_filter:
        variants = [variant for variant in variants if variant.name in variant_filter]

    image_dir = output_root / image_path.stem
    pre_dir = image_dir / "preprocessed"
    raw_dir = image_dir / "raw"
    vis_dir = image_dir / "vis"
    compare_dir = image_dir / "compare"
    pre_dir.mkdir(parents=True, exist_ok=True)

    baseline_boxes: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    vis_paths: list[Path] = []

    for variant in variants:
        prep_path = pre_dir / f"{variant.name}.jpg"
        cv2.imwrite(str(prep_path), variant.image)

        for hint_key in hints:
            for feature_key in features:
                experiment_id = f"{variant.name}__{feature_key}__{hint_key}"
                raw_path = raw_dir / f"{experiment_id}.json"
                cached = None if force else _load_cached_json(raw_path)
                if cached is None:
                    api = CloudVisionAPI(language_hints=HINTS[hint_key])
                    started = time.time()
                    boxes = _run_detection(api, feature_key, prep_path)
                    elapsed = time.time() - started
                    normalized = []
                    for item in boxes:
                        box = _normalize_box([int(v) for v in item["box_2d"]], variant.scale)
                        normalized.append({
                            **item,
                            "box_2d": box,
                            "source_box_2d": item["box_2d"],
                            "source_scale": variant.scale,
                        })
                    payload = {
                        "image": str(image_path),
                        "variant": variant.name,
                        "feature": FEATURES[feature_key],
                        "language_hints": HINTS[hint_key],
                        "elapsed_sec": round(elapsed, 3),
                        "boxes": normalized,
                    }
                    _save_json(raw_path, payload)
                else:
                    payload = cached

                payloads[experiment_id] = payload
                boxes = payload.get("boxes", [])
                if experiment_id == "original__document__ja_en":
                    baseline_boxes = boxes

                vis_path = vis_dir / f"{experiment_id}.jpg"
                _draw_boxes(image, boxes, experiment_id, vis_path)
                vis_paths.append(vis_path)

    if not baseline_boxes:
        for experiment_id, payload in payloads.items():
            if experiment_id.startswith("original__document__"):
                baseline_boxes = payload.get("boxes", [])
                break

    compare_paths: list[Path] = []
    for experiment_id, payload in sorted(payloads.items()):
        boxes = payload.get("boxes", [])
        matched = 0
        new_boxes = 0
        for box in boxes:
            best = max((_rect_iou(box["box_2d"], base["box_2d"]) for base in baseline_boxes), default=0.0)
            if best >= 0.5:
                matched += 1
            else:
                new_boxes += 1
        variant_name, feature_key, hint_key = experiment_id.split("__", 2)
        compare_path = compare_dir / f"{experiment_id}_vs_baseline.jpg"
        _draw_comparison(image, baseline_boxes, boxes, experiment_id, compare_path)
        compare_paths.append(compare_path)
        rows.append({
            "image": image_path.stem,
            "experiment": experiment_id,
            "variant": variant_name,
            "feature": feature_key,
            "language": hint_key,
            "box_count": len(boxes),
            "matched_baseline": matched,
            "new_vs_baseline": new_boxes,
            "elapsed_sec": payload.get("elapsed_sec", ""),
        })

    union_boxes = []
    for raw_path in sorted(raw_dir.glob("*.json")):
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        for box in payload.get("boxes", []):
            union_boxes.append(box)
    union_boxes = _dedupe_boxes(union_boxes)
    _save_json(image_dir / "union_boxes.json", union_boxes)
    union_path = image_dir / "union_vis.jpg"
    _draw_boxes(image, union_boxes, f"{image_path.stem} union", union_path, color=(0, 120, 255))
    vis_paths.insert(0, union_path)

    _make_contact_sheet(vis_paths, image_dir / "contact_sheet.jpg")
    _make_contact_sheet(compare_paths, image_dir / "compare_contact_sheet.jpg")
    return rows


def _variant_by_name(image_path: Path, variant_name: str) -> Variant:
    image = _load_image(image_path)
    for variant in _make_variants(image):
        if variant.name == variant_name:
            return variant
    raise ValueError(f"알 수 없는 variant: {variant_name}")


def _load_single_payload(output_root: Path, image_path: Path, variant_name: str) -> dict[str, Any] | None:
    raw_path = output_root / image_path.stem / "raw" / f"{variant_name}__document__ja_en.json"
    return _load_cached_json(raw_path)


def run_grid_baseline(
    image_paths: list[Path],
    output_root: Path,
    force: bool,
    grid_variants: list[str],
) -> None:
    if len(image_paths) < 2:
        return
    grid_dir = output_root / "_grid_baseline"
    summary_rows: list[dict[str, Any]] = []

    for variant_name in grid_variants:
        variant_dir = grid_dir / variant_name
        raw_path = variant_dir / "document_ja_en_grid.json"
        payload = None if force else _load_cached_json(raw_path)

        prep_paths: list[Path] = []
        scales: dict[str, float] = {}
        for image_path in image_paths:
            variant = _variant_by_name(image_path, variant_name)
            prep_path = variant_dir / "preprocessed" / f"{image_path.stem}_{variant_name}.jpg"
            prep_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(prep_path), variant.image)
            prep_paths.append(prep_path)
            scales[image_path.stem] = variant.scale

        if payload is None:
            api = CloudVisionAPI(language_hints=HINTS["ja_en"])
            started = time.time()
            per_image_raw = api.detect_text_grid([str(path) for path in prep_paths], chunk_size=len(prep_paths))
            elapsed = round(time.time() - started, 3)
            normalized_per_image: list[list[dict[str, Any]]] = []
            for image_path, boxes in zip(image_paths, per_image_raw or []):
                scale = scales[image_path.stem]
                normalized = []
                for item in boxes or []:
                    normalized.append({
                        **item,
                        "box_2d": _normalize_box([int(v) for v in item["box_2d"]], scale),
                        "source_box_2d": item["box_2d"],
                        "source_scale": scale,
                    })
                normalized_per_image.append(normalized)
            payload = {
                "images": [str(path) for path in image_paths],
                "variant": variant_name,
                "feature": "DOCUMENT_TEXT_DETECTION",
                "language_hints": HINTS["ja_en"],
                "elapsed_sec": elapsed,
                "per_image": normalized_per_image,
            }
            _save_json(raw_path, payload)

        compare_paths: list[Path] = []
        for image_path, boxes in zip(image_paths, payload.get("per_image", [])):
            image = _load_image(image_path)
            out = variant_dir / f"{image_path.stem}_grid_document_ja_en.jpg"
            _draw_boxes(image, boxes or [], f"{image_path.stem} grid_{variant_name}", out, color=(255, 0, 180))

            single_payload = _load_single_payload(output_root, image_path, variant_name)
            single_boxes = single_payload.get("boxes", []) if single_payload else []
            compare_path = variant_dir / f"{image_path.stem}_grid_vs_single.jpg"
            _draw_comparison(image, single_boxes, boxes or [], f"grid_{variant_name}_vs_single", compare_path)
            compare_paths.append(compare_path)

            matched = 0
            new_boxes = 0
            for box in boxes or []:
                best = max((_rect_iou(box["box_2d"], base["box_2d"]) for base in single_boxes), default=0.0)
                if best >= 0.5:
                    matched += 1
                else:
                    new_boxes += 1
            summary_rows.append({
                "image": image_path.stem,
                "variant": variant_name,
                "single_count": len(single_boxes),
                "grid_count": len(boxes or []),
                "grid_matched_single": matched,
                "grid_new_vs_single": new_boxes,
                "grid_missing_vs_single": max(0, len(single_boxes) - matched),
                "elapsed_sec": payload.get("elapsed_sec", ""),
            })

        _make_contact_sheet(compare_paths, variant_dir / "grid_vs_single_contact_sheet.jpg")

    if summary_rows:
        csv_path = grid_dir / "grid_summary.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "image",
                    "variant",
                    "single_count",
                    "grid_count",
                    "grid_matched_single",
                    "grid_new_vs_single",
                    "grid_missing_vs_single",
                    "elapsed_sec",
                ],
            )
            writer.writeheader()
            writer.writerows(summary_rows)
        lines = ["# Grid OCR summary", ""]
        for row in summary_rows:
            lines.append(
                f"- {row['image']} {row['variant']}: single={row['single_count']}, "
                f"grid={row['grid_count']}, missing_vs_single={row['grid_missing_vs_single']}, "
                f"new_vs_single={row['grid_new_vs_single']}"
            )
        (grid_dir / "grid_summary.md").write_text("\n".join(lines), encoding="utf-8")


def _write_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    csv_path = output_root / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "image",
                "experiment",
                "variant",
                "feature",
                "language",
                "box_count",
                "matched_baseline",
                "new_vs_baseline",
                "elapsed_sec",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    by_image: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_image.setdefault(str(row["image"]), []).append(row)

    lines = ["# OCR hint experiment summary", ""]
    for image, image_rows in sorted(by_image.items()):
        lines.append(f"## {image}")
        top_rows = sorted(image_rows, key=lambda r: (int(r["new_vs_baseline"]), int(r["box_count"])), reverse=True)[:8]
        for row in top_rows:
            lines.append(
                f"- {row['experiment']}: boxes={row['box_count']}, "
                f"new_vs_baseline={row['new_vs_baseline']}, matched={row['matched_baseline']}"
            )
        lines.append("")
    (output_root / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud Vision OCR 전처리 실험")
    parser.add_argument("images", nargs="+", help="실험할 이미지 경로")
    parser.add_argument("--output-root", default=str(PROOF_DIR / 'ocr_hint_experiments'))
    parser.add_argument("--variants", default="", help="쉼표 구분. 비우면 전체")
    parser.add_argument("--features", default="document,text", help="document,text 중 선택")
    parser.add_argument("--hints", default="ja_en,ja", help="ja_en,ja 중 선택")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-grid", action="store_true")
    parser.add_argument("--grid-variants", default="original,upscale2x,upscale2x_clahe")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_root = ROOT_DIR / args.output_root
    image_paths = [Path(path).resolve() for path in args.images]
    variant_filter = {v.strip() for v in args.variants.split(",") if v.strip()} or None
    features = [f.strip() for f in args.features.split(",") if f.strip()]
    hints = [h.strip() for h in args.hints.split(",") if h.strip()]

    invalid_features = [feature for feature in features if feature not in FEATURES]
    invalid_hints = [hint for hint in hints if hint not in HINTS]
    if invalid_features or invalid_hints:
        raise ValueError(f"지원하지 않는 옵션: features={invalid_features}, hints={invalid_hints}")

    rows: list[dict[str, Any]] = []
    for image_path in image_paths:
        rows.extend(run_image(image_path, output_root, variant_filter, features, hints, args.force))

    if not args.skip_grid:
        grid_variants = [v.strip() for v in args.grid_variants.split(",") if v.strip()]
        run_grid_baseline(image_paths, output_root, args.force, grid_variants)

    _write_summary(output_root, rows)
    print(f"완료: {output_root}")


if __name__ == "__main__":
    main()
