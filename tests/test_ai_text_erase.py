# 이미지 편집 모델로 페이지 글자를 지우는 흐름을 검증한다 (모델 호출은 가짜).
import base64
import types

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import app
from pipeline import ai_text_erase as ate
from tests.test_background_restore import _session_with_image, _wait_job


class FakeImages:
    """요청 이미지를 그대로 돌려주되 흰 페이지를 회색으로 칠한다. 요청 크기를 기록한다."""

    def __init__(self, size_override=None):
        self.calls = []
        self.size_override = size_override

    def edit(self, *, model, image, prompt, size, quality, output_format):
        self.calls.append({"model": model, "size": size, "quality": quality})
        data = image[0].read()
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        arr[:] = 128
        if self.size_override:
            arr = cv2.resize(arr, self.size_override)
        ok, buf = cv2.imencode(".png", arr)
        return types.SimpleNamespace(
            data=[types.SimpleNamespace(b64_json=base64.b64encode(buf.tobytes()).decode())],
            usage=types.SimpleNamespace(input_tokens=1564, output_tokens=471),
        )


def _client(images):
    return types.SimpleNamespace(images=images)


def test_page_is_padded_to_multiple_of_16_and_cropped_back(tmp_path):
    page = np.full((1920, 1350, 3), 255, np.uint8)
    images = FakeImages()

    erased, meta = ate.erase_page_text(page, tmp_path, client=_client(images))

    assert images.calls[0]["size"] == "1360x1920"  # 비율을 바꾸지 않고 오른쪽만 10px 덧댐
    assert images.calls[0]["model"] == ate.ERASE_MODEL
    assert erased.shape == page.shape
    assert meta["request_size"] == [1360, 1920]
    assert meta["estimated_cost_usd"] == pytest.approx(1564 * 8e-6 + 471 * 30e-6, abs=1e-4)


def test_default_client_disables_sdk_retries(tmp_path, monkeypatch):
    """자동 재시도가 켜져 있으면 실패한 이미지 편집 요청이 다시 나가 이중 과금될 수 있다."""
    import openai

    created = {}
    images = FakeImages()

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.images = images

    monkeypatch.setattr(openai, "OpenAI", RecordingOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    ate.erase_page_text(np.full((1920, 1350, 3), 255, np.uint8), tmp_path)
    assert created["max_retries"] == 0
    assert len(images.calls) == 1


def test_response_with_different_size_is_rejected(tmp_path):
    page = np.full((1920, 1350, 3), 255, np.uint8)
    with pytest.raises(ate.AiEraseError, match="다른 응답"):
        ate.erase_page_text(page, tmp_path, client=_client(FakeImages(size_override=(1024, 1536))))


def test_too_small_page_is_rejected_before_calling(tmp_path):
    images = FakeImages()
    with pytest.raises(ate.AiEraseError, match="너무 작습니다"):
        ate.erase_page_text(np.zeros((200, 160, 3), np.uint8), tmp_path, client=_client(images))
    assert images.calls == []


def test_ai_erase_job_accepts_as_full_page_patch(tmp_path, monkeypatch):
    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())

    def fake_erase(page, work_dir, *, quality="medium", **_kw):
        out = page.copy()
        out[:] = 90
        return out, {"model": ate.ERASE_MODEL, "quality": quality, "request_size": [160, 208], "elapsed_sec": 1.0,
                     "input_tokens": 10, "output_tokens": 20, "estimated_cost_usd": 0.001}

    monkeypatch.setattr(ate, "erase_page_text", fake_erase)
    started = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1"}).json()
    job = _wait_job(client, started["job_id"])
    assert job["status"] == "done", job
    assert job["meta"]["context_rect"] == [0, 0, 160, 200]

    info = client.post("/api/restore/accept", json={"session_id": "s1", "image_id": "p1", "job_id": job["job_id"]}).json()
    assert len(info["patches"]) == 1 and info["patches"][0]["engine"] == "ai-erase"
    assert info["patches"][0]["usage"]["estimated_cost_usd"] == 0.001

    # 확정 뒤 분석 탭 미리보기 URL이 생기고, 렌더가 쓰는 패치 합성이 페이지 전체를 지운 배경으로 바꾼다
    record = app._restore_record("s1", "p1")
    assert record["restore_preview_url"]
    from pathlib import Path
    from pipeline.background_restore import apply_restore_patches
    base = np.full((200, 160, 3), 255, np.uint8)
    assert apply_restore_patches(base, Path(record["restore_state_json"])) == 1
    assert (base == 90).all()


def test_ai_erase_rejects_unknown_quality(tmp_path, monkeypatch):
    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())
    res = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "quality": "ultra"})
    assert res.status_code == 400
