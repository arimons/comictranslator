# Flood split inflection 후보를 시각화하는 진단 도구
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]

from pipeline.flood_split import collect_inflection_candidates
from pipeline.archive.floodfill_anchor_probe import _erode_mask, _fill_mask_holes, _flood_fill_mask


PROOF_DIR = Path("proof")
IMAGE_DIR = Path("test_image")
JSON_DIR = PROOF_DIR / "json"
FLOOD_VIS_DIR = PROOF_DIR / "flood_vis"
DEFAULT_OUT_DIR = PROOF_DIR / "inflection_lines"

# shared flood pair마다 다른 tint 색 (BGR)
PAIR_COLORS = [
    (210, 245, 255),  # 연노랑
    (255, 220, 200),  # 연파랑
    (200, 255, 220),  # 연초록
    (240, 200, 255),  # 연보라
    (255, 255, 190),  # 연청록
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rect_key(rect: list[int]) -> tuple[int, int, int, int]:
    return tuple(int(v) for v in rect[:4])


def anchor_center(rect: list[int]) -> tuple[int, int]:
    x, y, w, h = [int(v) for v in rect[:4]]
    return int(round(x + w / 2)), int(round(y + h / 2))


def _load_flood_debug(num: str) -> np.ndarray | None:
    for name in (f"{num}_flood_debug.jpg", f"{num}_flood_vis.jpg"):
        p = FLOOD_VIS_DIR / name
        if p.exists():
            return cv2.imread(str(p))
    return None


def visualize_one(num: str, out_dir: Path) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    image = cv2.imread(str(IMAGE_DIR / f"{num}.jpg"))
    if image is None:
        raise FileNotFoundError(f"이미지 없음: {IMAGE_DIR / f'{num}.jpg'}")

    flood_debug = _load_flood_debug(num)
    data = load_json(JSON_DIR / f"{num}_flood_vis.json")
    clusters = load_json(JSON_DIR / f"{num}_cv_clusters_v5.json")
    clusters_by_id = {int(c.get("cluster_id", i + 1)): c for i, c in enumerate(clusters)}

    # flood_rect 기준으로 anchor 그룹핑
    by_rect: dict[tuple[int, int, int, int], list[dict]] = {}
    for result in data.get("results", []):
        rect = result.get("flood_rect")
        if rect and result.get("seed"):
            by_rect.setdefault(rect_key(rect), []).append(result)

    shared_pairs = [(rect, items) for rect, items in by_rect.items() if len(items) >= 2]
    if not shared_pairs:
        return None

    # 모든 쌍을 하나의 캔버스에 그린다
    canvas = image.copy()
    all_anchor_ids: list[int] = []
    meta_list: list[dict] = []

    for pair_idx, (rect, items) in enumerate(shared_pairs):
        tint = PAIR_COLORS[pair_idx % len(PAIR_COLORS)]
        anchor_ids = [int(item["cluster_id"]) for item in items]
        centers = [
            anchor_center(clusters_by_id[aid]["bounding_rect"])
            for aid in anchor_ids
            if aid in clusters_by_id
        ]
        if len(centers) != len(anchor_ids):
            continue

        anchor_rects = [
            clusters_by_id[aid]["bounding_rect"]
            for aid in anchor_ids
            if aid in clusters_by_id
        ]
        seed = tuple(int(v) for v in items[0]["seed"])
        mask, _, detected_rect = _flood_fill_mask(image, seed)
        inner = _erode_mask(_fill_mask_holes(mask), pixels=3)
        info = collect_inflection_candidates(inner, centers, anchor_rects=anchor_rects)
        candidates = info["candidates"]
        selected = info["selected"]
        defect_points = info["defect_points"]

        # flood region tint
        overlay = canvas.copy()
        overlay[inner > 0] = tint
        canvas = cv2.addWeighted(overlay, 0.40, canvas, 0.60, 0)

        # flood_rect 테두리
        x, y, w, h = [int(v) for v in detected_rect[:4]]
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 180, 255), 2)

        # 후보 선 (회색=미선택, 보라=유효)
        for cand in candidates:
            p1 = tuple(int(v) for v in cand["p1"])
            p2 = tuple(int(v) for v in cand["p2"])
            color = (170, 170, 170) if not (cand.get("separates") and cand.get("centers_inside")) else (150, 120, 255)
            cv2.line(canvas, p1, p2, color, 1, cv2.LINE_AA)

        # defect point
        for rank, item in enumerate(defect_points[:10]):
            pt = tuple(int(v) for v in item["pt"])
            cv2.circle(canvas, pt, 7, (0, 210, 0), -1)
            cv2.putText(canvas, f"D{rank}:{item['depth']:.0f}", (pt[0] + 6, pt[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 120, 0), 2, cv2.LINE_AA)

        # 선택된 split line (빨간 굵은 선)
        if selected is not None:
            p1 = tuple(int(v) for v in selected["p1"])
            p2 = tuple(int(v) for v in selected["p2"])
            cv2.line(canvas, p1, p2, (0, 0, 255), 4, cv2.LINE_AA)
            cv2.putText(canvas, "selected", (p1[0] + 4, p1[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 200), 2, cv2.LINE_AA)

        # anchor 중심 + 라벨
        for aid, center in zip(anchor_ids, centers):
            cv2.circle(canvas, center, 9, (220, 0, 220), -1)
            cv2.putText(canvas, f"A{aid}", (center[0] + 8, center[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (160, 0, 160), 2, cv2.LINE_AA)

        all_anchor_ids.extend(anchor_ids)
        meta_list.append({
            "anchor_ids": anchor_ids,
            "flood_rect": list(rect),
            "candidate_count": len(candidates),
            "selected": selected,
            "defect_points": defect_points,
        })

    if not meta_list:
        return None

    # 범례
    cv2.putText(canvas, f"{num}  shared floods: {len(shared_pairs)}",
                (20, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(canvas, "green=defects  purple=valid  red=selected",
                (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA)

    # flood_debug 이미지와 좌우 합성
    if flood_debug is not None:
        h_target = max(flood_debug.shape[0], canvas.shape[0])
        def _pad(img: np.ndarray) -> np.ndarray:
            gap = h_target - img.shape[0]
            return img if gap == 0 else np.vstack([img, np.full((gap, img.shape[1], 3), 255, dtype=np.uint8)])
        combined = np.hstack([_pad(flood_debug), _pad(canvas)])
    else:
        combined = canvas

    out = out_dir / f"{num}_inflection_lines.jpg"
    cv2.imwrite(str(out), combined)

    json_dir = out_dir / "json"
    json_dir.mkdir(exist_ok=True)
    (json_dir / f"{num}_inflection_lines.json").write_text(
        json.dumps({"num": num, "pairs": meta_list}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("nums", nargs="+")
    parser.add_argument("--output", "-o", default=str(DEFAULT_OUT_DIR), help="출력 폴더")
    args = parser.parse_args()
    out_dir = Path(args.output)
    for num in args.nums:
        path = visualize_one(num, out_dir)
        if path:
            print(path)
        else:
            print(f"{num}: shared flood 없음")


if __name__ == "__main__":
    main()
