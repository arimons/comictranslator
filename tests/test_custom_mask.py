# 지우기 보정(guidance.custom_masks): 칠한 곳을 결과 이미지에서 흰칠하고, 붙는 영역의 OCR 시트 crop에 합치는지 검증한다.
import json
from pathlib import Path

import cv2
import numpy as np

from pipeline import region_refs
from pipeline.dialogue_extractor import _custom_mask_shapes, build_dialogue_sheet
from pipeline.font_style_analyzer import _map_original_rect_to_sheet

TG_RECT = [136, 116, 28, 108]
LABEL_W = 48  # build_dialogue_sheet indexed_label_gutter 기본값
MISSED = (40, 300, 60, 330)  # 분석이 놓친 글자(어느 TG에도 없음)


def _page(tmp_path):
    image = np.full((400, 300, 3), 255, dtype=np.uint8)
    for y in range(120, 220, 28):  # 세로 글자 비슷한 획
        cv2.rectangle(image, (140, y), (160, y + 18), (0, 0, 0), -1)
    cv2.rectangle(image, MISSED[:2], MISSED[2:], (0, 0, 0), -1)
    paths = {name: str(tmp_path / name) for name in ("page.jpg", "groups.json", "masked.jpg", "regions.json", "sheet.jpg", "vis.jpg")}
    cv2.imwrite(paths["page.jpg"], image)
    group = {
        "text_group_id": 1, "cluster_id": 1, "text": "あいう",
        "fallback_rect": TG_RECT, "tight_rect": TG_RECT, "bounding_rect": TG_RECT,
        "orientation": "vertical",
        "lines": [{"bbox": TG_RECT, "fragments": [{"bbox": TG_RECT}]}],
    }
    Path(paths["groups.json"]).write_text(json.dumps([group]), encoding="utf-8")
    return paths


def _run(paths, monkeypatch, custom_masks=None):
    calls = []

    def fake_lama():
        def run(roi_pil, mask_pil):
            calls.append(1)
            return roi_pil
        return run

    monkeypatch.setattr("pipeline.dialogue_extractor.build_ocr_region_sheet", lambda *a, **k: {"regions": []})
    monkeypatch.setattr("pipeline.dialogue_extractor._get_lama", fake_lama)
    build_dialogue_sheet(
        paths["page.jpg"], paths["groups.json"], paths["masked.jpg"], paths["regions.json"], paths["sheet.jpg"], paths["vis.jpg"],
        custom_masks=custom_masks,
    )
    saved = json.loads(Path(paths["regions.json"]).read_text(encoding="utf-8"))
    return calls, saved, cv2.imread(paths["masked.jpg"]), cv2.imread(paths["sheet.jpg"])


def _sheet_pixel(sheet, region, x, y):
    """원본 좌표 (x, y)가 OCR 시트에서 어떤 값인지. crop 원점은 ocr_crop_rect 또는 영역 사각형이다."""
    origin = region.get("ocr_crop_rect") or region["unboxed_rect"]
    sx = region["indexed_sheet_rect"][0] + LABEL_W + x - origin[0]
    sy = region["indexed_sheet_rect"][1] + y - origin[1]
    if not (0 <= sx - region["indexed_sheet_rect"][0] - LABEL_W < region["sheet_rect"][2] and 0 <= sy < region["sheet_rect"][3]):
        return None
    return sheet[sy, sx]


MISSED_POLYGON = [[35, 295], [66, 295], [66, 336], [35, 336]]


def test_without_custom_masks_nothing_changes(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    calls, saved, masked, _ = _run(paths, monkeypatch)
    assert len(calls) == 1 and "custom_masks_applied" not in saved
    assert masked[315, 50].max() < 100


def test_page_record_without_overlap_is_whitened_but_not_in_ocr(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    calls, saved, masked, sheet = _run(paths, monkeypatch, custom_masks=[{"id": "cm_1", "polygons": [MISSED_POLYGON]}])
    report = saved["custom_masks_applied"]
    assert len(calls) == 1  # 흰칠만 한다. LaMa를 더 부르지 않는다
    assert masked[315, 50].min() > 240
    assert report["polygons"] == 1 and report["ocr_regions"] == [] and report["not_in_ocr"] == {"no_overlap": 1}
    region = saved["regions"][0]
    assert "ocr_crop_rect" not in region and _sheet_pixel(sheet, region, 50, 315) is None


def test_page_record_is_added_to_most_overlapping_region_ocr_crop(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    # 영역 사각형과 겹치면서 바깥 글자까지 덮는 사각형: 가장 많이 겹치는 영역(R1)의 OCR crop에 들어간다
    custom = {"id": "cm_2", "rects": [[150, 225, 30, 80]]}
    cv2.imwrite(paths["page.jpg"], cv2.rectangle(cv2.imread(paths["page.jpg"]), (165, 280), (172, 300), (0, 0, 0), -1))
    _, saved, masked, sheet = _run(paths, monkeypatch, custom_masks=[custom])
    region = saved["regions"][0]
    assert saved["custom_masks_applied"]["ocr_regions"] == [1]
    x, y, w, h = region["ocr_crop_rect"]
    assert y + h >= 305 and region["sheet_rect"][2:] == [w, h]
    assert region["custom_mask_ocr"]["mask_ids"] == ["cm_2"]
    assert _sheet_pixel(sheet, region, 168, 290).max() < 100  # 놓친 글자가 OCR 시트에 들어갔다
    assert masked[290, 168].min() > 240  # 결과 이미지에서는 흰칠


def test_region_record_follows_instance_ref_even_far_away(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    _, saved, _, _ = _run(paths, monkeypatch)
    region = saved["regions"][0]
    custom = {"id": "cm_3", "instance_ref": region["region_instance_ref"], "region_ref": region["region_ref"], "polygons": [MISSED_POLYGON]}
    _, saved, masked, sheet = _run(paths, monkeypatch, custom_masks=[custom])
    region = saved["regions"][0]
    assert saved["custom_masks_applied"]["ocr_regions"] == [1]
    assert _sheet_pixel(sheet, region, 50, 315).max() < 100
    assert region["unboxed_rect"] != region["ocr_crop_rect"]  # 영역 사각형(배치·영역 키)은 그대로
    assert masked[315, 50].min() > 240


def test_region_record_for_changed_instance_is_dormant_for_ocr_only(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    custom = {"id": "cm_4", "instance_ref": "auto|tg:1,2", "region_ref": "tg:1,2", "polygons": [MISSED_POLYGON]}
    _, saved, masked, _ = _run(paths, monkeypatch, custom_masks=[custom])
    report = saved["custom_masks_applied"]
    assert report["ocr_regions"] == [] and list(report["not_in_ocr"]) == ["dormant_shrink"]
    assert masked[315, 50].min() > 240  # 흰칠은 페이지 좌표라 계속된다


def test_resolve_instance_record_policy(monkeypatch):
    regions = region_refs.annotate_regions([
        {"region_id": 1, "type": "unboxed", "anchor_ids": [1], "unboxed_rect": [0, 0, 10, 10]},
        {"region_id": 2, "type": "unboxed", "anchor_ids": [5], "unboxed_rect": [50, 50, 10, 10], "region_origin": "manual_ubox_1"},
    ])
    found, kind = region_refs.resolve_instance_record({"instance_ref": "auto|tg:1"}, regions, "custom_mask")
    assert found["region_id"] == 1 and kind == region_refs.EXACT
    # TG가 줄어든(shrink) 경우 기본 정책은 휴면
    record = {"instance_ref": "auto|tg:1,2", "region_ref": "tg:1,2"}
    assert region_refs.resolve_instance_record(record, regions, "custom_mask") == (None, region_refs.SHRINK)
    # 정책을 move로 바꾸면 따라간다. 출처(자동/수동)가 다르면 붙지 않는다
    monkeypatch.setitem(region_refs.POLICY, (region_refs.SHRINK, "custom_mask"), "move")
    assert region_refs.resolve_instance_record(record, regions, "custom_mask")[0]["region_id"] == 1
    assert region_refs.resolve_instance_record({"instance_ref": "auto|tg:5,6", "region_ref": "tg:5,6"}, regions, "custom_mask")[0] is None
    # 같은 대사라도 다른 처리 인스턴스(수동 영역)는 정확 일치가 아니다
    assert region_refs.resolve_instance_record({"instance_ref": "manual_ubox_9|tg:5"}, regions, "custom_mask")[0] is None


def test_custom_mask_shapes_ignore_broken_shapes():
    record = {
        "strokes": [{"points": [["x", 1]]}, {"points": []}, "bad", {"points": [[15, 15], [20, 20]], "width": 9999}],
        "rects": [[1, 2], "bad", [12, 12, 0, 5]],
        "polygons": [[[1, 1], [2, 2]], [["a", 1], [2, 2], [3, 3]], "bad"],
    }
    shapes = _custom_mask_shapes([record, "bad", {"mode": "subtract", "rects": [[0, 0, 5, 5]]}], (100, 100, 3))
    assert [shape["kind"] for shape in shapes] == ["strokes"]
    assert _custom_mask_shapes([], (100, 100, 3)) == []


def test_font_style_mapping_uses_widened_ocr_crop_origin():
    region = {"sheet_rect": [0, 0, 80, 60], "indexed_sheet_rect": [0, 0, 128, 60], "unboxed_rect": [100, 100, 40, 40]}
    plain = _map_original_rect_to_sheet(region, [110, 110, 10, 10], 1000, 1000)
    widened = _map_original_rect_to_sheet({**region, "ocr_crop_rect": [80, 100, 60, 40]}, [110, 110, 10, 10], 1000, 1000)
    assert widened[0] - plain[0] == 20  # crop이 왼쪽으로 20px 넓어지면 시트 위치도 20px 밀린다


def test_changing_custom_masks_disables_unboxed_inpaint_reuse():
    import app

    item = {"id": "cm_1", "mode": "add", "strokes": [{"points": [[1, 2]], "width": 4}], "rects": []}
    base = {"manual_regions": [{"type": "unboxed"}]}
    assert app.guidance_has_edits({"custom_masks": [item]})
    prev = {**base, "custom_masks": [item]}
    assert app.guidance_allows_unboxed_mask_reuse(prev, {**prev, "manual_seed_points": [{"x": 1, "y": 2}]})
    assert not app.guidance_allows_unboxed_mask_reuse(base, prev)
    changed = {**item, "strokes": item["strokes"] + [{"points": [[3, 4]], "width": 4}]}
    assert not app.guidance_allows_unboxed_mask_reuse(prev, {**base, "custom_masks": [changed]})
    assert not app.guidance_allows_unboxed_mask_reuse(prev, base)
