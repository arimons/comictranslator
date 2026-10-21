# 이미지 모델로 렌더링 생략 페이지를 번역 이미지로 재생성하는 모듈 (OpenAI gpt-image / Gemini 이미지 모델)
#!/usr/bin/env python3

from __future__ import annotations

import base64
import mimetypes
import os
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from pipeline import gemini_image
from pipeline.openai_tasks import IMAGE_MODEL

load_dotenv()

LogCallback = Callable[[str], None]

GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_SIDE = 3840
GPT_IMAGE_2_MAX_RATIO = 3.0


def _emit(message: str, log_callback: LogCallback | None = None) -> None:
    print(message, flush=True)
    if log_callback is not None:
        log_callback(message)


def image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def nearest_multiple(value: float, step: int = 16) -> int:
    return max(step, int(round(value / step)) * step)


def gpt_image_2_size_from_image(image_path: str | Path) -> str:
    """원본 이미지 크기를 이미지 모델 제약에 맞는 16px 배수 size 문자열로 변환한다.

    제약 수치는 gpt-image-2 기준으로 정했고, 2.5-sunburst에서도 같다고 보고 쓴다(최소 픽셀은 2.5 실측과 일치).
    """
    with Image.open(image_path) as img:
        width, height = img.size
    if width <= 0 or height <= 0:
        return "auto"
    aspect = width / height
    if aspect > GPT_IMAGE_2_MAX_RATIO:
        width = int(height * GPT_IMAGE_2_MAX_RATIO)
    elif aspect < 1 / GPT_IMAGE_2_MAX_RATIO:
        height = int(width * GPT_IMAGE_2_MAX_RATIO)

    scale = min(1.0, GPT_IMAGE_2_MAX_SIDE / max(width, height))
    pixels = width * height * scale * scale
    if pixels > GPT_IMAGE_2_MAX_PIXELS:
        scale *= (GPT_IMAGE_2_MAX_PIXELS / pixels) ** 0.5
    elif pixels < GPT_IMAGE_2_MIN_PIXELS:
        scale *= (GPT_IMAGE_2_MIN_PIXELS / max(pixels, 1)) ** 0.5

    out_w = nearest_multiple(width * scale)
    out_h = nearest_multiple(height * scale)
    if max(out_w, out_h) > GPT_IMAGE_2_MAX_SIDE:
        side_scale = GPT_IMAGE_2_MAX_SIDE / max(out_w, out_h)
        out_w = nearest_multiple(out_w * side_scale)
        out_h = nearest_multiple(out_h * side_scale)
    while out_w * out_h > GPT_IMAGE_2_MAX_PIXELS:
        out_w = max(16, out_w - 16)
        out_h = max(16, nearest_multiple(out_w / (width / height)))
    while out_w * out_h < GPT_IMAGE_2_MIN_PIXELS:
        out_w += 16
        out_h = nearest_multiple(out_w / (width / height))
    return f"{out_w}x{out_h}"


def resolved_image_size(image_path: str | Path, size: str = "match_input") -> str:
    if size == "match_input":
        return gpt_image_2_size_from_image(image_path)
    return size or "auto"


def _layout_notes_prompt(prompt_guide: str) -> str:
    return f"""You are preparing a manga page for image-to-image translation.

Read the page and produce concise Korean image-generation guidance.

Return:
1. Page context and title/series hints if visible.
2. Region-by-region Korean translation guidance.
3. Layout constraints: speech bubble positions, vertical/horizontal writing, SFX/title style, font mood.
4. Any text that should be preserved as-is.

Global user guide:
{prompt_guide or "(none)"}
"""


def _layout_notes_with_gemini(
    image_path: Path, prompt: str, model_name: str, log_callback: LogCallback | None = None
) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        _emit("❌ GEMINI_API_KEY 환경변수가 필요합니다", log_callback)
        return ""
    from google import genai
    from google.genai import types

    _emit(f"🧭 번역/레이아웃 가이드 추출: {model_name}", log_callback)
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    try:
        response = genai.Client(api_key=api_key).models.generate_content(
            model=model_name,
            contents=[prompt, types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type)],
            config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="low")),
        )
    except Exception as exc:
        _emit(f"⚠️ 번역/레이아웃 가이드 추출 실패: {exc}", log_callback)
        return ""
    notes = str(getattr(response, "text", "") or "").strip()
    if notes:
        _emit("✅ 번역/레이아웃 가이드 추출 완료", log_callback)
    return notes


def build_translation_layout_notes(
    image_path: str | Path,
    model_name: str = "gpt-6.1-sol",
    prompt_guide: str = "",
    log_callback: LogCallback | None = None,
) -> str:
    """비전 텍스트 모델로 이미지 생성용 번역/레이아웃 지시문을 만든다. gemini-* 모델이면 Gemini로 보낸다."""
    image_path = Path(image_path)
    prompt = _layout_notes_prompt(prompt_guide)
    if gemini_image.is_gemini_model(model_name):
        return _layout_notes_with_gemini(image_path, prompt, model_name, log_callback)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        _emit("❌ OPENAI_API_KEY 환경변수가 필요합니다", log_callback)
        return ""
    try:
        from openai import OpenAI
    except ImportError:
        _emit("❌ openai 패키지가 필요합니다. requirements.txt를 설치하세요", log_callback)
        return ""

    client = OpenAI(api_key=api_key)
    _emit(f"🧭 번역/레이아웃 가이드 추출: {model_name}", log_callback)
    try:
        response = client.responses.create(
            model=model_name,
            reasoning={"effort": "low"},
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_data_url(image_path), "detail": "high"},
                    ],
                }
            ],
        )
    except Exception as exc:
        _emit(f"⚠️ 번역/레이아웃 가이드 추출 실패: {exc}", log_callback)
        return ""
    notes = str(getattr(response, "output_text", "") or "").strip()
    if notes:
        _emit("✅ 번역/레이아웃 가이드 추출 완료", log_callback)
    return notes


def redraw_image_with_openai(
    image_path: str | Path,
    output_path: str | Path,
    model_name: str = IMAGE_MODEL,
    prompt_guide: str = "",
    translation_layout_notes: str = "",
    text_model_name: str = "gpt-6.1-sol",
    analyze_first: bool = False,
    quality: str = "medium",
    size: str = "match_input",
    output_format: str = "jpeg",
    output_compression: int | None = 92,
    log_callback: LogCallback | None = None,
) -> bool:
    """이미지 편집 모델로 원본 페이지의 일본어 텍스트를 한국어 이미지로 재생성한다.
    model_name이 gemini-*이면 Gemini 이미지 모델로 보낸다."""
    use_gemini = gemini_image.is_gemini_model(model_name)
    api_key = os.getenv("GEMINI_API_KEY" if use_gemini else "OPENAI_API_KEY")
    if not api_key:
        _emit(f"❌ {'GEMINI_API_KEY' if use_gemini else 'OPENAI_API_KEY'} 환경변수가 필요합니다", log_callback)
        return False

    image_path = Path(image_path)
    output_path = Path(output_path)
    if not image_path.exists():
        _emit(f"❌ 원본 이미지를 찾을 수 없습니다: {image_path}", log_callback)
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if analyze_first and not translation_layout_notes:
        translation_layout_notes = build_translation_layout_notes(
            image_path,
            model_name=text_model_name,
            prompt_guide=prompt_guide,
            log_callback=log_callback,
        )
    prompt = """Translate this manga page image into Korean and return the edited image.

Requirements:
- Replace visible Japanese dialogue, captions, and sound effects with natural Korean.
- Preserve the original panel layout, character art, backgrounds, speech bubbles, and typography as much as possible.
- Keep the same page orientation and image bounds.
- Do not add explanations, watermarks, borders, or extra objects.
- If a text area is difficult to typeset exactly, prioritize a readable Korean image that preserves the page design."""
    if prompt_guide.strip():
        prompt += f"\n\nGlobal translation prompt guide:\n{prompt_guide.strip()}"
    if translation_layout_notes.strip():
        prompt += f"\n\nPage-specific translation and layout notes:\n{translation_layout_notes.strip()}"

    if use_gemini:
        return _redraw_with_gemini(image_path, output_path, prompt, model_name, quality,
                                   output_format, output_compression, log_callback)

    try:
        from openai import OpenAI
    except ImportError:
        _emit("❌ openai 패키지가 필요합니다. requirements.txt를 설치하세요", log_callback)
        return False

    client = OpenAI(api_key=api_key)
    _emit(f"🎨 OpenAI 이미지 생성 시작: {image_path.name}", log_callback)
    _emit(f"🤖 모델: {model_name}", log_callback)
    request_size = resolved_image_size(image_path, size)
    _emit(f"🖼️ 출력 설정: size={request_size}, quality={quality}, format={output_format}", log_callback)
    try:
        with image_path.open("rb") as image_file:
            kwargs = {
                "model": model_name,
                "image": [image_file],
                "prompt": prompt,
                "size": request_size,
                "quality": quality or "medium",
                "output_format": output_format or "jpeg",
                "n": 1,
            }
            if (output_format or "").lower() in {"jpeg", "webp"} and output_compression is not None:
                kwargs["output_compression"] = max(0, min(100, int(output_compression)))
            response = client.images.edit(**kwargs)
    except Exception as exc:
        _emit(f"❌ OpenAI 이미지 생성 실패: {exc}", log_callback)
        return False

    data = getattr(response, "data", None) or []
    if not data:
        _emit("❌ OpenAI 이미지 응답이 비어있습니다", log_callback)
        return False
    b64_json = getattr(data[0], "b64_json", None)
    if not b64_json:
        _emit("❌ OpenAI 이미지 응답에 b64_json이 없습니다", log_callback)
        return False
    output_path.write_bytes(base64.b64decode(b64_json))
    _emit(f"✅ 이미지 생성 완료: {output_path}", log_callback)
    return True


def _redraw_with_gemini(
    image_path: Path,
    output_path: Path,
    prompt: str,
    model_name: str,
    quality: str,
    output_format: str,
    output_compression: int | None,
    log_callback: LogCallback | None = None,
) -> bool:
    """Gemini 이미지 모델 경로. 크기는 비율 프리셋 덧대기로 원본 크기에 맞추므로 size 설정은 쓰지 않는다."""
    import tempfile

    import cv2

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        _emit(f"❌ 원본 이미지를 읽을 수 없습니다: {image_path}", log_callback)
        return False
    level = quality if quality in gemini_image.IMAGE_SIZE_FOR_QUALITY else "medium"
    _emit(f"🎨 Gemini 이미지 생성 시작: {image_path.name}", log_callback)
    _emit(f"🤖 모델: {model_name}", log_callback)
    _emit(f"🖼️ 출력 설정: {gemini_image.IMAGE_SIZE_FOR_QUALITY[level]}, 원본 크기로 맞춤, format={output_format}", log_callback)
    try:
        with tempfile.TemporaryDirectory() as work_dir:
            result, meta = gemini_image.edit_image(image, prompt, Path(work_dir), model=model_name, quality=level)
    except Exception as exc:
        _emit(f"❌ Gemini 이미지 생성 실패: {exc}", log_callback)
        return False

    fmt = (output_format or "jpeg").lower()
    params: list[int] = []
    if fmt in {"jpeg", "jpg"}:
        params = [cv2.IMWRITE_JPEG_QUALITY, max(0, min(100, int(output_compression if output_compression is not None else 92)))]
    elif fmt == "webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, max(0, min(100, int(output_compression if output_compression is not None else 92)))]
    ext = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "webp": ".webp"}.get(fmt, ".jpg")
    ok, buf = cv2.imencode(ext, result, params)
    if not ok:
        _emit("❌ 생성 이미지를 저장 형식으로 바꾸지 못했습니다", log_callback)
        return False
    output_path.write_bytes(buf.tobytes())
    _emit(f"✅ 이미지 생성 완료: {output_path} ({meta['elapsed_sec']}초, 약 ${meta['estimated_cost_usd']})", log_callback)
    return True
