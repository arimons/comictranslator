# GET /api/session의 읽기 전용성(I1), 요약/페이지 상세 분리(I3), overlay 적용 probe 생략(I4)을 검증한다.

import json
import os
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


def fs_snapshot(*roots: Path) -> dict[str, tuple]:
    snap = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                path = os.path.join(dirpath, name)
                st = os.lstat(path)
                snap[path] = (st.st_mtime_ns, st.st_ino, st.st_size)
    return snap


def make_guided_page(session_id: str, stem: str, payload: bytes, region_count: int = 2) -> tuple[dict, str]:
    """guidance 산출물 캐시가 있는 페이지를 만든다. regions에는 mask_ref가 있고 캐시에 mask PNG가 있다."""
    image_path = main_v2.SESSION_ROOT / session_id / "input" / f"{stem}.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(payload)
    image_hash = main_v2.file_sha256(image_path)
    record = main_v2.image_record(session_id, image_path, image_hash)
    masks_dir = Path(record["work_dir"]) / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)
    regions = []
    for index in range(1, region_count + 1):
        mask_path = masks_dir / f"{stem}_R{index}_mask.png"
        mask_path.write_bytes(f"mask {stem} {index}".encode())
        regions.append({"region_id": index, "type": "bubble", "mask_ref": str(mask_path)})
    guidance = {"manual_seed_points": [{"point": [10, 20]}]}
    for key, content in {
        "guidance_json": json.dumps(guidance).encode("utf-8"),
        "text_groups_json": b'[{"text_group_id": 1}]',
        "effective_text_groups_json": b'[{"text_group_id": 1, "manual": true}]',
        "guidance_report_json": b'{"changed": true}',
        "regions_json": json.dumps({"regions": regions}).encode("utf-8"),
        "indexed_sheet_jpg": f"sheet {stem}".encode(),
        "masked_jpg": f"masked {stem}".encode(),
        "bubble_vis_jpg": f"vis {stem}".encode(),
        "integrated_json": json.dumps({"clusters": [{"source_region_id": 1, "translated": "안녕"}]}).encode("utf-8"),
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert main_v2.store_guidance_output_cache(record, image_hash, guidance) is True
    # 세션 쪽 mask는 지워 두고, 첫 GET이 캐시에서 연결하게 한다.
    for mask in masks_dir.glob(f"{stem}_R*_mask.png"):
        mask.unlink()
    return {"id": stem, "name": f"{stem}.jpg", "input_path": str(image_path), "image_sha256": image_hash}, image_hash


def make_session(session_id: str, items: list[dict]) -> None:
    session = main_v2.ensure_session(session_id)
    session["images"] = items
    main_v2.save_session(session)


def test_second_get_session_performs_zero_writes(tmp_path):
    session_id = "readonly_get"
    page1, _ = make_guided_page(session_id, "page1", b"image one")
    page2, _ = make_guided_page(session_id, "page2", b"image two")
    make_session(session_id, [page1, page2])
    client = TestClient(main_v2.create_app())

    first = client.get(f"/api/session/{session_id}")
    assert first.status_code == 200
    roots = (main_v2.SESSION_ROOT, main_v2.ANALYSIS_CACHE_DIR)
    before = fs_snapshot(*roots)
    second = client.get(f"/api/session/{session_id}")
    after = fs_snapshot(*roots)

    assert second.status_code == 200
    assert after == before
    assert second.json() == first.json()


def test_guidance_hydration_keeps_masks_of_other_pages(tmp_path):
    session_id = "masks_kept"
    page1, _ = make_guided_page(session_id, "page1", b"image one")
    page2, _ = make_guided_page(session_id, "page2", b"image two")
    make_session(session_id, [page1, page2])

    main_v2.session_images(session_id)

    for stem in ("page1", "page2"):
        record = main_v2.image_record(session_id, Path(main_v2.get_image(session_id, stem)["input_path"]))
        for region in record["regions"]["regions"]:
            assert Path(region["mask_ref"]).exists(), region["mask_ref"]
            assert Path(region["mask_ref"]).read_bytes().startswith(f"mask {stem}".encode())


def test_get_session_does_not_run_step3_and_defers_reapply(tmp_path, monkeypatch):
    session_id = "defer_reapply"
    image_path = main_v2.SESSION_ROOT / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    image_hash = main_v2.file_sha256(image_path)
    record = main_v2.image_record(session_id, image_path, image_hash)
    cache_dir = main_v2.analysis_cache_dir(image_hash)
    cache_dir.mkdir(parents=True)
    (cache_dir / "masked.jpg").write_bytes(b"baseline masked")
    for key, content in {
        "guidance_json": b'{"disabled_tg_ids": [1]}',
        "text_groups_json": b'[{"text_group_id": 1}]',
        "regions_json": b'{"regions": [{"region_id": 1}]}',
        "indexed_sheet_jpg": b"sheet",
        "bubble_vis_jpg": b"vis",
    }.items():
        path = Path(record[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # 기본 분석 캐시에 연결된 상태(guidance 미반영)
    Path(record["masked_jpg"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["masked_jpg"]).symlink_to(cache_dir / "masked.jpg")
    make_session(session_id, [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path), "image_sha256": image_hash}])

    def fail_step3(*_args, **_kwargs):
        raise AssertionError("GET must not run Step 3")

    scheduled = []
    monkeypatch.setattr(main_v2, "run_step3_with_effective_text_groups", fail_step3)
    monkeypatch.setattr(main_v2, "start_background_load_cache_hydration", lambda sid, items: scheduled.append((sid, [item["id"] for item in items])))
    client = TestClient(main_v2.create_app())

    assert client.get(f"/api/session/{session_id}").status_code == 200
    assert client.get(f"/api/session/{session_id}").status_code == 200

    # 같은 guidance 상태로는 한 번만 예약한다.
    assert scheduled == [(session_id, ["page1"])]


def test_session_summary_omits_page_detail_and_page_endpoint_returns_it(tmp_path):
    session_id = "summary_split"
    page1, _ = make_guided_page(session_id, "page1", b"image one")
    make_session(session_id, [page1])
    client = TestClient(main_v2.create_app())

    summary = client.get(f"/api/session/{session_id}").json()
    item = summary["images"][0]
    detail = client.get(f"/api/session/{session_id}/page/page1").json()["image"]

    assert summary["hydration_pending"] is False
    for key in ("regions", "integrated", "text_groups", "effective_text_groups", "guidance", "overlay_layout"):
        assert key not in item
        assert key in detail
    assert item["detail_loaded"] is False
    assert detail["detail_loaded"] is True
    assert item["has_analysis"] is True
    assert item["ocr_region_count"] == 2
    assert item["integrated_skipped"] is False
    assert item["detail_rev"] == detail["detail_rev"]
    for key, value in item.items():
        if key != "detail_loaded":
            assert detail[key] == value, key
    assert detail["regions"]["regions"][0]["region_id"] == 1
    assert client.get(f"/api/session/{session_id}/page/missing").status_code == 404


def test_detail_rev_changes_when_page_json_changes(tmp_path):
    session_id = "detail_rev"
    page1, _ = make_guided_page(session_id, "page1", b"image one")
    make_session(session_id, [page1])
    before = main_v2.session_image_summaries(session_id)[0]
    overlay_path = Path(main_v2.image_record(session_id, Path(page1["input_path"]))["overlay_guidance_json"])
    overlay_path.write_text('{"regions": {"1": {"font_size": 30}}}', encoding="utf-8")
    after = main_v2.session_image_summaries(session_id)[0]

    assert before["detail_rev"] != after["detail_rev"]


def test_summary_flags_for_skipped_integrated(tmp_path):
    session_id = "skipped_flags"
    image_path = main_v2.SESSION_ROOT / session_id / "input" / "page1.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    record = main_v2.image_record(session_id, image_path)
    Path(record["regions_json"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["regions_json"]).write_text('{"regions": []}', encoding="utf-8")
    Path(record["integrated_json"]).write_text(json.dumps({"skipped": True, "message": "스킵됨"}), encoding="utf-8")
    make_session(session_id, [{"id": "page1", "name": "page1.jpg", "input_path": str(image_path)}])

    item = main_v2.session_image_summaries(session_id)[0]

    assert item["has_analysis"] is True
    assert item["ocr_region_count"] == 0
    assert item["integrated_skipped"] is True
    assert item["integrated_message"] == "스킵됨"


def test_overlay_apply_skips_full_image_margin_probe(tmp_path, monkeypatch):
    session_id = "overlay_no_probe"
    page1, _ = make_guided_page(session_id, "page1", b"image one")
    page1.update({"width": 800, "height": 1200})
    make_session(session_id, [page1])
    rendered = []

    def fail_probe(*_args, **_kwargs):
        raise AssertionError("overlay apply must not probe image margins")

    monkeypatch.setattr(main_v2, "estimate_horizontal_margins", fail_probe)
    monkeypatch.setattr(main_v2, "image_dimensions", fail_probe)
    monkeypatch.setattr(main_v2, "render_overlay_for_record", lambda record, font, tracking=None: rendered.append(record["id"]))
    client = TestClient(main_v2.create_app())

    response = client.post(
        "/api/overlay/apply",
        json={"session_id": session_id, "image_id": "page1", "overlay_guidance": {"regions": {"1": {"font_size": 20}}}},
    )

    assert response.status_code == 200, response.text
    image = response.json()["image"]
    assert rendered == ["page1"]
    assert image["width"] == 800
    assert image["overlay_guidance"]["regions"]["1"]["font_size"] == 20
    assert image["detail_loaded"] is True
