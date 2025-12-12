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


def split_compound_word(word, max_chars):
    """
    긴 단어를 의미있는 경계에서 분리

    한국어 복합동사 및 어미 패턴 기반 분리

    Args:
        word: 분리할 단어
        max_chars: 최대 글자수

    Returns:
        분리된 부분들의 리스트 (분리 불가능하면 [word])
    """
    import re

    # max_chars 이하면 분리 필요 없음
    if len(word) <= max_chars:
        return [word]

    # 1. 복합동사 패턴 (어간 + 보조동사)
    # "피어오르는데요" → "피어" + "오르는데요"
    compound_patterns = [
        (r'^(.{2,}?)(어|아)(오르|가|나|지|내|대|버리|주|보)(.*)$', 2),  # 어/아 + 보조동사
        (r'^(.{2,}?)(고)(있|싶|말)(.*)$', 2),  # 고 + 있다/싶다/말다
    ]

    for pattern, split_after_group in compound_patterns:
        match = re.match(pattern, word)
        if match:
            # 첫 부분: 어간 + 연결어미
            part1 = ''.join(match.groups()[:split_after_group])
            # 나머지: 보조동사 + 어미
            part2 = ''.join(match.groups()[split_after_group:])

            # 분리 후 둘 다 너무 짧지 않은지 확인
            if len(part1) >= 2 and len(part2) >= 2:
                # 재귀적으로 part2도 확인
                if len(part2) > max_chars:
                    sub_parts = split_compound_word(part2, max_chars)
                    return [part1] + sub_parts
                else:
                    return [part1, part2]

    # 2. 어미 분리 (마지막 수단)
    # "오르는데요" → "오르는" + "데요"
    # 일반적인 한국어 어미 패턴
    ending_patterns = [
        (r'^(.{2,}?)(는데요?|ㄴ데요?|은데요?)$', 1),  # ~는데요
        (r'^(.{2,}?)(습니까|ㅂ니까|ㄴ가요?|나요?)$', 1),  # 의문형 어미
        (r'^(.{2,}?)(는지|ㄴ지|을지)(.*)$', 1),  # ~는지
        (r'^(.{3,}?)(네요|군요|거든요?)$', 1),  # ~네요, ~군요
    ]

    for pattern, split_after_group in ending_patterns:
        match = re.match(pattern, word)
        if match:
            part1 = ''.join(match.groups()[:split_after_group])
            part2 = ''.join(match.groups()[split_after_group:])

            if len(part1) >= 2 and len(part2) >= 2:
                return [part1, part2]

    # 3. 모든 패턴 실패 시 음절 단위로 강제 분리
    # 하지만 너무 짧지 않게
    if len(word) > max_chars * 1.5:
        mid = len(word) // 2
        return [word[:mid], word[mid:]]

    # 분리 불가능
    return [word]


def split_text_intelligently(text, max_chars=8, min_chars=3):
    """
    텍스트를 의미 단위로 줄바꿈

    타원형 말풍선 고려:
    - 첫 줄/마지막 줄: max_chars (타이트)
    - 중간 줄들: max_chars × 1.4 (여유)

    우선순위:
    1. 말줄임표 + 특수문자는 분리 (...? → 별도 줄)
    2. 띄어쓰기 기준 - 자연스러운 단어 경계
    3. 복합동사 분리 (긴 단어 처리)
    4. 최대 글자수 강제 적용 (타원형 고려)

    Args:
        text: 원본 텍스트
        max_chars: 한 줄 최대 글자수 (강제, 기본값 8)
        min_chars: 한 줄 최소 글자수 (권장, 기본값 3)

    Returns:
        분리된 줄들의 리스트
    """
    if not text:
        return []

    import re

    lines = []
    current_line = ""

    # 말줄임표 + 특수문자를 분리 (앞 텍스트와 줄바꿈)
    # "뭐지...?" → "뭐지" + "...?"
    # 패턴: (일반텍스트)(말줄임표+특수문자)
    chunks = re.split(r'(\.{2,}[?!]*|…+[?!]*)', text)

    for chunk_idx, chunk in enumerate(chunks):
        if not chunk:
            continue

        # 말줄임표 + 특수문자 청크인 경우
        if re.match(r'^(\.{2,}|…+)[?!]*$', chunk):
            # 현재 줄이 있으면 먼저 완료
            if current_line:
                lines.append(current_line)
                current_line = ""
            # 말줄임표+특수문자를 별도 줄로
            lines.append(chunk)
            continue

        # 일반 텍스트는 띄어쓰기로 분리
        words = chunk.split()

        for word_idx, word in enumerate(words):
            # 단어가 너무 긴 경우 복합동사 분리 시도
            if len(word) > max_chars:
                sub_words = split_compound_word(word, max_chars)
                words[word_idx:word_idx+1] = sub_words  # 리스트에서 교체

        # 다시 분리된 단어들로 줄바꿈 처리
        for word_idx, word in enumerate(words):
            # 현재 줄에 단어 추가했을 때의 길이 계산
            if current_line:
                test_line = current_line + " " + word
            else:
                test_line = word

            # 줄바꿈 판단
            is_last_word = (word_idx == len(words) - 1) and (chunk_idx == len(chunks) - 1)

            # 타원형 말풍선 고려: 중간 줄은 여유있게
            # 첫 줄이거나 마지막이 될 가능성이 있으면 타이트하게
            if len(lines) == 0:
                # 첫 줄: 타이트
                effective_max = max_chars
            elif is_last_word:
                # 마지막 줄: 타이트
                effective_max = max_chars
            else:
                # 중간 줄: 여유 (1.4배)
                effective_max = int(max_chars * 1.4)

            # 1. 최대 글자수 초과 체크 (타원형 고려)
            if len(test_line) > effective_max:
                # 현재 줄이 비어있지 않으면 현재 줄 완료
                if current_line:
                    lines.append(current_line)
                    current_line = word
                else:
                    # 현재 줄이 비어있는데 단어 자체가 max_chars 초과
                    # 이미 split_compound_word 시도했으므로 그대로 추가
                    current_line = word
            # 2. 마지막 단어면 추가하고 종료
            elif is_last_word:
                current_line = test_line
                break
            # 3. 적당한 길이면 계속 추가
            else:
                current_line = test_line

    # 남은 줄 추가
    if current_line:
        lines.append(current_line)

    return lines if lines else [text]


def calculate_font_size(bbox_width, bbox_height, text, base_font_size=33):
    """
    bbox 크기와 텍스트 길이에 맞는 폰트 크기 계산

    기본 33px, 마진 대비 범위 조정
    bbox_width 기반으로 최대 글자수 계산하여 줄바꿈

    Args:
        bbox_width: bbox 너비
        bbox_height: bbox 높이
        text: 렌더링할 텍스트
        base_font_size: 기본 폰트 크기 (기본값 33)

    Returns:
        (폰트 크기, 최대 글자수)
    """
    min_font_size = max(20, base_font_size - 6)  # 최소 -6px
    max_font_size = base_font_size + 3  # 최대 +3px

    # bbox_width 기반으로 최대 글자수 계산
    def calc_max_chars_for_font(font_size):
        usable_width = bbox_width * 0.85
        avg_char_width = font_size * 0.9
        return max(3, int(usable_width / avg_char_width))

    # 기본 30px로 시작해서 bbox에 맞는지 확인
    for font_size in range(base_font_size, min_font_size - 1, -1):
        max_chars = calc_max_chars_for_font(font_size)
        lines = split_text_intelligently(text, max_chars)
        line_count = len(lines)

        # 줄 간격 1.3 고려
        required_height = font_size * line_count * 1.3

        # 높이가 bbox에 맞으면 이 폰트 크기 사용
        if required_height <= bbox_height * 0.95:
            return font_size, max_chars

    # 최소값도 안 맞으면, 더 큰 폰트 시도
    for font_size in range(base_font_size + 1, max_font_size + 1):
        max_chars = calc_max_chars_for_font(font_size)
        lines = split_text_intelligently(text, max_chars)
        line_count = len(lines)

        required_height = font_size * line_count * 1.3

        if required_height <= bbox_height * 0.95:
            return font_size, max_chars

    # 모든 경우에 기본값 반환
    max_chars = calc_max_chars_for_font(base_font_size)
    return base_font_size, max_chars


def render_korean_text(
    image,
    text,
    bbox,
    original_bbox=None,
    orientation='vertical',
    font_path="Fonts/Freesentation-4Regular.ttf",
    color=(0, 0, 0),
    align='center',
    base_font_size=33
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
        base_font_size: 기본 폰트 크기 (기본값 33)
    
    Returns:
        텍스트가 렌더링된 이미지
    """
    if not text:
        return image
    
    x, y, w, h = bbox
    
    # PIL 이미지로 변환
    pil_img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)

    # 폰트 크기 및 최대 글자수 계산
    font_size, max_chars = calculate_font_size(w, h, text, base_font_size)

    try:
        font = ImageFont.truetype(font_path, font_size)
    except:
        print(f"⚠️  폰트 로드 실패: {font_path}, 기본 폰트 사용")
        font = ImageFont.load_default()

    # 텍스트를 의미 단위로 줄별로 분리 (max_chars 기반)
    lines = split_text_intelligently(text, max_chars)
    
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
    font_path: str = "Fonts/Freesentation-4Regular.ttf",
    base_font_size: int = 33
):
    """
    Masked 이미지에 한글 번역 텍스트 오버레이
    
    Args:
        masked_image_path: White masking이 적용된 이미지 경로
        integrated_json_path: integrated_translated.json 경로
        cv_clusters_path: cv_clusters_v5.json 경로 (line 개수 확인용)
        output_path: 결과 저장 경로
        font_path: 한글 폰트 파일 경로
        base_font_size: 기본 폰트 크기 (기본값 33)
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
        # V3 형식 지원: matched_cluster (int) 또는 V2 형식: cluster_id (int)
        cluster_id = cluster.get('matched_cluster') or cluster.get('cluster_id', 0)

        # ✅ bounding_rect [x, y, w, h] 사용 (V3 형식)
        rect = cluster.get('bounding_rect', [0, 0, 0, 0])

        # [x, y, w, h]를 polygon 형식으로 변환
        poly = rect_to_polygon(rect)

        orientation = cluster.get('orientation', 'vertical')
        translated_text = cluster.get('text_translated', '')

        if not translated_text:
            continue

        # V3: matched_cluster (int) / V2: matched_clusters (string) 모두 지원
        matched_cluster = cluster.get('matched_cluster')
        if matched_cluster is not None:
            # V3 형식: matched_cluster는 이미 int
            cv_cluster_id = matched_cluster
        else:
            # V2 형식: matched_clusters에서 추출
            matched_clusters = cluster.get('matched_clusters', '')
            cluster_id_str = matched_clusters.replace('C', '').split('+')[0].strip() if matched_clusters else str(cluster_id)
            try:
                cv_cluster_id = int(cluster_id_str) if cluster_id_str else cluster_id
            except:
                cv_cluster_id = cluster_id

        # CV cluster에서 line 정보 가져오기
        cv_cluster = cv_cluster_map.get(cv_cluster_id)
            
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
            align='center',
            base_font_size=base_font_size
        )
        
        rendered_count += 1

        if rendered_count <= 5:
            font_size, max_chars = calculate_font_size(exp_w, exp_h, translated_text, base_font_size)
            lines = split_text_intelligently(translated_text, max_chars)
            short_text = len(translated_text.replace('?', '').replace('!', '').replace(' ', '')) <= 4 and len(lines) == 1
            print(f"  C{cluster_id}: {line_count}줄 → '{translated_text[:20]}...'")
            print(f"         BBox: {exp_w}x{exp_h}px, 최대 {max_chars}자/줄, 폰트: {font_size}px")
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
    print(f"  • 기본 33px, ±3px 범위 (30-36px)")
    print(f"  • bbox_width 기반 최대 글자수 계산하여 줄바꿈 강제")
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
    font_path = sys.argv[5] if len(sys.argv) > 5 else "fonts/Freesentation-5Medium.ttf"
    
    apply_text_overlay(masked_image, integrated_json, cv_clusters_json, output_path, font_path)
