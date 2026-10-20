"""페이지별로 Step 3가 LaMa를 실제로 호출하는지 분류한다 (측정용 아님).

각 페이지를 별도 프로세스에서 build_dialogue_sheet로 돌리고 _get_lama 호출 수를 센다.
출력은 proof/bench/lama_probe/ 아래에만 쓴다.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_common import BENCH, PY, ROOT, dump_json  # noqa: E402

PAGES = ["00001", "00003", "00014", "00083", "00010", "00060", "00081", "00097", "00011", "00053"]
CODE = r'''
import json, sys
from pathlib import Path
import pipeline.dialogue_extractor as de
calls = {"n": 0}
orig = de._get_lama
def counted():
    calls["n"] += 1
    return orig()
de._get_lama = counted
src = Path(sys.argv[1]); out = Path(sys.argv[2]); page = sys.argv[3]
for sub in ("json", "masked", "sheets", "vis"):
    (out / sub).mkdir(parents=True, exist_ok=True)
de.build_dialogue_sheet(
    image_path=str(src / "input.jpg"), clusters_json=str(src / "text_groups.json"),
    output_masked_path=str(out / "masked" / f"{page}_masked.jpg"),
    output_json_path=str(out / "json" / f"{page}_regions.json"),
    indexed_output_sheet_path=str(out / "sheets" / f"{page}_indexed_sheet.jpg"),
    output_vis_path=str(out / "vis" / f"{page}_bubble_vis.jpg"),
)
print("LAMA_CALLS", calls["n"])
'''
result = {}
for page in PAGES:
    proc = subprocess.run([PY, "-c", CODE, str(BENCH / "data" / page), str(BENCH / "lama_probe" / page / "work"), page], capture_output=True, text=True, cwd=ROOT)
    calls = next((int(l.split()[1]) for l in proc.stdout.splitlines() if l.startswith("LAMA_CALLS")), None)
    result[page] = {"lama_calls": calls, "uses_lama": bool(calls)}
    print(page, result[page], proc.stderr[-300:] if calls is None else "", flush=True)
dump_json(BENCH / "lama_probe.json", result)
