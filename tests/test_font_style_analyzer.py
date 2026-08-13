# CV 기반 폰트 스타일 판정 규칙을 검증하는 테스트
import unittest
import unicodedata
from pathlib import Path

from pipeline.font_style_analyzer import (
    _final_render_hint,
    _normalized_render_hint,
    _should_preserve_original_by_style,
)
from pipeline.render_text_overlay import font_path_for_cluster, font_path_for_style


class FontStyleAnalyzerTest(unittest.TestCase):
    def test_memory_serif_keeps_thin_small_lines_together(self) -> None:
        features = {
            "line_style_features": {
                "line_count": 2,
                "char_count": 10,
                "line_width_px": 25.0,
                "font_size_estimate": 26.1,
                "weight_ratio": 0.077,
                "line_stroke_width_px": 2.0,
                "line_ink_density": 0.224,
                "line_width_rel": 0.833,
                "line_ink_density_rel": 1.107,
                "line_shape_complexity_rel": 1.459,
            }
        }

        self.assertEqual(_final_render_hint("normal", features), "memory_serif")

    def test_memory_serif_does_not_steal_heavier_normal_dialogue(self) -> None:
        features = {
            "line_style_features": {
                "line_count": 3,
                "char_count": 18,
                "line_width_px": 37.0,
                "font_size_estimate": 38.0,
                "weight_ratio": 0.091,
                "line_stroke_width_px": 4.0,
                "line_ink_density": 0.225,
                "line_width_rel": 1.233,
                "line_ink_density_rel": 1.111,
                "line_shape_complexity_rel": 0.728,
            }
        }

        self.assertEqual(_final_render_hint("normal", features), "normal")

    def test_legacy_efx_hint_normalizes_to_serif_emphasis(self) -> None:
        self.assertEqual(_normalized_render_hint("efx"), "serif_emphasis")
        self.assertEqual(_final_render_hint("efx", {}), "serif_emphasis")

    def test_memory_serif_maps_to_source_han_serif_medium_font(self) -> None:
        font_path = font_path_for_cluster({"render_hint_final": "memory_serif"}, Path("Fonts/Freesentation-5Medium.ttf"))

        self.assertEqual(font_path, Path("Fonts/SourceHanSerifK-Medium.otf"))

    def test_legacy_efx_cluster_maps_to_serif_emphasis_font(self) -> None:
        default_font = Path("Fonts/Freesentation-5Medium.ttf")
        font_path = font_path_for_cluster({"render_hint_final": "efx"}, default_font)
        emphasis_path = font_path_for_style("serif_emphasis", default_font)

        self.assertEqual(font_path, emphasis_path)

    def test_overlay_manual_serif_maps_to_source_han_serif_medium_font(self) -> None:
        font_path = font_path_for_style("memory_serif", Path("Fonts/Freesentation-5Medium.ttf"))

        self.assertEqual(font_path, Path("Fonts/SourceHanSerifK-Medium.otf"))

    def test_overlay_manual_efx_and_serif_emphasis_map_to_atractor_font(self) -> None:
        default_font = Path("Fonts/Freesentation-5Medium.ttf")
        efx_path = font_path_for_style("efx", default_font)
        emphasis_path = font_path_for_style("serif_emphasis", default_font)

        self.assertEqual(efx_path, emphasis_path)
        self.assertTrue(efx_path.exists())
        self.assertEqual(unicodedata.normalize("NFC", efx_path.name), "a트랙터.ttf")

    def test_overlay_manual_handwritten_maps_to_nanum_gibbeum_font(self) -> None:
        font_path = font_path_for_style("handwritten", Path("Fonts/Freesentation-5Medium.ttf"))

        self.assertEqual(font_path, Path("Fonts/NanumGiBbeumBarkEum.ttf"))

    def test_long_dialogue_text_is_not_preserved_by_sparse_single_style_false_positive(self) -> None:
        features = {
            "line_style_features": {
                "line_count": 1,
                "char_count": 1,
                "line_width_rel": 3.714,
                "weight_ratio": 0.0215,
                "line_ink_density_rel": 0.129,
            }
        }

        preserve, reason = _should_preserve_original_by_style("口答えするな", features)

        self.assertFalse(preserve)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
