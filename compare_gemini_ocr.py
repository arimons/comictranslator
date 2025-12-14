#!/usr/bin/env python3
"""
Gemini 2.5 Flash vs Gemini 3 Pro OCR 비교
- 두 모델을 동시에 테스트
- bbox 시각화로 결과 비교
"""
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import google.generativeai as genai
from dotenv import load_dotenv
import os
import time
import cv2
import numpy as np

# 환경 설정
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY 환경 변수 필요")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)


def create_ocr_prompt() -> str:
    """OCR 프롬프트 생성 (모델 공통)"""
    prompt = """이 만화 이미지의 모든 텍스트를 찾아주세요.

각 텍스트마다 다음을 포함하는 JSON을 반환해주세요:
- text: 인식한 텍스트 정확히
- bbox: [left, top, width, height] 형식 (픽셀 단위)
- confidence: 확신도 (0.0-1.0)
- orientation: 텍스트 방향 (horizontal/vertical)

JSON 배열 형식:
[
  {
    "text": "텍스트1",
    "bbox": [x1, y1, w1, h1],
    "confidence": 0.95,
    "orientation": "vertical"
  },
  ...
]

중요:
- 모든 텍스트를 찾을 것
- bbox는 정확하게 (픽셀 단위)
- 장식이나 패턴이 아닌 실제 텍스트만
- JSON만 반환"""

    return prompt


def ocr_with_model(image_path: str, model_name: str) -> Tuple[Optional[List[Dict]], float]:
    """
    지정된 모델로 OCR 수행

    Returns:
        (results, elapsed_time)
    """

    # 이미지 로드
    with open(image_path, 'rb') as f:
        image_data = f.read()

    prompt = create_ocr_prompt()

    print(f"  🤖 {model_name} 호출 중...")
    start_time = time.time()

    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content([
            prompt,
            {
                'mime_type': 'image/jpeg',
                'data': image_data
            }
        ])

        elapsed_time = time.time() - start_time

        # JSON 파싱
        response_text = response.text

        if '```json' in response_text:
            json_start = response_text.find('```json') + 7
            json_end = response_text.find('```', json_start)
            json_str = response_text[json_start:json_end].strip()
        elif '```' in response_text:
            json_start = response_text.find('```') + 3
            json_end = response_text.find('```', json_start)
            json_str = response_text[json_start:json_end].strip()
        else:
            json_str = response_text

        results = json.loads(json_str)

        if not isinstance(results, list):
            results = [results] if isinstance(results, dict) else []

        return results, elapsed_time

    except Exception as e:
        print(f"    ❌ 실패: {e}")
        return None, 0


def visualize_ocr_results(image_path: str, results: List[Dict], output_path: str, title: str = "", scale_x: float = 1.0, scale_y: float = 1.0) -> None:
    """
    OCR 결과를 bbox로 시각화

    Args:
        image_path: 이미지 경로
        results: OCR 결과 (bbox, text, confidence 포함)
        output_path: 출력 이미지 경로
        title: 제목
        scale_x: X축 스케일 (기본값 1.0, Gemini는 1000x1000 기준이므로 1.35)
        scale_y: Y축 스케일 (기본값 1.0, Gemini는 1000x1000 기준이므로 1.92)
    """

    # 이미지 로드
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 이미지 로드 실패: {image_path}")
        return

    img_vis = img.copy()
    h, w = img_vis.shape[:2]

    print(f"  📐 스케일 조정: X={scale_x:.2f}, Y={scale_y:.2f}")

    # 색상 팔레트 (각 텍스트마다 다른 색)
    colors = [
        (255, 0, 0),      # 빨강
        (0, 255, 0),      # 초록
        (0, 0, 255),      # 파랑
        (255, 255, 0),    # 시안
        (255, 0, 255),    # 마젠타
        (0, 255, 255),    # 노랑
        (128, 0, 0),      # 어두운 빨강
        (0, 128, 0),      # 어두운 초록
        (0, 0, 128),      # 어두운 파랑
        (128, 128, 0),    # 올리브
        (128, 0, 128),    # 보라
        (0, 128, 128),    # 틸
    ]

    # 각 텍스트의 bbox 그리기
    for idx, item in enumerate(results):
        bbox = item.get('bbox', [0, 0, 0, 0])
        text = item.get('text', '')
        confidence = item.get('confidence', 0)

        # 스케일 조정 (Gemini는 1000x1000 기준, 실제 이미지는 1350x1920)
        x = int(bbox[0] * scale_x)
        y = int(bbox[1] * scale_y)
        w_box = int(bbox[2] * scale_x)
        h_box = int(bbox[3] * scale_y)

        # bbox 범위 확인
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        w_box = min(w_box, w - x)
        h_box = min(h_box, h - y)

        color = colors[idx % len(colors)]

        # bbox 그리기 (굵은 직사각형)
        cv2.rectangle(img_vis, (x, y), (x + w_box, y + h_box), color, 2)

        # 텍스트 및 ID 표시
        label = f"[{idx+1}] {text[:15]}"

        # 텍스트 배경
        text_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
        bg_x1 = x
        bg_y1 = max(0, y - 25)
        bg_x2 = x + text_size[0] + 5
        bg_y2 = bg_y1 + text_size[1] + 5

        cv2.rectangle(img_vis, (bg_x1, bg_y1), (bg_x2, bg_y2), color, -1)
        cv2.putText(img_vis, label, (x + 3, y - 5),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # 신뢰도 표시
        conf_text = f"{confidence:.0%}"
        cv2.putText(img_vis, conf_text, (x + 3, y + h_box - 5),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    # 제목 추가
    if title:
        cv2.putText(img_vis, title, (20, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)

    # 저장
    cv2.imwrite(output_path, img_vis)
    print(f"  💾 저장: {output_path}")


def print_results_table(results: List[Dict], model_name: str, elapsed_time: float) -> None:
    """결과를 표로 출력"""

    print(f"\n{'='*100}")
    print(f"📋 {model_name} 결과 ({elapsed_time:.1f}초)")
    print(f"{'='*100}")

    if not results:
        print("❌ 감지된 텍스트 없음")
        return

    print(f"{'ID':<4} {'텍스트':<25} {'Bbox':<45} {'확신도':<10} {'방향':<12}")
    print(f"{'-'*100}")

    for i, item in enumerate(results):
        text = item.get('text', '')[:25]
        bbox = item.get('bbox', [0, 0, 0, 0])
        confidence = item.get('confidence', 0)
        orientation = item.get('orientation', 'unknown')

        bbox_str = f"[{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}]"
        print(f"{i+1:<4} {text:<25} {bbox_str:<45} {confidence:<10.2%} {orientation:<12}")

    # 통계
    total_chars = sum(len(item.get('text', '')) for item in results)
    avg_confidence = sum(item.get('confidence', 0) for item in results) / len(results) if results else 0

    print(f"\n📊 통계:")
    print(f"  - 감지 항목: {len(results)}개")
    print(f"  - 총 글자: {total_chars}개")
    print(f"  - 평균 확신도: {avg_confidence:.2%}")


def compare_results(results_25: List[Dict], results_3pro: List[Dict]) -> None:
    """두 모델의 결과 비교"""

    print(f"\n{'='*100}")
    print(f"📊 Gemini 2.5 Flash vs Gemini 3 Pro 비교")
    print(f"{'='*100}")

    flash_count = len(results_25) if results_25 else 0
    pro_count = len(results_3pro) if results_3pro else 0

    flash_text = ' '.join([item.get('text', '') for item in (results_25 or [])])
    pro_text = ' '.join([item.get('text', '') for item in (results_3pro or [])])

    print(f"\nGemini 2.5 Flash:")
    print(f"  - 감지 항목: {flash_count}개")
    print(f"  - 총 글자: {len(flash_text)}개")
    print(f"  - 평균 bbox 크기: {np.mean([item.get('bbox', [0,0,100,100])[2:] for item in (results_25 or [])], axis=0) if results_25 else [0, 0]}")

    print(f"\nGemini 3 Pro:")
    print(f"  - 감지 항목: {pro_count}개")
    print(f"  - 총 글자: {len(pro_text)}개")
    print(f"  - 평균 bbox 크기: {np.mean([item.get('bbox', [0,0,100,100])[2:] for item in (results_3pro or [])], axis=0) if results_3pro else [0, 0]}")

    print(f"\n차이:")
    print(f"  - 감지 항목 차이: {pro_count - flash_count:+d} ({(pro_count - flash_count) / max(flash_count, 1) * 100:+.1f}%)")
    print(f"  - 글자 수 차이: {len(pro_text) - len(flash_text):+d}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python compare_gemini_ocr.py <image_path>")
        print("\nExample:")
        print("  python compare_gemini_ocr.py temp_output/Debug/00010_original.jpg")
        sys.exit(1)

    image_path = sys.argv[1]
    img_path = Path(image_path)

    if not img_path.exists():
        print(f"❌ 이미지를 찾을 수 없습니다: {image_path}")
        sys.exit(1)

    output_dir = img_path.parent

    print(f"\n{'='*100}")
    print(f"🚀 Gemini 2.5 Flash vs Gemini 3 Pro OCR 비교")
    print(f"{'='*100}")
    print(f"이미지: {image_path}\n")

    # Gemini 2.5 Flash
    print(f"1️⃣ Gemini 2.5 Flash 테스트")
    results_25, time_25 = ocr_with_model(image_path, 'gemini-2.5-flash')

    if results_25 is None:
        print("❌ Gemini 2.5 Flash 실패")
        sys.exit(1)

    print_results_table(results_25, "Gemini 2.5 Flash", time_25)

    # 시각화 - 2.5 Flash (스케일 조정: 1000x1000 → 1350x1920)
    print(f"\n  🎨 Gemini 2.5 Flash 시각화 중...")
    vis_25_path = output_dir / f"{img_path.stem}_gemini25_bbox.jpg"
    visualize_ocr_results(image_path, results_25, str(vis_25_path), "Gemini 2.5 Flash", scale_x=1.35, scale_y=1.92)

    # Gemini 3 Pro
    print(f"\n2️⃣ Gemini 3 Pro 테스트")
    results_3pro, time_3pro = ocr_with_model(image_path, 'gemini-3-pro-image-preview')

    if results_3pro is None:
        print("❌ Gemini 3 Pro 실패")
        sys.exit(1)

    print_results_table(results_3pro, "Gemini 3 Pro", time_3pro)

    # 시각화 - 3 Pro (스케일 조정: 1000x1000 → 1350x1920)
    print(f"\n  🎨 Gemini 3 Pro 시각화 중...")
    vis_3pro_path = output_dir / f"{img_path.stem}_gemini3pro_bbox.jpg"
    visualize_ocr_results(image_path, results_3pro, str(vis_3pro_path), "Gemini 3 Pro", scale_x=1.35, scale_y=1.92)

    # 비교
    compare_results(results_25, results_3pro)

    # 결과 JSON 저장
    results_25_path = output_dir / f"{img_path.stem}_gemini25_ocr.json"
    with open(results_25_path, 'w', encoding='utf-8') as f:
        json.dump(results_25, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Gemini 2.5 Flash 결과: {results_25_path}")

    results_3pro_path = output_dir / f"{img_path.stem}_gemini3pro_ocr.json"
    with open(results_3pro_path, 'w', encoding='utf-8') as f:
        json.dump(results_3pro, f, ensure_ascii=False, indent=2)
    print(f"💾 Gemini 3 Pro 결과: {results_3pro_path}")

    print(f"\n{'='*100}\n")


if __name__ == "__main__":
    main()
