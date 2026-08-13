#!/usr/bin/env python3
# FloodFill 파이프라인 배치 실행 스크립트 (test_image/ 폴더 기준)

import json
import os
import sys
import time
import traceback
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from pipeline.cloud_vision_api import CloudVisionAPI
from pipeline.cluster_cv_v5 import process_cv_clusters, format_json_output
from pipeline.floodfill_anchor_probe import apply_floodfill_white_mask, create_packed_ocr_sheet, visualize_anchor_floodfill
from pipeline.render_text_overlay import apply_text_overlay

load_dotenv()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

# ── 경로 설정 ───────────────────────────────────────────
IMAGE_DIR   = Path("test_image")
OUTPUT_DIR  = Path("temp_output/batch")
FONT_PATH   = "Fonts/NanumBarunpenB.otf"
MODEL_NAME  = "gemini-3.1-flash-lite-preview"

PACKED_OCR_PROMPT = """\
이 이미지는 일본 만화의 말풍선 텍스트를 모아 놓은 OCR sheet입니다.
상단에 R1, R2 ... 라벨이 있고, 각 라벨 바로 아래 칸에 해당 말풍선의 crop이 배치되어 있습니다.

각 Region에 대해 정확히 다음을 수행하세요.
1. ocr_text  : 이미지에 보이는 일본어 원문 그대로 (줄바꿈은 \\n)
2. text_translated : 자연스러운 한국어 번역 (가로쓰기)
3. orientation : "vertical" 또는 "horizontal"
4. text_type : "dialogue" / "sfx" / "narration" 중 하나

주의사항:
- 라벨(R1, R2 ...)은 OCR 결과에 포함하지 마세요.
- 빈 칸이거나 텍스트가 없으면 ocr_text를 "" 로 남기세요.
- region_id 는 라벨 번호(정수)를 그대로 사용하세요.

반드시 순수 JSON으로만 응답하세요 (Markdown 백틱 금지):
{"regions": [{"region_id": 1, "ocr_text": "...", "text_translated": "...", "orientation": "vertical", "text_type": "dialogue"}, ...]}
"""


# ── Step 함수들 ─────────────────────────────────────────

def step_cv_cluster(image_path: Path, out_dir: Path, img_num: str, force: bool = False):
    """CV API → 클러스터링 → cv_clusters_v5.json"""
    cv_json   = out_dir / f"{img_num}_cv.json"
    cv_cluster= out_dir / f"{img_num}_cv_clusters_v5.json"

    if not cv_json.exists() or force:
        print(f"  [CV] API 호출 중...")
        api = CloudVisionAPI()
        data = api.detect_text(str(image_path))
        if not data:
            raise RuntimeError("Cloud Vision API 호출 실패")
        cv_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"  [CV] → {cv_json.name}")
    else:
        print(f"  [CV] 캐시 사용: {cv_json.name}")

    if not cv_cluster.exists() or force:
        result = process_cv_clusters(str(cv_json), str(cv_cluster))
        cv_cluster.write_text(format_json_output(result), encoding="utf-8")
        print(f"  [CV] 클러스터: {len(result)}개 → {cv_cluster.name}")
    else:
        clusters = json.loads(cv_cluster.read_text())
        print(f"  [CV] 클러스터 캐시: {len(clusters)}개")

    return cv_cluster


def step_floodfill_mask(image_path: Path, cv_cluster: Path, out_dir: Path, img_num: str, force: bool = False):
    """FloodFill 마스킹 → holefilled_masked.jpg"""
    masked = out_dir / f"{img_num}_floodfill_holefilled_masked.jpg"

    if not masked.exists() or force:
        print(f"  [FF] FloodFill 마스킹 중...")
        apply_floodfill_white_mask(
            image_path=str(image_path),
            clusters_json=str(cv_cluster),
            output_image_path=str(masked),
        )
        print(f"  [FF] → {masked.name}")
    else:
        print(f"  [FF] 캐시 사용: {masked.name}")

    return masked


def step_packed_sheet(image_path: Path, cv_cluster: Path, out_dir: Path, img_num: str, force: bool = False):
    """packed OCR sheet 생성 (split 포함)"""
    sheet_jpg     = out_dir / f"{img_num}_packed_ocr_sheet_split.jpg"
    sheet_indexed = out_dir / f"{img_num}_packed_ocr_sheet_split_indexed.jpg"
    sheet_json    = out_dir / f"{img_num}_packed_ocr_sheet_split.json"

    if not sheet_indexed.exists() or force:
        print(f"  [SH] packed OCR sheet 생성 중...")
        result = create_packed_ocr_sheet(
            image_path=str(image_path),
            clusters_json=str(cv_cluster),
            output_image_path=str(sheet_jpg),
            output_json_path=str(sheet_json),
            indexed_output_image_path=str(sheet_indexed),
        )
        skipped = result.get("skipped", [])
        n_flood   = sum(1 for r in result["regions"] if r.get("source") != "bbox_fallback")
        n_bbox    = sum(1 for r in result["regions"] if r.get("source") == "bbox_fallback")
        n_unsafe  = sum(1 for s in skipped if s.get("reason") == "unsafe_region")
        n_noseed  = sum(1 for s in skipped if s.get("reason") == "no_seed")
        print(f"  [SH] {n_flood}개 flood + {n_bbox}개 bbox fallback  "
              f"(skip: unsafe={n_unsafe}, no_seed={n_noseed}) → {sheet_indexed.name}")
    else:
        result = json.loads(sheet_json.read_text(encoding="utf-8"))
        n_flood = sum(1 for r in result["regions"] if r.get("source") != "bbox_fallback")
        n_bbox  = sum(1 for r in result["regions"] if r.get("source") == "bbox_fallback")
        print(f"  [SH] 캐시 사용: {n_flood}개 flood + {n_bbox}개 bbox fallback")

    return sheet_indexed, sheet_json


def step_gemini_ocr(sheet_indexed: Path, out_dir: Path, img_num: str, force: bool = False):
    """Gemini OCR → ocr.json"""
    ocr_out = out_dir / f"{img_num}_floodfill_split_ocr.json"

    if not ocr_out.exists() or force:
        print(f"  [GM] Gemini OCR 중...")
        model = genai.GenerativeModel(MODEL_NAME)
        gen_cfg = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        )
        img_data = sheet_indexed.read_bytes()

        data = None
        for attempt in range(3):
            t0 = time.time()
            try:
                response = model.generate_content(
                    [PACKED_OCR_PROMPT, {"mime_type": "image/jpeg", "data": img_data}],
                    generation_config=gen_cfg,
                )
                elapsed = time.time() - t0
                raw = response.text.strip()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    clean = raw.split("```json")[-1].split("```")[0].strip()
                    data = json.loads(clean)
                break  # 성공
            except (json.JSONDecodeError, Exception) as e:
                elapsed = time.time() - t0
                print(f"  [GM] 시도 {attempt+1}/3 실패 ({elapsed:.1f}s): {e}")
                if attempt == 2:
                    raise
                time.sleep(3)

        regions = data.get("regions", [])
        result = {
            "model": MODEL_NAME,
            "image": str(sheet_indexed),
            "elapsed": round(elapsed, 2),
            "result": {"regions": regions},
        }
        ocr_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [GM] {len(regions)}개 region ({elapsed:.1f}s) → {ocr_out.name}")
    else:
        data = json.loads(ocr_out.read_text(encoding="utf-8"))
        regions = data["result"]["regions"]
        print(f"  [GM] 캐시 사용: {len(regions)}개 region")

    return ocr_out


def step_build_integrated(ocr_out: Path, sheet_json: Path, out_dir: Path, img_num: str, force: bool = False):
    """OCR + sheet bbox → integrated.json"""
    integrated = out_dir / f"{img_num}_floodfill_split_integrated.json"

    if not integrated.exists() or force:
        sheet   = json.loads(sheet_json.read_text(encoding="utf-8"))
        ocr_data= json.loads(ocr_out.read_text(encoding="utf-8"))
        regions = ocr_data["result"]["regions"]
        by_id   = {r["region_id"]: r for r in regions}

        clusters = []
        for region in sheet["regions"]:
            rid = region["region_id"]
            gr  = by_id.get(rid)
            if not gr:
                continue
            translated = gr.get("text_translated", "").strip()
            if not translated:
                continue
            clusters.append({
                "sentence_id"    : rid,
                "matched_cluster": region["anchor_ids"],
                "bounding_rect"  : region["original_flood_rect"],
                "orientation"    : gr.get("orientation", "vertical"),
                "location"       : "in_bubble",
                "text"           : gr.get("ocr_text", ""),
                "text_translated": translated,
            })

        result = {"clusters": clusters}
        integrated.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [IN] {len(clusters)}개 cluster → {integrated.name}")
    else:
        data = json.loads(integrated.read_text(encoding="utf-8"))
        print(f"  [IN] 캐시 사용: {len(data['clusters'])}개 cluster")

    return integrated


def step_visualize(image_path: Path, cv_cluster: Path, out_dir: Path, img_num: str, force: bool = False):
    """CV 텍스트 그룹 + FloodFill 결과 시각화 → _flood_vis.jpg"""
    vis_jpg  = out_dir / f"{img_num}_flood_vis.jpg"
    vis_json = out_dir / f"{img_num}_flood_vis.json"

    if not vis_jpg.exists() or force:
        print(f"  [VS] 시각화 생성 중...")
        result = visualize_anchor_floodfill(
            image_path=str(image_path),
            clusters_json=str(cv_cluster),
            output_json_path=str(vis_json),
            output_image_path=str(vis_jpg),
        )
        ok   = sum(1 for r in result["results"] if r.get("safe_region"))
        fail = sum(1 for r in result["results"] if not r.get("safe_region"))
        none_seed = sum(1 for r in result["results"] if r.get("seed") is None)
        print(f"  [VS] 성공:{ok}  실패(unsafe):{fail}  시드없음:{none_seed} → {vis_jpg.name}")
    else:
        print(f"  [VS] 캐시 사용: {vis_jpg.name}")

    return vis_jpg


def step_text_overlay(masked: Path, integrated: Path, cv_cluster: Path, out_dir: Path, img_num: str, force: bool = False):
    """text overlay → overlay.jpg"""
    overlay = out_dir / f"{img_num}_floodfill_split_overlay.jpg"

    if not overlay.exists() or force:
        print(f"  [OV] 텍스트 오버레이 중...")
        apply_text_overlay(
            masked_image_path=str(masked),
            integrated_json_path=str(integrated),
            cv_clusters_path=str(cv_cluster),
            output_path=str(overlay),
            font_path=FONT_PATH,
            base_font_size=33,
        )
        print(f"  [OV] → {overlay.name}")
    else:
        print(f"  [OV] 캐시 사용: {overlay.name}")

    return overlay


# ── 메인 배치 실행 ──────────────────────────────────────

def run_batch(start: int, end: int, force: bool = False):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    img_nums = [f"{n:05d}" for n in range(start, end + 1)]
    # 실제 파일이 있는 것만 필터
    targets = [(n, IMAGE_DIR / f"{n}.jpg") for n in img_nums if (IMAGE_DIR / f"{n}.jpg").exists()]

    if not targets:
        print(f"❌ {IMAGE_DIR}/ 에 {start:05d}~{end:05d} 이미지 없음")
        return

    print(f"{'='*60}")
    print(f"FloodFill 배치 파이프라인: {start:05d}~{end:05d}  ({len(targets)}장)")
    print(f"출력 디렉토리: {OUTPUT_DIR}")
    print(f"{'='*60}")

    summary = []
    total_start = time.time()

    for img_num, image_path in targets:
        t_page = time.time()
        print(f"\n{'─'*50}")
        print(f"🎬 {img_num}  ({image_path})")
        print(f"{'─'*50}")

        out_dir = OUTPUT_DIR / img_num
        out_dir.mkdir(parents=True, exist_ok=True)

        status = "✅"
        n_flood_ok = n_bbox_fallback = n_skip_unsafe = n_skip_noseed = n_integrated = 0
        try:
            cv_cluster  = step_cv_cluster(image_path, out_dir, img_num, force)
            step_visualize(image_path, cv_cluster, out_dir, img_num, force)
            masked      = step_floodfill_mask(image_path, cv_cluster, out_dir, img_num, force)
            sheet_idx, sheet_json = step_packed_sheet(image_path, cv_cluster, out_dir, img_num, force)

            # 실패 통계 수집
            sh_data = json.loads(sheet_json.read_text(encoding="utf-8"))
            n_flood_ok     = sum(1 for r in sh_data["regions"] if r.get("source") != "bbox_fallback")
            n_bbox_fallback= sum(1 for r in sh_data["regions"] if r.get("source") == "bbox_fallback")
            n_skip_unsafe  = sum(1 for s in sh_data.get("skipped", []) if s.get("reason") == "unsafe_region")
            n_skip_noseed  = sum(1 for s in sh_data.get("skipped", []) if s.get("reason") == "no_seed")

            ocr_out     = step_gemini_ocr(sheet_idx, out_dir, img_num, force)
            integrated  = step_build_integrated(ocr_out, sheet_json, out_dir, img_num, force)
            n_integrated = len(json.loads(integrated.read_text(encoding="utf-8")).get("clusters", []))
            overlay     = step_text_overlay(masked, integrated, cv_cluster, out_dir, img_num, force)

            elapsed = time.time() - t_page
            print(f"  ✅ 완료 ({elapsed:.1f}s) → {overlay}")
        except Exception as e:
            status = "❌"
            elapsed = time.time() - t_page
            print(f"  ❌ 오류 ({elapsed:.1f}s): {e}")
            traceback.print_exc()

        summary.append((
            img_num, status,
            round(time.time() - t_page, 1),
            n_flood_ok, n_bbox_fallback, n_skip_unsafe, n_skip_noseed, n_integrated,
        ))

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"배치 완료  총 {total_elapsed:.1f}s")
    print(f"{'='*60}")
    print(f"  {'이미지':>8}  {'상태':4}  {'시간':>6}  {'flood':>5}  {'bbox':>4}  {'unsafe':>6}  {'noseed':>6}  {'번역':>4}")
    for num, stat, sec, nf, nb, nu, ns, ni in summary:
        print(f"  {num}  {stat}  {sec:6.1f}s  {nf:5d}  {nb:4d}  {nu:6d}  {ns:6d}  {ni:4d}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FloodFill 배치 파이프라인")
    parser.add_argument("start", type=int, help="시작 번호 (예: 20)")
    parser.add_argument("end",   type=int, help="끝 번호 (예: 40)")
    parser.add_argument("--force", action="store_true", help="캐시 무시 강제 재처리")
    args = parser.parse_args()

    run_batch(args.start, args.end, force=args.force)
