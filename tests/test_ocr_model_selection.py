# Step 4 OCR/번역 모델 선택과 provider 분기 검사
from __future__ import annotations

import pytest

from pipeline import gemini_ocr_translate_v5 as ocr


@pytest.mark.parametrize(
    "model_name,expected",
    [
        ("gemini-3-flash-preview", "gemini"),
        ("gemini-3.6-flash", "gemini"),
        ("gpt-5.6-luna", "openai"),
        ("gpt-5.6-terra", "openai"),
    ],
)
def test_resolve_provider_maps_known_prefixes(model_name: str, expected: str) -> None:
    assert ocr.resolve_provider(model_name) == expected


@pytest.mark.parametrize("model_name", ["claude-opus-5", "llama-3", "", "flash"])
def test_resolve_provider_rejects_unknown(model_name: str) -> None:
    with pytest.raises(ValueError):
        ocr.resolve_provider(model_name)


def test_run_model_json_dispatches_to_gemini(monkeypatch) -> None:
    seen = {}

    def fake_gemini(image_path, prompt, temperature, max_retries=4, model_name=None):
        seen["provider"] = "gemini"
        seen["model"] = model_name
        return {"regions": []}, 0.0

    def fake_openai(*args, **kwargs):
        seen["provider"] = "openai"
        return {"regions": []}, 0.0

    monkeypatch.setattr(ocr, "_run_gemini_json", fake_gemini)
    monkeypatch.setattr(ocr, "_run_openai_json", fake_openai)

    ocr._run_model_json("sheet.jpg", "prompt", 0.2, model_name="gemini-3-flash-preview")
    assert seen["provider"] == "gemini"
    assert seen["model"] == "gemini-3-flash-preview"


def test_run_model_json_dispatches_to_openai(monkeypatch) -> None:
    seen = {}

    def fake_gemini(*args, **kwargs):
        seen["provider"] = "gemini"
        return {"regions": []}, 0.0

    def fake_openai(image_path, prompt, temperature, model_name, max_retries=4):
        seen["provider"] = "openai"
        seen["model"] = model_name
        return {"regions": []}, 0.0

    monkeypatch.setattr(ocr, "_run_gemini_json", fake_gemini)
    monkeypatch.setattr(ocr, "_run_openai_json", fake_openai)

    ocr._run_model_json("sheet.jpg", "prompt", 0.2, model_name="gpt-5.6-luna")
    assert seen["provider"] == "openai"
    assert seen["model"] == "gpt-5.6-luna"


def test_run_model_json_falls_back_to_module_default(monkeypatch) -> None:
    seen = {}

    def fake_gemini(image_path, prompt, temperature, max_retries=4, model_name=None):
        seen["model"] = model_name
        return {"regions": []}, 0.0

    monkeypatch.setattr(ocr, "_run_gemini_json", fake_gemini)
    monkeypatch.setattr(ocr, "MODEL_NAME", "gemini-3-flash-preview")

    ocr._run_model_json("sheet.jpg", "prompt", 0.2)
    assert seen["model"] == "gemini-3-flash-preview"


def test_require_api_key_reports_missing_provider_key(monkeypatch) -> None:
    monkeypatch.setattr(ocr, "OPENAI_API_KEY", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ocr._require_api_key("openai")

    monkeypatch.setattr(ocr, "GEMINI_API_KEY", None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        ocr._require_api_key("gemini")


def test_pipeline_runner_forwards_model_flag() -> None:
    """pipeline_runner가 ocr_model을 Step 4 subprocess 인자로 넘겨야 한다."""
    import inspect

    from pipeline import pipeline_runner

    source = inspect.getsource(pipeline_runner.run_single_image)
    assert '"--model", ocr_model' in source, "Step 4 호출에 --model 전달이 없습니다"

    # 두 진입점 모두 ocr_model 파라미터를 노출해야 한다.
    assert "ocr_model" in inspect.signature(pipeline_runner.run_single_image).parameters
    assert "ocr_model" in inspect.signature(pipeline_runner.run_batch_images).parameters
