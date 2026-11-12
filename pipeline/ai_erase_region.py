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


def all_regions_patch_mask(page: np.ndarray, regions: list[dict], extra_rects: list[list[int]] | None = None) -> np.ndarray:
    """쪽 크기 합성 마스크: 모든 영역(R) 사각형 안의 글자 획 주변만 255. 영역 밖 글자는 건드리지 않는다.

    extra_rects(효과음 찾기로 찾은 박스)는 박스 안의 굵은 획 둘레만 칠한다(`sfx_stroke_mask`). 박스 전체를 바꾸면 모델이 다시 그린
    점 톤 무늬가 사각형 모양으로 달라 보이기 때문이다. 굵은 획을 못 찾으면 박스 전체를 칠한다."""
    from pipeline.background_restore import MaskParams, build_text_stroke_mask

    page_h, page_w = page.shape[:2]
    seeds = [{"rect": rect, "fragments": []} for rect in (region_erase_rect(r) for r in regions) if rect]
    if seeds:
        stroke = build_text_stroke_mask(page, seeds, MaskParams(dilate_px=STROKE_DILATE))
        inside = np.zeros((page_h, page_w), dtype=np.uint8)
        for seed in seeds:
            x, y, w, h = seed["rect"]
            inside[max(0, y):y + h, max(0, x):x + w] = 255
        mask = cv2.bitwise_and(stroke, inside)
    else:
        mask = np.zeros((page_h, page_w), dtype=np.uint8)
    for rect in extra_rects or []:
        mask = cv2.bitwise_or(mask, sfx_stroke_mask(page, rect))
    return mask


SFX_DARK_LEVEL = 110       # 이보다 어두운 화소를 획 후보로 본다
SFX_OPEN_PX = 7            # 이보다 얇은 것(점 톤의 점, 가는 선)은 지우고 굵은 획만 남긴다(9로 하면 효과음의 얇은 가로획이 떨어져 나간다)
SFX_TIP_PX = 31            # 굵은 획에서 이 거리 안의 어두운 화소는 획 끝으로 보고 되살린다
SFX_BORDER_SPAN = 0.9      # 획 덩어리가 박스 가로·세로의 이 비율 이상을 가로지르면서 가늘면 컷 테두리로 보고 뺀다
SFX_BORDER_THIN = 0.14
SFX_PAD_PX = 33            # 굵은 획 둘레로 넓히는 폭(획 끝과 글자 둘레의 흰 테두리까지 덮는다)
SFX_MIN_COVER = 0.02       # 굵은 획이 박스 면적의 이 비율 미만이면 못 찾은 것으로 보고 박스 전체를 칠한다


def sfx_stroke_mask(page: np.ndarray, rect: list[int]) -> np.ndarray:
    """효과음 박스 안의 굵은 획 둘레만 255인 쪽 크기 마스크. 굵은 획을 못 찾으면 박스 전체를 칠한다."""
    page_h, page_w = page.shape[:2]
    x, y, w, h = (int(v) for v in rect[:4])
    x1, y1, x2, y2 = max(0, x), max(0, y), min(page_w, x + w), min(page_h, y + h)
    mask = np.zeros((page_h, page_w), dtype=np.uint8)
    if x2 <= x1 or y2 <= y1:
        return mask
    gray = cv2.cvtColor(page[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    dark = (gray < SFX_DARK_LEVEL).astype(np.uint8) * 255
    strokes = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SFX_OPEN_PX, SFX_OPEN_PX)))
    # 컷 테두리 같은 길고 가는 덩어리는 효과음 획이 아니므로 뺀다
    count, labels, stats, _ = cv2.connectedComponentsWithStats(strokes)
    bh, bw = strokes.shape
    for lab in range(1, count):
        cw, ch = stats[lab, cv2.CC_STAT_WIDTH], stats[lab, cv2.CC_STAT_HEIGHT]
        if (cw >= SFX_BORDER_SPAN * bw and ch <= SFX_BORDER_THIN * bh) or (ch >= SFX_BORDER_SPAN * bh and cw <= SFX_BORDER_THIN * bw):
            strokes[labels == lab] = 0
    if np.count_nonzero(strokes) < SFX_MIN_COVER * strokes.size:
        mask[y1:y2, x1:x2] = 255
        return mask
    # 획 끝은 가늘게 빠져서 열기에 잘려 나가므로, 굵은 획 가까이에 있는 어두운 화소(끝부분)를 되살린 뒤 넓힌다
    near = cv2.dilate(strokes, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SFX_TIP_PX, SFX_TIP_PX)))
    strokes = cv2.bitwise_or(strokes, cv2.bitwise_and(dark, near))
    grown = cv2.dilate(strokes, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SFX_PAD_PX, SFX_PAD_PX)))
    mask[y1:y2, x1:x2] = grown
    return mask


def sfx_erase_prompt(base_prompt: str, sfx_items: list[dict], page_w: int, page_h: int) -> str:
    """쪽 전체 지우기 프롬프트에 효과음 지시를 덧붙인다. 기본 프롬프트는 대사·설명글만 지우라고 해서 모델이 큰 효과음을
    그림으로 보고 남기기 때문에, 찾은 효과음의 읽기와 위치(쪽 대비 %)를 알려 그 글자도 지우게 한다."""
    areas = []
    for item in sfx_items:
        x, y, w, h = (int(v) for v in item["rect"][:4])
        reading = f' (reads "{item["text"]}")' if item.get("text") else ""
        areas.append(
            f"left {round(x / page_w * 100)}%, top {round(y / page_h * 100)}%, width {round(w / page_w * 100)}%, height {round(h / page_h * 100)}%{reading}"
        )
    return (
        base_prompt
        + " ALSO remove the large sound-effect lettering (onomatopoeia) drawn over the artwork in these areas: "
        + "; ".join(f"({a})" for a in areas)
        + ". Erase every stroke of that lettering, including its small dots and slash marks, and restore the halftone pattern, "
        "screentone and fabric lines behind it naturally. Do not draw any new text there."
    )


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
