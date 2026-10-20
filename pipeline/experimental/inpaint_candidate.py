# Flood 실패 후보에 inpaint를 적용하는 실험 모듈
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
from pathlib import Path
from pipeline.paths import DATA_HOME
from typing import List, Tuple

import cv2
import numpy as np


IMAGE_DIR = (DATA_HOME / "samples/test_image")
PROOF_DIR = Path("proof")
SHEET_DIR = PROOF_DIR / "json"
INPAINT_DIR = PROOF_DIR / "inpaint"

EXCLUDE_TYPES = {"in_bubble", "dark_bg_text", "symbol_only"}

INPAINT_RADIUS = 7
INPAINT_FLAGS = cv2.INPAINT_TELEA

TEXT_THRESH = 160
TEXT_DILATE_PX = 3
BBOX_PAD = 24
BORDER_MARGIN = 8
MIN_CONTOUR_AREA = 4
MAX_AREA_RATIO = 0.35


def make_text_mask(gray_roi: np.ndarray, label: str = "") -> np.ndarray:
    """ROI 내부의 텍스트 글리프만 골라낸 마스크를 반환한다."""
    h, w = gray_roi.shape[:2]
    _, binary = cv2.threshold(gray_roi, TEXT_THRESH, 255, cv2.THRESH_BINARY_INV)

    k_open = np.ones((2, 2), dtype=np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k_open)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    mask = np.zeros((h, w), dtype=np.uint8)
    max_area = h * w * MAX_AREA_RATIO
    margin = BORDER_MARGIN

    n_total = len(contours)
    n_border = n_area = n_pass = 0

    for cnt in contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bx <= margin or by <= margin or bx + bw >= w - margin or by + bh >= h - margin:
            n_border += 1
            continue
        area = cv2.contourArea(cnt)
        if area < MIN_CONTOUR_AREA or area > max_area:
            n_area += 1
            continue
        cv2.drawContours(mask, [cnt], -1, 255, cv2.FILLED)
        n_pass += 1

    tag = f"[{label}] " if label else ""
    print(
        f"    {tag}컨투어 총 {n_total}개 → 통과 {n_pass} / "
        f"경계필터 {n_border} / 면적필터 {n_area}  mask_px={int(np.count_nonzero(mask))}"
    )

    k_dilate = np.ones((TEXT_DILATE_PX * 2 + 1, TEXT_DILATE_PX * 2 + 1), dtype=np.uint8)
    return cv2.dilate(mask, k_dilate, iterations=1)


def padded_roi(image: np.ndarray, bbox: List[int], pad: int) -> Tuple[np.ndarray, int, int]:
    img_h, img_w = image.shape[:2]
    x, y, w, h = [int(v) for v in bbox[:4]]
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img_w, x + w + pad)
    y2 = min(img_h, y + h + pad)
    return image[y1:y2, x1:x2].copy(), x1, y1


def side_by_side(left: np.ndarray, right: np.ndarray, label_l: str, label_r: str) -> np.ndarray:
    h = max(left.shape[0], right.shape[0])

    def pad_h(img: np.ndarray, target_h: int) -> np.ndarray:
        if img.shape[0] < target_h:
            pad = np.full((target_h - img.shape[0], img.shape[1], 3), 255, dtype=np.uint8)
            return np.vstack([img, pad])
        return img

    lp = pad_h(left, h)
    rp = pad_h(right, h)
    out = np.hstack([lp, rp])
    font = cv2.FONT_HERSHEY_SIMPLEX
    for text, x_off in ((label_l, 4), (label_r, lp.shape[1] + 4)):
        cv2.putText(out, text, (x_off, 16), font, 0.52, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(out, text, (x_off, 16), font, 0.52, (0, 80, 220), 1, cv2.LINE_AA)
    cv2.line(out, (lp.shape[1], 0), (lp.shape[1], h - 1), (0, 140, 0), 2)
    return out


def is_inpaint_target(result: dict) -> bool:
    region_type = result.get("region_type_guess", "")
    safe = result.get("safe_region", False)
    if region_type in EXCLUDE_TYPES:
        return False
    if region_type == "narration_like" and safe:
        return False
    return True


def _load_flood_vis_image(img_num: str) -> np.ndarray | None:
    flood_vis_path = PROOF_DIR / "flood_vis" / f"{img_num}_flood_vis.jpg"
    if not flood_vis_path.exists():
        flood_vis_path = PROOF_DIR / "flood_vis" / f"{img_num}_flood_debug.jpg"
    return cv2.imread(str(flood_vis_path)) if flood_vis_path.exists() else None


def run(img_num: str) -> None:
    image_path = IMAGE_DIR / f"{img_num}.jpg"
    vis_json_path = SHEET_DIR / f"{img_num}_flood_vis.json"

    if not image_path.exists():
        print(f"이미지 없음. {image_path}")
        return
    if not vis_json_path.exists():
        print(f"flood_vis.json 없음. {vis_json_path}")
        print("먼저 run_pipeline.py --work-dir proof/ 를 실행하세요.")
        return

    vis_data = json.loads(vis_json_path.read_text(encoding="utf-8"))
    results = vis_data.get("results", [])
    targets = [r for r in results if is_inpaint_target(r)]
    excluded = [r for r in results if not is_inpaint_target(r)]

    print(f"[{img_num}] 총 {len(results)}개 anchor → inpaint 대상 {len(targets)}건 / 제외 {len(excluded)}건")
    print()

    by_type: dict = {}
    for result in targets:
        region_type = result.get("region_type_guess", "?")
        safe = "safe" if result.get("safe_region") else "!safe"
        key = f"{region_type}({safe})"
        by_type.setdefault(key, []).append(result["cluster_id"])
    for key, ids in by_type.items():
        print(f"  포함 {key}. A{', A'.join(str(i) for i in ids)}")

    by_excluded: dict = {}
    for result in excluded:
        region_type = result.get("region_type_guess", "?")
        by_excluded.setdefault(region_type, []).append(result["cluster_id"])
    for key, ids in by_excluded.items():
        print(f"  제외 {key}. A{', A'.join(str(i) for i in ids)}")
    print()

    if not targets:
        print("inpaint 대상 없음")
        return

    INPAINT_DIR.mkdir(parents=True, exist_ok=True)
    image = cv2.imread(str(image_path))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    contact_rows: List[np.ndarray] = []
    full_inpaint = image.copy()

    for diag in targets:
        cid = diag["cluster_id"]
        bbox = diag["anchor_bbox"]
        method = diag.get("region_type_guess", "?")

        roi_orig, rx1, ry1 = padded_roi(image, bbox, BBOX_PAD)
        rh, rw = roi_orig.shape[:2]
        roi_gray = gray[ry1:ry1 + rh, rx1:rx1 + rw]
        text_mask = make_text_mask(roi_gray, label=f"A{cid}")

        roi_inpainted = cv2.inpaint(roi_orig, text_mask, INPAINT_RADIUS, INPAINT_FLAGS)

        cv2.imwrite(str(INPAINT_DIR / f"{img_num}_A{cid:02d}_mask.jpg"), text_mask)

        compare = side_by_side(
            roi_orig,
            roi_inpainted,
            label_l=f"A{cid} orig ({method[:5]})",
            label_r=f"A{cid} inpaint",
        )
        out_path = INPAINT_DIR / f"{img_num}_A{cid:02d}_{method}_cmp.jpg"
        cv2.imwrite(str(out_path), compare)
        print(f"  A{cid} ({method}) → {out_path.name}")

        contact_rows.append(compare)
        full_inpaint[ry1:ry1 + rh, rx1:rx1 + rw] = roi_inpainted

    if contact_rows:
        max_w = max(row.shape[1] for row in contact_rows)
        rows_padded = []
        for row in contact_rows:
            if row.shape[1] < max_w:
                pad = np.full((row.shape[0], max_w - row.shape[1], 3), 255, dtype=np.uint8)
                row = np.hstack([row, pad])
            rows_padded.append(row)
        cv2.imwrite(str(INPAINT_DIR / f"{img_num}_contact.jpg"), np.vstack(rows_padded))
        print(f"\n  → {img_num}_contact.jpg")

    cv2.imwrite(str(INPAINT_DIR / f"{img_num}_full_inpaint.jpg"), full_inpaint)
    print(f"  → {img_num}_full_inpaint.jpg")

    flood_vis_img = _load_flood_vis_image(img_num)
    if flood_vis_img is not None:
        if flood_vis_img.shape == full_inpaint.shape:
            merge = np.hstack([flood_vis_img, full_inpaint])
        else:
            target_h = max(flood_vis_img.shape[0], full_inpaint.shape[0])

            def pad_img(img: np.ndarray, th: int) -> np.ndarray:
                if img.shape[0] < th:
                    pad = np.full((th - img.shape[0], img.shape[1], 3), 255, dtype=np.uint8)
                    return np.vstack([img, pad])
                return img

            merge = np.hstack([pad_img(flood_vis_img, target_h), pad_img(full_inpaint, target_h)])
        cv2.imwrite(str(INPAINT_DIR / f"{img_num}_merge.jpg"), merge)
        print(f"  → {img_num}_merge.jpg")
    else:
        print("flood_vis 이미지 없음. merge 생략")

    print("\n완료. proof/inpaint/ 확인.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="flood fail 케이스에 cv2.inpaint 적용 검증")
    parser.add_argument("img_num", nargs="?", default="00032")
    args = parser.parse_args()
    run(args.img_num)


if __name__ == "__main__":
    main()
