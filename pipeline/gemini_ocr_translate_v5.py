#!/usr/bin/env python3
"""
Gemini OCR + Translation + Integrated JSON 생성.

표준 경로는 flood indexed OCR sheet를 Gemini 입력으로 사용한다.
OCR sheet가 없을 때만 기존 원본 이미지 + CV cluster 힌트 경로로 fallback한다.
"""

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np
import google.genai as genai
from google.genai import types
from dotenv import load_dotenv

try:
    from pipeline.font_style_analyzer import (
        _normalized_render_hint,
        _clip_rect,
        _union_rects,
        _median,
        _japanese_text_len,
        _content_origin_in_sheet,
        _style_crop_rect_in_sheet,
        _map_original_rect_to_sheet,
        _measure_style_features_for_crop,
        _aggregate_line_style_features,
        _measure_region_style_features,
        _final_render_hint,
        _should_preserve_original_by_style,
        _style_features_for_anchor_ids,
    )
except ImportError:
    from font_style_analyzer import (
        _normalized_render_hint,
        _clip_rect,
        _union_rects,
        _median,
        _japanese_text_len,
        _content_origin_in_sheet,
        _style_crop_rect_in_sheet,
        _map_original_rect_to_sheet,
        _measure_style_features_for_crop,
        _aggregate_line_style_features,
        _measure_region_style_features,
        _final_render_hint,
        _should_preserve_original_by_style,
        _style_features_for_anchor_ids,
    )

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Step 4 기본 모델. --model 인자로 교체할 수 있다.
MODEL_NAME = "gemini-3-flash-preview"

# Gemini thinking budget. 문맥 번역은 품질이 필요하므로 0이 아닌 값을 쓴다.
GEMINI_THINKING_BUDGET = 1024
# OpenAI reasoning effort. none / low / medium / high / xhigh / max
OPENAI_REASONING_EFFORT = "low"

OCR_SHEET_TARGET_REGIONS = 5
OCR_SHEET_SOFT_MAX_REGIONS = 6
GEMINI_CALL_TIMEOUT_SECONDS = 35


# UI에 노출할 모델 목록. 근거는 docs/OCR_MODEL_COMPARISON.md 참고.
SUPPORTED_OCR_MODELS = [
    {
        "id": "gemini-3-flash-preview",
        "label": "Gemini 3 Flash (기본)",
        "note": "품질 동등하면서 가장 빠름",
    },
    {
        "id": "gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "note": "동등 품질 대안. 약 1.8배 느림",
    },
    {
        "id": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "note": "저가. 번역문 문장부호 잔존과 문맥 오역이 관측됨",
    },
]


def resolve_provider(model_name: str) -> str:
    """모델명으로 provider를 판별한다."""
    if model_name.startswith("gemini"):
        return "gemini"
    if model_name.startswith("gpt"):
        return "openai"
    raise ValueError(
        f"지원하지 않는 모델입니다: {model_name}. "
        f"'gemini-' 또는 'gpt-'로 시작하는 모델명을 사용하세요."
    )


def _require_api_key(provider: str) -> str:
    """provider에 필요한 API 키를 반환한다. 없으면 그 시점에 실패한다."""
    if provider == "gemini":
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY 환경 변수가 설정되어 있지 않습니다")
        return GEMINI_API_KEY
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 환경 변수가 설정되어 있지 않습니다")
    return OPENAI_API_KEY


def _normalize_text_for_rendering(text: Any) -> str:
    """렌더 폰트에서 빠지는 일부 OCR 기호를 안정적인 문자로 치환한다."""
    return str(text or "").replace("｜", "|")


def validate_gemini_ocr_regions(regions: list, expected_region_ids: set[int]) -> tuple[bool, str]:
    """Gemini OCR 응답이 후속 integrated 변환에 충분한지 검증한다."""
    if not isinstance(regions, list):
        return True, "반환된 regions가 리스트 형태가 아님"

    returned_by_id = {}
    for r in regions:
        if not isinstance(r, dict):
            return True, "regions 내에 객체가 아닌 요소가 포함됨"
        rid = r.get("region_id")
        if rid is not None:
            try:
                returned_by_id[int(rid)] = r
            except (ValueError, TypeError):
                return True, f"유효하지 않은 region_id 형식: {rid}"

    missing_ids = expected_region_ids - set(returned_by_id.keys())
    if missing_ids:
        return True, f"필수 region_id 누락: {sorted(list(missing_ids))}"

    for rid, r in returned_by_id.items():
        segments = r.get("segments")
        check_items = segments if isinstance(segments, list) and segments else [r]
        for seg_idx, item in enumerate(check_items, start=1):
            if not isinstance(item, dict):
                return True, f"Region {rid}: segment {seg_idx}가 객체가 아님"
            ocr_text = item.get("ocr_text")
            text_translated = item.get("text_translated")

            if ocr_text is None:
                return True, f"Region {rid}: segment {seg_idx} ocr_text가 null임 (OCR 실패)"

            if ocr_text.strip() != "":
                if text_translated is None:
                    return True, f"Region {rid}: segment {seg_idx} 원문이 존재하나 번역문이 null임"
                if text_translated.strip() == "":
                    return True, f"Region {rid}: segment {seg_idx} 원문이 존재하나 번역문이 빈 문자열임"
            render_hint = item.get("render_hint", r.get("render_hint"))
            if render_hint is not None and _normalized_render_hint(render_hint) == "normal" and str(render_hint or "normal") != "normal":
                return True, f"Region {rid}: segment {seg_idx} render_hint 허용값 밖: {render_hint}"

    return False, ""


JAPANESE_TO_KOREAN_STYLE_GUIDE = """\
번역 스타일 지침 (일본식 표현 → 자연스러운 한국어):
- 일본어 감탄사, 결의 표현, 직역투를 한국 독자가 자연스럽게 읽는 말로 완만하게 다듬으세요.
- 「絶対に」, 「ぜったいに」, 「zettaini」처럼 긍정 의지나 약속을 강조하는 표현은 「반드시」, 「꼭」, 「무조건」처럼 문맥에 맞게 번역하세요. 예: 「絶対に勝つ」→「반드시 이긴다」
- 금지나 부정 의미의 「絶対に〜ない」, 「絶対〜するな」는 「절대 ~하지 않는다」, 「절대 ~하지 마라」처럼 부정 의미를 유지하세요.
- 「~하지 않으면 안 된다」→「~해야 한다」
- 「~만 같으면」→「~라면」
- 「대단히」→「매우」또는「정말」
- 「역시」→ 문맥에 따라「역시나」/「과연」/「그렇구나」로 자연스럽게
- 「어째서」→「왜」
- 「~군」(君, 感嘆)→「~구나」/「~네」
- 「~이다 만」→「~이긴 한데」
- 효과음(sfx)은 한국어 의성어·의태어로 자연스럽게 의역 (ドーン→쾅, ガッ→퍽 등)
- 원문에 없는 쉼표(,)나 기타 문장부호를 번역문에 추가하지 마세요. 원문의 줄바꿈·공백 리듬을 그대로 따르세요.
"""

PACKED_OCR_PROMPT = f"""\
입력 이미지는 OCR sheet 1장입니다.
OCR sheet는 일본 만화의 말풍선 텍스트를 모아 놓은 이미지입니다.
각 타일의 좌상단에 영역 라벨이 표시되며, 형식은 "R숫자" 또는 "U숫자"입니다 (예: R1, U3).
타일 안에 작은 글씨로 "TG숫자" 서브라벨이 추가로 표시될 수 있지만, 이는 내부 참조 번호이므로 region_id로 절대 사용하지 마세요.
region_id는 반드시 "R숫자" 또는 "U숫자"의 숫자 부분만 사용합니다 (예: R4→4, U5→5).

각 Region에 대해 정확히 다음을 수행하세요.
1. ocr_text: 이미지에 보이는 일본어 원문 그대로. 줄바꿈은 \\n으로 유지하세요.
2. text_translated: 자연스러운 한국어 번역. 한국어는 가로쓰기 기준으로 작성하세요.
3. orientation: "vertical" 또는 "horizontal"
4. render_hint: 원문 crop 안의 일본어 글자 형태만 보고 아래 6종 중 하나를 약한 렌더링 힌트로 고르세요.
   이 값은 신뢰도가 낮은 1차 hint_raw로만 사용되고, 후처리에서 CV 기반 분석으로 다시 보정됩니다.
   확신이 낮으면 반드시 "normal"로 두세요.
   대사 내용, 감정, 번역문, 글자 크기, 말풍선 크기는 render_hint 판단 근거로 쓰지 마세요.
   - "normal"         : 균일하고 평범한 기본 대화 서체. 세리프가 강하지 않고 손글씨성이 약하며, 굵기도 보통입니다.
   - "memory_serif"   : 회상/서술 장면에 쓰이는 얇은 명조/바탕 계열 서체. normal보다 획이 가늘고 차분하며, 강조 효과음처럼 크거나 과장되지는 않습니다.
   - "semibold"       : normal보다 살짝 두껍지만 impact처럼 과장되지는 않은 반굵은 대사/이름표 서체.
   - "bold"           : normal보다 확실히 굵지만 impact처럼 외곽선/검은 채움이 과장되지는 않은 굵은 서체.
   - "impact"         : 획 자체가 normal보다 명백히 굵거나, 검은 채움 때문에 글자가 통통하고 강하게 보이는 강한 굵은 서체.
   - "serif_emphasis" : 명조/serif 계열처럼 획 끝의 삐침, 세로획/가로획 대비, 드라마틱한 인쇄체 느낌이 강한 강조 서체.

{JAPANESE_TO_KOREAN_STYLE_GUIDE}

주의사항:
- 한 Region 안에 시각적으로 분리된 텍스트 블록이 여러 개 있으면 segments 배열로 나누어 반환하세요.
- 분리 기준은 말풍선 내부에서 텍스트 덩어리 사이의 공간이 충분히 떨어져 있고, 서로 다른 문장 또는 독립된 의미 단위로 읽히는 경우입니다.
- 단순히 세로줄이 여러 줄인 하나의 문장은 segments로 나누지 마세요.
- 각 segment에는 가능한 경우 해당 TG 서브라벨 번호를 matched_tg_ids에 넣으세요.
- TG 번호는 region_id로 쓰지 말고, segment와 원본 위치를 연결하는 힌트로만 사용하세요.
- 라벨(R1, R2 ...), TG 라벨(TG1, TG2 ...), crop 경계선은 OCR 결과에 포함하지 마세요.
- 이미지에 텍스트가 분명히 존재하는데 번역을 누락하거나 null로 채우지 마세요. 글씨가 있으면 반드시 ocr_text와 text_translated를 모두 완벽히 기재해야 합니다.
- 진짜 글씨가 전혀 없는 빈 영역인 경우에만 ocr_text와 text_translated를 모두 ""(빈 문자열)로 작성해 주세요. null 값은 절대 응답에 포함하지 마세요.
- 각 region의 region_id는 누락 없이 반드시 "R숫자"/"U숫자" 라벨의 숫자 부분으로 정확히 매핑하여 출력해야 합니다. 임의로 순서를 바꾸거나 인덱스를 건너뛰지 마세요.
- region_id는 타일 좌상단의 "R숫자"/"U숫자" 라벨 숫자만 사용하세요. 타일 내부의 TG 서브라벨 숫자(TG9, TG10 등)는 region_id가 아닙니다.
- text_translated에 원문 일본어에 없는 문장부호를 절대 임의로 추가하지 마세요. 특히 쉼표(,)는 원문에 존재하지 않으면 번역문에도 절대 넣지 마세요. 원문의 줄바꿈이나 공백으로 표현된 리듬을 쉼표로 대체하는 것도 금지입니다.

반드시 순수 JSON으로만 응답하세요. Markdown 백틱은 쓰지 마세요.
{{"regions": [{{"region_id": 1, "ocr_text": "...", "text_translated": "...", "orientation": "vertical", "render_hint": "normal", "segments": [{{"matched_tg_ids": [1], "ocr_text": "...", "text_translated": "...", "orientation": "vertical", "render_hint": "normal"}}]}}]}}
"""



def _mime_type(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def _extract_json_response(response_text: str) -> Dict[str, Any]:
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # ```json ... ``` 뿐 아니라 언어 태그 없는 ``` ... ``` 펜스도 벗긴다.
        fenced = re.search(r"```(?:json)?\s*(.*?)```", response_text, re.S)
        if fenced:
            return json.loads(fenced.group(1).strip())
        raise


def _run_openai_json(
    image_path: str | list[str],
    prompt: str,
    temperature: float,
    model_name: str,
    max_retries: int = 4,
) -> tuple[Dict[str, Any], float]:
    """OpenAI Responses API로 OCR sheet를 처리한다.

    temperature는 받지만 사용하지 않는다. GPT-5.6 계열은 reasoning effort로
    출력 성향을 제어하고 temperature를 지원하지 않는다.
    """
    import base64

    from openai import OpenAI

    client = OpenAI(api_key=_require_api_key("openai"))
    image_paths = image_path if isinstance(image_path, list) else [image_path]

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for path in image_paths:
        with open(path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{_mime_type(path)};base64,{encoded}",
            }
        )

    started_at = time.time()
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            wait = 2 ** attempt
            print(f"  ⏳ OpenAI 재시도 {attempt}/{max_retries - 1} ({wait}s 대기)...")
            time.sleep(wait)
        try:
            response = client.responses.create(
                model=model_name,
                input=[{"role": "user", "content": content}],
                reasoning={"effort": OPENAI_REASONING_EFFORT},
                text={"format": {"type": "json_object"}},
            )
            elapsed = time.time() - started_at
            return _extract_json_response(response.output_text.strip()), elapsed
        except Exception as exc:
            last_exc = exc
            err_str = str(exc)
            if any(code in err_str for code in ("503", "429", "rate_limit", "overloaded")):
                continue
            raise
    raise RuntimeError(f"OpenAI API {max_retries}회 재시도 실패: {last_exc}") from last_exc


def _run_model_json(
    image_path: str | list[str],
    prompt: str,
    temperature: float,
    model_name: str | None = None,
    max_retries: int = 4,
) -> tuple[Dict[str, Any], float]:
    """모델명에 맞는 provider로 분기해 JSON 응답을 받는다."""
    model = model_name or MODEL_NAME
    if resolve_provider(model) == "openai":
        return _run_openai_json(image_path, prompt, temperature, model, max_retries)
    return _run_gemini_json(image_path, prompt, temperature, max_retries, model)


def _run_gemini_json(
    image_path: str | list[str],
    prompt: str,
    temperature: float,
    max_retries: int = 4,
    model_name: str | None = None,
) -> tuple[Dict[str, Any], float]:
    model = model_name or MODEL_NAME
    client = genai.Client(api_key=_require_api_key("gemini"))
    image_paths = image_path if isinstance(image_path, list) else [image_path]
    image_parts = []
    for path in image_paths:
        with open(path, "rb") as image_file:
            image_parts.append(types.Part.from_bytes(data=image_file.read(), mime_type=_mime_type(path)))

    generate_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=temperature,
        thinking_config=types.ThinkingConfig(thinking_budget=GEMINI_THINKING_BUDGET),
    )

    started_at = time.time()
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            wait = 2 ** attempt
            print(f"  ⏳ Gemini 재시도 {attempt}/{max_retries - 1} ({wait}s 대기)...")
            time.sleep(wait)
        try:
            if threading.current_thread() is threading.main_thread():
                def _timeout_handler(_signum, _frame):
                    raise TimeoutError(f"Gemini API 호출 timeout {GEMINI_CALL_TIMEOUT_SECONDS}s")

                previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(GEMINI_CALL_TIMEOUT_SECONDS)
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=[prompt, *image_parts],
                        config=generate_config,
                    )
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, previous_handler)
            else:
                response = client.models.generate_content(
                    model=model,
                    contents=[prompt, *image_parts],
                    config=generate_config,
                )
            elapsed = time.time() - started_at
            return _extract_json_response(response.text.strip()), elapsed
        except Exception as exc:
            last_exc = exc
            err_str = str(exc)
            # 503 / 429 / resource_exhausted 는 재시도 가능
            if any(code in err_str for code in ("503", "429", "RESOURCE_EXHAUSTED", "overloaded")):
                continue
            raise
    raise RuntimeError(f"Gemini API {max_retries}회 재시도 실패: {last_exc}") from last_exc


def _bbox_union(rects: list[list[int]]) -> list[int]:
    if not rects:
        return [0, 0, 0, 0]
    xs = [r[0] for r in rects]
    ys = [r[1] for r in rects]
    x2s = [r[0] + r[2] for r in rects]
    y2s = [r[1] + r[3] for r in rects]
    min_x, min_y = min(xs), min(ys)
    return [min_x, min_y, max(x2s) - min_x, max(y2s) - min_y]


def create_unified_prompt(cv_clusters: List[Dict], extra_prompt: str = "") -> str:
    cluster_info = []
    for cluster in cv_clusters:
        cluster_id = cluster["cluster_id"]
        lines = cluster.get("lines", [])
        text = " ".join(line["text"] for line in lines)
        bbox = cluster.get("bounding_rect", [])
        cluster_info.append(f"Cluster {cluster_id} (bbox: {bbox}): {text}")

    clusters_text = "\n".join(cluster_info)
    extra_instruction = f"\n추가 지시:\n- {extra_prompt}" if extra_prompt else ""
    return f"""당신은 일본어 만화/웹툰 전문 번역가입니다.

Cloud Vision API 감지 텍스트 힌트:
{clusters_text}

중요 BBOX 병합 지침:
1. 시각적으로 한 개의 말풍선에 들어있는 1개의 완결된 문장이 Cloud Vision 오류로 여러 CV 클러스터로 쪼개질 수 있습니다.
2. 이미지를 직접 보고 논리적으로 하나의 문장이 되는 조각들을 모두 찾아 완벽한 1개의 text와 text_translated로 병합하세요.
3. 병합된 문장의 출처가 된 모든 CV 클러스터 ID를 matched_cluster 배열에 담으세요.

번역 지침:
- 원문이 세로쓰기라도 한국어 번역문은 항상 가로쓰기 기준으로 자연스럽게 작성하세요.
{JAPANESE_TO_KOREAN_STYLE_GUIDE}
{extra_instruction}

반드시 다음 JSON Schema를 엄격히 준수하여 순수 JSON으로만 응답하세요.
{{
  "clusters": [
    {{
      "sentence_id": 1,
      "text": "일본어 원문",
      "text_translated": "한국어 번역문",
      "matched_cluster": [1, 2, 3],
      "orientation": "vertical"
    }}
  ]
}}
"""


def _cluster_details(anchor_ids: list[int], cv_cluster_map: dict[int, dict]) -> tuple[list[dict], list[list[int]]]:
    details = []
    rects = []
    for anchor_id in anchor_ids:
        cv_cluster = cv_cluster_map.get(anchor_id)
        if not cv_cluster:
            continue
        rect = cv_cluster.get("bounding_rect", [0, 0, 0, 0])
        rects.append(rect)
        details.append({
            "cluster_id": cv_cluster.get("cluster_id"),
            "text": cv_cluster.get("text", ""),
            "bounding_rect": rect,
            "lines": cv_cluster.get("lines", []),
        })
    return details, rects


def _normalized_scale_class(value: Any) -> str:
    scale_class = str(value or "normal")
    return scale_class if scale_class in ("small", "normal", "large", "xlarge") else "normal"


def _segments_for_region(gemini_region: dict) -> list[dict]:
    segments = gemini_region.get("segments")
    if isinstance(segments, list) and segments:
        return [s for s in segments if isinstance(s, dict)]
    return [gemini_region]


def _compact_japanese_text(text: Any) -> str:
    return "".join(ch for ch in str(text or "") if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")


def _best_tg_for_segment(
    segment: dict,
    available_ids: list[int],
    cv_cluster_map: dict[int, dict],
) -> int | None:
    """segment OCR 원문과 TG 원문 유사도로 가장 그럴듯한 TG를 고른다."""
    seg_text = _compact_japanese_text(segment.get("ocr_text"))
    if not seg_text:
        return None

    best_id: int | None = None
    best_score = -1
    for tg_id in available_ids:
        tg_text = _compact_japanese_text(cv_cluster_map.get(tg_id, {}).get("text", ""))
        if not tg_text:
            continue
        overlap = len(set(seg_text) & set(tg_text))
        containment = 0
        if seg_text in tg_text or tg_text in seg_text:
            containment = min(len(seg_text), len(tg_text))
        score = overlap * 10 + containment
        if score > best_score:
            best_score = score
            best_id = tg_id
    return best_id if best_score > 0 else None


def _resolve_segment_anchor_ids(
    segments: list[dict],
    region_anchor_ids: list[int],
    cv_cluster_map: dict[int, dict],
) -> list[list[int]]:
    """Gemini의 matched_tg_ids가 흔들릴 때 OCR 원문 기준으로 TG 매핑을 보정한다."""
    if len(segments) <= 1:
        return [region_anchor_ids]

    remaining = list(region_anchor_ids)
    resolved: list[list[int]] = []
    for segment in segments:
        chosen = _best_tg_for_segment(segment, remaining, cv_cluster_map)
        if chosen is None:
            raw_ids = []
            for raw_id in segment.get("matched_tg_ids") or segment.get("matched_cluster") or []:
                try:
                    tg_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if tg_id in remaining:
                    raw_ids.append(tg_id)
            chosen = raw_ids[0] if raw_ids else None
        if chosen is None:
            resolved.append(region_anchor_ids)
            continue
        resolved.append([chosen])
        remaining.remove(chosen)
    return resolved


def _render_rect_for_sheet_segment(
    region: dict,
    region_type: str,
    region_rect: list[int] | None,
    anchor_rects: list[list[int]],
    region_anchor_ids: list[int],
    anchor_ids: list[int],
    multi_segment: bool,
) -> list[int] | None:
    if region_type != "unboxed" or not multi_segment:
        return region_rect
    if not anchor_ids or sorted(anchor_ids) == sorted(region_anchor_ids):
        return region_rect
    segment_rect = _bbox_union(anchor_rects)
    return segment_rect or region_rect


def _is_unboxed_consolidated_region(region: dict) -> bool:
    flags = {str(flag) for flag in region.get("quality_flags", [])}
    if "unboxed_consolidated_tg_cluster" in flags:
        return True
    consolidated = region.get("consolidated_text_group") or {}
    tg_ids = consolidated.get("text_group_ids") or []
    return region.get("type") == "unboxed" and len(tg_ids) > 1


def _collapse_segments_for_consolidated_unboxed(
    region: dict,
    gemini_region: dict,
    segments: list[dict],
) -> list[dict]:
    """unboxed 전용 인접 TG 병합 region은 OCR segment를 다시 하나의 overlay 단위로 접는다."""
    if not _is_unboxed_consolidated_region(region) or len(segments) <= 1:
        return segments

    ocr_parts = [_normalize_text_for_rendering(segment.get("ocr_text")).strip() for segment in segments]
    translated_parts = [_normalize_text_for_rendering(segment.get("text_translated")).strip() for segment in segments]
    merged = {
        "ocr_text": "\n".join(part for part in ocr_parts if part),
        "text_translated": "\n".join(part for part in translated_parts if part),
        "orientation": gemini_region.get("orientation", segments[0].get("orientation", "vertical")),
        "text_scale_class": gemini_region.get("text_scale_class", segments[0].get("text_scale_class", "normal")),
        "render_hint": gemini_region.get("render_hint", segments[0].get("render_hint", "normal")),
        "matched_tg_ids": region.get("anchor_ids", []),
    }
    return [merged]


def _build_integrated_from_sheet_regions(
    gemini_regions: list[dict],
    sheet_json_path: str,
    cv_clusters_path: str,
    output_integrated_path: str,
    style_features_by_region: dict[int, dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    with open(sheet_json_path, "r", encoding="utf-8") as f:
        sheet = json.load(f)
    with open(cv_clusters_path, "r", encoding="utf-8") as f:
        cv_clusters = json.load(f)

    cv_cluster_map = {int(c.get("cluster_id")): c for c in cv_clusters}
    gemini_by_id = {}
    for r in gemini_regions:
        try:
            gemini_by_id[int(r.get("region_id"))] = r
        except (TypeError, ValueError):
            continue
    used_cv_ids: set[int] = set()
    final_clusters = []

    for region in sheet.get("regions", []):
        region_id = region.get("region_id")
        gemini_region = gemini_by_id.get(region_id)
        if not gemini_region:
            continue

        region_anchor_ids = [int(v) for v in region.get("anchor_ids", [])]
        
        region_type = region.get("type", "bubble")
        _all_details, all_anchor_rects = _cluster_details(region_anchor_ids, cv_cluster_map)
        if region_type == "unboxed":
            bubble_rect = region.get("unboxed_rect") or _bbox_union(all_anchor_rects)
        else:
            bubble_rect = region.get("bubble_rect") or region.get("original_rect") or _bbox_union(all_anchor_rects)

        segments = _collapse_segments_for_consolidated_unboxed(
            region,
            gemini_region,
            _segments_for_region(gemini_region),
        )
        multi_segment = len(segments) > 1
        resolved_segment_ids = _resolve_segment_anchor_ids(segments, region_anchor_ids, cv_cluster_map)
        for seg_idx, segment in enumerate(segments, start=1):
            translated = _normalize_text_for_rendering(segment.get("text_translated")).strip()
            ocr_text = _normalize_text_for_rendering(segment.get("ocr_text")).strip()
            if not translated:
                continue

            anchor_ids = resolved_segment_ids[seg_idx - 1] or region_anchor_ids
            used_cv_ids.update(anchor_ids)
            matched_cluster_details, anchor_rects = _cluster_details(anchor_ids, cv_cluster_map)
            render_rect = _render_rect_for_sheet_segment(
                region,
                region_type,
                bubble_rect,
                anchor_rects,
                region_anchor_ids,
                anchor_ids,
                multi_segment,
            )
            raw_render_hint = _normalized_render_hint(segment.get("render_hint", gemini_region.get("render_hint", "normal")))
            region_style_features = (style_features_by_region or {}).get(int(region_id), {})
            style_features = _style_features_for_anchor_ids(region_style_features, anchor_ids)
            render_hint_final = _final_render_hint(raw_render_hint, style_features)
            preserve_original, preserve_reason = _should_preserve_original_by_style(
                ocr_text,
                style_features,
            )

            cluster_data = {
                "sentence_id": int(region_id) * 100 + seg_idx if multi_segment else region_id,
                "text": ocr_text,
                "text_translated": translated,
                "orientation": segment.get("orientation", gemini_region.get("orientation", "vertical")),
                "text_scale_class": _normalized_scale_class(segment.get("text_scale_class", gemini_region.get("text_scale_class", "normal"))),
                "render_hint": render_hint_final,
                "render_hint_raw": raw_render_hint,
                "render_hint_final": render_hint_final,
                "style_features": style_features,

                "matched_cluster": anchor_ids,
                "matched_cluster_details": matched_cluster_details,
                "union_bbox": render_rect,
                "bounding_rect": render_rect,
                "location": region_type,
                "match_score": 1.0,
                "source_region_id": region_id,
                "source_region_rect": bubble_rect,
                "source_segment_index": seg_idx if multi_segment else None,
                "erase_rect": region.get("erase_rect"),
                "unboxed_rect": render_rect if region_type == "unboxed" else region.get("unboxed_rect"),
                "consolidated_text_group": region.get("consolidated_text_group") if region_type == "unboxed" else None,
                "inpaint_skipped": region.get("inpaint_skipped", False),
                "source_method": region.get("source_method"),
                "source_region_type": region.get("source_region_type"),
                "quality_flags": region.get("quality_flags", []),
                "review_tags": region.get("review_tags", []),
            }
            if preserve_original:
                cluster_data["preserve_original"] = True
                cluster_data["preserve_original_reason"] = preserve_reason
            final_clusters.append(cluster_data)

    unmatched_clusters = [
        {
            "cluster_id": cv_cluster.get("cluster_id"),
            "text": cv_cluster.get("text", ""),
            "bounding_rect": cv_cluster.get("bounding_rect", [0, 0, 0, 0]),
        }
        for cv_cluster in cv_clusters
        if cv_cluster.get("cluster_id") not in used_cv_ids
    ]

    integrated_data = {
        "clusters": final_clusters,
        "unmatched_clusters": unmatched_clusters,
        "model": MODEL_NAME,
        "ocr_source": "flood_indexed_sheet",
    }
    with open(output_integrated_path, "w", encoding="utf-8") as f:
        json.dump(integrated_data, f, ensure_ascii=False, indent=2)
    return integrated_data


def _same_rect(a: Any, b: Any) -> bool:
    return isinstance(a, list) and isinstance(b, list) and len(a) == 4 and len(b) == 4 and [int(v) for v in a] == [int(v) for v in b]


def _region_context_edge_penalty(left: dict, right: dict) -> float:
    """두 region 사이를 잘라도 되는 정도를 점수화한다. 높을수록 자르면 안 된다."""
    penalty = 0.0
    if _same_rect(left.get("split_parent_rect"), right.get("split_parent_rect")):
        penalty += 1000.0
    if left.get("source_order") is not None and left.get("source_order") == right.get("source_order"):
        penalty += 300.0

    left_flags = set(left.get("quality_flags") or []) | set(left.get("review_tags") or [])
    right_flags = set(right.get("quality_flags") or []) | set(right.get("review_tags") or [])
    if any("continuation" in str(v) for v in left_flags | right_flags):
        penalty += 60.0

    if left.get("source_region_type") == right.get("source_region_type"):
        penalty += 2.0
    if left.get("type") == right.get("type"):
        penalty += 1.0

    left_ids = left.get("anchor_ids") or []
    right_ids = right.get("anchor_ids") or []
    try:
        if left_ids and right_ids and abs(int(right_ids[0]) - int(left_ids[-1])) <= 1:
            penalty += 1.0
    except (TypeError, ValueError):
        pass
    return penalty


def _choose_chunk_end(regions: list[dict], start: int, target: int, soft_max: int) -> int:
    remaining = len(regions) - start
    if remaining <= soft_max:
        return len(regions)

    min_size = max(1, target - 1)
    max_size = max(target, soft_max)
    candidates = []
    for size in range(min_size, max_size + 1):
        end = start + size
        if end >= len(regions):
            continue
        penalty = _region_context_edge_penalty(regions[end - 1], regions[end])
        candidates.append((penalty, abs(size - target), end))

    if not candidates:
        return min(len(regions), start + target)

    _, _, chosen_end = min(candidates)
    # soft_max 를 초과하지 않는 범위 안에서만 경계 확장 (무한 오버슈트 방지)
    hard_cap = min(len(regions), start + soft_max)
    while chosen_end < hard_cap and _region_context_edge_penalty(regions[chosen_end - 1], regions[chosen_end]) >= 300:
        chosen_end += 1
    return chosen_end


def _split_regions_for_ocr_chunks(
    regions: list[dict],
    target: int = OCR_SHEET_TARGET_REGIONS,
    soft_max: int = OCR_SHEET_SOFT_MAX_REGIONS,
) -> list[list[dict]]:
    if len(regions) <= soft_max:
        return [regions]

    chunks = []
    start = 0
    while start < len(regions):
        end = _choose_chunk_end(regions, start, target, soft_max)
        chunks.append(regions[start:end])
        start = end
    return chunks


def _write_indexed_sheet_chunk(
    indexed_sheet_path: str,
    chunk_regions: list[dict],
    chunk_index: int,
    work_dir: Path,
) -> str:
    image = cv2.imread(indexed_sheet_path)
    if image is None:
        raise FileNotFoundError(f"OCR sheet 이미지 로드 실패: {indexed_sheet_path}")

    crops = []
    max_h = 1
    for region in chunk_regions:
        rect = region.get("indexed_sheet_rect") or region.get("sheet_rect")
        if not rect or len(rect) != 4:
            continue
        x, y, w, h = [int(v) for v in rect]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(image.shape[1], x + w), min(image.shape[0], y + h)
        if x2 <= x1 or y2 <= y1:
            continue
        crop = image[y1:y2, x1:x2].copy()
        crops.append(crop)
        max_h = max(max_h, crop.shape[0])

    if not crops:
        raise RuntimeError(f"OCR sheet chunk {chunk_index}에 유효한 crop이 없습니다")

    gap = 10
    total_w = sum(c.shape[1] for c in crops) + gap * max(0, len(crops) - 1)
    chunk_img = np.full((max_h, total_w, 3), 255, dtype=np.uint8)
    x = 0
    for i, crop in enumerate(crops):
        h, w = crop.shape[:2]
        chunk_img[0:h, x:x + w] = crop
        x += w + (gap if i < len(crops) - 1 else 0)

    out_path = work_dir / f"{Path(indexed_sheet_path).stem}_chunk_{chunk_index:02d}.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), chunk_img)
    return str(out_path)


def _process_sheet_ocr(
    sheet_image_path: str,
    sheet_json_path: str,
    cv_clusters_path: str,
    output_integrated_path: str,
    extra_prompt: str = "",
) -> Dict[str, Any]:
    try:
        sheet_work_dir = Path(sheet_image_path).parent
        missing_inputs = []
        if not Path(sheet_json_path).exists():
            missing_inputs.append("ocr_sheet_json")
        sheet_regions: list[Dict[str, Any]] = []
        sheet_meta: Dict[str, Any] = {}
        if not missing_inputs:
            try:
                with open(sheet_json_path, "r", encoding="utf-8") as _f:
                    sheet_meta = json.load(_f)
                raw_regions = sheet_meta.get("regions", [])
                sheet_regions = raw_regions if isinstance(raw_regions, list) else []
            except Exception:
                missing_inputs.append("ocr_sheet_json")
        if not missing_inputs and not sheet_regions:
            skipped = {
                "status": "skipped",
                "skipped": True,
                "stage": "ocr_translation",
                "reason": "no_ocr_regions",
                "message": "말풍선 분석은 완료됐지만 OCR 대상 region이 없어 OCR/번역을 건너뜁니다.",
                "missing_inputs": [],
                "analysis_status": sheet_meta.get("analysis_status") or "complete",
                "ocr_region_count": 0,
                "clusters": [],
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            Path(output_integrated_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_integrated_path, "w", encoding="utf-8") as f:
                json.dump(skipped, f, ensure_ascii=False, indent=2)
            return {
                "img_name": Path(sheet_image_path).stem,
                "success": True,
                "skipped": True,
                "output_path": output_integrated_path,
                "sentences": 0,
                "elapsed": 0.0,
            }
        if not Path(sheet_image_path).exists():
            missing_inputs.append("ocr_sheet_image")
        if missing_inputs:
            skipped = {
                "status": "skipped",
                "skipped": True,
                "stage": "ocr_translation",
                "reason": "missing_ocr_sheet",
                "message": "OCR sheet 산출물이 불완전해 Gemini OCR/번역을 건너뜁니다.",
                "missing_inputs": missing_inputs,
                "analysis_status": sheet_meta.get("analysis_status") or "unknown",
                "ocr_region_count": len(sheet_regions) if sheet_regions else None,
                "clusters": [],
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            Path(output_integrated_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_integrated_path, "w", encoding="utf-8") as f:
                json.dump(skipped, f, ensure_ascii=False, indent=2)
            return {
                "img_name": Path(sheet_image_path).stem,
                "success": True,
                "skipped": True,
                "output_path": output_integrated_path,
                "sentences": 0,
                "elapsed": 0.0,
            }

        prompt = PACKED_OCR_PROMPT
        if extra_prompt:
            prompt += f"\n\n추가 지시:\n{extra_prompt}\n"

        # 예상 region ID 목록 파악
        expected_ids = set()
        for r in sheet_regions:
            rid = r.get("region_id")
            if rid is not None:
                expected_ids.add(int(rid))

        # regions가 없으면 Gemini 호출 없이 빈 integrated.json 생성 후 즉시 반환
        if not sheet_regions:
            _build_integrated_from_sheet_regions([], sheet_json_path, cv_clusters_path, output_integrated_path)
            return {
                "img_name": Path(sheet_image_path).stem,
                "success": True,
                "output_path": output_integrated_path,
                "sentences": 0,
                "elapsed": 0.0,
            }

        ocr_chunks = _split_regions_for_ocr_chunks(sheet_regions)
        style_features_by_region = _measure_region_style_features(sheet_image_path, sheet_json_path, cv_clusters_path)
        if len(ocr_chunks) > 1:
            chunk_log = ", ".join(
                f"{i + 1}:[{','.join(str(r.get('region_id')) for r in chunk)}]"
                for i, chunk in enumerate(ocr_chunks)
            )
            print(f"  ✂️ OCR sheet chunk 분할: {chunk_log}")

        # 최대 3회 재시도 루프
        max_content_retries = 3
        all_returned_regions = []
        total_elapsed = 0.0

        for chunk_index, chunk_regions in enumerate(ocr_chunks, start=1):
            chunk_expected_ids = {
                int(r.get("region_id"))
                for r in chunk_regions
                if r.get("region_id") is not None
            }
            chunk_sheet_path = (
                _write_indexed_sheet_chunk(sheet_image_path, chunk_regions, chunk_index, sheet_work_dir)
                if len(ocr_chunks) > 1
                else sheet_image_path
            )
            input_images = [chunk_sheet_path]

            result = {}
            elapsed = 0.0
            final_retry_reason = ""
            for attempt in range(1, max_content_retries + 1):
                try:
                    result, elapsed = _run_model_json(input_images, prompt, temperature=0.25)
                    _returned = result.get("regions", [])

                    _retry, _reason = validate_gemini_ocr_regions(_returned, chunk_expected_ids)
                    if not _retry:
                        if attempt > 1:
                            print(f"  ✨ Gemini OCR/번역 검증 통과 chunk {chunk_index}/{len(ocr_chunks)} (시도 {attempt}/{max_content_retries})")
                        break

                    final_retry_reason = _reason
                    print(f"  ⚠️ Gemini OCR/번역 검증 실패 chunk {chunk_index}/{len(ocr_chunks)} — {_reason} (시도 {attempt}/{max_content_retries})")
                    if attempt < max_content_retries:
                        time.sleep(2)
                except Exception as e:
                    final_retry_reason = f"Gemini API 호출 실패: {str(e)}"
                    print(f"  ⚠️ Gemini API 호출 실패 chunk {chunk_index}/{len(ocr_chunks)} — {str(e)} (시도 {attempt}/{max_content_retries})")
                    if attempt == max_content_retries:
                        break
                    time.sleep(2)

            _returned = result.get("regions", []) if isinstance(result, dict) else []
            # expected가 비어있는 경우(regions=0 은 위에서 early return되므로 여기선 실질적으로 미도달)
            # result가 실제로 수신됐는지 여부로 판단 (빈 list와 미수신을 구분)
            _got_response = isinstance(result, dict) and result
            _retry, _reason = (
                validate_gemini_ocr_regions(_returned, chunk_expected_ids)
                if _got_response
                else (True, final_retry_reason)
            )
            if _retry:
                error_dump_path = output_integrated_path.replace(".json", f"_chunk_{chunk_index:02d}_error_dump.json")
                error_log_data = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "sheet_image_path": sheet_image_path,
                    "chunk_sheet_path": chunk_sheet_path,
                    "sheet_json_path": sheet_json_path,
                    "expected_region_ids": sorted(list(chunk_expected_ids)),
                    "last_attempt_result": result,
                    "failure_reason": _reason,
                    "max_attempts": max_content_retries,
                }
                try:
                    with open(error_dump_path, "w", encoding="utf-8") as _ef:
                        json.dump(error_log_data, _ef, ensure_ascii=False, indent=2)
                    error_save_msg = f"이상로그 덤프 파일 저장 완료: {error_dump_path}"
                except Exception as _log_e:
                    error_save_msg = f"이상로그 파일 저장 실패: {str(_log_e)}"

                print(f"\n❌ [이상로그] Gemini OCR/번역 검증 최종 실패 chunk {chunk_index}/{len(ocr_chunks)}", file=sys.stderr)
                print(f"    - 실패 사유: {_reason}", file=sys.stderr)
                print(f"    - {error_save_msg}\n", file=sys.stderr)

                raise RuntimeError(f"Gemini OCR 검증 실패 chunk {chunk_index}/{len(ocr_chunks)}: {_reason}")

            all_returned_regions.extend(_returned)
            total_elapsed += elapsed

        result = {"regions": all_returned_regions}

        integrated = _build_integrated_from_sheet_regions(
            result.get("regions", []),
            sheet_json_path,
            cv_clusters_path,
            output_integrated_path,
            style_features_by_region,
        )
        return {
            "img_name": Path(sheet_image_path).stem,
            "success": True,
            "output_path": output_integrated_path,
            "sentences": len(integrated.get("clusters", [])),
            "elapsed": total_elapsed,
        }
    except json.JSONDecodeError as e:
        return {"img_name": Path(sheet_image_path).stem, "success": False, "error": f"JSON 파싱 실패: {str(e)[:50]}"}
    except Exception as e:
        return {"img_name": Path(sheet_image_path).stem, "success": False, "error": str(e)[:100]}


def _process_single_ocr(
    image_path: str,
    cv_clusters_path: str,
    output_integrated_path: str,
    extra_prompt: str = "",
) -> Dict[str, Any]:
    img_name = Path(image_path).stem
    try:
        with open(cv_clusters_path, "r", encoding="utf-8") as f:
            cv_clusters = json.load(f)

        result, elapsed = _run_model_json(
            image_path,
            create_unified_prompt(cv_clusters, extra_prompt),
            temperature=0.2,
        )
        gemini_clusters = result.get("clusters", [])

        # Gemini가 비어있는 응답을 반환했으면 1회 재시도
        if not gemini_clusters and cv_clusters:
            print(f"  ⚠️ Gemini OCR 응답 비어있음 — 재시도...")
            time.sleep(2)
            result, elapsed = _run_model_json(
                image_path,
                create_unified_prompt(cv_clusters, extra_prompt),
                temperature=0.2,
            )
            gemini_clusters = result.get("clusters", [])
        cv_cluster_map = {int(cv_c.get("cluster_id")): cv_c for cv_c in cv_clusters}
        used_cv_ids = set()
        final_clusters = []

        for idx, sentence in enumerate(gemini_clusters):
            matched_ids = sentence.get("matched_cluster", sentence.get("source_clusters", []))
            if not isinstance(matched_ids, list):
                matched_ids = [matched_ids] if matched_ids else []
            anchor_ids = []
            for matched_id in matched_ids:
                try:
                    anchor_ids.append(int(matched_id))
                except (TypeError, ValueError):
                    continue
            used_cv_ids.update(anchor_ids)

            matched_cluster_details, union_rects = _cluster_details(anchor_ids, cv_cluster_map)
            union_bbox = _bbox_union(union_rects)
            final_clusters.append({
                "sentence_id": sentence.get("sentence_id", idx + 1),
                "text": _normalize_text_for_rendering(sentence.get("text", "")),
                "text_translated": _normalize_text_for_rendering(sentence.get("text_translated", "")),
                "orientation": sentence.get("orientation", "vertical"),
                "matched_cluster": anchor_ids,
                "matched_cluster_details": matched_cluster_details,
                "union_bbox": union_bbox,
                "bounding_rect": union_bbox,
                "location": "bubble",
                "match_score": 1.0,
            })

        unmatched_clusters = [
            {
                "cluster_id": cv_cluster.get("cluster_id"),
                "text": cv_cluster.get("text", ""),
                "bounding_rect": cv_cluster.get("bounding_rect", [0, 0, 0, 0]),
            }
            for cv_cluster in cv_clusters
            if cv_cluster.get("cluster_id") not in used_cv_ids
        ]

        integrated_data = {
            "clusters": final_clusters,
            "unmatched_clusters": unmatched_clusters,
            "model": MODEL_NAME,
            "ocr_source": "original_image_cv_clusters",
        }
        with open(output_integrated_path, "w", encoding="utf-8") as f:
            json.dump(integrated_data, f, ensure_ascii=False, indent=2)

        return {
            "img_name": img_name,
            "success": True,
            "output_path": output_integrated_path,
            "sentences": len(final_clusters),
            "elapsed": elapsed,
        }
    except json.JSONDecodeError as e:
        return {"img_name": img_name, "success": False, "error": f"JSON 파싱 실패: {str(e)[:50]}"}
    except Exception as e:
        return {"img_name": img_name, "success": False, "error": str(e)[:100]}


def process_ocr_and_translation_batch(
    image_paths: List[str],
    cv_clusters_paths: List[str],
    output_dir: str = "output",
    extra_prompt: str = "",
    max_workers: int = 8,
) -> Dict[str, Any]:
    print(f"\n{'=' * 60}")
    print("🚀 Gemini OCR + Translation 병렬 처리 시작")
    print(f"{'=' * 60}")
    print(f"📊 이미지 개수: {len(image_paths)}개")
    print(f"⚙️ 워커: {max_workers}개 병렬 처리")
    print(f"📁 출력 폴더: {output_dir}\n")

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for img_path, cv_path in zip(image_paths, cv_clusters_paths):
            img_name = Path(img_path).stem
            output_integrated_path = output_dir_path / f"{img_name}_integrated.json"
            future = executor.submit(
                _process_single_ocr,
                img_path,
                cv_path,
                str(output_integrated_path),
                extra_prompt,
            )
            futures[future] = img_name

        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            img_name = futures[future]
            if result.get("success"):
                print(f"  ✅ {completed}/{len(image_paths)} ({img_name}) - {result.get('sentences', 0)}개 문장")
            else:
                print(f"  ❌ {completed}/{len(image_paths)} ({img_name}) - {result.get('error', 'Unknown error')[:40]}")

    success_count = sum(1 for r in results if r.get("success"))
    failed_count = sum(1 for r in results if not r.get("success"))
    total_sentences = sum(r.get("sentences", 0) for r in results if r.get("success"))

    print(f"\n{'=' * 60}")
    print("✅ OCR + 번역 병렬 처리 완료")
    print(f"   📊 성공: {success_count}/{len(results)}")
    print(f"   📝 총 문장: {total_sentences}개")
    if failed_count > 0:
        print(f"   ❌ 실패: {failed_count}개")
    print(f"{'=' * 60}\n")

    return {
        "total": len(results),
        "success": success_count,
        "failed": failed_count,
        "total_sentences": total_sentences,
        "results": results,
    }


def process_ocr_and_translation(
    image_path: str,
    cv_clusters_path: str,
    output_integrated_path: str,
    extra_prompt: str = "",
) -> bool:
    print(f"\n{'=' * 60}")
    print("🚀 Gemini OCR + Translation + Integrated JSON 생성 시작")
    print(f"{'=' * 60}")

    result = _process_single_ocr(image_path, cv_clusters_path, output_integrated_path, extra_prompt)
    if result.get("success"):
        print(f"✅ OCR + 번역 성공. 소요시간: {result.get('elapsed', 0):.1f}초")
        return True
    print(f"❌ OCR + 번역 실패: {result.get('error', 'Unknown Error')}")
    return False


def main() -> None:
    # 모델을 모듈 전역으로 반영한다. integrated.json의 model 필드도 이 값을 따른다.
    global MODEL_NAME

    parser = argparse.ArgumentParser(description="Gemini OCR + Translation + Integrated JSON 생성")
    parser.add_argument("image_path", help="원본 이미지 경로")
    parser.add_argument("cv_clusters_json", help="cv_clusters JSON 경로")
    parser.add_argument("output_integrated_json", help="integrated JSON 출력 경로")
    parser.add_argument("legacy_extra_prompt", nargs="?", default="", help="기존 호환용 추가 프롬프트")
    parser.add_argument("--extra-prompt", default="", help="추가 번역 지시")
    parser.add_argument("--ocr-sheet-image", default=None, help="indexed OCR sheet 이미지 경로")
    parser.add_argument("--ocr-sheet-json", default=None, help="flood regions sheet JSON 경로")
    parser.add_argument(
        "--model",
        default=MODEL_NAME,
        help=f"OCR/번역 모델. gemini-* 또는 gpt-* (기본값: {MODEL_NAME})",
    )
    args = parser.parse_args()

    try:
        resolve_provider(args.model)
    except ValueError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    MODEL_NAME = args.model

    extra_prompt = args.extra_prompt or args.legacy_extra_prompt
    if args.ocr_sheet_image and args.ocr_sheet_json:
        result = _process_sheet_ocr(
            args.ocr_sheet_image,
            args.ocr_sheet_json,
            args.cv_clusters_json,
            args.output_integrated_json,
            extra_prompt=extra_prompt,
        )
    else:
        result = _process_single_ocr(
            args.image_path,
            args.cv_clusters_json,
            args.output_integrated_json,
            extra_prompt=extra_prompt,
        )

    if not result.get("success"):
        print(f"❌ Gemini OCR 실패: {result.get('error')}")
        sys.exit(1)
    print(f"✅ Gemini OCR 완료: {result.get('sentences', 0)}개 sentence")


if __name__ == "__main__":
    main()
