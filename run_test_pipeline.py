#!/usr/bin/env python3
"""
Test 파이프라인 실행 스크립트

현재 표준 파이프라인(`main.process_single_image`)으로 test_image/00020-00035.jpg를
처리한다. 예전 CV/cluster/OCR/integration 개별 subprocess 테스트 경로는
메인 파이프라인과 달라져 제거했다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import main
from main import process_single_image


def run_test_pipeline():
    """test_image의 00020-00035 이미지를 현재 메인 파이프라인으로 처리"""
    project_root = Path(__file__).parent
    test_image_dir = project_root / "test_image"
    temp_test_dir = project_root / "temp_output" / "test_pipeline"
    temp_test_dir.mkdir(parents=True, exist_ok=True)

    image_numbers = [f"{i:05d}" for i in range(20, 36)]
    image_paths = [
        test_image_dir / f"{num}.jpg"
        for num in image_numbers
        if (test_image_dir / f"{num}.jpg").exists()
    ]

    if not image_paths:
        print("❌ 처리할 이미지가 없습니다")
        return

    print(f"\n{'=' * 60}")
    print("🚀 Test 파이프라인 시작")
    print("  📁 입력: test_image/")
    print("  📁 출력: temp_output/test_pipeline/")
    print(f"  📸 이미지: {len(image_paths)}개")
    print(f"{'=' * 60}\n")

    original_output_dir = main.output_dir
    main.output_dir = project_root / "temp_output"

    try:
        for idx, image_path in enumerate(image_paths, 1):
            print(f"\n{'=' * 60}")
            print(f"🎬 {image_path.stem} 처리 시작 ({idx}/{len(image_paths)})")
            print(f"{'=' * 60}")

            final_img, log = process_single_image(
                str(image_path),
                output_subfolder="test_pipeline",
                force_reprocess=True,
            )

            print(log)
            if final_img:
                print(f"✅ {image_path.stem} 처리 완료: {final_img}")
            else:
                print(f"❌ {image_path.stem} 처리 실패")
    finally:
        main.output_dir = original_output_dir

    print(f"\n{'=' * 60}")
    print("🎉 Test 파이프라인 완료")
    print("  최종 이미지: temp_output/test_pipeline/*_final.jpg")
    print("  중간 파일: temp_output/test_pipeline/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run_test_pipeline()
