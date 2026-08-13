#!/usr/bin/env python3
"""Manual guidance helpers for the review UI.

The original OCR/TG outputs stay immutable. This module builds effective
runtime inputs from user guidance so Step 3 can be steered without losing the
automatic baseline.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any


Rect = list[int]
Point = tuple[float, float]


def load_json(path: str | Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def text_group_id(group: dict[str, Any], fallback: int) -> int:
    return int(group.get("text_group_id", group.get("cluster_id", fallback)))


def rect_from_fragments(fragments: list[dict[str, Any]]) -> Rect:
    rects = [f.get("bbox") for f in fragments if isinstance(f.get("bbox"), list) and len(f["bbox"]) >= 4]
    if not rects:
        return [0, 0, 0, 0]
    x1 = min(int(r[0]) for r in rects)
    y1 = min(int(r[1]) for r in rects)
    x2 = max(int(r[0]) + int(r[2]) for r in rects)
    y2 = max(int(r[1]) + int(r[3]) for r in rects)
    return [x1, y1, x2 - x1, y2 - y1]


def union_rect(rects: list[Rect]) -> Rect:
    rects = [r for r in rects if len(r) >= 4 and int(r[2]) > 0 and int(r[3]) > 0]
    if not rects:
        return [0, 0, 0, 0]
    x1 = min(int(r[0]) for r in rects)
    y1 = min(int(r[1]) for r in rects)
    x2 = max(int(r[0]) + int(r[2]) for r in rects)
    y2 = max(int(r[1]) + int(r[3]) for r in rects)
    return [x1, y1, x2 - x1, y2 - y1]


def padded_rect_like_source(tight_rect: Rect, source: dict[str, Any]) -> Rect:
    sx, sy, sw, sh = [int(v) for v in (source.get("tight_rect") or tight_rect)[:4]]
    fx, fy, fw, fh = [int(v) for v in (source.get("fallback_rect") or source.get("tight_rect") or tight_rect)[:4]]
    tx, ty, tw, th = [int(v) for v in tight_rect[:4]]
    left = max(0, sx - fx)
    top = max(0, sy - fy)
    right = max(0, (fx + fw) - (sx + sw))
    bottom = max(0, (fy + fh) - (sy + sh))
    return [tx - left, ty - top, tw + left + right, th + top + bottom]


def line_side(point: Point, p1: Point, p2: Point) -> float:
    return (p2[0] - p1[0]) * (point[1] - p1[1]) - (p2[1] - p1[1]) * (point[0] - p1[0])


def fragment_center(fragment: dict[str, Any]) -> Point:
    x, y, w, h = [float(v) for v in fragment.get("bbox", [0, 0, 0, 0])[:4]]
    return (x + w / 2.0, y + h / 2.0)


def copy_fragment(fragment: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(fragment)
    copied["manual_source"] = "tg_split_fragment"
    return copied


def build_line(line_template: dict[str, Any], fragments: list[dict[str, Any]], line_id: int) -> dict[str, Any]:
    fragments = [copy_fragment(f) for f in fragments]
    bbox = rect_from_fragments(fragments)
    text = "".join(str(f.get("text", "")) for f in fragments)
    line = copy.deepcopy(line_template)
    line["text_line_id"] = line_id
    line["text"] = text
    line["bbox"] = bbox
    line["fragments"] = fragments
    line["manual_source"] = "tg_split_line"
    return line


def group_from_lines(source: dict[str, Any], group_id: int, lines: list[dict[str, Any]], role: str, guidance_id: str) -> dict[str, Any]:
    rects = [line.get("bbox", [0, 0, 0, 0]) for line in lines]
    tight = union_rect(rects)
    group = copy.deepcopy(source)
    group["text_group_id"] = int(group_id)
    group["cluster_id"] = int(group_id)
    group["text"] = "".join(str(line.get("text", "")) for line in lines)
    group["lines"] = lines
    group["tight_rect"] = tight
    group["bounding_rect"] = tight
    group["fallback_rect"] = padded_rect_like_source(tight, source)
    group["manual_source"] = role
    group["derived_from"] = text_group_id(source, int(group_id))
    group["split_guidance_id"] = guidance_id
    group["schema"] = source.get("schema", "text_group_v2")
    return group


def split_text_group(
    group: dict[str, Any],
    split: dict[str, Any],
    next_group_id: int,
    ambiguous_px: float = 3.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_line = split.get("line") or split.get("points") or []
    if len(raw_line) < 2:
        return [group], {"ok": False, "reason": "missing_split_line"}

    p1 = (float(raw_line[0][0]), float(raw_line[0][1]))
    p2 = (float(raw_line[1][0]), float(raw_line[1][1]))
    line_len = max(1.0, math.hypot(p2[0] - p1[0], p2[1] - p1[1]))
    guidance_id = str(split.get("id") or f"tg_split_{text_group_id(group, 0)}")

    side_a_lines: list[dict[str, Any]] = []
    side_b_lines: list[dict[str, Any]] = []
    orphan_fragments: list[dict[str, Any]] = []
    line_id_a = 1
    line_id_b = 1

    for source_line in group.get("lines", []):
        fragments = source_line.get("fragments") or []
        side_a: list[dict[str, Any]] = []
        side_b: list[dict[str, Any]] = []
        for fragment in fragments:
            signed_distance = line_side(fragment_center(fragment), p1, p2) / line_len
            if abs(signed_distance) <= ambiguous_px:
                orphan_fragments.append(copy.deepcopy(fragment))
            elif signed_distance > 0:
                side_a.append(fragment)
            else:
                side_b.append(fragment)
        if side_a:
            side_a_lines.append(build_line(source_line, side_a, line_id_a))
            line_id_a += 1
        if side_b:
            side_b_lines.append(build_line(source_line, side_b, line_id_b))
            line_id_b += 1

    if not side_a_lines or not side_b_lines:
        return [group], {
            "ok": False,
            "reason": "split_did_not_create_two_sides",
            "orphan_count": len(orphan_fragments),
        }

    rect_a = union_rect([line["bbox"] for line in side_a_lines])
    rect_b = union_rect([line["bbox"] for line in side_b_lines])
    keep_side = str(split.get("keep_side") or "auto")
    if keep_side == "positive":
        primary_lines, child_lines = side_a_lines, side_b_lines
    elif keep_side == "negative":
        primary_lines, child_lines = side_b_lines, side_a_lines
    else:
        # Manga vertical text is commonly read right-to-left; keep the right side
        # on the original TG id for stable review labels.
        primary_lines, child_lines = (side_a_lines, side_b_lines) if rect_a[0] >= rect_b[0] else (side_b_lines, side_a_lines)

    original_id = text_group_id(group, 0)
    primary = group_from_lines(group, original_id, primary_lines, "tg_split_primary", guidance_id)
    child = group_from_lines(group, next_group_id, child_lines, "tg_split_child", guidance_id)
    report = {
        "ok": True,
        "text_group_id": original_id,
        "new_text_group_id": next_group_id,
        "guidance_id": guidance_id,
        "orphan_count": len(orphan_fragments),
        "manual_orphans": orphan_fragments,
    }
    return [primary, child], report


def normalize_guidance(guidance: dict[str, Any] | None) -> dict[str, Any]:
    guidance = guidance or {}
    disabled = set()
    for item in guidance.get("tg_overrides", []):
        if item.get("enabled") is False or item.get("action") in {"disable", "disable_anchor", "suppress"}:
            try:
                disabled.add(int(item.get("text_group_id")))
            except (TypeError, ValueError):
                pass
    disabled.update(int(v) for v in guidance.get("disabled_tg_ids", []) if str(v).lstrip("-").isdigit())
    return {
        **guidance,
        "disabled_tg_ids": sorted(disabled),
        "tg_splits": list(guidance.get("tg_splits", [])),
    }


def build_effective_text_groups(
    source_text_groups_path: str | Path,
    guidance_path: str | Path,
    output_text_groups_path: str | Path,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    groups = load_json(source_text_groups_path, default=[])
    guidance = normalize_guidance(load_json(guidance_path, default={}))
    disabled = set(int(v) for v in guidance.get("disabled_tg_ids", []))
    split_by_tg: dict[int, list[dict[str, Any]]] = {}
    for split in guidance.get("tg_splits", []):
        try:
            split_by_tg.setdefault(int(split.get("text_group_id")), []).append(split)
        except (TypeError, ValueError):
            continue

    max_id = max((text_group_id(group, idx + 1) for idx, group in enumerate(groups)), default=0)
    next_id = max_id + 1
    effective: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    for idx, group in enumerate(groups):
        gid = text_group_id(group, idx + 1)
        if gid in disabled:
            skipped = copy.deepcopy(group)
            skipped["manual_source"] = "tg_disabled"
            reports.append({"ok": True, "text_group_id": gid, "action": "disabled"})
            continue

        current_groups = [copy.deepcopy(group)]
        for split in split_by_tg.get(gid, []):
            next_groups: list[dict[str, Any]] = []
            for current in current_groups:
                if text_group_id(current, gid) != gid:
                    next_groups.append(current)
                    continue
                split_groups, report = split_text_group(current, split, next_id)
                reports.append(report)
                if report.get("ok"):
                    next_id += 1
                next_groups.extend(split_groups)
            current_groups = next_groups
        effective.extend(current_groups)

    effective.sort(key=lambda g: (-int((g.get("tight_rect") or [0, 0, 0, 0])[0]), int((g.get("tight_rect") or [0, 0, 0, 0])[1])))
    write_json(output_text_groups_path, effective)
    report = {
        "source": str(source_text_groups_path),
        "output": str(output_text_groups_path),
        "source_count": len(groups),
        "effective_count": len(effective),
        "disabled_tg_ids": sorted(disabled),
        "reports": reports,
    }
    if report_path is not None:
        write_json(report_path, report)
    return report
