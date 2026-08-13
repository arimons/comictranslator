# 파이프라인 CLI 진입점 - 단일 이미지 또는 폴더 배치 처리
#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline.input_preprocessor import prepare_pipeline_inputs
from pipeline.pipeline_runner import DEFAULT_FONT, run_batch_images, run_single_image

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def delete_intermediate_files(work_dir: Path, image_name: str) -> None:
    paths = [
        work_dir / "json"    / f"{image_name}_cv.json",
        work_dir / "json"    / f"{image_name}_text_groups.json",
        work_dir / "json"    / f"{image_name}_regions.json",
        work_dir / "json"    / f"{image_name}_integrated.json",
        work_dir / "masked"  / f"{image_name}_masked.jpg",
        work_dir / "sheets"  / f"{image_name}_indexed_sheet.jpg",
        work_dir / "vis"     / f"{image_name}_bubble_vis.jpg",
        work_dir / "merge"   / f"{image_name}_merge.jpg",
    ]
    for path in paths:
        if path.exists():
            path.unlink()


def resolve_font(font_arg: str | None) -> str | None:
    if not font_arg:
        return None
    p = Path(font_arg)
    if p.exists():
        return str(p)
    for ext in (".otf", ".ttf"):
        candidate = Path("Fonts") / (font_arg + ext)
        if candidate.exists():
            return str(candidate)
    return font_arg


def process_folder(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    prompt: str,
    force: bool,
    font_path: str,
    keep_cache: bool,
    workers: int,
    stop_after: str,
    split_landscape: bool,
    landscape_split_ratio: float,
):
    images = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        print(f"❌ 이미지 없음: {input_dir}")
        sys.exit(1)

    split_dir = work_dir / "split_input"
    prepared_images, split_meta = prepare_pipeline_inputs(
        images,
        split_dir,
        split_landscape=split_landscape,
        min_aspect_ratio=landscape_split_ratio,
        reading_order="rtl",
    )
    split_count = sum(1 for item in split_meta if item.get("split"))

    print(f"📁 {len(images)}개 입력 → {len(prepared_images)}개 처리 이미지 ({input_dir})")
    if split_count:
        print(f"📖 landscape spread {split_count}개 자동 분할: {split_dir}")
    print("☁️ Cloud Vision 단일 이미지 병렬 모드")

    def emit_log(message: str) -> None:
        print(message, flush=True)

    batch_results = run_batch_images(
        prepared_images, work_dir, output_dir,
        extra_prompt=prompt, force=force, font_path=font_path, max_workers=workers,
        log_callback=emit_log,
        stop_after=stop_after,
    )

    success = 0
    for ok, log, img_path in batch_results:
        if ok:
            success += 1
        if not keep_cache and stop_after not in {"extract", "pre_ocr"}:
            delete_intermediate_files(work_dir, img_path.stem)
    if stop_after in {"extract", "pre_ocr"} and not keep_cache:
        print("🗄️ OCR 전단계 모드라 중간 산출물을 유지합니다")

    print(f"\n{'='*50}")
    print(f"✅ 완료: {success}/{len(prepared_images)}")


def main():
    parser = argparse.ArgumentParser(description="만화 번역 파이프라인")
    parser.add_argument("--input", "-i", required=True, help="입력 이미지 또는 폴더")
    parser.add_argument("--output", "-o", required=True, help="출력 이미지 또는 폴더")
    parser.add_argument("--work-dir", default="temp_output", help="중간 파일 저장 폴더 (기본: temp_output)")
    parser.add_argument("--font", default=None, help="폰트 경로 또는 이름")
    parser.add_argument("--prompt", default="", help="추가 번역 프롬프트")
    parser.add_argument("--force", action="store_true", help="캐시 무시하고 강제 재처리")
    parser.add_argument("--keep-cache", action="store_true", help="처리 후 중간 파일 유지")
    parser.add_argument("--workers", type=int, default=4, help="폴더 처리 시 병렬 워커 수")
    parser.add_argument("--stop-after", choices=["extract", "overlay"], default="overlay", help="extract면 Gemini OCR 직전까지만 실행")
    parser.add_argument("--pre-ocr", action="store_true", help="Gemini OCR/번역 직전 Step 3까지만 실행")
    parser.add_argument("--no-split-landscape", action="store_true", help="landscape 2-page spread 자동 분할을 끕니다")
    parser.add_argument("--landscape-split-ratio", type=float, default=1.25, help="이 가로/세로 비율 이상이면 center split합니다")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    work_dir = Path(args.work_dir)
    font_path = resolve_font(args.font)
    stop_after = "extract" if args.pre_ocr else args.stop_after
    split_landscape = not args.no_split_landscape

    if not input_path.exists():
        print(f"❌ 입력 경로 없음: {input_path}")
        sys.exit(1)

    if input_path.is_dir():
        process_folder(
            input_path,
            output_path,
            work_dir,
            args.prompt,
            args.force,
            font_path,
            args.keep_cache,
            args.workers,
            stop_after,
            split_landscape,
            args.landscape_split_ratio,
        )
    else:
        # 단일 이미지도 동일한 batch runner를 사용한다.
        output_path.mkdir(parents=True, exist_ok=True) if output_path.suffix == "" else None
        out_dir = output_path if output_path.suffix == "" or str(args.output).endswith("/") else output_path.parent
        prepared_images, split_meta = prepare_pipeline_inputs(
            [input_path],
            work_dir / "split_input",
            split_landscape=split_landscape,
            min_aspect_ratio=args.landscape_split_ratio,
            reading_order="rtl",
        )
        if any(item.get("split") for item in split_meta):
            print(f"📖 landscape spread 자동 분할: {work_dir / 'split_input'}")
        batch_results = run_batch_images(
            prepared_images, work_dir, out_dir,
            extra_prompt=args.prompt, force=args.force, font_path=font_path, max_workers=1,
            log_callback=lambda message: print(message, flush=True),
            stop_after=stop_after,
        )
        ok = all(item[0] for item in batch_results)
        if not args.keep_cache and stop_after not in {"extract", "pre_ocr"}:
            for _ok, _log, img_path in batch_results:
                delete_intermediate_files(work_dir, img_path.stem)
        if stop_after in {"extract", "pre_ocr"} and not args.keep_cache:
            print("🗄️ OCR 전단계 모드라 중간 산출물을 유지합니다")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
