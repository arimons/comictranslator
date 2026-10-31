#!/usr/bin/env python3
"""초전자포 프로젝트들의 누적 지출을 추정해 출력한다. 예산(--limit)을 넘으면 종료 코드 1을 돌려준다.

- 이미지 편집: 처리 표식(*_flow_done.json)에 기록된 추정 비용의 합(실측 토큰 기준).
- terra: 스크리닝 토큰은 실측이고, 처리한 페이지는 영역 확정·OCR·번역 호출을 스크리닝과 같은 토큰으로 가정해 2배를 더한다. 단가는 100만 토큰당 $1(입출력 합산)로 가정한다.
이 추정에는 이 프로젝트들 밖에서 쓴 비용(시험·재시도로 중복된 호출 등)이 들어 있지 않다.
"""
import argparse
import json
import sys
from pathlib import Path

TERRA_USD_PER_TOKEN = 1 / 1_000_000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=float, help="추정 지출이 이 값 이상이면 종료 코드 1")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    root = Path.home() / "ComicTranslator/projects"
    ai = screen = processed_screen = 0.0
    pages = 0
    for project in sorted([*root.glob("*_railgun_*"), *root.glob("*_shibou_*")]):
        json_dir = project / "work/json"
        done = {f.name[: -len("_flow_done.json")] for f in json_dir.glob("*_flow_done.json")}
        for marker in json_dir.glob("*_flow_done.json"):
            ai += json.loads(marker.read_text(encoding="utf-8")).get("ai_cost_usd", 0)
        for path in json_dir.glob("*_screen.json"):
            usage = json.loads(path.read_text(encoding="utf-8")).get("_usage") or {}
            cost = ((usage.get("input") or 0) + (usage.get("output") or 0)) * TERRA_USD_PER_TOKEN
            screen += cost
            if path.name[: -len("_screen.json")] in done:
                processed_screen += cost
                pages += 1
    terra = screen + 2 * processed_screen
    total = ai + terra
    if not args.quiet:
        print(f"처리 {pages}장 | 이미지 편집 ${ai:.2f} + terra(추정) ${terra:.2f} = 추정 지출 ${total:.2f}")
    if args.limit is not None and total >= args.limit:
        sys.exit(1)


if __name__ == "__main__":
    main()
