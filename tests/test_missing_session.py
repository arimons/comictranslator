# 없는 프로젝트를 조회해도 빈 프로젝트가 생기지 않는지(404), 만드는 경로와 기존 프로젝트는 그대로인지 확인한다.

import json

import app
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolate_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(app, "INPUT_CACHE_DIR", tmp_path / "input_cache")
    monkeypatch.setattr(app, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(app, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")


@pytest.mark.parametrize("method,url,body", [
    ("get", "/api/session/ghost", None),
    ("get", "/api/session/ghost/page/001", None),
    ("get", "/api/speakers/ghost/001", None),
    ("get", "/api/sources/ghost", None),
    ("get", "/api/session/ghost/export", None),
    ("post", "/api/session/meta", {"session_id": "ghost", "project_name": "x"}),
    ("post", "/api/session/ocr-model", {"session_id": "ghost", "ocr_model": ""}),
    ("post", "/api/image/remove", {"session_id": "ghost", "image_id": "001"}),
    ("post", "/api/analyze", {"session_id": "ghost", "image_id": "001"}),
])
def test_unknown_session_is_404_and_creates_nothing(tmp_path, method, url, body):
    client = TestClient(app.create_app())
    response = client.get(url) if method == "get" else client.post(url, json=body)
    assert response.status_code == 404
    assert not (tmp_path / "sessions" / "ghost").exists()


def test_analyze_stream_for_unknown_session_reports_error_without_creating(tmp_path):
    client = TestClient(app.create_app())
    response = client.post("/api/analyze-stream", json={"session_id": "ghost", "image_id": "001"})
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert any(event.get("type") == "error" for event in events)
    assert not (tmp_path / "sessions" / "ghost").exists()


def test_new_session_and_upload_still_create(tmp_path):
    client = TestClient(app.create_app())
    created = client.post("/api/sessions/new").json()["session"]["session_id"]
    assert app.session_file(created).is_file()
    uploaded = client.post("/api/upload", files=[("files", ("p1.png", b"not-really-png", "image/png"))]).json()
    assert app.session_file(uploaded["session_id"]).is_file()
    assert client.get(f"/api/session/{uploaded['session_id']}").status_code == 200


def test_existing_legacy_session_json_still_loads(tmp_path):
    base = tmp_path / "sessions" / "legacy1"
    base.mkdir(parents=True)
    (base / "session.json").write_text(json.dumps({"session_id": "legacy1", "images": [], "project_name": "옛 프로젝트"}), encoding="utf-8")
    client = TestClient(app.create_app())
    response = client.get("/api/session/legacy1")
    assert response.status_code == 200
    assert response.json()["session"]["project_name"] == "옛 프로젝트"
    assert not (base / "project.json").exists()
