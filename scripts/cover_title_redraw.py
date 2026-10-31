#!/usr/bin/env python3
"""표지의 작품명 부분만 맥락 없이 잘라 이미지 편집 모델로 한국어로 바꾸고, 원본 위에 합성해 final로 저장한다.

용어집·terra 판단·페이지 설명은 보내지 않는다. 모델에는 잘라낸 제목 이미지와 짧은 지시문만 간다.
모델의 최소 입력 크기를 맞추려고 크롭을 비율 그대로 키워 보내고, 받은 결과는 원래 크기로 줄여 합성한다.
--copy-original은 다시 그릴 글자가 없는 표지(final을 원본 그대로 둔다)에 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import series_translate as st  # noqa: E402
from pipeline.ai_text_erase import MIN_PIXELS, erase_page_text  # noqa: E402
from pipeline.openai_tasks import IMAGE_MODEL  # noqa: E402

comic_app = st.comic_app
TARGET_PIXELS = 720_000
FEATHER = 5


def upscale_to_min(crop: np.ndarray) -> np.ndarray:
    h, w = crop.shape[:2]
    if h * w >= TARGET_PIXELS:
        return crop
    scale = (TARGET_PIXELS / (h * w)) ** 0.5
    return cv2.resize(crop, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_CUBIC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--rect", help="제목 영역 x,y,w,h (원본 픽셀)")
    parser.add_argument("--prompt", help="모델에 보낼 지시문(제목만 다루는 짧은 문장)")
    parser.add_argument("--quality", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--copy-original", action="store_true", help="다시 그릴 글자가 없는 표지: final을 원본 그대로 저장")
    args = parser.parse_args()

    item = comic_app.get_image(args.session_id, args.image_id)
    record = comic_app.image_record(args.session_id, Path(item["input_path"]), item.get("image_sha256"))
    page = comic_app._read_bgr(record["input_path"])
    final = Path(record["final_jpg"])
    final.parent.mkdir(parents=True, exist_ok=True)
    params = [cv2.IMWRITE_JPEG_QUALITY, 92]
    if args.copy_original:
        cv2.imwrite(str(final), page, params)
        print(f"원본 그대로 저장: {final}")
        return
    if not args.rect or not args.prompt:
        sys.exit("--rect와 --prompt가 필요합니다.")
    x, y, w, h = (int(v) for v in args.rect.split(","))
    crop = page[y:y + h, x:x + w].copy()
    sent = upscale_to_min(crop)
    if sent.shape[0] * sent.shape[1] < MIN_PIXELS:
        sys.exit("크롭이 모델 최소 크기보다 작습니다.")
    work_dir = Path(record["work_dir"]) / "cover_title"
    edited, meta = erase_page_text(sent, work_dir, model=IMAGE_MODEL, quality=args.quality, prompt=args.prompt)
    (work_dir / "sent.png").write_bytes(cv2.imencode(".png", sent)[1].tobytes())
    (work_dir / "received.png").write_bytes(cv2.imencode(".png", edited)[1].tobytes())
    edited = cv2.resize(edited, (w, h), interpolation=cv2.INTER_AREA)
    mask = np.zeros((h, w), dtype=np.float32)
    mask[FEATHER:h - FEATHER, FEATHER:w - FEATHER] = 1.0
    mask = cv2.GaussianBlur(mask, (0, 0), FEATHER / 2)[..., None]
    out = page.copy()
    blended = edited.astype(np.float32) * mask + crop.astype(np.float32) * (1 - mask)
    out[y:y + h, x:x + w] = np.clip(blended, 0, 255).astype(np.uint8)
    cv2.imwrite(str(final), out, params)
    (Path(record["work_dir"]) / "json" / f"{args.image_id}_cover_title.json").write_text(json.dumps(
        {"rect": [x, y, w, h], "prompt": args.prompt, "model": meta["model"], "quality": meta["quality"],
         "usage": {k: meta[k] for k in ("input_tokens", "output_tokens", "estimated_cost_usd", "request_size")}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"제목 합성 완료: {final} | 토큰 {meta['input_tokens']}/{meta['output_tokens']} ${meta['estimated_cost_usd']}")


if __name__ == "__main__":
    main()
