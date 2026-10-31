"""Step 3(가이던스 적용)를 같은 프로세스에서 반복 호출할 때의 메모리 증가를 잰다. 조사 메모: docs/BATCH_MEMORY_GROWTH_NOTES.md

분석 캐시가 있는 v20 이미지 8장으로 임시 프로젝트(20261031_probe_mem)를 만들고, 페이지마다 /api/analyze와 /api/guidance(빈 가이던스)를
부른 뒤 `footprint`(압축·스왑 포함 실제 점유량)와 tracemalloc(파이썬 객체 추적량)을 출력한다. 끝나면 임시 프로젝트를 직접 지운다.
맥미니의 저장소 루트에서 `.venv/bin/python scripts/probe_step3_memory.py`로 실행한다. 경로는 맥미니 기준으로 하드코딩돼 있다.
"""
import os, re, subprocess, sys, tempfile, tracemalloc
from pathlib import Path
sys.path.insert(0, "scripts"); sys.path.insert(0, ".")
import run_page_flow as rpf, series_translate as st
from fastapi.testclient import TestClient

c = st.comic_app
src = "/Volumes/FLASH/Comic/원본/초전자포/v20"
tmp = tempfile.mkdtemp()
ids = [f"{i:05d}" for i in (20, 21, 22, 23, 24, 25, 26, 27)]
for i in ids:
    os.symlink(f"{src}/{i}.jpg", f"{tmp}/{i}.jpg")
sid = "20261031_probe_mem"
st.create_project(sid, Path(tmp), "probe")
client = TestClient(c.create_app())

def fp():
    out = subprocess.run(["footprint", str(os.getpid())], capture_output=True, text=True).stdout
    return int(re.search(r"Footprint: (\d+) MB", out).group(1))

base = fp(); print("기준(앱 로드 직후) footprint", base, "MB", flush=True)
tracemalloc.start(12)
for n, iid in enumerate(ids, 1):
    rpf.call(client, "POST", "/api/analyze", json={"session_id": sid, "image_id": iid, "force": False})
    a = fp()
    rpf.call(client, "POST", "/api/guidance", json={"session_id": sid, "image_id": iid, "guidance": {"manual_regions": [], "disabled_tg_ids": []}})
    b = fp()
    cur, peak = tracemalloc.get_traced_memory()
    print(f"{n}번째 {iid}: analyze 후 +{a-base}MB, guidance 후 +{b-base}MB (이번 guidance +{b-a}MB) | 파이썬 추적 {cur//2**20}MB", flush=True)
snap = tracemalloc.take_snapshot()
print("--- 파이썬 할당 상위(줄 단위)")
for s in snap.statistics("lineno")[:10]:
    print(f"{s.size//2**20:5d}MB {s.count:7d}개 {s.traceback[0].filename.split('/')[-2]}/{s.traceback[0].filename.split('/')[-1]}:{s.traceback[0].lineno}")
