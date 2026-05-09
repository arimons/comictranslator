#!/usr/bin/env python3
"""
Debug 파이프라인 실행 스크립트
00013, 00014, 00016, 00019 이미지를 처리합니다.

현재 main.process_single_image 표준 파이프라인을 사용합니다:
1. Cloud Vision API → {번호}_cv.json
2. Clustering V5 → {번호}_cv_clusters_v5.json
3. Gemini OCR + 번역 + integrated JSON → {번호}_integrated.json
4. White Masking → {번호}_masked.jpg
5. Text Overlay → {번호}_final.jpg

시각화:
- {번호}_debug.jpg: line/cluster/union bbox 통합 시각화
- {번호}_tight_debug.jpg: tight text group bbox 시각화

Note: 모든 파일은 temp_output에만 저장 (debug 전용)
"""

import sys
import subprocess
from pathlib import Path

# main.py에서 process_single_image 함수 가져오기
sys.path.insert(0, str(Path(__file__).parent))

import main
from main import process_single_image


def run_debug_pipeline():
    """각 이미지를 메인 파이프라인으로 처리 (모든 결과를 temp_output에 저장)"""

    # 처리할 이미지 번호
    image_numbers = ["00013", "00014", "00016", "00019"]
    project_root = Path(__file__).parent
    temp_debug_dir = project_root / "temp_output" / "Debug"
    temp_debug_dir.mkdir(parents=True, exist_ok=True)

    # 최종 결과도 temp_output/Debug에 모으도록 output root를 temp_output으로 임시 변경
    original_output_dir = main.output_dir
    main.output_dir = project_root / "temp_output"

    try:
        for img_num in image_numbers:
            image_path = project_root / "Debug" / img_num / f"{img_num}.jpg"

            if not image_path.exists():
                print(f"❌ 이미지 없음: {image_path}")
                continue

            print(f"\n{'='*60}")
            print(f"🎬 {img_num} 처리 시작")
            print(f"{'='*60}")

            try:
                # 1. 메인 파이프라인 실행
                # output_subfolder을 "Debug"로 설정하여 덮어씌우기
                final_img, log = process_single_image(
                    str(image_path),
                    output_subfolder="Debug",
                    force_reprocess=True  # 강제 재처리 (모든 파일 덮어씌우기)
                )

                print(log)
                print(f"✅ {img_num} 처리 완료: {final_img}")

                # 2. Contour anchor 검증용 tight text group 생성
                cv_json = temp_debug_dir / f"{img_num}_cv.json"
                tight_json = temp_debug_dir / f"{img_num}_cv_text_groups_tight.json"
                cluster_script = project_root / "pipeline" / "cluster_cv_v5.py"
                if cv_json.exists():
                    tight_result = subprocess.run(
                        ["python3", str(cluster_script), "--tight", str(cv_json), str(tight_json)],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    print(tight_result.stdout)
                    if tight_result.returncode != 0:
                        print(f"⚠️  Tight Text Group 생성 경고: {tight_result.stderr}")
                else:
                    print(f"⚠️  CV JSON 없음: {cv_json}")

                # 3. 시각화 스크립트 실행
                print(f"\n🎨 {img_num} 시각화 중...")
                visualize_script = project_root / "visualize_debug.py"
                result = subprocess.run(
                    ["python3", str(visualize_script), img_num],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                print(result.stdout)
                if result.returncode != 0:
                    print(f"⚠️  시각화 경고: {result.stderr}")

            except Exception as e:
                print(f"❌ {img_num} 처리 실패: {str(e)}")
                import traceback
                traceback.print_exc()

    finally:
        # output_dir 복구
        main.output_dir = original_output_dir

        print(f"\n{'='*60}")
        print(f"🎉 Debug 파이프라인 완료!")
        print(f"\n📁 모든 파일은 temp_output에 저장됨:")
        print(f"  최종 이미지: temp_output/Debug/*_final.jpg")
        print(f"  중간 파일: temp_output/Debug/")
        print(f"  시각화: temp_output/Debug/*_debug.jpg, *_tight_debug.jpg")
        print(f"\n참고: DEBUG_PROCESS.md 참조")
        print(f"{'='*60}")


if __name__ == "__main__":
    run_debug_pipeline()
