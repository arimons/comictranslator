# 수동 Bubble guidance가 Step 3 region 후보로 반영되는지 검증한다.

import cv2
import pytest
import numpy as np
from PIL import Image

from pipeline.dialogue_extractor import (
    _apply_manual_ubox_lama,
    _apply_manual_unboxed_overrides,
    build_dialogue_sheet,
    _dedupe_dialogue_crops,
    _invalidate_containing_bubbles,
    _invalidate_regions_replaced_by_manual_edge,
    _invalidate_regions_replaced_by_manual_overrides,
    _invalidate_suppressed_regions,
    _manual_bubble_regions,
    _manual_edge_barrier_regions,
    _padded_context_rect,
    _manual_ubox_text_mask,
    _manual_seed_anchor_ids_from_result,
    _manual_seed_bright_component,
    _manual_seed_bubble_regions,
    _manual_seed_direct_flood,
    _manual_unboxed_crops,
    _mask_for_bubble_erasure,
    _overlay_final_unboxed_regions,
    _select_unboxed_inpaint_mask,
    _seed_point,
    _snap_manual_line_to_defects,
    _split_mask_by_projection_neck,
    _split_manual_seed_regions,
)


def test_manual_bubble_region_collects_overlapping_tg_ids():
    groups = [
        {"text_group_id": 1, "fallback_rect": [20, 20, 20, 20]},
        {"text_group_id": 2, "fallback_rect": [120, 20, 20, 20]},
    ]
    manual = [{"type": "bubble", "bubble_rect": [10, 10, 60, 60]}]

    regions = _manual_bubble_regions(manual, groups)

    assert regions[0]["method"] == "manual_bubble_guide"
    assert regions[0]["anchor_ids"] == [1]
    assert regions[0]["region_type_guess"] == "bubble"


def test_manual_edge_region_is_clipped_to_user_box(monkeypatch):
    image = np.full((120, 180, 3), 245, dtype=np.uint8)
    groups = [{"text_group_id": 9, "fallback_rect": [62, 35, 28, 42], "text": "ありました"}]
    leaked_mask = np.zeros((120, 180), dtype=np.uint8)
    leaked_mask[25:95, 55:160] = 255
    walls = [
        {"box_id": "edge_box_1", "line": [[50, 25], [100, 25]]},
        {"box_id": "edge_box_1", "line": [[100, 25], [100, 95]]},
        {"box_id": "edge_box_1", "line": [[100, 95], [50, 95]]},
        {"box_id": "edge_box_1", "line": [[50, 95], [50, 25]]},
    ]

    def fake_rescue(*args, **kwargs):
        return {
            "success": True,
            "seed": [70, 55],
            "rescue_rect": [55, 25, 105, 70],
            "mask": leaked_mask,
            "score": {"mask_page_ratio": 0.2, "rect_anchor_ratio": 5.0, "rank_score": 1.0},
            "best_label": "fake",
        }

    monkeypatch.setattr("pipeline.edge_barrier_flood_rescue.run_edge_barrier_rescue_auto", fake_rescue)

    regions = _manual_edge_barrier_regions(image, groups, walls)

    assert len(regions) == 1
    assert regions[0]["bubble_rect"] == [55, 25, 45, 70]
    assert np.count_nonzero(regions[0]["mask"][:, 100:] > 0) == 0
    assert regions[0]["anchor_ids"] == [9]


def test_manual_edge_region_uses_box_when_rescue_fails(monkeypatch):
    image = np.full((120, 180, 3), 245, dtype=np.uint8)
    groups = [{"text_group_id": 9, "fallback_rect": [62, 35, 28, 42], "text": "すみません"}]
    diagnostics = []
    walls = [
        {"box_id": "edge_box_1", "line": [[50, 25], [100, 25]]},
        {"box_id": "edge_box_1", "line": [[100, 25], [100, 95]]},
        {"box_id": "edge_box_1", "line": [[100, 95], [50, 95]]},
        {"box_id": "edge_box_1", "line": [[50, 95], [50, 25]]},
    ]

    def fake_rescue(*args, **kwargs):
        return {
            "success": False,
            "reason": "edge_barrier_no_safe_mask",
            "mask": np.zeros((120, 180), dtype=np.uint8),
            "score": {},
            "rescue_rect": [0, 0, 0, 0],
        }

    monkeypatch.setattr("pipeline.edge_barrier_flood_rescue.run_edge_barrier_rescue_auto", fake_rescue)

    regions = _manual_edge_barrier_regions(image, groups, walls, diagnostics=diagnostics)

    assert len(regions) == 1
    assert regions[0]["bubble_rect"] == [50, 25, 50, 70]
    assert np.count_nonzero(regions[0]["mask"][25:95, 50:100] > 0) == 3500
    assert regions[0]["anchor_ids"] == [9]
    assert "manual_edge_box_fallback=edge_barrier_no_safe_mask" in regions[0]["quality_flags"]
    assert diagnostics == [{
        "box_index": 1,
        "box_id": "edge_box_1",
        "ok": True,
        "reason": "edge_barrier_no_safe_mask",
        "rect": [50, 25, 50, 70],
        "region_rect": [50, 25, 50, 70],
        "anchor_ids": [9],
        "used_box_fallback": True,
        "region_id": 11001,
        "wall_count": 4,
    }]


def test_manual_edge_region_uses_box_without_matching_tg():
    image = np.full((120, 180, 3), 245, dtype=np.uint8)
    groups = [{"text_group_id": 2, "fallback_rect": [130, 10, 20, 20], "text": "別"}]
    diagnostics = []
    walls = [
        {"box_id": "edge_box_1", "line": [[50, 25], [100, 25]]},
        {"box_id": "edge_box_1", "line": [[100, 25], [100, 95]]},
        {"box_id": "edge_box_1", "line": [[100, 95], [50, 95]]},
        {"box_id": "edge_box_1", "line": [[50, 95], [50, 25]]},
    ]

    regions = _manual_edge_barrier_regions(image, groups, walls, diagnostics=diagnostics)

    assert len(regions) == 1
    assert regions[0]["bubble_rect"] == [50, 25, 50, 70]
    assert regions[0]["anchor_ids"] == []
    assert "manual_edge_box_fallback=no_target_group" in regions[0]["quality_flags"]
    assert diagnostics[0]["reason"] == "no_target_group"
    assert diagnostics[0]["used_box_fallback"] is True


def test_non_overlapping_manual_edge_boxes_are_reported_independently(monkeypatch):
    image = np.full((120, 220, 3), 245, dtype=np.uint8)
    groups = [
        {"text_group_id": 1, "fallback_rect": [22, 25, 20, 30], "text": "左"},
        {"text_group_id": 2, "fallback_rect": [142, 25, 20, 30], "text": "右"},
    ]
    calls = []
    walls = [
        {"box_id": "edge_box_left", "line": [[10, 10], [70, 10]]},
        {"box_id": "edge_box_left", "line": [[70, 10], [70, 80]]},
        {"box_id": "edge_box_left", "line": [[70, 80], [10, 80]]},
        {"box_id": "edge_box_left", "line": [[10, 80], [10, 10]]},
        {"box_id": "edge_box_right", "line": [[130, 10], [190, 10]]},
        {"box_id": "edge_box_right", "line": [[190, 10], [190, 80]]},
        {"box_id": "edge_box_right", "line": [[190, 80], [130, 80]]},
        {"box_id": "edge_box_right", "line": [[130, 80], [130, 10]]},
    ]

    def fake_rescue(_image, target_groups, source_rect, virtual_walls=None, **kwargs):
        calls.append({
            "target_ids": [item["text_group_id"] for item in target_groups],
            "source_rect": source_rect,
            "box_ids": sorted({item.get("box_id") for item in virtual_walls or []}),
        })
        return {
            "success": False,
            "reason": "edge_barrier_no_safe_mask",
            "mask": np.zeros((120, 220), dtype=np.uint8),
            "score": {},
            "rescue_rect": [0, 0, 0, 0],
        }

    monkeypatch.setattr("pipeline.edge_barrier_flood_rescue.run_edge_barrier_rescue_auto", fake_rescue)

    regions = _manual_edge_barrier_regions(image, groups, walls)

    assert [item["anchor_ids"] for item in regions] == [[1], [2]]
    assert calls == [
        {"target_ids": [1], "source_rect": [10, 10, 60, 70], "box_ids": ["edge_box_left"]},
        {"target_ids": [2], "source_rect": [130, 10, 60, 70], "box_ids": ["edge_box_right"]},
    ]


def test_manual_bubble_is_kept_when_it_contains_auto_bubble():
    manual = {"is_valid": True, "method": "manual_bubble_guide", "bubble_rect": [0, 0, 100, 100]}
    auto = {"is_valid": True, "method": "edge_barrier_rescue", "bubble_rect": [20, 20, 20, 20]}
    regions = [manual, auto]

    _invalidate_containing_bubbles(regions)

    assert manual["is_valid"] is True
    assert auto["is_valid"] is False


def test_suppressed_region_invalidates_matching_auto_region():
    auto = {
        "is_valid": True,
        "method": "edge_barrier_rescue",
        "region_id": 12,
        "bubble_rect": [20, 20, 80, 120],
    }
    unrelated = {
        "is_valid": True,
        "method": "flood",
        "region_id": 13,
        "bubble_rect": [200, 20, 80, 120],
    }

    _invalidate_suppressed_regions([auto, unrelated], [{"source_region_id": 12, "rect": [20, 20, 80, 120]}])

    assert auto["is_valid"] is False
    assert unrelated["is_valid"] is True
    assert "manual_region_suppressed" in auto["quality_flags"]


def test_suppressed_region_does_not_invalidate_manual_region():
    manual = {
        "is_valid": True,
        "method": "manual_bubble_seed_rescue",
        "region_id": 12,
        "bubble_rect": [20, 20, 80, 120],
    }

    _invalidate_suppressed_regions([manual], [{"source_region_id": 12, "rect": [20, 20, 80, 120]}])

    assert manual["is_valid"] is True


def test_anchorless_child_does_not_invalidate_anchored_parent_bubble():
    anchored = {"is_valid": True, "method": "flood", "anchor_ids": [5, 7], "bubble_rect": [140, 904, 690, 365]}
    orphan = {"is_valid": True, "method": "flood", "anchor_ids": [], "bubble_rect": [700, 1056, 99, 120]}
    regions = [anchored, orphan]

    _invalidate_containing_bubbles(regions)

    assert anchored["is_valid"] is True
    assert orphan["is_valid"] is False


def test_complex_unboxed_lama_uses_tg_rect_mask_without_extra_dilate():
    text_mask = np.zeros((80, 40), dtype=np.uint8)
    text_mask[10:25, 10:20] = 255
    candidate = {
        "text_mask": text_mask,
        "lane_mask": np.full((80, 40), 255, dtype=np.uint8),
        "roi": np.full((80, 40, 3), 255, dtype=np.uint8),
        "roi_rect": [100, 200, 40, 80],
        "tg_rect": [110, 215, 20, 45],
        "background_class": "mixed_line_art",
        "recommended_inpaint_engine": "lama",
    }

    selected, meta = _select_unboxed_inpaint_mask(candidate)

    assert meta["mask_strategy"] == "tg_rect_lama"
    assert meta["extra_dilate"] == 0
    assert selected[15, 12] == 255
    assert selected[58, 28] == 255
    assert selected[5, 5] == 0


def test_complex_unboxed_with_visible_text_uses_ubox_style_stroke_mask():
    # 검은 그림 위 흰 글자: 사각형 전체가 아니라 글자 획(+확장)만 지운다.
    roi = np.zeros((120, 60, 3), dtype=np.uint8)
    cv2.rectangle(roi, (26, 30), (32, 80), (255, 255, 255), -1)
    candidate = {
        "text_mask": np.zeros((120, 60), dtype=np.uint8),
        "lane_mask": np.full((120, 60), 255, dtype=np.uint8),
        "roi": roi,
        "roi_rect": [100, 200, 60, 120],
        "tg_rect": [120, 225, 20, 60],
        "background_class": "dark_bg",
        "recommended_inpaint_engine": "lama",
    }

    selected, meta = _select_unboxed_inpaint_mask(candidate)

    assert meta["mask_strategy"] == "ubox_style_stroke_lama"
    assert selected[55, 29] == 255  # 글자 획
    assert selected[55, 36] == 255  # 확장으로 덮인 외곽
    assert selected[30, 15] == 0    # 사각형 안이지만 글자에서 먼 배경은 남긴다


def test_furigana_pass_uses_rect_plus_furigana_lama_mask():
    text_mask = np.zeros((80, 50), dtype=np.uint8)
    text_mask[10:25, 10:20] = 255
    text_mask[30:45, 35:40] = 255
    candidate = {
        "text_mask": text_mask,
        "lane_mask": np.full((80, 50), 255, dtype=np.uint8),
        "roi": np.full((80, 50, 3), 255, dtype=np.uint8),
        "roi_rect": [100, 200, 50, 80],
        "tg_rect": [110, 210, 18, 45],
        "background_class": "white_flat",
        "recommended_inpaint_engine": "telea",
        "furigana_added_pixels": 40,
    }

    selected, meta = _select_unboxed_inpaint_mask(candidate)

    assert meta["mask_strategy"] == "tg_rect_plus_furigana_lama"
    assert meta["recommended_inpaint_engine_override"] == "lama"
    assert selected[20, 12] == 255
    assert selected[35, 37] == 255
    assert selected[5, 5] == 0


def test_manual_edge_walls_do_not_leak_into_auto_detector(tmp_path, monkeypatch):
    image_path = tmp_path / "page.jpg"
    clusters_path = tmp_path / "groups.json"
    masked_path = tmp_path / "masked.jpg"
    regions_path = tmp_path / "regions.json"
    sheet_path = tmp_path / "sheet.jpg"
    vis_path = tmp_path / "vis.jpg"
    cv2.imwrite(str(image_path), np.full((80, 80, 3), 255, dtype=np.uint8))
    clusters_path.write_text("[]", encoding="utf-8")
    captured = {}

    def fake_build_ocr_region_sheet(*args, manual_virtual_walls=None, **kwargs):
        captured["manual_virtual_walls"] = manual_virtual_walls
        return {"regions": []}

    monkeypatch.setattr("pipeline.dialogue_extractor.build_ocr_region_sheet", fake_build_ocr_region_sheet)
    monkeypatch.setattr("pipeline.dialogue_extractor._manual_edge_barrier_regions", lambda *args, **kwargs: [])

    build_dialogue_sheet(
        str(image_path),
        str(clusters_path),
        str(masked_path),
        str(regions_path),
        str(sheet_path),
        str(vis_path),
        manual_virtual_walls=[{"line": [[10, 10], [50, 10]], "box_id": "edge_box_1"}],
    )

    assert captured["manual_virtual_walls"] == []


def test_manual_ubox_region_becomes_unboxed_crop():
    image = np.zeros((120, 120, 3), dtype=np.uint8)
    groups = [{"text_group_id": 7, "fallback_rect": [25, 25, 20, 20], "text": "あ"}]
    manual = [{"type": "unboxed", "unboxed_rect": [20, 20, 50, 40]}]

    crops = _manual_unboxed_crops(image, manual, groups, 120, 120)

    assert crops[0]["type"] == "unboxed"
    assert crops[0]["source_method"] == "manual_ubox_guide"
    assert crops[0]["source_region_type"] == "manual_ubox"
    assert crops[0]["anchor_ids"] == [7]
    assert crops[0]["source_rect"] == [20, 20, 50, 40]
    assert crops[0]["inpaint_skipped"] is False
    assert crops[0]["inpaint_engine_override"] is None

    forced = _manual_unboxed_crops(image, [{**manual[0], "inpaint_engine": "lama"}], groups, 120, 120)
    assert forced[0]["inpaint_engine_override"] == "lama"


def test_manual_ubox_text_mask_covers_full_manual_rect_for_furigana():
    image = np.full((90, 90, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (25, 25), (35, 55), (0, 0, 0), -1)
    cv2.rectangle(image, (70, 25), (78, 55), (0, 0, 0), -1)
    cv2.line(image, (58, 20), (58, 60), (215, 215, 215), 1)
    groups = [{"text_group_id": 7, "fallback_rect": [24, 24, 14, 34], "text": "あ"}]

    rect, mask = _manual_ubox_text_mask(image, [20, 20, 65, 45], groups)

    assert rect == [20, 20, 65, 45]
    assert mask is not None
    assert mask[10, 10] > 0
    assert mask[10, 38] > 0
    assert mask[10, 54] > 0


def test_padded_context_rect_expands_manual_ubox_outward():
    image = np.zeros((100, 120, 3), dtype=np.uint8)

    rect = _padded_context_rect([40, 30, 20, 10], image.shape, padding=12)

    assert rect == [28, 18, 44, 34]


def test_manual_ubox_lama_erases_bright_text_on_dark_background(monkeypatch):
    image = np.full((140, 140, 3), 20, dtype=np.uint8)
    cv2.rectangle(image, (70, 65), (80, 85), (245, 245, 245), -1)
    masked = image.copy()
    item = {
        "type": "unboxed",
        "source_rect": [66, 61, 22, 30],
        "erase_rect": [66, 61, 22, 30],
        "anchor_ids": [7],
        "quality_flags": ["manual_ubox"],
    }
    group_by_id = {7: {"text_group_id": 7, "fallback_rect": [68, 62, 16, 28], "text": "あ"}}
    captured = {}

    class FakeLama:
        def __call__(self, image_pil, mask_pil):
            roi = np.array(image_pil).copy()
            mask = np.array(mask_pil)
            captured["roi_shape"] = roi.shape[:2]
            captured["mask_shape"] = mask.shape
            roi[mask > 0] = [180, 180, 180]
            return Image.fromarray(roi)

    monkeypatch.setattr("pipeline.dialogue_extractor._get_lama", lambda: FakeLama())

    applied = _apply_manual_ubox_lama(image, masked, item, group_by_id)

    assert applied is True
    assert item["inpaint_skipped"] is False
    assert item["manual_inpaint_background_class"] == "dark_bg"
    assert item["manual_inpaint_engine"] == "lama"
    assert item["manual_mask_polarity"] == "bright"
    assert "manual_ubox_lama" in item["quality_flags"]
    assert item["inpaint_context_rect"] == [42, 37, 70, 78]
    assert item["inpaint_context_rect"][2] > item["erase_rect"][2]
    assert captured["roi_shape"] == captured["mask_shape"]
    assert (masked[75, 75] == [180, 180, 180]).all()
    assert (masked[12, 22] == [20, 20, 20]).all()


def test_manual_ubox_uses_telea_for_white_background(monkeypatch):
    image = np.full((140, 140, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (70, 65), (80, 85), (0, 0, 0), -1)
    masked = image.copy()
    item = {
        "type": "unboxed",
        "source_rect": [66, 61, 22, 30],
        "erase_rect": [66, 61, 22, 30],
        "anchor_ids": [7],
        "quality_flags": ["manual_ubox"],
    }
    group_by_id = {7: {"text_group_id": 7, "fallback_rect": [68, 62, 16, 28], "text": "あ"}}
    captured = {}

    def fake_inpaint(roi, mask, radius, method):
        captured["radius"] = radius
        captured["method"] = method
        out = roi.copy()
        out[mask > 0] = [210, 210, 210]
        return out

    monkeypatch.setattr("pipeline.dialogue_extractor.cv2.inpaint", fake_inpaint)
    monkeypatch.setattr("pipeline.dialogue_extractor._get_lama", lambda: (_ for _ in ()).throw(AssertionError("LaMa should not be used")))

    applied = _apply_manual_ubox_lama(image, masked, item, group_by_id)

    assert applied is True
    assert item["manual_inpaint_background_class"] == "white_flat"
    assert item["manual_inpaint_engine"] == "telea"
    assert item["manual_mask_polarity"] == "dark"
    assert "manual_ubox_telea" in item["quality_flags"]
    assert captured["method"] == cv2.INPAINT_TELEA
    assert (masked[75, 75] == [210, 210, 210]).all()


def test_manual_ubox_lama_override_beats_white_background_telea(monkeypatch):
    image = np.full((140, 140, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (70, 65), (80, 85), (0, 0, 0), -1)
    masked = image.copy()
    item = {
        "type": "unboxed",
        "source_rect": [66, 61, 22, 30],
        "erase_rect": [66, 61, 22, 30],
        "anchor_ids": [7],
        "quality_flags": ["manual_ubox"],
        "inpaint_engine_override": "lama",
    }
    group_by_id = {7: {"text_group_id": 7, "fallback_rect": [68, 62, 16, 28], "text": "あ"}}

    class FakeLama:
        def __call__(self, image_pil, mask_pil):
            arr = np.array(image_pil).copy()
            arr[np.array(mask_pil) > 0] = [200, 200, 200]
            return Image.fromarray(arr)

    monkeypatch.setattr("pipeline.dialogue_extractor.cv2.inpaint", lambda *a: (_ for _ in ()).throw(AssertionError("Telea should not be used")))
    monkeypatch.setattr("pipeline.dialogue_extractor._get_lama", lambda: FakeLama())

    assert _apply_manual_ubox_lama(image, masked, item, group_by_id) is True
    assert item["manual_inpaint_background_class"] == "white_flat"
    assert item["manual_inpaint_engine"] == "lama"
    assert (masked[75, 75] == [200, 200, 200]).all()


def test_manual_ubox_does_not_consolidate_with_adjacent_unboxed_crop():
    image = np.zeros((140, 140, 3), dtype=np.uint8)
    manual = {
        "type": "unboxed",
        "source_rect": [20, 20, 40, 40],
        "erase_rect": [20, 20, 40, 40],
        "crop": image[20:60, 20:60].copy(),
        "anchor_ids": [1],
        "consolidated_text_group": {"text_group_ids": [1], "text": "あ", "lines": [], "tight_rect": [20, 20, 40, 40]},
        "source_method": "manual_ubox_guide",
        "source_region_type": "manual_ubox",
        "quality_flags": ["manual_ubox"],
        "review_tags": ["manual_ubox"],
    }
    auto = {
        "type": "unboxed",
        "source_rect": [64, 22, 35, 38],
        "erase_rect": [64, 22, 35, 38],
        "crop": image[22:60, 64:99].copy(),
        "anchor_ids": [2],
        "consolidated_text_group": {"text_group_ids": [2], "text": "い", "lines": [], "tight_rect": [64, 22, 35, 38]},
        "source_method": "bbox_fallback",
        "source_region_type": "bbox_fallback",
        "quality_flags": [],
        "review_tags": [],
    }

    crops = _apply_manual_unboxed_overrides(image, [manual, auto], 140, 140)

    assert len(crops) == 2
    assert [item["source_rect"] for item in crops] == [[20, 20, 40, 40], [64, 22, 35, 38]]


def test_manual_ubox_replaces_fully_overlapped_unboxed_crop():
    image = np.zeros((160, 160, 3), dtype=np.uint8)
    manual = {
        "type": "unboxed",
        "source_rect": [20, 20, 100, 100],
        "erase_rect": [20, 20, 100, 100],
        "crop": image[20:120, 20:120].copy(),
        "anchor_ids": [1, 2],
        "consolidated_text_group": {"text_group_ids": [1, 2], "text": "あい", "lines": [], "tight_rect": [20, 20, 100, 100]},
        "source_method": "manual_ubox_guide",
        "source_region_type": "manual_ubox",
        "quality_flags": ["manual_ubox"],
        "review_tags": ["manual_ubox"],
    }
    auto = {
        "type": "unboxed",
        "source_rect": [42, 44, 30, 36],
        "erase_rect": [42, 44, 30, 36],
        "crop": image[44:80, 42:72].copy(),
        "anchor_ids": [1],
        "consolidated_text_group": {"text_group_ids": [1], "text": "あ", "lines": [], "tight_rect": [42, 44, 30, 36]},
        "source_method": "bbox_fallback",
        "source_region_type": "bbox_fallback",
        "quality_flags": [],
        "review_tags": [],
    }

    crops = _apply_manual_unboxed_overrides(image, [manual, auto], 160, 160)

    assert len(crops) == 1
    assert crops[0]["source_rect"] == [20, 20, 100, 100]
    assert crops[0]["anchor_ids"] == [1, 2]
    assert "manual_ubox_replaced_overlapped_u" in crops[0]["quality_flags"]


def test_manual_ubox_does_not_consolidate_with_distant_unboxed_crop():
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    manual = {
        "type": "unboxed",
        "source_rect": [10, 10, 20, 20],
        "erase_rect": [10, 10, 20, 20],
        "crop": image[10:30, 10:30].copy(),
        "anchor_ids": [1],
        "consolidated_text_group": {"text_group_ids": [1], "text": "あ", "lines": [], "tight_rect": [10, 10, 20, 20]},
        "source_method": "manual_ubox_guide",
        "source_region_type": "manual_ubox",
        "quality_flags": ["manual_ubox"],
        "review_tags": ["manual_ubox"],
    }
    distant = {
        "type": "unboxed",
        "source_rect": [120, 120, 20, 20],
        "erase_rect": [120, 120, 20, 20],
        "crop": image[120:140, 120:140].copy(),
        "anchor_ids": [2],
        "consolidated_text_group": {"text_group_ids": [2], "text": "い", "lines": [], "tight_rect": [120, 120, 20, 20]},
        "source_method": "bbox_fallback",
        "source_region_type": "bbox_fallback",
        "quality_flags": [],
        "review_tags": [],
    }

    crops = _apply_manual_unboxed_overrides(image, [manual, distant], 200, 200)

    assert len(crops) == 2


def test_manual_bubble_seed_replaces_region_containing_seed():
    manual = {"is_valid": True, "method": "manual_bubble_seed_rescue", "anchor_ids": [], "seed_points": [[30, 30]]}
    auto = {"is_valid": True, "method": "edge_barrier_rescue", "anchor_ids": [1], "bubble_rect": [10, 10, 50, 50]}
    regions = [manual, auto]

    _invalidate_regions_replaced_by_manual_edge(regions)

    assert manual["is_valid"] is True
    assert auto["is_valid"] is False
    assert "replaced_by_manual_rescue" in auto["quality_flags"]


def test_manual_bubble_seed_regions_do_not_invalidate_each_other():
    left = {
        "is_valid": True,
        "method": "manual_bubble_seed_rescue",
        "anchor_ids": [1, 2],
        "seed_points": [[35, 50]],
        "bubble_rect": [10, 10, 60, 90],
    }
    right = {
        "is_valid": True,
        "method": "manual_bubble_seed_rescue",
        "anchor_ids": [1, 2],
        "seed_points": [[135, 50]],
        "bubble_rect": [110, 10, 60, 90],
    }
    auto = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [1],
        "bubble_rect": [12, 12, 55, 85],
        "quality_flags": [],
    }
    regions = [left, right, auto]

    _invalidate_regions_replaced_by_manual_edge(regions)

    assert left["is_valid"] is True
    assert right["is_valid"] is True
    assert auto["is_valid"] is False


def test_manual_edge_replaces_overlapped_auto_split_children():
    manual = {
        "is_valid": True,
        "method": "manual_edge_barrier_rescue",
        "anchor_ids": [],
        "bubble_rect": [10, 10, 120, 120],
        "quality_flags": [],
    }
    split_child = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [],
        "bubble_rect": [20, 20, 40, 50],
        "split_parent_rect": [15, 15, 100, 100],
        "quality_flags": ["auto_split_child"],
    }
    distant = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [],
        "bubble_rect": [180, 20, 40, 50],
        "quality_flags": [],
    }

    _invalidate_regions_replaced_by_manual_edge([manual, split_child, distant])

    assert split_child["is_valid"] is False
    assert "replaced_by_manual_rescue" in split_child["quality_flags"]
    assert distant["is_valid"] is True


def test_manual_edge_with_empty_seed_points_does_not_crash():
    manual = {
        "is_valid": True,
        "method": "manual_edge_barrier_rescue",
        "anchor_ids": [],
        "seed_points": [],
        "bubble_rect": [10, 10, 120, 120],
        "quality_flags": [],
    }
    distant = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [],
        "bubble_rect": [180, 20, 40, 50],
        "quality_flags": [],
    }

    _invalidate_regions_replaced_by_manual_edge([manual, distant])

    assert manual["is_valid"] is True
    assert distant["is_valid"] is True


def test_split_child_uses_parent_mask_for_bubble_erasure():
    parent = np.zeros((80, 100), dtype=np.uint8)
    parent[10:70, 10:90] = 255
    child = np.zeros_like(parent)
    child[10:70, 10:48] = 255
    region = {
        "method": "manual_edge_barrier_rescue",
        "split_parent_rect": [10, 10, 80, 60],
        "split_parent_mask": parent,
    }

    erasure_mask = _mask_for_bubble_erasure(region, child)

    assert erasure_mask[40, 75] == 255
    assert erasure_mask[40, 25] == 255


def test_edge_erasure_mask_closes_small_internal_gap():
    mask = np.zeros((120, 120), dtype=np.uint8)
    mask[20:100, 20:100] = 255
    mask[20:100, 58:62] = 0
    region = {"method": "manual_edge_barrier_rescue"}

    erasure_mask = _mask_for_bubble_erasure(region, mask)

    assert erasure_mask[60, 60] == 255


def test_manual_edge_does_not_replace_other_manual_guides_by_overlap():
    edge = {
        "is_valid": True,
        "method": "manual_edge_barrier_rescue",
        "anchor_ids": [],
        "bubble_rect": [10, 10, 120, 120],
    }
    manual_bubble = {
        "is_valid": True,
        "method": "manual_bubble_guide",
        "anchor_ids": [],
        "bubble_rect": [20, 20, 40, 50],
        "quality_flags": [],
    }

    _invalidate_regions_replaced_by_manual_edge([edge, manual_bubble])

    assert manual_bubble["is_valid"] is True


def test_manual_ubox_override_invalidates_overlapped_auto_bubble():
    auto = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [1],
        "bubble_rect": [20, 20, 80, 100],
        "quality_flags": [],
    }

    _invalidate_regions_replaced_by_manual_overrides(
        [auto],
        [{"type": "unboxed", "unboxed_rect": [15, 15, 90, 110]}],
        [],
    )

    assert auto["is_valid"] is False
    assert "manual_unboxed_override" in auto["quality_flags"]


def test_manual_edge_box_alone_does_not_invalidate_auto_split_parent():
    split_child = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [],
        "bubble_rect": [30, 30, 30, 40],
        "split_parent_rect": [20, 20, 90, 100],
        "quality_flags": [],
    }
    walls = [
        {"box_id": "edge_box_1", "line": [[10, 10], [120, 10]]},
        {"box_id": "edge_box_1", "line": [[120, 10], [120, 140]]},
        {"box_id": "edge_box_1", "line": [[120, 140], [10, 140]]},
        {"box_id": "edge_box_1", "line": [[10, 140], [10, 10]]},
    ]

    _invalidate_regions_replaced_by_manual_overrides([split_child], [], walls)

    assert split_child["is_valid"] is True
    assert "manual_edge_override" not in split_child["quality_flags"]


def test_small_manual_edge_box_inside_large_auto_bubble_does_not_kill_parent():
    large_auto = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [2, 3, 8, 9],
        "bubble_rect": [40, 40, 260, 360],
        "quality_flags": [],
    }
    walls = [
        {"box_id": "edge_box_1", "line": [[245, 270], [315, 270]]},
        {"box_id": "edge_box_1", "line": [[315, 270], [315, 370]]},
        {"box_id": "edge_box_1", "line": [[315, 370], [245, 370]]},
        {"box_id": "edge_box_1", "line": [[245, 370], [245, 270]]},
    ]

    _invalidate_regions_replaced_by_manual_overrides([large_auto], [], walls)

    assert large_auto["is_valid"] is True


def test_small_manual_edge_rescue_inside_large_auto_bubble_does_not_kill_parent():
    manual = {
        "is_valid": True,
        "method": "manual_edge_barrier_rescue",
        "anchor_ids": [],
        "bubble_rect": [245, 270, 70, 100],
        "quality_flags": [],
    }
    large_auto = {
        "is_valid": True,
        "method": "bubble",
        "anchor_ids": [2, 3, 8, 9],
        "bubble_rect": [40, 40, 260, 360],
        "quality_flags": [],
    }

    _invalidate_regions_replaced_by_manual_edge([manual, large_auto])

    assert large_auto["is_valid"] is True


def test_manual_seed_anchor_ids_are_refined_by_rescue_mask():
    mask = np.zeros((120, 220), dtype=np.uint8)
    mask[15:95, 10:80] = 255
    groups = [
        {"text_group_id": 1, "fallback_rect": [25, 35, 18, 32]},
        {"text_group_id": 2, "fallback_rect": [145, 35, 18, 32]},
    ]

    anchor_ids = _manual_seed_anchor_ids_from_result([10, 15, 70, 80], mask, groups)

    assert anchor_ids == [1]


def test_manual_seed_direct_flood_uses_seed_region_not_tg_sized_roi():
    image = np.full((280, 180, 3), 238, dtype=np.uint8)
    cv2.ellipse(image, (90, 140), (54, 108), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(image, (90, 140), (54, 108), 0, 0, 360, (0, 0, 0), 3)
    cv2.putText(image, "A", (78, 122), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2, cv2.LINE_AA)

    result = _manual_seed_direct_flood(image, [94, 150])

    assert result["success"] is True
    x, y, w, h = result["rescue_rect"]
    assert x <= 40
    assert y <= 35
    assert w >= 95
    assert h >= 195
    assert result["reason"] == "manual_seed_bright_component_ok"


def test_manual_seed_direct_flood_rejects_panel_sized_leak():
    image = np.full((240, 360, 3), (225, 242, 255), dtype=np.uint8)
    cv2.rectangle(image, (4, 4), (356, 236), (0, 0, 0), 3)
    cv2.ellipse(image, (290, 72), (34, 58), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(image, (290, 72), (34, 58), 0, 0, 360, (0, 0, 0), 3)
    cv2.putText(image, "oi", (276, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)

    result = _manual_seed_direct_flood(image, [292, 80])

    assert result["success"] is True
    x, y, w, h = result["rescue_rect"]
    assert w < 120
    assert h < 150
    assert result["score"]["mask_page_ratio"] < 0.12


def test_manual_seed_bright_component_prefers_closed_white_bubble():
    image = np.zeros((160, 160, 3), dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (159, 159), (235, 235, 235), -1)
    cv2.ellipse(image, (82, 78), (34, 52), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(image, (82, 78), (34, 52), 0, 0, 360, (0, 0, 0), 4)
    cv2.putText(image, "oi", (68, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)

    result = _manual_seed_bright_component(image, [96, 96])

    assert result is not None
    assert result["reason"] == "manual_seed_bright_component_ok"
    x, y, w, h = result["rescue_rect"]
    assert 40 <= x <= 55
    assert 20 <= y <= 35
    assert 55 <= w <= 75
    assert 90 <= h <= 115
    assert result["score"]["mask_page_ratio"] < 0.25


def test_manual_seed_regions_drop_panel_sized_fallback(monkeypatch):
    image = np.full((240, 360, 3), 245, dtype=np.uint8)
    groups = [{"text_group_id": 1, "fallback_rect": [20, 20, 40, 120], "text": "あ"}]

    monkeypatch.setattr(
        "pipeline.dialogue_extractor._manual_seed_direct_flood",
        lambda *args, **kwargs: {"success": False, "reason": "forced"},
    )
    monkeypatch.setattr(
        "pipeline.edge_barrier_flood_rescue.run_edge_barrier_rescue_auto",
        lambda *args, **kwargs: {
            "success": True,
            "reason": "leaked_panel",
            "seed": [200, 100],
            "rescue_rect": [5, 5, 350, 220],
            "mask": np.full((240, 360), 255, dtype=np.uint8),
            "score": {"mask_page_ratio": 0.89, "rect_anchor_ratio": 0.0, "rank_score": 1.0},
            "best_label": "panel",
        },
    )

    regions = _manual_seed_bubble_regions(image, groups, [{"point": [200, 100]}], [])

    assert regions == []


def test_seed_point_accepts_bubble_seed_guidance():
    assert _seed_point({"point": [12, 34]}) == [12, 34]


def test_manual_bubble_seed_region_can_be_split_after_flood():
    mask = np.zeros((120, 120), dtype=np.uint8)
    mask[10:110, 10:110] = 255
    regions = [{
        "is_valid": True,
        "method": "manual_bubble_seed_rescue",
        "anchor_ids": [1, 2],
        "seed_points": [[30, 50]],
        "bubble_rect": [10, 10, 100, 100],
        "mask": mask,
        "quality_flags": ["manual_bubble_seed"],
    }]
    groups = [
        {"text_group_id": 1, "fallback_rect": [20, 40, 16, 20]},
        {"text_group_id": 2, "fallback_rect": [82, 42, 16, 20]},
    ]
    split_guides = [{"line": [[60, 0], [60, 120]]}]

    split = _split_manual_seed_regions(regions, groups, split_guides)

    assert len(split) == 2
    assert [item["split_parent_rect"] for item in split] == [[10, 10, 100, 100], [10, 10, 100, 100]]
    assert all(item["split_line"] == [[60, 0], [60, 120]] for item in split)
    assert sorted([tuple(item["anchor_ids"]) for item in split]) == [(1,), (2,)]
    assert all("manual_split_guide" in item["quality_flags"] for item in split)


def test_manual_split_guide_drops_empty_rect_fallback_piece():
    mask = np.zeros((120, 160), dtype=np.uint8)
    mask[15:105, 20:140] = 255
    regions = [{
        "is_valid": True,
        "method": "manual_edge_barrier_rescue",
        "anchor_ids": [4, 7],
        "bubble_rect": [20, 15, 120, 90],
        "mask": mask,
        "quality_flags": ["manual_edge_barrier"],
    }]
    groups = [
        {"text_group_id": 4, "fallback_rect": [94, 35, 22, 34]},
        {"text_group_id": 7, "fallback_rect": [42, 36, 22, 36]},
    ]
    split_guides = [{"line": [[76, 103], [134, 103]]}]

    split = _split_manual_seed_regions(regions, groups, split_guides)

    assert len(split) == 1
    assert split[0]["anchor_ids"] == [4, 7]
    assert split[0]["split_parent_rect"] == [20, 15, 120, 90]
    assert split[0]["split_line"] == [[76, 103], [134, 103]]
    assert "manual_split_rect_fallback" in split[0]["quality_flags"]


def test_manual_split_guide_splits_region_without_mask_by_rect():
    regions = [{
        "is_valid": True,
        "source_method": "edge_barrier_rescue",
        "anchor_ids": [4, 7],
        "bubble_rect": [776, 844, 400, 353],
        "quality_flags": ["edge_barrier_rescue"],
    }]
    groups = [
        {"text_group_id": 4, "fallback_rect": [1046, 878, 98, 224]},
        {"text_group_id": 7, "fallback_rect": [825, 885, 125, 310]},
    ]
    split_guides = [{"line": [[997, 870], [999, 1136]]}]

    split = _split_manual_seed_regions(regions, groups, split_guides)

    assert len(split) == 2
    assert sorted([tuple(item["anchor_ids"]) for item in split]) == [(4,), (7,)]
    assert all(item["split_parent_rect"] == [776, 844, 400, 353] for item in split)
    assert all(item["split_line"] == [[997, 870], [999, 1136]] for item in split)
    assert all("manual_split_rect_fallback" in item["quality_flags"] for item in split)


def test_manual_split_guide_uses_missing_tg_inside_parent_rect():
    regions = [{
        "is_valid": True,
        "source_method": "edge_barrier_rescue",
        "anchor_ids": [5],
        "bubble_rect": [768, 135, 290, 365],
        "quality_flags": ["edge_barrier_rescue"],
    }]
    groups = [
        {"text_group_id": 1, "fallback_rect": [998, 170, 31, 128]},
        {"text_group_id": 5, "fallback_rect": [822, 258, 111, 188]},
    ]
    split_guides = [{"line": [[962, 236], [998, 327]]}]

    split = _split_manual_seed_regions(regions, groups, split_guides, image_shape=(1200, 1200))

    assert len(split) == 2
    assert sorted([tuple(item["anchor_ids"]) for item in split]) == [(1,), (5,)]
    assert all(item["split_parent_rect"] == [768, 135, 290, 365] for item in split)
    assert all("manual_split_rect_fallback" in item["quality_flags"] for item in split)


def test_manual_split_guide_splits_geometry_even_with_single_anchor_region():
    regions = [{
        "is_valid": True,
        "source_method": "edge_barrier_rescue",
        "anchor_ids": [5],
        "bubble_rect": [768, 135, 290, 365],
        "quality_flags": ["edge_barrier_rescue"],
    }]
    groups = [
        {"text_group_id": 5, "fallback_rect": [822, 258, 111, 188]},
    ]
    split_guides = [{"line": [[962, 236], [998, 327]]}]

    split = _split_manual_seed_regions(regions, groups, split_guides, image_shape=(1200, 1200))

    assert len(split) == 1
    assert sorted([tuple(item["anchor_ids"]) for item in split]) == [(5,)]
    assert all(item["split_parent_rect"] == [768, 135, 290, 365] for item in split)


def test_manual_split_line_snaps_to_nearby_defect_pair():
    mask = np.zeros((140, 180), dtype=np.uint8)
    cv2.ellipse(mask, (60, 75), (42, 55), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (120, 65), (38, 45), 0, 0, 360, 255, -1)
    cv2.rectangle(mask, (86, 54), (96, 76), 255, -1)
    anchor_centers = [(55, 75), (123, 64)]
    anchor_rects = [[45, 65, 18, 24], [115, 52, 18, 24]]

    p1, p2, source = _snap_manual_line_to_defects(
        mask,
        anchor_centers,
        anchor_rects,
        (82, 55),
        (98, 85),
    )

    assert source == "manual_split_defect_snap"
    assert [p1, p2] != [(82, 55), (98, 85)]
    assert abs(p1[0] - p2[0]) <= 12
    assert p1[1] < 45
    assert p2[1] > 95


def test_manual_seed_projection_neck_split_recovers_orphan_lobe():
    mask = np.zeros((120, 150), dtype=np.uint8)
    cv2.ellipse(mask, (45, 62), (32, 45), 0, 0, 360, 255, -1)
    cv2.ellipse(mask, (105, 48), (30, 34), 0, 0, 360, 255, -1)
    cv2.rectangle(mask, (70, 44), (82, 64), 255, -1)

    split = _split_mask_by_projection_neck(mask, [13], [(45, 62)])

    assert split is not None
    assert len(split) == 2
    assert sorted([tuple(item["anchor_ids"]) for item in split]) == [(), (13,)]
    assert all(item["split_line"] for item in split)


def test_final_analysis_overlay_fills_bubble_and_edge_regions(tmp_path):
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    bubble_mask = np.zeros((80, 100), dtype=np.uint8)
    edge_mask = np.zeros((80, 100), dtype=np.uint8)
    bubble_mask[38:65, 10:35] = 255
    edge_mask[45:70, 55:85] = 255
    bubble_mask_path = tmp_path / "bubble_mask.png"
    edge_mask_path = tmp_path / "edge_mask.png"
    cv2.imwrite(str(bubble_mask_path), bubble_mask)
    cv2.imwrite(str(edge_mask_path), edge_mask)
    vis_path = tmp_path / "vis.jpg"

    _overlay_final_unboxed_regions(
        image,
        str(vis_path),
        [
            {
                "region_id": 1,
                "type": "bubble",
                "bubble_rect": [10, 38, 25, 27],
                "mask_ref": str(bubble_mask_path),
                "source_method": "bubble",
            },
            {
                "region_id": 2,
                "type": "bubble",
                "bubble_rect": [55, 45, 30, 25],
                "mask_ref": str(edge_mask_path),
                "source_method": "edge_barrier_rescue",
            },
        ],
    )

    vis = cv2.imread(str(vis_path))
    bubble_px = vis[50, 20]
    edge_px = vis[55, 65]

    assert bubble_px[0] > bubble_px[1]
    assert edge_px[1] > edge_px[0]


def test_dedupe_removes_unboxed_crop_inside_bubble():
    bubble = {
        "type": "bubble",
        "source_rect": [10, 10, 100, 100],
        "anchor_ids": [4],
        "source_method": "edge_barrier_rescue",
    }
    unboxed = {
        "type": "unboxed",
        "source_rect": [30, 30, 20, 30],
        "erase_rect": [30, 30, 20, 30],
        "anchor_ids": [2],
        "source_method": "bbox_fallback",
    }

    crops = _dedupe_dialogue_crops([bubble, unboxed])

    assert crops == [bubble]


def test_dedupe_removes_short_unboxed_fragment_inside_unboxed_parent():
    fragment = {
        "type": "unboxed",
        "source_rect": [1031, 844, 55, 57],
        "erase_rect": [1031, 844, 55, 57],
        "anchor_ids": [1],
        "source_method": "bbox_fallback",
        "consolidated_text_group": {"text": "を"},
    }
    parent = {
        "type": "unboxed",
        "source_rect": [928, 578, 209, 326],
        "erase_rect": [928, 578, 209, 326],
        "anchor_ids": [2],
        "source_method": "bbox_fallback",
        "consolidated_text_group": {"text": "星詠族はそのアストラル体を人間とは違った扱い方をする種族だ"},
    }

    crops = _dedupe_dialogue_crops([fragment, parent])

    assert crops == [parent]
    assert "deduped_contained_unboxed_fragment" in parent["quality_flags"]


def test_dedupe_keeps_long_unboxed_child_inside_unboxed_parent():
    child = {
        "type": "unboxed",
        "source_rect": [100, 100, 80, 120],
        "erase_rect": [100, 100, 80, 120],
        "anchor_ids": [1],
        "source_method": "bbox_fallback",
        "consolidated_text_group": {"text": "長い台詞"},
    }
    parent = {
        "type": "unboxed",
        "source_rect": [80, 80, 180, 220],
        "erase_rect": [80, 80, 180, 220],
        "anchor_ids": [2],
        "source_method": "bbox_fallback",
        "consolidated_text_group": {"text": "別の長い台詞"},
    }

    crops = _dedupe_dialogue_crops([child, parent])

    assert crops == [child, parent]


def test_dedupe_prefers_edge_rescue_over_overlapping_bubble():
    regular = {
        "type": "bubble",
        "source_rect": [20, 20, 80, 120],
        "anchor_ids": [4],
        "source_method": "bubble",
    }
    edge = {
        "type": "bubble",
        "source_rect": [18, 18, 86, 126],
        "anchor_ids": [4],
        "source_method": "edge_barrier_rescue",
    }

    crops = _dedupe_dialogue_crops([regular, edge])

    assert crops == [edge]


def test_lama_process_slot_holds_exclusive_lock(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    import pipeline.dialogue_extractor as de

    monkeypatch.setattr(de, "_LAMA_SLOT_DIR", tmp_path)
    monkeypatch.setattr(de, "_lama_slot_handle", None)
    monkeypatch.setenv("LAMA_MAX_PROCESSES", "1")

    de._acquire_lama_process_slot()
    try:
        assert de._lama_slot_handle is not None
        # 다른 프로세스처럼 같은 슬롯 파일을 따로 열면 잠글 수 없어야 한다.
        with open(tmp_path / "slot0.lock", "a") as other:
            with pytest.raises(OSError):
                fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        de._lama_slot_handle.close()


@pytest.mark.parametrize("valid", [True, False])
def test_region_exclusion_never_resurrects_as_unboxed(tmp_path, monkeypatch, valid):
    """Region Off는 경계 검출과 텍스트 기반 fallback 모두에서 제외한다."""
    import json
    import pipeline.dialogue_extractor as de
    image = np.full((180, 180, 3), 255, dtype=np.uint8)
    image_path = tmp_path / "page.png"
    cv2.imwrite(str(image_path), image)
    groups = [{"text_group_id": 3, "fallback_rect": [40, 40, 30, 60], "text": "こんにちは"}]
    groups_path = tmp_path / "groups.json"
    (tmp_path / "json").mkdir()
    groups_path.write_text(json.dumps(groups))
    region = {"region_id": 8, "anchor_ids": [3], "is_valid": valid,
              "method": "bbox_fallback", "bubble_rect": [35, 35, 40, 70]}
    monkeypatch.setattr(de, "build_ocr_region_sheet", lambda **kwargs: {"regions": [region]})
    monkeypatch.setattr(de, "_classify_text_group_style", lambda *args: {"include_policy": "include"})
    captured = []
    def cluster(items, **kwargs):
        captured.extend(items)
        return []
    monkeypatch.setattr(de, "_cluster_adjacent_tgs", cluster)
    monkeypatch.setattr(de, "_get_lama", lambda: pytest.fail("삭제한 영역에 LaMa를 호출하면 안 됩니다"))
    result = de.build_dialogue_sheet(str(image_path), str(groups_path), str(tmp_path / "masked.png"),
        str(tmp_path / "json/regions.json"), str(tmp_path / "sheet.png"), str(tmp_path / "vis.png"),
        suppressed_regions=[{"source_region_id": 8, "anchor_ids": [3], "rect": [35, 35, 40, 70]}])
    assert captured == []
    assert result["regions"] == [] and result["ocr_region_count"] == 0
    assert np.array_equal(cv2.imread(str(tmp_path / "masked.png")), image)


def test_unboxed_exclusion_uses_saved_anchors_and_geometry():
    from pipeline.dialogue_extractor import _suppressed_text_group_ids
    groups = [{"text_group_id": 3, "fallback_rect": [20, 20, 30, 40]},
              {"text_group_id": 4, "fallback_rect": [20, 20, 30, 40]},
              {"text_group_id": 5, "fallback_rect": [130, 20, 30, 40]}]
    assert _suppressed_text_group_ids(groups, [], [{"anchor_ids": [3, 5], "rect": [10, 10, 60, 60]}]) == {3}
