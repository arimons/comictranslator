"""검색 인용 검증, 충돌 격리, 작품 확인·재사용, 실제 번역 전달 계약."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

import main_v2
from pipeline.manual_guidance import load_json, write_json
from pipeline.work_glossary import GlossaryStore, build_entries, glossary_snapshot, grounded_evidence, work_id


def report(target="유키", **overrides):
    data = {
        "work_matches": True, "summary": "작품의 기본 정보", "model": "test",
        "evidence": [
            {"text": f"雪의 한국어 표기는 {target}이다.", "url": "https://publisher.example/characters", "title": "출판사"},
            {"text": f"雪의 한국어 표기는 {target}이다.", "url": "https://wiki.test/characters", "title": "위키"},
        ],
        "entries": [{"source": "雪", "target": target, "uncertain": False, "evidence_indices": [0, 1]}],
    }
    return {**data, **overrides}


def test_auto_requires_explicit_pair_and_two_sites():
    assert build_entries(report(), [])[0]["status"] == "auto"
    data = report()
    data["evidence"][1]["url"] = "https://sub.publisher.example/other"
    assert build_entries(data, [])[0]["status"] == "review"
    data = report()
    data["evidence"][1]["text"] = "雪라는 인물이 나온다."
    assert build_entries(data, [])[0]["status"] == "review"
    assert build_entries(report(work_matches=False), [])[0]["status"] == "review"
    data = report()
    data["entries"][0]["uncertain"] = True
    assert build_entries(data, [])[0]["status"] == "review"


def test_conflicting_spelling_is_removed_from_prompt():
    previous = build_entries(report(), [])
    entries = build_entries(report("설"), previous)
    assert len(entries) == 2
    assert {e["status"] for e in entries} == {"review"}
    assert not glossary_snapshot({"entries": entries})["prompt"]


def test_model_supplied_urls_or_invalid_indices_do_not_count():
    data = report()
    data["entries"][0]["evidence_indices"] = [-1, 99, "0", True]
    data["entries"][0]["sources"] = ["https://invented.example"]
    entry = build_entries(data, [])[0]
    assert entry["status"] == "review"
    assert not entry["evidence"]


def test_grounding_links_are_from_metadata_and_proxy_domains_are_distinguished():
    response = NS(candidates=[NS(grounding_metadata=NS(
        grounding_chunks=[NS(web=NS(uri="https://vertexaisearch.cloud.google.com/a", title="출처", domain="publisher.example")), NS(web=None)],
        grounding_supports=[NS(segment=NS(text="雪는 유키"), grounding_chunk_indices=[0, 1, 999])],
    ))])
    evidence = grounded_evidence(response)
    assert len(evidence) == 1
    assert evidence[0]["site"] == "publisher.example"
    data = report(evidence=evidence)
    data["entries"][0]["evidence_indices"] = [0]
    assert build_entries(data, [])[0]["status"] == "review"


def test_identity_includes_author_and_edition():
    assert work_id({"title": " My Work "}) == work_id({"title": "mywork"})
    assert work_id({"title": "작품", "author": "갑"}) != work_id({"title": "작품", "author": "을"})
    assert work_id({"title": "작품"}) != work_id({"title": "작품", "edition": "외전"})


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    service = GlossaryStore(tmp_path / "glossaries", researcher=lambda work: report())
    monkeypatch.setattr(main_v2, "glossary_store", lambda: service)
    yield service
    service.pool.shutdown(wait=True)


def test_confirm_once_reuse_across_sessions_and_review_only_uncertain(store):
    client = TestClient(main_v2.create_app())
    assert client.post("/api/glossary/research", json={"session_id": "one"}).status_code == 400
    assert list(store.root.glob("*.json")) == []
    response = client.post("/api/glossary/confirm", json={"session_id": "one", "title": "작품", "author": "작가"})
    assert response.status_code == 200
    wid = response.json()["glossary"]["work_id"]
    assert response.json()["glossary"]["status"] == "idle"
    store.start(wid)
    store.pool.shutdown(wait=True)
    data = client.get("/api/glossary/one").json()["glossary"]
    assert data["status"] == "ready"
    assert data["entries"][0]["status"] == "auto"
    second = client.post("/api/glossary/confirm", json={"session_id": "two", "work_id": wid})
    assert second.json()["glossary"] == data
    rejected = client.post("/api/glossary/review", json={"session_id": "two", "work_id": wid, "entry_id": data["entries"][0]["id"], "decision": "rejected"})
    assert rejected.status_code == 200
    assert not main_v2.session_glossary_snapshot("one")["entries"]
    assert client.post("/api/glossary/confirm", json={"session_id": "two", "work_id": "../bad"}).status_code == 400


def test_research_does_not_block_editor_and_blocks_ocr_until_finished(store):
    main_v2.ensure_session("one")
    entered, release = threading.Event(), threading.Event()
    calls = []
    def research(work):
        calls.append(work)
        entered.set()
        assert release.wait(5)
        return report()
    store.researcher = research
    wid = store.confirm({"title": "작품"})
    write_json(main_v2.glossary_binding_path("one"), {"work_id": wid})
    try:
        assert store.start(wid)["status"] == "researching"
        assert entered.wait(2)
        store.start(wid)
        assert len(calls) == 1
        with pytest.raises(ValueError, match="조사 중"):
            main_v2.session_glossary_snapshot("one")
        assert TestClient(main_v2.create_app()).get("/api/glossary/one").status_code == 200
        with pytest.raises(ValueError, match="조사 완료"):
            store.review(wid, "missing", "accepted")
    finally:
        release.set()
    store.pool.shutdown(wait=True)
    assert main_v2.session_glossary_snapshot("one")["entries"]


def test_review_survives_research_and_accepting_conflict_excludes_other(store):
    wid = store.confirm({"title": "작품"})
    data = store.read(wid)
    data["entries"] = build_entries(report("설"), build_entries(report(), []))
    write_json(store.path(wid), data)
    chosen = data["entries"][0]
    accepted = store.review(wid, chosen["id"], "accepted", "눈")
    assert [e["status"] for e in accepted["entries"]] == ["accepted", "rejected"]
    store.start(wid)
    store.pool.shutdown(wait=True)
    entries = store.read(wid)["entries"]
    assert len({e["id"] for e in entries}) == len(entries)
    assert any(e["target"] == "눈" and e["status"] == "accepted" for e in entries)
    # 새로 발견된 다른 표기는 검토에 남고 수동 결정을 덮어쓰지 않는다.
    assert [e["target"] for e in glossary_snapshot({"entries": entries})["entries"]] == ["눈"]


def test_failure_has_no_secret_and_restart_is_retryable(store):
    def fail(work):
        raise RuntimeError("secret-key-that-must-not-be-stored")
    store.researcher = fail
    wid = store.confirm({"title": "작품"})
    store.start(wid)
    store.pool.shutdown(wait=True)
    data = store.read(wid)
    assert data["status"] == "failed"
    assert "secret-key" not in store.path(wid).read_text()
    data["status"] = "researching"
    write_json(store.path(wid), data)
    assert store.read(wid)["status"] == "failed"


def test_final_translation_receives_only_applied_terms_and_records_version(store, monkeypatch):
    sid = "one"
    wid = store.confirm({"title": "작품"})
    data = store.read(wid)
    data.update(status="ready", version=3, entries=build_entries(report(), []))
    data["entries"].append({"source": "不確実", "target": "불확실", "status": "review"})
    write_json(store.path(wid), data)
    write_json(main_v2.glossary_binding_path(sid), {"work_id": wid})
    image = main_v2.session_dir(sid) / "input" / "page.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"test")
    record = main_v2.image_record(sid, image)
    write_json(record["regions_json"], {"regions": [{"region_id": 1}]})
    Path(record["indexed_sheet_jpg"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["indexed_sheet_jpg"]).write_bytes(b"sheet")
    commands = []
    def subprocess(cmd, **kwargs):
        commands.append(cmd)
        write_json(record["integrated_json"], {"clusters": []})
    monkeypatch.setattr(main_v2, "run_subprocess", subprocess)
    monkeypatch.setattr(main_v2, "render_overlay_for_record", lambda *a, **k: None)
    main_v2.run_final_for_item(sid, {"input_path": str(image)}, "수동 지시: 말투 유지", None)
    prompt = next(arg for arg in commands[0] if arg.startswith("--extra-prompt="))
    assert "유키" in prompt and "불확실" not in prompt
    assert prompt.endswith("수동 지시: 말투 유지")
    assert load_json(record["integrated_json"])["glossary"]["version"] == 3
    assert load_json(Path(record["work_dir"]) / "json/page_glossary_snapshot.json")["work_id"] == wid


def test_batch_freezes_one_glossary_version(store, monkeypatch):
    sid = "batch"
    wid = store.confirm({"title": "작품"})
    data = store.read(wid)
    data.update(status="ready", version=3, entries=build_entries(report(), []))
    write_json(store.path(wid), data)
    write_json(main_v2.glossary_binding_path(sid), {"work_id": wid})
    session = main_v2.ensure_session(sid)
    session["images"] = [{"id": f"p{i}", "name": f"p{i}.jpg", "input_path": str(main_v2.session_dir(sid) / "input" / f"p{i}.jpg")} for i in range(2)]
    main_v2.save_session(session)
    seen = []
    def run(sid, item, *args, glossary_context=None, **kwargs):
        seen.append(glossary_context)
        data["version"] = 4
        data["entries"] = []
        write_json(store.path(wid), data)
        return {**item, "integrated": {"clusters": []}}
    monkeypatch.setattr(main_v2, "run_final_for_item", run)
    monkeypatch.setattr(main_v2, "session_image_summaries", lambda sid: [])
    events = [json.loads(e) for e in main_v2.stream_run_final(main_v2.RunFinalRequest(session_id=sid, image_id="all", force=True))]
    assert events[-1]["ok"] is True
    assert len(seen) == 2
    assert [context["version"] for context in seen] == [3, 3]
    assert all(context["entries"][0]["target"] == "유키" for context in seen)


def test_research_provider_uses_one_openai_generation_and_saved_evidence(monkeypatch):
    import openai
    from pipeline.work_glossary import research_work
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.responses = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            calls.append(kwargs)
            return NS(output_text=json.dumps({"work_matches":True,"entries":report()["entries"]}))
    monkeypatch.setattr(openai, "OpenAI", Client)
    sources = [{"url":"https://publisher.example/characters","title":"出典","text":"雪의 표기는 유키입니다." * 4,"content_hash":"abc","mode":"url"}]
    result = research_work({"title":"작품","sources":sources})
    assert len(calls) == 1 and calls[0]["model"] == "gpt-6-luna"
    assert "tools" not in calls[0] and "temperature" not in calls[0]
    assert result["evidence"][0]["url"] == sources[0]["url"]
    assert result["routing"]["model"] == "gpt-6-luna"


def test_edit_to_existing_target_merges_duplicate_rows(store):
    wid = store.confirm({"title": "작품"})
    data = store.read(wid)
    data["entries"] = build_entries(report("설"), build_entries(report(), []))
    write_json(store.path(wid), data)
    result = store.review(wid, data["entries"][0]["id"], "accepted", data["entries"][1]["target"])
    assert len(result["entries"]) == 1
    assert result["entries"][0]["status"] == "accepted"


def test_search_quota_failure_is_actionable_without_leaking_sdk_error(store):
    class QuotaError(Exception):
        code = 429
    def fail(work):
        raise QuotaError('request-with-secret-key')
    store.researcher = fail
    wid = store.confirm({'title': '작품'})
    store.start(wid)
    store.pool.shutdown(wait=True)
    data = store.read(wid)
    assert data['error_code'] == 'quota_exhausted'
    assert '429' in data['error'] and '요청량 또는 할당량' in data['error']
    assert 'secret-key' not in store.path(wid).read_text()


def test_payment_error_is_distinct_from_rate_limit(store):
    class PaymentError(Exception): code = 402
    def fail(work): raise PaymentError('private-sdk-content')
    store.researcher = fail
    wid = store.confirm({'title':'작품'})
    store.start(wid)
    store.pool.shutdown(wait=True)
    data = store.read(wid)
    assert data['error_code'] == 'payment_required'
    assert '결제 필요(402)' in data['error']
    assert 'private-sdk' not in json.dumps(data)
