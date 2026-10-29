"""pipeline/region_refs.py 단위 테스트. 합성 JSON만 쓴다."""
from __future__ import annotations

import unittest

from pipeline import region_refs as rr


def region(rid: int, tgs: list[int], rect: list[int], **extra) -> dict:
    return {"region_id": rid, "type": "bubble", "anchor_ids": tgs, "bubble_rect": rect, **extra}


def cluster(number: int, tgs: list[int], rect: list[int], segment: int | None = None, sentence: int | None = None) -> dict:
    item = {
        "source_region_id": number,
        "matched_cluster": tgs,
        "union_bbox": rect,
        "sentence_id": sentence if sentence is not None else number * 100 + (segment or 0),
    }
    if segment is not None:
        item["source_segment_index"] = segment
    return item


R_A, R_B, R_C = [10, 10, 100, 80], [200, 10, 100, 80], [10, 200, 100, 80]


class KeyFormatTest(unittest.TestCase):
    def test_parse_overlay_key_kinds(self) -> None:
        self.assertEqual(rr.parse_overlay_key("7"), ("legacy", "7", None))
        self.assertEqual(rr.parse_overlay_key("7:2"), ("legacy", "7", 2))
        self.assertEqual(rr.parse_overlay_key("7-2"), ("legacy", "7", 2))
        self.assertEqual(rr.parse_overlay_key("s:701"), ("legacy_sentence", "701", None))
        self.assertEqual(rr.parse_overlay_key("tg:10,13#2"), ("ref", "tg:10,13", 2))
        self.assertEqual(rr.parse_overlay_key("guide:manual_seed_1"), ("ref", "guide:manual_seed_1", None))

    def test_format_overlay_key_roundtrip(self) -> None:
        self.assertEqual(rr.format_overlay_key("tg:3"), "tg:3")
        self.assertEqual(rr.format_overlay_key("tg:3", 2), "tg:3#2")

    def test_parse_ref(self) -> None:
        self.assertEqual(rr.parse_ref("tg:10,13")["tg_ids"], (10, 13))
        parsed = rr.parse_ref("tg:10,13/s2")
        self.assertEqual((parsed["tg_ids"], parsed["split_order"]), ((10, 13), 2))
        self.assertEqual(rr.parse_ref("guide:manual_seed_5")["guide_id"], "manual_seed_5")
        self.assertEqual(rr.parse_ref("geo:t:120,340")["cx"], 120)
        self.assertEqual(rr.parse_ref("tg:6@edge_box_1")["suffix"], "edge_box_1")


class AnnotateTest(unittest.TestCase):
    def test_tg_ref_ignores_region_number_and_order(self) -> None:
        first = rr.annotate_regions([region(1, [3], R_A), region(2, [10, 13], R_B)])
        shifted = rr.annotate_regions([region(1, [9], R_C), region(2, [10, 13], R_B), region(3, [3], R_A)])
        self.assertEqual([r["region_ref"] for r in first], ["tg:3", "tg:10,13"])
        self.assertEqual(shifted[2]["region_ref"], "tg:3")
        self.assertEqual(shifted[1]["region_ref"], "tg:10,13")

    def test_original_is_not_modified(self) -> None:
        source = [region(1, [3], R_A)]
        rr.annotate_regions(source)
        self.assertNotIn("region_ref", source[0])

    def test_split_child_without_tg_uses_parent_tg_and_order(self) -> None:
        parent = [0, 0, 300, 100]
        regions = [
            region(1, [10], R_A, split_parent_rect=parent, split_order=0),
            {"region_id": 2, "type": "bubble", "bubble_rect": R_B, "split_parent_rect": parent, "split_order": 1},
        ]
        annotated = rr.annotate_regions(regions)
        self.assertEqual(annotated[0]["region_ref"], "tg:10")
        self.assertEqual(annotated[1]["region_ref"], "tg:10/s1")

    def test_manual_region_without_tg_uses_guide_id_then_geo(self) -> None:
        guided = {"region_id": 12001, "type": "bubble", "bubble_rect": R_A, "region_origin": "manual_seed_77"}
        bare = {"region_id": 12002, "type": "bubble", "bubble_rect": R_B, "source_method": "manual_bubble_seed_rescue"}
        annotated = rr.annotate_regions([guided, bare])
        self.assertEqual(annotated[0]["region_ref"], "guide:manual_seed_77")
        self.assertEqual(annotated[1]["region_ref"], "geo:b:250,50")
        self.assertTrue(annotated[1]["region_ref_weak"])

    def test_manual_edge_over_same_tg_shares_content_ref_but_not_instance(self) -> None:
        auto = rr.annotate_regions([region(1, [6], R_A)])[0]
        edge = rr.annotate_regions([region(1, [6], R_A, region_origin="edge_box_9")])[0]
        self.assertEqual(auto["region_ref"], edge["region_ref"])
        self.assertNotEqual(auto["region_instance_ref"], edge["region_instance_ref"])
        self.assertEqual(auto["region_instance_ref"], "auto|tg:6")

    def test_same_ref_collision_keeps_auto_key_and_suffixes_manual(self) -> None:
        annotated = rr.annotate_regions([
            region(1, [6], R_A, region_origin="edge_box_9"),
            region(2, [6], R_B),
        ])
        self.assertEqual(annotated[1]["region_ref"], "tg:6")
        self.assertEqual(annotated[0]["region_ref"], "tg:6@edge_box_9")
        self.assertTrue(annotated[0]["region_ref_collision"])
        self.assertTrue(rr.is_weak_ref(annotated[0]["region_ref"]))


class IntegratedGroupsTest(unittest.TestCase):
    def test_group_ref_is_tg_union_of_same_number(self) -> None:
        integrated = {"clusters": [
            cluster(2, [10], R_A, segment=1),
            cluster(2, [13], R_B, segment=2),
            cluster(3, [4], R_C),
        ]}
        refs = rr.integrated_cluster_refs(integrated)
        self.assertEqual(refs, {0: "tg:10,13", 1: "tg:10,13", 2: "tg:4"})

    def test_stored_source_region_ref_wins(self) -> None:
        item = cluster(1, [5], R_A)
        item["source_region_ref"] = "tg:5,6"
        self.assertEqual(rr.integrated_cluster_refs({"clusters": [item]}), {0: "tg:5,6"})

    def test_tg_less_group_matches_unique_current_region_else_geo(self) -> None:
        item = cluster(1, [], R_A)
        current = [region(1, [], R_A, region_origin="manual_seed_1")]
        self.assertEqual(rr.integrated_cluster_refs({"clusters": [item]}, current), {0: "guide:manual_seed_1"})
        self.assertTrue(rr.integrated_cluster_refs({"clusters": [item]}, None)[0].startswith("geo:"))


class ResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.regions = rr.annotate_regions([region(1, [1], R_A), region(2, [3], R_B)])

    def test_exact(self) -> None:
        res = rr.resolve("tg:3", None, self.regions)
        self.assertEqual((res["kind"], res["target_refs"]), (rr.EXACT, ["tg:3"]))

    def test_vanished_tg_is_unmatched_and_never_falls_back_to_a_number(self) -> None:
        res = rr.resolve("tg:2", {"rect": R_B, "region_id": 2}, self.regions)
        self.assertEqual(res["kind"], rr.UNMATCHED)

    def test_shrink_when_part_of_old_tg_set_remains(self) -> None:
        res = rr.resolve("tg:2,3", None, self.regions)
        self.assertEqual((res["kind"], res["target_refs"]), (rr.SHRINK, ["tg:3"]))

    def test_merge_when_old_ref_is_inside_new_region(self) -> None:
        regions = rr.annotate_regions([region(1, [10, 13], R_A)])
        res = rr.resolve("tg:13", None, regions)
        self.assertEqual((res["kind"], res["target_refs"]), (rr.MERGE, ["tg:10,13"]))

    def test_split_when_old_ref_spans_several_new_regions(self) -> None:
        regions = rr.annotate_regions([region(1, [10], R_A), region(2, [13], R_B)])
        res = rr.resolve("tg:10,13", None, regions)
        self.assertEqual((res["kind"], res["target_refs"]), (rr.SPLIT, ["tg:10", "tg:13"]))

    def test_partial_overlap_is_unmatched(self) -> None:
        regions = rr.annotate_regions([region(1, [2, 3], R_A)])
        self.assertEqual(rr.resolve("tg:1,2", None, regions)["kind"], rr.UNMATCHED)

    def test_guide_ref_needs_same_guide(self) -> None:
        regions = rr.annotate_regions([region(1, [], R_A, region_origin="manual_seed_1")])
        self.assertEqual(rr.resolve("guide:manual_seed_1", None, regions)["kind"], rr.EXACT)
        self.assertEqual(rr.resolve("guide:manual_seed_2", None, regions)["kind"], rr.UNMATCHED)

    def test_geo_needs_a_single_close_candidate(self) -> None:
        regions = rr.annotate_regions([region(1, [], R_A, source_method="manual_bubble_seed_rescue")])
        near = rr.resolve("geo:b:62,52", {"rect": [12, 12, 100, 80]}, regions)
        self.assertEqual(near["kind"], rr.GEO)
        self.assertEqual(rr.resolve("geo:b:400,400", None, regions)["kind"], rr.UNMATCHED)

    def test_iou_rematch_opens_only_when_tg_digest_changed(self) -> None:
        snapshot = {"rect": R_B, "tg_digest": "old"}
        self.assertEqual(rr.resolve("tg:99", snapshot, self.regions, "old")["kind"], rr.UNMATCHED)
        self.assertEqual(rr.resolve("tg:99", snapshot, self.regions, None)["kind"], rr.UNMATCHED)
        changed = rr.resolve("tg:99", snapshot, self.regions, "new")
        self.assertEqual((changed["kind"], changed["target_refs"]), (rr.GEO, ["tg:3"]))

    def test_tg_digest_changes_with_ids_or_rects(self) -> None:
        a = rr.tg_digest([{"text_group_id": 1, "bounding_rect": R_A}])
        b = rr.tg_digest([{"text_group_id": 2, "bounding_rect": R_A}])
        self.assertNotEqual(a, b)
        self.assertEqual(a, rr.tg_digest([{"text_group_id": 1, "bounding_rect": R_A}]))


class NormalizeOverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.integrated = {"clusters": [
            cluster(1, [1], R_A),
            cluster(2, [2], R_B),
            cluster(3, [3], R_C, segment=1, sentence=301),
            cluster(3, [4], [150, 200, 100, 80], segment=2, sentence=302),
        ]}

    def test_latest_numeric_keys_follow_current_integrated_numbers(self) -> None:
        guidance = {"regions": {"2": {"font_size": 30}, "3:2": {"text_override": "x"}}}
        v1, report = rr.normalize_overlay_guidance(guidance, self.integrated, None, overlay_newer_than_integrated=True)
        self.assertEqual(set(v1["regions_by_ref"]), {"tg:2", "tg:3,4#2"})
        self.assertEqual(v1["regions_by_ref"]["tg:2"]["font_size"], 30)
        self.assertEqual(v1["regions_by_ref"]["tg:2"]["_anchor"]["region_ref"], "tg:2")
        self.assertEqual(report["stashed"], {})

    def test_segment_alias_and_sentence_key(self) -> None:
        guidance = {"regions": {"3-1": {"font_size": 20}, "s:302": {"font_size": 25}}}
        v1, _ = rr.normalize_overlay_guidance(guidance, self.integrated)
        self.assertEqual(v1["regions_by_ref"]["tg:3,4#1"]["font_size"], 20)
        self.assertEqual(v1["regions_by_ref"]["tg:3,4#2"]["font_size"], 25)

    def test_old_overlay_uses_anchor_rect_not_number(self) -> None:
        """번역을 다시 해서 번호가 달라진 뒤의 옛 수동값: 번호가 아니라 저장 때의 위치로 묶음을 찾는다."""
        guidance = {"regions": {"1": {"font_size": 33, "_anchor": {"rect": R_B, "region_id": 1}}}}
        v1, _ = rr.normalize_overlay_guidance(guidance, self.integrated, None, overlay_newer_than_integrated=False)
        self.assertEqual(list(v1["regions_by_ref"]), ["tg:2"])

    def test_old_overlay_with_unmatched_anchor_is_stashed(self) -> None:
        guidance = {"regions": {"2": {"font_size": 33, "_anchor": {"rect": [900, 900, 50, 50], "region_id": 2}}}}
        v1, report = rr.normalize_overlay_guidance(guidance, self.integrated, None, overlay_newer_than_integrated=False)
        self.assertEqual(v1["regions_by_ref"], {})
        self.assertIn("2", v1["unmatched_regions"])
        self.assertEqual(report["stashed"]["2"], "no_overlapping_region")

    def test_old_overlay_without_anchor_reads_current_number_and_is_marked(self) -> None:
        guidance = {"regions": {"2": {"font_size": 33}}}
        v1, _ = rr.normalize_overlay_guidance(guidance, self.integrated, None, overlay_newer_than_integrated=False)
        self.assertEqual(v1["regions_by_ref"]["tg:2"]["_migrated"], "legacy_unanchored")

    def test_number_missing_from_integrated_is_stashed_without_number_fallback(self) -> None:
        regions = [region(9, [], R_C)]
        v1, report = rr.normalize_overlay_guidance({"regions": {"9": {"text_override": "x"}}}, self.integrated, regions)
        self.assertEqual(v1["regions_by_ref"], {})
        self.assertEqual(report["stashed"]["9"], "legacy_number_unresolved")

    def test_unrelated_fields_survive(self) -> None:
        guidance = {"regions": {}, "eraser_rects": [[1, 2, 3, 4]], "unmatched_regions": {"7": {"x": 1}}}
        v1, _ = rr.normalize_overlay_guidance(guidance, self.integrated)
        self.assertEqual(v1["eraser_rects"], [[1, 2, 3, 4]])
        self.assertEqual(v1["unmatched_regions"], {"7": {"x": 1}})

    def test_dual_write_then_read_trusts_refs_and_rollback_edit_wins(self) -> None:
        guidance = {"regions": {"2": {"font_size": 30}}}
        v1, _ = rr.normalize_overlay_guidance(guidance, self.integrated)
        saved = rr.dual_write_guidance(v1, self.integrated)
        self.assertEqual(saved["regions"], {"2": {"font_size": 30}})
        # 구 코드가 건드리지 않았으면 신형을 그대로 믿는다.
        again, report = rr.normalize_overlay_guidance(saved, self.integrated)
        self.assertEqual(report["source"], "regions_by_ref")
        self.assertEqual(list(again["regions_by_ref"]), ["tg:2"])
        # 롤백 기간에 구 코드가 regions를 고치면 구형이 이긴다.
        saved["regions"]["1"] = {"font_size": 44}
        edited, report = rr.normalize_overlay_guidance(saved, self.integrated)
        self.assertEqual(report["source"], "legacy_wins_after_rollback")
        self.assertEqual(set(edited["regions_by_ref"]), {"tg:1", "tg:2"})


class ReconcileOverlayTest(unittest.TestCase):
    def entry(self, ref: str, **value) -> dict:
        return {"regions_by_ref": {ref: dict(value)}}

    def test_t1_vanished_group_is_stashed_and_other_group_keeps_its_value(self) -> None:
        """00011 구조: 번역 묶음 2(TG2)가 사라지고 3(TG3)이 현재 R2가 되어도 수동값은 대사를 따라간다."""
        guidance = {"regions_by_ref": {
            "tg:2": {"text_override": "", "_anchor": {"rect": R_B}},
            "tg:3": {"font_size": 30},
        }}
        current = [region(1, [1], R_A), region(2, [3], R_C)]
        result, report = rr.reconcile_overlay_guidance(guidance, current)
        self.assertEqual(set(result["regions_by_ref"]), {"tg:3"})
        self.assertEqual(result["regions_by_ref"]["tg:3"]["font_size"], 30)
        self.assertEqual(result["regions_by_ref"]["tg:3"]["_anchor"]["region_id"], 2)
        self.assertIn("tg:2", result["unmatched_regions"])
        self.assertEqual(report["stashed"], {"tg:2": "no_region"})

    def test_t2_renumbering_alone_changes_nothing_but_the_displayed_number(self) -> None:
        guidance = {"regions_by_ref": {"tg:3": {"font_size": 30}}}
        current = [region(1, [9], R_C), region(2, [3], R_B)]
        result, report = rr.reconcile_overlay_guidance(guidance, current)
        self.assertEqual(list(result["regions_by_ref"]), ["tg:3"])
        self.assertEqual(report["moved"], {})
        self.assertEqual(result["regions_by_ref"]["tg:3"]["_anchor"]["region_id"], 2)

    def test_t4_merge_moves_single_source_and_stashes_conflict(self) -> None:
        merged = [region(1, [10, 13], R_A)]
        single, _ = rr.reconcile_overlay_guidance(self.entry("tg:10", font_size=20), merged)
        self.assertEqual(list(single["regions_by_ref"]), ["tg:10,13"])
        both = {"regions_by_ref": {"tg:10": {"font_size": 20}, "tg:13": {"font_size": 22}}}
        result, report = rr.reconcile_overlay_guidance(both, merged)
        self.assertEqual(result["regions_by_ref"], {})
        self.assertEqual(set(report["stashed"].values()), {"merge_conflict"})

    def test_t5_split_stashes_whole_value(self) -> None:
        split = [region(1, [10], R_A), region(2, [13], R_B)]
        result, report = rr.reconcile_overlay_guidance(self.entry("tg:10,13", font_size=20), split)
        self.assertEqual(result["regions_by_ref"], {})
        self.assertEqual(report["stashed"], {"tg:10,13": "split"})

    def test_t5b_shrink_moves_with_marker(self) -> None:
        current = [region(1, [3], R_A)]
        result, report = rr.reconcile_overlay_guidance(self.entry("tg:2,3#1", font_size=20), current)
        self.assertEqual(list(result["regions_by_ref"]), ["tg:3#1"])
        self.assertEqual(result["regions_by_ref"]["tg:3#1"]["_migrated"], "shrink")
        self.assertEqual(report["moved"], {"tg:2,3#1": "tg:3#1"})

    def test_t6_seed_region_follows_guide_id_and_is_stashed_when_deleted(self) -> None:
        guidance = self.entry("guide:manual_seed_5", font_size=20)
        moved = [region(1, [], R_B, region_origin="manual_seed_5")]
        result, _ = rr.reconcile_overlay_guidance(guidance, moved)
        self.assertEqual(list(result["regions_by_ref"]), ["guide:manual_seed_5"])
        gone = [region(1, [4], R_A)]
        result, report = rr.reconcile_overlay_guidance(guidance, gone)
        self.assertEqual(result["regions_by_ref"], {})
        self.assertIn("guide:manual_seed_5", report["stashed"])

    def test_t7_tg_less_split_child_stays_with_parent_and_order(self) -> None:
        parent = [0, 0, 300, 100]
        children = [
            region(1, [10], R_A, split_parent_rect=parent, split_order=0),
            {"region_id": 2, "type": "bubble", "bubble_rect": R_B, "split_parent_rect": parent, "split_order": 1},
        ]
        result, _ = rr.reconcile_overlay_guidance(self.entry("tg:10/s1", font_size=20), children)
        self.assertEqual(list(result["regions_by_ref"]), ["tg:10/s1"])

    def test_empty_current_regions_leaves_guidance_untouched(self) -> None:
        guidance = self.entry("tg:3", font_size=20)
        result, report = rr.reconcile_overlay_guidance(guidance, [])
        self.assertEqual(result, guidance)
        self.assertEqual(report["stashed"], {})

    def test_stash_keys_do_not_overwrite_earlier_stash(self) -> None:
        guidance = {"regions_by_ref": {"tg:2": {"font_size": 1}}, "unmatched_regions": {"tg:2": {"font_size": 99}}}
        result, _ = rr.reconcile_overlay_guidance(guidance, [region(1, [1], R_A)])
        self.assertEqual(result["unmatched_regions"]["tg:2"]["font_size"], 99)
        self.assertEqual(result["unmatched_regions"]["tg:2~2"]["font_size"], 1)

    def test_to_legacy_regions_uses_current_integrated_numbers(self) -> None:
        integrated = {"clusters": [cluster(5, [3], R_B), cluster(6, [4], R_C, segment=1)]}
        v1 = {"regions_by_ref": {"tg:3": {"font_size": 30}, "tg:4#1": {"text_override": "a"}, "tg:77": {"font_size": 1}}}
        legacy = rr.to_legacy_regions(v1, integrated)
        self.assertEqual(legacy, {"5": {"font_size": 30}, "6:1": {"text_override": "a"}})


class SpeakerReconcileTest(unittest.TestCase):
    def test_binding_key_seed_then_follow_renumbering(self) -> None:
        from pipeline.region_types import region_binding_key
        regions = [region(1, [3], R_A)]
        store = {"by_region": {region_binding_key(regions[0]): "hero"}}
        result, report = rr.reconcile_speakers(store, regions)
        self.assertEqual(result["by_region_ref"], {"tg:3": "hero"})
        renumbered = [region(5, [9], R_B), region(6, [3], R_A)]
        result, _ = rr.reconcile_speakers(result, renumbered)
        self.assertEqual(result["by_region_ref"], {"tg:3": "hero"})

    def test_a1_split_copies_to_all_children(self) -> None:
        store = {"by_region_ref": {"tg:10,13": "hero"}, "context_by_region_ref": {"tg:10,13": "memo"}}
        regions = [region(1, [10], R_A), region(2, [13], R_B)]
        result, report = rr.reconcile_speakers(store, regions)
        self.assertEqual(result["by_region_ref"], {"tg:10": "hero", "tg:13": "hero"})
        self.assertEqual(result["context_by_region_ref"], {"tg:10": "memo", "tg:13": "memo"})
        self.assertIn("tg:10,13", report["copied"])

    def test_a2_merge_keeps_equal_clears_different(self) -> None:
        regions = [region(1, [10, 13], R_A)]
        same, _ = rr.reconcile_speakers({"by_region_ref": {"tg:10": "hero", "tg:13": "hero"}}, regions)
        self.assertEqual(same["by_region_ref"], {"tg:10,13": "hero"})
        diff, report = rr.reconcile_speakers({"by_region_ref": {"tg:10": "hero", "tg:13": "rival"}}, regions)
        self.assertEqual(diff["by_region_ref"], {})
        self.assertEqual(report["cleared"], {"tg:10": "hero", "tg:13": "rival"})

    def test_unmatched_entry_stays_dormant_and_comes_back(self) -> None:
        store = {"by_region_ref": {"tg:2": "hero"}}
        gone, report = rr.reconcile_speakers(store, [region(1, [1], R_A)])
        self.assertEqual(gone["by_region_ref"], {"tg:2": "hero"})
        self.assertEqual(report["dormant"], ["tg:2"])
        back, _ = rr.reconcile_speakers(gone, [region(1, [1], R_A), region(2, [2], R_B)])
        self.assertEqual(back["by_region_ref"], {"tg:2": "hero"})


class ResolveClusterRefsTest(unittest.TestCase):
    def test_segments_attach_to_split_children_and_vanished_is_flagged(self) -> None:
        integrated = {"clusters": [
            cluster(1, [10], R_A, segment=1),
            cluster(1, [13], R_B, segment=2),
            cluster(1, [20], R_C, segment=3),
        ]}
        # 영역 {10,13,20}이 {10}, {13}으로 갈라지고 TG20은 사라졌다.
        current = [region(1, [10], R_A), region(2, [13], R_B)]
        refs = rr.resolve_cluster_refs(integrated, current)
        self.assertEqual([(r["ref"], r["kind"]) for r in refs], [("tg:10", rr.EXACT), ("tg:13", rr.EXACT), (None, rr.UNMATCHED)])
        self.assertEqual([r["vanished"] for r in refs], [False, False, True])

    def test_cluster_spanning_two_children_goes_to_the_one_with_most_tgs_then_reading_order(self) -> None:
        current = [region(1, [10, 11], R_A), region(2, [12], R_B)]
        spanning = {"clusters": [cluster(1, [10, 11, 12], R_A)]}
        self.assertEqual(rr.resolve_cluster_refs(spanning, current)[0]["ref"], "tg:10,11")
        tie = {"clusters": [cluster(1, [10, 12], R_A)]}
        tie_current = [region(1, [10], R_A), region(2, [12], R_B)]
        self.assertEqual(rr.resolve_cluster_refs(tie, tie_current)[0]["ref"], "tg:10")

    def test_no_current_regions_keeps_source_refs_and_never_flags_vanished(self) -> None:
        refs = rr.resolve_cluster_refs({"clusters": [cluster(1, [10], R_A)]}, [])
        self.assertEqual((refs[0]["ref"], refs[0]["vanished"]), ("tg:10", False))


class ProjectPageTest(unittest.TestCase):
    def test_projection_attaches_refs_without_touching_inputs(self) -> None:
        payload = {"regions": [region(1, [1], R_A), region(2, [3], R_B)]}
        integrated = {"clusters": [cluster(1, [1], R_A), cluster(5, [2, 3], R_B)], "glossary": {"x": 1}}
        overlay = {"regions": {"5": {"font_size": 30}}, "eraser_rects": [[1, 2, 3, 4]]}
        out = rr.project_page(payload, integrated, overlay, None, [{"text_group_id": 1, "bounding_rect": R_A}])

        self.assertEqual([r["region_ref"] for r in out["regions"]["regions"]], ["tg:1", "tg:3"])
        refs = [(c["source_region_ref"], c["resolved_region_ref"]) for c in out["integrated"]["clusters"]]
        self.assertEqual(refs, [("tg:1", "tg:1"), ("tg:2,3", "tg:3")])
        self.assertEqual(out["integrated"]["glossary"], {"x": 1})
        # 번역 묶음 5(TG2,3)의 수동값은 TG2가 사라진 뒤 현재 영역 tg:3으로 따라간다.
        self.assertEqual(list(out["overlay_guidance_v1"]["regions_by_ref"]), ["tg:3"])
        self.assertEqual(out["overlay_guidance_v1"]["eraser_rects"], [[1, 2, 3, 4]])
        self.assertNotIn("region_ref", payload["regions"][0])
        self.assertNotIn("resolved_region_ref", integrated["clusters"][0])
        self.assertEqual(overlay, {"regions": {"5": {"font_size": 30}}, "eraser_rects": [[1, 2, 3, 4]]})

    def test_projection_tolerates_missing_files(self) -> None:
        out = rr.project_page({}, [], {}, {}, [])
        self.assertEqual(out["overlay_guidance_v1"]["regions_by_ref"], {})
        self.assertEqual(out["integrated"], [])
        out = rr.project_page({"regions": []}, {"skipped": True}, None)
        self.assertEqual(out["integrated"], {"skipped": True})


class PolicyTableTest(unittest.TestCase):
    def test_every_kind_has_a_policy_for_every_consumer(self) -> None:
        for kind in (rr.EXACT, rr.SHRINK, rr.MERGE, rr.SPLIT, rr.GEO, rr.UNMATCHED):
            for consumer in ("overlay", "speaker", "context"):
                self.assertIn((kind, consumer), rr.POLICY)


if __name__ == "__main__":
    unittest.main()
