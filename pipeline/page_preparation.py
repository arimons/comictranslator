"""분석 전에 페이지 분할 계획을 만들고 원본과 별개의 입력 파일을 생성한다."""
from __future__ import annotations

import hashlib
import os
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


def page_plan(path: Path, override: dict | None = None) -> dict:
    """사용자가 확인한 중앙 표지/좌우 펼침 규칙. 원본 픽셀 좌표로 crop을 기록한다.

    override는 페이지별 사용자 지정이다. {"mode": "keep"}은 원본 그대로, {"mode": "split", "split_x": x}는 x에서 나눈다.
    """
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
    auto_mode = mode
    if override and override.get("mode") == "keep":
        mode, rects, sides = "keep", [[0, 0, width, height]], ["single"]
        reason = "사용자 지정: 가로 이미지를 나누지 않고 그대로 사용"
    elif override and override.get("mode") == "split" and width > 1:
        # 양쪽 모두 최소 1px이 남도록 분할선을 이미지 안으로 제한한다
        split_x = min(width - 1, max(1, int(override.get("split_x") or width // 2)))
        mode, rects, sides = "split", [[split_x, 0, width - split_x, height], [0, 0, split_x, height]], ["right", "left"]
        reason = f"사용자 지정: x={split_x}에서 오른쪽 → 왼쪽 순서로 분할"
    return {"mode": mode, "auto_mode": auto_mode, "override": override or None, "width": width, "height": height, "rects": rects, "sides": sides, "reason": reason}


def build_plan(images: list[dict], numbered: bool = True) -> dict:
    pages = []
    for index, image in enumerate(images):
        path = Path(image["input_path"])
        plan = page_plan(path, image.get("split_override"))
        outputs = []
        for number, (rect, side) in enumerate(zip(plan["rects"], plan["sides"]), 1):
            # 접두 번호가 원본의 자연 정렬과 페이지 순서를 모두 보존한다.
            suffix = {"right": "R", "left": "L", "center": "C", "single": "S"}[side]
            if numbered:
                name = f"{index + 1:05d}_{path.stem}_p{number:02d}_{suffix}.png" if plan["mode"] != "keep" else f"{index + 1:05d}_{path.name}"
            else:
                # 같은 프로젝트 안에서 나눌 때는 원본 이름을 그대로 앞에 둬, 자연 정렬이 원래 페이지 순서와 오른쪽→왼쪽 순서를 유지한다.
                name = f"{path.stem}_p{number:02d}_{suffix}.png" if plan["mode"] != "keep" else path.name
            outputs.append({"name": name, "crop_rect": rect, "side": side})
        pages.append({"image_id": image["id"], "source_name": image.get("name") or path.name, "source_path": str(path), "source_sha256": file_digest(path), "source_ref":image.get("source_ref"), "source_fallback_path":image.get("source_fallback_path"), **plan, "outputs": outputs})
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
                if page.get("source_ref"):
                    target.symlink_to(os.path.relpath(source.resolve(),target.parent.resolve()))
                else:
                    # 원본 미연결인 이전 페이지와 업로드는 프로젝트 소유 상태를 보존한다.
                    shutil.copyfile(source, target)
            else:
                x, y, width, height = output["crop_rect"]
                with Image.open(source) as image:
                    crop = image.crop((x, y, x + width, y + height))
                    if crop.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                        crop = crop.convert("RGB")
                    crop.save(target, format="PNG")
            storage = {"input_storage":"derived"} if page["mode"]!="keep" else ({"input_storage":"source","source_ref":page["source_ref"]} if page.get("source_ref") else {"input_storage":"upload"})
            if page["mode"]=="keep" and page.get("source_fallback_path"):
                storage["source_fallback_path"]=page["source_fallback_path"]
            results.append({**storage,"path": target, "parent_id": page["image_id"], "parent_name": page["source_name"], "parent_input_path": page["source_path"], "source_image_sha256": page["source_sha256"], "crop_rect": output["crop_rect"], "page_side": output["side"], "reading_order": "rtl", "split_policy": page["mode"]})
    return results


def is_split_page(image: dict) -> bool:
    """이미 나눈(또는 잘라낸) 페이지는 다시 나누지 않는다."""
    return bool(image.get("parent_id")) or image.get("input_storage") == "derived"


def split_in_place(images: list[dict], output_dir: Path) -> list[dict]:
    """같은 프로젝트 안에서 가로 펼침을 나눈다. 나눌 페이지의 조각만 PNG로 저장하고 원본 파일은 건드리지 않는다.

    반환 항목은 새 페이지 기록의 추가 필드(원본 id·좌표·읽기 방향)와 저장한 경로(path)다.
    중간에 실패하면 이미 저장한 조각을 지운다.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_plan([image for image in images if not is_split_page(image)], numbered=False)
    created: list[Path] = []
    results: list[dict] = []
    try:
        for page in plan["pages"]:
            if page["mode"] == "keep":
                continue
            source = Path(page["source_path"])
            if file_digest(source) != page["source_sha256"]:
                raise ValueError("원본이 변경되었습니다. 다시 시도하세요.")
            for output in page["outputs"]:
                target = output_dir / output["name"]
                if target.exists() or target.is_symlink():
                    raise FileExistsError(target)
                x, y, width, height = output["crop_rect"]
                with Image.open(source) as image:
                    crop = image.crop((x, y, x + width, y + height))
                    if crop.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                        crop = crop.convert("RGB")
                    crop.save(target, format="PNG")
                created.append(target)
                results.append({
                    "input_storage": "derived", "path": target, "parent_id": page["image_id"], "parent_name": page["source_name"],
                    "parent_input_path": page["source_path"], "source_image_sha256": page["source_sha256"],
                    "crop_rect": output["crop_rect"], "page_side": output["side"], "reading_order": "rtl", "split_policy": page["mode"],
                })
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return results
