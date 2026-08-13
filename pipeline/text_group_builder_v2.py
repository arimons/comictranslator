# Cloud Vision OCR → TextGroup 변환 (v2: Line Fragment Repair + 동적 y_tolerance)
#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


Rect = List[int]
FORBIDDEN_CV_TEXT_PATTERNS = {
    "rawnet",
    "dlrawnet",
    "netradl",
}
_HIRAGANA = set("ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとど"
                "なにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをん"
                "ゔー")
_KATAKANA = set("ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトド"
                "ナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲン"
                "ヴー")


def is_forbidden_cv_text(text: str) -> bool:
    """CV OCR 단계에서 제거해야 하는 출처/워터마크 텍스트인지 판별한다."""
    normalized = "".join(ch.lower() for ch in str(text or "") if ch.isalnum())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in FORBIDDEN_CV_TEXT_PATTERNS)


def _is_kana_text(text: str) -> bool:
    chars = [ch for ch in str(text or "").strip() if ch not in " 　・･…‥、。"]
    return bool(chars) and all(ch in _HIRAGANA or ch in _KATAKANA for ch in chars)


def _is_single_cjk_text(text: str) -> bool:
    chars = [ch for ch in str(text or "").strip() if ch not in " 　"]
    return len(chars) == 1 and "\u4e00" <= chars[0] <= "\u9fff"


# ==========================================
# CV JSON 로드 및 블록 변환
# ==========================================

def load_cv_json(filepath: str) -> List[Dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _convert_bbox_format(box_2d: List[int]) -> Tuple[int, int, int, int]:
    """[ymin, xmin, ymax, xmax] → (x, y, width, height)"""
    ymin, xmin, ymax, xmax = box_2d
    return (xmin, ymin, xmax - xmin, ymax - ymin)


def create_text_blocks(cv_data: List[Dict]) -> List[Dict]:
    blocks = []
    for item in cv_data:
        if is_forbidden_cv_text(item.get("label", "")):
            continue
        x, y, w, h = _convert_bbox_format(item["box_2d"])
        blocks.append({"text": item["label"], "bbox": [x, y, w, h]})
    return blocks


# ==========================================
# 2순위: 글자 높이 통계 기반 동적 y_tolerance
# ==========================================

def compute_height_stats(blocks: List[Dict]) -> Tuple[float, float]:
    """블록 높이의 median / 75퍼센타일 반환."""
    heights = [b["bbox"][3] for b in blocks if b["bbox"][3] > 0]
    if not heights:
        return 20.0, 20.0
    return float(np.median(heights)), float(np.percentile(heights, 75))


def compute_y_tolerance(image_height: int, median_h: float, k: float = 0.015, a: float = 1.5) -> float:
    """
    이미지 비율 + 글자 높이 통계 혼합.
    기본: max(image_height * 0.015, median_h * 1.5)
    - k: 이미지 높이 계수 (기존 0.015 유지)
    - a: 글자 높이 계수 (1.5 ~ 2.0 권장)
    """
    return max(image_height * k, median_h * a)


# ==========================================
# Line 오브젝트 생성/병합 유틸
# ==========================================

def _create_line_object(blocks: List[Dict]) -> Dict:
    all_x, all_y = [], []
    for block in blocks:
        x, y, w, h = block["bbox"]
        all_x.extend([x, x + w])
        all_y.extend([y, y + h])

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    line_bbox = [min_x, min_y, max_x - min_x, max_y - min_y]

    x_groups: List[Dict] = []
    for block in sorted(blocks, key=lambda b: -b["bbox"][0]):
        x = block["bbox"][0]
        added = False
        for group in x_groups:
            if abs(group["x"] - x) <= 5:
                group["blocks"].append(block)
                added = True
                break
        if not added:
            x_groups.append({"x": x, "blocks": [block]})

    blocks_sorted = []
    for group in x_groups:
        group["blocks"].sort(key=lambda b: b["bbox"][1])
        blocks_sorted.extend(group["blocks"])

    text = "".join(b["text"] for b in blocks_sorted)
    w, h = line_bbox[2], line_bbox[3]

    return {
        "bboxes": blocks_sorted,
        "bbox": line_bbox,
        "text": text,
        "orientation": "vertical" if h > w * 1.2 else "horizontal",
    }


def _merge_two_lines(line1: Dict, line2: Dict) -> Dict:
    """두 line dict를 하나로 병합."""
    bboxes1 = line1.get("bboxes", [])
    bboxes2 = line2.get("bboxes", [])
    all_bboxes = bboxes1 + bboxes2

    all_x, all_y = [], []
    for b in all_bboxes:
        x, y, w, h = b["bbox"]
        all_x.extend([x, x + w])
        all_y.extend([y, y + h])

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    merged_bbox = [min_x, min_y, max_x - min_x, max_y - min_y]

    text = line1.get("text", "") + line2.get("text", "")
    w, h = merged_bbox[2], merged_bbox[3]

    return {
        "bboxes": all_bboxes,
        "bbox": merged_bbox,
        "text": text,
        "orientation": "vertical" if h > w * 1.2 else "horizontal",
    }


# ==========================================
# Pass 1: TextFragment → TextLine (기존과 동일)
# ==========================================

def pass1_create_lines(blocks: List[Dict], avg_width: float, y_tolerance: float) -> List[Dict]:
    """같은 X 그룹에서 Y 축으로만 병합."""
    if not blocks:
        return []

    blocks_sorted = sorted(blocks, key=lambda b: -b["bbox"][0])
    visited: set = set()
    lines = []

    for seed_idx, seed_block in enumerate(blocks_sorted):
        if seed_idx in visited:
            continue

        queue = [seed_block]
        current_line_blocks = [seed_block]
        visited.add(seed_idx)

        while queue:
            curr = queue.pop(0)
            cx, cy, cw, ch = curr["bbox"]

            for cand_idx, candidate in enumerate(blocks_sorted):
                if cand_idx in visited:
                    continue
                x, y, w, h = candidate["bbox"]
                x_diff = abs(x - cx)
                y_gap = max(0, y - (cy + ch), cy - (y + h))
                narrow_candidate = min(cw, w) < avg_width * 0.4

                if x_diff <= avg_width * 0.33 and y_gap <= y_tolerance and not narrow_candidate:
                    current_line_blocks.append(candidate)
                    queue.append(candidate)
                    visited.add(cand_idx)

        lines.append(_create_line_object(current_line_blocks))

    return lines


# ==========================================
# Pass 1.5: Line Fragment Repair (1순위 - 신규)
# ==========================================

def repair_line_fragments(
    lines: List[Dict],
    x_tol: float,
    merge_gap: float,
    avg_width: float,
    debug: bool = True,
) -> Tuple[List[Dict], Dict]:
    """
    Pass1 이후 같은 세로줄(x ≈ 같음)의 line 조각들을 위아래로 재병합.

    merge_gap 권장값: max(y_tolerance * 2.0, median_h * 2.0)

    가드:
      - gap > merge_gap: 간격 초과
      - gap > avg_width * 1.5: 한 글자보다 큰 빈칸은 누락 글자로 보지 않음
      - width 비율 > 2.5: 후리가나/효과음 방지
      - 병합 후 폭 > avg_width * 3: 폭 폭발 방지
      - 간격 구간에 다른 x열의 line이 50% 이상 겹침: 다른 대사 영역 침범 방지

    반환: (병합된 lines, diagnostics dict)
    """
    if not lines:
        return lines, {}

    # x 기준 우→좌 정렬 후 버킷 그룹화
    lines_by_x = sorted(lines, key=lambda l: -l["bbox"][0])
    buckets: List[Dict] = []

    for line in lines_by_x:
        lx = line["bbox"][0]
        placed = False
        for bucket in buckets:
            if abs(lx - bucket["x"]) <= x_tol:
                bucket["lines"].append(line)
                placed = True
                break
        if not placed:
            buckets.append({"x": lx, "lines": [line]})

    merge_attempts = 0
    merge_success = 0
    rejected = {
        "horizontal": 0,
        "gap": 0,
        "large_blank_gap": 0,
        "narrow_large_y_gap": 0,
        "width_ratio": 0,
        "post_width": 0,
        "intervening": 0,
    }
    merged_all: List[Dict] = []

    for bucket in buckets:
        bucket_lines = sorted(bucket["lines"], key=lambda l: l["bbox"][1])
        result = [bucket_lines[0]]

        for curr in bucket_lines[1:]:
            prev = result[-1]
            px, py, pw, ph = prev["bbox"]
            cx, cy, cw, ch = curr["bbox"]
            gap = cy - (py + ph)
            merge_attempts += 1

            # Guard 0: horizontal line은 세로 열 조각 병합 대상이 아님
            if (prev.get("orientation") == "horizontal"
                    or curr.get("orientation") == "horizontal"):
                rejected["horizontal"] += 1
                result.append(curr)
                continue

            # Guard 1: y 간격 초과
            if gap > merge_gap:
                rejected["gap"] += 1
                result.append(curr)
                continue

            if min(pw, cw) < avg_width * 0.65 and gap > avg_width * 1.5:
                rejected["narrow_large_y_gap"] += 1
                result.append(curr)
                continue

            if gap > avg_width * 1.5:
                rejected["large_blank_gap"] += 1
                result.append(curr)
                continue

            # Guard 2: 폭 비율 이상 (후리가나·효과음 방지)
            w_ratio = max(pw, cw) / max(min(pw, cw), 1)
            if w_ratio > 2.5:
                rejected["width_ratio"] += 1
                result.append(curr)
                continue

            # Guard 3: 병합 후 폭 과대
            merged_w = max(px + pw, cx + cw) - min(px, cx)
            if merged_w > avg_width * 3:
                rejected["post_width"] += 1
                result.append(curr)
                continue

            # Guard 4: 간격 구간에 완전히 다른 x 영역의 line이 길게 겹치는지
            # 판단 기준: avg_width * 4 이상 떨어진 line만 "다른 말풍선"으로 봄
            # (인접 열은 같은 말풍선 내일 가능성이 높으므로 무시)
            gap_top = py + ph
            gap_bottom = cy
            gap_height = gap_bottom - gap_top
            has_intervening = False
            if gap_height > 0:
                this_cx = px + pw / 2
                far_threshold = avg_width * 4  # 인접 열은 같은 말풍선으로 간주
                for other in lines:
                    ox, oy, ow, oh = other["bbox"]
                    other_cx = ox + ow / 2
                    if abs(other_cx - this_cx) <= far_threshold:
                        continue  # 같은 말풍선 영역 내 (인접 열 포함)
                    if ow < avg_width * 0.4:
                        continue
                    overlap = min(oy + oh, gap_bottom) - max(oy, gap_top)
                    if overlap > gap_height * 0.5:
                        has_intervening = True
                        break

            if has_intervening:
                rejected["intervening"] += 1
                result.append(curr)
                continue

            # 병합 실행
            merge_success += 1
            result[-1] = _merge_two_lines(prev, curr)

        merged_all.extend(result)

    diagnostics = {
        "before": len(lines),
        "after": len(merged_all),
        "attempts": merge_attempts,
        "success": merge_success,
        "rejected": rejected,
    }

    if debug:
        print(
            f"  [Repair] lines {len(lines)} → {len(merged_all)} "
            f"(merge {merge_success}/{merge_attempts}, "
            f"rej: horiz={rejected['horizontal']} gap={rejected['gap']} blank_gap={rejected['large_blank_gap']} "
            f"narrow_gap={rejected['narrow_large_y_gap']} "
            f"wratio={rejected['width_ratio']} postw={rejected['post_width']} "
            f"interv={rejected['intervening']})"
        )

    return merged_all, diagnostics


# ==========================================
# Furigana 필터 (기존과 동일)
# ==========================================

def filter_furigana_lines(lines: List[Dict], avg_width: float, image_height: float = 1920.0) -> List[Dict]:
    """인접 Line 대비 width ≤ 60% + Y 범위 겹침인 라인을 후리가나로 제거."""
    if not lines:
        return lines

    lines_sorted = sorted(lines, key=lambda l: -l["bbox"][0])
    filtered = []
    remove_indices = set()

    y_tol = image_height * 0.026        # 50px @ 1920px
    x_search_limit = avg_width * 4.0    # 120px @ avg_width=30px

    for i, line in enumerate(lines_sorted):
        lx, ly, lw, lh = line["bbox"]
        line_y_end = ly + lh
        is_furigana = False

        for j in range(i + 1, len(lines_sorted)):
            left = lines_sorted[j]
            left_x, left_y, left_w, left_h = left["bbox"]
            left_y_end = left_y + left_h

            y_overlap = not (line_y_end < left_y - y_tol or ly > left_y_end + y_tol)

            if y_overlap:
                if lw <= left_w * 0.60:
                    is_furigana = True
                    break

            if left_x < lx - x_search_limit:
                break

        if not is_furigana:
            filtered.append(line)

    # 가로로 붙은 kana reading + 단일 kanji base는 기존 폭 비율 규칙을 빠져나가는 ruby pair다.
    for i, line in enumerate(filtered):
        lx, ly, lw, lh = line["bbox"]
        text = line.get("text", "").strip()
        if line.get("orientation") != "horizontal" or not _is_kana_text(text) or len(text) > 4:
            continue
        if lh > max(avg_width * 0.95, image_height * 0.018):
            continue

        for j, other in enumerate(filtered):
            if i == j:
                continue
            ox, oy, ow, oh = other["bbox"]
            if other.get("orientation") != "horizontal" or not _is_single_cjk_text(other.get("text", "")):
                continue
            y_overlap = min(ly + lh, oy + oh) - max(ly, oy)
            x_gap = max(0, ox - (lx + lw), lx - (ox + ow))
            combined_w = max(lx + lw, ox + ow) - min(lx, ox)
            if (
                y_overlap >= min(lh, oh) * 0.60
                and x_gap <= avg_width * 0.35
                and combined_w <= avg_width * 2.20
                and max(lh, oh) <= avg_width * 1.05
            ):
                remove_indices.update({i, j})
                break

    if remove_indices:
        filtered = [line for idx, line in enumerate(filtered) if idx not in remove_indices]

    print(f"  Furigana filter: {len(lines)} -> {len(filtered)} lines")
    return filtered


# ==========================================
# Pass 2: TextLine → TextGroup (기존과 동일)
# ==========================================

def pass2_create_clusters(lines: List[Dict], avg_width: float, y_tolerance: float) -> List[List[Dict]]:
    """4-stage BFS: 같은 말풍선 내 여러 줄을 하나의 TextGroup으로 묶음."""
    if not lines:
        return []

    dynamic_threshold = avg_width * 1.5
    lines_sorted = sorted(lines, key=lambda l: -l["bbox"][0])
    visited: set = set()
    clusters = []

    for seed_idx, seed_line in enumerate(lines_sorted):
        if seed_idx in visited:
            continue

        queue = [seed_line]
        current_cluster = [seed_line]
        visited.add(seed_idx)

        while queue:
            curr = queue.pop(0)
            cx, cy, cw, ch = curr["bbox"]
            curr_text = curr.get("text", "").strip()

            for cand_idx, candidate in enumerate(lines_sorted):
                if cand_idx in visited:
                    continue

                lx, ly, lw, lh = candidate["bbox"]

                if curr.get("orientation") == "horizontal" and candidate.get("orientation") == "horizontal":
                    y_start_diff = abs(ly - cy)
                    x_gap = max(0, lx - (cx + cw), cx - (lx + lw))
                    if y_start_diff <= y_tolerance and x_gap <= dynamic_threshold:
                        current_cluster.append(candidate)
                        queue.append(candidate)
                        visited.add(cand_idx)
                    continue

                y_gap = max(0, ly - (cy + ch), cy - (ly + lh))

                x_diff = abs(lx - cx)
                if x_diff <= avg_width * 0.33:
                    effective_tolerance = y_tolerance
                    if curr_text and curr_text[-1] in ("…", "!", "?", "！", "？", "。", "っ", "た", "だ"):
                        effective_tolerance = y_tolerance * 1.5
                    if y_gap <= effective_tolerance:
                        current_cluster.append(candidate)
                        queue.append(candidate)
                        visited.add(cand_idx)

        clusters.append(current_cluster)

    return clusters


# ==========================================
# Pass 2.5 (선택): 보수적 X방향 열 확장
# ==========================================

def _cluster_bbox(cluster: List[Dict]) -> List[int]:
    xs = [l["bbox"][0] for l in cluster] + [l["bbox"][0] + l["bbox"][2] for l in cluster]
    ys = [l["bbox"][1] for l in cluster] + [l["bbox"][1] + l["bbox"][3] for l in cluster]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def expand_x_adjacent(
    clusters: List[List[Dict]],
    avg_width: float,
    x_threshold: float,
    y_overlap_min: float = 0.80,
    max_merged_w_mult: float = 5.0,
    debug: bool = True,
) -> List[List[Dict]]:
    """
    보수적 X방향 열(column) 병합. Consolidate보다 훨씬 엄격한 조건으로 동작.

    조건:
      - x_gap ≤ x_threshold (권장: avg_width * 0.75 — 열 간 간격 1자 미만만 허용)
      - Y overlap ≥ y_overlap_min * min(h1, h2) (기본 80%)
      - 병합 후 폭 ≤ avg_width * max_merged_w_mult (기본 5배)
      - horizontal cluster는 대상에서 제외

    Consolidate와의 차이:
      - member_clusters 추적 없이 cluster bbox만 비교 (단순)
      - x_threshold가 훨씬 작아 과병합 방지 (예: TG8 "あったとでも?" + "スメラギが" 같은 오병합)
      - 수렴할 때까지 반복 (연속 3개+ 열 병합 지원)
    """
    if not clusters:
        return clusters

    max_merged_w = avg_width * max_merged_w_mult
    n_before = len(clusters)

    def _is_horizontal(cluster: List[Dict]) -> bool:
        h_count = sum(1 for l in cluster if l.get("orientation") == "horizontal")
        return h_count > len(cluster) / 2

    changed = True
    while changed:
        changed = False
        clusters = sorted(clusters, key=lambda c: -_cluster_bbox(c)[0])
        merged_flags = [False] * len(clusters)
        result = []

        for i in range(len(clusters)):
            if merged_flags[i]:
                continue

            current = clusters[i]
            if _is_horizontal(current):
                result.append(current)
                continue

            bi_x, bi_y, bi_w, bi_h = _cluster_bbox(current)

            for j in range(i + 1, len(clusters)):
                if merged_flags[j]:
                    continue
                cj = clusters[j]
                if _is_horizontal(cj):
                    continue

                cj_x, cj_y, cj_w, cj_h = _cluster_bbox(cj)
                x_gap = max(0, cj_x - (bi_x + bi_w), bi_x - (cj_x + cj_w))
                if x_gap > x_threshold:
                    continue

                y_top = max(bi_y, cj_y)
                y_bot = min(bi_y + bi_h, cj_y + cj_h)
                overlap = max(0, y_bot - y_top)
                min_h = min(bi_h, cj_h)
                if min_h == 0 or overlap / min_h < y_overlap_min:
                    continue

                merged_w = max(bi_x + bi_w, cj_x + cj_w) - min(bi_x, cj_x)
                if merged_w > max_merged_w:
                    continue

                # 병합
                current = current + cj
                merged_flags[j] = True
                bi_x, bi_y, bi_w, bi_h = _cluster_bbox(current)
                changed = True

            result.append(current)

        clusters = result

    if debug:
        print(f"  [XExpand] {n_before} → {len(clusters)} clusters "
              f"(x_thr={x_threshold:.1f}, y_ovlp≥{y_overlap_min:.0%})")

    return clusters


# ==========================================
# Pass 3 (선택): Cluster Consolidation
# ==========================================

def consolidate_clusters(
    clusters: List[List[Dict]],
    avg_width: float,
    y_tolerance: float,
    debug: bool = True,
) -> List[List[Dict]]:
    """
    Pass2 결과 cluster 중 같은 대사인데 분리된 것을 보수적으로 병합.
    조건:
      - Y 범위 겹침 ≥ 50%
      - X 간격 ≤ avg_width * 2.5
      - 병합 후 폭 ≤ avg_width * 6

    break 없이 반복하여 TG11+12+13처럼 연속 3개+ 병합도 지원.
    """
    if not clusters:
        return clusters

    def cluster_bbox(cluster: List[Dict]) -> Rect:
        xs = [l["bbox"][0] for l in cluster] + [l["bbox"][0] + l["bbox"][2] for l in cluster]
        ys = [l["bbox"][1] for l in cluster] + [l["bbox"][1] + l["bbox"][3] for l in cluster]
        return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]

    def is_horizontal_cluster(cluster: List[Dict]) -> bool:
        """cluster 내 line 과반수가 horizontal이면 True."""
        h_count = sum(1 for l in cluster if l.get("orientation") == "horizontal")
        return h_count > len(cluster) / 2

    dynamic_threshold = avg_width * 1.5  # 멀리 떨어진 cluster 오병합 방지 (2.5→2.0→1.5)
    n = len(clusters)
    merged_flags = [False] * n
    member_clusters: List[List[List[Dict]]] = [[cluster] for cluster in clusters]
    result = []
    merge_count = 0

    def _repr(cluster: List[Dict]) -> str:
        text = "".join(l.get("text", "") for l in cluster)
        safe = text[:20].encode("utf-8", errors="replace").decode("utf-8")
        return f"{cluster_bbox(cluster)} '{safe}'"

    def _merge_pair_allowed(base_cluster: List[Dict], candidate_cluster: List[Dict]) -> Tuple[bool, Dict[str, float]]:
        bi_x, bi_y, bi_w, bi_h = cluster_bbox(base_cluster)
        bj_x, bj_y, bj_w, bj_h = cluster_bbox(candidate_cluster)

        x_gap = max(0, bj_x - (bi_x + bi_w), bi_x - (bj_x + bj_w))
        if x_gap > dynamic_threshold:
            return False, {"x_gap": x_gap}

        y_top = max(bi_y, bj_y)
        y_bot = min(bi_y + bi_h, bj_y + bj_h)
        overlap_y = max(0, y_bot - y_top)
        min_h = min(bi_h, bj_h)

        narrow_w = min(bi_w, bj_w)
        if narrow_w < avg_width and x_gap > avg_width:
            return False, {"x_gap": x_gap, "overlap_y": overlap_y, "min_h": min_h}

        if (narrow_w < avg_width
                and max(bi_w, bj_w) >= avg_width * 2
                and min_h > avg_width * 4
                and overlap_y < min_h * 0.98):
            return False, {"x_gap": x_gap, "overlap_y": overlap_y, "min_h": min_h}

        if min_h == 0 or overlap_y / min_h < 0.5:
            return False, {"x_gap": x_gap, "overlap_y": overlap_y, "min_h": min_h}

        overlap_ratio = overlap_y / min_h
        if x_gap > avg_width * 0.75 and overlap_ratio < 0.75:
            return False, {"x_gap": x_gap, "overlap_y": overlap_y, "min_h": min_h}

        return True, {"x_gap": x_gap, "overlap_y": overlap_y, "min_h": min_h}

    for i in range(n):
        if merged_flags[i]:
            continue
        # horizontal cluster는 Consolidation 대상이 아님
        if is_horizontal_cluster(clusters[i]):
            result.append(clusters[i])
            continue
        ci_x, ci_y, ci_w, ci_h = cluster_bbox(clusters[i])

        # break 없이 순회하여 하나의 seed(i)에 여러 cluster 병합 가능
        for j in range(i + 1, n):
            if merged_flags[j]:
                continue
            pair_matches = [
                _merge_pair_allowed(member, clusters[j])
                for member in member_clusters[i]
            ]
            allowed_pairs = [diag for allowed, diag in pair_matches if allowed]
            if not allowed_pairs:
                continue

            cj_x, cj_y, cj_w, cj_h = cluster_bbox(clusters[j])
            merged_w = max(ci_x + ci_w, cj_x + cj_w) - min(ci_x, cj_x)
            if merged_w > avg_width * 7:
                continue

            best_diag = min(allowed_pairs, key=lambda d: d.get("x_gap", 0))
            if debug:
                msg = (
                    f"    [Consolidate] merge i={i} {_repr(clusters[i])} <- j={j} {_repr(clusters[j])}  "
                    f"x_gap={best_diag.get('x_gap', 0):.0f} "
                    f"y_overlap={best_diag.get('overlap_y', 0):.0f}/{best_diag.get('min_h', 0):.0f}"
                )
                try:
                    print(msg)
                except UnicodeEncodeError:
                    print(msg.encode("ascii", errors="replace").decode("ascii"))

            merged_flags[j] = True
            merge_count += 1
            clusters[i] = clusters[i] + clusters[j]
            member_clusters[i].extend(member_clusters[j])
            # bbox 재계산 후 계속 순회 (연속 병합 지원)
            ci_x, ci_y, ci_w, ci_h = cluster_bbox(clusters[i])

        result.append(clusters[i])

    if debug:
        print(f"  [Consolidate] clusters {n} → {len(result)} (merged={merge_count})")

    return result


# ==========================================
# TextGroup 포맷터 (기존과 동일)
# ==========================================

def _as_rect(rect: List[int]) -> Rect:
    x, y, w, h = [int(v) for v in rect[:4]]
    return [x, y, max(0, w), max(0, h)]


def _union_rect(rects: List[Rect]) -> Rect:
    valid = [r for r in rects if len(r) >= 4]
    if not valid:
        return [0, 0, 0, 0]
    x1 = min(int(r[0]) for r in valid)
    y1 = min(int(r[1]) for r in valid)
    x2 = max(int(r[0]) + int(r[2]) for r in valid)
    y2 = max(int(r[1]) + int(r[3]) for r in valid)
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _text_fragments(line: Dict[str, Any]) -> List[Dict[str, Any]]:
    fragments = []
    for idx, fragment in enumerate(line.get("bboxes", []), start=1):
        fragments.append({
            "text_fragment_id": idx,
            "text": fragment.get("text", ""),
            "bbox": _as_rect(fragment.get("bbox", [0, 0, 0, 0])),
        })
    return fragments


def _merge_line_group(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(lines) == 1:
        return lines[0]

    sorted_lines = sorted(lines, key=lambda line: line["bbox"][1])
    fragments: List[Dict[str, Any]] = []
    fragment_boxes: List[Rect] = []

    for line in sorted_lines:
        source_fragments = line.get("bboxes", [])
        if source_fragments:
            fragments.extend(source_fragments)
            fragment_boxes.extend(_as_rect(f.get("bbox", [0, 0, 0, 0])) for f in source_fragments)
        else:
            fragments.append({"text": line.get("text", ""), "bbox": line.get("bbox", [0, 0, 0, 0])})
            fragment_boxes.append(_as_rect(line.get("bbox", [0, 0, 0, 0])))

    rect = _union_rect(fragment_boxes or [_as_rect(line["bbox"]) for line in sorted_lines])
    text = "".join(line.get("text", "") for line in sorted_lines)
    orientation = "vertical" if rect[3] > rect[2] * 1.2 else "horizontal"

    return {"text": text, "bbox": rect, "bboxes": fragments, "orientation": orientation}


def _merge_adjacent_text_lines(lines: List[Dict[str, Any]], y_tolerance: float, avg_width: float = 30.0) -> List[Dict[str, Any]]:
    if not lines:
        return []

    merged = []
    current = [lines[0]]

    for line in lines[1:]:
        prev = current[-1]
        x, y, w, h = line["bbox"]
        px, py, pw, ph = prev["bbox"]
        x_diff = abs(x - px)
        y_gap = max(0, y - (py + ph), py - (y + h))

        if x_diff <= avg_width * 0.33 and y_gap <= y_tolerance:
            current.append(line)
            continue

        merged.append(_merge_line_group(current))
        current = [line]

    merged.append(_merge_line_group(current))
    return merged


def _fallback_rect(lines: List[Dict[str, Any]], avg_width: float) -> Rect:
    all_x: List[int] = []
    all_y: List[int] = []

    for idx, line in enumerate(lines):
        x, y, w, h = _as_rect(line["bbox"])
        text = line.get("text", "").strip()
        if text and text.endswith(("た", "だ", "?", ".", "！", "。", "つ")):
            h += 5

        if idx == 0:
            all_x.extend([x, x + w + int(w * 0.3)])
        else:
            all_x.extend([x, x + w])
        all_y.extend([y, y + h])

    if not all_x:
        return [0, 0, 0, 0]

    y_padding = int(avg_width * 0.15)
    x1 = min(all_x)
    y1 = max(0, min(all_y) - y_padding)
    x2 = max(all_x)
    y2 = max(all_y) + y_padding
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _format_text_groups(groups: List[List[Dict[str, Any]]], avg_width: float) -> List[Dict[str, Any]]:
    result = []
    line_merge_tolerance = avg_width * 1.5

    for group in groups:
        sorted_lines = sorted(group, key=lambda line: -line["bbox"][0])
        merged_lines = _merge_adjacent_text_lines(sorted_lines, line_merge_tolerance, avg_width)
        tight_rect = _union_rect([_as_rect(line["bbox"]) for line in merged_lines])
        fallback_rect = _fallback_rect(merged_lines, avg_width)

        lines_output = []
        for line_idx, line in enumerate(merged_lines, start=1):
            lines_output.append({
                "text_line_id": line_idx,
                "text": line.get("text", ""),
                "bbox": _as_rect(line["bbox"]),
                "fragments": _text_fragments(line),
            })

        result.append({
            "text_group_id": 0,
            "cluster_id": 0,
            "orientation": "vertical",
            "text": "".join(line["text"] for line in lines_output),
            "lines": lines_output,
            "tight_rect": tight_rect,
            "fallback_rect": fallback_rect,
            "bounding_rect": tight_rect,
            "schema": "text_group_v2",
        })

    result.sort(key=lambda group: (-group["tight_rect"][0], group["tight_rect"][1]))
    for idx, group in enumerate(result, start=1):
        group["text_group_id"] = idx
        group["cluster_id"] = idx

    return result


# ==========================================
# 공개 API
# ==========================================

def build_text_groups_v2(
    cv_path: str,
    image_height: int = 1920,
    filter_furigana: bool = True,
    enable_repair: bool = True,
    enable_x_expand: bool = True,
    enable_consolidate: bool = False,
    enable_dynamic_ytol: bool = False,
    debug: bool = True,
) -> List[Dict[str, Any]]:
    """
    파라미터:
      enable_repair        : Pass1.5 Line Fragment Repair ON/OFF (기본 True)
      enable_x_expand      : Pass2.5 보수적 X방향 열 확장 ON/OFF (기본 True)
                             x_threshold = avg_width * 0.75, y_overlap ≥ 80%
                             Consolidate보다 엄격 — 인접 열만 묶고 과병합 방지
      enable_consolidate   : Pass3 Cluster Consolidation ON/OFF  (기본 False)
                             True 시 더 공격적 병합 — 오병합 위험 주시
      enable_dynamic_ytol  : 2순위 동적 y_tolerance ON/OFF
                             OFF(기본) → 기존 image_height * 0.015 유지
                             ON        → max(image_height*k, median_h*a) 혼합
                             주의: Pass1/Pass2 모두 영향 → 오병합 위험 주시
      debug                : 각 단계 진단 로그 출력
    """
    cv_data = load_cv_json(cv_path)
    fragments = create_text_blocks(cv_data)

    widths = [f["bbox"][2] for f in fragments]
    avg_width = float(np.percentile(widths, 75)) if widths else 50.0

    median_h, p75_h = compute_height_stats(fragments)

    if enable_dynamic_ytol:
        # 2순위: 글자 높이 통계 혼합 (상한: image_height * 3.5% → 과합병 방지)
        y_tolerance = min(
            compute_y_tolerance(image_height, median_h, k=0.015, a=1.0),
            image_height * 0.035,
        )
    else:
        # 기존 방식 유지 (안전)
        y_tolerance = image_height * 0.015

    if debug:
        ytol_mode = "dynamic" if enable_dynamic_ytol else "fixed"
        print(f"  avg_width={avg_width:.1f}  median_h={median_h:.1f}  p75_h={p75_h:.1f}  y_tol={y_tolerance:.1f} ({ytol_mode})")

    lines = pass1_create_lines(fragments, avg_width, y_tolerance)
    if debug:
        print(f"  Pass1: {len(fragments)} blocks → {len(lines)} lines")

    if enable_repair:
        # merge_gap: 누락 글자 3개분 gap까지 커버 (3글자 * 글자높이 + 자간)
        # y_tolerance * 3.0 과 median_h * 3.0 중 더 큰 값 (min으로 상한 제한)
        merge_gap = min(
            max(y_tolerance * 3.0, median_h * 3.0),
            image_height * 0.12,  # 이미지 높이의 12%를 상한으로 (과도한 병합 방지)
        )
        if debug:
            print(f"  [Repair] merge_gap={merge_gap:.1f}")
        lines, _ = repair_line_fragments(
            lines, x_tol=avg_width * 0.33, merge_gap=merge_gap, avg_width=avg_width, debug=debug
        )

    if filter_furigana:
        lines = filter_furigana_lines(lines, avg_width, image_height=float(image_height))

    groups = pass2_create_clusters(lines, avg_width, y_tolerance)
    if debug:
        print(f"  Pass2: {len(lines)} lines → {len(groups)} clusters")

    if enable_x_expand:
        # x_threshold = avg_width * 0.80: 열 간 간격이 약 4/5자 이내일 때만 병합
        # max_merged_w_mult = 7: 7자 폭 이내 말풍선까지 허용 (consolidate와 동일)
        groups = expand_x_adjacent(
            groups,
            avg_width=avg_width,
            x_threshold=avg_width * 0.80,
            y_overlap_min=0.80,
            max_merged_w_mult=7.0,
            debug=debug,
        )

    if enable_consolidate:
        groups = consolidate_clusters(groups, avg_width, y_tolerance, debug=debug)

    return _format_text_groups(groups, avg_width)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python text_group_builder_v2.py <cv_json> <output_json> [image_height]")
        raise SystemExit(1)

    cv_path = sys.argv[1]
    output_path = sys.argv[2]
    image_height = int(sys.argv[3]) if len(sys.argv) > 3 else 1920

    text_groups = build_text_groups_v2(cv_path, image_height=image_height)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(text_groups, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(text_groups)} text groups to {output_path}")


if __name__ == "__main__":
    main()
