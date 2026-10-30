# 영역 참조(region_ref) 해석기: 번역·수동값·화자·메모가 R 번호가 아니라 대사 내용에 붙게 한다
"""영역 참조 해석기. 순수 함수만 있고 파일 I/O는 없다.

R 번호는 Step 3를 돌릴 때마다 읽기 순서로 다시 매겨진다. 번호를 키로 쓰던 번역(integrated),
글자 입히기 수동값(overlay_guidance), 화자·번역 메모가 재분석 뒤 다른 영역에 조용히 붙는 것을
막기 위해 두 가지 참조를 둔다.

- `region_ref` (내용 참조): "무슨 대사인가". 영역이 가진 TG(앵커) 집합으로 만든다.
  번역·Overlay 수동값·화자·메모가 이것으로 붙는다.
- `region_instance_ref` (인스턴스 참조): "어떤 처리 인스턴스인가". `<origin>|<region_ref>`.
  영역 제외·LaMa 고정처럼 특정 처리 영역에만 걸리는 기록이 쓴다.

형식 (우선순위)
    tg:3 / tg:10,13        TG 집합. 수동 가이드로 만든 영역도 TG가 있으면 이 형식이다.
    tg:10,13/s2            TG가 없는 분할 자식. 부모 TG 집합 + split_order.
    guide:<가이드 id>       TG가 없는 수동 영역(seed·수동 ubox·Edge box). 분할 자식이면 /s<n>.
    geo:<b|t>:<cx>,<cy>    위 셋이 모두 불가할 때만. 허용오차 매칭으로만 해석한다.
    충돌하면 뒤에 @<접미>가 붙는다(`tg:6@edge_box_1`).
Overlay 세그먼트 수동값 키는 `<region_ref>#<source_segment_index>`다.

짝을 못 찾으면 다른 번호의 영역으로 옮기지 않고 보관한다(번호 fallback 금지).
합침·분할·축소 등을 어떻게 처리할지는 아래 POLICY 표 하나로 정한다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Iterable

try:
    from pipeline.overlay_anchor import rect_iou
except ImportError:  # pipeline 폴더에서 스크립트로 실행할 때
    from overlay_anchor import rect_iou

SCHEME = "region_ref_v1"
ORIGIN_AUTO = "auto"
METHOD_ORIGIN_PREFIX = "method:"  # 가이드 id를 모르는 구 파일의 수동 영역
UNMATCHED_FIELD = "unmatched_regions"
ANCHOR_FIELD = "_anchor"

# 해석 결과 종류
EXACT, SHRINK, MERGE, SPLIT, GEO, UNMATCHED = "exact", "shrink", "merge", "split", "geo", "unmatched"

# geo 매칭 허용오차 (설계안 A6)
GEO_CENTER_RATIO = 0.25
GEO_MIN_IOU = 0.5
# TG 번호가 전부 바뀐 경우(TG digest 변경)에만 여는 IoU 재매칭 기준
TG_CHANGED_MIN_IOU = 0.6

# 처리 정책 표 (설계안 A1-A9). 사용자가 정책을 뒤집으면 이 표만 바꾼다.
# 키: (해석 종류, 소비처). 값: 소비처가 하는 동작.
#   keep / move                      그대로 두기 / 새 참조로 옮기기
#   move_if_sole_source              새 영역으로 오는 옛 참조가 하나뿐일 때만 옮기고, 아니면 전부 보관
#   unify_if_same                    값이 모두 같을 때만 옮기고, 다르면 비우고 보고
#   copy_all                         해당하는 새 영역 모두에 복사
#   stash                            값을 보관함으로 보내고 보고
#   dormant                          저장은 그대로 두되 어디에도 붙이지 않음(참조가 돌아오면 다시 붙는다)
POLICY: dict[tuple[str, str], str] = {
    (EXACT, "overlay"): "keep",
    (SHRINK, "overlay"): "move",
    (MERGE, "overlay"): "move_if_sole_source",
    (SPLIT, "overlay"): "stash",
    (GEO, "overlay"): "move",
    (UNMATCHED, "overlay"): "stash",
    (EXACT, "speaker"): "keep",
    (SHRINK, "speaker"): "move",
    (MERGE, "speaker"): "unify_if_same",
    (SPLIT, "speaker"): "copy_all",
    (GEO, "speaker"): "move",
    (UNMATCHED, "speaker"): "dormant",
    (EXACT, "context"): "keep",
    (SHRINK, "context"): "move",
    (MERGE, "context"): "unify_if_same",
    (SPLIT, "context"): "copy_all",
    (GEO, "context"): "move",
    (UNMATCHED, "context"): "dormant",
}

_LEGACY_KEY_RE = re.compile(r"^(\d+)(?:[:\-](\d+))?$")
_SENTENCE_KEY_RE = re.compile(r"^s:(\d+)$")
_REF_PREFIXES = ("tg:", "guide:", "geo:")


# ---------------------------------------------------------------- 형식 -----

def _int_list(values: Any) -> list[int]:
    result: set[int] = set()
    for value in values or []:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(result)


def _rect4(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        rect = [int(round(float(v))) for v in value[:4]]
    except (TypeError, ValueError):
        return None
    return rect if rect[2] > 0 and rect[3] > 0 else None


def region_rect(region: dict) -> list[int] | None:
    """영역의 대표 사각형. 처리 기준마다 키가 달라 순서대로 찾는다."""
    for key in ("bubble_rect", "unboxed_rect", "erase_rect", "source_rect", "rect"):
        rect = _rect4(region.get(key))
        if rect:
            return rect
    return None


def tg_ref(tg_ids: Iterable[int]) -> str:
    return "tg:" + ",".join(str(v) for v in sorted(set(int(v) for v in tg_ids)))


def geo_ref(basis: str, rect: list[int]) -> str:
    x, y, w, h = rect[:4]
    return f"geo:{basis}:{x + w // 2},{y + h // 2}"


def parse_ref(ref: str) -> dict:
    """참조 문자열을 분해한다. kind: tg | guide | geo | unknown."""
    text = str(ref or "")
    suffix = ""
    if "@" in text:
        text, suffix = text.split("@", 1)
    split_order = None
    if "/s" in text:
        text, order = text.rsplit("/s", 1)
        if order.lstrip("-").isdigit():
            split_order = int(order)
    result: dict[str, Any] = {"kind": "unknown", "tg_ids": (), "split_order": split_order, "suffix": suffix}
    if text.startswith("tg:"):
        ids = _int_list(part for part in text[3:].split(",") if part.strip())
        result.update(kind="tg", tg_ids=tuple(ids))
    elif text.startswith("guide:"):
        result.update(kind="guide", guide_id=text[6:])
    elif text.startswith("geo:"):
        match = re.fullmatch(r"geo:([bt]):(-?\d+),(-?\d+)", text)
        if match:
            result.update(kind="geo", basis=match.group(1), cx=int(match.group(2)), cy=int(match.group(3)))
    return result


def is_weak_ref(ref: str) -> bool:
    parsed = parse_ref(ref)
    return parsed["kind"] == "geo" or bool(parsed["suffix"])


def parse_overlay_key(key: str) -> tuple[str, str, int | None]:
    """Overlay 수동값 키를 분해한다. (종류, 영역 부분, 세그먼트)

    종류: legacy("7", "7:1", "7-1") | legacy_sentence("s:701") | ref | unknown.
    """
    text = str(key)
    if text.startswith(_REF_PREFIXES):
        region, _sep, segment = text.partition("#")
        return "ref", region, int(segment) if segment.isdigit() else None
    match = _LEGACY_KEY_RE.match(text)
    if match:
        return "legacy", match.group(1), int(match.group(2)) if match.group(2) is not None else None
    match = _SENTENCE_KEY_RE.match(text)
    if match:
        return "legacy_sentence", match.group(1), None
    return "unknown", text, None


def format_overlay_key(ref: str, segment: int | None = None) -> str:
    return ref if segment is None else f"{ref}#{int(segment)}"


# ---------------------------------------------------------- 영역 주석 -----

def _origin_of(region: dict) -> str:
    origin = region.get("region_origin")
    if isinstance(origin, str) and origin:
        return origin
    guide = region.get("guide_id")
    if isinstance(guide, str) and guide:
        return guide
    # 구 파일: 가이드 id가 영역까지 전달되지 않았다. 수동 영역은 방법 이름으로 자동과 구별만 한다.
    method = str(region.get("source_method") or region.get("method") or "")
    if method.startswith("manual_"):
        return f"{METHOD_ORIGIN_PREFIX}{method}"
    return ORIGIN_AUTO


def _is_guide_origin(origin: str) -> bool:
    return origin != ORIGIN_AUTO and not origin.startswith(METHOD_ORIGIN_PREFIX)


def _basis_char(region: dict) -> str:
    type_name = str(region.get("type") or region.get("processing_basis") or "")
    if type_name in {"unboxed", "text_based"}:
        return "t"
    return "b"


def _split_key(region: dict) -> tuple[int, ...] | None:
    rect = _rect4(region.get("split_parent_rect"))
    return tuple(rect) if rect else None


def _iou_rect(a: list[int] | None, b: list[int] | None) -> float:
    return rect_iou(a, b) if a and b else 0.0


def _derive_guide_ids(regions: list[dict], guidance: dict | None) -> dict[int, str]:
    """가이드 id가 없는 구 regions.json의 수동 영역에서 guidance로 가이드 id를 파생한다(설계 §3.5). 인덱스 → id."""
    if not isinstance(guidance, dict):
        return {}
    found: dict[int, str] = {}
    manual = [m for m in guidance.get("manual_regions") or [] if isinstance(m, dict) and m.get("id")]
    seeds = [m for m in guidance.get("seed_points") or guidance.get("manual_seed_points") or [] if isinstance(m, dict) and m.get("id")]
    boxes: dict[str, list[list[int]]] = {}
    for wall in guidance.get("virtual_walls") or []:
        line = wall.get("line") or wall.get("polyline") if isinstance(wall, dict) else None
        if wall.get("box_id") and isinstance(line, list) and len(line) >= 2:
            try:
                boxes.setdefault(str(wall["box_id"]), []).extend([[int(line[0][0]), int(line[0][1])], [int(line[-1][0]), int(line[-1][1])]])
            except (TypeError, ValueError, IndexError):
                continue
    for index, region in enumerate(regions):
        if not isinstance(region, dict) or region.get("region_origin") or region.get("guide_id"):
            continue
        method = str(region.get("source_method") or region.get("method") or "")
        rect = region_rect(region)
        if not rect or not method.startswith("manual_"):
            continue
        cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        if method in {"manual_ubox_guide", "manual_bubble_guide"}:
            wanted = "unboxed" if method == "manual_ubox_guide" else "bubble"
            best = max(
                ((_iou_rect(rect, _rect4(m.get("unboxed_rect") or m.get("bubble_rect") or m.get("rect"))), m) for m in manual
                 if (m.get("type") == wanted or (wanted == "bubble" and m.get("type") in (None, "bubble")))),
                key=lambda item: item[0], default=(0.0, None))
            if best[1] is not None and best[0] >= 0.9:
                found[index] = str(best[1]["id"])
        elif method == "manual_bubble_seed_rescue":
            inside = []
            for m in seeds:
                raw = m.get("point") or m.get("seed")
                try:
                    px, py = int(raw[0]), int(raw[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if rect[0] <= px <= rect[0] + rect[2] and rect[1] <= py <= rect[1] + rect[3]:
                    inside.append(((px - cx) ** 2 + (py - cy) ** 2, str(m["id"])))
            if inside:
                found[index] = min(inside)[1]
        elif method == "manual_edge_barrier_rescue":
            for box_id, pts in boxes.items():
                x1, x2 = min(p[0] for p in pts), max(p[0] for p in pts)
                y1, y2 = min(p[1] for p in pts), max(p[1] for p in pts)
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    found[index] = box_id
                    break
    return found


def annotate_regions(regions: list[dict], guidance: dict | None = None) -> list[dict]:
    """region_ref·region_instance_ref 등을 메모리로 붙인 사본을 돌려준다. 원본은 고치지 않는다.

    guidance는 구 파일에서 가이드 id를 찾을 때 쓸 수 있도록 받아 두지만, 지금은 region_origin /
    guide_id가 없으면 방법 이름으로만 자동과 수동을 구별한다.
    """
    items = [copy.deepcopy(r) if isinstance(r, dict) else r for r in regions or []]
    dict_items = [r for r in items if isinstance(r, dict)]
    for index, guide_id in _derive_guide_ids(items, guidance).items():
        items[index]["guide_id"] = guide_id

    # 분할 자식의 부모 TG 집합: 같은 split_parent_rect를 가진 형제들의 anchor_ids 합집합
    parent_tgs: dict[tuple[int, ...], set[int]] = {}
    for region in dict_items:
        key = _split_key(region)
        if key is not None:
            parent_tgs.setdefault(key, set()).update(_int_list(region.get("anchor_ids")))

    base_refs: list[str] = []
    for region in dict_items:
        origin = _origin_of(region)
        region["region_origin"] = origin
        anchors = _int_list(region.get("anchor_ids"))
        split_key = _split_key(region)
        split_order = region.get("split_order")
        weak = False
        if anchors:
            ref = tg_ref(anchors)
        elif split_key is not None and isinstance(split_order, int) and split_order >= 0:
            parent = _int_list(region.get("split_parent_anchor_ids")) or sorted(parent_tgs.get(split_key, set()))
            if parent:
                ref = f"{tg_ref(parent)}/s{split_order}"
            elif _is_guide_origin(origin):
                ref = f"guide:{origin}/s{split_order}"
            else:
                rect = region_rect(region)
                ref, weak = (geo_ref(_basis_char(region), rect), True) if rect else ("geo:b:0,0", True)
        elif _is_guide_origin(origin):
            ref = f"guide:{origin}"
        else:
            rect = region_rect(region)
            ref, weak = (geo_ref(_basis_char(region), rect), True) if rect else ("geo:b:0,0", True)
        region["region_ref"] = ref
        if weak:
            region["region_ref_weak"] = True
        base_refs.append(ref)

    # 같은 참조가 두 영역에 생기면 자동 영역이 기본 키를 갖고 나머지에 접미사를 붙인다.
    by_ref: dict[str, list[dict]] = {}
    for region in dict_items:
        by_ref.setdefault(region["region_ref"], []).append(region)
    for ref, group in by_ref.items():
        if len(group) < 2:
            continue
        primary = next((r for r in group if r["region_origin"] == ORIGIN_AUTO), group[0])
        taken = {ref}
        for index, region in enumerate(group):
            if region is primary:
                continue
            origin = region["region_origin"]
            suffix = origin if origin != ORIGIN_AUTO and f"{ref}@{origin}" not in taken else f"n{index}"
            candidate = f"{ref}@{suffix}"
            while candidate in taken:
                suffix += "_"
                candidate = f"{ref}@{suffix}"
            taken.add(candidate)
            region["region_ref"] = candidate
            region["region_ref_collision"] = True
        primary["region_ref_collision"] = True

    for region in dict_items:
        region["region_instance_ref"] = f"{region['region_origin']}|{region['region_ref']}"
    return items


def _annotated(regions: list[dict]) -> list[dict]:
    """이미 region_ref가 붙어 있으면 그대로, 아니면 붙여서 돌려준다."""
    if regions and all(isinstance(r, dict) and r.get("region_ref") for r in regions):
        return regions
    return annotate_regions(regions)


def region_ref(region: dict, page_regions: list[dict] | None = None, guidance: dict | None = None) -> str:
    """영역 하나의 내용 참조. 분할 형제·충돌을 보려면 페이지 영역 전체를 함께 넘긴다."""
    if region.get("region_ref"):
        return str(region["region_ref"])
    pool = page_regions if page_regions is not None else [region]
    annotated = annotate_regions(pool, guidance)
    for source, result in zip(pool, annotated):
        if source is region:
            return result["region_ref"]
    return annotate_regions([region], guidance)[0]["region_ref"]


def region_instance_ref(region: dict, page_regions: list[dict] | None = None, guidance: dict | None = None) -> str:
    if region.get("region_instance_ref"):
        return str(region["region_instance_ref"])
    ref = region_ref(region, page_regions, guidance)
    return f"{_origin_of(region)}|{ref}"


_INSTANCE_FIELDS = (
    "anchor_ids", "guide_id", "region_origin", "source_method", "method", "type", "processing_basis",
    "split_parent_rect", "split_order", "split_parent_anchor_ids",
    "bubble_rect", "unboxed_rect", "erase_rect", "source_rect",
)


def instance_ref_of(region: dict) -> str:
    """파이프라인 내부 영역(마스크 등 큰 값을 가진 dict)에서 region_instance_ref를 계산한다. 필요한 키만 복사한다.

    충돌 접미사(@)는 페이지 전체를 봐야 알 수 있어 여기서는 붙이지 않는다. 약한 참조는 소비처가 구형 키로 대신 판단한다.
    """
    slim = {key: region[key] for key in _INSTANCE_FIELDS if key in region}
    return annotate_regions([slim])[0]["region_instance_ref"]


def instance_ref_is_weak(instance_ref: str | None) -> bool:
    """사각형 중심(geo)이나 충돌 접미사로 만든 참조는 정확히 비교할 수 없다."""
    text = str(instance_ref or "")
    return not text or "|geo:" in text or "@" in text or f"|{METHOD_ORIGIN_PREFIX}" in text or text.startswith(METHOD_ORIGIN_PREFIX)


RAW_DIGEST_PREFIX = "raw:"


def digest_changed(stored: str | None, current: str | None) -> bool:
    """두 TG digest가 모두 raw 기준이고 서로 다를 때만 True. 예전 effective 기준 digest(접두사 없음)는 비교하지 않는다."""
    return bool(
        stored and current and str(stored).startswith(RAW_DIGEST_PREFIX) and str(current).startswith(RAW_DIGEST_PREFIX)
        and stored != current
    )


def tg_digest(text_groups: Iterable[dict] | None) -> str:
    """원본 TG(CV/Step 2 산출 text_groups.json)의 id·사각형 목록 해시. 달라졌다면 TG가 새로 매겨진 것이다.

    TG 끄기·분할은 effective 목록만 바꾸고 원본은 그대로라 digest가 변하지 않는다. 반드시 원본 목록을 넘긴다."""
    rows = []
    for index, group in enumerate(text_groups or [], start=1):
        if not isinstance(group, dict):
            continue
        gid = group.get("text_group_id", group.get("cluster_id", index))
        rect = _rect4(group.get("bounding_rect") or group.get("tight_rect")) or []
        rows.append([gid, rect])
    rows.sort(key=lambda row: str(row[0]))
    return RAW_DIGEST_PREFIX + hashlib.sha1(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]


# ----------------------------------------------------------- integrated ---

def _cluster_rect(cluster: dict) -> list[int] | None:
    return _rect4(cluster.get("union_bbox")) or _rect4(cluster.get("bounding_rect")) or _rect4(cluster.get("source_region_rect"))


def _union(rects: list[list[int]]) -> list[int] | None:
    if not rects:
        return None
    x1 = min(r[0] for r in rects)
    y1 = min(r[1] for r in rects)
    x2 = max(r[0] + r[2] for r in rects)
    y2 = max(r[1] + r[3] for r in rects)
    return [x1, y1, x2 - x1, y2 - y1]


def integrated_groups(integrated: Any, current_regions: list[dict] | None = None) -> list[dict]:
    """integrated 번역 조각을 번역 번호(source_region_id)별 묶음으로 만들고 묶음마다 참조를 파생한다.

    번호는 같은 integrated 파일 안에서 묶음을 찾는 데만 쓴다. 파일 밖으로는 `ref`를 쓴다.
    반환 항목: number, indexes, tg_ids, rect, ref, weak, clusters 순서 정보.
    """
    clusters = integrated.get("clusters", []) if isinstance(integrated, dict) else []
    groups: dict[Any, dict] = {}
    order: list[Any] = []
    for index, cluster in enumerate(clusters if isinstance(clusters, list) else []):
        if not isinstance(cluster, dict):
            continue
        number = cluster.get("source_region_id")
        try:
            number = int(number) if number is not None else None
        except (TypeError, ValueError):
            number = None
        key = number if number is not None else ("c", index)
        if key not in groups:
            groups[key] = {"number": number, "indexes": [], "tg_ids": set(), "rects": [], "stored_ref": None}
            order.append(key)
        group = groups[key]
        group["indexes"].append(index)
        group["tg_ids"].update(_int_list(cluster.get("matched_cluster")))
        rect = _cluster_rect(cluster)
        if rect:
            group["rects"].append(rect)
        if not group["stored_ref"] and isinstance(cluster.get("source_region_ref"), str) and cluster["source_region_ref"]:
            group["stored_ref"] = cluster["source_region_ref"]
        if "location" not in group and cluster.get("location"):
            group["location"] = cluster.get("location")

    current = _annotated(current_regions or []) if current_regions else []
    result = []
    for key in order:
        group = groups[key]
        rect = _union(group["rects"])
        weak = False
        if group["stored_ref"]:
            ref = group["stored_ref"]
            weak = is_weak_ref(ref)
        elif group["tg_ids"]:
            ref = tg_ref(group["tg_ids"])
        else:
            ref, weak = _rect_only_ref(rect, group.get("location"), current)
        result.append({
            "number": group["number"],
            "indexes": group["indexes"],
            "tg_ids": sorted(group["tg_ids"]),
            "rect": rect,
            "ref": ref,
            "weak": weak,
        })
    return result


def _rect_only_ref(rect: list[int] | None, location: str | None, current: list[dict]) -> tuple[str, bool]:
    """TG가 없는 번역 묶음: 현재 영역과 IoU 0.6 이상 단일 매칭이면 그 영역의 참조, 아니면 geo."""
    if rect is None:
        return "geo:b:0,0", True
    matches = [(rect_iou(rect, region_rect(r) or [0, 0, 0, 0]), r) for r in current]
    matches = [(iou, r) for iou, r in matches if iou >= TG_CHANGED_MIN_IOU]
    if len(matches) == 1:
        return matches[0][1]["region_ref"], bool(matches[0][1].get("region_ref_weak"))
    return geo_ref("t" if location == "unboxed" else "b", rect), True


def integrated_cluster_refs(integrated: Any, current_regions: list[dict] | None = None) -> dict[int, str]:
    """cluster 인덱스 → 번역 시점 영역 참조."""
    refs: dict[int, str] = {}
    for group in integrated_groups(integrated, current_regions):
        for index in group["indexes"]:
            refs[index] = group["ref"]
    return refs


# --------------------------------------------------------------- 해석 -----

def _resolution(kind: str, targets: list[str], reason: str, old_ref: str) -> dict:
    return {"kind": kind, "target_refs": targets, "reason": reason, "old_ref": old_ref}


def resolve(
    old_ref: str,
    snapshot: dict | None,
    current_regions: list[dict],
    current_tg_digest: str | None = None,
) -> dict:
    """저장된 참조가 현재 영역들에서 무엇을 가리키는지 찾는다.

    반환: {kind: exact|shrink|merge|split|geo|unmatched, target_refs, reason, old_ref}
    번호로는 절대 찾지 않는다. snapshot: {region_ref, tg_ids, rect, region_id, tg_digest} (없어도 된다).
    """
    snapshot = snapshot or {}
    regions = _annotated(current_regions)
    by_ref = {r["region_ref"]: r for r in regions if isinstance(r, dict)}
    if digest_changed(snapshot.get("tg_digest"), current_tg_digest):
        # CV/Step 2를 다시 돌려 TG 번호가 새로 매겨졌다. 같은 참조 문자열도 다른 말풍선일 수 있으니 위치(IoU)로만 찾는다(A7).
        rect = _rect4(snapshot.get("rect"))
        if rect:
            scored = sorted(
                ((rect_iou(rect, region_rect(r) or [0, 0, 0, 0]), r) for r in regions),
                key=lambda item: -item[0],
            )
            if scored and scored[0][0] >= TG_CHANGED_MIN_IOU and (len(scored) < 2 or scored[1][0] < scored[0][0]):
                return _resolution(GEO, [scored[0][1]["region_ref"]], "iou_after_tg_change", old_ref)
        return _resolution(UNMATCHED, [], "tg_digest_changed", old_ref)
    if old_ref in by_ref:
        return _resolution(EXACT, [old_ref], "same_ref", old_ref)

    parsed = parse_ref(old_ref)
    if parsed["kind"] == "tg" and parsed["split_order"] is None and not parsed["suffix"] and parsed["tg_ids"]:
        old_set = set(parsed["tg_ids"])
        subs: list[dict] = []
        sups: list[dict] = []
        partial: list[dict] = []
        equal: list[dict] = []
        for region in regions:
            tgs = set(_int_list(region.get("anchor_ids")))
            if not tgs or not (tgs & old_set):
                continue
            if tgs == old_set:
                equal.append(region)  # 충돌 접미사가 붙어 참조 문자열만 다른 같은 TG 집합
            elif tgs < old_set:
                subs.append(region)
            elif old_set < tgs:
                sups.append(region)
            else:
                partial.append(region)
        if len(equal) == 1 and not (subs or sups or partial):
            return _resolution(EXACT, [equal[0]["region_ref"]], "same_tg_set", old_ref)
        if partial or equal:
            return _resolution(UNMATCHED, [], "partial_overlap", old_ref)
        if len(subs) == 1 and not sups:
            return _resolution(SHRINK, [subs[0]["region_ref"]], "tg_subset", old_ref)
        if len(subs) > 1 and not sups:
            return _resolution(SPLIT, [r["region_ref"] for r in subs], "tg_split", old_ref)
        if len(sups) == 1 and not subs:
            return _resolution(MERGE, [sups[0]["region_ref"]], "tg_superset", old_ref)
        if subs or sups:
            return _resolution(UNMATCHED, [], "ambiguous_tg_relation", old_ref)

    rect = _rect4(snapshot.get("rect"))
    if parsed["kind"] == "geo":
        found = _geo_match(parsed, rect, regions)
        if found:
            return _resolution(GEO, [found["region_ref"]], "geo_tolerance", old_ref)
        return _resolution(UNMATCHED, [], "no_geo_match", old_ref)

    if rect and digest_changed(snapshot.get("tg_digest"), current_tg_digest):
        scored = sorted(
            ((rect_iou(rect, region_rect(r) or [0, 0, 0, 0]), r) for r in regions),
            key=lambda item: -item[0],
        )
        if scored and scored[0][0] >= TG_CHANGED_MIN_IOU and (len(scored) < 2 or scored[1][0] < scored[0][0]):
            return _resolution(GEO, [scored[0][1]["region_ref"]], "iou_after_tg_change", old_ref)
    return _resolution(UNMATCHED, [], "no_region", old_ref)


def _geo_match(parsed: dict, rect: list[int] | None, regions: list[dict]) -> dict | None:
    """중심 거리 ≤ 짧은 변의 25%, IoU ≥ 0.5(사각형이 있을 때). 후보가 둘 이상이면 매칭하지 않는다."""
    candidates = []
    for region in regions:
        target = region_rect(region)
        if not target:
            continue
        cx, cy = target[0] + target[2] / 2, target[1] + target[3] / 2
        limit = min(target[2], target[3]) * GEO_CENTER_RATIO
        if ((cx - parsed["cx"]) ** 2 + (cy - parsed["cy"]) ** 2) ** 0.5 > limit:
            continue
        if rect and rect_iou(rect, target) < GEO_MIN_IOU:
            continue
        candidates.append(region)
    return candidates[0] if len(candidates) == 1 else None


def snapshot_for_region(region: dict, digest: str | None = None) -> dict:
    """참조를 저장할 때 같이 남기는 위치 기록."""
    snapshot = {
        "region_ref": region.get("region_ref"),
        "tg_ids": _int_list(region.get("anchor_ids")),
        "rect": region_rect(region),
        "region_id": region.get("region_id"),
    }
    if digest:
        snapshot["tg_digest"] = digest
    return snapshot


# ------------------------------------------------------------ Overlay -----

def _legacy_digest(regions: Any) -> str:
    return hashlib.sha1(
        json.dumps(regions if isinstance(regions, dict) else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def legacy_digest(regions: Any) -> str:
    """구형 `regions` dict의 정규화 해시. 롤백 중 구 코드가 고쳤는지 알아내는 데 쓴다."""
    return _legacy_digest(_strip_private(regions))


def _strip_private(regions: Any) -> Any:
    if not isinstance(regions, dict):
        return {}
    return {
        str(k): {kk: vv for kk, vv in v.items() if kk != ANCHOR_FIELD} if isinstance(v, dict) else v
        for k, v in regions.items()
    }


def _stash(target: dict, key: str, value: Any, reason: str, ref: str | None = None) -> None:
    item = copy.deepcopy(value)
    if isinstance(item, dict):
        item["_unmatched_reason"] = reason
        if ref:
            item["_unmatched_ref"] = ref
            item["_unmatched_key"] = key
    name = key
    counter = 2
    while name in target:
        name = f"{key}~{counter}"
        counter += 1
    target[name] = item


def normalize_overlay_guidance(
    guidance: dict | None,
    integrated: Any,
    regions: list[dict] | None = None,
    overlay_newer_than_integrated: bool = True,
    overlay_newer_than_regions: bool = False,
) -> tuple[dict, dict]:
    """구형·신형 overlay_guidance를 `regions_by_ref` 형식으로 맞춘다. (guidance_v1, 보고)

    - 신형(`key_scheme == region_ref_v1`)이고 legacy_digest가 구형 `regions`와 맞으면 그대로 쓴다.
      맞지 않으면 롤백 중 구 코드가 고친 것이므로 구형 `regions`를 우선해 다시 만든다.
    - 구형 키 "N"/"N:k"/"N-k"는 integrated의 번역 묶음 N의 참조로, "s:ID"는 그 sentence_id의 참조로 옮긴다.
      overlay가 integrated보다 오래됐고 `_anchor`가 있으면 번호 대신 `_anchor.rect`로 묶음을 찾는다.
    - 번호만으로는 어느 영역인지 알 수 없는 키는 보관(`unmatched_regions`)으로 보낸다. 다만 overlay가 regions.json보다
      나중에 저장됐고(overlay_newer_than_regions) 그 번호가 번역 없는 현재 영역이면 현재 R 번호로 해석한다.
    - 롤백 중 구 코드가 고쳐 구형이 이길 때, 구형으로 표현할 수 없던 참조 전용 값은 보관함으로 옮긴다.
    """
    guidance = guidance if isinstance(guidance, dict) else {}
    report: dict[str, Any] = {"converted": {}, "stashed": {}, "source": "empty"}
    result = {k: copy.deepcopy(v) for k, v in guidance.items() if k not in {"regions", "regions_by_ref"}}
    result["key_scheme"] = SCHEME
    stash = dict(result.get(UNMATCHED_FIELD) or {})
    by_ref: dict[str, dict] = {}

    legacy_regions = guidance.get("regions") if isinstance(guidance.get("regions"), dict) else {}
    stored_by_ref = guidance.get("regions_by_ref") if isinstance(guidance.get("regions_by_ref"), dict) else None
    trust_refs = (
        guidance.get("key_scheme") == SCHEME
        and stored_by_ref is not None
        and guidance.get("legacy_digest") == legacy_digest(legacy_regions)
    )
    if trust_refs:
        report["source"] = "regions_by_ref"
        for key, value in stored_by_ref.items():
            by_ref[str(key)] = copy.deepcopy(value)
        result["regions_by_ref"] = by_ref
        result[UNMATCHED_FIELD] = stash
        return result, report
    if stored_by_ref is not None and guidance.get("key_scheme") == SCHEME:
        report["source"] = "legacy_wins_after_rollback"
    elif legacy_regions:
        report["source"] = "legacy"

    groups = integrated_groups(integrated, regions)
    by_number = {g["number"]: g for g in groups if g["number"] is not None}
    sentence_to_cluster: dict[int, tuple[dict, dict]] = {}
    clusters = integrated.get("clusters", []) if isinstance(integrated, dict) else []
    group_of_index = {i: g for g in groups for i in g["indexes"]}
    for index, cluster in enumerate(clusters if isinstance(clusters, list) else []):
        if isinstance(cluster, dict) and cluster.get("sentence_id") is not None:
            try:
                sentence_to_cluster[int(cluster["sentence_id"])] = (cluster, group_of_index.get(index, {}))
            except (TypeError, ValueError):
                continue

    pending: list[tuple[float, str, str, dict]] = []  # (우선순위, 원래 키, 새 키, 값)
    for key, value in legacy_regions.items():
        key = str(key)
        if not isinstance(value, dict):
            continue
        kind, region_part, segment = parse_overlay_key(key)
        anchor = value.get(ANCHOR_FIELD) if isinstance(value.get(ANCHOR_FIELD), dict) else None
        group = None
        migrated = None
        if kind == "legacy_sentence":
            found = sentence_to_cluster.get(int(region_part))
            if found and found[1]:
                group = found[1]
                segment = found[0].get("source_segment_index")
                try:
                    segment = int(segment) if segment is not None else None
                except (TypeError, ValueError):
                    segment = None
        elif kind == "legacy":
            number = int(region_part)
            rect = _rect4(anchor.get("rect")) if anchor else None
            if anchor and anchor.get("region_ref") and any(g["ref"] == anchor["region_ref"] for g in groups):
                group = next(g for g in groups if g["ref"] == anchor["region_ref"])
            elif rect and not overlay_newer_than_integrated:
                # 번역을 다시 한 뒤라 번호는 옛 번호다. 저장 때 남긴 위치로 묶음을 찾는다.
                scored = sorted(
                    ((rect_iou(rect, g["rect"]), g) for g in groups if g["rect"]),
                    key=lambda item: -item[0],
                )
                if scored and scored[0][0] >= TG_CHANGED_MIN_IOU and (len(scored) < 2 or scored[1][0] < scored[0][0]):
                    group = scored[0][1]
            else:
                group = by_number.get(number)
                if group is not None and not overlay_newer_than_integrated and not anchor:
                    migrated = "legacy_unanchored"
            if group is None and not anchor and overlay_newer_than_regions and regions and segment is None:
                # 번역이 없는 영역에 직접 입력한 값: overlay가 regions.json보다 나중에 저장됐으면 그 번호는 현재 R 번호다.
                # 번역 묶음이 쓰는 영역이면 번호가 번역 번호이므로 여기서는 해석하지 않는다.
                current = _annotated(regions)
                translated_tgs = set().union(*[set(g["tg_ids"]) for g in groups]) if groups else set()
                translated_refs = {g["ref"] for g in groups}
                target = next((r for r in current if r.get("region_id") == number), None)
                if target and target["region_ref"] not in translated_refs and not (set(_int_list(target.get("anchor_ids"))) & translated_tgs):
                    group = {"ref": target["region_ref"], "rect": region_rect(target),
                             "tg_ids": _int_list(target.get("anchor_ids")), "number": number}
                    migrated = "legacy_region_number"
            if group is None and rect and regions:
                # integrated에 묶음이 없는 번호(번역이 비어 있던 영역 등): 저장 때 남긴 위치가 현재 영역과 맞을 때만 쓴다.
                current = _annotated(regions)
                scored = sorted(
                    ((rect_iou(rect, region_rect(r) or [0, 0, 0, 0]), r) for r in current),
                    key=lambda item: -item[0],
                )
                if scored and scored[0][0] >= TG_CHANGED_MIN_IOU and (len(scored) < 2 or scored[1][0] < scored[0][0]):
                    group = {"ref": scored[0][1]["region_ref"], "rect": region_rect(scored[0][1]),
                             "tg_ids": _int_list(scored[0][1].get("anchor_ids")), "number": number}
                    migrated = "anchor_rect"
        elif kind == "ref":
            by_ref[key] = copy.deepcopy(value)
            continue
        else:
            _stash(stash, key, value, "unknown_key")
            report["stashed"][key] = "unknown_key"
            continue
        if group is None:
            reason = "no_overlapping_region" if anchor else "legacy_number_unresolved"
            _stash(stash, key, value, reason)
            report["stashed"][key] = reason
            continue
        new_key = format_overlay_key(group["ref"], segment)
        priority = 1.0 if migrated is None else 0.5
        pending.append((priority, key, new_key, {"group": group, "value": value, "migrated": migrated}))

    # 같은 새 키를 여럿이 원하면 먼저 만난(번호 키 우선) 쪽이 갖는다.
    for _priority, old_key, new_key, payload in sorted(pending, key=lambda item: -item[0]):
        if new_key in by_ref:
            _stash(stash, old_key, payload["value"], "target_taken", group_ref(payload["group"]))
            report["stashed"][old_key] = "target_taken"
            continue
        value = {k: copy.deepcopy(v) for k, v in payload["value"].items() if k != ANCHOR_FIELD}
        group = payload["group"]
        value[ANCHOR_FIELD] = {
            "region_ref": group["ref"],
            "tg_ids": list(group.get("tg_ids") or []),
            "rect": group.get("rect"),
            "region_id": group.get("number"),
        }
        if payload["migrated"]:
            value["_migrated"] = payload["migrated"]
        by_ref[new_key] = value
        report["converted"][old_key] = new_key
    if report["source"] == "legacy_wins_after_rollback":
        representable = legacy_key_map({"regions_by_ref": stored_by_ref}, integrated, regions)
        for key, value in stored_by_ref.items():
            if str(key) in representable or str(key) in by_ref or not isinstance(value, dict):
                continue
            base, _segment = _split_entry_key(str(key))
            _stash(stash, str(key), value, "rollback_not_representable", base)
            report["stashed"][str(key)] = "rollback_not_representable"
    result["regions_by_ref"] = by_ref
    result[UNMATCHED_FIELD] = stash
    return result, report


def group_ref(group: dict) -> str:
    return str(group.get("ref") or "")


def _split_entry_key(key: str) -> tuple[str, int | None]:
    kind, region, segment = parse_overlay_key(key)
    return (region, segment) if kind == "ref" else (key, None)


def segment_index_by_ref(integrated: Any, current_regions: list[dict], current_tg_digest: str | None = None) -> dict[str, set[int]]:
    """번역 조각이 있는 현재 영역 참조 → 그 영역에 실제로 있는 세그먼트 번호 집합(세그먼트가 없으면 빈 집합)."""
    clusters = integrated.get("clusters", []) if isinstance(integrated, dict) else []
    result: dict[str, set[int]] = {}
    for cluster, info in zip(clusters if isinstance(clusters, list) else [], resolve_cluster_refs(integrated, current_regions, current_tg_digest)):
        if not info["ref"] or not isinstance(cluster, dict):
            continue
        bucket = result.setdefault(info["ref"], set())
        try:
            if cluster.get("source_segment_index") is not None:
                bucket.add(int(cluster["source_segment_index"]))
        except (TypeError, ValueError):
            continue
    return result


def reconcile_overlay_guidance(
    guidance_v1: dict,
    current_regions: list[dict],
    current_tg_digest: str | None = None,
    segments: dict[str, set[int]] | None = None,
) -> tuple[dict, dict]:
    """regions_by_ref의 참조를 현재 영역에 맞춘다. (guidance_v1, 보고)

    옮길 수 없는 값은 지우지 않고 unmatched_regions로 보낸다. 번호는 보지 않는다.
    segments(segment_index_by_ref)를 주면 번역 조각이 있는 영역에서 사라진 세그먼트(`#k`)의 값만 보관한다.
    보관함 값 중 원래 참조가 현재 영역과 정확히 일치하게 돌아왔고 자리가 비어 있으면 다시 붙인다.
    """
    report: dict[str, Any] = {"moved": {}, "stashed": {}, "kept": 0}
    entries = guidance_v1.get("regions_by_ref") if isinstance(guidance_v1, dict) else None
    stash_in = guidance_v1.get(UNMATCHED_FIELD) if isinstance(guidance_v1, dict) else None
    if (not isinstance(entries, dict) or not entries) and not (isinstance(stash_in, dict) and stash_in):
        return guidance_v1, report
    entries = entries if isinstance(entries, dict) else {}
    regions = _annotated(current_regions or [])
    if not regions:
        # 영역이 없으면(분석 전·실패) 판단할 근거가 없으니 건드리지 않는다.
        return guidance_v1, report
    by_ref_regions = {r["region_ref"]: r for r in regions}

    base_info: dict[str, dict] = {}
    for key, value in entries.items():
        base, _segment = _split_entry_key(key)
        if base in base_info:
            continue
        anchor = value.get(ANCHOR_FIELD) if isinstance(value, dict) and isinstance(value.get(ANCHOR_FIELD), dict) else {}
        base_info[base] = resolve(base, anchor, regions, current_tg_digest)

    # 같은 새 영역으로 오는 서로 다른 옛 참조 수 (병합 판단용)
    sources_per_target: dict[str, set[str]] = {}
    for base, res in base_info.items():
        if res["kind"] in {EXACT, SHRINK, MERGE, GEO}:
            sources_per_target.setdefault(res["target_refs"][0], set()).add(base)

    result = copy.deepcopy(guidance_v1)
    new_entries: dict[str, dict] = {}
    stash = dict(result.get(UNMATCHED_FIELD) or {})

    def place(old_key: str, new_key: str, value: dict, target_ref: str, migrated: str | None) -> None:
        if new_key in new_entries:
            _stash(stash, old_key, value, "target_taken", target_ref)
            report["stashed"][old_key] = "target_taken"
            return
        item = copy.deepcopy(value)
        region = by_ref_regions[target_ref]
        item[ANCHOR_FIELD] = snapshot_for_region(region, current_tg_digest)
        if migrated:
            item["_migrated"] = migrated
        new_entries[new_key] = item
        if new_key != old_key:
            report["moved"][old_key] = new_key
        report["kept"] += 1

    def segment_gone(ref: str, segment: int | None) -> bool:
        return bool(segments) and segment is not None and ref in segments and segment not in segments[ref]

    # 정확히 일치하는 항목이 자리를 먼저 갖는다.
    ordered = sorted(entries.items(), key=lambda kv: base_info[_split_entry_key(kv[0])[0]]["kind"] != EXACT)
    for key, value in ordered:
        base, segment = _split_entry_key(key)
        res = base_info[base]
        action = POLICY[(res["kind"], "overlay")]
        if not isinstance(value, dict):
            continue
        if action in {"keep", "move"} and segment_gone(res["target_refs"][0], segment):
            _stash(stash, key, value, "segment_missing", res["target_refs"][0])
            report["stashed"][key] = "segment_missing"
        elif action == "keep":
            place(key, key, value, res["target_refs"][0], None)
        elif action == "move":
            target = res["target_refs"][0]
            place(key, format_overlay_key(target, segment), value, target, "shrink" if res["kind"] == SHRINK else "geo")
        elif action == "move_if_sole_source":
            target = res["target_refs"][0]
            if len(sources_per_target.get(target, ())) == 1:
                place(key, format_overlay_key(target, segment), value, target, "merge")
            else:
                _stash(stash, key, value, "merge_conflict", target)
                report["stashed"][key] = "merge_conflict"
        else:  # stash
            reason = "split" if res["kind"] == SPLIT else res["reason"]
            _stash(stash, key, value, reason, base)
            report["stashed"][key] = reason

    # 보관함 값 되돌리기: 원래 참조가 지금 현재 영역과 정확히 일치하고 자리가 비어 있을 때만 다시 붙인다.
    for stash_key in list(stash):
        item = stash[stash_key]
        if not isinstance(item, dict) or not item.get("_unmatched_ref"):
            continue
        original_key = str(item.get("_unmatched_key") or stash_key.split("~", 1)[0])
        base, segment = _split_entry_key(original_key)
        if base != item["_unmatched_ref"]:
            continue
        snapshot = item.get(ANCHOR_FIELD) if isinstance(item.get(ANCHOR_FIELD), dict) else {}
        res = resolve(base, snapshot, regions, current_tg_digest)
        # 예전 effective 기준 digest 때문에 보관된 값(tg_digest_changed)은 거짓 신호였으니 TG 관계(축소·합침)로도 되돌린다.
        false_signal = item.get("_unmatched_reason") == "tg_digest_changed" and not str(snapshot.get("tg_digest") or "").startswith(RAW_DIGEST_PREFIX)
        if false_signal:
            res = resolve(base, {**snapshot, "tg_digest": None}, regions, None)
        usable = res["kind"] == EXACT or (res["kind"] == GEO and res["reason"] == "iou_after_tg_change") or (
            false_signal and res["kind"] in {SHRINK, MERGE}
        )
        if not usable:
            continue
        target = res["target_refs"][0]
        if res["kind"] == EXACT and current_tg_digest and not str(snapshot.get("tg_digest") or "").startswith(RAW_DIGEST_PREFIX) and not false_signal:
            # digest가 없는 예전 보관분은 지금 digest를 알 때 위치(IoU 0.5)가 맞을 때만 되돌린다(보수적).
            old_rect, new_rect = _rect4(snapshot.get("rect")), region_rect(by_ref_regions[target])
            if old_rect and new_rect and rect_iou(old_rect, new_rect) < GEO_MIN_IOU:
                continue
        if segment_gone(target, segment):
            continue
        original_key = format_overlay_key(target, segment)
        base = target
        if original_key in new_entries:
            continue
        restored = {k: copy.deepcopy(v) for k, v in item.items() if not k.startswith("_unmatched")}
        restored[ANCHOR_FIELD] = snapshot_for_region(by_ref_regions[target], current_tg_digest)
        restored["_migrated"] = "restored"
        new_entries[original_key] = restored
        del stash[stash_key]
        report.setdefault("restored", []).append(original_key)
    result["regions_by_ref"] = new_entries
    result[UNMATCHED_FIELD] = stash
    return result, report


def legacy_key_map(guidance_v1: dict, integrated: Any, current_regions: list[dict] | None = None) -> dict[str, str]:
    """regions_by_ref 키 → 구형 regions 키. 구형으로 표현할 수 없는 값(번호 충돌 등)은 들어 있지 않다."""
    mapping: dict[str, str] = {}
    entries = guidance_v1.get("regions_by_ref") if isinstance(guidance_v1, dict) else None
    if not isinstance(entries, dict):
        return mapping
    groups = integrated_groups(integrated, current_regions)
    resolved = resolve_group_refs(groups, current_regions or [])
    number_by_ref: dict[str, int] = {}
    for group, ref in zip(groups, resolved):
        if group["number"] is not None and ref and ref not in number_by_ref:
            number_by_ref[ref] = group["number"]
    region_number_by_ref = {
        r["region_ref"]: r.get("region_id") for r in _annotated(current_regions or []) if r.get("region_id") is not None
    }
    used: set[str] = set()
    pending_direct: list[tuple[str, str]] = []
    for key, value in entries.items():
        if not isinstance(value, dict):
            continue
        base, segment = _split_entry_key(key)
        number = number_by_ref.get(base)
        if number is not None:
            legacy_key = f"{number}:{segment}" if segment is not None else str(number)
            if legacy_key not in used:
                used.add(legacy_key)
                mapping[str(key)] = legacy_key
        elif segment is None and region_number_by_ref.get(base) is not None:
            pending_direct.append((str(key), str(region_number_by_ref[base])))
    # 번역 번호 키가 먼저 자리를 갖고, 번역 없는 영역의 직접 입력(현재 R 번호 키)은 비어 있을 때만 쓴다.
    for key, legacy_key in pending_direct:
        if legacy_key not in used and not any(m == legacy_key or m.startswith(f"{legacy_key}:") for m in used):
            used.add(legacy_key)
            mapping[key] = legacy_key
    return mapping


def to_legacy_regions(guidance_v1: dict, integrated: Any, current_regions: list[dict] | None = None) -> dict[str, dict]:
    """구 코드가 읽을 수 있게 regions_by_ref를 현재 integrated 번호 키로 다시 만든다.

    옛 참조가 현재 integrated 묶음과 정확히 같거나 현재 영역을 거쳐 닿는 경우만 쓰고, 못 찾는 값은 뺀다.
    """
    entries = guidance_v1.get("regions_by_ref") if isinstance(guidance_v1, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {
        legacy_key: {k: copy.deepcopy(v) for k, v in entries[key].items() if k != ANCHOR_FIELD}
        for key, legacy_key in legacy_key_map(guidance_v1, integrated, current_regions).items()
    }


def resolve_group_refs(groups: list[dict], current_regions: list[dict], current_tg_digest: str | None = None) -> list[str | None]:
    """번역 묶음마다 현재 영역 기준 참조(resolved_region_ref)를 구한다. 못 찾으면 None."""
    regions = _annotated(current_regions) if current_regions else []
    result: list[str | None] = []
    for group in groups:
        if not regions:
            result.append(group["ref"])
            continue
        res = resolve(group["ref"], {"rect": group.get("rect")}, regions, current_tg_digest)
        if res["kind"] in {EXACT, SHRINK, MERGE, GEO}:
            result.append(res["target_refs"][0])
        else:
            result.append(None)
    return result


def resolve_cluster_refs(
    integrated: Any,
    current_regions: list[dict],
    current_tg_digest: str | None = None,
) -> list[dict]:
    """번역 조각(cluster)마다 현재 영역 기준 참조를 구한다. integrated cluster 순서와 같은 길이의 목록.

    조각 자신의 TG(matched_cluster)로 먼저 해석하므로, 영역이 갈라졌을 때 조각이 TG가 있는 자식에 붙는다.
    한 조각의 TG가 여러 새 영역에 걸치면 TG가 가장 많이 겹치는 영역에, 동률이면 읽기 순서가 앞인 영역에 붙인다(A14).
    반환 항목: {ref, kind, source_ref, vanished}. 영역을 못 찾으면 ref가 None이다.
    vanished는 조각의 TG가 어느 현재 영역에도 없을 때 True(TG 비활성화·영역 제외로 대사가 사라진 경우).
    """
    clusters = integrated.get("clusters", []) if isinstance(integrated, dict) else []
    regions = _annotated(current_regions) if current_regions else []
    groups = integrated_groups(integrated, regions)
    group_ref_of = {i: g["ref"] for g in groups for i in g["indexes"]}
    group_rect_of = {i: g["rect"] for g in groups for i in g["indexes"]}
    region_tgs = [(r, set(_int_list(r.get("anchor_ids")))) for r in regions]
    all_current_tgs = set().union(*[t for _r, t in region_tgs]) if region_tgs else set()
    results: list[dict] = []
    for index, cluster in enumerate(clusters if isinstance(clusters, list) else []):
        if not isinstance(cluster, dict):
            results.append({"ref": None, "kind": UNMATCHED, "source_ref": None, "vanished": False})
            continue
        tgs = set(_int_list(cluster.get("matched_cluster")))
        source_ref = tg_ref(tgs) if tgs else group_ref_of.get(index)
        if not regions or source_ref is None:
            results.append({"ref": source_ref, "kind": EXACT if source_ref else UNMATCHED, "source_ref": source_ref, "vanished": False})
            continue
        res = resolve(source_ref, {"rect": group_rect_of.get(index)}, regions, current_tg_digest)
        ref = res["target_refs"][0] if res["kind"] in {EXACT, SHRINK, MERGE, GEO} else None
        if res["kind"] == SPLIT:
            best = max(
                res["target_refs"],
                key=lambda r: (len(tgs & set(_int_list(next(x for x in regions if x["region_ref"] == r).get("anchor_ids")))), -res["target_refs"].index(r)),
            )
            ref = best
        vanished = bool(ref is None and tgs and not (tgs & all_current_tgs) and all_current_tgs)
        results.append({"ref": ref, "kind": res["kind"], "source_ref": source_ref, "vanished": vanished})
    return results


def dual_write_guidance(guidance_v1: dict, integrated: Any, current_regions: list[dict] | None = None) -> dict:
    """저장용 사본: regions_by_ref(신형)와 regions(구형)를 함께 담는다."""
    result = copy.deepcopy(guidance_v1)
    legacy = to_legacy_regions(guidance_v1, integrated, current_regions)
    result["key_scheme"] = SCHEME
    result["regions"] = legacy
    result["legacy_digest"] = legacy_digest(legacy)
    return result


# ---------------------------------------------------------- 화자·메모 -----

def reconcile_speakers(store: dict | None, current_regions: list[dict], current_tg_digest: str | None = None) -> tuple[dict, dict]:
    """화자·번역 메모(by_region_ref, context_by_region_ref)를 현재 영역에 맞춘다. (store, 보고)

    구형 by_region/context_by_region(처리 기준·검출 방법·사각형 키)은 현재 영역 중 같은 키를 찾아
    참조 키가 없을 때만 채운다. 구형 키와 by_tg는 건드리지 않는다.
    """
    try:
        from pipeline.region_types import region_binding_key
    except ImportError:
        from region_types import region_binding_key

    store = copy.deepcopy(store) if isinstance(store, dict) else {}
    report: dict[str, Any] = {"moved": {}, "copied": {}, "cleared": {}, "dormant": [], "seeded": []}
    regions = _annotated(current_regions or [])
    if not regions:
        return store, report
    by_ref_regions = {r["region_ref"]: r for r in regions}
    binding_to_ref = {region_binding_key(r): r["region_ref"] for r in regions}

    for field, legacy_field, consumer in (
        ("by_region_ref", "by_region", "speaker"),
        ("context_by_region_ref", "context_by_region", "context"),
    ):
        mapping = store.get(field) if isinstance(store.get(field), dict) else {}
        mapping = dict(mapping)
        for binding, value in (store.get(legacy_field) or {}).items():
            ref = binding_to_ref.get(binding)
            if ref and ref not in mapping:
                mapping[ref] = value
                report["seeded"].append(ref)
        if not mapping:
            continue
        resolutions = {ref: resolve(ref, None, regions, current_tg_digest) for ref in mapping}
        incoming: dict[str, list[tuple[str, Any]]] = {}
        out: dict[str, Any] = {}
        for ref, value in mapping.items():
            res = resolutions[ref]
            action = POLICY[(res["kind"], consumer)]
            if action == "keep":
                out[ref] = value
            elif action == "move":
                incoming.setdefault(res["target_refs"][0], []).append((ref, value))
            elif action == "unify_if_same":
                incoming.setdefault(res["target_refs"][0], []).append((ref, value))
            elif action == "copy_all":
                for target in res["target_refs"]:
                    incoming.setdefault(target, []).append((ref, value))
                report["copied"][ref] = list(res["target_refs"])
            else:  # dormant: 저장은 두되 붙이지 않는다
                out[ref] = value
                report["dormant"].append(ref)
        for target, items in incoming.items():
            if target in out:
                continue  # 이미 그 영역에 정확히 붙은 값이 있으면 그것이 우선이다
            values = {json.dumps(v, ensure_ascii=False, sort_keys=True) for _ref, v in items}
            if len(values) == 1:
                out[target] = items[0][1]
                for old_ref, _v in items:
                    if old_ref != target and old_ref not in report["copied"]:
                        report["moved"][old_ref] = target
            else:
                for old_ref, v in items:
                    report["cleared"][old_ref] = v
                    if consumer == "context":
                        # 설계 A2: 서로 다른 메모는 비우지 않고 전부 보관한다.
                        store.setdefault("unmatched_context_by_region_ref", {})[old_ref] = v
        store[field] = {ref: value for ref, value in out.items() if ref in by_ref_regions or ref in report["dormant"]}
    return store, report


# --------------------------------------------------------------- 페이지 ---

def project_page(
    regions_payload: Any,
    integrated: Any,
    overlay_guidance: Any,
    manual_guidance: Any = None,
    effective_text_groups: Any = None,
    overlay_newer_than_integrated: bool = True,
    overlay_newer_than_regions: bool = False,
) -> dict:
    """페이지 하나의 읽기 projection. 저장 파일은 건드리지 않고 메모리 사본만 만든다.

    반환: regions_payload(영역에 region_ref 등 부착), integrated(cluster에 source_region_ref·resolved_region_ref),
    overlay_guidance_v1(현재 영역 기준으로 재조정된 regions_by_ref), report.
    """
    regions_list = regions_payload.get("regions") if isinstance(regions_payload, dict) else None
    annotated = annotate_regions(regions_list, manual_guidance if isinstance(manual_guidance, dict) else None) if isinstance(regions_list, list) else []
    out_regions = {**regions_payload, "regions": annotated} if isinstance(regions_payload, dict) and isinstance(regions_list, list) else regions_payload

    groups_source = integrated if isinstance(integrated, dict) else {"clusters": integrated if isinstance(integrated, list) else []}
    digest = tg_digest(effective_text_groups) if isinstance(effective_text_groups, list) and effective_text_groups else None
    groups = integrated_groups(groups_source, annotated)
    cluster_refs = resolve_cluster_refs(groups_source, annotated, digest)
    clusters = copy.deepcopy(groups_source.get("clusters", [])) if groups_source.get("clusters") else []
    for group in groups:
        for index in group["indexes"]:
            cluster = clusters[index]
            if not cluster.get("source_region_ref"):
                cluster["source_region_ref"] = group["ref"]
                cluster["source_region_ref_derived"] = True
            cluster["resolved_region_ref"] = cluster_refs[index]["ref"]
            if cluster_refs[index]["vanished"]:
                cluster["region_vanished"] = True
    if isinstance(integrated, dict):
        out_integrated: Any = {**integrated, "clusters": clusters} if "clusters" in integrated else integrated
    else:
        out_integrated = clusters if isinstance(integrated, list) else integrated

    v1, normalize_report = normalize_overlay_guidance(
        overlay_guidance if isinstance(overlay_guidance, dict) else {},
        groups_source,
        annotated,
        overlay_newer_than_integrated,
        overlay_newer_than_regions,
    )
    v1, reconcile_report = reconcile_overlay_guidance(v1, annotated, digest, segment_index_by_ref(groups_source, annotated, digest))
    return {
        "regions": out_regions,
        "integrated": out_integrated,
        "overlay_guidance_v1": v1,
        "report": {"overlay": {**normalize_report, "reconcile": reconcile_report}, "tg_digest": digest},
    }
