"""벤치마크 입력 준비.

1) proof/bench/data/<page>/ : 대표 10페이지의 입력·JSON·masked·mask 사본 (경로 재작성)
2) proof/bench/session_copy/ : 세션 20260530_144110 전체 + 해당 분석/입력 캐시 사본.
   temp_output/ 접두어를 session_copy/ 로 바꾼 경로로 JSON 내용과 symlink 대상을 다시 쓴다.

temp_output/ 에는 아무것도 쓰지 않는다(읽기만).
사용: bench_setup.py [--data] [--session]
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path, manifest_files
from pipeline.manual_guidance import load_json

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_common import BENCH, ROOT  # noqa: E402

SRC_TEMP = DATA_HOME
SID = "20260530_144110"
SRC_SESSION = SRC_TEMP / "projects" / SID
PAGES = ["00001", "00003", "00014", "00083", "00010", "00060", "00081", "00097", "00011", "00053"]
COPY_ROOT = BENCH / "session_copy"


def rewrite_text(text: str, old_prefix: str, new_prefix: str) -> str:
    return text.replace(old_prefix, new_prefix)


def build_data() -> None:
    data_root = BENCH / "data"
    if data_root.exists():
        shutil.rmtree(data_root)
    for page in PAGES:
        dst = data_root / page
        (dst / "masks").mkdir(parents=True)
        src_json = SRC_SESSION / "work" / "json"
        shutil.copy2((SRC_SESSION / "input" / f"{page}.jpg").resolve(), dst / "input.jpg")
        for name in ("cv", "text_groups", "integrated", "regions", "overlay_guidance", "manual_guidance", "text_groups_effective"):
            src = src_json / f"{page}_{name}.json"
            if src.exists():
                shutil.copy2(src.resolve(), dst / f"{name}.json")
        masked = SRC_SESSION / "work" / "masked" / f"{page}_masked.jpg"
        if masked.exists():
            shutil.copy2(masked.resolve(), dst / "masked.jpg")
        # regions.json의 mask_ref를 사본 mask로 바꾼다.
        regions_path = dst / "regions.json"
        if regions_path.exists():
            regions = json.loads(regions_path.read_text(encoding="utf-8"))
            for region in regions.get("regions", []):
                ref = region.get("mask_ref")
                if ref and Path(ref).exists():
                    target = dst / "masks" / Path(ref).name
                    shutil.copy2(Path(ref).resolve(), target)
                    region["mask_ref"] = str(target)
            regions_path.write_text(json.dumps(regions, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"data {page}: {sorted(p.name for p in dst.iterdir())}")


def build_session_copy() -> None:
    if COPY_ROOT.exists():
        shutil.rmtree(COPY_ROOT)
    old_prefix = str(SRC_TEMP) + "/"
    new_prefix = str(COPY_ROOT) + "/"
    dst_session = COPY_ROOT / "projects" / SID
    session = load_json(manifest_path(SRC_SESSION))
    hashes = {item.get("image_sha256") for item in session.get("images", []) if item.get("image_sha256")}

    # 1) 분석 캐시 사본 (이 세션 이미지 hash만)
    for image_hash in sorted(hashes):
        src = SRC_TEMP / "cache/analysis" / image_hash[:2] / image_hash
        if src.exists():
            shutil.copytree(src, COPY_ROOT / "cache/analysis" / image_hash[:2] / image_hash, symlinks=True)

    # 2) 세션 트리 사본 (exports 제외, symlink 유지)
    shutil.copytree(SRC_SESSION, dst_session, symlinks=True, ignore=shutil.ignore_patterns("exports"))

    # 3) symlink 재지정 + 입력 캐시 파일 복사
    for path in list(COPY_ROOT.rglob("*")):
        if not path.is_symlink():
            continue
        target = os.readlink(path)
        if not target.startswith(old_prefix):
            continue
        new_target = Path(new_prefix + target[len(old_prefix):])
        src_target = Path(target)
        if not new_target.exists() and src_target.exists():
            new_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_target, new_target)
        path.unlink()
        path.symlink_to(new_target)

    # 4) JSON 내용의 절대 경로 재작성 (mtime 보존)
    for path in COPY_ROOT.rglob("*.json"):
        if path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8")
        if old_prefix in text:
            stat = path.stat()
            path.write_text(rewrite_text(text, old_prefix, new_prefix), encoding="utf-8")
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    # 남은 temp_output 참조 확인
    leftovers = 0
    for path in COPY_ROOT.rglob("*"):
        if path.is_symlink() and os.readlink(path).startswith(old_prefix):
            leftovers += 1
    print(f"session_copy 완료: hashes={len(hashes)} leftover_links={leftovers}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", action="store_true")
    parser.add_argument("--session", action="store_true")
    args = parser.parse_args()
    if args.data:
        build_data()
    if args.session:
        build_session_copy()


if __name__ == "__main__":
    main()
