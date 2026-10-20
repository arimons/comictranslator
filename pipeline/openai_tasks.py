"""작업 성격에 따른 OpenAI 모델 선택. 분류용 API 요청은 하지 않는다."""
from __future__ import annotations

import json
import os

SOL_MODEL = 'gpt-6.1-sol'
LUNA_MODEL = 'gpt-6-luna'
IMAGE_MODEL = 'gpt-image-2'
ROUTING_VERSION = 'openai-tasks-v1'


def route_task(task: str, *, text_chars: int = 0, source_count: int = 0, override: str | None = None) -> dict:
    if override and override != 'auto':
        if not override.startswith('gpt-') or override == IMAGE_MODEL:
            raise ValueError('텍스트 작업은 OpenAI GPT 모델을 지정하세요. Gemini는 사용하지 않습니다.')
        return {'model': override, 'reason': '사용자가 지정한 모델', 'policy': ROUTING_VERSION}
    if task == 'glossary' and text_chars <= 12000 and source_count <= 3:
        return {'model': LUNA_MODEL, 'reason': '짧은 저장 자료의 용어 추출·JSON 정리', 'policy': ROUTING_VERSION}
    if task in ('glossary', 'ocr_translate', 'image_translation_guide'):
        return {'model': SOL_MODEL, 'reason': '문맥 OCR·번역 또는 긴 다중 자료의 정리', 'policy': ROUTING_VERSION}
    raise ValueError('지원하지 않는 OpenAI 작업입니다.')


def response_json(prompt: str, route: dict, *, validator=None, max_output_tokens: int = 6000) -> tuple[dict, dict]:
    """OpenAI Python Responses API. Luna의 형식 실패만 Sol로 1회 승격한다."""
    from openai import OpenAI
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        raise ValueError('.env에 OPENAI_API_KEY를 설정하세요.')
    models = [route['model']]
    if route['model'] == LUNA_MODEL:
        models.append(SOL_MODEL)
    attempts = []
    # 공식 SDK: https://github.com/openai/openai-python . SDK 자동 재시도는 비활성화한다.
    with OpenAI(api_key=key, max_retries=0, timeout=120) as client:
        for model in models:
            response = client.responses.create(
                model=model, input=prompt, reasoning={'effort': 'low'},
                text={'format': {'type': 'json_object'}}, max_output_tokens=max_output_tokens, store=False,
            )
            attempts.append(model)
            try:
                if getattr(response, 'status', None) == 'incomplete':
                    raise ValueError('용어 정리 응답이 완료되지 않았습니다.')
                result = json.loads(response.output_text)
                if not isinstance(result, dict) or (validator and not validator(result)):
                    raise ValueError('용어 정리 JSON 형식이 올바르지 않습니다.')
                return result, {**route, 'model': model, 'attempted_models': attempts,
                                'reason': route['reason'] if len(attempts) == 1 else 'Luna JSON 형식 실패로 Sol에서 다시 정리'}
            except (ValueError, TypeError):
                if model != LUNA_MODEL:
                    raise
    raise ValueError('용어 정리 응답을 해석할 수 없습니다.')
