#!/usr/bin/env python3
"""
CV Lines 시각화 (새 구조: bboxes 통합)
"""

import json
import sys
import cv2
import numpy as np
from pathlib import Path


def visualize_cv_lines(image_path: str, json_path: str, output_path: str):
    """CV Clusters 결과를 이미지에 시각화"""

    # 이미지 로드
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: Cannot load image {image_path}")
        return

    # JSON 로드
    with open(json_path, 'r', encoding='utf-8') as f:
        clusters = json.load(f)

    print(f"Loaded {len(clusters)} clusters from {json_path}")

    # 오버레이 생성
    overlay = img.copy()

    # 각 cluster 시각화
    for cluster in clusters:
        cluster_id = cluster['cluster_id']

        # 색상 (cluster_id 기반)
        hue = (cluster_id * 30) % 180
        color = tuple(map(int, cv2.cvtColor(np.uint8([[[hue, 255, 200]]]), cv2.COLOR_HSV2BGR)[0][0]))

        # bboxes 그리기 (개별 line들)
        for i, line_item in enumerate(cluster['bboxes']):
            bbox = line_item['bbox']
            text = line_item['text']
            x, y, w, h = bbox

            # 반투명 사각형
            cv2.rectangle(overlay, (x, y), (x+w, y+h), color, -1)

            # 테두리
            cv2.rectangle(img, (x, y), (x+w, y+h), color, 2)

            # 일본어는 OpenCV로 렌더링 불가 (??? 표시됨) → 텍스트 표시 제거

        # 외접 직사각형 계산 (cluster의 모든 bboxes 포함)
        if cluster['bboxes'] and len(cluster['bboxes']) > 1:
            all_x = [b['bbox'][0] for b in cluster['bboxes']]
            all_y = [b['bbox'][1] for b in cluster['bboxes']]
            all_x2 = [b['bbox'][0] + b['bbox'][2] for b in cluster['bboxes']]
            all_y2 = [b['bbox'][1] + b['bbox'][3] for b in cluster['bboxes']]

            bound_x = min(all_x)
            bound_y = min(all_y)
            bound_x2 = max(all_x2)
            bound_y2 = max(all_y2)

            # 단순 직사각형만 표시
            cv2.rectangle(img, (bound_x, bound_y), (bound_x2, bound_y2), (0, 0, 0), 3)
        elif cluster['bboxes']:
            # bbox가 1개만 있으면 일반 직사각형
            bbox = cluster['bboxes'][0]['bbox']
            x, y, w, h = bbox
            cv2.rectangle(img, (x, y), (x+w, y+h), (0, 0, 0), 3)

        # 라벨 (첫 번째 bbox에)
        if cluster['bboxes']:
            first_bbox = cluster['bboxes'][0]['bbox']
            label_text = f"C{cluster_id}"
            cv2.putText(img, label_text, (first_bbox[0], first_bbox[1] - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    
    # 블렌딩
    alpha = 0.3
    result = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)
    
    # 저장
    cv2.imwrite(output_path, result)
    print(f"✅ Saved visualization: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python visualize_cv_lines_filled.py <image> <cv_lines.json> [output]")
        sys.exit(1)
    
    output = sys.argv[3] if len(sys.argv) > 3 else "cv_lines_vis.jpg"
    visualize_cv_lines(sys.argv[1], sys.argv[2], output)
