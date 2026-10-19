# 말풍선 밖 배경 글자 제거 — 문맥 영역/제거 마스크 분리, 해상도 변환, 마스크 내부 합성, LaMa 엔진
"""배경 글자 복원 모듈.

핵심 원칙
- 문맥 영역(context_rect)과 제거 마스크(mask)를 분리한다. 모델에는 문맥 영역 전체를 보여 주고,
  실제로 바꾸는 것은 마스크 픽셀뿐이다.
- 처리 해상도는 문맥 영역 종횡비를 유지한 채 긴 변 512/768/원본 중에서 고른다.
  모델 크기 제약(16의 배수)은 오른쪽·아래 패딩으로 맞추고, 역변환 때 그대로 잘라낸다.
- 이진 마스크 크기 변경은 최근접 보간만 쓴다.
- 결과는 원본 좌표로 되돌린 뒤 마스크 내부만 원본에 합성한다. 마스크 바깥 픽셀은 바이트 단위로 같다.
"""

from __future__ import annotations

import base64
import json
import math
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    from pipeline.region_types import TEXT_BASED, region_processing_basis
except ImportError:
    from region_types import TEXT_BASED, region_processing_basis

Rect = list[int]

RESOLUTION_CHOICES = {"512": 512, "768": 768, "original": None}
MODEL_SIZE_MULTIPLE = 16
ENGINES = ("lama",)


# ── 기하 ────────────────────────────────────────────────────────────────


def clamp_rect(rect: Any, width: int, height: int) -> Rect | None:
    if not isinstance(rect, (list, tuple)) or len(rect) < 4:
        return None
    x, y, w, h = [int(round(float(v))) for v in rect[:4]]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(width, x + w), min(height, y + h)
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


def mask_bbox(mask: np.ndarray) -> Rect | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    x1, y1 = int(xs.min()), int(ys.min())
    return [x1, y1, int(xs.max()) + 1 - x1, int(ys.max()) + 1 - y1]


def suggest_context_rect(mask: np.ndarray, margin: int = 96) -> Rect | None:
    """마스크 외접 사각형에 여백을 더한 제안값. 컷 경계가 아니므로 사용자가 확인·수정해야 한다."""
    bbox = mask_bbox(mask)
    if bbox is None:
        return None
    h, w = mask.shape[:2]
    x, y, bw, bh = bbox
    return clamp_rect([x - margin, y - margin, bw + margin * 2, bh + margin * 2], w, h)


# ── 마스크 ──────────────────────────────────────────────────────────────


def is_grayscale_image(image: np.ndarray, tolerance: float = 6.0, ratio: float = 0.995) -> bool:
    """채널 차이가 거의 없으면 흑백 원고로 본다."""
    if image.ndim == 2 or image.shape[2] == 1:
        return True
    small = image if image.shape[0] * image.shape[1] <= 400_000 else cv2.resize(image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    b, g, r = [small[..., i].astype(np.int16) for i in range(3)]
    spread = np.maximum(np.maximum(abs(b - g), abs(g - r)), abs(b - r))
    return float(np.count_nonzero(spread <= tolerance)) / spread.size >= ratio


@dataclass
class MaskParams:
    polarity: str = "auto"      # auto | dark | bright
    threshold: int | None = None  # None이면 극성별 기본값
    dilate_px: int = 4           # 외곽선·그림자·안티앨리어싱까지 덮는 확장량
    min_area: int = 10           # 이보다 작은 성분(망점 점 등)은 글자 획으로 보지 않는다
    use_fragments: bool = False  # CV 단어 bbox로 후보를 더 좁힌다. CV가 일부 글자만 잡으면 본문이 빠지므로 기본 끔
    fragment_pad: int = 3


def _seed_polarity(gray_roi: np.ndarray) -> str:
    mean = float(gray_roi.mean()) if gray_roi.size else 255.0
    dark_ratio = float(np.count_nonzero(gray_roi < 80)) / max(1, gray_roi.size)
    return "bright" if mean < 120 or dark_ratio > 0.35 else "dark"


def build_text_stroke_mask(
    image: np.ndarray,
    seeds: list[dict[str, Any]],
    params: MaskParams | None = None,
) -> np.ndarray:
    """글자 좌표(seed) 안에서 글자 획만 뽑고 외곽선 폭만큼 확장한 페이지 크기 마스크(0/255).

    seed: {"rect": [x,y,w,h], "fragments": [[x,y,w,h], ...]}
    사각형 전체를 채우지 않고, 사각형 안에서 극성에 맞는 획 성분만 고른 뒤 dilate_px만큼 넓힌다.
    use_fragments가 켜져 있으면 CV 단어 bbox 안쪽만 획 후보로 본다.
    """
    params = params or MaskParams()
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    core = np.zeros((h, w), dtype=np.uint8)
    for seed in seeds:
        rect = clamp_rect(seed.get("rect"), w, h)
        if rect is None:
            continue
        x, y, rw, rh = rect
        lane = np.zeros((rh, rw), dtype=np.uint8)
        fragments = [f for f in (seed.get("fragments") or []) if isinstance(f, (list, tuple)) and len(f) >= 4]
        if params.use_fragments and fragments:
            pad = params.fragment_pad
            for fx, fy, fw, fh in (map(int, f[:4]) for f in fragments):
                lx1, ly1 = max(0, fx - x - pad), max(0, fy - y - pad)
                lx2, ly2 = min(rw, fx + fw - x + pad), min(rh, fy + fh - y + pad)
                if lx2 > lx1 and ly2 > ly1:
                    lane[ly1:ly2, lx1:lx2] = 255
        else:
            lane[:, :] = 255
        roi = gray[y:y + rh, x:x + rw]
        polarity = params.polarity if params.polarity in {"dark", "bright"} else _seed_polarity(roi)
        if polarity == "bright":
            thr = params.threshold if params.threshold is not None else 175
            strokes = (roi >= thr) & (lane > 0)
        else:
            thr = params.threshold if params.threshold is not None else 120
            strokes = (roi <= thr) & (lane > 0)
        strokes = strokes.astype(np.uint8) * 255
        n, labels, stats, _ = cv2.connectedComponentsWithStats(strokes, connectivity=8)
        keep = np.zeros(n, dtype=bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= max(1, params.min_area)
        core[y:y + rh, x:x + rw] |= np.where(keep[labels], 255, 0).astype(np.uint8)
    if params.dilate_px > 0:
        r = int(params.dilate_px)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        core = cv2.dilate(core, kernel, iterations=1)
    return core


def seeds_from_regions(regions_payload: Any, text_groups: Any) -> list[dict[str, Any]]:
    """Step 3의 텍스트 기반 영역을 글자 seed로 바꾼다. 경계 기반 영역은 제외한다."""
    regions = (regions_payload or {}).get("regions", []) if isinstance(regions_payload, dict) else []
    tg_by_id: dict[int, dict[str, Any]] = {}
    for idx, group in enumerate(text_groups if isinstance(text_groups, list) else []):
        try:
            tg_by_id[int(group.get("text_group_id", group.get("cluster_id", idx)))] = group
        except (TypeError, ValueError):
            continue
    seeds: list[dict[str, Any]] = []
    for region in regions:
        if region_processing_basis(region) != TEXT_BASED:
            continue
        rect = region.get("erase_rect") or region.get("unboxed_rect")
        if not rect:
            continue
        fragments: list[list[int]] = []
        for tid in region.get("anchor_ids", []):
            try:
                group = tg_by_id.get(int(tid))
            except (TypeError, ValueError):
                group = None
            for line in (group or {}).get("lines", []):
                for frag in line.get("fragments", []):
                    bbox = frag.get("bbox")
                    if isinstance(bbox, list) and len(bbox) >= 4:
                        fragments.append([int(v) for v in bbox[:4]])
        seeds.append({
            "rect": [int(v) for v in rect[:4]],
            "fragments": fragments,
            "region_id": region.get("region_id"),
            "source": "regions_unboxed",
        })
    return seeds


def decode_mask_png(data: str | bytes, width: int, height: int) -> np.ndarray:
    """data URL/base64 PNG를 페이지 크기 0/255 마스크로. 크기가 다르면 최근접 보간."""
    if isinstance(data, str):
        if "," in data and data.strip().startswith("data:"):
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
    else:
        raw = data
    arr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ValueError("마스크 PNG를 읽을 수 없습니다")
    if arr.ndim == 3:
        if arr.shape[2] == 4:
            # 투명 배경 캔버스: 알파가 있는 곳 중 밝은 곳을 칠한 것으로 본다
            alpha = arr[..., 3]
            luma = cv2.cvtColor(arr[..., :3], cv2.COLOR_BGR2GRAY)
            arr = np.where((alpha > 127) & (luma > 127), 255, 0).astype(np.uint8)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    if arr.shape[:2] != (height, width):
        arr = cv2.resize(arr, (width, height), interpolation=cv2.INTER_NEAREST)
    return np.where(arr > 127, 255, 0).astype(np.uint8)


# ── 좌표 변환 ───────────────────────────────────────────────────────────


@dataclass
class CanvasTransform:
    context_rect: Rect     # 원본 좌표의 문맥 영역 [x, y, w, h]
    scaled_w: int          # 종횡비 유지 리사이즈 후 크기(패딩 전)
    scaled_h: int
    model_w: int           # 16의 배수로 패딩한 모델 입력 크기
    model_h: int
    scale_x: float
    scale_y: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def target_size(width: int, height: int, long_side: int | None) -> tuple[int, int]:
    if not long_side:
        return width, height
    scale = long_side / float(max(width, height))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _round_up(value: int, multiple: int) -> int:
    return int(math.ceil(value / multiple) * multiple)


def prepare_model_input(
    source: np.ndarray,
    mask: np.ndarray,
    context_rect: Rect,
    long_side: int | None,
    multiple: int = MODEL_SIZE_MULTIPLE,
) -> tuple[np.ndarray, np.ndarray, CanvasTransform]:
    """문맥 영역을 잘라 종횡비 유지 리사이즈 → 16 배수 패딩한 이미지·마스크를 만든다.

    축소할 때 최근접 보간만 쓰면 1〜2px 획이 표본점 사이로 빠져 모델이 글자를 '보존 영역'으로 보게 된다.
    그래서 축소 배율의 역수만큼 원본 해상도에서 먼저 팽창한 뒤 최근접으로 줄인다(값은 계속 0/255).
    패딩 영역은 마스크 0(보존)이고 역변환 때 잘라낸다.
    """
    x, y, cw, ch = context_rect
    crop = source[y:y + ch, x:x + cw]
    mcrop = mask[y:y + ch, x:x + cw]
    sw, sh = target_size(cw, ch, long_side)
    sx, sy = sw / cw, sh / ch
    if (sw, sh) == (cw, ch):
        img_s, mask_s = crop.copy(), mcrop.copy()
    else:
        interp = cv2.INTER_AREA if sw < cw else cv2.INTER_CUBIC
        img_s = cv2.resize(crop, (sw, sh), interpolation=interp)
        mask_src = mcrop
        shrink = max(cw / sw, ch / sh)
        if shrink > 1.0:
            r = int(math.ceil(shrink / 2.0))
            kernel = np.ones((2 * r + 1, 2 * r + 1), dtype=np.uint8)
            mask_src = cv2.dilate(mcrop, kernel, iterations=1)
        mask_s = cv2.resize(mask_src, (sw, sh), interpolation=cv2.INTER_NEAREST)
    mw, mh = _round_up(sw, multiple), _round_up(sh, multiple)
    img_in = cv2.copyMakeBorder(img_s, 0, mh - sh, 0, mw - sw, cv2.BORDER_REPLICATE)
    mask_in = cv2.copyMakeBorder(mask_s, 0, mh - sh, 0, mw - sw, cv2.BORDER_CONSTANT, value=0)
    mask_in = np.where(mask_in > 127, 255, 0).astype(np.uint8)
    return img_in, mask_in, CanvasTransform(list(context_rect), sw, sh, mw, mh, sx, sy)


def restore_from_model_output(output: np.ndarray, transform: CanvasTransform) -> np.ndarray:
    """모델 출력에서 패딩을 잘라내고 문맥 영역 원본 크기로 되돌린다."""
    if output.shape[:2] != (transform.model_h, transform.model_w):
        raise ValueError(f"모델 출력 크기 불일치: {output.shape[:2]} != {(transform.model_h, transform.model_w)}")
    cropped = output[:transform.scaled_h, :transform.scaled_w]
    _, _, cw, ch = transform.context_rect
    if (transform.scaled_w, transform.scaled_h) == (cw, ch):
        return cropped.copy()
    interp = cv2.INTER_CUBIC if transform.scaled_w < cw else cv2.INTER_AREA
    return cv2.resize(cropped, (cw, ch), interpolation=interp)


def composite_inside_mask(
    source: np.ndarray,
    restored_crop: np.ndarray,
    mask: np.ndarray,
    context_rect: Rect,
    feather_px: int = 0,
) -> np.ndarray:
    """마스크 내부만 복원 결과로 바꾼 페이지를 돌려준다. 마스크 바깥은 source와 동일.

    feather_px > 0이면 마스크 경계에서 안쪽으로 feather_px 구간만 원본과 섞는다(바깥은 건드리지 않음).
    """
    x, y, cw, ch = context_rect
    out = source.copy()
    region = out[y:y + ch, x:x + cw]
    m = mask[y:y + ch, x:x + cw] > 0
    if feather_px and feather_px > 0:
        dist = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 3)
        alpha = np.clip(dist / float(feather_px), 0.0, 1.0)[..., None]
        blended = (restored_crop.astype(np.float32) * alpha + region.astype(np.float32) * (1.0 - alpha))
        blended = np.clip(np.rint(blended), 0, 255).astype(np.uint8)
        region[m] = blended[m]
    else:
        region[m] = restored_crop[m]
    return out


def changed_pixels_outside_mask(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> int:
    outside = mask == 0
    diff = np.any(before != after, axis=2) if before.ndim == 3 else before != after
    return int(np.count_nonzero(diff & outside))


# ── 엔진 ────────────────────────────────────────────────────────────────


class EngineError(RuntimeError):
    pass


_lama_lock = threading.Lock()
_lama_model: Any = None
_lama_load_sec: float | None = None


def _get_lama() -> tuple[Any, float, bool]:
    """SimpleLama를 한 번만 올린다. (모델, 로딩 초, 이번에 로드했는지)"""
    global _lama_model, _lama_load_sec
    with _lama_lock:
        if _lama_model is not None:
            return _lama_model, 0.0, False
        started = time.perf_counter()  # torch import까지 로딩 시간에 넣는다
        # Step 3(dialogue_extractor)와 같은 모델 인스턴스를 쓴다. 서버가 LaMa를 두 번 올리면 2〜3GB가 중복된다.
        try:
            from pipeline.dialogue_extractor import _get_lama as _shared_lama
        except ImportError:
            from dialogue_extractor import _get_lama as _shared_lama
        try:
            _lama_model = _shared_lama()
        except RuntimeError as exc:
            raise EngineError(str(exc)) from exc
        _lama_load_sec = time.perf_counter() - started
        return _lama_model, _lama_load_sec, True


def run_lama(img_in: np.ndarray, mask_in: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    from PIL import Image

    lama, load_sec, loaded_now = _get_lama()
    started = time.perf_counter()
    out = lama(Image.fromarray(cv2.cvtColor(img_in, cv2.COLOR_BGR2RGB)), Image.fromarray(mask_in))
    infer_sec = time.perf_counter() - started
    out_bgr = cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)
    # SimpleLama는 내부에서 8의 배수로 패딩한 결과를 돌려줄 수 있다. 입력 크기로 자른다.
    h, w = img_in.shape[:2]
    if out_bgr.shape[0] < h or out_bgr.shape[1] < w:
        raise EngineError(f"LaMa 출력이 입력보다 작습니다: {out_bgr.shape[:2]} < {(h, w)}")
    return out_bgr[:h, :w].copy(), {"load_sec": round(load_sec, 3), "model_loaded_now": loaded_now, "infer_sec": round(infer_sec, 3)}


# ── 한 번의 복원 실행 ────────────────────────────────────────────────────


@dataclass
class RestoreParams:
    engine: str = "lama"
    resolution: str = "original"  # 512 | 768 | original
    feather_px: int = 0


def run_restore(
    source: np.ndarray,
    mask: np.ndarray,
    context_rect: Rect,
    params: RestoreParams,
    work_dir: Path,
) -> dict[str, Any]:
    """source(BGR 페이지)·mask(0/255 페이지)·context_rect로 복원하고 산출물을 work_dir에 남긴다."""
    if params.engine not in ENGINES:
        raise EngineError(f"지원하지 않는 엔진: {params.engine}")
    if params.resolution not in RESOLUTION_CHOICES:
        raise EngineError(f"지원하지 않는 처리 해상도: {params.resolution}")
    h, w = source.shape[:2]
    if mask.shape[:2] != (h, w):
        raise EngineError("마스크와 원본 크기가 다릅니다")
    rect = clamp_rect(context_rect, w, h)
    if rect is None:
        raise EngineError("문맥 영역이 비어 있습니다")
    total_mask = int(np.count_nonzero(mask))
    if total_mask == 0:
        raise EngineError("제거 마스크가 비어 있습니다")
    x, y, cw, ch = rect
    inside = np.zeros_like(mask)
    inside[y:y + ch, x:x + cw] = mask[y:y + ch, x:x + cw]
    dropped = total_mask - int(np.count_nonzero(inside))
    if int(np.count_nonzero(inside)) == 0:
        raise EngineError("제거 마스크가 문맥 영역 밖에만 있습니다")

    work_dir.mkdir(parents=True, exist_ok=True)
    img_in, mask_in, transform = prepare_model_input(source, inside, rect, RESOLUTION_CHOICES[params.resolution])
    in_path, mask_path, out_path = work_dir / "model_input.png", work_dir / "model_mask.png", work_dir / "model_output.png"
    cv2.imwrite(str(in_path), img_in)
    cv2.imwrite(str(mask_path), mask_in)

    started = time.perf_counter()
    output, timing = run_lama(img_in, mask_in)
    cv2.imwrite(str(out_path), output)
    total_sec = time.perf_counter() - started

    restored_crop = restore_from_model_output(output, transform)
    page = composite_inside_mask(source, restored_crop, inside, rect, params.feather_px)
    outside_changed = changed_pixels_outside_mask(source, page, inside)
    if outside_changed:
        raise EngineError(f"마스크 바깥 픽셀 {outside_changed}개가 바뀌었습니다. 결과를 버립니다.")

    crop_mask = inside[y:y + ch, x:x + cw]
    cv2.imwrite(str(work_dir / "crop_before.png"), source[y:y + ch, x:x + cw])
    cv2.imwrite(str(work_dir / "crop_after.png"), page[y:y + ch, x:x + cw])
    cv2.imwrite(str(work_dir / "crop_mask.png"), crop_mask)
    cv2.imwrite(str(work_dir / "crop_model_raw.png"), restored_crop)
    meta = {
        "engine": params.engine,
        "params": asdict(params),
        "context_rect": rect,
        "transform": transform.to_dict(),
        "mask_pixels": int(np.count_nonzero(inside)),
        "mask_pixels_dropped_outside_context": dropped,
        "outside_mask_changed_pixels": outside_changed,
        "timing": {**timing, "total_sec": round(total_sec, 3)},
    }
    (work_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"page": page, "meta": meta}


# ── 확정 패치 저장/적용 ─────────────────────────────────────────────────


def load_restore_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def apply_restore_patches(image: np.ndarray, state_path: Path | None) -> int:
    """확정된 복원 패치를 마스크 내부에만 덮어쓴다(in-place). 적용 개수 반환.

    패치는 PNG(무손실)로 저장되어 있어 페이지 JPEG를 다시 저장하지 않고 렌더 시점에 합성된다.
    """
    if state_path is None or not Path(state_path).exists():
        return 0
    state = load_restore_state(Path(state_path))
    base = Path(state_path).parent
    h, w = image.shape[:2]
    applied = 0
    for patch in state.get("patches", []):
        if patch.get("enabled") is False:
            continue
        rect = clamp_rect(patch.get("rect"), w, h)
        if rect is None or list(rect) != [int(v) for v in patch["rect"][:4]]:
            continue
        pixels = cv2.imread(str(base / patch["patch_png"]), cv2.IMREAD_COLOR)
        pmask = cv2.imread(str(base / patch["mask_png"]), cv2.IMREAD_GRAYSCALE)
        x, y, pw, ph = rect
        if pixels is None or pmask is None or pixels.shape[:2] != (ph, pw) or pmask.shape[:2] != (ph, pw):
            continue
        region = image[y:y + ph, x:x + pw]
        m = pmask > 127
        region[m] = pixels[m]
        applied += 1
    return applied
