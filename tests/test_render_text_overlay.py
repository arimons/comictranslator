# 번역 텍스트 오버레이 줄바꿈 규칙을 검증하는 테스트
import json
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pipeline.render_text_overlay import (
    FONT_PATH,
    Layout,
    allowed_line_width,
    apply_overlay_eraser_rects,
    area_based_font_cap,
    choose_layout,
    chunk_korean_text,
    decorative_line_kind,
    draw_text_block,
    draw_tracked_text,
    join_tokens,
    edge_split_tg_anchored_rect,
    expand_vertical_ellipsis_to_periods,
    explode_long_tokens,
    font_path_from_overlay_override,
    font_path_for_style,
    load_font,
    is_vertical_decorative_line_text,
    is_vertical_rotated_glyph,
    is_dot_ellipsis_only_text,
    dot_ellipsis_vertical_lines,
    merge_unseparated_shared_segments,
    normalize_inline_dot_ellipsis,
    split_trailing_dot_ellipsis,
    overlay_bbox_for_cluster,
    overlay_eraser_rects,
    overlay_layout_summary,
    overlay_override_for_cluster,
    overlay_override_disables_automatic_fallback,
    overlay_override_font_weight,
    overlay_override_has_font_size_delta,
    overlay_override_has_manual_layout,
    overlay_override_stroke_width,
    overlay_suppressed_region_ids,
    prepare_page_layouts,
    region_id_for_cluster,
    shared_group_is_spatially_separated,
    should_soften_unboxed_tg_bbox_layout,
    split_trailing_ellipsis_tokens,
    split_parent_font_cap,
    layout_fill_ratio,
    layout_uses_vertical_dot_runs,
    layout_with_font_size_delta,
    softened_unboxed_tg_bbox_font_cap,
    should_anchor_single_tg_in_irregular_large_bubble,
    should_keep_safe_rect_centered_for_small_bubble,
    should_rescue_area_fit,
    should_use_bubble_rect_for_short_vertical_source,
    shared_segment_safe_rect,
    single_tg_centered_safe_rect,
    synthetic_vertical_ellipsis_size,
    synthetic_vertical_dot_run_size,
    synthetic_dot_radius_for_font,
    text_group_centered_sub_rect,
    text_size,
    vertical_line_offsets,
    _is_short_unboxed_label_cluster,
    _should_preserve_original_cluster,
    _should_preserve_original_symbol_english_cluster,
    _should_preserve_original_unboxed_cluster,
    should_force_vertical_short_interjection,
    should_force_serif_for_large_title_style,
    should_try_vertical_title_layout,
    split_leading_horizontal_decorative_line,
    split_trailing_horizontal_decorative_line,
    split_overlong_vertical_ellipsis_text,
    tokenize,
)


class RenderTextOverlayTest(unittest.TestCase):
    def test_explode_long_tokens_splits_korean_copula_suffix(self) -> None:
        tokens = tokenize("나는 평범한 대학생이고")

        self.assertEqual(explode_long_tokens(tokens), ["나는", " 평범한", " 대학생", "이고"])

    def test_overlay_layout_summary_uses_rendered_lines_by_region(self) -> None:
        layout = Layout(
            font_size=24,
            lines=["편지의", "답장인가?!"],
            inner_rect=(10, 20, 120, 80),
            line_height=28,
            measured_width=96,
            measured_height=56,
            target_lines=2,
        )
        prepared = [
            (
                {"matched_cluster": [3]},
                "편지의 답장인가?!",
                [8, 18, 140, 100],
                "safe_text_rect_from_A3",
                layout,
                1,
                FONT_PATH,
            )
        ]

        summary = overlay_layout_summary(prepared, {3: {"region_id": 2}})

        self.assertEqual(summary["regions"]["2"]["text_layout"], "편지의\n답장인가?!")
        self.assertEqual(summary["regions"]["2"]["font_size"], 24)

    def test_draw_text_block_centers_visual_ink_vertically_in_inner_rect(self) -> None:
        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=42,
            lines=["그럼"],
            inner_rect=(20, 20, 120, 80),
            line_height=48,
            measured_width=80,
            measured_height=42,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image,
            "그럼",
            [20, 20, 120, 80],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_stroke_width=0,
        )
        dark_pixels = np.where(np.all(rendered < 80, axis=2))

        self.assertGreater(len(dark_pixels[0]), 0)
        ink_center_y = (float(dark_pixels[0].min()) + float(dark_pixels[0].max())) / 2.0
        self.assertAlmostEqual(ink_center_y, 60.0, delta=3.0)

    def test_draw_text_block_can_rotate_text_layer(self) -> None:
        image = np.full((140, 180, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=34,
            lines=["회전"],
            inner_rect=(35, 35, 110, 70),
            line_height=40,
            measured_width=80,
            measured_height=34,
            target_lines=1,
        )

        straight, _ = draw_text_block(
            image.copy(),
            "회전",
            [30, 30, 120, 80],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_stroke_width=0,
        )
        rotated, _ = draw_text_block(
            image.copy(),
            "회전",
            [30, 30, 120, 80],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_stroke_width=0,
            rotation_deg=12,
        )

        self.assertGreater(np.count_nonzero(np.any(straight != rotated, axis=2)), 20)

    def test_overlay_eraser_rects_are_normalized_and_applied(self) -> None:
        image = np.zeros((12, 14, 3), dtype=np.uint8)
        rects = overlay_eraser_rects({
            "eraser_rects": [
                [2.2, 3.6, 5, 4],
                [1, 1, 0, 4],
                ["x", 1, 2, 3],
            ]
        })

        applied = apply_overlay_eraser_rects(image, rects)

        self.assertEqual(rects, [[2, 4, 5, 4]])
        self.assertEqual(applied, 1)
        self.assertTrue(np.all(image[4:8, 2:7] == 255))
        self.assertEqual(int(image[:4].sum()), 0)

    def test_decorative_line_kind_requires_two_line_chars(self) -> None:
        self.assertIsNone(decorative_line_kind(["|"]))
        self.assertEqual(decorative_line_kind(["|", "|"]), "vertical")
        self.assertEqual(decorative_line_kind(["∣", "∣"]), "vertical")
        self.assertEqual(decorative_line_kind(["---"]), "horizontal")
        self.assertEqual(decorative_line_kind(["――"]), "horizontal")
        self.assertIsNone(decorative_line_kind(["말|풍선"]))
        self.assertTrue(is_vertical_decorative_line_text("|"))
        self.assertTrue(is_vertical_decorative_line_text("∣"))
        self.assertFalse(is_vertical_decorative_line_text("에|"))
        self.assertEqual(split_trailing_horizontal_decorative_line("한다면――"), ("한다면", "――"))
        self.assertEqual(split_trailing_horizontal_decorative_line("한다면 - -"), ("한다면", "--"))
        self.assertEqual(split_trailing_horizontal_decorative_line("한다면―"), ("한다면", "―"))
        self.assertEqual(split_leading_horizontal_decorative_line("──이"), ("──", "이"))
        self.assertEqual(split_leading_horizontal_decorative_line("─이"), ("─", "이"))
        self.assertIsNone(split_trailing_horizontal_decorative_line("-"))
        self.assertIsNone(split_leading_horizontal_decorative_line("-"))

    def test_draw_text_block_renders_decorative_lines_without_glyphs(self) -> None:
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["|", "|"],
            inner_rect=(45, 20, 30, 80),
            line_height=36,
            measured_width=10,
            measured_height=72,
            target_lines=2,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "|\n|",
            [40, 15, 40, 90],
            Path(FONT_PATH),
            layout=layout,
        )

        dark_pixels = np.count_nonzero(np.all(rendered < 64, axis=2))
        self.assertGreater(dark_pixels, 80)

    def test_draw_text_block_renders_leading_bar_lines_inside_mixed_text(self) -> None:
        image = np.full((180, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["|", "|", "에", "?"],
            inner_rect=(35, 15, 50, 145),
            line_height=36,
            measured_width=32,
            measured_height=140,
            target_lines=4,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "|\n|\n에\n?",
            [30, 10, 60, 155],
            Path(FONT_PATH),
            layout=layout,
        )

        top_strip_dark = np.count_nonzero(np.all(rendered[:82, 50:70] < 64, axis=2))
        bottom_strip_dark = np.count_nonzero(np.all(rendered[82:, 50:70] < 64, axis=2))
        self.assertGreater(top_strip_dark, 80)
        self.assertGreater(bottom_strip_dark, 20)

    def test_draw_text_block_renders_single_line_double_bar_as_line(self) -> None:
        image = np.full((180, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["||", "에", "?"],
            inner_rect=(35, 15, 50, 145),
            line_height=36,
            measured_width=32,
            measured_height=120,
            target_lines=3,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "||\n에\n?",
            [30, 10, 60, 155],
            Path(FONT_PATH),
            layout=layout,
        )

        line_dark = np.count_nonzero(np.all(rendered[:75, 50:70] < 64, axis=2))
        self.assertGreater(line_dark, 80)

    def test_draw_text_block_renders_math_vertical_bars_as_line(self) -> None:
        image = np.full((180, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["∣", "∣", "에", "?"],
            inner_rect=(35, 15, 50, 145),
            line_height=36,
            measured_width=32,
            measured_height=140,
            target_lines=4,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "∣\n∣\n에\n?",
            [30, 10, 60, 155],
            Path(FONT_PATH),
            layout=layout,
        )

        line_dark = np.count_nonzero(np.all(rendered[:82, 50:70] < 64, axis=2))
        self.assertGreater(line_dark, 80)

    def test_draw_text_block_renders_trailing_dash_run_as_line(self) -> None:
        image = np.full((120, 180, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=30,
            lines=["한다면――"],
            inner_rect=(10, 35, 160, 50),
            line_height=34,
            measured_width=120,
            measured_height=34,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "한다면――",
            [5, 30, 170, 60],
            Path(FONT_PATH),
            layout=layout,
        )

        # trailing dash가 텍스트 glyph가 아닌 실선으로 그려지면 오른쪽 영역에 검은 픽셀이 충분히 생긴다.
        right_strip_dark = np.count_nonzero(np.all(rendered[:, 95:] < 64, axis=2))
        self.assertGreater(right_strip_dark, 20)

    def test_draw_text_block_renders_leading_dash_run_as_line(self) -> None:
        image = np.full((120, 180, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=30,
            lines=["──이"],
            inner_rect=(10, 35, 160, 50),
            line_height=34,
            measured_width=90,
            measured_height=34,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "──이",
            [5, 30, 170, 60],
            Path(FONT_PATH),
            layout=layout,
        )

        left_strip_dark = np.count_nonzero(np.all(rendered[:, :82] < 64, axis=2))
        self.assertGreater(left_strip_dark, 20)

    def test_draw_text_block_renders_single_trailing_dash_as_line(self) -> None:
        image = np.full((120, 180, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=30,
            lines=["쳇-"],
            inner_rect=(10, 35, 160, 50),
            line_height=34,
            measured_width=60,
            measured_height=34,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "쳇-",
            [5, 30, 170, 60],
            Path(FONT_PATH),
            layout=layout,
        )

        right_strip_dark = np.count_nonzero(np.all(rendered[:, 42:95] < 64, axis=2))
        self.assertGreater(right_strip_dark, 20)

    def test_draw_text_block_applies_text_stroke(self) -> None:
        image = np.zeros((120, 160, 3), dtype=np.uint8)
        layout = Layout(
            font_size=34,
            lines=["나를"],
            inner_rect=(20, 35, 120, 50),
            line_height=38,
            measured_width=70,
            measured_height=38,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image.copy(),
            "나를",
            [10, 20, 140, 80],
            Path(FONT_PATH),
            layout=layout,
            text_fill=(0, 0, 0),
            text_stroke_width=3,
            text_stroke_fill=(255, 255, 255),
        )

        white_pixels = np.count_nonzero(np.all(rendered > 230, axis=2))
        self.assertGreater(white_pixels, 50)

    def test_overlay_layout_summary_uses_source_region_id_without_anchor(self) -> None:
        layout = Layout(
            font_size=24,
            lines=["야"],
            inner_rect=(10, 20, 120, 80),
            line_height=28,
            measured_width=24,
            measured_height=28,
            target_lines=1,
        )
        prepared = [
            (
                {"matched_cluster": [], "source_region_id": 6},
                "야",
                [8, 18, 140, 100],
                "cluster_bbox",
                layout,
                1,
                FONT_PATH,
            )
        ]

        summary = overlay_layout_summary(prepared, {})

        self.assertEqual(summary["regions"]["6"]["text_layout"], "야")

    def test_overlay_layout_summary_keeps_segment_entries_separate(self) -> None:
        layout = Layout(
            font_size=24,
            lines=["둘째"],
            inner_rect=(10, 20, 120, 80),
            line_height=28,
            measured_width=48,
            measured_height=28,
            target_lines=1,
        )
        prepared = [
            (
                {"matched_cluster": [3], "source_region_id": 2, "source_segment_index": 1, "sentence_id": 201},
                "첫째",
                [8, 18, 140, 100],
                "safe_text_rect_from_A3",
                layout,
                1,
                FONT_PATH,
            ),
            (
                {"matched_cluster": [5], "source_region_id": 2, "source_segment_index": 2, "sentence_id": 202},
                "둘째",
                [20, 18, 140, 100],
                "safe_text_rect_from_A5",
                layout,
                1,
                FONT_PATH,
            ),
        ]

        summary = overlay_layout_summary(prepared, {3: {"region_id": 2}, 5: {"region_id": 2}})

        self.assertEqual(set(summary["regions"].keys()), {"2:1", "2:2"})
        self.assertEqual(summary["regions"]["2:1"]["text_source"], "첫째")
        self.assertEqual(summary["regions"]["2:2"]["text_source"], "둘째")

    def test_overlay_override_uses_source_region_id_without_anchor(self) -> None:
        cluster = {"matched_cluster": [], "source_region_id": 6}
        overrides = {"6": {"font_size_delta": 2}}

        self.assertEqual(region_id_for_cluster(cluster, {}), 6)
        self.assertEqual(overlay_override_for_cluster(cluster, {}, overrides), {"font_size_delta": 2})

    def test_overlay_override_prefers_source_segment_key(self) -> None:
        cluster = {"matched_cluster": [], "source_region_id": 2, "source_segment_index": 2, "sentence_id": 202}
        overrides = {
            "2": {"font_size_delta": 1},
            "2:2": {"text_override": "두 번째"},
        }

        self.assertEqual(overlay_override_for_cluster(cluster, {}, overrides), {"text_override": "두 번째"})

    def test_overlay_override_accepts_segment_label_alias(self) -> None:
        cluster = {"matched_cluster": [], "source_region_id": 2, "source_segment_index": 2}
        overrides = {"2-2": {"text_override": "두 번째"}}

        self.assertEqual(overlay_override_for_cluster(cluster, {}, overrides), {"text_override": "두 번째"})

    def test_overlay_override_does_not_apply_parent_region_to_segment(self) -> None:
        cluster = {"matched_cluster": [], "source_region_id": 2, "source_segment_index": 2}
        overrides = {"2": {"safe_text_rect": [10, 20, 30, 40]}}

        self.assertEqual(overlay_override_for_cluster(cluster, {}, overrides), {})

    def test_light_font_style_uses_light_font(self) -> None:
        expected = Path("Fonts/Freesentation-3Light.ttf")
        if not expected.exists():
            self.skipTest(f"font not found: {expected}")

        self.assertEqual(font_path_for_style("light", FONT_PATH), expected)

    def test_serif_bold_font_style_uses_bold_serif_font(self) -> None:
        expected = Path("Fonts/SourceHanSerifK-Bold.otf")
        if not expected.exists():
            self.skipTest(f"font not found: {expected}")

        self.assertEqual(font_path_for_style("serif_bold", FONT_PATH), expected)

    def test_non_etc_font_style_ignores_stale_custom_font_path(self) -> None:
        stale_font = Path("Fonts/RIDIBatang.otf")
        bold_font = Path("Fonts/Freesentation-7Bold.ttf")
        if not stale_font.exists() or not bold_font.exists():
            self.skipTest("font not found")

        resolved = font_path_from_overlay_override(
            {"font_style": "bold", "font_path": str(stale_font)},
            FONT_PATH,
            {},
        )

        self.assertEqual(resolved, bold_font)

    def test_overlay_override_font_weight_is_normalized(self) -> None:
        self.assertEqual(overlay_override_font_weight({"font_style": "etc", "font_weight": 700}), 700)
        self.assertEqual(overlay_override_font_weight({"font_style": "etc", "font_weight": 9999}), 1000)
        self.assertEqual(overlay_override_font_weight({"font_style": "etc", "font_weight": 1}), 100)
        self.assertIsNone(overlay_override_font_weight({"font_style": "etc", "font_weight": ""}))
        self.assertIsNone(overlay_override_font_weight({"font_weight": 700}))
        self.assertIsNone(overlay_override_font_weight({}))

    def test_load_font_ignores_weight_for_non_variable_font(self) -> None:
        font = load_font(FONT_PATH, 28, 700)

        self.assertGreater(font.getlength("테스트"), 0)

    def test_load_font_applies_variable_weight_when_supported(self) -> None:
        variable_font = Path("Fonts/SUIT-Variable.ttf")
        if not variable_font.exists():
            self.skipTest("variable font not found")

        font = load_font(variable_font, 28, 800)

        axes = font.get_variation_axes()
        self.assertTrue(any("Weight" in str(axis.get("name")) for axis in axes))

    def test_segment_overlay_override_prevents_shared_segment_merge(self) -> None:
        from pipeline.render_text_overlay import merge_unseparated_shared_segments

        clusters = [
            {
                "matched_cluster": [5],
                "source_region_id": 5,
                "source_segment_index": 1,
                "sentence_id": 501,
                "text_translated": "유노…인가",
                "matched_cluster_details": [{"bounding_rect": [470, 950, 30, 140]}],
            },
            {
                "matched_cluster": [7],
                "source_region_id": 5,
                "source_segment_index": 2,
                "sentence_id": 502,
                "text_translated": "칫",
                "matched_cluster_details": [{"bounding_rect": [340, 1070, 40, 80]}],
            },
        ]
        shared_groups = {5: [5, 7], 7: [5, 7]}
        flood_info = {
            5: {"region_id": 5, "safe_text_rect": [140, 920, 640, 300], "bubble_rect": [120, 900, 690, 340]},
            7: {"region_id": 5, "safe_text_rect": [140, 920, 640, 300], "bubble_rect": [120, 900, 690, 340]},
        }

        merged = merge_unseparated_shared_segments(clusters, shared_groups, flood_info)
        split = merge_unseparated_shared_segments(
            clusters,
            shared_groups,
            flood_info,
            {"5:1": {"text_override": ""}, "5:2": {"text_override": ""}},
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(len(split), 2)
        self.assertEqual([cluster["source_segment_index"] for cluster in split], [1, 2])

    def test_empty_segment_overrides_suppress_split_siblings_after_merge_gate(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((1300, 900, 3), 255, dtype=np.uint8)
        clusters = [
            {
                "location": "bubble",
                "matched_cluster": [5],
                "source_region_id": 5,
                "source_segment_index": 1,
                "sentence_id": 501,
                "text_translated": "유노…인가",
                "matched_cluster_details": [{"bounding_rect": [470, 950, 30, 140], "lines": [{"bbox": [470, 950, 30, 140]}]}],
            },
            {
                "location": "bubble",
                "matched_cluster": [7],
                "source_region_id": 5,
                "source_segment_index": 2,
                "sentence_id": 502,
                "text_translated": "칫",
                "matched_cluster_details": [{"bounding_rect": [340, 1070, 40, 80], "lines": [{"bbox": [340, 1070, 40, 80]}]}],
            },
        ]
        shared_groups = {5: [5, 7], 7: [5, 7]}
        flood_info = {
            5: {"region_id": 5, "anchor_ids": [5, 7], "safe_text_rect": [140, 920, 640, 300], "bubble_rect": [120, 900, 690, 340], "is_valid": True, "region_type_guess": "bubble"},
            7: {"region_id": 5, "anchor_ids": [5, 7], "safe_text_rect": [140, 920, 640, 300], "bubble_rect": [120, 900, 690, 340], "is_valid": True, "region_type_guess": "bubble"},
        }

        prepared = prepare_page_layouts(
            image,
            clusters,
            shared_groups=shared_groups,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"5:1": {"text_override": ""}, "5:2": {"text_override": ""}}},
        )

        self.assertEqual(prepared, [])

    def test_shared_segment_safe_rect_splits_safe_rect_by_tg_centers(self) -> None:
        cluster = {
            "matched_cluster": [3],
            "source_region_id": 2,
            "source_segment_index": 1,
            "matched_cluster_details": [
                {"bounding_rect": [714, 102, 203, 460], "lines": [{"bbox": [714, 102, 203, 460]}]},
            ],
        }
        flood_info = {
            3: {"anchor_bbox": [714, 102, 203, 460]},
            5: {"anchor_bbox": [598, 103, 96, 657]},
        }

        rect = shared_segment_safe_rect(cluster, [591, 86, 280, 682], [3, 5], flood_info)

        self.assertEqual(rect, [731, 86, 140, 550])

    def test_anchorless_region_font_size_delta_uses_source_region_override(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((260, 260, 3), 255, dtype=np.uint8)
        cluster = {
            "source_region_id": 6,
            "matched_cluster": [],
            "text_translated": "야",
            "location": "bubble",
            "bounding_rect": [40, 40, 120, 120],
            "union_bbox": [40, 40, 120, 120],
        }

        base = prepare_page_layouts(image, [cluster], font_path=FONT_PATH)
        adjusted = prepare_page_layouts(
            image,
            [cluster],
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"6": {"font_size_delta": 2}}},
        )

        self.assertEqual(adjusted[0][4].font_size, base[0][4].font_size + 4)

    def test_font_size_delta_directly_changes_layout_font_size(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = Image.new("RGB", (300, 300), "white")
        draw = ImageDraw.Draw(image)
        layout = Layout(
            font_size=24,
            lines=["편지의", "답장인가?!"],
            inner_rect=(10, 20, 120, 80),
            line_height=28,
            measured_width=96,
            measured_height=56,
            target_lines=2,
        )

        adjusted = layout_with_font_size_delta(draw, layout, FONT_PATH, 2)

        self.assertEqual(adjusted.font_size, 28)
        self.assertEqual(adjusted.lines, layout.lines)

    def test_nonzero_font_size_delta_is_manual_split_group_override(self) -> None:
        self.assertTrue(overlay_override_has_font_size_delta({"font_size_delta": -1}))
        self.assertTrue(overlay_override_has_font_size_delta({"font_size_delta": 1}))
        self.assertFalse(overlay_override_has_font_size_delta({"font_size_delta": 0}))
        self.assertFalse(overlay_override_has_font_size_delta({}))

    def test_manual_layout_override_is_split_group_override(self) -> None:
        self.assertTrue(overlay_override_has_manual_layout({"force_vertical": True}))
        self.assertTrue(overlay_override_has_manual_layout({"force_horizontal": True}))
        self.assertTrue(overlay_override_has_manual_layout({"safe_text_rect": [1, 2, 3, 4]}))
        self.assertTrue(overlay_override_has_manual_layout({"font_style": "bold"}))
        self.assertTrue(overlay_override_has_manual_layout({"text_override": ""}))
        self.assertFalse(overlay_override_has_manual_layout({"font_size_delta": 0}))
        self.assertFalse(overlay_override_has_manual_layout({}))
        self.assertTrue(overlay_override_has_manual_layout({"font_size": 42}))
        self.assertFalse(overlay_override_has_manual_layout({"font_size": 0}))
        self.assertTrue(overlay_override_has_manual_layout({"rotation_deg": 8}))
        self.assertFalse(overlay_override_has_manual_layout({"rotation_deg": 0}))

    def test_explode_long_tokens_splits_korean_role_suffix(self) -> None:
        tokens = tokenize("해롤드 스톡스로서")

        self.assertEqual(explode_long_tokens(tokens), ["해롤드", " 스톡스", "로서"])

    def test_explode_long_tokens_preserves_transliterated_name_particle(self) -> None:
        tokens = tokenize("스토크스는")

        self.assertEqual(explode_long_tokens(tokens), ["스토크스는"])

    def test_explode_long_tokens_splits_polite_request_with_ellipsis(self) -> None:
        tokens = tokenize("부탁드립니다…!")

        self.assertEqual(explode_long_tokens(tokens), ["부탁", "드립니다…!"])

    def test_explode_long_tokens_splits_casual_explanatory_ending(self) -> None:
        tokens = tokenize("자업자득이잖아…")

        self.assertEqual(explode_long_tokens(tokens), ["자업자득", "이잖아…"])

    def test_split_trailing_ellipsis_tokens_keeps_final_ellipsis_breakable(self) -> None:
        tokens = tokenize("미안하다 에리카……")

        self.assertEqual(split_trailing_ellipsis_tokens(tokens), ["미안하다", " 에리카", "……"])

    def test_expand_vertical_ellipsis_to_periods_normalizes_only_for_vertical(self) -> None:
        self.assertEqual(expand_vertical_ellipsis_to_periods("그럼…!"), "그럼...!")
        self.assertEqual(expand_vertical_ellipsis_to_periods("그럼……!"), "그럼......!")

    def test_dash_run_is_breakable_token(self) -> None:
        self.assertEqual(tokenize("구해――"), ["구해", "――"])

    def test_single_dash_is_kept_and_attached_to_previous_word(self) -> None:
        self.assertEqual(tokenize("그-그게 아니라"), ["그-", "그게", " 아니라"])
        self.assertEqual(tokenize("A - B"), ["A", " -", " B"])
        for text in ["몇 시간 뒤―", "150-8510", "해롤드 님-", "-아"]:
            self.assertEqual(join_tokens(tokenize(text)), text)

    def test_text_size_matches_drawn_width_of_edge_dash_rule(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        font = load_font(Path(FONT_PATH), 40)
        for text in ["그-", "-아", "몇 시간 뒤—"]:
            image = Image.new("L", (600, 100), 0)
            draw = ImageDraw.Draw(image)
            draw_tracked_text(draw, (50, 20), text, font, 255, vertical_dot_run=False)
            cols = np.where((np.array(image) > 0).any(axis=0))[0]
            drawn_width = int(cols.max() + 1 - cols.min())
            measured_width = text_size(draw, font, text, vertical_dot_run=False)[0]
            self.assertLessEqual(abs(drawn_width - measured_width), 2, text)

    def test_vertical_dash_run_collapses_to_single_bar(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "파멸을――",
            [100, 1200, 120, 360],
            image.size,
            FONT_PATH,
            src_line_count=1,
        )

        self.assertEqual(layout.lines, ["파", "멸", "을", "｜"])

    def test_vertical_ellipsis_uses_period_stack(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "기다려...",
            [100, 1200, 120, 360],
            image.size,
            FONT_PATH,
            src_line_count=1,
        )

        self.assertEqual(layout.lines, ["기", "다", "려", ".", ".", "."])

    def test_vertical_ellipsis_before_exclamation_keeps_compact_dot_gap(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "그럼...!",
            [100, 1200, 120, 420],
            image.size,
            FONT_PATH,
            src_line_count=1,
        )

        self.assertEqual(layout.lines, ["그", "럼", ".", ".", ".", "!"])
        offsets = vertical_line_offsets(layout.lines, layout.line_height, layout.font_size)
        self.assertLess(offsets[5] - offsets[4], layout.line_height)
        self.assertLess(offsets[5] - offsets[4], layout.font_size * 0.70)
        self.assertGreater(offsets[5] - offsets[4], offsets[4] - offsets[3])

    def test_ellipsis_vertical_hint_is_region_candidate_not_glyph_candidate(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "그게 너무 무서워…",
            [200, 200, 260, 260],
            image.size,
            FONT_PATH,
            src_line_count=1,
        )

        self.assertGreater(len(layout.lines), 1)
        self.assertIn("그게", layout.lines)

    def test_trailing_inline_ellipsis_is_split_for_direct_dot_rendering(self) -> None:
        self.assertEqual(split_trailing_dot_ellipsis("무서워…"), ("무서워", "…"))
        self.assertEqual(split_trailing_dot_ellipsis("무서워..."), ("무서워", "..."))
        self.assertIsNone(split_trailing_dot_ellipsis("끝."))

    def test_vertical_ellipsis_size_does_not_depend_on_font_glyph(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (80, 80), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(FONT_PATH), 32)

        self.assertEqual(text_size(draw, font, "⋮"), synthetic_vertical_dot_run_size(32, 3, font=font))

    def test_vertical_tilde_glyph_is_measured_as_rotated_only_in_vertical_mode(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (80, 80), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(FONT_PATH), 40)
        horizontal_size = text_size(draw, font, "~", vertical_dot_run=False)
        vertical_size = text_size(draw, font, "~", vertical_dot_run=True)

        self.assertTrue(is_vertical_rotated_glyph("~"))
        self.assertEqual(vertical_size, (horizontal_size[1], horizontal_size[0]))

    def test_vertical_tilde_glyph_draws_as_tall_rotated_mark(self) -> None:
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=44,
            lines=["~"],
            inner_rect=(30, 20, 60, 80),
            line_height=50,
            measured_width=20,
            measured_height=44,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image,
            "~",
            [25, 15, 70, 90],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark_pixels = np.where(np.all(rendered < 80, axis=2))

        self.assertGreater(len(dark_pixels[0]), 0)
        self.assertGreater(dark_pixels[0].max() - dark_pixels[0].min(), dark_pixels[1].max() - dark_pixels[1].min())

    def test_synthetic_vertical_ellipsis_is_rendered_with_stroke(self) -> None:
        image = np.full((100, 100, 3), 0, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["⋮"],
            inner_rect=(20, 20, 60, 60),
            line_height=36,
            measured_width=6,
            measured_height=30,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image,
            "⋮",
            [20, 20, 60, 60],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=3,
            text_stroke_fill=(255, 255, 255),
        )
        white_pixels = np.where(np.all(rendered > 220, axis=2))

        self.assertGreater(len(white_pixels[0]), 0)

    def test_halfwidth_middle_dot_is_rendered_as_vertical_dot(self) -> None:
        image = np.full((100, 100, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["･"],
            inner_rect=(20, 20, 60, 60),
            line_height=36,
            measured_width=6,
            measured_height=6,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image,
            "･",
            [20, 20, 60, 60],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark_pixels = np.where(np.all(rendered < 80, axis=2))

        self.assertGreater(len(dark_pixels[0]), 0)

    def test_mixed_halfwidth_middle_dot_line_uses_font_ellipsis(self) -> None:
        image = np.full((120, 180, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["･･･흑"],
            inner_rect=(20, 20, 140, 80),
            line_height=36,
            measured_width=64,
            measured_height=32,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image,
            "･･･흑",
            [20, 20, 140, 80],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark_pixels = np.where(np.all(rendered < 80, axis=2))

        self.assertGreater(len(dark_pixels[0]), 0)
        self.assertGreaterEqual(dark_pixels[1].max() - dark_pixels[1].min(), 20)
        self.assertEqual(normalize_inline_dot_ellipsis("･･･흑"), "…흑")
        self.assertEqual(normalize_inline_dot_ellipsis("･･･"), "…")
        self.assertEqual(normalize_inline_dot_ellipsis("･･････"), "……")

    def test_dot_only_ellipsis_line_uses_font_ellipsis(self) -> None:
        image = np.full((120, 180, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["･･････"],
            inner_rect=(20, 20, 140, 80),
            line_height=36,
            measured_width=64,
            measured_height=32,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image,
            "･･････",
            [20, 20, 140, 80],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark_pixels = np.where(np.all(rendered < 80, axis=2))

        self.assertGreater(len(dark_pixels[0]), 0)

    def test_dot_only_ellipsis_text_uses_single_dot_run_line(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "………",
            [950, 130, 90, 200],
            image.size,
            FONT_PATH,
            src_line_count=1,
        )

        self.assertTrue(is_dot_ellipsis_only_text("………"))
        self.assertEqual(dot_ellipsis_vertical_lines("………"), ["………"])
        self.assertEqual(layout.lines, ["………"])

    def test_ocr_vertical_ellipsis_is_treated_as_dot_run(self) -> None:
        self.assertTrue(is_dot_ellipsis_only_text("⋮"))
        self.assertEqual(dot_ellipsis_vertical_lines("⋮"), ["⋮"])

    def test_synthetic_dot_radius_follows_font_weight(self) -> None:
        normal = ImageFont.truetype(str(FONT_PATH), 48)
        bold_path = Path("Fonts/Pretendard-Black.otf")
        if not bold_path.exists():
            self.skipTest(f"font not found: {bold_path}")
        bold = ImageFont.truetype(str(bold_path), 48)

        self.assertGreater(synthetic_dot_radius_for_font(bold), synthetic_dot_radius_for_font(normal))

    def test_halfwidth_middle_dots_use_compact_spacing(self) -> None:
        offsets = vertical_line_offsets(["그", "･", "･", "･", "!"], 42, 32)

        self.assertLess(offsets[2] - offsets[1], 42)
        self.assertAlmostEqual(offsets[2] - offsets[1], offsets[3] - offsets[2])
        self.assertLess(offsets[4] - offsets[3], 42)

    def test_single_dot_line_starts_below_previous_glyph(self) -> None:
        image = np.full((180, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=36,
            lines=["워", "."],
            inner_rect=(20, 20, 80, 140),
            line_height=41,
            measured_width=30,
            measured_height=80,
            target_lines=2,
        )

        rendered, _ = draw_text_block(
            image,
            "워.",
            [20, 20, 80, 140],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark_by_y = np.where(np.all(rendered < 80, axis=2))[0]
        self.assertGreater(len(dark_by_y), 0)

    def test_dot_run_line_starts_below_previous_glyph(self) -> None:
        image = np.full((220, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=36,
            lines=["워", "………"],
            inner_rect=(20, 20, 80, 180),
            line_height=41,
            measured_width=30,
            measured_height=120,
            target_lines=2,
        )

        rendered, _ = draw_text_block(
            image,
            "워………",
            [20, 20, 80, 180],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark_by_y = np.where(np.all(rendered < 80, axis=2))[0]
        self.assertGreater(len(dark_by_y), 0)

    def test_inline_trailing_ellipsis_draws_below_body(self) -> None:
        image = np.full((180, 180, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=36,
            lines=["무서워…"],
            inner_rect=(20, 20, 140, 140),
            line_height=41,
            measured_width=90,
            measured_height=80,
            target_lines=1,
        )

        rendered, _ = draw_text_block(
            image,
            "무서워…",
            [20, 20, 140, 140],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark_pixels = np.where(np.all(rendered < 80, axis=2))

        self.assertGreater(len(dark_pixels[0]), 0)

    def test_horizontal_multiline_keeps_ellipsis_horizontal(self) -> None:
        layout = Layout(
            font_size=44,
            lines=["그게", "너무", "무서워", "…"],
            inner_rect=(20, 20, 140, 220),
            line_height=50,
            measured_width=90,
            measured_height=180,
            target_lines=4,
        )

        self.assertFalse(layout_uses_vertical_dot_runs(layout))

    def test_explicit_leading_ellipsis_keeps_horizontal_line_gap(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "……\n저기",
            [100, 100, 220, 260],
            image.size,
            FONT_PATH,
            src_line_count=1,
            preserve_explicit_lines=True,
        )

        self.assertEqual(layout.lines, ["……", "저기"])
        self.assertFalse(layout_uses_vertical_dot_runs(layout))
        offsets = [idx * layout.line_height for idx in range(len(layout.lines))]
        self.assertLessEqual(offsets[1] - offsets[0], layout.line_height)

    def test_llm_lines_are_kept_with_smaller_font_when_nothing_fits_min_size(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (960, 1080), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "불과 며칠 만에\n약을 개발하는 건\n불가능해요",
            [589, 791, 102, 97],
            image.size,
            FONT_PATH,
            src_line_count=3,
        )

        self.assertEqual(layout.lines, ["불과 며칠 만에", "약을 개발하는 건", "불가능해요"])
        self.assertLess(layout.font_size, 16)

    def test_explicit_translation_lines_are_preferred_over_height_filling_split(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "보고도\n못 본 척을 하며…",
            [438, 451, 142, 314],
            image.size,
            FONT_PATH,
            src_line_count=2,
            is_split_rect=True,
            prefer_explicit_lines_when_reasonable=True,
        )

        self.assertEqual(layout.lines, ["보고도", "못 본 척을 하며…"])
        self.assertLess(layout.measured_height / layout.inner_rect[3], 0.5)

    def test_manual_stacked_dots_use_compact_spacing(self) -> None:
        offsets = vertical_line_offsets(["아", ".", ".", "."], 42, 32)

        first_dot_gap = offsets[1] - offsets[0]
        dot_gap_1 = offsets[2] - offsets[1]
        dot_gap_2 = offsets[3] - offsets[2]
        self.assertGreaterEqual(first_dot_gap, 42)
        self.assertGreater(first_dot_gap, 32 * 1.0)
        self.assertGreater(dot_gap_1, 32 * 0.28)
        self.assertAlmostEqual(dot_gap_1, dot_gap_2)

    def test_manual_stacked_dots_keep_trailing_char_gap_readable(self) -> None:
        offsets = vertical_line_offsets([".", ".", ".", "아"], 42, 32)

        trailing_gap = offsets[3] - offsets[2]
        self.assertLess(trailing_gap, 42)
        self.assertAlmostEqual(trailing_gap, 32 * 0.22, delta=1.0)

    def test_manual_stacked_dots_before_short_text_stays_compact(self) -> None:
        offsets = vertical_line_offsets([".", ".", ".", "그", "저"], 42, 32)

        dot_gap = offsets[2] - offsets[1]
        trailing_gap = offsets[3] - offsets[2]
        normal_gap = offsets[4] - offsets[3]
        self.assertLess(trailing_gap, normal_gap)
        self.assertLess(trailing_gap, dot_gap)

    def test_dot_run_line_before_short_text_stays_compact(self) -> None:
        offsets = vertical_line_offsets(["...", "그", "저"], 42, 32)

        dot_run_height = synthetic_vertical_dot_run_size(32, 3)[1]
        trailing_gap = offsets[1] - offsets[0] - dot_run_height
        normal_gap = offsets[2] - offsets[1]
        self.assertLess(trailing_gap, normal_gap)
        self.assertAlmostEqual(trailing_gap, 32 * 0.06, delta=1.0)

    def test_rendered_dot_run_to_korean_glyph_gap_is_compact(self) -> None:
        image = np.full((260, 120, 3), 255, dtype=np.uint8)
        layout = Layout(
            font_size=32,
            lines=["...", "그", "저"],
            inner_rect=(20, 20, 80, 220),
            line_height=42,
            measured_width=40,
            measured_height=160,
            target_lines=3,
        )

        rendered, _ = draw_text_block(
            image,
            "...\n그\n저",
            [20, 20, 80, 220],
            FONT_PATH,
            layout=layout,
            bg_alpha=0,
            text_fill=(0, 0, 0),
            text_stroke_width=0,
        )
        dark = np.all(rendered < 100, axis=2)
        ys, xs = np.where(dark)
        dot_bottom = int(ys[(xs >= 56) & (xs <= 64) & (ys < 110)].max())
        glyph_top = int(ys[(ys > dot_bottom) & (xs >= 45) & (xs <= 75)].min())

        self.assertLessEqual(glyph_top - dot_bottom - 1, 12)

    def test_single_korean_vertical_chars_are_compact_after_dot_run(self) -> None:
        offsets = vertical_line_offsets(["...", "그", "저"], 42, 32)

        self.assertLess(offsets[2] - offsets[1], 42)
        self.assertAlmostEqual(offsets[2] - offsets[1], 32 * 0.94, delta=1.0)

    def test_vertical_ellipsis_marker_keeps_regular_spacing(self) -> None:
        offsets = vertical_line_offsets(["거", "짓", "말", "⋮"], 21, 20)

        self.assertGreater(offsets[3] - offsets[2], 21)

    def test_large_title_style_forces_serif_font(self) -> None:
        cluster = {
            "render_hint_final": "normal",
            "style_features": {
                "line_style_features": {
                    "font_size_estimate": 144.4,
                    "char_count": 7,
                    "line_count": 2,
                    "short_label_candidate": True,
                }
            },
        }

        self.assertTrue(should_force_serif_for_large_title_style(cluster))

    def test_large_title_style_tries_vertical_layout(self) -> None:
        cluster = {
            "render_hint_final": "normal",
            "style_features": {
                "line_style_features": {
                    "font_size_estimate": 144.4,
                    "char_count": 7,
                    "line_count": 2,
                    "short_label_candidate": True,
                }
            },
        }

        self.assertTrue(should_try_vertical_title_layout(cluster, "「플레임\n컬럼」!"))

    def test_impact_short_effect_tries_vertical_layout(self) -> None:
        cluster = {
            "render_hint_final": "impact",
            "style_features": {
                "line_style_features": {
                    "font_size_estimate": 81.5,
                    "char_count": 2,
                    "line_count": 1,
                    "short_label_candidate": True,
                }
            },
        }

        self.assertTrue(should_try_vertical_title_layout(cluster, "으와아"))

    def test_short_single_line_with_ellipsis_can_switch_to_vertical_on_moderate_aspect(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "응...",
            [100, 1200, 150, 240],
            image.size,
            FONT_PATH,
            src_line_count=1,
        )

        self.assertEqual(layout.lines, ["응", ".", ".", "."])

    def test_tall_vertical_bubble_switches_to_vertical_when_horizontal_underuses_height(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "아\n알겠습니다",
            [508, 304, 84, 194],
            image.size,
            FONT_PATH,
            max_font_size=29,
            is_split_rect=True,
            src_line_count=1,
            mask_centroid=[550, 401],
            round_score=0.561,
        )

        self.assertEqual(layout.lines, ["아", "알", "겠", "습", "니", "다"])
        self.assertGreaterEqual(layout.font_size, 29)

    def test_chunk_korean_text_avoids_one_character_tail(self) -> None:
        self.assertEqual(chunk_korean_text("플레이어들로부터"), ["플레", "이어", "들로", "부터"])

    def test_long_single_vertical_source_uses_short_chunks_after_page_font_cap(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "플레이어들로부터",
            [1132, 125, 127, 302],
            image.size,
            FONT_PATH,
            max_font_size=21,
            src_line_count=1,
            round_score=0.518,
        )

        self.assertEqual(layout.lines, ["플레", "이어", "들로", "부터"])

    def test_overlong_vertical_ellipsis_phrase_can_use_two_short_lines(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        self.assertEqual(split_overlong_vertical_ellipsis_text("…만들었다고…?"), ["...만들었", "다고...?"])

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "…만들었다고…?",
            [423, 768, 83, 192],
            image.size,
            FONT_PATH,
            max_font_size=19,
            is_split_rect=True,
            src_line_count=1,
            round_score=0.438,
        )

        self.assertEqual(layout.lines, ["...만들었", "다고...?"])

    def test_tall_bubble_splits_copula_ending_without_isolating_initial_particle(self) -> None:
        font_path = Path("Fonts/Freesentation-7Bold.ttf")
        if not font_path.exists():
            self.skipTest(f"font not found: {font_path}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "는 그 고용인이구나",
            [836, 480, 125, 250],
            image.size,
            font_path,
            min_font_size=24,
            max_font_size=30,
            is_split_rect=True,
            src_line_count=2,
            mask_centroid=[899, 605],
            round_score=0.541,
        )

        self.assertEqual(layout.lines, ["는 그", "고용인", "이구나"])

    def test_tall_bubble_splits_casual_explanatory_ending_for_target_lines(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "결국\n자업자득이잖아…",
            [587, 550, 144, 231],
            image.size,
            FONT_PATH,
            is_split_rect=True,
            src_line_count=2,
            mask_centroid=[659, 666],
            round_score=0.739,
        )

        self.assertEqual(layout.lines, ["결국", "자업자득", "이잖아", "…"])

    def test_tall_unboxed_text_expands_korean_suffixes_for_vertical_flow(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "현상태에서 보고할 수\n있는 건 이 정도인가…",
            [927, 1475, 96, 316],
            image.size,
            FONT_PATH,
            is_unboxed=True,
            src_line_count=2,
            round_score=0.289,
        )

        self.assertEqual(layout.lines, ["현상태", "에서", "보고할 수", "있는 건", "이 정도", "인가", "…"])

    def test_tall_unboxed_two_token_text_can_expand_beyond_source_line_count(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "에리카만으로도\n성가신데",
            [974, 1217, 125, 308],
            image.size,
            FONT_PATH,
            is_unboxed=True,
            src_line_count=2,
            round_score=0.134,
        )

        self.assertEqual(layout.lines, ["에리카", "만으로도", "성가신데"])

    def test_multiline_tall_bubble_keeps_name_and_trailing_ellipsis_separate(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "미안하다\n에리카……",
            [186, 1537, 149, 383],
            image.size,
            FONT_PATH,
            src_line_count=2,
            round_score=0.633,
        )

        self.assertEqual(layout.lines[-2:], ["에리카", "……"])

    def test_vertical_terminal_punctuation_gets_own_line(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "안녕!",
            [100, 1200, 120, 360],
            image.size,
            FONT_PATH,
            src_line_count=1,
        )

        self.assertEqual(layout.lines, ["안", "녕", "!"])

    def test_area_based_unboxed_cap_grows_for_large_bbox(self) -> None:
        self.assertGreaterEqual(area_based_font_cap(235, 331, is_unboxed=True), 52)

    def test_unboxed_font_can_grow_to_match_large_bbox(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "왠지\n텐션이\n올라가!",
            [523, 1139, 188, 258],
            image.size,
            FONT_PATH,
            max_font_size=area_based_font_cap(188, 258, is_unboxed=True),
            is_unboxed=True,
            src_line_count=3,
        )

        self.assertGreaterEqual(layout.font_size, 52)
        self.assertGreater(layout_fill_ratio(layout), 0.42)

    def test_overfilled_bubble_raw_layout_is_not_rescued_above_page_band(self) -> None:
        raw = Layout(
            font_size=45,
            lines=["애초에", "이건", "현실", "인가?"],
            inner_rect=(948, 836, 104, 256),
            line_height=51,
            measured_width=98,
            measured_height=191,
            target_lines=4,
        )

        self.assertGreater(layout_fill_ratio(raw), 0.62)
        self.assertFalse(should_rescue_area_fit(raw, page_max_size=33, is_unboxed=False))

    def test_wrap_recombines_exploded_tokens_to_avoid_short_singleton(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "※큰일이\n나버리니까\n부디 잘\n부탁드립니다",
            [183, 1102, 160, 277],
            image.size,
            FONT_PATH,
            src_line_count=4,
            round_score=0.731,
        )

        self.assertEqual(layout.lines, ["※큰일이", "나버리니까", "부디 잘", "부탁", "드립니다"])

    def test_wrap_avoids_splitting_transliterated_name(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "최근 몇 년간\n스토크스는\n자연재해가 많고",
            [1017, 734, 160, 309],
            image.size,
            FONT_PATH,
            src_line_count=3,
            round_score=0.416,
        )

        self.assertNotIn("스토", layout.lines)
        self.assertNotIn("크스는", layout.lines)
        self.assertIn("스토크스는", layout.lines)

    def test_tall_bubble_prefers_single_word_first_line_over_dangling_modifier(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "최근 몇 년간\n스토크스는\n자연재해가 많고",
            [1017, 734, 160, 309],
            image.size,
            FONT_PATH,
            src_line_count=3,
            round_score=0.416,
        )

        self.assertEqual(layout.lines, ["최근", "몇 년간", "스토크스는", "자연재해가", "많고"])

    def test_tall_bubble_avoids_dangling_korean_particle_at_line_end(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "뭐 솔직히 이 집이\n어떻게 되든 알 바\n아니긴 한데",
            [684, 1398, 141, 241],
            image.size,
            FONT_PATH,
            src_line_count=1,
            round_score=0.742,
        )

        self.assertEqual(layout.lines, ["뭐 솔직히", "이 집이", "어떻게", "되든 알 바", "아니긴", "한데"])

    def test_narrow_vertical_unboxed_text_allows_small_overflow_for_better_phrase_wrap(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "그 정도로 세세한\n묘사는 없었다",
            [174, 1040, 93, 235],
            image.size,
            FONT_PATH,
            is_unboxed=True,
            src_line_count=2,
            round_score=0.127,
        )

        self.assertEqual(layout.lines, ["그 정도로", "세세한", "묘사는", "없었다"])
        self.assertLessEqual(layout.measured_width, allowed_line_width(layout.inner_rect[2], layout.inner_rect[3], True))

    def test_tall_bubble_prefers_aggressive_four_line_wrap(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "나는 평범한\n대학생이고",
            [361, 412, 98, 230],
            image.size,
            FONT_PATH,
            src_line_count=2,
            mask_centroid=[414, 528],
        )

        normalized_lines = [re.sub(r"\s+", " ", line).strip() for line in layout.lines]
        self.assertEqual(normalized_lines, ["나는", "평범한", "대학생", "이고"])
        self.assertEqual(layout.target_lines, 4)

    def test_short_sfx_cluster_forces_vertical_interjection_layout(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        cluster = {
            "text_translated": "으와아",
            "text_type": "sfx",
            "render_hint": "sfx",
            "orientation": "vertical",
            "location": "bubble",
            "matched_cluster_details": [
                {
                    "text": "おわ",
                    "bounding_rect": [191, 918, 76, 163],
                    "lines": [{"text": "おわ", "bbox": [191, 918, 76, 163]}],
                }
            ],
        }
        self.assertTrue(should_force_vertical_short_interjection(cluster, "으와아"))

    def test_legacy_efx_cluster_forces_vertical_interjection_layout(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        cluster = {
            "text_translated": "으와아",
            "text_type": "sfx",
            "render_hint": "efx",
            "orientation": "vertical",
            "location": "bubble",
            "matched_cluster_details": [
                {
                    "text": "おわ",
                    "bounding_rect": [191, 918, 76, 163],
                    "lines": [{"text": "おわ", "bbox": [191, 918, 76, 163]}],
                }
            ],
        }
        self.assertTrue(should_force_vertical_short_interjection(cluster, "으와아"))

        image = Image.new("RGB", (1350, 1920), "white")
        draw = ImageDraw.Draw(image)
        layout = choose_layout(
            draw,
            "으와아",
            [139, 906, 181, 271],
            image.size,
            FONT_PATH,
            src_line_count=1,
            force_vertical_short_interjection=True,
        )

        self.assertEqual(layout.lines, ["으", "와", "아"])

    def test_shared_safe_text_rect_uses_text_group_centered_sub_rect(self) -> None:
        cluster = {
            "matched_cluster": [5],
            "union_bbox": [573, 725, 158, 275],
            "matched_cluster_details": [
                {
                    "bounding_rect": [600, 794, 103, 149],
                    "lines": [{"bbox": [600, 794, 103, 149]}],
                }
            ],
        }
        flood_info = {
            5: {
                "bubble_rect": [204, 725, 552, 421],
                "safe_text_rect": [204, 736, 552, 327],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }

        bbox, source = overlay_bbox_for_cluster(
            cluster,
            represented_anchor_ids={5, 13},
            shared_groups={5: [5, 13], 13: [5, 13]},
            flood_info_by_anchor=flood_info,
        )

        self.assertEqual(bbox, [564, 742, 175, 253])
        self.assertEqual(source, "text_group_centered_sub_rect_from_A5")

    def test_shared_safe_text_rect_uses_sub_rect_even_when_tg_is_near_safe_center(self) -> None:
        cluster = {
            "matched_cluster": [1],
            "union_bbox": [847, 698, 503, 426],
            "matched_cluster_details": [
                {
                    "bounding_rect": [1103, 781, 109, 238],
                    "lines": [{"bbox": [1103, 781, 109, 238]}],
                }
            ],
        }
        flood_info = {
            1: {
                "bubble_rect": [847, 698, 503, 426],
                "safe_text_rect": [891, 717, 459, 390],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }

        bbox, source = overlay_bbox_for_cluster(
            cluster,
            represented_anchor_ids={1, 3},
            shared_groups={1: [1, 3], 3: [1, 3]},
            flood_info_by_anchor=flood_info,
        )

        self.assertEqual(bbox, [1065, 717, 185, 385])
        self.assertEqual(source, "text_group_centered_sub_rect_from_A1")

    def test_subset_of_single_split_region_does_not_reuse_parent_rect(self) -> None:
        cluster = {
            "matched_cluster": [9],
            "union_bbox": [76, 1256, 286, 572],
            "matched_cluster_details": [
                {
                    "bounding_rect": [229, 1356, 62, 206],
                    "lines": [{"bbox": [229, 1356, 62, 206]}],
                }
            ],
        }
        split_info = {
            9: {
                "region_id": 8,
                "anchor_ids": [9, 10],
                "rect": [76, 1256, 286, 572],
                "safe_text_rect": [76, 1256, 286, 572],
            },
            10: {
                "region_id": 8,
                "anchor_ids": [9, 10],
                "rect": [76, 1256, 286, 572],
                "safe_text_rect": [76, 1256, 286, 572],
            },
        }
        flood_info = {
            9: {
                "bubble_rect": [76, 1256, 286, 572],
                "safe_text_rect": [76, 1256, 286, 572],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
            10: {
                "bubble_rect": [76, 1256, 286, 572],
                "safe_text_rect": [76, 1256, 286, 572],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
        }

        bbox, source = overlay_bbox_for_cluster(
            cluster,
            represented_anchor_ids={9, 10},
            shared_groups={9: [9, 10], 10: [9, 10]},
            flood_info_by_anchor=flood_info,
            split_region_by_anchor=split_info,
        )

        self.assertNotEqual(bbox, [76, 1256, 286, 572])
        self.assertEqual(source, "text_group_centered_sub_rect_from_A9")

    def test_text_group_centered_sub_rect_requires_off_center_small_detail(self) -> None:
        cluster = {
            "matched_cluster_details": [
                {
                    "bounding_rect": [350, 830, 120, 130],
                    "lines": [{"bbox": [350, 830, 120, 130]}],
                }
            ],
        }

        self.assertIsNone(text_group_centered_sub_rect(cluster, [200, 700, 420, 420]))

    def test_single_tg_centered_safe_rect_biases_toward_distant_detail(self) -> None:
        cluster = {
            "text_translated": "역시 지금\n혼자 두게 해서는\n안 됐던 걸까…?",
            "matched_cluster_details": [
                {
                    "bounding_rect": [710, 1029, 149, 201],
                    "lines": [{"bbox": [710, 1029, 149, 201]}],
                }
            ],
        }

        rect = single_tg_centered_safe_rect(cluster, [310, 1010, 458, 238], [143, 1005, 799, 253])

        self.assertEqual(rect, [650, 1010, 268, 238])

    def test_single_tg_centered_safe_rect_skips_long_text(self) -> None:
        cluster = {
            "text_translated": "최근 몇 년간 스토크스는 자연재해가 많고 홍수와 중세에 시달리는 농가가 많으며 복구도 더디다",
            "matched_cluster_details": [
                {
                    "bounding_rect": [710, 1029, 149, 201],
                    "lines": [{"bbox": [710, 1029, 149, 201]}],
                }
            ],
        }

        self.assertIsNone(single_tg_centered_safe_rect(cluster, [310, 1010, 458, 238]))

    def test_large_irregular_single_tg_bubble_can_use_tg_center_for_long_text(self) -> None:
        cluster = {
            "matched_cluster": [7],
            "text_translated": "문제는\n어떻게\n이 녀석을\n건네줄까…",
            "matched_cluster_details": [
                {
                    "bounding_rect": [629, 1188, 188, 189],
                    "lines": [{"bbox": [629, 1188, 188, 189]}],
                }
            ],
        }
        flood_info = {
            "is_valid": True,
            "region_type_guess": "bubble",
            "anchor_ids": [7],
            "bubble_rect": [143, 1172, 860, 216],
            "quality_flags": ["raw_solid=0.987, raw_round=0.398, solid=0.987, round=0.481, shape=1.063"],
        }

        self.assertTrue(should_anchor_single_tg_in_irregular_large_bubble(flood_info))
        rect = single_tg_centered_safe_rect(
            cluster,
            [143, 1172, 860, 216],
            [143, 1172, 860, 216],
            allow_long_text=True,
        )

        self.assertEqual(rect, [486, 1172, 473, 216])

    def test_small_bubble_keeps_safe_rect_centered(self) -> None:
        flood_info = {
            "bubble_rect": [1016, 1366, 147, 248],
            "safe_text_rect": [1037, 1380, 108, 205],
        }

        self.assertTrue(should_keep_safe_rect_centered_for_small_bubble(flood_info))

    def test_short_vertical_source_uses_bubble_rect_for_vertical_layout(self) -> None:
        cluster = {
            "matched_cluster": [4],
            "text_translated": "거짓말…",
            "matched_cluster_details": [
                {
                    "bounding_rect": [811, 911, 9, 29],
                    "lines": [{"bbox": [811, 911, 9, 29]}],
                }
            ],
        }
        flood_info = {
            "is_valid": True,
            "region_type_guess": "bubble",
            "bubble_rect": [764, 883, 64, 118],
        }

        self.assertTrue(should_use_bubble_rect_for_short_vertical_source(cluster, flood_info))

    def test_close_shared_segments_are_merged_before_overlay(self) -> None:
        clusters = [
            {
                "matched_cluster": [6],
                "source_region_id": 6,
                "text": "どうか",
                "text_translated": "부디",
                "matched_cluster_details": [
                    {"bounding_rect": [340, 1282, 36, 133], "lines": [{"bbox": [340, 1282, 36, 133]}]},
                ],
            },
            {
                "matched_cluster": [8],
                "source_region_id": 6,
                "text": "お願いします…!",
                "text_translated": "부탁드립니다…!",
                "matched_cluster_details": [
                    {"bounding_rect": [268, 1287, 40, 359], "lines": [{"bbox": [268, 1287, 40, 359]}]},
                ],
            },
        ]
        flood_info = {
            6: {
                "region_id": 6,
                "anchor_ids": [6, 8],
                "bubble_rect": [184, 1203, 283, 517],
                "safe_text_rect": [222, 1252, 197, 390],
                "mask_centroid": [320, 1447],
                "ocr_text_rects": [[338, 1280, 40, 137], [266, 1285, 44, 363]],
            },
            8: {
                "region_id": 6,
                "anchor_ids": [6, 8],
                "bubble_rect": [184, 1203, 283, 517],
                "safe_text_rect": [222, 1252, 197, 390],
                "mask_centroid": [320, 1447],
                "ocr_text_rects": [[338, 1280, 40, 137], [266, 1285, 44, 363]],
            },
        }

        self.assertFalse(shared_group_is_spatially_separated([6, 8], flood_info))
        merged = merge_unseparated_shared_segments(clusters, {6: [6, 8], 8: [6, 8]}, flood_info)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_cluster"], [6, 8])
        self.assertEqual(merged[0]["text_translated"], "부디\n부탁드립니다…!")

    def test_far_shared_segments_remain_split(self) -> None:
        flood_info = {
            5: {
                "region_id": 7,
                "anchor_ids": [5, 14],
                "bubble_rect": [204, 725, 552, 421],
                "safe_text_rect": [204, 736, 552, 327],
                "mask_centroid": [480, 900],
                "ocr_text_rects": [[600, 794, 103, 149], [250, 790, 110, 150]],
            },
            14: {
                "region_id": 7,
                "anchor_ids": [5, 14],
                "bubble_rect": [204, 725, 552, 421],
                "safe_text_rect": [204, 736, 552, 327],
                "mask_centroid": [480, 900],
                "ocr_text_rects": [[600, 794, 103, 149], [250, 790, 110, 150]],
            },
        }

        self.assertTrue(shared_group_is_spatially_separated([5, 14], flood_info))

    def test_round_normal_sized_ellipse_does_not_split_by_width_only(self) -> None:
        clusters = [
            {
                "matched_cluster": [11],
                "source_region_id": 11,
                "source_segment_index": 1,
                "text_translated": "혈통을 원했던\n스토크스 가문이",
                "matched_cluster_details": [
                    {"bounding_rect": [172, 1522, 162, 236], "lines": [{"bbox": [172, 1522, 162, 236]}]},
                ],
            },
            {
                "matched_cluster": [13],
                "source_region_id": 11,
                "source_segment_index": 2,
                "text_translated": "앞뒤 가리지 않고\n은혜를 팔러 갔다\n는 거지",
                "matched_cluster_details": [
                    {"bounding_rect": [128, 1525, 25, 143], "lines": [{"bbox": [128, 1525, 25, 143]}]},
                ],
            },
        ]
        flood_info = {
            11: {
                "region_id": 11,
                "anchor_ids": [11, 13],
                "bubble_rect": [101, 1433, 272, 385],
                "safe_text_rect": [123, 1472, 228, 308],
                "mask_centroid": [237, 1626],
                "quality_flags": ["raw_solid=0.996, raw_round=0.855, solid=0.996, round=0.857, shape=1.36"],
            },
            13: {
                "region_id": 11,
                "anchor_ids": [11, 13],
                "bubble_rect": [101, 1433, 272, 385],
                "safe_text_rect": [123, 1472, 228, 308],
                "mask_centroid": [237, 1626],
                "quality_flags": ["raw_solid=0.996, raw_round=0.855, solid=0.996, round=0.857, shape=1.36"],
            },
        }

        self.assertFalse(shared_group_is_spatially_separated(
            [11, 13],
            flood_info,
            [c["matched_cluster_details"][0]["bounding_rect"] for c in clusters],
        ))
        merged = merge_unseparated_shared_segments(clusters, {11: [11, 13], 13: [11, 13]}, flood_info)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_cluster"], [11, 13])

    def test_segment_mode_split_forces_split_even_when_auto_merges(self) -> None:
        clusters = [
            {"matched_cluster": [1], "source_region_id": 3, "source_segment_index": 1, "text_translated": "첫째", "matched_cluster_details": [{"bounding_rect": [100, 100, 40, 120]}]},
            {"matched_cluster": [2], "source_region_id": 3, "source_segment_index": 2, "text_translated": "둘째", "matched_cluster_details": [{"bounding_rect": [140, 105, 38, 110]}]},
        ]
        flood_info = {
            1: {"region_id": 3, "anchor_ids": [1, 2], "bubble_rect": [80, 80, 180, 240], "safe_text_rect": [90, 90, 150, 210], "mask_centroid": [160, 190]},
            2: {"region_id": 3, "anchor_ids": [1, 2], "bubble_rect": [80, 80, 180, 240], "safe_text_rect": [90, 90, 150, 210], "mask_centroid": [160, 190]},
        }

        merged = merge_unseparated_shared_segments(clusters, {1: [1, 2], 2: [1, 2]}, flood_info)
        split = merge_unseparated_shared_segments(
            clusters,
            {1: [1, 2], 2: [1, 2]},
            flood_info,
            {"3": {"segment_mode": "split"}},
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(len(split), 2)

    def test_segment_mode_merge_forces_merge_even_when_auto_splits(self) -> None:
        clusters = [
            {"matched_cluster": [5], "source_region_id": 7, "source_segment_index": 1, "text_translated": "해롤드", "matched_cluster_details": [{"bounding_rect": [600, 794, 103, 149]}]},
            {"matched_cluster": [14], "source_region_id": 7, "source_segment_index": 2, "text_translated": "원작", "matched_cluster_details": [{"bounding_rect": [250, 790, 110, 150]}]},
        ]
        flood_info = {
            5: {"region_id": 7, "anchor_ids": [5, 14], "bubble_rect": [204, 725, 552, 421], "safe_text_rect": [204, 736, 552, 327], "mask_centroid": [480, 900], "ocr_text_rects": [[600, 794, 103, 149], [250, 790, 110, 150]]},
            14: {"region_id": 7, "anchor_ids": [5, 14], "bubble_rect": [204, 725, 552, 421], "safe_text_rect": [204, 736, 552, 327], "mask_centroid": [480, 900], "ocr_text_rects": [[600, 794, 103, 149], [250, 790, 110, 150]]},
        }

        split = merge_unseparated_shared_segments(clusters, {5: [5, 14], 14: [5, 14]}, flood_info)
        merged = merge_unseparated_shared_segments(
            clusters,
            {5: [5, 14], 14: [5, 14]},
            flood_info,
            {"7": {"segment_mode": "merge"}},
        )

        self.assertEqual(len(split), 2)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_cluster"], [5, 14])

    def test_far_shared_segments_use_cluster_details_when_region_has_no_ocr_rects(self) -> None:
        clusters = [
            {
                "matched_cluster": [5],
                "source_region_id": 7,
                "text_translated": "해롤드\n스토크스로서",
                "matched_cluster_details": [
                    {"bounding_rect": [600, 794, 103, 149], "lines": [{"bbox": [600, 794, 103, 149]}]},
                ],
            },
            {
                "matched_cluster": [13],
                "source_region_id": 7,
                "text_translated": "원작을 따르면서\n최선의 수단이다",
                "matched_cluster_details": [
                    {"bounding_rect": [256, 865, 163, 207], "lines": [{"bbox": [256, 865, 163, 207]}]},
                ],
            },
        ]
        flood_info = {
            5: {
                "region_id": 7,
                "anchor_ids": [5, 13],
                "bubble_rect": [204, 725, 552, 421],
                "safe_text_rect": [204, 736, 552, 327],
                "mask_centroid": [456, 900],
            },
            13: {
                "region_id": 7,
                "anchor_ids": [5, 13],
                "bubble_rect": [204, 725, 552, 421],
                "safe_text_rect": [204, 736, 552, 327],
                "mask_centroid": [456, 900],
            },
        }

        merged = merge_unseparated_shared_segments(clusters, {5: [5, 13], 13: [5, 13]}, flood_info)

        self.assertEqual(len(merged), 2)
        self.assertEqual([c["matched_cluster"] for c in merged], [[5], [13]])

    def test_large_shared_bubble_splits_when_tg_centers_are_far_from_each_other(self) -> None:
        clusters = [
            {
                "matched_cluster": [1],
                "source_region_id": 1,
                "text_translated": "왜\n게임 캐릭터가\n움직이고 있는 거지…?",
                "matched_cluster_details": [
                    {"bounding_rect": [1103, 781, 109, 238], "lines": [{"bbox": [1103, 781, 109, 238]}]},
                ],
            },
            {
                "matched_cluster": [3],
                "source_region_id": 1,
                "text_translated": "애초에 이건\n현실인가?",
                "matched_cluster_details": [
                    {"bounding_rect": [963, 895, 75, 208], "lines": [{"bbox": [963, 895, 75, 208]}]},
                ],
            },
        ]
        flood_info = {
            1: {
                "region_id": 1,
                "anchor_ids": [1, 3],
                "bubble_rect": [847, 698, 503, 426],
                "safe_text_rect": [891, 717, 459, 390],
                "mask_centroid": [1130, 912],
            },
            3: {
                "region_id": 1,
                "anchor_ids": [1, 3],
                "bubble_rect": [847, 698, 503, 426],
                "safe_text_rect": [891, 717, 459, 390],
                "mask_centroid": [1130, 912],
            },
        }

        merged = merge_unseparated_shared_segments(clusters, {1: [1, 3], 3: [1, 3]}, flood_info)

        self.assertEqual(len(merged), 2)
        self.assertEqual([c["matched_cluster"] for c in merged], [[1], [3]])

    def test_large_round_vertical_bubble_keeps_close_columns_merged(self) -> None:
        clusters = [
            {
                "matched_cluster": [5],
                "source_region_id": 1,
                "source_segment_index": 1,
                "text_translated": "우선\n스메라기 가문을 대표해서",
                "matched_cluster_details": [
                    {"bounding_rect": [970, 177, 149, 397], "lines": [{"bbox": [970, 177, 149, 397]}]},
                ],
            },
            {
                "matched_cluster": [2],
                "source_region_id": 1,
                "source_segment_index": 2,
                "text_translated": "감사 인사를\n드리겠습니다",
                "matched_cluster_details": [
                    {"bounding_rect": [1145, 174, 33, 118], "lines": [{"bbox": [1145, 174, 33, 118]}]},
                ],
            },
        ]
        flood_info = {
            2: {
                "region_id": 1,
                "anchor_ids": [2, 5],
                "bubble_rect": [937, 83, 272, 558],
                "safe_text_rect": [937, 120, 272, 482],
                "mask_centroid": [1076, 361],
                "quality_flags": ["raw_solid=0.998, raw_round=0.767, solid=0.998, round=0.772, shape=1.344"],
            },
            5: {
                "region_id": 1,
                "anchor_ids": [2, 5],
                "bubble_rect": [937, 83, 272, 558],
                "safe_text_rect": [937, 120, 272, 482],
                "mask_centroid": [1076, 361],
                "quality_flags": ["raw_solid=0.998, raw_round=0.767, solid=0.998, round=0.772, shape=1.344"],
            },
        }

        self.assertFalse(shared_group_is_spatially_separated([2, 5], flood_info, [c["matched_cluster_details"][0]["bounding_rect"] for c in clusters]))
        merged = merge_unseparated_shared_segments(clusters, {2: [2, 5], 5: [2, 5]}, flood_info)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_cluster"], [5, 2])
        self.assertEqual(merged[0]["text_translated"], "우선\n스메라기 가문을 대표해서\n감사 인사를\n드리겠습니다")

    def test_text_group_centered_sub_rect_layout_keeps_tg_center_after_region_centroid(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = np.full((1920, 1350, 3), 255, dtype=np.uint8)
        clusters = [
            {
                "matched_cluster": [8],
                "source_region_id": 7,
                "text_translated": "해롤드 님을\n곁에서 보살피지\n않았던\n걸까요…",
                "matched_cluster_details": [
                    {"bounding_rect": [456, 595, 147, 208], "lines": [{"bbox": [456, 595, 147, 208]}]},
                ],
            },
            {
                "matched_cluster": [11],
                "source_region_id": 7,
                "text_translated": "그의 마음을\n알아채지 못했던\n걸까요…",
                "matched_cluster_details": [
                    {"bounding_rect": [260, 595, 114, 208], "lines": [{"bbox": [260, 595, 114, 208]}]},
                ],
            },
        ]
        flood_info = {
            8: {
                "region_id": 7,
                "anchor_ids": [8, 11],
                "bubble_rect": [208, 548, 448, 326],
                "safe_text_rect": [208, 548, 448, 326],
                "mask_centroid": [432, 710],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
            11: {
                "region_id": 7,
                "anchor_ids": [8, 11],
                "bubble_rect": [208, 548, 448, 326],
                "safe_text_rect": [208, 548, 448, 326],
                "mask_centroid": [432, 710],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
        }

        prepared = prepare_page_layouts(
            image,
            clusters,
            shared_groups={8: [8, 11], 11: [8, 11]},
            flood_info_by_anchor=flood_info,
        )

        by_anchor = {tuple(item[0]["matched_cluster"]): item for item in prepared}
        self.assertAlmostEqual(by_anchor[(8,)][4].inner_rect[0] + by_anchor[(8,)][4].inner_rect[2] / 2, 529.5, delta=12)
        self.assertAlmostEqual(by_anchor[(11,)][4].inner_rect[0] + by_anchor[(11,)][4].inner_rect[2] / 2, 317.0, delta=12)

    def test_small_shared_bubble_keeps_nearby_segments_merged(self) -> None:
        clusters = [
            {
                "matched_cluster": [2],
                "source_region_id": 3,
                "text_translated": "하지만",
                "matched_cluster_details": [
                    {"bounding_rect": [1028, 1502, 34, 73], "lines": [{"bbox": [1028, 1502, 34, 73]}]},
                ],
            },
            {
                "matched_cluster": [4],
                "source_region_id": 3,
                "text_translated": "해럴드 님\n이라면――",
                "matched_cluster_details": [
                    {"bounding_rect": [925, 1502, 83, 188], "lines": [{"bbox": [925, 1502, 83, 188]}]},
                ],
            },
        ]
        flood_info = {
            2: {
                "region_id": 3,
                "anchor_ids": [2, 4],
                "bubble_rect": [868, 1435, 252, 327],
                "safe_text_rect": [884, 1485, 225, 228],
                "mask_centroid": [997, 1599],
            },
            4: {
                "region_id": 3,
                "anchor_ids": [2, 4],
                "bubble_rect": [868, 1435, 252, 327],
                "safe_text_rect": [884, 1485, 225, 228],
                "mask_centroid": [997, 1599],
            },
        }

        merged = merge_unseparated_shared_segments(clusters, {2: [2, 4], 4: [2, 4]}, flood_info)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_cluster"], [2, 4])

    def test_small_shared_bubble_keeps_short_interjection_with_sentence(self) -> None:
        clusters = [
            {
                "matched_cluster": [1],
                "source_region_id": 1,
                "text_translated": "…라니",
                "matched_cluster_details": [
                    {"bounding_rect": [1076, 1540, 24, 56], "lines": [{"bbox": [1076, 1540, 24, 56]}]},
                ],
            },
            {
                "matched_cluster": [5],
                "source_region_id": 1,
                "text_translated": "뭐 하고\n있는 건가요?",
                "matched_cluster_details": [
                    {"bounding_rect": [979, 1492, 74, 205], "lines": [{"bbox": [979, 1492, 74, 205]}]},
                ],
            },
        ]
        flood_info = {
            1: {
                "region_id": 1,
                "anchor_ids": [1, 5],
                "bubble_rect": [939, 1441, 205, 295],
                "safe_text_rect": [953, 1471, 176, 233],
                "mask_centroid": [1041, 1587],
            },
            5: {
                "region_id": 1,
                "anchor_ids": [1, 5],
                "bubble_rect": [939, 1441, 205, 295],
                "safe_text_rect": [953, 1471, 176, 233],
                "mask_centroid": [1041, 1587],
            },
        }

        merged = merge_unseparated_shared_segments(clusters, {1: [1, 5], 5: [1, 5]}, flood_info)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_cluster"], [1, 5])

    def test_split_parent_font_flattening_caps_only_large_side(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = np.full((1920, 1350, 3), 255, dtype=np.uint8)
        clusters = [
            {
                "location": "bubble",
                "matched_cluster": [1],
                "source_region_id": 1,
                "text_translated": "플레이어들로부터",
                "matched_cluster_details": [
                    {"bounding_rect": [1132, 125, 127, 302], "lines": [{"bbox": [1132, 125, 127, 302]}]},
                ],
            },
            {
                "location": "bubble",
                "matched_cluster": [2],
                "source_region_id": 2,
                "text_translated": "『스토크스의\n양심』",
                "matched_cluster_details": [
                    {"bounding_rect": [1025, 271, 147, 322], "lines": [{"bbox": [1025, 271, 147, 322]}]},
                ],
            },
        ]
        flood_info = {
            1: {
                "region_id": 1,
                "anchor_ids": [1],
                "safe_text_rect": [1132, 125, 127, 302],
                "bubble_rect": [1000, 100, 280, 510],
                "split_parent_rect": [1000, 100, 280, 510],
                "mask_centroid": [1195, 276],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
            2: {
                "region_id": 2,
                "anchor_ids": [2],
                "safe_text_rect": [1025, 271, 147, 322],
                "bubble_rect": [1000, 100, 280, 510],
                "split_parent_rect": [1000, 100, 280, 510],
                "mask_centroid": [1098, 432],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
        }

        prepared = prepare_page_layouts(image, clusters, flood_info_by_anchor=flood_info)

        by_anchor = {tuple(item[0]["matched_cluster"]): item[4].font_size for item in prepared}
        small_font = by_anchor[(2,)]
        large_font = by_anchor[(1,)]
        self.assertGreater(large_font, small_font)
        self.assertLessEqual(large_font, split_parent_font_cap(small_font))

        adjusted = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            overlay_guidance={"regions": {"2": {"font_size_delta": -2}}},
        )
        adjusted_by_anchor = {tuple(item[0]["matched_cluster"]): item[4].font_size for item in adjusted}
        self.assertEqual(adjusted_by_anchor[(1,)], large_font)
        self.assertLess(adjusted_by_anchor[(2,)], small_font)

        direct_adjusted = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            overlay_guidance={"regions": {"2": {"font_size": small_font + 8}}},
        )
        direct_by_anchor = {tuple(item[0]["matched_cluster"]): item[4].font_size for item in direct_adjusted}
        self.assertEqual(direct_by_anchor[(1,)], large_font)
        self.assertEqual(direct_by_anchor[(2,)], small_font + 8)

    def test_manual_vertical_does_not_relayout_adjacent_split_sibling(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")

        image = np.full((1920, 1350, 3), 255, dtype=np.uint8)
        clusters = [
            {
                "location": "bubble",
                "matched_cluster": [1],
                "source_region_id": 1,
                "text_translated": "플레이어들로부터",
                "matched_cluster_details": [
                    {"bounding_rect": [1132, 125, 127, 302], "lines": [{"bbox": [1132, 125, 127, 302]}]},
                ],
            },
            {
                "location": "bubble",
                "matched_cluster": [2],
                "source_region_id": 2,
                "text_translated": "『스토크스의 양심』",
                "matched_cluster_details": [
                    {"bounding_rect": [1025, 271, 147, 322], "lines": [{"bbox": [1025, 271, 147, 322]}]},
                ],
            },
        ]
        flood_info = {
            1: {
                "region_id": 1,
                "anchor_ids": [1],
                "safe_text_rect": [1132, 125, 127, 302],
                "bubble_rect": [1000, 100, 280, 510],
                "split_parent_rect": [1000, 100, 280, 510],
                "mask_centroid": [1195, 276],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
            2: {
                "region_id": 2,
                "anchor_ids": [2],
                "safe_text_rect": [1025, 271, 147, 322],
                "bubble_rect": [1000, 100, 280, 510],
                "split_parent_rect": [1000, 100, 280, 510],
                "mask_centroid": [1098, 432],
                "is_valid": True,
                "region_type_guess": "bubble",
            },
        }

        base = prepare_page_layouts(image, clusters, flood_info_by_anchor=flood_info)
        adjusted = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            overlay_guidance={"regions": {"2": {"force_vertical": True}}},
        )

        base_first = next(item for item in base if item[0]["matched_cluster"] == [1])
        adjusted_first = next(item for item in adjusted if item[0]["matched_cluster"] == [1])
        self.assertEqual(adjusted_first[2], base_first[2])
        self.assertEqual(adjusted_first[4].font_size, base_first[4].font_size)
        self.assertEqual(adjusted_first[4].lines, base_first[4].lines)

    def test_short_unboxed_label_cluster_is_preserved(self) -> None:
        cluster = {
            "location": "unboxed",
            "matched_cluster_details": [
                {
                    "text": "よかった",
                    "bounding_rect": [283, 307, 35, 97],
                    "lines": [{"text": "よかった", "bbox": [283, 307, 35, 97]}],
                }
            ],
        }

        self.assertTrue(_is_short_unboxed_label_cluster(cluster))

    def test_short_unboxed_label_with_korean_translation_is_rendered(self) -> None:
        cluster = self._standalone_unboxed_cluster("LP農", [976, 497, 39, 69], [])
        cluster["text_translated"] = "LP\n농법"

        self.assertFalse(_should_preserve_original_unboxed_cluster(cluster))

    def test_short_unboxed_dialogue_like_text_is_not_preserved(self) -> None:
        cases = [
            ("00036_TG2", "なるほどな", [1086, 1469, 31, 184], ["dark_background"]),
            ("00036_TG5", "コレットが", [521, 1495, 34, 143], []),
            ("00065_TG1", "はつ", [720, 1542, 40, 91], []),
            ("00073_TG3", "その日まで", [265, 1110, 75, 395], []),
            ("00119_TG1", "はぁ~?", [1114, 178, 28, 145], []),
            ("00140_TG3", "さて･･･", [1131, 1202, 51, 167], ["dark_background", "dark_flood_candidate"]),
            ("00022_TG5", "ユノ…か", [469, 951, 31, 140], []),
        ]

        for name, source_text, rect, flags in cases:
            with self.subTest(name=name):
                self.assertFalse(_should_preserve_original_unboxed_cluster(
                    self._standalone_unboxed_cluster(source_text, rect, flags)
                ))

    def test_short_unboxed_effect_or_horizontal_regions_are_rendered(self) -> None:
        cases = [
            ("00026_TG7", "ボスンツ", [516, 1376, 307, 116], []),
            ("00069_TG10", "ずいっ", [607, 1012, 317, 239], []),
            ("00084_TG4", "ひょこ", [935, 86, 80, 111], []),
            ("00112_TG2", "リス", [955, 1246, 70, 93], []),
            ("00130_TG14", "ぴぺん", [183, 1568, 134, 198], []),
            ("00165_TG2", "フラグ死亡", [170, 4, 410, 1300], ["dark_background"]),
        ]

        for name, source_text, rect, flags in cases:
            with self.subTest(name=name):
                self.assertFalse(_should_preserve_original_unboxed_cluster(
                    self._standalone_unboxed_cluster(source_text, rect, flags)
                ))

    def test_symbol_english_bubble_title_is_preserved(self) -> None:
        cluster = {
            "location": "bubble",
            "matched_cluster": [3],
            "text": "『Brave Hearts』",
            "text_translated": "『Brave Hearts』",
        }

        self.assertTrue(_should_preserve_original_symbol_english_cluster(cluster))

    def test_symbol_english_preserve_requires_untranslated_text(self) -> None:
        cluster = {
            "location": "bubble",
            "matched_cluster": [2],
            "text": "TV Game",
            "text_translated": "TV 게임",
        }

        self.assertFalse(_should_preserve_original_symbol_english_cluster(cluster))

    def test_failed_dark_edge_rescue_bubble_is_preserved(self) -> None:
        cluster = {
            "location": "bubble",
            "source_method": "edge_barrier_rescue",
            "quality_flags": [
                "dark_background",
                "dark_flood_candidate",
                "vertical_dark_bg_allowed",
                "dark_flood_failed_validation",
                "edge_barrier_rescue",
            ],
        }

        self.assertTrue(_should_preserve_original_cluster(cluster))

    def test_overlay_guidance_disables_preserve_original_fallback(self) -> None:
        cluster = {
            "location": "bubble",
            "matched_cluster": [2],
            "source_region_id": 2,
            "text": "口答えするな",
            "text_translated": "말대꾸하지 마라",
            "preserve_original": True,
            "preserve_original_reason": "oversized_thin_sparse_single_efx",
        }
        overrides = {"2": {"safe_text_rect": [1096, 1490, 144, 211], "force_vertical": True}}
        override = overlay_override_for_cluster(cluster, {2: {"region_id": 2}}, overrides)

        self.assertTrue(_should_preserve_original_cluster(cluster))
        self.assertTrue(overlay_override_disables_automatic_fallback(override))

    def test_overlay_guidance_can_override_text_outline_width(self) -> None:
        self.assertEqual(overlay_override_stroke_width({"stroke_width": 0}, 2), 0)
        self.assertEqual(overlay_override_stroke_width({"stroke_width": 5}, 2), 5)
        self.assertEqual(overlay_override_stroke_width({"text_stroke_width": 20}, 2), 12)
        self.assertEqual(overlay_override_stroke_width({}, 2), 2)

    def test_unboxed_tg_bbox_fallback_font_softening_targets_only_fallback(self) -> None:
        self.assertTrue(should_soften_unboxed_tg_bbox_layout(
            {"location": "unboxed", "source_method": "bbox_fallback", "quality_flags": []},
            "unboxed_rect",
        ))
        self.assertTrue(should_soften_unboxed_tg_bbox_layout(
            {"location": "unboxed", "quality_flags": ["unboxed_consolidated_tg_cluster"]},
            "unboxed_rect",
        ))
        self.assertFalse(should_soften_unboxed_tg_bbox_layout(
            {"location": "bubble", "source_method": "bbox_fallback", "quality_flags": []},
            "safe_text_rect",
        ))
        self.assertFalse(should_soften_unboxed_tg_bbox_layout(
            {"location": "unboxed", "source_method": "dark_flood", "quality_flags": ["dark_background"]},
            "unboxed_rect",
        ))

    def test_unboxed_tg_bbox_font_cap_keeps_minimum(self) -> None:
        self.assertEqual(softened_unboxed_tg_bbox_font_cap(30), 27)
        self.assertEqual(softened_unboxed_tg_bbox_font_cap(16), 16)

    def test_split_parent_font_cap_allows_moderate_difference(self) -> None:
        self.assertEqual(split_parent_font_cap(31), 38)
        self.assertEqual(split_parent_font_cap(44), 53)

    def test_edge_split_tg_anchored_rect_pulls_tall_child_toward_tg(self) -> None:
        cluster = {
            "matched_cluster": [6],
            "matched_cluster_details": [
                {
                    "bounding_rect": [518, 1525, 30, 130],
                    "lines": [{"bbox": [518, 1525, 30, 130]}],
                }
            ],
        }
        flood_info = {
            "is_valid": True,
            "region_type_guess": "bubble",
            "anchor_ids": [6],
            "bubble_rect": [462, 1308, 126, 400],
            "split_parent_rect": [462, 1308, 291, 400],
            "quality_flags": ["edge_barrier_rescue"],
        }

        rect = edge_split_tg_anchored_rect(cluster, flood_info)

        self.assertIsNotNone(rect)
        self.assertGreater(rect[1], 1430)
        self.assertLess(rect[3], 400)

    def test_edge_split_tg_anchored_rect_ignores_non_tall_child(self) -> None:
        cluster = {
            "matched_cluster": [5],
            "matched_cluster_details": [
                {
                    "bounding_rect": [588, 1360, 150, 216],
                    "lines": [{"bbox": [588, 1360, 150, 216]}],
                }
            ],
        }
        flood_info = {
            "is_valid": True,
            "region_type_guess": "bubble",
            "anchor_ids": [5],
            "bubble_rect": [570, 1308, 183, 329],
            "split_parent_rect": [462, 1308, 291, 400],
            "quality_flags": ["edge_barrier_rescue"],
        }

        self.assertIsNone(edge_split_tg_anchored_rect(cluster, flood_info))

    def test_small_bubble_does_not_split_shared_segments_even_when_texts_are_far(self) -> None:
        flood_info = {
            5: {
                "region_id": 10,
                "anchor_ids": [5, 7],
                "bubble_rect": [697, 1191, 227, 313],
                "safe_text_rect": [711, 1235, 201, 229],
                "mask_centroid": [811, 1349],
                "quality_flags": ["raw_solid=0.995, raw_round=0.841, solid=0.995, round=0.852"],
            }
        }
        detail_rects = [
            [832, 1248, 29, 109],
            [762, 1246, 39, 112],
        ]

        self.assertFalse(shared_group_is_spatially_separated([5, 7], flood_info, detail_rects))

    def test_overlay_guidance_overrides_text_rect_and_style(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((220, 180, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "text_translated": "아니 이럴수가!",
            "matched_cluster_details": [{"bounding_rect": [60, 60, 40, 80], "lines": [{"bbox": [60, 60, 40, 80]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 7,
                "anchor_ids": [1],
                "bubble_rect": [30, 20, 120, 180],
                "safe_text_rect": [48, 45, 84, 130],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }
        overlay_guidance = {
            "regions": {
                "7": {
                    "safe_text_rect": [50, 40, 70, 100],
                    "text_override": "아니\n이럴수가!",
                    "font_style": "handwritten",
                }
            }
        }

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance=overlay_guidance,
        )

        self.assertEqual(prepared[0][1], "아니\n이럴수가!")
        self.assertEqual(prepared[0][2], [50, 40, 70, 100])
        self.assertEqual(prepared[0][4].lines, ["아니", "이럴수가!"])

    def test_text_override_renders_region_without_ocr_cluster(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((180, 160, 3), 255, dtype=np.uint8)
        overlay_guidance = {
            "regions": {
                "1": {
                    "safe_text_rect": [90, 30, 42, 70],
                    "text_override": "맞다",
                }
            }
        }
        region_info_by_id = {
            1: {
                "region_id": 1,
                "type": "bubble",
                "anchor_ids": [],
                "bubble_rect": [84, 24, 54, 86],
                "safe_text_rect": [90, 30, 42, 70],
            }
        }

        prepared = prepare_page_layouts(
            image,
            [],
            region_info_by_id=region_info_by_id,
            font_path=FONT_PATH,
            overlay_guidance=overlay_guidance,
        )

        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0][1], "맞다")
        self.assertEqual(prepared[0][2], [90, 30, 42, 70])

    def test_text_override_line_breaks_are_preserved_even_in_tall_rect(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((520, 220, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "text_translated": "자신의 키와 거의 차이 없는 검을 저렇게 가볍게…",
            "orientation": "vertical",
            "matched_cluster_details": [{"bounding_rect": [40, 30, 120, 440], "lines": [{"bbox": [40, 30, 120, 440]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 3,
                "anchor_ids": [1],
                "bubble_rect": [30, 20, 150, 470],
                "safe_text_rect": [45, 55, 110, 380],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }
        override_text = "자신의\n키와\n거의 차이\n없는 검을\n저렇게\n가볍게…"

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"3": {"text_override": override_text, "force_vertical": True}}},
        )

        self.assertEqual(prepared[0][4].lines, override_text.splitlines())

    def test_text_override_line_breaks_do_not_fallback_when_lines_are_tight(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((520, 180, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "text_translated": "자신의 키와 거의 차이 없는 검을 저렇게 가볍게…",
            "orientation": "vertical",
            "matched_cluster_details": [{"bounding_rect": [40, 30, 70, 440], "lines": [{"bbox": [40, 30, 70, 440]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 3,
                "anchor_ids": [1],
                "bubble_rect": [30, 20, 95, 470],
                "safe_text_rect": [45, 55, 52, 360],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }
        override_text = "자신의\n키와\n거의 차이\n없는 검을\n저렇게\n가볍게…"

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"3": {"text_override": override_text}}},
        )

        self.assertEqual(prepared[0][4].lines, override_text.splitlines())

    def test_text_override_line_breaks_survive_unboxed_softening_relayout(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((360, 220, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [],
            "source_region_id": 9,
            "location": "unboxed",
            "source_method": "bbox_fallback",
            "text_translated": "자동 줄바꿈 대상 문장입니다",
            "unboxed_rect": [50, 40, 80, 260],
            "bounding_rect": [50, 40, 80, 260],
            "matched_cluster_details": [{"bounding_rect": [50, 40, 80, 260], "lines": [{"bbox": [50, 40, 80, 260]}]}],
        }]
        override_text = "수동\n줄바꿈\n유지"

        prepared = prepare_page_layouts(
            image,
            clusters,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"9": {"text_override": override_text}}},
        )

        self.assertEqual(prepared[0][4].lines, override_text.splitlines())

    def test_manual_font_size_bypasses_unboxed_auto_softening(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((360, 260, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [],
            "source_region_id": 9,
            "location": "unboxed",
            "source_method": "bbox_fallback",
            "text_translated": "큰 글자",
            "unboxed_rect": [40, 40, 160, 160],
            "bounding_rect": [40, 40, 160, 160],
            "matched_cluster_details": [{"bounding_rect": [40, 40, 160, 160], "lines": [{"bbox": [40, 40, 160, 160]}]}],
        }]

        prepared = prepare_page_layouts(
            image,
            clusters,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"9": {"font_size": 64}}},
        )

        self.assertEqual(prepared[0][4].font_size, 64)
        self.assertGreater(prepared[0][4].font_size, softened_unboxed_tg_bbox_font_cap(64))

    def test_text_override_with_font_size_uses_direct_manual_layout(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((420, 220, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "source_region_id": 2,
            "location": "bubble",
            "text_translated": "자동 줄바꿈 대상",
            "matched_cluster_details": [{"bounding_rect": [60, 50, 90, 300], "lines": [{"bbox": [60, 50, 90, 300]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 2,
                "anchor_ids": [1],
                "safe_text_rect": [60, 50, 90, 300],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }
        override_text = "수\n동\n줄\n바\n꿈"

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"2": {"text_override": override_text, "font_size": 48}}},
        )

        self.assertEqual(prepared[0][4].lines, override_text.splitlines())
        self.assertEqual(prepared[0][4].font_size, 48)

    def test_text_override_with_ellipsis_keeps_compact_line_height(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((900, 520, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "source_region_id": 2,
            "location": "bubble",
            "text_translated": "자동 줄바꿈 대상 문장입니다",
            "matched_cluster_details": [{"bounding_rect": [100, 100, 260, 700], "lines": [{"bbox": [100, 100, 260, 700]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 2,
                "anchor_ids": [1],
                "safe_text_rect": [100, 100, 260, 700],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }
        override_text = "다정하게\n녹여줄지도\n모르는\n빛으로부터\n눈을\n돌리고…"

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"2": {"text_override": override_text}}},
        )
        layout = prepared[0][4]

        self.assertEqual(layout.lines, override_text.splitlines())
        self.assertLess(layout.line_height / layout.font_size, 1.25)

    def test_empty_text_override_suppresses_region_rendering(self) -> None:
        image = np.full((220, 180, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "text_translated": "지워질 텍스트",
            "matched_cluster_details": [{"bounding_rect": [60, 60, 40, 80], "lines": [{"bbox": [60, 60, 40, 80]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 7,
                "anchor_ids": [1],
                "bubble_rect": [30, 20, 120, 180],
                "safe_text_rect": [48, 45, 84, 130],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }
        overlay_guidance = {"regions": {"7": {"text_override": ""}}}

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance=overlay_guidance,
        )

        self.assertEqual(prepared, [])
        self.assertEqual(overlay_suppressed_region_ids(overlay_guidance), {7})

    def test_empty_segment_text_override_suppresses_parent_region_restore(self) -> None:
        overlay_guidance = {"regions": {"5:1": {"text_override": ""}, "5-2": {"text_override": ""}}}

        self.assertEqual(overlay_suppressed_region_ids(overlay_guidance), {5})

    def test_empty_text_override_prevents_untranslated_region_restore(self) -> None:
        from pipeline.render_text_overlay import restore_untranslated_regions

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            regions_json = tmp_path / "regions.json"
            regions_json.write_text(
                json.dumps({
                    "regions": [
                        {"region_id": 7, "anchor_ids": [1], "bubble_rect": [10, 10, 40, 40]},
                    ]
                }),
                encoding="utf-8",
            )
            image = np.zeros((80, 80, 3), dtype=np.uint8)
            original = np.full((80, 80, 3), 255, dtype=np.uint8)

            restored = restore_untranslated_regions(image, original, regions_json, [], {7})

            self.assertEqual(restored, 0)
            self.assertEqual(int(image.sum()), 0)

    def test_partial_anchor_overlap_prevents_untranslated_region_restore(self) -> None:
        from pipeline.render_text_overlay import restore_untranslated_regions

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            regions_json = tmp_path / "regions.json"
            regions_json.write_text(
                json.dumps({
                    "regions": [
                        {"region_id": 3, "anchor_ids": [3, 6], "bubble_rect": [10, 10, 40, 40]},
                    ]
                }),
                encoding="utf-8",
            )
            image = np.zeros((80, 80, 3), dtype=np.uint8)
            original = np.full((80, 80, 3), 255, dtype=np.uint8)
            clusters = [{"matched_cluster": [3], "text_translated": "LP\n농법"}]

            restored = restore_untranslated_regions(image, original, regions_json, clusters)

            self.assertEqual(restored, 0)
            self.assertEqual(int(image.sum()), 0)

    def test_overlay_guidance_etc_uses_region_font_path(self) -> None:
        custom_font = Path("Fonts/RIDIBatang.otf")
        if not Path(FONT_PATH).exists() or not custom_font.exists():
            self.skipTest("font not found")
        image = np.full((220, 180, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "text_translated": "직접 폰트",
            "matched_cluster_details": [{"bounding_rect": [60, 60, 40, 80], "lines": [{"bbox": [60, 60, 40, 80]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 7,
                "anchor_ids": [1],
                "bubble_rect": [30, 20, 120, 180],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"7": {"font_style": "etc", "font_path": str(custom_font)}}},
        )

        self.assertEqual(prepared[0][6], custom_font)

    def test_text_override_without_font_style_keeps_cluster_render_hint_font(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((220, 180, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "text_translated": "효과음",
            "render_hint": "impact",
            "matched_cluster_details": [{"bounding_rect": [60, 60, 40, 80], "lines": [{"bbox": [60, 60, 40, 80]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 7,
                "anchor_ids": [1],
                "bubble_rect": [30, 20, 120, 180],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }

        base = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
        )
        adjusted = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance={"regions": {"7": {"text_override": "｜\n｜"}}},
        )

        self.assertEqual(adjusted[0][1], "｜\n｜")
        self.assertEqual(adjusted[0][6], base[0][6])

    def test_overlay_guidance_can_force_vertical_layout(self) -> None:
        if not Path(FONT_PATH).exists():
            self.skipTest(f"font not found: {FONT_PATH}")
        image = np.full((240, 180, 3), 255, dtype=np.uint8)
        clusters = [{
            "matched_cluster": [1],
            "text_translated": "편지의 답장인가?",
            "matched_cluster_details": [{"bounding_rect": [70, 40, 40, 150], "lines": [{"bbox": [70, 40, 40, 150]}]}],
        }]
        flood_info = {
            1: {
                "region_id": 2,
                "anchor_ids": [1],
                "bubble_rect": [50, 20, 80, 200],
                "safe_text_rect": [70, 35, 40, 160],
                "is_valid": True,
                "region_type_guess": "bubble",
            }
        }
        overlay_guidance = {"regions": {"2": {"force_vertical": True}}}

        prepared = prepare_page_layouts(
            image,
            clusters,
            flood_info_by_anchor=flood_info,
            font_path=FONT_PATH,
            overlay_guidance=overlay_guidance,
        )

        self.assertGreater(len(prepared[0][4].lines), 3)

    @staticmethod
    def _standalone_unboxed_cluster(source_text: str, rect: list[int], flags: list[str]) -> dict:
        return {
            "location": "unboxed",
            "source_method": "bbox_fallback",
            "matched_cluster": [1],
            "quality_flags": flags,
            "matched_cluster_details": [
                {
                    "text": source_text,
                    "bounding_rect": rect,
                    "lines": [{"text": source_text, "bbox": rect}],
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
