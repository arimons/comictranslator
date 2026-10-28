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


def _project(tmp_path, monkeypatch, with_portrait=True):
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "sessions")
    data = app.ensure_session("book")
    images = samples(tmp_path)
    if with_portrait:
        portrait = tmp_path / "003.png"
        Image.new("RGB", (600, 800), "green").save(portrait)
        images.append({"id": "003", "name": "003.png", "input_path": str(portrait)})
    data["images"] = images
    data["project_name"] = "Ore_3"
    app.save_session(data)
    return TestClient(app.create_app())


def test_preview_is_read_only_and_marks_worked_pages(tmp_path, monkeypatch):
    client = _project(tmp_path, monkeypatch)
    before = app.session_file("book").read_bytes()
    plan = client.post("/api/pages/preview", json={"session_id": "book"}).json()
    assert plan["output_count"] == 4 and plan["changed_count"] == 2
    assert [page["outputs"][0]["name"] for page in plan["pages"]] == ["001_p01_C.png", "002_p01_R.png", "003.png"]
    assert not any(page["has_work"] for page in plan["pages"])
    assert before == app.session_file("book").read_bytes()
    assert list((app.session_dir("book") / "input").iterdir()) == []
    assert client.post("/api/pages/preview", json={"session_id": "missing"}).status_code == 404


def test_split_happens_inside_the_same_project_and_keeps_reading_order(tmp_path, monkeypatch):
    client = _project(tmp_path, monkeypatch)
    originals = {item["input_path"]: open(item["input_path"], "rb").read() for item in samples(tmp_path)}
    response = client.post("/api/pages/split", json={"session_id": "book"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["split_pages"], result["created"], result["center_kept"], result["skipped_worked"]) == (2, 3, 1, [])

    data = app.ensure_session("book")
    names = [item["name"] for item in data["images"]]
    # 표지(가운데 한 장) → 펼침 오른쪽 → 왼쪽 → 세로 페이지. 자연 정렬이 읽기 순서를 그대로 유지한다
    assert names == ["001_p01_C.png", "002_p01_R.png", "002_p02_L.png", "003.png"]
    assert "parent_session_id" not in data  # 별도 프로젝트를 만들지 않는다
    by_name = {item["name"]: item for item in data["images"]}
    assert by_name["002_p01_R.png"]["page_side"] == "right" and by_name["002_p01_R.png"]["parent_id"] == "002"
    assert by_name["002_p01_R.png"]["reading_order"] == "rtl" and by_name["002_p01_R.png"]["input_storage"] == "derived"
    assert by_name["003.png"].get("parent_id") is None  # 세로 페이지는 손대지 않는다
    with Image.open(by_name["002_p01_R.png"]["input_path"]) as image:
        assert image.size == (600, 800) and image.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(by_name["002_p02_L.png"]["input_path"]) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)
    # 원본 파일은 그대로 있다
    assert originals == {path: open(path, "rb").read() for path in originals}

    # 다시 눌러도 이미 나눈 페이지는 나누지 않는다
    again = client.post("/api/pages/split", json={"session_id": "book"}).json()
    assert (again["split_pages"], again["created"]) == (0, 0)
    assert [item["name"] for item in app.ensure_session("book")["images"]] == names


def test_pages_with_work_are_skipped_unless_included(tmp_path, monkeypatch):
    client = _project(tmp_path, monkeypatch)
    item = next(item for item in app.ensure_session("book")["images"] if item["id"] == "002")
    record = app.image_record("book", app.Path(item["input_path"]), item.get("image_sha256"))
    app.Path(record["regions_json"]).parent.mkdir(parents=True, exist_ok=True)
    app.Path(record["regions_json"]).write_text("{}", encoding="utf-8")

    plan = client.post("/api/pages/preview", json={"session_id": "book"}).json()
    assert {page["image_id"]: page["has_work"] for page in plan["pages"]} == {"001": False, "002": True, "003": False}
    result = client.post("/api/pages/split", json={"session_id": "book"}).json()
    assert result["skipped_worked"] == ["002.png"] and result["split_pages"] == 1
    assert "002.png" in [i["name"] for i in app.ensure_session("book")["images"]]
    forced = client.post("/api/pages/split", json={"session_id": "book", "include_worked": True}).json()
    assert forced["split_pages"] == 1 and forced["skipped_worked"] == []
    assert "002.png" not in [i["name"] for i in app.ensure_session("book")["images"]]


def test_name_collision_rolls_back_without_changing_the_project(tmp_path, monkeypatch):
    client = _project(tmp_path, monkeypatch)
    input_dir = app.session_dir("book") / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "002_p02_L.png").write_bytes(b"occupied")  # 두 번째 조각의 이름이 이미 쓰이고 있다
    before = app.session_file("book").read_bytes()
    response = client.post("/api/pages/split", json={"session_id": "book"})
    assert response.status_code == 409
    assert before == app.session_file("book").read_bytes()
    assert sorted(path.name for path in input_dir.iterdir()) == ["002_p02_L.png"]  # 먼저 저장한 조각도 지워졌다


def test_prepare_endpoint_no_longer_creates_separate_projects(tmp_path, monkeypatch):
    client = _project(tmp_path, monkeypatch)
    assert client.post("/api/pages/prepare", json={"session_id": "book", "revision": "x"}).status_code in {404, 405}
    assert len(list(app.SESSION_ROOT.iterdir())) == 1


def test_health_does_not_create_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "sessions")
    assert TestClient(app.create_app()).get("/api/health").json()["status"] == "ok"
    assert not app.SESSION_ROOT.exists()


def test_split_page_reuses_parent_spread_cv_without_cloud_vision(tmp_path, monkeypatch):
    import app
    from pipeline.manual_guidance import load_json, write_json

    monkeypatch.setattr(app, "CV_CACHE_DIR", tmp_path / "cv")
    parent = "p" * 64
    rows = [
        {"box_2d": [100, 1000, 300, 1040], "label": "오른쪽 조각 글자"},
        {"box_2d": [100, 100, 300, 140], "label": "왼쪽 조각 글자"},
        {"box_2d": [500, 900, 540, 990], "label": "경계 걸침(중심은 왼쪽)"},
    ]
    write_json(app.cv_cache_path(parent), rows)
    work = tmp_path / "work"
    record = {"cv_json": str(work / "page_cv.json"), "text_groups_json": str(work / "page_text_groups.json")}
    item = {"source_image_sha256": parent, "crop_rect": [960, 0, 960, 1080], "width": 960, "height": 1080}
    logs = []

    assert app.hydrate_cv_cache(record, "c" * 64, False, logs.append, item) is True
    assert load_json(work / "page_cv.json") == [{"box_2d": [100, 40, 300, 80], "label": "오른쪽 조각 글자"}]
    assert "분할 전 원본 CV 재사용" in logs[0]

    # 이미 TG가 있는 페이지의 일반 분석은 건드리지 않고, 다시 분석(force)은 원본 CV로 다시 채운다.
    (work / "page_cv.json").unlink()
    write_json(work / "page_text_groups.json", [])
    assert app.hydrate_cv_cache(record, "c" * 64, False, logs.append, item) is False
    write_json(work / "page_cv.json", [])
    assert app.hydrate_cv_cache(record, "c" * 64, True, logs.append, item) is True
    assert len(load_json(work / "page_cv.json")) == 1

    left = app.crop_parent_cv_rows(rows, [0, 0, 960, 1080])
    assert [row["label"] for row in left] == ["왼쪽 조각 글자", "경계 걸침(중심은 왼쪽)"]
    assert left[1]["box_2d"] == [500, 900, 540, 960]


def test_page_override_keeps_or_moves_split_line(tmp_path):
    images = samples(tmp_path)
    images[1]["split_override"] = {"mode": "split", "split_x": 500}
    page = build_plan(images)["pages"][1]
    assert page["rects"] == [[500, 0, 700, 800], [0, 0, 500, 800]] and page["auto_mode"] == "split"
    images[1]["split_override"] = {"mode": "keep"}
    plan = build_plan(images)
    assert plan["pages"][1]["mode"] == "keep" and plan["output_count"] == 2
    assert page_plan(tmp_path / "002.png", {"mode": "split", "split_x": 99999})["rects"][1] == [0, 0, 1199, 800]


def test_override_endpoint_persists_and_split_follows_it(tmp_path, monkeypatch):
    client = _project(tmp_path, monkeypatch)
    response = client.post("/api/pages/override", json={"session_id": "book", "image_id": "002", "mode": "split", "split_x": 400})
    assert response.status_code == 200
    assert response.json()["page"]["rects"][1] == [0, 0, 400, 800]
    assert client.post("/api/pages/override", json={"session_id": "book", "image_id": "001", "mode": "keep"}).json()["page"]["mode"] == "keep"
    plan = client.post("/api/pages/preview", json={"session_id": "book"}).json()
    assert plan["changed_count"] == 1
    result = client.post("/api/pages/split", json={"session_id": "book"}).json()
    assert result["split_pages"] == 1
    crops = sorted(item.get("crop_rect") for item in app.ensure_session("book")["images"] if item.get("crop_rect"))
    assert crops == [[0, 0, 400, 800], [400, 0, 800, 800]]
    assert client.post("/api/pages/override", json={"session_id": "book", "image_id": "001", "mode": "auto"}).json()["page"]["mode"] == "center_crop"
    assert client.post("/api/pages/override", json={"session_id": "book", "image_id": "001", "mode": "split"}).status_code == 400
