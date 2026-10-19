# TextFragment 기준 글꼴 스타일 판정을 계산하는 유틸리티
from __future__ import annotations

import math
from typing import Any, Iterable

import cv2
import numpy as np

try:
    from pipeline.geometry import as_rect
    from pipeline.unboxed_grouping import text_group_text
except ImportError:
    from geometry import as_rect
    from unboxed_grouping import text_group_text


def kana_kanji_count(text: str) -> int:
    """가나·한자만 센다. 숫자·영문만 있는 TG(쪽번호, 효과음 로마자 등)는 0이다."""
    return sum(1 for ch in str(text or "") if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")


# 굵은 글씨라도 가나·한자가 이만큼 이어지면 효과음보다 강조 대사로 본다.
# Ore_2 실측: 수동 Ubox로 다시 지정한 굵은 대사는 모두 5자 이상(今日は一切, やっぱりなあ… 등),
# 같은 규칙으로 제외돼 그대로 둔 효과음·잡음은 대부분 3자 이하(シーン, 襲来, 22 등)였다.
HEAVY_DIALOGUE_MIN_KANA_KANJI = 5


def japanese_char_count(text: str) -> int:
    """일본어 글자 수를 세고, 일본어가 없으면 공백 제거 길이를 사용한다."""
    compact = "".join(ch for ch in str(text or "") if not ch.isspace())
    jp = sum(1 for ch in compact if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    return jp or len(compact)


def _iter_text_fragments(group: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for line in group.get("lines") or []:
        fragments = line.get("fragments") or []
        if fragments:
            for fragment in fragments:
                if fragment.get("bbox"):
                    yield fragment
        elif line.get("bbox"):
            yield {"text": line.get("text", ""), "bbox": line.get("bbox")}


def _font_proxy(rect: list[int], char_count: int) -> float:
    _x, _y, w, h = as_rect(rect)
    if w <= 0 or h <= 0:
        return 0.0
    chars = max(1, int(char_count))
    long_axis = float(max(w, h))
    short_axis = float(min(w, h))
    long_per_char = long_axis / chars
    area_per_char = math.sqrt(max(1.0, float(w * h) / chars))
    return max(short_axis, long_per_char, area_per_char)


def _ink_metrics(gray: np.ndarray, rect: list[int]) -> tuple[float, float, int]:
    x, y, w, h = as_rect(rect)
    img_h, img_w = gray.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)
    if x2 <= x1 or y2 <= y1:
        return 0.0, 0.0, 0

    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0, 0.0, 0

    otsu, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold = int(max(80, min(170, float(otsu))))
    dark = roi < threshold
    dark_pixels = int(np.count_nonzero(dark))
    density = dark_pixels / max(1, int(roi.size))
    if dark_pixels == 0:
        return density, 0.0, 0

    ys, xs = np.where(dark)
    ink_area = int((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1))
    fill = dark_pixels / max(1, ink_area)
    return density, fill, dark_pixels


def compute_text_group_style(image: np.ndarray, group: dict[str, Any]) -> dict[str, Any]:
    """TG의 TF 기반 font/ink metric을 char-weighted로 계산한다."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    fragments = []
    for fragment in _iter_text_fragments(group):
        rect = as_rect(fragment.get("bbox", [0, 0, 0, 0]))
        chars = japanese_char_count(str(fragment.get("text", "")))
        proxy = _font_proxy(rect, chars)
        if proxy <= 0:
            continue
        density, fill, dark_pixels = _ink_metrics(gray, rect)
        fragments.append({
            "text": str(fragment.get("text", "")),
            "bbox": rect,
            "char_count": max(1, chars),
            "font_proxy": proxy,
            "dark_density": density,
            "ink_fill": fill,
            "dark_pixels": dark_pixels,
            "aspect": rect[2] / max(1, rect[3]),
        })

    if not fragments:
        rect = as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0])))
        chars = japanese_char_count(text_group_text(group))
        proxy = _font_proxy(rect, chars)
        density, fill, dark_pixels = _ink_metrics(gray, rect)
        fragments = [{
            "text": text_group_text(group),
            "bbox": rect,
            "char_count": max(1, chars),
            "font_proxy": proxy,
            "dark_density": density,
            "ink_fill": fill,
            "dark_pixels": dark_pixels,
            "aspect": rect[2] / max(1, rect[3]),
        }]

    font_values = np.array([f["font_proxy"] for f in fragments], dtype=float)
    if len(font_values) >= 2:
        q75 = float(np.percentile(font_values, 75))
        main = [f for f in fragments if f["font_proxy"] >= max(6.0, q75 * 0.60)]
        if main:
            fragments = main

    weights = np.array([max(1, int(f["char_count"])) for f in fragments], dtype=float)
    total_chars = int(np.sum(weights))

    def weighted(key: str) -> float:
        return float(np.average([float(f[key]) for f in fragments], weights=weights))

    rect = as_rect(group.get("tight_rect", group.get("bounding_rect", [0, 0, 0, 0])))
    return {
        "char_count": total_chars,
        "kana_kanji_count": kana_kanji_count(text_group_text(group)),
        "tf_count": len(fragments),
        "font_proxy": weighted("font_proxy"),
        "dark_density": weighted("dark_density"),
        "ink_fill": weighted("ink_fill"),
        "dark_pixels": int(sum(int(f["dark_pixels"]) for f in fragments)),
        "aspect": rect[2] / max(1, rect[3]),
        "bbox": rect,
        "orientation": str(group.get("orientation", "")),
        "page_long_side": float(max(gray.shape[:2])),
        "fragments": fragments,
    }


def compute_page_style_baseline(image: np.ndarray, groups: list[dict[str, Any]]) -> dict[str, float]:
    """페이지 내 보통 대사에 가까운 TF metric 중앙값을 기준선으로 잡는다."""
    styles = [compute_text_group_style(image, group) for group in groups]
    candidates = [
        s for s in styles
        if s["char_count"] >= 2
        and 0.03 <= s["dark_density"] <= 0.55
        and s["font_proxy"] >= 6
        and s["ink_fill"] > 0
    ]
    if not candidates:
        candidates = [s for s in styles if s["font_proxy"] > 0]

    font_values = np.array([s["font_proxy"] for s in candidates], dtype=float)
    if len(font_values) >= 5:
        lo, hi = np.percentile(font_values, [20, 75])
        font_pool = font_values[(font_values >= lo) & (font_values <= hi)]
        if len(font_pool) == 0:
            font_pool = font_values
    else:
        font_pool = font_values

    density_values = np.array([s["dark_density"] for s in candidates if s["dark_density"] > 0], dtype=float)
    fill_values = np.array([s["ink_fill"] for s in candidates if s["ink_fill"] > 0], dtype=float)

    return {
        "font_proxy": float(np.median(font_pool)) if len(font_pool) else 1.0,
        "dark_density": float(np.median(density_values)) if len(density_values) else 0.18,
        "ink_fill": float(np.median(fill_values)) if len(fill_values) else 0.35,
    }


def classify_text_group_style(style: dict[str, Any], baseline: dict[str, float]) -> dict[str, Any]:
    """TF font/ink metric으로 unboxed OCR 포함 여부를 보수적으로 판정한다."""
    base_font = max(1.0, float(baseline.get("font_proxy", 1.0)))
    base_density = max(0.001, float(baseline.get("dark_density", 0.18)))
    base_fill = max(0.001, float(baseline.get("ink_fill", 0.35)))

    font_ratio = float(style.get("font_proxy", 0.0)) / base_font
    font_page_ratio = float(style.get("font_proxy", 0.0)) / max(1.0, float(style.get("page_long_side", 1.0)))
    density_rel = float(style.get("dark_density", 0.0)) / base_density
    fill_rel = float(style.get("ink_fill", 0.0)) / base_fill
    aspect = float(style.get("aspect", 0.0))
    char_count = int(style.get("char_count", 0) or 0)
    long_japanese = int(style.get("kana_kanji_count", 0) or 0) >= HEAVY_DIALOGUE_MIN_KANA_KANJI

    style_class = "normal"
    include_policy = "include"
    review_tags: list[str] = []
    quality_flags: list[str] = []

    if aspect >= 1.15 and (font_ratio >= 1.10 or font_page_ratio >= 0.040):
        style_class = "landscape_irregular"
        include_policy = "exclude"
        review_tags.append("style_landscape_irregular")
    elif font_ratio >= 1.45 and density_rel <= 0.55 and fill_rel <= 0.65:
        style_class = "large_sparse_efx"
        include_policy = "exclude"
        review_tags.append("style_large_sparse_efx")
    elif font_ratio >= 1.45 and density_rel >= 1.45 and fill_rel >= 1.15 and not long_japanese:
        style_class = "large_heavy_efx"
        include_policy = "exclude"
        review_tags.append("style_large_heavy_efx")
    elif font_ratio >= 1.45:
        style_class = "large_normal"
        quality_flags.append("style_large_normal")
    elif font_ratio >= 1.08 and density_rel >= 1.18 and fill_rel >= 1.02 and char_count <= 10 and not long_japanese:
        style_class = "large_heavy"
        include_policy = "exclude"
        review_tags.append("style_large_heavy")
    elif font_ratio >= 1.08 and density_rel >= 1.18 and fill_rel >= 1.02:
        style_class = "large_heavy_dialogue"
        quality_flags.append("style_large_heavy_dialogue")
    elif font_ratio <= 0.90 and density_rel <= 0.92 and fill_rel <= 0.98:
        if char_count <= 10:
            style_class = "small_light_handwritten"
            include_policy = "exclude"
            review_tags.append("style_small_light_handwritten")
        else:
            style_class = "small_light_dialogue"
            quality_flags.append("style_small_light_dialogue")
    elif font_ratio <= 0.78:
        style_class = "small_font_review"
        include_policy = "review"
        review_tags.append("style_small_font_review")
    elif density_rel <= 0.78 and fill_rel <= 0.78 and font_ratio >= 1.15:
        style_class = "low_ink_handwritten"
        include_policy = "exclude"
        review_tags.append("style_low_ink_handwritten")
    elif density_rel >= 1.35 and fill_rel >= 1.15:
        style_class = "heavy_or_single_glyph"
        include_policy = "review"
        review_tags.append("style_heavy_or_single_glyph")

    quality_flags.extend([
        f"style_class={style_class}",
        f"font_ratio={font_ratio:.2f}",
        f"font_page_ratio={font_page_ratio:.3f}",
        f"ink_density_rel={density_rel:.2f}",
        f"ink_fill_rel={fill_rel:.2f}",
    ])

    return {
        **style,
        "style_class": style_class,
        "include_policy": include_policy,
        "font_ratio": font_ratio,
        "font_page_ratio": font_page_ratio,
        "ink_density_rel": density_rel,
        "ink_fill_rel": fill_rel,
        "quality_flags": quality_flags,
        "review_tags": review_tags,
    }


def aggregate_style_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """여러 TG가 한 region으로 합쳐질 때 글자 수 기준으로 스타일 힌트를 합친다."""
    if not decisions:
        return {
            "style_class": "unknown",
            "include_policy": "include",
            "quality_flags": [],
            "review_tags": [],
        }
    weights = np.array([max(1, int(d.get("char_count", 1))) for d in decisions], dtype=float)

    def weighted(key: str) -> float:
        return float(np.average([float(d.get(key, 0.0)) for d in decisions], weights=weights))

    policies = [str(d.get("include_policy", "include")) for d in decisions]
    style_class = max(
        (str(d.get("style_class", "normal")) for d in decisions),
        key=lambda cls: sum(max(1, int(d.get("char_count", 1))) for d in decisions if d.get("style_class") == cls),
    )
    include_policy = "exclude" if all(p == "exclude" for p in policies) else ("review" if any(p != "include" for p in policies) else "include")
    quality_flags = [
        f"cluster_style_class={style_class}",
        f"cluster_font_ratio={weighted('font_ratio'):.2f}",
        f"cluster_ink_density_rel={weighted('ink_density_rel'):.2f}",
        f"cluster_ink_fill_rel={weighted('ink_fill_rel'):.2f}",
    ]
    review_tags = []
    for decision in decisions:
        review_tags.extend(str(tag) for tag in decision.get("review_tags", []))
    return {
        "style_class": style_class,
        "include_policy": include_policy,
        "quality_flags": quality_flags,
        "review_tags": list(dict.fromkeys(review_tags)),
    }
