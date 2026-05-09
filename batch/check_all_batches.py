import os
from pathlib import Path

import requests
from dotenv import load_dotenv


def load_batches(history_file: Path):
    if not history_file.exists():
        return []

    batches = []
    with open(history_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" | ")
            if len(parts) >= 3:
                folder = parts[0].split("Folder: ")[1].split("/")[-1]
                batch_id = parts[1].split("Batch ID: ")[1]
                batches.append((folder, batch_id))

    return batches


def main():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        print("❌ GEMINI_API_KEY 환경변수가 필요합니다.")
        return 1

    history_file = Path("batch_history.log")
    if not history_file.exists():
        print("❌ batch_history.log 파일이 없습니다.")
        return 1

    batches = load_batches(history_file)
    if not batches:
        print("❌ 확인할 배치 내역이 없습니다.")
        return 0

    print("==========================================================")
    print(f"🔍 전체 Batch API 대기열 상태 조회기 (총 {len(batches)}건)")
    print("==========================================================")

    for folder, batch_id in batches:
        url = f"https://generativelanguage.googleapis.com/v1beta/{batch_id}"
        try:
            response = requests.get(
                url,
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            if response.status_code == 200:
                job_data = response.json()
                metadata = job_data.get("metadata", job_data)
                state = metadata.get("state", "알 수 없음")
                stats = metadata.get("batchStats", {})
                req = int(stats.get("requestCount", 0))
                comp = int(stats.get("successfulRequestCount", 0))
                fail = int(stats.get("failedRequestCount", 0))

                progress = int(((comp + fail) / req) * 100) if req > 0 else 0

                icon = "⏳"
                if state in ("SUCCEEDED", "COMPLETED"):
                    icon = "✅"
                elif state == "FAILED":
                    icon = "❌"
                elif 0 < progress < 100:
                    icon = "🔄"

                folder_display = folder[:25] + ".." if len(folder) > 25 else folder
                print(f"[{icon} {state:^15}] {folder_display:<27} | 진행률: {progress:3}% ({comp + fail}/{req})")
            else:
                print(f"[⚠️ STATUS_ERROR  ] {folder[:25]} | HTTP {response.status_code}")
        except Exception as e:
            print(f"[❌ NETWORK_ERROR ] {folder[:25]} | {type(e).__name__}")

    print("==========================================================")
    print("💡 완료된(SUCCEEDED) 작업은 python3 batch/receive_batch_result.py --batch_id <송장번호> 로 수령하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
