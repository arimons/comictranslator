#!/usr/bin/env python3
"""
Gemini OCR for V5 Clusters
"""

import sys
import json
from pathlib import Path
from gemini_api import GeminiAPI
from dotenv import load_dotenv
import os

load_dotenv()

def main():
    if len(sys.argv) < 4:
        print("Usage: python3 gemini_ocr_v5.py <image_path> <clusters_json> <output_json>")
        sys.exit(1)
    
    image_path = sys.argv[1]
    clusters_json = sys.argv[2]
    output_json = sys.argv[3]
    
    # Gemini API 초기화
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY 환경변수가 필요합니다")
        sys.exit(1)
    
    gemini = GeminiAPI(api_key=api_key)
    
    # Gemini OCR 수행
    # ocr_clusters signature: (image_path, clusters_json_path, retry_count=3)
    print(f"🤖 Gemini OCR 시작: {Path(image_path).name}")
    result = gemini.ocr_clusters(image_path, clusters_json)
    
    if not result:
        print("❌ Gemini OCR 실패")
        sys.exit(1)
    
    # 결과를 output_json에 저장
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ OCR 완료: {len(result.get('sentences', []))}개 문장")
    
if __name__ == "__main__":
    main()
