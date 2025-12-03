#!/usr/bin/env python3
"""
Merge suggested clusters and visualize
"""
import json
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict


def merge_clusters(clusters: List[Dict], merge_suggestions: List[Dict]) -> List[Dict]:
    """
    Merge suggestions에 따라 cluster들을 병합

    Args:
        clusters: 원본 cluster 리스트
        merge_suggestions: merge 제안 리스트

    Returns:
        병합된 cluster 리스트 (원본 + 신규 merged clusters)
    """
    merged_clusters = clusters.copy()
    next_id = max(c['cluster_id'] for c in clusters) + 1

    for suggestion in merge_suggestions:
        cluster_ids = suggestion['clusters']
        merged_text = suggestion.get('merged_text', '')
        reason = suggestion.get('reason', '')

        # 병합할 clusters 찾기
        to_merge = [c for c in clusters if c['cluster_id'] in cluster_ids]

        if len(to_merge) < 2:
            print(f"⚠️ Merge 건너뜀: C{cluster_ids} (cluster 찾을 수 없음)")
            continue

        # 모든 bboxes 수집
        all_bboxes = []
        for cluster in to_merge:
            all_bboxes.extend(cluster['bboxes'])

        # 통합 bounding_poly 계산
        all_xs = []
        all_ys = []
        for bbox_item in all_bboxes:
            x, y, w, h = bbox_item['bbox']
            all_xs.extend([x, x + w])
            all_ys.extend([y, y + h])

        merged_bbox = [
            [min(all_xs), min(all_ys)],
            [max(all_xs), min(all_ys)],
            [max(all_xs), max(all_ys)],
            [min(all_xs), max(all_ys)]
        ]

        # 신규 merged cluster 생성
        merged_cluster = {
            "cluster_id": next_id,
            "merged_from": cluster_ids,
            "bboxes": all_bboxes,
            "bounding_poly": merged_bbox,
            "text": merged_text,
            "gemini_text": merged_text,
            "gemini_confidence": to_merge[0].get('gemini_confidence', 'high'),
            "text_type": to_merge[0].get('text_type', 'dialogue'),
            "location": to_merge[0].get('location', 'in_bubble'),
            "orientation": to_merge[0]['orientation'],
            "merge_reason": reason
        }

        merged_clusters.append(merged_cluster)
        print(f"  ✅ Merged C{cluster_ids} → C{next_id}: '{merged_text[:50]}...'")
        next_id += 1

    return merged_clusters


def visualize_with_merged(image_path: str, clusters: List[Dict], output_path: str = None):
    """
    Original clusters + merged clusters 시각화

    Args:
        image_path: 원본 이미지 경로
        clusters: cluster 리스트 (merged 포함)
        output_path: 출력 이미지 경로
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 이미지 로드 실패: {image_path}")
        return None

    overlay = img.copy()

    for cluster in clusters:
        cid = cluster['cluster_id']
        poly = cluster['bounding_poly']

        # Bounding poly 좌표
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        bound_x, bound_x2 = min(xs), max(xs)
        bound_y, bound_y2 = min(ys), max(ys)

        # Merged cluster는 빨간색, 일반은 초록색
        is_merged = 'merged_from' in cluster
        color = (0, 0, 255) if is_merged else (0, 255, 0)  # BGR
        label_bg = (0, 0, 200) if is_merged else (0, 200, 0)

        # 직사각형 그리기
        cv2.rectangle(overlay, (bound_x, bound_y), (bound_x2, bound_y2), color, 2)

        # Cluster ID 라벨
        if is_merged:
            merged_from = cluster['merged_from']
            merge_num = cid - max(c['cluster_id'] for c in clusters if 'merged_from' not in c)
            from_str = '+'.join([f"C{i}" for i in merged_from])
            label = f"M{merge_num} ({from_str})"
        else:
            label = f"C{cid}"

        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
        label_x = bound_x
        label_y = bound_y - 5

        # 라벨 배경
        cv2.rectangle(overlay,
                      (label_x, label_y - label_size[1] - 4),
                      (label_x + label_size[0] + 4, label_y + 2),
                      label_bg, -1)

        # 라벨 텍스트
        cv2.putText(overlay, label,
                    (label_x + 2, label_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # 반투명 overlay
    alpha = 0.6
    result = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)

    # 저장
    if output_path is None:
        output_path = str(Path(image_path).parent / "merged_clusters_vis.jpg")

    cv2.imwrite(output_path, result)
    print(f"✅ 시각화 저장: {output_path}")
    return output_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python merge_and_visualize.py <image> <gemini_ocr.json> [output.jpg]")
        print("\nExample:")
        print("  python merge_and_visualize.py temp_output/00039.jpg temp_output/00039_gemini_ocr_v2.json")
        sys.exit(1)

    image_path = sys.argv[1]
    ocr_json_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    # OCR 결과 로드
    with open(ocr_json_path, 'r', encoding='utf-8') as f:
        ocr_result = json.load(f)

    clusters = ocr_result.get('clusters', [])
    merge_suggestions = ocr_result.get('merge_suggestions', [])

    print(f"📦 {len(clusters)}개 cluster, {len(merge_suggestions)}개 merge 제안")

    # Merge 수행
    if merge_suggestions:
        print("\n🔄 Cluster 병합 중...")
        merged_clusters = merge_clusters(clusters, merge_suggestions)
        print(f"  총 {len(merged_clusters)}개 cluster (원본 {len(clusters)} + 병합 {len(merged_clusters) - len(clusters)})")
    else:
        print("\n💡 Merge 제안 없음")
        merged_clusters = clusters

    # 시각화
    print("\n🎨 시각화 생성 중...")
    visualize_with_merged(image_path, merged_clusters, output_path)
