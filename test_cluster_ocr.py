#!/usr/bin/env python3
"""
Cluster 기반 Gemini OCR 테스트 스크립트
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from gemini_api import GeminiAPI

load_dotenv()

def test_cluster_ocr(image_path: str, clusters_json_path: str, output_path: str = None):
    """
    Cluster 기반 OCR 테스트

    Args:
        image_path: 이미지 파일 경로
        clusters_json_path: cluster JSON 파일 경로
        output_path: 결과 저장 경로 (옵션)
    """
    # API 초기화
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY 환경 변수를 설정해주세요")
        return None

    api = GeminiAPI(api_key)

    # Cluster 기반 OCR 수행
    result = api.ocr_clusters(image_path, clusters_json_path)

    if result:
        # 결과 출력
        print("\n" + "="*60)
        print("📊 OCR 결과 요약:")
        print("="*60)
        for cluster in result:
            print(f"\nCluster {cluster['cluster_id']} ({cluster['orientation']}):")
            print(f"  CV Text:     {cluster['text'][:60]}...")
            print(f"  Gemini Text: {cluster['gemini_text'][:60]}...")
            print(f"  Confidence:  {cluster['gemini_confidence']}")

        # 결과 저장
        if output_path is None:
            output_path = Path(clusters_json_path).parent / f"{Path(image_path).stem}_gemini_ocr.json"

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"\n✅ 결과 저장: {output_path}")
        return result
    else:
        print("❌ OCR 실패")
        return None

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python test_cluster_ocr.py <image> <clusters.json> [output.json]")
        print("\nExample:")
        print("  python test_cluster_ocr.py temp_output/00038.jpg temp_output/00038_cv_clusters_v4.json")
        sys.exit(1)

    image_path = sys.argv[1]
    clusters_json = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    test_cluster_ocr(image_path, clusters_json, output_path)
