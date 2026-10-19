# 배경 글자 복원: 좌표 변환·역변환, 마스크 내부 합성, 패치 적용, 복원 API 흐름을 검증한다.

import base64
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import main_v2
from pipeline import background_restore as br


def _page(h=400, w=300, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def _png_b64(mask):
    ok, buf = cv2.imencode(".png", mask)
    assert ok
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()


@pytest.mark.parametrize("resolution,expected_long", [("512", 512), ("768", 768), ("original", 250)])
def test_prepare_keeps_aspect_and_pads_to_multiple_of_16(resolution, expected_long):
    page = _page()
    mask = np.zeros(page.shape[:2], np.uint8)
    mask[100:120, 50:60] = 255
    context = [20, 30, 170, 250]
    img_in, mask_in, tf = br.prepare_model_input(page, mask, context, br.RESOLUTION_CHOICES[resolution])

    assert max(tf.scaled_w, tf.scaled_h) == expected_long
    assert abs(tf.scaled_w / tf.scaled_h - 170 / 250) < 0.01
    assert tf.model_w % 16 == 0 and tf.model_h % 16 == 0
    assert img_in.shape[:2] == (tf.model_h, tf.model_w)
    assert set(np.unique(mask_in)) <= {0, 255}
    # 패딩 영역은 보존(0)
    assert not mask_in[tf.scaled_h:, :].any() and not mask_in[:, tf.scaled_w:].any()


def test_downscaled_mask_keeps_thin_strokes():
    page = _page(800, 800)
    mask = np.zeros((800, 800), np.uint8)
    mask[100:700, 400] = 255  # 1px 세로 획
    _, mask_in, tf = br.prepare_model_input(page, mask, [0, 0, 800, 800], 512)
    rows_with_mask = np.count_nonzero(mask_in[: tf.scaled_h].any(axis=1))
    assert rows_with_mask >= int(600 * 512 / 800) - 2


def test_round_trip_returns_context_size_and_identity_at_original():
    page = _page()
    mask = np.zeros(page.shape[:2], np.uint8)
    context = [10, 20, 123, 77]
    img_in, _, tf = br.prepare_model_input(page, mask, context, None)
    back = br.restore_from_model_output(img_in, tf)
    x, y, w, h = context
    assert back.shape[:2] == (h, w)
    assert np.array_equal(back, page[y:y + h, x:x + w])

    img_in, _, tf = br.prepare_model_input(page, mask, context, 512)
    assert br.restore_from_model_output(img_in, tf).shape[:2] == (h, w)
    with pytest.raises(ValueError):
        br.restore_from_model_output(img_in[:-16], tf)


@pytest.mark.parametrize("feather", [0, 3])
def test_composite_changes_only_mask_pixels(feather):
    page = _page()
    mask = np.zeros(page.shape[:2], np.uint8)
    cv2.circle(mask, (150, 200), 30, 255, -1)
    context = [80, 120, 150, 160]
    restored = np.full((160, 150, 3), 7, np.uint8)
    out = br.composite_inside_mask(page, restored, mask, context, feather_px=feather)

    assert br.changed_pixels_outside_mask(page, out, mask) == 0
    assert np.array_equal(out[mask == 0], page[mask == 0])
    if feather == 0:
        assert np.all(out[mask > 0] == 7)


def test_stroke_mask_does_not_fill_whole_rect():
    page = np.full((200, 200, 3), 255, np.uint8)
    cv2.putText(page, "AB", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 4)
    seeds = [{"rect": [20, 50, 160, 100], "fragments": []}]
    mask = br.build_text_stroke_mask(page, seeds, br.MaskParams(dilate_px=0))
    text_px = np.count_nonzero(page[..., 0] < 120)
    assert 0 < np.count_nonzero(mask) <= text_px
    assert np.count_nonzero(mask) < 160 * 100 * 0.5
    wider = br.build_text_stroke_mask(page, seeds, br.MaskParams(dilate_px=3))
    assert np.count_nonzero(wider) > np.count_nonzero(mask)
    assert not wider[:40].any()  # seed 바깥은 비어 있다(팽창 폭 제외)


def test_decode_mask_png_resizes_nearest_and_binarizes():
    small = np.zeros((10, 20), np.uint8)
    small[2:5, 3:9] = 255
    mask = br.decode_mask_png(_png_b64(small), 40, 20)
    assert mask.shape == (20, 40)
    assert set(np.unique(mask)) <= {0, 255}


def test_run_restore_with_stub_engine_keeps_outside(monkeypatch, tmp_path):
    page = _page(300, 260)
    mask = np.zeros(page.shape[:2], np.uint8)
    mask[100:140, 90:170] = 255
    mask[5:10, 5:10] = 255  # 문맥 영역 밖 마스크는 버려진다

    def fake_lama(img_in, mask_in):
        out = img_in.copy()
        out[:] = 128
        return out, {"load_sec": 0.0, "model_loaded_now": False, "infer_sec": 0.01}

    monkeypatch.setattr(br, "run_lama", fake_lama)
    result = br.run_restore(page, mask, [60, 60, 150, 150], br.RestoreParams(engine="lama", resolution="512"), tmp_path)
    out = result["page"]
    inside = np.zeros_like(mask)
    inside[60:210, 60:210] = mask[60:210, 60:210]
    assert result["meta"]["outside_mask_changed_pixels"] == 0
    assert result["meta"]["mask_pixels_dropped_outside_context"] == 25
    assert np.array_equal(out[inside == 0], page[inside == 0])
    assert (tmp_path / "crop_after.png").exists()


def test_apply_restore_patches_only_inside_patch_mask(tmp_path):
    page = _page(120, 100)
    patch = np.full((40, 30, 3), 9, np.uint8)
    pmask = np.zeros((40, 30), np.uint8)
    pmask[10:20, 5:15] = 255
    (tmp_path / "patches").mkdir()
    cv2.imwrite(str(tmp_path / "patches" / "p_patch.png"), patch)
    cv2.imwrite(str(tmp_path / "patches" / "p_mask.png"), pmask)
    (tmp_path / "state.json").write_text(
        '{"patches": [{"id": "p", "rect": [20, 30, 30, 40], "patch_png": "patches/p_patch.png", "mask_png": "patches/p_mask.png"}]}'
    )
    out = page.copy()
    assert br.apply_restore_patches(out, tmp_path / "state.json") == 1
    full = np.zeros((120, 100), np.uint8)
    full[30:70, 20:50] = pmask
    assert np.array_equal(out[full == 0], page[full == 0])
    assert np.all(out[full > 0] == 9)


def test_grayscale_detection():
    gray = np.repeat(_page(50, 50)[..., :1], 3, axis=2)
    color = _page(50, 50)
    assert br.is_grayscale_image(gray)
    assert not br.is_grayscale_image(color)


def _session_with_image(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "INPUT_CACHE_DIR", tmp_path / "input_cache")
    data = main_v2.ensure_session("s1")
    input_dir = main_v2.session_dir("s1") / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    img_path = input_dir / "p1.png"
    cv2.imwrite(str(img_path), np.full((200, 160, 3), 255, np.uint8))
    data["images"] = [{"id": "p1", "name": "p1.png", "input_path": str(img_path)}]
    main_v2.save_session(data)
    return img_path


def _wait_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/restore/job/{job_id}").json()
        if job["status"] in {"done", "error"}:
            return job
        time.sleep(0.02)
    raise AssertionError("job timeout")


def test_restore_api_run_accept_and_failure_keeps_patches(tmp_path, monkeypatch):
    _session_with_image(tmp_path, monkeypatch)
    client = TestClient(main_v2.create_app())

    def fake_lama(img_in, mask_in):
        out = img_in.copy()
        out[mask_in > 0] = 0
        return out, {"load_sec": 0.0, "model_loaded_now": False, "infer_sec": 0.0}

    monkeypatch.setattr(br, "run_lama", fake_lama)
    mask = np.zeros((200, 160), np.uint8)
    mask[50:70, 40:90] = 255
    body = {"session_id": "s1", "image_id": "p1", "engine": "lama", "context_rect": [20, 20, 120, 120],
            "mask_png": _png_b64(mask), "resolution": "original"}
    job = _wait_job(client, client.post("/api/restore/run", json=body).json()["job_id"])
    assert job["status"] == "done", job
    assert job["meta"]["outside_mask_changed_pixels"] == 0

    info = client.post("/api/restore/accept", json={"session_id": "s1", "image_id": "p1", "job_id": job["job_id"]}).json()
    assert len(info["patches"]) == 1

    def broken(*_args, **_kwargs):
        raise br.EngineError("boom")

    monkeypatch.setattr(br, "run_lama", broken)
    failed = _wait_job(client, client.post("/api/restore/run", json=body).json()["job_id"])
    assert failed["status"] == "error" and "boom" in failed["error"]
    info = client.get("/api/restore/info", params={"session_id": "s1", "image_id": "p1"}).json()
    assert len(info["patches"]) == 1
    bad_accept = client.post("/api/restore/accept", json={"session_id": "s1", "image_id": "p1", "job_id": failed["job_id"]})
    assert bad_accept.status_code == 400
