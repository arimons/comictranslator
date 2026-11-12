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


def test_page_ai_erase_regions_scope_only_replaces_strokes_inside_regions(tmp_path, monkeypatch):
    from pathlib import Path
    from pipeline.background_restore import apply_restore_patches

    img_path = _session_with_image(tmp_path, monkeypatch)
    page = np.full((200, 160, 3), 255, np.uint8)
    page[40:70, 30:40] = 0       # 영역 안 대사 글자
    page[140:170, 100:110] = 0   # 영역 밖 효과음 글자
    cv2.imwrite(str(img_path), page)
    client = TestClient(app.create_app())
    monkeypatch.setattr(ate, "erase_page_text", _fake_erase_factory())
    record = app._restore_record("s1", "p1")
    _write_regions(record, [{"region_id": 1, "region_instance_ref": "auto|tg:1", "unboxed_rect": [20, 30, 40, 50]}])

    bad = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "scope": "everything"})
    assert bad.status_code == 400
    started = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "scope": "regions"}).json()
    job = _wait_job(client, started["job_id"])
    assert job["status"] == "done" and job["meta"]["scope"] == "regions"
    info = client.post("/api/restore/accept", json={"session_id": "s1", "image_id": "p1", "job_id": job["job_id"]}).json()
    assert info["patches"][0]["scope"] == "regions" and info["patches"][0].get("region_id") is None

    out = page.copy()
    assert apply_restore_patches(out, Path(record["restore_state_json"])) == 1
    assert (out[40:70, 30:40] == 90).all()      # 영역 안 글자 자리는 지워진 배경
    assert (out[140:170, 100:110] == 0).all()   # 영역 밖 효과음은 원본 그대로
    assert (out[0:10, 0:10] == 255).all()        # 글자 없는 곳도 그대로


# ── 효과음 찾기(후보 변환과 API) ───────────────────────────────────────────────

def test_sfx_candidates_convert_pad_and_filter():
    from pipeline.sfx_locator import items_to_candidates

    items = [
        {"text": "グッ", "box": [500, 500, 600, 700]},          # 정상
        {"text": "대사", "box": [100, 100, 200, 200]},           # 이미 영역과 겹침
        {"text": "깨짐", "box": ["a", 1, 2, 3]},                  # 잘못된 좌표
        {"text": "점", "box": [10, 10, 11, 11]},                  # 너무 작음
        {"text": "거꾸로", "box": [900, 900, 800, 800]},          # 좌표 순서가 뒤집혀도 받아 줌
        "문자열",                                                  # 잘못된 항목
    ]
    existing = [[95, 95, 120, 120]]                                # 1000x1000 쪽에서 100,100~200,200
    cands = items_to_candidates(items, 1000, 1000, existing)
    assert [c["text"] for c in cands] == ["グッ", "거꾸로"]
    first = cands[0]["rect"]
    assert first[0] < 500 and first[1] < 500 and first[0] + first[2] > 600 and first[1] + first[3] > 700  # 사방으로 넓어짐
    assert all(0 <= v for c in cands for v in c["rect"][:2])


def test_sfx_locate_api_returns_candidates_and_skips_existing_regions(tmp_path, monkeypatch):
    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())
    _write_regions(app._restore_record("s1", "p1"), [{"region_id": 1, "unboxed_rect": [10, 10, 40, 40]}])
    import pipeline.gemini_ocr_translate_v5 as ocr

    seen = {}
    def fake_run(image_path, prompt, temperature, model_name=None, max_retries=4):
        seen["model"] = model_name
        return {"items": [{"text": "ドン", "box": [500, 500, 800, 800]}, {"text": "대사", "box": [60, 50, 250, 200]}]}, 0.5
    monkeypatch.setattr(ocr, "_run_model_json", fake_run)
    res = client.post("/api/sfx/locate", json={"session_id": "s1", "image_id": "p1", "ocr_model": "vertex:gemini-3.8-flash"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert seen["model"] == "vertex:gemini-3.8-flash"
    assert [c["text"] for c in body["items"]] == ["ドン"]   # 이미 영역인 "대사"는 빠진다
    assert len(body["items"][0]["rect"]) == 4


def test_sfx_locate_api_reports_model_errors_as_502(tmp_path, monkeypatch):
    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())
    _write_regions(app._restore_record("s1", "p1"), [])
    import pipeline.gemini_ocr_translate_v5 as ocr

    def boom(*_a, **_k):
        raise RuntimeError("키가 없습니다")
    monkeypatch.setattr(ocr, "_run_model_json", boom)
    res = client.post("/api/sfx/locate", json={"session_id": "s1", "image_id": "p1"})
    assert res.status_code == 502 and "키가 없습니다" in res.json()["detail"]


def test_page_ai_erase_with_sfx_bundles_locate_and_erases_found_boxes(tmp_path, monkeypatch):
    from pathlib import Path
    from pipeline.background_restore import apply_restore_patches
    import pipeline.sfx_locator as locator

    img_path = _session_with_image(tmp_path, monkeypatch)
    page = np.full((200, 160, 3), 255, np.uint8)
    page[40:70, 30:40] = 0        # 영역 안 대사 글자
    page[140:170, 100:110] = 0    # 영역 밖 효과음 글자
    cv2.imwrite(str(img_path), page)
    client = TestClient(app.create_app())
    monkeypatch.setattr(ate, "erase_page_text", _fake_erase_factory())
    record = app._restore_record("s1", "p1")
    _write_regions(record, [{"region_id": 1, "region_instance_ref": "auto|tg:1", "unboxed_rect": [20, 30, 40, 50]}])
    seen = {}

    def fake_locate(image_path, model_name, existing_rects=None):
        seen["existing"] = existing_rects
        return {"model": model_name, "elapsed_sec": 0.1, "items": [{"text": "ドン", "rect": [90, 130, 30, 50]}]}

    monkeypatch.setattr(locator, "locate_sfx", fake_locate)
    started = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "scope": "regions", "include_sfx": True}).json()
    job = _wait_job(client, started["job_id"])
    assert job["status"] == "done", job
    assert seen["existing"] == [[20, 30, 40, 50]]                # 이미 영역인 곳은 후보에서 빼도록 넘긴다
    assert job["meta"]["sfx_items"] == [{"text": "ドン", "rect": [90, 130, 30, 50]}] and job["meta"]["sfx_count"] == 1
    info = client.post("/api/restore/accept", json={"session_id": "s1", "image_id": "p1", "job_id": job["job_id"]}).json()
    assert info["patches"][0]["sfx_count"] == 1

    out = page.copy()
    assert apply_restore_patches(out, Path(record["restore_state_json"])) == 1
    assert (out[40:70, 30:40] == 90).all()          # 대사는 지워짐
    assert (out[140:170, 100:110] == 90).all()       # 찾은 효과음 박스 안의 굵은 획 자리는 지워진 배경
    assert (out[0:10, 0:10] == 255).all()            # 나머지는 그대로


def test_page_ai_erase_with_sfx_keeps_going_when_locate_fails(tmp_path, monkeypatch):
    import pipeline.sfx_locator as locator

    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())
    monkeypatch.setattr(ate, "erase_page_text", _fake_erase_factory())
    _write_regions(app._restore_record("s1", "p1"), [{"region_id": 1, "unboxed_rect": [20, 30, 40, 50]}])

    def boom(*_a, **_k):
        raise RuntimeError("모델 키가 없습니다")

    monkeypatch.setattr(locator, "locate_sfx", boom)
    started = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "scope": "regions", "include_sfx": True}).json()
    job = _wait_job(client, started["job_id"])
    assert job["status"] == "done", job                          # 지우기는 계속 진행
    assert job["meta"]["sfx_items"] == [] and "모델 키가 없습니다" in job["meta"]["sfx_error"]


def test_page_ai_erase_with_sfx_adds_sfx_instruction_to_the_prompt(tmp_path, monkeypatch):
    import pipeline.sfx_locator as locator

    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(app.create_app())
    prompts = []

    def fake_erase(image, work_dir, *, quality="medium", prompt=None, **_kw):
        prompts.append(prompt)
        out = image.copy(); out[:] = 90
        return out, {"model": ate.ERASE_MODEL, "quality": quality, "request_size": [160, 208], "elapsed_sec": 1.0,
                     "input_tokens": 10, "output_tokens": 20, "estimated_cost_usd": 0.001}

    monkeypatch.setattr(ate, "erase_page_text", fake_erase)
    _write_regions(app._restore_record("s1", "p1"), [{"region_id": 1, "unboxed_rect": [20, 30, 40, 50]}])
    monkeypatch.setattr(locator, "locate_sfx", lambda *_a, **_k: {"items": [{"text": "グッ", "rect": [80, 120, 40, 60]}]})

    def run(include_sfx):
        started = client.post("/api/restore/ai-erase", json={"session_id": "s1", "image_id": "p1", "scope": "regions", "include_sfx": include_sfx}).json()
        assert _wait_job(client, started["job_id"])["status"] == "done"

    run(True)
    assert prompts[-1] and "sound-effect" in prompts[-1] and "グッ" in prompts[-1]   # 기본 프롬프트는 효과음을 남기므로 지시를 덧붙인다
    run(False)
    assert prompts[-1] is None                                                      # 묶지 않으면 기본 프롬프트 그대로


def test_sfx_stroke_mask_covers_thick_strokes_not_halftone_dots_and_falls_back_to_the_box():
    from pipeline.ai_erase_region import sfx_stroke_mask

    page = np.full((300, 300, 3), 255, np.uint8)
    page[::6, ::6] = 0                                  # 점 톤처럼 작은 점(1px)
    page[100:180, 150:166] = 0                          # 굵은 획
    mask = sfx_stroke_mask(page, [100, 80, 150, 120])
    assert mask[100:180, 150:166].min() == 255          # 획 자리는 칠한다
    assert mask[:, :90].max() == 0 and mask[:70].max() == 0   # 박스 밖은 건드리지 않는다
    assert 0 < np.count_nonzero(mask) < 150 * 120       # 박스 전체가 아니라 획 둘레만

    flat = np.full((300, 300, 3), 255, np.uint8)        # 굵은 획이 없으면 박스 전체
    full = sfx_stroke_mask(flat, [100, 80, 150, 120])
    assert full[80:200, 100:250].min() == 255


def test_refine_rect_asks_again_on_a_zoomed_crop_and_grows_until_stable():
    import cv2 as _cv
    from pipeline.sfx_locator import refine_rect

    page = np.zeros((1000, 1000, 3), np.uint8)
    calls = []

    def fake_run(path, prompt, temperature, model_name):
        crop = _cv.imread(path)
        calls.append(crop.shape[:2])
        # 첫 크롭은 위쪽 일부만 잡고, 두 번째 크롭은 전체(박스 0-1000)를 잡는다
        return ({"box": [100, 100, 900, 500]} if len(calls) == 1 else {"box": [100, 50, 900, 950]}), 0.1

    rect = refine_rect(page, [400, 300, 100, 100], "グッ", "m", fake_run)
    assert len(calls) == 2                                 # 범위가 커져서 한 번 더 물었다
    assert rect[3] > 100 and rect[0] >= 0 and rect[1] >= 0 and rect[0] + rect[2] <= 1000
    broken = refine_rect(page, [400, 300, 100, 100], "グッ", "m", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert broken[2] >= 100 and broken[3] >= 100           # 실패하면 원래 박스(약간 넓힌 값)를 쓴다
