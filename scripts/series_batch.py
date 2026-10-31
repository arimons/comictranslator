#!/usr/bin/env python3
"""권 단위 일괄 처리: 프로젝트 생성 -> terra 스크리닝 -> 통번역 대상 페이지를 편집기 흐름으로 처리.

처리(process) 단계는 페이지마다 analyze -> regions(terra가 영역 확정) -> erase(AI 편집 1회) -> final(OCR·번역·오버레이)을 거쳐
프로젝트에 R 영역·배경 패치·오버레이를 남긴다. 끝낸 페이지는 표식 파일이 있어 다시 실행하면 건너뛴다.
예산(--budget-usd)은 이미지 편집 호출의 추정 비용만 센다(terra 비용은 포함하지 않는다).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_page_flow as rpf  # noqa: E402
import series_translate as st  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

comic_app = st.comic_app


def done_marker(session_id: str, image_id: str) -> Path:
    return comic_app.session_dir(session_id) / "work" / "json" / f"{image_id}_flow_done.json"


def failed_marker(session_id: str, image_id: str) -> Path:
    return comic_app.session_dir(session_id) / "work" / "json" / f"{image_id}_flow_failed.json"


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


def mark_failed(session_id: str, image_id: str, reason: str) -> None:
    failed_marker(session_id, image_id).write_text(json.dumps({"reason": reason[:400]}, ensure_ascii=False), encoding="utf-8")


def process_page(client: TestClient, sid: str, iid: str, args) -> float:
    # Step 3(LaMa 지우기)는 terra가 영역을 정한 뒤 /api/guidance에서 돌아가므로 분석에서는 Step 1-2까지만 한다
    rpf.call(client, "POST", "/api/analyze", json={"session_id": sid, "image_id": iid, "force": False, "stop_after": "text_groups"})
    record = comic_app.image_record(sid, Path(comic_app.get_image(sid, iid)["input_path"]), comic_app.get_image(sid, iid).get("image_sha256"))
    guidance, _report, draw_plan = rpf.terra_guidance(record, args.ocr_model, allow_draw=not args.no_ai_edit)
    Path(record["work_dir"], "json", f"{iid}_ai_draw.json").write_text(json.dumps(draw_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rpf.call(client, "POST", "/api/guidance", json={"session_id": sid, "image_id": iid, "guidance": guidance})
    cost = 0.0
    if not args.no_ai_edit and (guidance["manual_regions"] or draw_plan["draws"]):
        cost = rpf.erase_regions(client, sid, iid, args.erase_quality, args.ocr_model, tight=args.tight)
    body = {"session_id": sid, "image_id": iid, "ocr_model": args.ocr_model, "force": True, "extra_prompt": args.extra_prompt}
    rpf.call(client, "POST", "/api/run-final", json=body)
    ensure_final(sid, iid)
    done_marker(sid, iid).write_text(json.dumps({"ai_cost_usd": round(cost, 4), "no_ai_edit": bool(args.no_ai_edit)}), encoding="utf-8")
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
    parser.add_argument("--only", help="screen·process: 쉼표로 구분한 페이지 ID만")
    parser.add_argument("--use-picks", action="store_true", help="series_select가 고른 페이지(work/selection.json)만 스크리닝·처리한다")
    parser.add_argument("--picks-file", help="선별 페이지 목록 JSON({\"picks\": [\"00001\", ...]})만 스크리닝·처리한다(프로젝트가 아직 없어도 된다)")
    parser.add_argument("--no-ai-edit", action="store_true", help="이미지 편집 모델을 쓰지 않는다(기존 LaMa 지우기 + 폰트 오버레이만). 안전 필터로 거절된 페이지용")
    parser.add_argument("--retry-failed", action="store_true", help="process: 이전에 실패 표식이 남은 페이지도 다시 시도")
    parser.add_argument("--tight", action="store_true", help="process: 영역마다 따로 작게 잘라 이미지 편집을 부른다(안전 필터에 걸린 페이지 재시도용)")
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
    only_ids: set[str] | None = set(args.only.split(",")) if args.only else None
    if args.picks_file:
        picks = set(json.loads(Path(args.picks_file).read_text(encoding="utf-8")).get("picks", []))
        only_ids = picks if only_ids is None else (only_ids & picks)
        print(f"선별 목록 {args.picks_file}: {len(only_ids)}장만 처리합니다", flush=True)
    if args.use_picks:
        picks_path = comic_app.session_dir(args.session_id) / "work" / "selection.json"
        picks = set(json.loads(picks_path.read_text(encoding="utf-8")).get("picks", [])) if picks_path.exists() else set()
        only_ids = picks if only_ids is None else (only_ids & picks)
        print(f"선별 페이지 {len(only_ids)}장만 처리합니다", flush=True)
    if args.stage in ("screen", "all"):
        st.run_screen(data, args.session_id, guide, args.ocr_model, args.workers, args.limit, only=only_ids)
    if args.stage in ("process", "all"):
        ids = redraw_ids(args.session_id, data)
        if only_ids is not None:
            ids = [i for i in ids if i in only_ids]
        pending = [i for i in ids if not done_marker(args.session_id, i).exists()]
        # 안전 필터 거절 등으로 실패한 페이지는 표식을 남겨 다음 실행에서 되풀이하지 않는다(--retry-failed로 다시 시도)
        todo = [i for i in pending if args.retry_failed or not failed_marker(args.session_id, i).exists()]
        if args.max_pages:
            todo = todo[: args.max_pages]
        print(f"처리 대상 {len(todo)}장 (판정 redraw {len(ids)}장 중 미완료 {len(pending)}장)", flush=True)
        spent, lock, failed = [0.0], threading.Lock(), []

        def attempt(iid: str) -> tuple[float, bool]:
            """안전 필터 거절은 이미지 편집 없이 대체 모드로 바로 처리하고, 그 밖의 오류는 20초 뒤 한 번 더 시도한다."""
            last: BaseException | None = None
            for tries in range(2):
                try:
                    return process_page(client, args.session_id, iid, args), False
                except (Exception, SystemExit) as exc:  # noqa: BLE001 - rpf.call은 오류를 SystemExit로 올린다
                    last = exc
                    if "safety system" in str(exc):
                        break
                    if tries == 0:
                        time.sleep(20)  # 마운트 끊김·시간 초과 같은 일시 오류
            if last is not None and "safety system" in str(last) and not args.no_ai_edit:
                fallback = copy.copy(args)
                fallback.no_ai_edit = True
                return process_page(client, args.session_id, iid, fallback), True
            raise last  # type: ignore[misc]

        def work(iid: str) -> None:
            with lock:
                if args.budget_usd is not None and spent[0] >= args.budget_usd:
                    print(f"  [{iid}] 예산 도달로 건너뜀", flush=True)
                    return
            try:
                cost, fell_back = attempt(iid)
            except SystemExit as exc:  # rpf.call이 오류를 SystemExit로 올린다
                mark_failed(args.session_id, iid, str(exc)); failed.append(iid); print(f"  [{iid}] 실패: {exc}", flush=True); return
            except Exception as exc:  # noqa: BLE001 - 한 장의 실패가 전체를 멈추지 않게 한다
                where = " > ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in traceback.extract_tb(exc.__traceback__)[-3:])
                mark_failed(args.session_id, iid, f"{type(exc).__name__}: {exc} @ {where}"); failed.append(iid)
                print(f"  [{iid}] 실패: {type(exc).__name__}: {str(exc)[:160]} @ {where}", flush=True); return
            failed_marker(args.session_id, iid).unlink(missing_ok=True)
            with lock:
                spent[0] += cost
                print(f"  [{iid}] 완료 AI ${cost:.4f} (누적 ${spent[0]:.3f}){' [안전 필터 거절 -> 이미지 편집 없는 대체 모드]' if fell_back else ''}", flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(work, i) for i in todo]):
                future.result()
        print(f"처리 종료: 누적 이미지 편집 ${spent[0]:.3f}, 실패 {len(failed)}장 {failed}", flush=True)
        remaining = [i for i in ids if not done_marker(args.session_id, i).exists() and not failed_marker(args.session_id, i).exists()]
        print(f"남은 대상 {len(remaining)}장", flush=True)  # 드라이버가 이 줄을 읽어 같은 권을 새 프로세스로 이어서 돌린다


if __name__ == "__main__":
    main()
