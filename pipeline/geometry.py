# 파이프라인 전반에서 쓰는 사각형 좌표 유틸리티
from __future__ import annotations

from typing import Iterable


Rect = list[int]


def as_rect(rect: Iterable[int]) -> Rect:
    """입력 rect를 [x, y, w, h] 정수 rect로 정규화한다."""
    x, y, w, h = [int(v) for v in list(rect)[:4]]
    return [x, y, max(0, w), max(0, h)]


def union_rect(rects: Iterable[Iterable[int]]) -> Rect:
    """여러 [x, y, w, h] rect의 외접 rect를 반환한다."""
    valid = []
    for rect in rects:
        values = list(rect)
        if len(values) < 4:
            continue
        normalized = as_rect(values)
        if normalized[2] <= 0 or normalized[3] <= 0:
            continue
        valid.append(normalized)

    if not valid:
        return [0, 0, 0, 0]

    x1 = min(rect[0] for rect in valid)
    y1 = min(rect[1] for rect in valid)
    x2 = max(rect[0] + rect[2] for rect in valid)
    y2 = max(rect[1] + rect[3] for rect in valid)
    return [x1, y1, x2 - x1, y2 - y1]
