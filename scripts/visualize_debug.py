import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


def resolve_debug_image_path(img_num: str) -> Path:
    """Debug 시각화에 사용할 원본 이미지 경로를 찾는다."""
    candidates = [
        Path("Debug") / img_num / f"{img_num}.jpg",
        Path("test_image") / f"{img_num}.jpg",
        Path("archive/temp_output/Ore6-NEW") / f"{img_num}.jpg",
        Path("archive/temp_outputs/archive") / f"{img_num}.jpg",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


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


def visualize_tight_text_groups(
    image_path: str,
    tight_groups_json: str,
    output_path: str
):
    """
    Tight Text Group 결과를 원본 이미지 위에 시각화한다.

    - 자홍색: Tight Text Group bbox
    - 청록색: Text Line bbox
    - 노란색: CV Text Fragment bbox
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 이미지 로드 실패: {image_path}")
        return

    with open(tight_groups_json, 'r', encoding='utf-8') as f:
        groups = json.load(f)

    try:
        font_large = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 20)
    except:
        font_large = ImageFont.load_default()

    for group in groups:
        rect = group.get("bounding_rect", [])
        if len(rect) >= 4:
            x, y, w, h = [int(v) for v in rect[:4]]
            cv2.rectangle(img, (x, y), (x + w, y + h), (255, 0, 255), 4)

        for line in group.get("lines", []):
            bbox = line.get("bbox", [])
            if len(bbox) >= 4:
                lx, ly, lw, lh = [int(v) for v in bbox[:4]]
                cv2.rectangle(img, (lx, ly), (lx + lw, ly + lh), (255, 255, 0), 2)

            for fragment in line.get("fragments", []):
                fb = fragment.get("bbox", [])
                if len(fb) >= 4:
                    fx, fy, fw, fh = [int(v) for v in fb[:4]]
                    cv2.rectangle(img, (fx, fy), (fx + fw, fy + fh), (0, 255, 255), 1)

    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)

    for group in groups:
        rect = group.get("bounding_rect", [])
        group_id = group.get("text_group_id", "?")
        if len(rect) < 4:
            continue

        x, y = int(rect[0]), int(rect[1])
        text = f"TG{group_id}"
        bbox_text = draw.textbbox((0, 0), text, font=font_large)
        text_width = bbox_text[2] - bbox_text[0] + 8
        text_height = bbox_text[3] - bbox_text[1] + 6

        draw.rectangle([x, y - text_height - 2, x + text_width, y], fill=(255, 0, 255))
        draw.text((x + 4, y - text_height + 1), text, fill=(255, 255, 255), font=font_large)

    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    cv2.imwrite(output_path, img)
    print(f"✅ Tight Text Group 시각화 완료: {output_path}")


def visualize_anchor_text_groups(
    image_path: str,
    clusters_json: str,
    output_path: str
):
    """
    Loose CV Cluster를 Contour 탐색용 Anchor Text Group으로 시각화한다.

    - 주황색: Anchor Text Group bbox
    - 청록색: Text Line bbox
    - 회색 반투명: bbox 2.5x / 1.6y ROI 후보 영역
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 이미지 로드 실패: {image_path}")
        return

    with open(clusters_json, 'r', encoding='utf-8') as f:
        groups = json.load(f)

    overlay = img.copy()

    try:
        font_large = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 22)
        font_small = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 16)
    except:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    img_h, img_w = img.shape[:2]

    def expanded_roi(rect, scale_x=2.5, scale_y=1.6):
        x, y, w, h = [int(v) for v in rect[:4]]
        cx, cy = x + w / 2.0, y + h / 2.0
        rw, rh = w * scale_x, h * scale_y
        left = max(0, int(round(cx - rw / 2.0)))
        top = max(0, int(round(cy - rh / 2.0)))
        right = min(img_w, int(round(cx + rw / 2.0)))
        bottom = min(img_h, int(round(cy + rh / 2.0)))
        return [left, top, right - left, bottom - top]

    # ROI 후보 영역을 먼저 옅게 표시한다.
    for group in groups:
        rect = group.get("bounding_rect", [])
        if len(rect) < 4:
            continue
        rx, ry, rw, rh = expanded_roi(rect)
        cv2.rectangle(overlay, (rx, ry), (rx + rw, ry + rh), (180, 180, 180), -1)
        cv2.rectangle(img, (rx, ry), (rx + rw, ry + rh), (170, 170, 170), 1)

    img = cv2.addWeighted(overlay, 0.18, img, 0.82, 0)

    # Anchor bbox와 line bbox를 그린다.
    for group in groups:
        rect = group.get("bounding_rect", [])
        if len(rect) >= 4:
            x, y, w, h = [int(v) for v in rect[:4]]
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 150, 255), 5)

        for line in group.get("lines", []):
            bbox = line.get("bbox", [])
            if len(bbox) >= 4:
                lx, ly, lw, lh = [int(v) for v in bbox[:4]]
                cv2.rectangle(img, (lx, ly), (lx + lw, ly + lh), (255, 255, 0), 2)

    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)

    for group in groups:
        rect = group.get("bounding_rect", [])
        group_id = group.get("cluster_id", "?")
        if len(rect) < 4:
            continue

        x, y = int(rect[0]), int(rect[1])
        label = f"A{group_id}"
        bbox_text = draw.textbbox((0, 0), label, font=font_large)
        text_width = bbox_text[2] - bbox_text[0] + 10
        text_height = bbox_text[3] - bbox_text[1] + 8

        draw.rectangle([x, y - text_height - 4, x + text_width, y], fill=(255, 150, 0))
        draw.text((x + 5, y - text_height), label, fill=(255, 255, 255), font=font_large)

    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    cv2.imwrite(output_path, img)
    print(f"✅ Anchor Text Group 시각화 완료: {output_path}")


def visualize_all(img_num: str):
    """
    특정 이미지의 통합 시각화 수행
    (CV Clusters + Integrated 데이터를 하나의 파일로)
    """
    base_dir = Path("temp_output/Debug")
    image_path = resolve_debug_image_path(img_num)
    clusters_json = base_dir / f"{img_num}_cv_clusters_v5.json"
    tight_groups_json = base_dir / f"{img_num}_cv_text_groups_tight.json"
    integrated_json = base_dir / f"{img_num}_integrated.json"
    output_path = base_dir / f"{img_num}_debug.jpg"  # ✅ 통합 파일 하나
    tight_output_path = base_dir / f"{img_num}_tight_debug.jpg"
    anchor_output_path = base_dir / f"{img_num}_anchor_debug.jpg"

    print(f"\n🎨 {img_num} 시각화 중...")

    # 클러스터와 integrated 정보를 하나에 시각화
    if clusters_json.exists() and integrated_json.exists():
        visualize_unified(str(image_path), str(clusters_json), str(integrated_json), str(output_path))
    else:
        if not clusters_json.exists():
            print(f"⚠️  클러스터 파일 없음: {clusters_json}")
        if not integrated_json.exists():
            print(f"⚠️  통합 파일 없음: {integrated_json}")

    if tight_groups_json.exists():
        visualize_tight_text_groups(str(image_path), str(tight_groups_json), str(tight_output_path))
    else:
        print(f"⚠️  Tight Text Group 파일 없음: {tight_groups_json}")

    if clusters_json.exists():
        visualize_anchor_text_groups(str(image_path), str(clusters_json), str(anchor_output_path))
    else:
        print(f"⚠️  Anchor Text Group 파일 없음: {clusters_json}")


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
