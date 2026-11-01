"""LaMa 작업 프로세스: 호출 왕복, 비우기, 유휴 종료, 서버 등록. 실제 모델 대신 입력을 반전해 돌려주는 가짜를 쓴다."""
import sys
import time

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

import app
from pipeline import dialogue_extractor, lama_worker

FAKE = [sys.executable, "-c", "from pipeline.lama_worker import _serve; _serve(lambda: (lambda img, m: img.point(lambda v: 255 - v)))"]


def _call(client):
    img = Image.fromarray(np.full((20, 30, 3), 10, np.uint8))
    return np.asarray(client(img, Image.fromarray(np.zeros((20, 30), np.uint8))))


def test_worker_round_trip_and_release():
    client = lama_worker.LamaWorkerClient(command=FAKE)
    out = _call(client)
    assert out.shape == (20, 30, 3) and int(out[0, 0, 0]) == 245
    pid = client.status()["pid"]
    assert client.status()["running"] and pid
    assert client.release("시험") is True
    assert client.status()["running"] is False
    assert client.release("시험") is False
    # 비운 뒤 다시 부르면 새 프로세스로 이어서 처리한다
    assert int(_call(client)[0, 0, 0]) == 245
    assert client.status()["pid"] != pid
    client.release("정리")


def test_worker_stops_after_idle():
    client = lama_worker.LamaWorkerClient(command=FAKE, idle_release_sec=0.4)
    _call(client)
    deadline = time.monotonic() + 5
    while client.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.1)
    assert client.status()["running"] is False


def test_worker_error_is_reported():
    broken = [sys.executable, "-c", "from pipeline.lama_worker import _serve; _serve(lambda: (lambda img, m: 1 / 0))"]
    client = lama_worker.LamaWorkerClient(command=broken)
    try:
        _call(client)
    except RuntimeError as exc:
        assert "ZeroDivisionError" in str(exc)
    else:
        raise AssertionError("실패를 돌려줘야 한다")
    client.release("정리")


def test_server_routes_lama_to_worker_and_release_api(monkeypatch):
    fake = lama_worker.LamaWorkerClient(command=FAKE)
    monkeypatch.setattr(lama_worker, "_client", fake)
    try:
        api = TestClient(app.create_app())
        assert dialogue_extractor._get_lama() is fake
        _call(dialogue_extractor._get_lama())
        assert api.get("/api/lama/status").json()["running"] is True
        body = api.post("/api/lama/release").json()
        assert body["released"] is True and body["status"]["running"] is False
    finally:
        dialogue_extractor.set_lama_provider(None)
        fake.release("정리")


def test_release_waits_while_other_work_runs(monkeypatch):
    monkeypatch.setattr(app, "busy_job_count", lambda: 1)
    body = app.release_lama_worker("시험")
    assert body["released"] is False and body["busy"] == 1
