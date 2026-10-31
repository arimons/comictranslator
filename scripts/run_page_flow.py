#!/usr/bin/env python3
"""한 페이지를 편집기와 같은 흐름으로 처리해 프로젝트에 R 영역·배경 패치·오버레이를 남긴다.

단계(--stage): bind(용어집 연결) -> analyze(CV·TG·R) -> regions(terra가 TG를 묶어 R 확정) -> erase(AI 글자 지우기 후 확정) -> final(OCR·번역·오버레이)
서버를 띄우지 않고 FastAPI TestClient로 앱 API를 그대로 호출하므로, 결과는 편집기에서 열어 다시 고칠 수 있다.
"""
from __future__ import annotations

import argparse
import json
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


RUBY_GUIDE = ("후리가나(루비)가 달린 단어는 후리가나 발음을 옮기지 말고 본 글자의 뜻을 한국어로 옮기세요. "
              "후리가나가 설정 용어의 뜻을 따로 설명하면 번역 뒤에 괄호로 짧게 덧붙이세요. "
              "예: 본 글자가 '초능력자'이고 후리가나가 '레벨 5'이면 '초능력자(레벨 5)'.")
REGION_PAD = 14
DRAW_MIN_LINE_RATIO = 0.030  # 글자 한 줄 높이가 페이지 가로폭의 3% 미만이면 AI가 그리지 않고 폰트로 넣는다
CLUE_MAX_SIDE = 1800
REGION_PROMPT = """일본어 만화 한 페이지입니다. 이미지에는 Cloud Vision이 찾은 글자 조각(TG)이 번호 박스로 그려져 있습니다.
CV 조각은 한 덩어리 글을 여러 조각으로 쪼개거나 일부 글자를 놓칠 수 있으니 번호는 단서로만 쓰고, 이미지를 보고 판단하세요.

JSON 객체 하나로만 답하세요.
{"regions": [
  {"tg_ids": [이 영역에 속하는 TG 번호들],
   "translate": 한국어로 번역해 다시 넣어야 하면 true (로마자 표기·쪽번호·영문 로고·장식은 false),
   "kind": "caption | title | label | sfx | keep",
   "draw": true면 이미지 모델이 한국어 글자를 직접 새로 그려야 하는 영역. 다음만 true입니다: (1) 제목·이름·성우 줄·라벨처럼 크고 장식적인 글자, (2) 배경 그림 위에 크게 쓰인 효과음·의성어·의태어(예: 쿵, 우당탕 같은 소리 글자)나 그림과 얽혀 있어 폰트로 얹기 어려운 글자. 말풍선 대사·생각 풍선·내레이션·박스 안 설명문처럼 읽는 글은 글자 양과 크기에 상관없이 반드시 false(지운 뒤 폰트로 넣음),
   "ko": draw=true일 때만 그 영역의 한국어 번역(작품 참고 정보의 표기 우선). 아니면 빈 문자열,
   "tg_incomplete": 이 영역의 글자 중 TG 박스로 덮이지 않은 부분이 있으면 true,
   "lines": 이 영역의 글자 줄 수(정수),
   "rect_norm": [x, y, w, h]  // 모든 영역(translate=false 포함)에 필수. 이 영역 글자 전체를 감싸는 사각형. 페이지 가로·세로를 각각 0-1000으로 본 좌표. 이웃한 다른 글자(로마자 등)는 포함하지 마세요
  }]}

규칙:
- 읽는 단위(한 문단, 한 줄 제목, 한 줄 라벨)가 하나의 영역입니다. 한 문단의 TG를 여러 영역으로 나누지 마세요.
- 반투명 박스 안의 문단은 박스 전체가 아니라 글자가 있는 부분을 하나의 영역으로 묶으세요.
- draw 판단은 글자의 종류로 하세요. 대사·내레이션은 짧아도 draw=false입니다. 말풍선 안의 작은 효과음 표기는 대사로 보고 draw=false입니다.
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


def terra_guidance(record: dict, model: str, allow_draw: bool = True) -> dict:
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

    def padded(rect: list[int]) -> list[int]:
        x, y, w, h = rect
        return [max(0, x - REGION_PAD), max(0, y - REGION_PAD), w + 2 * REGION_PAD, h + 2 * REGION_PAD]

    def norm_px(norm: list) -> list[int]:
        return [round(norm[0] * width / 1000), round(norm[1] * height / 1000),
                round(norm[2] * width / 1000), round(norm[3] * height / 1000)]

    manual, disabled, report, draws, keep_rects = [], set(), [], [], []
    used: set[int] = set()
    regions = list(enumerate(plan.get("regions", []), start=1))
    # 번역 영역이 TG를 먼저 가져간다. 한 TG는 한 영역에만 속한다(terra가 같은 TG를 중복 배정하는 경우가 있다)
    for index, region in sorted(regions, key=lambda pair: not pair[1].get("translate")):
        ids = [i for i in region.get("tg_ids", []) if i in by_id and i not in used]
        used.update(ids)
        norm = region.get("rect_norm")
        norm_rect = norm_px(norm) if isinstance(norm, list) and len(norm) >= 4 else None
        if not region.get("translate"):
            disabled.update(ids)
            if norm_rect:
                keep_rects.append(norm_rect)
            report.append({"tg_ids": ids, "translate": False, "kind": region.get("kind"), "rect": norm_rect})
            continue
        rects = [by_id[i]["tight_rect"] for i in ids]
        if norm_rect and (region.get("tg_incomplete") or not rects):
            rects.append(norm_rect)
        rect_cv = padded(union(rects)) if rects else None
        wants_draw = allow_draw and bool(region.get("draw")) and bool(str(region.get("ko", "")).strip())
        # CV가 제목·로마자를 한 TG로 묶는 경우가 있어, 그리는 영역은 terra가 눈으로 정한 사각형을 우선한다
        draw_rect = padded(norm_rect) if norm_rect else rect_cv
        if wants_draw and draw_rect:
            lines = max(1, int(region.get("lines") or 1))
            line_h = (draw_rect[3] - 2 * REGION_PAD) / lines
            if line_h >= DRAW_MIN_LINE_RATIO * width:
                disabled.update(ids)
                draws.append({"rect": draw_rect, "ko": str(region["ko"]).strip(), "kind": region.get("kind")})
                report.append({"tg_ids": ids, "translate": True, "draw": True, "kind": region.get("kind"),
                               "rect": draw_rect, "line_h": round(line_h), "ko": region["ko"]})
                continue
            report.append({"tg_ids": ids, "demoted_to_font": True, "line_h": round(line_h),
                           "min": round(DRAW_MIN_LINE_RATIO * width), "kind": region.get("kind")})
        if not rect_cv:
            continue
        manual.append({"id": f"terra_ubox_{index}", "type": "unboxed", "unboxed_rect": rect_cv, "source_method": "manual_ubox_guide"})
        report.append({"tg_ids": ids, "translate": True, "kind": region.get("kind"), "rect": rect_cv,
                       "tg_incomplete": bool(region.get("tg_incomplete"))})
    return {"manual_regions": manual, "disabled_tg_ids": sorted(disabled)}, report, {"draws": draws, "keep_rects": keep_rects}


ERASE_CROP_PROMPT = (
    "Remove ALL Japanese text from this image, including small furigana above words. If the text sits on a "
    "semi-transparent box, keep the box with its tint and rounded corners exactly, and restore the artwork or "
    "background behind the text naturally. Keep everything else exactly the same. "
    "Do not add any text, letters or new objects."
)
ERASE_MIN_PIXELS = 700_000  # 모델 최소(655,360)에 16배수 덧대기 여유를 더한 값
ERASE_MAX_RATIO = 2.8
ERASE_MARGIN = 60
STROKE_DILATE = 8
KEEP_PAD = 6
DRAW_MASK_SPREAD = 0.2
MODERATION_RETRIES = 3


def context_rect(rect: list[int], page_w: int, page_h: int) -> list[int]:
    """영역을 감싸되 모델 최소 크기·비율을 만족하도록 넓힌 크롭 사각형."""
    x, y, w, h = rect
    x1, y1, x2, y2 = x - ERASE_MARGIN, y - ERASE_MARGIN, x + w + ERASE_MARGIN, y + h + ERASE_MARGIN
    cw, ch = x2 - x1, y2 - y1
    if cw > ch * ERASE_MAX_RATIO:
        grow = int(cw / ERASE_MAX_RATIO - ch)
        y1, y2 = y1 - grow // 2, y2 + grow - grow // 2
    elif ch > cw * ERASE_MAX_RATIO:
        grow = int(ch / ERASE_MAX_RATIO - cw)
        x1, x2 = x1 - grow // 2, x2 + grow - grow // 2
    while (x2 - x1) * (y2 - y1) < ERASE_MIN_PIXELS:
        x1, y1, x2, y2 = x1 - 20, y1 - 20, x2 + 20, y2 + 20
    # 페이지 밖으로 나가면 반대쪽으로 민다(크기는 유지)
    dx = max(0, -x1) - max(0, x2 - page_w)
    dy = max(0, -y1) - max(0, y2 - page_h)
    x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(page_w, x2), min(page_h, y2)
    return [x1, y1, x2 - x1, y2 - y1]


def pct(rect: list[int], ctx: list[int]) -> str:
    x, y, w, h = rect
    cx, cy, cw, ch = ctx
    return (f"left {round((x - cx) / cw * 100)}%, top {round((y - cy) / ch * 100)}%, "
            f"width {round(w / cw * 100)}%, height {round(h / ch * 100)}%")


def build_edit_prompt(erase_rects: list[list[int]], draws: list[dict], ctx: list[int], extra: str) -> str:
    lines = ["This is a crop of a manga page. Edit it as described and keep everything else exactly the same "
             "(artwork, colors, lines, logos, Latin letters, layout)."]
    for item in draws:
        lines.append(f'- Large title text at ({pct(item["rect"], ctx)}): remove the Japanese title completely and redraw it as '
                     f'Korean text "{item["ko"]}" on one line, in a heavy bold display typeface with similar weight, size, '
                     "color and position as the original title. If the Korean text is longer than the original, reduce its size slightly "
                     "so it fits within about the same width.")
    if erase_rects:
        boxes = "; ".join(f"({pct(r, ctx)})" for r in erase_rects)
        lines.append(f"- Erase ALL Japanese text, including small furigana, inside these areas: {boxes}. Leave the areas "
                     "empty. If the text sits on a semi-transparent box, keep the box tint and rounded corners and restore "
                     "the background behind the text naturally.")
    lines.append("- Do not touch Latin/English text or logos. Do not add any other text, letters or objects.")
    if extra.strip():
        lines.append(extra.strip())
    return "\n".join(lines)


def erase_regions(client: TestClient, sid: str, iid: str, quality: str, model: str, extra: str = "", tight: bool = False) -> float:
    """영역들을 한 번의 이미지 호출로 처리한다(큰 제목은 한국어로 다시 그리기, 나머지는 글자 지우기).
    결과는 크롭 하나를 패치로 확정하고, 합성 마스크는 제목=영역 전체, 작은 글씨=글자 획 주변으로 만든다."""
    import hashlib

    import cv2
    import numpy as np

    from pipeline.ai_text_erase import MAX_SIDE, erase_page_text
    from pipeline.background_restore import MaskParams, build_text_stroke_mask
    from pipeline.gemini_image import image_model_for

    record = comic_app._restore_record(sid, iid)
    guidance = comic_app.load_json(record["guidance_json"], default={}) or {}
    draw_path = Path(record["work_dir"]) / "json" / f"{iid}_ai_draw.json"
    draw_plan = json.loads(draw_path.read_text(encoding="utf-8")) if draw_path.exists() else {}
    draws, keep_rects = draw_plan.get("draws", []), draw_plan.get("keep_rects", [])
    erase_rects = [item["unboxed_rect"] for item in guidance.get("manual_regions", []) if item.get("unboxed_rect")]
    if not erase_rects and not draws:
        sys.exit("처리할 영역이 없습니다. regions 단계를 먼저 실행하세요.")
    page = comic_app._read_bgr(record["input_path"])
    page_h, page_w = page.shape[:2]
    for old in comic_app._restore_state(record).get("patches", []):
        if old.get("engine") == comic_app.AI_ERASE_ENGINE:
            call(client, "POST", "/api/restore/patch/remove", json={"session_id": sid, "image_id": iid, "patch_id": old["id"]})
    stroke_mask = build_text_stroke_mask(page, [{"rect": r, "fragments": []} for r in erase_rects], MaskParams(dilate_px=STROKE_DILATE))
    image_model = image_model_for(model, comic_app.IMAGE_MODEL)

    everything = [("erase", r) for r in erase_rects] + [("draw", d["rect"]) for d in draws]
    ctx = context_rect(union([r for _, r in everything]), page_w, page_h)
    if not tight and max(ctx[2], ctx[3]) <= MAX_SIDE:
        groups = [(erase_rects, draws)]
    else:  # 한 크롭에 다 안 들어가면 항목별로 따로 처리한다
        groups = [([r], []) for r in erase_rects] + [([], [d]) for d in draws]
    total = 0.0
    for gi, (g_erase, g_draws) in enumerate(groups, start=1):
        cx, cy, cw, ch = context_rect(union(g_erase + [d["rect"] for d in g_draws]), page_w, page_h)
        crop = page[cy:cy + ch, cx:cx + cw].copy()
        prompt = build_edit_prompt(g_erase, g_draws, [cx, cy, cw, ch], extra)
        job_id = f"{time.strftime('%H%M%S')}_ai-erase_{hashlib.sha1((str(g_erase) + str(g_draws)).encode()).hexdigest()[:6]}"
        job_dir = Path(record["restore_dir"]) / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        for attempt in range(1, MODERATION_RETRIES + 1):
            try:
                edited, meta = erase_page_text(crop, job_dir, model=image_model, quality=quality, prompt=prompt)
                break
            except Exception as exc:  # noqa: BLE001
                # 생성 결과가 안전 필터(moderation_blocked, output 단계)에 걸리면 결과가 매번 달라서 다시 시도하면 통과하기도 한다
                if "moderation_blocked" in str(exc) and attempt < MODERATION_RETRIES:
                    print(f"  생성 결과가 안전 필터에 걸려 다시 시도합니다({attempt}/{MODERATION_RETRIES})", flush=True)
                    continue
                raise
        mask = np.zeros((page_h, page_w), dtype=np.uint8)
        for r in g_erase:
            x, y, w, h = r
            mask[y:y + h, x:x + w] = stroke_mask[y:y + h, x:x + w]
        for d in g_draws:
            x, y, w, h = d["rect"]
            # 한국어가 일본어보다 길어질 수 있어 좌우를 넓혀 합성한다
            gx = int(w * DRAW_MASK_SPREAD)
            mask[y:y + h, max(0, x - gx):x + w + gx] = 255
            for kx, ky, kw, kh in keep_rects:  # 로마자 등 보존 영역은 AI 결과를 합성하지 않는다
                mask[max(0, ky - KEEP_PAD):ky + kh + KEEP_PAD, max(0, kx - KEEP_PAD):kx + kw + KEEP_PAD] = 0
            # 순백이던 자리는 순백으로 되돌린다(이미지 모델이 흰색을 살짝 회색으로 바꾸는 것을 막는다)
            sub_o = crop[max(0, y - cy):max(0, y - cy) + h, max(0, x - cx):max(0, x - cx) + w]
            sub_e = edited[max(0, y - cy):max(0, y - cy) + sub_o.shape[0], max(0, x - cx):max(0, x - cx) + sub_o.shape[1]]
            white = (sub_o.min(axis=2) >= 250) & (sub_e.min(axis=2) >= 238)
            sub_e[white] = sub_o[white]
        mask = mask[cy:cy + ch, cx:cx + cw]
        cv2.imwrite(str(job_dir / "crop_before.png"), crop)
        cv2.imwrite(str(job_dir / "crop_after.png"), edited)
        cv2.imwrite(str(job_dir / "crop_mask.png"), mask)
        usage = {k: meta[k] for k in ("input_tokens", "output_tokens", "estimated_cost_usd", "request_size")}
        with comic_app._RESTORE_JOBS_LOCK:
            comic_app._RESTORE_JOBS[job_id] = {
                "job_id": job_id, "session_id": sid, "image_id": iid, "engine": comic_app.AI_ERASE_ENGINE,
                "source": "original", "status": "done", "_job_dir": str(job_dir),
                "meta": {"context_rect": [cx, cy, cw, ch],
                         "params": {"engine": comic_app.AI_ERASE_ENGINE, "model": meta["model"], "quality": meta["quality"]},
                         "timing": {"total_sec": meta["elapsed_sec"]}, "usage": usage}}
        call(client, "POST", "/api/restore/accept", json={"session_id": sid, "image_id": iid, "job_id": job_id})
        # 확정된 패치는 patches/에 복사돼 있다. 작업 폴더의 PNG는 중복이라 지워 디스크를 아낀다(prompt.txt는 남김)
        for png in job_dir.glob("*.png"):
            png.unlink(missing_ok=True)
        total += meta["estimated_cost_usd"]
        print(f"  호출 {gi}/{len(groups)} 확정 지우기 {len(g_erase)}·그리기 {len(g_draws)} 크롭={[cx, cy, cw, ch]} "
              f"토큰 {usage['input_tokens']}/{usage['output_tokens']} ${meta['estimated_cost_usd']}", flush=True)
    print(f"AI 편집 완료: 호출 {len(groups)}회, 추정 ${round(total, 4)}", flush=True)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--stage", choices=["bind", "analyze", "regions", "erase", "final", "all"], default="all")
    parser.add_argument("--erase-quality", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--ocr-model", default=TERRA_MODEL)
    parser.add_argument("--font", help="오버레이 폰트 경로(비우면 기본 폰트)")
    parser.add_argument("--extra-prompt", default=RUBY_GUIDE, help="final: Step 4 OCR·번역에 덧붙일 지시")
    parser.add_argument("--prompt-extra", default="", help="erase: 이미지 편집 프롬프트 끝에 덧붙일 지시(프롬프트 제어 시험용)")
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
        guidance, report, draw_plan = terra_guidance(record, args.ocr_model)
        Path(record["work_dir"], "json", f"{iid}_ai_draw.json").write_text(
            json.dumps(draw_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        for row in report:
            print("  ", row, flush=True)
        call(client, "POST", "/api/guidance", json={"session_id": sid, "image_id": iid, "guidance": guidance})
        print(f"R 영역 {len(guidance['manual_regions'])}개 확정(제외 TG {guidance['disabled_tg_ids']})", flush=True)
    if "erase" in stages:
        erase_regions(client, sid, iid, args.erase_quality, args.ocr_model, args.prompt_extra)
    if "final" in stages:
        body = {"session_id": sid, "image_id": iid, "ocr_model": args.ocr_model, "force": args.force,
                "extra_prompt": args.extra_prompt}
        if args.font:
            body["font_path"] = args.font
        call(client, "POST", "/api/run-final", json=body)
        print("OCR·번역·오버레이 완료", flush=True)


if __name__ == "__main__":
    main()
