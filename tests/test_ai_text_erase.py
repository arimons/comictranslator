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
    assert job["meta"]["params"]["quality"] == "medium"  # gpt-image 글자 지우기 기본

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


# ── 영역 모드(선택한 영역만 AI로 지우기) ─────────────────────────────────────

def test_region_context_rect_meets_model_minimum_and_stays_inside_page():
    from pipeline.ai_erase_region import ERASE_MIN_PIXELS, context_rect

    x, y, w, h = context_rect([1449, 1013, 251, 408], 1920, 2160)
    assert w * h >= ERASE_MIN_PIXELS
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 2160
    # 좁고 긴 영역도 비율 한도(2.8)를 넘지 않게 넓힌다
    cx, cy, cw, ch = context_rect([100, 100, 40, 700], 1920, 2160)
    assert ch <= cw * 2.8 + 1


def test_region_patch_mask_covers_only_strokes_inside_region_rect():
    from pipeline.ai_erase_region import region_patch_mask

    page = np.full((300, 300, 3), 255, np.uint8)
    page[100:140, 100:112] = 0   # 영역 안 글자 획
    page[200:240, 200:212] = 0   # 영역 밖 글자 획(다른 영역)
    mask = region_patch_mask(page, [80, 80, 100, 100], [0, 0, 300, 300])
    assert mask[100:140, 100:112].min() == 255
    assert mask[200:240, 200:212].max() == 0


def test_stale_patch_ids_only_flags_patches_whose_region_is_gone():
    from pipeline.ai_erase_region import stale_patch_ids

    patches = [
        {"id": "a", "engine": "ai-erase", "region_rect": [100, 100, 50, 80]},
        {"id": "b", "engine": "ai-erase", "region_rect": [400, 400, 50, 80]},
        {"id": "page", "engine": "ai-erase"},                    # 쪽 전체 패치는 대상 아님
        {"id": "lama", "engine": "lama", "region_rect": [400, 400, 50, 80]},
    ]
    # a의 영역은 모양만 조금 바뀌어 남아 있고, b의 영역은 사라졌다
    assert stale_patch_ids(patches, [[110, 105, 60, 90]]) == ["b"]


def _fake_erase_factory():
    def fake_erase(image, work_dir, *, quality="medium", **_kw):
        out = image.copy()
        out[:] = 90
        return out, {"model": ate.ERASE_MODEL, "quality": quality, "request_size": [160, 208], "elapsed_sec": 1.0,
                     "input_tokens": 10, "output_tokens": 20, "estimated_cost_usd": 0.01}
    return fake_erase


def _write_regions(record, regions):
    from pathlib import Path
    import json
    Path(record["regions_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["regions_json"]).write_text(json.dumps({"regions": regions}), encoding="utf-8")


def test_region_ai_erase_replaces_only_same_region_patch_and_prunes_deleted_regions(tmp_path, monkeypatch):
    img_path = _session_with_image(tmp_path, monkeypatch)
    page = np.full((200, 160, 3), 255, np.uint8)
    page[40:70, 30:40] = 0
    page[120:150, 100:110] = 0
    cv2.imwrite(str(img_path), page)
    client = TestClient(app.create_app())
    monkeypatch.setattr(ate, "erase_page_text", _fake_erase_factory())
    record = app._restore_record("s1", "p1")
    _write_regions(record, [
        {"region_id": 1, "region_instance_ref": "auto|tg:1", "unboxed_rect": [20, 30, 40, 50]},
        {"region_id": 2, "region_instance_ref": "auto|tg:2", "unboxed_rect": [90, 110, 40, 50]},
    ])

    def erase_region(region_id):
        started = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "region_id": region_id})
        assert started.status_code == 200, started.text
        job = _wait_job(client, started.json()["job_id"])
        assert job["status"] == "done", job
        assert job["meta"]["region_id"] == region_id and job["meta"]["region_ref"] == f"auto|tg:{region_id}"
        info = client.post("/api/restore/accept", json={"session_id": "s1", "image_id": "p1", "job_id": job["job_id"]}).json()
        return info["patches"]

    assert len(erase_region(1)) == 1
    assert len(erase_region(2)) == 2
    patches = erase_region(1)  # 같은 영역을 다시 지우면 그 영역 패치만 바뀐다
    assert sorted(p["region_ref"] for p in patches) == ["auto|tg:1", "auto|tg:2"]

    # 영역 2가 지워진 뒤 재분석: 영역 2의 패치만 정리되고 AI는 다시 부르지 않는다
    _write_regions(record, [{"region_id": 1, "region_instance_ref": "auto|tg:1", "unboxed_rect": [20, 30, 40, 50]}])
    removed = app.prune_stale_region_ai_patches(record)
    assert len(removed) == 1
    left = app._restore_state(record)["patches"]
    assert [p["region_ref"] for p in left] == ["auto|tg:1"]


def test_region_ai_erase_unknown_region_is_404(tmp_path, monkeypatch):
    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())
    _write_regions(app._restore_record("s1", "p1"), [])
    res = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "region_id": 9})
    assert res.status_code == 404
