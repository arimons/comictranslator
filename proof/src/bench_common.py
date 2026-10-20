"""성능 벤치마크 공용 도구: 부하 게이트, 부하 샘플러, 통계.

다른 무거운 작업과 겹친 측정을 막기 위해 매 측정 전에 1분 load average를 확인하고,
측정 중 부하를 샘플링해 기록한다. 측정 중 부하가 DISCARD_LOAD를 넘으면 그 샘플은 버린다.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path

import hashlib
import json
import math
import os
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = PROOF_DIR / "bench"
PY = str(ROOT / ".venv" / "bin" / "python")
GATE_LOAD = 4.0
DISCARD_LOAD = 6.0
GATE_TIMEOUT_S = 4 * 3600


def load1() -> float:
    return os.getloadavg()[0]


def wait_for_load(gate: float = GATE_LOAD, timeout: float = GATE_TIMEOUT_S, poll: float = 5.0) -> float:
    started = time.time()
    value = load1()
    while value >= gate:
        if time.time() - started > timeout:
            raise RuntimeError(f"load gate timeout: load1={value:.2f}")
        print(f"  [gate] load1={value:.2f} >= {gate}, 대기", file=sys.stderr, flush=True)
        time.sleep(poll)
        value = load1()
    return value


class LoadSampler:
    """측정 구간의 최대 load1을 기록한다."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.max_load = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.max_load = max(self.max_load, load1())
            self._stop.wait(self.interval)

    def __enter__(self):
        self.max_load = load1()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.max_load = max(self.max_load, load1())
        return False


def gated_sample(fn, retries: int = 3) -> dict:
    """부하 게이트 통과 후 fn()을 실행한다. fn은 dict를 반환한다. 부하 초과 샘플은 재시도."""
    for attempt in range(retries + 1):
        before = wait_for_load()
        with LoadSampler() as sampler:
            result = fn()
        sample = {**result, "load_before": round(before, 2), "load_max": round(sampler.max_load, 2)}
        # 측정 대상 자신이 만든 부하(멀티스레드 torch 등)는 빼고 외부 부하만 판정한다.
        # 1분 load average는 EMA라서 k코어를 t초 쓰면 대략 k*(1-exp(-t/60))만큼 오른다.
        self_rise = 0.0
        if result.get("cpu_s") and result.get("wall_s"):
            cores = result["cpu_s"] / max(result["wall_s"], 1e-6)
            self_rise = cores * (1 - math.exp(-result["wall_s"] / 60.0))
        sample["self_load_est"] = round(self_rise, 2)
        sample["external_load_est"] = round(sampler.max_load - self_rise, 2)
        if sampler.max_load - self_rise <= DISCARD_LOAD:
            sample["discarded_retries"] = attempt
            return sample
        print(f"  [discard] load_max={sampler.max_load:.2f} > {DISCARD_LOAD}, 재측정", file=sys.stderr, flush=True)
    sample["discarded_retries"] = retries
    sample["over_load"] = True
    return sample


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
