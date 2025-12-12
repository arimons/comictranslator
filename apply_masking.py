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




def apply_white_masking(
    image_path: str,
    integrated_json_path: str,
    cv_clusters_path: str,
    output_path: str = None,
    debug_viz: bool = False
):
    """
    White Masking 적용

    Args:
        image_path: 원본 이미지 경로
        integrated_json_path: integrated.json 경로
        cv_clusters_path: cv_clusters_v5.json 경로
        output_path: 결과 저장 경로
        debug_viz: True면 원본 bbox(Green) + 확장 bbox(Blue)를 시각화
    """
    print(f"\n{'='*60}")
    print(f"🎨 White Masking 적용")
    if debug_viz:
        print(f"🔍 [DEBUG MODE] CV bbox(Green) + 확장(Blue) 시각화")
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
        # ✅ bounding_rect [x, y, w, h] 사용 (V2, V3 모두 동일)
        rect = sentence.get('bounding_rect', [0, 0, 0, 0])

        # [x, y, w, h]를 polygon 형식으로 변환
        poly = rect_to_polygon(rect)

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

        # ✅ NEW: integrated.json에서 orientation 직접 읽기
        orientation = sentence.get('orientation', 'vertical')

        if cv_line_count == 0 or not text_sentence:
            continue

        # ✅ NEW: integrated.json의 bounding_rect를 그대로 사용 (확장 없음)
        # poly는 이미 rect_to_polygon으로 변환된 상태
        
        # Debug visualization
        if debug_viz and img_debug is not None:
            cv2.polylines(img_debug, [np.array(poly, dtype=np.int32)], isClosed=True, color=(0, 255, 0), thickness=2)

        # sentence_id 가져오기
        sentence_id = sentence.get('sentence_id', 0)

        # 마스킹 색상
        mask_color = (0, 255, 0) if debug_viz else (255, 255, 255)  # Green or White (BGR)

        # Location 기반 마스킹
        if location == 'in_bubble':
            # in_bubble: 100% 불투명 흰색
            cv2.fillPoly(height_expanded_mask, [np.array(poly, dtype=np.int32)], mask_color + (255,))
        else:
            # outside_bubble: 50% 투명도 흰색
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
        print(f"  🟩 초록색 Box: 원본 CV BBox")
        print(f"  🟦 파란색 Box: 최종 확장 BBox")


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
