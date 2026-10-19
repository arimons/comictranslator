# 만화 번역 텍스트 오버레이 렌더링 모듈 (DP 줄바꿈 + 페이지 폰트 조화)
#!/usr/bin/env python3

import argparse
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from pipeline.background_restore import apply_restore_patches
except ImportError:
    from background_restore import apply_restore_patches


# 기본 폰트 (normal dialogue)
FONT_PATH = Path("Fonts/Freesentation-5Medium.ttf")

# render_hint 별 폰트 매핑
def _first_existing_font(*paths: str) -> Path:
    for path in paths:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    return Path(paths[0])


_FONT_NORMAL_DIALOGUE  = Path("Fonts/Freesentation-5Medium.ttf")
_FONT_LIGHT            = Path("Fonts/Freesentation-3Light.ttf")
_FONT_SEMIBOLD         = Path("Fonts/Freesentation-6SemiBold.ttf")
_FONT_BOLD             = Path("Fonts/Freesentation-7Bold.ttf")
_FONT_IMPACT           = Path("Fonts/Pretendard-Black.otf")
_FONT_SERIF_EMPHASIS   = _first_existing_font("Fonts/a트랙터.ttf", "Fonts/a트랙터.ttf")
_FONT_MEMORY_SERIF     = _first_existing_font("Fonts/SourceHanSerifK-Medium.otf", "Fonts/RIDIBatang.otf")
_FONT_MEMORY_SERIF_BOLD = _first_existing_font("Fonts/SourceHanSerifK-Bold.otf", "Fonts/SourceHanSerifK-SemiBold.otf", "Fonts/RIDIBatang.otf")
_FONT_HANDWRITTEN      = Path("Fonts/NanumGiBbeumBarkEum.ttf")

MIN_FIT_FONT_SIZE = 16
VERTICAL_ELLIPSIS = "⋮"
VERTICAL_DOT_CHARS = frozenset({".", "･", "・"})
VERTICAL_ROTATED_GLYPHS = frozenset({"~", "～", "〜"})

_FONT_TRACKING: dict[str, int] = {
    "Freesentation-5Medium": -2,
    "Freesentation-6SemiBold": -2,
    "Freesentation-7Bold": -2,
    "Freesentation-4Regular": -1,
    "Pretendard-Black":        0,
    "RIDIBatang":              0,
    "SourceHanSerifK-Medium":  0,
}

_GLOBAL_TRACKING_OFFSET: int = 0

UNBOXED_MAX_FONT_SIZE = 38
UNBOXED_BG_ALPHA = 38          # 15% — LaMa 적용 unboxed (배경 이미 inpainting 완료)
UNBOXED_FALLBACK_BG_ALPHA = 64 # 25% — bbox_fallback unboxed
UNBOXED_DARK_BG_ALPHA = 0      # 외곽선으로 가독성 확보, 배경 박스는 거의 사용하지 않음
UNBOXED_TG_FALLBACK_FONT_SCALE = 0.90
SPLIT_PARENT_FONT_CAP_RATIO = 1.20
BUBBLE_BG_ALPHA = 0
TEXT_STROKE_WIDTH = 2
DARK_TEXT_STROKE_WIDTH = 3


@dataclass
class Layout:
    font_size: int
    lines: list[str]
    inner_rect: tuple[int, int, int, int]
    line_height: int
    measured_width: int
    measured_height: int
    target_lines: int


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def load_clusters(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("clusters", [])
    if isinstance(data, list):
        return data
    return []


def load_overlay_guidance(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def overlay_region_overrides(overlay_guidance: dict) -> dict[str, dict]:
    regions = overlay_guidance.get("regions") if isinstance(overlay_guidance, dict) else {}
    if not isinstance(regions, dict):
        return {}
    result: dict[str, dict] = {}
    for key, value in regions.items():
        if not isinstance(value, dict):
            continue
        result[str(key)] = value
    return result


def build_region_info_by_id_from_regions_json(regions_json_path: Path | None) -> dict[int, dict]:
    """regions.json에서 overlay 수동 렌더 fallback용 region 정보를 만든다."""
    if regions_json_path is None or not regions_json_path.exists():
        return {}
    try:
        data = json.loads(regions_json_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    regions = data.get("regions", []) if isinstance(data, dict) else []
    result: dict[int, dict] = {}
    for region in regions:
        if not isinstance(region, dict):
            continue
        try:
            region_id = int(region.get("region_id"))
        except (TypeError, ValueError):
            continue
        result[region_id] = region
    return result


def overlay_eraser_rects(overlay_guidance: dict | None) -> list[list[int]]:
    """overlay 수동 지우개 rect 목록을 정수 bbox로 정규화한다."""
    rects = overlay_guidance.get("eraser_rects") if isinstance(overlay_guidance, dict) else []
    if not isinstance(rects, list):
        return []
    result: list[list[int]] = []
    for rect in rects:
        if not isinstance(rect, list) or len(rect) < 4:
            continue
        try:
            x, y, w, h = [int(round(float(v))) for v in rect[:4]]
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        result.append([x, y, w, h])
    return result


def apply_overlay_eraser_rects(
    image: np.ndarray,
    rects: list[list[int]],
    fill: tuple[int, int, int] = (255, 255, 255),
) -> int:
    """수동 지우개 rect를 흰색으로 채운다."""
    img_h, img_w = image.shape[:2]
    applied = 0
    for rect in rects:
        x, y, w, h = [int(v) for v in rect[:4]]
        x1 = max(0, min(img_w, x))
        y1 = max(0, min(img_h, y))
        x2 = max(0, min(img_w, x + w))
        y2 = max(0, min(img_h, y + h))
        if x2 <= x1 or y2 <= y1:
            continue
        image[y1:y2, x1:x2] = fill
        applied += 1
    return applied


def overlay_key_for_cluster(cluster: dict, flood_info_by_anchor: dict[int, dict]) -> str | None:
    """overlay override/layout에서 사용할 cluster 단위 key를 반환한다."""
    region_id = region_id_for_cluster(cluster, flood_info_by_anchor)
    if region_id is None:
        return None
    segment_index = cluster.get("source_segment_index")
    try:
        if segment_index is not None:
            return f"{region_id}:{int(segment_index)}"
    except (TypeError, ValueError):
        pass
    return str(region_id)


def overlay_override_for_cluster(
    cluster: dict,
    flood_info_by_anchor: dict[int, dict],
    overlay_overrides: dict[str, dict],
) -> dict:
    cluster_key = overlay_key_for_cluster(cluster, flood_info_by_anchor)
    if cluster_key:
        for key in (cluster_key, cluster_key.replace(":", "-") if ":" in cluster_key else None):
            if not key:
                continue
            override = overlay_overrides.get(key)
            if override:
                return override
    try:
        sentence_id = cluster.get("sentence_id")
        if sentence_id is not None:
            override = overlay_overrides.get(f"s:{int(sentence_id)}")
            if override:
                return override
    except (TypeError, ValueError):
        pass
    if cluster.get("source_segment_index") is not None:
        return {}
    try:
        source_region_id = cluster.get("source_region_id")
        if source_region_id is not None:
            override = overlay_overrides.get(str(int(source_region_id)))
            if override:
                return override
    except (TypeError, ValueError):
        pass
    for raw_anchor_id in cluster.get("matched_cluster", []):
        try:
            anchor_id = int(raw_anchor_id)
        except (TypeError, ValueError):
            continue
        region_id = flood_info_by_anchor.get(anchor_id, {}).get("region_id")
        if region_id is None:
            continue
        try:
            override = overlay_overrides.get(str(int(region_id)))
        except (TypeError, ValueError):
            override = None
        if override:
            return override
    return {}


def overlay_segment_mode_for_region(region_id: int | None, overlay_overrides: dict[str, dict]) -> str | None:
    """같은 region 안 segment를 overlay에서 강제 merge/split할지 반환한다."""
    if region_id is None:
        return None
    override = overlay_overrides.get(str(region_id))
    if not isinstance(override, dict):
        return None
    mode = override.get("segment_mode")
    return mode if mode in {"merge", "split"} else None


def overlay_override_text_align(override: dict | None) -> str:
    """safe_text_rect 내부 텍스트 좌/중/우 정렬 값을 반환한다."""
    value = str((override or {}).get("text_align") or "center").lower()
    return value if value in {"left", "center", "right"} else "center"


def overlay_override_has_font_size_delta(override: dict | None) -> bool:
    """수동 font size 조절이 있는지 판정한다."""
    try:
        return int((override or {}).get("font_size_delta", 0) or 0) != 0
    except (TypeError, ValueError):
        return False


def overlay_override_has_font_size(override: dict | None) -> bool:
    """절대 font size 지정이 있는지 판정한다."""
    try:
        return int((override or {}).get("font_size", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def overlay_override_rotation_deg(override: dict | None) -> float:
    """overlay override의 회전 각도를 -45~45도로 정규화한다."""
    try:
        value = float((override or {}).get("rotation_deg", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return max(-45.0, min(45.0, value))


def normalized_font_weight_value(value: object) -> int | None:
    """font weight 숫자값을 100~1000 범위로 정규화한다."""
    try:
        weight = int(round(float(value or 0)))
    except (TypeError, ValueError):
        return None
    if weight <= 0:
        return None
    return max(100, min(1000, weight))


def overlay_override_font_weight(override: dict | None) -> int | None:
    """etc style의 variable font weight override를 정규화한다."""
    if not isinstance(override, dict):
        return None
    if override.get("font_style") != "etc":
        return None
    return normalized_font_weight_value(override.get("font_weight"))


def overlay_override_stroke_width(override: dict | None, default_width: int) -> int:
    """수동 outline/stroke 두께를 0~12px 범위로 정규화한다."""
    if not isinstance(override, dict):
        return default_width
    raw_value = override.get("stroke_width", override.get("text_stroke_width"))
    if raw_value is None:
        return default_width
    try:
        width = int(round(float(raw_value)))
    except (TypeError, ValueError):
        return default_width
    return max(0, min(12, width))


def overlay_override_has_manual_layout(override: dict | None) -> bool:
    """split sibling 자동 보정에서 분리해야 하는 수동 layout override인지 판정한다."""
    if not isinstance(override, dict):
        return False
    if overlay_override_has_font_size_delta(override) or overlay_override_has_font_size(override):
        return True
    if overlay_override_rotation_deg(override):
        return True
    if "text_override" in override or "safe_text_rect" in override:
        return True
    if override.get("force_vertical") or override.get("force_horizontal"):
        return True
    if (
        override.get("font_style")
        or override.get("font_path")
        or override.get("custom_font_path")
        or overlay_override_font_weight(override)
    ):
        return True
    return False


def overlay_override_disables_automatic_fallback(override: dict | None) -> bool:
    """최종 overlay 수동 수정이 있으면 preserve/restore 계열 자동 fallback을 폐기한다."""
    return bool(override)


def overlay_suppressed_region_ids(overlay_guidance: dict | None) -> set[int]:
    """빈 text_override는 해당 region 렌더/원본 복원을 명시적으로 끄는 신호다."""
    regions = overlay_guidance.get("regions") if isinstance(overlay_guidance, dict) else {}
    if not isinstance(regions, dict):
        return set()
    suppressed: set[int] = set()
    for key, value in regions.items():
        if not isinstance(value, dict):
            continue
        if not isinstance(value.get("text_override"), str):
            continue
        if value.get("text_override", "").strip():
            continue
        try:
            region_key = str(key).split(":", 1)[0].split("-", 1)[0]
            suppressed.add(int(region_key))
        except (TypeError, ValueError):
            continue
    return suppressed


def region_id_for_cluster(cluster: dict, flood_info_by_anchor: dict[int, dict]) -> int | None:
    """cluster anchor가 속한 flood/dialogue region id를 반환한다."""
    try:
        source_region_id = cluster.get("source_region_id")
        if source_region_id is not None:
            return int(source_region_id)
    except (TypeError, ValueError):
        pass
    for raw_anchor_id in cluster.get("matched_cluster", []):
        try:
            anchor_id = int(raw_anchor_id)
        except (TypeError, ValueError):
            continue
        region_id = flood_info_by_anchor.get(anchor_id, {}).get("region_id")
        if region_id is None:
            continue
        try:
            return int(region_id)
        except (TypeError, ValueError):
            return None
    return None


def overlay_layout_summary(prepared: list, flood_info_by_anchor: dict[int, dict]) -> dict:
    """실제 렌더러가 선택한 region별 기본 텍스트 배치 정보를 JSON으로 만든다."""
    regions: dict[str, dict] = {}
    for item in prepared:
        cluster, text, bbox, bbox_source, layout, _src_lc, cluster_font_path, *_ = item
        region_id = region_id_for_cluster(cluster, flood_info_by_anchor)
        if region_id is None or layout is None:
            continue
        key = overlay_key_for_cluster(cluster, flood_info_by_anchor) or str(region_id)
        regions[key] = {
            "region_id": region_id,
            "overlay_key": key,
            "sentence_id": cluster.get("sentence_id"),
            "source_segment_index": cluster.get("source_segment_index"),
            "matched_cluster": cluster.get("matched_cluster", []),
            "text_source": text,
            "text_layout": "\n".join(layout.lines),
            "lines": layout.lines,
            "bbox": [int(v) for v in bbox[:4]],
            "bbox_source": bbox_source,
            "inner_rect": [int(v) for v in layout.inner_rect],
            "font_size": int(layout.font_size),
            "font_path": str(cluster_font_path),
        }
    return {"regions": regions}


def font_path_for_cluster(cluster: dict, default_font_path: Path) -> Path:
    """render_hint 단일 필드로 폰트를 결정한다."""
    render_hint = cluster.get("render_hint_final") or cluster.get("render_hint", "")
    if render_hint in {"sfx", "efx"}:
        render_hint = "serif_emphasis"
    if should_force_serif_for_large_title_style(cluster):
        render_hint = "serif_emphasis"

    if render_hint == "impact":
        return _FONT_IMPACT if _FONT_IMPACT.exists() else default_font_path

    if render_hint == "memory_serif":
        return _FONT_MEMORY_SERIF if _FONT_MEMORY_SERIF.exists() else default_font_path

    if render_hint == "serif_bold":
        return _FONT_MEMORY_SERIF_BOLD if _FONT_MEMORY_SERIF_BOLD.exists() else font_path_for_style("bold", default_font_path)

    if render_hint == "semibold":
        return _FONT_SEMIBOLD if _FONT_SEMIBOLD.exists() else default_font_path

    if render_hint == "bold":
        return _FONT_BOLD if _FONT_BOLD.exists() else (_FONT_SEMIBOLD if _FONT_SEMIBOLD.exists() else default_font_path)

    if render_hint == "serif_emphasis":
        if _FONT_SERIF_EMPHASIS.exists():
            return _FONT_SERIF_EMPHASIS
        return _FONT_IMPACT if _FONT_IMPACT.exists() else default_font_path

    if render_hint == "normal":
        return default_font_path

    return default_font_path


def font_path_for_style(font_style: str | None, default_font_path: Path) -> Path:
    """수동 overlay style 값으로 폰트를 결정한다."""
    if font_style == "light":
        return _FONT_LIGHT if _FONT_LIGHT.exists() else default_font_path
    if font_style == "impact":
        return _FONT_IMPACT if _FONT_IMPACT.exists() else default_font_path
    if font_style in {"memory_serif", "serif"}:
        return _FONT_MEMORY_SERIF if _FONT_MEMORY_SERIF.exists() else default_font_path
    if font_style == "serif_bold":
        return _FONT_MEMORY_SERIF_BOLD if _FONT_MEMORY_SERIF_BOLD.exists() else font_path_for_style("bold", default_font_path)
    if font_style == "serif_emphasis":
        if _FONT_SERIF_EMPHASIS.exists():
            return _FONT_SERIF_EMPHASIS
        return _FONT_IMPACT if _FONT_IMPACT.exists() else default_font_path
    if font_style == "bold":
        return _FONT_BOLD if _FONT_BOLD.exists() else (_FONT_SEMIBOLD if _FONT_SEMIBOLD.exists() else default_font_path)
    if font_style == "efx":
        if _FONT_SERIF_EMPHASIS.exists():
            return _FONT_SERIF_EMPHASIS
        return _FONT_IMPACT if _FONT_IMPACT.exists() else default_font_path
    if font_style == "handwritten":
        return _FONT_HANDWRITTEN if _FONT_HANDWRITTEN.exists() else default_font_path
    return default_font_path


def font_path_from_overlay_override(override: dict | None, default_font_path: Path, cluster: dict) -> Path:
    """region overlay override에서 직접 폰트 지정이 있으면 우선 적용한다."""
    if isinstance(override, dict):
        font_style = override.get("font_style")
        if not font_style:
            return font_path_for_cluster(cluster, default_font_path)
        if font_style == "etc":
            raw_path = override.get("font_path") or override.get("custom_font_path")
            if raw_path:
                candidate = Path(str(raw_path))
                if candidate.exists():
                    return candidate
            return default_font_path
        return font_path_for_style(font_style, default_font_path)
    return font_path_for_cluster(cluster, default_font_path)


def font_axis_name(axis: dict) -> str:
    """Pillow variable font axis 이름을 비교 가능한 문자열로 변환한다."""
    raw_name = axis.get("name", "")
    if isinstance(raw_name, bytes):
        return raw_name.decode("utf-8", errors="ignore").lower()
    return str(raw_name).lower()


@lru_cache(maxsize=512)
def cached_truetype_font(font_path: str, font_size: int) -> ImageFont.FreeTypeFont:
    """반복 layout 탐색에서 같은 폰트를 계속 여는 비용을 줄인다."""
    return ImageFont.truetype(font_path, int(font_size))


def load_font(font_path: Path, font_size: int, font_weight: int | None = None) -> ImageFont.FreeTypeFont:
    """폰트를 로드하고 variable weight axis가 있으면 적용한다."""
    weight = normalized_font_weight_value(font_weight)
    if weight is None:
        return cached_truetype_font(str(font_path), int(font_size))
    font = ImageFont.truetype(str(font_path), font_size)
    try:
        axes = font.get_variation_axes()
    except Exception:
        return font
    if not axes:
        return font
    values: list[float] = []
    changed = False
    for axis in axes:
        try:
            minimum = float(axis.get("minimum", 0))
            default = float(axis.get("default", minimum))
            maximum = float(axis.get("maximum", default))
        except (TypeError, ValueError):
            values.append(0)
            continue
        if "weight" in font_axis_name(axis) or "wght" in font_axis_name(axis):
            values.append(clamp(float(weight), minimum, maximum))
            changed = True
        else:
            values.append(default)
    if not changed:
        return font
    try:
        font.set_variation_by_axes(values)
    except Exception:
        return font
    # 측정 캐시는 (path, size)의 기본 weight 인스턴스로 재므로, weight를 적용한 폰트는 캐시에서 빼도록 표시한다.
    font._overlay_variation_weight = weight
    return font


def should_force_serif_for_large_title_style(cluster: dict) -> bool:
    """CV상 초대형 짧은 제목형 텍스트는 OCR hint와 무관하게 serif 강조 폰트로 렌더한다."""
    features = cluster.get("style_features") or {}
    line_features = features.get("line_style_features") or features
    try:
        font_size_estimate = float(line_features.get("font_size_estimate", 0.0) or 0.0)
        char_count = int(line_features.get("char_count", 999) or 999)
        line_count = int(line_features.get("line_count", 999) or 999)
    except (TypeError, ValueError):
        return False
    short_label = bool(line_features.get("short_label_candidate")) or (char_count <= 10 and line_count <= 3)
    return short_label and font_size_estimate >= 80 and char_count <= 10 and line_count <= 3


def should_try_vertical_title_layout(cluster: dict, text: str) -> bool:
    """CV상 큰 제목/효과문 후보는 가로 배치 후 세로 후보도 비교한다."""
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized or weighted_len(normalized) > 12:
        return False
    features = cluster.get("style_features") or {}
    line_features = features.get("line_style_features") or features
    try:
        font_size_estimate = float(line_features.get("font_size_estimate", 0.0) or 0.0)
        char_count = int(line_features.get("char_count", 999) or 999)
        line_count = int(line_features.get("line_count", 999) or 999)
    except (TypeError, ValueError):
        font_size_estimate = 0.0
        char_count = 999
        line_count = 999
    render_hint = cluster.get("render_hint_final") or cluster.get("render_hint")
    if render_hint in {"sfx", "efx"}:
        render_hint = "serif_emphasis"
    style_large_title = should_force_serif_for_large_title_style(cluster)
    impact_like = render_hint in {"impact", "serif_emphasis"} and font_size_estimate >= 70
    return (style_large_title or impact_like) and char_count <= 10 and line_count <= 3


def should_soften_unboxed_tg_bbox_layout(cluster: dict, bbox_source: str) -> bool:
    """TG bbox 기반 unboxed fallback은 실제 글자 영역이 빡빡하므로 폰트만 살짝 낮춘다."""
    if cluster.get("location") != "unboxed" or bbox_source != "unboxed_rect":
        return False

    quality_flags = {str(flag) for flag in (cluster.get("quality_flags") or [])}
    source_method = str(cluster.get("source_method") or "")
    source_region_type = str(cluster.get("source_region_type") or "")

    # 어두운 배경 효과음은 이미 별도 배경/폰트 처리 대상이라 여기서 누르지 않는다.
    if source_method == "dark_flood" or "dark_background" in quality_flags:
        return False

    return (
        source_method in {"bbox_fallback", "unboxed_consolidated"}
        or source_region_type in {"bbox_fallback", "unboxed_consolidated"}
        or "unboxed_consolidated_tg_cluster" in quality_flags
    )


def softened_unboxed_tg_bbox_font_cap(font_size: int) -> int:
    """U TG fallback 폰트 상한. 최저 렌더 가능 크기 아래로는 내리지 않는다."""
    return max(MIN_FIT_FONT_SIZE, int(math.floor(font_size * UNBOXED_TG_FALLBACK_FONT_SCALE)))


def split_parent_font_cap(reference_font_size: int) -> int:
    """같은 원본 bubble에서 split된 항목의 큰 폰트만 완만하게 누르는 상한."""
    return max(MIN_FIT_FONT_SIZE, int(math.ceil(reference_font_size * SPLIT_PARENT_FONT_CAP_RATIO)))


def vertical_cap_for(
    max_font_size: int | None,
    is_unboxed: bool,
    strict_max_font_size: bool = False,
) -> int | None:
    """세로쓰기 후보의 max font를 결정한다."""
    if max_font_size is None:
        return None
    if is_unboxed or strict_max_font_size:
        return max_font_size
    return max_font_size + 8


def weighted_len(text: str) -> float:
    total = 0.0
    for ch in text:
        if ch.isspace():
            total += 0.45
        elif ch in ".,!?…。．、，·":
            total += 0.35
        elif re.match(r"[A-Za-z0-9]", ch):
            total += 0.65
        else:
            total += 1.0
    return max(total, 1.0)


def token_weight(token: str) -> float:
    return weighted_len(token)


def tokenize(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    # 닫힌 따옴표/괄호 類(」』)）)를 별도 그룹으로 분리 → 앞 토큰에 suffix로 합침
    # 단일 dash도 별도 그룹으로 잡아야 findall에서 누락되지 않는다.
    pattern = r"\s+|[—―-]{2,}|\.{2,}[?!?]*|…+[?!?]*|[!?]+|[.,。．、，·]+|[」』)）]+|[—―-]|[^\s.…!?.,。．、，·」』)）—―-]+"
    raw_tokens = re.findall(pattern, text.strip())
    if not raw_tokens:
        return [text.strip()] if text.strip() else []
    tokens: list[str] = []
    pending_space = False
    for token in raw_tokens:
        if token.isspace():
            pending_space = True
            continue
        # 일반 마침/느낌/물음/말줄임표 + 닫힌 괄호 類: 앞 토큰에 suffix로 붙임
        if re.fullmatch(r"[.,!?。．、，·…」』)）]+", token) and tokens:
            tokens[-1] = tokens[-1].rstrip() + token
            pending_space = False
            continue
        # 공백 없이 붙은 단일 dash는 앞 토큰에 붙여 줄 끝 dash가 단어와 같은 줄에 남게 한다.
        if token in {"-", "—", "―"} and tokens and not pending_space:
            tokens[-1] += token
            continue
        if pending_space and token[0] not in set(".,!?…。．、，·」』)）") and tokens:
            tokens.append(" " + token)
        else:
            tokens.append(token)
        pending_space = False
    return tokens


def join_tokens(tokens: Iterable[str]) -> str:
    text = "".join(tokens).strip()
    text = re.sub(r"\s+([.,!?…。．、，·])", r"\1", text)
    text = re.sub(r"([….])\s+", r"\1", text)
    return text


def ellipsis_to_periods(ellipsis: str) -> str:
    """세로 렌더용 말줄임표 글리프를 period run으로 정규화한다."""
    if set(ellipsis) <= {"…"}:
        return "." * (len(ellipsis) * 3)
    return "." * len(ellipsis)


def split_trailing_ellipsis_tokens(tokens: list[str]) -> list[str]:
    """마지막 단어에 붙은 순수 말줄임표를 별도 줄 후보로 분리한다."""
    result: list[str] = []
    for token in tokens:
        match = re.match(r"^(\s*)(.*?)(\.{2,}|…+)$", token)
        body = match.group(2).strip() if match else ""
        if not match or not body:
            result.append(token)
            continue
        prefix, head, ellipsis = match.groups()
        result.append(prefix + head)
        result.append(ellipsis)
    return result


def expand_vertical_ellipsis_to_periods(text: str) -> str:
    """세로쓰기에서 말줄임표를 점 3개 단위로 펼친다."""
    return re.sub(r"\.{2,}|…+", lambda m: ellipsis_to_periods(m.group(0)), text)


def chunk_korean_text(text: str, chunk_size: int = 2) -> list[str]:
    """긴 한글 단일 구절을 2~3글자 단위로 나눈다."""
    compact = re.sub(r"\s+", "", text or "")
    if not re.fullmatch(r"[가-힣]+", compact):
        return []
    chunks: list[str] = []
    i = 0
    while i < len(compact):
        remaining = len(compact) - i
        if remaining <= chunk_size + 1:
            chunks.append(compact[i:])
            break
        chunks.append(compact[i:i + chunk_size])
        i += chunk_size
    return chunks


def split_overlong_vertical_ellipsis_text(text: str) -> list[str]:
    """말줄임표 때문에 과하게 길어진 짧은 세로문을 2줄 구절 후보로 나눈다."""
    compact = re.sub(r"\s+", "", text or "")
    match = re.fullmatch(r"(?P<lead>\.{2,}|…+)?(?P<body>[가-힣]{4,8})(?P<trail>\.{2,}|…+)?(?P<punct>[!?？！?]*)", compact)
    if not match:
        return []
    lead = ellipsis_to_periods(match.group("lead") or "")
    body = match.group("body")
    trail = ellipsis_to_periods(match.group("trail") or "")
    punct = match.group("punct") or ""
    if not lead and not trail:
        return []

    split_at = math.ceil(len(body) / 2)
    if len(body) - split_at < 2 and len(body) >= 4:
        split_at = len(body) - 2
    return [lead + body[:split_at], body[split_at:] + trail + punct]


def should_preserve_korean_token(clean: str) -> bool:
    return bool(re.search(r"스[은는이가을를의]$", clean) and len(clean) >= 5)


def split_korean_particle_compound(clean: str) -> list[str] | None:
    """긴 한글 복합어가 조사로 끝나면 의미 단위에 가깝게 둘로 나눈다.

    4자 이하 어간은 분리하지 않는다.
    - 외래어 음차("시나리오는" → 어간 4자) 오분리 방지
    - "할아버지가" → 어간 4자 → 분리 안 함
    5자 이상 어간: "열등종류들은" 등 장음절 복합어만 분리.
    """
    if len(clean) < 5 or clean[-1] not in "이가은는을를":
        return None
    stem = clean[:-1]
    if len(stem) < 5:  # 4자 어간까지는 midpoint 분리 금지
        return None
    split_at = len(stem) // 2
    return [clean[:split_at], clean[split_at:]]


def split_korean_token(token: str) -> list[str]:
    clean = token.strip()
    prefix = token[:len(token) - len(token.lstrip())]
    trailing_punct = ""
    punct_match = re.search(r"[…—\-\.!?。．、，·]+$", clean)
    if punct_match:
        trailing_punct = punct_match.group(0)
        clean = clean[:-len(trailing_punct)]
    if not re.fullmatch(r"[가-힣~]+", clean):
        return [token]
    if should_preserve_korean_token(clean):
        return [prefix + clean + trailing_punct]
    particle_compound = split_korean_particle_compound(clean)
    if particle_compound is not None:
        return [prefix + particle_compound[0], particle_compound[1] + trailing_punct]

    suffixes = [
        "드립니다", "습니다", "습니까", "입니다", "입니까",
        "만으로도", "으로도", "이구나", "구나", "이잖아", "잖아", "는데요", "군요", "네요", "나요",
        "에서", "인가", "라고", "처럼", "까지", "부터", "이고", "로서",
        "님", "군", "씨",
    ]
    for suffix in suffixes:
        if clean.endswith(suffix) and len(clean) - len(suffix) >= 2:
            head = clean[:-len(suffix)]
            return [prefix + head, suffix + trailing_punct]

    # 인식된 형태소 경계가 없으면 분리하지 않는다.
    # 무조건 중간 분리("해롤드의"→["해롤","드의"])는 고유명사·일반어를 망가뜨린다.
    return [prefix + clean + trailing_punct]


def explode_long_tokens(tokens: list[str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        if token.startswith(("…", ".")):
            result.append(token)
        else:
            split = split_korean_token(token)
            if (
                split == [token]
                and weighted_len(token.strip()) > 8.0  # 9자 이상 초장음절어만 글자단위 fallback
                and re.fullmatch(r"[가-힣~]+", token.strip())
                and not should_preserve_korean_token(token.strip())
            ):
                result.extend(list(token))
            else:
                result.extend(split)
    return result


def is_synthetic_vertical_ellipsis(text: str) -> bool:
    """폰트 fallback 없이 직접 렌더할 세로 말줄임표인지 판단한다."""
    return text.strip() == VERTICAL_ELLIPSIS


def is_synthetic_vertical_dot(text: str) -> bool:
    """세로쓰기 말줄임표용 단독 점을 직접 렌더할지 판단한다."""
    return text.strip() in VERTICAL_DOT_CHARS


def is_vertical_rotated_glyph(text: str) -> bool:
    """세로쓰기에서 글리프만 90도 회전해 그릴 문자."""
    return text.strip() in VERTICAL_ROTATED_GLYPHS


def normalize_inline_dot_ellipsis(text: str) -> str:
    """글자와 섞인 halfwidth 점 run은 폰트 baseline을 타는 말줄임표로 바꾼다."""
    def replace(match: re.Match) -> str:
        count = max(1, math.ceil(len(match.group(0)) / 3))
        return "…" * count

    return re.sub(r"[.･・]{2,}", replace, str(text or ""))


def has_dot_ellipsis_run(text: str) -> bool:
    return bool(re.search(r"[.･・]{2,}", str(text or "")))


def split_trailing_dot_ellipsis(text: str) -> tuple[str, str] | None:
    """본문 뒤 말줄임표 run을 본문과 점 run으로 분리한다."""
    match = re.match(r"^(?P<body>.*?)(?P<dots>[.…･・⋮]+)$", str(text or ""))
    if not match:
        return None
    body = match.group("body")
    dots = match.group("dots")
    if not body.strip() or dot_ellipsis_unit_count(dots) < 2:
        return None
    return body, dots


def is_dot_ellipsis_only_text(text: str) -> bool:
    return bool(re.fullmatch(r"[\s.…･・⋮]+", str(text or ""))) and dot_ellipsis_unit_count(text) >= 2


def dot_ellipsis_unit_count(text: str) -> int:
    total = 0
    for ch in str(text or ""):
        if ch in {"…", VERTICAL_ELLIPSIS}:
            total += 3
        elif ch in VERTICAL_DOT_CHARS:
            total += 1
    return total


def dot_ellipsis_vertical_lines(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", str(text or ""))
    return [compact] if compact else []


def synthetic_vertical_dot_run_size(
    font_size: int,
    count: int,
    font: ImageFont.FreeTypeFont | None = None,
) -> tuple[int, int]:
    """말줄임표 dot run 전체의 논리 크기."""
    radius = synthetic_dot_radius_for_font(font=font, font_size=font_size)
    diameter = radius * 2
    step = max(diameter + 2, int(round(font_size * 0.34)))
    height = diameter + step * max(0, count - 1)
    return diameter, height


def synthetic_vertical_dot_size(font_size: int) -> tuple[int, int]:
    """세로쓰기 단독 점의 논리 크기."""
    radius = synthetic_dot_radius_for_font(font_size=font_size)
    diameter = radius * 2
    return diameter, diameter


def draw_synthetic_vertical_dot(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    font_size: int,
    fill: tuple[int, int, int],
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] | None = None,
    font: ImageFont.FreeTypeFont | None = None,
) -> None:
    """세로쓰기 말줄임표용 단독 점을 폰트 glyph 대신 직접 그린다."""
    x, y = position
    radius = synthetic_dot_radius_for_font(font=font, font_size=font_size)
    outer_radius = radius + max(0, int(stroke_width))
    cx = x + radius
    cy = y + radius
    if stroke_width > 0:
        outline_fill = stroke_fill if stroke_fill is not None else fill
        draw.ellipse(
            (cx - outer_radius, cy - outer_radius, cx + outer_radius, cy + outer_radius),
            fill=outline_fill,
        )
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)


def draw_synthetic_vertical_dot_run(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    font_size: int,
    count: int,
    fill: tuple[int, int, int],
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] | None = None,
    font: ImageFont.FreeTypeFont | None = None,
) -> None:
    """말줄임표 dot run을 끊김 없는 균일 간격 원형 점으로 그린다."""
    x, y = position
    radius = synthetic_dot_radius_for_font(font=font, font_size=font_size)
    diameter, height = synthetic_vertical_dot_run_size(font_size, count, font=font)
    step = (height - diameter) / max(1, count - 1)
    cx = x + diameter / 2
    outer_radius = radius + max(0, int(stroke_width))
    for idx in range(count):
        cy = y + radius + step * idx
        if stroke_width > 0:
            outline_fill = stroke_fill if stroke_fill is not None else fill
            draw.ellipse(
                (cx - outer_radius, cy - outer_radius, cx + outer_radius, cy + outer_radius),
                fill=outline_fill,
            )
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)


def synthetic_vertical_ellipsis_size(font_size: int) -> tuple[int, int]:
    """세로 말줄임표를 직접 그릴 때 사용할 논리 크기."""
    radius = synthetic_dot_radius_for_font(font_size=font_size)
    diameter = radius * 2
    step = max(diameter + 2, int(round(font_size * 0.24)))
    return diameter, diameter + step * 2


def draw_synthetic_vertical_ellipsis(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    font_size: int,
    fill: tuple[int, int, int],
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] | None = None,
    font: ImageFont.FreeTypeFont | None = None,
) -> None:
    """폰트에 없는 U+22EE 대신 원형 점 3개를 직접 그린다."""
    x, y = position
    radius = synthetic_dot_radius_for_font(font=font, font_size=font_size)
    diameter, height = synthetic_vertical_ellipsis_size(font_size)
    cx = x + diameter / 2
    step = (height - diameter) / 2
    outer_radius = radius + max(0, int(stroke_width))
    for idx in range(3):
        cy = y + radius + step * idx
        if stroke_width > 0:
            outline_fill = stroke_fill if stroke_fill is not None else fill
            draw.ellipse(
                (cx - outer_radius, cy - outer_radius, cx + outer_radius, cy + outer_radius),
                fill=outline_fill,
            )
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)


def font_cache_key(font: ImageFont.FreeTypeFont) -> tuple[str, int] | None:
    # variable weight를 적용한 폰트는 (path, size) 캐시로 재면 기본 weight로 측정되므로 캐시하지 않는다.
    if getattr(font, "_overlay_variation_weight", None) is not None:
        return None
    path = getattr(font, "path", None)
    size = getattr(font, "size", None)
    if not path or not size:
        return None
    return str(path), int(size)


@lru_cache(maxsize=20000)
def cached_text_bbox(font_path: str, font_size: int, text: str, stroke_width: int = 0) -> tuple[int, int, int, int]:
    font = cached_truetype_font(font_path, int(font_size))
    return font.getbbox(text, stroke_width=stroke_width)


def font_text_bbox(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    text: str,
    stroke_width: int = 0,
) -> tuple[int, int, int, int]:
    key = font_cache_key(font)
    if key is None:
        return draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return cached_text_bbox(key[0], key[1], text, int(stroke_width))


def rotated_vertical_glyph_size(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    text: str,
    stroke_width: int = 0,
) -> tuple[int, int]:
    """가로 glyph를 90도 회전했을 때의 논리 크기."""
    box = font_text_bbox(draw, font, text, stroke_width=stroke_width)
    return max(1, box[3] - box[1]), max(1, box[2] - box[0])


def draw_rotated_vertical_glyph(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] | None = None,
) -> None:
    """세로쓰기용 회전 glyph를 현재 draw target에 합성한다."""
    box = font_text_bbox(draw, font, text, stroke_width=stroke_width)
    src_w = max(1, box[2] - box[0])
    src_h = max(1, box[3] - box[1])
    pad = max(2, int(stroke_width) + 2)
    src = Image.new("RGBA", (src_w + pad * 2, src_h + pad * 2), (0, 0, 0, 0))
    src_draw = ImageDraw.Draw(src)
    src_draw.text(
        (pad - box[0], pad - box[1]),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill if stroke_fill is not None else fill,
    )
    rotated = src.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
    x, y = position
    target = draw._image
    paste_xy = (int(round(x - pad)), int(round(y - pad)))
    if target.mode == "RGBA":
        target.alpha_composite(rotated, paste_xy)
    else:
        target.paste(rotated.convert(target.mode), paste_xy, rotated)


def text_size(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    text: str,
    vertical_dot_run: bool = True,
) -> tuple[int, int]:
    if not text:
        return 0, 0
    if vertical_dot_run and is_dot_ellipsis_only_text(text):
        return synthetic_vertical_dot_run_size(
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            dot_ellipsis_unit_count(text),
            font=font,
        )
    trailing_dot = split_trailing_dot_ellipsis(text) if vertical_dot_run else None
    if trailing_dot is not None:
        body, dots = trailing_dot
        body_w, body_h = text_size(draw, font, body, vertical_dot_run=vertical_dot_run)
        dot_w, dot_h = synthetic_vertical_dot_run_size(
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            dot_ellipsis_unit_count(dots),
            font=font,
        )
        gap = max(3, int(round(getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.22)))
        return max(body_w, dot_w), body_h + gap + dot_h
    if vertical_dot_run and is_synthetic_vertical_ellipsis(text):
        return synthetic_vertical_ellipsis_size(getattr(font, "size", MIN_FIT_FONT_SIZE))
    if vertical_dot_run and is_synthetic_vertical_dot(text):
        return synthetic_vertical_dot_size(getattr(font, "size", MIN_FIT_FONT_SIZE))
    if vertical_dot_run and is_vertical_rotated_glyph(text):
        return rotated_vertical_glyph_size(draw, font, text)
    if has_dot_ellipsis_run(text):
        text = normalize_inline_dot_ellipsis(text)
    # 앞/뒤 dash run은 draw_tracked_text가 최소 0.9em 선으로 그리므로 폭도 같은 방식으로 잰다.
    decorative_width = decorative_line_text_width(draw, font, text, vertical_dot_run=vertical_dot_run)
    if vertical_dot_run and any(is_synthetic_vertical_ellipsis(ch) for ch in text):
        tracking = tracking_for_font(font)
        widths = [text_size(draw, font, ch, vertical_dot_run=vertical_dot_run)[0] for ch in text]
        heights = [text_size(draw, font, ch, vertical_dot_run=vertical_dot_run)[1] for ch in text]
        width = sum(widths) + tracking * max(0, len(text) - 1)
        if decorative_width is not None:
            width = decorative_width
        return max(0, int(round(width))), max(heights, default=0)
    box = font_text_bbox(draw, font, text)
    tracking = tracking_for_font(font)
    width = box[2] - box[0] + tracking * max(0, len(text) - 1)
    if decorative_width is not None:
        width = decorative_width
    return max(0, int(round(width))), box[3] - box[1]


def text_visual_y_bounds(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    text: str,
    stroke_width: int = 0,
    vertical_dot_run: bool = True,
) -> tuple[float, float]:
    """텍스트를 실제로 그렸을 때의 y 기준 상하단을 반환한다."""
    if not text:
        return 0.0, 0.0
    if vertical_dot_run and is_dot_ellipsis_only_text(text):
        _, height = synthetic_vertical_dot_run_size(
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            dot_ellipsis_unit_count(text),
            font=font,
        )
        return 0.0, float(height)
    trailing_dot = split_trailing_dot_ellipsis(text) if vertical_dot_run else None
    if trailing_dot is not None:
        body, dots = trailing_dot
        body_top, body_bottom = text_visual_y_bounds(
            draw,
            font,
            body,
            stroke_width=stroke_width,
            vertical_dot_run=vertical_dot_run,
        )
        _, dot_h = synthetic_vertical_dot_run_size(
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            dot_ellipsis_unit_count(dots),
            font=font,
        )
        gap = max(3.0, getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.22)
        return body_top, body_bottom + gap + dot_h
    if vertical_dot_run and is_synthetic_vertical_ellipsis(text):
        _, height = synthetic_vertical_ellipsis_size(getattr(font, "size", MIN_FIT_FONT_SIZE))
        return 0.0, float(height)
    if vertical_dot_run and is_synthetic_vertical_dot(text):
        _, height = synthetic_vertical_dot_size(getattr(font, "size", MIN_FIT_FONT_SIZE))
        return 0.0, float(height)
    if vertical_dot_run and is_vertical_rotated_glyph(text):
        _, height = rotated_vertical_glyph_size(draw, font, text, stroke_width=stroke_width)
        return 0.0, float(height)
    if has_dot_ellipsis_run(text):
        text = normalize_inline_dot_ellipsis(text)
    if vertical_dot_run and any(is_synthetic_vertical_ellipsis(ch) for ch in text):
        tops: list[float] = []
        bottoms: list[float] = []
        for ch in text:
            top, bottom = text_visual_y_bounds(
                draw,
                font,
                ch,
                stroke_width=stroke_width,
                vertical_dot_run=vertical_dot_run,
            )
            tops.append(top)
            bottoms.append(bottom)
        return min(tops, default=0.0), max(bottoms, default=0.0)
    box = font_text_bbox(draw, font, text, stroke_width=stroke_width)
    return float(box[1]), float(box[3])


def layout_visual_y_bounds(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    layout: Layout,
    stroke_width: int = 0,
    vertical_dot_run: bool = True,
) -> tuple[float, float]:
    """layout y=0 기준으로 실제 잉크 bbox의 상하단을 계산한다."""
    offsets = (
        vertical_line_offsets(layout.lines, layout.line_height, layout.font_size)
        if vertical_dot_run
        else [idx * layout.line_height for idx in range(len(layout.lines))]
    )
    top: float | None = None
    bottom: float | None = None
    for idx, line in enumerate(layout.lines):
        offset = offsets[idx] if idx < len(offsets) else idx * layout.line_height
        line_top, line_bottom = text_visual_y_bounds(
            draw,
            font,
            line,
            stroke_width=stroke_width,
            vertical_dot_run=vertical_dot_run,
        )
        actual_top = offset + line_top
        actual_bottom = offset + line_bottom
        top = actual_top if top is None else min(top, actual_top)
        bottom = actual_bottom if bottom is None else max(bottom, actual_bottom)
    if top is None or bottom is None:
        return 0.0, float(layout.measured_height)
    return top, bottom


def centered_draw_y_for_visual_bounds(
    target_top: float,
    target_height: float,
    visual_top: float,
    visual_bottom: float,
) -> float:
    """target 영역 안에서 실제 잉크 bbox 중심이 오도록 draw y를 계산한다."""
    visual_height = max(0.0, visual_bottom - visual_top)
    return target_top + (target_height - visual_height) / 2.0 - visual_top


def clamp_draw_y_to_visual_bounds(
    y: float,
    target_top: float,
    target_height: float,
    visual_top: float,
    visual_bottom: float,
) -> float:
    """잉크 bbox가 target 영역을 벗어나지 않게 draw y를 제한한다."""
    low = target_top - visual_top
    high = target_top + target_height - visual_bottom
    if high < low:
        return (low + high) / 2.0
    return clamp(y, low, high)


def layout_uses_vertical_dot_runs(layout: Layout) -> bool:
    """현재 layout이 세로쓰기 성격일 때만 말줄임표를 세로 dot run으로 그린다."""
    text_lines = [line.strip() for line in layout.lines if line.strip()]
    non_dot_lines = [line for line in text_lines if not is_dot_ellipsis_only_text(line)]
    if not non_dot_lines:
        return True
    short_count = sum(weighted_len(line) <= 1.5 for line in non_dot_lines)
    return short_count / max(1, len(non_dot_lines)) >= 0.6


def layout_with_font_size_delta(
    draw: ImageDraw.ImageDraw,
    layout: Layout,
    font_path: Path,
    delta_steps: int,
    pixels_per_step: int = 2,
) -> Layout:
    """수동 font size offset을 실제 렌더 크기에 직접 반영한다."""
    if not delta_steps:
        return layout
    next_size = max(MIN_FIT_FONT_SIZE, int(layout.font_size + delta_steps * pixels_per_step))
    if next_size == layout.font_size:
        return layout
    font = cached_truetype_font(str(font_path), int(next_size))
    line_widths = [text_size(draw, font, line, vertical_dot_run=False)[0] for line in layout.lines]
    line_heights = [text_size(draw, font, line, vertical_dot_run=False)[1] for line in layout.lines]
    line_height = max(int(round(next_size * 1.14)), max(line_heights, default=next_size))
    measured_height = line_height * max(0, len(layout.lines) - 1) + max(line_heights, default=next_size)
    return Layout(
        font_size=next_size,
        lines=layout.lines,
        inner_rect=layout.inner_rect,
        line_height=line_height,
        measured_width=max(line_widths, default=0),
        measured_height=measured_height,
        target_lines=layout.target_lines,
    )


def layout_from_manual_lines_with_font_size(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    inner_rect: tuple[int, int, int, int],
    font_path: Path,
    font_size: int,
    target_lines: int | None = None,
) -> Layout:
    """수동 줄바꿈과 font size가 있으면 fit 탐색 없이 그대로 Layout을 만든다."""
    next_size = max(MIN_FIT_FONT_SIZE, int(font_size))
    font = cached_truetype_font(str(font_path), int(next_size))
    clean_lines = [line.strip() for line in lines if line.strip()]
    if not clean_lines:
        clean_lines = [""]
    line_widths = [text_size(draw, font, line, vertical_dot_run=False)[0] for line in clean_lines]
    line_heights = [text_size(draw, font, line, vertical_dot_run=False)[1] for line in clean_lines]
    line_height = max(int(round(next_size * 1.14)), max(line_heights, default=next_size))
    candidate = Layout(
        font_size=next_size,
        lines=clean_lines,
        inner_rect=inner_rect,
        line_height=line_height,
        measured_width=max(line_widths, default=0),
        measured_height=0,
        target_lines=target_lines or len(clean_lines),
    )
    if layout_uses_vertical_dot_runs(candidate):
        offsets = vertical_line_offsets(clean_lines, line_height, next_size)
        measured_height = int(round((offsets[-1] if offsets else 0.0) + max(line_heights, default=next_size)))
    else:
        measured_height = line_height * max(0, len(clean_lines) - 1) + max(line_heights, default=next_size)
    return Layout(
        font_size=next_size,
        lines=clean_lines,
        inner_rect=inner_rect,
        line_height=line_height,
        measured_width=max(line_widths, default=0),
        measured_height=measured_height,
        target_lines=target_lines or len(clean_lines),
    )


@lru_cache(maxsize=256)
def _font_tracking_base(font_path: str) -> int:
    # 글자마다 불리므로 Path(...).stem 계산을 폰트 경로별로 한 번만 한다.
    return _FONT_TRACKING.get(Path(font_path).stem, 0)  # 예: 'Freesentation-5Medium'


def tracking_for_font(font: ImageFont.FreeTypeFont) -> int:
    """폰트 이름에서 stem을 추출해 tracking 오프셋(px) 반환."""
    try:
        return _font_tracking_base(font.path) + _GLOBAL_TRACKING_OFFSET
    except Exception:
        return _GLOBAL_TRACKING_OFFSET


def synthetic_dot_radius_for_font(font: ImageFont.FreeTypeFont | None = None, font_size: int | None = None) -> int:
    """폰트 굵기에 맞춰 직접 렌더 점의 반지름을 결정한다."""
    size = int(font_size or getattr(font, "size", MIN_FIT_FONT_SIZE))
    ratio = 0.050
    try:
        stem = Path(getattr(font, "path", "")).stem
    except Exception:
        stem = ""
    if stem in {"Freesentation-6SemiBold", "Freesentation-7Bold", "Pretendard-Black"}:
        ratio = 0.070
    elif stem in {Path(_FONT_SERIF_EMPHASIS).stem, Path(_FONT_MEMORY_SERIF).stem}:
        ratio = 0.060
    return max(1, int(round(size * ratio)))


def draw_tracked_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] | None = None,
    vertical_dot_run: bool = True,
) -> None:
    if vertical_dot_run and is_dot_ellipsis_only_text(text):
        draw_synthetic_vertical_dot_run(
            draw,
            position,
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            dot_ellipsis_unit_count(text),
            fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            font=font,
        )
        return
    if vertical_dot_run and is_synthetic_vertical_ellipsis(text):
        draw_synthetic_vertical_ellipsis(
            draw,
            position,
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            font=font,
        )
        return
    if vertical_dot_run and is_synthetic_vertical_dot(text):
        draw_synthetic_vertical_dot(
            draw,
            position,
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        return
    if vertical_dot_run and is_vertical_rotated_glyph(text):
        draw_rotated_vertical_glyph(
            draw,
            position,
            text.strip(),
            font,
            fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        return
    x, y = position
    trailing_dot = split_trailing_dot_ellipsis(text) if vertical_dot_run else None
    if trailing_dot is not None:
        body, dots = trailing_dot
        body_w, _body_h = text_size(draw, font, body, vertical_dot_run=vertical_dot_run)
        dot_w, _dot_h = synthetic_vertical_dot_run_size(
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            dot_ellipsis_unit_count(dots),
            font=font,
        )
        total_w = max(body_w, dot_w)
        body_x = x + (total_w - body_w) / 2.0
        dot_x = x + (total_w - dot_w) / 2.0
        body_top, body_bottom = text_visual_y_bounds(
            draw,
            font,
            body,
            stroke_width=stroke_width,
            vertical_dot_run=vertical_dot_run,
        )
        gap = max(3.0, getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.22)
        draw_tracked_text(
            draw,
            (body_x, y),
            body,
            font,
            fill,
            stroke_width,
            stroke_fill,
            vertical_dot_run=vertical_dot_run,
        )
        draw_synthetic_vertical_dot_run(
            draw,
            (dot_x, y + body_bottom + gap),
            getattr(font, "size", MIN_FIT_FONT_SIZE),
            dot_ellipsis_unit_count(dots),
            fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            font=font,
        )
        return
    # text_size와 같은 조건으로 점 run을 말줄임표로 바꾼다.
    # Freesentation에는 U+FF65/U+30FB 글리프가 없어 그대로 그리면 점이 보이지 않는다.
    if has_dot_ellipsis_run(text):
        text = normalize_inline_dot_ellipsis(text)
    tracking = tracking_for_font(font)
    leading_line = split_leading_horizontal_decorative_line(text)
    if leading_line:
        prefix, suffix = leading_line
        prefix_w = horizontal_rule_width(draw, font, prefix)
        line_width = max(2, int(round(getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.08)))
        mid_y = int(round(y + getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.55))
        draw.line((int(round(x)), mid_y, int(round(x + prefix_w)), mid_y), fill=fill, width=line_width)
        x += prefix_w + max(tracking, 0)
        if suffix:
            draw_tracked_text(draw, (x, y), suffix, font, fill, stroke_width, stroke_fill, vertical_dot_run=vertical_dot_run)
        return
    trailing_line = split_trailing_horizontal_decorative_line(text)
    if trailing_line:
        prefix, suffix = trailing_line
        if prefix:
            draw_tracked_text(draw, (x, y), prefix, font, fill, stroke_width, stroke_fill, vertical_dot_run=vertical_dot_run)
            x += text_size(draw, font, prefix, vertical_dot_run=vertical_dot_run)[0] + max(tracking, 0)
        suffix_w = horizontal_rule_width(draw, font, suffix)
        line_width = max(2, int(round(getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.08)))
        mid_y = int(round(y + getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.55))
        draw.line((int(round(x)), mid_y, int(round(x + suffix_w)), mid_y), fill=fill, width=line_width)
        return
    for ch in text:
        if vertical_dot_run and is_synthetic_vertical_dot(ch):
            draw_synthetic_vertical_dot(
                draw,
                (x, y),
                getattr(font, "size", MIN_FIT_FONT_SIZE),
                fill,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
                font=font,
            )
            x += text_size(draw, font, ch, vertical_dot_run=vertical_dot_run)[0] + tracking
            continue
        if vertical_dot_run and is_synthetic_vertical_ellipsis(ch):
            draw_synthetic_vertical_ellipsis(
                draw,
                (x, y),
                getattr(font, "size", MIN_FIT_FONT_SIZE),
                fill,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
                font=font,
            )
            x += text_size(draw, font, ch, vertical_dot_run=vertical_dot_run)[0] + tracking
            continue
        if vertical_dot_run and is_vertical_rotated_glyph(ch):
            draw_rotated_vertical_glyph(
                draw,
                (x, y),
                ch,
                font,
                fill,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
            )
            x += text_size(draw, font, ch, vertical_dot_run=vertical_dot_run)[0] + tracking
            continue
        draw.text(
            (x, y),
            ch,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill if stroke_fill is not None else fill,
        )
        x += draw.textbbox((0, 0), ch, font=font)[2] + tracking


_DECORATIVE_VERTICAL_LINE_CHARS = frozenset("|｜│┃║⏐∣❘❙❚︱丨")
_DECORATIVE_HORIZONTAL_LINE_CHARS = frozenset("-‐‑‒–—―─━_＿")


def split_trailing_horizontal_decorative_line(text: str) -> tuple[str, str] | None:
    """대사 뒤에 붙은 dash run을 분리한다. 텍스트 뒤 단일 '-'도 보이는 선으로 렌더한다."""
    chars = list(str(text or ""))
    idx = len(chars) - 1
    tail: list[str] = []
    while idx >= 0:
        ch = chars[idx]
        if ch.isspace():
            idx -= 1
            continue
        if ch in _DECORATIVE_HORIZONTAL_LINE_CHARS:
            tail.append(ch)
            idx -= 1
            continue
        break
    prefix = "".join(chars[: idx + 1]).rstrip()
    if len(tail) < 2 and not prefix:
        return None
    if len(tail) < 1:
        return None
    return prefix, "".join(reversed(tail))


def split_leading_horizontal_decorative_line(text: str) -> tuple[str, str] | None:
    """대사 앞에 붙은 dash run을 분리한다. 텍스트 앞 단일 '-'도 보이는 선으로 렌더한다."""
    chars = list(str(text or ""))
    idx = 0
    head: list[str] = []
    while idx < len(chars):
        ch = chars[idx]
        if ch.isspace():
            idx += 1
            continue
        if ch in _DECORATIVE_HORIZONTAL_LINE_CHARS:
            head.append(ch)
            idx += 1
            continue
        break
    suffix = "".join(chars[idx:]).lstrip()
    if len(head) < 2 and not suffix:
        return None
    if len(head) < 1:
        return None
    return "".join(head), suffix


def horizontal_rule_width(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, dash_run: str) -> int:
    """앞/뒤 dash run을 가로 선으로 그릴 때의 폭. 최소 0.9em을 보장한다."""
    box = font_text_bbox(draw, font, dash_run)
    glyph_w = max(0, int(round(box[2] - box[0] + tracking_for_font(font) * max(0, len(dash_run) - 1))))
    return max(int(round(getattr(font, "size", MIN_FIT_FONT_SIZE) * 0.9)), glyph_w)


def decorative_line_text_width(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    text: str,
    vertical_dot_run: bool = True,
) -> int | None:
    """draw_tracked_text의 앞/뒤 dash 선 분기와 같은 방식으로 그려지는 폭을 잰다. 해당 없으면 None."""
    tracking = tracking_for_font(font)
    leading_line = split_leading_horizontal_decorative_line(text)
    if leading_line:
        prefix, suffix = leading_line
        width = horizontal_rule_width(draw, font, prefix)
        if suffix:
            width += max(tracking, 0) + text_size(draw, font, suffix, vertical_dot_run=vertical_dot_run)[0]
        return width
    trailing_line = split_trailing_horizontal_decorative_line(text)
    if trailing_line:
        prefix, suffix = trailing_line
        width = 0
        if prefix:
            width += text_size(draw, font, prefix, vertical_dot_run=vertical_dot_run)[0] + max(tracking, 0)
        return width + horizontal_rule_width(draw, font, suffix)
    return None


def decorative_line_kind(lines: list[str]) -> str | None:
    """선 문자만 2개 이상인 텍스트를 장식선 렌더 대상으로 분류한다."""
    compact = "".join(re.sub(r"\s+", "", str(line or "")) for line in lines)
    if len(compact) < 2:
        return None
    chars = set(compact)
    if chars and chars <= _DECORATIVE_VERTICAL_LINE_CHARS:
        return "vertical"
    if chars and chars <= _DECORATIVE_HORIZONTAL_LINE_CHARS:
        return "horizontal"
    return None


def is_vertical_decorative_line_text(text: str) -> bool:
    """한 줄 전체가 세로 장식선 문자 1개 이상인지 판정한다."""
    compact = re.sub(r"\s+", "", str(text or ""))
    return bool(compact) and set(compact) <= _DECORATIVE_VERTICAL_LINE_CHARS


def draw_decorative_line(
    draw: ImageDraw.ImageDraw,
    layout: Layout,
    kind: str,
    fill: tuple[int, int, int],
) -> None:
    """폰트 glyph 대신 safe rect 안에 끊김 없는 선을 직접 그린다."""
    ix, iy, iw, ih = layout.inner_rect
    line_width = max(2, int(round(layout.font_size * 0.10)))
    inset = max(1, int(round(line_width * 0.8)))
    if kind == "vertical":
        x = int(round(ix + iw / 2))
        draw.line(
            (x, int(round(iy + inset)), x, int(round(iy + ih - inset))),
            fill=fill,
            width=line_width,
        )
        return
    if kind == "horizontal":
        y = int(round(iy + ih / 2))
        draw.line(
            (int(round(ix + inset)), y, int(round(ix + iw - inset)), y),
            fill=fill,
            width=line_width,
        )


def draw_decorative_vertical_line_span(
    draw: ImageDraw.ImageDraw,
    x: float,
    y1: float,
    y2: float,
    font_size: int,
    fill: tuple[int, int, int],
) -> None:
    """연속된 | 줄을 하나의 끊김 없는 세로선으로 그린다."""
    line_width = max(2, int(round(font_size * 0.10)))
    draw.line(
        (int(round(x)), int(round(y1)), int(round(x)), int(round(y2))),
        fill=fill,
        width=line_width,
    )


def _parse_round_score(quality_flags: list) -> float:
    """quality_flags 문자열 리스트에서 round 점수를 파싱해 반환. 없으면 -1.0."""
    for flag in quality_flags or []:
        m = re.search(r"(?<![a-z_])round=([0-9.]+)", str(flag))
        if m:
            return float(m.group(1))
    return -1.0


def inner_rect_for(
    bbox: list[int],
    image_size: tuple[int, int],
    is_unboxed: bool = False,
    is_split_rect: bool = False,
    split_order: int = -1,
    mask_centroid: list[int] | None = None,
    round_score: float = -1.0,
) -> tuple[int, int, int, int]:
    """내부 텍스트 배치 영역을 계산한다.

    split_order >= 0 인 split region 에는 수평 bias 를 적용한다:
      split_order == 0: 우측 조각 → ix 를 +8% 우측 시프트
      split_order >= 1: 좌측 조각 → ix 를 -8% 좌측 시프트
    이렇게 하면 인접한 두 split region 의 텍스트 중심이 서로 멀어져
    물리적 오버랩 없이 읽기 자연스럽게 배치된다.

    mask_centroid 가 있으면 bbox 기하 중심 대비 편차의 50% 만큼 ix, iy 를 보정한다.
    삐죽 튀어나온 말풍선 꼬리 등으로 인해 bbox 중심이 실제 글자 공간에서 벗어날 때 유효.
    split_order bias 이후에 적용되므로 두 보정이 독립적으로 누적된다.

    round_score < 0.45: 직사각형 버블(나레이션박스·세로줄 효과음 등) → 패딩 축소.
    """
    img_w, img_h = image_size
    x, y, w, h = [int(v) for v in bbox]
    x = max(0, min(x, img_w - 1))
    y = max(0, min(y, img_h - 1))
    w = max(1, min(w, img_w - x))
    h = max(1, min(h, img_h - y))

    # round_score < 0.45: 직사각형/비원형 버블 → 패딩 축소 (split_rect 모드와 동일)
    is_rectangular = (0.0 <= round_score < 0.45)

    if is_unboxed:
        # unboxed fallback: bbox 그대로 사용 (패딩 없음)
        pad_x = 0
        pad_y = 0
    elif is_split_rect or is_rectangular:
        # split region 또는 직사각형 버블: 패딩 축소
        aspect = h / max(w, 1)
        if aspect >= 1.8:
            pad_x = clamp(w * 0.09, 8, 22)
            pad_y = clamp(h * 0.05, 8, 28)
        elif aspect <= 0.65:
            pad_x = clamp(w * 0.05, 6, 22)
            pad_y = clamp(h * 0.09, 6, 18)
        else:
            pad_x = clamp(w * 0.07, 6, 18)
            pad_y = clamp(h * 0.07, 6, 20)
    else:
        aspect = h / max(w, 1)
        if aspect >= 1.8:
            pad_x = clamp(w * 0.17, 16, 42)
            pad_y = clamp(h * 0.10, 18, 58)
        elif aspect <= 0.65:
            pad_x = clamp(w * 0.10, 14, 44)
            pad_y = clamp(h * 0.17, 12, 36)
        else:
            pad_x = clamp(w * 0.13, 14, 38)
            pad_y = clamp(h * 0.13, 12, 44)

    if w - pad_x * 2 < 32:
        pad_x = max(4, (w - 32) / 2)
    if h - pad_y * 2 < 28:
        pad_y = max(4, (h - 28) / 2)

    ix = int(round(x + pad_x))
    iy = int(round(y + pad_y))
    iw = int(round(w - pad_x * 2))
    ih = int(round(h - pad_y * 2))

    # split_order 기반 수평 bias (split region 전용)
    # split_order == 0: 우측 조각이므로 우측으로 치우침
    # split_order >= 1: 좌측 조각이므로 좌측으로 치우침
    if is_split_rect and split_order >= 0:
        bias_x = int(w * 0.08)
        if split_order == 0:
            # 우측 치우침: ix 증가, 단 bubble 우측 경계(x+w)를 벗어나지 않도록
            ix = min(ix + bias_x, x + w - iw)
        else:
            # 좌측 치우침: ix 감소, 단 bubble 좌측 경계(x)를 벗어나지 않도록
            ix = max(ix - bias_x, x)

    # mask_centroid 기반 수평·수직 보정 (unboxed 및 split 제외)
    # bbox 기하 중심과 실제 마스크 픽셀 무게중심의 차이 50% 만큼 ix, iy 를 이동
    # → 꼬리가 튀어나온 말풍선에서 텍스트 중심이 실제 버블 공간으로 당겨짐
    if not is_unboxed and not is_split_rect and mask_centroid is not None:
        bbox_cx = x + w / 2
        bbox_cy = y + h / 2
        cx_offset = int((mask_centroid[0] - bbox_cx) * 0.5)
        cy_offset = int((mask_centroid[1] - bbox_cy) * 0.5)
        ix = int(clamp(ix + cx_offset, x, x + w - iw))
        iy = int(clamp(iy + cy_offset, y, y + h - ih))

    return ix, iy, max(1, iw), max(1, ih)


def estimate_target_lines(text: str, inner_w: int, inner_h: int) -> int:
    weight = weighted_len(text)
    aspect = inner_h / max(inner_w, 1)
    raw = math.sqrt(weight * aspect) * 0.92
    target = int(round(raw))

    if aspect <= 1.15 and weight <= 7:
        target = 1
    elif aspect > 2.1 and weight >= 7:
        target = max(target, 4)
    elif aspect > 1.6 and weight >= 5:
        target = max(target, 3)

    if aspect < 1.5 and weight >= 18:
        target = min(target, 5)
    elif aspect < 1.5 and weight < 14:
        target = min(target, 3)

    if weight <= 4:
        if aspect >= 2.0:
            target = max(target, 2)
        target = min(target, 2)
    elif aspect >= 2.0 and weight <= 14:
        target = min(target, 4)
    elif aspect >= 2.0 and weight <= 20:
        target = min(target, 5)

    max_lines = max(1, int(inner_h / 24))

    # 매우 좁지만 세로로 길지 않은 버블 + 짧은 텍스트: 줄 수 과추정 방지
    if inner_w < 70 and aspect < 2.0 and weight <= 12:
        target = min(target, 2)

    return max(1, min(target, max_lines, 8))


BAD_STANDALONE_LINES = {
    "이",
    "안",
    "되는",
    "거야",
    "그가",
    "수는",
    "할 수",
    "한",
    "건",
    "것",
    "수",
    "년간",
}


BAD_PREFIX_FRAGMENTS = {
    "스토",
}

BAD_SUFFIX_FRAGMENTS = {
    "크스는",
    "크스가",
}


BAD_TRAILING_SINGLE_WORDS = {
    "이",
    "그",
    "저",
    "몇",
    "한",
}


def line_has_unsplit_particle_compound(line: str) -> bool:
    for word in re.findall(r"[가-힣]+", line):
        if should_preserve_korean_token(word):
            continue
        if split_korean_particle_compound(word) is not None:
            return True
    return False


def lines_have_unsplit_particle_compound(lines: list[str]) -> bool:
    return any(line_has_unsplit_particle_compound(line.strip()) for line in lines)


def line_penalty(line: str, ideal_weight: float, line_index: int, line_count: int) -> float:
    weight = weighted_len(line)
    penalty = (weight - ideal_weight) ** 2
    if line_count >= 3 and line_index in (0, line_count - 1):
        penalty += max(0.0, weight - ideal_weight * 1.05) ** 2 * 1.4
    stripped = line.strip()
    if weight < 1.2 and not re.fullmatch(r"(\.{2,}|…+)[?!?]*", stripped):
        penalty += 6.0
    if line_count >= 3 and re.fullmatch(r"[가-힣]", stripped):
        penalty += 90.0
    if stripped in BAD_PREFIX_FRAGMENTS or stripped in BAD_SUFFIX_FRAGMENTS:
        penalty += 260.0
    if (
        re.search(r"\s+(" + "|".join(BAD_TRAILING_SINGLE_WORDS) + r")$", stripped)
        and not re.match(r"^[이가은는을를]\s+", stripped)
    ):
        penalty += 180.0
    if stripped.startswith("잘 "):
        penalty += 140.0
    if line_has_unsplit_particle_compound(stripped):
        penalty += 420.0
    if re.match(r"^(바|수|것|건)\s+", stripped):
        penalty += 180.0
    if re.search(r"\s+[이가은는을를]$", stripped):
        penalty += 180.0
    if stripped in BAD_STANDALONE_LINES:
        penalty += 240.0
    if stripped.startswith("수는 "):
        penalty += 220.0
    if stripped.startswith("수 "):
        penalty += 180.0
    if stripped.endswith(" 나는"):
        penalty += 160.0
    # 교차절 조사 패턴: 부사격조사(으로/로/에서/에게 등)로 끝나는 성분 + 격조사(을/를/이/가/은/는)로 끝나는 성분이
    # 한 줄에 고립 → 서로 다른 절 성분이 한 줄에 묶여 읽기 어색 (예: "피로로 방을", "집에서 손을")
    if re.search(r"[가-힣]+(?:으로서?|로서?|에서|에게서?|에게|에)\s+[가-힣]+(?:을|를|이|가|은|는)$", stripped):
        penalty += 200.0
    # 행 끝 관형격 '의': 수식 대상이 다음 줄로 넘어가는 경우 (예: "열등종의", "목적의")
    if re.search(r"[가-힣]{2,}의$", stripped):
        penalty += 140.0
    # 행 시작 비교·범위 조사: 앞 절 없이 홀로 시작하면 어색 (예: "보다", "만큼", "처럼", "부터", "까지")
    if re.match(r"^(?:보다|만큼|처럼|부터|까지)(?:\s|$)", stripped):
        penalty += 180.0
    if line_count >= 4 and line_index not in (0, line_count - 1):
        penalty += max(0.0, weight - ideal_weight * 1.45) ** 2 * 0.25
    return penalty


def layout_line_quality_score(lines: list[str], target_lines: int, aspect: float) -> float:
    """줄 후보의 의미 단위와 시각 균형을 비교하기 위한 공통 점수."""
    if not lines:
        return float("inf")
    total_weight = sum(weighted_len(line) for line in lines)
    ideal = total_weight / max(1, len(lines))
    score = sum(line_penalty(line, ideal, idx, len(lines)) for idx, line in enumerate(lines))
    score += abs(len(lines) - target_lines) * 30.0

    weights = [weighted_len(line) for line in lines]
    if len(weights) >= 3:
        avg = sum(weights) / len(weights)
        score += sum((weight - avg) ** 2 for weight in weights) * 0.18
        if aspect >= 1.6:
            first, last = weights[0], weights[-1]
            score += max(0.0, first - avg * 1.25) ** 2 * 0.45
            score += max(0.0, last - avg * 1.25) ** 2 * 0.45

    return score


def should_prefer_layout_by_quality(candidate: Layout, current: Layout, target_lines: int, aspect: float) -> bool:
    """폰트 크기 손실이 작다면 의미 단위가 더 좋은 후보로 교체한다."""
    if has_bad_line_break(candidate.lines):
        return False
    if candidate.font_size < current.font_size - 2:
        return False

    current_score = layout_line_quality_score(current.lines, target_lines, aspect)
    candidate_score = layout_line_quality_score(candidate.lines, target_lines, aspect)
    current_has_unsplit = lines_have_unsplit_particle_compound(current.lines)
    candidate_has_unsplit = lines_have_unsplit_particle_compound(candidate.lines)

    if current_has_unsplit and not candidate_has_unsplit:
        return candidate_score <= current_score + 90.0
    return candidate_score + 25.0 < current_score


def has_awkward_short_line(lines: list[str]) -> bool:
    if len(lines) < 3:
        return False
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"[가-힣]", stripped):
            return True
        if stripped in BAD_PREFIX_FRAGMENTS or stripped in BAD_SUFFIX_FRAGMENTS:
            return True
    return False


def has_bad_line_break(lines: list[str]) -> bool:
    if has_awkward_short_line(lines):
        return True
    for line in lines:
        stripped = line.strip()
        if stripped in BAD_STANDALONE_LINES:
            return True
        # 단어 하나만 있는 줄은 줄 안에서 더 나눌 방법이 없으므로 검사 제외
        if len(lines) >= 4 and " " in stripped and line_has_unsplit_particle_compound(stripped):
            return True
        if re.search(r"\s+[이가은는을를]$", stripped):
            return True
        if re.match(r"^(바|수|것|건)\s+", stripped):
            return True
        if stripped.startswith("수는 "):
            return True
        if stripped.endswith(" 나는"):
            return True
        # 교차절 조사 패턴: 부사격조사 + 격조사가 한 줄에 고립 (예: "피로로 방을")
        if re.search(r"[가-힣]+(?:으로서?|로서?|에서|에게서?|에게|에)\s+[가-힣]+(?:을|를|이|가|은|는)$", stripped):
            return True
        # 행 시작 비교·범위 조사
        if re.match(r"^(?:보다|만큼|처럼|부터|까지)(?:\s|$)", stripped):
            return True
    return False


def allowed_line_width(inner_w: int, inner_h: int, is_unboxed: bool) -> int:
    """좁고 긴 텍스트 영역에서는 줄바꿈 판단 폭을 소폭 완화한다."""
    aspect = inner_h / max(inner_w, 1)
    if aspect < 2.0 or inner_w > 110:
        return inner_w
    if is_unboxed:
        overflow = int(round(clamp(inner_w * 0.18, 10, 24)))
    else:
        overflow = int(round(clamp(inner_w * 0.10, 6, 14)))
    return inner_w + overflow


def has_ellipsis(text: str) -> bool:
    return bool(re.search(r"\.{2,}|…+", text or ""))


def vertical_line_offsets(lines: list[str], line_height: int, font_size: int) -> list[float]:
    """세로쓰기 줄별 y offset. 연속 말줄임표 점은 정사각형에 가깝게 압축한다.

    점은 직접 원형 렌더이므로 일반 글자 아래에 붙을 때만 충분한 여백을 둔다.
    """
    offsets: list[float] = []
    y = 0.0
    dot_lead_step = max(line_height, font_size * 1.08)  # 일반 char → 첫 번째 점
    dot_step = max(4.0, font_size * 0.34)            # 점 → 다음 점 (연속)
    dot_to_char_step = max(4.0, font_size * 0.22)   # 마지막 점 → 일반 글자 (glyph top 여백 보정)
    dot_to_punct_step = max(4.0, font_size * 0.62)  # 마지막 점 → !? 구두점
    dot_run_to_char_step = max(0.0, font_size * 0.06)
    compact_char_step = max(4.0, font_size * 0.94)

    def _is_compact_vertical_char(line: str) -> bool:
        text = line.strip()
        return len(text) == 1 and bool(re.match(r"[\w가-힣]", text))

    for idx, line in enumerate(lines):
        if idx == 0:
            offsets.append(0.0)
            continue
        prev = lines[idx - 1].strip()
        cur = line.strip()
        if is_synthetic_vertical_dot(cur):
            y += dot_step if is_synthetic_vertical_dot(prev) else dot_lead_step
        elif is_synthetic_vertical_dot(prev) and re.search(r"[!?！？]", cur):
            y += dot_to_punct_step
        elif is_synthetic_vertical_dot(prev):
            y += dot_to_char_step
        elif is_vertical_decorative_line_text(prev):
            decorative_units = max(1, len(re.sub(r"\s+", "", prev)))
            y += line_height * decorative_units
        elif is_dot_ellipsis_only_text(prev) and not is_dot_ellipsis_only_text(cur):
            _, dot_run_height = synthetic_vertical_dot_run_size(font_size, dot_ellipsis_unit_count(prev))
            y += dot_run_height + dot_run_to_char_step
        elif cur == VERTICAL_ELLIPSIS:
            y += line_height + max(2.0, font_size * 0.12)
        elif _is_compact_vertical_char(prev) and _is_compact_vertical_char(cur):
            y += compact_char_step
        else:
            y += line_height
        offsets.append(y)
    return offsets


def text_weight_without_ellipsis(text: str) -> float:
    stripped = re.sub(r"\.{2,}|…+", "", text or "")
    return weighted_len(re.sub(r"\s+", " ", stripped.strip()))


def wrap_tokens(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    tokens: list[str],
    inner_w: int,
    target_lines: int,
    min_line_count_override: int | None = None,
    max_line_w: int | None = None,
) -> list[str] | None:
    n = len(tokens)
    if n == 0:
        return []

    line_cache: dict[tuple[int, int], tuple[str, int]] = {}
    line_limit = max_line_w if max_line_w is not None else inner_w
    for i in range(n):
        for j in range(i + 1, n + 1):
            line = join_tokens(tokens[i:j])
            # 가로 줄바꿈 판단은 실제 가로 렌더 폭으로 측정한다(세로 점 run 측정 금지).
            width, _ = text_size(draw, font, line, vertical_dot_run=False)
            if width > line_limit:
                # 같은 시작점에서 토큰을 더 붙인 줄은 더 넓어지므로 더 볼 필요가 없다.
                break
            line_cache[(i, j)] = (line, width)

    if not line_cache:
        return None

    max_lines = min(n, max(1, target_lines))
    best: tuple[float, list[str]] | None = None
    total_weight = sum(token_weight(t) for t in tokens)

    if min_line_count_override is None:
        min_line_count = min(max_lines, max(1, target_lines - 1))
    else:
        min_line_count = min(max_lines, max(1, min_line_count_override))
    for line_count in range(1, max_lines + 1):
        if line_count < min_line_count:
            continue
        ideal = total_weight / line_count
        dp: list[dict[int, tuple[float, list[str]]]] = [dict() for _ in range(line_count + 1)]
        dp[0][0] = (0.0, [])
        for used_lines in range(line_count):
            for start, (cost, lines) in list(dp[used_lines].items()):
                min_left = line_count - used_lines - 1
                for end in range(start + 1, n - min_left + 1):
                    cached = line_cache.get((start, end))
                    if cached is None:
                        continue
                    line = cached[0]
                    add = line_penalty(line, ideal, used_lines, line_count)
                    next_cost = cost + add
                    old = dp[used_lines + 1].get(end)
                    if old is None or next_cost < old[0]:
                        dp[used_lines + 1][end] = (next_cost, lines + [line])

        candidate = dp[line_count].get(n)
        if candidate is None:
            continue
        line_count_cost = abs(line_count - target_lines) * 30.0
        cost = candidate[0] + line_count_cost
        if best is None or cost < best[0]:
            best = (cost, candidate[1])

    return best[1] if best else None


def _try_vertical_layout(
    draw: ImageDraw.ImageDraw,
    text: str,
    bbox: list[int],
    image_size: tuple[int, int],
    font_path: Path,
    is_unboxed: bool = False,
    is_split_rect: bool = False,
    max_font_size: int | None = None,
    split_order: int = -1,
    mask_centroid: list[int] | None = None,
    round_score: float = -1.0,
) -> Layout | None:
    """세로쓰기 레이아웃 — 글자 하나씩 한 줄로 쌓아 올림.

    - 폰트 크기는 inner_w 기준으로 결정 (bbox height 채우기 시도 안 함)
    - 말줄임표(…/...)는 앞 글자에 붙이지 않고 별도 항목으로 분리
    - max_font_size: 페이지 밴드 상한 (+leeway 적용 후 전달) — loop 시작점을 제한해
      font_size와 line_height를 항상 일관되게 유지 (cap 후 line_height 불일치 방지)
    """
    inner = inner_rect_for(bbox, image_size, is_unboxed=is_unboxed, is_split_rect=is_split_rect, split_order=split_order, mask_centroid=mask_centroid, round_score=round_score)
    _, _, inner_w, inner_h = inner

    if is_dot_ellipsis_only_text(text):
        lines = dot_ellipsis_vertical_lines(text)
        max_font = max_font_size if max_font_size is not None else 72
        font_start = max(MIN_FIT_FONT_SIZE, min(int(inner_w * 0.90), max_font))
        for font_size in range(font_start, MIN_FIT_FONT_SIZE - 1, -1):
            font = cached_truetype_font(str(font_path), int(font_size))
            widths = [text_size(draw, font, line)[0] for line in lines]
            heights = [text_size(draw, font, line)[1] for line in lines]
            line_height = max(int(round(font_size * 1.14)), max(heights, default=font_size))
            offsets = vertical_line_offsets(lines, line_height, font_size)
            measured_height = (offsets[-1] if offsets else 0.0) + max(heights, default=font_size)
            measured_width = max(widths, default=0)
            if measured_width <= allowed_line_width(inner_w, inner_h, is_unboxed) and measured_height <= inner_h:
                return Layout(
                    font_size=font_size,
                    lines=lines,
                    inner_rect=inner,
                    line_height=line_height,
                    measured_width=measured_width,
                    measured_height=int(round(measured_height)),
                    target_lines=len(lines),
                )
        return None

    # 세로쓰기 말줄임표는 글자 간격 제어를 위해 period 3개 단위로 펼친다.
    text = expand_vertical_ellipsis_to_periods(text)
    # U+2015 HORIZONTAL BAR → U+2014 EM DASH (폰트에서 h=0으로 보이지 않음)
    text = text.replace("―", "—")
    text = re.sub(r"[—-]{2,}", "｜", text)

    # 공백 제거 후 글자 단위 분리
    # 세로쓰기에서는 말줄임표와 종결부호도 각각 독립 줄로 배치한다.
    # !? / ！？ 조합은 한 줄로 묶어 처리한다 (원본 느낌 유지).
    raw_chars = [ch for ch in text if not ch.isspace()]
    chars: list[str] = []
    i = 0
    while i < len(raw_chars):
        ch = raw_chars[i]
        if ch in {"!", "！"} and i + 1 < len(raw_chars) and raw_chars[i + 1] in {"?", "？"}:
            chars.append(ch + raw_chars[i + 1])
            i += 2
        else:
            chars.append(ch)
            i += 1
    if not chars:
        return None

    # 폰트 시작값: inner_w 의 90% 이하, max_font_size 상한 모두 적용
    _width_cap = int(inner_w * 0.90)
    _max_cap = max_font_size if max_font_size is not None else 72
    font_start = max(MIN_FIT_FONT_SIZE, min(_width_cap, _max_cap))
    for font_size in range(font_start, MIN_FIT_FONT_SIZE - 1, -1):
        font = cached_truetype_font(str(font_path), int(font_size))
        char_widths = [text_size(draw, font, c)[0] for c in chars]
        char_heights = [text_size(draw, font, c)[1] for c in chars]
        # 세로쓰기 글자 간격은 일반 폰트 ascender/descender 여유를 포함해 안정적으로 둔다.
        line_height = max(int(round(font_size * 1.05)), max(char_heights))
        offsets = vertical_line_offsets(chars, line_height, font_size)
        total_h = int(round((offsets[-1] if offsets else 0) + max(char_heights, default=font_size)))
        max_w = max(char_widths)
        if max_w <= inner_w and total_h <= inner_h:
            return Layout(
                font_size=font_size,
                lines=chars,
                inner_rect=inner,
                line_height=line_height,
                measured_width=max_w,
                measured_height=total_h,
                target_lines=len(chars),
            )
    return None


def _try_fixed_lines_layout(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    bbox: list[int],
    image_size: tuple[int, int],
    font_path: Path,
    min_font_size: int = 20,
    max_font_size: int | None = None,
    is_unboxed: bool = False,
    is_split_rect: bool = False,
    split_order: int = -1,
    mask_centroid: list[int] | None = None,
    round_score: float = -1.0,
    allow_overflow: bool = False,
) -> Layout | None:
    """이미 정한 짧은 줄 묶음을 다시 합치지 않고 fit만 평가한다."""
    if not lines:
        return None
    inner = inner_rect_for(bbox, image_size, is_unboxed=is_unboxed, is_split_rect=is_split_rect, split_order=split_order, mask_centroid=mask_centroid, round_score=round_score)
    _, _, inner_w, inner_h = inner
    line_w_limit = allowed_line_width(inner_w, inner_h, is_unboxed)
    font_start = max(MIN_FIT_FONT_SIZE, max_font_size if max_font_size is not None else 56)
    font_floor = min(min_font_size, font_start)
    smallest: Layout | None = None
    for font_size in range(font_start, font_floor - 1, -1):
        font = cached_truetype_font(str(font_path), int(font_size))
        line_widths = [text_size(draw, font, line, vertical_dot_run=False)[0] for line in lines]
        line_heights = [text_size(draw, font, line, vertical_dot_run=False)[1] for line in lines]
        line_height = max(int(round(font_size * 1.14)), max(line_heights, default=font_size))
        measured_height = line_height * max(0, len(lines) - 1) + max(line_heights, default=font_size)
        measured_width = max(line_widths, default=0)
        smallest = Layout(
            font_size=font_size,
            lines=lines,
            inner_rect=inner,
            line_height=line_height,
            measured_width=measured_width,
            measured_height=measured_height,
            target_lines=len(lines),
        )
        if measured_width <= line_w_limit and measured_height <= inner_h:
            return smallest
    return smallest if allow_overflow else None


def _manual_explicit_lines_layout(
    draw: ImageDraw.ImageDraw,
    text: str,
    bbox: list[int],
    image_size: tuple[int, int],
    font_path: Path,
    max_font_size: int | None = None,
    is_unboxed: bool = False,
    is_split_rect: bool = False,
    split_order: int = -1,
    mask_centroid: list[int] | None = None,
    round_score: float = -1.0,
) -> Layout | None:
    explicit_lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(explicit_lines) <= 1:
        return None
    return _try_fixed_lines_layout(
        draw,
        explicit_lines,
        bbox,
        image_size,
        font_path,
        min_font_size=10,
        max_font_size=max_font_size,
        is_unboxed=is_unboxed,
        is_split_rect=is_split_rect,
        split_order=split_order,
        mask_centroid=mask_centroid,
        round_score=round_score,
        allow_overflow=True,
    )


def _try_overlong_vertical_ellipsis_chunk_layout(
    draw: ImageDraw.ImageDraw,
    text: str,
    bbox: list[int],
    image_size: tuple[int, int],
    font_path: Path,
    vertical_layout: Layout,
    min_font_size: int = 20,
    max_font_size: int | None = None,
    is_unboxed: bool = False,
    is_split_rect: bool = False,
    split_order: int = -1,
    mask_centroid: list[int] | None = None,
    round_score: float = -1.0,
) -> Layout | None:
    """세로 1열이 말줄임표 때문에 꽉 찰 때 짧은 구절 2줄 후보를 시도한다."""
    _, _, _inner_w, inner_h = vertical_layout.inner_rect
    if len(vertical_layout.lines) < 9:
        return None
    if vertical_layout.measured_height / max(1, inner_h) < 0.82:
        return None

    chunk_lines = split_overlong_vertical_ellipsis_text(text)
    if len(chunk_lines) < 2:
        return None

    candidate = _try_fixed_lines_layout(
        draw,
        chunk_lines,
        bbox,
        image_size,
        font_path,
        min_font_size=min(MIN_FIT_FONT_SIZE, min_font_size),
        max_font_size=max_font_size,
        is_unboxed=is_unboxed,
        is_split_rect=is_split_rect,
        split_order=split_order,
        mask_centroid=mask_centroid,
        round_score=round_score,
    )
    if candidate is None:
        return None
    if candidate.font_size < vertical_layout.font_size:
        return None
    if candidate.measured_height > vertical_layout.measured_height * 0.72:
        return None
    return candidate


def _choose_layout_raw(
    draw: ImageDraw.ImageDraw,
    text: str,
    bbox: list[int],
    image_size: tuple[int, int],
    font_path: Path,
    min_font_size: int = 20,
    max_font_size: int | None = None,
    is_unboxed: bool = False,
    is_split_rect: bool = False,
    split_order: int = -1,
    mask_centroid: list[int] | None = None,
    round_score: float = -1.0,
    source_min_lines: int = 0,
    preserve_explicit_lines: bool = False,
    prefer_explicit_lines_when_reasonable: bool = False,
    allow_overestimated_font_size: bool = False,
) -> Layout:
    """세로쓰기 보정 없이 순수 가로쓰기 레이아웃만 결정."""
    # 정규화 전에 명시적 줄바꿈 수 저장 (Gemini가 넣은 \n을 target_lines 하한으로 활용)
    raw_text = text
    explicit_min_lines = text.strip().count("\n") + 1
    if preserve_explicit_lines and explicit_min_lines > 1:
        fixed = _manual_explicit_lines_layout(
            draw,
            text,
            bbox,
            image_size,
            font_path,
            max_font_size=max_font_size,
            is_unboxed=is_unboxed,
            is_split_rect=is_split_rect,
            split_order=split_order,
            mask_centroid=mask_centroid,
            round_score=round_score,
        )
        if fixed is not None:
            return fixed
    text = re.sub(r"\s+", " ", text.strip())
    # U+2015 HORIZONTAL BAR → U+2014 EM DASH
    # Freesentation 폰트에서 U+2015는 h=0 (0px 높이)이라 사실상 보이지 않음
    text = text.replace("―", "—")
    inner = inner_rect_for(bbox, image_size, is_unboxed=is_unboxed, is_split_rect=is_split_rect, split_order=split_order, mask_centroid=mask_centroid, round_score=round_score)
    _, _, inner_w, inner_h = inner
    line_w_limit = allowed_line_width(inner_w, inner_h, is_unboxed)
    source_min_lines = source_min_lines if 2 <= source_min_lines <= 3 else 0
    target_lines = max(estimate_target_lines(text, inner_w, inner_h), explicit_min_lines, source_min_lines)
    tokens = tokenize(text)

    estimated_max = int(clamp(math.sqrt(inner_w * inner_h / weighted_len(text)) * 0.68 + 16, 24, 56))
    if max_font_size is not None and allow_overestimated_font_size:
        max_size = max_font_size
    else:
        max_size = min(estimated_max, max_font_size) if max_font_size is not None else estimated_max
    min_size = min(min_font_size, max_size)

    def find_layout(
        candidate_tokens: list[str],
        search_min_size: int = min_size,
        max_size_override: int | None = None,
        min_line_count_override: int | None = None,
    ) -> tuple[Layout | None, Layout | None]:
        _max = max_size_override if max_size_override is not None else max_size
        best_overflow: Layout | None = None
        for font_size in range(_max, search_min_size - 1, -1):
            font = cached_truetype_font(str(font_path), int(font_size))
            lines = wrap_tokens(
                draw,
                font,
                candidate_tokens,
                inner_w,
                target_lines,
                min_line_count_override,
                max_line_w=line_w_limit,
            )
            if lines is None:
                continue

            line_widths = [text_size(draw, font, line, vertical_dot_run=False)[0] for line in lines]
            line_heights = [text_size(draw, font, line, vertical_dot_run=False)[1] for line in lines]
            line_height = max(int(round(font_size * 1.14)), max(line_heights, default=font_size))
            measured_height = line_height * max(0, len(lines) - 1) + max(line_heights, default=font_size)
            measured_width = max(line_widths, default=0)

            layout = Layout(
                font_size=font_size,
                lines=lines,
                inner_rect=inner,
                line_height=line_height,
                measured_width=measured_width,
                measured_height=measured_height,
                target_lines=target_lines,
            )
            if measured_width <= line_w_limit and measured_height <= inner_h:
                if has_bad_line_break(lines):
                    continue
                return layout, best_overflow
            if best_overflow is None or measured_height < best_overflow.measured_height:
                best_overflow = layout
        return None, best_overflow

    layout: Layout | None = None
    best_overflow: Layout | None = None
    if source_min_lines:
        # 일반 토큰 우선 — explode_long_tokens는 한국어 단어를 글자 단위로 분해해
        # "그렇다면" → "그렇 / 다면대략" 같은 괴상한 줄바꿈을 유발하므로 fallback으로만 사용
        layout, best_overflow = find_layout(tokens, min_line_count_override=source_min_lines)
        if layout is None:
            layout, best_overflow = find_layout(
                explode_long_tokens(tokens),
                min_line_count_override=source_min_lines,
            )
    if layout is None:
        layout, best_overflow = find_layout(tokens, min_line_count_override=source_min_lines or None)
    if layout is not None:
        aspect = inner_h / max(inner_w, 1)
        if prefer_explicit_lines_when_reasonable and not preserve_explicit_lines and explicit_min_lines > 1:
            explicit_layout = _manual_explicit_lines_layout(
                draw,
                raw_text,
                bbox,
                image_size,
                font_path,
                max_font_size=max_font_size,
                is_unboxed=is_unboxed,
                is_split_rect=is_split_rect,
                split_order=split_order,
                mask_centroid=mask_centroid,
                round_score=round_score,
            )
            if (
                explicit_layout is not None
                and explicit_layout.measured_height <= inner_h
                and explicit_layout.font_size >= max(MIN_FIT_FONT_SIZE, int(layout.font_size * 0.45))
            ):
                return explicit_layout
        exploded_tokens = explode_long_tokens(tokens)
        ellipsis_tokens = split_trailing_ellipsis_tokens(tokens)
        exploded_ellipsis_tokens = explode_long_tokens(ellipsis_tokens)
        if aspect >= 1.4 and has_ellipsis(text) and len(ellipsis_tokens) > len(tokens):
            ellipsis_layout, _ = find_layout(
                exploded_ellipsis_tokens,
                min_line_count_override=max(source_min_lines + 1, min(target_lines, len(exploded_ellipsis_tokens))),
            )
            if (
                ellipsis_layout is not None
                and len(ellipsis_layout.lines) > len(layout.lines)
                and re.fullmatch(r"\.{2,}|…+", ellipsis_layout.lines[-1].strip())
                and ellipsis_layout.font_size >= layout.font_size - 3
            ):
                return ellipsis_layout
        if len(layout.lines) < target_lines and len(exploded_tokens) > len(tokens):
            strict_exploded_layout, _ = find_layout(
                exploded_tokens,
                min_line_count_override=target_lines,
            )
            if (
                strict_exploded_layout is not None
                and len(strict_exploded_layout.lines) > len(layout.lines)
                and not has_awkward_short_line(strict_exploded_layout.lines)
                and strict_exploded_layout.font_size >= layout.font_size - 2
            ):
                return strict_exploded_layout
        if has_awkward_short_line(layout.lines) and len(exploded_tokens) > len(tokens):
            strict_exploded_layout, _ = find_layout(
                exploded_tokens,
                min_line_count_override=target_lines,
            )
            if (
                strict_exploded_layout is not None
                and not has_awkward_short_line(strict_exploded_layout.lines)
                and strict_exploded_layout.font_size >= layout.font_size - 2
            ):
                return strict_exploded_layout
            exploded_layout, _ = find_layout(exploded_tokens)
            if (
                exploded_layout is not None
                and not has_awkward_short_line(exploded_layout.lines)
                and exploded_layout.font_size >= layout.font_size - 2
            ):
                return exploded_layout
        if len(exploded_tokens) > len(tokens):
            strict_exploded_layout, _ = find_layout(
                exploded_tokens,
                min_line_count_override=target_lines,
            )
            if (
                strict_exploded_layout is not None
                and should_prefer_layout_by_quality(strict_exploded_layout, layout, target_lines, aspect)
            ):
                return strict_exploded_layout
            exploded_layout, _ = find_layout(exploded_tokens)
            if (
                exploded_layout is not None
                and should_prefer_layout_by_quality(exploded_layout, layout, target_lines, aspect)
            ):
                return exploded_layout
            if lines_have_unsplit_particle_compound(layout.lines) and layout.font_size > MIN_FIT_FONT_SIZE:
                smaller_exploded_layout, _ = find_layout(
                    exploded_tokens,
                    search_min_size=max(MIN_FIT_FONT_SIZE, layout.font_size - 2),
                    max_size_override=layout.font_size - 1,
                    min_line_count_override=target_lines,
                )
                if (
                    smaller_exploded_layout is not None
                    and should_prefer_layout_by_quality(smaller_exploded_layout, layout, target_lines, aspect)
                ):
                    return smaller_exploded_layout
        if aspect >= 1.25 and 4.5 <= weighted_len(text) <= 8 and len(exploded_tokens) > len(tokens):
            exploded_layout, _ = find_layout(exploded_tokens)
            if exploded_layout is not None and len(exploded_layout.lines) > len(layout.lines):
                return exploded_layout
        if aspect >= 2.0 and layout.font_size <= 22:
            exploded_layout, _ = find_layout(exploded_tokens)
            if (
                exploded_layout is not None
                and exploded_layout.font_size >= layout.font_size + 4
                and len(exploded_layout.lines) > len(layout.lines)
            ):
                return exploded_layout

        # ── 66% 줄 채움률 강제 보정 ──────────────────────────────────────────
        # actual_lines / target_lines < 0.66 이고 target_lines >= 3인 경우:
        # exploded 토큰 + 폰트 상한 70%로 재시도해 버블을 더 꽉 채움.
        if target_lines >= 3 and len(layout.lines) / target_lines < 0.66:
            reduced_max = max(MIN_FIT_FONT_SIZE, int(max_size * 0.70))
            aggressive_tokens = explode_long_tokens(tokens)
            min_aggressive_lines = None
            if aspect >= 2.0 and is_unboxed:
                min_aggressive_lines = min(target_lines, len(aggressive_tokens))
            aggressive_layout, _ = find_layout(
                aggressive_tokens,
                max_size_override=reduced_max,
                min_line_count_override=min_aggressive_lines,
            )
            if (
                aggressive_layout is not None
                and len(aggressive_layout.lines) > len(layout.lines)
                and len(aggressive_layout.lines) / target_lines >= 0.50  # 최소 50%는 만족
            ):
                return aggressive_layout

        if aspect >= 2.0 and target_lines >= 4 and len(layout.lines) < target_lines:
            strict_layout, _ = find_layout(
                explode_long_tokens(tokens),
                min_line_count_override=target_lines,
            )
            if strict_layout is not None and len(strict_layout.lines) > len(layout.lines):
                return strict_layout

        return layout

    if min_size > MIN_FIT_FONT_SIZE:
        natural_small_layout, natural_small_overflow = find_layout(tokens, MIN_FIT_FONT_SIZE)
        if natural_small_layout is not None:
            aspect = inner_h / max(inner_w, 1)
            if aspect >= 2.0 and natural_small_layout.font_size <= 22:
                exploded_small_layout, _ = find_layout(explode_long_tokens(tokens), MIN_FIT_FONT_SIZE)
                if (
                    exploded_small_layout is not None
                    and exploded_small_layout.font_size >= natural_small_layout.font_size + 4
                    and len(exploded_small_layout.lines) > len(natural_small_layout.lines)
                ):
                    return exploded_small_layout
            return natural_small_layout
        if best_overflow is None:
            best_overflow = natural_small_overflow

    exploded_layout, exploded_overflow = find_layout(explode_long_tokens(tokens))
    if exploded_layout is not None:
        return exploded_layout

    if min_size > MIN_FIT_FONT_SIZE:
        exploded_small_layout, exploded_small_overflow = find_layout(explode_long_tokens(tokens), MIN_FIT_FONT_SIZE)
        if exploded_small_layout is not None:
            return exploded_small_layout
        if exploded_overflow is None:
            exploded_overflow = exploded_small_overflow

    relaxed_layout, relaxed_overflow = find_layout(
        tokens,
        MIN_FIT_FONT_SIZE,
        min_line_count_override=min(3, max(2, target_lines // 2)),
    )
    if relaxed_layout is not None and len(relaxed_layout.lines) > 1:
        return relaxed_layout
    if best_overflow is not None:
        return best_overflow
    if exploded_overflow is not None:
        return exploded_overflow

    font_size = MIN_FIT_FONT_SIZE if min_size > MIN_FIT_FONT_SIZE else min_size
    font = cached_truetype_font(str(font_path), int(font_size))
    return Layout(
        font_size=font_size,
        lines=[text],
        inner_rect=inner,
        line_height=int(round(font_size * 1.14)),
        measured_width=text_size(draw, font, text, vertical_dot_run=False)[0],
        measured_height=text_size(draw, font, text, vertical_dot_run=False)[1],
        target_lines=target_lines,
    )


def choose_layout(
    draw: ImageDraw.ImageDraw,
    text: str,
    bbox: list[int],
    image_size: tuple[int, int],
    font_path: Path,
    min_font_size: int = 20,
    max_font_size: int | None = None,
    is_unboxed: bool = False,
    src_line_count: int = 0,
    is_split_rect: bool = False,
    split_order: int = -1,
    mask_centroid: list[int] | None = None,
    round_score: float = -1.0,
    force_vertical_short_interjection: bool = False,
    force_vertical_title_layout: bool = False,
    force_vertical_layout: bool = False,
    force_horizontal_layout: bool = False,
    strict_max_font_size: bool = False,
    preserve_explicit_lines: bool = False,
    prefer_explicit_lines_when_reasonable: bool = False,
    allow_overestimated_font_size: bool = False,
) -> Layout:
    """레이아웃 결정 — 조건 충족 시 세로쓰기로 자동 전환.

    세로 전환 조건: 원본 1줄(src_line_count==1) + aspect >= 2.0 + 짧은 텍스트(weighted_len <= 6)
    조건 충족 시 폰트 크기 비교 없이 세로쓰기 사용, 페이지 밴드 max로 캡.
    """
    layout = _choose_layout_raw(
        draw, text, bbox, image_size, font_path,
        min_font_size, max_font_size, is_unboxed, is_split_rect, split_order, mask_centroid,
        round_score=round_score,
        source_min_lines=src_line_count,
        preserve_explicit_lines=preserve_explicit_lines,
        prefer_explicit_lines_when_reasonable=prefer_explicit_lines_when_reasonable,
        allow_overestimated_font_size=allow_overestimated_font_size,
    )
    if preserve_explicit_lines and text.strip().count("\n") > 0:
        return layout

    # 세로쓰기 폴백 — bbox 기준 aspect/wlen 재계산
    inner = inner_rect_for(bbox, image_size, is_unboxed=is_unboxed, is_split_rect=is_split_rect, split_order=split_order, mask_centroid=mask_centroid, round_score=round_score)
    _, _, inner_w, inner_h = inner
    aspect = inner_h / max(inner_w, 1)
    wlen = weighted_len(re.sub(r"\s+", " ", text.strip()))
    if force_horizontal_layout:
        return layout

    if force_vertical_layout:
        v_max = vertical_cap_for(max_font_size, is_unboxed, strict_max_font_size)
        vertical = _try_vertical_layout(
            draw, text, bbox, image_size, font_path, is_unboxed, is_split_rect,
            v_max, split_order, mask_centroid,
            round_score=round_score,
        )
        if vertical:
            return vertical

    if force_vertical_title_layout and aspect >= 1.35 and wlen <= 12:
        vertical = _try_vertical_layout(
            draw, text, bbox, image_size, font_path, is_unboxed, is_split_rect,
            vertical_cap_for(max_font_size, is_unboxed, strict_max_font_size) if max_font_size is not None else 88,
            split_order, mask_centroid,
            round_score=round_score,
        )
        if vertical:
            horizontal_height_usage = layout.measured_height / max(1, layout.inner_rect[3])
            vertical_height_usage = vertical.measured_height / max(1, vertical.inner_rect[3])
            if vertical_height_usage >= 0.65 and vertical_height_usage >= horizontal_height_usage * 1.6:
                return vertical

    ellipsis_vertical_hint = (
        src_line_count == 1
        and has_ellipsis(text)
        and aspect >= 1.45
        and text_weight_without_ellipsis(text) <= 6
    )
    if src_line_count == 1 and aspect >= 2.2 and 6 < wlen <= 12 and len(layout.lines) == 1 and layout.target_lines >= 3:
        chunks = chunk_korean_text(text)
        if len(chunks) >= 3:
            chunk_max = vertical_cap_for(max_font_size, is_unboxed, strict_max_font_size)
            chunked_layout = _try_fixed_lines_layout(
                draw,
                chunks,
                bbox,
                image_size,
                font_path,
                min_font_size=min_font_size,
                max_font_size=chunk_max,
                is_unboxed=is_unboxed,
                is_split_rect=is_split_rect,
                split_order=split_order,
                mask_centroid=mask_centroid,
                round_score=round_score,
            )
            if (
                chunked_layout is not None
                and len(chunked_layout.lines) > 1
                and chunked_layout.font_size >= layout.font_size - 1
                and chunked_layout.measured_height <= chunked_layout.inner_rect[3]
            ):
                return chunked_layout

    if src_line_count == 1 and ((aspect >= 2.0 and wlen <= 6) or ellipsis_vertical_hint or force_vertical_short_interjection):
        # 페이지 밴드 cap에 +8 leeway 적용 후 _try_vertical_layout에 전달
        # → font_size와 line_height가 항상 동일한 크기 기준으로 결정됨
        v_max = vertical_cap_for(max_font_size, is_unboxed, strict_max_font_size)
        vertical = _try_vertical_layout(
            draw, text, bbox, image_size, font_path, is_unboxed, is_split_rect, v_max, split_order, mask_centroid,
            round_score=round_score,
        )
        if vertical:
            chunked = _try_overlong_vertical_ellipsis_chunk_layout(
                draw,
                text,
                bbox,
                image_size,
                font_path,
                vertical,
                min_font_size=min_font_size,
                max_font_size=v_max,
                is_unboxed=is_unboxed,
                is_split_rect=is_split_rect,
                split_order=split_order,
                mask_centroid=mask_centroid,
                round_score=round_score,
            )
            if chunked:
                return chunked
            return vertical

    height_usage = layout.measured_height / max(1, layout.inner_rect[3])
    # 추가 세로쓰기 fallback: wlen이 6을 초과하지만 가로/explode로도 폰트가 작거나
    # 세로 safe rect 높이를 거의 쓰지 못하는 경우.
    if (
        src_line_count <= 1
        and aspect >= 2.0
        and wlen <= 10
        and (
            layout.font_size <= MIN_FIT_FONT_SIZE + 2
            or (len(layout.lines) <= 2 and height_usage <= 0.42)
        )
    ):
        v_max = vertical_cap_for(max_font_size, is_unboxed, strict_max_font_size)
        vertical = _try_vertical_layout(
            draw, text, bbox, image_size, font_path, is_unboxed, is_split_rect, v_max, split_order, mask_centroid,
            round_score=round_score,
        )
        if vertical and vertical.font_size > layout.font_size:
            chunked = _try_overlong_vertical_ellipsis_chunk_layout(
                draw,
                text,
                bbox,
                image_size,
                font_path,
                vertical,
                min_font_size=min_font_size,
                max_font_size=v_max,
                is_unboxed=is_unboxed,
                is_split_rect=is_split_rect,
                split_order=split_order,
                mask_centroid=mask_centroid,
                round_score=round_score,
            )
            if chunked:
                return chunked
            return vertical

    return layout


def draw_text_block(
    image: np.ndarray,
    text: str,
    bbox: list[int],
    font_path: Path,
    layout: Layout | None = None,
    edge_anchor: dict | None = None,
    is_unboxed: bool = False,
    bg_alpha: int = 128,
    text_fill: tuple[int, int, int] = (0, 0, 0),
    bg_color: tuple[int, int, int] = (255, 255, 255),
    text_stroke_width: int = 0,
    text_stroke_fill: tuple[int, int, int] | None = None,
    rotation_deg: float = 0.0,
    font_weight: int | None = None,
    text_align: str = "center",
) -> tuple[np.ndarray, Layout]:
    pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    pil, layout = draw_text_block_pil(
        pil,
        text,
        bbox,
        font_path,
        layout=layout,
        edge_anchor=edge_anchor,
        is_unboxed=is_unboxed,
        bg_alpha=bg_alpha,
        text_fill=text_fill,
        bg_color=bg_color,
        text_stroke_width=text_stroke_width,
        text_stroke_fill=text_stroke_fill,
        rotation_deg=rotation_deg,
        font_weight=font_weight,
        text_align=text_align,
    )
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR), layout


def draw_text_block_pil(
    pil: Image.Image,
    text: str,
    bbox: list[int],
    font_path: Path,
    layout: Layout | None = None,
    edge_anchor: dict | None = None,
    is_unboxed: bool = False,
    bg_alpha: int = 128,
    text_fill: tuple[int, int, int] = (0, 0, 0),
    bg_color: tuple[int, int, int] = (255, 255, 255),
    text_stroke_width: int = 0,
    text_stroke_fill: tuple[int, int, int] | None = None,
    rotation_deg: float = 0.0,
    font_weight: int | None = None,
    text_align: str = "center",
) -> tuple[Image.Image, Layout]:
    """draw_text_block의 PIL(RGB) 버전. 페이지를 블록마다 numpy↔PIL로 바꾸지 않도록 PIL 이미지를 그대로 넘긴다."""
    draw = ImageDraw.Draw(pil)
    img_w, img_h = pil.size
    if layout is None:
        layout = choose_layout(draw, text, bbox, (img_w, img_h), font_path, is_unboxed=is_unboxed)
    font = load_font(font_path, layout.font_size, font_weight)
    vertical_dot_run = layout_uses_vertical_dot_runs(layout)

    ix, iy, iw, ih = layout.inner_rect
    aspect = ih / max(iw, 1)
    visual_top, visual_bottom = layout_visual_y_bounds(
        draw,
        font,
        layout,
        stroke_width=text_stroke_width,
        vertical_dot_run=vertical_dot_run,
    )
    edge_mid_y = edge_anchor.get("mid_y") if edge_anchor is not None else None
    edge_inside_inner = edge_mid_y is not None and iy <= float(edge_mid_y) <= iy + ih
    if is_unboxed:
        # 가장 긴변(높이)의 중점 기준 정렬: inner_rect padding 제외하고 bbox 전체 높이로 centering
        bx, by, bw, bh = [int(v) for v in bbox[:4]]
        y = centered_draw_y_for_visual_bounds(float(by), float(bh), visual_top, visual_bottom)
        y = clamp_draw_y_to_visual_bounds(y, float(by), float(bh), visual_top, visual_bottom)
    elif edge_anchor is not None and edge_inside_inner and aspect >= 1.6:
        visual_height = max(0.0, visual_bottom - visual_top)
        y = float(edge_anchor["mid_y"]) - visual_height / 2.0 - visual_top
        y = clamp_draw_y_to_visual_bounds(y, float(iy), float(ih), visual_top, visual_bottom)
    else:
        y = centered_draw_y_for_visual_bounds(float(iy), float(ih), visual_top, visual_bottom)
        y = clamp_draw_y_to_visual_bounds(y, float(iy), float(ih), visual_top, visual_bottom)

    rotation_deg = overlay_override_rotation_deg({"rotation_deg": rotation_deg})
    text_align = text_align if text_align in {"left", "center", "right"} else "center"
    base_rgba = None
    render_layer = None
    if rotation_deg:
        base_rgba = pil.convert("RGBA")
        render_layer = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(render_layer)

    if is_unboxed:
        # 투명도 있는 흰색 배경 박스: RGBA 레이어를 별도 생성 후 alpha_composite
        # LaMa 대상: unboxed_rect 전체를 흰 배경으로 커버
        bg_rect = [bx, by, bx + bw, by + bh]
        if render_layer is not None:
            draw.rectangle(bg_rect, fill=(*bg_color, bg_alpha))
        else:
            pil_rgba = pil.convert("RGBA")
            alpha_layer = Image.new("RGBA", pil_rgba.size, (0, 0, 0, 0))
            draw_layer = ImageDraw.Draw(alpha_layer)
            draw_layer.rectangle(bg_rect, fill=(*bg_color, bg_alpha))
            pil = Image.alpha_composite(pil_rgba, alpha_layer).convert("RGB")
            draw = ImageDraw.Draw(pil)

    decorative_kind = decorative_line_kind(layout.lines)
    if decorative_kind:
        draw_decorative_line(draw, layout, decorative_kind, text_fill)
        if render_layer is not None and base_rgba is not None:
            cx = float(bbox[0]) + float(bbox[2]) / 2.0
            cy = float(bbox[1]) + float(bbox[3]) / 2.0
            rotated = render_layer.rotate(-rotation_deg, resample=Image.Resampling.BICUBIC, center=(cx, cy))
            pil = Image.alpha_composite(base_rgba, rotated).convert("RGB")
        return pil, layout

    offsets = (
        vertical_line_offsets(layout.lines, layout.line_height, layout.font_size)
        if vertical_dot_run
        else [idx * layout.line_height for idx in range(len(layout.lines))]
    )
    previous_visual_bottom: float | None = None
    idx = 0
    while idx < len(layout.lines):
        line = layout.lines[idx]
        if is_vertical_decorative_line_text(line):
            end_idx = idx + 1
            while end_idx < len(layout.lines) and is_vertical_decorative_line_text(layout.lines[end_idx]):
                end_idx += 1
            decorative_units = sum(
                len(re.sub(r"\s+", "", str(item or "")))
                for item in layout.lines[idx:end_idx]
            )
            if decorative_units >= 2:
                first_y = y + (offsets[idx] if idx < len(offsets) else idx * layout.line_height)
                last_offset = offsets[end_idx - 1] if end_idx - 1 < len(offsets) else (end_idx - 1) * layout.line_height
                last_y = y + last_offset
                draw_decorative_vertical_line_span(
                    draw,
                    ix + iw / 2,
                    first_y + layout.font_size * 0.08,
                    last_y + layout.font_size * (0.92 if end_idx - idx >= 2 else max(1.1, decorative_units * 0.82)),
                    layout.font_size,
                    text_fill,
                )
                idx = end_idx
                continue
        line_w, _ = text_size(draw, font, line, vertical_dot_run=vertical_dot_run)
        x = ix + (iw - line_w) / 2
        line_y = y + (offsets[idx] if idx < len(offsets) else idx * layout.line_height)
        if vertical_dot_run and (is_dot_ellipsis_only_text(line) or is_synthetic_vertical_dot(line)) and previous_visual_bottom is not None:
            min_gap = max(3.0, layout.font_size * 0.22)
            line_top, _line_bottom = text_visual_y_bounds(
                draw,
                font,
                line,
                stroke_width=text_stroke_width,
                vertical_dot_run=vertical_dot_run,
            )
            line_y = max(line_y, previous_visual_bottom + min_gap - line_top)
        if text_align == "left":
            x = ix
        elif text_align == "right":
            x = ix + iw - line_w
        draw_tracked_text(
            draw,
            (x, line_y),
            line,
            font,
            text_fill,
            stroke_width=text_stroke_width,
            stroke_fill=text_stroke_fill,
            vertical_dot_run=vertical_dot_run,
        )
        line_top, line_bottom = text_visual_y_bounds(
            draw,
            font,
            line,
            stroke_width=text_stroke_width,
            vertical_dot_run=vertical_dot_run,
        )
        previous_visual_bottom = line_y + line_bottom
        idx += 1

    if render_layer is not None and base_rgba is not None:
        cx = float(bbox[0]) + float(bbox[2]) / 2.0
        cy = float(bbox[1]) + float(bbox[3]) / 2.0
        rotated = render_layer.rotate(-rotation_deg, resample=Image.Resampling.BICUBIC, center=(cx, cy))
        pil = Image.alpha_composite(base_rgba, rotated).convert("RGB")

    return pil, layout


def cluster_line_count(cluster: dict) -> int:
    """matched_cluster_details 의 lines 합산 → 원본 OCR 줄 수."""
    details = cluster.get("matched_cluster_details") or []
    return sum(len(d.get("lines") or []) for d in details)


def _cluster_detail_rects(cluster: dict) -> list[list[int]]:
    rects: list[list[int]] = []
    for detail in cluster.get("matched_cluster_details") or []:
        rect = detail.get("tight_rect") or detail.get("bounding_rect")
        if rect and len(rect) == 4:
            rects.append([int(v) for v in rect])
        for line in detail.get("lines") or []:
            line_rect = line.get("bbox")
            if line_rect and len(line_rect) == 4:
                rects.append([int(v) for v in line_rect])
    return rects


def _is_japanese_kana_only(text: str) -> bool:
    stripped = re.sub(r"\s+", "", text or "")
    if not stripped:
        return False
    return all(("ぁ" <= ch <= "ゟ") or ("ァ" <= ch <= "ヿ") or ch in "ー〜～っッ" for ch in stripped)


def should_force_vertical_short_interjection(cluster: dict, translated_text: str) -> bool:
    """CV가 한 줄로 잡은 짧은 세로 효과음만 세로쓰기 폴백을 허용한다."""
    if cluster.get("location") == "unboxed":
        return False
    if cluster_line_count(cluster) != 1:
        return False
    if cluster.get("orientation") != "vertical":
        return False
    render_hint = cluster.get("render_hint_final") or cluster.get("render_hint")
    if render_hint in {"sfx", "efx"}:
        render_hint = "serif_emphasis"
    if render_hint != "serif_emphasis":
        return False

    source_text = "".join(str(d.get("text", "")) for d in cluster.get("matched_cluster_details") or [])
    if not _is_japanese_kana_only(source_text) or len(re.sub(r"\s+", "", source_text)) > 4:
        return False

    translated_compact = re.sub(r"\s+", "", translated_text or "")
    if weighted_len(translated_compact) > 4:
        return False

    rects = _cluster_detail_rects(cluster)
    if not rects:
        return False
    return any(rect[3] / max(rect[2], 1) >= 1.65 for rect in rects)


def cluster_bbox(cluster: dict) -> list[int]:
    return (
        cluster.get("overlay_rect")
        or cluster.get("union_bbox")
        or cluster.get("bounding_rect_expanded")
        or cluster.get("bounding_rect")
        or [0, 0, 1, 1]
    )


def cluster_detail_bbox(cluster: dict) -> list[int] | None:
    rects = _cluster_detail_rects(cluster)
    if not rects:
        return None
    x1 = min(rect[0] for rect in rects)
    y1 = min(rect[1] for rect in rects)
    x2 = max(rect[0] + rect[2] for rect in rects)
    y2 = max(rect[1] + rect[3] for rect in rects)
    return [x1, y1, max(1, x2 - x1), max(1, y2 - y1)]


def text_group_centered_sub_rect(
    cluster: dict,
    safe_rect: list[int],
    require_off_center: bool = True,
) -> list[int] | None:
    """shared bubble 내부의 sentence를 원본 TG center clue 기준 sub-rect에 배치한다."""
    detail_rect = cluster_detail_bbox(cluster)
    if not detail_rect or not safe_rect or len(safe_rect) != 4:
        return None

    sx, sy, sw, sh = [int(v) for v in safe_rect[:4]]
    dx, dy, dw, dh = [int(v) for v in detail_rect[:4]]
    safe_area = max(1, sw * sh)
    detail_area = max(1, dw * dh)
    detail_cx = dx + dw / 2
    detail_cy = dy + dh / 2
    safe_cx = sx + sw / 2
    safe_cy = sy + sh / 2
    center_far_enough = (
        abs(detail_cx - safe_cx) >= sw * 0.16
        or abs(detail_cy - safe_cy) >= sh * 0.16
    )
    if detail_area > safe_area * 0.45 or (require_off_center and not center_far_enough):
        return None

    pad_x = max(24, int(round(dw * 0.35)))
    pad_y = max(30, int(round(dh * 0.35)))

    x1 = max(sx, dx - pad_x)
    y1 = max(sy, dy - pad_y)
    x2 = min(sx + sw, dx + dw + pad_x)
    y2 = min(sy + sh, dy + dh + pad_y)
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


def shared_segment_safe_rect(
    cluster: dict,
    safe_rect: list[int],
    group: list[int] | None,
    flood_info_by_anchor: dict[int, dict],
) -> list[int] | None:
    """같은 bubble 안 여러 TG segment의 기본 safe rect를 TG 중심 경계로 분할한다."""
    matched = [int(v) for v in cluster.get("matched_cluster", [])]
    if len(matched) != 1 or not group or len(group) < 2:
        return None
    if cluster.get("source_segment_index") is None:
        return None
    current_anchor = matched[0]
    if current_anchor not in {int(v) for v in group}:
        return None
    if not safe_rect or len(safe_rect) != 4:
        return None

    current_detail = cluster_detail_bbox(cluster)
    if not current_detail:
        return None

    centers: list[tuple[int, float, list[int]]] = []
    for raw_anchor_id in group:
        anchor_id = int(raw_anchor_id)
        rect = flood_info_by_anchor.get(anchor_id, {}).get("anchor_bbox")
        if not rect or len(rect) != 4:
            continue
        rx, ry, rw, rh = [int(v) for v in rect[:4]]
        if rw <= 0 or rh <= 0:
            continue
        centers.append((anchor_id, rx + rw / 2, [rx, ry, rw, rh]))

    if len(centers) < 2:
        return None
    if current_anchor not in {item[0] for item in centers}:
        return None

    sx, sy, sw, sh = [int(v) for v in safe_rect[:4]]
    if sw <= 0 or sh <= 0:
        return None

    def _axis_center(item: tuple[int, float, list[int]], axis: str) -> float:
        _anchor_id, _cx, rect = item
        x, y, w, h = rect
        return x + w / 2 if axis == "x" else y + h / 2

    xs = [_axis_center(item, "x") for item in centers]
    ys = [_axis_center(item, "y") for item in centers]
    axis = "x" if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else "y"
    ordered = sorted(centers, key=lambda item: _axis_center(item, axis))
    index = next((idx for idx, item in enumerate(ordered) if item[0] == current_anchor), -1)
    if index < 0:
        return None

    def _centered_span(
        center: float,
        raw_start: int,
        raw_end: int,
        min_size: int,
        max_size: int,
        bound_start: int,
        bound_end: int,
    ) -> tuple[int, int] | None:
        raw_size = raw_end - raw_start
        if raw_size < min_size:
            return None
        size = max(min_size, min(raw_size, max_size, bound_end - bound_start))
        start = int(round(center - size / 2))
        start = int(clamp(start, bound_start, bound_end - size))
        return start, start + size

    current_center = _axis_center(ordered[index], axis)
    prev_center = _axis_center(ordered[index - 1], axis) if index > 0 else None
    next_center = _axis_center(ordered[index + 1], axis) if index + 1 < len(ordered) else None

    current_rect = ordered[index][2]
    rx, ry, rw, rh = [int(v) for v in current_rect[:4]]

    if axis == "x":
        left = sx if prev_center is None else int(round((prev_center + current_center) / 2))
        right = sx + sw if next_center is None else int(round((current_center + next_center) / 2))
        left = int(clamp(left, sx, sx + sw))
        right = int(clamp(right, sx, sx + sw))
        min_w = max(32, int(round(sw * 0.18)))
        max_w = max(min_w, int(round(max(rw * 4.2, sw * 0.34))))
        span = _centered_span(current_center, left, right, min_w, max_w, sx, sx + sw)
        if not span:
            return None
        left, right = span
        pad_y = max(30, int(round(rh * 0.16)))
        top = int(clamp(ry - pad_y, sy, sy + sh))
        bottom = int(clamp(ry + rh + pad_y, sy, sy + sh))
        min_h = max(40, int(round(sh * 0.28)))
        max_h = max(min_h, int(round(max(rh * 1.65, sh * 0.72))))
        span = _centered_span(ry + rh / 2, top, bottom, min_h, max_h, sy, sy + sh)
        if not span:
            return None
        top, bottom = span
        return [left, top, right - left, bottom - top]

    top = sy if prev_center is None else int(round((prev_center + current_center) / 2))
    bottom = sy + sh if next_center is None else int(round((current_center + next_center) / 2))
    top = int(clamp(top, sy, sy + sh))
    bottom = int(clamp(bottom, sy, sy + sh))
    min_h = max(32, int(round(sh * 0.18)))
    max_h = max(min_h, int(round(max(rh * 4.2, sh * 0.34))))
    span = _centered_span(current_center, top, bottom, min_h, max_h, sy, sy + sh)
    if not span:
        return None
    top, bottom = span
    pad_x = max(24, int(round(rw * 0.22)))
    left = int(clamp(rx - pad_x, sx, sx + sw))
    right = int(clamp(rx + rw + pad_x, sx, sx + sw))
    min_w = max(40, int(round(sw * 0.28)))
    max_w = max(min_w, int(round(max(rw * 1.65, sw * 0.72))))
    span = _centered_span(rx + rw / 2, left, right, min_w, max_w, sx, sx + sw)
    if not span:
        return None
    left, right = span
    return [left, top, right - left, bottom - top]


def single_tg_centered_safe_rect(
    cluster: dict,
    safe_rect: list[int],
    bubble_rect: list[int] | None = None,
    allow_long_text: bool = False,
) -> list[int] | None:
    """단일 TG가 safe rect 중심에서 크게 치우친 경우 bubble 안에서 TG 방향으로 배치 rect를 잡는다."""
    text = (cluster.get("text_translated") or "").strip()
    normalized_text = re.sub(r"\s+", " ", text)
    if not allow_long_text and (
        weighted_len(normalized_text) >= 22
        or len(tokenize(normalized_text)) >= 10
        or text.count("\n") + 1 >= 4
    ):
        return None

    detail_rect = cluster_detail_bbox(cluster)
    if not detail_rect or not safe_rect or len(safe_rect) != 4:
        return None

    sx, sy, sw, sh = [int(v) for v in safe_rect[:4]]
    if bubble_rect and len(bubble_rect) == 4:
        bx, by, bw, bh = [int(v) for v in bubble_rect[:4]]
    else:
        bx, by, bw, bh = sx, sy, sw, sh
    dx, dy, dw, dh = [int(v) for v in detail_rect[:4]]
    if sw <= 0 or sh <= 0 or bw <= 0 or bh <= 0 or dw <= 0 or dh <= 0:
        return None
    safe_area = max(1, sw * sh)
    detail_area = max(1, dw * dh)
    if detail_area > safe_area * 0.65:
        return None

    safe_cx = sx + sw / 2
    safe_cy = sy + sh / 2
    detail_cx = dx + dw / 2
    detail_cy = dy + dh / 2
    far_ratio = 0.15 if allow_long_text else 0.22
    x_far = abs(detail_cx - safe_cx) >= sw * far_ratio
    y_far = abs(detail_cy - safe_cy) >= sh * far_ratio
    if not (x_far or y_far):
        return None

    target_w = sw
    target_h = sh
    detail_aspect = dw / max(dh, 1)
    if x_far:
        aspect_cap_w = sh * clamp(detail_aspect * 1.35, 0.70, 1.25)
        min_width_ratio = 0.55 if allow_long_text else 0.45
        detail_width_gain = 2.4 if allow_long_text else 1.8
        target_w = int(round(min(sw, max(dw * detail_width_gain, aspect_cap_w, sw * min_width_ratio))))
    if y_far:
        aspect_cap_h = sw / max(clamp(detail_aspect * 1.35, 0.70, 1.25), 0.01)
        target_h = int(round(min(sh, max(dh * 1.6, aspect_cap_h, sh * 0.55))))

    nx = int(round(detail_cx - target_w / 2))
    ny = int(round(detail_cy - target_h / 2))
    nx = int(clamp(nx, bx, bx + bw - target_w))
    ny = int(clamp(ny, by, by + bh - target_h))
    if target_w < sw * 0.40 or target_h < sh * 0.50:
        return None
    return [nx, ny, max(1, target_w), max(1, target_h)]


def should_anchor_single_tg_in_irregular_large_bubble(
    flood_info: dict,
) -> bool:
    """큰 불규칙 단일 TG bubble은 safe rect 중앙보다 TG 위치를 신뢰한다."""
    if not flood_info.get("is_valid") or flood_info.get("region_type_guess") != "bubble":
        return False
    bubble_rect = flood_info.get("bubble_rect")
    anchor_ids = flood_info.get("anchor_ids") or []
    if not bubble_rect or len(bubble_rect) != 4 or len(anchor_ids) != 1:
        return False
    _x, _y, bw, bh = [int(v) for v in bubble_rect]
    area = bw * bh
    is_large = area >= 120_000 or max(bw, bh) >= 520
    if not is_large:
        return False
    round_score = _parse_round_score(flood_info.get("quality_flags", []))
    return 0.0 <= round_score < 0.58


def should_project_tg_inside_large_bubble(
    flood_info: dict,
    group: list[int] | None,
    matched: list[int],
) -> bool:
    """큰 bubble 안의 일부 TG/segment는 전체 safe rect 대신 원본 TG 위치감을 보존한다."""
    if not group or set(group).issubset(set(matched)):
        return False
    if not flood_info.get("is_valid") or flood_info.get("region_type_guess") != "bubble":
        return False
    bubble_rect = flood_info.get("bubble_rect")
    safe_rect = flood_info.get("safe_text_rect")
    if not bubble_rect or len(bubble_rect) != 4 or not safe_rect or len(safe_rect) != 4:
        return False
    _x, _y, bw, bh = [int(v) for v in bubble_rect]
    _sx, _sy, sw, sh = [int(v) for v in safe_rect]
    bubble_area = bw * bh
    safe_area = sw * sh
    return bubble_area >= 90_000 or safe_area >= 60_000 or max(bw, bh) >= 420


def safe_rect_is_too_short_for_short_vertical_source(
    cluster: dict,
    safe_rect: list[int],
    region_rect: list[int],
) -> bool:
    """짧은 세로 원문에서 eroded safe rect가 높이를 과도하게 잃으면 region rect로 되돌린다."""
    if cluster_line_count(cluster) != 1:
        return False
    translated = re.sub(r"\s+", " ", str(cluster.get("text_translated") or "").strip())
    if not translated or weighted_len(translated) > 5.5 or len(tokenize(translated)) > 2:
        return False
    detail_rect = cluster_detail_bbox(cluster)
    if not detail_rect or not safe_rect or len(safe_rect) != 4 or not region_rect or len(region_rect) != 4:
        return False
    _dx, _dy, dw, dh = [int(v) for v in detail_rect[:4]]
    _sx, _sy, sw, sh = [int(v) for v in safe_rect[:4]]
    _rx, _ry, rw, rh = [int(v) for v in region_rect[:4]]
    if min(dw, dh, sw, sh, rw, rh) <= 0:
        return False
    return dw <= 36 and dh / max(dw, 1) >= 2.0 and sh < rh * 0.70


def should_use_bubble_rect_for_short_vertical_source(
    cluster: dict,
    flood_info: dict,
) -> bool:
    """짧은 원문 세로 1줄은 TG 주변 축소 rect 대신 bubble 전체로 세로쓰기 후보에 올린다."""
    if not flood_info.get("is_valid") or flood_info.get("region_type_guess") != "bubble":
        return False
    if len(cluster.get("matched_cluster") or []) != 1:
        return False
    if cluster_line_count(cluster) != 1:
        return False

    translated = re.sub(r"\s+", " ", str(cluster.get("text_translated") or "").strip())
    if not translated or weighted_len(translated) > 5.5 or len(tokenize(translated)) > 2:
        return False

    detail_rect = cluster_detail_bbox(cluster)
    bubble_rect = flood_info.get("bubble_rect")
    if not detail_rect or not bubble_rect or len(bubble_rect) != 4:
        return False

    _dx, _dy, dw, dh = [int(v) for v in detail_rect[:4]]
    _bx, _by, bw, bh = [int(v) for v in bubble_rect[:4]]
    if dw <= 0 or dh <= 0 or bw <= 0 or bh <= 0:
        return False

    return dw <= 36 and dh / max(dw, 1) >= 2.0 and bh / max(bw, 1) >= 1.45


def should_keep_safe_rect_centered_for_small_bubble(flood_info: dict) -> bool:
    """작은 bubble은 원문 TG 중심 보정 대신 safe rect 중앙을 우선한다."""
    bubble_rect = flood_info.get("bubble_rect")
    safe_rect = flood_info.get("safe_text_rect")
    if not bubble_rect or len(bubble_rect) != 4 or not safe_rect or len(safe_rect) != 4:
        return False

    _bx, _by, bw, bh = [int(v) for v in bubble_rect[:4]]
    _sx, _sy, sw, sh = [int(v) for v in safe_rect[:4]]
    if bw <= 0 or bh <= 0 or sw <= 0 or sh <= 0:
        return False

    bubble_area = bw * bh
    safe_area = sw * sh
    return bubble_area <= 45_000 and safe_area <= 30_000


def rect_key(rect: list[int] | None) -> tuple[int, int, int, int] | None:
    if not rect or len(rect) != 4:
        return None
    return tuple(int(v) for v in rect)


def build_shared_flood_groups_from_regions_json(regions_json_path: Path) -> dict[int, list[int]]:
    """regions.json의 anchor_ids 필드에서 shared_groups(동일 bubble 클러스터 그룹) 구축.

    flood_vis.json 없이도 같은 버블에 속한 ATG를 그룹핑할 수 있다.
    """
    if not regions_json_path.exists():
        return {}
    try:
        data = json.loads(regions_json_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    regions = data.get("regions", []) if isinstance(data, dict) else data
    groups: dict[int, list[int]] = {}
    for region in regions:
        anchor_ids = sorted(int(a) for a in (region.get("anchor_ids") or []) if a is not None)
        if len(anchor_ids) <= 1:
            continue  # 단일 anchor: shared group 불필요
        for anchor_id in anchor_ids:
            groups[anchor_id] = anchor_ids
    return groups


def build_shared_flood_groups(flood_vis_path: Path) -> dict[int, list[int]]:
    if not flood_vis_path.exists():
        return {}
    data = json.loads(flood_vis_path.read_text(encoding="utf-8"))
    by_rect: dict[tuple[int, int, int, int], list[int]] = {}
    for result in data.get("results", []):
        key = rect_key(result.get("bubble_rect"))
        cluster_id = result.get("cluster_id")
        if key is None or cluster_id is None:
            continue
        by_rect.setdefault(key, []).append(int(cluster_id))

    groups: dict[int, list[int]] = {}
    for ids in by_rect.values():
        if len(ids) <= 1:
            continue
        sorted_ids = sorted(ids)
        for cluster_id in sorted_ids:
            groups[cluster_id] = sorted_ids
    return groups


def build_flood_info_by_anchor(flood_vis_path: Path) -> dict[int, dict]:
    if not flood_vis_path.exists():
        return {}
    data = json.loads(flood_vis_path.read_text(encoding="utf-8"))
    result: dict[int, dict] = {}
    for item in data.get("results", []):
        cluster_id = item.get("cluster_id")
        rect = item.get("bubble_rect")
        if cluster_id is not None and rect and len(rect) == 4:
            result[int(cluster_id)] = {
                "bubble_rect": [int(v) for v in rect],
                "region_type_guess": item.get("region_type_guess", ""),
                "touches_image_border": bool(item.get("touches_image_border", False)),
                "is_valid": item.get("is_valid", False),
                "seed": item.get("seed"),
                "anchor_bbox": item.get("anchor_bbox"),
            }
    return result


def build_split_region_info_by_anchor(flood_regions_path: Path) -> dict[int, dict]:
    """_flood_regions.json(구형 파이프라인 산출물)에서 anchor → region 정보를 구축.

    split_order 가 없는 구형 포맷에서는 -1 로 설정하여 bias 적용을 생략한다.
    """
    if not flood_regions_path.exists():
        return {}
    data = json.loads(flood_regions_path.read_text(encoding="utf-8"))
    result: dict[int, dict] = {}
    for region in data.get("regions", []):
        rect = region.get("original_rect") or region.get("bubble_rect")
        anchor_ids = region.get("anchor_ids") or []
        if not rect or len(rect) != 4:
            continue
        # split_order: 구형 flood_regions 에는 없으므로 -1 (bias 미적용)
        split_order = region.get("split_order")
        split_order = int(split_order) if split_order is not None else -1
        info = {
            "rect": [int(v) for v in rect],
            "anchor_ids": [int(v) for v in anchor_ids],
            "region_id": region.get("region_id"),
            "is_valid": bool(region.get("is_valid", False)),
            "method": region.get("method", ""),
            "quality_flags": region.get("quality_flags", []),
            "split_order": split_order,
            "safe_text_rect": [int(v) for v in region.get("safe_text_rect", [])[:4]] if len(region.get("safe_text_rect", [])) >= 4 else None,
            "region_type_guess": region.get("type") or region.get("region_type_guess", ""),
        }
        for anchor_id in info["anchor_ids"]:
            result[int(anchor_id)] = info
    return result


def overlay_rect_from_split_regions(
    matched_anchor_ids: list[int],
    split_region_by_anchor: dict[int, dict],
    cluster: dict | None = None,
) -> tuple[list[int] | None, str | None]:
    if not matched_anchor_ids or not split_region_by_anchor:
        return None, None

    matched_set = {int(v) for v in matched_anchor_ids}
    region_by_id: dict[int, dict] = {}
    for anchor_id in matched_set:
        info = split_region_by_anchor.get(anchor_id)
        if not info:
            continue
        region_id = info.get("region_id")
        if region_id is not None:
            region_by_id[int(region_id)] = info

    if not region_by_id:
        return None, None

    for info in region_by_id.values():
        if set(info.get("anchor_ids", [])) == matched_set:
            safe_rect = info.get("safe_text_rect")
            if safe_rect and len(safe_rect) == 4:
                if (
                    cluster is not None
                    and len(matched_set) == 1
                    and safe_rect_is_too_short_for_short_vertical_source(cluster, safe_rect, info["rect"])
                ):
                    return info["rect"], f"split_region_short_vertical_fallback_R{info.get('region_id')}"
                return safe_rect, f"split_safe_text_rect_R{info.get('region_id')}"
            return info["rect"], f"split_region_fallback_R{info.get('region_id')}"

    if len(region_by_id) == 1:
        return None, None

    rects = [info["rect"] for info in region_by_id.values()]
    xs = [r[0] for r in rects]
    ys = [r[1] for r in rects]
    x2s = [r[0] + r[2] for r in rects]
    y2s = [r[1] + r[3] for r in rects]
    union = [min(xs), min(ys), max(x2s) - min(xs), max(y2s) - min(ys)]
    return union, "split_flood_region_union"


def edge_split_tg_anchored_rect(cluster: dict, flood_info: dict) -> list[int] | None:
    """방사형 누수로 split child가 길게 잡힌 edge rescue는 TG 주변으로 overlay rect를 당긴다."""
    if not flood_info.get("is_valid") or flood_info.get("region_type_guess") != "bubble":
        return None
    if not flood_info.get("split_parent_rect"):
        return None
    flags = {str(flag) for flag in flood_info.get("quality_flags", [])}
    if "edge_barrier_rescue" not in flags:
        return None
    anchor_ids = flood_info.get("anchor_ids") or []
    if len(anchor_ids) != 1 or len(cluster.get("matched_cluster") or []) != 1:
        return None

    bubble_rect = flood_info.get("bubble_rect")
    detail_rect = cluster_detail_bbox(cluster)
    if not bubble_rect or len(bubble_rect) != 4 or not detail_rect:
        return None

    bx, by, bw, bh = [int(v) for v in bubble_rect]
    dx, dy, dw, dh = [int(v) for v in detail_rect]
    if min(bw, bh, dw, dh) <= 0:
        return None

    bubble_cy = by + bh / 2
    detail_cy = dy + dh / 2
    height_ratio = bh / max(dh, 1)
    y_offset = detail_cy - bubble_cy
    if height_ratio < 2.35 or y_offset < max(42.0, dh * 0.42):
        return None

    target_w = int(round(min(bw, max(dw * 2.8, bw * 0.82, 96))))
    target_h = int(round(min(bh, max(dh * 1.85, bh * 0.58, 190))))
    detail_cx = dx + dw / 2
    nx = int(round(detail_cx - target_w / 2))
    ny = int(round(detail_cy - target_h / 2))
    nx = int(clamp(nx, bx, bx + bw - target_w))
    ny = int(clamp(ny, by, by + bh - target_h))
    return [nx, ny, max(1, target_w), max(1, target_h)]


def build_flood_info_from_regions_json(regions_json_path: Path) -> dict[int, dict]:
    """구형 *_regions.json을 flood_info_by_anchor 형식으로 변환.

    flood_vis.json이 없는 구형 파이프라인 출력(regions.json)에서
    bubble_rect 정보를 추출해 overlay_bbox_for_cluster에 활용.
    is_split_rect 판단은 불가하지만 fill_ratio 기반 tg_anchored 로직은 작동.
    """
    if not regions_json_path.exists():
        return {}
    try:
        data = json.loads(regions_json_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    regions = data.get("regions", [])
    result: dict[int, dict] = {}
    _type_map = {"bubble": "bubble", "narration_like": "narration_like", "unboxed": "noise_like"}
    for region in regions:
        region_type = region.get("type", "")
        bubble_rect = region.get("bubble_rect")
        anchor_ids = region.get("anchor_ids") or []
        if not bubble_rect or len(bubble_rect) != 4:
            continue
        region_type_guess = _type_map.get(region_type, "noise_like")
        is_valid = region_type in {"bubble", "narration_like"}
        quality_flags = region.get("quality_flags", [])
        touches_border = any("border=Y" in str(f) for f in quality_flags)
        # split_order: _regions.json 에는 split된 region에 기록됨
        split_order = region.get("split_order")
        split_order = int(split_order) if split_order is not None else -1
        # mask_centroid: dialogue_extractor 가 polygon mask 로 계산한 픽셀 무게중심
        mask_centroid = region.get("mask_centroid")  # [cx, cy] or None
        for anchor_id in anchor_ids:
            result[int(anchor_id)] = {
                "region_id": region.get("region_id"),
                "anchor_ids": [int(v) for v in anchor_ids],
                "bubble_rect": [int(v) for v in bubble_rect],
                "safe_text_rect": [int(v) for v in region.get("safe_text_rect", [])[:4]] if len(region.get("safe_text_rect", [])) >= 4 else None,
                "safe_text_rect_quality": region.get("safe_text_rect_quality"),
                "ocr_text_rects": [
                    [int(v) for v in rect[:4]]
                    for rect in region.get("ocr_text_rects", [])
                    if isinstance(rect, list) and len(rect) >= 4
                ],
                "region_type_guess": region_type_guess,
                "touches_image_border": touches_border,
                "is_valid": is_valid,
                "seed": None,
                "anchor_bbox": None,
                "split_order": split_order,
                "split_parent_rect": [int(v) for v in region.get("split_parent_rect", [])[:4]] if len(region.get("split_parent_rect", [])) >= 4 else None,
                "mask_centroid": mask_centroid,
                "quality_flags": quality_flags,  # round_score 파싱용
            }
    return result


# (Q1에 의해 _tg_anchored_overlay_rect 및 _OVERSIZED_BUBBLE_FILL_THRESHOLD 제거됨)
# bubble 타입이면 무조건 bubble_rect 사용 → inner_rect_for가 적절한 패딩을 적용


def shared_group_is_spatially_separated(
    group: list[int],
    flood_info_by_anchor: dict[int, dict],
    detail_rects: list[list[int]] | None = None,
) -> bool:
    """같은 bubble 안 TG들이 별도 sub-rect로 렌더할 만큼 떨어졌는지 판단한다."""
    if len(group) < 2:
        return False

    info = flood_info_by_anchor.get(int(group[0]), {})
    safe_rect = info.get("safe_text_rect")
    bubble_rect = info.get("bubble_rect")
    text_rects = info.get("ocr_text_rects") or detail_rects or []
    if not safe_rect or len(safe_rect) != 4 or not bubble_rect or len(bubble_rect) != 4:
        return False
    if len(text_rects) < 2:
        return False

    sx, sy, sw, sh = [int(v) for v in safe_rect]
    bx, by, bw, bh = [int(v) for v in bubble_rect]
    bubble_aspect = bh / max(bw, 1)
    round_score = _parse_round_score(info.get("quality_flags", []))
    # mask_centroid 키가 None으로 들어 있어도 safe rect 중심으로 대체한다.
    centroid = info.get("mask_centroid") or [sx + sw / 2, sy + sh / 2]
    cx, cy = centroid[0], centroid[1]

    centers = [(r[0] + r[2] / 2, r[1] + r[3] / 2) for r in text_rects]
    min_x = min(x for x, _ in centers)
    max_x = max(x for x, _ in centers)
    min_y = min(y for _, y in centers)
    max_y = max(y for _, y in centers)

    left_and_right = (
        min_x <= cx - sw * 0.20
        and max_x >= cx + sw * 0.20
        and max_x - min_x >= max(sw * 0.45, bw * 0.28)
    )
    above_and_below = (
        min_y <= cy - sh * 0.20
        and max_y >= cy + sh * 0.20
        and max_y - min_y >= max(sh * 0.45, bh * 0.28)
    )
    is_large_bubble = (bw * bh >= 120_000) or max(bw, bh) >= 380
    if not is_large_bubble:
        return False
    x_axis_far = is_large_bubble and max_x - min_x >= max(sw * 0.30, bw * 0.28)
    y_axis_far = is_large_bubble and max_y - min_y >= max(sh * 0.30, bh * 0.28)
    if round_score >= 0.80 and (bw * bh) < 120_000 and bubble_aspect >= 1.25:
        x_axis_far = False
        left_and_right = False
    if bubble_aspect >= 1.45 and round_score >= 0.65:
        left_and_right = False
        x_axis_far = False
    return bool(left_and_right or above_and_below or x_axis_far or y_axis_far)


def _shared_group_detail_rects_from_clusters(
    group: list[int],
    anchor_to_cluster: dict[int, dict],
) -> list[list[int]]:
    rects: list[list[int]] = []
    for anchor_id in group:
        cluster = anchor_to_cluster.get(int(anchor_id))
        if not cluster:
            continue
        rect = cluster_detail_bbox(cluster)
        if rect:
            rects.append(rect)
    return rects


def merge_unseparated_shared_segments(
    clusters: list[dict],
    shared_groups: dict[int, list[int]],
    flood_info_by_anchor: dict[int, dict],
    overlay_overrides: dict[str, dict] | None = None,
) -> list[dict]:
    """같은 bubble의 segment들이 가깝게 붙어 있으면 overlay에서 다시 한 문장으로 합친다."""
    overlay_overrides = overlay_overrides or {}
    anchor_to_cluster = {
        int(cluster.get("matched_cluster", [])[0]): cluster
        for cluster in clusters
        if len(cluster.get("matched_cluster") or []) == 1
    }

    def _has_segment_override(cluster: dict) -> bool:
        key = overlay_key_for_cluster(cluster, flood_info_by_anchor)
        if key and (key in overlay_overrides or key.replace(":", "-") in overlay_overrides):
            return True
        try:
            sentence_id = cluster.get("sentence_id")
            return sentence_id is not None and f"s:{int(sentence_id)}" in overlay_overrides
        except (TypeError, ValueError):
            return False

    by_region: dict[int, list[dict]] = {}
    passthrough: list[dict] = []
    for cluster in clusters:
        matched = [int(v) for v in cluster.get("matched_cluster", [])]
        detail_rect = cluster_detail_bbox(cluster)
        if detail_rect and len(matched) == 1:
            anchor_id = int(matched[0])
            flood_info_by_anchor.setdefault(anchor_id, {})["anchor_bbox"] = detail_rect
        if len(matched) != 1:
            passthrough.append(cluster)
            continue
        anchor_id = matched[0]
        group = shared_groups.get(anchor_id)
        info = flood_info_by_anchor.get(anchor_id, {})
        region_id = info.get("region_id") or cluster.get("source_region_id")
        if not group or len(group) < 2 or region_id is None:
            passthrough.append(cluster)
            continue
        segment_mode = overlay_segment_mode_for_region(int(region_id), overlay_overrides)
        if segment_mode == "split":
            passthrough.append(cluster)
            continue
        group_clusters = [anchor_to_cluster.get(int(group_anchor)) for group_anchor in group]
        if segment_mode != "merge" and any(group_cluster and _has_segment_override(group_cluster) for group_cluster in group_clusters):
            passthrough.append(cluster)
            continue
        detail_rects = _shared_group_detail_rects_from_clusters(group, anchor_to_cluster)
        if segment_mode != "merge" and shared_group_is_spatially_separated(group, flood_info_by_anchor, detail_rects):
            passthrough.append(cluster)
            continue
        by_region.setdefault(int(region_id), []).append(cluster)

    merged: list[dict] = list(passthrough)
    for region_clusters in by_region.values():
        if len(region_clusters) == 1:
            merged.append(region_clusters[0])
            continue

        def _reading_key(cluster: dict) -> tuple[float, float]:
            segment_index = cluster.get("source_segment_index")
            if segment_index is not None:
                try:
                    return (float(segment_index), 0.0)
                except (TypeError, ValueError):
                    pass
            rect = cluster_detail_bbox(cluster) or cluster_bbox(cluster)
            x, y, w, h = [int(v) for v in rect]
            return (-(x + w / 2), y + h / 2)

        ordered = sorted(region_clusters, key=_reading_key)
        base = dict(ordered[0])
        merged_ids: list[int] = []
        merged_details: list[dict] = []
        translated_parts: list[str] = []
        source_parts: list[str] = []
        for cluster in ordered:
            merged_ids.extend(int(v) for v in cluster.get("matched_cluster", []))
            merged_details.extend(cluster.get("matched_cluster_details") or [])
            translated = (cluster.get("text_translated") or "").strip()
            source = (cluster.get("text") or cluster.get("ocr_text") or "").strip()
            if translated:
                translated_parts.append(translated)
            if source:
                source_parts.append(source)
        base["matched_cluster"] = merged_ids
        base["matched_cluster_details"] = merged_details
        base["text_translated"] = "\n".join(translated_parts)
        base["text"] = "\n".join(source_parts)
        base["source_segment_index"] = None
        merged.append(base)
    return merged


def overlay_bbox_for_cluster(
    cluster: dict,
    represented_anchor_ids: set[int],
    shared_groups: dict[int, list[int]],
    flood_info_by_anchor: dict[int, dict],
    split_region_by_anchor: dict[int, dict] | None = None,
) -> tuple[list[int], str]:
    bbox = cluster_bbox(cluster)
    matched = [int(v) for v in cluster.get("matched_cluster", [])]

    # bubble 계열은 먼저 split/bubble region을 확정하고, 그 region의 safe_text_rect를 기본 overlay rect로 쓴다.
    split_rect, split_source = overlay_rect_from_split_regions(
        matched,
        split_region_by_anchor or {},
        cluster,
    )
    if split_rect:
        return split_rect, split_source or "split_flood_region"

    for anchor_id in matched:
        flood_info = flood_info_by_anchor.get(anchor_id, {})
        group = shared_groups.get(anchor_id)
        safe_text_rect = flood_info.get("safe_text_rect")
        if (
            safe_text_rect
            and len(safe_text_rect) == 4
            and flood_info.get("is_valid")
            and flood_info.get("region_type_guess") == "bubble"
        ):
            edge_child_rect = edge_split_tg_anchored_rect(cluster, flood_info)
            if edge_child_rect:
                return edge_child_rect, f"edge_split_tg_anchored_rect_from_A{anchor_id}"
            if should_project_tg_inside_large_bubble(flood_info, group, matched):
                segment_rect = shared_segment_safe_rect(
                    cluster,
                    safe_text_rect,
                    group,
                    flood_info_by_anchor,
                )
                if segment_rect:
                    return segment_rect, f"shared_segment_safe_rect_from_A{anchor_id}"
                sub_rect = text_group_centered_sub_rect(
                    cluster,
                    safe_text_rect,
                    require_off_center=False,
                )
                if sub_rect:
                    return sub_rect, f"text_group_centered_sub_rect_from_A{anchor_id}"
            if len(matched) == 1 and should_anchor_single_tg_in_irregular_large_bubble(flood_info):
                centered = single_tg_centered_safe_rect(
                    cluster,
                    safe_text_rect,
                    flood_info.get("bubble_rect"),
                    allow_long_text=True,
                )
                if centered:
                    return centered, f"large_bubble_single_tg_safe_rect_from_A{anchor_id}"
            if (
                len(matched) == 1
                and safe_rect_is_too_short_for_short_vertical_source(
                    cluster,
                    safe_text_rect,
                    flood_info.get("bubble_rect"),
                )
            ):
                return flood_info.get("bubble_rect"), f"short_vertical_source_region_fallback_from_A{anchor_id}"
            return safe_text_rect, f"safe_text_rect_from_A{anchor_id}"

    # safe_text_rect가 없을 때만 bubble/fallback rect를 사용한다.
    for anchor_id in matched:
        flood_info = flood_info_by_anchor.get(anchor_id, {})
        bubble_rect = flood_info.get("bubble_rect")
        if not bubble_rect:
            continue
        if flood_info.get("is_valid") and flood_info.get("region_type_guess") == "bubble":
            edge_child_rect = edge_split_tg_anchored_rect(cluster, flood_info)
            if edge_child_rect:
                return edge_child_rect, f"edge_split_tg_anchored_rect_from_A{anchor_id}"
            return bubble_rect, f"bubble_rect_fallback_from_A{anchor_id}"
        if (
            flood_info.get("region_type_guess") == "narration_like"
            and flood_info.get("touches_image_border")
        ):
            return bubble_rect, f"narration_touch_boundary_fallback_from_A{anchor_id}"

    for anchor_id in matched:
        flood_info = flood_info_by_anchor.get(anchor_id, {})
        group = shared_groups.get(anchor_id)
        if group and not set(group).issubset(set(matched)):
            sub_rect = text_group_centered_sub_rect(
                cluster,
                flood_info.get("safe_text_rect"),
                require_off_center=False,
            )
            if sub_rect:
                return sub_rect, f"text_group_centered_sub_rect_from_A{anchor_id}"
            continue
    return bbox, "cluster_bbox"


def layout_fill_ratio(layout: Layout) -> float:
    _, _, inner_w, inner_h = layout.inner_rect
    inner_area = max(1, inner_w * inner_h)
    return (layout.measured_width * layout.measured_height) / inner_area


def layout_with_font_size(
    draw: ImageDraw.ImageDraw,
    layout: Layout,
    font_path: Path,
    font_size: int,
) -> Layout:
    """수동 지정 font size를 줄바꿈 유지 상태로 적용한다."""
    next_size = max(MIN_FIT_FONT_SIZE, int(font_size))
    if next_size == layout.font_size:
        return layout
    font = cached_truetype_font(str(font_path), int(next_size))
    line_widths = [text_size(draw, font, line, vertical_dot_run=False)[0] for line in layout.lines]
    line_heights = [text_size(draw, font, line, vertical_dot_run=False)[1] for line in layout.lines]
    line_height = max(int(round(next_size * 1.14)), max(line_heights, default=next_size))
    measured_height = line_height * max(0, len(layout.lines) - 1) + max(line_heights, default=next_size)
    return Layout(
        font_size=next_size,
        lines=layout.lines,
        inner_rect=layout.inner_rect,
        line_height=line_height,
        measured_width=max(line_widths, default=0),
        measured_height=measured_height,
        target_lines=layout.target_lines,
    )


def area_based_font_cap(inner_w: int, inner_h: int, is_unboxed: bool) -> int:
    area_root = math.sqrt(max(1, inner_w * inner_h))
    if is_unboxed:
        return int(round(clamp(area_root * 0.22 + 8, UNBOXED_MAX_FONT_SIZE, 56)))
    return int(round(clamp(area_root * 0.24 + 8, 30, 58)))


def should_rescue_area_fit(raw: Layout, page_max_size: int, is_unboxed: bool) -> bool:
    _, _, inner_w, inner_h = raw.inner_rect
    inner_area = inner_w * inner_h
    if raw.font_size <= page_max_size + 3:
        return False
    fill_ratio = layout_fill_ratio(raw)
    if fill_ratio > (0.68 if is_unboxed else 0.62):
        return False
    if is_unboxed:
        return inner_area >= 52000 and fill_ratio >= 0.55
    return inner_area >= 18000 and fill_ratio >= 0.45


def _get_split_order_for_cluster(
    cluster: dict,
    split_region_by_anchor: dict[int, dict],
    flood_info_by_anchor: dict[int, dict],
) -> int:
    """cluster 의 matched_cluster anchor_ids 를 기반으로 split_order 를 결정.

    우선순위:
    1. split_region_by_anchor (현재 파이프라인 _regions.json 유래)
    2. flood_info_by_anchor (구형 _regions.json fallback 유래)
    3. -1 (split_order 정보 없음 — bias 미적용)
    """
    for anchor_id in cluster.get("matched_cluster", []):
        aid = int(anchor_id)
        info = split_region_by_anchor.get(aid)
        if info and info.get("split_order", -1) >= 0:
            return int(info["split_order"])
        info = flood_info_by_anchor.get(aid)
        if info and info.get("split_order", -1) >= 0:
            return int(info["split_order"])
    return -1


def prepare_page_layouts(
    image: np.ndarray,
    clusters: list[dict],
    shared_groups: dict[int, list[int]] | None = None,
    flood_info_by_anchor: dict[int, dict] | None = None,
    split_region_by_anchor: dict[int, dict] | None = None,
    region_info_by_id: dict[int, dict] | None = None,
    font_path: Path = FONT_PATH,
    overlay_guidance: dict | None = None,
) -> list[tuple[dict, str, list[int], str, Layout, int, Path]]:
    pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    img_w, img_h = pil.size
    shared_groups = shared_groups or {}
    flood_info_by_anchor = flood_info_by_anchor or {}
    split_region_by_anchor = split_region_by_anchor or {}
    region_info_by_id = region_info_by_id or {}
    overlay_overrides = overlay_region_overrides(overlay_guidance or {})
    clusters = merge_unseparated_shared_segments(clusters, shared_groups, flood_info_by_anchor, overlay_overrides)
    represented_region_ids = {
        int(region_id)
        for cluster in clusters
        for region_id in [region_id_for_cluster(cluster, flood_info_by_anchor)]
        if region_id is not None
    }
    synthetic_clusters: list[dict] = []
    for key, override in overlay_overrides.items():
        if ":" in key or key.startswith("s:"):
            continue
        try:
            region_id = int(str(key).split("-", 1)[0])
        except (TypeError, ValueError):
            continue
        if region_id in represented_region_ids:
            continue
        text_override = override.get("text_override")
        if not isinstance(text_override, str) or not text_override.strip():
            continue
        region_info = region_info_by_id.get(region_id, {})
        rect = (
            override.get("safe_text_rect")
            or region_info.get("safe_text_rect")
            or region_info.get("bubble_rect")
            or region_info.get("unboxed_rect")
            or region_info.get("source_rect")
        )
        if not isinstance(rect, list) or len(rect) < 4:
            continue
        rect = [int(v) for v in rect[:4]]
        region_type = str(region_info.get("type") or region_info.get("region_type_guess") or "")
        synthetic_clusters.append({
            "matched_cluster": [int(v) for v in region_info.get("anchor_ids", []) if str(v).lstrip("-").isdigit()],
            "source_region_id": region_id,
            "location": "unboxed" if region_type == "unboxed" else "bubble",
            "source_method": "manual_overlay_text_override",
            "text_translated": text_override.strip(),
            "overlay_rect": rect,
            "bounding_rect": rect,
            "matched_cluster_details": [{"bounding_rect": rect, "lines": [{"bbox": rect}]}],
        })
        represented_region_ids.add(region_id)
    if synthetic_clusters:
        clusters = [*clusters, *synthetic_clusters]
    represented_anchor_ids = {
        int(anchor_id)
        for cluster in clusters
        for anchor_id in cluster.get("matched_cluster", [])
    }
    # (cluster, text, bbox, bbox_source, layout, src_lc, font_path, is_split_rect, split_order, mask_centroid)
    prepared: list[tuple[dict, str, list[int], str, Layout, int, Path, bool, int, list | None]] = []

    for cluster in clusters:
        override = overlay_override_for_cluster(cluster, flood_info_by_anchor, overlay_overrides)
        default_text = str(cluster.get("text_translated") or "").strip()
        has_text_override = isinstance(override.get("text_override"), str)
        if has_text_override:
            text = str(override.get("text_override") or "").strip()
        else:
            text = default_text
        if not text:
            continue
        is_unboxed = cluster.get("location") == "unboxed"
        if is_unboxed:
            # LaMa 대상: 원본 TG bbox(unboxed_rect) 사용. flood_vis 없이도 올바른 위치에 그림
            unboxed_rect = cluster.get("unboxed_rect")
            bbox = [int(v) for v in unboxed_rect] if unboxed_rect and len(unboxed_rect) == 4 else cluster_bbox(cluster)
            bbox_source = "unboxed_rect"
        else:
            bbox, bbox_source = overlay_bbox_for_cluster(
                cluster,
                represented_anchor_ids,
                shared_groups,
                flood_info_by_anchor,
                split_region_by_anchor,
            )
        auto_bbox = list(bbox)
        auto_bbox_source = bbox_source
        override_rect = override.get("safe_text_rect")
        if isinstance(override_rect, list) and len(override_rect) >= 4:
            bbox = [int(v) for v in override_rect[:4]]
            bbox_source = "manual_overlay_safe_text_rect"
        # split rect는 split_order bias를 적용하고, safe rect는 이미 mask/centroid clue라 중앙정렬한다.
        is_safe_rect = "safe_text_rect" in bbox_source
        is_split_rect = "split_flood_region" in bbox_source or is_safe_rect
        # split_order: split flood region에만 bias 적용. safe_text_rect에는 적용하지 않는다.
        split_order = (
            _get_split_order_for_cluster(cluster, split_region_by_anchor, flood_info_by_anchor)
            if is_split_rect and not is_safe_rect
            else -1
        )
        src_lc = cluster_line_count(cluster)
        base_cluster_font_path = font_path_for_cluster(cluster, font_path)
        cluster_font_path = font_path_from_overlay_override(override, font_path, cluster)
        # mask_centroid / round_score: cluster의 첫 anchor id 기준으로 flood_info_by_anchor에서 추출
        # TG 중심 sub-rect는 이미 원본 TG center clue를 반영하므로 region centroid 보정을 다시 적용하지 않는다.
        cluster_anchor_ids = [int(aid) for aid in cluster.get("matched_cluster", [])]
        use_mask_centroid = "text_group_centered_sub_rect" not in bbox_source
        mask_centroid = None
        round_score = -1.0
        for _aid in cluster_anchor_ids:
            fi = flood_info_by_anchor.get(_aid, {})
            if use_mask_centroid and mask_centroid is None:
                mc = fi.get("mask_centroid")
                if mc is not None:
                    mask_centroid = mc
            if round_score < 0.0:
                qf = fi.get("quality_flags", [])
                rs = _parse_round_score(qf)
                if rs >= 0.0:
                    round_score = rs
            if mask_centroid is not None and round_score >= 0.0:
                break
        # integrated.json quality_flags fallback (regions.json 없을 때)
        if round_score < 0.0:
            for _aid in cluster_anchor_ids:
                rs = _parse_round_score(cluster.get("quality_flags", []))
                if rs >= 0.0:
                    round_score = rs
                    break
        fvs = should_force_vertical_short_interjection(cluster, text)
        fvt = should_try_vertical_title_layout(cluster, text)
        force_vertical = bool(override.get("force_vertical"))
        force_horizontal = bool(override.get("force_horizontal"))
        has_manual_layout_override = overlay_override_has_manual_layout(override)
        try:
            font_size_delta = int(override.get("font_size_delta", 0) or 0)
        except (TypeError, ValueError):
            font_size_delta = 0
        try:
            font_size_override = int(override.get("font_size", 0) or 0)
        except (TypeError, ValueError):
            font_size_override = 0
        split_cap_reference_layout = None
        if has_manual_layout_override:
            cap_text = default_text or text
            cap_is_safe_rect = "safe_text_rect" in auto_bbox_source
            cap_is_split_rect = "split_flood_region" in auto_bbox_source or cap_is_safe_rect
            cap_split_order = (
                _get_split_order_for_cluster(cluster, split_region_by_anchor, flood_info_by_anchor)
                if cap_is_split_rect and not cap_is_safe_rect
                else -1
            )
            split_cap_reference_layout = choose_layout(
                draw, cap_text, auto_bbox, (img_w, img_h), base_cluster_font_path,
                is_unboxed=is_unboxed, src_line_count=src_lc, is_split_rect=cap_is_split_rect,
                split_order=cap_split_order, mask_centroid=mask_centroid, round_score=round_score,
                force_vertical_short_interjection=fvs,
                force_vertical_title_layout=fvt,
            )
        explicit_override_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if font_size_override and has_text_override and len(explicit_override_lines) > 1:
            manual_inner = inner_rect_for(
                bbox,
                (img_w, img_h),
                is_unboxed=is_unboxed,
                is_split_rect=is_split_rect,
                split_order=split_order,
                mask_centroid=mask_centroid,
                round_score=round_score,
            )
            raw = layout_from_manual_lines_with_font_size(
                draw,
                explicit_override_lines,
                manual_inner,
                cluster_font_path,
                font_size_override,
                target_lines=len(explicit_override_lines),
            )
        else:
            raw = choose_layout(
                draw, text, bbox, (img_w, img_h), cluster_font_path,
                is_unboxed=is_unboxed, src_line_count=src_lc, is_split_rect=is_split_rect,
                split_order=split_order, mask_centroid=mask_centroid, round_score=round_score,
                force_vertical_short_interjection=fvs,
                force_vertical_title_layout=fvt,
                force_vertical_layout=force_vertical,
                force_horizontal_layout=force_horizontal,
                preserve_explicit_lines=has_text_override,
            )
        pre_manual_layout = raw
        if font_size_override and not (has_text_override and len(explicit_override_lines) > 1):
            raw = layout_with_font_size(draw, raw, cluster_font_path, font_size_override)
        if font_size_delta:
            raw = layout_with_font_size_delta(draw, raw, cluster_font_path, font_size_delta)
        has_manual_font_size_override = bool(font_size_override or font_size_delta)
        if not has_manual_font_size_override and should_soften_unboxed_tg_bbox_layout(cluster, bbox_source):
            softened_cap = softened_unboxed_tg_bbox_font_cap(raw.font_size)
            if softened_cap < raw.font_size:
                softened = choose_layout(
                    draw, text, bbox, (img_w, img_h), cluster_font_path,
                    max_font_size=softened_cap,
                    is_unboxed=is_unboxed, src_line_count=src_lc, is_split_rect=is_split_rect,
                    split_order=split_order, mask_centroid=mask_centroid, round_score=round_score,
                    force_vertical_short_interjection=fvs,
                    force_vertical_title_layout=fvt,
                    force_vertical_layout=force_vertical,
                    force_horizontal_layout=force_horizontal,
                    preserve_explicit_lines=has_text_override,
                )
                if softened is not None:
                    raw = softened
        if split_cap_reference_layout is not None:
            split_cap_font_size = split_cap_reference_layout.font_size
        elif (font_size_delta or font_size_override) and pre_manual_layout is not None:
            split_cap_font_size = pre_manual_layout.font_size
        else:
            split_cap_font_size = raw.font_size if raw is not None else 0
        prepared.append((cluster, text, bbox, bbox_source, raw, src_lc, cluster_font_path, is_split_rect, split_order, mask_centroid, round_score, fvs, fvt, font_size_delta, split_cap_font_size, has_manual_layout_override))

    # ── Split 버블 폰트 균일화 (2nd pass) ──────────────────────────────────────
    # 같은 flood region에서 분리된 버블(R1/R2 등)은 큰 font_size만 완만하게 누른다.
    # 소스 1: split_region_by_anchor (_flood_regions.json) — region_id 기준
    # 소스 2: flood_info_by_anchor (_regions.json)     — split_parent_rect 기준
    # 초기 font size 추정이 끝난 뒤, 그룹 내 최솟값의 120%를 상한으로 재배치한다.
    split_group_indices: dict[str, list[int]] = {}
    for idx, item in enumerate(prepared):
        c_i = item[0]
        layout_i = item[4]
        is_split_i = item[7]
        if layout_i is None:
            continue
        if c_i.get("location") == "unboxed":
            continue
        group_key: str | None = None
        for aid in c_i.get("matched_cluster", []):
            aid_i = int(aid)
            # 소스 1: split_region_by_anchor (flood_regions.json)
            info = split_region_by_anchor.get(aid_i) if split_region_by_anchor else None
            if info and info.get("region_id") is not None:
                group_key = f"flood:{info['region_id']}"
                break
            # 소스 2: flood_info_by_anchor (regions.json) — split_parent_rect 기준
            fi = flood_info_by_anchor.get(aid_i, {})
            spr = fi.get("split_parent_rect")
            if spr and len(spr) == 4:
                group_key = f"parent:{spr[0]},{spr[1]},{spr[2]},{spr[3]}"
                break
        if group_key:
            split_group_indices.setdefault(group_key, []).append(idx)

    for _key, indices in split_group_indices.items():
        if len(indices) < 2:
            continue
        adjustable_indices = [idx for idx in indices if not bool(prepared[idx][15])]
        if not adjustable_indices:
            continue
        min_fs = min(int(prepared[idx][14] or prepared[idx][4].font_size) for idx in indices)
        cap_fs = split_parent_font_cap(min_fs)
        for idx in adjustable_indices:
            (c_i, text_i, bbox_i, bsrc_i, layout_i,
             src_lc_i, fp_i, is_split_i, so_i, mc_i, rs_i, fvs_i, fvt_i,
             font_size_delta_i, split_cap_font_size_i, manual_layout_override_i) = prepared[idx]
            if font_size_delta_i:
                continue
            if manual_layout_override_i:
                continue
            if layout_i.font_size <= cap_fs:
                continue
            new_layout = choose_layout(
                draw, text_i, bbox_i, (img_w, img_h), fp_i,
                max_font_size=cap_fs,
                is_unboxed=False,
                src_line_count=src_lc_i,
                is_split_rect=is_split_i,
                split_order=so_i,
                mask_centroid=mc_i,
                round_score=rs_i,
                force_vertical_short_interjection=fvs_i,
                force_vertical_title_layout=fvt_i,
                force_horizontal_layout=bool(overlay_override_for_cluster(c_i, flood_info_by_anchor, overlay_overrides).get("force_horizontal")),
                strict_max_font_size=True,
            )
            if new_layout is not None:
                prepared[idx] = (c_i, text_i, bbox_i, bsrc_i, new_layout,
                                 src_lc_i, fp_i, is_split_i, so_i, mc_i, rs_i, fvs_i, fvt_i,
                                 font_size_delta_i, split_cap_font_size_i, manual_layout_override_i)

    return prepared


def restore_original_pixels(
    image: np.ndarray,
    original: np.ndarray,
    rect: list[int],
) -> np.ndarray:
    """원본 이미지에서 rect 영역 픽셀을 image에 복원."""
    img_h, img_w = image.shape[:2]
    x, y, w, h = [int(v) for v in rect]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(img_w, x + w), min(img_h, y + h)
    if x2 > x1 and y2 > y1:
        image[y1:y2, x1:x2] = original[y1:y2, x1:x2]
    return image


def restore_untranslated_regions(
    image: np.ndarray,
    original: np.ndarray | None,
    regions_json: Path | None,
    clusters: list[dict],
    suppressed_region_ids: set[int] | None = None,
) -> int:
    """OCR/integrated에서 빠진 masked region은 빈칸 방지용으로 원본을 복원한다."""
    if original is None or regions_json is None or not regions_json.exists():
        return 0

    try:
        data = json.loads(regions_json.read_text(encoding="utf-8"))
    except Exception:
        return 0

    covered_region_ids: set[int] = set()
    covered_anchor_ids: set[int] = set()
    for cluster in clusters:
        try:
            if cluster.get("source_region_id") is not None:
                covered_region_ids.add(int(cluster.get("source_region_id")))
        except (TypeError, ValueError):
            pass
        for anchor_id in cluster.get("matched_cluster") or []:
            try:
                covered_anchor_ids.add(int(anchor_id))
            except (TypeError, ValueError):
                continue

    suppressed_region_ids = suppressed_region_ids or set()
    restored = 0
    for region in data.get("regions", []):
        try:
            region_id = int(region.get("region_id"))
        except (TypeError, ValueError):
            region_id = -1
        if region_id in suppressed_region_ids:
            continue
        anchor_ids = set()
        for anchor_id in region.get("anchor_ids") or []:
            try:
                anchor_ids.add(int(anchor_id))
            except (TypeError, ValueError):
                continue

        if region_id in covered_region_ids or (anchor_ids and bool(anchor_ids & covered_anchor_ids)):
            continue

        rect = region.get("bubble_rect") or region.get("unboxed_rect") or region.get("erase_rect")
        if rect and len(rect) == 4:
            restore_original_pixels(image, original, rect)
            restored += 1

    return restored


def _is_short_unboxed_label_cluster(cluster: dict) -> bool:
    """이미 integrated에 들어온 작고 짧은 unboxed 라벨은 번역 렌더 대신 원본을 보존한다."""
    if cluster.get("location") != "unboxed":
        return False
    details = cluster.get("matched_cluster_details") or []
    if len(details) != 1:
        return False
    source_text = re.sub(r"\s+", "", str(details[0].get("text", "")))
    if not source_text or len(source_text) > 5:
        return False
    jp = sum(1 for ch in source_text if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    if jp == 0:
        return False
    lines = details[0].get("lines") or []
    if len(lines) != 1:
        return False
    rect = details[0].get("bounding_rect") or lines[0].get("bbox")
    if not rect or len(rect) != 4:
        return False
    if _is_likely_unboxed_dialogue_text(cluster, source_text, rect):
        return False
    _x, _y, w, h = [int(v) for v in rect[:4]]
    return w * h <= 4500 and max(w, h) <= 120


def _jp_text_len(text: str) -> int:
    return sum(1 for ch in text if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")


def _has_dialogue_punctuation(text: str) -> bool:
    return bool(re.search(r"[~〜?？!！…]|\.{2,}|･･･|・・", text or ""))


def _short_unboxed_vertical_dialogue_score(
    *,
    jp_len: int,
    width: int,
    height: int,
    has_punct: bool,
    is_dark: bool,
) -> int:
    """짧은 1줄 U region 이 일반 세로 대사인지 shape 비율로 점수화한다."""
    if jp_len <= 0 or width <= 0 or height <= 0:
        return 0

    aspect = height / max(width, 1)
    aspect_per_char = aspect / max(jp_len, 1)
    char_per_aspect = jp_len / max(aspect, 0.01)
    char_area = (width * height) / max(jp_len, 1)
    max_dim = max(width, height)

    if aspect < 0.9 or width >= 120 or max_dim >= 700 or char_area >= 30000:
        return 0

    score = 0
    if width <= 80:
        score += 1
    if aspect >= 2.0:
        score += 2
    if aspect >= 3.0:
        score += 1
    if 0.75 <= aspect_per_char <= 1.7:
        score += 3
    elif has_punct and 0.6 <= aspect_per_char <= 2.7:
        score += 2
    if 0.35 <= char_per_aspect <= 1.35:
        score += 2
    if char_area <= 7000:
        score += 2
    if has_punct:
        score += 1
    if is_dark:
        score += 1
    return score


def _is_likely_unboxed_dialogue_text(cluster: dict, source_text: str, rect: list[int]) -> bool:
    """짧은 unboxed 후보 중 실제 대사/본문으로 보이는 케이스를 판정한다."""
    if not rect or len(rect) != 4:
        return False
    _x, _y, w, h = [int(v) for v in rect[:4]]
    if w <= 0 or h <= 0:
        return False

    jp_len = _jp_text_len(source_text)
    if jp_len <= 0:
        return False

    aspect = h / max(w, 1)
    aspect_per_char = aspect / max(jp_len, 1)
    char_per_aspect = jp_len / max(aspect, 0.01)
    char_area = (w * h) / max(jp_len, 1)
    max_dim = max(w, h)
    flag_text = " ".join(str(f) for f in (cluster.get("quality_flags") or []))
    is_dark = "dark_background" in flag_text or "dark_flood_candidate" in flag_text
    has_punct = _has_dialogue_punctuation(source_text)
    shape_score = _short_unboxed_vertical_dialogue_score(
        jp_len=jp_len,
        width=w,
        height=h,
        has_punct=has_punct,
        is_dark=is_dark,
    )

    # 가로형 U region과 과대 bbox는 장식/효과음/비일반 페이지일 가능성이 높다.
    if aspect < 0.9 or w >= 120 or max_dim >= 700 or char_area >= 30000:
        return False

    # 검은 배경 세로 대사는 일반 LaMa/렌더 대상에 더 가깝다.
    if is_dark and w <= 90 and h <= 260 and aspect >= 2.2:
        return True

    # 1~2글자 세로 대사는 폭이 매우 좁고, 말줄임표/감탄/물음 등의 대사 부호가 있거나
    # 글자 셀이 세로 대사처럼 충분히 길게 쌓일 때만 살린다.
    if jp_len <= 2:
        if w <= 52 and aspect >= 2.0 and char_area <= 5200:
            return has_punct or aspect_per_char >= 1.0 or char_per_aspect <= 1.0
        return False

    if has_punct and w <= 60 and h <= 220 and aspect >= 2.4 and char_area <= 6000:
        return True

    if shape_score >= 8:
        return True

    # 3글자 이상은 글자당 세로 셀이 1 근처이고 폭이 좁으면 본문 세로 텍스트로 본다.
    return (
        w <= 80
        and max_dim <= 450
        and char_area <= 7000
        and 0.75 <= aspect_per_char <= 1.35
        and 0.7 <= char_per_aspect <= 1.35
    )


def _should_preserve_original_unboxed_cluster(cluster: dict) -> bool:
    """원본 보존으로 처리할 unboxed 클러스터인지 판단한다."""
    return False


def _compact_letters_and_digits(text: str) -> str:
    return "".join(ch.lower() for ch in text or "" if ch.isalnum())


def _has_japanese_or_korean(text: str) -> bool:
    return bool(re.search(r"[가-힣ぁ-ゟァ-ヿ一-鿿]", text or ""))


def _is_symbol_english_text(text: str) -> bool:
    """라틴 문자와 장식 기호만으로 이뤄진 보존 대상 텍스트인지 판단한다."""
    compact = _compact_letters_and_digits(text)
    if not compact or not re.search(r"[a-z]", compact):
        return False
    if _has_japanese_or_korean(text):
        return False
    return all(("a" <= ch <= "z") or ch.isdigit() for ch in compact)


def _should_preserve_original_symbol_english_cluster(cluster: dict) -> bool:
    """bubble이라도 순수 영문/기호 title은 번역 렌더 대신 원본을 보존한다."""
    if cluster.get("location") != "bubble":
        return False
    if len(cluster.get("matched_cluster") or []) != 1:
        return False

    source_text = str(cluster.get("text") or "")
    translated_text = str(cluster.get("text_translated") or "")
    if not (_is_symbol_english_text(source_text) and _is_symbol_english_text(translated_text)):
        return False

    source_compact = _compact_letters_and_digits(source_text)
    translated_compact = _compact_letters_and_digits(translated_text)
    return bool(source_compact and source_compact == translated_compact)


def _should_preserve_failed_dark_bubble_cluster(cluster: dict) -> bool:
    """dark flood 검증 실패 후 edge rescue된 컬러 라벨은 원본을 보존한다."""
    if cluster.get("location") != "bubble":
        return False
    flags = {str(flag) for flag in (cluster.get("quality_flags") or [])}
    return (
        "dark_background" in flags
        and "dark_flood_failed_validation" in flags
        and (
            cluster.get("source_method") == "edge_barrier_rescue"
            or "edge_barrier_rescue" in flags
        )
    )


def _should_preserve_original_cluster(cluster: dict) -> bool:
    """번역 렌더를 건너뛰고 원본 픽셀을 복원할 클러스터인지 판단한다."""
    if cluster.get("preserve_original") is True:
        return True
    return (
        _should_preserve_original_unboxed_cluster(cluster)
        or _should_preserve_original_symbol_english_cluster(cluster)
        or _should_preserve_failed_dark_bubble_cluster(cluster)
    )


def restore_rect_for_preserved_cluster(cluster: dict, fallback_bbox: list[int]) -> list[int]:
    """원본 보존 클러스터의 복원 영역을 결정한다."""
    rect = (
        cluster.get("erase_rect")
        or cluster.get("unboxed_rect")
        or cluster.get("source_region_rect")
        or cluster.get("bounding_rect")
        or fallback_bbox
    )
    return [int(v) for v in rect[:4]]


def apply_text_overlay(
    masked_jpg: Path,
    integrated_json: Path,
    output_path: Path,
    flood_vis_json: Path | None = None,
    flood_regions_json: Path | None = None,
    regions_json: Path | None = None,
    font_path: Path = FONT_PATH,
    original_image_path: Path | None = None,
    tracking_offset: int | None = None,
    overlay_guidance_path: Path | None = None,
    overlay_layout_output: Path | None = None,
    debug_vis_image: Path | None = None,
    debug_merge_output: Path | None = None,
    restore_state_path: Path | None = None,
) -> int:
    global _GLOBAL_TRACKING_OFFSET
    _GLOBAL_TRACKING_OFFSET = tracking_offset if tracking_offset is not None else 0

    image = cv2.imread(str(masked_jpg))
    if image is None:
        raise FileNotFoundError(f"이미지 로드 실패: {masked_jpg}")

    original_image: np.ndarray | None = None
    if original_image_path is not None and original_image_path.exists():
        original_image = cv2.imread(str(original_image_path))

    clusters = load_clusters(integrated_json)
    overlay_guidance = load_overlay_guidance(overlay_guidance_path)
    flood_info = build_flood_info_by_anchor(flood_vis_json) if flood_vis_json else {}
    shared_groups = build_shared_flood_groups(flood_vis_json) if flood_vis_json else {}
    split_region_by_anchor = (
        build_split_region_info_by_anchor(flood_regions_json)
        if flood_regions_json
        else {}
    )
    # 구형 regions.json fallback: flood_vis.json 없을 때 bubble_rect 정보 보완
    if not flood_info and regions_json:
        flood_info = build_flood_info_from_regions_json(regions_json)
    elif regions_json and regions_json.exists():
        # regions_json이 있다면 mask_centroid 등의 풍부한 정보를 추가 연동
        regions_info = build_flood_info_from_regions_json(regions_json)
        for anchor_id, info in regions_info.items():
            if anchor_id in flood_info:
                if "mask_centroid" in info and info["mask_centroid"] is not None:
                    flood_info[anchor_id]["mask_centroid"] = info["mask_centroid"]
            else:
                flood_info[anchor_id] = info
    # shared_groups가 없으면 regions.json의 anchor_ids로 동일 버블 그룹핑 보완
    if not shared_groups and regions_json:
        shared_groups = build_shared_flood_groups_from_regions_json(regions_json)
    region_info_by_id = build_region_info_by_id_from_regions_json(regions_json)

    restored_untranslated = restore_untranslated_regions(
        image,
        original_image,
        regions_json,
        clusters,
        overlay_suppressed_region_ids(overlay_guidance),
    )
    # 배경 복원 탭에서 확정한 패치(PNG)를 마스크 내부에만 합성한다. masked.jpg를 다시 저장하지 않는다.
    apply_restore_patches(image, restore_state_path)
    applied_eraser_count = apply_overlay_eraser_rects(image, overlay_eraser_rects(overlay_guidance))

    prepared = prepare_page_layouts(
        image,
        clusters,
        shared_groups=shared_groups,
        flood_info_by_anchor=flood_info,
        split_region_by_anchor=split_region_by_anchor,
        region_info_by_id=region_info_by_id,
        font_path=font_path,
        overlay_guidance=overlay_guidance,
    )
    if overlay_layout_output is not None:
        overlay_layout_output.parent.mkdir(parents=True, exist_ok=True)
        overlay_layout_output.write_text(
            json.dumps(overlay_layout_summary(prepared, flood_info), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    rendered_count = 0
    preserved_original_count = 0
    overlay_overrides = overlay_region_overrides(overlay_guidance or {})
    # 텍스트 블록은 PIL(RGB) 페이지 하나에 이어서 그린다. numpy(BGR)가 필요한 원본 복원 때만 되돌린다.
    page_pil: Image.Image | None = None
    for cluster, text, bbox, bbox_source, layout, _src_lc, cluster_font_path, _is_split_rect, _split_order, *_ in prepared:
        override = overlay_override_for_cluster(cluster, flood_info, overlay_overrides)
        if (
            _should_preserve_original_cluster(cluster)
            and not overlay_override_disables_automatic_fallback(override)
        ):
            restore_rect = restore_rect_for_preserved_cluster(cluster, bbox)
            if original_image is not None and len(restore_rect) == 4:
                if page_pil is not None:
                    image = cv2.cvtColor(np.array(page_pil), cv2.COLOR_RGB2BGR)
                    page_pil = None
                image = restore_original_pixels(image, original_image, restore_rect)
                preserved_original_count += 1
            continue
        is_unboxed = cluster.get("location") == "unboxed"
        _flags_str = " ".join(str(f) for f in (cluster.get("quality_flags") or []))
        is_dark_flood = (
            cluster.get("source_method") == "dark_flood"
            or "dark_flood_success" in _flags_str
        )
        has_dark_background = "dark_background" in (cluster.get("quality_flags") or [])
        # bbox_fallback(LaMa 미적용)은 배경이 미정리 → 90%, LaMa 적용 unboxed → 80%, bubble → 50%
        is_bbox_fallback = cluster.get("source_method") == "bbox_fallback"
        if has_dark_background or is_dark_flood:
            bg_alpha = UNBOXED_DARK_BG_ALPHA
        elif is_unboxed:
            bg_alpha = UNBOXED_FALLBACK_BG_ALPHA if is_bbox_fallback else UNBOXED_BG_ALPHA
        else:
            bg_alpha = BUBBLE_BG_ALPHA
        if has_dark_background or is_dark_flood:
            bg_color  = (0, 0, 0)        # 검은 반투명 박스
            text_fill = (0, 0, 0)
            text_stroke_width = DARK_TEXT_STROKE_WIDTH
            text_stroke_fill = (255, 255, 255)
        else:
            bg_color  = (255, 255, 255)
            text_fill = (0, 0, 0)
            text_stroke_width = TEXT_STROKE_WIDTH
            text_stroke_fill = (255, 255, 255)
        text_stroke_width = overlay_override_stroke_width(override, text_stroke_width)
        rotation_deg = overlay_override_rotation_deg(override)
        font_weight = overlay_override_font_weight(override)
        text_align = overlay_override_text_align(override)
        if page_pil is None:
            page_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        page_pil, layout = draw_text_block_pil(
            page_pil, text, bbox, cluster_font_path, layout=layout,
            is_unboxed=is_unboxed, bg_alpha=bg_alpha, text_fill=text_fill, bg_color=bg_color,
            text_stroke_width=text_stroke_width, text_stroke_fill=text_stroke_fill,
            rotation_deg=rotation_deg,
            font_weight=font_weight,
            text_align=text_align,
        )
        rendered_count += 1
    if page_pil is not None:
        image = cv2.cvtColor(np.array(page_pil), cv2.COLOR_RGB2BGR)

    cv2.imwrite(str(output_path), image)
    if restored_untranslated:
        print(f"  ↩️ 미번역/누락 region {restored_untranslated}개 — 원본 복원")
    if preserved_original_count:
        print(f"  ↩️ 원본 보존 클러스터 {preserved_original_count}개 — 원본 복원")
    if applied_eraser_count:
        print(f"  🧽 수동 지우개 rect {applied_eraser_count}개 적용")

    # debug merge: vis 이미지에 centroid(파란 dot) 를 그린 뒤 final과 나란히 저장
    if debug_vis_image is not None and debug_merge_output is not None and debug_vis_image.exists():
        vis_bgr = cv2.imread(str(debug_vis_image))
        if vis_bgr is not None:
            _draw_layout_debug(vis_bgr, prepared, flood_info)
            _save_merge_image(vis_bgr, image, debug_merge_output)

    return rendered_count


def _draw_layout_debug(
    vis: np.ndarray,
    prepared: list,
    flood_info_by_anchor: dict,
) -> None:
    """vis 이미지 위에 각 클러스터의 centroid(파란 dot)를 그린다.

    centroid는 flood_info_by_anchor의 mask_centroid 우선, 없으면 bbox 기하 중심으로 fallback.
    """
    for item in prepared:
        cluster, _text, bbox, _bbox_source, layout, _src_lc, _fp, _is_split, _split_order, *_ = item
        # centroid 그리기 (파란색) — flood_info mask_centroid 우선, 없으면 bbox 기하 중심 fallback
        cx, cy = None, None
        anchor_ids = [int(a) for a in cluster.get("matched_cluster", [])]
        for aid in anchor_ids:
            mc = flood_info_by_anchor.get(aid, {}).get("mask_centroid")
            if mc is not None:
                cx, cy = int(mc[0]), int(mc[1])
                break
        if cx is None:
            bx, by, bw, bh = [int(v) for v in bbox]
            cx, cy = bx + bw // 2, by + bh // 2
        cv2.circle(vis, (cx, cy), 12, (255, 80, 0), -1)   # 파란 채운 원 (BGR: 파랑)
        cv2.circle(vis, (cx, cy), 12, (255, 255, 255), 2)  # 흰 테두리


def _save_merge_image(left: np.ndarray, right: np.ndarray, out_path: Path) -> None:
    """left/right 이미지를 높이 기준으로 맞추고 가로로 이어 붙여 저장한다."""
    h = max(left.shape[0], right.shape[0])

    def _pad(img: np.ndarray) -> np.ndarray:
        pad = h - img.shape[0]
        return img if pad <= 0 else np.vstack([
            img, np.full((pad, img.shape[1], 3), 255, dtype=np.uint8)
        ])

    gap = np.full((h, 12, 3), 220, dtype=np.uint8)
    merged = np.hstack([_pad(left), gap, _pad(right)])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), merged)


def main() -> None:
    parser = argparse.ArgumentParser(description="텍스트 오버레이 렌더링")
    parser.add_argument("masked_jpg", help="마스킹된 이미지 경로")
    parser.add_argument("integrated_json", help="integrated JSON 경로")
    parser.add_argument("clusters_json", help="cv_clusters JSON 경로 (미사용, 호환성 유지)")
    parser.add_argument("output_path", help="출력 이미지 경로")
    parser.add_argument("font_path", nargs="?", default=str(FONT_PATH), help="폰트 파일 경로")
    parser.add_argument("--flood_vis_json", default=None, help="flood_vis JSON 경로 (선택)")
    parser.add_argument("--flood_regions_json", default=None, help="split된 flood region JSON 경로 (선택)")
    parser.add_argument("--original_image", default=None, help="원본 이미지 경로 — 손글씨 영역 복원용 (선택)")
    parser.add_argument("--regions_json", default=None, help="regions JSON 경로 (무게중심 연동용)")
    parser.add_argument("--tracking-offset", type=int, default=None, help="자간 추가 오프셋 (px)")
    parser.add_argument("--overlay-guidance", default=None, help="수동 overlay guidance JSON 경로")
    parser.add_argument("--overlay-layout-output", default=None, help="실제 렌더 layout JSON 출력 경로")
    parser.add_argument("--restore-state", default=None, help="배경 복원 패치 state.json 경로 (선택)")
    args = parser.parse_args()

    flood_vis = Path(args.flood_vis_json) if args.flood_vis_json else None
    flood_regions = Path(args.flood_regions_json) if args.flood_regions_json else None
    original_image = Path(args.original_image) if args.original_image else None
    regions = Path(args.regions_json) if args.regions_json else None
    count = apply_text_overlay(
        Path(args.masked_jpg),
        Path(args.integrated_json),
        Path(args.output_path),
        flood_vis_json=flood_vis,
        flood_regions_json=flood_regions,
        regions_json=regions,
        font_path=Path(args.font_path),
        original_image_path=original_image,
        tracking_offset=args.tracking_offset,
        overlay_guidance_path=Path(args.overlay_guidance) if args.overlay_guidance else None,
        overlay_layout_output=Path(args.overlay_layout_output) if args.overlay_layout_output else None,
        restore_state_path=Path(args.restore_state) if args.restore_state else None,
    )
    print(f"렌더링 완료: {count}개")


if __name__ == "__main__":
    main()
