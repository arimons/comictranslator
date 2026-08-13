# 말풍선 후보 판정에 쓰는 텍스트 기반 필터 정책
from __future__ import annotations

from typing import Any

try:
    from pipeline.geometry import as_rect
except ImportError:
    from geometry import as_rect


JP_PUNCT = frozenset("\u30fb\u3002\u3001\u300c\u300d\u300e\u300f\u3010\u3011\u3014\u3015\u2026\u2025\u301c\uff5e\uff01\uff1f\uff1a\uff1b\u30fc\u2015\u2014")
FORBIDDEN_TEXT_PATTERNS = {
    "rawnet",
    "dlrawnet",
    "netradl",
}


def is_forbidden_text(text: str) -> bool:
    """출처/워터마크 텍스트를 정규화해서 판별한다."""
    normalized = "".join(ch.lower() for ch in str(text or "") if ch.isalnum())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in FORBIDDEN_TEXT_PATTERNS)


def is_japanese_char(ch: str) -> bool:
    """히라가나/카타카나/한자 문자인지 판별한다. 구두점은 제외한다."""
    if ch in JP_PUNCT:
        return False
    return "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff"


def text_stats(text: str) -> dict[str, int]:
    """텍스트에서 일본어/영숫자/기호 문자 수를 집계한다."""
    jp = sum(1 for ch in text if is_japanese_char(ch))
    alnum = sum(1 for ch in text if ch.isalnum())
    symbols = sum(1 for ch in text if not ch.isspace() and not ch.isalnum() and not is_japanese_char(ch))
    return {"jp_chars": jp, "alnum_chars": alnum, "symbol_chars": symbols}


def cluster_text(cluster: dict[str, Any]) -> str:
    if cluster.get("text"):
        return str(cluster.get("text", ""))
    return "".join(line.get("text", "") for line in cluster.get("lines", []))


def strip_and_stats(text: str) -> tuple[str, dict[str, int]]:
    """공백 제거 + text_stats 계산을 한 번에 수행한다."""
    stripped = "".join(ch for ch in text if not ch.isspace())
    return stripped, text_stats(stripped)


def is_symbol_only_text(text: str) -> bool:
    """기호/공백만으로 이루어진 텍스트를 판별한다."""
    if is_forbidden_text(text):
        return True
    _, stats = strip_and_stats(text)
    return stats["jp_chars"] == 0 and stats["alnum_chars"] == 0


def is_short_non_japanese_noise_text(text: str) -> bool:
    """일본어 없이 짧은 숫자/영문/기호만 있는 CV 노이즈인지 판단한다."""
    if is_forbidden_text(text):
        return True
    stripped, stats = strip_and_stats(text)
    if not stripped:
        return True
    return stats["jp_chars"] == 0 and len(stripped) <= 5


def is_english_only_text(text: str) -> bool:
    """일본어 글자가 전혀 없고 영숫자가 있는 텍스트인지 판단한다."""
    if is_forbidden_text(text):
        return True
    stripped, stats = strip_and_stats(text)
    if not stripped:
        return False
    return stats["jp_chars"] == 0 and stats["alnum_chars"] > 0


def is_large_single_glyph_sfx_like(cluster: dict[str, Any], rect: list[int]) -> bool:
    """큰 단일 일본어 glyph는 효과음/손글씨 후보로 보고 flood 이전에 제외한다."""
    stripped, stats = strip_and_stats(cluster_text(cluster))
    lines = cluster.get("lines") or []
    if len(stripped) != 1 or stats["jp_chars"] != 1:
        return False
    if stats["alnum_chars"] > stats["jp_chars"] or stats["symbol_chars"] > 0:
        return False
    if len(lines) > 1:
        return False
    _x, _y, w, h = as_rect(rect)
    area = w * h
    return area >= 3500 and max(w, h) >= 55


def is_large_short_kana_sfx_like(cluster: dict[str, Any], rect: list[int]) -> bool:
    """큰 짧은 kana TG는 효과음/손글씨 후보로 보고 flood/OCR 이전에 제외한다."""
    stripped, stats = strip_and_stats(cluster_text(cluster))
    if not (2 <= len(stripped) <= 4):
        return False
    kana = sum(1 for ch in stripped if "぀" <= ch <= "ヿ")
    if kana != len(stripped) or stats["symbol_chars"] > 0:
        return False
    lines = cluster.get("lines") or []
    if not lines or len(lines) > 3:
        return False
    line_texts = [str(line.get("text", "")).strip() for line in lines]
    if not all(1 <= len(text) <= 4 for text in line_texts):
        return False
    _x, _y, w, h = as_rect(rect)
    area = w * h
    max_dim = max(w, h)
    aspect = w / max(h, 1)
    char_step = max_dim / max(len(stripped), 1)
    if area < 25000 or max_dim < 150:
        return False
    return 0.55 <= aspect <= 1.45 and char_step >= 45
