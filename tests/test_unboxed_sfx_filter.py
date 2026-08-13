# 짧고 큰 kana 효과음 필터를 검증하는 테스트
import unittest
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from pipeline.bubble_detector import (
    _is_large_short_kana_sfx_like,
    _should_demote_lone_edge_split_rescue,
)
from pipeline.bubble_text_filters import is_forbidden_text
from pipeline.run_masked_vis import (
    _cluster_adjacent_tgs,
)
from pipeline.text_group_builder_v2 import is_forbidden_cv_text
from pipeline.unboxed_grouping import (
    is_large_single_sfx_like as _is_large_single_sfx_like,
    is_short_handwritten_label_like as _is_short_handwritten_label_like,
)


class UnboxedSfxFilterTest(unittest.TestCase):
    def test_raw_net_variants_are_forbidden_cv_text(self) -> None:
        self.assertTrue(is_forbidden_cv_text("Raw.Net"))
        self.assertTrue(is_forbidden_cv_text("DL-Raw.Net"))
        self.assertTrue(is_forbidden_text("NetRaDL"))
        self.assertFalse(is_forbidden_cv_text("まかせてよ"))

    def test_repeated_large_kana_sfx_is_filtered(self) -> None:
        group = {
            "text": "ぶんぶん",
            "orientation": "vertical",
            "tight_rect": [672, 802, 145, 195],
            "lines": [{"text": "ぶん"}, {"text": "ぶん"}],
        }

        self.assertTrue(_is_large_short_kana_sfx_like(group, group["tight_rect"]))

    def test_large_two_glyph_kana_sfx_is_filtered(self) -> None:
        group = {
            "text": "はが",
            "orientation": "vertical",
            "tight_rect": [124, 1149, 150, 252],
            "lines": [{"text": "は"}, {"text": "が"}],
        }

        self.assertTrue(_is_large_short_kana_sfx_like(group, group["tight_rect"]))

    def test_normal_vertical_dialogue_is_not_filtered(self) -> None:
        group = {
            "text": "いやいや",
            "orientation": "vertical",
            "tight_rect": [915, 820, 73, 178],
            "lines": [{"text": "いやいや"}],
        }

        self.assertFalse(_is_large_single_sfx_like(group))
        self.assertFalse(_is_large_short_kana_sfx_like(group, group["tight_rect"]))

    def test_small_short_unboxed_labels_are_filtered(self) -> None:
        for group in [
            {"text": "指南", "tight_rect": [349, 153, 53, 62], "lines": [{"text": "指南"}]},
            {"text": "よかった", "tight_rect": [283, 307, 35, 97], "lines": [{"text": "よかった"}]},
        ]:
            self.assertTrue(_is_short_handwritten_label_like(group))

    def test_larger_short_text_is_not_label_filtered(self) -> None:
        group = {
            "text": "いやいや",
            "tight_rect": [915, 820, 73, 178],
            "lines": [{"text": "いやいや"}],
        }

        self.assertFalse(_is_short_handwritten_label_like(group))

    def test_diagonal_square_unboxed_candidates_are_not_consolidated(self) -> None:
        tg3 = {
            "text": "こんの悪たれ口め:",
            "tight_rect": [1003, 1157, 137, 149],
            "lines": [{"text": "こんの悪たれ口め:", "bbox": [1003, 1157, 137, 149]}],
        }
        tg6 = {
            "text": "せっかくのノーマンさんの気遣いを切って捨てやがって…っ",
            "tight_rect": [824, 1228, 146, 267],
            "lines": [{"text": "せっかくのノーマンさんの気遣いを切って捨てやがって…っ", "bbox": [824, 1228, 146, 267]}],
        }

        clusters = _cluster_adjacent_tgs([(3, tg3), (6, tg6)])

        self.assertEqual([[tid for tid, _ in cluster] for cluster in clusters], [[3], [6]])

    def test_aligned_vertical_columns_are_still_consolidated(self) -> None:
        right_col = {
            "text": "いえいえ",
            "tight_rect": [1128, 179, 41, 249],
            "lines": [{"text": "いえいえ", "bbox": [1128, 179, 41, 249]}],
        }
        left_col = {
            "text": "大したこと",
            "tight_rect": [1046, 170, 51, 257],
            "lines": [{"text": "大したこと", "bbox": [1046, 170, 51, 257]}],
        }

        clusters = _cluster_adjacent_tgs([(1, right_col), (2, left_col)])

        self.assertEqual([[tid for tid, _ in cluster] for cluster in clusters], [[1, 2]])

    def test_small_page_average_does_not_split_wide_overlapping_unboxed_pair(self) -> None:
        narrow_unrelated = {
            "text": "なるほどな",
            "tight_rect": [744, 1650, 31, 236],
            "lines": [{"text": "なるほどな", "bbox": [744, 1650, 31, 236]}],
        }
        right_col = {
            "text": "コレットが",
            "tight_rect": [521, 1495, 34, 143],
            "lines": [{"text": "コレットが", "bbox": [521, 1495, 34, 143]}],
        }
        left_group = {
            "text": "身寄りがなかったのはそういう理由だったのか",
            "tight_rect": [329, 1469, 124, 333],
            "lines": [
                {"text": "身寄りがなかったのは", "bbox": [420, 1472, 33, 330]},
                {"text": "そういう理由", "bbox": [367, 1469, 37, 203]},
                {"text": "だったのか", "bbox": [329, 1469, 35, 168]},
            ],
        }

        clusters = _cluster_adjacent_tgs([(2, narrow_unrelated), (5, right_col), (7, left_group)])

        self.assertEqual([[tid for tid, _ in cluster] for cluster in clusters], [[2], [5, 7]])

    def test_lone_edge_split_rescue_is_demoted_when_siblings_are_thin_unboxed_like(self) -> None:
        self.assertTrue(
            _should_demote_lone_edge_split_rescue(
                [2],
                {
                    4: "edge_reject_all_guard_thin_unboxed_like",
                    5: "edge_reject_all_guard_thin_unboxed_like",
                    6: "edge_reject_all_guard_thin_unboxed_like",
                },
            )
        )

    def test_edge_split_rescue_consensus_does_not_demote_pairs_or_mixed_rejects(self) -> None:
        self.assertFalse(
            _should_demote_lone_edge_split_rescue(
                [2, 4],
                {5: "edge_reject_all_guard_thin_unboxed_like"},
            )
        )
        self.assertFalse(
            _should_demote_lone_edge_split_rescue(
                [2],
                {
                    4: "edge_reject_all_guard_thin_unboxed_like",
                    5: "edge_reject_split_too_large_for_anchor",
                },
            )
        )


if __name__ == "__main__":
    unittest.main()
