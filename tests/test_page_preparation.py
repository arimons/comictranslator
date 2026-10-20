import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import app
from pipeline.page_preparation import build_plan, materialize_plan, page_plan


def samples(tmp_path):
    cover = tmp_path / "001.png"
    image = Image.new("RGB", (1200, 800), "white")
    ImageDraw.Draw(image).rectangle((370, 0, 829, 799), fill="#a63564")
    image.save(cover)
    spread = tmp_path / "002.png"
    image = Image.new("RGB", (1200, 800), "blue")
    ImageDraw.Draw(image).rectangle((600, 0, 1199, 799), fill="red")
    image.save(spread)
    return [{"id": p.stem, "name": p.name, "input_path": str(p)} for p in (cover, spread)]


def test_cover_center_and_right_before_left_without_modifying_originals(tmp_path):
    images = samples(tmp_path)
    before = {item["input_path"]: open(item["input_path"], "rb").read() for item in images}
    plan = build_plan(images)
    assert plan["output_count"] == 3
    assert plan["pages"][0]["mode"] == "center_crop"
    assert plan["pages"][1]["sides"] == ["right", "left"]
    results = materialize_plan(plan, tmp_path / "prepared")
    with Image.open(results[1]["path"]) as image:
        assert image.size == (600, 800)
        assert image.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(results[2]["path"]) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)
    assert before == {path: open(path, "rb").read() for path in before}


def test_revision_and_materialization_reject_changed_source(tmp_path):
    images = samples(tmp_path)
    first = build_plan(images)
    Image.new("RGB", (1200, 800), "green").save(images[0]["input_path"])
    assert build_plan(images)["revision"] != first["revision"]
    with pytest.raises(ValueError):
        materialize_plan(first, tmp_path / "prepared")


def test_odd_spread_preserves_every_column_and_portrait_is_unchanged(tmp_path):
    path = tmp_path / "odd.png"
    Image.new("RGB", (1201, 800), "blue").save(path)
    plan = page_plan(path)
    assert plan["rects"] == [[600, 0, 601, 800], [0, 0, 600, 800]]
    Image.new("RGB", (600, 800), "blue").save(path)
    assert page_plan(path)["mode"] == "keep"


def test_preview_is_read_only_and_apply_creates_separate_project(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "sessions")
    data = app.ensure_session("original")
    data["images"] = samples(tmp_path)
    data["project_name"] = "Ore_3"
    app.save_session(data)
    before = app.session_file("original").read_bytes()
    client = TestClient(app.create_app())
    plan = client.post("/api/pages/preview", json={"session_id": "original"}).json()
    assert plan["output_count"] == 3
    assert before == app.session_file("original").read_bytes()
    assert len(list(app.SESSION_ROOT.iterdir())) == 1
    response = client.post("/api/pages/prepare", json={"session_id": "original", "revision": plan["revision"]})
    assert response.status_code == 200
    result = app.ensure_session(response.json()["session_id"])
    assert result["parent_session_id"] == "original"
    assert len(result["images"]) == 3
    assert result["images"][1]["page_side"] == "right"
    assert before == app.session_file("original").read_bytes()
    assert not list(app.SESSION_ROOT.glob("*/work/json/*"))
    assert client.post("/api/pages/prepare", json={"session_id": "original", "revision": "stale"}).status_code == 409
    assert client.post("/api/pages/preview", json={"session_id": "missing"}).status_code == 404


def test_health_does_not_create_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "sessions")
    assert TestClient(app.create_app()).get("/api/health").json()["status"] == "ok"
    assert not app.SESSION_ROOT.exists()
