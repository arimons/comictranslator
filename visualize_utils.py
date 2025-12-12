import cv2
import json
import numpy as np
from pathlib import Path

def visualize_integrated_bboxes(image_path, integrated_path, output_path):
    """
    integrated.json의 bbox를 원본 이미지에 시각화하여 저장
    
    Args:
        image_path (str): 원본 이미지 경로
        integrated_path (str): integrated.json 파일 경로
        output_path (str): 저장할 이미지 경로
    """
    try:
        # 이미지 로드
        img = cv2.imread(str(image_path))
        if img is None:
            print(f"❌ 시각화 실패: 이미지 로드 불가 ({image_path})")
            return False
            
        # JSON 로드
        with open(integrated_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # 사본 생성
        vis_img = img.copy()
        
        # BBox 그리기
        for item in data:
            # 기본 정보 추출
            sid = item.get('sentence_id', '?')
            rect = item.get('bounding_rect')
            location = item.get('location', 'unknown')
            text_type = item.get('text_type', 'unknown')
            
            if not rect or len(rect) != 4:
                continue
                
            x, y, w, h = rect
            
            # 색상 설정 (BGR)
            # in_bubble: 초록색, outside_bubble: 주황색, unknown: 빨간색
            if location == 'in_bubble':
                color = (0, 255, 0)      # Green
                thickness = 2
            elif location == 'outside_bubble':
                color = (0, 165, 255)    # Orange
                thickness = 2
            else:
                color = (0, 0, 255)      # Red
                thickness = 2
                
            # 사각형 그리기
            cv2.rectangle(vis_img, (x, y), (x + w, y + h), color, thickness)
            
            # 텍스트 배경 (가독성 위해)
            label = f"S{sid} ({location})"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            
            # 텍스트가 위로 벗어나지 않게 조정
            text_y = y - 5 if y - 5 > th else y + h + th + 5
            
            cv2.rectangle(vis_img, (x, text_y - th - 5), (x + tw, text_y + 5), color, -1)
            
            # 텍스트 쓰기 (검은색 또는 흰색)
            text_color = (0, 0, 0) if location in ['in_bubble', 'outside_bubble'] else (255, 255, 255)
            cv2.putText(vis_img, label, (x, text_y), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, text_color, 1, cv2.LINE_AA)
            
        # 저장
        cv2.imwrite(str(output_path), vis_img)
        return True
        
    except Exception as e:
        print(f"❌ 시각화 중 오류 발생: {e}")
        return False
