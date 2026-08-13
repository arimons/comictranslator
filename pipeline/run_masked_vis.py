# flood fill + unboxed_text 통합 마스킹 시각화 이미지를 생성합니다.
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = Path(__file__).resolve().parent

try:
    from pipeline.geometry import as_rect as _as_rect, union_rect as _union_rect
    from pipeline.unboxed_grouping import (
        cluster_adjacent_text_groups as _cluster_adjacent_tgs,
        is_noise_text as _is_noise,
        text_group_id as _tg_id,
        text_group_stats as _text_stats,
        text_group_text as _tg_text,
    )
    from pipeline.text_style_metrics import (
        classify_text_group_style as _classify_text_group_style,
        compute_page_style_baseline as _compute_page_style_baseline,
        compute_text_group_style as _compute_text_group_style,
    )
    from pipeline.unboxed_text_detector import extract_unboxed_text_candidate
    from pipeline.bubble_detector import build_ocr_region_sheet
except ImportError:
    from geometry import as_rect as _as_rect, union_rect as _union_rect
    from unboxed_grouping import (
        cluster_adjacent_text_groups as _cluster_adjacent_tgs,
        is_noise_text as _is_noise,
        text_group_id as _tg_id,
        text_group_stats as _text_stats,
        text_group_text as _tg_text,
    )
    from text_style_metrics import (
        classify_text_group_style as _classify_text_group_style,
        compute_page_style_baseline as _compute_page_style_baseline,
        compute_text_group_style as _compute_text_group_style,
    )
    from unboxed_text_detector import extract_unboxed_text_candidate
    from bubble_detector import build_ocr_region_sheet

# flood fill valid region: 파란색
BLUE_FILL = (160, 80, 0)
BLUE_LINE = (220, 140, 40)
BLUE_LABEL = (255, 180, 80)
# unboxed text erase 성공: 노란색
YELLOW_FILL = (0, 160, 220)
YELLOW_LINE = (0, 220, 255)
YELLOW_LABEL = (0, 230, 255)
# unboxed text 감지 실패 (skip): 회색
SKIP_LINE = (120, 120, 120)
# unboxed text 처리 품질 리뷰 필요: 자홍색
REVIEW_LINE = (220, 80, 220)
REVIEW_LABEL = (240, 120, 240)

LABEL_BG = (0, 0, 0)


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _candidate_tg_map(regions: List[Dict]) -> Dict[int, int]:
    """bbox_fallback 영역의 TG ID → region_id 매핑 (edge_barrier 없이 실행하므로 RB 없음)."""
    result: Dict[int, int] = {}
    for r in regions:
        if not r.get("is_valid") and r.get("region_type_guess") == "bbox_fallback":
            for tg_id in r.get("anchor_ids", []):
                result[int(tg_id)] = int(r.get("region_id", 0))
    return result


def _load_saved_regions(path: Path) -> List[Dict]:
    """저장된 regions.json을 run()이 기대하는 형식으로 변환합니다.

    ore1 파이프라인이 생성한 regions.json은 build_ocr_region_sheet()의 반환값과
    필드 이름이 다르므로 (type vs is_valid/region_type_guess) 통일 변환이 필요합니다.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw: List[Dict] = data.get("regions", data) if isinstance(data, dict) else data
    result: List[Dict] = []
    for r in raw:
        r_type = r.get("type", "")
        is_valid = r_type == "bubble"
        result.append({
            "region_id": r.get("region_id", 0),
            "anchor_ids": r.get("anchor_ids", []),
            "is_valid": is_valid,
            # 저장된 type 값 그대로 사용; 유효하지 않으면 bbox_fallback으로 표시
            "region_type_guess": r_type if is_valid else "bbox_fallback",
            "bubble_rect": r.get("bubble_rect"),
            # ore1 형식엔 polygon이 없지만 혹시 있을 경우 대비
            "polygon": r.get("polygon"),
        })
    return result


def _polygon_to_mask(polygon: Optional[List[List[int]]], img_shape: Tuple) -> Optional[np.ndarray]:
    """polygon 좌표 리스트 → full-image numpy mask."""
    if not polygon:
        return None
    mask = np.zeros(img_shape[:2], dtype=np.uint8)
    pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
    cv2.fillPoly(mask, [pts], 255)
    return mask


def _expand_mask(mask: np.ndarray, pixels: int) -> np.ndarray:
    """말풍선 안전지대를 원본 contour보다 약간 넓힙니다."""
    if pixels <= 0:
        return mask.copy()
    kernel_size = pixels * 2 + 1
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    return cv2.dilate(mask, kernel, iterations=1)


def _erode_mask(mask: np.ndarray, pixels: int) -> np.ndarray:
    """말풍선 내부 텍스트 오버레이용 마스크를 경계에서 살짝 줄입니다."""
    if pixels <= 0:
        return mask.copy()
    kernel_size = pixels * 2 + 1
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    return cv2.erode(mask, kernel, iterations=1)


def _draw_label(img: np.ndarray, rect: List[int], text: str, color: Tuple) -> None:
    x, y = int(rect[0]), int(rect[1])
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.6, 2
    (tw, th), bl = cv2.getTextSize(text, font, scale, thick)
    ty = max(th + 4, y - 4)
    cv2.rectangle(img, (x - 2, ty - th - 4), (x + tw + 4, ty + bl + 2), LABEL_BG, -1)
    cv2.putText(img, text, (x, ty), font, scale, color, thick, cv2.LINE_AA)


def _ring_dark_pixels(candidate: Dict, dark_threshold: int, ring_px: int = 18) -> int:
    """lane 바깥 인접 영역에 아직 어두운 획이 있는지 봅니다."""
    roi = candidate["roi"]
    lane_mask = candidate["lane_mask"]
    if roi.size == 0 or lane_mask.size == 0:
        return 0

    kernel = np.ones((ring_px, ring_px), dtype=np.uint8)
    expanded = cv2.dilate(lane_mask, kernel, iterations=1)
    ring = (expanded > 0) & (lane_mask == 0)
    if not np.any(ring):
        return 0

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # 문서의 dark_threshold보다 약간 보수적으로 잡아 외곽 획 후보만 본다.
    return int(np.count_nonzero(ring & (gray < min(210, dark_threshold))))


def _extract_with_lane_retry(
    image: np.ndarray,
    group: Dict | List[Dict],
    pad: int,
    lane_pad: int,
    dark_threshold: int,
) -> Dict:
    """lane 경계 밖 어두운 획이 보이면 TG padding을 넓혀 한 번 더 추출합니다."""
    groups = group if isinstance(group, list) else [group]
    cand = extract_unboxed_text_candidate(
        image, groups, pad=pad, lane_pad=lane_pad, dark_threshold=dark_threshold
    )
    ring_dark = _ring_dark_pixels(cand, dark_threshold)
    cand["lane_retry"] = False
    cand["ring_dark_pixels"] = ring_dark

    if ring_dark < 60:
        return cand

    retry_lane_pad = min(28, lane_pad + 8)
    retry_pad = pad + max(0, retry_lane_pad - lane_pad)
    retry = extract_unboxed_text_candidate(
        image,
        groups,
        pad=retry_pad,
        lane_pad=retry_lane_pad,
        dark_threshold=dark_threshold,
    )
    retry["lane_retry"] = True
    retry["ring_dark_pixels"] = ring_dark
    return retry


def _inpaint_quality(candidate: Dict) -> Dict[str, float | str]:
    """지운 결과가 지저분할 가능성을 수치화합니다."""
    roi = candidate["roi"]
    inpainted = candidate["inpainted"]
    text_mask = candidate["text_mask"] > 0
    lane_mask = candidate["lane_mask"] > 0
    if roi.size == 0:
        return {"quality": "empty", "bg_std": 0.0, "inpaint_std": 0.0, "mask_ratio": 0.0}

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bg = lane_mask & (~text_mask)
    bg_std = float(np.std(gray[bg])) if np.any(bg) else 0.0
    mask_ratio = float(np.count_nonzero(text_mask) / max(1, np.count_nonzero(lane_mask)))

    ex, ey, ew, eh = _as_rect(candidate["erase_rect"])
    rx, ry, _rw, _rh = _as_rect(candidate["roi_rect"])
    lx, ly = max(0, ex - rx), max(0, ey - ry)
    crop = inpainted[ly:ly + eh, lx:lx + ew]
    inpaint_std = 0.0
    if crop.size:
        inpaint_std = float(np.std(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)))

    quality = "clean"
    if inpaint_std >= 55.0 or bg_std >= 35.0:
        quality = "messy_background"
    elif mask_ratio >= 0.70:
        quality = "wide_mask_review"

    return {
        "quality": quality,
        "bg_std": round(bg_std, 2),
        "inpaint_std": round(inpaint_std, 2),
        "mask_ratio": round(mask_ratio, 4),
    }


def run(
    img_num: str,
    pad: int = 60,
    lane_pad: int = 20,
    dark_threshold: int = 214,
    min_mask_pixels: int = 80,
    bubble_protect_pad: int = 10,
    bubble_mask_erode: int = 2,
    alpha: float = 0.38,
    tg_json_path: Optional[Path] = None,
    regions_json_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """마스킹 시각화를 실행합니다.

    Args:
        tg_json_path: TG JSON 경로. None이면 experiment/proof/json/ 기본값 사용.
        regions_json_path: 저장된 regions.json 경로. None이면 build_ocr_region_sheet() 재실행.
            ore1처럼 사전 계산된 regions가 있을 때 이 경로를 전달하면 bubble/unboxed 분류가
            저장된 결과와 일치합니다.
    """
    image_path = ROOT_DIR / "test_image" / f"{img_num}.jpg"
    tg_path = Path(tg_json_path) if tg_json_path is not None else (
        ROOT_DIR / "proof" / "json" / f"{img_num}_text_groups.json"
    )
    out_dir = ROOT_DIR / "proof" / "masked_vis"
    out_dir.mkdir(parents=True, exist_ok=True)

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    groups = _load_json(tg_path)

    if regions_json_path is not None:
        # 사전 계산된 regions 사용 (build_ocr_region_sheet 재실행 없이 저장된 분류 그대로 적용)
        regions = _load_saved_regions(Path(regions_json_path))
    else:
        # edge_barrier 비활성화로 빠르게 flood fill만 실행, mask를 메모리에서 직접 사용
        sheet = build_ocr_region_sheet(
            image_path=str(image_path),
            clusters_json=str(tg_path),
            output_vis_path=None,
            enable_edge_barrier_rescue=False,
        )
        regions = sheet["regions"]
    tg_map = {_tg_id(g, i + 1): g for i, g in enumerate(groups)}

    # ── Step 1: flood fill valid regions → bubble 보호 마스크 준비 ─────────────
    masked = image.copy()
    bubble_base = np.zeros(image.shape[:2], dtype=np.uint8)
    # bubble 픽셀 보호 마스크: unboxed inpaint가 침범하지 못하고, 마지막에 원본 복원
    protected = np.zeros(image.shape[:2], dtype=np.uint8)
    valid_regions = [r for r in regions if r.get("is_valid")]
    for region in valid_regions:
        m = _polygon_to_mask(region.get("polygon"), image.shape)
        if m is not None:
            bubble_base[m > 0] = 255
        else:
            rect = region.get("bubble_rect")
            if rect:
                x, y, w, h = _as_rect(rect)
                cv2.rectangle(bubble_base, (x, y), (x + w, y + h), 255, -1)
    protected[_expand_mask(bubble_base, bubble_protect_pad) > 0] = 255

    # ── Step 2: bbox_fallback TGs → 인접 클러스터 묶음 → unboxed_text inpaint ──
    # bubble TG(valid region 소속)는 eligible에 포함하지 않으므로 클러스터 혼용 없음
    tg_id_to_rid = _candidate_tg_map(regions)
    unboxed_ok: List[Tuple[List[int], Dict]] = []  # ([tg_ids], candidate)
    unboxed_skip: List[int] = []
    unboxed_skip_reasons: Dict[int, str] = {}
    unboxed_retry: List[int] = []
    unboxed_review: List[int] = []
    unboxed_quality: Dict[str, Dict[str, float | str]] = {}

    style_baseline = _compute_page_style_baseline(image, groups)
    style_decisions_by_tg: Dict[int, Dict[str, Any]] = {}
    for idx, group in enumerate(groups, start=1):
        tid = _tg_id(group, idx)
        style_decisions_by_tg[tid] = _classify_text_group_style(
            _compute_text_group_style(image, group),
            style_baseline,
        )

    # 노이즈와 스타일 제외 후 eligible TG 수집
    eligible: List[Tuple[int, Dict]] = []
    for idx, group in enumerate(groups, start=1):
        tid = _tg_id(group, idx)
        if tid not in tg_id_to_rid:
            continue
        if _is_noise(_tg_text(group)):
            unboxed_skip.append(tid)
            unboxed_skip_reasons[tid] = "noise"
            continue
        style_decision = style_decisions_by_tg.get(tid, {})
        if style_decision.get("include_policy") == "exclude":
            unboxed_skip.append(tid)
            unboxed_skip_reasons[tid] = str(style_decision.get("style_class", "style_exclude"))
            continue
        eligible.append((tid, group))

    # 인접 TG 클러스터별 통합 처리 (외접 bbox 단일 lane_mask)
    for cluster in _cluster_adjacent_tgs(eligible):
        tids = [t for t, _ in cluster]

        cand = _extract_with_lane_retry(
            image, [g for _, g in cluster], pad=pad, lane_pad=lane_pad, dark_threshold=dark_threshold
        )
        if cand["mask_pixels"] < min_mask_pixels or cand["erase_rect"][2] <= 0:
            unboxed_skip.extend(tids)
            for tid in tids:
                unboxed_skip_reasons[tid] = "low_mask_pixels"
            continue

        # text_mask 픽셀만 inpaint 적용, bubble protected 픽셀은 건너뜀
        rx, ry, rw, rh = cand["roi_rect"]
        text_mask = cand["text_mask"]
        roi_slice = masked[ry : ry + rh, rx : rx + rw]
        roi_protected = protected[ry : ry + rh, rx : rx + rw]
        safe_text_mask = np.where((text_mask > 0) & (roi_protected == 0), 255, 0).astype(np.uint8)
        safe_inpainted = cv2.inpaint(
            cand["roi"],
            safe_text_mask,
            int(cand.get("inpaint_radius", 7)),
            cv2.INPAINT_TELEA,
        )
        apply_mask = safe_text_mask > 0
        roi_slice[apply_mask] = safe_inpainted[apply_mask]
        unboxed_ok.append((tids, cand))
        if cand.get("lane_retry"):
            unboxed_retry.extend(tids)
        quality = _inpaint_quality(cand)
        for tid in tids:
            unboxed_quality[str(tid)] = quality
        if quality["quality"] != "clean":
            unboxed_review.extend(tids)

    if np.any(protected):
        masked[protected > 0] = image[protected > 0]
    bubble_overlay = _erode_mask(bubble_base, bubble_mask_erode)
    masked[bubble_overlay > 0] = (255, 255, 255)

    # ── Step 3: vis 오버레이 생성 (원본 이미지 위에 반투명 색칠, 버블 경계 보존) ──
    overlay = image.copy()

    # flood fill 영역: 파란색 fill (polygon 기반)
    for region in valid_regions:
        m = _polygon_to_mask(region.get("polygon"), image.shape)
        if m is not None:
            overlay[m > 0] = BLUE_FILL
        else:
            rect = region.get("bubble_rect")
            if rect:
                x, y, w, h = _as_rect(rect)
                cv2.rectangle(overlay, (x, y), (x + w, y + h), BLUE_FILL, -1)

    # unboxed text erase_rect: 노란색 fill
    for _tids, cand in unboxed_ok:
        ex, ey, ew, eh = _as_rect(cand["erase_rect"])
        cv2.rectangle(overlay, (ex, ey), (ex + ew, ey + eh), YELLOW_FILL, -1)

    vis = cv2.addWeighted(overlay, alpha, image, 1.0 - alpha, 0)

    # 경계선 & 레이블
    for region in valid_regions:
        rid = region.get("region_id", "?")
        rect = region.get("bubble_rect")
        m = _polygon_to_mask(region.get("polygon"), image.shape)
        if m is not None:
            contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(vis, contours, -1, BLUE_LINE, 3, cv2.LINE_AA)
        elif rect:
            x, y, w, h = _as_rect(rect)
            cv2.rectangle(vis, (x, y), (x + w, y + h), BLUE_LINE, 3, cv2.LINE_AA)
        if rect:
            _draw_label(vis, rect, f"R{rid}", BLUE_LABEL)

    for tids, cand in unboxed_ok:
        ex, ey, ew, eh = _as_rect(cand["erase_rect"])
        cv2.rectangle(vis, (ex, ey), (ex + ew, ey + eh), YELLOW_LINE, 3, cv2.LINE_AA)
        label = "+".join(f"TG{t}" for t in tids)
        _draw_label(vis, cand["erase_rect"], label, YELLOW_LABEL)
        if any(t in unboxed_review for t in tids):
            cv2.rectangle(vis, (ex, ey), (ex + ew, ey + eh), REVIEW_LINE, 5, cv2.LINE_AA)
            _draw_label(vis, [ex, ey + eh + 8, ew, 24], "review", REVIEW_LABEL)

    for tid in unboxed_skip:
        tg = tg_map.get(tid)
        if not tg:
            continue
        rect = _as_rect(tg.get("tight_rect", tg.get("bounding_rect", [0, 0, 0, 0])))
        if rect[2] <= 0:
            continue
        x, y, w, h = rect
        cv2.rectangle(vis, (x, y), (x + w, y + h), SKIP_LINE, 1, cv2.LINE_AA)
        _draw_label(vis, rect, f"TG{tid}:skip", SKIP_LINE)

    masked_path = str(out_dir / f"{img_num}_masked.jpg")
    vis_path = str(out_dir / f"{img_num}_masked_vis.jpg")
    summary_path = str(out_dir / f"{img_num}_summary.json")
    cv2.imwrite(masked_path, masked)
    cv2.imwrite(vis_path, vis)

    summary = {
        "flood_fill_valid": len(valid_regions),
        "unboxed_ok": len(unboxed_ok),
        "unboxed_skip": len(unboxed_skip),
        "tg_ids_ok": [t for tids, _ in unboxed_ok for t in tids],
        "tg_ids_skip": unboxed_skip,
        "tg_ids_lane_retry": unboxed_retry,
        "tg_ids_review": unboxed_review,
        "tg_skip_reasons": unboxed_skip_reasons,
        "tg_quality": unboxed_quality,
        "bubble_protect_pad": bubble_protect_pad,
        "bubble_mask_erode": bubble_mask_erode,
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({"img": img_num, "masked": masked_path, "vis": vis_path, "summary_json": summary_path, "summary": summary}, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="flood fill + unboxed_text 통합 마스킹 시각화")
    parser.add_argument("img_num", nargs="*")
    parser.add_argument("--all", action="store_true", help="test_image/ 전체 이미지 처리")
    parser.add_argument("--start", type=int, default=3)
    parser.add_argument("--end", type=int, default=160)
    parser.add_argument("--pad", type=int, default=60)
    parser.add_argument("--lane-pad", type=int, default=20)
    parser.add_argument("--dark-threshold", type=int, default=214)
    parser.add_argument("--min-mask-pixels", type=int, default=80)
    parser.add_argument("--bubble-protect-pad", type=int, default=10)
    parser.add_argument("--bubble-mask-erode", type=int, default=2)
    parser.add_argument("--alpha", type=float, default=0.38)
    args = parser.parse_args()

    if args.all:
        img_dir = ROOT_DIR / "test_image"
        targets = sorted(
            p.stem for p in img_dir.glob("*.jpg")
            if p.stem.isdigit() and args.start <= int(p.stem) <= args.end
        )
    else:
        targets = args.img_num

    for img_num in targets:
        try:
            run(img_num, pad=args.pad, lane_pad=args.lane_pad,
                dark_threshold=args.dark_threshold,
                min_mask_pixels=args.min_mask_pixels,
                bubble_protect_pad=args.bubble_protect_pad,
                bubble_mask_erode=args.bubble_mask_erode,
                alpha=args.alpha)
        except Exception as e:
            print(f"❌ {img_num}: {e}")


if __name__ == "__main__":
    main()
