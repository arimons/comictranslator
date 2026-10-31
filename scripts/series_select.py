#!/usr/bin/env python3
"""처리하지 않은 권들에서 AI로 처리할 만한 페이지를 기존 로컬 분석 결과로 고른다.

stage analyze  권마다 프로젝트를 만들고(없으면) 페이지를 분석한다(Step 1-3: Cloud Vision + 로컬 말풍선 검출).
               OpenAI 비용은 없다. 분석된 페이지는 건너뛴다.
stage pick     분석 결과의 `image_gen_review_candidate` 태그(로컬 로직이 말풍선·마스크를 못 잡은 영역)를 점수로 삼아
               전역 순위를 매기고, 표지(00001)와 태그 영역이 많은 페이지를 권별 work/selection.json에 저장한다.
선별 페이지는 `series_batch.py --use-picks`로 스크리닝·처리한다. 이미 처리한 페이지(완료 표식)는 뽑지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import series_translate as st  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

comic_app = st.comic_app
BASE = Path("/Volumes/FLASH/Comic/원본/초전자포")
TAG = "image_gen_review_candidate"


def session_id(vol: str) -> str:
    return f"20261031_railgun_{vol}"


def json_dir(vol: str) -> Path:
    return comic_app.session_dir(session_id(vol)) / "work" / "json"


def analyze_volume(client: TestClient, vol: str, workers: int) -> None:
    sid = session_id(vol)
    data = st.create_project(sid, BASE / vol, f"초전자포 {vol}")
    jd = json_dir(vol)
    jd.mkdir(parents=True, exist_ok=True)
    items = comic_app.sorted_image_records(data["images"])
    todo = [i for i in items if not (jd / f"{i['id']}_regions.json").exists()]
    print(f"{vol}: 분석 대상 {len(todo)}장 (전체 {len(items)}장)", flush=True)
    failed, lock = [], threading.Lock()

    def work(item: dict) -> None:
        for attempt in range(2):
            response = client.post("/api/analyze", json={"session_id": sid, "image_id": item["id"], "force": False})
            if response.status_code < 400:
                return
        with lock:
            failed.append(item["id"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, future in enumerate(as_completed([pool.submit(work, i) for i in todo]), start=1):
            future.result()
            if n % 25 == 0:
                print(f"  {vol} {n}/{len(todo)}", flush=True)
    print(f"{vol}: 분석 완료, 실패 {len(failed)}장 {failed[:10]}", flush=True)


def page_rows(vol: str) -> list[dict]:
    jd = json_dir(vol)
    rows = []
    for path in sorted(jd.glob("*_regions.json")):
        pid = path.name[: -len("_regions.json")]
        if (jd / f"{pid}_flow_done.json").exists():
            continue  # 이미 처리한 페이지
        try:
            regions = json.loads(path.read_text(encoding="utf-8")).get("regions", []) or []
        except (OSError, json.JSONDecodeError):
            continue
        n = len(regions)
        k = sum(1 for r in regions if TAG in (r.get("review_tags") or []))
        rows.append({"vol": vol, "id": pid, "n": n, "k": k, "cover": pid in {"00001", "cover"}})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--volumes", default="v15,v14,v13,v12,v11,v10,v09,v08,v07,v06,v05,v04,v03,v02,v01")
    parser.add_argument("--stage", choices=["analyze", "pick", "all"], default="all")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--min-flagged", type=int, default=3, help="pick: 태그 영역이 이 값 이상인 페이지를 후보로 본다")
    parser.add_argument("--pages", type=int, help="pick: 선별 장수 상한(점수 순)")
    args = parser.parse_args()
    vols = args.volumes.split(",")

    if args.stage in ("analyze", "all"):
        client = TestClient(comic_app.create_app())
        for vol in vols:
            analyze_volume(client, vol, args.workers)
    if args.stage in ("pick", "all"):
        rows = [r for vol in vols for r in page_rows(vol)]
        cover = [r for r in rows if r["cover"]]
        others = [r for r in rows if not r["cover"] and r["k"] >= args.min_flagged]
        others.sort(key=lambda r: (-r["k"], -(r["k"] / max(r["n"], 1)), -r["n"]))
        picks = cover + others
        if args.pages:
            picks = picks[: args.pages]
        by_vol: dict[str, list[dict]] = {}
        for r in picks:
            by_vol.setdefault(r["vol"], []).append(r)
        for vol in vols:
            chosen = by_vol.get(vol, [])
            out = json_dir(vol) / ".." / "selection.json"
            out.write_text(json.dumps({"picks": sorted(r["id"] for r in chosen),
                                       "scores": {r["id"]: {"flagged": r["k"], "regions": r["n"], "cover": r["cover"]} for r in chosen}},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{vol}: 선별 {len(chosen)}장 (분석된 미처리 페이지 {sum(1 for r in rows if r['vol'] == vol)}장 중)")
        print(f"합계 선별 {len(picks)}장: 표지 {len(cover)}장 + 태그 영역 {args.min_flagged}개 이상 {len(picks) - len(cover) if not args.pages else len([r for r in picks if not r['cover']])}장")


if __name__ == "__main__":
    main()
