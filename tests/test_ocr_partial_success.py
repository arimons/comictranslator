# Step 4 OCR/번역이 일부 region만 실패해도 통과한 region을 버리지 않는지 검증한다 (모델 호출은 가짜).
import json
import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pipeline.gemini_ocr_translate_v5 as ocr


def _region(rid, translated="번역"):
    return {"region_id": rid, "ocr_text": "原文", "text_translated": translated, "orientation": "vertical", "render_hint": "normal"}


@pytest.fixture
def sheet(tmp_path, monkeypatch):
    image = np.full((60, 130, 3), 255, dtype=np.uint8)
    sheet_image = tmp_path / "sheet.jpg"
    cv2.imwrite(str(sheet_image), image)
    regions = [
        {"region_id": 1, "anchor_ids": [], "type": "bubble", "bubble_rect": [0, 0, 50, 50], "indexed_sheet_rect": [0, 0, 60, 60]},
        {"region_id": 2, "anchor_ids": [], "type": "bubble", "bubble_rect": [60, 0, 50, 50], "indexed_sheet_rect": [70, 0, 60, 60]},
    ]
    sheet_json = tmp_path / "regions.json"
    sheet_json.write_text(json.dumps({"regions": regions}), encoding="utf-8")
    cv_json = tmp_path / "text_groups.json"
    cv_json.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)
    return sheet_image, sheet_json, cv_json, tmp_path / "integrated.json"


def _run(sheet, responses, monkeypatch):
    sheet_image, sheet_json, cv_json, integrated = sheet
    calls = []

    def fake_model(images, prompt, temperature=0.25):
        calls.append(images[0])
        reply = responses[min(len(calls), len(responses)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return {"regions": reply}, 0.1

    monkeypatch.setattr(ocr, "_run_model_json", fake_model)
    result = ocr._process_sheet_ocr(str(sheet_image), str(sheet_json), str(cv_json), str(integrated))
    return result, calls, integrated


def test_retry_requests_only_failed_region(sheet, monkeypatch):
    responses = [
        [_region(1), _region(2, translated="")],  # R2 번역 비어 있음
        [_region(2)],
    ]
    result, calls, integrated = _run(sheet, responses, monkeypatch)

    assert result["success"] is True
    assert result["missing_region_ids"] == []
    assert len(calls) == 2
    assert calls[1].endswith("_retry2.jpg")  # 두 번째 요청은 실패한 region만 담은 시트
    data = json.loads(integrated.read_text(encoding="utf-8"))
    assert sorted(c["source_region_id"] for c in data["clusters"]) == [1, 2]
    assert "missing_region_ids" not in data


def test_region_failing_every_attempt_is_left_missing(sheet, monkeypatch):
    responses = [[_region(1), _region(2, translated="")], [_region(2, translated="")]]
    result, calls, integrated = _run(sheet, responses, monkeypatch)

    assert result["success"] is True
    assert result["missing_region_ids"] == [2]
    assert len(calls) == 3
    data = json.loads(integrated.read_text(encoding="utf-8"))
    assert [c["source_region_id"] for c in data["clusters"]] == [1]
    assert data["missing_region_ids"] == [2]


def test_page_fails_when_no_region_succeeds(sheet, monkeypatch):
    result, calls, integrated = _run(sheet, [RuntimeError("401 invalid key")], monkeypatch)

    assert result["success"] is False
    assert "401" in result["error"]
    assert not integrated.exists()
