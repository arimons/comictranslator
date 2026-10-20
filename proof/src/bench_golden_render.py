import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path, manifest_files
from pipeline.manual_guidance import load_json
"""I5 골든 렌더: 세션 전체 페이지를 apply_text_overlay로 렌더해 PNG(무손실)와 layout JSON을 남긴다.

temp_output/ 은 읽기만 하고, 결과는 proof/bench/golden_<label>/<session>/ 에 쓴다.
render_overlay_for_record와 같은 인자 구성(기본 폰트, overlay guidance, restore state)을 쓴다.
사용: bench_golden_render.py --label before|after
      bench_golden_render.py --compare before after
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_common import BENCH, ROOT, dump_json, wait_for_load  # noqa: E402

SESSIONS = ["29991231_000000_sandbox", "20260530_144110", "20260530_104431"]


def render(label: str) -> None:
    sys.path.insert(0, str(ROOT))
    from pipeline.pipeline_runner import DEFAULT_FONT
    from pipeline.render_text_overlay import apply_text_overlay

    out_root = BENCH / f"golden_{label}"
    summary = {}
    started = time.time()
    for sid in SESSIONS:
        base = PROJECTS_DIR / sid
        session = load_json(manifest_path(base))
        rendered = skipped = 0
        for item in session.get("images", []):
            stem = Path(item["input_path"]).stem
            j = base / "work" / "json"
            integrated = j / f"{stem}_integrated.json"
            masked = base / "work" / "masked" / f"{stem}_masked.jpg"
            if not integrated.exists() or not masked.exists():
                skipped += 1
                continue
            data = json.loads(integrated.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("skipped"):
                skipped += 1
                continue
            wait_for_load(6.0)
            regions = j / f"{stem}_regions.json"
            og = j / f"{stem}_overlay_guidance.json"
            restore_state = base / "work" / "restore" / stem / "state.json"
            original = Path(item["input_path"])
            out_dir = out_root / sid
            out_dir.mkdir(parents=True, exist_ok=True)
            apply_text_overlay(
                masked,
                integrated,
                out_dir / f"{stem}_final.png",
                regions_json=regions if regions.exists() else None,
                font_path=Path(DEFAULT_FONT),
                original_image_path=original if original.exists() else None,
                tracking_offset=None,
                overlay_guidance_path=og if og.exists() else None,
                overlay_layout_output=out_dir / f"{stem}_layout.json",
                restore_state_path=restore_state if restore_state.exists() else None,
            )
            rendered += 1
        summary[sid] = {"rendered": rendered, "skipped": skipped}
        print(sid, summary[sid], flush=True)
    summary["elapsed_s"] = round(time.time() - started, 1)
    dump_json(out_root / "summary.json", summary)


def compare(a: str, b: str) -> None:
    import cv2
    import numpy as np

    ra, rb = BENCH / f"golden_{a}", BENCH / f"golden_{b}"
    files = sorted(p.relative_to(ra) for p in ra.rglob("*") if p.is_file() and p.name != "summary.json")
    report = {"files": len(files), "png_pixel_identical": 0, "png_byte_identical": 0, "json_identical": 0, "diffs": []}
    for rel in files:
        pa, pb = ra / rel, rb / rel
        if not pb.exists():
            report["diffs"].append({"file": str(rel), "reason": "missing"})
            continue
        if rel.suffix == ".png":
            if pa.read_bytes() == pb.read_bytes():
                report["png_byte_identical"] += 1
            ia, ib = cv2.imread(str(pa)), cv2.imread(str(pb))
            if ia.shape == ib.shape and np.array_equal(ia, ib):
                report["png_pixel_identical"] += 1
            else:
                diff = int(np.count_nonzero(np.any(ia != ib, axis=2))) if ia.shape == ib.shape else -1
                report["diffs"].append({"file": str(rel), "diff_pixels": diff})
        else:
            if pa.read_bytes() == pb.read_bytes():
                report["json_identical"] += 1
            else:
                report["diffs"].append({"file": str(rel), "reason": "json differs"})
    dump_json(BENCH / f"golden_compare_{a}_vs_{b}.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "diffs"}), "diffs:", len(report["diffs"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label")
    parser.add_argument("--compare", nargs=2)
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
    else:
        render(args.label)
