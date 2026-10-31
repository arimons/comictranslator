#!/usr/bin/env python3
"""한 페이지를 편집기와 같은 흐름으로 처리해 프로젝트에 R 영역·배경 패치·오버레이를 남긴다.

단계(--stage): bind(용어집 연결) -> analyze(CV·TG·R) -> erase(AI 글자 지우기 후 확정) -> final(OCR·번역·오버레이)
서버를 띄우지 않고 FastAPI TestClient로 앱 API를 그대로 호출하므로, 결과는 편집기에서 열어 다시 고칠 수 있다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi.testclient import TestClient  # noqa: E402

import app as comic_app  # noqa: E402
from pipeline.openai_tasks import TERRA_MODEL  # noqa: E402


def call(client: TestClient, method: str, url: str, **kw) -> dict:
    response = client.request(method, url, **kw)
    if response.status_code >= 400:
        sys.exit(f"{method} {url} -> {response.status_code}: {response.text[:600]}")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--stage", choices=["bind", "analyze", "erase", "final", "all"], default="all")
    parser.add_argument("--erase-quality", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--ocr-model", default=TERRA_MODEL)
    parser.add_argument("--font", help="오버레이 폰트 경로(비우면 기본 폰트)")
    parser.add_argument("--force", action="store_true", help="analyze·final을 캐시 무시하고 다시 실행")
    args = parser.parse_args()
    sid, iid = args.session_id, args.image_id
    stages = ["bind", "analyze", "erase", "final"] if args.stage == "all" else [args.stage]

    client = TestClient(comic_app.create_app())
    if "bind" in stages:
        call(client, "POST", "/api/glossary/bind", json={"session_id": sid, "work_id": args.glossary})
        print("용어집 연결 완료", flush=True)
    if "analyze" in stages:
        result = call(client, "POST", "/api/analyze", json={"session_id": sid, "image_id": iid, "force": args.force})
        print("분석 완료\n" + result.get("logs", "")[-800:], flush=True)
    if "erase" in stages:
        job = call(client, "POST", "/api/restore/ai-erase",
                   json={"session_id": sid, "image_id": iid, "quality": args.erase_quality, "ocr_model": args.ocr_model})
        job_id = job.get("job_id") or job.get("job", {}).get("job_id")
        while True:
            state = call(client, "GET", f"/api/restore/job/{job_id}")
            if state.get("status") in {"done", "error"}:
                break
            time.sleep(3)
        if state["status"] != "done":
            sys.exit(f"AI 지우기 실패: {state.get('error')}")
        print("AI 지우기 완료", state.get("meta", {}).get("usage"), flush=True)
        call(client, "POST", "/api/restore/accept", json={"session_id": sid, "image_id": iid, "job_id": job_id})
        print("배경 패치 확정", flush=True)
    if "final" in stages:
        body = {"session_id": sid, "image_id": iid, "ocr_model": args.ocr_model, "force": args.force}
        if args.font:
            body["font_path"] = args.font
        call(client, "POST", "/api/run-final", json=body)
        print("OCR·번역·오버레이 완료", flush=True)


if __name__ == "__main__":
    main()
