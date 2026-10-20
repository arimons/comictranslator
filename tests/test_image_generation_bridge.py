"""생성 도구는 호출하지 않고 원본 보존·요청 대응·revision·이미지 검증을 검사한다."""
import asyncio
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

from pipeline.image_generation_bridge import ImageGenerationStore


@pytest.fixture
def project(tmp_path):
    base = tmp_path / "data/projects/book"
    original = base / "input/cover.png"
    original.parent.mkdir(parents=True)
    Image.new("RGB", (200, 300), "yellow").save(original)
    (base / "session.json").write_text(json.dumps({"images": [{"id": "cover", "input_path": str(original)}]}))
    inbox = tmp_path / "data/inbox/image_generation"
    inbox.mkdir(parents=True)
    returned = inbox / "generated.png"
    Image.new("RGB", (400, 600), "blue").save(returned)
    return tmp_path, base, original, returned, ImageGenerationStore(tmp_path)


def prepare(store):
    revision = store.inspect_page("book", "cover")["page_revision"]
    return store.prepare("book", "cover", revision, "표지의 일본어를 한국어로")


def test_registration_preserves_inputs_final_and_is_idempotent(project):
    root, base, original, returned, store = project
    final = base / "output/final/cover_final.jpg"
    final.parent.mkdir(parents=True)
    Image.new("RGB", (200, 300), "red").save(final)
    before = [p.read_bytes() for p in (original, final, base / "session.json")]
    request = prepare(store)
    assert request == prepare(store)
    assert request["context"]["glossary"]["version"] == 0
    result = store.register("book", "cover", request["request_id"], str(returned))
    assert result == store.register("book", "cover", request["request_id"], str(returned))
    assert result["status"] == "preview" and result["actual_model"] is None
    assert (root / result["returned_path"]).read_bytes() == returned.read_bytes()
    assert [p.read_bytes() for p in (original, final, base / "session.json")] == before
    assert store.status("book", "cover")["results"][0]["stale"] is False
    original.write_bytes(returned.read_bytes())
    assert store.status("book", "cover")["results"][0]["stale"] is True


def test_revision_unknown_request_paths_and_ratio_rejected(project):
    root, base, original, returned, store = project
    request = prepare(store)
    with pytest.raises(ValueError, match="요청"):
        store.register("book", "cover", "0" * 32, str(returned))
    with pytest.raises(ValueError, match="inbox"):
        store.register("book", "cover", request["request_id"], str(original))
    Image.new("RGB", (600, 400)).save(returned)
    with pytest.raises(ValueError, match="비율"):
        store.register("book", "cover", request["request_id"], str(returned))
    Image.new("RGB", (200, 300), "black").save(original)
    with pytest.raises(ValueError, match="revision"):
        store.prepare("book", "cover", request["context"]["page_revision"])
    Image.new("RGB", (400, 600)).save(returned)
    with pytest.raises(ValueError, match="revision"):
        store.register("book", "cover", request["request_id"], str(returned))
    assert not list((base / "work/image_generation/cover").glob("result-*"))


def test_glossary_change_invalidates_request_and_no_research_is_run(project):
    root, base, _, returned, store = project
    wid = "a" * 24
    directory = root / "data/glossaries"
    directory.mkdir()
    glossary = {"work_id": wid, "format": "work-memo-v1", "title": "작품", "updated_at": "v2",
                "characters": [{"id": "c00000001", "name": "유키", "speech": "반말", "protagonist": True}], "terms": []}
    path = directory / f"{wid}.json"
    path.write_text(json.dumps(glossary))
    (base / "work").mkdir(exist_ok=True)
    (base / "work/glossary_binding.json").write_text(json.dumps({"work_id": wid}))
    request = prepare(store)
    assert request["context"]["glossary"]["version"] == "v2"
    assert "유키" in request["prompt"]
    glossary["updated_at"] = "v3"
    path.write_text(json.dumps(glossary))
    with pytest.raises(ValueError, match="revision"):
        store.register("book", "cover", request["request_id"], str(returned))


def test_recommendation_is_not_generation_and_symlink_writes_rejected(project):
    root, base, _, _, store = project
    before = set(root.rglob("*"))
    assert store.status("book", "cover")["recommendation"] is None
    assert before == set(root.rglob("*"))
    revision = store.inspect_page("book", "cover")["page_revision"]
    result = store.recommend("book", "cover", revision, "ユーザー確認済みの表紙", "recommended")
    assert result["results"] == [] and result["automatic_generation"] is False
    directory = base / "work/image_generation/cover"
    (directory / "recommendation.json").unlink()
    target = root / "private.json"
    target.write_text("{}")
    (directory / "recommendation.json").symlink_to(target)
    with pytest.raises(ValueError, match="リンク|링크"):
        store.recommend("book", "cover", revision, "test")
    assert target.read_text() == "{}"


def test_stdio_generation_roundtrip(project):
    root, _, original, returned, _ = project
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve().parents[1] / "scripts/run_editor_mcp.py"), "--root", str(root)])
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                async def call(name, **args):
                    result = await client.call_tool(name, {"session_id": "book", "image_id": "cover", **args})
                    assert not result.isError, result
                    return result.structuredContent
                page = await call("inspect_page")
                await call("recommend_image_generation", base_revision=page["page_revision"], reason="확인된 커버")
                request = await call("prepare_image_generation", base_revision=page["page_revision"])
                result = await call("register_generated_image", request_id=request["request_id"], source_path=str(returned))
                status = await call("inspect_image_generation")
                assert status["results"][0]["result_id"] == result["result_id"]
    asyncio.run(asyncio.wait_for(run(), 30))


def test_http_status_exposes_preview_without_analysis(project, monkeypatch):
    import app
    from fastapi.testclient import TestClient
    root, _, _, returned, store = project
    monkeypatch.setattr(app, "ROOT", root)
    monkeypatch.setattr(app, "SESSION_ROOT", root / "data/projects")
    request = prepare(store)
    result = store.register("book", "cover", request["request_id"], str(returned))
    with TestClient(app.create_app()) as client:
        response = client.get("/api/image-generation/book/cover")
        assert response.status_code == 200
        item = response.json()["results"][0]
        assert item["result_id"] == result["result_id"]
        assert item["preview_url"]
        assert client.get(item["preview_url"]).status_code == 200
        assert client.get("/api/image-generation/book/unknown").status_code == 400


def test_bad_image_and_inbox_symlink_escape_are_rejected(project):
    root, base, _, returned, store = project
    request = prepare(store)
    returned.write_text("not an image")
    with pytest.raises(OSError):
        store.register("book", "cover", request["request_id"], str(returned))
    outside = root / "outside.png"
    Image.new("RGB", (200, 300)).save(outside)
    returned.unlink()
    returned.symlink_to(outside)
    with pytest.raises(ValueError, match="inbox"):
        store.register("book", "cover", request["request_id"], str(returned))
