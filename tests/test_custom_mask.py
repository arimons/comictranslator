# 지우기 보정(guidance.custom_masks)이 분석 지우기가 끝난 결과에 마지막으로 합성되는지 검증한다.
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from pipeline.dialogue_extractor import CUSTOM_MASK_CONTEXT_PAD, _custom_mask_layer, build_dialogue_sheet

TG_RECT = [136, 116, 28, 108]


def _page(tmp_path):
    image = np.full((400, 300, 3), 255, dtype=np.uint8)
    for y in range(120, 220, 28):  # 세로 글자 비슷한 획
        cv2.rectangle(image, (140, y), (160, y + 18), (0, 0, 0), -1)
    cv2.rectangle(image, (40, 300), (60, 330), (0, 0, 0), -1)  # 분석이 놓친 글자(어느 TG에도 없음)
    paths = {name: str(tmp_path / name) for name in ("page.jpg", "groups.json", "masked.jpg", "regions.json", "sheet.jpg", "vis.jpg")}
    cv2.imwrite(paths["page.jpg"], image)
    group = {
        "text_group_id": 1, "cluster_id": 1, "text": "あいう",
        "fallback_rect": TG_RECT, "tight_rect": TG_RECT, "bounding_rect": TG_RECT,
        "orientation": "vertical",
        "lines": [{"bbox": TG_RECT, "fragments": [{"bbox": TG_RECT}]}],
    }
    Path(paths["groups.json"]).write_text(json.dumps([group]), encoding="utf-8")
    return paths


def _run(paths, monkeypatch, custom_masks=None):
    calls = []

    def fake_lama():
        # 마스크 자리를 흰색으로 채우는 가짜 LaMa. 받은 창 크기를 기록한다.
        def run(roi_pil, mask_pil):
            roi, mask = np.array(roi_pil).copy(), np.array(mask_pil)
            calls.append(roi.shape[:2])
            roi[mask > 0] = 255
            return Image.fromarray(roi)
        return run

    monkeypatch.setattr("pipeline.dialogue_extractor.build_ocr_region_sheet", lambda *a, **k: {"regions": []})
    monkeypatch.setattr("pipeline.dialogue_extractor._get_lama", fake_lama)
    build_dialogue_sheet(
        paths["page.jpg"], paths["groups.json"], paths["masked.jpg"], paths["regions.json"], paths["sheet.jpg"], paths["vis.jpg"],
        custom_masks=custom_masks,
    )
    saved = json.loads(Path(paths["regions.json"]).read_text(encoding="utf-8"))
    return calls, saved, cv2.imread(paths["masked.jpg"])


def test_without_custom_masks_nothing_changes(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    calls, saved, masked = _run(paths, monkeypatch)
    assert len(calls) == 1  # 텍스트 기반 영역 하나만 지운다
    assert "custom_masks_applied" not in saved
    assert masked[315, 50].max() < 100  # 분석이 놓친 글자는 남아 있다


def test_custom_polygon_is_erased_on_final_result(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    custom = {"id": "cm_1", "mode": "add", "polygons": [[[35, 295], [66, 295], [66, 336], [35, 336]]]}
    calls, saved, masked = _run(paths, monkeypatch, custom_masks=[custom])
    report = saved["custom_masks_applied"]
    assert report["mask_ids"] == ["cm_1"] and report["polygons"] == 1 and report["pixels"] > 0
    # 영역 처리(1회) 뒤 보정 덩어리 하나를 따로 한 번 채운다. 창은 칠한 곳 둘레까지 포함한다
    assert len(calls) == 2 and len(report["windows"]) == 1
    x, y, w, h = report["windows"][0]
    assert x <= 35 - CUSTOM_MASK_CONTEXT_PAD + 1 and x + w >= 66 + CUSTOM_MASK_CONTEXT_PAD - 1
    assert calls[1] == (h, w)
    assert masked[315, 50].min() > 200  # 놓친 글자가 지워졌다
    assert masked[150, 150].min() > 200  # 분석이 지운 결과도 그대로다


def test_custom_mask_is_independent_of_regions(tmp_path, monkeypatch):
    paths = _page(tmp_path)
    # 영역 참조·영역 키가 없어도, 영역과 겹쳐도, 이미지 밖으로 나가도 칠한 곳 그대로 적용된다
    custom = {"id": "cm_2", "strokes": [{"points": [[150, 120], [150, 210]], "width": 30}], "rects": [[280, 380, 50, 50]]}
    _, saved, _ = _run(paths, monkeypatch, custom_masks=[custom])
    report = saved["custom_masks_applied"]
    assert (report["strokes"], report["rects"]) == (1, 1)
    assert len(report["windows"]) == 2
    assert all(x + w <= 300 and y + h <= 400 for x, y, w, h in report["windows"])


def test_custom_mask_layer_ignores_broken_shapes():
    record = {
        "strokes": [{"points": [["x", 1]]}, {"points": []}, "bad", {"points": [[15, 15], [20, 20]], "width": 9999}],
        "rects": [[1, 2], "bad", [12, 12, 0, 5]],
        "polygons": [[[1, 1], [2, 2]], [["a", 1], [2, 2], [3, 3]], "bad"],
    }
    layer, meta = _custom_mask_layer([record, "bad", {"mode": "subtract", "rects": [[0, 0, 5, 5]]}], (100, 100, 3))
    assert (meta["strokes"], meta["rects"], meta["polygons"]) == (1, 0, 0)
    assert layer.shape == (100, 100) and layer.max() == 255
    assert _custom_mask_layer([], (100, 100, 3)) == (None, None)


def test_changing_custom_masks_disables_unboxed_inpaint_reuse():
    import app

    item = {"id": "cm_1", "mode": "add", "strokes": [{"points": [[1, 2]], "width": 4}], "rects": []}
    base = {"manual_regions": [{"type": "unboxed"}]}
    assert app.guidance_has_edits({"custom_masks": [item]})
    prev = {**base, "custom_masks": [item]}
    # 보정이 같으면 덧붙이는 편집만 있을 때 재사용한다
    assert app.guidance_allows_unboxed_mask_reuse(prev, {**prev, "manual_seed_points": [{"x": 1, "y": 2}]})
    # 보정을 더하거나 고치거나 지우면 이전 지우기 결과를 쓰지 않는다
    assert not app.guidance_allows_unboxed_mask_reuse(base, prev)
    changed = {**item, "strokes": item["strokes"] + [{"points": [[3, 4]], "width": 4}]}
    assert not app.guidance_allows_unboxed_mask_reuse(prev, {**base, "custom_masks": [changed]})
    assert not app.guidance_allows_unboxed_mask_reuse(prev, base)
