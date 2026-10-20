"""Step 3 A/B 교차 측정: 변경 전 스냅숏(proof/bench/baseline_src/dialogue_extractor.py) vs 현재 모듈.

같은 페이지를 A,B,A,B... 순서로 번갈아 돌려 시간대 영향(부하 변화)을 줄인다. 출력은 proof/bench/ab/ 아래.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_common import BENCH, PY, dump_json, gated_sample, stats  # noqa: E402
from bench_run import run_child  # noqa: E402

PAGES = sys.argv[1].split(",") if len(sys.argv) > 1 else ["00014", "00010"]
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
VARIANTS = {"before": [PY, str(BENCH / "baseline_src" / "dialogue_extractor.py")], "after": [PY, "-m", "pipeline.dialogue_extractor"]}
result = {}
for page in PAGES:
    src = BENCH / "data" / page
    samples = {k: [] for k in VARIANTS}
    for _ in range(REPS):
        for name, prefix in VARIANTS.items():
            work = BENCH / "ab" / name / page
            for sub in ("json", "masked", "sheets", "vis"):
                (work / sub).mkdir(parents=True, exist_ok=True)
            cmd = prefix + [
                str(src / "input.jpg"), str(src / "text_groups.json"),
                str(work / "masked" / "m.jpg"), str(work / "json" / "r.json"), str(work / "sheets" / "s.jpg"),
                "--output-vis-path", str(work / "vis" / "v.jpg"),
            ]
            samples[name].append(gated_sample(lambda: run_child(cmd)))
    result[page] = {
        name: {"wall": stats([s["wall_s"] for s in v]), "maxrss_mb": stats([s["maxrss_mb"] for s in v]), "load_max": max(s["load_max"] for s in v)}
        for name, v in samples.items()
    }
    print(page, json.dumps({k: (v["wall"]["median"], round(v["maxrss_mb"]["median"])) for k, v in result[page].items()}), flush=True)
dump_json(BENCH / "results_ab_step3.json", result)
