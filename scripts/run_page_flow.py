#!/usr/bin/env python3
"""한 페이지를 편집기와 같은 흐름으로 처리해 프로젝트에 R 영역·배경 패치·오버레이를 남긴다.

단계(--stage): bind(용어집 연결) -> analyze(CV·TG·R) -> regions(terra가 TG를 묶어 R 확정) -> erase(AI 글자 지우기 후 확정) -> final(OCR·번역·오버레이)
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


REGION_PAD = 14
CLUE_MAX_SIDE = 1800
REGION_PROMPT = """일본어 만화 한 페이지입니다. 이미지에는 Cloud Vision이 찾은 글자 조각(TG)이 번호 박스로 그려져 있습니다.
CV 조각은 한 덩어리 글을 여러 조각으로 쪼개거나 일부 글자를 놓칠 수 있으니 번호는 단서로만 쓰고, 이미지를 보고 판단하세요.

JSON 객체 하나로만 답하세요.
{"regions": [
  {"tg_ids": [이 영역에 속하는 TG 번호들],
   "translate": 한국어로 번역해 다시 넣어야 하면 true (로마자 표기·쪽번호·영문 로고·장식은 false),
   "kind": "caption | title | label | sfx | keep",
   "tg_incomplete": 이 영역의 글자 중 TG 박스로 덮이지 않은 부분이 있으면 true,
   "rect_norm": [x, y, w, h]  // 이 영역 글자 전체를 감싸는 사각형. 페이지 가로·세로를 각각 0-1000으로 본 좌표
  }]}

규칙:
- 읽는 단위(한 문단, 한 줄 제목, 한 줄 라벨)가 하나의 영역입니다. 한 문단의 TG를 여러 영역으로 나누지 마세요.
- 반투명 박스 안의 문단은 박스 전체가 아니라 글자가 있는 부분을 하나의 영역으로 묶으세요.
- 어느 영역에도 안 들어가는 TG는 translate=false 영역으로 따로 적으세요."""


def clue_image(page_path: str, groups: list[dict]) -> tuple[str, tuple[int, int]]:
    import base64
    import io

    from PIL import Image, ImageDraw, ImageFont

    with Image.open(page_path) as src:
        page = src.convert("RGB")
    scale = min(1.0, CLUE_MAX_SIDE / max(page.size))
    small = page.resize((round(page.width * scale), round(page.height * scale)), Image.LANCZOS)
    draw = ImageDraw.Draw(small)
    font = ImageFont.truetype(str(ROOT / "Fonts/Freesentation-7Bold.ttf"), 22)
    for group in groups:
        x, y, w, h = [round(v * scale) for v in group["tight_rect"]]
        draw.rectangle((x, y, x + w, y + h), outline=(255, 0, 80), width=2)
        draw.text((x, max(0, y - 24)), str(group["text_group_id"]), fill=(255, 0, 80), font=font)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), page.size


def union(rects: list[list[int]]) -> list[int]:
    x1 = min(r[0] for r in rects)
    y1 = min(r[1] for r in rects)
    x2 = max(r[0] + r[2] for r in rects)
    y2 = max(r[1] + r[3] for r in rects)
    return [x1, y1, x2 - x1, y2 - y1]


def terra_guidance(record: dict, model: str) -> dict:
    import json as _json
    import os

    from openai import OpenAI

    groups = _json.loads(Path(record["text_groups_json"]).read_text(encoding="utf-8"))
    data_url, (width, height) = clue_image(record["input_path"], groups)
    listing = [{"tg": g["text_group_id"], "rect": g["tight_rect"], "cv_text": g.get("text", "")} for g in groups]
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=180, max_retries=1)
    response = client.responses.create(
        model=model, reasoning={"effort": "low"},
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": REGION_PROMPT + "\n\nTG 목록(원본 픽셀 좌표 x,y,w,h):\n" + _json.dumps(listing, ensure_ascii=False)},
            {"type": "input_image", "image_url": data_url, "detail": "high"}]}],
        text={"format": {"type": "json_object"}}, max_output_tokens=4000, store=False)
    plan = _json.loads(response.output_text)
    by_id = {g["text_group_id"]: g for g in groups}
    manual, disabled, report = [], set(), []
    for index, region in enumerate(plan.get("regions", []), start=1):
        ids = [i for i in region.get("tg_ids", []) if i in by_id]
        if not region.get("translate"):
            disabled.update(ids)
            report.append({"tg_ids": ids, "translate": False, "kind": region.get("kind")})
            continue
        rects = [by_id[i]["tight_rect"] for i in ids]
        norm = region.get("rect_norm")
        if norm and (region.get("tg_incomplete") or not rects):
            rects.append([round(norm[0] * width / 1000), round(norm[1] * height / 1000),
                          round(norm[2] * width / 1000), round(norm[3] * height / 1000)])
        if not rects:
            continue
        x, y, w, h = union(rects)
        rect = [max(0, x - REGION_PAD), max(0, y - REGION_PAD), w + 2 * REGION_PAD, h + 2 * REGION_PAD]
        manual.append({"id": f"terra_ubox_{index}", "type": "unboxed", "unboxed_rect": rect, "source_method": "manual_ubox_guide"})
        report.append({"tg_ids": ids, "translate": True, "kind": region.get("kind"), "rect": rect,
                       "tg_incomplete": bool(region.get("tg_incomplete"))})
    return {"manual_regions": manual, "disabled_tg_ids": sorted(disabled)}, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--stage", choices=["bind", "analyze", "regions", "erase", "final", "all"], default="all")
    parser.add_argument("--erase-quality", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--ocr-model", default=TERRA_MODEL)
    parser.add_argument("--font", help="오버레이 폰트 경로(비우면 기본 폰트)")
    parser.add_argument("--force", action="store_true", help="analyze·final을 캐시 무시하고 다시 실행")
    args = parser.parse_args()
    sid, iid = args.session_id, args.image_id
    stages = ["bind", "analyze", "regions", "erase", "final"] if args.stage == "all" else [args.stage]

    client = TestClient(comic_app.create_app())
    if "bind" in stages:
        call(client, "POST", "/api/glossary/bind", json={"session_id": sid, "work_id": args.glossary})
        print("용어집 연결 완료", flush=True)
    if "analyze" in stages:
        result = call(client, "POST", "/api/analyze", json={"session_id": sid, "image_id": iid, "force": args.force})
        print("분석 완료\n" + result.get("logs", "")[-800:], flush=True)
    if "regions" in stages:
        record = comic_app.get_image(sid, iid)
        record = comic_app.image_record(sid, Path(record["input_path"]), record.get("image_sha256"))
        guidance, report = terra_guidance(record, args.ocr_model)
        for row in report:
            print("  ", row, flush=True)
        call(client, "POST", "/api/guidance", json={"session_id": sid, "image_id": iid, "guidance": guidance})
        print(f"R 영역 {len(guidance['manual_regions'])}개 확정(제외 TG {guidance['disabled_tg_ids']})", flush=True)
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
