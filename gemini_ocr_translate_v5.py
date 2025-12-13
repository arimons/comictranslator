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

# 환경 변수 로드
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY 환경 변수가 설정되어 있지 않습니다")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)


def create_unified_prompt(cv_clusters: List[Dict], extra_prompt: str = "") -> str:
    """
    OCR + 번역 통합 프롬프트 생성
    
    한 번의 API 호출로:
    1. 클러스터별 텍스트 검증/수정
    2. 번역 제공
    """
    
    # 클러스터 정보 추출
    cluster_info = []
    for cluster in cv_clusters:
        cluster_id = cluster['cluster_id']
        lines = cluster.get('lines', [])
        text = ' '.join([line['text'] for line in lines])
        cluster_info.append(f"Cluster {cluster_id}: {text}")
    
    clusters_text = "\n".join(cluster_info)
    
    prompt = f"""당신은 만화 번역 전문가입니다.

다음은 Cloud Vision API로 감지한 텍스트입니다:

{clusters_text}

작업:
1. 각 클러스터의 텍스트를 검증하고 필요시 수정해주세요
2. 각 클러스터를 한국어로 번역해주세요
3. 결과를 JSON 형식으로 반환해주세요

출력 형식 (JSON만):
{{
  "corrections": [
    {{"cluster_id": 1, "original": "...", "corrected": "..."}},
    ...
  ],
  "translations": [
    {{"cluster_id": 1, "korean": "..."}},
    ...
  ]
}}

주의:
- 존댓말은 존댓말로, 반말은 반말로 유지
- 의성어/의태어는 적절한 한국 의성어로
- 기술 용어나 고유명사는 원문 그대로 (괄호 안에 영문 표기)
- 정확도 > 자연스러움

{extra_prompt}"""
    
    return prompt


def process_ocr_and_translation(image_path: str, cv_clusters_path: str, 
                               output_ocr_path: str, output_translated_path: str,
                               extra_prompt: str = "") -> bool:
    """
    OCR + Translation 통합 처리
    
    한 번의 API 호출로 두 작업을 처리하고 두 개의 JSON 파일 생성
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
        model = genai.GenerativeModel('gemini-2.5-flash')
        
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
    
    # OCR 결과 생성 (수정된 텍스트로)
    print(f"📝 OCR 결과 생성 중...")
    ocr_result = []
    corrections = {c['cluster_id']: c.get('corrected', c['original']) 
                   for c in result.get('corrections', [])}
    
    for cluster in cv_clusters:
        cluster_id = cluster['cluster_id']
        corrected_text = corrections.get(cluster_id, '')
        
        if corrected_text:
            ocr_result.append({
                "sentence_id": cluster_id,
                "text": corrected_text,
                "confidence": "high",
                "text_type": "dialogue"
            })
    
    # 번역 결과 생성
    print(f"🌐 번역 결과 생성 중...")
    translations = {t['cluster_id']: t['korean'] 
                    for t in result.get('translations', [])}
    
    translated_result = []
    for cluster in cv_clusters:
        cluster_id = cluster['cluster_id']
        korean = translations.get(cluster_id, '')
        original = corrections.get(cluster_id, '')
        
        if korean:
            translated_result.append({
                "sentence_id": cluster_id,
                "original": original,
                "korean": korean,
                "confidence": "high"
            })
    
    # 결과 저장
    print(f"💾 결과 저장 중...")
    
    # OCR JSON 저장
    with open(output_ocr_path, 'w', encoding='utf-8') as f:
        json.dump({
            "sentences": ocr_result,
            "model": "gemini-2.5-flash (Unified OCR+Translation)"
        }, f, ensure_ascii=False, indent=2)
    print(f"  ✅ OCR 결과: {output_ocr_path}")
    
    # 번역 JSON 저장
    with open(output_translated_path, 'w', encoding='utf-8') as f:
        json.dump(translated_result, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 번역 결과: {output_translated_path}")
    
    print(f"\n{'='*60}")
    print(f"✅ OCR + 번역 완료")
    print(f"  📝 {len(ocr_result)}개 클러스터 처리")
    print(f"  🌐 {len(translated_result)}개 번역 완료")
    print(f"{'='*60}")
    
    return True


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python3 gemini_ocr_translate_v5.py <image_path> <cv_clusters.json> <output_ocr.json> <output_translated.json> [extra_prompt]")
        sys.exit(1)
    
    image_path = sys.argv[1]
    cv_clusters_path = sys.argv[2]
    output_ocr_path = sys.argv[3]
    output_translated_path = sys.argv[4]
    extra_prompt = sys.argv[5] if len(sys.argv) > 5 else ""
    
    success = process_ocr_and_translation(
        image_path, cv_clusters_path, 
        output_ocr_path, output_translated_path,
        extra_prompt
    )
    
    sys.exit(0 if success else 1)
