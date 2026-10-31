#!/usr/bin/env python3
"""권 단위 일괄 처리: 프로젝트 생성 -> terra 스크리닝 -> 통번역 대상 페이지를 편집기 흐름으로 처리.

처리(process) 단계는 페이지마다 analyze -> regions(terra가 영역 확정) -> erase(AI 편집 1회) -> final(OCR·번역·오버레이)을 거쳐
프로젝트에 R 영역·배경 패치·오버레이를 남긴다. 끝낸 페이지는 표식 파일이 있어 다시 실행하면 건너뛴다.
예산(--budget-usd)은 이미지 편집 호출의 추정 비용만 센다(terra 비용은 포함하지 않는다).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_page_flow as rpf  # noqa: E402
import series_translate as st  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

comic_app = st.comic_app


def done_marker(session_id: str, image_id: str) -> Path:
    return comic_app.session_dir(session_id) / "work" / "json" / f"{image_id}_flow_done.json"


def redraw_ids(session_id: str, data: dict) -> list[str]:
    json_dir = comic_app.session_dir(session_id) / "work" / "json"
    ids = []
    for item in comic_app.sorted_image_records(data["images"]):
        path = json_dir / f"{item['id']}_screen.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("redraw"):
            ids.append(item["id"])
    return ids


def ensure_final(sid: str, iid: str) -> None:
    """오버레이할 영역이 없으면 앱이 렌더를 건너뛰어 final이 없다. 이때는 AI 패치를 얹은 미리보기를 final로 쓴다."""
    import cv2

    item = comic_app.get_image(sid, iid)
    record = comic_app.image_record(sid, Path(item["input_path"]), item.get("image_sha256"))
    final = Path(record["final_jpg"])
    preview = Path(record["restore_dir"]) / "preview.png"
    if not final.exists() and preview.exists():
        final.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(final), cv2.imread(str(preview)), [cv2.IMWRITE_JPEG_QUALITY, 92])


def process_page(client: TestClient, sid: str, iid: str, args) -> float:
    rpf.call(client, "POST", "/api/analyze", json={"session_id": sid, "image_id": iid, "force": False})
    record = comic_app.image_record(sid, Path(comic_app.get_image(sid, iid)["input_path"]), comic_app.get_image(sid, iid).get("image_sha256"))
    guidance, _report, draw_plan = rpf.terra_guidance(record, args.ocr_model)
    Path(record["work_dir"], "json", f"{iid}_ai_draw.json").write_text(json.dumps(draw_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rpf.call(client, "POST", "/api/guidance", json={"session_id": sid, "image_id": iid, "guidance": guidance})
    cost = 0.0
    if guidance["manual_regions"] or draw_plan["draws"]:
        cost = rpf.erase_regions(client, sid, iid, args.erase_quality, args.ocr_model)
    rpf.call(client, "POST", "/api/run-final", json={"session_id": sid, "image_id": iid, "ocr_model": args.ocr_model,
                                                     "force": True, "extra_prompt": args.extra_prompt})
    ensure_final(sid, iid)
    done_marker(sid, iid).write_text(json.dumps({"ai_cost_usd": round(cost, 4)}), encoding="utf-8")
    return cost


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--stage", choices=["screen", "process", "all"], default="all")
    parser.add_argument("--limit", type=int, help="screen: 앞에서 N장만 스크리닝")
    parser.add_argument("--max-pages", type=int, help="process: 이번 실행에서 처리할 최대 장수")
    parser.add_argument("--budget-usd", type=float, help="process: 이미지 편집 추정 비용이 이 값에 닿으면 새 페이지를 시작하지 않는다")
    parser.add_argument("--only", help="process: 쉼표로 구분한 페이지 ID만")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--ocr-model", default=st.SCREEN_MODEL)
    parser.add_argument("--erase-quality", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--extra-prompt", default=rpf.RUBY_GUIDE)
    args = parser.parse_args()
    if not st.os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY가 필요합니다(.env).")
    folder = Path(args.folder)
    data = st.create_project(args.session_id, folder, args.name or f"{folder.parent.name} {folder.name}")
    guide = st.load_guide(args.glossary)
    client = TestClient(comic_app.create_app())
    rpf.call(client, "POST", "/api/glossary/bind", json={"session_id": args.session_id, "work_id": args.glossary})
    if args.stage in ("screen", "all"):
        st.run_screen(data, args.session_id, guide, args.ocr_model, args.workers, args.limit)
    if args.stage in ("process", "all"):
        ids = redraw_ids(args.session_id, data)
        if args.only:
            ids = [i for i in ids if i in set(args.only.split(","))]
        todo = [i for i in ids if not done_marker(args.session_id, i).exists()]
        if args.max_pages:
            todo = todo[: args.max_pages]
        print(f"처리 대상 {len(todo)}장 (판정 redraw {len(ids)}장 중 미완료)", flush=True)
        spent, lock, failed = [0.0], threading.Lock(), []

        def work(iid: str) -> None:
            with lock:
                if args.budget_usd is not None and spent[0] >= args.budget_usd:
                    print(f"  [{iid}] 예산 도달로 건너뜀", flush=True)
                    return
            try:
                cost = process_page(client, args.session_id, iid, args)
            except SystemExit as exc:  # rpf.call이 오류를 SystemExit로 올린다
                failed.append(iid); print(f"  [{iid}] 실패: {exc}", flush=True); return
            except Exception as exc:  # noqa: BLE001 - 한 장의 실패가 전체를 멈추지 않게 한다
                failed.append(iid); print(f"  [{iid}] 실패: {type(exc).__name__}: {exc}", flush=True); return
            with lock:
                spent[0] += cost
                print(f"  [{iid}] 완료 AI ${cost:.4f} (누적 ${spent[0]:.3f})", flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(work, i) for i in todo]):
                future.result()
        print(f"처리 종료: 누적 이미지 편집 ${spent[0]:.3f}, 실패 {len(failed)}장 {failed}", flush=True)


if __name__ == "__main__":
    main()
