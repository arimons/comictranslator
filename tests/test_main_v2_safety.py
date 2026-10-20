# main_v2의 파일 서빙 범위, 입력 파일 충돌, 분석 스트림 실패 처리를 검증한다.

import json
from pathlib import Path

import main_v2
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolate_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "INPUT_CACHE_DIR", tmp_path / "input_cache")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")


def test_file_endpoint_serves_session_images_only(tmp_path):
    client = TestClient(main_v2.create_app())
    image = tmp_path / "sessions" / "s1" / "output" / "final" / "page1_final.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpeg")
    session_json = tmp_path / "sessions" / "s1" / "session.json"
    session_json.write_text("{}", encoding="utf-8")
    outside_image = tmp_path / "elsewhere" / "page.jpg"
    outside_image.parent.mkdir()
    outside_image.write_bytes(b"jpeg")

    assert client.get("/api/file", params={"path": str(image)}).status_code == 200
    # 이미지가 아닌 파일, 세션·캐시 폴더 밖 파일, 저장소 비밀 파일은 내려주지 않는다.
    assert client.get("/api/file", params={"path": str(session_json)}).status_code == 403
    assert client.get("/api/file", params={"path": str(outside_image)}).status_code == 403
    assert client.get("/api/file", params={"path": str(main_v2.ROOT / ".env")}).status_code == 403


def test_repeated_upload_of_same_name_never_overwrites_cached_originals():
    client = TestClient(main_v2.create_app())
    contents = [b"first image", b"second image", b"third image"]
    records = []
    for content in contents:
        response = client.post(
            "/api/upload?session_id=dup_upload",
            files=[("files", ("page.jpg", content, "image/jpeg"))],
        )
        assert response.status_code == 200
        records.extend(response.json()["images"])

    assert len({record["id"] for record in records}) == 3
    for record, content in zip(records, contents):
        assert Path(record["input_path"]).read_bytes() == content
        assert main_v2.input_cache_path(record["image_sha256"], ".jpg").read_bytes() == content


def test_upload_same_stem_with_different_extensions_gets_distinct_ids():
    client = TestClient(main_v2.create_app())

    response = client.post(
        "/api/upload?session_id=stem_upload",
        files=[
            ("files", ("a.jpg", b"jpeg image", "image/jpeg")),
            ("files", ("a.png", b"png image", "image/png")),
        ],
    )

    assert response.status_code == 200
    ids = [record["id"] for record in response.json()["images"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_reimporting_same_file_name_keeps_previous_record_intact(tmp_path):
    first = tmp_path / "batch1" / "page.jpg"
    second = tmp_path / "batch2" / "page.jpg"
    third = tmp_path / "batch3" / "page.jpg"
    for path, content in ((first, b"first"), (second, b"second"), (third, b"third")):
        path.parent.mkdir(parents=True)
        path.write_bytes(content)

    records = []
    for source in (first, second, third):
        records.extend(main_v2.copy_images_into_session([source], "reimport"))

    assert len({record["id"] for record in records}) == 3
    assert [Path(record["input_path"]).read_bytes() for record in records] == [b"first", b"second", b"third"]


def test_stream_analyze_finishes_remaining_images_after_one_failure(tmp_path, monkeypatch):
    session_id = "partial_failure"
    session = main_v2.ensure_session(session_id)
    session["images"] = []
    for name in ("page1", "page2"):
        image_path = tmp_path / "sessions" / session_id / "input" / f"{name}.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(name.encode())
        session["images"].append({"id": name, "name": f"{name}.jpg", "input_path": str(image_path)})
    main_v2.save_session(session)

    def fake_run_single_image(image_path, *args, **kwargs):
        if Path(image_path).stem == "page1":
            return False, "page1 실패"
        return True, "완료"

    monkeypatch.setattr(main_v2, "run_single_image", fake_run_single_image)

    events = [json.loads(line) for line in main_v2.stream_analyze(main_v2.AnalyzeRequest(session_id=session_id))]

    statuses = {(event.get("image_id"), event.get("status")) for event in events if event["type"] == "image_status"}
    assert ("page1", "failed") in statuses
    assert ("page2", "done") in statuses
    assert events[-1]["type"] == "complete"
    assert events[-1]["ok"] is False


def test_ui_assets_are_revalidated_so_rebuilt_bundle_is_picked_up():
    client = TestClient(main_v2.create_app())

    response = client.get("/app.bundle.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_health_reports_python_files_changed_after_start(tmp_path, monkeypatch):
    import os
    import sys
    import types

    import main_v2
    from fastapi.testclient import TestClient

    module_file = tmp_path / "loaded_mod.py"
    module_file.write_text("X = 1\n")
    fake = types.ModuleType("loaded_mod_for_health")
    fake.__file__ = str(module_file)
    monkeypatch.setitem(sys.modules, "loaded_mod_for_health", fake)
    monkeypatch.setattr(main_v2, "ROOT", tmp_path)
    monkeypatch.setattr(main_v2, "_CODE_MTIMES", {})
    client = TestClient(main_v2.create_app())
    assert client.get("/api/health").json()["stale_code"] == []
    stat = module_file.stat()
    os.utime(module_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert client.get("/api/health").json()["stale_code"] == ["loaded_mod.py"]
