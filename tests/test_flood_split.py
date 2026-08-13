# Flood split 후보의 defect depth 기준을 검증합니다.
import unittest
from unittest.mock import patch

import numpy as np

from pipeline.flood_split import (
    _adaptive_min_split_area,
    _extract_defect_points,
    _search_best_recursive_split,
    _split_binary_by_geometry_components,
    split_mask_by_inflection_line,
)


class FloodSplitDepthTest(unittest.TestCase):
    def test_adaptive_geometry_split_area_scales_with_image_size(self) -> None:
        self.assertEqual(_adaptive_min_split_area(960 * 1080), 900)
        self.assertEqual(_adaptive_min_split_area(1350 * 1920), 1036)
        self.assertEqual(_adaptive_min_split_area(4000 * 4000), 2500)

    def test_geometry_split_rejects_line_close_to_bubble_side_length(self) -> None:
        binary = np.zeros((140, 140), dtype=np.uint8)
        binary[10:111, 10:111] = 255

        result = _split_binary_by_geometry_components(
            binary,
            (10, 60),
            (110, 60),
            anchor_ids=[],
            anchor_centers=[],
            min_sub_area=900,
        )

        self.assertIsNone(result)

    def test_anchor_split_failure_can_fall_back_to_geometry_split(self) -> None:
        binary = np.zeros((140, 140), dtype=np.uint8)
        binary[10:111, 10:46] = 255
        binary[10:111, 75:111] = 255
        binary[50:71, 45:76] = 255

        selected = {
            "p1": [60, 45],
            "p2": [60, 75],
            "score_raw": 1.0,
            "score": 1.0,
        }

        with (
            patch("pipeline.flood_split._search_best_recursive_split", return_value=None),
            patch("pipeline.flood_split.collect_inflection_candidates", return_value={"selected": None, "candidates": []}),
            patch("pipeline.flood_split.collect_geometry_split_candidates", return_value={"selected": selected, "candidates": [selected]}),
        ):
            result = split_mask_by_inflection_line(
                binary,
                anchor_ids=[1, 2],
                anchor_centers=[(20, 30), (20, 85)],
                anchor_rects=[[18, 28, 4, 4], [18, 83, 4, 4]],
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(result), 2)
        self.assertEqual(sorted(tuple(child["anchor_ids"]) for child in result), [(), (1, 2)])
        self.assertTrue(all(child.get("split_source") == "geometry_fallback" for child in result))

    def test_two_anchor_split_allows_shallow_strong_turn_defects(self) -> None:
        contour = np.zeros((4, 1, 2), dtype=np.int32)
        approx = np.array(
            [
                [[10, 10]],
                [[20, 20]],
                [[30, 30]],
                [[40, 40]],
            ],
            dtype=np.int32,
        )
        defects = np.array(
            [
                [[0, 1, 1, int(16.0 * 256)]],
                [[1, 2, 2, int(10.5 * 256)]],
            ],
            dtype=np.int32,
        )

        with (
            patch("pipeline.flood_split.cv2.approxPolyDP", return_value=approx),
            patch("pipeline.flood_split.cv2.convexHull", return_value=np.array([[0], [1], [2]], dtype=np.int32)),
            patch("pipeline.flood_split.cv2.convexityDefects", return_value=defects),
            patch("pipeline.flood_split._contour_turn_angle", return_value=45.0),
        ):
            _approx, defect_points = _extract_defect_points(
                contour,
                anchor_count=2,
                min_defect_depth=15.0,
            )

        self.assertEqual(
            [(round(depth, 1), point) for _idx, depth, point, _turn in defect_points],
            [(16.0, (20, 20)), (10.5, (30, 30))],
        )

    def test_depth_filter_only_removes_tiny_defects(self) -> None:
        contour = np.zeros((4, 1, 2), dtype=np.int32)
        approx = np.array(
            [
                [[10, 10]],
                [[20, 20]],
                [[30, 30]],
                [[40, 40]],
            ],
            dtype=np.int32,
        )
        defects = np.array(
            [
                [[0, 1, 1, int(6.0 * 256)]],
                [[1, 2, 2, int(4.5 * 256)]],
            ],
            dtype=np.int32,
        )

        with (
            patch("pipeline.flood_split.cv2.approxPolyDP", return_value=approx),
            patch("pipeline.flood_split.cv2.convexHull", return_value=np.array([[0], [1], [2]], dtype=np.int32)),
            patch("pipeline.flood_split.cv2.convexityDefects", return_value=defects),
            patch("pipeline.flood_split._contour_turn_angle", return_value=45.0),
        ):
            _approx, defect_points = _extract_defect_points(
                contour,
                anchor_count=2,
                min_defect_depth=15.0,
            )

        self.assertEqual(
            [(round(depth, 1), point) for _idx, depth, point, _turn in defect_points],
            [(6.0, (20, 20))],
        )

    def test_three_anchor_split_still_allows_shallow_connector_defects(self) -> None:
        contour = np.zeros((4, 1, 2), dtype=np.int32)
        approx = np.array(
            [
                [[10, 10]],
                [[20, 20]],
                [[30, 30]],
                [[40, 40]],
            ],
            dtype=np.int32,
        )
        defects = np.array(
            [
                [[0, 1, 1, int(16.0 * 256)]],
                [[1, 2, 2, int(10.5 * 256)]],
            ],
            dtype=np.int32,
        )

        with (
            patch("pipeline.flood_split.cv2.approxPolyDP", return_value=approx),
            patch("pipeline.flood_split.cv2.convexHull", return_value=np.array([[0], [1], [2]], dtype=np.int32)),
            patch("pipeline.flood_split.cv2.convexityDefects", return_value=defects),
            patch("pipeline.flood_split._contour_turn_angle", return_value=45.0),
        ):
            _approx, defect_points = _extract_defect_points(
                contour,
                anchor_count=3,
                min_defect_depth=15.0,
            )

        self.assertEqual(
            [(round(depth, 1), point) for _idx, depth, point, _turn in defect_points],
            [(16.0, (20, 20)), (10.5, (30, 30))],
        )

    def test_low_turn_defects_are_rejected(self) -> None:
        contour = np.zeros((4, 1, 2), dtype=np.int32)
        approx = np.array(
            [
                [[10, 10]],
                [[20, 20]],
                [[30, 30]],
                [[40, 40]],
            ],
            dtype=np.int32,
        )
        defects = np.array(
            [
                [[0, 1, 1, int(30.0 * 256)]],
                [[1, 2, 2, int(25.0 * 256)]],
            ],
            dtype=np.int32,
        )

        with (
            patch("pipeline.flood_split.cv2.approxPolyDP", return_value=approx),
            patch("pipeline.flood_split.cv2.convexHull", return_value=np.array([[0], [1], [2]], dtype=np.int32)),
            patch("pipeline.flood_split.cv2.convexityDefects", return_value=defects),
            patch("pipeline.flood_split._contour_turn_angle", return_value=20.0),
        ):
            _approx, defect_points = _extract_defect_points(
                contour,
                anchor_count=2,
                min_defect_depth=15.0,
            )

        self.assertEqual(defect_points, [])

    def test_single_anchor_split_keeps_weak_turn_defects(self) -> None:
        contour = np.zeros((4, 1, 2), dtype=np.int32)
        approx = np.array(
            [
                [[10, 10]],
                [[20, 20]],
                [[30, 30]],
                [[40, 40]],
            ],
            dtype=np.int32,
        )
        defects = np.array(
            [
                [[0, 1, 1, int(30.0 * 256)]],
            ],
            dtype=np.int32,
        )

        with (
            patch("pipeline.flood_split.cv2.approxPolyDP", return_value=approx),
            patch("pipeline.flood_split.cv2.convexHull", return_value=np.array([[0], [1], [2]], dtype=np.int32)),
            patch("pipeline.flood_split.cv2.convexityDefects", return_value=defects),
            patch("pipeline.flood_split._contour_turn_angle", return_value=32.0),
        ):
            _approx, defect_points = _extract_defect_points(
                contour,
                anchor_count=1,
                min_defect_depth=15.0,
            )

        self.assertEqual(
            [(round(depth, 1), point, round(turn, 1)) for _idx, depth, point, turn in defect_points],
            [(30.0, (20, 20), 32.0)],
        )

    def test_two_anchor_split_rejects_weak_turn_defects(self) -> None:
        contour = np.zeros((4, 1, 2), dtype=np.int32)
        approx = np.array(
            [
                [[10, 10]],
                [[20, 20]],
                [[30, 30]],
                [[40, 40]],
            ],
            dtype=np.int32,
        )
        defects = np.array(
            [
                [[0, 1, 1, int(30.0 * 256)]],
            ],
            dtype=np.int32,
        )

        with (
            patch("pipeline.flood_split.cv2.approxPolyDP", return_value=approx),
            patch("pipeline.flood_split.cv2.convexHull", return_value=np.array([[0], [1], [2]], dtype=np.int32)),
            patch("pipeline.flood_split.cv2.convexityDefects", return_value=defects),
            patch("pipeline.flood_split._contour_turn_angle", return_value=32.0),
        ):
            _approx, defect_points = _extract_defect_points(
                contour,
                anchor_count=2,
                min_defect_depth=15.0,
            )

        self.assertEqual(defect_points, [])

    def test_recursive_search_can_choose_lower_rank_parent_with_better_tree(self) -> None:
        binary = np.ones((100, 100), dtype=np.uint8) * 255
        anchor_ids = [1, 2, 3]
        anchor_centers = [(20, 20), (20, 80), (80, 50)]
        anchor_rects = [[18, 18, 4, 4], [18, 78, 4, 4], [78, 48, 4, 4]]

        def candidate(
            p1,
            p2,
            score_raw,
            pos_indices,
            neg_indices,
        ):
            return {
                "p1": list(p1),
                "p2": list(p2),
                "score_raw": score_raw,
                "score": round(score_raw, 3),
                "separates": True,
                "centers_inside": True,
                "bboxes_clear": True,
                "area_balance": 0.5,
                "anchor_coverage": 1.0,
                "min_area_ratio": 0.3,
                "max_cut_ratio": 0.0,
                "partition_preferred": False,
                "pos_indices": pos_indices,
                "neg_indices": neg_indices,
            }

        def fake_collect(_mask, centers, *_args, **_kwargs):
            if len(centers) == 3:
                return {
                    "candidates": [
                        candidate((50, 0), (50, 99), 0.0, [0, 1], [2]),
                        candidate((0, 50), (99, 50), 5.0, [1], [0, 2]),
                    ],
                    "selected": None,
                    "defect_points": [],
                }
            if len(centers) == 2 and centers == [(20, 20), (20, 80)]:
                return {
                    "candidates": [
                        candidate((0, 50), (49, 50), 0.0, [1], [0]),
                    ],
                    "selected": None,
                    "defect_points": [],
                }
            return {"candidates": [], "selected": None, "defect_points": []}

        with patch("pipeline.flood_split.collect_inflection_candidates", side_effect=fake_collect):
            result = _search_best_recursive_split(
                binary,
                anchor_ids,
                anchor_centers,
                min_defect_depth=15.0,
                anchor_rects=anchor_rects,
                depth=0,
            )

        self.assertIsNotNone(result)
        _score, children = result
        self.assertEqual(
            sorted(tuple(child["anchor_ids"]) for child in children),
            [(1,), (2,), (3,)],
        )


if __name__ == "__main__":
    unittest.main()
