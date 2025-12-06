#!/usr/bin/env python3
"""
만화 번역 파이프라인 - Gradio UI (통합 버전)

Grid 처리 + V5 파이프라인 통합:
1. CV API (Grid 처리) → cv.json
2. 클러스터링 V5 (병렬) → cv_cluster_v5.json
3. Gemini OCR (병렬) → ocr_v5.json  
4. 텍스트 통합 (병렬) → integrated.json
5. Gemini Translation (병렬) → integrated_translated.json
6. White Masking (병렬) → _masked.jpg
7. Text Overlay (병렬) → _final.jpg
"""

import os
import json
import shutil
import gradio as gr
import zipfile
import subprocess
import numpy as np
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

# 환경 변수 로드
load_dotenv()

# 전역 변수
temp_dir = Path("temp_output")  # 중간 파일 (JSON, masked)
output_dir = Path("output")  # 최종 결과물 (final.jpg, ZIP)
temp_dir.mkdir(exist_ok=True)
output_dir.mkdir(exist_ok=True)
prompt_history_file = temp_dir / "last_prompt.txt"


def load_last_prompt() -> str:
    """마지막 사용 프롬프트 로드"""
    try:
        if prompt_history_file.exists():
            with open(prompt_history_file, 'r', encoding='utf-8') as f:
                return f.read().strip()
    except Exception as e:
        print(f"⚠️ 프롬프트 로드 실패: {e}")
    return ""


def save_prompt(prompt: str):
    """현재 프롬프트 저장"""
    try:
        if prompt:
            with open(prompt_history_file, 'w', encoding='utf-8') as f:
                f.write(prompt)
    except Exception as e:
        print(f"⚠️ 프롬프트 저장 실패: {e}")


def check_existing_results(image_name: str) -> dict:
    """기존 결과 파일 확인"""
    final_img = output_dir / f"{image_name}_final.jpg"
    
    existing = {
        'final': final_img if final_img.exists() else None,
        'intermediate': []
    }
    
    # 중간 파일 확인
    for suffix in ['_cv.json', '_cv_clusters_v5.json', '_ocr_v5.json', 
                   '_integrated.json', '_integrated_translated.json', '_masked.jpg']:
        path = temp_dir / f"{image_name}{suffix}"
        if path.exists():
            existing['intermediate'].append(path.name)
    
    return existing


def is_grayscale(image_path: str, threshold: int = 30) -> bool:
    """
    이미지가 흑백(만화 페이지)인지 컬러(표지/일러스트)인지 판별
    
    Args:
        image_path: 이미지 경로
        threshold: 채도(Saturation) 임계값 (평균 채도가 이보다 낮으면 흑백)
    
    Returns:
        True if grayscale/black-and-white, False if color
    """
    try:
        import cv2
        import numpy as np
        
        img = cv2.imread(str(image_path))
        if img is None:
            return True # 읽을 수 없으면 기본값으로 처리
            
        # HSV로 변환하여 Saturation 확인
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        s = hsv[:, :, 1]
        
        # 평균 채도 계산
        mean_s = np.mean(s)
        
        # 채도가 낮으면 흑백으로 간주
        return mean_s < threshold
    except Exception as e:
        print(f"⚠️ 컬러 감지 실패: {e}")
        return True # 에러 시 안전하게 흑백으로 처리


def process_cover_image_wrapper(image_path: str) -> tuple:
    """
    translate_cover를 process_single_image와 동일한 인터페이스로 래핑
    """
    from translate_cover import translate_cover
    
    img_name = Path(image_path).stem
    output_path = output_dir / f"{img_name}_ai.jpg"
    log = []
    
    log.append(f"🎨 AI 표지 번역 (컬러 감지): {img_name}")
    
    try:
        success = translate_cover(str(image_path), str(output_path))
        if success:
            log.append("  ✅ 완료")
            return str(output_path), "\n".join(log)
        else:
            log.append("  ⚠️ 실패 (텍스트 가이드 생성)")
            return None, "\n".join(log)
    except Exception as e:
        log.append(f"  ❌ 오류: {e}")
        return None, "\n".join(log)


def get_available_fonts() -> List[str]:
    """fonts/ 폴더에서 사용 가능한 폰트 목록 반환"""
    fonts_dir = Path("fonts")
    if not fonts_dir.exists():
        return ["기본 폰트"]
    
    fonts = []
    for font_file in sorted(fonts_dir.glob("*.ttf")):
        fonts.append(font_file.stem)
    for font_file in sorted(fonts_dir.glob("*.otf")):
        fonts.append(font_file.stem)
    
    return fonts if fonts else ["기본 폰트"]


def translate_cover_batch(image_files, progress=gr.Progress()):
    """Gemini 3 Pro Image Preview로 배치 번역"""
    from translate_cover import translate_cover
    
    if not image_files:
        return [], "이미지를 업로드해주세요"
    
    results = []
    log = []
    log.append("🎨 AI 표지 번역 시작\n")
    
    for idx, img_file in enumerate(image_files):
        img_path = img_file.name if hasattr(img_file, 'name') else img_file
        img_name = Path(img_path).stem
        output_path = output_dir / f"{img_name}_ai.jpg"
        
        progress((idx + 1) / len(image_files), 
                desc=f"처리 중 ({idx+1}/{len(image_files)})...")
        
        log.append(f"\n🎨 {img_name}")
        success = translate_cover(str(img_path), str(output_path))
        
        if success:
            results.append(str(output_path))
            log.append("  ✅ 완료")
        else:
            log.append("  ⚠️ 텍스트 가이드 생성")
    
    log.append(f"\n\n✅ 완료: {len(results)}/{len(image_files)}개 성공")
    return results, "\n".join(log)


def create_output_zip():
    """output/ 폴더의 모든 이미지를 ZIP으로 압축"""
    output_files = list(output_dir.glob("*.jpg"))
    if not output_files:
        return None
    
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = output_dir / f"translated_{timestamp}.zip"
    
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for f in output_files:
            zf.write(f, f.name)
    
    return str(zip_path)


def process_single_image(image_path: str, extra_prompt: str = "", force_reprocess: bool = False, font_path: str = None) -> tuple:
    """
    단일 이미지 처리 (병렬 실행용)
    
    Returns:
        (final_image_path, log_text)
    """
    temp_dir = Path("temp_output")
    output_dir = Path("output")
    
    img_name = Path(image_path).stem
    log = []
    log.append(f"📸 처리 중: {img_name}")
    
    # 빈 이미지 플래그 (텍스트 없는 이미지)
    is_blank_image = False
    
    try:
        # 출력 경로 설정
        # 중간 파일들은 temp_output에
        cv_json = temp_dir / f"{img_name}_cv.json"
        cv_clusters = temp_dir / f"{img_name}_cv_clusters_v5.json"
        ocr_json = temp_dir / f"{img_name}_ocr_v5.json"
        llm_viz = temp_dir / f"{img_name}_llm_viz.jpg"
        
        integrated = temp_dir / f"{img_name}_integrated.json"
        integrated_translated = temp_dir / f"{img_name}_integrated_translated.json"
        masked_img = temp_dir / f"{img_name}_masked.jpg"
        
        # 최종 결과물은 output에
        final_img = output_dir / f"{img_name}_final.jpg"
        
        log.append(f"📸 처리 중: {img_name}")
        
        # 기존 결과 확인
        if not force_reprocess and final_img.exists():
            log.append(f"  ✅ 기존 결과 발견: {final_img.name}")
            log.append(f"  ℹ️  force_reprocess=False이므로 기존 파일 사용")
            return str(final_img), "\n".join(log)
        
        # 1. Cloud Vision API (캐시 활용)
        if not cv_json.exists():
            log.append("  🔍 Cloud Vision API 호출...")
            result = subprocess.run(
                ["python3", "cloud_vision_api.py", image_path, str(cv_json)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"CV API 실패: {result.stderr}")
        else:
            log.append("  ✅ CV JSON 캐시 사용")
        
        # CV JSON이 비어있는지 확인 (텍스트 없는 이미지)
        with open(cv_json, 'r', encoding='utf-8') as f:
            cv_data = json.load(f)
            if not cv_data or len(cv_data) == 0:
                is_blank_image = True
                log.append("  ℹ️  텍스트 없는 이미지 - 원본 복사")
                # 원본을 output으로 복사
                import shutil
                final_output = output_dir / f"{img_name}_final.jpg"
                shutil.copy(image_path, final_output)
                return str(final_output), "\n".join(log)
        
        # 2. 클러스터링 V5
        if not cv_clusters.exists() or force_reprocess:
            log.append("  📦 클러스터링 V5...")
            result = subprocess.run(
                ["python3", "cluster_cv_v5.py", str(cv_json), str(cv_clusters)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"클러스터링 실패: {result.stderr}")
        else:
            log.append("  ✅ Clusters JSON 캐시 사용")
        
        # 시각화 (옵션)
        if not llm_viz.exists():
            subprocess.run(
                ["python3", "visualize_clusters_v5.py", image_path, str(cv_clusters), str(llm_viz)],
                capture_output=True, text=True, timeout=30
            )
        
        # 3. Gemini OCR
        if not ocr_json.exists() or force_reprocess:
            log.append("  🤖 Gemini OCR...")
            result = subprocess.run(
                ["python3", "gemini_ocr_v5.py", image_path, str(cv_clusters), str(ocr_json)],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise Exception(f"Gemini OCR 실패: {result.stderr}")
        else:
            log.append("  ✅ OCR JSON 캐시 사용")
        
        # 4. 텍스트 통합
        if not integrated.exists() or force_reprocess:
            log.append("  🔄 텍스트 통합...")
            result = subprocess.run(
                ["python3", "integrate_ocr.py", str(cv_clusters), str(ocr_json), str(integrated)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                raise Exception(f"통합 실패: {result.stderr}")
        else:
            log.append("  ✅ Integrated JSON 캐시 사용")
        
        # 5. Gemini Translation (재시도 로직 포함)
        if not integrated_translated.exists() or force_reprocess:
            log.append("  🌐 번역 중...")
            
            max_retries = 5
            translation_success = False
            
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        log.append(f"     재시도 {attempt}/{max_retries-1}...")
                        import time
                        time.sleep(3)  # 3초 대기
                    
                    result = subprocess.run(
                        ["python3", "translate_integrated.py", str(integrated), str(integrated_translated)],
                        capture_output=True, text=True, timeout=120
                    )
                    
                    if result.returncode == 0:
                        # 파일이 실제로 생성되었는지 확인
                        if integrated_translated.exists():
                            translation_success = True
                            break
                        else:
                            log.append(f"     ⚠️  번역 파일 미생성, 재시도...")
                    else:
                        error_msg = result.stderr[:200] if result.stderr else "Unknown error"
                        log.append(f"     ⚠️  번역 오류: {error_msg}")
                        
                except subprocess.TimeoutExpired:
                    log.append(f"     ⚠️  번역 타임아웃 (120초)")
                except Exception as e:
                    log.append(f"     ⚠️  번역 예외: {str(e)[:100]}")
            
            if not translation_success:
                raise Exception(f"번역 실패 (3회 시도 후)")
        else:
            log.append("  ✅ Translation JSON 캐시 사용")
        
        
        # 6. White Masking
        if not masked_img.exists() or force_reprocess:
            log.append("  🎨 White Masking...")
            result = subprocess.run(
                ["python3", "apply_masking.py", image_path, str(integrated), str(cv_clusters), str(masked_img)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"Masking 실패: {result.stderr}")
        else:
            log.append("  ✅ Masked 이미지 캐시 사용")
        
        # 7. Text Overlay (항상 최종 결과는 재생성)
        log.append("  📝 Text Overlay...")
        cmd = ["python3", "render_text_overlay.py", str(masked_img), str(integrated_translated), str(cv_clusters), str(final_img)]
        if font_path:
            cmd.append(font_path)
            
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise Exception(f"Text Overlay 실패: {result.stderr}")
        
        log.append(f"  ✅ 완료: {final_img.name}")
        log.append(f"  📁 중간 파일: temp_output/")
        log.append(f"  📁 최종 결과: output/")
        
        return str(final_img), "\n".join(log)
        
    except Exception as e:
        error_msg = f"  ❌ 오류: {str(e)}"
        log.append(error_msg)
        return None, "\n".join(log)


def batch_translate_images(
    image_files: List, 
    extra_prompt: str = "", 
    force_reprocess: bool = False,
    font_name: str = "기본 폰트",
    auto_detect_color: bool = False,
    progress=gr.Progress()
) -> tuple:
    """
    배치 이미지 처리 (Grid CV + 병렬 처리)
    
    Args:
        image_files: 업로드된 이미지 파일 리스트
        extra_prompt: 추가 번역 프롬프트
        progress: Gradio Progress tracker
    
    Returns:
        (최종 이미지 리스트, 로그 메시지, ZIP 파일 경로)
    """
    if not image_files or len(image_files) == 0:
        return None, "❌ 이미지를 업로드해주세요", None
    
    try:
        # 이미지 경로 추출
        image_paths = [img if isinstance(img, str) else img.name for img in image_files]
        n_images = len(image_paths)
        
        log = []
        log.append(f"{'='*60}")
        log.append(f"🎨 배치 번역 시작")
        log.append(f"{'='*60}")
        log.append(f"📁 총 {n_images}개 이미지")
        
        # 프롬프트 저장
        save_prompt(extra_prompt)
        
        # 폰트 경로 결정
        font_path = "fonts/Freesentation-5Medium.ttf"  # 기본값
        if font_name and font_name != "기본 폰트":
            # ttf 우선 확인
            p = Path(f"fonts/{font_name}.ttf")
            if p.exists():
                font_path = str(p)
            else:
                # otf 확인
                p = Path(f"fonts/{font_name}.otf")
                if p.exists():
                    font_path = str(p)
        
        log.append(f"🎨 폰트: {font_name} ({font_path})")
        
        # Step 1: Grid CV API 처리 (파일 크기 고려)
        progress(0.05, desc="Cloud Vision Grid 처리 중...")
        log.append(f"\n🔍 Step 1: Cloud Vision Grid API")
        
        # 캐싱 체크: 모든 이미지의 CV JSON이 있으면 건너뜀
        cv_json_exists = [(temp_dir / f"{Path(p).stem}_cv.json").exists() for p in image_paths]
        all_cv_exist = all(cv_json_exists)
        
        # 디버깅: 어떤 파일이 없는지 확인
        if not all_cv_exist:
            missing_count = sum(1 for exists in cv_json_exists if not exists)
            print(f"  ⚠️  CV JSON 누락: {missing_count}/{n_images}개 파일")
            # 첫 5개 누락 파일 출력
            for idx, (p, exists) in enumerate(zip(image_paths, cv_json_exists)):
                if not exists and idx < 5:
                    print(f"     - {Path(p).stem}_cv.json 없음")
        
        if all_cv_exist and not force_reprocess:
            print(f"  ✅ CV JSON 캐시 사용 (모든 파일 존재, {n_images}개)")
            log.append(f"  ✅ CV JSON 캐시 사용 (모든 파일 존재)")
            cv_results = []
            for img_path in image_paths:
                img_name = Path(img_path).stem
                cv_json_path = temp_dir / f"{img_name}_cv.json"
                with open(cv_json_path, 'r', encoding='utf-8') as f:
                    cv_results.append(json.load(f))
        else:
            # 파일 크기 계산 및 유연한 Grid 크기 결정
            file_sizes_mb = []
            total_size_mb = 0
            max_resolution = 0
            
            for img_path in image_paths:
                size_bytes = os.path.getsize(img_path)
                size_mb = size_bytes / (1024 * 1024)
                file_sizes_mb.append(size_mb)
                total_size_mb += size_mb
                
                # 개별 파일 크기 체크 (20MB 제한)
                if size_mb > 20:
                    log.append(f"  ⚠️  {Path(img_path).name}: {size_mb:.1f}MB (제한: 20MB)")
                
                # 해상도 확인 (75 megapixel 제한)
                import cv2
                img = cv2.imread(img_path)
                if img is not None:
                    h, w = img.shape[:2]
                    resolution = h * w
                    max_resolution = max(max_resolution, resolution)
            
            avg_size_mb = total_size_mb / n_images if n_images > 0 else 0
            
            # 해상도 경고
            MAX_PIXELS = 75_000_000  # 75 megapixels
            if max_resolution > MAX_PIXELS:
                log.append(f"  ⚠️  최대 해상도: {max_resolution:,} 픽셀 (제한: {MAX_PIXELS:,})")
                log.append(f"  ℹ️  CV API가 자동으로 리사이즈 처리합니다")
            
            # Cloud Vision API 제한:
            # - 개별 이미지: 20MB
            # - Request payload: 40MB (Grid 전체)
            # - 이미지 해상도: 75 megapixels
            
            # 40MB payload 제한에 맞게 배치 크기 계산
            MAX_PAYLOAD_MB = 38  # 안전 마진 (40MB - 2MB for overhead)
            
            # 평균 파일 크기로 최대 이미지 수 계산
            max_images_per_batch = int(MAX_PAYLOAD_MB / avg_size_mb) if avg_size_mb > 0 else 16
            max_images_per_batch = max(1, min(max_images_per_batch, 25))  # 1~25개 제한
            
            log.append(f"  📊 평균 파일 크기: {avg_size_mb:.2f}MB")
            log.append(f"  📐 배치당 최대: {max_images_per_batch}개 이미지 (40MB 제한)")
            
            # 배치 분할 필요 여부 확인
            if n_images > max_images_per_batch:
                num_batches = int(np.ceil(n_images / max_images_per_batch))
                log.append(f"  ⚠️  이미지가 많아 {num_batches}개 배치로 분할")
                
                # 배치별 처리
                all_cv_results = []
                for batch_idx in range(num_batches):
                    start_idx = batch_idx * max_images_per_batch
                    end_idx = min((batch_idx + 1) * max_images_per_batch, n_images)
                    batch_paths = image_paths[start_idx:end_idx]
                    batch_size = len(batch_paths)
                    
                    # 유연한 Grid 크기 계산 (정사각형이 아닐 수 있음)
                    # 가로를 더 길게 (5x4, 5x3 등)
                    grid_cols = int(np.ceil(np.sqrt(batch_size * 1.2)))  # 약간 넓게
                    grid_rows = int(np.ceil(batch_size / grid_cols))
                    
                    # 누적 진행률 표시 (이미지 1~25/160 형식)
                    log.append(f"  📦 배치 {batch_idx+1}/{num_batches}: 이미지 {start_idx+1}~{end_idx}/{n_images}, Grid {grid_rows}x{grid_cols}")
                    
                    
                    from cloud_vision_api import CloudVisionAPI
                    
                    # API 키 우선, 없으면 서비스 계정
        # 이미지 분류 (흑백 vs 컬러)
        standard_images = []
        color_images = []
        
        if auto_detect_color:
            progress(0.02, desc="이미지 분석 중...")
            log.append(f"\n🔍 이미지 분석 (흑백/컬러 자동 감지)")
            for img_path in image_paths:
                if is_grayscale(img_path):
                    standard_images.append(img_path)
                    # log.append(f"  📄 흑백(표준): {Path(img_path).name}")
                else:
                    color_images.append(img_path)
                    log.append(f"  🎨 컬러(AI 표지): {Path(img_path).name}")
        else:
            standard_images = image_paths
            
        log.append(f"📋 처리 계획: 표준 {len(standard_images)}개, AI 표지 {len(color_images)}개")
        
        # Step 1: Grid CV API 처리 (표준 이미지만)
        if standard_images:
            progress(0.05, desc="Cloud Vision Grid 처리 중...")
            log.append(f"\n🔍 Step 1: Cloud Vision Grid API (표준 이미지 {len(standard_images)}개)")
            
            # API 키 우선, 없으면 서비스 계정
            api_key = os.getenv("CLOUD_VISION_API_KEY")
            credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            
            if not api_key and not credentials_path:
                raise Exception("CLOUD_VISION_API_KEY 또는 GOOGLE_APPLICATION_CREDENTIALS 환경변수 필요")
            
            from cloud_vision_api import CloudVisionAPI
            cv_api = CloudVisionAPI(api_key=api_key, credentials_path=credentials_path)
            cv_results = cv_api.detect_text_grid(standard_images)
            
            if not cv_results:
                raise Exception("CV Grid 처리 실패")
        
            # 각 이미지별 CV JSON 저장 (temp_output에)
            for idx, (img_path, cv_boxes) in enumerate(zip(standard_images, cv_results)):
                img_name = Path(img_path).stem
                cv_json_path = temp_dir / f"{img_name}_cv.json"
                with open(cv_json_path, 'w', encoding='utf-8') as f:
                    json.dump(cv_boxes, f, ensure_ascii=False, indent=2)
            
            log.append(f"  ✅ CV JSON 생성 완료")
        
        # Step 2-7: 병렬 처리 (통합)
        progress(0.15, desc="파이프라인 병렬 처리 중...")
        log.append(f"\n🚀 Step 2-7: 병렬 파이프라인 실행")
        
        output_files = []
        completed_count = 0
        max_workers = min(5, n_images)  # 최대 5개 병렬
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {}
            
            # 표준 이미지 작업 제출
            for img_path in standard_images:
                future = executor.submit(process_single_image, img_path, extra_prompt, force_reprocess, font_path)
                future_to_path[future] = img_path
                
            # 컬러 이미지 작업 제출
            for img_path in color_images:
                future = executor.submit(process_cover_image_wrapper, img_path)
                future_to_path[future] = img_path
            
            # 완료되는 순서대로 결과 수집
            failed_images = []
            for future in as_completed(future_to_path):
                img_path = future_to_path[future]
                
                try:
                    final_img, img_log = future.result()
                    
                    log.append(f"\n{img_log}")
                    
                    if final_img:
                        output_files.append(final_img)
                        print(f"  ✅ 이미지 {completed_count+1}/{n_images} 완료: {Path(img_path).name}")
                    else:
                        failed_images.append(Path(img_path).name)
                        print(f"  ⚠️  이미지 {completed_count+1}/{n_images} 실패: {Path(img_path).name}")
                        
                except Exception as e:
                    failed_images.append(Path(img_path).name)
                    error_msg = str(e)[:200]  # 에러 메시지 200자 제한
                    print(f"  ❌ 이미지 {completed_count+1}/{n_images} 에러: {Path(img_path).name} - {error_msg}")
                    log.append(f"\n❌ {Path(img_path).name}: {error_msg}")
                
                # 각 이미지 처리 완료 시 (성공/실패 무관)
                completed_count += 1
                progress((0.15 + (0.75 * completed_count / n_images)), 
                        desc=f"처리 중 ({completed_count}/{n_images})...")
        
        progress(1.0, desc="완료!")
        
        # 완료 메시지
        log.append(f"\n✅ 완료: {len(output_files)}/{n_images}개 성공")
        if failed_images:
            log.append(f"⚠️  실패: {len(failed_images)}개")
        log.append(f"{'='*60}")
        
        return output_files, "\n".join(log)
    
    except Exception as e:
        import traceback
        error_msg = f"\n❌ 배치 처리 오류: {e}\n{traceback.format_exc()}"
        log.append(error_msg)
        return None, "\n".join(log)


# Gradio UI 구성
with gr.Blocks(title="Comic Translator V5", theme=gr.themes.Soft()) as app:
    gr.Markdown("""
    # 🎨 Comic Translator V5
    V5 클러스터링 • Grid CV • 병렬 처리 • AI 표지 번역
    """)
    
    # 상단: API 설정 + 폰트 선택
    with gr.Row():
        with gr.Accordion("🔑 API 설정", open=False):
            gr.Markdown("GEMINI_API_KEY와 CLOUD_VISION_API_KEY를 .env에 설정")
        
        font_dropdown = gr.Dropdown(
            choices=get_available_fonts(),
            label="🎨 폰트 선택",
            value="Freesentation-5Medium" if "Freesentation-5Medium" in get_available_fonts() else (get_available_fonts()[0] if get_available_fonts() else "기본 폰트"),
            scale=1
        )
    
    # 메인: 2-Column
    with gr.Row():
        # 좌측: 배치 번역
        with gr.Column():
            gr.Markdown("### 🚀 배치 번역 (표준 파이프라인)")
            
            batch_images = gr.File(
                label="📤 이미지 업로드 (복수 가능)",
                file_count="multiple",
                file_types=["image"],
                height=200
            )
            
            batch_prompt = gr.Textbox(
                label="추가 번역 프롬프트 (선택)",
                placeholder="예: 반말을 사용하고, 소년 만화 스타일로 박진감 넘치게 번역해줘",
                lines=2,
                value=load_last_prompt()
            )
            
            force_reprocess = gr.Checkbox(
                label="🔄 강제 재처리",
                value=False
            )
            
            auto_detect_color = gr.Checkbox(
                label="🎨 컬러 이미지 자동 감지 (Gemini 3 Pro 사용)",
                value=False,
                info="체크하면 컬러 이미지는 자동으로 AI 표지 번역 모델로 처리합니다."
            )
            
            batch_btn = gr.Button("🚀 배치 번역 시작", variant="primary", size="lg")
            batch_status = gr.Textbox(label="📊 진행 상황", lines=10)
            batch_gallery = gr.Gallery(label="📸 결과 이미지", columns=3, height=400)
        
        # 우측: AI 표지 번역
        with gr.Column():
            gr.Markdown("### 🎨 AI 표지 번역 (Gemini 3 Pro)")
            
            cover_images = gr.File(
                label="📤 이미지 업로드 (복수 가능)",
                file_count="multiple",
                file_types=["image"],
                height=200
            )
            
            gr.Markdown("""
            **사용 시기:**
            - 📖 만화책 표지
            - 🎨 복잡한 레이아웃
            - 🌈 색상이 많은 디자인
            
            > ⚠️ 이미지 생성 실패 시 텍스트 가이드 제공
            """)
            
            cover_btn = gr.Button("🎨 AI 번역 시작", variant="primary", size="lg")
            cover_status = gr.Textbox(label="📊 진행 상황", lines=10)
            cover_gallery = gr.Gallery(label="📸 결과 이미지", columns=3, height=400)
    
    # 하단: ZIP 압축
    with gr.Row():
        zip_btn = gr.Button("📦 결과 압축하기", size="lg", scale=3)
        zip_download = gr.File(label="📥 ZIP 다운로드", scale=2)
    
    # 이벤트 연결
    batch_btn.click(
        fn=batch_translate_images,
        inputs=[batch_images, batch_prompt, force_reprocess, font_dropdown, auto_detect_color],
        outputs=[batch_gallery, batch_status]
    )
    
    cover_btn.click(
        fn=translate_cover_batch,
        inputs=[cover_images],
        outputs=[cover_gallery, cover_status]
    )
    
    zip_btn.click(
        fn=create_output_zip,
        outputs=[zip_download]
    )
    
    gr.Markdown("""
    ---
    ### 📋 파이프라인:
    **배치 번역:** CV Grid → Cluster → OCR → 번역 → Masking → Overlay  
    **AI 번역:** Gemini 3 Pro 직접 처리
    
    ### 📁 폴더:
    - **temp_output/**: 중간 파일
    - **output/**: 최종 결과물
    """)


if __name__ == "__main__":
    # 서버 실행
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True
    )
