#!/usr/bin/env python3
"""
✅ Gemini OCR + Translation 통합 (V5)
한 번의 API 호출로 OCR과 번역을 동시에 처리

사용법:
  python3 gemini_ocr_translate_v5.py <image_path> <cv_clusters_path> <output_ocr.json> <output_translated.json> [extra_prompt]

결과:
  - output_ocr.json: OCR 결과 (기존 형식)
  - output_translated.json: 번역 결과 (기존 형식)
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Any
import google.generativeai as genai
from dotenv import load_dotenv
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# 환경 변수 로드
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY 환경 변수가 설정되어 있지 않습니다")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)


def create_unified_prompt(cv_clusters: List[Dict], extra_prompt: str = "") -> str:
    """
    OCR + 번역 통합 프롬프트 생성 (V2 - 자연스러운 문장 단위)

    CV 클러스터를 힌트로만 제공, Gemini가 자유롭게 문장 재구성
    """

    # 클러스터 정보 추출 (bbox 포함)
    cluster_info = []
    for cluster in cv_clusters:
        cluster_id = cluster['cluster_id']
        lines = cluster.get('lines', [])
        text = ' '.join([line['text'] for line in lines])
        bbox = cluster.get('bounding_rect', [])
        cluster_info.append(f"Cluster {cluster_id} (bbox: {bbox}): {text}")

    clusters_text = "\n".join(cluster_info)

    prompt = f"""일본어 만화 OCR + 번역 전문가입니다.

📌 Cloud Vision API 감지 텍스트 (참고용 힌트):
{clusters_text}

⚠️ 중요 지침:
1. CV가 놓친 텍스트가 있을 수 있습니다. 이미지를 직접 확인하여 누락된 텍스트를 찾아 추가하세요.
2. **인접한 클러스터는 하나의 문장일 수 있습니다.** bbox 위치를 고려하여 자연스러운 문장 단위로 재구성하세요.
3. CV 클러스터는 힌트일 뿐이며, 이미지를 보고 자연스러운 의미 단위로 문장을 나누세요.
4. 각 문장은 완결된 의미를 가져야 합니다.

📝 번역 텍스트 방향:
- 일본 원문이 세로쓰기(縦書き)라 하더라도, 한국어 번역문은 항상 가로쓰기(가로 읽기)로 작성해야 합니다.
- text overlay 시 가로 방향으로만 렌더링됩니다.

작업:
1. 이미지를 보고 모든 일본어 텍스트를 자연스러운 문장 단위로 인식
2. 각 문장을 한국어로 번역 (항상 가로쓰기 형식)
3. JSON 반환

출력 형식:
{{
  "sentences": [
    {{
      "sentence_id": 1,
      "text": "일본어 원문 (자연스러운 문장 단위)",
      "text_translated": "한국어 번역 (가로쓰기로 표현)",
      "source_clusters": [1, 2]  // 이 문장이 어느 CV 클러스터에서 왔는지 (병합 가능)
    }},
    ...
  ]
}}

번역 규칙: 존댓말/반말 유지, 의성어 한국어로, 정확도 우선, 자연스러운 한국어 표현
{extra_prompt}"""

    return prompt


def _process_single_ocr(image_path: str, cv_clusters_path: str,
                        output_ocr_path: str, extra_prompt: str = "") -> Dict[str, Any]:
    """
    단일 이미지 OCR + 번역 처리 (내부 함수)

    Returns:
        처리 결과 딕셔너리
    """
    img_name = Path(image_path).stem

    try:
        # CV 클러스터 로드
        with open(cv_clusters_path, 'r', encoding='utf-8') as f:
            cv_clusters = json.load(f)

        # 프롬프트 생성
        prompt = create_unified_prompt(cv_clusters, extra_prompt)

        # 이미지 로드
        img = Path(image_path)
        if not img.exists():
            return {
                "img_name": img_name,
                "success": False,
                "error": "이미지를 찾을 수 없음"
            }

        # Gemini API 호출
        api_start = time.time()
        model = genai.GenerativeModel('gemini-3-flash-preview')

        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()

        response = model.generate_content([
            prompt,
            {
                'mime_type': 'image/jpeg',
                'data': image_data
            }
        ])

        api_elapsed = time.time() - api_start

        # 응답 파싱
        response_text = response.text

        if '```json' in response_text:
            json_start = response_text.find('```json') + 7
            json_end = response_text.find('```', json_start)
            json_str = response_text[json_start:json_end].strip()
        elif '```' in response_text:
            json_start = response_text.find('```') + 3
            json_end = response_text.find('```', json_start)
            json_str = response_text[json_start:json_end].strip()
        else:
            json_str = response_text

        result = json.loads(json_str)

        # OCR + 번역 결과 생성
        unified_result = []
        for sentence in result.get('sentences', []):
            if sentence.get('text') and sentence.get('text_translated'):
                unified_result.append({
                    "sentence_id": sentence.get('sentence_id', len(unified_result) + 1),
                    "text": sentence['text'],
                    "text_translated": sentence['text_translated'],
                    "text_type": "dialogue",
                    "source_clusters": sentence.get('source_clusters', [])
                })

        # 결과 저장
        with open(output_ocr_path, 'w', encoding='utf-8') as f:
            json.dump({
                "sentences": unified_result,
                "model": "gemini-3-flash-preview (Parallel Batch OCR+Translation V2)"
            }, f, ensure_ascii=False, indent=2)

        return {
            "img_name": img_name,
            "success": True,
            "output_path": output_ocr_path,
            "sentences": len(unified_result),
            "elapsed": api_elapsed
        }

    except json.JSONDecodeError as e:
        return {
            "img_name": img_name,
            "success": False,
            "error": f"JSON 파싱 실패: {str(e)[:50]}"
        }
    except Exception as e:
        return {
            "img_name": img_name,
            "success": False,
            "error": str(e)[:100]
        }


def process_ocr_and_translation_batch(image_paths: List[str], cv_clusters_paths: List[str],
                                     output_dir: str = "output",
                                     extra_prompt: str = "",
                                     max_workers: int = 8) -> Dict[str, Any]:
    """
    여러 이미지를 병렬로 OCR + 번역 처리

    Args:
        image_paths: 이미지 파일 경로 리스트
        cv_clusters_paths: CV 클러스터 JSON 경로 리스트 (image_paths와 같은 순서)
        output_dir: 출력 디렉토리
        extra_prompt: 추가 프롬프트
        max_workers: 동시 처리 워커 수 (기본값: 8)

    Returns:
        처리 결과 딕셔너리
    """
    print(f"\n{'='*60}")
    print(f"🚀 Gemini OCR + Translation 병렬 처리 시작")
    print(f"{'='*60}")
    print(f"📊 이미지 개수: {len(image_paths)}개")
    print(f"⚙️ 워커: {max_workers}개 병렬 처리")
    print(f"📁 출력 폴더: {output_dir}\n")

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}

        for idx, (img_path, cv_path) in enumerate(zip(image_paths, cv_clusters_paths)):
            img_name = Path(img_path).stem
            output_ocr_path = output_dir_path / f"{img_name}_ocr_v5.json"

            future = executor.submit(
                _process_single_ocr,
                img_path, cv_path, str(output_ocr_path), extra_prompt
            )
            futures[future] = img_name

        # 완료된 것부터 수집
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1

            img_name = futures[future]
            if result.get('success'):
                print(f"  ✅ {completed}/{len(image_paths)} ({img_name}) - {result.get('sentences', 0)}개 문장")
            else:
                print(f"  ❌ {completed}/{len(image_paths)} ({img_name}) - {result.get('error', 'Unknown error')[:40]}")

    # 통계
    success_count = sum(1 for r in results if r.get('success'))
    failed_count = sum(1 for r in results if not r.get('success'))
    total_sentences = sum(r.get('sentences', 0) for r in results if r.get('success'))

    print(f"\n{'='*60}")
    print(f"✅ OCR + 번역 병렬 처리 완료!")
    print(f"   📊 성공: {success_count}/{len(results)}")
    print(f"   📝 총 문장: {total_sentences}개")
    if failed_count > 0:
        print(f"   ❌ 실패: {failed_count}개")
    print(f"{'='*60}\n")

    return {
        "total": len(results),
        "success": success_count,
        "failed": failed_count,
        "total_sentences": total_sentences,
        "results": results
    }


def process_ocr_and_translation(image_path: str, cv_clusters_path: str,
                               output_ocr_path: str,
                               extra_prompt: str = "") -> bool:
    """
    OCR + Translation 통합 처리

    한 번의 API 호출로 OCR과 번역을 처리하고 하나의 JSON 파일 생성
    """
    
    print(f"\n{'='*60}")
    print(f"🚀 Gemini OCR + Translation 통합 처리 시작")
    print(f"{'='*60}")
    
    # CV 클러스터 로드
    print(f"📂 CV 클러스터 로드: {cv_clusters_path}")
    with open(cv_clusters_path, 'r', encoding='utf-8') as f:
        cv_clusters = json.load(f)
    print(f"  ✅ {len(cv_clusters)}개 클러스터 로드")
    
    # 프롬프트 생성
    print(f"📝 프롬프트 생성 중...")
    prompt = create_unified_prompt(cv_clusters, extra_prompt)
    
    # 이미지 로드
    print(f"🖼️ 이미지 로드: {image_path}")
    img = Path(image_path)
    if not img.exists():
        print(f"❌ 이미지를 찾을 수 없습니다: {image_path}")
        return False
    
    # Gemini API 호출
    print(f"🤖 Gemini API 호출 중 (OCR + 번역)...")
    api_start = time.time()
    
    try:
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()
        
        response = model.generate_content([
            prompt,
            {
                'mime_type': 'image/jpeg',
                'data': image_data
            }
        ])
        
        api_elapsed = time.time() - api_start
        print(f"✅ Gemini API 응답 (소요시간: {api_elapsed:.1f}초)")
        
    except Exception as e:
        print(f"❌ Gemini API 오류: {e}")
        return False
    
    # 응답 파싱
    print(f"📊 응답 파싱 중...")
    try:
        response_text = response.text
        
        # JSON 추출 (```json ... ``` 형식 처리)
        if '```json' in response_text:
            json_start = response_text.find('```json') + 7
            json_end = response_text.find('```', json_start)
            json_str = response_text[json_start:json_end].strip()
        elif '```' in response_text:
            json_start = response_text.find('```') + 3
            json_end = response_text.find('```', json_start)
            json_str = response_text[json_start:json_end].strip()
        else:
            json_str = response_text
        
        result = json.loads(json_str)
        
    except json.JSONDecodeError as e:
        print(f"❌ JSON 파싱 실패: {e}")
        print(f"응답: {response_text[:200]}")
        return False
    
    # OCR + 번역 통합 결과 생성
    print(f"📝 OCR + 번역 결과 생성 중...")

    # Gemini가 반환한 sentences 배열 직접 사용
    unified_result = []
    for sentence in result.get('sentences', []):
        # 필수 필드 확인
        if sentence.get('text') and sentence.get('text_translated'):
            unified_result.append({
                "sentence_id": sentence.get('sentence_id', len(unified_result) + 1),
                "text": sentence['text'],
                "text_translated": sentence['text_translated'],
                "text_type": "dialogue",
                "source_clusters": sentence.get('source_clusters', [])  # CV 클러스터 매핑 정보
            })

    # 결과 저장
    print(f"💾 결과 저장 중...")
    with open(output_ocr_path, 'w', encoding='utf-8') as f:
        json.dump({
            "sentences": unified_result,
            "model": "gemini-3-flash-preview (Natural Sentence-based OCR+Translation V2)"
        }, f, ensure_ascii=False, indent=2)
    print(f"  ✅ OCR + 번역 결과: {output_ocr_path}")

    print(f"\n{'='*60}")
    print(f"✅ OCR + 번역 완료")
    print(f"  📝 {len(unified_result)}개 문장 처리")
    print(f"{'='*60}")
    
    return True


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python3 gemini_ocr_translate_v5.py <image_path> <cv_clusters.json> <output_ocr.json> [extra_prompt]")
        sys.exit(1)

    image_path = sys.argv[1]
    cv_clusters_path = sys.argv[2]
    output_ocr_path = sys.argv[3]
    extra_prompt = sys.argv[4] if len(sys.argv) > 4 else ""

    success = process_ocr_and_translation(
        image_path, cv_clusters_path,
        output_ocr_path,
        extra_prompt
    )

    sys.exit(0 if success else 1)
