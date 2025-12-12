#!/usr/bin/env python3
"""
Integrated JSON 기반 번역

integrated.json 파일의 텍스트를 Gemini로 번역
"""
import os
import json
import sys
from pathlib import Path
from dotenv import load_dotenv
from gemini_api import GeminiAPI

load_dotenv()


def translate_integrated(
    api: GeminiAPI,
    integrated_json_path: str,
    output_path: str = None,
    retry_count: int = 3
):
    """
    Integrated JSON 번역

    Args:
        api: GeminiAPI 인스턴스
        integrated_json_path: integrated.json 경로
        output_path: 번역 결과 저장 경로 (옵션)
        retry_count: 재시도 횟수

    Returns:
        Dict: 번역 결과
    """
    print(f"\n{'='*60}")
    print(f"🌐 통합 OCR 번역 시작")
    print(f"{'='*60}")

    # Integrated JSON 로드
    with open(integrated_json_path, 'r', encoding='utf-8') as f:
        clusters = json.load(f)

    print(f"\n📦 {len(clusters)}개 cluster 로드")

    # 번역할 텍스트 추출
    texts_to_translate = []
    for sentence in clusters:
        # V3 형식 (integrated_ocr_v3.py 출력)
        sentence_id = sentence.get('sentence_id')
        # text_sentence (V2) 또는 text (V3) 모두 지원
        text = sentence.get('text') or sentence.get('text_sentence', '')
        text_type = sentence.get('text_type', 'other')
        location = sentence.get('location', 'unknown')
        orientation = sentence.get('orientation', 'vertical')

        if text.strip() and sentence_id is not None:
            texts_to_translate.append({
                'sentence_id': sentence_id,
                'text': text,
                'text_type': text_type,
                'location': location,
                'orientation': orientation
            })

    print(f"📝 {len(texts_to_translate)}개 텍스트 번역 준비")

    # 번역 프롬프트 생성
    prompt = f"""다음 일본어 만화 텍스트들을 자연스러운 한국어로 번역해주세요.

**중요 지침:**
1. 자연스럽고 자연스러운 한국어 대사로 번역하세요
2. 문맥을 파악하여 적절한 어투를 사용하세요 (존댓말/반말/경어)
3. 효과음(sound_effect)은 한국어 의성어/의태어로 변환하세요
4. 나레이션(narration)은 설명조로 번역하세요
5. orientation이 'vertical'이면 세로쓰기, 'horizontal'이면 가로쓰기 텍스트입니다. 이 문맥을 번역에 반영하세요.
6. 기술명이나 주문은 의미를 살려 번역하세요

**텍스트 목록:**
{json.dumps([{'id': t['sentence_id'], 'text': t['text'], 'type': t['text_type'], 'location': t['location'], 'orientation': t['orientation']} for t in texts_to_translate], ensure_ascii=False, indent=2)}

**반환 형식 (JSON):**
{{
  "translations": [
    {{
      "sentence_id": 1,
      "original": "일본어 텍스트",
      "translated": "한국어 번역"
    }},
    ...
  ]
}}

순수 JSON만 반환하세요.
"""

    # Gemini API 호출
    print(f"\n🤖 Gemini 번역 API 호출 중...")

    for attempt in range(retry_count):
        try:
            response = api.translation_model.generate_content(prompt)
            response_text = response.text.strip()

            # JSON 추출
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                json_lines = []
                in_json = False
                for line in lines:
                    if line.strip().startswith('```'):
                        if in_json:
                            break
                        in_json = True
                        continue
                    if in_json:
                        json_lines.append(line)
                response_text = '\n'.join(json_lines)

            # JSON 파싱
            result = json.loads(response_text)

            if 'translations' not in result:
                print(f"  ⚠️ 'translations' 필드 없음 (시도 {attempt + 1}/{retry_count})")
                if attempt < retry_count - 1:
                    continue
                else:
                    return None

            print(f"  ✅ 번역 완료: {len(result['translations'])}개")

            # 원본 sentence에 번역 추가
            translated_sentences = []
            translation_map = {t['sentence_id']: t['translated'] for t in result['translations']}

            for sentence in clusters:
                # ✅ FIX: 새로운 형식 - sentence_id 사용
                sid = sentence.get('sentence_id')
                sentence_with_translation = sentence.copy()
                sentence_with_translation['text_translated'] = translation_map.get(sid, '')
                translated_sentences.append(sentence_with_translation)

            # 결과 출력
            print(f"\n{'='*60}")
            print(f"📊 번역 결과 미리보기:")
            print(f"{'='*60}")
            for i, item in enumerate(translated_sentences[:3], 1):
                print(f"\nSentence {item.get('sentence_id')}:")
                # V3: text, V2: text_sentence 모두 지원
                original_text = item.get('text') or item.get('text_sentence', '')
                print(f"  원문: {original_text[:40]}...")
                print(f"  번역: {item.get('text_translated', '')[:40]}...")

            if len(translated_sentences) > 3:
                print(f"\n... 외 {len(translated_sentences) - 3}개")

            # 결과 저장
            if output_path is None:
                output_path = Path(integrated_json_path).parent / f"{Path(integrated_json_path).stem}_translated.json"

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(translated_sentences, f, ensure_ascii=False, indent=2)

            print(f"\n✅ 번역 결과 저장: {output_path}")
            return translated_sentences

        except json.JSONDecodeError as e:
            print(f"  ⚠️ JSON 파싱 실패 (시도 {attempt + 1}/{retry_count}): {e}")
            if attempt < retry_count - 1:
                continue
            else:
                print(f"❌ JSON 파싱 최종 실패")
                return None

        except Exception as e:
            print(f"  ⚠️ API 호출 실패 (시도 {attempt + 1}/{retry_count}): {e}")
            if attempt < retry_count - 1:
                continue
            else:
                print(f"❌ API 호출 최종 실패")
                return None

    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python translate_integrated.py <integrated.json> [output.json]")
        print("\nExample:")
        print("  python translate_integrated.py temp_output/00038_integrated.json")
        sys.exit(1)

    integrated_json = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    # API 초기화
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY 환경 변수를 설정해주세요")
        sys.exit(1)

    api = GeminiAPI(api_key)

    # 번역 수행
    translate_integrated(api, integrated_json, output_path)
