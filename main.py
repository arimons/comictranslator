# 만화 번역 파이프라인 Gradio UI
#!/usr/bin/env python3

import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from pipeline.gemini_image_redraw import redraw_image
from pipeline.input_preprocessor import prepare_pipeline_inputs
from pipeline.pipeline_runner import DEFAULT_FONT, run_batch_images

# 출력 폴더
temp_dir = Path("temp_output")
output_dir = Path("output")
temp_dir.mkdir(exist_ok=True)
output_dir.mkdir(exist_ok=True)

prompt_history_file = Path("last_prompt.txt")


def load_last_prompt() -> str:
    try:
        if prompt_history_file.exists():
            return prompt_history_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def save_prompt(prompt: str):
    try:
        if prompt:
            prompt_history_file.write_text(prompt, encoding="utf-8")
    except Exception:
        pass


def get_available_fonts() -> list[str]:
    fonts_dir = Path("Fonts")
    if not fonts_dir.exists():
        return ["기본 폰트"]
    fonts = [f.stem for f in sorted(fonts_dir.glob("*.ttf"))]
    fonts += [f.stem for f in sorted(fonts_dir.glob("*.otf"))]
    return fonts if fonts else ["기본 폰트"]


def resolve_font(font_name: str) -> str:
    if not font_name or font_name == "기본 폰트":
        return DEFAULT_FONT
    for ext in (".ttf", ".otf"):
        p = Path("Fonts") / (font_name + ext)
        if p.exists():
            return str(p)
    return DEFAULT_FONT


def batch_translate_images(
    image_files,
    extra_prompt: str,
    force_reprocess: bool,
    font_name: str,
    output_subfolder: str,
    keep_cache: bool,
    progress=gr.Progress(),
):
    """
    배치 이미지 처리. Step 1은 그리드 모드로 일괄, Steps 2-6은 병렬 실행.
    완료 후 난이도 점수 내림차순으로 갤러리 반환.

    Returns:
        (gallery_items, log_text)
        gallery_items: [(image_path, caption), ...] 난이도 내림차순 정렬
    """
    if not image_files:
        return [], "이미지를 업로드해주세요."

    image_paths = [Path(f.name if hasattr(f, "name") else f) for f in image_files]
    input_count = len(image_paths)

    if not output_subfolder or not output_subfolder.strip():
        import datetime
        output_subfolder = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    work_subdir = temp_dir / output_subfolder
    out_subdir = output_dir / output_subfolder
    prepared_paths, split_meta = prepare_pipeline_inputs(
        image_paths,
        work_subdir / "split_input",
        split_landscape=True,
        min_aspect_ratio=1.25,
        reading_order="rtl",
    )
    n = len(prepared_paths)
    split_count = sum(1 for item in split_meta if item.get("split"))

    font_path = resolve_font(font_name)
    save_prompt(extra_prompt)

    log = [
        f"{'='*60}",
        f"🎨 배치 번역 시작 ({input_count}개 입력 → {n}개 처리 이미지)",
        f"{'='*60}",
    ]
    if split_count:
        log.append(f"📖 landscape spread {split_count}개 자동 분할: {work_subdir / 'split_input'}")
    log_lock = Lock()

    def emit_log(message: str) -> None:
        print(message, flush=True)
        with log_lock:
            log.append(message)

    try:
        batch_results = run_batch_images(
            prepared_paths, work_subdir, out_subdir,
            extra_prompt=extra_prompt, force=force_reprocess,
            font_path=font_path, max_workers=8,
            log_callback=emit_log,
        )
    except Exception as e:
        log.append(f"\n❌ 배치 처리 오류: {e}")
        return [], "\n".join(log)

    gallery_items: list[tuple[str, str]] = []
    completed = 0
    for ok, img_log, img_path in batch_results:
        if ok:
            out_path = str(out_subdir / f"{img_path.stem}_final.jpg")
            gallery_items.append((out_path, img_path.stem))
        completed += 1
        progress(completed / n, desc=f"처리 중 ({completed}/{n})")

    if not keep_cache:
        if work_subdir.exists():
            shutil.rmtree(work_subdir)
        log.append(f"\n🧹 중간 파일 삭제: {work_subdir}")
    else:
        log.append(f"\n🗄️ 캐시 유지됨")

    log.append(f"\n✅ 완료: {len(gallery_items)}/{n}개")
    log.append(f"{'='*60}")
    return gallery_items, "\n".join(log)


def redraw_selected_images(
    image_files,
    output_subfolder: str,
    selected_model: str,
    progress=gr.Progress(),
):
    """
    선택된 이미지를 Gemini 생성형 AI로 직접 재생성하여 번역.

    Returns:
        (gallery_items, log_text)
    """
    if not image_files:
        return [], "이미지를 업로드해주세요."

    image_paths = [f.name if hasattr(f, "name") else f for f in image_files]
    n = len(image_paths)

    if not output_subfolder or not output_subfolder.strip():
        import datetime
        output_subfolder = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    out_subdir = output_dir / output_subfolder
    out_subdir.mkdir(parents=True, exist_ok=True)

    log = [f"🎨 AI 재생성 번역 시작 (모델: {selected_model}, {n}개)"]
    log_lock = Lock()
    results = []
    completed = 0

    max_workers = min(10, n) if "flash" in selected_model else min(3, n)

    def process_one(img_path: str):
        name = Path(img_path).stem
        out = out_subdir / f"{name}_ai.jpg"
        if out.exists():
            return True, str(out), f"{name} (캐시)"
        def emit_log(message: str) -> None:
            with log_lock:
                log.append(f"[{name}] {message}")

        ok = redraw_image(img_path, str(out), model_name=selected_model, log_callback=emit_log)
        return ok, str(out) if ok else None, name

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_one, p): p for p in image_paths}
        for future in as_completed(futures):
            try:
                ok, out_path, name = future.result()
            except Exception as e:
                name = Path(futures[future]).stem
                ok, out_path = False, None
                with log_lock:
                    log.append(f"  ❌ {name} 예외: {e}")
            if ok and out_path:
                log.append(f"  ✅ {name}")
                results.append(out_path)
            else:
                log.append(f"  ⚠️ {name} 실패")
            completed += 1
            progress(completed / n, desc=f"처리 중 ({completed}/{n})")

    log.append(f"\n✅ 완료: {len(results)}/{n}개")
    return results, "\n".join(log)


def create_output_zip():
    files = list(output_dir.rglob("*.jpg"))
    if not files:
        return None
    from datetime import datetime
    zip_path = output_dir / f"translated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in files:
            zf.write(f, f.relative_to(output_dir))
    return str(zip_path)


# ─────────────────────────── Gradio UI ───────────────────────────

with gr.Blocks(title="Comic Translator V5") as app:
    gr.Markdown("# 🎨 Comic Translator\n표준 파이프라인 (Bubble Detect + LaMa + Gemini OCR) • AI 재생성")

    with gr.Row():
        output_subfolder_input = gr.Textbox(
            label="📂 출력 폴더명 (선택)",
            placeholder="예: MyManga_Vol1  (비워두면 타임스탬프 자동 생성)",
            scale=2,
        )
        font_dropdown = gr.Dropdown(
            choices=get_available_fonts(),
            value="Freesentation-5Medium" if "Freesentation-5Medium" in get_available_fonts() else (get_available_fonts() or ["기본 폰트"])[0],
            label="🎨 폰트 선택",
            scale=1,
        )

    with gr.Row():
        # 좌측: 표준 파이프라인 배치
        with gr.Column():
            gr.Markdown("### 🚀 배치 번역 (표준 파이프라인)")

            batch_images = gr.File(
                label="📤 이미지 업로드 (복수 가능)",
                file_count="multiple",
                file_types=["image"],
                height=180,
            )
            batch_prompt = gr.Textbox(
                label="추가 번역 프롬프트 (선택)",
                placeholder="예: 반말을 사용하고 박진감 넘치게 번역해줘",
                lines=2,
                value=load_last_prompt(),
            )
            with gr.Row():
                force_reprocess = gr.Checkbox(label="🔄 강제 재처리", value=False)
                keep_cache = gr.Checkbox(label="🗄️ 캐시 유지", value=False)

            batch_btn = gr.Button("🚀 배치 번역 시작", variant="primary", size="lg")
            batch_status = gr.Textbox(label="📊 진행 상황", lines=10)
            batch_gallery = gr.Gallery(
                label="📸 번역 결과",
                columns=3,
                height=400,
            )

        # 우측: AI 재생성 번역
        with gr.Column():
            gr.Markdown("### 🎨 AI 재생성 번역")
            gr.Markdown("""
            **사용 시기:** 표준 파이프라인이 처리하기 어려운 복잡한 레이아웃 이미지.
            Gemini 생성형 AI가 이미지를 직접 재생성하여 번역합니다.
            """)

            cover_images = gr.File(
                label="📤 이미지 업로드",
                file_count="multiple",
                file_types=["image"],
                height=180,
            )
            ai_model_dropdown = gr.Dropdown(
                choices=["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview"],
                value="gemini-3.1-flash-image-preview",
                label="🤖 모델 선택",
                info="flash: 빠르고 할당량 넉넉 / pro: 고품질",
            )

            cover_btn = gr.Button("🎨 AI 재생성 시작", variant="primary", size="lg")
            cover_status = gr.Textbox(label="📊 진행 상황", lines=10)
            cover_gallery = gr.Gallery(label="📸 결과 이미지", columns=3, height=400)

    with gr.Row():
        zip_btn = gr.Button("📦 결과 압축하기", scale=3)
        zip_download = gr.File(label="📥 ZIP 다운로드", scale=2)

    batch_btn.click(
        fn=batch_translate_images,
        inputs=[batch_images, batch_prompt, force_reprocess, font_dropdown, output_subfolder_input, keep_cache],
        outputs=[batch_gallery, batch_status],
    )
    cover_btn.click(
        fn=redraw_selected_images,
        inputs=[cover_images, output_subfolder_input, ai_model_dropdown],
        outputs=[cover_gallery, cover_status],
    )
    zip_btn.click(fn=create_output_zip, outputs=[zip_download])

    gr.Markdown("""
    ---
    **파이프라인:** Cloud Vision → Text Grouping → 만화 대사 추출 (Bubble Detect + LaMa) → Gemini OCR/번역 → Text Overlay
    **폴더:** `temp_output/` 중간 파일 · `output/` 최종 결과
    """)


if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=7860, share=False, inbrowser=True, theme=gr.themes.Soft())
