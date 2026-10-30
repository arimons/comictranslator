#!/usr/bin/env python3
"""권 단위 일괄 통번역: 프로젝트 생성 -> LLM 스크리닝(OCR·번역·선별) -> 선별 페이지만 이미지 모델로 통번역.

단계(--stage):
  create  원본 폴더로 프로젝트를 만든다(원본은 앱과 같은 방식으로 링크).
  screen  페이지마다 비전 모델이 OCR·번역·통번역 필요 여부를 판정해 work/json/<id>_screen.json에 저장한다.
  redraw  screen에서 redraw=true인 페이지만 gpt-image로 통번역해 output/final/<id>_final.jpg에 쓴다.
각 단계는 결과 파일이 있으면 건너뛰므로 중단 후 다시 실행하면 이어진다.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import app as comic_app  # noqa: E402
from PIL import Image  # noqa: E402
from pipeline.openai_image_redraw import redraw_image_with_openai  # noqa: E402
from pipeline.openai_tasks import IMAGE_MODEL  # noqa: E402
from pipeline.work_glossary import glossary_snapshot  # noqa: E402

SCREEN_MODEL = "gpt-6.1-terra"
SCREEN_MAX_SIDE = 2000

SCREEN_PROMPT = """일본어 만화 한 페이지를 보고 JSON 객체 하나로만 답하세요.

{
  "page_kind": "cover | story | character_intro | ad | illustration_only | colophon | blank | other",
  "has_text": 번역할 일본어 글자(대사·나레이션·효과음·제목·소개문)가 있으면 true,
  "regions": [{"ja": "원문", "ko": "한국어 번역", "kind": "dialogue | narration | sfx | title | caption"}],
  "redraw": 이 페이지를 한국어로 다시 그릴 가치가 있으면 true,
  "reason": "판정 사유 한 문장"
}

판정 기준:
- 표지, 본문, 캐릭터 소개처럼 독자가 읽는 일본어 글이 있는 페이지는 redraw=true.
- 광고, 다른 작품 홍보, 서점 안내, 그림만 있고 글이 없는 페이지, 빈 페이지, 판권 안내만 있는 페이지는 redraw=false.
- 그림 속 사소한 간판·낙서 글자뿐이면 redraw=false.
- 제작 사이트·스캔 크레딧 문구는 regions에 넣지 마세요.
- 번역은 아래 작품 참고 정보의 표기를 따르세요.
"""


def log(message: str) -> None:
    print(message, flush=True)


def natural_images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.suffix.lower() in comic_app.IMAGE_EXTS and not p.name.startswith(".")),
                  key=lambda p: comic_app.natural_name_key(p.name))


def create_project(session_id: str, folder: Path, name: str) -> dict:
    data = comic_app.ensure_session(session_id)
    if data.get("images"):
        log(f"프로젝트가 이미 있습니다: {session_id} ({len(data['images'])}장)")
        return data
    paths = natural_images(folder)
    data["project_name"] = name
    data["source_folder_name"] = folder.name
    comic_app.save_session(data)
    comic_app.copy_images_into_session(paths, session_id)
    data = comic_app.ensure_session(session_id)
    log(f"프로젝트 생성: {session_id} ({len(data['images'])}장)")
    return data


def load_guide(glossary_id: str) -> str:
    store = comic_app.glossary_store()
    return glossary_snapshot(store.read(glossary_id)).get("prompt", "")


def image_data_url(path: Path) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        scale = SCREEN_MAX_SIDE / max(img.size)
        if scale < 1:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def screen_page(client, path: Path, guide: str, model: str) -> dict:
    response = client.responses.create(
        model=model,
        reasoning={"effort": "low"},
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": SCREEN_PROMPT + ("\n" + guide if guide else "")},
            {"type": "input_image", "image_url": image_data_url(path), "detail": "high"},
        ]}],
        text={"format": {"type": "json_object"}},
        max_output_tokens=4000,
        store=False,
    )
    result = json.loads(response.output_text)
    if not isinstance(result, dict) or "redraw" not in result:
        raise ValueError("스크리닝 JSON 형식이 올바르지 않습니다.")
    usage = getattr(response, "usage", None)
    result["_usage"] = {"input": getattr(usage, "input_tokens", None), "output": getattr(usage, "output_tokens", None)}
    return result


def run_screen(data: dict, session_id: str, guide: str, model: str, workers: int, limit: int | None) -> None:
    from openai import OpenAI
    base = comic_app.session_dir(session_id)
    json_dir = base / "work" / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    items = comic_app.sorted_image_records(data["images"])
    if limit:
        items = items[:limit]
    todo = [i for i in items if not (json_dir / f"{i['id']}_screen.json").exists()]
    log(f"스크리닝 대상 {len(todo)}장 (전체 {len(items)}장, 모델 {model})")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=2, timeout=180)

    def work(item: dict) -> tuple[str, str]:
        out = json_dir / f"{item['id']}_screen.json"
        try:
            result = screen_page(client, Path(item["input_path"]), guide, model)
        except Exception as exc:  # 한 장의 실패가 전체를 멈추지 않게 한다
            return item["id"], f"실패: {exc}"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return item["id"], "redraw" if result.get("redraw") else "skip"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(work, i) for i in todo]):
            pid, status = future.result()
            log(f"  [{pid}] {status}")
    write_screen_report(items, json_dir, base)


def write_screen_report(items: list[dict], json_dir: Path, base: Path) -> None:
    rows = []
    for item in items:
        path = json_dir / f"{item['id']}_screen.json"
        if not path.exists():
            rows.append({"id": item["id"], "name": item["name"], "status": "missing"})
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append({"id": item["id"], "name": item["name"], "status": "redraw" if data.get("redraw") else "skip",
                     "page_kind": data.get("page_kind"), "reason": data.get("reason"),
                     "ko_preview": " / ".join(r.get("ko", "") for r in data.get("regions", [])[:3])})
    report = base / "work" / "screening_report.json"
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {s: sum(r["status"] == s for r in rows) for s in ("redraw", "skip", "missing")}
    log(f"스크리닝 결과: {counts} -> {report}")
    for row in rows:
        if row["status"] == "redraw":
            log(f"  통번역 {row['id']} [{row['page_kind']}] {row['reason']}")


def notes_from_screen(screen: dict) -> str:
    lines = [f"페이지 유형: {screen.get('page_kind')}", "영역별 번역(이 번역문을 그대로 식자하세요):"]
    for index, region in enumerate(screen.get("regions", []), start=1):
        lines.append(f"{index}. [{region.get('kind')}] {region.get('ja')} => {region.get('ko')}")
    return "\n".join(lines)


def run_redraw(data: dict, session_id: str, guide: str, args) -> None:
    base = comic_app.session_dir(session_id)
    json_dir = base / "work" / "json"
    items = comic_app.sorted_image_records(data["images"])
    targets = []
    for item in items:
        screen_path = json_dir / f"{item['id']}_screen.json"
        if not screen_path.exists():
            continue
        screen = json.loads(screen_path.read_text(encoding="utf-8"))
        if screen.get("redraw"):
            targets.append((item, screen))
    if args.only:
        wanted = set(args.only.split(","))
        targets = [t for t in targets if t[0]["id"] in wanted]
    final_dir = base / "output" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    todo = [(i, s) for i, s in targets if args.force or not (final_dir / f"{i['id']}_final.jpg").exists()]
    if args.max_pages:
        todo = todo[: args.max_pages]
    log(f"통번역 대상 {len(todo)}장 (판정 redraw {len(targets)}장, 품질 {args.quality})")
    lock = threading.Lock()
    failed: list[str] = []

    def work(pair) -> None:
        item, screen = pair
        out = final_dir / f"{item['id']}_final.jpg"
        ok = redraw_image_with_openai(
            item["input_path"], out, model_name=args.image_model, prompt_guide=guide,
            translation_layout_notes=notes_from_screen(screen), analyze_first=False,
            quality=args.quality, size="match_input", output_format="jpeg", output_compression=92,
            log_callback=lambda m, n=item["id"]: log(f"  [{n}] {m}"),
        )
        if not ok:
            with lock:
                failed.append(item["id"])

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(work, p) for p in todo]):
            future.result()
    log(f"완료. 실패 {len(failed)}장: {failed}" if failed else "완료. 실패 없음")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", required=True, help="원본 이미지 폴더(예: /Volumes/FLASH/Comic/원본/초전자포/v20)")
    parser.add_argument("--session-id", required=True, help="프로젝트 ID(예: 20261031_railgun_v20)")
    parser.add_argument("--name", default="", help="프로젝트 표시 이름")
    parser.add_argument("--glossary", required=True, help="용어집 work_id")
    parser.add_argument("--stage", choices=["create", "screen", "redraw", "all"], default="create")
    parser.add_argument("--limit", type=int, help="screen: 앞에서 N장만")
    parser.add_argument("--only", help="redraw: 쉼표로 구분한 페이지 ID만")
    parser.add_argument("--max-pages", type=int, help="redraw: 이번 실행에서 생성할 최대 장수(예산 제어)")
    parser.add_argument("--quality", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--screen-model", default=SCREEN_MODEL)
    parser.add_argument("--image-model", default=IMAGE_MODEL)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="redraw: 이미 있는 결과도 다시 생성")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY가 필요합니다(.env).")
    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"폴더를 찾을 수 없습니다: {folder}")
    data = create_project(args.session_id, folder, args.name or f"{folder.parent.name} {folder.name}")
    if args.stage == "create":
        return
    guide = load_guide(args.glossary)
    log(f"용어집 가이드 {len(guide)}자")
    if args.stage in ("screen", "all"):
        run_screen(data, args.session_id, guide, args.screen_model, args.workers, args.limit)
    if args.stage in ("redraw", "all"):
        run_redraw(data, args.session_id, guide, args)


if __name__ == "__main__":
    main()
