#!/usr/bin/env python3
"""썸네일 시트(한 장에 20페이지)를 terra에 보내 권별 특별 페이지(표지·캐릭터 소개·컬러 일러스트 등)를 싸게 고른다.

페이지당 약 170토큰이라 3,000장이 $1 안팎이다(전체 페이지 스크리닝은 장당 약 5,500토큰). 글자를 읽지 않고 레이아웃으로
페이지 종류를 판정하므로 일반 본문(story)·광고·판권·빈 페이지는 걸러지고, 번역할 글이 있는 특별 페이지만 후보가 된다.
결과는 --out-dir/<권>.json에 저장한다. --merge-dir에 같은 이름의 선별 목록(예: 컬러 페이지)이 있으면 합쳐서 --merged-dir에 쓴다.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

load_dotenv(ROOT / ".env")
BASE = Path("/Volumes/FLASH/Comic/원본/초전자포")
TW, TH, COLS, PER_SHEET = 300, 420, 5, 20
MODEL = "gpt-6.1-terra"
CANDIDATE_KINDS = {"cover", "character_intro", "color_illustration", "other"}
PRIORITY = {"cover": 0, "character_intro": 1, "color_illustration": 2, "other": 3}
PROMPT = """일본어 만화 단행본의 페이지 썸네일 시트입니다. 각 칸의 왼쪽 위 번호가 페이지 번호입니다.
각 칸이 어떤 종류의 페이지인지 판정해 JSON 객체 하나로만 답하세요.
{"pages": [{"n": 번호, "kind": "story | cover | character_intro | color_illustration | ad | colophon | blank | other", "translate": 번역할 일본어 글(제목·소개문·대사 등)이 있으면 true}]}
- story: 칸이 나뉜 일반 만화 본문. 광고·판권·빈 페이지·그림만 있는 페이지는 translate=false."""


def font() -> ImageFont.FreeTypeFont:
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if Path(path).exists():
            return ImageFont.truetype(path, 34)
    return ImageFont.load_default()


def make_sheet(files: list[Path]) -> str:
    label_font = font()
    sheet = Image.new("RGB", (COLS * TW, ((len(files) + COLS - 1) // COLS) * TH), "white")
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(files):
        with Image.open(path) as im:
            im.draft("RGB", (TW * 2, TH * 2))
            tile = im.convert("RGB")
        tile.thumbnail((TW - 6, TH - 6))
        x, y = (i % COLS) * TW, (i // COLS) * TH
        sheet.paste(tile, (x + 3, y + 3))
        draw.rectangle((x + 3, y + 3, x + 52, y + 44), fill="black")
        draw.text((x + 8, y + 5), str(i + 1), fill="yellow", font=label_font)
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def classify(client: OpenAI, files: list[Path]) -> tuple[list[dict], int]:
    url = make_sheet(files)
    for attempt in range(2):
        try:
            response = client.responses.create(
                model=MODEL, reasoning={"effort": "low"},
                input=[{"role": "user", "content": [{"type": "input_text", "text": PROMPT},
                                                   {"type": "input_image", "image_url": url, "detail": "high"}]}],
                text={"format": {"type": "json_object"}}, max_output_tokens=3000, store=False)
            rows = json.loads(response.output_text).get("pages", [])
            usage = response.usage
            return rows, int(usage.input_tokens + usage.output_tokens)
        except Exception as exc:  # noqa: BLE001 - 시트 하나의 실패가 전체를 멈추지 않게 한다
            last = exc
            time.sleep(3)
    print(f"  시트 실패: {type(last).__name__}: {str(last)[:100]}", flush=True)
    return [], 0


def color_page_ids(files: list[Path], threshold: float) -> set[str]:
    """평균 채도가 threshold 이상인 컬러 페이지(표지·컬러 일러스트 등). 로컬 계산이라 비용이 없다."""
    import numpy as np

    ids = set()
    for path in files:
        try:
            with Image.open(path) as im:
                im.draft("RGB", (320, 460))
                hsv = np.asarray(im.convert("RGB").resize((160, 230)).convert("HSV"), dtype=np.uint8)
        except Exception:  # noqa: BLE001
            continue
        sat, val = hsv[..., 1].astype(np.float32), hsv[..., 2].astype(np.float32)
        mask = (val > 40) & (val < 250)
        if mask.sum() >= 50 and float(sat[mask].mean()) >= threshold:
            ids.add(path.stem)
    return ids


def triage_volume(client: OpenAI, vol: str, workers: int, base: Path | None = None, color_sat: float | None = None) -> dict:
    base = base or BASE
    files = sorted(p for p in (base / vol).iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    chunks = [files[i:i + PER_SHEET] for i in range(0, len(files), PER_SHEET)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda chunk: classify(client, chunk), chunks))
    pages, tokens, missing = {}, 0, 0
    for chunk, (rows, used) in zip(chunks, results):
        tokens += used
        seen = set()
        for row in rows:
            n = row.get("n")
            if isinstance(n, int) and 1 <= n <= len(chunk):
                seen.add(n)
                pages[chunk[n - 1].stem] = {"kind": row.get("kind"), "translate": bool(row.get("translate"))}
        missing += len(chunk) - len(seen)
    picks = sorted((pid for pid, v in pages.items() if v["kind"] in CANDIDATE_KINDS and v["translate"]),
                   key=lambda pid: (PRIORITY.get(pages[pid]["kind"], 9), pid))
    color = color_page_ids(files, color_sat) if color_sat is not None else set()
    return {"picks": sorted(set(picks) | color), "rule": "thumbnail_sheet_triage" + ("+color" if color_sat is not None else ""),
            "pages": pages, "tokens": tokens, "missing": missing, "color": sorted(color)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--volumes", default="v15,v14,v13,v12,v11,v10,v09,v08,v07,v06,v05,v04,v03,v02,v01")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--merge-dir", help="합칠 기존 선별 목록 폴더(권별 <권>.json, 예: 컬러 페이지)")
    parser.add_argument("--merged-dir", help="합친 결과를 쓸 폴더(--merge-dir와 함께)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--base-dir", help="권 폴더들이 들어 있는 작품 폴더(기본: 초전자포)")
    parser.add_argument("--color-sat", type=float, help="평균 채도가 이 값 이상인 컬러 페이지도 후보에 합친다(예: 5)")
    args = parser.parse_args()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=1)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    total_tokens = total_picks = 0
    for vol in args.volumes.split(","):
        result = triage_volume(client, vol, args.workers, Path(args.base_dir) if args.base_dir else None, args.color_sat)
        (out / f"{vol}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        merged = set(result["picks"])
        if args.merge_dir and args.merged_dir:
            existing = Path(args.merge_dir) / f"{vol}.json"
            if existing.exists():
                merged |= set(json.loads(existing.read_text(encoding="utf-8")).get("picks", []))
            Path(args.merged_dir).mkdir(parents=True, exist_ok=True)
            (Path(args.merged_dir) / f"{vol}.json").write_text(
                json.dumps({"picks": sorted(merged), "rule": "triage+color"}, ensure_ascii=False, indent=2), encoding="utf-8")
        total_tokens += result["tokens"]
        total_picks += len(merged)
        kinds = {}
        for v in result["pages"].values():
            kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
        print(f"{vol}: 시트 선별 {len(result['picks'])}장(합친 후 {len(merged)}장) | 판정 누락 {result['missing']}장 | {kinds}", flush=True)
    print(f"합계 후보 {total_picks}장, 토큰 {total_tokens} (추정 ${total_tokens / 1e6:.2f}, 실제는 약 1.23배)", flush=True)


if __name__ == "__main__":
    main()
