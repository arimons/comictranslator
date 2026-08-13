# rect bubble 후보의 전장 side 침범을 검증하는 테스트
import unittest

import cv2
import numpy as np

from pipeline.bubble_detector import _rect_side_intrusion_reject_reason


class RectSideIntrusionRejectTest(unittest.TestCase):
    def test_clean_large_rect_is_not_rejected(self) -> None:
        mask = np.zeros((260, 260), dtype=np.uint8)
        cv2.rectangle(mask, (40, 30), (220, 230), 255, -1)

        reason = _rect_side_intrusion_reject_reason(
            mask,
            ["shape_class=rect", "large_bubble_tg_ratio"],
        )

        self.assertIsNone(reason)

    def test_full_height_side_intrusion_is_rejected(self) -> None:
        mask = np.zeros((280, 280), dtype=np.uint8)
        points = np.array([
            [40, 30],
            [220, 30],
            [235, 65],
            [180, 115],
            [240, 170],
            [140, 240],
            [40, 240],
        ], dtype=np.int32)
        cv2.fillPoly(mask, [points], 255)

        reason = _rect_side_intrusion_reject_reason(
            mask,
            ["shape_class=rect", "large_bubble_tg_ratio"],
        )

        self.assertIsNotNone(reason)
        self.assertTrue(str(reason).startswith("rect_side_intrusion_reject:right"))

    def test_closed_boundary_rect_like_intrusion_is_rejected(self) -> None:
        mask = np.zeros((320, 520), dtype=np.uint8)
        points = np.array([
            [40, 40],
            [430, 40],
            [470, 95],
            [320, 155],
            [470, 240],
            [270, 285],
            [40, 285],
        ], dtype=np.int32)
        cv2.fillPoly(mask, [points], 255)

        reason = _rect_side_intrusion_reject_reason(
            mask,
            ["shape_class=closed_boundary", "large_bubble_tg_ratio"],
        )

        self.assertIsNotNone(reason)
        self.assertTrue(str(reason).startswith("rect_side_intrusion_reject:right"))


if __name__ == "__main__":
    unittest.main()
