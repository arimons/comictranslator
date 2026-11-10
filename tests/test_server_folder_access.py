# 인증 없는 서버에서 폴더 열람과 경로 가져오기가 허용 목록(홈, /Volumes, 데이터 홈, 환경변수) 밖으로 나가지 않는지 검증한다.

from pathlib import Path

import app
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    for folder in (home / "만화" / "1권", outside / "비밀", tmp_path / "volumes", tmp_path / "data"):
        folder.mkdir(parents=True)
    (outside / "page.jpg").write_bytes(b"fixture")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(app, "SERVER_VOLUMES_DIR", tmp_path / "volumes")
    monkeypatch.setattr(app, "DATA_HOME", tmp_path / "data")
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "data" / "projects")
    monkeypatch.setattr(app, "INPUT_CACHE_DIR", tmp_path / "data" / "cache" / "input")
    monkeypatch.setattr(app, "hydrate_load_cache_for_records", lambda *args, **kwargs: None)
    monkeypatch.delenv(app.BROWSE_ROOTS_ENV, raising=False)
    return {"home": home, "outside": outside, "client": TestClient(app.create_app())}


def test_allowed_path_lists_subfolders_and_home_is_default(roots):
    client, home = roots["client"], roots["home"]
    data = client.get("/api/fs/list", params={"path": str(home / "만화")}).json()
    assert [item["name"] for item in data["dirs"]] == ["1권"]
    assert data["parent"] == str(home.resolve())
    assert data["roots"][0] == {"name": "홈", "path": str(home)}

    default = client.get("/api/fs/list").json()
    assert default["path"] == str(home.resolve())
    # 허용 범위의 맨 위에서는 상위 폴더로 올라가지 않는다
    assert default["parent"] == ""


def test_outside_path_and_symlink_escape_are_forbidden(roots):
    client, home, outside = roots["client"], roots["home"], roots["outside"]
    response = client.get("/api/fs/list", params={"path": str(outside)})
    assert response.status_code == 403
    assert "허용된 폴더" in response.json()["detail"]

    (home / "바로가기").symlink_to(outside)
    assert client.get("/api/fs/list", params={"path": str(home / "바로가기")}).status_code == 403
    # 존재하지 않는 바깥 경로도 존재 여부를 알려 주지 않고 막는다
    assert client.get("/api/fs/list", params={"path": str(outside / "없음")}).status_code == 403


def test_env_var_adds_a_browse_root(roots, monkeypatch):
    monkeypatch.setenv(app.BROWSE_ROOTS_ENV, str(roots["outside"]))
    data = roots["client"].get("/api/fs/list", params={"path": str(roots["outside"])}).json()
    assert [item["name"] for item in data["dirs"]] == ["비밀"]


def test_import_path_outside_is_forbidden_and_creates_nothing(roots):
    client, home, outside = roots["client"], roots["home"], roots["outside"]
    response = client.post("/api/import-path", json={"session_id": "blocked", "path": str(outside)})
    assert response.status_code == 403
    assert not (app.SESSION_ROOT / "blocked").exists()

    # 허용 폴더 안의 링크가 바깥 이미지를 가리키면 그 파일은 가져오지 않는다
    (home / "만화" / "1권" / "p1.jpg").write_bytes(b"inside")
    (home / "만화" / "1권" / "leak.jpg").symlink_to(outside / "page.jpg")
    imported = client.post("/api/import-path", json={"session_id": "allowed", "path": str(home / "만화" / "1권")}).json()
    assert [item["name"] for item in imported["images"]] == ["p1.jpg"]
