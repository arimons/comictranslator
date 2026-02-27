#!/usr/bin/env python3
"""
Debug 시각화 스크립트

통합 시각화:
1. CV Clusters (모든 클러스터) - 초록색
2. Matched CV Clusters (매칭된 클러스터) - 주황색
3. Union bbox (확장된 영역) - 빨간색
"""

import json
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict
from PIL import Image, ImageDraw, ImageFont


def visualize_unified(
    image_path: str,
    clusters_json: str,
    integrated_json: str,
    output_path: str
):
    """
    CV Clusters와 Integrated 데이터를 하나의 이미지에 시각화

    1. Sentence (Union bbox) - 빨간색 굵은 선
    2. Cluster (bounding_rect) - 파란색 중간 선
    3. Line (bbox) - 초록색 얇은 선

    Index:
    - Line: 없음
    - Cluster: 좌상단 아래 (파란색 배경)
    - Sentence: 좌상단 위 (빨간색 배경)
    """
    # 이미지 로드
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 이미지 로드 실패: {image_path}")
        return

    # Integrated 정보 로드
    with open(integrated_json, 'r', encoding='utf-8') as f:
        integrated_data = json.load(f)

    if not integrated_data:
        print(f"⚠️  통합 정보 없음: {integrated_json}")
        return

    clusters_list = integrated_data.get("clusters", [])

    # PIL 폰트 준비 (더 크고 굵게)
    try:
        font_large = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 20)
    except:
        font_large = ImageFont.load_default()

    # ===== Step 1: Sentence (Union bbox) 먼저 그리기 (뒤에 보이게) - 빨간색 굵은 선 =====
    for cluster_info in clusters_list:
        union_bbox = cluster_info.get("union_bbox", [])

        if not union_bbox or len(union_bbox) < 4:
            continue

        ux, uy, uw, uh = int(union_bbox[0]), int(union_bbox[1]), int(union_bbox[2]), int(union_bbox[3])

        # Sentence: 빨간색 굵은 선
        cv2.rectangle(img, (ux, uy), (ux + uw, uy + uh), (0, 0, 255), 5)

    # ===== Step 2: Cluster (bounding_rect) 그리기 - 파란색 중간 선 =====
    for cluster_info in clusters_list:
        matched_cluster_details = cluster_info.get("matched_cluster_details", [])

        for detail in matched_cluster_details:
            bbox = detail.get("bounding_rect", [])

            if bbox and len(bbox) >= 4:
                cx, cy, cw, ch = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                # Cluster: 파란색 중간 선
                cv2.rectangle(img, (cx, cy), (cx + cw, cy + ch), (255, 0, 0), 3)

    # ===== Step 3: Line (bbox) 그리기 (앞에 보이게) - 초록색 얇은 선 =====
    for cluster_info in clusters_list:
        matched_cluster_details = cluster_info.get("matched_cluster_details", [])

        for detail in matched_cluster_details:
            lines = detail.get("lines", [])

            for line in lines:
                bbox = line.get("bbox", [])

                if bbox and len(bbox) >= 4:
                    lx, ly, lw, lh = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                    # Line: 초록색 얇은 선 (Index 없음)
                    cv2.rectangle(img, (lx, ly), (lx + lw, ly + lh), (0, 255, 0), 1)

    # ===== Step 4: PIL로 텍스트 추가 (박스 배경 포함) =====
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)

    # Cluster IDs - 파란색 배경 (좌상단 아래)
    for cluster_info in clusters_list:
        matched_cluster_details = cluster_info.get("matched_cluster_details", [])

        for detail in matched_cluster_details:
            bbox = detail.get("bounding_rect", [])
            cluster_id = detail.get("cluster_id", "?")

            if bbox and len(bbox) >= 4:
                cx, cy = int(bbox[0]), int(bbox[1])
                text = f"C{cluster_id}"

                # 텍스트 크기 계산
                bbox_text = draw.textbbox((0, 0), text, font=font_large)
                text_width = bbox_text[2] - bbox_text[0] + 8
                text_height = bbox_text[3] - bbox_text[1] + 6

                # 파란색 배경 박스 (좌상단 아래)
                draw.rectangle([cx, cy + text_height, cx + text_width, cy + text_height * 2 + 2], fill=(255, 0, 0))

                # 흰색 텍스트
                draw.text((cx + 4, cy + text_height + 3), text, fill=(255, 255, 255), font=font_large)

    # Sentence IDs - 빨간색 배경 (좌상단 위)
    for cluster_info in clusters_list:
        sentence_id = cluster_info.get("sentence_id", "?")
        union_bbox = cluster_info.get("union_bbox", [])

        if not union_bbox or len(union_bbox) < 4:
            continue

        ux, uy = int(union_bbox[0]), int(union_bbox[1])
        text = f"S{sentence_id}"

        # 텍스트 크기 계산
        bbox_text = draw.textbbox((0, 0), text, font=font_large)
        text_width = bbox_text[2] - bbox_text[0] + 8
        text_height = bbox_text[3] - bbox_text[1] + 6

        # 빨간색 배경 박스 (좌상단 위)
        draw.rectangle([ux, uy - text_height - 2, ux + text_width, uy], fill=(0, 0, 255))

        # 흰색 텍스트
        draw.text((ux + 4, uy - text_height + 1), text, fill=(255, 255, 255), font=font_large)

    # PIL 이미지를 OpenCV로 변환
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    # 저장
    cv2.imwrite(output_path, img)
    print(f"✅ 통합 시각화 완료: {output_path}")


def visualize_all(img_num: str):
    """
    특정 이미지의 통합 시각화 수행
    (CV Clusters + Integrated 데이터를 하나의 파일로)
    """
    base_dir = Path("temp_output/Debug")
    image_path = Path("Debug") / img_num / f"{img_num}.jpg"
    clusters_json = base_dir / f"{img_num}_cv_clusters_v5.json"
    integrated_json = base_dir / f"{img_num}_integrated.json"
    output_path = base_dir / f"{img_num}_debug.jpg"  # ✅ 통합 파일 하나

    print(f"\n🎨 {img_num} 시각화 중...")

    # 클러스터와 integrated 정보를 하나에 시각화
    if clusters_json.exists() and integrated_json.exists():
        visualize_unified(str(image_path), str(clusters_json), str(integrated_json), str(output_path))
    else:
        if not clusters_json.exists():
            print(f"⚠️  클러스터 파일 없음: {clusters_json}")
        if not integrated_json.exists():
            print(f"⚠️  통합 파일 없음: {integrated_json}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        img_num = sys.argv[1]
        visualize_all(img_num)
    else:
        # 모든 Debug 이미지 시각화
        for img_num in ["00013", "00014", "00016", "00019"]:
            visualize_all(img_num)

        print(f"\n✅ 모든 시각화 완료!")
