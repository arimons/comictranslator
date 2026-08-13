# dual rectangular 말풍선 보완 점수 기준을 검증하는 테스트
import unittest

from pipeline.bubble_detector import _dual_rect_validation_score, _is_edge_narrow_metadata_rect


class DualRectValidationScoreTest(unittest.TestCase):
    def test_edge_narrow_metadata_allows_medium_vertical_glyph_width(self) -> None:
        self.assertTrue(_is_edge_narrow_metadata_rect([50, 1299, 43, 603], 1350))
        self.assertFalse(_is_edge_narrow_metadata_rect([50, 1299, 46, 603], 1350))

    def test_stair_dual_rect_survives_low_convex_hull_solidity(self) -> None:
        ok, score, parts = _dual_rect_validation_score(
            raw_solid=0.721,
            raw_round=0.426,
            shape_score=0.650,
            boundary_sides=4,
            contour_probe={
                "status": "closed",
                "coverage": 1.0,
                "quadrants": 4,
                "max_gap": 0.0,
                "distance_cv": 0.17,
            },
            dual_rect_profile={
                "ok": True,
                "score": 0.947,
                "center_shift": 0.45,
                "neck_ratio": 1.90,
                "width_floor": 0.44,
                "balance": 1.0,
            },
            bubble_page_ratio=0.0231,
            touches_border=False,
        )

        self.assertTrue(ok)
        self.assertGreaterEqual(score, 0.72)
        self.assertEqual(parts["solid"], 1.0)

    def test_weak_contour_does_not_pass_from_profile_alone(self) -> None:
        ok, score, _ = _dual_rect_validation_score(
            raw_solid=0.721,
            raw_round=0.426,
            shape_score=0.650,
            boundary_sides=4,
            contour_probe={"status": "weak"},
            dual_rect_profile={"ok": True, "score": 0.947},
            bubble_page_ratio=0.0231,
            touches_border=False,
        )

        self.assertFalse(ok)
        self.assertLess(score, 0.72)

    def test_convex_regular_rect_is_not_dual_rect_without_step_profile(self) -> None:
        ok, _, parts = _dual_rect_validation_score(
            raw_solid=0.90,
            raw_round=0.50,
            shape_score=0.90,
            boundary_sides=4,
            contour_probe={"status": "closed"},
            dual_rect_profile={"ok": False, "score": 0.20},
            bubble_page_ratio=0.02,
            touches_border=False,
        )

        self.assertFalse(ok)
        self.assertEqual(parts["profile"], 0.0)


if __name__ == "__main__":
    unittest.main()
