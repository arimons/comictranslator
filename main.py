#!/usr/bin/env python3
"""
만화 번역 파이프라인 - Gradio UI

현재 표준 파이프라인:
1. Cloud Vision API → *_cv.json
2. Clustering V5 → *_cv_clusters_v5.json
3. Gemini OCR + 번역 + integrated JSON 생성 → *_integrated.json
4. White Masking → *_masked.jpg
5. Text Overlay → *_final.jpg
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
import time

# 환경 변수 로드
load_dotenv()

# ⏱️ 타이밍 추적 함수
class TimingTracker:
    """각 단계의 소요 시간을 추적"""
    def __init__(self):
        self.start_time = time.time()
        self.last_time = self.start_time
        self.timings = {}

    def checkpoint(self, step_name: str) -> str:
        """현재 시점까지의 소요 시간 기록"""
        current_time = time.time()
        elapsed = current_time - self.last_time
        total_elapsed = current_time - self.start_time

        self.timings[step_name] = elapsed
        self.last_time = current_time

        return f"⏱️ {step_name}: {elapsed:.1f}s (전체: {total_elapsed:.1f}s)"

    def get_summary(self) -> str:
        """전체 타이밍 요약"""
        total = sum(self.timings.values())
        summary = "\n📊 단계별 소요 시간:\n"
        for step, elapsed in self.timings.items():
            percentage = (elapsed / total * 100) if total > 0 else 0
            summary += f"  {step}: {elapsed:.1f}s ({percentage:.1f}%)\n"
        summary += f"  전체: {total:.1f}s"
        return summary

# 전역 변수
temp_dir = Path("temp_output")  # 중간 파일 (JSON, masked)
output_dir = Path("output")  # 최종 결과물 (final.jpg, ZIP)
temp_dir.mkdir(exist_ok=True)
output_dir.mkdir(exist_ok=True)
prompt_history_file = Path("last_prompt.txt")


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
    for suffix in ['_cv.json', '_cv_clusters_v5.json', '_integrated.json', '_masked.jpg']:
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


def process_cover_image_wrapper(image_path: str, output_subfolder: str = None, force_reprocess: bool = False, selected_model: str = "gemini-3.1-flash-image-preview") -> tuple:
    """
    translate_cover를 process_single_image와 동일한 인터페이스로 래핑
    """
    from pipeline.translate_cover import translate_cover

    img_name = Path(image_path).stem
    
    # 하위 폴더 처리
    target_dir = output_dir
    if output_subfolder:
        target_dir = output_dir / output_subfolder
        target_dir.mkdir(parents=True, exist_ok=True)
        
    output_path = target_dir / f"{img_name}_ai.jpg"
    log = []
    
    log.append(f"🎨 AI 표지 번역 (컬러 감지): {img_name}")
    
    # 캐시 확인
    if output_path.exists() and not force_reprocess:
        log.append(f"  ✅ 기존 파일 발견: {output_path.name}")
        log.append(f"  ℹ️  force_reprocess=False이므로 기존 파일 사용 (스킵)")
        return str(output_path), "\n".join(log)
    
    try:
        success = translate_cover(str(image_path), str(output_path), model_name=selected_model)
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
    """Fonts/ 폴더에서 사용 가능한 폰트 목록 반환"""
    fonts_dir = Path("Fonts")
    if not fonts_dir.exists():
        return ["기본 폰트"]
    
    fonts = []
    for font_file in sorted(fonts_dir.glob("*.ttf")):
        fonts.append(font_file.stem)
    for font_file in sorted(fonts_dir.glob("*.otf")):
        fonts.append(font_file.stem)
    
    return fonts if fonts else ["기본 폰트"]


def translate_cover_batch(image_files, output_subfolder_input="", selected_model="gemini-3.1-flash-image-preview", progress=gr.Progress()):
    """
    만화책 이미지를 Gemini Image Preview 모델로 AI 번역 (병렬 처리 지원)

    Args:
        image_files: 업로드된 이미지 파일들
        output_subfolder_input: 출력 폴더명 (예: "ai_translated")
        selected_model: AI 번역에 사용할 모델명 (기본: gemini-3.1-flash-image-preview)
        progress: Gradio progress bar

    Returns:
        (results, log_text)
    """
    if not image_files:
        return [], "이미지를 업로드해주세요"

    # 이미지 경로 추출
    image_paths = [f.name if hasattr(f, 'name') else f for f in image_files]
    n_images = len(image_paths)

    output_subfolder = output_subfolder_input if output_subfolder_input and output_subfolder_input.strip() else ""
    if not output_subfolder and image_paths:
        import datetime
        current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_subfolder = f"auto_{current_time}"

    missing_images = []
    cached_files = []

    for img_path in image_paths:
        img_name = Path(img_path).stem
        target_dir = output_dir
        if output_subfolder:
            target_dir = output_dir / str(output_subfolder)
            target_dir.mkdir(parents=True, exist_ok=True)

        output_path_jpg = target_dir / f"{img_name}_ai.jpg"
        output_path_png = target_dir / f"{img_name}_ai.png"

        if output_path_jpg.exists():
            cached_files.append((img_name, str(output_path_jpg)))
        elif output_path_png.exists():
            cached_files.append((img_name, str(output_path_png)))
        else:
            missing_images.append(img_path)
    
    results = []
    log = []
    
    if missing_images:
        from pipeline.translate_cover import translate_cover

        log.append(f"🚀 고성능 병렬 번역 모드 (모델: {selected_model})\n")
        
        # 캐시된 파일 정보
        if cached_files:
            log.append(f"✅ 캐시 파일: {len(cached_files)}개 (스킵)")
            for img_name, output_path in cached_files:
                results.append(output_path)
                log.append(f"   - {img_name}")
            log.append("")
        
        log.append(f"📦 새로운 이미지: {len(missing_images)}개\n")
        
        # 병렬 처리 함수
        def process_ai_single(img_path):
            img_name = Path(img_path).stem
            target_dir = output_dir
            if output_subfolder:
                target_dir = output_dir / str(output_subfolder)
                target_dir.mkdir(parents=True, exist_ok=True)

            output_path = target_dir / f"{img_name}_ai.jpg"

            try:
                # 타임아웃 300초 적용된 translate_cover 호출
                success = translate_cover(str(img_path), str(output_path), model_name=selected_model)
                return (img_name, success, str(output_path) if success else None)
            except Exception as e:
                return (img_name, False, None)

        # 병렬 실행 (모델에 따라 워커 수 조정)
        if "flash" in selected_model:
            max_workers = min(10, len(missing_images))
        else:
            max_workers = min(3, len(missing_images))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_ai_single, img_path) for img_path in missing_images]

            for future in as_completed(futures):
                img_name, success, output_path = future.result()

                log.append(f"🎨 {img_name}")
                if success and output_path:
                    results.append(output_path)
                    log.append("  ✅ 완료")
                else:
                    log.append("  ⚠️ 실패 (텍스트 가이드 확인)")

                completed_count = len(results) + (len(cached_files) if 'cached_files' in locals() else 0)
                progress(completed_count / n_images,
                        desc=f"처리 중 ({completed_count}/{n_images})...")
    
    # 모든 이미지가 캐시된 경우
    if not missing_images and cached_files:
        log.append(f"✅ 모든 이미지가 이미 생성됨\n")
        results = [output_path for _, output_path in cached_files]
    
    log.append(f"\n✅ 완료: {len(results)}/{n_images}개 (캐시 포함)")
    return results, "\n".join(log)
    
    # 모든 이미지가 캐시된 경우
    if not missing_images and cached_files:
        log.append(f"✅ 모든 이미지가 이미 생성됨\n")
        results = [output_path for _, output_path in cached_files]
    
    log.append(f"\n✅ 완료: {len(results)}/{n_images}개 (캐시 포함)")
    return results, "\n".join(log)


def create_output_zip():
    """output/ 폴더의 모든 이미지를 ZIP으로 압축 (하위 폴더 포함)"""
    output_files = list(output_dir.rglob("*.jpg"))
    if not output_files:
        return None
    
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = output_dir / f"translated_{timestamp}.zip"
    
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for f in output_files:
            # ZIP 내 경로를 output_dir 기준 상대 경로로 저장하여 폴더 구조 유지
            arcname = f.relative_to(output_dir)
            zf.write(f, arcname)
    
    return str(zip_path)


def process_single_image(image_path: str, extra_prompt: str = "", force_reprocess: bool = False, font_path: str = None, font_size: int = 33, output_subfolder: str = None, skip_cv: bool = False) -> tuple:
    """
    단일 이미지 처리 (병렬 실행용)

    Args:
        skip_cv: 하위 호환용 인자. 현재 표준 파이프라인은 cluster hint가 필요하므로
            캐시가 없으면 Cloud Vision/cluster를 생성한다.

    Returns:
        (final_image_path, log_text)
    """
    timing = TimingTracker()

    temp_dir = Path("temp_output")
    if output_subfolder:
        temp_dir = temp_dir / output_subfolder
        temp_dir.mkdir(parents=True, exist_ok=True)
    # output_dir = Path("output") # 전역 변수 사용

    img_name = Path(image_path).stem
    log = []
    log.append(f"📸 {img_name} 처리 시작")

    # 빈 이미지 플래그 (텍스트 없는 이미지)
    is_blank_image = False

    try:
        # 출력 경로 설정
        # 중간 파일들은 temp_output에
        integrated = temp_dir / f"{img_name}_integrated.json"
        masked_img = temp_dir / f"{img_name}_masked.jpg"
        cv_clusters = temp_dir / f"{img_name}_cv_clusters_v5.json"
        
        # 최종 결과물은 output에 (하위 폴더 지원)
        target_dir = output_dir
        if output_subfolder:
            target_dir = output_dir / output_subfolder
            target_dir.mkdir(parents=True, exist_ok=True)
            
        final_img = target_dir / f"{img_name}_final.jpg"
        
        log.append(f"📸 처리 중: {img_name}")
        
        # 기존 결과 확인
        if not force_reprocess and final_img.exists():
            log.append(f"  ✅ 기존 결과 발견: {final_img.name}")
            log.append(f"  ℹ️  force_reprocess=False이므로 기존 파일 사용")
            return str(final_img), "\n".join(log)
            
        # 1. OCR/번역/integrated JSON 생성
        log.append("\n🤖 Step 1: OCR/Translation/Integrated JSON")
        if not integrated.exists() or force_reprocess:
            log.append("  🔍 CV cluster hint 생성 후 Gemini 통합 처리")
            
            # Step 1: CV API (Bounding Box 추출기)
            json_file_path = temp_dir / f"{image_path.stem}_cv.json"
            
            if not json_file_path.exists() or force_reprocess:
                from pipeline.cloud_vision_api import CloudVisionAPI
                cv_api = CloudVisionAPI() # Use user's dedicated Vision Credentials from .env
                cv_data = cv_api.detect_text(str(image_path))
                if not cv_data:
                    raise Exception("Cloud Vision API 호출 실패 또는 텍스트 없음")
                with open(json_file_path, 'w', encoding='utf-8') as f:
                    json.dump(cv_data, f, ensure_ascii=False)
            
            # Step 2: CV Clustering (Gemini 위치 힌트)
            if not cv_clusters.exists() or force_reprocess:
                from pipeline.cluster_cv_v5 import process_cv_clusters, format_json_output
                clusters_result = process_cv_clusters(str(json_file_path), str(cv_clusters))
                with open(cv_clusters, 'w', encoding='utf-8') as f:
                    f.write(format_json_output(clusters_result))

            # Step 3: Gemini OCR + 번역 + integrated JSON 생성
            ocr_script = Path(__file__).parent / "pipeline" / "gemini_ocr_translate_v5.py"
            cmd = ["python3", str(ocr_script), str(image_path), str(cv_clusters), str(integrated)]
            if extra_prompt:
                cmd.append(extra_prompt)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise Exception(f"Gemini 통합 OCR/번역 실패: {result.stderr}\n\nSTDOUT: {result.stdout}")
            
            log.append("  ✅ 완료")
        else:
            log.append("  ✅ 캐시 사용")
        log.append(f"  {timing.checkpoint('Step 1: OCR/Translation/Integrated')}")

        
        # 2. White Masking
        log.append("\n🎨 Step 2: Masking")
        if not masked_img.exists() or force_reprocess:
            log.append("  🖌️ 마스킹 중...")
            masking_script = Path(__file__).parent / "pipeline" / "apply_masking.py"
            result = subprocess.run(
                ["python3", str(masking_script), image_path, str(integrated), str(cv_clusters), str(masked_img)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"Masking 실패: {result.stderr}")
            log.append("  ✅ 완료")
        else:
            log.append("  ✅ 캐시 사용")
        log.append(f"  {timing.checkpoint('Step 2: Masking')}")

        # 3. Text Overlay (항상 최종 결과는 재생성)
        log.append("\n✍️ Step 3: Text Overlay")
        log.append("  🖋️ 텍스트 렌더링 중...")
        render_script = Path(__file__).parent / "pipeline" / "render_text_overlay.py"
        cmd = ["python3", str(render_script), str(masked_img), str(integrated), str(cv_clusters), str(final_img)]
        if font_path:
            cmd.append(font_path)
        if font_size != 33:  # 기본값이 아니면 추가
            cmd.append(str(font_size))

        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise Exception(f"Text Overlay 실패: {result.stderr}")
        log.append("  ✅ 완료")
        log.append(f"  {timing.checkpoint('Step 3: Text Overlay')}")

        # 타이밍 요약 추가
        log.append("\n" + timing.get_summary())

        log.append(f"\n✅ 완료: {final_img.name}")
        log.append(f"📁 최종 결과: {final_img}")

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
    font_size: int = 33,
    auto_detect_color: bool = False,
    output_subfolder: str = "",
    keep_cache: bool = False,
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
        
        # Temp Dir 설정 (Subfolder 지원)
        current_temp_dir = temp_dir
        if output_subfolder:
            current_temp_dir = temp_dir / output_subfolder
            current_temp_dir.mkdir(parents=True, exist_ok=True)
        
        log = []
        log.append(f"{'='*60}")
        log.append(f"🎨 배치 번역 시작")
        log.append(f"{'='*60}")
        log.append(f"📁 총 {n_images}개 이미지")
        
        # 프롬프트 저장
        save_prompt(extra_prompt)
        
        # 폰트 경로 결정
        font_path = "Fonts/Freesentation-5Medium.ttf"  # 기본값
        if font_name and font_name != "기본 폰트":
            # ttf 우선 확인
            p = Path(f"Fonts/{font_name}.ttf")
            if p.exists():
                font_path = str(p)
            else:
                # otf 확인
                p = Path(f"Fonts/{font_name}.otf")
                if p.exists():
                    font_path = str(p)
        
        log.append(f"🎨 폰트: {font_name} ({font_path})")
        
        # 이미지 분류 (흑백 vs 컬러)
        standard_images = []
        color_images = []
        
        if auto_detect_color:
            progress(0.02, desc="이미지 분석 중...")
            log.append(f"\n🔍 이미지 분석 (흑백/컬러 자동 감지)")
            for img_path in image_paths:
                if is_grayscale(img_path):
                    standard_images.append(img_path)
                else:
                    color_images.append(img_path)
                    log.append(f"  🎨 컬러(AI 표지): {Path(img_path).name}")
        else:
            standard_images = image_paths
            
        if output_subfolder:
            log.append(f"📂 출력 폴더: output/{output_subfolder}")
            
        log.append(f"📋 처리 계획: 표준 {len(standard_images)}개, AI 표지 {len(color_images)}개")
        
        # Step 1: 개별/병렬 처리 파이프라인 준비
        if standard_images:
            log.append(f"\n🔍 Step 1: 표준 파이프라인 병렬 처리 ({len(standard_images)}개)")
        
        # Step 2-7: 병렬 처리 (통합)
        progress(0.15, desc="파이프라인 병렬 처리 중...")
        log.append(f"\n🚀 Step 2-7: 병렬 파이프라인 실행")
        
        output_files = []
        completed_count = 0
        max_workers = min(20, n_images)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {}
            
            # 표준 이미지 작업 제출
            for img_path in standard_images:
                future = executor.submit(process_single_image, img_path, extra_prompt, force_reprocess, font_path, font_size, output_subfolder, skip_cv=True)
                future_to_path[future] = img_path
                
            # 컬러 이미지 작업 제출
            for img_path in color_images:
                # 표준 파이프라인에서는 현재 UI에 별도의 모델 선택이 없으므로 기본값(3.1-flash) 사용
                future = executor.submit(process_cover_image_wrapper, img_path, output_subfolder, force_reprocess, "gemini-3.1-flash-image-preview")
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
            
        # 캐시 정리 (keep_cache가 False일 때)
        if not keep_cache:
            log.append(f"\n🧹 캐시 정리 중...")
            deleted_count = 0
            for img_path in image_paths:
                img_name = Path(img_path).stem
                # 삭제할 중간 파일 패턴
                patterns = [
                    f"{img_name}_cv.json",
                    f"{img_name}_cv_clusters_v5.json",
                    f"{img_name}_integrated.json",
                    f"{img_name}_masked.jpg",
                    f"{img_name}_llm_viz.jpg"
                ]
                
                for pattern in patterns:
                    f_path = temp_dir / pattern
                    if f_path.exists():
                        try:
                            f_path.unlink()
                            deleted_count += 1
                        except Exception:
                            pass
            log.append(f"  🗑️ {deleted_count}개 중간 파일 삭제 완료")
        else:
            log.append(f"\n🗄️ 캐시 파일 유지됨 (디버깅용)")
            
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
    
    # 상단: 출력 폴더 + 폰트 설정
    with gr.Row():
        output_subfolder_input = gr.Textbox(
            label="📂 출력 폴더명 (선택)",
            placeholder="예: MyManga_Vol1 (비워두면 output/에 저장)",
            lines=1,
            scale=2
        )
        
        font_dropdown = gr.Dropdown(
            choices=get_available_fonts(),
            label="🎨 폰트 선택",
            value="Freesentation-5Medium" if "Freesentation-5Medium" in get_available_fonts() else (get_available_fonts()[0] if get_available_fonts() else "기본 폰트"),
            scale=1
        )
        
        font_size_slider = gr.Slider(
            minimum=24,
            maximum=42,
            value=33,
            step=1,
            label="🔤 폰트 크기",
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
            
            keep_cache = gr.Checkbox(
                label="🗄️ 캐시 파일 유지 (디버깅용)",
                value=False,
                info="체크하면 temp_output/ 폴더의 중간 파일들을 삭제하지 않습니다."
            )
            
            batch_btn = gr.Button("🚀 배치 번역 시작", variant="primary", size="lg")
            batch_status = gr.Textbox(label="📊 진행 상황", lines=10)
            batch_gallery = gr.Gallery(label="📸 결과 이미지", columns=3, height=400)
        
        # 우측: AI 표지 번역
        with gr.Column():
            gr.Markdown("### 🎨 AI 번역")

            cover_images = gr.File(
                label="📤 이미지 업로드 (복수 가능)",
                file_count="multiple",
                file_types=["image"],
                height=200
            )

            # 모델 선택
            ai_model_dropdown = gr.Dropdown(
                choices=[
                    "gemini-3.1-flash-image-preview",
                    "gemini-3-pro-image-preview"
                ],
                label="🤖 AI 모델 선택",
                value="gemini-3.1-flash-image-preview",
                info="3.1-flash (나노바나나2) 모델은 속도가 빠르고 할당량이 넉넉합니다."
            )
            
            gr.Markdown("""
            **사용 시기:**
            - 📖 만화책 표지
            - 🎨 복잡한 레이아웃
            - 🌈 색상이 많은 디자인
            
            **특징:**
            - 🚀 고성능 병렬 처리 적용
            - 🛡️ 자동 Rate Limit 관리
            
            > ⚠️ 이미지 직접 생성 실패 시 텍스트 번역 가이드가 제공됩니다.
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
        inputs=[batch_images, batch_prompt, force_reprocess, font_dropdown, font_size_slider, auto_detect_color, output_subfolder_input, keep_cache],
        outputs=[batch_gallery, batch_status]
    )
    
    cover_btn.click(
        fn=translate_cover_batch,
        inputs=[cover_images, output_subfolder_input, ai_model_dropdown],
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
    **AI 번역:** Gemini 3.1 Flash / 3 Pro 직접 처리 (병렬)
    
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
