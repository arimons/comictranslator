# unboxed 텍스트 마스크의 오른쪽 후리가나 보조 검출을 검증합니다.
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pipeline.unboxed_text_detector import _build_furigana_side_mask, _classify_inpaint_background, _clip_roi_to_panel


def test_vertical_furigana_side_mask_adds_small_right_side_components():
    roi = np.full((140, 90, 3), 255, dtype=np.uint8)
    group = {
        "text_group_id": 1,
        "orientation": "vertical",
        "tight_rect": [30, 20, 20, 100],
        "lines": [
            {
                "bbox": [30, 20, 20, 100],
                "fragments": [{"bbox": [30, 20, 20, 100], "text": "本文"}],
            }
        ],
    }
    cv2.rectangle(roi, (34, 25), (45, 115), (0, 0, 0), -1)
    cv2.line(roi, (58, 34), (63, 42), (0, 0, 0), 1)
    cv2.line(roi, (63, 34), (58, 42), (0, 0, 0), 1)
    cv2.line(roi, (58, 58), (64, 67), (0, 0, 0), 1)
    cv2.line(roi, (64, 58), (58, 67), (0, 0, 0), 1)
    text_mask = np.zeros((140, 90), dtype=np.uint8)
    text_mask[25:116, 34:46] = 255

    mask, meta = _build_furigana_side_mask(
        roi,
        [group],
        [0, 0, 90, 140],
        text_mask,
        dark_threshold=214,
    )

    assert meta["furigana_pass"] == "right_side_band"
    assert meta["furigana_components"] == 2
    assert meta["furigana_added_pixels"] > 0
    assert mask[38, 60] == 255
    assert mask[62, 61] == 255


def test_furigana_side_mask_skips_horizontal_groups():
    roi = np.full((80, 160, 3), 255, dtype=np.uint8)
    group = {
        "text_group_id": 1,
        "orientation": "horizontal",
        "tight_rect": [20, 30, 110, 20],
        "lines": [
            {
                "bbox": [20, 30, 110, 20],
                "fragments": [{"bbox": [20, 30, 110, 20], "text": "horizontal"}],
            }
        ],
    }
    cv2.rectangle(roi, (134, 32), (140, 40), (0, 0, 0), -1)
    text_mask = np.zeros((80, 160), dtype=np.uint8)

    mask, meta = _build_furigana_side_mask(
        roi,
        [group],
        [0, 0, 160, 80],
        text_mask,
        dark_threshold=214,
    )

    assert meta["furigana_pass"] == "skipped_non_vertical"
    assert np.count_nonzero(mask) == 0


def test_inpaint_background_classifier_uses_telea_for_flat_white():
    roi = np.full((120, 80, 3), 255, dtype=np.uint8)
    mask = np.zeros((120, 80), dtype=np.uint8)
    mask[20:100, 30:48] = 255

    meta = _classify_inpaint_background(roi, mask)

    assert meta["background_class"] == "white_flat"
    assert meta["recommended_inpaint_engine"] == "telea"


def test_inpaint_background_classifier_uses_lama_for_light_screentone():
    roi = np.full((160, 120, 3), 255, dtype=np.uint8)
    for y in range(0, 160, 5):
        for x in range(0, 120, 5):
            roi[y:y + 2, x:x + 2] = 170
    mask = np.zeros((160, 120), dtype=np.uint8)
    mask[30:130, 45:65] = 255

    meta = _classify_inpaint_background(roi, mask)

    assert meta["background_class"] == "screentone_light"
    assert meta["recommended_inpaint_engine"] == "lama"


def test_panel_clip_preserves_right_furigana_context_for_vertical_text():
    roi = [830, 450, 180, 320]
    tg_rect = [890, 492, 52, 237]
    boundaries = {"top": None, "bottom": None, "left": None, "right": 944}

    clipped = _clip_roi_to_panel(roi, boundaries, tg_rect=tg_rect)

    assert clipped[0] == 830
    assert clipped[2] >= 148
