#!/usr/bin/env python3
"""
텍스트 오버레이 적용

확장된 bbox에 white masking 적용:
- 원본 영역: 100% 불투명 흰색
- 확장 영역: 50% 투명 흰색
"""
import json
import sys
import cv2
import numpy as np
from pathlib import Path


def calculate_expanded_bbox(
    bounding_poly,
    line_count,
    orientation='vertical',
    cv_text='',
    gemini_text=''
):
    """
    Line 개수와 텍스트 길이를 고려한 확장된 bbox 계산

    Args:
        bounding_poly: 4개 좌표 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        line_count: 실제 line 개수
        orientation: 'vertical' 또는 'horizontal'
        cv_text: CV에서 인식한 텍스트
        gemini_text: Gemini에서 인식한 텍스트

    Returns:
        (원본 bounding_poly, 전체 확장 bounding_poly, 원본+height확장 poly)
    """
    xs = [p[0] for p in bounding_poly]
    ys = [p[1] for p in bounding_poly]

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    current_width = x_max - x_min
    current_height = y_max - y_min

    if line_count == 0:
        return bounding_poly, bounding_poly, bounding_poly

    # Width 확장 비율 계산
    if line_count >= 4:
        width_expansion_ratio = 1.0
    else:
        width_expansion_ratio = 4.0 / (line_count + 1)

    # Height 확장: 1줄인 경우 텍스트 길이 비교
    height_expansion_ratio = 1.0

    if line_count == 1 and cv_text and gemini_text:
        # 공백 제거 후 실제 글자 수 비교
        cv_len = len(cv_text.replace(' ', '').replace('　', '').strip())
        gemini_len = len(gemini_text.replace(' ', '').replace('　', '').strip())

        if gemini_len > cv_len and cv_len > 0:
            # Gemini가 더 긴 경우, 글자 수 비율로 높이 확장
            height_expansion_ratio = gemini_len / cv_len
            print(f"  [DEBUG] Line=1 확장: CV '{cv_text}'({cv_len}자) → Gemini '{gemini_text}'({gemini_len}자), ratio={height_expansion_ratio:.2f}")

    if orientation == 'vertical':
        # Vertical: width 확장 + height 확장 (1줄인 경우)
        new_width = current_width * width_expansion_ratio
        width_expansion = new_width - current_width

        new_height = current_height * height_expansion_ratio
        height_expansion = new_height - current_height

        # Width: 중점 기준으로 좌우로 확장
        new_x_min = x_min - width_expansion / 2
        new_x_max = x_max + width_expansion / 2

        # Height: 아래쪽으로만 확장 (세로 텍스트는 위→아래로 읽음)
        new_y_min = y_min
        new_y_max = y_max + height_expansion

        # 전체 확장 poly (width + height)
        expanded_poly = [
            [new_x_min, new_y_min],
            [new_x_max, new_y_min],
            [new_x_max, new_y_max],
            [new_x_min, new_y_max]
        ]

        # 원본 + height 확장만 (GREEN 영역)
        height_expanded_poly = [
            [x_min, y_min],
            [x_max, y_min],
            [x_max, new_y_max],
            [x_min, new_y_max]
        ]
    else:  # horizontal
        # Horizontal: height 확장 (기존 로직)
        new_height = current_height * width_expansion_ratio
        height_expansion = new_height - current_height

        # Width: 1줄인 경우 텍스트 길이로 확장
        new_width = current_width * height_expansion_ratio
        width_expansion = new_width - current_width

        # Height: 중점 기준으로 상하로 확장
        new_y_min = y_min - height_expansion / 2
        new_y_max = y_max + height_expansion / 2

        # Width: 오른쪽으로만 확장 (가로 텍스트는 왼→오로 읽음)
        new_x_min = x_min
        new_x_max = x_max + width_expansion

        # 전체 확장 poly
        expanded_poly = [
            [new_x_min, new_y_min],
            [new_x_max, new_y_min],
            [new_x_max, new_y_max],
            [new_x_min, new_y_max]
        ]

        # 원본 + width 확장만 (GREEN 영역, horizontal의 경우 width가 reading direction)
        height_expanded_poly = [
            [x_min, new_y_min],
            [new_x_max, new_y_min],
            [new_x_max, new_y_max],
            [x_min, new_y_max]
        ]

    return bounding_poly, expanded_poly, height_expanded_poly


def apply_white_masking(
    image_path: str,
    integrated_json_path: str,
    cv_clusters_path: str,
    output_path: str = None
):
    """
    White Masking 적용

    Args:
        image_path: 원본 이미지 경로
        integrated_json_path: integrated.json 경로
        cv_clusters_path: cv_clusters_v5.json 경로 (line 개수 및 CV 텍스트 확인용)
        output_path: 결과 저장 경로
    """
    print(f"\n{'='*60}")
    print(f"🎨 White Masking 적용")
    print(f"{'='*60}")

    # 이미지 로드
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 이미지를 불러올 수 없습니다: {image_path}")
        return

    img_height, img_width = img.shape[:2]
    print(f"\n📐 이미지 크기: {img_width} x {img_height}")

    # Integrated JSON 로드
    with open(integrated_json_path, 'r', encoding='utf-8') as f:
        clusters = json.load(f)

    # CV Clusters 로드 (line 개수 및 CV 텍스트 확인용)
    with open(cv_clusters_path, 'r', encoding='utf-8') as f:
        cv_clusters = json.load(f)

    # CV cluster를 ID로 매핑
    cv_cluster_map = {c['cluster_id']: c for c in cv_clusters}

    print(f"📦 {len(clusters)}개 Cluster 로드")

    # 마스킹용 레이어 생성 (RGBA)
    # Width 확장 영역용 (50% 투명 BLUE)
    width_expanded_mask = np.zeros((img_height, img_width, 4), dtype=np.uint8)
    # 원본 + Height 확장 영역용 (100% 불투명 GREEN)
    height_expanded_mask = np.zeros((img_height, img_width, 4), dtype=np.uint8)

    print(f"\n{'='*60}")
    print(f"🖌️ Masking 적용 중...")
    print(f"{'='*60}")

    masked_count = 0

    for cluster in clusters:
        cluster_id = cluster['cluster_id']
        poly = cluster['bounding_poly']
        orientation = cluster.get('orientation', 'vertical')

        # Integrated JSON에서 Gemini 텍스트 가져오기
        gemini_text = cluster.get('text', '')

        # CV cluster에서 line 정보 및 CV 텍스트 가져오기
        cv_cluster = cv_cluster_map.get(cluster_id)
        if cv_cluster:
            lines = cv_cluster.get('lines', [])
            line_count = len(lines)
            # CV 텍스트 추출 (lines에서)
            cv_text = ''.join([line['text'] for line in lines])
        else:
            # CV cluster가 없으면 Gemini 텍스트로 추정
            line_count = gemini_text.count(' ') + 1 if gemini_text else 1
            cv_text = ''

        if line_count == 0 or not gemini_text:
            continue

        # 확장된 bbox 계산 (3개 반환)
        original_poly, expanded_poly, height_exp_poly = calculate_expanded_bbox(
            poly, line_count, orientation, cv_text, gemini_text
        )

        # 전체 확장 좌표 (width + height)
        exp_xs = [p[0] for p in expanded_poly]
        exp_ys = [p[1] for p in expanded_poly]
        exp_x_min, exp_x_max = int(min(exp_xs)), int(max(exp_xs))
        exp_y_min, exp_y_max = int(min(exp_ys)), int(max(exp_ys))

        # 원본 + height 확장 좌표 (GREEN 영역)
        height_xs = [p[0] for p in height_exp_poly]
        height_ys = [p[1] for p in height_exp_poly]
        height_x_min, height_x_max = int(min(height_xs)), int(max(height_xs))
        height_y_min, height_y_max = int(min(height_ys)), int(max(height_ys))

        # 이미지 범위 내로 제한
        exp_x_min = max(0, exp_x_min)
        exp_y_min = max(0, exp_y_min)
        exp_x_max = min(img_width, exp_x_max)
        exp_y_max = min(img_height, exp_y_max)

        height_x_min = max(0, height_x_min)
        height_y_min = max(0, height_y_min)
        height_x_max = min(img_width, height_x_max)
        height_y_max = min(img_height, height_y_max)

        # Width 확장 영역에 50% 투명 WHITE (RGBA: 255, 255, 255, 128)
        width_expanded_mask[exp_y_min:exp_y_max, exp_x_min:exp_x_max] = [255, 255, 255, 128]

        # 원본 + Height 확장 영역에 100% 불투명 WHITE (RGBA: 255, 255, 255, 255)
        height_expanded_mask[height_y_min:height_y_max, height_x_min:height_x_max] = [255, 255, 255, 255]

        masked_count += 1

        if masked_count <= 5:
            print(f"  C{cluster_id}: {line_count}줄 - "
                  f"전체확장 ({exp_x_max-exp_x_min}x{exp_y_max-exp_y_min}), "
                  f"GREEN영역 ({height_x_max-height_x_min}x{height_y_max-height_y_min})")
            if cluster_id == 1:
                print(f"    [DEBUG] 전체 확장 poly: {expanded_poly}")
                print(f"    [DEBUG] GREEN(height) poly: {height_exp_poly}")

    if masked_count > 5:
        print(f"  ... 외 {masked_count - 5}개")

    print(f"\n✅ {masked_count}개 Cluster Masking 완료")

    # 이미지를 BGRA로 변환
    img_bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)

    # Width 확장 영역 마스크 적용 (50% 투명 BLUE)
    for y in range(img_height):
        for x in range(img_width):
            if width_expanded_mask[y, x, 3] > 0:  # 알파 채널이 0보다 크면
                alpha = width_expanded_mask[y, x, 3] / 255.0
                img_bgra[y, x, :3] = img_bgra[y, x, :3] * (1 - alpha) + width_expanded_mask[y, x, :3] * alpha

    # 원본 + Height 확장 영역 마스크 적용 (100% 불투명 GREEN)
    for y in range(img_height):
        for x in range(img_width):
            if height_expanded_mask[y, x, 3] > 0:  # 알파 채널이 0보다 크면
                img_bgra[y, x, :3] = height_expanded_mask[y, x, :3]

    # BGR로 다시 변환
    result = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

    # 저장
    if output_path is None:
        output_path = Path(image_path).parent / f"{Path(image_path).stem}_masked.jpg"

    cv2.imwrite(str(output_path), result)

    print(f"\n{'='*60}")
    print(f"✅ Color Masking 완료: {output_path}")
    print(f"{'='*60}")
    print(f"\nMasking 규칙:")
    print(f"  🟩 GREEN 영역: 원본 + Height 확장 영역 (100% 불투명)")
    print(f"  🟦 BLUE 영역: Width 확장 영역만 (50% 투명)")
    print(f"\n확장 규칙:")
    print(f"  Width: 1줄=2.0배, 2줄=1.33배, 3줄=1.0배, 4줄+=유지")
    print(f"  Height: 1줄인 경우 텍스트 길이 비율로 확장")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python apply_text_overlay.py <image> <integrated.json> <cv_clusters_v5.json> [output.jpg]")
        print("\nExample:")
        print("  python apply_text_overlay.py temp_output/00038.jpg temp_output/00038_integrated.json temp_output/00038_cv_clusters_v5.json")
        sys.exit(1)

    image_path = sys.argv[1]
    integrated_json = sys.argv[2]
    cv_clusters_json = sys.argv[3]
    output_path = sys.argv[4] if len(sys.argv) > 4 else None

    apply_white_masking(image_path, integrated_json, cv_clusters_json, output_path)
