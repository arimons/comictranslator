# main_v2 UI의 정렬과 분석 로그 스트림 동작을 검증한다.

import json
import main_v2
import pytest
import threading
import zipfile
from fastapi.testclient import TestClient
from PIL import Image
from pathlib import Path
from urllib.parse import quote


@pytest.fixture(autouse=True)
def isolate_input_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "INPUT_CACHE_DIR", tmp_path / "input_cache")


def run_load_cache_hydration_immediately(monkeypatch):
    def hydrate_now(session_id, items):
        for item in items:
            main_v2.hydrate_load_cache_for_item(session_id, item, allow_backfill=True)

    monkeypatch.setattr(main_v2, "start_background_load_cache_hydration", hydrate_now)


def test_sorted_image_records_uses_natural_name_order():
    records = [
        {"id": "page10", "name": "page10.jpg"},
        {"id": "page2", "name": "page2.jpg"},
        {"id": "page1", "name": "page1.jpg"},
    ]

    sorted_records = main_v2.sorted_image_records(records)

    assert [item["name"] for item in sorted_records] == ["page1.jpg", "page2.jpg", "page10.jpg"]


def test_fonts_static_route_serves_handwritten_font():
    response = TestClient(main_v2.create_app()).get("/fonts/NanumGiBbeumBarkEum.ttf")

    assert response.status_code == 200
    assert response.content[:4] in {b"\x00\x01\x00\x00", b"ttcf", b"OTTO"}


def test_list_recent_sessions_skips_empty_and_sorts_by_updated_time(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    empty = main_v2.ensure_session("empty_session")
    main_v2.save_session(empty)
    older = main_v2.ensure_session("older_session")
    older["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(tmp_path / "page1.jpg")}]
    main_v2.save_session(older)
    newer = main_v2.ensure_session("newer_session")
    newer["images"] = [{"id": "page2", "name": "page2.jpg", "input_path": str(tmp_path / "page2.jpg")}]
    main_v2.save_session(newer)
    old_time = 1000
    new_time = 2000
    session_older = main_v2.session_file("older_session")
    session_newer = main_v2.session_file("newer_session")
    session_older.touch()
    session_newer.touch()
    import os
    os.utime(session_older, (old_time, old_time))
    os.utime(session_newer, (new_time, new_time))

    sessions = main_v2.list_recent_sessions()

    assert [item["session_id"] for item in sessions] == ["newer_session", "older_session"]
    assert sessions[0]["image_count"] == 1
    assert sessions[0]["first_image"] == "page2.jpg"
    assert "thumbnail_url" in sessions[0]


def test_home_progress_counts_artifacts_and_reflects_manual_edits_without_writes(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    session = main_v2.ensure_session("progress")
    session["images"] = [{"id": f"page{i}", "name": f"page{i}.jpg", "input_path": str(tmp_path / f"page{i}.jpg")} for i in (1, 2, 3)]
    main_v2.save_session(session)
    manifest = main_v2.session_file("progress")
    os.utime(manifest, (1000, 1000))
    base = manifest.parent
    artifacts = {
        "work/json/page1_regions.json": (b"{}", 2000),
        "work/json/page2_regions.json": (b"{}", 2000),
        "output/final/page1_final.jpg": (b"image", 3000),
        "work/json/page2_manual_guidance.json": (b"{}", 4000),
    }
    for name, (content, timestamp) in artifacts.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, (timestamp, timestamp))
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in base.rglob("*") if path.is_file()}

    recent = main_v2.list_recent_sessions()[0]

    assert recent["analysis_count"] == 2
    assert recent["final_count"] == 1
    assert recent["image_count"] == 3
    assert recent["updated_at"] == main_v2.dt.datetime.fromtimestamp(4000).isoformat(timespec="seconds")
    assert "manual_completed_count" not in recent
    assert before == {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in base.rglob("*") if path.is_file()}


@pytest.mark.parametrize("existing_name", ["", "사용자가 정한 작품명"])
def test_folder_upload_uses_folder_name_without_overwriting_project_name(tmp_path, monkeypatch, existing_name):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "hydrate_load_cache_for_records", lambda *args, **kwargs: None)
    session = main_v2.ensure_session("folder_upload")
    session["project_name"] = existing_name
    main_v2.save_session(session)
    response = TestClient(main_v2.create_app()).post(
        "/api/upload?session_id=folder_upload&source_folder_name=작품폴더",
        files=[("files", ("page1.jpg", b"upload image", "image/jpeg"))],
    )
    assert response.status_code == 200
    saved = main_v2.ensure_session("folder_upload")
    assert saved["source_folder_name"] == "작품폴더"
    assert saved["project_name"] == (existing_name or "작품폴더")


@pytest.mark.parametrize("existing_name", ["", "내 작품"])
def test_file_upload_defaults_to_first_natural_filename_and_keeps_custom_name(tmp_path, monkeypatch, existing_name):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "hydrate_load_cache_for_records", lambda *args, **kwargs: None)
    session = main_v2.ensure_session("files_named")
    session["project_name"] = existing_name
    main_v2.save_session(session)
    response = TestClient(main_v2.create_app()).post(
        "/api/upload?session_id=files_named",
        files=[("files", (name, b"fixture", "image/jpeg")) for name in ("page10.jpg", "page2.jpg")],
    )
    assert response.status_code == 200
    assert main_v2.ensure_session("files_named")["project_name"] == (existing_name or "page2")


@pytest.mark.parametrize("is_folder", [True, False])
def test_import_path_defaults_project_name_to_input_name(tmp_path, monkeypatch, is_folder):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "hydrate_load_cache_for_records", lambda *args, **kwargs: None)
    folder = tmp_path / "작품 폴더"
    folder.mkdir()
    source = folder / "page1.jpg"
    source.write_bytes(b"fixture")
    response = TestClient(main_v2.create_app()).post("/api/import-path", json={"session_id":"path_named", "path":str(folder if is_folder else source)})
    assert response.status_code == 200
    saved = main_v2.ensure_session("path_named")
    assert saved["project_name"] == ("작품 폴더" if is_folder else "page1")


def test_update_session_meta_persists_project_name(tmp_path, monkeypatch):
    session_id = "project_named"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    session = main_v2.ensure_session(session_id)
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)

    response = TestClient(main_v2.create_app()).post(
        "/api/session/meta",
        json={"session_id": session_id, "project_name": "  ORE2 1권  "},
    )

    assert response.status_code == 200
    assert response.json()["session"]["project_name"] == "ORE2 1권"
    assert main_v2.ensure_session(session_id)["project_name"] == "ORE2 1권"
    assert main_v2.list_recent_sessions()[0]["project_name"] == "ORE2 1권"


def test_delete_session_endpoint_removes_project(tmp_path, monkeypatch):
    session_id = "delete_project"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    session = main_v2.ensure_session(session_id)
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)

    response = TestClient(main_v2.create_app()).post(
        "/api/session/delete",
        json={"session_id": session_id},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert not main_v2.session_dir(session_id).exists()
    assert all(item["session_id"] != session_id for item in response.json()["sessions"])


def test_list_recent_sessions_dedupes_same_hash_and_subset_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_paths = {}
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        path = tmp_path / name
        path.write_bytes(name.encode("utf-8"))
        image_paths[name] = path
    hashes = {name: main_v2.file_sha256(path) for name, path in image_paths.items()}

    subset = main_v2.ensure_session("subset_session")
    subset["images"] = [
        {"id": "a", "name": "a.jpg", "input_path": str(image_paths["a.jpg"]), "image_sha256": hashes["a.jpg"]},
        {"id": "b", "name": "b.jpg", "input_path": str(image_paths["b.jpg"]), "image_sha256": hashes["b.jpg"]},
    ]
    main_v2.save_session(subset)
    duplicate = main_v2.ensure_session("duplicate_session")
    duplicate["images"] = [
        {"id": "a", "name": "a.jpg", "input_path": str(image_paths["a.jpg"]), "image_sha256": hashes["a.jpg"]},
        {"id": "b", "name": "b.jpg", "input_path": str(image_paths["b.jpg"]), "image_sha256": hashes["b.jpg"]},
    ]
    main_v2.save_session(duplicate)
    superset = main_v2.ensure_session("superset_session")
    superset["images"] = [
        {"id": "a", "name": "a.jpg", "input_path": str(image_paths["a.jpg"]), "image_sha256": hashes["a.jpg"]},
        {"id": "b", "name": "b.jpg", "input_path": str(image_paths["b.jpg"]), "image_sha256": hashes["b.jpg"]},
        {"id": "c", "name": "c.jpg", "input_path": str(image_paths["c.jpg"]), "image_sha256": hashes["c.jpg"]},
    ]
    main_v2.save_session(superset)

    import os
    os.utime(main_v2.session_file("subset_session"), (3000, 3000))
    os.utime(main_v2.session_file("duplicate_session"), (2000, 2000))
    os.utime(main_v2.session_file("superset_session"), (1000, 1000))

    sessions = main_v2.list_recent_sessions()

    assert [item["session_id"] for item in sessions] == ["superset_session"]
    assert sessions[0]["image_count"] == 3


def test_stream_analyze_emits_live_log_events(tmp_path, monkeypatch):
    session_id = "stream_test"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path)
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    image_path = tmp_path / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)

    def fake_run_single_image(*args, log_callback=None, **kwargs):
        log_callback("Step 1 시작")
        log_callback("Step 2 시작")
        return True, "완료"

    monkeypatch.setattr(main_v2, "run_single_image", fake_run_single_image)

    events = list(main_v2.stream_analyze(main_v2.AnalyzeRequest(session_id=session_id)))

    assert any("Step 1 시작" in event for event in events)
    assert any('"status": "done"' in event and '"image"' in event for event in events)
    assert any('"type": "complete"' in event for event in events)


def test_analyze_uses_global_cv_cache_by_image_hash(tmp_path, monkeypatch):
    session_id = "cache_test"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"same image bytes")
    image_hash = main_v2.file_sha256(image_path)
    cache_path = main_v2.cv_cache_path(image_hash)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('{"cached": true}', encoding="utf-8")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path), "image_sha256": image_hash}]
    main_v2.save_session(session)

    def fake_run_single_image(image_path_arg, work_dir_arg, output_path_arg, **kwargs):
        cv_json = tmp_path / "sessions" / session_id / "work" / "json" / "page1_cv.json"
        assert cv_json.read_text(encoding="utf-8") == '{"cached": true}'
        return True, "캐시로 실행"

    monkeypatch.setattr(main_v2, "run_single_image", fake_run_single_image)
    response = TestClient(main_v2.create_app()).post("/api/analyze", json={"session_id": session_id})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_file_url_includes_mtime_cache_buster(tmp_path):
    image_path = tmp_path / "page.jpg"
    image_path.write_bytes(b"image")

    url = main_v2.file_url(image_path)

    assert url is not None
    assert "path=" in url
    assert "&v=" in url


def test_guidance_has_edits_counts_suppressed_regions():
    assert main_v2.guidance_has_edits({"suppressed_regions": [{"rect": [1, 2, 3, 4]}]}) is True


def test_file_url_keeps_session_symlink_path(tmp_path):
    cache_path = tmp_path / "cache" / "masked.jpg"
    session_path = tmp_path / "session" / "work" / "masked" / "page1_masked.jpg"
    cache_path.parent.mkdir(parents=True)
    session_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"masked")
    session_path.symlink_to(cache_path)

    url = main_v2.file_url(session_path)

    assert quote(session_path.absolute().as_posix()) in url
    assert quote(cache_path.absolute().as_posix()) not in url


def test_link_or_copy_file_noops_when_dest_already_points_to_source(tmp_path):
    src = tmp_path / "cache" / "indexed_sheet.jpg"
    dest = tmp_path / "session" / "work" / "sheets" / "page1_indexed_sheet.jpg"
    src.parent.mkdir(parents=True)
    dest.parent.mkdir(parents=True)
    src.write_bytes(b"sheet")
    dest.symlink_to(src)

    main_v2._link_or_copy_file(src, dest)

    assert dest.is_symlink()
    assert dest.resolve() == src


def test_load_json_returns_default_for_partial_json(tmp_path):
    path = tmp_path / "page1_manual_guidance.json"
    path.write_text("", encoding="utf-8")

    assert main_v2.load_json(path, default={}) == {}


def test_image_record_marks_empty_effective_text_groups_as_present(tmp_path, monkeypatch):
    session_id = "empty_effective"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    record = main_v2.image_record(session_id, image_path)
    effective_path = Path(record["effective_text_groups_json"])
    effective_path.parent.mkdir(parents=True, exist_ok=True)
    effective_path.write_text("[]", encoding="utf-8")

    refreshed = main_v2.image_record(session_id, image_path)

    assert refreshed["has_effective_text_groups"] is True
    assert refreshed["effective_text_groups"] == []


def test_session_images_uses_manifest_dimensions_without_reopening_image(tmp_path, monkeypatch):
    session_id = "manifest_fast_load"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"not a real image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{
        "id": "page1",
        "name": "page1.jpg",
        "input_path": str(image_path),
        "image_sha256": "hash1",
        "width": 1200,
        "height": 1800,
        "aspect_ratio": 0.6667,
        "wide_spread_candidate": False,
        "left_margin_ratio": 0.1,
        "right_margin_ratio": 0.2,
        "spread_layout": "wide_page",
    }]
    main_v2.save_session(session)

    monkeypatch.setattr(main_v2, "image_dimensions", lambda _path: (_ for _ in ()).throw(AssertionError("image reopened")))
    monkeypatch.setattr(main_v2, "estimate_horizontal_margins", lambda _path: (_ for _ in ()).throw(AssertionError("margin recalculated")))

    images = main_v2.session_images(session_id)

    assert images[0]["width"] == 1200
    assert images[0]["height"] == 1800
    assert images[0]["left_margin_ratio"] == 0.1
    assert images[0]["spread_layout"] == "wide_page"


def test_session_images_does_not_probe_legacy_manifest_without_dimensions(tmp_path, monkeypatch):
    session_id = "legacy_manifest_fast_load"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"not a real image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{
        "id": "page1",
        "name": "page1.jpg",
        "input_path": str(image_path),
        "image_sha256": "hash1",
    }]
    main_v2.save_session(session)

    monkeypatch.setattr(main_v2, "image_dimensions", lambda _path: (_ for _ in ()).throw(AssertionError("image reopened")))
    monkeypatch.setattr(main_v2, "estimate_horizontal_margins", lambda _path: (_ for _ in ()).throw(AssertionError("margin recalculated")))

    images = main_v2.session_images(session_id)

    assert images[0]["wide_spread_candidate"] is False
    assert "width" not in images[0]


def test_import_hydrates_pre_ocr_analysis_cache_by_image_hash(tmp_path, monkeypatch):
    session_id = "analysis_cache_load"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    run_load_cache_hydration_immediately(monkeypatch)
    source = tmp_path / "source" / "page1.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(source)
    cache_dir = main_v2.analysis_cache_dir(image_hash)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "cv.json").write_text('{"cv": true}', encoding="utf-8")
    (cache_dir / "text_groups.json").write_text('[{"text_group_id": 1}]', encoding="utf-8")
    (cache_dir / "regions.json").write_text(
        '{"regions": [{"region_id": 1, "mask_ref": "/old/page1_R1_mask.png"}]}',
        encoding="utf-8",
    )
    (cache_dir / "indexed_sheet.jpg").write_bytes(b"sheet")
    (cache_dir / "masked.jpg").write_bytes(b"masked")
    (cache_dir / "bubble_vis.jpg").write_bytes(b"vis")
    (cache_dir / "meta.json").write_text(
        f'{{"analysis_cache_version": "{main_v2.ANALYSIS_CACHE_VERSION}"}}',
        encoding="utf-8",
    )
    (cache_dir / "masks").mkdir()
    (cache_dir / "masks" / "page1_R1_mask.png").write_bytes(b"mask")

    records = main_v2.copy_images_into_session([source], session_id)
    record = main_v2.image_record(session_id, Path(records[0]["input_path"]), image_hash)

    assert Path(record["regions_json"]).exists()
    assert Path(record["indexed_sheet_jpg"]).read_bytes() == b"sheet"
    assert record["text_groups"] == [{"text_group_id": 1}]
    assert record["regions"]["regions"][0]["mask_ref"].endswith("work/masks/page1_R1_mask.png")
    assert Path(record["regions"]["regions"][0]["mask_ref"]).exists()
    assert Path(record["masked_jpg"]).is_symlink()
    assert Path(record["indexed_sheet_jpg"]).is_symlink()
    assert Path(record["bubble_vis_jpg"]).is_symlink()
    assert Path(record["regions"]["regions"][0]["mask_ref"]).is_symlink()


def test_empty_region_cache_does_not_keep_stale_analysis_visual(tmp_path, monkeypatch):
    session_id = "empty_region_cache_visual"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    run_load_cache_hydration_immediately(monkeypatch)
    source = tmp_path / "source" / "page1.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(source)
    cache_dir = main_v2.analysis_cache_dir(image_hash)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "text_groups.json").write_text('[{"text_group_id": 1}]', encoding="utf-8")
    (cache_dir / "regions.json").write_text('{"regions": []}', encoding="utf-8")
    (cache_dir / "indexed_sheet.jpg").write_bytes(b"sheet")
    (cache_dir / "masked.jpg").write_bytes(b"masked")
    (cache_dir / "bubble_vis.jpg").write_bytes(b"stale boxed visual")
    (cache_dir / "meta.json").write_text(
        f'{{"analysis_cache_version": "{main_v2.ANALYSIS_CACHE_VERSION}"}}',
        encoding="utf-8",
    )

    records = main_v2.copy_images_into_session([source], session_id)
    record = main_v2.image_record(session_id, Path(records[0]["input_path"]), image_hash)

    assert record["regions"]["regions"] == []
    assert Path(record["bubble_vis_jpg"]).read_bytes() == source.read_bytes()
    assert not Path(record["bubble_vis_jpg"]).is_symlink()


def test_unlink_symlinked_analysis_outputs_before_regeneration(tmp_path, monkeypatch):
    session_id = "analysis_link_unlink"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    record = main_v2.image_record(session_id, image_path)
    cache_file = tmp_path / "cache" / "masked.jpg"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"cached")
    masked = Path(record["masked_jpg"])
    masked.parent.mkdir(parents=True)
    masked.symlink_to(cache_file)

    main_v2._unlink_symlinked_analysis_outputs(record)

    assert not masked.exists()
    assert cache_file.exists()


def test_unlink_symlinked_analysis_outputs_ignores_broken_symlink(tmp_path, monkeypatch):
    session_id = "analysis_broken_link_unlink"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    record = main_v2.image_record(session_id, image_path)
    missing_cache_file = tmp_path / "cache" / "missing_mask.png"
    mask_path = Path(record["work_dir"]) / "masks" / "page1_R1_mask.png"
    mask_path.parent.mkdir(parents=True)
    mask_path.symlink_to(missing_cache_file)

    main_v2._unlink_symlinked_analysis_outputs(record)

    assert not mask_path.exists()
    assert not mask_path.is_symlink()


def test_stream_analyze_skips_when_analysis_cache_exists(tmp_path, monkeypatch):
    session_id = "analysis_cache_stream"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(image_path)
    cache_dir = main_v2.analysis_cache_dir(image_hash)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "text_groups.json").write_text("[]", encoding="utf-8")
    (cache_dir / "regions.json").write_text('{"regions": []}', encoding="utf-8")
    (cache_dir / "indexed_sheet.jpg").write_bytes(b"sheet")
    (cache_dir / "masked.jpg").write_bytes(b"masked")
    (cache_dir / "bubble_vis.jpg").write_bytes(b"vis")
    (cache_dir / "meta.json").write_text(
        f'{{"analysis_cache_version": "{main_v2.ANALYSIS_CACHE_VERSION}"}}',
        encoding="utf-8",
    )
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path), "image_sha256": image_hash}]
    main_v2.save_session(session)

    def fail_run_single_image(*args, **kwargs):
        raise AssertionError("analysis cache should skip pipeline execution")

    monkeypatch.setattr(main_v2, "run_single_image", fail_run_single_image)

    events = list(main_v2.stream_analyze(main_v2.AnalyzeRequest(session_id=session_id)))

    assert any("OCR 전단계 분석 캐시 사용" in event for event in events)
    assert any("분석 캐시 복원" in event for event in events)


def test_import_hydrates_existing_session_cache_for_initial_images(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    run_load_cache_hydration_immediately(monkeypatch)
    old_id = "old_session"
    old_image = tmp_path / "sessions" / old_id / "input" / "page1.jpg"
    old_image.parent.mkdir(parents=True)
    old_image.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(old_image)
    old_session = main_v2.ensure_session(old_id)
    old_session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(old_image), "image_sha256": image_hash}]
    main_v2.save_session(old_session)
    old_record = main_v2.image_record(old_id, old_image, image_hash)
    for key, content in {
        "text_groups_json": b"[]",
        "regions_json": b'{"regions": []}',
        "indexed_sheet_jpg": b"sheet",
        "masked_jpg": b"masked",
        "bubble_vis_jpg": b"vis",
    }.items():
        path = Path(old_record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    new_source = tmp_path / "source" / "page1.jpg"
    new_source.parent.mkdir(parents=True)
    new_source.write_bytes(b"same image")

    records = main_v2.copy_images_into_session([new_source], "new_session")
    new_record = main_v2.image_record("new_session", Path(records[0]["input_path"]), image_hash)

    assert Path(new_record["regions_json"]).exists()
    assert Path(new_record["indexed_sheet_jpg"]).read_bytes() == b"sheet"


def test_import_hydrates_guidance_cache_without_reapplying_step3(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    run_load_cache_hydration_immediately(monkeypatch)
    old_id = "old_guidance"
    old_image = tmp_path / "sessions" / old_id / "input" / "00005.jpg"
    old_image.parent.mkdir(parents=True)
    old_image.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(old_image)
    old_session = main_v2.ensure_session(old_id)
    old_session["images"] = [{"id": "00005", "name": "00005.jpg", "input_path": str(old_image), "image_sha256": image_hash}]
    main_v2.save_session(old_session)
    old_record = main_v2.image_record(old_id, old_image, image_hash)
    Path(old_record["guidance_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(old_record["guidance_json"]).write_text('{"disabled_tg_ids": [1]}', encoding="utf-8")
    calls = []

    def fake_reapply(session_id_arg, record_arg, log_callback):
        calls.append((session_id_arg, record_arg["id"]))
        return main_v2.image_record(session_id_arg, Path(record_arg["input_path"]), image_hash)

    monkeypatch.setattr(main_v2, "reapply_guidance_if_present", fake_reapply)
    new_source = tmp_path / "source" / "00005.jpg"
    new_source.parent.mkdir(parents=True)
    new_source.write_bytes(b"same image")

    records = main_v2.copy_images_into_session([new_source], "new_guidance")
    new_record = main_v2.image_record("new_guidance", Path(records[0]["input_path"]), image_hash)

    assert new_record["guidance"]["disabled_tg_ids"] == [1]
    assert calls == []


def test_import_hydrates_previous_version_analysis_cache_before_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    run_load_cache_hydration_immediately(monkeypatch)
    old_id = "old_version_guided"
    old_image = tmp_path / "sessions" / old_id / "input" / "page1.jpg"
    old_image.parent.mkdir(parents=True)
    old_image.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(old_image)
    old_session = main_v2.ensure_session(old_id)
    old_session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(old_image), "image_sha256": image_hash}]
    main_v2.save_session(old_session)

    cache_dir = main_v2.analysis_cache_dir(image_hash)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "text_groups.json").write_text('[{"text_group_id": 1}]', encoding="utf-8")
    (cache_dir / "regions.json").write_text('{"regions": [{"region_id": 1}]}', encoding="utf-8")
    (cache_dir / "indexed_sheet.jpg").write_bytes(b"sheet")
    (cache_dir / "masked.jpg").write_bytes(b"masked")
    (cache_dir / "bubble_vis.jpg").write_bytes(b"vis")
    (cache_dir / "meta.json").write_text(
        '{"analysis_cache_version": "2026-05-29-seed-bright-component-v1"}',
        encoding="utf-8",
    )
    main_v2.guidance_cache_path(image_hash).write_text('{"disabled_tg_ids": [1]}', encoding="utf-8")
    reapplied = []

    def fake_reapply(session_id_arg, record_arg, log_callback):
        reapplied.append(record_arg["id"])
        return main_v2.image_record(session_id_arg, Path(record_arg["input_path"]), record_arg["image_sha256"])

    monkeypatch.setattr(main_v2, "reapply_guidance_if_present", fake_reapply)

    new_source = tmp_path / "source" / "page1.jpg"
    new_source.parent.mkdir(parents=True)
    new_source.write_bytes(b"same image")

    records = main_v2.copy_images_into_session([new_source], "new_version_guided")
    new_record = main_v2.image_record("new_version_guided", Path(records[0]["input_path"]), image_hash)

    assert new_record["guidance"]["disabled_tg_ids"] == [1]
    assert new_record["regions"]["regions"] == [{"region_id": 1}]
    assert Path(new_record["indexed_sheet_jpg"]).exists()
    assert Path(new_record["masked_jpg"]).exists()
    assert reapplied == ["page1"]


def test_import_reapplies_guidance_after_analysis_cache_hydrate(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    run_load_cache_hydration_immediately(monkeypatch)
    old_id = "old_guided_analysis"
    old_image = tmp_path / "sessions" / old_id / "input" / "page1.jpg"
    old_image.parent.mkdir(parents=True)
    old_image.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(old_image)
    old_session = main_v2.ensure_session(old_id)
    old_session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(old_image), "image_sha256": image_hash}]
    main_v2.save_session(old_session)
    old_record = main_v2.image_record(old_id, old_image, image_hash)
    for key, content in {
        "text_groups_json": b'[{"text_group_id": 1}]',
        "regions_json": b'{"regions": [{"region_id": 1}]}',
        "indexed_sheet_jpg": b"baseline sheet",
        "masked_jpg": b"baseline masked",
        "bubble_vis_jpg": b"baseline vis",
        "guidance_json": b'{"manual_seed_points": [{"point": [10, 20]}]}',
    }.items():
        path = Path(old_record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    calls = []

    def fake_reapply(session_id_arg, record_arg, log_callback):
        calls.append((session_id_arg, record_arg["id"]))
        main_v2._unlink_symlinked_analysis_outputs(record_arg)
        Path(record_arg["indexed_sheet_jpg"]).write_bytes(b"manual sheet")
        Path(record_arg["masked_jpg"]).write_bytes(b"manual masked")
        Path(record_arg["bubble_vis_jpg"]).write_bytes(b"manual vis")
        return main_v2.image_record(session_id_arg, Path(record_arg["input_path"]), record_arg["image_sha256"])

    monkeypatch.setattr(main_v2, "reapply_guidance_if_present", fake_reapply)
    new_source = tmp_path / "source" / "page1.jpg"
    new_source.parent.mkdir(parents=True)
    new_source.write_bytes(b"same image")

    records = main_v2.copy_images_into_session([new_source], "new_guided_analysis")
    new_record = main_v2.image_record("new_guided_analysis", Path(records[0]["input_path"]), image_hash)

    assert calls == [("new_guided_analysis", "page1")]
    assert Path(new_record["indexed_sheet_jpg"]).read_bytes() == b"manual sheet"
    assert not Path(new_record["indexed_sheet_jpg"]).is_symlink()
    assert new_record["guidance"]["manual_seed_points"] == [{"point": [10, 20]}]


def test_guidance_output_cache_restores_apply_outputs_without_rerun(tmp_path, monkeypatch):
    session_id = "guidance_output_cache"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(image_path)
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path), "image_sha256": image_hash}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path, image_hash)
    guidance = {"manual_seed_points": [{"point": [10, 20]}]}
    for key, content in {
        "text_groups_json": b'[{"text_group_id": 1}]',
        "guidance_json": json.dumps(guidance).encode("utf-8"),
        "effective_text_groups_json": b'[{"text_group_id": 1, "manual": true}]',
        "guidance_report_json": b'{"changed": true}',
        "regions_json": b'{"regions": [{"region_id": 1}]}',
        "indexed_sheet_jpg": b"manual sheet",
        "masked_jpg": b"manual masked",
        "bubble_vis_jpg": b"manual vis",
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert main_v2.store_guidance_output_cache(record, image_hash, guidance) is True
    for key in main_v2.GUIDANCE_OUTPUT_REQUIRED:
        Path(record[key]).unlink()

    def fail_rerun(*args, **kwargs):
        raise AssertionError("guidance output cache should skip Step 3 rerun")

    monkeypatch.setattr(main_v2, "run_step3_with_effective_text_groups", fail_rerun)

    refreshed = main_v2.reapply_guidance_if_present(session_id, record, lambda _message: None)

    assert refreshed is not None
    assert Path(record["indexed_sheet_jpg"]).read_bytes() == b"manual sheet"
    assert Path(record["masked_jpg"]).read_bytes() == b"manual masked"
    assert Path(record["bubble_vis_jpg"]).read_bytes() == b"manual vis"
    assert Path(record["indexed_sheet_jpg"]).is_symlink()


def test_session_images_prefers_guidance_outputs_over_existing_baseline(tmp_path, monkeypatch):
    session_id = "guidance_preferred_on_load"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(image_path)
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path), "image_sha256": image_hash}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path, image_hash)
    guidance = {"manual_seed_points": [{"point": [10, 20]}]}
    for key, content in {
        "guidance_json": json.dumps(guidance).encode("utf-8"),
        "text_groups_json": b'[{"text_group_id": 1}]',
        "effective_text_groups_json": b'[{"text_group_id": 1, "manual": true}]',
        "guidance_report_json": b'{"changed": true}',
        "regions_json": b'{"regions": [{"region_id": 1}]}',
        "indexed_sheet_jpg": b"manual sheet",
        "masked_jpg": b"manual masked",
        "bubble_vis_jpg": b"manual vis",
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert main_v2.store_guidance_output_cache(record, image_hash, guidance) is True
    for key, content in {
        "regions_json": b'{"regions": [{"region_id": 99}]}',
        "indexed_sheet_jpg": b"baseline sheet",
        "masked_jpg": b"baseline masked",
        "bubble_vis_jpg": b"baseline vis",
    }.items():
        Path(record[key]).write_bytes(content)

    images = main_v2.session_images(session_id)
    refreshed = images[0]

    assert Path(refreshed["indexed_sheet_jpg"]).read_bytes() == b"manual sheet"
    assert Path(refreshed["masked_jpg"]).read_bytes() == b"manual masked"
    assert Path(refreshed["bubble_vis_jpg"]).read_bytes() == b"manual vis"
    assert refreshed["regions"]["regions"] == [{"region_id": 1}]


def test_import_defers_session_backfill_after_initial_sync_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    started = []
    monkeypatch.setattr(main_v2, "start_background_load_cache_hydration", lambda sid, items: started.append((sid, [item["id"] for item in items])))

    old_id = "old_lazy"
    old_image = tmp_path / "sessions" / old_id / "input" / "page1.jpg"
    old_image.parent.mkdir(parents=True)
    old_image.write_bytes(b"same image 1")
    image_hash = main_v2.file_sha256(old_image)
    old_session = main_v2.ensure_session(old_id)
    old_session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(old_image), "image_sha256": image_hash}]
    main_v2.save_session(old_session)
    old_record = main_v2.image_record(old_id, old_image, image_hash)
    for key, content in {
        "text_groups_json": b"[]",
        "regions_json": b'{"regions": []}',
        "indexed_sheet_jpg": b"sheet",
        "masked_jpg": b"masked",
        "bubble_vis_jpg": b"vis",
    }.items():
        path = Path(old_record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    source1 = tmp_path / "source" / "page1.jpg"
    source2 = tmp_path / "source" / "page2.jpg"
    source1.parent.mkdir(parents=True)
    source1.write_bytes(b"same image 1")
    source2.write_bytes(b"same image 2")

    import_hydrate = main_v2.hydrate_load_cache_for_records
    monkeypatch.setattr(main_v2, "hydrate_load_cache_for_records", lambda *_args, **_kwargs: None)
    records = main_v2.copy_images_into_session([source1, source2], "new_lazy")
    import_hydrate("new_lazy", records, sync_limit=1)
    first_record = main_v2.image_record("new_lazy", Path(records[0]["input_path"]), records[0]["image_sha256"])
    second_record = main_v2.image_record("new_lazy", Path(records[1]["input_path"]), records[1]["image_sha256"])

    assert Path(first_record["regions_json"]).exists()
    assert not Path(second_record["regions_json"]).exists()
    assert started == [("new_lazy", ["page2"])]


def test_import_starts_load_cache_hydration_in_background(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    calls = []
    monkeypatch.setattr(
        main_v2,
        "hydrate_load_cache_for_records",
        lambda session_id, records, sync_limit=None: calls.append((session_id, [item["id"] for item in records], sync_limit)),
    )
    source = tmp_path / "source" / "page1.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image")

    records = main_v2.copy_images_into_session([source], "background_import")

    assert [item["id"] for item in records] == ["page1"]
    assert calls == [("background_import", ["page1"], main_v2.IMPORT_CACHE_SYNC_LIMIT)]


def test_import_links_session_input_to_hash_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    source = tmp_path / "source" / "page1.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(source)

    records = main_v2.copy_images_into_session([source], "input_cache_import")
    input_path = Path(records[0]["input_path"])
    cache_path = main_v2.input_cache_path(image_hash, ".jpg")

    assert records[0]["image_sha256"] == image_hash
    assert cache_path.read_bytes() == b"same image"
    assert input_path.read_bytes() == b"same image"
    assert input_path.is_symlink()
    assert input_path.resolve() == cache_path


def test_upload_replaces_session_input_with_hash_cache_link(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    client = TestClient(main_v2.create_app())

    response = client.post(
        "/api/upload?session_id=upload_cache",
        files=[("files", ("page1.jpg", b"upload image", "image/jpeg"))],
    )

    assert response.status_code == 200
    record = response.json()["images"][0]
    input_path = Path(record["input_path"])
    cache_path = main_v2.input_cache_path(record["image_sha256"], ".jpg")
    assert cache_path.read_bytes() == b"upload image"
    assert input_path.is_symlink()
    assert input_path.resolve() == cache_path


def test_clear_guidance_outputs_removes_manual_files_without_restoring_cache(tmp_path, monkeypatch):
    session_id = "clear_guidance"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(image_path)
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path), "image_sha256": image_hash}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path, image_hash)
    main_v2.guidance_cache_path(image_hash).parent.mkdir(parents=True)
    main_v2.guidance_cache_path(image_hash).write_text('{"disabled_tg_ids": [1]}', encoding="utf-8")

    cache_dir = main_v2.analysis_cache_dir(image_hash)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "text_groups.json").write_text('[{"text_group_id": 1}]', encoding="utf-8")
    (cache_dir / "regions.json").write_text('{"regions": [{"region_id": 1}]}', encoding="utf-8")
    (cache_dir / "indexed_sheet.jpg").write_bytes(b"baseline sheet")
    (cache_dir / "masked.jpg").write_bytes(b"baseline masked")
    (cache_dir / "bubble_vis.jpg").write_bytes(b"baseline vis")
    (cache_dir / "meta.json").write_text(
        f'{{"analysis_cache_version": "{main_v2.ANALYSIS_CACHE_VERSION}"}}',
        encoding="utf-8",
    )

    for key, content in {
        "guidance_json": b'{"manual_regions": []}',
        "guidance_report_json": b'{"changed": true}',
        "effective_text_groups_json": b'[{"text_group_id": 99}]',
        "regions_json": b'{"regions": [{"region_id": 99}]}',
        "indexed_sheet_jpg": b"manual sheet",
        "masked_jpg": b"manual masked",
        "bubble_vis_jpg": b"manual vis",
        "integrated_json": b'{"stale": true}',
        "final_jpg": b"stale final",
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    mask_dir = Path(record["work_dir"]) / "masks"
    mask_dir.mkdir(parents=True)
    stale_mask = mask_dir / "page1_R1_mask.png"
    stale_mask.write_bytes(b"stale mask")

    refreshed = main_v2.clear_guidance_outputs(session_id, "page1")

    assert refreshed["guidance"] == {}
    assert refreshed["guidance_report"] == {}
    assert refreshed["effective_text_groups"] == []
    assert not Path(record["regions_json"]).exists()
    assert not Path(record["indexed_sheet_jpg"]).exists()
    assert not Path(record["masked_jpg"]).exists()
    assert not Path(record["bubble_vis_jpg"]).exists()
    assert not Path(record["integrated_json"]).exists()
    assert not Path(record["final_jpg"]).exists()
    assert not stale_mask.exists()
    assert not main_v2.guidance_cache_path(image_hash).exists()
    assert main_v2.guidance_cache_tombstone_path(image_hash).exists()
    assert Path(record["guidance_clear_json"]).exists()

    images = main_v2.session_images(session_id)

    assert images[0]["indexed_sheet_url"] is None
    assert not Path(record["indexed_sheet_jpg"]).exists()


def test_build_session_export_zip_uses_final_then_original_fallback(tmp_path, monkeypatch):
    session_id = "export_session"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    data = main_v2.ensure_session(session_id)
    data["project_name"] = "ORE2 1권"
    base = main_v2.session_dir(session_id)
    (base / "input" / "page1.jpg").write_bytes(b"input")
    (base / "input" / "page2.jpg").write_bytes(b"original")
    (base / "work" / "json").mkdir(parents=True)
    (base / "work" / "json" / "page1_regions.json").write_text("{}", encoding="utf-8")
    (base / "output" / "final").mkdir(parents=True, exist_ok=True)
    (base / "output" / "final" / "page1_final.jpg").write_bytes(b"final")
    data["images"] = [
        {"id": "page1", "name": "page1.jpg", "input_path": str(base / "input" / "page1.jpg")},
        {"id": "page2", "name": "page2.jpg", "input_path": str(base / "input" / "page2.jpg")},
    ]
    main_v2.save_session(data)

    zip_path = main_v2.build_session_export_zip(session_id)

    assert zip_path.exists()
    assert zip_path.name == "ORE2 1권.zip"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert zf.read("final/page1.jpg") == b"final"
        assert zf.read("final/page2.jpg") == b"original"
    assert "session.json" not in names
    assert "input/page1.jpg" not in names
    assert "work/json/page1_regions.json" not in names
    assert "output/final/page1_final.jpg" not in names
    assert not any(name.startswith("exports/") for name in names)


def test_remove_session_image_updates_manifest_and_removes_session_input(tmp_path, monkeypatch):
    session_id = "remove_image"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)

    images = main_v2.remove_session_image(session_id, "page1")
    data = main_v2.ensure_session(session_id)

    assert images == []
    assert data["images"] == []
    assert not image_path.exists()


def test_remove_image_endpoint_returns_refreshed_session_state(tmp_path, monkeypatch):
    session_id = "remove_image_endpoint"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = tmp_path / "sessions" / session_id / "input"
    page1 = base / "page1.jpg"
    page2 = base / "page2.jpg"
    base.mkdir(parents=True)
    page1.write_bytes(b"image1")
    page2.write_bytes(b"image2")
    session = main_v2.ensure_session(session_id)
    session["images"] = [
        {"id": "page1", "name": "page1.jpg", "input_path": str(page1), "image_sha256": "hash1"},
        {"id": "page2", "name": "page2.jpg", "input_path": str(page2), "image_sha256": "hash2"},
    ]
    main_v2.save_session(session)

    response = TestClient(main_v2.create_app()).post(
        "/api/image/remove",
        json={"session_id": session_id, "image_id": "page1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data["images"]] == ["page2"]
    assert [item["id"] for item in data["session"]["images"]] == ["page2"]
    assert data["sessions"][0]["session_id"] == session_id
    assert data["sessions"][0]["image_count"] == 1
    assert not page1.exists()
    assert page2.exists()


def test_guidance_additive_change_reuses_existing_unboxed_mask(tmp_path, monkeypatch):
    session_id = "guidance_fast_path"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (100, 120), "white").save(image_path)
    session = main_v2.ensure_session(session_id)
    session["images"] = [main_v2.image_manifest_item(image_path, main_v2.file_sha256(image_path))]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path)
    Path(record["text_groups_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["text_groups_json"]).write_text("[]", encoding="utf-8")
    Path(record["guidance_json"]).write_text('{"manual_seed_points": [{"point": [10, 20]}]}', encoding="utf-8")
    Path(record["regions_json"]).write_text('{"regions": []}', encoding="utf-8")
    Path(record["masked_jpg"]).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 120), "white").save(record["masked_jpg"])
    captured = {}

    def fake_build_effective_text_groups(*args, **kwargs):
        Path(record["effective_text_groups_json"]).write_text("[]", encoding="utf-8")
        return {"reports": []}

    def fake_build_dialogue_sheet(**kwargs):
        captured.update(kwargs)
        Path(kwargs["output_json_path"]).write_text('{"regions": [], "manual_guidance_report": {}}', encoding="utf-8")
        Path(kwargs["indexed_output_sheet_path"]).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1, 1), "white").save(kwargs["indexed_output_sheet_path"])
        Image.new("RGB", (100, 120), "white").save(kwargs["output_masked_path"])
        if kwargs.get("output_vis_path"):
            Path(kwargs["output_vis_path"]).parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (100, 120), "white").save(kwargs["output_vis_path"])
        return {"regions": [], "manual_guidance_report": {}, "reused_unboxed_inpaint": True}

    monkeypatch.setattr(main_v2, "build_effective_text_groups", fake_build_effective_text_groups)
    monkeypatch.setattr("pipeline.dialogue_extractor.build_dialogue_sheet", fake_build_dialogue_sheet)

    refreshed = main_v2.run_step3_with_effective_text_groups(
        session_id,
        "page1",
        {"manual_seed_points": [{"point": [10, 20]}, {"point": [30, 40]}]},
    )

    assert captured["reuse_existing_unboxed_inpaint"] is True
    assert captured["base_masked_path"] == refreshed["masked_jpg"]
    assert refreshed["guidance_report"]["fast_path"]["reused_unboxed_inpaint"] is True


def test_guidance_destructive_change_disables_unboxed_mask_reuse(tmp_path, monkeypatch):
    previous = {"manual_seed_points": [{"point": [10, 20]}], "suppressed_regions": []}
    current = {"manual_seed_points": [], "suppressed_regions": []}
    assert main_v2.guidance_allows_unboxed_mask_reuse(previous, current) is False
    current = {"manual_seed_points": [{"point": [10, 20]}], "suppressed_regions": [{"region_id": 1}]}
    assert main_v2.guidance_allows_unboxed_mask_reuse(previous, current) is False


def test_export_session_endpoint_returns_zip(tmp_path, monkeypatch):
    session_id = "export_endpoint"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    data = main_v2.ensure_session(session_id)
    data["project_name"] = "My Project"
    main_v2.save_session(data)
    (base / "output" / "final" / "page1_final.jpg").write_bytes(b"final")

    response = TestClient(main_v2.create_app()).get(f"/api/session/{session_id}/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "My%20Project.zip" in response.headers["content-disposition"]
    assert response.content.startswith(b"PK")


def test_generate_missing_finals_with_openai_writes_final_outputs(tmp_path, monkeypatch):
    session_id = "export_generate_missing"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    input_path = base / "input" / "page1.jpg"
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(input_path)}]
    main_v2.save_session(session)

    calls = []

    def fake_redraw(
        image_path,
        output_path,
        model_name,
        prompt_guide="",
        text_model_name="",
        analyze_first=False,
        quality="medium",
        size="match_input",
        output_format="jpeg",
        output_compression=None,
        log_callback=None,
    ):
        calls.append((
            Path(image_path).name,
            Path(output_path).name,
            model_name,
            prompt_guide,
            text_model_name,
            analyze_first,
            quality,
            size,
            output_format,
            output_compression,
        ))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"generated")
        if log_callback:
            log_callback("generated")
        return True

    monkeypatch.setattr(main_v2, "redraw_image_with_openai", fake_redraw)

    result = main_v2.generate_missing_finals_with_openai(
        main_v2.GenerateMissingFinalRequest(
            session_id=session_id,
            image_ids=["page1"],
            model="gpt-image-2",
            prompt_guide="작품명: 테스트",
            text_model="gpt-5.4-mini",
            analyze_first=True,
            quality="high",
            size="match_input",
            output_format="jpeg",
            output_compression=88,
        )
    )

    assert result["ok"] is True
    assert result["generated"] == ["page1"]
    assert calls == [("page1.jpg", "page1_final.jpg", "gpt-image-2", "작품명: 테스트", "gpt-5.4-mini", True, "high", "match_input", "jpeg", 88)]
    assert (base / "output" / "final" / "page1_final.jpg").read_bytes() == b"generated"
    assert result["images"][0]["final_url"]
    recovery = main_v2.load_json(base / "work" / "json" / "page1_export_recovery.json")
    assert recovery["status"] == "generated"
    assert recovery["source"] == "image_generation_fallback"
    assert recovery["attempts"] == 1
    assert recovery["model"] == "gpt-image-2"
    assert result["images"][0]["has_image_generation_fallback"] is True
    assert result["images"][0]["export_recovery"]["prompt_guide"] == "작품명: 테스트"


def test_generate_missing_finals_uses_requested_workers(tmp_path, monkeypatch):
    session_id = "export_generate_missing_workers"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    input_dir = base / "input"
    input_dir.mkdir(parents=True)
    images = []
    for index in range(3):
        image_id = f"page{index + 1}"
        input_path = input_dir / f"{image_id}.jpg"
        input_path.write_bytes(b"image")
        images.append({"id": image_id, "name": input_path.name, "input_path": str(input_path)})
    session = main_v2.ensure_session(session_id)
    session["images"] = images
    main_v2.save_session(session)

    lock = threading.Lock()
    barrier = threading.Barrier(2)
    active = 0
    max_active = 0
    call_count = 0

    def fake_redraw(
        image_path,
        output_path,
        model_name,
        prompt_guide="",
        text_model_name="",
        analyze_first=False,
        quality="medium",
        size="match_input",
        output_format="jpeg",
        output_compression=None,
        log_callback=None,
    ):
        nonlocal active, max_active, call_count
        with lock:
            active += 1
            call_count += 1
            current_call = call_count
            max_active = max(max_active, active)
        if current_call <= 2:
            barrier.wait(timeout=2)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"generated")
        with lock:
            active -= 1
        return True

    monkeypatch.setattr(main_v2, "redraw_image_with_openai", fake_redraw)

    result = main_v2.generate_missing_finals_with_openai(
        main_v2.GenerateMissingFinalRequest(
            session_id=session_id,
            image_ids=[item["id"] for item in images],
            max_workers=2,
        )
    )

    assert result["ok"] is True
    assert sorted(result["generated"]) == ["page1", "page2", "page3"]
    assert max_active == 2
    assert "이미지 생성 worker 2개로 실행" in result["logs"]


def test_generate_missing_finals_stream_emits_progress_logs(tmp_path, monkeypatch):
    session_id = "export_generate_missing_stream"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    input_path = base / "input" / "page1.jpg"
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(input_path)}]
    main_v2.save_session(session)

    def fake_redraw(
        image_path,
        output_path,
        model_name,
        prompt_guide="",
        text_model_name="",
        analyze_first=False,
        quality="medium",
        size="match_input",
        output_format="jpeg",
        output_compression=None,
        log_callback=None,
    ):
        if log_callback:
            log_callback("OpenAI 요청 전")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"generated")
        return True

    monkeypatch.setattr(main_v2, "redraw_image_with_openai", fake_redraw)

    response = TestClient(main_v2.create_app()).post(
        "/api/export/generate-missing/stream",
        json={"session_id": session_id, "image_ids": ["page1"]},
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert any(event.get("type") == "log" and "이미지 생성 시작" in event.get("message", "") for event in events)
    assert any(event.get("type") == "log" and "OpenAI 요청 전" in event.get("message", "") for event in events)
    complete = [event for event in events if event.get("type") == "complete"][-1]
    assert complete["generated"] == ["page1"]
    assert complete["images"][0]["final_url"]


def test_gpt_image_2_size_from_image_uses_nearest_16px_multiple(tmp_path):
    from pipeline.openai_image_redraw import gpt_image_2_size_from_image

    image_path = tmp_path / "page.jpg"
    Image.new("RGB", (1350, 1920), "white").save(image_path)

    assert gpt_image_2_size_from_image(image_path) == "1344x1920"


def test_overlay_apply_saves_guidance_and_runs_renderer(tmp_path, monkeypatch):
    session_id = "overlay_apply"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path)
    for key, content in {
        "integrated_json": b"[]",
        "masked_jpg": b"masked",
        "regions_json": b'{"regions": []}',
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    calls = []

    # overlay 렌더는 subprocess가 아니라 in-process apply_text_overlay로 호출된다.
    def fake_apply_text_overlay(masked, integrated, final, **kwargs):
        calls.append(kwargs)
        Path(final).parent.mkdir(parents=True, exist_ok=True)
        Path(final).write_bytes(b"final")
        layout_path = kwargs.get("overlay_layout_output")
        if layout_path is not None:
            Path(layout_path).parent.mkdir(parents=True, exist_ok=True)
            Path(layout_path).write_text('{"regions": {"1": {"text_layout": "실제\\n배치"}}}', encoding="utf-8")
        return 1

    monkeypatch.setattr(main_v2, "apply_text_overlay", fake_apply_text_overlay)

    response = TestClient(main_v2.create_app()).post(
        "/api/overlay/apply",
        json={
            "session_id": session_id,
            "image_id": "page1",
            "overlay_guidance": {"regions": {"1": {"font_style": "handwritten"}}},
        },
    )

    assert response.status_code == 200
    saved = main_v2.load_json(Path(record["overlay_guidance_json"]), default={})
    assert saved["regions"]["1"]["font_style"] == "handwritten"
    assert any(kwargs.get("overlay_guidance_path") is not None for kwargs in calls)
    assert any(kwargs.get("overlay_layout_output") is not None for kwargs in calls)
    assert response.json()["image"]["overlay_layout"]["regions"]["1"]["text_layout"] == "실제\n배치"


def test_overlay_apply_replace_drops_removed_keys(tmp_path, monkeypatch):
    """replace=True는 편집기 전체 상태를 그대로 저장한다(실행 취소로 지운 키가 되살아나지 않는다)."""
    session_id = "overlay_guidance_replace"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path)
    for key, content in {
        "integrated_json": b"[]",
        "masked_jpg": b"masked",
        "regions_json": b'{"regions": []}',
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    Path(record["overlay_guidance_json"]).write_text(
        '{"regions": {"1": {"font_style": "impact", "safe_text_rect": [1, 2, 30, 40], "text_override": "원문"}}}',
        encoding="utf-8",
    )

    def fake_apply_text_overlay(masked, integrated, final, **kwargs):
        Path(final).parent.mkdir(parents=True, exist_ok=True)
        Path(final).write_bytes(b"final")
        return 1

    monkeypatch.setattr(main_v2, "apply_text_overlay", fake_apply_text_overlay)

    response = TestClient(main_v2.create_app()).post(
        "/api/overlay/apply",
        json={
            "session_id": session_id,
            "image_id": "page1",
            "overlay_guidance": {"regions": {"1": {"text_override": "원문"}}, "eraser_rects": []},
            "replace": True,
        },
    )

    assert response.status_code == 200
    saved = main_v2.load_json(Path(record["overlay_guidance_json"]), default={})
    assert saved["regions"]["1"] == {"text_override": "원문"}


def test_overlay_apply_merges_text_override_without_dropping_font_style(tmp_path, monkeypatch):
    session_id = "overlay_guidance_merge"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path)
    for key, content in {
        "integrated_json": b"[]",
        "masked_jpg": b"masked",
        "regions_json": b'{"regions": []}',
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    Path(record["overlay_guidance_json"]).write_text(
        '{"regions": {"1": {"font_style": "handwritten", "font_size_delta": -3}}}',
        encoding="utf-8",
    )

    def fake_apply_text_overlay(masked, integrated, final, **kwargs):
        Path(final).parent.mkdir(parents=True, exist_ok=True)
        Path(final).write_bytes(b"final")
        return 1

    monkeypatch.setattr(main_v2, "apply_text_overlay", fake_apply_text_overlay)

    response = TestClient(main_v2.create_app()).post(
        "/api/overlay/apply",
        json={
            "session_id": session_id,
            "image_id": "page1",
            "overlay_guidance": {"regions": {"1": {"text_override": "수정문"}}},
        },
    )

    assert response.status_code == 200
    saved = main_v2.load_json(Path(record["overlay_guidance_json"]), default={})
    assert saved["regions"]["1"]["font_style"] == "handwritten"
    assert saved["regions"]["1"]["font_size_delta"] == -3
    assert saved["regions"]["1"]["text_override"] == "수정문"


def test_run_final_stream_runs_all_images_and_emits_progress(tmp_path, monkeypatch):
    session_id = "run_final_stream"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_a = base / "input" / "page1.jpg"
    image_b = base / "input" / "page2.jpg"
    image_a.parent.mkdir(parents=True)
    image_a.write_bytes(b"image a")
    image_b.write_bytes(b"image b")
    session = main_v2.ensure_session(session_id)
    session["images"] = [
        {"id": "page1", "name": "page1.jpg", "input_path": str(image_a)},
        {"id": "page2", "name": "page2.jpg", "input_path": str(image_b)},
    ]
    main_v2.save_session(session)
    for image_path in (image_a, image_b):
        record = main_v2.image_record(session_id, image_path)
        for key, content in {
            "text_groups_json": b"[]",
            "regions_json": b'{"analysis_status": "complete", "ocr_region_count": 1, "regions": [{"region_id": 1}]}',
            "indexed_sheet_jpg": b"sheet",
            "masked_jpg": b"masked",
        }.items():
            path = Path(record[key])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def fake_run_subprocess(cmd, timeout=300):
        if "pipeline.gemini_ocr_translate_v5" in cmd:
            Path(cmd[5]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[5]).write_text("[]", encoding="utf-8")
        return "ok"

    def fake_run_subprocess_stream(cmd, timeout=300, log_callback=None):
        if log_callback is not None:
            log_callback("fake subprocess progress")
        return fake_run_subprocess(cmd, timeout)

    # overlay 렌더는 subprocess가 아니라 in-process apply_text_overlay로 호출된다.
    def fake_apply_text_overlay(masked, integrated, final, **kwargs):
        Path(final).parent.mkdir(parents=True, exist_ok=True)
        Path(final).write_bytes(b"final")
        return 1

    monkeypatch.setattr(main_v2, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(main_v2, "run_subprocess_stream", fake_run_subprocess_stream)
    monkeypatch.setattr(main_v2, "apply_text_overlay", fake_apply_text_overlay)

    events = list(main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=session_id, image_id="all", max_workers=2)))

    assert any("OCR/Translation worker 2개로 실행" in event for event in events)
    assert any("fake subprocess progress" in event for event in events)
    status_events = [json.loads(event) for event in events if json.loads(event).get("type") == "image_status"]
    assert sum(event.get("status") == "done" for event in status_events) == 2
    assert any('"type": "complete"' in event and '"ok": true' in event for event in events)


def test_run_final_stream_persists_failed_ocr_status(tmp_path, monkeypatch):
    session_id = "run_final_stream_failed_status"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_path = base / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)

    def fail_run_final_for_item(*args, **kwargs):
        raise RuntimeError("Gemini timeout")

    monkeypatch.setattr(main_v2, "run_final_for_item", fail_run_final_for_item)

    events = list(main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=session_id, image_id="all")))
    record = main_v2.image_record(session_id, image_path)

    assert any('"status": "failed"' in event for event in events)
    assert record["ocr_status"]["status"] == "failed"
    assert record["ocr_status"]["message"] == "Gemini timeout"
    assert main_v2.session_images(session_id)[0]["ocr_status"]["status"] == "failed"


def test_run_final_stream_skips_images_with_existing_integrated_result(tmp_path, monkeypatch):
    session_id = "run_final_stream_skip_integrated"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_a = base / "input" / "page1.jpg"
    image_b = base / "input" / "page2.jpg"
    image_a.parent.mkdir(parents=True)
    image_a.write_bytes(b"image a")
    image_b.write_bytes(b"image b")
    session = main_v2.ensure_session(session_id)
    session["images"] = [
        {"id": "page1", "name": "page1.jpg", "input_path": str(image_a)},
        {"id": "page2", "name": "page2.jpg", "input_path": str(image_b)},
    ]
    main_v2.save_session(session)
    completed_record = main_v2.image_record(session_id, image_a)
    Path(completed_record["integrated_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(completed_record["integrated_json"]).write_text("[]", encoding="utf-8")
    pending_record = main_v2.image_record(session_id, image_b)
    calls = []

    def fake_run_final_for_item(session_id_arg, item, *args, **kwargs):
        calls.append(item["id"])
        record = main_v2.image_record(session_id_arg, Path(item["input_path"]))
        Path(record["integrated_json"]).parent.mkdir(parents=True, exist_ok=True)
        Path(record["integrated_json"]).write_text("[]", encoding="utf-8")
        Path(record["final_jpg"]).parent.mkdir(parents=True, exist_ok=True)
        Path(record["final_jpg"]).write_bytes(b"final")
        return main_v2.image_record(session_id_arg, Path(item["input_path"]))

    monkeypatch.setattr(main_v2, "run_final_for_item", fake_run_final_for_item)

    events = list(main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=session_id, image_id="all")))

    assert calls == ["page2"]
    assert any("이미 OCR 결과가 있는 1개 파일은 건너뜁니다." in event for event in events)
    assert pending_record["id"] == "page2"


def test_run_final_stream_failed_target_retries_only_failed_images(tmp_path, monkeypatch):
    session_id = "run_final_stream_failed_target"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_a = base / "input" / "page1.jpg"
    image_b = base / "input" / "page2.jpg"
    image_a.parent.mkdir(parents=True)
    image_a.write_bytes(b"image a")
    image_b.write_bytes(b"image b")
    session = main_v2.ensure_session(session_id)
    session["images"] = [
        {"id": "page1", "name": "page1.jpg", "input_path": str(image_a)},
        {"id": "page2", "name": "page2.jpg", "input_path": str(image_b)},
    ]
    main_v2.save_session(session)
    failed_record = main_v2.image_record(session_id, image_a)
    ready_record = main_v2.image_record(session_id, image_b)
    main_v2.mark_ocr_status(failed_record, "failed", "실패", "timeout")
    calls = []

    def fake_run_final_for_item(session_id_arg, item, *args, **kwargs):
        calls.append(item["id"])
        record = main_v2.image_record(session_id_arg, Path(item["input_path"]))
        Path(record["integrated_json"]).parent.mkdir(parents=True, exist_ok=True)
        Path(record["integrated_json"]).write_text("[]", encoding="utf-8")
        Path(record["final_jpg"]).parent.mkdir(parents=True, exist_ok=True)
        Path(record["final_jpg"]).write_bytes(b"final")
        return main_v2.image_record(session_id_arg, Path(item["input_path"]))

    monkeypatch.setattr(main_v2, "run_final_for_item", fake_run_final_for_item)

    events = list(main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=session_id, image_id="failed")))

    assert calls == ["page1"]
    assert any('"status": "done"' in event for event in events)
    assert ready_record["id"] == "page2"


def test_run_final_stream_no_pending_keeps_session_images(tmp_path, monkeypatch):
    session_id = "run_final_stream_no_pending"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_path = base / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path)
    Path(record["integrated_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["integrated_json"]).write_text("[]", encoding="utf-8")

    events = [json.loads(event) for event in main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=session_id, image_id="all"))]
    complete = next(event for event in events if event.get("type") == "complete")

    assert complete["images"][0]["id"] == "page1"


def test_run_final_stream_force_current_reruns_existing_integrated(tmp_path, monkeypatch):
    session_id = "run_final_stream_force_current"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_path = base / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path)
    Path(record["integrated_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["integrated_json"]).write_text("[]", encoding="utf-8")
    calls = []

    def fake_run_final_for_item(session_id_arg, item, *args, **kwargs):
        calls.append((item["id"], kwargs.get("force")))
        return main_v2.image_record(session_id_arg, Path(item["input_path"]))

    monkeypatch.setattr(main_v2, "run_final_for_item", fake_run_final_for_item)

    list(main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=session_id, image_id="page1", force=True)))

    assert calls == [("page1", True)]


def test_run_final_skips_when_analysis_has_not_run(tmp_path, monkeypatch):
    session_id = "analysis_not_run"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_path = base / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    record = main_v2.image_record(session_id, image_path)
    final_path = Path(record["final_jpg"])
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"stale final")

    def fail_subprocess(*args, **kwargs):
        raise AssertionError("Gemini subprocess must not run without OCR sheet")

    monkeypatch.setattr(main_v2, "run_subprocess", fail_subprocess)
    monkeypatch.setattr(main_v2, "run_subprocess_stream", fail_subprocess)

    logs = []
    refreshed = main_v2.run_final_for_item(
        session_id,
        {"id": "page1", "name": "page1.jpg", "input_path": str(image_path)},
        "",
        None,
        log_callback=logs.append,
    )

    integrated = main_v2.load_json(Path(record["integrated_json"]), default={})
    assert integrated["skipped"] is True
    assert integrated["reason"] == "analysis_not_run"
    assert integrated["analysis_status"] == "not_run"
    assert integrated["ocr_region_count"] is None
    assert "regions_json" in integrated["missing_inputs"]
    assert refreshed["integrated"]["skipped"] is True
    assert not final_path.exists()
    assert any("말풍선 분석 산출물이 없어" in log for log in logs)


def test_run_final_skips_no_ocr_regions_after_analysis(tmp_path, monkeypatch):
    session_id = "no_ocr_regions"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_path = base / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    record = main_v2.image_record(session_id, image_path)
    regions_path = Path(record["regions_json"])
    regions_path.parent.mkdir(parents=True, exist_ok=True)
    regions_path.write_text(
        '{"analysis_status": "complete", "ocr_region_count": 0, "regions": []}',
        encoding="utf-8",
    )

    def fail_subprocess(*args, **kwargs):
        raise AssertionError("Gemini subprocess must not run without OCR regions")

    monkeypatch.setattr(main_v2, "run_subprocess", fail_subprocess)
    monkeypatch.setattr(main_v2, "run_subprocess_stream", fail_subprocess)

    refreshed = main_v2.run_final_for_item(
        session_id,
        {"id": "page1", "name": "page1.jpg", "input_path": str(image_path)},
        "",
        None,
    )

    integrated = main_v2.load_json(Path(record["integrated_json"]), default={})
    assert integrated["skipped"] is True
    assert integrated["reason"] == "no_ocr_regions"
    assert integrated["analysis_status"] == "complete"
    assert integrated["ocr_region_count"] == 0
    assert integrated["missing_inputs"] == []
    assert refreshed["integrated"]["reason"] == "no_ocr_regions"


def test_run_final_skips_incomplete_ocr_sheet_after_regions_exist(tmp_path, monkeypatch):
    session_id = "missing_ocr_sheet"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_path = base / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    record = main_v2.image_record(session_id, image_path)
    regions_path = Path(record["regions_json"])
    regions_path.parent.mkdir(parents=True, exist_ok=True)
    regions_path.write_text(
        '{"analysis_status": "complete", "ocr_region_count": 1, "regions": [{"region_id": 1}]}',
        encoding="utf-8",
    )

    def fail_subprocess(*args, **kwargs):
        raise AssertionError("Gemini subprocess must not run without OCR sheet image")

    monkeypatch.setattr(main_v2, "run_subprocess", fail_subprocess)
    monkeypatch.setattr(main_v2, "run_subprocess_stream", fail_subprocess)

    refreshed = main_v2.run_final_for_item(
        session_id,
        {"id": "page1", "name": "page1.jpg", "input_path": str(image_path)},
        "",
        None,
    )

    integrated = main_v2.load_json(Path(record["integrated_json"]), default={})
    assert integrated["skipped"] is True
    assert integrated["reason"] == "missing_ocr_sheet"
    assert integrated["analysis_status"] == "complete"
    assert integrated["ocr_region_count"] == 1
    assert integrated["missing_inputs"] == ["indexed_sheet_jpg"]
    assert refreshed["integrated"]["reason"] == "missing_ocr_sheet"


def test_run_final_stream_marks_missing_ocr_sheet_as_skipped(tmp_path, monkeypatch):
    session_id = "stream_missing_ocr_sheet"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    base = main_v2.session_dir(session_id)
    image_path = base / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}]
    main_v2.save_session(session)

    events = list(main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=session_id, image_id="all")))

    assert any('"status": "skipped"' in event for event in events)
    assert not any('"type": "final_error"' in event for event in events)
    assert any('"type": "complete"' in event and '"ok": true' in event for event in events)


def test_analyze_cache_reapplies_existing_guidance(tmp_path, monkeypatch):
    session_id = "guidance_priority"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "CV_CACHE_DIR", tmp_path / "cv_cache")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "analysis_cache")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"same image")
    image_hash = main_v2.file_sha256(image_path)
    cache_dir = main_v2.analysis_cache_dir(image_hash)
    cache_dir.mkdir(parents=True)
    (cache_dir / "text_groups.json").write_text('[{"text_group_id": 1}]', encoding="utf-8")
    (cache_dir / "regions.json").write_text('{"regions": [{"region_id": 1}]}', encoding="utf-8")
    (cache_dir / "indexed_sheet.jpg").write_bytes(b"sheet")
    (cache_dir / "masked.jpg").write_bytes(b"masked")
    (cache_dir / "bubble_vis.jpg").write_bytes(b"vis")
    (cache_dir / "meta.json").write_text(
        f'{{"analysis_cache_version": "{main_v2.ANALYSIS_CACHE_VERSION}"}}',
        encoding="utf-8",
    )
    session = main_v2.ensure_session(session_id)
    session["images"] = [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path), "image_sha256": image_hash}]
    main_v2.save_session(session)
    record = main_v2.image_record(session_id, image_path, image_hash)
    Path(record["guidance_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["guidance_json"]).write_text('{"manual_seed_points": [{"point": [10, 20]}]}', encoding="utf-8")
    calls = []

    def fake_reapply(session_id_arg, record_arg, log_callback):
        calls.append((session_id_arg, record_arg["id"]))
        log_callback("guidance reapplied")
        main_v2._unlink_symlinked_analysis_outputs(record_arg)
        Path(record_arg["indexed_sheet_jpg"]).write_bytes(b"manual sheet")
        Path(record_arg["masked_jpg"]).write_bytes(b"manual masked")
        Path(record_arg["bubble_vis_jpg"]).write_bytes(b"manual vis")
        refreshed = main_v2.image_record(session_id_arg, Path(record_arg["input_path"]), image_hash)
        refreshed["guidance"] = {"manual_seed_points": [{"point": [10, 20]}]}
        return refreshed

    monkeypatch.setattr(main_v2, "reapply_guidance_if_present", fake_reapply)

    events = list(main_v2.stream_analyze(main_v2.AnalyzeRequest(session_id=session_id)))

    assert calls == [(session_id, "page1")]
    assert any("Guidance 재적용" in event for event in events)


def test_run_final_for_item_replace_drops_removed_overlay_keys(tmp_path, monkeypatch):
    """run-final도 replace면 편집기 상태로 교체한다(Delete로 지운 위치값이 병합으로 되살아나지 않는다)."""
    session_id = "run_final_replace"
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    image_path = tmp_path / "sessions" / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    item = {"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}
    record = main_v2.image_record(session_id, image_path)
    guidance_path = Path(record["overlay_guidance_json"])
    guidance_path.parent.mkdir(parents=True, exist_ok=True)
    guidance_path.write_text('{"regions": {"1": {"safe_text_rect": [1, 2, 30, 40], "rotation_deg": 5}}}', encoding="utf-8")
    # OCR 이전 단계가 없으므로 overlay_guidance 저장 직후 skip 경로로 끝난다.
    main_v2.run_final_for_item(session_id, item, "", None, overlay_guidance={"regions": {"1": {}}}, replace_overlay=True)
    assert main_v2.load_json(guidance_path, default={}) == {"regions": {"1": {}}}

    guidance_path.write_text('{"regions": {"1": {"safe_text_rect": [1, 2, 30, 40]}}}', encoding="utf-8")
    main_v2.run_final_for_item(session_id, item, "", None, overlay_guidance={"regions": {"1": {}}})
    assert main_v2.load_json(guidance_path, default={})["regions"]["1"].get("safe_text_rect") == [1, 2, 30, 40]


def test_old_region_suppression_cache_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path)
    guidance = {"suppressed_regions": [{"anchor_ids": [3], "rect": [10, 10, 20, 30]}]}
    cache = main_v2.guidance_output_cache_dir("imagehash", guidance)
    cache.mkdir(parents=True)
    main_v2.write_json(cache / "meta.json", {
        "analysis_cache_version": main_v2.ANALYSIS_CACHE_VERSION,
        "guidance_digest": main_v2.guidance_digest(guidance)})
    # 옛 캐시는 같은 guidance라도 텍스트 기반 복구로 R이 되살아난 결과일 수 있다.
    assert main_v2.hydrate_guidance_output_cache({}, "imagehash", guidance, lambda text: None) is False
