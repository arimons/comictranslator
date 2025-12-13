import cv2
import numpy as np
from pathlib import Path

def preprocess_image_for_ocr(image_path, output_dir=None):
    """
    Cloud Vision OCR 인식을 위한 이미지 전처리

    적용 기법:
    1. Grayscale 변환
    2. Noise Reduction (NLMeans - 강화)
    3. Contrast Enhancement (CLAHE - 강화)
    4. Otsu Threshold (명확한 흑백 이진화)
    5. Morphology Closing (텍스트 연결성 개선)

    Args:
        image_path (str): 원본 이미지 경로
        output_dir (Path, optional): 저장할 디렉토리. 없으면 원본과 같은 위치.

    Returns:
        str: 전처리된 이미지 경로
    """
    try:
        img = cv2.imread(str(image_path))
        if img is None:
            print(f"❌ 전처리 실패: 이미지 로드 불가 ({image_path})")
            return image_path

        # 1. Grayscale 변환
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 2. Noise Reduction (NLMeans - 강력한 노이즈 제거)
        # fastNlMeansDenoising: 효과적인 노이즈 제거, CV 인식 개선
        denoised = cv2.fastNlMeansDenoising(gray)

        # 3. Contrast Enhancement (CLAHE - 강화)
        # clipLimit 3.0: 과도한 명암 증강 방지하면서 충분한 개선
        # tileGridSize (8,8): 로컬 히스토그램 영역 크기
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)

        # 4. Otsu Threshold (자동 임계값으로 명확한 흑백 이진화)
        # THRESH_OTSU: 명확한 흑백 분리 → CV 인식 정확도 향상
        # 이전 회색 이미지 대신 명확한 이진 이미지로 변경
        _, binary = cv2.threshold(enhanced, 0, 255,
                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 5. Morphology Closing (작은 구멍 채우기, 텍스트 연결성 개선)
        # kernel (2,2): S→16 같은 오인식 방지
        kernel = np.ones((2, 2), np.uint8)
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        # 저장 경로 설정
        p = Path(image_path)
        if output_dir:
            output_path = output_dir / f"{p.stem}_prep{p.suffix}"
        else:
            output_path = p.parent / f"{p.stem}_prep{p.suffix}"

        # 흑백 이미지 저장 (CV API에 최적화)
        cv2.imwrite(str(output_path), cleaned)

        return str(output_path)

    except Exception as e:
        print(f"❌ 전처리 중 오류: {e}")
        return image_path  # 실패 시 원본 반환
