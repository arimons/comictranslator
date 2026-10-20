# Step 4 OCR/번역 모델 선택과 provider 분기 검사
from __future__ import annotations

import pytest

from pipeline import gemini_ocr_translate_v5 as ocr


@pytest.mark.parametrize("model_name", ["gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-terra"])
def test_resolve_provider_maps_openai(model_name):
    assert ocr.resolve_provider(model_name) == "openai"


@pytest.mark.parametrize("model_name", ["gemini-3-flash-preview", "claude-opus-5", ""])
def test_resolve_provider_rejects_non_openai(model_name):
    with pytest.raises(ValueError): ocr.resolve_provider(model_name)


def test_default_and_explicit_model_dispatch_to_openai(monkeypatch):
    seen = []
    def fake(image_path, prompt, temperature, model_name, max_retries=4):
        seen.append(model_name)
        return {"regions": []}, 0
    monkeypatch.setattr(ocr, "_run_openai_json", fake)
    ocr._run_model_json("sheet.jpg", "JSON prompt", 0.2)
    ocr._run_model_json("sheet.jpg", "JSON prompt", 0.2, model_name="gpt-6-luna")
    assert seen == ["gpt-6.1-sol", "gpt-6-luna"]
    with pytest.raises(ValueError): ocr._run_model_json("sheet.jpg", "prompt", 0.2, model_name="gemini-3-flash-preview")


def test_require_existing_openai_key_only(monkeypatch):
    monkeypatch.setattr(ocr, "OPENAI_API_KEY", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"): ocr._require_api_key("openai")
    with pytest.raises(ValueError): ocr._require_api_key("gemini")


def test_pipeline_runner_forwards_model_flag() -> None:
    """pipeline_runner가 ocr_model을 Step 4 subprocess 인자로 넘겨야 한다."""
    import inspect

    from pipeline import pipeline_runner

    source = inspect.getsource(pipeline_runner.run_single_image)
    assert '"--model", ocr_model' in source, "Step 4 호출에 --model 전달이 없습니다"

    # 두 진입점 모두 ocr_model 파라미터를 노출해야 한다.
    assert "ocr_model" in inspect.signature(pipeline_runner.run_single_image).parameters
    assert "ocr_model" in inspect.signature(pipeline_runner.run_batch_images).parameters


def test_main_v2_exposes_ocr_model_endpoint() -> None:
    from fastapi.testclient import TestClient

    import main_v2

    response = TestClient(main_v2.create_app()).get("/api/ocr-models")
    assert response.status_code == 200
    body = response.json()
    assert body["default"] == ocr.MODEL_NAME
    ids = [m["id"] for m in body["models"]]
    assert ocr.MODEL_NAME in ids
    for model_id in ids:
        ocr.resolve_provider(model_id)  # 목록의 모든 모델이 provider 판별을 통과해야 한다


def test_main_v2_forwards_ocr_model_to_step4() -> None:
    """main_v2가 요청의 ocr_model을 Step 4 subprocess 인자로 넘겨야 한다."""
    import inspect

    import main_v2

    source = inspect.getsource(main_v2.run_final_for_item)
    assert '"--model", ocr_model' in source, "Step 4 호출에 --model 전달이 없습니다"
    assert "ocr_model" in inspect.signature(main_v2.run_final_for_item).parameters
    assert "ocr_model" in main_v2.RunFinalRequest.model_fields
