"""UI를 띄우지 않고 MCP의 관찰·초안 계약과 실제 stdio 왕복을 검증한다."""
import asyncio
import base64
import io
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

from pipeline.editor_review_bridge import CandidateAction, Observation, ReviewStore

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


@pytest.fixture
def project(tmp_path):
    base = tmp_path / "data/projects/example"
    image = base / "input/page.jpg"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (200, 300), "white").save(image)
    write_json(base / "session.json", {"session_id": "example", "project_name": "테스트",
               "images": [{"id": "page", "name": "page.jpg", "input_path": str(image)}]})
    write_json(base / "work/json/page_regions.json", {"regions": [
        {"region_id": 1, "type": "bubble", "source_method": "manual_edge_barrier_rescue",
         "bubble_rect": [10, 20, 40, 60], "indexed_sheet_rect": [0, 0, 80, 100],
         "mask_ref": str(base / "work/masks/page_R1_mask.png")},
        {"region_id": 4, "type": "unboxed", "unboxed_rect": [100, 150, 30, 60],
         "indexed_sheet_rect": [80, 0, 50, 100]},
    ]})
    mask = base / "work/masks/page_R1_mask.png"
    mask.parent.mkdir(parents=True)
    Image.new("L", (200, 300), 255).save(mask)
    sheet = base / "work/sheets/page_indexed_sheet.jpg"
    sheet.parent.mkdir(parents=True)
    Image.new("RGB", (130, 100), "gray").save(sheet)
    return tmp_path, base, ReviewStore(tmp_path)


def snapshot_files(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_all_read_tools_preserve_files_and_legacy_types(project):
    root, base, store = project
    before = snapshot_files(root)
    assert store.list_projects()["projects"][0]["session_id"] == "example"
    assert store.inspect_page("example")["pages"][0]["image_id"] == "page"
    page = store.inspect_page("example", "page")
    assert [r["type"] for r in page["payload"]["regions"]["regions"]] == ["bubble", "unboxed"]
    assert [r["processing_basis"] for r in page["payload"]["regions"]["regions"]] == ["boundary_based", "text_based"]
    meta, png = store.view_region("example", "page", 4, margin=0, expected_revision=page["page_revision"])
    assert meta["crop_rect"] == [100, 150, 30, 60]
    assert Image.open(io.BytesIO(png)).size == (30, 60)
    assert meta["coordinate_space"] == "original_pixels"
    assert snapshot_files(root) == before


def test_project_listing_is_not_deduplicated_and_supports_paging(project):
    root, base, store = project
    manifest = json.loads((base / "session.json").read_text())
    manifest["session_id"] = "duplicate"
    write_json(base.parent / "duplicate/session.json", manifest)
    first = store.list_projects(limit=1)
    second = store.list_projects(offset=first["next_offset"], limit=1)
    assert first["total"] == 2
    assert first["projects"][0]["session_id"] != second["projects"][0]["session_id"]


def test_crop_bounds_sheet_coordinates_and_masks(project):
    _, _, store = project
    meta, _ = store.view_region("example", "page", 1, margin=100)
    assert meta["crop_rect"] == [0, 0, 150, 180]
    meta, _ = store.view_region("example", "page", 4, artifact="ocr_sheet", margin=0)
    assert meta["crop_rect"] == [80, 0, 50, 100]
    assert meta["coordinate_space"] == "indexed_sheet_pixels"
    meta, _ = store.view_region("example", "page", 1, artifact="region_mask", margin=0)
    assert meta["crop_rect"] == [10, 20, 40, 60]
    with pytest.raises(ValueError, match="마스크"):
        store.view_region("example", "page", 4, artifact="region_mask")


@pytest.mark.parametrize("session_id,image_id", [("../example", "page"), ("example", "../page"),
                                                  ("unknown", "page"), ("example", "unknown")])
def test_unknown_or_traversing_ids_never_create_files(project, session_id, image_id):
    root, _, store = project
    before = snapshot_files(root)
    with pytest.raises(ValueError):
        store.inspect_page(session_id, image_id)
    assert before == snapshot_files(root)


def test_symlink_escape_is_rejected_for_input_and_proposals(project, tmp_path):
    root, base, store = project
    external = root.parent / (root.name + "-outside")
    external.mkdir()
    Image.new("RGB", (200, 300)).save(external / "private.png")
    image = base / "input/page.jpg"
    image.unlink()
    image.symlink_to(external / "private.png")
    with pytest.raises(ValueError, match="경로"):
        store.inspect_page("example", "page")
    image.unlink()
    Image.new("RGB", (200, 300)).save(image)
    revision = store.inspect_page("example", "page")["page_revision"]
    (base / "work/reviews").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="경로"):
        store.propose_review("example", "page", revision, [Observation(description="관찰")], [])
    assert not list(external.glob("*.json"))


def test_revision_covers_mask_pixels_and_stale_proposals_are_rejected(project):
    _, base, store = project
    revision = store.inspect_page("example", "page")["page_revision"]
    Image.new("L", (200, 300), 0).save(base / "work/masks/page_R1_mask.png")
    assert store.inspect_page("example", "page")["page_revision"] != revision
    with pytest.raises(ValueError, match="revision"):
        store.propose_review("example", "page", revision, [Observation(description="관찰")], [])
    with pytest.raises(ValueError, match="revision"):
        store.view_region("example", "page", expected_revision=revision)
    assert not (base / "work/reviews").exists()


def test_cache_root_cannot_be_retargeted_outside_repository_after_startup(project):
    root, base, store = project
    external = root.parent / (root.name + "-other-cache")
    external.mkdir()
    Image.new("L", (200, 300), 255).save(external / "mask.png")
    cache = root / "data/cache/analysis"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.symlink_to(external, target_is_directory=True)
    regions_path = base / "work/json/page_regions.json"
    regions = json.loads(regions_path.read_text())
    regions["regions"][0]["mask_ref"] = str(cache / "mask.png")
    write_json(regions_path, regions)
    with pytest.raises(ValueError, match="경로"):
        store.inspect_page("example", "page")


def test_proposals_are_separate_immutable_idempotent_drafts(project):
    root, base, store = project
    before = snapshot_files(root)
    revision = store.inspect_page("example", "page")["page_revision"]
    observations = [Observation(region_id=4, description="경계가 흐리게 보임", interpretation="사용자 확인 필요")]
    actions = [CandidateAction(action="review_only", region_id=4, reason="시각적 더블체크")]
    result = store.propose_review("example", "page", revision, observations, actions)
    saved = snapshot_files(root)
    assert store.propose_review("example", "page", revision, observations, actions) == result
    assert snapshot_files(root) == saved
    assert {k: saved[k] for k in before} == before
    assert len(set(saved) - set(before)) == 1
    assert result["applied"] is False
    assert result["proposal"]["needs_human_check"] is True
    assert result["proposal"]["quality_verdict"] == "unreviewed"
    page = store.inspect_page("example", "page")
    assert page["page_revision"] == revision
    assert page["review_proposals"][0]["stale"] is False
    write_json(base / "work/json/page_manual_guidance.json", {"disabled_tg_ids": [3]})
    assert store.inspect_page("example", "page")["review_proposals"][0]["stale"] is True


def test_missing_artifact_bad_json_and_bad_region_are_explicit(project):
    _, base, store = project
    (base / "work/json/page_integrated.json").write_text("{")
    page = store.inspect_page("example", "page")
    assert page["issues"] == [{"kind": "invalid_json", "artifact": "integrated"}]
    assert page["artifacts"]["final"]["available"] is False
    with pytest.raises(ValueError, match="생성"):
        store.view_region("example", "page", artifact="final")
    with pytest.raises(ValueError, match="region_id"):
        store.propose_review("example", "page", page["page_revision"], [Observation(region_id=999, description="없음")], [])


def test_real_stdio_handshake_images_errors_and_proposal(project):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    root, _, _ = project

    async def run():
        # 다른 cwd에서도 실행하며 FastAPI, API 키, 네트워크 listener는 필요 없다.
        parameters = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / "scripts/run_editor_mcp.py"), "--root", str(root)], cwd=str(root))
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = {t.name: t for t in (await client.list_tools()).tools}
                assert set(tools) == {"list_projects", "inspect_page", "view_region", "propose_review", "inspect_image_generation", "recommend_image_generation", "prepare_image_generation", "register_generated_image"}
                assert tools["view_region"].annotations.readOnlyHint is True
                assert tools["propose_review"].annotations.readOnlyHint is False
                projects = await client.call_tool("list_projects", {})
                assert projects.structuredContent["projects"][0]["session_id"] == "example"
                page = await client.call_tool("inspect_page", {"session_id": "example", "image_id": "page"})
                assert not page.isError
                revision = page.structuredContent["page_revision"]
                image = await client.call_tool("view_region", {"session_id": "example", "image_id": "page",
                    "region_id": 4, "margin": 0, "expected_revision": revision})
                assert not image.isError
                png = next(c for c in image.content if c.type == "image")
                assert Image.open(io.BytesIO(base64.b64decode(png.data))).size == (30, 60)
                proposal = await client.call_tool("propose_review", {"session_id": "example", "image_id": "page",
                    "base_revision": revision, "observations": [{"region_id": 4, "description": "확인 필요"}], "candidate_actions": []})
                assert not proposal.isError
                assert proposal.structuredContent["applied"] is False
                error = await client.call_tool("view_region", {"session_id": "example", "image_id": "page", "region_id": 999})
                assert error.isError

    asyncio.run(asyncio.wait_for(run(), timeout=30))


def test_local_client_config_preserves_other_servers_and_is_idempotent(tmp_path):
    from scripts.configure_editor_mcp import configure
    import tomllib
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts/run_editor_mcp.py").touch()
    (tmp_path / ".codex").mkdir()
    original = '[mcp_servers.other]\ncommand = "other-server"\n'
    (tmp_path / ".codex/config.toml").write_text(original)
    write_json(tmp_path / ".mcp.json", {"mcpServers": {"other": {"command": "other-server"}}})
    paths = configure(tmp_path)
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    configure(tmp_path)
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    assert paths[0].read_text().startswith(original)
    servers = tomllib.loads(paths[0].read_text())["mcp_servers"]
    assert servers["other"] == {"command": "other-server"}
    assert servers["comic-translator-review"]["command"] == str(tmp_path / ".venv/bin/python")
    assert json.loads(paths[1].read_text())["mcpServers"]["other"] == {"command": "other-server"}


def test_local_client_config_does_not_overwrite_existing_manual_server(tmp_path):
    from scripts.configure_editor_mcp import configure
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts/run_editor_mcp.py").touch()
    (tmp_path / ".codex").mkdir()
    path = tmp_path / ".codex/config.toml"
    original = '[mcp_servers.comic-translator-review]\ncommand = "existing"\n'
    path.write_text(original)
    with pytest.raises(ValueError, match="수동"):
        configure(tmp_path)
    assert path.read_text() == original
    assert not (tmp_path / ".mcp.json").exists()
