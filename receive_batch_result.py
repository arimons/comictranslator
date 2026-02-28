import os
import time
import json
import requests
import argparse
import base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY 환경변수가 필요합니다. .env 파일을 확인해주세요.")

def get_latest_job_info():
    """batch_history.log 에서 가장 마지막에 접수한 작업 번호와 대상 폴더를 찾아옵니다."""
    history_file = Path("batch_history.log")
    if not history_file.exists():
        return None, None
        
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                return None, None
            last_line = lines[-1].strip()
            # 포맷: [2026-02-28 08:05:12] Folder: test_image | Batch ID: batch_1234abcd | Files: 2
            folder_str = last_line.split("Folder: ")[1].split(" |")[0]
            batch_id = last_line.split("Batch ID: ")[1].split(" |")[0]
            return batch_id, Path(folder_str)
    except Exception as e:
        print(f"❌ 히스토리 파일 분석 실패: {e}")
        return None, None

def get_mapping_file(folder_path):
    """해당 폴더 안에 있는 _batch_mapping_*.json 파일 중 가장 최신 것을 찾습니다."""
    mapping_files = list(folder_path.glob("_batch_mapping_*.json"))
    if not mapping_files:
        return None
    
    # 수정 시간 기준으로 가장 최신 것 정렬
    mapping_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return mapping_files[0]

def main():
    parser = argparse.ArgumentParser(description="수령함(Receive) - 구글 통짜 Batch 완료품 다운로드 및 압축 해제")
    parser.add_argument("--batch_id", type=str, help="조회할 특정 Batch ID (입력 안하면 가장 최근에 보낸 작업을 자동 추적)")
    args = parser.parse_args()

    print(f"==========================================================")
    print(f"📦 대규모 통짜 배치 수령기 (Google Async Batch API)")
    print(f"==========================================================")

    # 1. 수령할 송장 번호(Batch ID) 파악하기
    target_batch_id = args.batch_id
    target_folder = None
    
    if target_batch_id:
        print(f"🔍 지정하신 송장 번호 '{target_batch_id}' 의 상태를 조회합니다...")
    else:
        print(f"🔍 배치 ID를 입력하지 않으셨으므로, 내가 가장 마지막으로 보낸 택배를 찾습니다...")
        target_batch_id, target_folder = get_latest_job_info()
        
        if not target_batch_id:
            print(f"❌ batch_history.log 기록이 없거나 분석할 수 없습니다. --batch_id 옵션을 붙여 직접 실행하세요.")
            return
            
        print(f"🏷️ 발견된 최신 송장 번호: {target_batch_id}")
        print(f"📂 원본 폴더 (배송 목적지): {target_folder}")

    # 2. 구글에 작업 상태 물어보기 (Polling)
    print("\n🚨 1. 구글 서버에 현재 택배(작업) 상태를 물어보는 중...")
    url = f"https://generativelanguage.googleapis.com/v1beta/{target_batch_id}?key={API_KEY}"
    
    try:
        response = requests.get(url, headers={"Content-Type": "application/json"})
        if response.status_code != 200:
            print(f"❌ 구글 측 조회 오류: HTTP {response.status_code}")
            print(response.text)
            return
            
        job_data = response.json()
        state = job_data.get("state", "알 수 없음")
        request_count = job_data.get("requestCount", 0)
        completed_count = job_data.get("completedCount", 0)
        failed_count = job_data.get("failedCount", 0)
        output_uri = job_data.get("outputFile")
        
    except Exception as e:
        print(f"❌ 상태 조회 중 네트워크 통신 오류 발생: {e}")
        return

    # 3. 상태 출력
    print(f"\n================ [택배(배치) 진행 상황] ================")
    print(f"📦 상태: {state}")
    print(f"📊 총 접수량: {request_count} 건")
    print(f"✅ 처리 완료: {completed_count} 건")
    print(f"❌ 실패 건수: {failed_count} 건")
    print(f"⏳ 진행률: {int((completed_count+failed_count)/request_count * 100) if request_count > 0 else 0}%")
    
    if state not in ("SUCCEEDED", "FAILED", "COMPLETED"):
        print("\n⚠️ 아직 구글이 공장에서 열심히 번역/작업 중입니다! 100%가 될 때까지 기다려 주세요.")
        print("💡 나중에 생각나실 때 이 스크립트를 다시 실행하시면 됩니다.")
        return

    # 4. 다운로드 및 복구 (택배 상자 까기)
    print("\n==========================================================")
    print("🎉 번역 작업 완료! 결과물 다운로드(수령)를 시작합니다.")
    print("==========================================================")
    
    if not output_uri:
        print("❌ 작업은 완료되었다고 뜨는데 Output 파일(결과물 URI)이 없습니다. 구글 측 오류일 가능성이 있습니다.")
        return
        
    if not target_folder or not target_folder.exists():
        print(f"⚠️ 원본 대상 폴더를 찾을 수 없습니다! 현재 디렉토리에 'batch_result' 폴더를 파서 다운로드합니다.")
        target_folder = Path("batch_result")
        
    # 결과 해석을 위한 로컬 맵핑(Mapping) 파일 읽어오기
    mapping_data = {}
    mapping_file = get_mapping_file(target_folder)
    if mapping_file:
        print(f"📜 맵핑(Mapping) 지도 발견: {mapping_file.name}")
        with open(mapping_file, "r", encoding="utf-8") as f:
            try:
                mapping_list = json.load(f)
                for item in mapping_list:
                    # {"job_id": "job_00001", "original_path": "child/001.jpg"}
                    mapping_data[item["job_id"]] = item["original_path"]
            except Exception as e:
                print(f"⚠️ 맵핑 파일을 읽는 데 실패했습니다: {e}")
    else:
        print(f"⚠️ 맵핑 파일을 찾을 수 없습니다! 결과물이 폴더 트리 없이 일괄 다운로드 될 수 있습니다.")

    # 5. Output 파일(JSONL 덩어리) 다운로드
    output_download_url = f"{output_uri}?key={API_KEY}&alt=media"
    print(f"\n🚨 2. 결과물 덩어리(Output file)를 다운로드합니다...")
    
    try:
        download_res = requests.get(output_download_url)
        if download_res.status_code != 200:
            print(f"❌ 다운로드 실패! HTTP {download_res.status_code}")
            return
            
        output_content = download_res.text
        lines = output_content.strip().split("\n")
        print(f"   ✓ 다운로드 완료! 총 {len(lines)}개의 결과 응답이 들어있습니다.")
        
    except Exception as e:
        print(f"❌ 다운로드 중 에러 발생: {e}")
        return

    # 6. 결과물(JSONL) 압축 풀어서 로컬 폴더에 이미지 저장
    print("\n🚨 3. 결과물 압축(JSONL 해독)을 풀어 원본 폴더 위치에 생성합니다...")
    success_count = 0
    fail_count = 0
    
    for line_idx, line in enumerate(lines, 1):
        if not line.strip():
            continue
            
        try:
            res_obj = json.json() if hasattr(json, 'json') else json.loads(line)
            # 수령한 택배 안에 들어있는 응답 ID
            response_id = res_obj.get("id") # 예: "job_00001"
            
            # 발송 전 적어둔 매핑 지도에서 원래 얘가 있어야 했던 상대 경로를 찾습니다.
            original_rel_path = mapping_data.get(response_id) 
            
            if original_rel_path:
                # 원래의 폴더 구조와 파일명(예: child_1/001.jpg)을 그대로 사용
                original_path_obj = Path(original_rel_path)
                image_name = original_path_obj.stem # 001
                
                # 저장 폴더: 대상 최상위 폴더 / 하위 폴더 트리 복구
                save_dir = target_folder / original_path_obj.parent
            else:
                # 맵핑을 실패하거나 못 찾은 경우, 미아(orphan) 폴더에 임의의 번호로 저장
                image_name = f"unknown_{line_idx:04d}"
                save_dir = target_folder / "unknown_orphans"
                
            save_dir.mkdir(parents=True, exist_ok=True)
            saved_path = save_dir / f"{image_name}_ai.jpg" # _ai.jpg 로 저장
            
            # 구글 응답의 에러 체크
            if "error" in res_obj:
                print(f"   [{line_idx}/{len(lines)}] ❌ 에러 반환됨 ({response_id}): {res_obj['error'].get('message', '알수없음')}")
                fail_count += 1
                continue
            
            # 응답 깊은 곳에서 생성된 이미지 데이터(base64) 뽑아내기
            response_data = res_obj.get("response", {})
            parts = response_data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            
            image_found = False
            for part in parts:
                if "inlineData" in part:
                    # 마침내 그림(base64) 획득 성공!
                    b64_str = part["inlineData"].get("data", "")
                    img_bytes = base64.b64decode(b64_str)
                    
                    with open(saved_path, "wb") as img_file:
                        img_file.write(img_bytes)
                        
                    print(f"   [{line_idx}/{len(lines)}] ✅ 수령 완료: {saved_path.relative_to(target_folder)}")
                    image_found = True
                    success_count += 1
                    break
                    
            if not image_found:
                # 텍스트만 나왔거나 빈 껍데기일 경우
                print(f"   [{line_idx}/{len(lines)}] ⚠️ 생성된 그림(이미지) 데이터가 응답(JSON)에 없습니다. ({response_id})")
                fail_count += 1
                
        except Exception as e:
            print(f"   [{line_idx}/{len(lines)}] ❌ 결과 해석/저장 중 프로그램 에러: {e}")
            fail_count += 1
            
    # 마무리 정리
    print(f"\n================ [택배 배송 완료] ================")
    print(f"🎉 총 {success_count} 개의 이미지가 성공적으로 배달(저장) 되었습니다!")
    if fail_count > 0:
        print(f"⚠️ {fail_count} 개는 번역 실패 또는 부분 에러가 있었습니다.")
    print(f"📁 결과물 폴더 확인: {target_folder}")

if __name__ == "__main__":
    main()
