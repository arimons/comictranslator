import sys
import os
import requests
from dotenv import load_dotenv
import google.generativeai as genai
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=API_KEY)
if len(sys.argv) < 2:
    print("사용법: python resume_batch.py [방금_생성된_jsonl_파일경로]")
    sys.exit(1)
jsonl_file = sys.argv[1]
print(f"🚨 복구: 기존 로컬 JSONL 파일 '{jsonl_file}' 을 업로드합니다...")
batch_input_file = genai.upload_file(jsonl_file, mime_type="application/jsonl")
print(f"   ✓ 업로드 완료 Name: {batch_input_file.name}")
print("\n🚨 진짜 Batch API(REST) 작업 제출 발송 중...")
url = f"https://generativelanguage.googleapis.com/v1beta/batches?key={API_KEY}"
res = requests.post(
    url, 
    headers={"Content-Type": "application/json"}, 
    json={"inputFile": batch_input_file.name}
)
if res.status_code == 200:
    batch_name = res.json().get("name")
    print(f"✅ 축하합니다! 4683개 묶음이 무사히 이어달리기로 구글 서버에 접수되었습니다!")
    print(f"🏷️ [작업 번호 (Batch ID)] : {batch_name}")
    print(f"\n💡 이 번호를 따로 메모해두시거나, 내일 receive_batch_result.py --batch_id {batch_name} 로 조회하시면 됩니다!")
else:
    print(f"❌ 에러: {res.text}")
