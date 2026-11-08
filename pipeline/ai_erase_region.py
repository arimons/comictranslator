"""영역 하나만 이미지 편집 모델로 보내 글자를 지우는 AI 지우기(영역 모드)의 순수 계산.

쪽 전체를 보내는 AI 지우기와 달리 선택한 영역 둘레의 크롭만 보내고, 받은 결과는 그 영역의
글자 획 주변 화소에만 합성한다. 다른 영역·다른 화소는 건드리지 않아 영역별로 따로 확정·해제할 수 있다.
"""

from __future__ import annotations

import cv2
import numpy as np

ERASE_PROMPT = (
    "Remove ALL Japanese text from this image, including small furigana above words. If the text sits on a "
    "semi-transparent box, keep the box with its tint and rounded corners exactly, and restore the artwork or "
    "background behind the text naturally. Keep everything else exactly the same. "
    "Do not add any text, letters or new objects."
)
ERASE_MIN_PIXELS = 700_000  # 모델 최소(655,360)에 16배수 덧대기 여유를 더한 값
ERASE_MAX_RATIO = 2.8
ERASE_MARGIN = 60
STROKE_DILATE = 8
# 확정한 패치를 쪽 재분석 뒤에도 남길지 정할 때, 영역이 패치 대상 사각형과 이 비율 이상 겹치면 같은 자리로 본다
PATCH_KEEP_OVERLAP = 0.3


def region_erase_rect(region: dict) -> list[int] | None:
    """영역 데이터에서 글자를 지우는 사각형. 텍스트 기반은 unboxed/erase 사각형이 bubble 사각형보다 글자에 가깝다."""
    for key in ("unboxed_rect", "erase_rect", "bubble_rect"):
        rect = region.get(key)
        if isinstance(rect, (list, tuple)) and len(rect) >= 4 and int(rect[2]) > 0 and int(rect[3]) > 0:
            return [int(v) for v in rect[:4]]
    return None


def context_rect(rect: list[int], page_w: int, page_h: int) -> list[int]:
    """영역을 감싸되 모델 최소 크기·비율을 만족하도록 넓힌 크롭 사각형."""
    x, y, w, h = rect
    x1, y1, x2, y2 = x - ERASE_MARGIN, y - ERASE_MARGIN, x + w + ERASE_MARGIN, y + h + ERASE_MARGIN
    cw, ch = x2 - x1, y2 - y1
    if cw > ch * ERASE_MAX_RATIO:
        grow = int(cw / ERASE_MAX_RATIO - ch)
        y1, y2 = y1 - grow // 2, y2 + grow - grow // 2
    elif ch > cw * ERASE_MAX_RATIO:
        grow = int(ch / ERASE_MAX_RATIO - cw)
        x1, x2 = x1 - grow // 2, x2 + grow - grow // 2
    while (x2 - x1) * (y2 - y1) < ERASE_MIN_PIXELS:
        x1, y1, x2, y2 = x1 - 20, y1 - 20, x2 + 20, y2 + 20
    # 페이지 밖으로 나가면 반대쪽으로 민다(크기는 유지)
    dx = max(0, -x1) - max(0, x2 - page_w)
    dy = max(0, -y1) - max(0, y2 - page_h)
    x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(page_w, x2), min(page_h, y2)
    return [x1, y1, x2 - x1, y2 - y1]


def region_patch_mask(page: np.ndarray, rect: list[int], crop: list[int]) -> np.ndarray:
    """크롭 좌표계의 합성 마스크: 영역 사각형 안의 글자 획 주변만 255."""
    from pipeline.background_restore import MaskParams, build_text_stroke_mask

    page_h, page_w = page.shape[:2]
    stroke = build_text_stroke_mask(page, [{"rect": rect, "fragments": []}], MaskParams(dilate_px=STROKE_DILATE))
    inside = np.zeros((page_h, page_w), dtype=np.uint8)
    x, y, w, h = rect
    inside[y:y + h, x:x + w] = 255
    cx, cy, cw, ch = crop
    return cv2.bitwise_and(stroke, inside)[cy:cy + ch, cx:cx + cw]


def all_regions_patch_mask(page: np.ndarray, regions: list[dict]) -> np.ndarray:
    """쪽 크기 합성 마스크: 모든 영역(R) 사각형 안의 글자 획 주변만 255. 영역 밖 글자는 건드리지 않는다."""
    from pipeline.background_restore import MaskParams, build_text_stroke_mask

    page_h, page_w = page.shape[:2]
    seeds = [{"rect": rect, "fragments": []} for rect in (region_erase_rect(r) for r in regions) if rect]
    if not seeds:
        return np.zeros((page_h, page_w), dtype=np.uint8)
    stroke = build_text_stroke_mask(page, seeds, MaskParams(dilate_px=STROKE_DILATE))
    inside = np.zeros((page_h, page_w), dtype=np.uint8)
    for seed in seeds:
        x, y, w, h = seed["rect"]
        inside[max(0, y):y + h, max(0, x):x + w] = 255
    return cv2.bitwise_and(stroke, inside)


def overlap_ratio(rect: list[int], other: list[int]) -> float:
    """rect 면적 대비 other와 겹치는 면적 비율."""
    x1, y1 = max(rect[0], other[0]), max(rect[1], other[1])
    x2, y2 = min(rect[0] + rect[2], other[0] + other[2]), min(rect[1] + rect[3], other[1] + other[3])
    area = max(1, rect[2] * rect[3])
    return max(0, x2 - x1) * max(0, y2 - y1) / area


def stale_patch_ids(patches: list[dict], region_rects: list[list[int]]) -> list[str]:
    """영역 모드로 확정한 패치 중 현재 어느 영역과도 겹치지 않는(영역이 지워진) 패치 id."""
    stale = []
    for patch in patches:
        target = patch.get("region_rect")
        if patch.get("engine") != "ai-erase" or not target:
            continue
        if not any(overlap_ratio([int(v) for v in target], rect) >= PATCH_KEEP_OVERLAP for rect in region_rects):
            stale.append(str(patch["id"]))
    return stale
