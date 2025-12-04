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


def process_single_image(image_path: str, extra_prompt: str = "", force_reprocess: bool = False) -> tuple:
    """
    단일 이미지 전체 파이프라인 처리
    
    Returns:
        (최종 이미지 경로, 로그 메시지)
    """
    image_name = Path(image_path).stem
    log = []
    
    try:
        # 출력 경로 설정
        # 중간 파일들은 temp_output에
        cv_json = temp_dir / f"{image_name}_cv.json"
        cv_clusters = temp_dir / f"{image_name}_cv_clusters_v5.json"
        llm_viz = temp_dir / f"{image_name}_llm_viz.jpg"
        ocr_json = temp_dir / f"{image_name}_ocr_v5.json"
        integrated = temp_dir / f"{image_name}_integrated.json"
        integrated_translated = temp_dir / f"{image_name}_integrated_translated.json"
        masked_img = temp_dir / f"{image_name}_masked.jpg"
        
        # 최종 결과물은 output에
        final_img = output_dir / f"{image_name}_final.jpg"
        
        log.append(f"📸 처리 중: {image_name}")
        
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
                ["python3", "test_cluster_ocr.py", image_path, str(cv_clusters), str(ocr_json)],
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
        
        # 5. Gemini Translation
        if not integrated_translated.exists() or force_reprocess:
            log.append("  🌐 번역 중...")
            result = subprocess.run(
                ["python3", "translate_integrated.py", str(integrated), str(integrated_translated)],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise Exception(f"번역 실패: {result.stderr}")
        else:
            log.append("  ✅ Translation JSON 캐시 사용")
        
        # 6. White Masking
        if not masked_img.exists() or force_reprocess:
            log.append("  🎨 White Masking...")
            result = subprocess.run(
                ["python3", "apply_text_overlay.py", image_path, str(integrated), str(cv_clusters), str(masked_img)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                raise Exception(f"Masking 실패: {result.stderr}")
        else:
            log.append("  ✅ Masked 이미지 캐시 사용")
        
        # 7. Text Overlay (항상 최종 결과는 재생성)
        log.append("  📝 Text Overlay...")
        result = subprocess.run(
            ["python3", "render_text_overlay.py", str(masked_img), str(integrated_translated), str(cv_clusters), str(final_img)],
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


def process_batch_with_grid(
    image_files: List, 
    extra_prompt: str = "", 
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
        
        # Step 1: Grid CV API 처리 (파일 크기 고려)
        progress(0.05, desc="Cloud Vision Grid 처리 중...")
        log.append(f"\n🔍 Step 1: Cloud Vision Grid API")
        
        # 파일 크기 계산 및 Grid 크기 결정
        total_size_mb = 0
        for img_path in image_paths:
            size_bytes = os.path.getsize(img_path)
            total_size_mb += size_bytes / (1024 * 1024)
        
        avg_size_mb = total_size_mb / n_images if n_images > 0 else 0
        
        # Cloud Vision API 제한: 40MB
        # 평균 파일 크기에 따라 Grid 크기 결정
        if avg_size_mb < 2.0:  # 작은 파일: 4x4 (최대 16개)
            max_grid_size = 4
            max_images_per_batch = 16
        else:  # 큰 파일: 3x3 (최대 9개)
            max_grid_size = 3
            max_images_per_batch = 9
        
        log.append(f"  📊 평균 파일 크기: {avg_size_mb:.2f}MB")
        log.append(f"  📐 최대 Grid 크기: {max_grid_size}x{max_grid_size} ({max_images_per_batch}개)")
        
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
                
                # 배치별 Grid 크기 계산
                grid_cols = min(int(np.ceil(np.sqrt(batch_size))), max_grid_size)
                grid_rows = int(np.ceil(batch_size / grid_cols))
                
                log.append(f"  📦 배치 {batch_idx+1}/{num_batches}: {batch_size}개 이미지, Grid {grid_rows}x{grid_cols}")
                
                from cloud_vision_api import CloudVisionAPI
                credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
                if not credentials_path:
                    raise Exception("GOOGLE_APPLICATION_CREDENTIALS 환경변수 필요")
                
                cv_api = CloudVisionAPI(credentials_path=credentials_path)
                batch_results = cv_api.detect_text_grid(batch_paths)
                
                if not batch_results:
                    raise Exception(f"CV Grid 처리 실패 (배치 {batch_idx+1})")
                
                all_cv_results.extend(batch_results)
            
            cv_results = all_cv_results
        else:
            # 단일 배치 처리
            grid_cols = min(int(np.ceil(np.sqrt(n_images))), max_grid_size)
            grid_rows = int(np.ceil(n_images / grid_cols))
            log.append(f"  📐 Grid: {grid_rows}x{grid_cols}")
            
            from cloud_vision_api import CloudVisionAPI
            credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if not credentials_path:
                raise Exception("GOOGLE_APPLICATION_CREDENTIALS 환경변수 필요")
            
            cv_api = CloudVisionAPI(credentials_path=credentials_path)
            cv_results = cv_api.detect_text_grid(image_paths)
            
            if not cv_results:
                raise Exception("CV Grid 처리 실패")
        
        # 각 이미지별 CV JSON 저장 (temp_output에)
        for idx, (img_path, cv_boxes) in enumerate(zip(image_paths, cv_results)):
            img_name = Path(img_path).stem
            cv_json_path = temp_dir / f"{img_name}_cv.json"
            with open(cv_json_path, 'w', encoding='utf-8') as f:
                json.dump(cv_boxes, f, ensure_ascii=False, indent=2)
        
        log.append(f"  ✅ CV JSON 생성 완료")
        
        # Step 2-7: 병렬 처리
        progress(0.15, desc="파이프라인 병렬 처리 중...")
        log.append(f"\n🚀 Step 2-7: 병렬 파이프라인 실행")
        
        output_files = []
        completed_count = 0
        max_workers = min(5, n_images)  # 최대 5개 병렬
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 각 이미지를 병렬로 처리
            future_to_path = {
                executor.submit(process_single_image, img_path, extra_prompt): img_path
                for img_path in image_paths
            }
            
            # 완료되는 순서대로 결과 수집
            for future in as_completed(future_to_path):
                img_path = future_to_path[future]
                final_img, img_log = future.result()
                
                log.append(f"\n{img_log}")
                
                if final_img:
                    output_files.append(final_img)
                
                completed_count += 1
                progress(0.15 + (0.8 * completed_count / n_images),
                        desc=f"처리 중... ({completed_count}/{n_images})")
        
        progress(0.95, desc="ZIP 파일 생성 중...")
        
        # ZIP 파일 생성
        if output_files:
            log.append(f"\n📦 ZIP 파일 생성 중...")
            zip_path = output_dir / "translated_comics.zip"
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for file_path in output_files:
                    zipf.write(file_path, Path(file_path).name)
            
            log.append(f"✅ 완료! {len(output_files)}/{n_images}개 성공")
            log.append(f"{'='*60}")
            
            progress(1.0, desc="완료!")
            return output_files, "\n".join(log), str(zip_path)
        else:
            log.append(f"\n❌ 모든 이미지 처리 실패")
            return None, "\n".join(log), None
    
    except Exception as e:
        import traceback
        error_msg = f"\n❌ 배치 처리 오류: {e}\n{traceback.format_exc()}"
        log.append(error_msg)
        return None, "\n".join(log), None


# Gradio UI 구성
with gr.Blocks(title="Comic Translator V5", theme=gr.themes.Soft()) as app:
    gr.Markdown("""
    # 🎨 Comic Translator V5
    ### 만화 자동 번역 시스템 (일본어 → 한국어)
    
    **새로운 기능:**
    - ✨ V5 클러스터링 (orientation, lines 구조)
    - 🎯 최소 4글자/줄 의미 단위 줄바꿈
    - 📝 말줄임표 별도 줄 처리
    - 🎨 짧은 텍스트 중앙 정렬
    - 🔄 Grid CV 처리 (1회 API 호출)
    - ⚡ 병렬 파이프라인 실행
    """)
    
    # API 키 입력
    with gr.Accordion("🔑 API 설정", open=False):
        gr.Markdown("GEMINI_API_KEY와 GOOGLE_APPLICATION_CREDENTIALS를 .env 파일에 설정하세요")
    
    with gr.Tab("🚀 배치 번역"):
        gr.Markdown("### Grid 처리 + 병렬 파이프라인")
        
        with gr.Row():
            batch_images = gr.File(
                label="📤 이미지 업로드 (복수 가능)",
                file_count="multiple",
                file_types=["image"]
            )
        
        batch_prompt = gr.Textbox(
            label="추가 번역 프롬프트 (선택)",
            placeholder="예: 반말을 사용하고, 소년 만화 스타일로 박진감 넘치게 번역해줘",
            lines=2,
            value=load_last_prompt()
        )
        
        force_reprocess = gr.Checkbox(
            label="🔄 강제 재처리 (기존 결과 무시)",
            value=False,
            info="체크하면 기존 캐시를 무시하고 모든 단계를 다시 실행합니다"
        )
        
        batch_btn = gr.Button("🚀 배치 번역 시작", variant="primary", size="lg")
        
        batch_status = gr.Textbox(label="📊 진행 상황", lines=15)
        
        with gr.Row():
            batch_gallery = gr.Gallery(label="📸 결과 이미지", columns=3, height="auto")
            batch_download = gr.File(label="📥 ZIP 다운로드")
        
        batch_btn.click(
            fn=process_batch_with_grid,
            inputs=[batch_images, batch_prompt],
            outputs=[batch_gallery, batch_status, batch_download]
        )
    
    gr.Markdown("""
    ---
    ### 📋 파이프라인 단계:
    1. **Cloud Vision Grid API** → cv.json (1회 호출)
    2. **클러스터링 V5** → cv_cluster_v5.json (병렬)  
    3. **Gemini OCR** → ocr_v5.json (병렬)
    4. **텍스트 통합** → integrated.json (병렬)
    5. **Gemini Translation** → integrated_translated.json (병렬)
    6. **White Masking** → _masked.jpg (병렬)
    7. **Text Overlay** → _final.jpg (병렬)
    
    ### 📁 폴더 구조:
    - **temp_output/**: 중간 파일 (JSON, masked.jpg, viz.jpg)
    - **output/**: 최종 결과물 (final.jpg, ZIP)
    
    ### 💡 사용 팁:
    - Grid 처리로 CV API 비용 절감 (이미지 N개 → 1회 호출)
    - 병렬 처리로 빠른 속도 (최대 5개 동시 처리)
    - 기존 결과가 있으면 자동으로 캐시 사용
    - 강제 재처리를 체크하면 모든 단계를 다시 실행
    - 폰트: Freesentation-4Regular.ttf, 30px (±3px)
    """)

if __name__ == "__main__":
    # 서버 실행
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True
    )
