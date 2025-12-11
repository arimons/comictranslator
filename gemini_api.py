#!/usr/bin/env python3
"""
Gemini API 통합 모듈
- gemini-2.5-flash: bbox + OCR 추출 (Hybrid with Cloud Vision)
- gemini-2.5-flash: 번역
"""

import google.generativeai as genai
import json
from typing import List, Dict, Optional
import time
from pathlib import Path
from cloud_vision_api import detect_text_cv
from bbox_utils import merge_bboxes_regional_cluster
import cv2

class GeminiAPI:
    def __init__(self, api_key: str):
        """
        Gemini API 초기화
        
        Args:
            api_key: Google AI Studio API 키
        """
        genai.configure(api_key=api_key)
        # OCR/BBox 추출용 - Gemini 2.5 Flash
        try:
            self.ocr_model = genai.GenerativeModel('gemini-2.5-flash')
            print("✅ OCR 모델: gemini-2.5-flash")
        except:
            print("⚠️ gemini-2.5-flash를 찾을 수 없어 gemini-1.5-flash로 대체합니다.")
            self.ocr_model = genai.GenerativeModel('gemini-1.5-flash')
        # 번역용 (Gemini 2.5 Flash)
        try:
            self.translation_model = genai.GenerativeModel('gemini-2.5-flash')
            print("✅ 번역 모델: gemini-2.5-flash")
        except:
            print("⚠️ gemini-2.5-flash를 찾을 수 없어 gemini-1.5-flash로 대체합니다.")
            self.translation_model = genai.GenerativeModel('gemini-1.5-flash')
        
    def extract_text_bubbles(self, image_path: str, retry_count: int = 3, force_gemini: bool = False) -> Optional[List[Dict]]:
        """
        Hybrid OCR: Cloud Vision (정확한 bbox) + Gemini (완전한 OCR + 메타데이터)

        Args:
            image_path: 이미지 파일 경로
            retry_count: 재시도 횟수
            force_gemini: True면 기존 G3 JSON 무시하고 재실행 (기본값: False)

        Returns:
            List[Dict]: [{"box_2d": [ymin, xmin, ymax, xmax], "label": "텍스트",
                         "bubble_type": "in_bubble/on_background", "font_style": "normal/bold/stylized"}, ...]

        캐싱:
            - CV JSON 존재 시: 자동 재사용 (API skip)
            - G3 JSON 존재 시: force_gemini=False면 재사용, True면 재실행
            - OCR(병합): 항상 수행
        """
        # 이미지 크기 확인
        img = cv2.imread(image_path)
        if img is None:
            print(f"❌ 이미지 로드 실패: {image_path}")
            return None
        height, width = img.shape[:2]

        # Step 1: Cloud Vision API로 정확한 bbox 추출
        from pathlib import Path
        output_dir = Path("temp_output")
        output_dir.mkdir(exist_ok=True)
        cv_json_path = output_dir / f"{Path(image_path).stem}_cv.json"

        # CV JSON이 이미 존재하면 로드 (API 호출 skip)
        if cv_json_path.exists():
            print(f"📂 기존 CV 결과 로드 중... ({cv_json_path.name})")
            with open(cv_json_path, 'r', encoding='utf-8') as f:
                cv_boxes = json.load(f)
            print(f"✅ Cloud Vision 캐시 사용: {len(cv_boxes)}개 bbox (API 호출 skip)")
        else:
            print("📸 Cloud Vision API 호출 중...")
            cv_boxes = detect_text_cv(image_path)
            if not cv_boxes:
                print("⚠️ Cloud Vision 결과 없음, Gemini 단독 사용")
                cv_boxes = []
            else:
                print(f"✅ Cloud Vision: {len(cv_boxes)}개 bbox 추출")
                # CV 결과를 JSON 파일로 저장
                with open(cv_json_path, 'w', encoding='utf-8') as f:
                    json.dump(cv_boxes, f, ensure_ascii=False, indent=2)
                print(f"  💾 저장: {cv_json_path}")
        
        prompt = f"""이 만화 이미지에서 번역이 필요한 모든 텍스트를 찾아주세요.

이미지 크기: {width}x{height} 픽셀

**작업 순서:**
1. 이미지의 **모든 일본어 텍스트**를 찾으세요 (크기에 상관없이 모든 텍스트 포함)
2. 각 텍스트의 **글자체 스타일**을 파악하세요 (일반/굵은체/손글씨/효과음체)
3. 각 텍스트에 대해 **모든 글자를 포함**하는 정확한 bbox를 추출하세요
4. 텍스트가 **말풍선 안에 있는지, 배경에 직접 쓰인 것인지** 판단하세요

**반환 형식 (JSON 배열):**
각 텍스트에 대해 다음 정보를 포함:
- box_2d: [ymin, xmin, ymax, xmax] - **실제 픽셀 좌표** (0~{width}, 0~{height} 범위)
- label: 일본어 텍스트 (OCR 결과)
- font_style: 글자체 스타일
  * "normal": 일반 명조체/고딕체
  * "bold": 굵은 글씨 (강조)
  * "stylized": 손글씨/효과음체/특수 폰트
- bubble_type: 텍스트 위치
  * "in_bubble": 말풍선/테두리 안에 있는 대사
  * "on_background": 배경에 직접 쓰인 효과음/나레이션

**중요 규칙:**
1. **완전한 텍스트 감지**:
   - 크기가 작아도, 흐려도, 배경과 섞여 있어도 **모든 텍스트를 찾으세요**
   - 코너, 가장자리, 패널 경계의 텍스트도 놓치지 마세요
   - 의심스러우면 포함하세요 (누락보다 과다 포함이 낫습니다)

2. **BBox 정확도** (모든 글자 포함 필수):
   - bbox는 **모든 글자를 완전히 포함**해야 합니다 (일부 글자 잘림 금지)
   - 후리가나가 **실제로 존재하면** 반드시 bbox에 포함하세요
   - Tight하게 잡되, 글자가 잘리지 않게 약간 여유를 두세요

3. **세로쓰기 처리**:
   - 세로쓰기가 여러 열로 나뉘어도 **하나의 연속된 문장이면 하나의 bbox로** 잡으세요
   - 예: "あいう" "えお" 두 열이 하나의 문장 → 전체를 하나의 bbox로

4. **Bubble Type 판단 기준**:
   - **말풍선/테두리가 보이면** → "in_bubble" (글자체 무관)
   - **말풍선 없이 배경에 직접 쓰임** → "on_background"
   - 판단 애매하면: stylized/bold 폰트 → "on_background" 가능성 높음

5. **제외 대상**:
   - 순수 문장기호만 있는 경우 **제외** (예: "!?", "!!", "...", "…")
   - 번역 불필요한 효과음 기호만 있으면 제외

6. **기타**:
   - 좌표는 정규화하지 말고 **실제 픽셀 값**으로 반환
   - 순수 JSON 배열만 반환 (설명/주석 없이)

**예시:**
[
  {{"box_2d": [100, 200, 300, 400], "label": "こんにちは", "font_style": "normal", "bubble_type": "in_bubble"}},
  {{"box_2d": [500, 600, 700, 800], "label": "ドーン", "font_style": "stylized", "bubble_type": "on_background"}},
  {{"box_2d": [50, 100, 80, 200], "label": "やった!", "font_style": "bold", "bubble_type": "in_bubble"}}
]"""

        # G3 JSON 경로 확인
        g3_json_path = output_dir / f"{Path(image_path).stem}_g3.json"

        # G3 JSON이 존재하고 force_gemini=False면 기존 결과 사용
        if not force_gemini and g3_json_path.exists():
            print(f"📂 기존 Gemini 결과 로드 중... ({g3_json_path.name})")
            with open(g3_json_path, 'r', encoding='utf-8') as f:
                gemini_boxes = json.load(f)
            print(f"✅ Gemini 캐시 사용: {len(gemini_boxes)}개 bbox (API 호출 skip)")
        else:
            # Gemini API 호출
            if force_gemini:
                print("🔄 Gemini 재실행 (force_gemini=True)")
            else:
                print("🤖 Gemini API 호출 중...")

            gemini_boxes = None  # 초기화
            for attempt in range(retry_count):
                try:
                    # 이미지 업로드
                    image_file = genai.upload_file(image_path)

                    # API 호출
                    response = self.ocr_model.generate_content([prompt, image_file])

                    # 응답 파싱
                    response_text = response.text.strip()

                    # JSON 추출 (코드 블록 제거)
                    if response_text.startswith('```'):
                        # ```json ... ``` 형식 처리
                        lines = response_text.split('\n')
                        json_lines = []
                        in_code_block = False
                        for line in lines:
                            if line.startswith('```'):
                                in_code_block = not in_code_block
                                continue
                            if in_code_block or (not line.startswith('```')):
                                json_lines.append(line)
                        response_text = '\n'.join(json_lines)

                    # JSON 파싱
                    gemini_boxes = json.loads(response_text)

                    print(f"✅ Gemini OCR 추출 성공: {len(gemini_boxes)}개 말풍선")

                    # Step 2: Gemini bbox 정규화 (1000x1000 → 실제 이미지 크기)
                    x_multiplier = width / 1000.0
                    y_multiplier = height / 1000.0

                    for box in gemini_boxes:
                        bbox = box["box_2d"]
                        box["box_2d"] = [
                            int(bbox[0] * y_multiplier),  # ymin
                            int(bbox[1] * x_multiplier),  # xmin
                            int(bbox[2] * y_multiplier),  # ymax
                            int(bbox[3] * x_multiplier)   # xmax
                        ]

                    # Gemini 결과를 JSON 파일로 저장 (정규화 후, temp_output 폴더에)
                    with open(g3_json_path, 'w', encoding='utf-8') as f:
                        json.dump(gemini_boxes, f, ensure_ascii=False, indent=2)
                    print(f"  💾 저장: {g3_json_path}")

                    break  # 성공하면 루프 탈출

                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON 파싱 실패 (시도 {attempt + 1}/{retry_count}): {e}")
                    print(f"응답: {response_text[:200]}...")
                    if attempt < retry_count - 1:
                        time.sleep(1)
                    else:
                        print(f"❌ JSON 파싱 최종 실패")
                        return None

                except Exception as e:
                    print(f"⚠️ API 호출 실패 (시도 {attempt + 1}/{retry_count}): {e}")
                    if attempt < retry_count - 1:
                        time.sleep(2)
                    else:
                        print(f"❌ API 호출 최종 실패")
                        return None

            # API 호출이 모두 실패한 경우
            if gemini_boxes is None:
                print(f"❌ Gemini API 호출 최종 실패")
                return None

        # Step 3: Hybrid 병합 (CV bbox가 있는 경우만) - 캐시든 API든 항상 실행
        if cv_boxes:
            print("🔄 Hybrid 병합 중 (Cloud Vision + Gemini)...")
            merged_boxes = merge_bboxes_regional_cluster(
                cv_boxes,
                gemini_boxes,
                expand_margin=30,
                x_threshold=15,
                y_threshold=8,
                min_cv_count=3,
                constrain_margin=20
            )
            print(f"✅ Hybrid 병합 완료: {len(merged_boxes)}개 bbox")
            return merged_boxes
        else:
            # Cloud Vision 없으면 Gemini만 사용
            print("✅ Gemini bbox 사용 (Cloud Vision 없음)")
            return gemini_boxes

    def extract_text_bubbles_batch(self, image_paths: List[str], retry_count: int = 3, max_concurrent: int = None) -> Optional[List[List[Dict]]]:
        """
        배치 Hybrid OCR: CV 그리드 → 각 이미지별 Gemini (병렬)

        프로세스:
        1. CV 그리드 처리: 여러 이미지 → 그리드 병합 → CV 1번 호출 → 각 이미지별 cv_boxes
        2. Gemini 병렬 처리: 각 이미지 + CV bbox 정보를 프롬프트에 포함 → 그리드 크기만큼 병렬 호출
        3. Hybrid 병합: CV bbox + Gemini OCR → Regional clustering

        Args:
            image_paths: 이미지 파일 경로 리스트
            retry_count: 재시도 횟수
            max_concurrent: 최대 동시 Gemini 호출 수 (None이면 그리드 크기에 맞춤)
                           - 3x3 그리드 → 9개 동시
                           - 4x4 그리드 → 16개 동시

        Returns:
            List[List[Dict]]: 각 이미지별 Hybrid OCR 결과
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os
        import numpy as np
        from dotenv import load_dotenv
        load_dotenv()

        if not image_paths:
            return None

        # 그리드 크기 계산
        n_images = len(image_paths)
        grid_cols = int(np.ceil(np.sqrt(n_images)))
        grid_rows = int(np.ceil(n_images / grid_cols))
        grid_size = grid_rows * grid_cols

        # max_concurrent를 그리드 크기에 맞춤 (None이면 자동)
        if max_concurrent is None:
            max_concurrent = grid_size
            print(f"🔧 동시 처리 수 자동 설정: {grid_rows}x{grid_cols} 그리드 → {max_concurrent}개 동시 호출")
        else:
            print(f"🔧 동시 처리 수: {max_concurrent}개 (그리드: {grid_rows}x{grid_cols})")

        # Step 1: Cloud Vision 그리드 처리 (1번 호출)
        print(f"\n📸 Step 1: Cloud Vision 그리드 처리 ({n_images}개 이미지, {grid_rows}x{grid_cols} 그리드)")
        from cloud_vision_api import CloudVisionAPI

        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        cv_api = CloudVisionAPI(credentials_path=credentials_path)

        cv_results = cv_api.detect_text_grid(image_paths)
        if not cv_results:
            print("❌ Cloud Vision 그리드 처리 실패")
            return None

        # CV 결과를 각 이미지별 JSON 파일로 저장 (temp_output 폴더에)
        from pathlib import Path
        output_dir = Path("temp_output")
        output_dir.mkdir(exist_ok=True)
        for idx, (img_path, cv_boxes) in enumerate(zip(image_paths, cv_results)):
            cv_json_path = output_dir / f"{Path(img_path).stem}_cv.json"
            with open(cv_json_path, 'w', encoding='utf-8') as f:
                json.dump(cv_boxes, f, ensure_ascii=False, indent=2)
            print(f"  💾 저장: {cv_json_path.name} ({len(cv_boxes)}개 bbox)")

        # 이미지 크기 정보
        image_sizes = []
        for img_path in image_paths:
            img = cv2.imread(img_path)
            if img is None:
                return None
            h, w = img.shape[:2]
            image_sizes.append((w, h))

        # Step 2: Gemini 병렬 처리 (max_concurrent개씩)
        print(f"\n🤖 Step 2: Gemini OCR 병렬 처리 (최대 {max_concurrent}개 동시)")

        def process_single_gemini(idx: int, img_path: str, cv_boxes: List[Dict], w: int, h: int) -> tuple:
            """단일 이미지 Gemini OCR + Hybrid 병합"""
            try:
                # CV bbox 정보를 프롬프트에 포함
                cv_bbox_info = ""
                if cv_boxes:
                    cv_bbox_info = f"\n**Cloud Vision이 감지한 {len(cv_boxes)}개의 문자 영역:**\n"
                    for i, box in enumerate(cv_boxes[:20], 1):  # 최대 20개만 포함
                        bbox = box["box_2d"]
                        cv_bbox_info += f"{i}. '{box['label']}' at [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}]\n"
                    if len(cv_boxes) > 20:
                        cv_bbox_info += f"... (총 {len(cv_boxes)}개)\n"

                prompt = f"""이 만화 이미지에서 번역이 필요한 모든 텍스트를 찾아주세요.

이미지 크기: {w}x{h} 픽셀
{cv_bbox_info}
**작업:**
1. 위 Cloud Vision bbox 정보를 참고하여 텍스트 위치 파악
2. 각 텍스트의 정확한 OCR 수행 (완전한 문장으로)
3. bubble_type, font_style 판단

**반환 형식 (JSON 배열):**
[
  {{"box_2d": [ymin, xmin, ymax, xmax], "label": "완전한 일본어 텍스트", "font_style": "normal/bold/stylized", "bubble_type": "in_bubble/on_background"}}
]

**중요:**
- box_2d는 참고용입니다 (정밀한 bbox는 Cloud Vision 활용)
- label은 완전한 문장으로 OCR
- 순수 JSON만 반환
"""

                # 이미지 업로드
                image_file = genai.upload_file(img_path)

                # Gemini API 호출
                response = self.ocr_model.generate_content([prompt, image_file])
                response_text = response.text.strip()

                # JSON 추출
                if response_text.startswith('```'):
                    lines = response_text.split('\n')
                    json_lines = []
                    in_code_block = False
                    for line in lines:
                        if line.startswith('```'):
                            in_code_block = not in_code_block
                            continue
                        if in_code_block:
                            json_lines.append(line)
                    response_text = '\n'.join(json_lines)

                gemini_boxes = json.loads(response_text)

                # Gemini bbox 정규화
                x_multiplier = w / 1000.0
                y_multiplier = h / 1000.0

                for box in gemini_boxes:
                    bbox = box["box_2d"]
                    box["box_2d"] = [
                        int(bbox[0] * y_multiplier),
                        int(bbox[1] * x_multiplier),
                        int(bbox[2] * y_multiplier),
                        int(bbox[3] * x_multiplier)
                    ]

                # Gemini 결과를 JSON 파일로 저장 (정규화 후, temp_output 폴더에)
                from pathlib import Path
                output_dir = Path("temp_output")
                output_dir.mkdir(exist_ok=True)
                g3_json_path = output_dir / f"{Path(img_path).stem}_g3.json"
                with open(g3_json_path, 'w', encoding='utf-8') as f:
                    json.dump(gemini_boxes, f, ensure_ascii=False, indent=2)

                # Hybrid 병합
                if cv_boxes and gemini_boxes:
                    merged = merge_bboxes_regional_cluster(
                        cv_boxes, gemini_boxes,
                        expand_margin=30, x_threshold=15, y_threshold=8,
                        min_cv_count=3, constrain_margin=20
                    )
                    return (idx, merged, None)
                elif gemini_boxes:
                    return (idx, gemini_boxes, None)
                else:
                    return (idx, [], None)

            except Exception as e:
                return (idx, None, str(e))

        # ThreadPoolExecutor로 병렬 처리
        final_results = [None] * len(image_paths)

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(process_single_gemini, idx, img_path, cv_results[idx], image_sizes[idx][0], image_sizes[idx][1]): idx
                for idx, img_path in enumerate(image_paths)
            }

            completed = 0
            for future in as_completed(futures):
                idx, result, error = future.result()
                completed += 1

                if error:
                    print(f"  ❌ 이미지 {idx+1}/{len(image_paths)} 실패: {error}")
                    final_results[idx] = []
                else:
                    final_results[idx] = result
                    print(f"  ✅ 이미지 {idx+1}/{len(image_paths)}: {len(result)}개 bbox ({completed}/{len(image_paths)})")

        print(f"\n✅ Step 2 완료: {len(image_paths)}개 이미지 처리")

        return final_results

    def translate_bubbles(self, bubbles: List[Dict], retry_count: int = 3, extra_prompt: str = "") -> Optional[List[Dict]]:
        """
        텍스트 말풍선 번역
        
        Args:
            bubbles: OCR 결과 리스트
            retry_count: 재시도 횟수
            extra_prompt: 추가 번역 지침 (어투, 분위기 등)
            
        Returns:
            List[Dict]: 번역된 정보가 포함된 리스트
        """
        # 텍스트만 추출 (label 또는 text 키 사용)
        texts_to_translate = []
        for item in bubbles:
            if "label" in item:
                texts_to_translate.append(item["label"])
            elif "text" in item:
                texts_to_translate.append(item["text"])
            else:
                texts_to_translate.append("")
                
        expected_count = len(texts_to_translate)
        
        if expected_count == 0:
            return bubbles

        # 추가 프롬프트 처리
        style_instruction = f"\n5. {extra_prompt}" if extra_prompt else ""
        
        prompt = f"""다음 일본어 만화 대사들을 자연스러운 한국어로 번역해주세요.

일본어 텍스트 ({expected_count}개):
{json.dumps(texts_to_translate, ensure_ascii=False, indent=2)}

요구사항:
1. 만화 대사 스타일에 맞게 자연스럽게 번역
2. 순서를 유지하여 JSON 배열로 반환
3. **반드시 {expected_count}개의 번역을 반환해야 합니다**
4. 순수 JSON 배열만 반환 (다른 설명 없이){style_instruction}

예시 (3개 입력 → 3개 출력):
["안녕하세요", "감사합니다", "또 만나요"]"""

        for attempt in range(retry_count):
            try:
                response = self.translation_model.generate_content(prompt)
                response_text = response.text.strip()
                
                # JSON 추출
                if response_text.startswith('```'):
                    lines = response_text.split('\n')
                    json_lines = []
                    in_code_block = False
                    for line in lines:
                        if line.startswith('```'):
                            in_code_block = not in_code_block
                            continue
                        if in_code_block or (not line.startswith('```')):
                            json_lines.append(line)
                    response_text = '\n'.join(json_lines)
                
                # JSON 파싱
                translations = json.loads(response_text)
                
                # 원본 bubbles에 번역 적용
                if len(translations) != len(bubbles):
                    print(f"⚠️ 번역 개수 불일치 (시도 {attempt + 1}/{retry_count}): {len(translations)} vs {len(bubbles)}")
                    if attempt < retry_count - 1:
                        print(f"   재시도 중...")
                        time.sleep(1)
                        continue
                    else:
                        print(f"❌ 번역 개수 불일치 최종 실패")
                        # 마지막 시도에서는 부분 결과라도 반환
                        print(f"   부분 결과 사용: {min(len(translations), len(bubbles))}개")
                        min_len = min(len(translations), len(bubbles))
                        translated_bubbles = []
                        for i in range(min_len):
                            translated_bubble = bubbles[i].copy()
                            translated_bubble["label"] = translations[i]
                            translated_bubbles.append(translated_bubble)
                        # 남은 bubbles는 원문 유지
                        for i in range(min_len, len(bubbles)):
                            translated_bubbles.append(bubbles[i].copy())
                        return translated_bubbles
                
                translated_bubbles = []
                for bubble, translation in zip(bubbles, translations):
                    translated_bubble = bubble.copy()
                    translated_bubble["label"] = translation
                    translated_bubbles.append(translated_bubble)
                
                print(f"✅ 번역 성공: {len(translated_bubbles)}개 말풍선")
                return translated_bubbles
                
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON 파싱 실패 (시도 {attempt + 1}/{retry_count}): {e}")
                print(f"응답: {response_text[:200]}...")
                if attempt < retry_count - 1:
                    time.sleep(1)
                else:
                    print(f"❌ JSON 파싱 최종 실패")
                    return None
                    
            except Exception as e:
                print(f"⚠️ API 호출 실패 (시도 {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    time.sleep(2)
                else:
                    print(f"❌ API 호출 최종 실패")
                    return None
        
        return None
    
    def ocr_clusters(self, image_path: str, clusters_json_path: str, retry_count: int = 3) -> Optional[Dict]:
        """
        Cluster 기반 OCR: 전체 이미지 + cluster 좌표로 Gemini OCR 수행

        전체 이미지를 Gemini에 전달하고, 각 cluster의 bounding_rect 좌표 정보를 함께 제공.
        Gemini가 OCR한 텍스트를 cluster_id와 매칭하여 반환하고,
        필요시 merge 제안도 포함.

        Args:
            image_path: 원본 이미지 경로
            clusters_json_path: cluster JSON 파일 경로 (cluster_cv_v5.py 출력)
            retry_count: 재시도 횟수

        Returns:
            Dict: {
                "clusters": List[Dict] - 각 cluster에 OCR 결과 추가,
                "merge_suggestions": List[Dict] - cluster 병합 제안 (optional)
            }
        """
        print(f"📸 Cluster 기반 OCR 시작: {Path(image_path).name}")

        # 이미지 로드
        img = cv2.imread(image_path)
        if img is None:
            print(f"❌ 이미지 로드 실패: {image_path}")
            return None
        height, width = img.shape[:2]

        # Cluster JSON 로드
        with open(clusters_json_path, 'r', encoding='utf-8') as f:
            clusters = json.load(f)

        print(f"  📦 {len(clusters)}개 cluster 로드")
        print(f"  🖼️  전체 이미지 크기: {width}x{height}")

        # Cluster 정보를 프롬프트 형식으로 변환 (V5 구조)
        cluster_info = "\n**감지된 Cluster 영역:**\n"
        for cluster in clusters:
            cid = cluster['cluster_id']
            rect = cluster['bounding_rect']  # [x, y, w, h]
            orientation = cluster.get('orientation', 'vertical')
            
            # Lines 정보 추출
            lines = cluster.get('lines', [])
            cv_text_parts = [line['text'] for line in lines]
            cv_text = '\n'.join(cv_text_parts)

            # bounding_rect의 좌표 범위 계산
            x, y, w, h = rect
            x_min, x_max = x, x + w
            y_min, y_max = y, y + h

            cluster_info += f"\nCluster {cid} ({orientation}):\n"
            cluster_info += f"  - 영역: x=[{x_min}, {x_max}], y=[{y_min}, {y_max}]\n"
            cluster_info += f"  - Rect: [{x}, {y}, {w}, {h}]\n"
            cluster_info += f"  - CV 텍스트 (참고): '{cv_text}'\n"

        # 프롬프트 생성
        prompt = f"""이 만화 이미지에서 모든 일본어 텍스트를 OCR하고 의미 단위로 문장을 구성해주세요.

**이미지 크기**: {width}x{height} 픽셀

{cluster_info}

**작업:**
1. 이미지의 **모든 일본어 텍스트를 정확히 OCR** (누락 금지!)
2. 의미 단위로 **완전한 문장(Sentence)을 구성**
3. **Cloud Vision의 텍스트 정보를 참고하면서 철저히 검토**
   - 각 Cluster의 CV 텍스트에 나열된 모든 문자를 이미지에서 확인
   - CV에는 있지만 이미지에 없는 경우: CV 오류 (제외)
   - 이미지에는 있지만 CV에 없는 경우: CV가 놓친 문자 (반드시 포함!)
   - ⚠️ 특히 누락된 문자를 놓치지 마세요! (예: 軽率)
4. **세로쓰기 줄바꿈 규칙:**
   - 세로쓰기는 하나의 **연속된 문장**입니다 (여러 열로 나뉨)
   - 각 **열(column)의 경계에서만** \\n을 넣으세요
   - ❌ 나쁜 예: "う\\nわ\\nっ\\n!?" (글자 하나마다 줄바꿈)
   - ✅ 좋은 예: "うわっ!?" (하나의 열) 또는 "うわ\\nっ!?" (2개 열)
5. **가로쓰기 줄바꿈 규칙:**
   - 각 **행(row)의 경계에서만** \\n을 넣으세요
   - 예: "첫 줄\\n둘째 줄\\n셋째 줄"
6. 각 문장의 유형 분류 (dialogue/narration/sound_effect/other)
7. 후리가나(furigana) 읽기는 절대 포함하지 마세요 (한자/히라가나/카타카나만)

**반환 형식 (JSON):**
{{
  "sentences": [
    {{
      "sentence_id": 1,
      "text": "완전한 일본어 문장",
      "confidence": "high/medium/low",
      "text_type": "dialogue/sound_effect/narration/other"
    }},
    ...
  ]
}}

**중요:**
- **텍스트 누락 금지!** 이미지의 모든 텍스트를 OCR하세요
- **CV 참고 필수:** Cluster에 나열된 모든 글자를 이미지에서 확인
- **줄바꿈 필수:** 모든 줄바꿈을 \\n으로 정확히 표현 (예: "줄1\\n줄2\\n줄3")
- **후리가나 제외:** 한자와 히라가나/카타카나만 (루비 텍스트 제외)

"""

        # 이미지 업로드
        image_file = genai.upload_file(image_path)
        
        # API 호출 (타임아웃 처리)
        import threading
        response_container = [None]
        error_container = [None]
        
        def api_call():
            try:
                response_container[0] = self.ocr_model.generate_content([prompt, image_file])
            except Exception as e:
                error_container[0] = e
        
        for attempt in range(retry_count):
            try:
                print(f"  🔄 Gemini API 호출 중... (시도 {attempt + 1}/{retry_count})")
                
                # 타임아웃 설정 (60초)
                thread = threading.Thread(target=api_call)
                thread.daemon = True
                thread.start()
                thread.join(timeout=60)
                
                if thread.is_alive():
                    print(f"  ⚠️ API 호출 타임아웃 (60초 초과)")
                    if attempt < retry_count - 1:
                        time.sleep(2)
                        continue
                    else:
                        return None
                
                if error_container[0]:
                    raise error_container[0]
                
                response = response_container[0]
                if not response:
                    print(f"  ⚠️ 응답 없음")
                    if attempt < retry_count - 1:
                        time.sleep(2)
                        continue
                    else:
                        return None
                
                response_text = response.text.strip()
                
                # JSON 추출
                if response_text.startswith('```'):
                    lines = response_text.split('\n')
                    json_lines = []
                    in_json = False
                    for line in lines:
                        if line.strip().startswith('```'):
                            if in_json:
                                break
                            in_json = True
                            continue
                        if in_json:
                            json_lines.append(line)
                    response_text = '\n'.join(json_lines)
                
                # JSON 파싱
                result = json.loads(response_text)

                # 결과 검증
                if 'sentences' not in result:
                    print(f"  ⚠️ 'sentences' 필드 없음")
                    if attempt < retry_count - 1:
                        time.sleep(2)
                        continue
                    else:
                        return None

                print(f"  ✅ OCR 완료: {len(result['sentences'])}개 sentence")

                return result
                
            except json.JSONDecodeError as e:
                print(f"  ⚠️ JSON 파싱 실패 (시도 {attempt + 1}/{retry_count}): {e}")
                print(f"  응답: {response_text[:200]}...")
                if attempt < retry_count - 1:
                    time.sleep(1)
                else:
                    print(f"❌ JSON 파싱 최종 실패")
                    return None
                    
            except Exception as e:
                print(f"  ⚠️ API 호출 실패 (시도 {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    time.sleep(2)
                else:
                    print(f"❌ API 호출 최종 실패")
                    return None
        
        return None
    
    def process_image(self, image_path: str) -> Optional[tuple]:
        """
        이미지 전체 처리 파이프라인

        Args:
            image_path: 이미지 파일 경로

        Returns:
            tuple: (ocr_result, translated_result) 또는 None
        """
        print(f"📸 이미지 처리 시작: {Path(image_path).name}")

        # Step 1: OCR + bbox 추출
        print("1️⃣ OCR + bbox 추출 중...")
        ocr_result = self.extract_text_bubbles(image_path)
        if not ocr_result:
            print("❌ OCR 추출 실패")
            return None

        # Step 2: 번역
        print("2️⃣ 번역 중...")
        translated_result = self.translate_bubbles(ocr_result)
        if not translated_result:
            print("❌ 번역 실패")
            return None

        print(f"✅ 이미지 처리 완료: {Path(image_path).name}")
        return (ocr_result, translated_result)


# 테스트 코드
if __name__ == "__main__":
    import os
    
    # API 키는 환경 변수에서 가져오기
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY 환경 변수를 설정해주세요")
        exit(1)
    
    # API 초기화
    api = GeminiAPI(api_key)
    
    # 테스트 이미지
    test_image = "test_image/image.jpg"
    if not Path(test_image).exists():
        print(f"❌ 테스트 이미지를 찾을 수 없습니다: {test_image}")
        exit(1)
    
    # 처리
    result = api.process_image(test_image)
    if result:
        ocr_result, translated_result = result
        print("\n📄 OCR 결과:")
        print(json.dumps(ocr_result, ensure_ascii=False, indent=2))
        print("\n📄 번역 결과:")
        print(json.dumps(translated_result, ensure_ascii=False, indent=2))
