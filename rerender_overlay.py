#!/usr/bin/env python3
"""
Re-render text overlay using existing JSON files
기존 JSON으로 텍스트 오버레이만 다시 실행
"""

import sys
import json
import subprocess
from pathlib import Path
from glob import glob

def main():
    temp_output = Path("temp_output")
    
    # integrated_translated.json 파일들 찾기
    translated_jsons = sorted(glob(str(temp_output / "*_integrated_translated.json")))
    
    if not translated_jsons:
        print("❌ *_integrated_translated.json 파일을 찾을 수 없습니다")
        sys.exit(1)
    
    print(f"📝 {len(translated_jsons)}개 파일 발견")
    
    for trans_json in translated_jsons:
        trans_path = Path(trans_json)
        base_name = trans_path.stem.replace("_integrated_translated", "")
        
        # 필요한 파일들
        original_img = temp_output / f"{base_name}.jpg"
        integrated_json = temp_output / f"{base_name}_integrated.json"
        cv_clusters_json = temp_output / f"{base_name}_cv_clusters_v5.json"
        
        # 파일 존재 확인
        if not original_img.exists():
            print(f"  ⚠️  원본 이미지 없음: {base_name}.jpg")
            continue
        
        if not integrated_json.exists():
            print(f"  ⚠️  integrated.json 없음: {base_name}")
            continue
            
        if not cv_clusters_json.exists():
            print(f"  ⚠️  cv_clusters_v5.json 없음: {base_name}")
            continue
        
        # 출력 파일
        masked_img = temp_output / f"{base_name}_masked.jpg"
        final_img = temp_output / f"{base_name}_final.jpg"
        
        print(f"\n🎨 처리 중: {base_name}")
        
        # Step 1: White Masking
        print(f"  📐 Masking...")
        result = subprocess.run([
            "python3", "apply_masking.py",
            str(original_img),
            str(integrated_json),
            str(cv_clusters_json),
            str(masked_img)
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"  ❌ Masking 실패: {result.stderr}")
            continue
        
        # Step 2: Text Overlay  
        print(f"  ✍️  Overlay...")
        result = subprocess.run([
            "python3", "render_text_overlay.py",
            str(masked_img),
            str(trans_path),
            str(cv_clusters_json),
            str(final_img)
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"  ❌ Overlay 실패: {result.stderr}")
            continue
        
        print(f"  ✅ 완료: {final_img.name}")
    
    print(f"\n✅ 모든 작업 완료!")

if __name__ == "__main__":
    main()
