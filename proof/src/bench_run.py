"""성능 측정 본체 (before/after 동일 프로토콜).

M1  Step 2 / Step 3 / Step 6 subprocess wall time (pipeline_runner.stream_subprocess 경유),
    모듈 import 시간, Step 3 peak RSS
M2  /api/overlay/apply 요청당 시간 (warm, 3페이지) + image_record probe 비용
M3  GET /api/session (170페이지 사본): payload bytes, 시간, 파일시스템 쓰기 수

한 번에 무거운 프로세스 하나만 돌린다. 매 샘플 전에 load gate(<4.0)를 통과해야 하고,
측정 중 load가 6을 넘으면 그 샘플은 버리고 다시 잰다.

사용: bench_run.py --label before|after [--only m1,m2,m3] [--reps 5]
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.project_paths import manifest_path, manifest_files
from pipeline.manual_guidance import load_json

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_common import BENCH, PY, ROOT, dump_json, gated_sample, sha256_file, stats  # noqa: E402

DATA = BENCH / "data"
PAGES = ["00001", "00003", "00014", "00083", "00010", "00060", "00081", "00097", "00011", "00053"]
OVERLAY_PAGES = ["00003", "00083", "00011"]
# 축소 측정: BENCH_PAGES="00003,00014,00010" 처럼 페이지를 고르고, BENCH_SKIP_STEP2=1이면 Step 2는 건너뛴다.
if os.environ.get("BENCH_PAGES"):
    PAGES = os.environ["BENCH_PAGES"].split(",")
SID = "20260530_144110"
CHILD = str(Path(__file__).resolve().parent / "bench_child.py")


def run_child(cmd: list[str]) -> dict:
    proc = subprocess.run([PY, CHILD, json.dumps(cmd)], capture_output=True, text=True, cwd=ROOT)
    for line in proc.stdout.splitlines():
        if line.startswith("BENCH_RESULT "):
            result = json.loads(line[len("BENCH_RESULT "):])
            if result["returncode"] != 0:
                raise RuntimeError(f"child failed: {cmd}\n{proc.stdout[-2000:]}")
            return result
    raise RuntimeError(f"no result: {proc.stdout[-2000:]} {proc.stderr[-2000:]}")


def image_height(path: Path) -> int:
    import cv2

    img = cv2.imread(str(path))
    return img.shape[0] if img is not None else 1920


def pixel_hash(path: Path) -> str | None:
    import hashlib

    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    return hashlib.sha256(img.tobytes()).hexdigest()


def snapshot_outputs(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            entry = {"sha256": sha256_file(path)}
            if path.suffix.lower() in {".jpg", ".png"}:
                entry["pixels"] = pixel_hash(path)
            out[rel] = entry
    return out


# ───────────────────────── M1 ─────────────────────────

def m1(label: str, reps: int) -> dict:
    result: dict = {"step2": {}, "step3": {}, "step6": {}, "imports": {}}
    out_root = BENCH / f"out_{label}"
    run_root = BENCH / "run"
    if run_root.exists():
        shutil.rmtree(run_root)

    # import time
    for module in ("pipeline.dialogue_extractor",):
        walls, cumul = [], []
        loads = []
        for _ in range(reps):
            def once():
                started = time.perf_counter()
                proc = subprocess.run([PY, "-X", "importtime", "-c", f"import {module}"], capture_output=True, text=True, cwd=ROOT)
                wall = time.perf_counter() - started
                us = None
                for line in proc.stderr.splitlines():
                    m = re.match(r"import time:\s+\d+\s+\|\s+(\d+)\s+\|\s+(\S+)$", line.strip())
                    if m and m.group(2) == module:
                        us = int(m.group(1))
                return {"wall_s": wall, "cumulative_s": (us or 0) / 1e6, "torch_loaded": bool(re.search(r"\|\s+torch$", proc.stderr, re.M))}
            sample = gated_sample(once)
            walls.append(sample["wall_s"])
            cumul.append(sample["cumulative_s"])
            loads.append(sample["load_max"])
        result["imports"][module] = {"process_wall": stats(walls), "import_cumulative": stats(cumul), "torch_loaded": sample["torch_loaded"], "load_max": max(loads)}
        print(f"import {module}: {result['imports'][module]['import_cumulative']}", flush=True)

    for page in PAGES:
        src = DATA / page
        work = run_root / page / "work"
        for sub in ("json", "masked", "sheets", "vis", "masks"):
            (work / sub).mkdir(parents=True, exist_ok=True)
        json_dir = work / "json"
        img_h = image_height(src / "input.jpg")

        # Step 2
        tg_out = json_dir / f"{page}_text_groups.json"
        cmd2 = [PY, "-m", "pipeline.text_group_builder_v2", str(src / "cv.json"), str(tg_out), str(img_h)]
        if os.environ.get("BENCH_SKIP_STEP2"):
            result["step2"][page] = {"wall": {"n": 0, "median": None}, "load_max": None}
        else:
            samples = [gated_sample(lambda: run_child(cmd2)) for _ in range(reps)]
            result["step2"][page] = {"wall": stats([s["wall_s"] for s in samples]), "load_max": max(s["load_max"] for s in samples)}

        # Step 3 (입력은 원본 세션 text_groups — Step 2 재생성본과 같아야 하지만 원본을 쓴다)
        cmd3 = [
            PY, "-m", "pipeline.dialogue_extractor",
            str(src / "input.jpg"), str(src / "text_groups.json"),
            str(work / "masked" / f"{page}_masked.jpg"), str(json_dir / f"{page}_regions.json"),
            str(work / "sheets" / f"{page}_indexed_sheet.jpg"),
            "--output-vis-path", str(work / "vis" / f"{page}_bubble_vis.jpg"),
        ]
        samples = []
        hashes = []
        for _ in range(reps):
            samples.append(gated_sample(lambda: run_child(cmd3)))
            hashes.append(json.dumps(snapshot_outputs(work), sort_keys=True))
        stable = len(set(hashes)) == 1
        result["step3"][page] = {
            "wall": stats([s["wall_s"] for s in samples]),
            "maxrss_mb": stats([s["maxrss_mb"] for s in samples]),
            "load_max": max(s["load_max"] for s in samples),
            "outputs_stable_across_reps": stable,
        }
        dest = out_root / "step3" / page
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(work, dest)

        # Step 6 (pipeline_runner와 같은 인자 구성, 세션 masked/regions 사용)
        final = run_root / page / "final.jpg"
        cmd6 = [
            PY, "-m", "pipeline.render_text_overlay",
            str(src / "masked.jpg"), str(src / "integrated.json"), str(src / "text_groups.json"), str(final),
            "--regions_json", str(src / "regions.json"), "--original_image", str(src / "input.jpg"),
        ]
        integrated = json.loads((src / "integrated.json").read_text(encoding="utf-8"))
        if isinstance(integrated, dict) and integrated.get("skipped"):
            result["step6"][page] = {"skipped": True}
        else:
            samples = [gated_sample(lambda: run_child(cmd6)) for _ in range(reps)]
            result["step6"][page] = {"wall": stats([s["wall_s"] for s in samples]), "load_max": max(s["load_max"] for s in samples)}
            (out_root / "step6").mkdir(parents=True, exist_ok=True)
            shutil.copy2(final, out_root / "step6" / f"{page}_final.jpg")
        print(f"page {page}: s2={result['step2'][page]['wall']['median']} s3={result['step3'][page]['wall']['median']} rss={result['step3'][page]['maxrss_mb']['median']:.0f}MB s6={result['step6'][page].get('wall', {}).get('median')}", flush=True)
    return result


# ───────────────────────── M2/M3 공용 ─────────────────────────

def patch_app():
    # BENCH_MAIN_V2로 다른 app.py(예: 변경 전 스냅숏)를 지정할 수 있다.
    override = os.environ.get("BENCH_MAIN_V2")
    if override and "app" not in sys.modules:
        import importlib.util

        spec = importlib.util.spec_from_file_location("app", override)
        module = importlib.util.module_from_spec(spec)
        sys.modules["app"] = module
        spec.loader.exec_module(module)
    import app
    print(f"app = {app.__file__}", flush=True)

    copy_root = BENCH / "session_copy"
    app.SESSION_ROOT = copy_root / "projects"
    app.INPUT_CACHE_DIR = copy_root / "cache/input"
    app.CV_CACHE_DIR = copy_root / "cache/cv"
    app.ANALYSIS_CACHE_DIR = copy_root / "cache/analysis"
    return app


class WriteCounter:
    """sys.audit 훅으로 session_copy 아래 쓰기성 연산을 센다."""

    WRITE_EVENTS = {"os.remove", "os.rename", "os.symlink", "shutil.copyfile", "os.utime", "os.chmod", "os.link"}

    def __init__(self, root: Path):
        self.root = str(root)
        self.active = False
        self.counts: dict[str, int] = {}
        sys.addaudithook(self._hook)

    def _hit(self, name: str, path) -> None:
        try:
            p = os.fsdecode(path) if not isinstance(path, int) else ""
        except Exception:
            return
        if p.startswith(self.root):
            self.counts[name] = self.counts.get(name, 0) + 1

    def _hook(self, event, args):
        if not self.active:
            return
        if event == "open" and args and len(args) >= 2:
            mode = args[1] or ""
            flags = args[2] if len(args) > 2 else 0
            write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
            )
            if write:
                self._hit("open_write", args[0])
        elif event in self.WRITE_EVENTS and args:
            self._hit(event, args[0])

    def start(self):
        self.counts = {}
        self.active = True

    def stop(self) -> dict:
        self.active = False
        return dict(self.counts)


def fs_snapshot(root: Path) -> dict[str, tuple]:
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + dirnames:
            p = os.path.join(dirpath, name)
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                continue
            snap[p] = (st.st_mtime_ns, st.st_ino, st.st_size)
    return snap


def fs_diff(a: dict, b: dict) -> dict:
    changed = [k for k in a.keys() & b.keys() if a[k] != b[k]]
    return {"created": len(b.keys() - a.keys()), "removed": len(a.keys() - b.keys()), "modified": len(changed)}


# ───────────────────────── M3 ─────────────────────────

def m3(reps: int) -> dict:
    subprocess.run([PY, str(Path(__file__).resolve().parent / "bench_setup.py"), "--session"], check=True, cwd=ROOT)
    app = patch_app()
    from fastapi.testclient import TestClient

    client = TestClient(app.create_app())
    counter = WriteCounter(BENCH / "session_copy")
    session_root = BENCH / "session_copy" / "projects" / SID
    samples = []
    first_payload = None
    for i in range(reps + 1):
        before = fs_snapshot(session_root)

        def once():
            counter.start()
            started = time.perf_counter()
            response = client.get(f"/api/session/{SID}")
            wall = time.perf_counter() - started
            writes = counter.stop()
            body = response.content
            return {"wall_s": wall, "bytes": len(body), "status": response.status_code, "audit_writes": writes, "_body": body}

        sample = gated_sample(once)
        after = fs_snapshot(session_root)
        sample["fs_diff"] = fs_diff(before, after)
        body = sample.pop("_body")
        if first_payload is None:
            first_payload = body
        sample["payload_equal_first"] = body == first_payload
        sample["rep"] = i
        samples.append(sample)
        print(f"GET#{i}: {sample['wall_s']:.3f}s {sample['bytes']}B writes={sample['audit_writes']} fs={sample['fs_diff']}", flush=True)
    steady = samples[1:]
    result = {
        "cold": samples[0],
        "steady": {
            "wall": stats([s["wall_s"] for s in steady]),
            "bytes": steady[-1]["bytes"],
            "audit_writes_total": [sum(s["audit_writes"].values()) for s in steady],
            "fs_changes": [s["fs_diff"] for s in steady],
            "load_max": max(s["load_max"] for s in steady),
        },
        "samples": samples,
    }
    # 신규 page 엔드포인트가 있으면 같이 잰다 (after 전용)
    page_route = f"/api/session/{SID}/page/00083"
    probe = client.get(page_route)
    if probe.status_code == 200:
        page_samples = []
        for _ in range(reps):
            def once_page():
                counter.start()
                started = time.perf_counter()
                response = client.get(page_route)
                wall = time.perf_counter() - started
                writes = counter.stop()
                return {"wall_s": wall, "bytes": len(response.content), "audit_writes": writes}
            page_samples.append(gated_sample(once_page))
        result["page_endpoint"] = {
            "wall": stats([s["wall_s"] for s in page_samples]),
            "bytes": page_samples[-1]["bytes"],
            "audit_writes_total": [sum(s["audit_writes"].values()) for s in page_samples],
            "load_max": max(s["load_max"] for s in page_samples),
        }
    return result


# ───────────────────────── M2 ─────────────────────────

def m2(reps: int) -> dict:
    app = patch_app()
    from fastapi.testclient import TestClient

    client = TestClient(app.create_app())
    session_root = BENCH / "session_copy" / "projects" / SID
    result: dict = {"overlay_apply": {}, "image_record": {}}
    for page in OVERLAY_PAGES:
        og_path = session_root / "work" / "json" / f"{page}_overlay_guidance.json"
        overlay_guidance = json.loads(og_path.read_text(encoding="utf-8")) if og_path.exists() else {}
        body = {"session_id": SID, "image_id": page, "overlay_guidance": overlay_guidance}
        warm = client.post("/api/overlay/apply", json=body)
        assert warm.status_code == 200, warm.text[:500]

        def once():
            started = time.perf_counter()
            response = client.post("/api/overlay/apply", json=body)
            wall = time.perf_counter() - started
            assert response.status_code == 200, response.text[:500]
            return {"wall_s": wall}

        samples = [gated_sample(once) for _ in range(reps)]
        result["overlay_apply"][page] = {"wall": stats([s["wall_s"] for s in samples]), "load_max": max(s["load_max"] for s in samples)}
        final = session_root / "output" / "final" / f"{page}_final.jpg"
        dest = BENCH / "m2_finals"
        dest.mkdir(parents=True, exist_ok=True)

        input_path = session_root / "input" / f"{page}.jpg"
        for probe in (True, False):
            def once_record():
                started = time.perf_counter()
                app.image_record(SID, input_path, None, probe_image=probe)
                return {"wall_s": time.perf_counter() - started}
            rec = [gated_sample(once_record) for _ in range(reps)]
            result["image_record"].setdefault(page, {})[f"probe_{probe}"] = stats([s["wall_s"] for s in rec])
        print(f"overlay {page}: {result['overlay_apply'][page]['wall']} image_record={result['image_record'][page]}", flush=True)
        result["overlay_apply"][page]["final_pixels"] = pixel_hash(final)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--only", default="m1,m3,m2")
    parser.add_argument("--reps", type=int, default=5)
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    out_path = BENCH / f"results_{args.label}.json"
    results = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    results["label"] = args.label
    results["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    results.setdefault("app_file", {})[args.only] = os.environ.get("BENCH_MAIN_V2") or str(ROOT / "app.py")
    for key in args.only.split(","):
        started = time.time()
        if key == "m1":
            results["m1"] = m1(args.label, args.reps)
        elif key == "m3":
            results["m3"] = m3(args.reps)
        elif key == "m2":
            results["m2"] = m2(args.reps)
        results.setdefault("durations_s", {})[key] = round(time.time() - started, 1)
        dump_json(out_path, results)
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()
