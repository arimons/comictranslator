# 글자 입히기 수동값을 region 번호가 아니라 영역 위치로 짝짓는다
"""overlay_guidance 수동값의 위치 기준 재매칭.

region 번호는 Step 3가 돌 때마다 읽기 순서로 새로 매겨진다. Ubox·Edge 하나만 추가해도
패널 띠 계산이 바뀌어 번호가 크게 뒤섞이고, OCR을 다시 돌리면 integrated.json도 새 번호를 쓴다.
수동값(번역 수정·폰트·크기·위치)은 "5", "7:1" 같은 번호 키로 저장되므로 그대로 두면
다른 말풍선에 조용히 붙는다.

그래서 수동값마다 저장 시점의 영역 사각형을 `_anchor`로 기록해 두고, 번호가 바뀐 integrated가
생기면 겹침(IoU)이 가장 큰 영역의 새 번호로 옮긴다. 짝이 없으면 지우지 않고
`unmatched_regions`에 보관한다. 영역 사각형은 integrated cluster의 union_bbox(또는 bounding_rect)를
region별로 합친 값이다. 렌더가 integrated의 번호로 수동값을 찾기 때문이다.
"""

from __future__ import annotations

import copy
import re
from typing import Any

ANCHOR_FIELD = "_anchor"
UNMATCHED_FIELD = "unmatched_regions"
MIN_OVERLAP = 0.6

_KEY_RE = re.compile(r"^(\d+)([:\-]\d+)?$")


def split_override_key(key: str) -> tuple[int | None, str]:
    """"7:1" → (7, ":1"), "5" → (5, ""). 번호 키가 아니면 (None, "")."""
    match = _KEY_RE.match(str(key))
    if not match:
        return None, ""
    return int(match.group(1)), match.group(2) or ""


def region_rects_from_integrated(integrated: Any) -> dict[int, list[int]]:
    """integrated cluster를 source_region_id별로 묶어 영역 사각형(x, y, w, h)을 만든다."""
    boxes: dict[int, list[int]] = {}
    clusters = integrated.get("clusters", []) if isinstance(integrated, dict) else []
    for cluster in clusters if isinstance(clusters, list) else []:
        if not isinstance(cluster, dict):
            continue
        try:
            region_id = int(cluster.get("source_region_id"))
        except (TypeError, ValueError):
            continue
        rect = cluster.get("union_bbox") or cluster.get("bounding_rect")
        if not isinstance(rect, list) or len(rect) < 4:
            continue
        x, y, w, h = (int(v) for v in rect[:4])
        if w <= 0 or h <= 0:
            continue
        if region_id in boxes:
            bx, by, bw, bh = boxes[region_id]
            x1, y1 = min(bx, x), min(by, y)
            x2, y2 = max(bx + bw, x + w), max(by + bh, y + h)
            boxes[region_id] = [x1, y1, x2 - x1, y2 - y1]
        else:
            boxes[region_id] = [x, y, w, h]
    return boxes


def rect_iou(a: list[int], b: list[int]) -> float:
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def stamp_overlay_anchors(guidance: dict[str, Any], rects: dict[int, list[int]]) -> tuple[dict[str, Any], int]:
    """위치가 기록되지 않은 수동값에 현재 영역 사각형을 기록한다. (새 guidance, 기록한 개수)

    수동값이 현재 integrated 번호 기준으로 만들어졌을 때만 불러야 한다.
    """
    regions = guidance.get("regions") if isinstance(guidance, dict) else None
    if not isinstance(regions, dict) or not rects:
        return guidance, 0
    result = copy.deepcopy(guidance)
    stamped = 0
    for key, value in result["regions"].items():
        if not isinstance(value, dict) or isinstance(value.get(ANCHOR_FIELD), dict):
            continue
        region_id, _suffix = split_override_key(key)
        if region_id is None or region_id not in rects:
            continue
        value[ANCHOR_FIELD] = {"rect": list(rects[region_id]), "region_id": region_id}
        stamped += 1
    return result, stamped


def remap_overlay_guidance(
    guidance: dict[str, Any],
    rects: dict[int, list[int]],
    min_overlap: float = MIN_OVERLAP,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """위치가 기록된 수동값을 겹침이 가장 큰 현재 영역의 번호로 옮긴다. (새 guidance, 보고)

    보고: moved {예전 키: 새 키}, unmatched [예전 키]. 위치 기록이 없는 예전 수동값은 그 자리에 둔다.
    """
    report: dict[str, Any] = {"moved": {}, "unmatched": []}
    regions = guidance.get("regions") if isinstance(guidance, dict) else None
    if not isinstance(regions, dict) or not regions or not rects:
        # 새 integrated에 영역이 없으면(OCR 건너뜀 등) 판단할 근거가 없으니 건드리지 않는다.
        return guidance, report

    # 위치 기록이 있는 수동값마다 가장 많이 겹치는 현재 영역을 찾는다.
    proposals: list[tuple[float, str, str]] = []  # (겹침, 예전 키, 새 키)
    legacy: dict[str, Any] = {}
    for key, value in regions.items():
        anchor = value.get(ANCHOR_FIELD) if isinstance(value, dict) else None
        _region_id, suffix = split_override_key(key)
        if not isinstance(anchor, dict) or not isinstance(anchor.get("rect"), list) or _region_id is None:
            legacy[key] = value
            continue
        best_id, best_overlap = None, 0.0
        for region_id, rect in rects.items():
            overlap = rect_iou(anchor["rect"], rect)
            if overlap > best_overlap:
                best_id, best_overlap = region_id, overlap
        if best_id is None or best_overlap < min_overlap:
            proposals.append((0.0, key, ""))
        else:
            proposals.append((best_overlap, key, f"{best_id}{suffix}"))

    # 같은 새 키를 여럿이 원하면 더 많이 겹치는 쪽이 갖는다.
    new_regions: dict[str, Any] = {}
    unmatched: dict[str, Any] = {}
    for overlap, old_key, new_key in sorted(proposals, key=lambda item: -item[0]):
        value = copy.deepcopy(regions[old_key])
        if not new_key or new_key in new_regions:
            value["_unmatched_reason"] = "no_overlapping_region" if not new_key else "target_taken"
            unmatched[old_key] = value
            continue
        region_id, _suffix = split_override_key(new_key)
        value[ANCHOR_FIELD] = {"rect": list(rects[region_id]), "region_id": region_id}
        new_regions[new_key] = value
        if new_key != old_key:
            report["moved"][old_key] = new_key

    # 위치 기록이 없는 예전 수동값: 자리가 비어 있으면 그대로, 옮겨 온 수동값과 겹치면 보관함으로.
    for key, value in legacy.items():
        if key in new_regions:
            kept = copy.deepcopy(value)
            if isinstance(kept, dict):
                kept["_unmatched_reason"] = "legacy_key_taken"
            unmatched[key] = kept
        else:
            new_regions[key] = value

    if not report["moved"] and not unmatched:
        return guidance, report
    result = copy.deepcopy(guidance)
    result["regions"] = new_regions
    if unmatched:
        stash = dict(result.get(UNMATCHED_FIELD) or {})
        stash.update(unmatched)
        result[UNMATCHED_FIELD] = stash
        report["unmatched"] = sorted(unmatched)
    return result, report
