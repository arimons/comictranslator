# TextFragment 기반 스타일 판정 유틸리티를 검증하는 테스트
import unittest

from pipeline.text_style_metrics import classify_text_group_style


class TextStyleMetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = {"font_proxy": 24.0, "dark_density": 0.20, "ink_fill": 0.42}

    def test_large_normal_is_included(self) -> None:
        decision = classify_text_group_style(
            {"font_proxy": 42.0, "dark_density": 0.14, "ink_fill": 0.28, "aspect": 0.25, "page_long_side": 1080},
            self.baseline,
        )

        self.assertEqual(decision["style_class"], "large_normal")
        self.assertEqual(decision["include_policy"], "include")

    def test_large_sparse_efx_is_excluded_before_large_normal(self) -> None:
        decision = classify_text_group_style(
            {"font_proxy": 56.0, "dark_density": 0.05, "ink_fill": 0.11, "aspect": 1.04, "page_long_side": 1080},
            self.baseline,
        )

        self.assertEqual(decision["style_class"], "large_sparse_efx")
        self.assertEqual(decision["include_policy"], "exclude")

    def test_large_heavy_efx_is_excluded_before_large_normal(self) -> None:
        decision = classify_text_group_style(
            {"font_proxy": 42.0, "dark_density": 0.31, "ink_fill": 0.50, "aspect": 0.40, "page_long_side": 1080},
            self.baseline,
        )

        self.assertEqual(decision["style_class"], "large_heavy_efx")
        self.assertEqual(decision["include_policy"], "exclude")

    def test_landscape_irregular_is_excluded_without_char_count_gate(self) -> None:
        decision = classify_text_group_style(
            {"font_proxy": 32.0, "dark_density": 0.18, "ink_fill": 0.38, "aspect": 1.35, "page_long_side": 1080},
            self.baseline,
        )

        self.assertEqual(decision["style_class"], "landscape_irregular")
        self.assertEqual(decision["include_policy"], "exclude")

    def test_small_light_handwritten_is_excluded(self) -> None:
        decision = classify_text_group_style(
            {
                "char_count": 5,
                "font_proxy": 19.0,
                "dark_density": 0.16,
                "ink_fill": 0.34,
                "aspect": 0.18,
                "page_long_side": 1080,
            },
            self.baseline,
        )

        self.assertEqual(decision["style_class"], "small_light_handwritten")
        self.assertEqual(decision["include_policy"], "exclude")

    def test_long_small_light_vertical_text_is_included(self) -> None:
        decision = classify_text_group_style(
            {
                "char_count": 28,
                "font_proxy": 20.7,
                "dark_density": 0.162,
                "ink_fill": 0.35,
                "aspect": 0.26,
                "page_long_side": 1920,
            },
            {"font_proxy": 24.0, "dark_density": 0.20, "ink_fill": 0.42},
        )

        self.assertEqual(decision["style_class"], "small_light_dialogue")
        self.assertEqual(decision["include_policy"], "include")

    def test_single_glyph_heavy_density_is_review_not_excluded(self) -> None:
        decision = classify_text_group_style(
            {"font_proxy": 23.0, "dark_density": 0.31, "ink_fill": 0.58, "aspect": 1.05, "page_long_side": 1080},
            self.baseline,
        )

        self.assertEqual(decision["style_class"], "heavy_or_single_glyph")
        self.assertEqual(decision["include_policy"], "review")

    def test_long_heavy_dialogue_is_included(self) -> None:
        decision = classify_text_group_style(
            {
                "char_count": 14,
                "font_proxy": 26.5,
                "dark_density": 0.252,
                "ink_fill": 0.435,
                "aspect": 0.66,
                "page_long_side": 1080,
            },
            self.baseline,
        )

        self.assertEqual(decision["style_class"], "large_heavy_dialogue")
        self.assertEqual(decision["include_policy"], "include")

    def test_large_landscape_uses_page_scaled_font_size(self) -> None:
        decision = classify_text_group_style(
            {"font_proxy": 130.0, "dark_density": 0.20, "ink_fill": 0.42, "aspect": 1.16, "page_long_side": 3000},
            {"font_proxy": 130.0, "dark_density": 0.20, "ink_fill": 0.42},
        )

        self.assertEqual(decision["style_class"], "landscape_irregular")
        self.assertEqual(decision["include_policy"], "exclude")


if __name__ == "__main__":
    unittest.main()
