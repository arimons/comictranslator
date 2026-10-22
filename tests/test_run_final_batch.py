# 확인한 페이지만 모아 백그라운드로 OCR/번역하는 요청(image_id="batch")을 검증한다 (모델 호출은 가짜).
import json

import cv2
import numpy as np
from fastapi.testclient import TestClient

import app


def _session_with_pages(tmp_path, monkeypatch, count=4):
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(app, "INPUT_CACHE_DIR", tmp_path / "input_cache")
    data = app.ensure_session("s1")
    input_dir = app.session_dir("s1") / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for index in range(1, count + 1):
        path = input_dir / f"p{index}.png"
        cv2.imwrite(str(path), np.full((200, 160, 3), 255, np.uint8))
        images.append({"id": f"p{index}", "name": f"p{index}.png", "input_path": str(path)})
    data["images"] = images
    app.save_session(data)


def _events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_batch_runs_only_requested_pages_and_skips_finished_ones(tmp_path, monkeypatch):
    _session_with_pages(tmp_path, monkeypatch)
    called = []

    def fake_run_final(session_id, item, *args, **kwargs):
        called.append((item["id"], args[3], kwargs.get("replace_overlay")))  # overlay_guidance, replace
        return {"id": item["id"], "integrated": {}}

    monkeypatch.setattr(app, "run_final_for_item", fake_run_final)
    monkeypatch.setattr(app, "ocr_completed_for_record", lambda record: record["id"] == "p3")  # p3는 이미 번역됨

    client = TestClient(app.create_app())
    response = client.post("/api/run-final-stream", json={
        "session_id": "s1", "image_id": "batch", "image_ids": ["p3", "p1", "p2"],
        "overlay_guidance": {"regions": {"x": 1}}, "replace": True,
    })
    assert response.status_code == 200, response.text
    events = _events(response)

    assert sorted(item[0] for item in called) == ["p1", "p2"]  # p4는 요청에 없고 p3은 이미 끝남
    # 여러 페이지 요청은 페이지별 overlay 편집값을 받아 덮어쓰지 않는다
    assert all(item[1] is None and item[2] is False for item in called)
    done = {e["image_id"] for e in events if e.get("type") == "image_status" and e.get("status") == "done"}
    assert done == {"p1", "p2"}
    assert events[-1]["type"] == "complete" and events[-1]["ok"] is True


def test_batch_with_unknown_or_empty_ids_does_nothing(tmp_path, monkeypatch):
    _session_with_pages(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "run_final_for_item", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    client = TestClient(app.create_app())
    for ids in ([], ["nope"]):
        response = client.post("/api/run-final-stream", json={"session_id": "s1", "image_id": "batch", "image_ids": ids})
        events = _events(response)
        assert events[-1]["type"] == "complete" and events[-1]["ok"] is True
        assert any("OCR/번역할 이미지가 없습니다" in e.get("message", "") for e in events)
