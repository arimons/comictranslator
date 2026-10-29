"""영역 참조(region_ref) 회귀 fixture.

Step 3 재분석마다 영역 R 번호가 읽기 순서로 다시 매겨져, 번역(integrated)의 번호 공간과
현재 regions.json의 번호 공간이 어긋난다. 렌더러가 두 번호 공간을 같은 dict에서 섞으면
수동값·복원 억제가 다른 영역에 조용히 붙는다.

이 파일은 합성 JSON만 쓴다(실제 만화 번역문 없음).
- 특성 테스트: 지금도 통과해야 하는 동작(번호가 일치하는 정상 경로, 구형 숫자 키 호환)
- xfail(strict): 영역 참조 전환(설계안 단계 5)이 끝나면 통과해야 하는 사고 재현.
  통과로 바뀌면 strict가 실패를 내므로 그때 xfail 표시를 지운다.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from pipeline import render_text_overlay as rto

PAGE_W, PAGE_H = 360, 240
ORIGINAL_GRAY = 60  # 원본 픽셀. 복원되면 이 값이 보인다.
MASKED_GRAY = 255  # 마스킹된(지워진) 픽셀

RECTS = {
    "A": [20, 20, 100, 80],
    "B": [140, 20, 100, 80],
    "C": [260, 20, 80, 80],
    "D": [20, 130, 100, 80],
}


def _region(region_id: int, tg: int, rect_key: str) -> dict:
    rect = RECTS[rect_key]
    return {
        "region_id": region_id,
        "type": "bubble",
        "anchor_ids": [tg],
        "bubble_rect": list(rect),
        "safe_text_rect": list(rect),
        "source_method": "synthetic",
    }


def _cluster(source_region_id: int, tg: int, rect_key: str, text: str = "테스트") -> dict:
    rect = RECTS[rect_key]
    return {
        "matched_cluster": [tg],
        "source_region_id": source_region_id,
        "sentence_id": source_region_id * 100,
        "location": "bubble",
        "text_translated": text,
        "bounding_rect": list(rect),
        "union_bbox": list(rect),
    }


def _render(
    clusters: list[dict],
    regions: list[dict],
    overlay: dict | None = None,
) -> tuple[np.ndarray, dict]:
    """합성 입력으로 apply_text_overlay를 돌려 (결과 이미지, overlay_layout)을 돌려준다."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        masked = np.full((PAGE_H, PAGE_W, 3), MASKED_GRAY, dtype=np.uint8)
        original = np.full((PAGE_H, PAGE_W, 3), ORIGINAL_GRAY, dtype=np.uint8)
        cv2.imwrite(str(tmp / "masked.png"), masked)
        cv2.imwrite(str(tmp / "original.png"), original)
        (tmp / "integrated.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8"
        )
        (tmp / "regions.json").write_text(
            json.dumps({"regions": regions}, ensure_ascii=False), encoding="utf-8"
        )
        guidance_path = None
        if overlay is not None:
            guidance_path = tmp / "overlay_guidance.json"
            guidance_path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
        rto.apply_text_overlay(
            tmp / "masked.png",
            tmp / "integrated.json",
            tmp / "final.png",
            regions_json=tmp / "regions.json",
            original_image_path=tmp / "original.png",
            overlay_guidance_path=guidance_path,
            overlay_layout_output=tmp / "layout.json",
        )
        final = cv2.imread(str(tmp / "final.png"))
        layout = json.loads((tmp / "layout.json").read_text(encoding="utf-8"))
        return final, layout


def _corner_pixel(final: np.ndarray, rect_key: str) -> int:
    """영역 사각형의 왼쪽 위 모서리 안쪽 픽셀. 글자가 닿지 않아 복원 여부만 드러난다."""
    x, y, _w, _h = RECTS[rect_key]
    return int(final[y + 1, x + 1, 0])


def _layout_entry_for_rect(layout: dict, rect_key: str) -> dict | None:
    target = RECTS[rect_key]
    for entry in layout.get("regions", {}).values():
        bbox = entry.get("bbox")
        if bbox and abs(bbox[0] - target[0]) <= 2 and abs(bbox[1] - target[1]) <= 2:
            return entry
    return None


class CurrentBehaviorTest(unittest.TestCase):
    """번호가 일치하는 정상 경로. 영역 참조 전환 뒤에도 같은 결과여야 한다."""

    def test_numeric_override_applies_to_matching_translation_group(self) -> None:
        clusters = [_cluster(1, 1, "A"), _cluster(2, 2, "B")]
        regions = [_region(1, 1, "A"), _region(2, 2, "B")]
        _final, layout = _render(clusters, regions, {"regions": {"2": {"font_size": 30}}})

        self.assertEqual(_layout_entry_for_rect(layout, "B")["font_size"], 30)
        self.assertNotEqual(_layout_entry_for_rect(layout, "A")["font_size"], 30)

    def test_empty_text_override_hides_only_that_group(self) -> None:
        clusters = [_cluster(1, 1, "A"), _cluster(2, 2, "B")]
        regions = [_region(1, 1, "A"), _region(2, 2, "B")]
        final, layout = _render(clusters, regions, {"regions": {"2": {"text_override": ""}}})

        self.assertIsNone(_layout_entry_for_rect(layout, "B"))
        self.assertIsNotNone(_layout_entry_for_rect(layout, "A"))
        # 빈 text_override는 원본 복원도 끈다.
        self.assertEqual(_corner_pixel(final, "B"), MASKED_GRAY)

    def test_untranslated_region_is_restored_when_numbers_agree(self) -> None:
        clusters = [_cluster(1, 1, "A")]
        regions = [_region(1, 1, "A"), _region(2, 2, "B")]
        final, _layout = _render(clusters, regions)

        self.assertEqual(_corner_pixel(final, "B"), ORIGINAL_GRAY)

    def test_renumbered_regions_do_not_move_translation_overrides(self) -> None:
        """영역이 하나 앞에 추가되어 regions 번호가 +1 되어도, 번역 번호 기준 수동값은 같은 대사에 남는다."""
        clusters = [_cluster(1, 1, "A"), _cluster(2, 2, "B")]
        regions = [_region(1, 9, "D"), _region(2, 1, "A"), _region(3, 2, "B")]
        _final, layout = _render(clusters, regions, {"regions": {"2": {"font_size": 30}}})

        self.assertEqual(_layout_entry_for_rect(layout, "B")["font_size"], 30)
        self.assertNotEqual(_layout_entry_for_rect(layout, "A")["font_size"], 30)


class NumberSpaceCollisionTest(unittest.TestCase):
    """번역 번호와 현재 regions 번호가 어긋난 사고. 설계안 단계 5 뒤에 통과해야 한다."""

    @unittest.expectedFailure
    def test_new_region_sharing_a_translation_number_is_still_restored(self) -> None:
        """번역은 {1:TG1}뿐인데 현재 R1이 다른 영역(TG9)이면, TG9 영역은 번역이 없으니 원본을 복원해야 한다.

        지금은 번역의 source_region_id 1과 현재 region_id 1이 같다고 보고 복원을 건너뛴다.
        """
        clusters = [_cluster(1, 1, "A")]
        regions = [_region(1, 9, "D"), _region(2, 1, "A")]
        final, _layout = _render(clusters, regions)

        self.assertEqual(_corner_pixel(final, "D"), ORIGINAL_GRAY)

    @unittest.expectedFailure
    def test_suppression_of_a_vanished_group_does_not_hide_another_region(self) -> None:
        """00011 사고 구조: 번역 묶음 2(TG2)의 빈 text_override가, TG2가 사라진 뒤 현재 R2(TG9)의 복원을 막는다."""
        clusters = [_cluster(1, 1, "A"), _cluster(2, 2, "B")]
        regions = [_region(1, 1, "A"), _region(2, 9, "D")]
        # TG2는 비활성화되어 현재 regions에 없고, 번호 2는 새 영역(TG9)이 가져갔다.
        # 번역 묶음 2(B)는 integrated에 남아 있어 그대로 그려진다.
        overlay = {"regions": {"2": {"text_override": ""}}}
        final, _layout = _render(clusters, regions, overlay)

        # 억제는 번역 묶음 2(B 자리)에만 적용되어야 한다. 현재 R2(D)는 번역이 없으니 원본이 복원되어야 한다.
        self.assertEqual(_corner_pixel(final, "D"), ORIGINAL_GRAY)


if __name__ == "__main__":
    unittest.main()
