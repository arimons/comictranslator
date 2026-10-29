import json

import cv2
import numpy as np

import pipeline.dialogue_extractor as dx


def _page(tmp_path):
    image = np.full((200, 200, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (100, 100), (70, 85), 0, 0, 360, (0, 0, 0), 2)
    cv2.putText(image, "HI", (75, 105), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), image)
    groups = tmp_path / "groups.json"
    groups.write_text(json.dumps([{"text_group_id": 1, "fallback_rect": [70, 80, 60, 35], "text": "HI", "lines": []}]))
    return path, groups


def _run(tmp_path, path, groups, name, **guidance):
    return dx.build_dialogue_sheet(
        str(path), str(groups), str(tmp_path / f"{name}_m.png"), str(tmp_path / f"{name}_r.json"),
        str(tmp_path / f"{name}_s.jpg"), str(tmp_path / f"{name}_v.jpg"), **guidance,
    )


def test_detection_is_reused_when_only_manual_guidance_changes(tmp_path, monkeypatch):
    path, groups = _page(tmp_path)
    calls = []
    original = dx.build_ocr_region_sheet
    monkeypatch.setattr(dx, "build_ocr_region_sheet", lambda **kw: calls.append(1) or original(**kw))
    dx.clear_detection_cache()

    _run(tmp_path, path, groups, "a")
    _run(tmp_path, path, groups, "b", manual_virtual_walls=[{"id": "w", "box_id": "b", "line": [[10, 10], [20, 10]]}])
    assert len(calls) == 1                                  # 가이드만 바뀌면 자동 검출은 한 번만
    assert (tmp_path / "b_v.jpg").exists()                  # 시각화도 캐시에서 다시 써 준다


def test_detection_cache_is_not_reused_when_text_groups_or_image_change(tmp_path, monkeypatch):
    path, groups = _page(tmp_path)
    calls = []
    original = dx.build_ocr_region_sheet
    monkeypatch.setattr(dx, "build_ocr_region_sheet", lambda **kw: calls.append(1) or original(**kw))
    dx.clear_detection_cache()

    _run(tmp_path, path, groups, "a")
    groups.write_text(json.dumps([{"text_group_id": 1, "fallback_rect": [72, 82, 56, 33], "text": "HI", "lines": []}]))
    _run(tmp_path, path, groups, "b")
    assert len(calls) == 2                                  # 텍스트 그룹이 바뀌면 다시 검출
    cv2.imwrite(str(path), np.full((200, 200, 3), 250, dtype=np.uint8))
    _run(tmp_path, path, groups, "c")
    assert len(calls) == 3                                  # 이미지가 바뀌면 다시 검출


def test_cached_result_equals_uncached_result(tmp_path):
    path, groups = _page(tmp_path)
    dx.clear_detection_cache()
    cold = _run(tmp_path, path, groups, "cold")
    warm = _run(tmp_path, path, groups, "warm")
    assert json.dumps(cold["regions"], sort_keys=True, default=str) == json.dumps(warm["regions"], sort_keys=True, default=str)
    assert np.array_equal(cv2.imread(str(tmp_path / "cold_m.png")), cv2.imread(str(tmp_path / "warm_m.png")))
