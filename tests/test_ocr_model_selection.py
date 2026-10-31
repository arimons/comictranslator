# Step 4 OCR/번역 모델 선택과 provider 분기 검사
from __future__ import annotations

import pytest

from pipeline import gemini_ocr_translate_v5 as ocr


@pytest.mark.parametrize("model_name", ["gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-terra"])
def test_resolve_provider_maps_openai(model_name):
    assert ocr.resolve_provider(model_name) == "openai"


@pytest.mark.parametrize("model_name", ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
def test_resolve_provider_maps_gemini(model_name):
    assert ocr.resolve_provider(model_name) == "gemini"


@pytest.mark.parametrize("model_name", ["claude-opus-5", ""])
def test_resolve_provider_rejects_unknown(model_name):
    with pytest.raises(ValueError): ocr.resolve_provider(model_name)


def test_supported_models_use_confirmed_api_ids():
    ids = [m["id"] for m in ocr.SUPPORTED_OCR_MODELS]
    assert ids == ["gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-luna", "gemini-3.8-flash", "gemini-3.5-flash-lite"]
    assert ocr.MODEL_NAME == "gpt-6.1-terra"
    assert all(ocr.resolve_provider(m["id"]) == m["provider"] for m in ocr.SUPPORTED_OCR_MODELS)


def test_default_and_explicit_model_dispatch_by_provider(monkeypatch):
    seen = []
    def fake_openai(image_path, prompt, temperature, model_name, max_retries=4):
        seen.append(("openai", model_name)); return {"regions": []}, 0
    def fake_gemini(image_path, prompt, temperature, model_name, max_retries=4):
        seen.append(("gemini", model_name)); return {"regions": []}, 0
    monkeypatch.setattr(ocr, "_run_openai_json", fake_openai)
    monkeypatch.setattr(ocr, "_run_gemini_json", fake_gemini)
    ocr._run_model_json("sheet.jpg", "JSON prompt", 0.2)
    ocr._run_model_json("sheet.jpg", "JSON prompt", 0.2, model_name="gpt-6-luna")
    ocr._run_model_json("sheet.jpg", "JSON prompt", 0.2, model_name="gemini-3.8-flash")
    assert seen == [("openai", "gpt-6.1-terra"), ("openai", "gpt-6-luna"), ("gemini", "gemini-3.8-flash")]
    with pytest.raises(ValueError): ocr._run_model_json("sheet.jpg", "prompt", 0.2, model_name="claude-opus-5")


def test_require_api_key_per_provider(monkeypatch):
    monkeypatch.setattr(ocr, "OPENAI_API_KEY", None)
    monkeypatch.setattr(ocr, "GEMINI_API_KEY", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"): ocr._require_api_key("openai")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"): ocr._require_api_key("gemini")
    with pytest.raises(ValueError): ocr._require_api_key("anthropic")
    monkeypatch.setattr(ocr, "GEMINI_API_KEY", "fixture-key")
    assert ocr._require_api_key("gemini") == "fixture-key"


def test_gemini_request_shape_without_network(monkeypatch, tmp_path):
    """이미지는 medium 해상도 Part로, 응답은 JSON mime·thinking low로 요청한다."""
    pytest.importorskip("google.genai")
    from types import SimpleNamespace
    sheet = tmp_path / "sheet.jpg"; sheet.write_bytes(b"jpeg-bytes")
    calls = []
    class FakeModels:
        def generate_content(self, **kw):
            calls.append(kw); return SimpleNamespace(text='{"regions": []}')
    monkeypatch.setattr(ocr, "GEMINI_API_KEY", "fixture-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace(models=FakeModels()))
    result, _ = ocr._run_gemini_json(str(sheet), "prompt", 0.2, "gemini-3.5-flash-lite")
    assert result == {"regions": []}
    kw = calls[0]
    assert kw["model"] == "gemini-3.5-flash-lite"
    assert kw["config"].response_mime_type == "application/json"
    assert kw["config"].thinking_config.thinking_level.value == "LOW"
    parts = kw["contents"][0].parts
    assert parts[0].text == "prompt" and parts[1].inline_data.data == b"jpeg-bytes"
    assert parts[1].media_resolution.level.value == "MEDIA_RESOLUTION_MEDIUM"


def test_gemini_retries_rate_limit_then_raises_other_errors(monkeypatch, tmp_path):
    pytest.importorskip("google.genai")
    from types import SimpleNamespace
    sheet = tmp_path / "sheet.jpg"; sheet.write_bytes(b"x")
    class Err(Exception):
        def __init__(self, code): self.code = code
    outcomes = [Err(429), SimpleNamespace(text='{"regions": []}')]
    class FakeModels:
        def generate_content(self, **kw):
            item = outcomes.pop(0)
            if isinstance(item, Exception): raise item
            return item
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(ocr, "GEMINI_API_KEY", "fixture-key")
    monkeypatch.setattr(ocr.time, "sleep", lambda s: None)
    assert ocr._run_gemini_json(str(sheet), "p", 0.2, "gemini-3.8-flash")[0] == {"regions": []}
    outcomes[:] = [Err(400)]
    with pytest.raises(Err): ocr._run_gemini_json(str(sheet), "p", 0.2, "gemini-3.8-flash")


def test_pipeline_runner_forwards_model_flag() -> None:
    """pipeline_runner가 ocr_model을 Step 4 subprocess 인자로 넘겨야 한다."""
    import inspect

    from pipeline import pipeline_runner

    source = inspect.getsource(pipeline_runner.run_single_image)
    assert '"--model", ocr_model' in source, "Step 4 호출에 --model 전달이 없습니다"

    # 두 진입점 모두 ocr_model 파라미터를 노출해야 한다.
    assert "ocr_model" in inspect.signature(pipeline_runner.run_single_image).parameters
    assert "ocr_model" in inspect.signature(pipeline_runner.run_batch_images).parameters


def test_app_exposes_ocr_model_endpoint() -> None:
    from fastapi.testclient import TestClient

    import app

    response = TestClient(app.create_app()).get("/api/ocr-models")
    assert response.status_code == 200
    body = response.json()
    assert body["default"] == ocr.MODEL_NAME
    ids = [m["id"] for m in body["models"]]
    assert ocr.MODEL_NAME in ids
    for model_id in ids:
        ocr.resolve_provider(model_id)  # 목록의 모든 모델이 provider 판별을 통과해야 한다


def test_app_forwards_ocr_model_to_step4() -> None:
    """app가 요청의 ocr_model을 Step 4 subprocess 인자로 넘겨야 한다."""
    import inspect

    import app

    source = inspect.getsource(app.run_final_for_item)
    assert '"--model", ocr_model' in source, "Step 4 호출에 --model 전달이 없습니다"
    assert "ocr_model" in inspect.signature(app.run_final_for_item).parameters
    assert "ocr_model" in app.RunFinalRequest.model_fields
