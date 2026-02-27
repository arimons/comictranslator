#!/usr/bin/env python3
"""
만화 번역 파이프라인 - Gradio UI (통합 버전)

Grid 처리 + V5 파이프라인 통합:
1. CV API (Grid 처리) → cv.json
2. 클러스터링 V5 (병렬) → cv_cluster_v5.json
3. Gemini OCR + 번역 + 통합 (병렬) → ocr_v5.json + integrated.json
   (integrated.json에 text_translated 필드 포함)
4. White Masking (병렬) → _masked.jpg
5. Text Overlay (병렬) → _final.jpg
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


def process_cover_image_wrapper(image_path: str, output_subfolder: str = None, force_reprocess: bool = False) -> tuple:
    """
    translate_cover를 process_single_image와 동일한 인터페이스로 래핑
    """
    from translate_cover import translate_cover
    
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


def translate_cover_batch(image_files, output_subfolder_input="", use_batch_api=True, selected_model="gemini-3-pro-image-preview", progress=gr.Progress()):
    """
    만화책 이미지를 Gemini Image Preview 모델로 AI 번역 (Batch API 지원)

    Args:
        image_files: 업로드된 이미지 파일들
        output_subfolder_input: 출력 폴더명 (예: "ai_translated")
        use_batch_api: True면 Batch API 사용 (여러 이미지 동시), False면 개별 처리
        selected_model: AI 번역에 사용할 모델명 (gemini-3-pro-image-preview 또는 gemini-3.1-flash-image-preview)
        progress: Gradio progress bar

    Returns:
        (results, log_text)
    """
    if not image_files:
        return [], "이미지를 업로드해주세요"

    # 이미지 경로 추출
    image_paths = [f.name if hasattr(f, 'name') else f for f in image_files]
    n_images = len(image_paths)

    # 캐시 확인: 생성된 파일이 있는지 미리 체크
    # 입력값이 비어있을 경우, 첫 번째 업로드된 파일의 부모 폴더명을 기본값으로 사용
    output_subfolder = output_subfolder_input if output_subfolder_input and output_subfolder_input.strip() else ""
    if not output_subfolder and image_paths:
        try:
            # Gradio에서 넘어온 임시 경로나 실제 경로에서 상위 폴더 이름 추출 시도
            # macOS에서 업로드 시, 브라우저가 제공하는 원본 경로 혹은 임시 폴더의 상위 폴더를 찾음
            # 단, Gradio의 특성상 /private/var/folders/... 같은 임시 경로일 수 있어 완벽하지 않을 수 있음
            first_path = Path(image_paths[0])
            # 임시 파일 경로를 회피하고 원본의 디렉토리 구조를 알기 힘들 때를 대비해, 
            # 단순히 fallback 으로 'auto_translated' 등을 줄 수도 있으나, 최대한 이름을 따옴
            parent_name = first_path.parent.name
            if parent_name and parent_name not in ('tmp', 'graido', '', '/'):
                output_subfolder = parent_name
            else:
                output_subfolder = "ai_translated"
        except:
            output_subfolder = "ai_translated"

    missing_images = []
    cached_files = []

    for img_path in image_paths:
        img_name = Path(img_path).stem
        target_dir = output_dir
        if output_subfolder:
            # 문자열로 변환 후 Path로 결합
            target_dir = output_dir / str(output_subfolder)
            target_dir.mkdir(parents=True, exist_ok=True) # 폴더가 없으면 에러가 날 수 있으니 미리 생성

        # 결과물이 jpg 거나 png 일 수 있으므로 둘 다 확인
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
    
    # Batch API 사용 (여러 이미지를 한 번에 처리)
    if use_batch_api and missing_images:
        from gemini_batch_api import GeminiBatchProcessor
        import os
        
        log.append("🚀 Batch API 모드 (여러 이미지 동시 처리)\n")
        
        # 캐시된 파일 정보
        if cached_files:
            log.append(f"✅ 캐시 파일: {len(cached_files)}개 (스킵)")
            for img_name, output_path in cached_files:
                results.append(output_path)
                log.append(f"   - {img_name}")
            log.append("")
        
        try:
            # API 키 확인
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                return results, "\n".join(log) + "\n❌ GEMINI_API_KEY 환경변수가 설정되지 않았습니다."
            
            log.append(f"📦 새로운 이미지: {len(missing_images)}개")
            log.append(f"💰 예상 비용: 50% 절감\n")
            
            # 모델 환경에 따른 Rate Limit 및 병렬 워커 설정
            if selected_model == "gemini-3.1-flash-image-preview":
                # Flash API는 더 여유롭고 빠르지만, 너무 한 번에 던지면 구글 서버에서 생성 시 504 Deadline 에러 발생.
                # 사용자 요청에 의해 다시 8개로 테스트 (429 에러가 안나는 한계까지)
                max_workers_count = 10
                rpm_limit_val = 60
                log.append(f"⚡ Flash 모델: {max_workers_count}워커, RPM {rpm_limit_val} 허용 (공격적 세팅)")
            else:
                # Pro Preview 모델 (엄격한 Quota 관리)
                max_workers_count = 3
                rpm_limit_val = 10
                log.append(f"🛡️ Pro 프리뷰: {max_workers_count}워커, RPM {rpm_limit_val} 권장 제한")

            progress(0.1, desc="배치 처리 준비 중...")
            processor = GeminiBatchProcessor(
                api_key=api_key, 
                max_workers=max_workers_count, 
                rpm_limit=rpm_limit_val, 
                model_name=selected_model
            )
            
            # 결과 저장 폴더 설정
            save_dir = output_dir
            if output_subfolder:
                save_dir = output_dir / str(output_subfolder)
            save_dir.mkdir(parents=True, exist_ok=True)

            # 배치 작업 실행
            batch_result = processor.create_batch_job(
                image_paths=missing_images,
                prompt="""이 일본 만화책 이미지를 한국어 가로쓰기로 번역된 새로운 이미지로 생성해주세요.

요구사항:
1. 모든 일본어 텍스트를 자연스러운 한국어로 번역
   - 제목, 작가명, 출판사, 캐치프레이즈, 말풍선 등 모든 텍스트
2. 세로쓰기 절대 금지 (가장 중요)
   - 한글은 무조건 좌에서 우로 읽는 **가로쓰기**로만 배치하세요.
   - 원본이 세로로 긴 2줄 이상의 문장이거나 세로로 좁고 긴 말풍선이더라도, 절대 세로쓰기를 유지하지 마세요.
   - 세로로 긴 공간이라면, 글자 크기를 약간 줄이고 가로 길이에 맞춰 여러 줄로 줄바꿈(Word Wrap)하여 기입하세요.
3. 원본의 보존 및 자연스런 어우러짐 
   - 디자인, 레이아웃, 구성
   - 폰트 스타일, 텍스트 색상 및 효과
   - 텍스트 뒤의 배경 일러스트와 캐릭터 훼손 금지
4. 고품질 출력 (원본과 동일한 해상도)

번역된 이미지를 생성해주세요.""",
                output_dir=str(save_dir),
                job_name="ai_manga_translation_batch"
            )
            
            if batch_result.get('success'):
                # 디버깅: 저장 전 확인
                batch_results = batch_result.get('results', [])
                has_image_count = sum(1 for r in batch_results if r.get('has_image') and r.get('image_data'))
                has_text_count = sum(1 for r in batch_results if r.get('text'))

                log.append(f"\n📊 저장 전 확인:")
                log.append(f"   총 결과: {len(batch_results)}개")
                log.append(f"   - 이미지 데이터 포함: {has_image_count}개")
                log.append(f"   - 텍스트만: {has_text_count}개")
                log.append(f"   저장 경로: {save_dir}")

                saved_files = processor.save_results(batch_result, str(save_dir))
                results.extend(saved_files)

                log.append(f"\n✅ Batch 처리 완료!")
                log.append(f"   🖼️ {len(saved_files)}개 파일 저장됨")
                log.append(f"   📁 저장 위치: {save_dir}")

                # 재시도 가능한 이미지 정보
                stats = batch_result.get('stats', {})
                if stats.get('retryable', 0) > 0:
                    log.append("")
                    log.append(f"⏳ 재시도 가능한 이미지: {stats['retryable']}개")
                    log.append("   (500 서버 오류, 429 Rate Limit, 503 서비스 불가 등)")
                    log.append("   💡 체크박스를 해제하고 다시 시도하면 자동으로 재시도됩니다")

                    # 재시도 가능한 이미지 목록
                    retryable_images = [r for r in batch_result.get('results', [])
                                       if r.get('retryable')]
                    if retryable_images:
                        log.append("")
                        log.append("   재시도 가능 목록:")
                        for img in retryable_images[:5]:  # 처음 5개만 표시
                            log.append(f"     - {img['img_name']}: {img.get('error_code', 'Unknown')}")
                        if len(retryable_images) > 5:
                            log.append(f"     ... 외 {len(retryable_images) - 5}개")

                progress(1.0, desc="완료!")
            else:
                log.append(f"❌ Batch 처리 실패: {batch_result.get('error')}")
        
        except Exception as e:
            log.append(f"❌ Batch API 오류: {str(e)}")
            log.append(f"\n💡 개별 처리로 재시도하려면 체크박스를 해제하세요")
    
    # 개별 처리 모드 (기존 방식)
    elif missing_images:
        from translate_cover import translate_cover
        
        log.append("🎨 개별 처리 모드 (순차 처리)\n")
        
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
                success = translate_cover(str(img_path), str(output_path))
                return (img_name, success, str(output_path) if success else None)
            except Exception as e:
                return (img_name, False, None)

        # 병렬 실행
        completed_count = 0
        max_workers = min(5, len(missing_images))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_ai_single, img_path) for img_path in missing_images]

            for future in as_completed(futures):
                img_name, success, output_path = future.result()

                log.append(f"🎨 {img_name}")
                if success and output_path:
                    results.append(output_path)
                    log.append("  ✅ 완료")
                else:
                    log.append("  ⚠️ 실패 또는 텍스트 가이드 생성")

                completed_count += 1
                progress((len(cached_files) + completed_count) / n_images,
                        desc=f"처리 중 ({len(cached_files) + completed_count}/{n_images})...")
    
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
        skip_cv: True면 Cloud Vision API 호출 생략 (이미 Grid로 처리된 경우)

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
        cv_json = temp_dir / f"{img_name}_cv.json"
        cv_clusters = temp_dir / f"{img_name}_cv_clusters_v5.json"
        ocr_json = temp_dir / f"{img_name}_ocr_v5.json"
        llm_viz = temp_dir / f"{img_name}_llm_viz.jpg"
        
        integrated = temp_dir / f"{img_name}_integrated.json"
        masked_img = temp_dir / f"{img_name}_masked.jpg"
        
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
        
        # 1. Cloud Vision API (캐시 활용)
        log.append("\n📦 Step 1: Cloud Vision API")
        
        # skip_cv=True면 CV 호출 생략 (Grid로 이미 처리됨)
        # 단, 파일이 없으면 에러
        if skip_cv:
            if not cv_json.exists():
                raise Exception("CV JSON 파일이 없습니다 (Grid 처리 실패?)")
            log.append("  ✅ Grid 결과 사용")
            
        # skip_cv=False면 필요 시 호출
        elif not cv_json.exists() or force_reprocess:
            log.append("  🔍 호출 중...")
            cv_script = Path(__file__).parent / "cloud_vision_api.py"
            result = subprocess.run(
                ["python3", str(cv_script), image_path, str(cv_json)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"CV API 실패: {result.stderr}")
            log.append("  ✅ 완료")
        else:
            log.append("  ✅ 캐시 사용")
        
        # CV JSON이 비어있는지 확인 (텍스트 없는 이미지)
        with open(cv_json, 'r', encoding='utf-8') as f:
            cv_data = json.load(f)
            if not cv_data or len(cv_data) == 0:
                is_blank_image = True
                log.append("  ℹ️  텍스트 없는 이미지 - 원본 복사")
                # 원본을 output으로 복사 (output_subfolder 고려)
                import shutil
                final_output = target_dir / f"{img_name}_final.jpg"
                shutil.copy(image_path, final_output)
                log.append(f"  ✅ 원본 파일 복사 완료: {final_output.name}")
                return str(final_output), "\n".join(log)
        
        # 2. 클러스터링 V5
        log.append("\n📦 Step 2: Clustering")
        if not cv_clusters.exists() or force_reprocess:
            log.append("  🔄 클러스터링 중...")
            cluster_script = Path(__file__).parent / "cluster_cv_v5.py"
            result = subprocess.run(
                ["python3", str(cluster_script), str(cv_json), str(cv_clusters)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"클러스터링 실패: {result.stderr}")
            log.append("  ✅ 완료")
        else:
            log.append("  ✅ 캐시 사용")
        log.append(f"  {timing.checkpoint('Step 2: Clustering')}")

        # 시각화 (옵션)
        if not llm_viz.exists():
            viz_script = Path(__file__).parent / "visualize_clusters_v5.py"
            subprocess.run(
                ["python3", str(viz_script), image_path, str(cv_clusters), str(llm_viz)],
                capture_output=True, text=True, timeout=30
            )

        # 3a. Gemini OCR + 번역 (ocr_v5.json 생성)
        log.append("\n🤖 Step 3a: Gemini OCR + Translation")
        if not ocr_json.exists() or force_reprocess:
            log.append("  🤖 Gemini OCR + 번역 중...")
            ocr_script = Path(__file__).parent / "gemini_ocr_translate_v5.py"
            result = subprocess.run(
                ["python3", str(ocr_script), image_path, str(cv_clusters), str(ocr_json)],
                capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                raise Exception(f"Gemini OCR 실패: {result.stderr}")
            log.append("  ✅ OCR + 번역 완료")
        else:
            log.append("  ✅ 캐시 사용")
        log.append(f"  {timing.checkpoint('Step 3a: Gemini OCR')}")

        # 3b. 통합 (integrated.json 생성)
        log.append("\n📊 Step 3b: Integration")
        if not integrated.exists() or force_reprocess:
            log.append("  🔗 통합 중...")
            integrate_script = Path(__file__).parent / "integrate_ocr_v3.py"
            result = subprocess.run(
                ["python3", str(integrate_script), str(cv_clusters), str(ocr_json), str(integrated), image_path],
                capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                raise Exception(f"통합 실패: {result.stderr}")
            log.append("  ✅ 통합 완료")
            log.append(f"  {timing.checkpoint('Step 3b: Integration')}")

            # stdout에서 처리 정보 추출
            if result.stdout:
                for line in result.stdout.split('\n'):
                    if '개 클러스터' in line or '개 번역' in line or 'Merged' in line:
                        log.append(f"  {line}")
        else:
            log.append("  ✅ 캐시 사용")
        
        # 6. White Masking
        log.append("\n🎨 Step 6: Masking")
        if not masked_img.exists() or force_reprocess:
            log.append("  🖌️ 마스킹 중...")
            masking_script = Path(__file__).parent / "apply_masking.py"
            result = subprocess.run(
                ["python3", str(masking_script), image_path, str(integrated), str(cv_clusters), str(masked_img)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"Masking 실패: {result.stderr}")
            log.append("  ✅ 완료")
        else:
            log.append("  ✅ 캐시 사용")
        log.append(f"  {timing.checkpoint('Step 6: Masking')}")

        # 7. Text Overlay (항상 최종 결과는 재생성)
        log.append("\n✍️ Step 7: Text Overlay")
        log.append("  🖋️ 텍스트 렌더링 중...")
        render_script = Path(__file__).parent / "render_text_overlay.py"
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
        log.append(f"  {timing.checkpoint('Step 7: Text Overlay')}")

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
        cv_json_exists = [(current_temp_dir / f"{Path(p).stem}_cv.json").exists() for p in image_paths]
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
                cv_json_path = current_temp_dir / f"{img_name}_cv.json"
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
                    
                    # Grid 크기 계산 (grid_cols는 최대 5로 제한, grid_rows는 필요한 만큼)
                    grid_cols = min(int(np.ceil(np.sqrt(batch_size * 1.2))), 5)
                    grid_rows = int(np.ceil(batch_size / grid_cols))  # 실제 필요한 행 수
                    
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
                else:
                    color_images.append(img_path)
                    log.append(f"  🎨 컬러(AI 표지): {Path(img_path).name}")
        else:
            standard_images = image_paths
            
        if output_subfolder:
            log.append(f"📂 출력 폴더: output/{output_subfolder}")
            
        log.append(f"📋 처리 계획: 표준 {len(standard_images)}개, AI 표지 {len(color_images)}개")
        
        # Step 1: Grid CV API 처리 (표준 이미지만)
        if standard_images:
            progress(0.05, desc="Cloud Vision Grid 처리 중...")
            log.append(f"\n🔍 Step 1: Cloud Vision Grid API (표준 이미지 {len(standard_images)}개)")

            # 캐시 체크: CV JSON이 없는 이미지만 필터링
            missing_cv_images = []
            cached_cv_data = {}

            for img_path in standard_images:
                img_name = Path(img_path).stem
                cv_json_path = current_temp_dir / f"{img_name}_cv.json"

                if cv_json_path.exists() and not force_reprocess:
                    # 캐시된 CV 데이터 로드
                    try:
                        with open(cv_json_path, 'r', encoding='utf-8') as f:
                            cached_cv_data[img_path] = json.load(f)
                    except:
                        missing_cv_images.append(img_path)
                else:
                    missing_cv_images.append(img_path)

            # 캐시된 이미지 수
            cached_count = len(cached_cv_data)
            missing_count = len(missing_cv_images)

            if cached_count > 0:
                log.append(f"  ✅ CV JSON 캐시 사용: {cached_count}개 이미지")

            # 누락된 이미지만 API 호출
            if missing_count > 0:
                log.append(f"  🔍 CV API 호출: {missing_count}개 이미지")

                api_key = os.getenv("CLOUD_VISION_API_KEY")
                credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

                if not api_key and not credentials_path:
                    raise Exception("CLOUD_VISION_API_KEY 또는 GOOGLE_APPLICATION_CREDENTIALS 환경변수 필요")

                from cloud_vision_api import CloudVisionAPI
                cv_api = CloudVisionAPI(api_key=api_key, credentials_path=credentials_path)

                # 누락된 이미지 배치 분할 처리
                n_missing = len(missing_cv_images)
                avg_size_mb = sum(Path(p).stat().st_size for p in missing_cv_images) / n_missing / 1024 / 1024
                max_images_per_batch = max(1, min(int(38 / avg_size_mb) if avg_size_mb > 0 else 16, 25))

                all_cv_results = []
                if n_missing > max_images_per_batch:
                    num_batches = int(np.ceil(n_missing / max_images_per_batch))
                    log.append(f"  ⚠️  누락된 이미지가 많아 {num_batches}개 배치로 분할")

                    from preprocess_for_cv import preprocess_image_for_ocr

                    for batch_idx in range(num_batches):
                        start_idx = batch_idx * max_images_per_batch
                        end_idx = min((batch_idx + 1) * max_images_per_batch, n_missing)
                        batch_paths = missing_cv_images[start_idx:end_idx]

                        log.append(f"  📦 배치 {batch_idx+1}/{num_batches}: 이미지 {start_idx+1}~{end_idx}/{n_missing}")

                        # 전처리 (병렬)
                        prep_paths = []
                        with ThreadPoolExecutor(max_workers=min(10, len(batch_paths))) as executor:
                            futures = []
                            for p in batch_paths:
                                futures.append(executor.submit(preprocess_image_for_ocr, p, current_temp_dir))

                            for future in futures:
                                prep_paths.append(future.result())

                        # 전처리된 이미지로 CV 호출
                        batch_results = cv_api.detect_text_grid(prep_paths)

                        if batch_results:
                            all_cv_results.extend(batch_results)

                    cv_results = all_cv_results
                else:
                    # 단일 배치도 전처리
                    from preprocess_for_cv import preprocess_image_for_ocr

                    prep_paths = []
                    with ThreadPoolExecutor(max_workers=min(10, len(missing_cv_images))) as executor:
                        futures = []
                        for p in missing_cv_images:
                            futures.append(executor.submit(preprocess_image_for_ocr, p, current_temp_dir))

                        for future in futures:
                            prep_paths.append(future.result())

                    cv_results = cv_api.detect_text_grid(prep_paths)

                if not cv_results:
                    raise Exception("CV Grid 처리 실패")

                # 누락된 이미지별 CV JSON 저장
                for img_path, cv_boxes in zip(missing_cv_images, cv_results):
                    img_name = Path(img_path).stem
                    cv_json_path = current_temp_dir / f"{img_name}_cv.json"
                    with open(cv_json_path, 'w', encoding='utf-8') as f:
                        json.dump(cv_boxes, f, ensure_ascii=False, indent=2)

                log.append(f"  ✅ CV API 처리 완료")
            else:
                log.append(f"  ✅ 모든 이미지 캐시됨")
        
        # Step 2-7: 병렬 처리 (통합)
        progress(0.15, desc="파이프라인 병렬 처리 중...")
        log.append(f"\n🚀 Step 2-7: 병렬 파이프라인 실행")
        
        output_files = []
        completed_count = 0
        max_workers = min(20, n_images)  # 최대 20개 병렬 (Gemini 2.5 Flash: 10 RPM free, 300 RPM paid)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {}
            
            # 표준 이미지 작업 제출
            for img_path in standard_images:
                future = executor.submit(process_single_image, img_path, extra_prompt, force_reprocess, font_path, font_size, output_subfolder, skip_cv=True)
                future_to_path[future] = img_path
                
            # 컬러 이미지 작업 제출
            for img_path in color_images:
                future = executor.submit(process_cover_image_wrapper, img_path, output_subfolder, force_reprocess)
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
                    f"{img_name}_ocr_v5.json",
                    f"{img_name}_integrated.json",
                    f"{img_name}_integrated_translated.json",
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

            # Batch API 옵션
            use_batch_api = gr.Checkbox(
                label="🚀 Batch API 사용 (50% 비용 절감, 빠른 처리)",
                value=True,
                info="기본: Batch API (50% 절감, 높은 처리량). 체크 해제하면 실시간 API (즉시 처리)"
            )
            
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
            
            **Batch API 장점:**
            - 💰 비용 50% 절감
            - 📊 대량 처리 가능 (최대 200,000개)
            - 🚫 Quota 제한 완화
            
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
        inputs=[batch_images, batch_prompt, force_reprocess, font_dropdown, font_size_slider, auto_detect_color, output_subfolder_input, keep_cache],
        outputs=[batch_gallery, batch_status]
    )
    
    cover_btn.click(
        fn=translate_cover_batch,
        inputs=[cover_images, output_subfolder_input, use_batch_api, ai_model_dropdown],
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
