import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Debug 이미지(00013, 00014, 00016, 00019)를 표준 파이프라인으로 처리
#!/usr/bin/env python3

import subprocess
from pathlib import Path

from pipeline.paths import PROOF_DIR
from pipeline.paths import PROOF_DIR
from pipeline.pipeline_runner import run_single_image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEBUG_IMAGES = ["00013", "00014", "00016", "00019"]

# 중간 파일 및 최종 결과 모두 proof/Debug에 저장
WORK_DIR = PROOF_DIR / "Debug"
OUTPUT_DIR = PROOF_DIR / "Debug"


def run_debug_pipeline():
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    for img_num in DEBUG_IMAGES:
        image_path = PROJECT_ROOT / "Debug" / img_num / f"{img_num}.jpg"

        if not image_path.exists():
            print(f"❌ 이미지 없음: {image_path}")
            continue

        print(f"\n{'='*60}")
        print(f"🎬 {img_num} 처리 시작")
        print(f"{'='*60}")

        final_img = OUTPUT_DIR / f"{img_num}_final.jpg"
        ok, log = run_single_image(
            image_path,
            work_dir=WORK_DIR,
            output_path=final_img,
            force=True,
        )
        print(log)

        if not ok:
            print(f"❌ {img_num} 처리 실패")
            continue

        print(f"✅ {img_num} 처리 완료: {final_img}")

        # tight text group 생성 (contour anchor 검증용)
        cv_json = WORK_DIR / f"{img_num}_cv.json"
        tight_json = WORK_DIR / f"{img_num}_cv_text_groups_tight.json"
        cluster_script = PROJECT_ROOT / "pipeline" / "cluster_cv_v5.py"
        if cv_json.exists():
            result = subprocess.run(
                ["python3", str(cluster_script), "--tight", str(cv_json), str(tight_json)],
                capture_output=True, text=True, timeout=30,
            )
            print(result.stdout)
            if result.returncode != 0:
                print(f"⚠️ Tight Text Group 경고: {result.stderr}")
        else:
            print(f"⚠️ CV JSON 없음: {cv_json}")

        # 시각화
        print(f"\n🎨 {img_num} 시각화 중...")
        result = subprocess.run(
            ["python3", str(PROJECT_ROOT / "scripts/visualize_debug.py"), img_num],
            capture_output=True, text=True, timeout=30,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"⚠️ 시각화 경고: {result.stderr}")

    print(f"\n{'='*60}")
    print(f"🎉 Debug 파이프라인 완료!")
    print(f"📁 모든 파일: proof/Debug/")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_debug_pipeline()
