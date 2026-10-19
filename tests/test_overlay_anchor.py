# 글자 입히기 수동값이 region 번호가 바뀌어도 원래 말풍선을 따라가는지 검증한다.
from pipeline.overlay_anchor import (
    ANCHOR_FIELD,
    UNMATCHED_FIELD,
    region_rects_from_integrated,
    remap_overlay_guidance,
    stamp_overlay_anchors,
)

OLD = {1: [1100, 100, 120, 190], 2: [1000, 1000, 170, 300], 3: [200, 400, 340, 580]}
# Ubox 하나가 끼어 번호가 섞인 뒤: 예전 1→2, 2→5, 3→3, 새 영역 1·4
NEW = {1: [1250, 40, 70, 70], 2: [1100, 100, 120, 190], 3: [200, 400, 340, 580], 4: [600, 100, 200, 300], 5: [1002, 1000, 170, 300]}


def _guidance():
    return {
        "regions": {
            "1": {"text_override": "안심해라"},
            "2": {"text_override": "진심이에요?", "font_size": 37},
            "3:1": {"font_style": "impact"},
        },
        "eraser_rects": [[1, 2, 3, 4]],
    }


def test_override_follows_region_after_renumbering():
    stamped, count = stamp_overlay_anchors(_guidance(), OLD)
    assert count == 3
    remapped, report = remap_overlay_guidance(stamped, NEW)

    assert report["moved"] == {"1": "2", "2": "5"}
    regions = remapped["regions"]
    assert regions["2"]["text_override"] == "안심해라"
    assert regions["5"]["text_override"] == "진심이에요?"
    assert regions["3:1"]["font_style"] == "impact"  # 분할 조각 접미사 유지
    assert "1" not in regions  # 새로 끼어든 영역에는 아무것도 붙지 않는다
    assert regions["5"][ANCHOR_FIELD]["region_id"] == 5
    assert remapped["eraser_rects"] == [[1, 2, 3, 4]]


def test_override_without_matching_region_is_kept_aside():
    stamped, _ = stamp_overlay_anchors(_guidance(), OLD)
    remapped, report = remap_overlay_guidance(stamped, {9: [0, 0, 50, 50]})

    assert remapped["regions"] == {}
    assert report["unmatched"] == ["1", "2", "3:1"]
    assert remapped[UNMATCHED_FIELD]["2"]["text_override"] == "진심이에요?"


def test_legacy_override_without_anchor_stays_unless_key_taken():
    guidance = {"regions": {"2": {"font_size": 20}, "7": {"font_size": 30, ANCHOR_FIELD: {"rect": OLD[1], "region_id": 7}}}}
    remapped, report = remap_overlay_guidance(guidance, NEW)

    assert report["moved"] == {"7": "2"}
    assert remapped["regions"]["2"]["font_size"] == 30
    assert remapped[UNMATCHED_FIELD]["2"]["font_size"] == 20
    assert remapped[UNMATCHED_FIELD]["2"]["_unmatched_reason"] == "legacy_key_taken"


def test_no_regions_in_new_integrated_leaves_guidance_untouched():
    stamped, _ = stamp_overlay_anchors(_guidance(), OLD)
    remapped, report = remap_overlay_guidance(stamped, {})
    assert remapped is stamped
    assert report == {"moved": {}, "unmatched": []}


def test_stamp_keeps_existing_anchor_and_skips_unknown_region():
    guidance = {"regions": {"1": {ANCHOR_FIELD: {"rect": [0, 0, 1, 1], "region_id": 1}}, "8": {"font_size": 10}}}
    stamped, count = stamp_overlay_anchors(guidance, OLD)
    assert count == 0
    assert stamped["regions"]["1"][ANCHOR_FIELD]["rect"] == [0, 0, 1, 1]


def test_region_rects_union_segments_of_same_region():
    integrated = {"clusters": [
        {"source_region_id": 7, "union_bbox": [10, 10, 20, 20]},
        {"source_region_id": 7, "union_bbox": [25, 5, 10, 10]},
        {"source_region_id": None, "union_bbox": [0, 0, 5, 5]},
    ]}
    assert region_rects_from_integrated(integrated) == {7: [10, 5, 25, 25]}
