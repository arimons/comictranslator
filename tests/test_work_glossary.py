"""작품 메모(인물·말투·용어)와 region 화자 지정이 번역 프롬프트로 전달되는 계약."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as app
from pipeline.manual_guidance import write_json
from pipeline.work_glossary import GlossaryStore, glossary_snapshot, page_speakers, speaker_prompt

LEGACY = {"work_id": "a" * 24, "work": {"title": "사망플래그"}, "status": "ready", "version": 3, "entries": [
    {"kind": "인물", "target": "해롤드 스톡스", "aliases": ["해롤드"], "source_forms": ["ハロルド・ストークス"], "status": "auto"},
    {"kind": "가문", "target": "스톡스 가", "aliases": ["스톡스"], "source_forms": ["ストークス家"], "status": "auto"},
    {"kind": "인물", "target": "제외된 인물", "status": "rejected"},
]}


def test_legacy_glossary_migrates_once_and_protagonist_is_key_one(tmp_path):
    store = GlossaryStore(tmp_path)
    write_json(tmp_path / f"{'a' * 24}.json", LEGACY)
    data = store.read("a" * 24)
    assert [row["name"] for row in data["characters"]] == ["해롤드 스톡스"]
    assert data["terms"][0]["korean"] == "스톡스 가" and "ストークス家" in data["terms"][0]["original"]
    assert (tmp_path / f"{'a' * 24}.legacy.json").exists()

    data["characters"].append({"name": "엘리", "speech": "존댓말", "protagonist": True})
    saved = store.save("a" * 24, data)
    # 주인공은 한 명이고 항상 1번(표 맨 앞)이다.
    assert [(row["name"], row["protagonist"]) for row in saved["characters"]] == [("엘리", True), ("해롤드 스톡스", False)]
    prompt = glossary_snapshot(saved)["prompt"]
    assert "엘리 (주인공)" in prompt and "말투 존댓말" in prompt and "ストークス家 → 스톡스 가" in prompt


def test_speaker_priority_manual_then_narration_then_text_based_default():
    snapshot = {"characters": [{"id": "c00000001", "name": "해롤드", "protagonist": True},
                               {"id": "c00000002", "name": "엘리", "protagonist": False}]}
    regions = [{"region_id": 1, "type": "bubble", "anchor_ids": [3]},
               {"region_id": 2, "type": "bubble", "anchor_ids": [4]},
               {"region_id": 3, "type": "unboxed", "anchor_ids": [5]},
               {"region_id": 4, "type": "unboxed", "anchor_ids": [6]}]
    speakers = page_speakers(regions, {"3": "c00000002", "6": "narration"}, snapshot)
    assert [(row["region_id"], row["speaker"], row["source"]) for row in speakers] == [
        (1, "엘리", "manual"), (3, "해롤드", "default"), (4, "내레이션", "narration")]
    prompt = speaker_prompt(speakers)
    assert "region_id 1: 엘리" in prompt and "중립적인 서술체" in prompt
    assert "region_id 2" not in prompt


def test_memo_api_binds_saves_drafts_and_assigns_speakers(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setattr(app, "GLOSSARY_DIR", tmp_path / "temp_output/v2_glossaries")
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "temp_output/v2_sessions")
    data = app.ensure_session("book")
    original = app.session_dir("book") / "input/page.png"
    Image.new("RGB", (60, 80), "white").save(original)
    data["images"] = [app.image_manifest_item(original, app.file_sha256(original))]
    app.save_session(data)
    image_id = data["images"][0]["id"]
    client = TestClient(app.create_app())

    assert client.get("/api/glossary/missing").status_code == 400
    assert not app.session_dir("missing").exists()
    bound = client.post("/api/glossary/bind", json={"session_id": "book", "title": "사망플래그"}).json()
    wid = bound["glossary"]["work_id"]
    saved = client.post("/api/glossary/save", json={"session_id": "book", "work_id": wid,
                        "characters": [{"name": "해롤드", "speech": "거친 반말", "protagonist": True}], "terms": []}).json()
    cid = saved["glossary"]["characters"][0]["id"]

    # 초안은 새 항목만 더하고 사용자가 쓴 칸은 유지한다. 네트워크·OpenAI는 호출하지 않는다.
    store = app.glossary_store()
    monkeypatch.setattr(store, "drafter", lambda title, source, existing: {
        "characters": [{"name": "해롤드", "speech": "덮어쓰면 안 됨", "personality": "오만"}, {"name": "엘리", "protagonist": True}],
        "terms": [{"korean": "스톡스 가"}]})
    drafted = client.post("/api/glossary/draft", json={"session_id": "book", "work_id": wid, "url": "https://example.com/w",
                          "text": "작품 소개 본문입니다. 해롤드와 엘리가 등장하는 이야기입니다."}).json()["glossary"]
    assert [(row["name"], row["protagonist"]) for row in drafted["characters"]] == [("해롤드", True), ("엘리", False)]
    assert drafted["characters"][0]["personality"] == "오만" and drafted["characters"][0]["speech"] == "거친 반말"

    response = client.post("/api/speakers", json={"session_id": "book", "image_id": image_id, "tg_ids": [2, 3], "character_id": cid})
    assert response.json()["by_tg"] == {"2": cid, "3": cid}
    assert client.post("/api/speakers", json={"session_id": "book", "image_id": image_id, "tg_ids": [2], "character_id": "cffffffff"}).status_code == 400
    cleared = client.post("/api/speakers", json={"session_id": "book", "image_id": image_id, "tg_ids": [2], "character_id": None}).json()
    assert cleared["by_tg"] == {"3": cid}
    assert client.get(f"/api/speakers/book/{image_id}").json()["by_tg"] == {"3": cid}


def test_cv_match_counts_memo_originals_and_auto_links_only_clear_winner(tmp_path, monkeypatch):
    from pipeline.work_glossary import match_works
    flag = {"work_id": "b" * 24, "title": "사망플래그", "characters": [{"original": "ハロルド・ストークス, ハロルド"}, {"original": "エリカ"}],
            "terms": [{"original": "スメラギ家"}]}
    other = {"work_id": "c" * 24, "title": "다른 작품", "characters": [{"original": "エリカ"}], "terms": []}
    result = match_works("ハロルドさま!スメラギ家のエリカです", [flag, other])
    assert result["candidates"][0]["work_id"] == "b" * 24 and result["candidates"][0]["strong"]
    weak = match_works("エリカです", [flag, other])
    assert not weak["candidates"][0]["strong"]
    assert match_works("", [flag]) == {"has_cv": False, "candidates": []}

    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setattr(app, "GLOSSARY_DIR", tmp_path / "temp_output/v2_glossaries")
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path / "temp_output/v2_sessions")
    data = app.ensure_session("vol")
    original = app.session_dir("vol") / "input/p1.png"
    Image.new("RGB", (20, 20)).save(original)
    data["images"] = [app.image_manifest_item(original, app.file_sha256(original))]
    app.save_session(data)
    write_json(app.session_dir("vol") / "work/json/p1_cv.json", [{"label": "ハロルド"}, {"label": "スメラギ家"}, {"label": "エリカ"}])
    write_json(tmp_path / "temp_output/v2_glossaries" / f"{'b' * 24}.json", {**flag, "format": "work-memo-v1"})
    client = TestClient(app.create_app())
    payload = client.get("/api/glossary/vol").json()
    assert payload["glossary"] is None and payload["match"]["candidates"][0]["strong"]
    assert not app.glossary_binding_path("vol").exists()  # GET은 연결하지 않는다
    bound = client.post("/api/glossary/bind", json={"session_id": "vol", "work_id": "b" * 24, "matched_by": "cv",
                                                     "hits": payload["match"]["candidates"][0]["hits"]}).json()
    assert bound["binding"]["matched_by"] == "cv" and bound["match"]["candidates"]
    unbound = client.post("/api/glossary/unbind", json={"session_id": "vol"}).json()
    assert unbound["glossary"] is None and unbound["binding"]["matched_by"] == "none"


def test_japanese_draft_merges_by_original_form_instead_of_duplicating():
    from pipeline.work_glossary import merge_draft
    memo = {"characters": [{"id": "c00000001", "name": "해롤드 스톡스", "original": "ハロルド・ストークス", "protagonist": True}],
            "terms": [{"korean": "스톡스 가", "original": "ストークス家"}]}
    draft = {"characters": [{"name": "해럴드 스토크스", "original": "ハロルド・ストークス, ハロルド", "personality": "주인공"},
                            {"name": "리타", "original": "リタ"}],
             "terms": [{"korean": "스토크스 가문", "original": "ストークス家"}]}
    merged = merge_draft(memo, draft)
    assert [row["name"] for row in merged["characters"]] == ["해롤드 스톡스", "리타"]
    assert merged["characters"][0]["original"] == "ハロルド・ストークス, ハロルド"
    assert merged["characters"][0]["personality"] == "주인공"
    assert [row["korean"] for row in merged["terms"]] == ["스톡스 가"]
