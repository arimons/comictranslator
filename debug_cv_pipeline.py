#!/usr/bin/env python3
"""
디버그 전용: 전처리 → CV → 클러스터링 → 시각화
00010.jpg 같은 단일 파일을 Debug 폴더에서 빠르게 테스트
"""
import cv2
import numpy as np
import json
from pathlib import Path
from typing import List, Dict, Tuple
from dotenv import load_dotenv
import os
import time
from cloud_vision_api import CloudVisionAPI

# 환경 설정
load_dotenv()
CV_API_KEY = os.getenv("CLOUD_VISION_API_KEY") or os.getenv("GOOGLE_CLOUD_VISION_API_KEY")
if not CV_API_KEY:
    print("❌ CLOUD_VISION_API_KEY 또는 GOOGLE_CLOUD_VISION_API_KEY 환경 변수 필요")
    exit(1)


def preprocess_image(image_path: str, output_dir: Path) -> str:
    """전처리: Otsu + Morphology Closing"""
    print(f"\n1️⃣ 전처리 시작")
    print(f"   입력: {image_path}")

    img = cv2.imread(str(image_path))
    if img is None:
        print(f"❌ 이미지 로드 실패")
        return None

    # Grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # NLMeans denoising
    denoised = cv2.fastNlMeansDenoising(gray)

    # CLAHE
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)

    # Otsu Threshold (KEY: 바이너리 출력)
    _, binary = cv2.threshold(enhanced, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Morphology Closing
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # 저장
    p = Path(image_path)
    output_path = output_dir / f"{p.stem}_prep.jpg"
    cv2.imwrite(str(output_path), cleaned)

    print(f"   ✅ 전처리 완료: {output_path}")
    return str(output_path)


def run_cv_api(image_path: str, output_dir: Path) -> List[Dict]:
    """CV API로 텍스트 감지"""
    print(f"\n2️⃣ Cloud Vision API 호출")
    print(f"   입력: {image_path}")

    start = time.time()

    try:
        # CloudVision API 사용
        cv_api = CloudVisionAPI(api_key=CV_API_KEY)
        detections_raw = cv_api.detect_text(image_path)

        elapsed = time.time() - start

        if not detections_raw:
            print(f"   ⚠️ 감지된 텍스트 없음")
            return []

        # box_2d -> bbox [x, y, w, h] 형식으로 변환
        detections = []
        for item in detections_raw:
            box = item.get('box_2d', [])
            if len(box) >= 4:
                # Cloud Vision은 [ymin, xmin, ymax, xmax] 형식
                # bbox [x, y, w, h] 형식으로 변환
                ymin, xmin, ymax, xmax = box[0], box[1], box[2], box[3]
                x, y = int(xmin), int(ymin)
                w, h = int(xmax - xmin), int(ymax - ymin)

                detections.append({
                    'text': item.get('label', ''),
                    'bbox': [x, y, w, h]
                })

        # 저장
        p = Path(image_path)
        cv_output = output_dir / f"{p.stem}_cv.json"
        with open(cv_output, 'w', encoding='utf-8') as f:
            json.dump(detections, f, ensure_ascii=False, indent=2)

        print(f"   ✅ CV API 완료 ({elapsed:.1f}초): {len(detections)}개 텍스트")
        print(f"   저장: {cv_output}")

        return detections

    except Exception as e:
        print(f"❌ CV API 오류: {e}")
        import traceback
        traceback.print_exc()
        return []


def cluster_detections(detections: List[Dict]) -> List[Dict]:
    """CV 결과 클러스터링"""
    print(f"\n3️⃣ CV 결과 클러스터링")
    print(f"   입력: {len(detections)}개 텍스트")

    if not detections:
        return []

    # 간단한 그리드 기반 클러스터링 (5px 허용오차)
    GRID_SIZE = 50
    grid = {}

    for det in detections:
        bbox = det['bbox']  # [x, y, w, h]
        x, y = bbox[0], bbox[1]

        # 그리드 셀 결정
        grid_x = int(x // GRID_SIZE)
        grid_y = int(y // GRID_SIZE)
        cell = (grid_x, grid_y)

        if cell not in grid:
            grid[cell] = []
        grid[cell].append(det)

    # 그리드 셀을 클러스터로 변환
    clusters = []
    cluster_id = 1

    for cell, items in grid.items():
        if not items:
            continue

        # 행 방향 정렬 (위에서 아래로)
        items_sorted = sorted(items, key=lambda d: (d['bbox'][1], d['bbox'][0]))

        # Bounding rect 계산
        xs = [item['bbox'][0] for item in items_sorted]
        ys = [item['bbox'][1] for item in items_sorted]
        ws = [item['bbox'][2] for item in items_sorted]
        hs = [item['bbox'][3] for item in items_sorted]

        x_min, y_min = min(xs), min(ys)
        x_max = max(xs[i] + ws[i] for i in range(len(xs)))
        y_max = max(ys[i] + hs[i] for i in range(len(ys)))

        cluster = {
            'cluster_id': cluster_id,
            'lines': [{'text': item['text'], 'bbox': item['bbox']}
                     for item in items_sorted],
            'bounding_rect': [x_min, y_min, x_max - x_min, y_max - y_min],
            'orientation': 'vertical' if (y_max - y_min) > (x_max - x_min) else 'horizontal'
        }

        clusters.append(cluster)
        cluster_id += 1

    print(f"   ✅ 클러스터링 완료: {len(clusters)}개 클러스터")

    return clusters


def visualize_clusters(image_path: str, clusters: List[Dict], output_dir: Path):
    """클러스터 시각화"""
    print(f"\n4️⃣ 시각화 시작")

    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 이미지 로드 실패")
        return

    img_vis = img.copy()
    h, w = img_vis.shape[:2]

    # 색상 팔레트 (클러스터별 고유 색)
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
    ]

    for cluster in clusters:
        color = colors[cluster['cluster_id'] % len(colors)]

        # Cluster bbox 그리기 (굵은 테두리)
        bbox = cluster['bounding_rect']
        x, y, w_b, h_b = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        cv2.rectangle(img_vis, (x, y), (x + w_b, y + h_b), color, 3)

        # 클러스터 ID 텍스트
        cv2.putText(img_vis, f"C{cluster['cluster_id']}", (x, y - 5),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        # 개별 라인 bbox 그리기 (얇은 테두리)
        for line in cluster['lines']:
            lbbox = line['bbox']
            lx, ly, lw, lh = int(lbbox[0]), int(lbbox[1]), int(lbbox[2]), int(lbbox[3])
            cv2.rectangle(img_vis, (lx, ly), (lx + lw, ly + lh), color, 1)

            # 텍스트 출력
            text = line['text'][:10]  # 첫 10글자만
            cv2.putText(img_vis, text, (lx, ly + 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    # 저장
    p = Path(image_path)
    vis_output = output_dir / f"{p.stem}_clusters_vis.jpg"
    cv2.imwrite(str(vis_output), img_vis)

    print(f"   ✅ 시각화 저장: {vis_output}")

    # 정보 출력
    print(f"\n   이미지 크기: {w}x{h}")
    for cluster in clusters:
        bbox = cluster['bounding_rect']
        print(f"   Cluster {cluster['cluster_id']}: {len(cluster['lines'])} lines @ ({int(bbox[0])}, {int(bbox[1])})")


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python debug_cv_pipeline.py <image_path>")
        print("Example: python debug_cv_pipeline.py temp_output/Debug/00010.jpg")
        sys.exit(1)

    image_path = sys.argv[1]

    # 파일 확인
    if not Path(image_path).exists():
        print(f"❌ 파일을 찾을 수 없습니다: {image_path}")
        sys.exit(1)

    # 출력 디렉토리
    output_dir = Path(image_path).parent

    print(f"\n{'='*60}")
    print(f"🔍 DEBUG: CV 파이프라인 (전처리 → CV → 클러스터링 → 시각화)")
    print(f"{'='*60}")
    print(f"입력 파일: {image_path}")
    print(f"출력 디렉토리: {output_dir}")

    # Step 1: 전처리
    prep_path = preprocess_image(image_path, output_dir)
    if not prep_path:
        sys.exit(1)

    # Step 2: CV API
    detections = run_cv_api(prep_path, output_dir)
    if not detections:
        print("❌ CV 결과가 없습니다")
        sys.exit(1)

    # Step 3: 클러스터링
    clusters = cluster_detections(detections)

    # Step 4: 시각화
    visualize_clusters(image_path, clusters, output_dir)

    # 클러스터 JSON 저장
    p = Path(image_path)
    cluster_output = output_dir / f"{p.stem}_cv_clusters_v5.json"
    with open(cluster_output, 'w', encoding='utf-8') as f:
        json.dump(clusters, f, ensure_ascii=False, indent=2)
    print(f"\n   저장: {cluster_output}")

    print(f"\n{'='*60}")
    print(f"✅ 디버그 파이프라인 완료!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
