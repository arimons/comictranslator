#!/usr/bin/env python3
"""배경 글자 복원 LaMa vs FLUX.1 Fill 비교·실측 스크립트.

같은 원본·마스크·문맥 영역으로 두 엔진을 돌리고 다음을 기록한다.
- 엔진별 최초 모델 로딩 시간과 순수 복원 시간(해상도·반복 횟수별)
- 마스크 바깥 변경 픽셀 수(0이어야 함)
- 복원 지표(pipeline.experimental.clone_stamp.restoration_score)
- 비교 시트 이미지(원본 | 마스크 | LaMa | FLUX ...)

예)
  .venv/bin/python proof/src/run_background_restore_compare.py \
      --image test_image/00123.jpg --regions .../00123_regions.json --text-groups .../00123_text_groups.json \
      --seed-index 0 --context-margin 96 --resolutions 512 768 --steps 20
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pipeline.background_restore import (  # noqa: E402
    MaskParams,
    RestoreParams,
    build_text_stroke_mask,
    clamp_rect,
    get_flux_client,
    run_restore,
    seeds_from_regions,
    suggest_context_rect,
)
from pipeline.experimental.clone_stamp import restoration_score  # noqa: E402


def label(img: np.ndarray, text: str) -> np.ndarray:
    out = cv2.copyMakeBorder(img, 22, 0, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    cv2.putText(out, text, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 200), 1, cv2.LINE_AA)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--regions", required=True)
    ap.add_argument("--text-groups", required=True)
    ap.add_argument("--seed-index", type=int, nargs="*", default=None, help="사용할 unboxed seed 번호(생략 시 전체)")
    ap.add_argument("--context", type=int, nargs=4, default=None, help="문맥 영역 x y w h")
    ap.add_argument("--context-margin", type=int, default=96)
    ap.add_argument("--dilate", type=int, default=4)
    ap.add_argument("--resolutions", nargs="+", default=["512", "768"])
    ap.add_argument("--steps", type=int, nargs="+", default=[20])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--engines", nargs="+", default=["lama", "flux"])
    ap.add_argument("--out", default=str(PROOF_DIR / 'restore_compare'))
    args = ap.parse_args()

    image = cv2.imread(args.image, cv2.IMREAD_COLOR)
    name = Path(args.image).stem
    seeds = seeds_from_regions(json.loads(Path(args.regions).read_text()), json.loads(Path(args.text_groups).read_text()))
    if args.seed_index is not None:
        seeds = [seeds[i] for i in args.seed_index]
    mask = build_text_stroke_mask(image, seeds, MaskParams(dilate_px=args.dilate))
    h, w = image.shape[:2]
    context = clamp_rect(args.context, w, h) if args.context else suggest_context_rect(mask, args.context_margin)
    out_root = ROOT / args.out / name
    out_root.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_root / "page_mask.png"), mask)

    records = []
    first_flux = True
    x, y, cw, ch = context
    tiles = [label(image[y:y + ch, x:x + cw], f"{name} original {cw}x{ch}")]
    mask_vis = image[y:y + ch, x:x + cw].copy()
    mm = mask[y:y + ch, x:x + cw] > 0
    mask_vis[mm] = (0.45 * mask_vis[mm] + np.array([0, 0, 140])).astype(np.uint8)
    tiles.append(label(mask_vis, f"mask dilate={args.dilate}"))

    if "flux" in args.engines:
        t0 = time.perf_counter()
        msg = get_flux_client().load()
        records.append({"engine": "flux", "event": "initial_load", "wall_sec": round(time.perf_counter() - t0, 2), "worker_load_sec": msg.get("load_sec")})
        print("FLUX load", records[-1], flush=True)
        first_flux = False

    for engine in args.engines:
        for res in args.resolutions:
            for steps in (args.steps if engine == "flux" else [0]):
                tag = f"{engine}_{res}" + (f"_s{steps}" if engine == "flux" else "")
                params = RestoreParams(engine=engine, resolution=res, steps=steps or 20, seed=args.seed)
                t0 = time.perf_counter()
                try:
                    result = run_restore(image, mask, context, params, out_root / tag,
                                         on_progress=lambda s, t: print(f"  {tag} step {s}/{t}", end="\r", flush=True))
                except Exception as exc:  # noqa: BLE001
                    records.append({"engine": engine, "tag": tag, "error": str(exc)})
                    print("ERROR", tag, exc, flush=True)
                    continue
                wall = time.perf_counter() - t0
                page = result["page"]
                score = restoration_score(image, page, mask)
                rec = {"engine": engine, "tag": tag, "resolution": res, "steps": steps or None, "wall_sec": round(wall, 2),
                       **result["meta"]["timing"], "model_size": [result["meta"]["transform"]["model_w"], result["meta"]["transform"]["model_h"]],
                       "outside_mask_changed_pixels": result["meta"]["outside_mask_changed_pixels"], "score": score}
                records.append(rec)
                print("\n", json.dumps(rec, ensure_ascii=False), flush=True)
                tiles.append(label(page[y:y + ch, x:x + cw], f"{tag} infer={rec.get('infer_sec')}s"))

    (out_root / "records.json").write_text(json.dumps({"context": context, "mask_pixels": int(np.count_nonzero(mask)), "records": records}, ensure_ascii=False, indent=2))
    th = max(t.shape[0] for t in tiles)
    row = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, 8, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
    cv2.imwrite(str(out_root / "compare.png"), np.hstack(row))
    print("saved", out_root / "compare.png")


if __name__ == "__main__":
    main()
