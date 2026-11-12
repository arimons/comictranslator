"""효과음 찾기: Cloud Vision이 못 잡는 그림 위 효과음 글자의 대략적인 위치를 비전 모델에게 묻는다.

CV·글자 묶음(TG)을 거치지 않고 쪽 이미지를 줄여 모델에 직접 보낸다. 모델이 준 박스는 효과음 일부만 감싸는 경우가
있어(길게 내려간 획, 작은 점 등) 조금 넓혀서 돌려주고, 최종 범위는 사용자가 영역 크기 조절로 맞춘다.
돌려주는 값은 후보일 뿐이며 영역(R)으로 만드는 일은 화면이 요청할 때 가이드(manual_regions)에 추가해 한다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import cv2

LONG_SIDE = 1600           # 모델에 보내는 쪽 이미지의 긴 변(px). 줄여 보내 비용과 시간을 아낀다
BOX_PAD_RATIO = 0.12       # 모델 박스는 작게 잡히는 경향이 있어 사방으로 이 비율만큼 넓힌다
BOX_PAD_MIN = 14           # 넓히는 최소 픽셀
MIN_SIDE = 20              # 이보다 작은 박스는 버린다
SKIP_OVERLAP = 0.5         # 후보가 이미 있는 영역과 이 비율 이상 겹치면 같은 글자로 보고 뺀다

PROMPT = (
    "일본어 만화 한 페이지입니다. 말풍선 대사와 설명글(나레이션)은 제외하고, 그림 위에 그려진 **효과음**(의성어·의태어 글자, 큰 붓글씨와 장식 글자 포함)만 찾아 주세요.\n"
    "JSON 객체 하나로만 답하세요: {\"items\": [{\"text\": \"보이는 일본어 글자\", \"box\": [x1, y1, x2, y2]}]}\n"
    "box는 이미지의 가로·세로를 각각 0-1000으로 본 좌표이고, x1·y1은 왼쪽 위, x2·y2는 오른쪽 아래입니다. "
    "효과음 글자 전체(탁점, 작은 점, 꼬리처럼 따라 붙은 획까지)를 빠짐없이 감싸는 직사각형으로 주세요. "
    "효과음이 없으면 {\"items\": []}를 답하세요."
)


def _overlap_ratio(rect: list[int], other: list[int]) -> float:
    """rect 면적 대비 other와 겹치는 면적 비율."""
    x1, y1 = max(rect[0], other[0]), max(rect[1], other[1])
    x2, y2 = min(rect[0] + rect[2], other[0] + other[2]), min(rect[1] + rect[3], other[1] + other[3])
    return max(0, x2 - x1) * max(0, y2 - y1) / max(1, rect[2] * rect[3])


def items_to_candidates(items: Any, width: int, height: int, existing_rects: list[list[int]] | None = None) -> list[dict[str, Any]]:
    """모델 응답의 items(0-1000 좌표)를 쪽 픽셀 좌표 후보로 바꾼다. 잘못된 항목과 이미 영역인 항목은 뺀다."""
    candidates: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return candidates
    for item in items:
        if not isinstance(item, dict):
            continue
        box = item.get("box")
        try:
            x1, y1, x2, y2 = (float(v) for v in box[:4])
        except (TypeError, ValueError, IndexError):
            continue
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        left, right = x1 * width / 1000, x2 * width / 1000
        top, bottom = y1 * height / 1000, y2 * height / 1000
        if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
            continue  # 모델이 준 박스 자체가 너무 작으면 넓히기 전에 버린다(점·잡티)
        pad_x = max(BOX_PAD_MIN, (right - left) * BOX_PAD_RATIO)
        pad_y = max(BOX_PAD_MIN, (bottom - top) * BOX_PAD_RATIO)
        left, top = max(0, left - pad_x), max(0, top - pad_y)
        right, bottom = min(width, right + pad_x), min(height, bottom + pad_y)
        rect = [int(round(left)), int(round(top)), int(round(right - left)), int(round(bottom - top))]
        if any(_overlap_ratio(rect, other) >= SKIP_OVERLAP for other in existing_rects or []):
            continue
        if any(_overlap_ratio(rect, c["rect"]) >= SKIP_OVERLAP for c in candidates):
            continue
        candidates.append({"text": str(item.get("text") or "").strip(), "rect": rect})
    return candidates


REFINE_EXPAND = 0.6        # 1차 박스 둘레로 이만큼 더 넓게 잘라 확대해서 다시 묻는다
REFINE_ROUNDS = 2          # 1차 박스가 일부만 잡았을 수 있어 범위가 커지면 한 번 더 묻는다
REFINE_PAD_RATIO = 0.06    # 다듬은 박스를 사방으로 조금만 넓힌다
REFINE_PROMPT = (
    "이 이미지는 일본어 만화 한 컷의 일부입니다. 그림 위에 '{reading}' 같은 효과음 글자(굵은 붓글씨, 탁점·작은 점·꼬리 획 포함)가 있습니다. "
    "그 효과음 글자의 **모든 획**(윗부분의 꺾인 획과 짧은 사선들, 길게 내려간 획, 아래의 작은 점들까지)을 빠짐없이 감싸는 가장 작은 직사각형을 찾아 주세요. "
    "배경 그림(옷 주름, 손, 윤곽선)은 포함하지 마세요. JSON 객체 하나로만 답하세요: "
    "{{\"box\": [x1, y1, x2, y2]}} (이미지 가로·세로를 각각 0-1000으로 본 값, x1·y1은 왼쪽 위, x2·y2는 오른쪽 아래)."
)


def refine_rect(page: Any, rect: list[int], reading: str, model_name: str, run_json: Any) -> list[int]:
    """1차 박스 둘레를 크게 잘라 확대해서 모델에게 효과음 전체 범위를 다시 묻는다. 실패하면 1차 박스를 그대로 돌려준다.

    쪽 전체 이미지에서는 박스가 효과음 일부만 감싸는 경우가 많은데, 확대한 크롭에서는 훨씬 정확하다. 범위가 커졌으면
    그 박스로 한 번 더 묻는다(1차 박스가 위쪽만 잡았으면 아래쪽이 첫 크롭에 안 들어오기 때문)."""
    page_h, page_w = page.shape[:2]
    current = [int(v) for v in rect[:4]]
    for _ in range(REFINE_ROUNDS):
        x, y, w, h = current
        wx1, wy1 = max(0, int(x - w * REFINE_EXPAND)), max(0, int(y - h * REFINE_EXPAND))
        wx2, wy2 = min(page_w, int(x + w * (1 + REFINE_EXPAND))), min(page_h, int(y + h * (1 + REFINE_EXPAND)))
        if wx2 - wx1 < MIN_SIDE or wy2 - wy1 < MIN_SIDE:
            break
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "sfx_crop.jpg"
                cv2.imwrite(str(path), page[wy1:wy2, wx1:wx2], [cv2.IMWRITE_JPEG_QUALITY, 90])
                data, _elapsed = run_json(str(path), REFINE_PROMPT.format(reading=reading or "効果音"), 0.0, model_name)
            bx1, by1, bx2, by2 = (float(v) for v in data["box"][:4])
        except Exception:  # noqa: BLE001 - 다듬기는 보조 단계라 실패하면 지금 박스를 쓴다
            break
        cw, ch = wx2 - wx1, wy2 - wy1
        nx1, nx2 = sorted((bx1, bx2)); ny1, ny2 = sorted((by1, by2))
        refined = [int(wx1 + nx1 * cw / 1000), int(wy1 + ny1 * ch / 1000), int((nx2 - nx1) * cw / 1000), int((ny2 - ny1) * ch / 1000)]
        if refined[2] < MIN_SIDE or refined[3] < MIN_SIDE:
            break
        grew = refined[2] * refined[3] > current[2] * current[3] * 1.15
        current = refined
        if not grew:
            break
    pad_x, pad_y = max(8, int(current[2] * REFINE_PAD_RATIO)), max(8, int(current[3] * REFINE_PAD_RATIO))
    left, top = max(0, current[0] - pad_x), max(0, current[1] - pad_y)
    right, bottom = min(page_w, current[0] + current[2] + pad_x), min(page_h, current[1] + current[3] + pad_y)
    return [left, top, right - left, bottom - top]


def locate_sfx(image_path: str, model_name: str | None, existing_rects: list[list[int]] | None = None) -> dict[str, Any]:
    """쪽 이미지를 모델에 보내 효과음 후보(text, rect)를 받는다."""
    from pipeline.gemini_ocr_translate_v5 import MODEL_NAME, _run_model_json

    page = cv2.imread(str(image_path))
    if page is None:
        raise ValueError(f"이미지를 읽을 수 없습니다: {image_path}")
    height, width = page.shape[:2]
    scale = min(1.0, LONG_SIDE / max(width, height))
    small = cv2.resize(page, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else page
    model = model_name or MODEL_NAME
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sfx_page.jpg"
        cv2.imwrite(str(path), small, [cv2.IMWRITE_JPEG_QUALITY, 88])
        data, elapsed = _run_model_json(str(path), PROMPT, 0.0, model)
    items = data.get("items") if isinstance(data, dict) else data
    # 1차 박스는 거칠게 잡히므로 박스마다 확대해서 한 번 더 묻는다(박스 수만큼 호출이 늘며 한 번에 5초 안팎).
    candidates = items_to_candidates(items, width, height, existing_rects)
    for candidate in candidates:
        candidate["rect"] = refine_rect(page, candidate["rect"], candidate["text"], model, _run_model_json)
    return {"model": model, "elapsed_sec": round(elapsed, 1), "items": candidates}

