"""페이지 제외는 기존 작업을 지우지 않고 모든 실행을 차단하며 출력에 원본을 사용한다."""
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import app as app
from pipeline.editor_review_bridge import ReviewStore
from pipeline.image_generation_bridge import ImageGenerationStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "data/projects")
    monkeypatch.setattr(app, "ANALYSIS_CACHE_DIR", tmp_path / "data/cache/analysis")
    data = app.ensure_session("book")
    original = app.session_dir("book") / "input/page.png"
    original.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (180, 240), "yellow").save(original)
    data["images"] = [app.image_manifest_item(original, app.file_sha256(original))]
    app.save_session(data)
    record = app.image_record("book", original)
    for key, value in {"regions_json":{"regions":[{"region_id":1,"type":"unboxed","unboxed_rect":[10,10,40,50]}]},
                       "guidance_json":{"disabled_tg_ids":[3]}, "integrated_json":{"sentences":[{"text":"기존 번역"}]}}.items():
        app.write_json(Path(record[key]), value)
    final = Path(record["final_jpg"])
    final.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (180, 240), "blue").save(final)
    def forbidden(*args, **kwargs):
        raise AssertionError("제외 페이지에서 분석·번역·생성 호출 금지")
    for name in ["run_single_image","run_subprocess","run_subprocess_stream","redraw_image_with_openai","session_glossary_snapshot","apply_text_overlay"]:
        monkeypatch.setattr(app,name,forbidden)
    return tmp_path, original, final, record, TestClient(app.create_app())


def toggle(client, excluded):
    response = client.post("/api/page-processing", json={"session_id":"book","image_id":"page","excluded":excluded})
    assert response.status_code == 200, response.text
    return response.json()["image"]


def test_toggle_export_original_bytes_and_restore_previous_final(project):
    _, original, final, record, client = project
    paths = [original,final,Path(record["guidance_json"]),Path(record["regions_json"]),Path(record["integrated_json"])]
    assert app.page_processing_excluded("book", "00002") is False
    before = [path.read_bytes() for path in paths]
    item = toggle(client,True)
    assert item["processing_excluded"] is True and item["final_url"] == item["input_url"]
    assert item["has_integrated_json"] is False
    assert client.get('/api/session/book').json()["images"][0]["processing_excluded"] is True
    with zipfile.ZipFile(app.build_session_export_zip("book")) as archive:
        assert archive.read('final/page.png') == original.read_bytes()
    item = toggle(client,False)
    assert not item["processing_excluded"] and item["has_integrated_json"]
    with zipfile.ZipFile(app.build_session_export_zip("book")) as archive:
        assert archive.read('final/page.jpg') == final.read_bytes()
    assert [path.read_bytes() for path in paths] == before


def test_excluded_execution_paths_do_not_call_models_or_remove_results(project):
    _, _, final, record, client = project
    before = final.read_bytes()
    toggle(client, True)
    assert client.post('/api/analyze', json={"session_id":"book","image_id":"all","force":True}).status_code == 200
    item = app.get_image('book','page')
    events=[]
    assert app.analyze_one_image('book',item,1,1,True,events.append)
    assert events[-1]['status']=='skipped'
    assert app.run_final_for_item('book',item,'',None,force=True)['processing_excluded']
    assert client.post('/api/run-final',json={"session_id":"book","image_id":"page","force":True}).status_code == 200
    stream = client.post('/api/run-final-stream',json={"session_id":"book","image_id":"all","force":True})
    assert stream.status_code == 200 and 'final_error' not in stream.text, stream.text
    assert '"type": "error"' not in stream.text
    result=app.generate_missing_finals_with_openai(app.GenerateMissingFinalRequest(session_id='book',image_ids=['page'],force=True))
    assert not result.get('generated')
    stream=client.post('/api/export/generate-missing/stream',json={"session_id":"book","image_ids":["page"],"force":True})
    assert stream.status_code == 200 and '"type": "error"' not in stream.text
    assert final.read_bytes() == before
    with pytest.raises(app.HTTPException):
        app._restore_record("book", "page")
    assert client.post("/api/overlay/apply",json={"session_id":"book","image_id":"page","overlay_guidance":{}}).status_code == 409
    with pytest.raises(app.HTTPException):
        app.run_step3_with_effective_text_groups('book','page',{})


def test_mcp_policy_in_revision_and_generation_block(project):
    root, _, _, _, client = project
    store=ReviewStore(root)
    before=store.inspect_page('book','page')['page_revision']
    toggle(client,True)
    page=store.inspect_page('book','page')
    assert page['processing_excluded'] and page['page_revision']!=before
    with pytest.raises(ValueError,match='제외'):
        ImageGenerationStore(root).prepare('book','page',page['page_revision'])
    toggle(client,False)
    assert not store.inspect_page('book','page')['processing_excluded']
