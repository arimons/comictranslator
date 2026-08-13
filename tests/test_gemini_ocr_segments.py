# Gemini OCR segment 응답을 integrated cluster로 변환하는 규칙을 검증합니다.
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from pipeline.gemini_ocr_translate_v5 import (
    PACKED_OCR_PROMPT,
    _build_integrated_from_sheet_regions,
    validate_gemini_ocr_regions,
)


class GeminiOcrSegmentsTest(unittest.TestCase):
    def test_packed_ocr_prompt_keeps_render_hint_as_raw_hint(self) -> None:
        self.assertIn("render_hint", PACKED_OCR_PROMPT)
        self.assertIn("1차 hint_raw", PACKED_OCR_PROMPT)
        self.assertNotIn("STYLE GUIDE", PACKED_OCR_PROMPT)
        self.assertNotIn("reference panel", PACKED_OCR_PROMPT)

    def test_segments_create_separate_clusters_with_tg_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "regions.json"
            cv_json = tmp_path / "text_groups.json"
            integrated_json = tmp_path / "integrated.json"

            sheet_json.write_text(json.dumps({
                "regions": [
                    {
                        "region_id": 7,
                        "anchor_ids": [5, 14],
                        "type": "bubble",
                        "bubble_rect": [204, 725, 552, 421],
                        "safe_text_rect": [204, 736, 552, 327],
                    }
                ]
            }), encoding="utf-8")
            cv_json.write_text(json.dumps([
                {"cluster_id": 5, "text": "ハロルド", "bounding_rect": [600, 794, 103, 149], "lines": []},
                {"cluster_id": 14, "text": "原作に沿いつつ", "bounding_rect": [256, 865, 163, 207], "lines": []},
            ]), encoding="utf-8")

            result = _build_integrated_from_sheet_regions(
                [
                    {
                        "region_id": 7,
                        "segments": [
                            {
                                "matched_tg_ids": [5],
                                "ocr_text": "ハロルド",
                                "text_translated": "해럴드",
                                "orientation": "vertical",
                                "text_scale_class": "normal",
                                "render_hint": "normal",
                            },
                            {
                                "matched_tg_ids": [14],
                                "ocr_text": "原作に沿いつつ",
                                "text_translated": "원작을 따르면서",
                                "orientation": "vertical",
                                "text_scale_class": "normal",
                                "render_hint": "normal",
                            },
                        ],
                    }
                ],
                str(sheet_json),
                str(cv_json),
                str(integrated_json),
            )

            clusters = result["clusters"]
            self.assertEqual(len(clusters), 2)
            self.assertEqual(clusters[0]["matched_cluster"], [5])
            self.assertEqual(clusters[1]["matched_cluster"], [14])
            self.assertEqual(clusters[0]["source_region_id"], 7)
            self.assertEqual(clusters[0]["source_segment_index"], 1)
            self.assertEqual(clusters[1]["source_segment_index"], 2)
            self.assertEqual(result["unmatched_clusters"], [])

    def test_anchorless_manual_seed_region_still_creates_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "regions.json"
            cv_json = tmp_path / "text_groups.json"
            integrated_json = tmp_path / "integrated.json"

            sheet_json.write_text(json.dumps({
                "regions": [
                    {
                        "region_id": 6,
                        "anchor_ids": [],
                        "type": "bubble",
                        "bubble_rect": [10, 20, 100, 120],
                    }
                ]
            }), encoding="utf-8")
            cv_json.write_text("[]", encoding="utf-8")

            result = _build_integrated_from_sheet_regions(
                [
                    {
                        "region_id": 6,
                        "ocr_text": "おい",
                        "text_translated": "야",
                        "orientation": "vertical",
                        "text_scale_class": "normal",
                        "render_hint": "normal",
                    }
                ],
                str(sheet_json),
                str(cv_json),
                str(integrated_json),
            )

            self.assertEqual(len(result["clusters"]), 1)
            cluster = result["clusters"][0]
            self.assertEqual(cluster["source_region_id"], 6)
            self.assertEqual(cluster["matched_cluster"], [])
            self.assertEqual(cluster["union_bbox"], [10, 20, 100, 120])
            self.assertEqual(cluster["text_translated"], "야")

    def test_legacy_efx_render_hint_becomes_serif_emphasis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "regions.json"
            cv_json = tmp_path / "text_groups.json"
            integrated_json = tmp_path / "integrated.json"

            sheet_json.write_text(json.dumps({
                "regions": [
                    {
                        "region_id": 9,
                        "anchor_ids": [4],
                        "type": "bubble",
                        "bubble_rect": [30, 40, 120, 160],
                    }
                ]
            }), encoding="utf-8")
            cv_json.write_text(json.dumps([
                {"cluster_id": 4, "text": "あっ", "bounding_rect": [50, 60, 40, 90], "lines": []},
            ]), encoding="utf-8")

            result = _build_integrated_from_sheet_regions(
                [
                    {
                        "region_id": 9,
                        "segments": [
                            {
                                "matched_tg_ids": [4],
                                "ocr_text": "あっ",
                                "text_translated": "앗",
                                "orientation": "vertical",
                                "text_scale_class": "normal",
                                "render_hint": "efx",
                            },
                        ],
                    }
                ],
                str(sheet_json),
                str(cv_json),
                str(integrated_json),
            )

            cluster = result["clusters"][0]
            self.assertEqual(cluster["render_hint_raw"], "serif_emphasis")
            self.assertEqual(cluster["render_hint_final"], "serif_emphasis")
            self.assertEqual(cluster["render_hint"], "serif_emphasis")

    def test_fullwidth_vertical_bar_is_normalized_for_render_font(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "regions.json"
            cv_json = tmp_path / "text_groups.json"
            integrated_json = tmp_path / "integrated.json"

            sheet_json.write_text(json.dumps({
                "regions": [
                    {
                        "region_id": 3,
                        "anchor_ids": [1],
                        "type": "bubble",
                        "bubble_rect": [10, 20, 100, 120],
                    }
                ]
            }), encoding="utf-8")
            cv_json.write_text(json.dumps([
                {"cluster_id": 1, "text": "｜", "bounding_rect": [20, 30, 20, 90], "lines": []},
            ]), encoding="utf-8")

            result = _build_integrated_from_sheet_regions(
                [
                    {
                        "region_id": 3,
                        "segments": [
                            {
                                "matched_tg_ids": [1],
                                "ocr_text": "｜",
                                "text_translated": "｜",
                                "orientation": "vertical",
                                "text_scale_class": "normal",
                                "render_hint": "normal",
                            },
                        ],
                    }
                ],
                str(sheet_json),
                str(cv_json),
                str(integrated_json),
            )

            cluster = result["clusters"][0]
            self.assertEqual(cluster["text"], "|")
            self.assertEqual(cluster["text_translated"], "|")

    def test_validation_allows_repeated_ocr_text_in_different_regions(self) -> None:
        needs_retry, reason = validate_gemini_ocr_regions(
            [
                {
                    "region_id": 3,
                    "ocr_text": "アクア\nスラッシュ！",
                    "text_translated": "아쿠아\n슬래시!",
                    "segments": [
                        {
                            "matched_tg_ids": [3],
                            "ocr_text": "アクア\nスラッシュ！",
                            "text_translated": "아쿠아\n슬래시!",
                            "render_hint": "bold",
                        }
                    ],
                },
                {
                    "region_id": 4,
                    "ocr_text": "アクア\nスラッシュ！",
                    "text_translated": "아쿠아\n슬래시!",
                    "segments": [
                        {
                            "matched_tg_ids": [2],
                            "ocr_text": "アクア\nスラッシュ！",
                            "text_translated": "아쿠아\n슬래시!",
                            "render_hint": "bold",
                        }
                    ],
                },
            ],
            {3, 4},
        )

        self.assertFalse(needs_retry, reason)

    def test_segments_correct_swapped_tg_ids_by_ocr_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "regions.json"
            cv_json = tmp_path / "text_groups.json"
            integrated_json = tmp_path / "integrated.json"

            sheet_json.write_text(json.dumps({
                "regions": [
                    {
                        "region_id": 7,
                        "anchor_ids": [5, 14],
                        "type": "bubble",
                        "bubble_rect": [204, 725, 552, 421],
                    }
                ]
            }), encoding="utf-8")
            cv_json.write_text(json.dumps([
                {"cluster_id": 5, "text": "ハロルド・ストークスとして", "bounding_rect": [600, 794, 103, 149], "lines": []},
                {"cluster_id": 14, "text": "原作に沿いつつクズ的行動を避けていくのがベストな手段だ", "bounding_rect": [256, 865, 163, 207], "lines": []},
            ]), encoding="utf-8")

            result = _build_integrated_from_sheet_regions(
                [
                    {
                        "region_id": 7,
                        "segments": [
                            {
                                "matched_tg_ids": [14],
                                "ocr_text": "ハロルド・\nストークス\nとして",
                                "text_translated": "해럴드\n스토크스\n로서",
                            },
                            {
                                "matched_tg_ids": [5],
                                "ocr_text": "原作に沿いつつ\nクズ的行動を\n避けていくのが\nベストな手段だ",
                                "text_translated": "원작을 따르면서\n쓰레기 같은 행동을\n피해 가는 것이\n최선의 수단이다",
                            },
                        ],
                    }
                ],
                str(sheet_json),
                str(cv_json),
                str(integrated_json),
            )

            clusters = result["clusters"]
            self.assertEqual(clusters[0]["matched_cluster"], [5])
            self.assertEqual(clusters[1]["matched_cluster"], [14])

    def test_unboxed_segments_use_each_tg_rect_for_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "regions.json"
            cv_json = tmp_path / "text_groups.json"
            integrated_json = tmp_path / "integrated.json"

            sheet_json.write_text(json.dumps({
                "regions": [
                    {
                        "region_id": 2,
                        "anchor_ids": [3, 6],
                        "type": "unboxed",
                        "unboxed_rect": [816, 1145, 342, 362],
                        "erase_rect": [816, 1119, 332, 399],
                    }
                ]
            }), encoding="utf-8")
            cv_json.write_text(json.dumps([
                {"cluster_id": 3, "text": "こんの悪たれ口め", "bounding_rect": [1003, 1153, 147, 157], "lines": []},
                {"cluster_id": 6, "text": "せっかくのノーマンさんの気遣い", "bounding_rect": [824, 1224, 153, 275], "lines": []},
            ]), encoding="utf-8")

            result = _build_integrated_from_sheet_regions(
                [
                    {
                        "region_id": 2,
                        "segments": [
                            {
                                "ocr_text": "こんの\n悪たれ口\nめ…",
                                "text_translated": "이런\n입만 산 녀석이…",
                            },
                            {
                                "ocr_text": "せっかくの\nノーマンさんの\n気遣い",
                                "text_translated": "모처럼\n노먼 씨의\n배려를",
                            },
                        ],
                    }
                ],
                str(sheet_json),
                str(cv_json),
                str(integrated_json),
            )

            clusters = result["clusters"]
            self.assertEqual(clusters[0]["matched_cluster"], [3])
            self.assertEqual(clusters[1]["matched_cluster"], [6])
            self.assertEqual(clusters[0]["unboxed_rect"], [1003, 1153, 147, 157])
            self.assertEqual(clusters[1]["unboxed_rect"], [824, 1224, 153, 275])
            self.assertEqual(clusters[0]["bounding_rect"], [1003, 1153, 147, 157])
            self.assertEqual(clusters[1]["bounding_rect"], [824, 1224, 153, 275])
            self.assertEqual(clusters[0]["source_region_rect"], [816, 1145, 342, 362])
            self.assertEqual(clusters[1]["source_region_rect"], [816, 1145, 342, 362])

    def test_consolidated_unboxed_segments_use_whole_region_rect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "regions.json"
            cv_json = tmp_path / "text_groups.json"
            integrated_json = tmp_path / "integrated.json"

            sheet_json.write_text(json.dumps({
                "regions": [
                    {
                        "region_id": 3,
                        "anchor_ids": [2, 1],
                        "type": "unboxed",
                        "unboxed_rect": [632, 1529, 148, 167],
                        "erase_rect": [640, 1541, 120, 143],
                        "quality_flags": ["unboxed_consolidated_tg_cluster"],
                        "consolidated_text_group": {
                            "text_group_ids": [1, 2],
                            "text": "はつ醜い顔",
                        },
                    }
                ]
            }), encoding="utf-8")
            cv_json.write_text(json.dumps([
                {"cluster_id": 1, "text": "はつ", "bounding_rect": [720, 1542, 40, 91], "lines": []},
                {"cluster_id": 2, "text": "醜い顔", "bounding_rect": [640, 1541, 49, 143], "lines": []},
            ]), encoding="utf-8")

            result = _build_integrated_from_sheet_regions(
                [
                    {
                        "region_id": 3,
                        "segments": [
                            {"ocr_text": "はっ", "text_translated": "핫", "matched_tg_ids": [1]},
                            {"ocr_text": "醜い顔", "text_translated": "추악한 얼굴", "matched_tg_ids": [2]},
                        ],
                    }
                ],
                str(sheet_json),
                str(cv_json),
                str(integrated_json),
            )

            clusters = result["clusters"]
            self.assertEqual(len(clusters), 1)
            self.assertEqual(clusters[0]["matched_cluster"], [2, 1])
            self.assertEqual(clusters[0]["text_translated"], "핫\n추악한 얼굴")
            self.assertEqual(clusters[0]["unboxed_rect"], [632, 1529, 148, 167])
            self.assertEqual(clusters[0]["bounding_rect"], [632, 1529, 148, 167])
            self.assertIsNone(clusters[0]["source_segment_index"])


if __name__ == "__main__":
    unittest.main()
