# Gemini 이미지 모델(Nano Banana 2.1)로 페이지를 편집한다. 글자 지우기·통번역 재생성의 Gemini 경로
"""Gemini 이미지 편집.

OCR·번역 모델로 Gemini를 고른 세션은 이미지 작업도 Gemini로 보낸다(OpenAI 경로는 gpt-image).
이 모델은 픽셀 크기를 받지 않고 비율 프리셋(aspect_ratio)과 해상도 등급(1K/2K/4K)만 받는다.
그래서 원본 오른쪽·아래를 가장 가까운 비율 프리셋까지 가장자리 픽셀로 덧대 보내고,
받은 이미지를 덧댄 크기로 같은 배율 축소한 뒤 덧댄 만큼 잘라 원본 크기로 되돌린다(그림 비율은 그대로).

공식 문서(2026-10-21 확인): https://ai.google.dev/gemini-api/docs/image-generation
- Interactions API 사용, 응답 형식은 image/jpeg만 받는다.
- 단가(Standard): 입력 $1.50, 출력 이미지 $30 / 100만 토큰. 장당 1K $0.0336, 2K $0.0504, 4K $0.113.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

GEMINI_IMAGE_MODEL = "gemini-nano-banana-2.1"
PRICE_PER_INPUT_TOKEN = 1.5 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 30 / 1_000_000  # 출력 이미지 토큰
PRICE_PER_THOUGHT_TOKEN = 7.5 / 1_000_000
# 문서에 나열된 비율 프리셋. 모델이 받지 않는 비율이 있으면 400으로 돌아온다.
ASPECT_RATIOS = ("1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
# 기존 quality 선택지(low/medium/high)를 해상도 등급으로 옮긴다
IMAGE_SIZE_FOR_QUALITY = {"low": "1K", "medium": "2K", "high": "4K"}


class GeminiImageError(RuntimeError):
    pass


def is_gemini_model(model_name: str | None) -> bool:
    return bool(model_name) and str(model_name).startswith("gemini-")


def image_model_for(text_model: str | None, openai_image_model: str) -> str:
    """세션의 OCR·번역 모델 제공자에 맞는 이미지 모델. Gemini를 골랐으면 Gemini 이미지 모델로 간다."""
    return GEMINI_IMAGE_MODEL if is_gemini_model(text_model) else openai_image_model


def fit_aspect_ratio(width: int, height: int) -> tuple[str, int, int]:
    """덧대기만으로 맞출 수 있는 비율 중 덧댈 픽셀이 가장 적은 프리셋과 덧댄 뒤 크기."""
    best: tuple[int, str, int, int] | None = None
    for ratio in ASPECT_RATIOS:
        rw, rh = (int(v) for v in ratio.split(":"))
        if width * rh >= height * rw:  # 원본이 더 넓다 → 아래로 덧댄다
            pad_w, pad_h = width, -(-width * rh // rw)
        else:
            pad_w, pad_h = -(-height * rw // rh), height
        added = pad_w * pad_h - width * height
        if best is None or added < best[0]:
            best = (added, ratio, pad_w, pad_h)
    assert best is not None
    return best[1], best[2], best[3]


def estimate_cost(input_tokens: int, output_tokens: int, thought_tokens: int = 0) -> float:
    return (input_tokens * PRICE_PER_INPUT_TOKEN + output_tokens * PRICE_PER_OUTPUT_TOKEN
            + thought_tokens * PRICE_PER_THOUGHT_TOKEN)


def _client(client: Any) -> Any:
    if client is not None:
        return client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiImageError("GEMINI_API_KEY 환경 변수가 필요합니다")
    from google import genai

    # SDK 자동 재시도는 끈다. 시간 초과·5xx 뒤 같은 요청이 다시 나가면 이미지 편집이 이중 과금될 수 있다.
    # google-genai 2.6은 HttpRetryOptions(attempts=0)을 1로 바꿔 interactions에 재시도 1회가 남으므로,
    # interactions 전용 클라이언트에서 직접 0으로 맞춘다.
    return genai.Client(api_key=api_key)._nextgen_client.with_options(max_retries=0, timeout=300)


def edit_image(
    image: np.ndarray,
    prompt: str,
    work_dir: Path,
    *,
    model: str = GEMINI_IMAGE_MODEL,
    quality: str = "medium",
    client: Any = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """페이지(BGR)를 편집해 같은 크기의 결과와 메타(시간·토큰·추정 비용)를 돌려준다."""
    image_size = IMAGE_SIZE_FOR_QUALITY.get(quality)
    if image_size is None:
        raise GeminiImageError(f"지원하지 않는 quality입니다: {quality}")
    height, width = image.shape[:2]
    ratio, pad_w, pad_h = fit_aspect_ratio(width, height)
    padded = cv2.copyMakeBorder(image, 0, pad_h - height, 0, pad_w - width, cv2.BORDER_REPLICATE)
    work_dir.mkdir(parents=True, exist_ok=True)
    request_path = work_dir / "gemini_image_request.png"
    cv2.imwrite(str(request_path), padded)

    client = _client(client)
    started = time.perf_counter()
    response = client.interactions.create(
        model=model,
        input=[
            {"type": "text", "text": prompt},
            {"type": "image", "data": base64.b64encode(request_path.read_bytes()).decode("ascii"), "mime_type": "image/png"},
        ],
        response_format={"type": "image", "mime_type": "image/jpeg", "aspect_ratio": ratio, "image_size": image_size},
    )
    elapsed = time.perf_counter() - started

    output = getattr(response, "output_image", None)
    data = getattr(output, "data", None) if output is not None else None
    if not data:
        raise GeminiImageError("이미지 응답이 비어 있습니다")
    generated = cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR)
    if generated is None:
        raise GeminiImageError("이미지 응답을 읽을 수 없습니다")
    gen_h, gen_w = generated.shape[:2]
    if abs(gen_w / gen_h - pad_w / pad_h) > 0.02:
        # 요청 비율과 다르게 오면 늘이면 그림이 틀어지므로 쓰지 않는다
        raise GeminiImageError(f"요청 비율({ratio})과 다른 응답({gen_w}×{gen_h})")
    interpolation = cv2.INTER_AREA if gen_w >= pad_w else cv2.INTER_CUBIC
    result = cv2.resize(generated, (pad_w, pad_h), interpolation=interpolation)[:height, :width].copy()

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "total_input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "total_output_tokens", 0) or 0)
    thought_tokens = int(getattr(usage, "total_thought_tokens", 0) or 0)
    meta = {
        "model": model,
        "quality": quality,
        "image_size": image_size,
        "aspect_ratio": ratio,
        "request_size": [pad_w, pad_h],
        "response_size": [gen_w, gen_h],
        "elapsed_sec": round(elapsed, 1),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thought_tokens": thought_tokens,
        "estimated_cost_usd": round(estimate_cost(input_tokens, output_tokens, thought_tokens), 4),
    }
    return result, meta
