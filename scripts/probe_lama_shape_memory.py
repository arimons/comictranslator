"""LaMa(MPS)를 입력 크기를 바꿔 가며 반복 호출할 때 footprint를 잰다. 조사 메모: docs/BATCH_MEMORY_GROWTH_NOTES.md

사용: .venv/bin/python scripts/probe_lama_shape_memory.py {fixed|vary|vary_empty|bucket|bucket_empty}  (DEV=cpu로 장치 변경)
API 호출이 없고 임의 이미지만 쓴다.
"""
import os, re, subprocess, sys, gc
import numpy as np, torch
from PIL import Image
from simple_lama_inpainting import SimpleLama
mode = sys.argv[1]  # vary | fixed | vary_empty | vary_nojit
dev = torch.device(os.environ.get("DEV", "mps"))
def fp():
    out = subprocess.run(["footprint", str(os.getpid())], capture_output=True, text=True).stdout
    return int(re.search(r"Footprint: (\d+) MB", out).group(1))
if mode == "vary_nojit":
    torch._C._jit_set_profiling_executor(False)
lama = SimpleLama(dev)
rng = np.random.default_rng(0)
base = fp(); print("load", base, flush=True)
for i in range(120):
    if mode == "fixed":
        h, w = 320, 320
    else:
        h, w = int(rng.integers(100, 700)), int(rng.integers(100, 700))
        if mode.startswith("bucket"):
            h, w = -(-h // 128) * 128, -(-w // 128) * 128
    img = Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))
    m = np.zeros((h, w), np.uint8); m[h//4:h//2, w//4:w//2] = 255
    lama(img, Image.fromarray(m))
    if mode in ("vary_empty", "bucket_empty"):
        gc.collect(); torch.mps.empty_cache()
    if i % 10 == 9:
        print(i + 1, fp(), flush=True)
