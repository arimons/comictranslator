#!/usr/bin/env python3
"""
Cloud Vision API 통합 모듈
정확한 텍스트 bbox 추출용
"""

import os
import requests
import base64
from typing import List, Dict, Optional
from pathlib import Path
from google.cloud import vision
import io


class CloudVisionAPI:
    def __init__(self, api_key: Optional[str] = None, credentials_path: Optional[str] = None):
        """
        Cloud Vision API 초기화

        Args:
            api_key: Cloud Vision API 키 (선택) - REST API 사용
            credentials_path: Service Account JSON 경로 (선택) - Python 클라이언트 사용
        """
        self.api_key = api_key
        self.use_rest_api = bool(api_key)
        
        if not self.use_rest_api:
            # Service Account 방식 우선
            if credentials_path:
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path
            # If credentials_path is not provided, it will try to load from GOOGLE_APPLICATION_CREDENTIALS env var or default credentials.
            self.client = vision.ImageAnnotatorClient()

    def detect_text(self, image_path: str) -> Optional[List[Dict]]:
        """
        이미지에서 텍스트 bbox 추출 (TEXT_DETECTION)

        Args:
            image_path: 이미지 파일 경로

        Returns:
            List[Dict]: [{"box_2d": [ymin, xmin, ymax, xmax], "label": "텍스트"}, ...]
        """
        if self.use_rest_api:
            return self._detect_text_rest(image_path)
        else:
            return self._detect_text_client(image_path)
    
    def _detect_text_rest(self, image_path: str) -> Optional[List[Dict]]:
        """REST API로 텍스트 감지 (API 키 사용)"""
        try:
            # 이미지를 base64로 인코딩
            with open(image_path, 'rb') as f:
                image_content = f.read()
            
            encoded_image = base64.b64encode(image_content).decode('utf-8')
            
            # REST API 요청
            url = f"https://vision.googleapis.com/v1/images:annotate?key={self.api_key}"
            
            request_body = {
                "requests": [{
                    "image": {"content": encoded_image},
                    "features": [{"type": "TEXT_DETECTION"}]
                }]
            }
            
            response = requests.post(url, json=request_body, timeout=60)
            
            if response.status_code != 200:
                raise Exception(f"{response.status_code} {response.text}")
            
            result = response.json()
            
            if 'responses' not in result or not result['responses']:
                return []
            
            annotations = result['responses'][0].get('textAnnotations', [])
            
            if not annotations:
                print(f"⚠️ Cloud Vision: 텍스트를 찾지 못했습니다")
                return []
            
            # 첫 번째는 전체 텍스트이므로 제외
            results = []
            for text in annotations[1:]:
                vertices = text['boundingPoly']['vertices']
                
                xs = [v.get('x', 0) for v in vertices]
                ys = [v.get('y', 0) for v in vertices]
                
                xmin = min(xs)
                xmax = max(xs)
                ymin = min(ys)
                ymax = max(ys)
                
                results.append({
                    "box_2d": [ymin, xmin, ymax, xmax],
                    "label": text['description'],
                    "confidence": 1.0 # REST API response for TEXT_DETECTION doesn't typically include confidence per word/line
                })
            
            print(f"✅ Cloud Vision (API Key): {len(results)}개 텍스트 감지")
            return results
            
        except Exception as e:
            print(f"❌ Cloud Vision API 오류: {e}")
            return None

    def _detect_text_client(self, image_path: str) -> Optional[List[Dict]]:
        """Python 클라이언트로 텍스트 감지 (서비스 계정 사용)"""
        try:
            # 이미지 로드
            with io.open(image_path, 'rb') as image_file:
                content = image_file.read()

            image = vision.Image(content=content)

            # TEXT_DETECTION 수행
            response = self.client.text_detection(image=image)
            texts = response.text_annotations

            if response.error.message:
                raise Exception(f'Cloud Vision API Error: {response.error.message}')

            if not texts:
                print(f"⚠️ Cloud Vision: 텍스트를 찾지 못했습니다")
                return []

            # 첫 번째 항목은 전체 텍스트이므로 제외
            # 개별 텍스트만 추출
            results = []
            for text in texts[1:]:  # Skip first item (full text)
                vertices = text.bounding_poly.vertices

                # bbox 좌표 추출 (ymin, xmin, ymax, xmax)
                xs = [v.x for v in vertices]
                ys = [v.y for v in vertices]

                xmin = min(xs)
                xmax = max(xs)
                ymin = min(ys)
                ymax = max(ys)

                results.append({
                    "box_2d": [ymin, xmin, ymax, xmax],
                    "label": text.description,
                    "confidence": text.confidence if hasattr(text, 'confidence') else 1.0
                })

            print(f"✅ Cloud Vision: {len(results)}개 텍스트 감지")
            return results

        except Exception as e:
            print(f"❌ Cloud Vision API 오류: {e}")
            return None

    def detect_document_text(self, image_path: str) -> Optional[List[Dict]]:
        """
        이미지에서 문서 텍스트 bbox 추출 (DOCUMENT_TEXT_DETECTION)
        더 정확한 레이아웃 분석

        Args:
            image_path: 이미지 파일 경로

        Returns:
            List[Dict]: [{"box_2d": [ymin, xmin, ymax, xmax], "label": "텍스트"}, ...]
        """
        try:
            # 이미지 로드
            with io.open(image_path, 'rb') as image_file:
                content = image_file.read()

            image = vision.Image(content=content)

            # DOCUMENT_TEXT_DETECTION 수행
            response = self.client.document_text_detection(image=image)

            if response.error.message:
                raise Exception(f'Cloud Vision API Error: {response.error.message}')

            if not response.full_text_annotation:
                print(f"⚠️ Cloud Vision: 문서 텍스트를 찾지 못했습니다")
                return []

            # 단어 단위로 추출
            results = []
            for page in response.full_text_annotation.pages:
                for block in page.blocks:
                    for paragraph in block.paragraphs:
                        for word in paragraph.words:
                            # 단어 텍스트 결합
                            word_text = ''.join([symbol.text for symbol in word.symbols])

                            # bbox 좌표 추출
                            vertices = word.bounding_box.vertices
                            xs = [v.x for v in vertices]
                            ys = [v.y for v in vertices]

                            xmin = min(xs)
                            xmax = max(xs)
                            ymin = min(ys)
                            ymax = max(ys)

                            results.append({
                                "box_2d": [ymin, xmin, ymax, xmax],
                                "label": word_text,
                                "confidence": word.confidence
                            })

            print(f"✅ Cloud Vision (Document): {len(results)}개 텍스트 감지")
            return results

        except Exception as e:
            print(f"❌ Cloud Vision API 오류: {e}")
            return None

    def detect_text_grid(self, image_paths: List[str]) -> Optional[List[List[Dict]]]:
        """
        여러 이미지를 그리드로 병합하여 Cloud Vision에 1번 호출 후 각 이미지별 bbox 분리

        Args:
            image_paths: 이미지 파일 경로 리스트

        Returns:
            List[List[Dict]]: 각 이미지별 bbox 리스트
        """
        import cv2
        import numpy as np

        if not image_paths:
            return None

        # 이미지 로드
        images = []
        image_sizes = []  # (width, height) 저장

        for img_path in image_paths:
            img = cv2.imread(img_path)
            if img is None:
                print(f"❌ 이미지 로드 실패: {img_path}")
                return None
            images.append(img)
            h, w = img.shape[:2]
            image_sizes.append((w, h))

        # Grid 크기 계산 (grid_cols는 최대 5로 제한, grid_rows는 필요한 만큼)
        # 40MB 제한을 위해 한 번에 처리하는 이미지 수를 제한해야 함
        n_images = len(images)
        grid_cols = min(int(np.ceil(np.sqrt(n_images))), 5)
        grid_rows = int(np.ceil(n_images / grid_cols))  # 실제 필요한 행 수

        # 각 이미지 크기 정규화 (가장 큰 이미지 기준)
        max_h = max(h for w, h in image_sizes)
        max_w = max(w for w, h in image_sizes)

        # 그리드 이미지 생성
        grid_img = np.ones((max_h * grid_rows, max_w * grid_cols, 3), dtype=np.uint8) * 255

        # 이미지 배치 및 오프셋 기록
        offsets = []  # (y_offset, x_offset, original_w, original_h)
        for idx, img in enumerate(images):
            row = idx // grid_cols
            col = idx % grid_cols
            y_offset = row * max_h
            x_offset = col * max_w
            h, w = img.shape[:2]
            grid_img[y_offset:y_offset+h, x_offset:x_offset+w] = img
            offsets.append((y_offset, x_offset, w, h))

        # 임시 그리드 이미지 저장
        temp_grid_path = Path("temp_output") / "temp_cv_grid.jpg"
        temp_grid_path.parent.mkdir(exist_ok=True)
        cv2.imwrite(str(temp_grid_path), grid_img)

        print(f"📸 Cloud Vision 그리드 처리 중 ({n_images}개 이미지, {grid_rows}x{grid_cols} 그리드)...")

        # Cloud Vision으로 그리드 전체 OCR
        grid_boxes = self.detect_text(str(temp_grid_path))

        if not grid_boxes:
            print("❌ Cloud Vision 그리드 OCR 실패")
            temp_grid_path.unlink(missing_ok=True)
            return None

        # 각 이미지별로 bbox 분리
        results = [[] for _ in range(n_images)]

        for box in grid_boxes:
            bbox = box["box_2d"]  # [ymin, xmin, ymax, xmax]

            # ✅ 개선: bbox의 중심점이 아닌 bbox 영역과 셀의 교집합으로 판단
            # bbox가 여러 셀에 걸쳐있을 수 있으므로, 가장 많이 겹치는 셀을 선택
            ymin, xmin, ymax, xmax = bbox

            best_idx = -1
            best_overlap = 0

            for idx, (y_off, x_off, orig_w, orig_h) in enumerate(offsets):
                # 셀의 범위: y [y_off, y_off + orig_h), x [x_off, x_off + orig_w)
                cell_ymin, cell_ymax = y_off, y_off + orig_h
                cell_xmin, cell_xmax = x_off, x_off + orig_w

                # bbox와 셀의 교집합 계산
                overlap_ymin = max(ymin, cell_ymin)
                overlap_ymax = min(ymax, cell_ymax)
                overlap_xmin = max(xmin, cell_xmin)
                overlap_xmax = min(xmax, cell_xmax)

                # 교집합이 존재하는지 확인
                if overlap_ymin < overlap_ymax and overlap_xmin < overlap_xmax:
                    overlap_area = (overlap_ymax - overlap_ymin) * (overlap_xmax - overlap_xmin)

                    # 가장 많이 겹치는 셀 선택
                    if overlap_area > best_overlap:
                        best_overlap = overlap_area
                        best_idx = idx

            if best_idx >= 0:
                y_off, x_off, orig_w, orig_h = offsets[best_idx]
                # 그리드 좌표 → 원본 이미지 좌표로 변환
                local_bbox = [
                    bbox[0] - y_off,  # ymin
                    bbox[1] - x_off,  # xmin
                    bbox[2] - y_off,  # ymax
                    bbox[3] - x_off   # xmax
                ]
                results[best_idx].append({
                    "box_2d": local_bbox,
                    "label": box["label"],
                    "confidence": box.get("confidence", 1.0)
                })

        # 임시 파일 삭제
        temp_grid_path.unlink(missing_ok=True)

        # 결과 출력 (개별 이미지 세부사항은 생략)
        total_boxes = sum(len(boxes) for boxes in results)
        print(f"  ✅ Grid OCR 완료: {n_images}개 이미지, 총 {total_boxes}개 bbox")
        

        return results


# 간단한 래퍼 함수 (호환성)
def detect_text_cv(image_path: str) -> Optional[List[Dict]]:
    """Cloud Vision API로 텍스트 bbox 추출 (간편 함수)"""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    api = CloudVisionAPI(credentials_path=credentials_path)
    return api.detect_text(image_path)


# 테스트 코드
if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv()

    # API 초기화
    api_key = os.getenv("CLOUD_VISION_API_KEY")
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not api_key and not credentials_path:
        print("❌ CLOUD_VISION_API_KEY 또는 GOOGLE_APPLICATION_CREDENTIALS 환경 변수를 설정해주세요")
        sys.exit(1)

    api = CloudVisionAPI(api_key=api_key, credentials_path=credentials_path)

    # 테스트 이미지
    test_image = "temp_output/00037.jpg"
    if not Path(test_image).exists():
        print(f"❌ 테스트 이미지를 찾을 수 없습니다: {test_image}")
        sys.exit(1)

    # TEXT_DETECTION 테스트
    print("\n📝 TEXT_DETECTION 테스트:")
    results = api.detect_text(test_image)
    if results:
        print(f"감지된 텍스트 수: {len(results)}")
        for i, item in enumerate(results[:5], 1):
            print(f"  {i}. {item['label']} - bbox: {item['box_2d']}")

    # DOCUMENT_TEXT_DETECTION 테스트
    print("\n📄 DOCUMENT_TEXT_DETECTION 테스트:")
    doc_results = api.detect_document_text(test_image)
    if doc_results:
        print(f"감지된 텍스트 수: {len(doc_results)}")
        for i, item in enumerate(doc_results[:5], 1):
            print(f"  {i}. {item['label']} - bbox: {item['box_2d']}")
