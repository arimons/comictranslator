"""stream_subprocess로 명령 하나를 실행하고 wall time과 자식 peak RSS를 JSON으로 출력한다.

이 래퍼 프로세스는 자식 하나만 기다리므로 RUSAGE_CHILDREN.ru_maxrss가 곧 그 자식의 peak RSS다.
사용: bench_child.py '<json list cmd>'
"""
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.pipeline_runner import stream_subprocess  # noqa: E402

cmd = json.loads(sys.argv[1])
started = time.perf_counter()
code, lines = stream_subprocess(cmd, 600)
wall = time.perf_counter() - started
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
rss = usage.ru_maxrss  # macOS: bytes
cpu = usage.ru_utime + usage.ru_stime
print("BENCH_RESULT " + json.dumps({"returncode": code, "wall_s": wall, "maxrss_mb": rss / (1024 * 1024), "cpu_s": cpu, "tail": lines[-3:]}, ensure_ascii=False))
