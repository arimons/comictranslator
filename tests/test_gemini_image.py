# Gemini 이미지 모델 경로(글자 지우기·통번역 재생성)를 검증한다 (모델 호출은 가짜).
import base64
import types

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import app
from pipeline import ai_text_erase as ate
from pipeline import gemini_image as gi
from pipeline import openai_image_redraw as redraw
from tests.test_background_restore import _session_with_image, _wait_job


class FakeInteractions:
    """요청 이미지를 받은 비율 그대로 두 배 크기로 키워 회색으로 칠해 돌려준다. 요청을 기록한다."""

    def __init__(self, out_size=None):
        self.calls = []
        self.out_size = out_size

    def create(self, *, model, input, response_format):
        self.calls.append({"model": model, "response_format": response_format})
        arr = cv2.imdecode(np.frombuffer(base64.b64decode(input[1]["data"]), np.uint8), cv2.IMREAD_COLOR)
        h, w = arr.shape[:2]
        out = np.full((h * 2, w * 2, 3), 128, np.uint8)
        if self.out_size:
            out = cv2.resize(out, self.out_size)
        ok, buf = cv2.imencode(".jpg", out)
        return types.SimpleNamespace(
            output_image=types.SimpleNamespace(data=base64.b64encode(buf.tobytes()).decode()),
            usage=types.SimpleNamespace(total_input_tokens=1000, total_output_tokens=1680, total_thought_tokens=0),
        )


def _client(interactions):
    return types.SimpleNamespace(interactions=interactions)


def test_aspect_ratio_picks_least_padding():
    assert gi.fit_aspect_ratio(1350, 1920) == ("2:3", 1350, 2025)  # 아래로 105px만 덧댄다
    assert gi.fit_aspect_ratio(1000, 1000) == ("1:1", 1000, 1000)


def test_image_model_follows_ocr_provider():
    assert gi.image_model_for("gemini-3.8-flash", "gpt-image-2.5-sunburst") == gi.GEMINI_IMAGE_MODEL
    assert gi.image_model_for("gpt-6.1-sol", "gpt-image-2.5-sunburst") == "gpt-image-2.5-sunburst"
    assert gi.image_model_for(None, "gpt-image-2.5-sunburst") == "gpt-image-2.5-sunburst"


def test_erase_with_gemini_model_crops_back_to_original(tmp_path):
    page = np.full((1920, 1350, 3), 255, np.uint8)
    fake = FakeInteractions()

    erased, meta = ate.erase_page_text(page, tmp_path, model=gi.GEMINI_IMAGE_MODEL, client=_client(fake))

    fmt = fake.calls[0]["response_format"]
    assert fake.calls[0]["model"] == gi.GEMINI_IMAGE_MODEL
    assert fmt["aspect_ratio"] == "2:3" and fmt["image_size"] == "2K" and fmt["mime_type"] == "image/jpeg"
    assert erased.shape == page.shape
    assert abs(int(erased.mean()) - 128) <= 2
    assert meta["request_size"] == [1350, 2025]
    assert meta["estimated_cost_usd"] == pytest.approx(1000 * 1.5e-6 + 1680 * 30e-6, abs=1e-4)


def test_gemini_response_with_other_ratio_is_rejected(tmp_path):
    page = np.full((1920, 1350, 3), 255, np.uint8)
    with pytest.raises(ate.AiEraseError, match="다른 응답"):
        ate.erase_page_text(page, tmp_path, model=gi.GEMINI_IMAGE_MODEL, client=_client(FakeInteractions(out_size=(1024, 1024))))


def test_ai_erase_job_uses_gemini_when_session_ocr_is_gemini(tmp_path, monkeypatch):
    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())
    seen = {}

    def fake_erase(page, work_dir, *, model, quality, **_kw):
        seen["model"] = model
        return page.copy(), {"model": model, "quality": quality, "request_size": [160, 240], "elapsed_sec": 1.0,
                             "input_tokens": 1, "output_tokens": 1, "estimated_cost_usd": 0.05}

    monkeypatch.setattr(ate, "erase_page_text", fake_erase)
    started = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "ocr_model": "gemini-3.8-flash"}).json()
    job = _wait_job(client, started["job_id"])
    assert job["status"] == "done", job
    assert seen["model"] == gi.GEMINI_IMAGE_MODEL
    assert job["meta"]["params"]["quality"] == "low"  # Gemini 글자 지우기 기본은 1K
    assert job["meta"]["params"]["model"] == gi.GEMINI_IMAGE_MODEL

    models = client.get("/api/ocr-models").json()
    assert models["image_models"]["gemini"] == gi.GEMINI_IMAGE_MODEL


def test_redraw_with_gemini_model_writes_original_size(tmp_path, monkeypatch):
    src = tmp_path / "page.png"
    cv2.imwrite(str(src), np.full((1920, 1350, 3), 255, np.uint8))
    out = tmp_path / "final.jpg"
    fake = FakeInteractions()
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gi, "_client", lambda client: _client(fake))

    ok = redraw.redraw_image_with_openai(src, out, model_name=gi.GEMINI_IMAGE_MODEL, analyze_first=False)

    assert ok
    assert fake.calls[0]["model"] == gi.GEMINI_IMAGE_MODEL
    assert cv2.imread(str(out)).shape == (1920, 1350, 3)


def test_cost_prices_only_image_modality_at_image_rate(tmp_path):
    fake = FakeInteractions()
    original = fake.create

    def create(**kw):
        response = original(**kw)
        response.usage = types.SimpleNamespace(
            total_input_tokens=1200, total_output_tokens=1700, total_thought_tokens=580,
            output_tokens_by_modality=[types.SimpleNamespace(modality="image", tokens=1120),
                                       types.SimpleNamespace(modality="text", tokens=580)],
        )
        return response

    fake.create = create
    page = np.full((1080, 960, 3), 255, np.uint8)
    _, meta = gi.edit_image(page, "erase", tmp_path, quality="low", client=_client(fake))

    assert meta["image_tokens"] == 1120
    # 이미지 1,120은 이미지 단가, 텍스트 580 + 생각 580은 텍스트 단가
    assert meta["estimated_cost_usd"] == pytest.approx(1200 * 1.5e-6 + 1120 * 30e-6 + 1160 * 7.5e-6, abs=1e-4)


def test_default_client_requires_pinned_sdk_internals(monkeypatch):
    """SDK 내부 속성이 사라지면 AttributeError 대신 원인을 알려 주는 오류를 낸다."""
    from types import SimpleNamespace

    from google import genai

    monkeypatch.setenv("GEMINI_API_KEY", "fixture-key")
    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace())
    with pytest.raises(gi.GeminiImageError, match="고정 버전"):
        gi._client(None)
