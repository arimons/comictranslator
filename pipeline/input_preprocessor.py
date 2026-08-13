# 파이프라인 입력 이미지를 실행 전 표준 페이지 단위로 정리하는 전처리 유틸리티
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _safe_stem(path: Path) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in path.stem)


def _write_image(path: Path, image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    params: list[int] = []
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        params = [int(cv2.IMWRITE_JPEG_QUALITY), 96]
    cv2.imwrite(str(path), image, params)


def split_landscape_page(
    image_path: Path,
    split_dir: Path,
    min_aspect_ratio: float = 1.25,
    reading_order: str = "rtl",
) -> tuple[list[Path], dict[str, Any]]:
    """landscape 2-page spread를 물리 파일로 나누고 처리 순서대로 반환한다."""
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"이미지를 불러올 수 없습니다: {image_path}")

    h, w = image.shape[:2]
    aspect_ratio = w / max(1, h)
    meta: dict[str, Any] = {
        "source": str(image_path),
        "width": int(w),
        "height": int(h),
        "aspect_ratio": round(float(aspect_ratio), 4),
        "split": False,
        "outputs": [],
    }
    if aspect_ratio < min_aspect_ratio:
        return [image_path], meta

    split_x = w // 2
    left = image[:, :split_x]
    right = image[:, split_x:]
    stem = _safe_stem(image_path)
    suffix = ".jpg"

    right_path = split_dir / f"{stem}_p01_R{suffix}"
    left_path = split_dir / f"{stem}_p02_L{suffix}"
    _write_image(right_path, right)
    _write_image(left_path, left)

    ordered = [right_path, left_path] if reading_order == "rtl" else [left_path, right_path]
    meta.update(
        {
            "split": True,
            "split_x": int(split_x),
            "reading_order": reading_order,
            "outputs": [str(path) for path in ordered],
        }
    )
    return ordered, meta


def prepare_pipeline_inputs(
    image_paths: list[Path],
    split_dir: Path,
    split_landscape: bool = True,
    min_aspect_ratio: float = 1.25,
    reading_order: str = "rtl",
) -> tuple[list[Path], list[dict[str, Any]]]:
    """입력 이미지 목록을 파이프라인 처리 단위로 확장한다."""
    prepared: list[Path] = []
    metadata: list[dict[str, Any]] = []
    split_dir.mkdir(parents=True, exist_ok=True)

    for image_path in image_paths:
        if not split_landscape:
            prepared.append(image_path)
            metadata.append({"source": str(image_path), "split": False, "outputs": [str(image_path)]})
            continue
        outputs, meta = split_landscape_page(
            image_path,
            split_dir,
            min_aspect_ratio=min_aspect_ratio,
            reading_order=reading_order,
        )
        prepared.extend(outputs)
        metadata.append(meta)

    meta_path = split_dir / "split_manifest.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return prepared, metadata
