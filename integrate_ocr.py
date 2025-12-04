#!/usr/bin/env python3
"""
OCR 결과 통합
CV Clusters + Gemini OCR → Integrated JSON
"""
import json
import sys
from pathlib import Path


def integrate_ocr(cv_clusters_path: str, gemini_ocr_path: str, output_path: str):
    """
    CV Clusters와 Gemini OCR 결과를 통합
    
    Args:
        cv_clusters_path: cv_clusters_v5.json 경로
        gemini_ocr_path: ocr_v5.json (Gemini OCR 결과)
        output_path: integrated.json 출력 경로
    """
    print(f"\n{'='*60}")
    print(f"🔄 OCR 결과 통합")
    print(f"{'='*60}\n")
    
    # CV Clusters 로드
    with open(cv_clusters_path, 'r', encoding='utf-8') as f:
        cv_clusters = json.load(f)
    
    # Gemini OCR 로드
    with open(gemini_ocr_path, 'r', encoding='utf-8') as f:
        gemini_result = json.load(f)
    
    gemini_clusters = gemini_result.get('clusters', [])
    
    print(f"📦 CV Clusters: {len(cv_clusters)}개")
    print(f"🤖 Gemini OCR: {len(gemini_clusters)}개")
    
    # Gemini 결과를 cluster_id로 매핑
    gemini_map = {c['cluster_id']: c for c in gemini_clusters}
    
    # 통합
    integrated = []
    
    for cv_cluster in cv_clusters:
        cluster_id = cv_cluster['cluster_id']
        
        # CV 정보
        integrated_cluster = {
            'cluster_id': cluster_id,
            'bounding_poly': cv_cluster['bounding_poly'],
            'orientation': cv_cluster.get('orientation', 'vertical'),
            'text': '',
            'text_type': 'dialogue',
            'location': 'in_bubble',
            'confidence': 'unknown',
            'match_score': 0.0
        }
        
        # Gemini 정보 병합
        if cluster_id in gemini_map:
            gemini_cluster = gemini_map[cluster_id]
            integrated_cluster['text'] = gemini_cluster.get('text', '')
            integrated_cluster['text_type'] = gemini_cluster.get('text_type', 'dialogue')
            integrated_cluster['location'] = gemini_cluster.get('location', 'in_bubble')
            integrated_cluster['confidence'] = gemini_cluster.get('confidence', 'unknown')
            integrated_cluster['match_score'] = 1.0
        else:
            # Gemini에 없으면 CV 텍스트 사용
            lines = cv_cluster.get('lines', [])
            cv_text = '\n'.join([line['text'] for line in lines])
            integrated_cluster['text'] = cv_text
            integrated_cluster['match_score'] = 0.5
        
        integrated.append(integrated_cluster)
    
    # 저장
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(integrated, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 통합 완료: {len(integrated)}개 cluster")
    print(f"💾 저장: {output_path}")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python integrate_ocr.py <cv_clusters_v5.json> <ocr_v5.json> <output.json>")
        print("\nExample:")
        print("  python integrate_ocr.py temp_output/00038_cv_clusters_v5.json temp_output/00038_ocr_v5.json temp_output/00038_integrated.json")
        sys.exit(1)
    
    cv_clusters = sys.argv[1]
    gemini_ocr = sys.argv[2]
    output = sys.argv[3]
    
    integrate_ocr(cv_clusters, gemini_ocr, output)
