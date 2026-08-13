# floodfill 결과(region_type)를 기반으로 이미지 처리 난이도 점수 계산
#!/usr/bin/env python3

import json
from pathlib import Path

# 번역 파이프라인이 처리하기 어려운 region_type
UNCLEAR_TYPES = {"noise_like", "symbol_only", None}


def score_image(flood_vis_json_path: str | Path) -> float:
    """
    flood_vis.json을 읽어 이미지 난이도 점수를 반환.

    Returns:
        0.0 (쉬움) ~ 1.0 (어려움).
        flood_vis.json이 없으면 -1.0 (판단 불가).
    """
    path = Path(flood_vis_json_path)
    if not path.exists():
        return -1.0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return -1.0

    results = data.get("results", [])
    if not results:
        return -1.0

    unclear = sum(1 for r in results if r.get("region_type_guess") in UNCLEAR_TYPES)
    return unclear / len(results)


def score_label(score: float) -> str:
    """점수를 사람이 읽기 좋은 레이블로 변환."""
    if score < 0:
        return "분석 불가"
    if score >= 0.5:
        return f"🔴 어려움 ({score:.0%})"
    if score >= 0.25:
        return f"🟡 보통 ({score:.0%})"
    return f"🟢 쉬움 ({score:.0%})"
