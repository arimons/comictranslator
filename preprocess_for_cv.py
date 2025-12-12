import cv2
import numpy as np
from pathlib import Path

def preprocess_image_for_ocr(image_path, output_dir=None):
    """
    Cloud Vision OCR 인식을 위한 이미지 전처리
    
    적용 기법:
    1. Grayscale 변환
    2. Noise Reduction (Gaussian Blur)
    3. Contrast Enhancement (CLAHE)
    4. Sharpening (Unsharp Mask)
    
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
            
        # 1. Grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 2. Noise Reduction (약하게)
        denoised = cv2.GaussianBlur(gray, (3, 3), 0)
        
        # 3. Contrast Enhancement (CLAHE)
        # ClipLimit 2.0, GridSize 8x8은 일반적인 문서/텍스트에 적합
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(denoised)
        
        # 4. Sharpening (Unsharp Mask)
        # Gaussian Blur로 블러링된 버전을 원본에서 빼서 엣지 강조
        gaussian = cv2.GaussianBlur(enhanced, (0, 0), 2.0)
        sharpened = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)
        
        # 저장 경로 설정
        p = Path(image_path)
        if output_dir:
            output_path = output_dir / f"{p.stem}_prep{p.suffix}"
        else:
            output_path = p.parent / f"{p.stem}_prep{p.suffix}"
            
        cv2.imwrite(str(output_path), sharpened)
        
        return str(output_path)
        
    except Exception as e:
        print(f"❌ 전처리 중 오류: {e}")
        return image_path  # 실패 시 원본 반환
