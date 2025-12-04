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
output_dir = Path("output")
output_dir.mkdir(exist_ok=True)
prompt_history_file = output_dir / "last_prompt.txt"


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


def process_single_image(image_path: str, extra_prompt: str = "") -> tuple:
    """
    단일 이미지 전체 파이프라인 처리
    
    Returns:
        (최종 이미지 경로, 로그 메시지)
    """
    image_name = Path(image_path).stem
    log = []
    
    try:
        # 출력 경로 설정
        cv_json = output_dir / f"{image_name}_cv.json"
        cv_clusters = output_dir / f"{image_name}_cv_clusters_v5.json"
        llm_viz = output_dir / f"{image_name}_llm_viz.jpg"
        ocr_json = output_dir / f"{image_name}_ocr_v5.json"
        integrated = output_dir / f"{image_name}_integrated.json"
        integrated_translated = output_dir / f"{image_name}_integrated_translated.json"
        masked_img = output_dir / f"{image_name}_masked.jpg"
        final_img = output_dir / f"{image_name}_final.jpg"
        
        log.append(f"📸 처리 중: {image_name}")
        
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
        log.append("  📦 클러스터링 V5...")
        result = subprocess.run(
            ["python3", "cluster_cv_v5.py", str(cv_json), str(cv_clusters)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise Exception(f"클러스터링 실패: {result.stderr}")
        
        # 시각화 (옵션)
        subprocess.run(
            ["python3", "visualize_clusters_v5.py", image_path, str(cv_clusters), str(llm_viz)],
            capture_output=True, text=True, timeout=30
        )
        
        # 3. Gemini OCR
        log.append("  🤖 Gemini OCR...")
        result = subprocess.run(
            ["python3", "test_cluster_ocr.py", image_path, str(cv_clusters), str(ocr_json)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise Exception(f"Gemini OCR 실패: {result.stderr}")
        
        # 4. 텍스트 통합
        log.append("  🔄 텍스트 통합...")
        result = subprocess.run(
            ["python3", "integrate_ocr.py", str(cv_clusters), str(ocr_json), str(integrated)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise Exception(f"통합 실패: {result.stderr}")
        
        # 5. Gemini Translation
        log.append("  🌐 번역 중...")
        
        # 임시로 추가 프롬프트가 있으면 JSON에 삽입
        # (translate_integrated.py는 extra_prompt를 받지 않으므로)
        result = subprocess.run(
            ["python3", "translate_integrated.py", str(integrated), str(integrated_translated)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise Exception(f"번역 실패: {result.stderr}")
        
        # Extra prompt가 있으면 재번역 (TODO: translate_integrated.py에 extra_prompt 지원 추가)
        
        # 6. White Masking
        log.append("  🎨 White Masking...")
        result = subprocess.run(
            ["python3", "apply_text_overlay.py", image_path, str(integrated), str(cv_clusters), str(masked_img)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise Exception(f"Masking 실패: {result.stderr}")
        
        # 7. Text Overlay
        log.append("  📝 Text Overlay...")
        result = subprocess.run(
            ["python3", "render_text_overlay.py", str(masked_img), str(integrated_translated), str(cv_clusters), str(final_img)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise Exception(f"Text Overlay 실패: {result.stderr}")
        
        log.append(f"  ✅ 완료: {final_img.name}")
        
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
        
        # Step 1: Grid CV API 처리 (1회 호출)
        progress(0.05, desc="Cloud Vision Grid 처리 중...")
        log.append(f"\n🔍 Step 1: Cloud Vision Grid API")
        
        grid_cols = int(np.ceil(np.sqrt(n_images)))
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
        
        # 각 이미지별 CV JSON 저장
        for idx, (img_path, cv_boxes) in enumerate(zip(image_paths, cv_results)):
            img_name = Path(img_path).stem
            cv_json_path = output_dir / f"{img_name}_cv.json"
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
    
    ### 💡 사용 팁:
    - Grid 처리로 CV API 비용 절감 (이미지 N개 → 1회 호출)
    - 병렬 처리로 빠른 속도 (최대 5개 동시 처리)
    - 결과는 `temp_output/` 폴더에 저장됩니다
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
