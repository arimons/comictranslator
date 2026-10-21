# 이미지 생성 모델로 페이지의 글자를 지운 배경을 만든다 (번역 글자는 오버레이가 따로 얹는다)
"""페이지 글자 지우기 (gpt-image 편집).

LaMa·Telea 결과가 아쉬운 페이지를 통째로 이미지 편집 모델에 보내 글자만 지운 배경을 받는다.
받은 배경은 배경 복원 패치(페이지 전체)로 저장되어, Step 3를 다시 돌려도 덮이지 않고 렌더 때 합성된다.
영역·OCR 시트는 원본에서 만든 기존 것을 그대로 쓴다.

크기: 모델은 16의 배수 크기만 받는다. 비율을 바꿔 늘이면 그림이 틀어지므로, 원본 오른쪽·아래를
16의 배수까지 가장자리 픽셀로 덧대 보내고 받은 뒤 그만큼 잘라 원본 크기로 되돌린다.
실측(2026-10-20, medium): 1350×1920 → 1360×1920 요청, 24초, 입력 1,564 / 출력 471 토큰(약 2.7센트).
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pipeline import gemini_image
from pipeline.openai_tasks import IMAGE_MODEL

# 모델 문자열은 llm-model-reference(2026-10-06)의 이미지 편집 최고 품질 모델. 단가: 입력 $8 / 출력 $30 (100만 토큰당)
ERASE_MODEL = IMAGE_MODEL
PRICE_PER_INPUT_TOKEN = 8 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 30 / 1_000_000
MIN_PIXELS = 655_360  # 이보다 작은 크기는 API가 거절한다 (512×512 실측 거절)
MAX_SIDE = 3840

ERASE_PROMPT = (
    "Remove every piece of Japanese text on this manga page: dialogue in speech bubbles, captions, narration, "
    "furigana and small side notes. Leave the speech bubbles empty and keep their outlines. Where text overlaps "
    "artwork or a semi-transparent bubble, restore the artwork and the bubble tint behind it naturally. Keep "
    "everything else exactly the same: panel layout, characters, colors, lines and image bounds. "
    "Do not add any text, letters or new objects."
)


class AiEraseError(RuntimeError):
    pass


def padded_size(width: int, height: int, step: int = 16) -> tuple[int, int]:
    """비율을 바꾸지 않고 덧대기만 해서 맞출 16의 배수 크기."""
    return -(-width // step) * step, -(-height // step) * step


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * PRICE_PER_INPUT_TOKEN + output_tokens * PRICE_PER_OUTPUT_TOKEN


def erase_page_text(
    image: np.ndarray,
    work_dir: Path,
    *,
    model: str = ERASE_MODEL,
    quality: str = "medium",
    prompt: str = ERASE_PROMPT,
    client: Any = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """페이지(BGR)를 보내 글자를 지운 같은 크기의 페이지와 메타(시간·토큰·추정 비용)를 돌려준다.
    model이 gemini-*이면 Gemini 이미지 모델 경로로 보낸다(크기 맞추기 방식이 다르다)."""
    if gemini_image.is_gemini_model(model):
        try:
            return gemini_image.edit_image(image, prompt, work_dir, model=model, quality=quality, client=client)
        except gemini_image.GeminiImageError as exc:
            raise AiEraseError(str(exc)) from exc
    height, width = image.shape[:2]
    pad_w, pad_h = padded_size(width, height)
    if max(pad_w, pad_h) > MAX_SIDE:
        raise AiEraseError(f"페이지가 너무 큽니다({width}×{height}). 긴 변 {MAX_SIDE}px 이하만 보낼 수 있습니다")
    if pad_w * pad_h < MIN_PIXELS:
        raise AiEraseError(f"페이지가 너무 작습니다({width}×{height}). 모델 최소 크기({MIN_PIXELS:,}픽셀) 미만입니다")

    work_dir.mkdir(parents=True, exist_ok=True)
    request_path = work_dir / "ai_erase_request.png"
    cv2.imwrite(str(request_path), cv2.copyMakeBorder(image, 0, pad_h - height, 0, pad_w - width, cv2.BORDER_REPLICATE))

    if client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise AiEraseError("OPENAI_API_KEY 환경 변수가 필요합니다")
        from openai import OpenAI

        # SDK 자동 재시도는 끈다. 시간 초과·5xx 뒤 같은 요청이 다시 나가면 이미지 편집이 이중 과금될 수 있다.
        client = OpenAI(api_key=api_key, timeout=300, max_retries=0)

    started = time.perf_counter()
    with request_path.open("rb") as handle:
        response = client.images.edit(
            model=model,
            image=[handle],
            prompt=prompt,
            size=f"{pad_w}x{pad_h}",
            quality=quality,
            output_format="png",
        )
    elapsed = time.perf_counter() - started

    data = getattr(response, "data", None) or []
    b64 = getattr(data[0], "b64_json", None) if data else None
    if not b64:
        raise AiEraseError("이미지 응답이 비어 있습니다")
    generated = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
    if generated is None:
        raise AiEraseError("이미지 응답을 읽을 수 없습니다")
    if generated.shape[:2] != (pad_h, pad_w):
        # 요청 크기와 다르게 오면 비율이 틀어진 것이라 그대로 쓰지 않는다
        raise AiEraseError(f"요청 크기({pad_w}×{pad_h})와 다른 응답({generated.shape[1]}×{generated.shape[0]})")
    result = generated[:height, :width].copy()

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    meta = {
        "model": model,
        "quality": quality,
        "request_size": [pad_w, pad_h],
        "elapsed_sec": round(elapsed, 1),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": round(estimate_cost(input_tokens, output_tokens), 4),
    }
    return result, meta
