# TextGroup repair 병합 가드를 검증하는 테스트
import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from pipeline.text_group_builder_v2 import (
    consolidate_clusters,
    filter_furigana_lines,
    pass1_create_lines,
    pass2_create_clusters,
    repair_line_fragments,
)


class TextGroupBuilderRepairTest(unittest.TestCase):
    def test_pass1_does_not_append_half_width_ruby_to_vertical_line(self) -> None:
        blocks = [
            {"text": "生活", "bbox": [250, 1020, 23, 46]},
            {"text": "も", "bbox": [250, 1070, 23, 20]},
            {"text": "儘", "bbox": [250, 1092, 23, 24]},
            {"text": "なり", "bbox": [250, 1117, 24, 44]},
            {"text": "さいていげん", "bbox": [243, 1163, 9, 72]},
        ]

        lines = pass1_create_lines(blocks, avg_width=31.0, y_tolerance=46.5)

        self.assertEqual(
            sorted((line["text"], line["bbox"]) for line in lines),
            [
                ("さいていげん", [243, 1163, 9, 72]),
                ("生活も儘なり", [250, 1020, 24, 141]),
            ],
        )

    def test_horizontal_kana_single_kanji_ruby_pair_is_filtered(self) -> None:
        lines = [
            {"text": "つま", "bbox": [339, 1622, 39, 26], "orientation": "horizontal"},
            {"text": "続", "bbox": [310, 1622, 24, 26], "orientation": "horizontal"},
            {"text": "現れるなんてな", "bbox": [238, 1120, 68, 224], "orientation": "vertical"},
        ]

        filtered = filter_furigana_lines(lines, avg_width=39.0, image_height=1680.0)

        self.assertEqual([line["text"] for line in filtered], ["現れるなんてな"])

    def test_narrow_fragment_with_large_y_gap_is_not_repaired_into_previous_line(self) -> None:
        lines = [
            {
                "text": "そうしてやる",
                "bbox": [441, 1407, 20, 166],
                "orientation": "vertical",
                "bboxes": [
                    {"text": "そう", "bbox": [441, 1407, 20, 51]},
                    {"text": "し", "bbox": [441, 1464, 20, 25]},
                    {"text": "て", "bbox": [441, 1494, 20, 23]},
                    {"text": "やる", "bbox": [441, 1521, 20, 52]},
                ],
            },
            {
                "text": "べっけん",
                "bbox": [438, 1631, 17, 53],
                "orientation": "vertical",
                "bboxes": [{"text": "べっけん", "bbox": [438, 1631, 17, 53]}],
            },
        ]

        repaired, diagnostics = repair_line_fragments(
            lines,
            x_tol=31.0 * 0.33,
            merge_gap=93.0,
            avg_width=31.0,
            debug=False,
        )

        self.assertEqual([line["text"] for line in repaired], ["そうしてやる", "べっけん"])
        self.assertEqual(diagnostics["rejected"]["narrow_large_y_gap"], 1)

    def test_close_same_width_fragments_are_still_repaired(self) -> None:
        lines = [
            {
                "text": "そう",
                "bbox": [441, 1407, 20, 51],
                "orientation": "vertical",
                "bboxes": [{"text": "そう", "bbox": [441, 1407, 20, 51]}],
            },
            {
                "text": "して",
                "bbox": [441, 1464, 20, 53],
                "orientation": "vertical",
                "bboxes": [{"text": "して", "bbox": [441, 1464, 20, 53]}],
            },
        ]

        repaired, diagnostics = repair_line_fragments(
            lines,
            x_tol=31.0 * 0.33,
            merge_gap=93.0,
            avg_width=31.0,
            debug=False,
        )

        self.assertEqual([line["text"] for line in repaired], ["そうして"])
        self.assertEqual(diagnostics["success"], 1)

    def test_large_blank_gap_is_not_repaired_even_when_widths_match(self) -> None:
        lines = [
            {
                "text": "だわ",
                "bbox": [292, 431, 22, 54],
                "orientation": "vertical",
                "bboxes": [
                    {"text": "だ", "bbox": [292, 431, 22, 23]},
                    {"text": "わ", "bbox": [292, 461, 22, 24]},
                ],
            },
            {
                "text": "しっ",
                "bbox": [286, 616, 22, 50],
                "orientation": "vertical",
                "bboxes": [{"text": "しっ", "bbox": [286, 616, 22, 50]}],
            },
        ]

        repaired, diagnostics = repair_line_fragments(
            lines,
            x_tol=24.0 * 0.33,
            merge_gap=135.0,
            avg_width=24.0,
            debug=False,
        )

        self.assertEqual([line["text"] for line in repaired], ["だわ", "しっ"])
        self.assertEqual(diagnostics["rejected"]["large_blank_gap"], 1)

    def test_one_char_missing_like_gap_is_not_repaired(self) -> None:
        lines = [
            {
                "text": "ね",
                "bbox": [302, 260, 16, 20],
                "orientation": "vertical",
                "bboxes": [{"text": "ね", "bbox": [302, 260, 16, 20]}],
            },
            {
                "text": "ウチのライナー",
                "bbox": [299, 318, 17, 116],
                "orientation": "vertical",
                "bboxes": [{"text": "ウチのライナー", "bbox": [299, 318, 17, 116]}],
            },
        ]

        repaired, diagnostics = repair_line_fragments(
            lines,
            x_tol=16.0 * 0.33,
            merge_gap=52.5,
            avg_width=16.0,
            debug=False,
        )

        self.assertEqual([line["text"] for line in repaired], ["ね", "ウチのライナー"])
        self.assertEqual(diagnostics["rejected"]["large_blank_gap"], 1)

    def test_pass2_horizontal_branch_has_defined_gap_threshold(self) -> None:
        lines = [
            {"text": "Hello", "bbox": [100, 200, 80, 22], "orientation": "horizontal"},
            {"text": "world", "bbox": [190, 202, 80, 22], "orientation": "horizontal"},
        ]

        clusters = pass2_create_clusters(lines, avg_width=60.0, y_tolerance=8.0)

        self.assertEqual([sorted(line["text"] for line in cluster) for cluster in clusters], [["Hello", "world"]])

    def test_consolidate_rejects_ambiguous_gap_then_merges_clear_neighbor(self) -> None:
        clusters = [
            [{"text": "そうですか", "bbox": [1056, 1464, 22, 146], "orientation": "vertical"}],
            [{"text": "そこまで", "bbox": [1011, 1541, 22, 105], "orientation": "vertical"}],
            [{"text": "言われるのなら", "bbox": [969, 1541, 24, 186], "orientation": "vertical"}],
        ]

        merged = consolidate_clusters(clusters, avg_width=29.0, y_tolerance=28.8, debug=False)

        self.assertEqual(len(merged), 2)
        self.assertEqual([line["text"] for line in merged[0]], ["そうですか"])
        self.assertEqual([line["text"] for line in merged[1]], ["そこまで", "言われるのなら"])


if __name__ == "__main__":
    unittest.main()
