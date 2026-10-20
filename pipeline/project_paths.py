"""프로젝트 JSON의 경로만 상대 경로로 저장하고, 실행 시 절대 경로로 해석한다.

대사·프롬프트 같은 일반 문자열은 바꾸지 않는다. 기존 절대 경로도 그대로 읽는다.
patch_png/mask_png는 원래 state.json 기준이므로 그 규칙을 유지한다.
"""
from __future__ import annotations
import os
from pathlib import Path
from pipeline.paths import REPO_ROOT

PATH_KEYS = {"source_fallback_path","input_path", "parent_input_path", "mask_ref", "original_path", "preview_path", "returned_path",
             "image", "clusters", "font_path", "source", "output", "board", "all_board", "session",
             "anime_model", "sheet_image_path", "chunk_sheet_path", "sheet_json_path"}


def project_base(path: Path) -> Path | None:
    path = Path(path).absolute()
    if path.name in {"project.json", "session.json"}:
        return path.parent
    for parent in path.parents:
        if (parent / "project.json").is_file() or (parent / "session.json").is_file():
            return parent
    return None


def manifest_path(base: Path) -> Path:
    current = base / "project.json"
    return current if current.exists() or not (base / "session.json").exists() else base / "session.json"


def manifest_files(root: Path):
    # 같은 폴더의 구 manifest는 중복 표시하지 않는다.
    return sorted(manifest_path(p) for p in root.iterdir() if p.is_dir()
                  and ((p / "project.json").is_file() or (p / "session.json").is_file())) if root.exists() else []


def transform_paths(value, base: Path, *, encode: bool):
    def path_value(text):
        if encode:
            if not Path(text).is_absolute():
                return text
            path = Path(os.path.abspath(text))
            if path.is_relative_to(REPO_ROOT) and not path.is_relative_to(base):
                # 저장소에 남는 폰트·모델 등 코드 자원은 재배치된 저장소를 기준으로 한다.
                relative = path.relative_to(REPO_ROOT)
                if relative.parts and relative.parts[0] in {"Fonts", "models"}:
                    return "@repo/" + relative.as_posix()
            return os.path.relpath(path, base)
        if text.startswith("@repo/"):
            return str(REPO_ROOT / text[6:])
        if Path(text).is_absolute():
            return text
        return os.path.abspath(base / text)
    def walk(item, key=""):
        if isinstance(item, dict):
            return {k: walk(v, k) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v, key) for v in item]
        if isinstance(item, str) and item and key in PATH_KEYS:
            # image/source는 모델명·일반 문자열에도 사용되므로 경로 형태만 처리한다.
            if Path(item).is_absolute() or item.startswith(("@repo/", "./", "../", "input/", "work/", "output/", "cache/")):
                return path_value(item)
        return item
    return walk(value)


def storage_json(path: Path, value):
    base = project_base(path)
    return transform_paths(value, base, encode=True) if base else value


def runtime_json(path: Path, value):
    base = project_base(path)
    return transform_paths(value, base, encode=False) if base else value
