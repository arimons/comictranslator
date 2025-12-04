#!/usr/bin/env python3
"""
Comic Translator - 핵심 처리 함수들
White masking과 text overlay 기능을 함수형으로 제공
"""

import cv2
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import platform
import math
import textwrap

def draw_rounded_rectangle(img, pt1, pt2, color, thickness, radius):
    """Draw a rounded rectangle"""
    x1, y1 = pt1
    x2, y2 = pt2
    
    radius = min(radius, abs(x2 - x1) // 2, abs(y2 - y1) // 2)
    
    if thickness == -1:
        mask = np.zeros(img.shape[:2], dtype=np.uint8)
        cv2.rectangle(mask, (x1 + radius, y1), (x2 - radius, y2), 255, -1)
        cv2.rectangle(mask, (x1, y1 + radius), (x2, y2 - radius), 255, -1)
        cv2.circle(mask, (x1 + radius, y1 + radius), radius, 255, -1)
        cv2.circle(mask, (x2 - radius, y1 + radius), radius, 255, -1)
        cv2.circle(mask, (x1 + radius, y2 - radius), radius, 255, -1)
        cv2.circle(mask, (x2 - radius, y2 - radius), radius, 255, -1)
        img[mask == 255] = color

def draw_ellipse(img, pt1, pt2, color, thickness):
    """Draw an ellipse fitting the bounding box"""
    x1, y1 = pt1
    x2, y2 = pt2
    center = ((x1 + x2) // 2, (y1 + y2) // 2)
    axes = (abs(x2 - x1) // 2, abs(y2 - y1) // 2)
    if thickness == -1:
        cv2.ellipse(img, center, axes, 0, 0, 360, color, -1)
    else:
        cv2.ellipse(img, center, axes, 0, 0, 360, color, thickness)

def simple_line_break(text, max_chars=6, bbox_w=None, bbox_h=None):
    """Aspect ratio 기반 적응형 줄바꿈"""
    if not text:
        return []

    # Aspect ratio에 따라 max_chars 조정
    if bbox_w and bbox_h and bbox_h > 0:
        aspect_ratio = bbox_h / bbox_w  # 세로 / 가로

        # 세로로 긴 경우 (aspect_ratio > 2.0) 더 짧게 줄바꿈
        if aspect_ratio > 2.5:
            max_chars = 3  # 매우 세로로 긴 경우
        elif aspect_ratio > 2.0:
            max_chars = 4  # 세로로 긴 경우
        elif aspect_ratio > 1.5:
            max_chars = 5  # 약간 세로로 긴 경우
        # 가로로 긴 경우 (aspect_ratio < 0.7) 더 길게
        elif aspect_ratio < 0.5:
            max_chars = 8  # 매우 가로로 긴 경우
        elif aspect_ratio < 0.7:
            max_chars = 7  # 가로로 긴 경우
        # 기본값: 6자

    # 단순하고 안전한 줄바꿈: textwrap만 사용
    try:
        lines = textwrap.wrap(text, width=max_chars, break_long_words=False, break_on_hyphens=False)
        return lines if lines else [text]
    except Exception as e:
        print(f"⚠️ simple_line_break 오류: {e}")
        # 폴백: 그냥 max_chars 단위로 자르기
        return [text[i:i+max_chars] for i in range(0, len(text), max_chars)]

def visualize_bboxes(image_path: str, bubbles: list, output_path: str) -> bool:
    """
    디버그용: bbox를 시각화한 이미지 생성
    
    Args:
        image_path: 원본 이미지 경로
        bubbles: bbox 정보 리스트
        output_path: 결과 이미지 저장 경로
        
    Returns:
        bool: 성공 여부
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            print(f"❌ 이미지 로드 실패: {image_path}")
            return False
        
        height, width = img.shape[:2]
        
        # 좌표 정규화 여부 확인
        is_normalized = True
        for item in bubbles:
            box = item["box_2d"]
            if any(c > 1000 for c in box):
                is_normalized = False
                break
        
        x_multiplier = width / 1000.0 if is_normalized else 1.0
        y_multiplier = height / 1000.0 if is_normalized else 1.0
        
        # bbox 그리기
        for idx, item in enumerate(bubbles):
            box = item["box_2d"]
            label = item.get("label", "")
            # bubble_type: in_bubble / on_background
            # 호환성을 위해 기존 필드도 체크
            b_type = item.get("bubble_type", item.get("type", "in_bubble"))
            
            is_background = "background" in b_type or "narration" in b_type
            
            ymin, xmin, ymax, xmax = box
            
            # 스케일링 적용
            xmin = int(xmin * x_multiplier)
            ymin = int(ymin * y_multiplier)
            xmax = int(xmax * x_multiplier)
            ymax = int(ymax * y_multiplier)
            
            # 색상 구분 (말풍선: 빨강, 배경글: 파랑)
            color = (255, 0, 0) if is_background else (0, 0, 255)
            
            # 사각형
            cv2.rectangle(img, (xmin, ymin), (xmax, ymax), color, 2)
            
            # 번호 및 타입 표시
            type_label = "BG" if is_background else "BB"  # Background / Bubble
            info_text = f"#{idx+1}[{type_label}]"
            cv2.putText(img, info_text, (xmin, ymin - 5), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        cv2.imwrite(output_path, img)
        print(f"✅ Bbox 시각화 완료: {output_path}")
        return True
        
    except Exception as e:
        print(f"❌ Bbox 시각화 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def white_mask_image(image_path: str, bubbles: list, output_path: str, corner_radius: int = 10, 
                    save_json: bool = True, mask_type: str = 'rectangle') -> bool:
    """
    이미지에 white masking 적용 (배경글은 반투명)
    
    Args:
        image_path: 원본 이미지 경로
        bubbles: bbox 정보 리스트 [{"box_2d": [ymin, xmin, ymax, xmax], ...}, ...]
        output_path: 결과 이미지 저장 경로
        corner_radius: 모서리 둥글기
        save_json: JSON 파일도 함께 저장할지 여부
        mask_type: 'rectangle' 또는 'ellipse'
        
    Returns:
        bool: 성공 여부
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            print(f"❌ 이미지 로드 실패: {image_path}")
            return False
        
        height, width = img.shape[:2]
        
        # 좌표 정규화 여부 확인 (모든 좌표가 0~1000 사이인지 체크)
        is_normalized = True
        for item in bubbles:
            box = item["box_2d"]
            if any(c > 1000 for c in box):
                is_normalized = False
                break
        
        x_multiplier = width / 1000.0 if is_normalized else 1.0
        y_multiplier = height / 1000.0 if is_normalized else 1.0
        
        if is_normalized:
            print(f"ℹ️ 정규화된 좌표 감지 (0~1000). 스케일링 적용: x*{x_multiplier:.2f}, y*{y_multiplier:.2f}")
        else:
            print(f"ℹ️ 실제 픽셀 좌표 감지. 스케일링 미적용")
        
        # 다시 구현:
        # img: 원본 + 대사 마스킹(100%)
        # 나레이션은 루프 돌면서 개별적으로 처리
        
        # img = cv2.imread(image_path) # 이미 로드됨
        
        for item in bubbles:
            box = item["box_2d"]
            # bubble_type 확인 (호환성 유지)
            b_type = item.get("bubble_type", item.get("type", "in_bubble"))
            is_background = "background" in b_type or "narration" in b_type
            
            ymin, xmin, ymax, xmax = box
            xmin = int(xmin * x_multiplier)
            ymin = int(ymin * y_multiplier)
            xmax = int(xmax * x_multiplier)
            ymax = int(ymax * y_multiplier)
            
            if is_background:
                # 배경글: ROI 추출 -> 흰색 사각형과 블렌딩 -> 다시 붙여넣기
                # 배경 이미지가 지워지는게 거슬려서 -> 둥글기보다는 투명도가 중요.
                
                # ROI가 유효한지 확인
                if xmin >= xmax or ymin >= ymax or xmin < 0 or ymin < 0 or xmax > width or ymax > height:
                    print(f"⚠️ 유효하지 않은 배경글 bbox: {xmin, ymin, xmax, ymax}. 건너뜁니다.")
                    continue

                roi = img[ymin:ymax, xmin:xmax].copy() # .copy() 중요, 원본 img에 직접 변경 방지
                
                # 흰색 마스크 생성
                white_mask_roi = np.zeros_like(roi, dtype=np.uint8)
                if mask_type == 'ellipse':
                    draw_ellipse(white_mask_roi, (0, 0), (xmax-xmin, ymax-ymin), (255, 255, 255), -1)
                else:
                    draw_rounded_rectangle(white_mask_roi, (0, 0), (xmax-xmin, ymax-ymin), (255, 255, 255), -1, corner_radius)
                
                # 흰색 마스크와 ROI 블렌딩 (50% 투명도)
                # cv2.addWeighted는 알파 채널을 직접 다루지 않으므로, 마스크를 이용해 흰색 영역만 블렌딩
                # 마스크가 255인 부분만 블렌딩하고, 0인 부분은 원본 ROI 유지
                
                # 흰색 마스크 80% + 원본 배경 20% = 배경이 흐리게 보임
                blended_roi = cv2.addWeighted(roi, 0.2, white_mask_roi, 0.8, 0)
                
                # 마스크 영역만 blended_roi로 교체
                # white_mask_roi의 흰색 영역 (255)을 마스크로 사용
                mask_indices = np.all(white_mask_roi == [255, 255, 255], axis=-1)
                roi[mask_indices] = blended_roi[mask_indices]
                
                img[ymin:ymax, xmin:xmax] = roi
                
            else:
                # 말풍선: 100% 흰색
                if mask_type == 'ellipse':
                    draw_ellipse(img, (xmin, ymin), (xmax, ymax), (255, 255, 255), -1)
                else:
                    draw_rounded_rectangle(img, (xmin, ymin), (xmax, ymax),
                                         (255, 255, 255), -1, corner_radius)

        cv2.imwrite(output_path, img)
        print(f"✅ White masking 완료 ({mask_type}): {output_path}")
        
        # JSON 파일도 저장 (디버깅용)
        if save_json:
            json_path = Path(output_path).with_suffix('.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(bubbles, f, ensure_ascii=False, indent=2)
            print(f"   JSON 저장: {json_path}")
        
        return True
        
    except Exception as e:
        print(f"❌ White masking 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_font_path(font_style="normal"):
    """
    폰트 스타일에 맞는 한글 폰트 경로 찾기

    Args:
        font_style: "normal", "bold", "stylized" 중 하나

    Returns:
        str: 폰트 파일 경로
    """
    fonts_dir = Path(__file__).parent / "fonts"

    # font_style별 우선순위 폰트 목록
    if font_style == "stylized":
        # Stylized: 효과음/손글씨 느낌 (ExtraBold)
        font_candidates = [
            fonts_dir / "NanumSquareNeo-dEb.ttf",  # ExtraBold
            fonts_dir / "NanumSquareNeo-cBd.ttf",  # Bold (fallback)
            fonts_dir / "NanumSquareNeo-bRg.ttf",  # Regular (fallback)
        ]
    elif font_style == "bold":
        # Bold: 강조 (Bold)
        font_candidates = [
            fonts_dir / "NanumSquareNeo-cBd.ttf",  # Bold
            fonts_dir / "NanumSquareNeo-dEb.ttf",  # ExtraBold (fallback)
            fonts_dir / "NanumSquareNeo-bRg.ttf",  # Regular (fallback)
        ]
    else:  # normal
        # Normal: 일반 대사 (Regular)
        font_candidates = [
            fonts_dir / "NanumSquareNeo-bRg.ttf",  # Regular
            fonts_dir / "NanumSquareNeo-cBd.ttf",  # Bold (fallback)
            fonts_dir / "NanumSquareNeo-dEb.ttf",  # ExtraBold (fallback)
        ]

    # 폰트 파일 찾기
    for font_path in font_candidates:
        if font_path.exists():
            return str(font_path)

    # 시스템 폰트 fallback (macOS)
    if platform.system() == "Darwin":
        system_fonts = [
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "/Library/Fonts/NanumSquare.ttc",
        ]
        for font_path in system_fonts:
            if Path(font_path).exists():
                return font_path

    return None

def calculate_font_size(text, max_width, max_height, font_path, min_size=25, max_size=80):
    """텍스트가 영역에 맞는 최적 폰트 크기 계산"""
    if not font_path:
        return 35

    # Aspect ratio 기반 적응형 줄바꿈
    lines = simple_line_break(text, max_chars=6, bbox_w=max_width, bbox_h=max_height)
    
    available_width = max_width * 0.9
    available_height = max_height * 0.9
    
    while max_size - min_size > 2:
        test_size = (min_size + max_size) // 2
        
        try:
            font = ImageFont.truetype(font_path, test_size)
            total_height = 0
            max_line_width = 0
            
            for line in lines:
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                line_height = bbox[3] - bbox[1]
                max_line_width = max(max_line_width, line_width)
                total_height += line_height
            
            line_spacing = test_size * 0.15
            total_height += (len(lines) - 1) * line_spacing
            
            if max_line_width <= available_width and total_height <= available_height:
                min_size = test_size
            else:
                max_size = test_size - 1
        except Exception:
            max_size = test_size - 1
    
    return min_size

def overlay_text_on_image(image_path: str, bubbles: list, output_path: str) -> bool:
    """
    이미지에 번역 텍스트 오버레이 (개선된 폰트 크기 및 줄바꿈)
    
    Args:
        image_path: White masked 이미지 경로
        bubbles: 번역된 bbox 정보 리스트
        output_path: 결과 이미지 저장 경로
        
    Returns:
        bool: 성공 여부
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            print(f"❌ 이미지 로드 실패: {image_path}")
            return False
        
        height, width = img.shape[:2]
        
        # 좌표 정규화 여부 확인
        is_normalized = True
        for item in bubbles:
            box = item["box_2d"]
            if any(c > 1000 for c in box):
                is_normalized = False
                break
        
        x_multiplier = width / 1000.0 if is_normalized else 1.0
        y_multiplier = height / 1000.0 if is_normalized else 1.0

        # font_style별 폰트 경로 미리 로드
        font_paths = {
            "normal": get_font_path("normal"),
            "bold": get_font_path("bold"),
            "stylized": get_font_path("stylized")
        }

        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_image)
        
        # 1. 모든 텍스트의 최적 폰트 크기 미리 계산
        calculated_sizes = []
        scaled_boxes = []

        for item in bubbles:
            box = item["box_2d"]
            text = item["label"]
            font_style = item.get("font_style", "normal")

            # 스케일링 적용
            ymin, xmin, ymax, xmax = box
            xmin = int(xmin * x_multiplier)
            ymin = int(ymin * y_multiplier)
            xmax = int(xmax * x_multiplier)
            ymax = int(ymax * y_multiplier)

            scaled_boxes.append((xmin, ymin, xmax, ymax))

            bbox_w = xmax - xmin
            bbox_h = ymax - ymin

            # 해당 font_style의 폰트로 크기 계산
            font_path = font_paths.get(font_style, font_paths["normal"])
            optimal_size = calculate_font_size(text, bbox_w, bbox_h, font_path)
            calculated_sizes.append(optimal_size)
            
        # 2. 평균 폰트 크기 계산 및 제한 설정 (1.05 ~ 1.15배)
        if calculated_sizes:
            avg_size = sum(calculated_sizes) / len(calculated_sizes)
            min_allowed = avg_size * 0.8 # 너무 작은 폰트 방지
            max_allowed = avg_size * 1.15 # 최대 15%까지만 커지도록 제한
            
            print(f"ℹ️ 폰트 크기 조정: 평균 {avg_size:.1f}px, 범위 {min_allowed:.1f}~{max_allowed:.1f}px")
        else:
            min_allowed = 10
            max_allowed = 100
            
        # 3. 텍스트 그리기
        for i, item in enumerate(bubbles):
            text = item["label"]
            xmin, ymin, xmax, ymax = scaled_boxes[i]
            bbox_w = xmax - xmin
            bbox_h = ymax - ymin

            # font_style 추출 (기본값: normal)
            font_style = item.get("font_style", "normal")

            # 폰트 크기 제한 적용 (약간 키워서 적용: 1.05배)
            base_size = calculated_sizes[i]
            adjusted_size = base_size * 1.05

            # 범위 클램핑
            final_size = max(min_allowed, min(adjusted_size, max_allowed))
            final_size = int(final_size)

            # 해당 font_style의 폰트 로드
            font_path = font_paths.get(font_style, font_paths["normal"])
            if font_path:
                font = ImageFont.truetype(font_path, final_size)
            else:
                font = ImageFont.load_default()

            # font_style에 따른 렌더링 옵션 설정
            if font_style == "bold":
                # Bold: 두꺼운 검은 테두리로 굵게 표현
                stroke_width = max(2, int(final_size * 0.08))  # 폰트 크기의 8%
                stroke_fill = (0, 0, 0)
                text_fill = (0, 0, 0)
            elif font_style == "stylized":
                # Stylized: 약간 두꺼운 테두리 (효과음/손글씨 느낌)
                stroke_width = max(1, int(final_size * 0.05))  # 폰트 크기의 5%
                stroke_fill = (0, 0, 0)
                text_fill = (0, 0, 0)
            else:  # normal
                # Normal: 기본 (테두리 없음)
                stroke_width = 0
                stroke_fill = None
                text_fill = (0, 0, 0)

            # Aspect ratio 기반 적응형 줄바꿈
            lines = simple_line_break(text, max_chars=6, bbox_w=bbox_w, bbox_h=bbox_h)

            line_heights = []
            max_line_width = 0

            for line in lines:
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                line_height = bbox[3] - bbox[1]
                line_heights.append(line_height)
                max_line_width = max(max_line_width, line_width)

            line_spacing = final_size * 0.15
            total_height = sum(line_heights) + (len(lines) - 1) * line_spacing

            center_x = xmin + bbox_w // 2
            center_y = ymin + bbox_h // 2
            text_y = center_y - total_height // 2

            current_y = text_y
            for j, line in enumerate(lines):
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                text_x = center_x - line_width // 2

                # font_style에 따라 stroke 적용
                draw.text(
                    (text_x, current_y),
                    line,
                    font=font,
                    fill=text_fill,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill
                )
                current_y += line_heights[j] + line_spacing
        
        final_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_BGR2RGB)
        cv2.imwrite(output_path, final_image)
        print(f"✅ 텍스트 오버레이 완료: {output_path}")
        return True
        
    except Exception as e:
        print(f"❌ 텍스트 오버레이 실패: {e}")
        return False

def process_comic_image(image_path: str, bubbles_ocr: list, bubbles_translated: list, 
                       output_dir: str, mask_type: str = 'rectangle') -> dict:
    """
    만화 이미지 전체 처리 파이프라인
    
    Args:
        image_path: 원본 이미지 경로
        bubbles_ocr: OCR 결과
        bubbles_translated: 번역 결과
        output_dir: 출력 디렉토리
        mask_type: 'rectangle' 또는 'ellipse'
        
    Returns:
        dict: {"bbox_viz": 경로, "ocr_json": 경로, "translated_json": 경로, "masked": 경로, "final": 경로} 또는 None
    """
    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        image_name = Path(image_path).stem
        
        # 각 단계별 출력 파일 경로
        bbox_viz_path = output_dir / f"{image_name}_bbox.jpg"
        ocr_json_path = output_dir / f"{image_name}_ocr.json"
        translated_json_path = output_dir / f"{image_name}_translated.json"
        masked_path = output_dir / f"{image_name}_masked.jpg"
        final_path = output_dir / f"{image_name}_final.jpg"
        
        # Step 0: Bbox 시각화 (디버그용)
        visualize_bboxes(image_path, bubbles_ocr, str(bbox_viz_path))
        
        # Step 1: OCR JSON 저장
        with open(ocr_json_path, 'w', encoding='utf-8') as f:
            json.dump(bubbles_ocr, f, ensure_ascii=False, indent=2)
        print(f"✅ OCR JSON 저장: {ocr_json_path}")
        
        # Step 2: 번역 JSON 저장
        with open(translated_json_path, 'w', encoding='utf-8') as f:
            json.dump(bubbles_translated, f, ensure_ascii=False, indent=2)
        print(f"✅ 번역 JSON 저장: {translated_json_path}")
        
        # Step 3: White masking
        if not white_mask_image(image_path, bubbles_translated, str(masked_path), 
                              save_json=False, mask_type=mask_type):
            print(f"⚠️ White masking 실패했지만 계속 진행")
        
        # Step 4: Text overlay
        if not overlay_text_on_image(str(masked_path), bubbles_translated, str(final_path)):
            print(f"⚠️ Text overlay 실패")
            return {
                "bbox_viz": str(bbox_viz_path),
                "ocr_json": str(ocr_json_path),
                "translated_json": str(translated_json_path),
                "masked": str(masked_path) if masked_path.exists() else None,
                "final": None
            }
        
        return {
            "bbox_viz": str(bbox_viz_path),
            "ocr_json": str(ocr_json_path),
            "translated_json": str(translated_json_path),
            "masked": str(masked_path),
            "final": str(final_path)
        }
        
    except Exception as e:
        print(f"❌ 이미지 처리 실패: {e}")
        import traceback
        traceback.print_exc()
        return None
