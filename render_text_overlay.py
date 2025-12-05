#!/usr/bin/env python3
"""
한글 텍스트 오버레이 적용
확장된 bbox에 맞춰 능동적으로 폰트 크기를 조정하여 한글 텍스트를 렌더링
"""
import json
import sys
import cv2
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def calculate_expanded_bbox(
    bounding_poly,
    line_count,
    orientation='vertical'
):
    """
    Line 개수에 따라 확장된 bbox 계산 (수정된 로직)
    
    확장 비율:
    - line수 < 4: 4 / (line수 + 1)
    - line수 >= 4: 1.0 (원래 width 유지)
    
    Args:
        bounding_poly: 4개 좌표 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        line_count: 실제 line 개수
        orientation: 'vertical' 또는 'horizontal'
    
    Returns:
        (원본 bbox [x, y, w, h], 확장된 bbox [x, y, w, h])
    """
    xs = [p[0] for p in bounding_poly]
    ys = [p[1] for p in bounding_poly]
    
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    
    current_width = x_max - x_min
    current_height = y_max - y_min
    
    if line_count == 0:
        line_count = 1
    
    # Width 확장 비율 계산 (수정된 로직)
    if line_count >= 4:
        width_expansion_ratio = 1.0
    else:
        width_expansion_ratio = 4.0 / (line_count + 1)
    
    # Vertical: width 확장
    if orientation == 'vertical':
        new_width = current_width * width_expansion_ratio
        width_expansion = new_width - current_width
        
        # Width: 중점 기준으로 좌우로 확장
        new_x_min = x_min - width_expansion / 2
        new_x_max = x_max + width_expansion / 2
        new_y_min = y_min
        new_y_max = y_max
    else:  # horizontal
        # Horizontal: height 확장
        new_height = current_height * width_expansion_ratio
        height_expansion = new_height - current_height
        
        # Height: 중점 기준으로 상하로 확장
        new_y_min = y_min - height_expansion / 2
        new_y_max = y_max + height_expansion / 2
        new_x_min = x_min
        new_x_max = x_max
    
    orig_bbox = [int(x_min), int(y_min), int(current_width), int(current_height)]
    exp_bbox = [int(new_x_min), int(new_y_min), int(new_x_max - new_x_min), int(new_y_max - new_y_min)]
    
    return orig_bbox, exp_bbox


def calculate_max_chars(bbox_width, font_size):
    """
    bbox width와 폰트 크기에 따라 한 줄에 들어갈 최대 글자수 계산
    
    Args:
        bbox_width: bbox의 width (픽셀)
        font_size: 사용할 폰트 크기 (픽셀)
    
    Returns:
        최대 글자수
    """
    # 한글 글자 폭은 대략 폰트 크기의 0.9배
    # 여유 공간 10% 추가 고려
    usable_width = bbox_width * 0.85
    avg_char_width = font_size * 0.9
    max_chars = int(usable_width / avg_char_width)
    return max(1, max_chars)


def split_text_intelligently(text, min_chars=4):
    """
    텍스트를 의미 단위로 줄바꿈
    
    우선순위:
    1. 말줄임표(..., …, ……) 뒤에서 줄바꿈 (최우선)
    2. 띄어쓰기 기준 - 자연스러운 단어 경계
    3. 최소 글자수는 권장사항 (말줄임표 등 예외 허용)
    
    Args:
        text: 원본 텍스트
        min_chars: 한 줄 권장 최소 글자수 (기본값 4, 강제 아님)
    
    Returns:
        분리된 줄들의 리스트
    """
    if not text:
        return []
    
    lines = []
    current_line = ""
    
    # 말줄임표 패턴으로 먼저 분리
    import re
    # 말줄임표(......, ..., …, ……) 뒤에서 분할
    parts = re.split(r'(\.{2,}|…+)', text)
    
    for part_idx, part in enumerate(parts):
        if not part:
            continue
        
        # 말줄임표 자체인 경우
        if re.match(r'^(\.{2,}|…+)$', part):
            current_line += part
            # 말줄임표 뒤에서 줄바꿈 (다음 파트가 있으면)
            if part_idx < len(parts) - 1 and parts[part_idx + 1].strip():
                lines.append(current_line)
                current_line = ""
            continue
        
        # 일반 텍스트는 띄어쓰기로 분리
        words = part.split()
        
        for word_idx, word in enumerate(words):
            # 현재 줄에 단어 추가
            if current_line:
                test_line = current_line + " " + word
            else:
                test_line = word
            
            # 줄바꿈 판단
            is_last_word = (word_idx == len(words) - 1) and (part_idx == len(parts) - 1)
            
            # 1. 마지막 단어면 추가하고 종료
            if is_last_word:
                current_line = test_line
                break
            
            # 2. 현재 줄이 적당한 길이면 계속 추가
            if len(test_line) < min_chars * 1.5:  # 권장 길이보다 약간 여유
                current_line = test_line
            else:
                # 너무 길어지므로 현재 줄 완료하고 다음 줄로
                if current_line:
                    lines.append(current_line)
                current_line = word
    
    # 남은 줄 추가
    if current_line:
        lines.append(current_line)
    
    return lines if lines else [text]


def calculate_font_size(bbox_width, bbox_height, text):
    """
    bbox 크기와 텍스트 길이에 맞는 폰트 크기 계산
    
    기본 30px, ±3px 범위 (27-33px)
    최소 4글자/줄
    
    Args:
        bbox_width: bbox 너비
        bbox_height: bbox 높이
        text: 렌더링할 텍스트
    
    Returns:
        적절한 폰트 크기 (픽셀)
    """
    base_font_size = 30
    min_font_size = 27
    max_font_size = 33
    min_chars = 4  # 최소
    
    # 기본 30px로 시작해서 bbox에 맞는지 확인
    for font_size in range(base_font_size, min_font_size - 1, -1):
        lines = split_text_intelligently(text, min_chars)
        line_count = len(lines)
        
        # 줄 간격 1.3 고려
        required_height = font_size * line_count * 1.3
        
        # 높이가 bbox에 맞으면 이 폰트 크기 사용
        if required_height <= bbox_height * 0.95:
            return font_size
    
    # 최소값도 안 맞으면, 더 큰 폰트 시도
    for font_size in range(base_font_size + 1, max_font_size + 1):
        lines = split_text_intelligently(text, min_chars)
        line_count = len(lines)
        
        required_height = font_size * line_count * 1.3
        
        if required_height <= bbox_height * 0.95:
            return font_size
    
    # 모든 경우에 기본값 반환
    return base_font_size


def render_korean_text(
    image,
    text,
    bbox,
    original_bbox=None,
    orientation='vertical',
    font_path="Fonts/Freesentation-4Regular.ttf",
    color=(0, 0, 0),
    align='center'
):
    """
    한글 텍스트를 이미지에 렌더링
    
    Args:
        image: OpenCV 이미지 (BGR)
        text: 렌더링할 텍스트
        bbox: [x, y, w, h] 확장된 bbox
        original_bbox: [x, y, w, h] 원본 bbox (짧은 텍스트 중앙 정렬용)
        orientation: 'vertical' 또는 'horizontal'
        font_path: 폰트 파일 경로
        color: RGB 색상 튜플
        align: 텍스트 정렬 ('left', 'center', 'right')
    
    Returns:
        텍스트가 렌더링된 이미지
    """
    if not text:
        return image
    
    x, y, w, h = bbox
    
    # PIL 이미지로 변환
    pil_img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    
    # 폰트 크기 계산
    font_size = calculate_font_size(w, h, text)
    
    try:
        font = ImageFont.truetype(font_path, font_size)
    except:
        print(f"⚠️  폰트 로드 실패: {font_path}, 기본 폰트 사용")
        font = ImageFont.load_default()
    
    # 텍스트를 의미 단위로 줄별로 분리 (최소 4글자)
    min_chars = 4
    lines = split_text_intelligently(text, min_chars)
    
    # 줄 간격
    line_height = font_size * 1.3
    total_text_height = line_height * len(lines)
    
    # 짧은 텍스트 (4글자 이하, 1줄, 세로쓰기)의 경우 원본 bbox 중간부터 시작
    use_original_center = (
        original_bbox is not None and 
        len(text.replace('?', '').replace('!', '').replace(' ', '')) <= 4 and 
        len(lines) == 1 and 
        orientation == 'vertical'
    )
    
    if use_original_center:
        # 원본 bbox의 중간 y 좌표
        orig_x, orig_y, orig_w, orig_h = original_bbox
        center_y = orig_y + orig_h / 2
        # 중간부터 시작
        start_y = center_y
    else:
        # 기본: 확장된 bbox 내에서 수직 중앙 정렬
        start_y = y + (h - total_text_height) / 2
    
    # 각 줄 렌더링
    for i, line in enumerate(lines):
        # 텍스트 bbox 계산
        try:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
        except:
            text_width = len(line) * font_size * 0.6
        
        # 수평 정렬
        if align == 'center':
            text_x = x + (w - text_width) / 2
        elif align == 'right':
            text_x = x + w - text_width - 5
        else:  # left
            text_x = x + 5
        
        text_y = start_y + i * line_height
        
        # 텍스트 그리기
        draw.text((text_x, text_y), line, font=font, fill=color)
    
    # OpenCV 이미지로 변환
    result = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    
    return result


def apply_text_overlay(
    masked_image_path: str,
    integrated_json_path: str,
    cv_clusters_path: str,
    output_path: str = None,
    font_path: str = "Fonts/Freesentation-4Regular.ttf"
):
    """
    Masked 이미지에 한글 번역 텍스트 오버레이
    
    Args:
        masked_image_path: White masking이 적용된 이미지 경로
        integrated_json_path: integrated_translated.json 경로
        cv_clusters_path: cv_clusters_v5.json 경로 (line 개수 확인용)
        output_path: 결과 저장 경로
        font_path: 한글 폰트 파일 경로
    """
    print(f"\n{'='*60}")
    print(f"📝 텍스트 오버레이 적용")
    print(f"{'='*60}")
    
    # 이미지 로드
    img = cv2.imread(masked_image_path)
    if img is None:
        print(f"❌ 이미지를 불러올 수 없습니다: {masked_image_path}")
        return
    
    img_height, img_width = img.shape[:2]
    print(f"\n📐 이미지 크기: {img_width} x {img_height}")
    
    # Integrated JSON 로드
    with open(integrated_json_path, 'r', encoding='utf-8') as f:
        clusters = json.load(f)
    
    # CV Clusters 로드 (line 개수 확인용)
    with open(cv_clusters_path, 'r', encoding='utf-8') as f:
        cv_clusters = json.load(f)
    
    # CV cluster를 ID로 매핑
    cv_cluster_map = {c['cluster_id']: c for c in cv_clusters}
    
    print(f"📦 {len(clusters)}개 Cluster 로드")
    print(f"\n{'='*60}")
    print(f"🖌️  텍스트 렌더링 중...")
    print(f"{'='*60}\n")
    
    rendered_count = 0
    
    for cluster in clusters:
        cluster_id = cluster['cluster_id']
        poly = cluster['bounding_poly']
        orientation = cluster.get('orientation', 'vertical')
        translated_text = cluster.get('translated', '')
        
        if not translated_text:
            continue
        
        # CV cluster에서 line 정보 가져오기
        cv_cluster = cv_cluster_map.get(cluster_id)
        if cv_cluster:
            lines = cv_cluster.get('lines', [])
            line_count = len(lines)
        else:
            line_count = 1
        
        # 확장된 bbox 계산
        orig_bbox, exp_bbox = calculate_expanded_bbox(poly, line_count, orientation)
        
        # 원본 bbox (poly에서 직접 계산)
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        orig_x, orig_y = min(xs), min(ys)
        orig_w, orig_h = max(xs) - orig_x, max(ys) - orig_y
        original_bbox = [orig_x, orig_y, orig_w, orig_h]
        
        # 이미지 범위 내로 제한
        exp_x, exp_y, exp_w, exp_h = exp_bbox
        exp_x = max(0, exp_x)
        exp_y = max(0, exp_y)
        exp_w = min(img_width - exp_x, exp_w)
        exp_h = min(img_height - exp_y, exp_h)
        
        exp_bbox = [exp_x, exp_y, exp_w, exp_h]
        
        # 한글 텍스트 렌더링 (가로쓰기)
        img = render_korean_text(
            img,
            translated_text,
            exp_bbox,
            original_bbox=original_bbox,
            orientation=orientation,
            font_path=font_path,
            color=(0, 0, 0),  # 검정색
            align='center'
        )
        
        rendered_count += 1
        
        if rendered_count <= 5:
            font_size = calculate_font_size(exp_w, exp_h, translated_text)
            min_chars = 4
            lines = split_text_intelligently(translated_text, min_chars)
            short_text = len(translated_text.replace('?', '').replace('!', '').replace(' ', '')) <= 4 and len(lines) == 1
            print(f"  C{cluster_id}: {line_count}줄 → '{translated_text[:20]}...'")
            print(f"         BBox: {exp_w}x{exp_h}px, 최소 {min_chars}자/줄, 폰트: {font_size}px")
            print(f"         줄바꿈: {lines}")
            if short_text and orientation == 'vertical':
                print(f"         ⭐ 짧은 텍스트: 원본 bbox 중간부터 시작")
    
    if rendered_count > 5:
        print(f"  ... 외 {rendered_count - 5}개")
    
    print(f"\n✅ {rendered_count}개 텍스트 렌더링 완료")
    
    # 저장
    if output_path is None:
        output_path = Path(masked_image_path).parent / f"{Path(masked_image_path).stem}_overlay.jpg"
    
    cv2.imwrite(str(output_path), img)
    
    print(f"\n{'='*60}")
    print(f"✅ 텍스트 오버레이 완료: {output_path}")
    print(f"{'='*60}")
    print(f"\nBBox 확장 규칙:")
    print(f"  • Line수 < 4: 4 / (line수 + 1)")
    print(f"  • Line수 >= 4: 원래 width 유지")
    print(f"\n폰트 크기 결정:")
    print(f"  • 기본 30px, ±3px 범위 (27-33px)")
    print(f"  • 최소 4글자/줄 (띄어쓰기 우선)")
    print(f"  • 기본 폰트: Freesentation-4Regular.ttf")
    print(f"\n짧은 텍스트 (4글자 이하, 1줄, 세로):")
    print(f"  • 원본 bbox 중간부터 렌더링")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python render_text_overlay.py <masked_image> <integrated_translated.json> <cv_clusters_v5.json> [output.jpg] [font.ttf]")
        print("\nExample:")
        print("  python render_text_overlay.py temp_output/00038_masked.jpg temp_output/00038_integrated_translated.json temp_output/00038_cv_clusters_v5.json")
        sys.exit(1)
    
    masked_image = sys.argv[1]
    integrated_json = sys.argv[2]
    cv_clusters_json = sys.argv[3]
    output_path = sys.argv[4] if len(sys.argv) > 4 else None
    font_path = sys.argv[5] if len(sys.argv) > 5 else "Fonts/Freesentation-4Regular.ttf"
    
    apply_text_overlay(masked_image, integrated_json, cv_clusters_json, output_path, font_path)
