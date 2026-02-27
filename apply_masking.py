#!/usr/bin/env python3
"""
White Masking 적용

마스킹 로직:
- outside_bubble: 50% 투명도 흰색 (말풍선 외부)
- in_bubble: 중심~90% 100% 흰색 + 90~100% 경계 50% 투명도 (말풍선 침범 안전장치)
"""
import json
import sys
import cv2
import numpy as np
from pathlib import Path


def rect_to_polygon(bounding_rect):
    """
    [x, y, w, h] 형식의 bounding_rect를 polygon 형식으로 변환

    Args:
        bounding_rect: [x, y, w, h]

    Returns:
        [[x1,y1], [x2,y1], [x2,y2], [x1,y2]] polygon 형식
    """
    if not bounding_rect or len(bounding_rect) != 4:
        return [[0, 0], [0, 0], [0, 0], [0, 0]]

    x, y, w, h = bounding_rect
    return [
        [x, y],
        [x + w, y],
        [x + w, y + h],
        [x, y + h]
    ]





def apply_distance_based_masking(
    mask_layer,
    poly,
    mask_color,
    inner_ratio=0.9
):
    """
    중심으로부터 거리 기반 alpha 마스킹

    Args:
        mask_layer: RGBA 마스크 레이어 (높이, 너비, 4)
        poly: polygon 좌표 [[x1,y1], ...]
        mask_color: (B, G, R) 색상값
        inner_ratio: 내부 영역 비율 (기본값 0.9 = 90%)
    """
    h, w = mask_layer.shape[:2]

    # 임시 마스크 생성 (polygon 영역)
    temp_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(temp_mask, [np.array(poly, dtype=np.int32)], 255)

    # Distance transform (중심으로부터 거리 계산)
    dist_transform = cv2.distanceTransform(temp_mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)

    # 최대 거리 찾기
    max_dist = np.max(dist_transform)
    if max_dist == 0:
        return

    # 거리값을 0~1로 정규화
    dist_normalized = dist_transform / max_dist

    # Alpha 결정: 중심~90% 영역은 255 (100% 불투명), 90~100% 경계는 128 (50% 투명)
    alpha_map = np.where(dist_normalized <= inner_ratio, 255, 128).astype(np.uint8)

    # 마스크 영역에만 적용
    mask_area = temp_mask > 0

    # 색상 적용
    mask_layer[mask_area, 0] = mask_color[0]  # B
    mask_layer[mask_area, 1] = mask_color[1]  # G
    mask_layer[mask_area, 2] = mask_color[2]  # R
    mask_layer[mask_area, 3] = alpha_map[mask_area]


def apply_corner_fade_masking(
    mask_layer,
    bbox,
    mask_color,
    edge_width=20,
    edge_height=15,
    alpha=0.5
):
    """
    우상단, 우하단 모서리만 대각선 Fade 마스킹
    - 좌측 전체: 100% white (불투명)
    - 우측 중앙: 100% white (모서리 위아래 제외)
    - 우상단 모서리: 대각선 gradient fade
    - 우하단 모서리: 대각선 gradient fade

    Args:
        mask_layer: RGBA 마스크 레이어 (높이, 너비, 4)
        bbox: [x, y, w, h] 형식
        mask_color: (B, G, R) 색상값
        edge_width: 우측 모서리 가로 범위 (픽셀)
        edge_height: 상단/하단 모서리 세로 범위 (픽셀)
        alpha: 반투명도 (0~1, 모서리의 최대값)
    """
    h, w = mask_layer.shape[:2]
    x, y, bw, bh = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])

    # 이미지 범위 내로 제한
    y_start = max(0, y)
    y_end = min(h, y + bh)
    x_start = max(0, x)
    x_end = min(w, x + bw)

    # 1. 좌측 전체 (100% white)
    for row in range(y_start, y_end):
        for col in range(x_start, min(x + bw - edge_width, x_end)):
            mask_layer[row, col, 0] = mask_color[0]  # B
            mask_layer[row, col, 1] = mask_color[1]  # G
            mask_layer[row, col, 2] = mask_color[2]  # R
            mask_layer[row, col, 3] = 255  # 100% 불투명

    # 2. 우측 중앙 (100% white, 모서리 위아래 제외)
    for row in range(max(y_start, y + edge_height), min(y_end, y + bh - edge_height)):
        for col in range(max(x_start, x + bw - edge_width), x_end):
            mask_layer[row, col, 0] = mask_color[0]  # B
            mask_layer[row, col, 1] = mask_color[1]  # G
            mask_layer[row, col, 2] = mask_color[2]  # R
            mask_layer[row, col, 3] = 255  # 100% 불투명

    # 3. 우상단 모서리: 대각선 gradient (왼쪽 100% 불투명 → 오른쪽 50% 반투명)
    if edge_width > 0 and edge_height > 0:
        for row in range(y_start, min(y + edge_height, y_end)):
            for col in range(max(x_start, x + bw - edge_width), x_end):
                # 우상향 대각선 gradient (거리가 증가할수록 투명도 증가)
                dist_from_left = col - (x + bw - edge_width)
                dist_from_top = row - y

                # 유클리드 거리 기반 gradient
                max_dist = np.sqrt(edge_width**2 + edge_height**2)
                dist = np.sqrt(dist_from_left**2 + dist_from_top**2)
                # ✅ 방향 반대: dist=0일 때 불투명, dist=max_dist일 때 반투명
                gradient = max(0.0, 1.0 - dist / max_dist)

                # gradient=1.0일 때: 100% 불투명, gradient=0일 때: alpha(50%)로 반투명
                current_alpha = int((gradient + (1.0 - gradient) * alpha) * 255)

                mask_layer[row, col, 0] = mask_color[0]  # B
                mask_layer[row, col, 1] = mask_color[1]  # G
                mask_layer[row, col, 2] = mask_color[2]  # R
                mask_layer[row, col, 3] = max(mask_layer[row, col, 3], current_alpha)

    # 4. 우하단 모서리: 대각선 gradient (왼쪽 100% 불투명 → 오른쪽 50% 반투명)
    if edge_width > 0 and edge_height > 0:
        for row in range(max(y_start, y + bh - edge_height), y_end):
            for col in range(max(x_start, x + bw - edge_width), x_end):
                # 우하향 대각선 gradient (거리가 증가할수록 투명도 증가)
                dist_from_left = col - (x + bw - edge_width)
                dist_from_bottom = (y + bh) - row

                # 유클리드 거리 기반 gradient
                max_dist = np.sqrt(edge_width**2 + edge_height**2)
                dist = np.sqrt(dist_from_left**2 + dist_from_bottom**2)
                # ✅ 방향 반대: dist=0일 때 불투명, dist=max_dist일 때 반투명
                gradient = max(0.0, 1.0 - dist / max_dist)

                # gradient=1.0일 때: 100% 불투명, gradient=0일 때: alpha(50%)로 반투명
                current_alpha = int((gradient + (1.0 - gradient) * alpha) * 255)

                mask_layer[row, col, 0] = mask_color[0]  # B
                mask_layer[row, col, 1] = mask_color[1]  # G
                mask_layer[row, col, 2] = mask_color[2]  # R
                mask_layer[row, col, 3] = max(mask_layer[row, col, 3], current_alpha)


def apply_white_masking(
    image_path: str,
    integrated_json_path: str,
    cv_clusters_path: str,
    output_path: str = None,
    debug_viz: bool = False
):
    """
    White Masking 적용 (손글씨 제거)

    마스킹 로직:
    - in_bubble: 우상단, 우하단 모서리만 대각선 fade 마스킹
      * 좌측 전체: 100% white (불투명)
      * 우측 중앙: 100% white (모서리 위아래 제외)
      * 우상단/우하단 모서리: 대각선 gradient fade (말풍선 침범 방지)
    - outside_bubble: 모든 영역 50% 투명도 흰색 (말풍선 외부)

    Args:
        image_path: 원본 이미지 경로
        integrated_json_path: integrated.json 경로
        cv_clusters_path: cv_clusters_v5.json 경로
        output_path: 결과 저장 경로
        debug_viz: True면 원본 bbox(Green)를 시각화
    """
    print(f"\n{'='*60}")
    print(f"🎨 White Masking 적용")
    if debug_viz:
        print(f"🔍 [DEBUG MODE] bbox 시각화")
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
        data = json.load(f)

    # 새로운 구조 (clusters + unmatched_clusters) vs 기존 구조 호환성
    if isinstance(data, dict) and 'clusters' in data:
        clusters = data['clusters']
    else:
        clusters = data if isinstance(data, list) else []

    # CV Clusters 로드 (line 개수 및 CV 텍스트 확인용)
    with open(cv_clusters_path, 'r', encoding='utf-8') as f:
        cv_clusters = json.load(f)

    # CV cluster를 ID로 매핑 (cluster_id 문자열 기반)
    cv_cluster_map = {str(c['cluster_id']): c for c in cv_clusters}

    print(f"📦 {len(clusters)}개 Sentence 로드")
    
    # Debug visualization 이미지 (BGR)
    img_debug = img.copy() if debug_viz else None

    # 마스킹용 레이어 생성 (RGBA)
    # 마스킹용 레이어 생성 (RGBA)
    height_expanded_mask = np.zeros((img_height, img_width, 4), dtype=np.uint8)

    print(f"\n{'='*60}")
    print(f"🖌️ Masking 적용 중...")
    print(f"{'='*60}")

    # 디버그용 이미지 복사 (필요할 때만)
    img_debug = img.copy() if debug_viz else None

    masked_count = 0

    for sentence in clusters:
        # ✅ union_bbox 우선 사용 (복수 cluster 통합), 없으면 bounding_rect_expanded 또는 bounding_rect 사용
        rect = sentence.get('union_bbox') or sentence.get('bounding_rect_expanded') or sentence.get('bounding_rect', [0, 0, 0, 0])

        # V3 형식 지원: text 또는 V2 형식: text_sentence
        text_sentence = sentence.get('text') or sentence.get('text_sentence', '')
        location = sentence.get('location', 'in_bubble')

        # V3: matched_cluster (int) / V2: matched_clusters (string) 모두 지원
        matched_cluster = sentence.get('matched_cluster')
        if matched_cluster is not None:
            # V3 형식
            cluster_id_str = str(matched_cluster)
        else:
            # V2 형식: matched_clusters 문자열에서 추출
            matched_clusters = sentence.get('matched_clusters', '')
            cluster_id_str = matched_clusters.replace('C', '').split('+')[0].strip() if matched_clusters else ''

        # CV cluster에서 line 정보 가져오기
        cv_cluster = cv_cluster_map.get(cluster_id_str)
        if cv_cluster:
            lines = cv_cluster.get('lines', [])
            cv_line_count = len(lines)
        else:
            # CV cluster가 없으면 기본값
            cv_line_count = 1

        if cv_line_count == 0 or not text_sentence:
            continue

        # [x, y, w, h]를 polygon 형식으로 변환 (integrated.json 값 그대로 사용)
        poly = rect_to_polygon(rect)

        # sentence_id 가져오기
        sentence_id = sentence.get('sentence_id', 0)

        # 마스킹 색상
        mask_color = (0, 255, 0) if debug_viz else (255, 255, 255)  # Green or White (BGR)

        # Debug visualization (bbox와 마스킹 영역 표시)
        if debug_viz and img_debug is not None:
            if location == 'in_bubble':
                # in_bubble: 초록색 전체 bbox + 빨강색 모서리 영역
                cv2.polylines(img_debug, [np.array(poly, dtype=np.int32)], isClosed=True, color=(0, 255, 0), thickness=2)

                # 우상단, 우하단 모서리 영역 표시 (빨강색)
                x, y, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
                edge_width, edge_height = 20, 15

                # 우상단 모서리 (빨강)
                pt1_top = (x + w - edge_width, y)
                pt2_top = (x + w, y + edge_height)
                cv2.rectangle(img_debug, pt1_top, pt2_top, (0, 0, 255), 2)

                # 우하단 모서리 (빨강)
                pt1_bottom = (x + w - edge_width, y + h - edge_height)
                pt2_bottom = (x + w, y + h)
                cv2.rectangle(img_debug, pt1_bottom, pt2_bottom, (0, 0, 255), 2)

                # 우측 중앙 (100% white) 표시 (노란색)
                pt1_middle = (x + w - edge_width, y + edge_height)
                pt2_middle = (x + w, y + h - edge_height)
                cv2.rectangle(img_debug, pt1_middle, pt2_middle, (0, 255, 255), 1)

                # 좌측 전체 (100% white) 표시 (초록색)
                pt1_left = (x, y)
                pt2_left = (x + w - edge_width, y + h)
                cv2.rectangle(img_debug, pt1_left, pt2_left, (0, 255, 0), 1)

                # 텍스트 라벨
                cv2.putText(img_debug, f"S{sentence_id}(in)", (x, y - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            else:
                # outside_bubble: 파랑색 전체 bbox
                cv2.polylines(img_debug, [np.array(poly, dtype=np.int32)], isClosed=True, color=(255, 0, 0), thickness=2)

                # 텍스트 라벨
                x, y = int(rect[0]), int(rect[1])
                cv2.putText(img_debug, f"S{sentence_id}(out)", (x, y - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

        # Location 기반 마스킹
        if location == 'in_bubble':
            # in_bubble: 우상단, 우하단 모서리만 대각선 fade 마스킹
            # 좌측 100% white, 우측 중앙 100% white, 우상단/우하단 gradient fade
            apply_corner_fade_masking(height_expanded_mask, rect, mask_color[:3],
                                     edge_width=20, edge_height=15, alpha=0.5)
        else:
            # outside_bubble: 모든 영역 50% 투명도 흰색 (말풍선 외부)
            cv2.fillPoly(height_expanded_mask, [np.array(poly, dtype=np.int32)], mask_color + (128,))

        masked_count += 1

        if masked_count <= 5:
            matched_display = f"C{cluster_id_str}" if cluster_id_str else "?"
            print(f"  S{sentence_id}→{matched_display}: {text_sentence[:30]}")
            print(f"    위치: {location}, BBox: {rect}")

    if masked_count > 5:
        print(f"  ... 외 {masked_count - 5}개")

    print(f"\n✅ {masked_count}개 Cluster Masking 완료")

    # --- 마스크 적용 (NumPy 최적화) ---
    # 이미지를 BGRA로 변환
    img_bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)

    # 마스크 적용 (알파 블렌딩)
    # 알파 채널이 0보다 큰 영역만 선택
    height_mask_area = height_expanded_mask[:, :, 3] > 0
    alpha = height_expanded_mask[height_mask_area, 3].reshape(-1, 1) / 255.0
    mask_color = height_expanded_mask[height_mask_area, :3]
    
    # 알파 블렌딩: result = original * (1 - alpha) + mask_color * alpha
    img_bgra[height_mask_area, :3] = (
        img_bgra[height_mask_area, :3] * (1 - alpha) +
        mask_color * alpha
    ).astype(np.uint8)

    # BGR로 다시 변환
    result = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

    # 결과 저장
    if output_path is None:
        output_path = Path(image_path).parent / f"{Path(image_path).stem}_masked.jpg"
    cv2.imwrite(str(output_path), result)

    # ✅ NEW: 디버그 이미지 저장
    if debug_viz and img_debug is not None:
        debug_output_path = Path(output_path).parent / f"{Path(output_path).stem}_debug.jpg"
        cv2.imwrite(str(debug_output_path), img_debug)
        print(f"🔍 Debug 시각화 저장: {debug_output_path}")

    print(f"\n{'='*60}")
    if debug_viz:
        print(f"✅ Color Masking (Debug) 완료: {output_path}")
    else:
        print(f"✅ White Masking 완료: {output_path}")
    print(f"{'='*60}")

    if debug_viz:
        print(f"\nDebug 시각화 규칙:")
        print(f"  🟩 초록색 Box: Integrated.json BBox")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python apply_masking.py <image> <integrated.json> <cv_clusters_v5.json> [output.jpg] [--debug]")
        print("\nExample:")
        print("  python apply_masking.py temp_output/00038.jpg temp_output/00038_integrated.json temp_output/00038_cv_clusters_v5.json")
        print("\nDebug Mode Example:")
        print("  python apply_masking.py temp_output/00038.jpg temp_output/00038_integrated.json temp_output/00038_cv_clusters_v5.json --debug")
        sys.exit(1)

    image_path = sys.argv[1]
    integrated_json = sys.argv[2]
    cv_clusters_json = sys.argv[3]
    
    # Output path와 debug flag 처리
    output_path = None
    debug_mode = False
    if len(sys.argv) > 4:
        if sys.argv[4] != '--debug':
            output_path = sys.argv[4]
    if '--debug' in sys.argv:
        debug_mode = True

    apply_white_masking(image_path, integrated_json, cv_clusters_json, output_path, debug_viz=debug_mode)
