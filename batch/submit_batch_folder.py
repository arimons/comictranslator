import os
import time
import json
import requests
import argparse
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY 환경변수가 필요합니다. .env 파일을 확인해주세요.")

genai.configure(api_key=API_KEY)
MODEL_NAME = "models/gemini-3.1-flash-image-preview"

# API 서버 보호를 위한 재시도 업로드 래퍼
def upload_with_retry(img_path, max_retries=3, mime_type=None):
    start_time = time.time()
    for attempt in range(max_retries):
        try:
            if mime_type:
                uploaded = genai.upload_file(str(img_path), mime_type=mime_type)
            else:
                uploaded = genai.upload_file(str(img_path))
            return uploaded
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "quota" in error_msg:
                wait_time = (2 ** attempt) * 2
                print(f"      [429 한계] {wait_time}초 대기 후 재시도... ({attempt+1}/{max_retries})")
                time.sleep(wait_time)
            elif "503" in error_msg or "reset" in error_msg:
                print(f"      [네트워크 오류] 2초 대기 후 재시도... ({attempt+1}/{max_retries})")
                time.sleep(2)
            else:
                print(f"      알 수 없는 에러: {e}")
                raise e
    raise Exception(f"{img_path} 업로드 최종 실패")

def main():
    parser = argparse.ArgumentParser(description="폴더 단위 (재귀 탐색) 구글 통짜 Batch 전송 스크립트")
    parser.add_argument("folder_path", type=str, help="배치 전송할 최상위 폴더 경로 (예: test_image)")
    parser.add_argument("--prompt", type=str, default="이 일본 만화책 이미지를 한국어 가로쓰기로 번역된 새로운 이미지로 생성해주세요. 요구사항: 1. 모든 일본어 텍스트를 자연스러운 한국어로 번역 (세로쓰기 절대 금지, 가로쓰기 유지) 2. 글자 폰트, 말풍선 크기, 배경 일러스트 완전 유지 3. 고품질 출력", help="적용할 프롬프트")
    args = parser.parse_args()

    input_dir = Path(args.folder_path)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"❌ 폴더를 찾을 수 없습니다: {input_dir}")
        return

    print(f"==========================================================")
    print(f"🚀 대규모 폴더 배치 전송기 (Google 순정 Async Batch API)")
    print(f"==========================================================")
    print(f"📂 대상 최상위 폴더: {input_dir.absolute()}")

    # 1. 재귀적으로(Recursive) 모든 하위 폴더의 jpg, png 검색
    print(f"\n🚨 1. 하위 폴더까지 샅샅이 뒤져 이미지 파일을 수집합니다...")
    # rglob()을 쓰면 자식 폴더1, 자식 폴더2 안에 있는 이미지까지 한 큐에 찾아옵니다!
    image_paths = list(input_dir.rglob("*.jpg")) + list(input_dir.rglob("*.png")) + list(input_dir.rglob("*.jpeg"))
    
    if not image_paths:
        print("❌ 폴더 내부에 이미지 파일이 하나도 없습니다!")
        return

    print(f"   ✓ 총 {len(image_paths)}장의 이미지가 발견되었습니다.")
    if len(image_paths) > 200000:
        print("⚠️ 최대 20만 장 제한을 초과했습니다.")
        return

    # 2. 이미지 File API에 개별 업로드 준비 (병렬 처리)
    print("\n🚨 2. 이미지를 Google 클라우드에 병렬로 업로드합니다. (8개 동시)")
    uploaded_files = [] 
    mapping_data = []
    
    # 병렬 업로드를 위한 헬퍼 함수
    def process_upload(idx, img_path):
        rel_path = img_path.relative_to(input_dir)
        try:
            uploaded = upload_with_retry(img_path)
            safe_id = f"job_{idx:05d}"
            return {"success": True, "idx": idx, "safe_id": safe_id, "rel_path": rel_path, "uploaded": uploaded}
        except Exception as e:
            return {"success": False, "idx": idx, "rel_path": rel_path, "error": str(e)}

    # ThreadPoolExecutor를 이용한 병렬 업로드
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_upload, idx, img_path): img_path for idx, img_path in enumerate(image_paths, 1)}
        
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result["success"]:
                print(f"   [{i}/{len(image_paths)}] 업로드 성공: {result['rel_path']}")
                uploaded_files.append((result["safe_id"], result["rel_path"], result["uploaded"]))
                mapping_data.append({"job_id": result["safe_id"], "original_path": str(result["rel_path"])})
            else:
                print(f"   [{i}/{len(image_paths)}] ❌ {result['rel_path']} 실패: {result['error']}")

    if not uploaded_files:
        print("❌ 업로드 성공한 파일이 0개입니다. 중단합니다.")
        return

    # 나중에 결과를 매칭하기 쉽도록 매핑 파일(mapping_info) 별도 로컬 저장 
    mapping_file = input_dir / f"_batch_mapping_{int(time.time())}.json"
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(mapping_data, f, ensure_ascii=False, indent=2)
    print(f"\n   ✓ 복구용 매핑 파일 생성 완료: {mapping_file}")

    # 3. JSONL 구우기
    print("\n🚨 3. JSONL 요청 꾸러미를 굽고 있습니다...")
    jsonl_filename = input_dir / f"_batch_requests_{int(time.time())}.jsonl"
    
    with open(jsonl_filename, "w", encoding="utf-8") as f:
        for job_id, rel_path, up_file in uploaded_files:
            request_obj = {
                "id": job_id, # 고유 ID (알파벳, 숫자, _, - 만 가능)
                "request": {
                    "contents": [{
                        "role": "user",
                        "parts": [
                            {"text": args.prompt},
                            {"file_data": {"mime_type": up_file.mime_type, "file_uri": up_file.uri}}
                        ]
                    }],
                    "model": MODEL_NAME,
                    "generation_config": {
                         "temperature": 0.2
                    }
                }
            }
            f.write(json.dumps(request_obj) + "\n")
            
    print(f"   ✓ JSONL 파일 저장 완료: {jsonl_filename}")

    # 4. JSONL 업로드
    print("\n🚨 4. JSONL 덩어리를 통째로 구글 접수실로 올립니다...")
    batch_input_file = upload_with_retry(jsonl_filename, mime_type="application/jsonl")
    print(f"   ✓ JSONL 업로드 완료 Name: {batch_input_file.name}")

    # 5. REST 쏴주기
    print("\n🚨 5. ✨진짜 Batch API (최대 50% 할인)✨ 발송 시작!")
    # model: batchGenerateContent endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/{MODEL_NAME}:batchGenerateContent"
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "batch": {
            "model": MODEL_NAME,
            "displayName": f"Batch_{int(time.time())}",
            "inputConfig": {
                "fileName": batch_input_file.name
            }
        }
    }
    
    try:
        res = requests.post(url, params={"key": API_KEY}, headers=headers, json=payload)
        
        print("\n================ [Google 접수 응답 결과] ================")
        if res.status_code == 200:
            resp_data = res.json()
            # The API returns a long-running Operation. 
            # The actual Batch resource name is inside `metadata` -> `name` (or `metadata` -> `batchStats`)
            # Usually resp_data["name"] is the operation name "tunedModels/.../operations/..." and "metadata"]["name"] is "batches/..."
            metadata = resp_data.get("metadata", {})
            batch_name = metadata.get("name", "Unknown")
            state = metadata.get("state", "Unknown")
            print(f"✅ 축하합니다! {len(uploaded_files)}개의 이미지가 하나의 배치 작업으로 묶여 접수되었습니다.")
            print(f"🏷️ [작업 번호 (Batch ID)] : {batch_name}")
            print(f"📈 [현재 상태] : {state}")
            
            # 조회 명령어 편의성
            print("\n💡 [향후 조회 방법] 💡")
            print("터미널에 다음 명령어를 복사/붙여넣기 하시면 진행률(Task count)을 볼 수 있습니다:")
            print("----------------------------------------------------------")
            print(f"curl -H 'Content-Type: application/json' https://generativelanguage.googleapis.com/v1beta/{batch_name}?key=YOUR_API_KEY")
            print("----------------------------------------------------------")
            
            # 상태 저장해두기
            history_file = Path("batch_history.log")
            with open(history_file, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Folder: {input_dir} | Batch ID: {batch_name} | Files: {len(uploaded_files)}\n")
            print(f"(참고: 작업 ID는 batch_history.log 에도 기록되었습니다.)")
            print("\n⚠️ 안내: 화면을 끄고 주무셔도 됩니다. 결과 처리는 구글 클라우드에서 비동기로 알아서 진행됩니다.")
        else:
            print(f"❌ 구글 측 일괄 접수 거절: HTTP {res.status_code}")
            print(f"오류: {res.text}")
    except Exception as e:
        print(f"❌ REST API 전송 통신 장애: {e}")

if __name__ == "__main__":
    main()
