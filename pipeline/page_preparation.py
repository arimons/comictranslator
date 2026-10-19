"""분석 전에 페이지 분할 계획을 만들고 원본과 별개의 입력 파일을 생성한다."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def page_plan(path: Path) -> dict:
    """사용자가 확인한 중앙 표지/좌우 펼침 규칙. 원본 픽셀 좌표로 crop을 기록한다."""
    with Image.open(path) as source:
        # 현재 분석 엔진과 같은 저장 픽셀 좌표를 사용한다. EXIF 회전 좌표를 섞지 않는다.
        width, height = source.size
        sample = source.convert("L")
        sample.thumbnail((1536, 1536))
        pixels = np.asarray(sample)
    mode = "keep"
    rects = [[0, 0, width, height]]
    sides = ["single"]
    reason = "세로·단일 페이지는 원본 유지"
    if width / max(height, 1) >= 1.25:
        # 낮은 빈도의 옅은 워터마크·압축 잡음을 페이지 경계로 삼지 않는다.
        active = np.flatnonzero((pixels < 235).mean(axis=0) >= .04)
        if active.size:
            left = int(active[0] * width / sample.width)
            right = min(width, int(np.ceil((active[-1] + 1) * width / sample.width)))
        else:
            left, right = 0, width
        if left >= width * .15 and width - right >= width * .15 and .25 * width <= right - left <= 1.05 * height:
            padding = max(2, round(width * .002))
            left, right = max(0, left - padding), min(width, right + padding)
            mode, rects, sides = "center_crop", [[left, 0, right - left, height]], ["center"]
            reason = "좌우 넓은 여백 사이의 세로 콘텐츠를 한 장으로 보존"
        elif not active.size:
            reason = "뚜렷한 내용이 없는 가로 이미지는 확인을 위해 원본 유지"
        else:
            middle = width // 2
            mode = "split"
            rects = [[middle, 0, width - middle, height], [0, 0, middle, height]]
            sides = ["right", "left"]
            reason = "가로 펼침을 중앙에서 오른쪽 → 왼쪽 순서로 분할"
    return {"mode": mode, "width": width, "height": height, "rects": rects, "sides": sides, "reason": reason}


def build_plan(images: list[dict]) -> dict:
    pages = []
    for index, image in enumerate(images):
        path = Path(image["input_path"])
        plan = page_plan(path)
        outputs = []
        for number, (rect, side) in enumerate(zip(plan["rects"], plan["sides"]), 1):
            # 접두 번호가 원본의 자연 정렬과 페이지 순서를 모두 보존한다.
            suffix = {"right": "R", "left": "L", "center": "C", "single": "S"}[side]
            outputs.append({"name": f"{index + 1:05d}_{path.stem}_p{number:02d}_{suffix}.png" if plan["mode"] != "keep" else f"{index + 1:05d}_{path.name}", "crop_rect": rect, "side": side})
        pages.append({"image_id": image["id"], "source_name": image.get("name") or path.name, "source_path": str(path), "source_sha256": file_digest(path), **plan, "outputs": outputs})
    content = {"schema_version": 1, "reading_order": "rtl", "pages": pages}
    revision = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**content, "revision": revision, "source_count": len(pages), "output_count": sum(len(page["outputs"]) for page in pages), "changed_count": sum(page["mode"] != "keep" for page in pages)}


def materialize_plan(plan: dict, output_dir: Path) -> list[dict]:
    """새 프로젝트 전용. 기존 원본·분석 결과는 쓰지 않는다."""
    import shutil

    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for page in plan["pages"]:
        source = Path(page["source_path"])
        if file_digest(source) != page["source_sha256"]:
            raise ValueError("원본이 변경되었습니다. 분할 미리보기를 다시 열어주세요.")
        for output in page["outputs"]:
            target = output_dir / output["name"]
            if target.exists():
                raise FileExistsError(target)
            if page["mode"] == "keep":
                shutil.copyfile(source, target)
            else:
                x, y, width, height = output["crop_rect"]
                with Image.open(source) as image:
                    crop = image.crop((x, y, x + width, y + height))
                    if crop.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                        crop = crop.convert("RGB")
                    crop.save(target, format="PNG")
            results.append({"path": target, "parent_id": page["image_id"], "parent_name": page["source_name"], "parent_input_path": page["source_path"], "source_image_sha256": page["source_sha256"], "crop_rect": output["crop_rect"], "page_side": output["side"], "reading_order": "rtl", "split_policy": page["mode"]})
    return results
