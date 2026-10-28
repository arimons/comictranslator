# 자동 텍스트 기반 영역을 사용자가 LaMa로 고정하면 Step 3가 Telea 분기를 건너뛰는지 검증한다.
import json

import cv2
import numpy as np

from pipeline.dialogue_extractor import _crop_binding_key, _forced_lama_keys, build_dialogue_sheet
from pipeline.region_types import region_binding_key


def _page_with_vertical_text(tmp_path):
    image = np.full((400, 300, 3), 255, dtype=np.uint8)
    for y in range(120, 220, 28):  # 세로 글자 비슷한 획
        cv2.rectangle(image, (140, y), (160, y + 18), (0, 0, 0), -1)
    paths = {name: str(tmp_path / f"{name}") for name in ("page.jpg", "groups.json", "masked.jpg", "regions.json", "sheet.jpg", "vis.jpg")}
    cv2.imwrite(paths["page.jpg"], image)
    group = {
        "text_group_id": 1, "cluster_id": 1, "text": "あいう",
        "fallback_rect": [136, 116, 28, 108], "tight_rect": [136, 116, 28, 108], "bounding_rect": [136, 116, 28, 108],
        "orientation": "vertical",
        "lines": [{"bbox": [136, 116, 28, 108], "fragments": [{"bbox": [136, 116, 28, 108]}]}],
    }
    (tmp_path / "groups.json").write_text(json.dumps([group]), encoding="utf-8")
    return paths


def _run(paths, monkeypatch, overrides=None):
    calls = {"lama": 0}  # cv2.inpaint는 후보 추출 단계에서도 쓰여 호출 수로 구분할 수 없다. LaMa 호출 수와 영역 기록으로 검증한다.

    def fake_lama():
        def run(roi_pil, mask_pil):
            calls["lama"] += 1
            return roi_pil
        return run

    monkeypatch.setattr("pipeline.dialogue_extractor.build_ocr_region_sheet", lambda *a, **k: {"regions": []})
    monkeypatch.setattr("pipeline.dialogue_extractor._get_lama", fake_lama)
    build_dialogue_sheet(
        paths["page.jpg"], paths["groups.json"], paths["masked.jpg"], paths["regions.json"], paths["sheet.jpg"], paths["vis.jpg"],
        inpaint_engine_overrides=overrides,
    )
    regions = json.loads((tmp_path_of(paths) / "regions.json").read_text(encoding="utf-8"))["regions"]
    return calls, regions


def tmp_path_of(paths):
    from pathlib import Path
    return Path(paths["regions.json"]).parent


def test_auto_unboxed_region_uses_telea_then_forced_lama_by_region_key(tmp_path, monkeypatch):
    paths = _page_with_vertical_text(tmp_path)

    calls, regions = _run(paths, monkeypatch)
    text_regions = [r for r in regions if r.get("type") == "unboxed"]
    assert len(text_regions) == 1, regions
    region = text_regions[0]
    assert calls["lama"] == 0
    assert region["inpaint_engine_used"] == "telea" and region["inpaint_engine_source"] == "auto"

    # 화면이 쓰는 영역 키(출력 영역에서 계산)로 고정하면 같은 키가 Step 3 안에서도 맞아야 한다
    key = region_binding_key(region)
    calls, regions = _run(paths, monkeypatch, overrides=[{"target_key": key, "engine": "lama"}])
    region = [r for r in regions if r.get("type") == "unboxed"][0]
    assert calls["lama"] == 1
    assert region["inpaint_engine_used"] == "lama" and region["inpaint_engine_source"] == "forced"


def test_override_for_other_region_or_other_engine_is_ignored(tmp_path, monkeypatch):
    paths = _page_with_vertical_text(tmp_path)
    calls, _ = _run(paths, monkeypatch, overrides=[
        {"target_key": '["text_based","other",[0,0,1,1]]', "engine": "lama"},
        {"target_key": "x", "engine": "telea"},
        {"target_key": '["text_based","other",[0,0,1,1]]', "engine": "unknown"},
        {"engine": "lama"},
        "bad",
    ])
    assert calls["lama"] == 0


def test_forced_keys_and_crop_key_helpers():
    assert _forced_lama_keys([{"target_key": "a", "engine": "lama"}, {"target_key": "b", "engine": "telea"}, None]) == {"a"}
    assert _forced_lama_keys(None) == set()
    crop = {"type": "unboxed", "source_method": "unboxed_consolidated", "source_rect": [1, 2, 3, 4], "erase_rect": [0, 0, 9, 9]}
    # erase_rect가 아니라 source_rect(=출력 영역의 unboxed_rect)로 키를 만든다
    assert _crop_binding_key(crop) == region_binding_key({"type": "unboxed", "source_method": "unboxed_consolidated", "unboxed_rect": [1, 2, 3, 4]})


def test_changing_lama_overrides_disables_unboxed_inpaint_reuse():
    import app

    key = {"target_key": '["text_based","unboxed_consolidated",[1,2,3,4]]', "engine": "lama"}
    base = {"manual_regions": [{"type": "unboxed"}]}
    assert app.guidance_has_edits({"inpaint_engine_overrides": [key]})
    # 같은 고정이면 추가 편집만 있을 때 기존처럼 재사용한다
    prev = {**base, "inpaint_engine_overrides": [key]}
    assert app.guidance_allows_unboxed_mask_reuse(prev, {**prev, "manual_seed_points": [{"x": 1, "y": 2}]})
    # 고정을 새로 켜거나 풀면 이전 지우기 결과를 쓰지 않고 다시 지운다
    assert not app.guidance_allows_unboxed_mask_reuse(base, {**base, "inpaint_engine_overrides": [key]})
    assert not app.guidance_allows_unboxed_mask_reuse(prev, base)


def test_text_based_region_can_be_forced_to_telea(tmp_path, monkeypatch):
    paths = _page_with_vertical_text(tmp_path)
    _, regions = _run(paths, monkeypatch)
    key = region_binding_key([r for r in regions if r.get("type") == "unboxed"][0])

    calls, regions = _run(paths, monkeypatch, overrides=[{"target_key": key, "engine": "telea"}])
    region = [r for r in regions if r.get("type") == "unboxed"][0]
    assert calls["lama"] == 0
    assert region["inpaint_engine_used"] == "telea" and region["inpaint_engine_source"] == "forced"


def test_resized_text_region_disables_unboxed_inpaint_reuse():
    import app

    base = {"manual_regions": [{"type": "unboxed", "unboxed_rect": [1, 2, 3, 4]}]}
    added = {"manual_regions": base["manual_regions"] + [{"type": "unboxed", "unboxed_rect": [5, 5, 9, 9]}]}
    resized = {"manual_regions": base["manual_regions"] + [{"type": "unboxed", "unboxed_rect": [5, 5, 9, 9], "resize_source_key": "k"}]}
    assert app.guidance_allows_unboxed_mask_reuse(base, added)
    assert not app.guidance_allows_unboxed_mask_reuse(base, resized)


def test_resized_text_region_replaces_auto_region(tmp_path, monkeypatch):
    paths = _page_with_vertical_text(tmp_path)
    _, regions = _run(paths, monkeypatch)
    auto = [r for r in regions if r.get("type") == "unboxed"][0]
    # 화면에서 크기를 바꾸면 같은 TG를 덮는 수동 텍스트 영역 가이드가 생긴다
    manual = {"type": "unboxed", "unboxed_rect": [120, 100, 60, 140], "source_method": "manual_ubox_guide", "resize_source_key": region_binding_key(auto)}
    monkeypatch.setattr("pipeline.dialogue_extractor.build_ocr_region_sheet", lambda *a, **k: {"regions": []})
    monkeypatch.setattr("pipeline.dialogue_extractor._get_lama", lambda: (lambda roi, mask: roi))
    build_dialogue_sheet(
        paths["page.jpg"], paths["groups.json"], paths["masked.jpg"], paths["regions.json"], paths["sheet.jpg"], paths["vis.jpg"],
        manual_regions=[manual],
    )
    regions = json.loads((tmp_path / "regions.json").read_text(encoding="utf-8"))["regions"]
    text_regions = [r for r in regions if r.get("type") == "unboxed"]
    assert len(text_regions) == 1, text_regions
    assert text_regions[0]["unboxed_rect"] == [120, 100, 60, 140]
