# 말풍선 밖 텍스트 후보를 선별하고 인접 TextGroup을 묶는 유틸리티
from __future__ import annotations

from typing import Any

import numpy as np

try:
    from pipeline.bubble_text_filters import is_forbidden_text
    from pipeline.geometry import as_rect, union_rect
except ImportError:
    from bubble_text_filters import is_forbidden_text
    from geometry import as_rect, union_rect


def text_group_id(group: dict[str, Any], fallback: int) -> int:
    return int(group.get("text_group_id", group.get("cluster_id", fallback)))


def text_group_text(group: dict[str, Any]) -> str:
    if group.get("text"):
        return str(group["text"])
    return "".join(line.get("text", "") for line in group.get("lines", []))


def is_noise_text(text: str) -> bool:
    compact = "".join(ch for ch in text if not ch.isspace())
    if not compact:
        return True
    if is_forbidden_text(compact):
        return True
    jp = sum(1 for ch in compact if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    return jp == 0


def text_group_stats(group: dict[str, Any]) -> dict[str, int]:
    compact = "".join(ch for ch in text_group_text(group) if not ch.isspace())
    jp = sum(1 for ch in compact if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    kanji = sum(1 for ch in compact if "一" <= ch <= "鿿")
    kana = sum(1 for ch in compact if "぀" <= ch <= "ヿ")
    lines = group.get("lines") or []
    return {"chars": len(compact), "jp": jp, "kanji": kanji, "kana": kana, "lines": max(1, len(lines))}


def is_short_kana_text(group: dict[str, Any]) -> bool:
    stats = text_group_stats(group)
    return 2 <= stats["chars"] <= 4 and stats["kana"] == stats["chars"] and stats["kanji"] == 0


def is_large_single_sfx_like(group: dict[str, Any]) -> bool:
    """짧은 TG가 bbox만 큰 경우 효과음으로 보고 unboxed 마스킹에서 제외한다."""
    rect = as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0])))
    area = rect[2] * rect[3]
    stats = text_group_stats(group)
    if stats["jp"] == 0:
        return False
    if stats["lines"] == 1 and stats["chars"] == 1:
        return area >= 3500 and max(rect[2], rect[3]) >= 55

    if not is_short_kana_text(group):
        return False
    max_dim = max(rect[2], rect[3])
    aspect = rect[2] / max(rect[3], 1)
    char_step = max_dim / max(stats["chars"], 1)
    if area < 25000 or max_dim < 150:
        return False
    if not (0.55 <= aspect <= 1.45 and char_step >= 45):
        return False
    line_texts = [str(line.get("text", "")).strip() for line in group.get("lines") or []]
    return bool(line_texts) and all(1 <= len(text) <= 4 for text in line_texts)


def is_single_char_text_group(group: dict[str, Any]) -> bool:
    """단일 글자 TG는 unboxed inpaint 대상에서 제외한다."""
    return text_group_stats(group)["chars"] <= 1


def is_short_handwritten_label_like(group: dict[str, Any]) -> bool:
    """작고 짧은 단일 라인 TG는 손글씨/라벨로 보고 unboxed OCR에서 제외한다."""
    stats = text_group_stats(group)
    if stats["jp"] == 0 or stats["chars"] > 5 or stats["lines"] != 1:
        return False
    rect = as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0])))
    _x, _y, w, h = rect
    if w <= 0 or h <= 0:
        return False
    return w * h <= 4500 and max(w, h) <= 120


def merged_text_group(cluster: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    """클러스터 전체 외접 bbox를 단일 lane_mask로 사용하기 위한 synthetic group."""
    rects = [as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0]))) for _, group in cluster]
    return {"tight_rect": union_rect(rects), "lines": [{"bbox": union_rect(rects), "fragments": []}]}


def cluster_adjacent_text_groups(
    tg_list: list[tuple[int, dict[str, Any]]],
    x_gap_threshold: int = 30,
) -> list[list[tuple[int, dict[str, Any]]]]:
    """인접한 TG들을 BFS로 클러스터링한다."""
    n = len(tg_list)
    if n == 0:
        return []

    def rect(group: dict[str, Any]) -> list[int]:
        return as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0])))

    def merge_width(group: dict[str, Any]) -> int:
        if str(group.get("orientation", "")).lower() == "vertical":
            line_widths = [
                max(1, as_rect(line.get("bbox", [0, 0, 0, 0]))[2])
                for line in group.get("lines", [])
                if len(line.get("bbox", [])) >= 4
            ]
            if line_widths:
                return int(np.median(line_widths))
        return max(1, rect(group)[2])

    widths = [merge_width(group) for _, group in tg_list]
    page_avg_width = float(np.median(widths)) if widths else float(x_gap_threshold)

    def diagonal_square_merge_like(r1: list[int], r2: list[int], overlap_ratio: float) -> bool:
        x1 = min(r1[0], r2[0])
        y1 = min(r1[1], r2[1])
        x2 = max(r1[0] + r1[2], r2[0] + r2[2])
        y2 = max(r1[1] + r1[3], r2[1] + r2[3])
        union_w = max(1, x2 - x1)
        union_h = max(1, y2 - y1)
        union_aspect = union_w / union_h
        rect_area = r1[2] * r1[3] + r2[2] * r2[3]
        union_fill = rect_area / max(1, union_w * union_h)

        c1x, c1y = r1[0] + r1[2] / 2, r1[1] + r1[3] / 2
        c2x, c2y = r2[0] + r2[2] / 2, r2[1] + r2[3] / 2
        center_dx = abs(c1x - c2x)
        center_dy = abs(c1y - c2y)
        min_h = max(1, min(r1[3], r2[3]))
        avg_w = max(1.0, (r1[2] + r2[2]) / 2)

        return (
            0.75 <= union_aspect <= 1.35
            and union_fill < 0.68
            and overlap_ratio < 0.65
            and center_dy > min_h * 0.60
            and center_dx > avg_w * 0.90
        )

    def mismatched_short_effect_scale(g1: dict[str, Any], g2: dict[str, Any], r1: list[int], r2: list[int]) -> bool:
        s1 = text_group_stats(g1)
        s2 = text_group_stats(g2)
        if s1["jp"] == 0 or s2["jp"] == 0:
            return False

        def char_step(stats: dict[str, int], r: list[int]) -> float:
            chars = max(1, stats["chars"])
            is_landscape = r[2] / max(r[3], 1) >= 1.15
            axis = r[2] if is_landscape else r[3]
            return axis / chars

        pairs = ((s1, r1, s2, r2), (s2, r2, s1, r1))
        for short_stats, short_rect, long_stats, long_rect in pairs:
            if not (1 <= short_stats["lines"] <= 1 and 1 <= short_stats["chars"] <= 4):
                continue
            if long_stats["chars"] < 5:
                continue
            short_step = char_step(short_stats, short_rect)
            long_step = char_step(long_stats, long_rect)
            if short_step / max(long_step, 1.0) >= 2.4:
                return True
        return False

    def adjacent(g1: dict[str, Any], g2: dict[str, Any]) -> bool:
        r1, r2 = rect(g1), rect(g2)
        x_gap = max(0, r1[0] - (r2[0] + r2[2]), r2[0] - (r1[0] + r1[2]))
        pair_avg_width = (merge_width(g1) + merge_width(g2)) / 2
        dynamic_x_threshold = min(90, max(x_gap_threshold, page_avg_width * 0.9, pair_avg_width * 1.2))
        if x_gap > dynamic_x_threshold:
            return False

        y_top = max(r1[1], r2[1])
        y_bot = min(r1[1] + r1[3], r2[1] + r2[3])
        overlap_y = max(0, y_bot - y_top)
        min_h = min(r1[3], r2[3])
        y_overlap_ratio = overlap_y / min_h if min_h > 0 else 0.0

        narrow_page_guard_width = max(page_avg_width, pair_avg_width * 0.75)
        if x_gap > narrow_page_guard_width * 1.2 and abs(r1[1] - r2[1]) > narrow_page_guard_width * 0.5:
            if y_overlap_ratio < 0.70:
                return False
        if x_gap > pair_avg_width * 0.55 and abs(r1[1] - r2[1]) > pair_avg_width * 0.5:
            if y_overlap_ratio < 0.70:
                return False

        if min_h == 0 or y_overlap_ratio < 0.45:
            return False
        overlap_ratio = overlap_y / min_h
        if diagonal_square_merge_like(r1, r2, overlap_ratio):
            return False
        if mismatched_short_effect_scale(g1, g2, r1, r2):
            return False

        if abs(r1[1] - r2[1]) > page_avg_width * 5 and x_gap > page_avg_width * 0.5:
            return False

        narrow_w = min(r1[2], r2[2])
        if (
            narrow_w < page_avg_width
            and x_gap > page_avg_width * 1.8
            and abs(r1[1] - r2[1]) > page_avg_width * 0.5
        ):
            return False

        merged_w = max(r1[0] + r1[2], r2[0] + r2[2]) - min(r1[0], r2[0])
        return merged_w <= page_avg_width * 8

    visited = [False] * n
    clusters: list[list[tuple[int, dict[str, Any]]]] = []

    for i in range(n):
        if visited[i]:
            continue
        cluster = [tg_list[i]]
        visited[i] = True
        queue = [i]
        while queue:
            curr = queue.pop(0)
            for j in range(n):
                if visited[j]:
                    continue
                if adjacent(tg_list[curr][1], tg_list[j][1]):
                    visited[j] = True
                    cluster.append(tg_list[j])
                    queue.append(j)
        clusters.append(cluster)

    return clusters
