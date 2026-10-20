import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path, manifest_files
from pipeline.manual_guidance import load_json
"""가짜 검색 응답으로 작품 확인/검토 UI를 점검한다. 외부 API는 호출하지 않는다.

실행: PYTHONPATH=. .venv/bin/python proof/src/verify_work_glossary_ui.py
"""
from pathlib import Path

import uvicorn
from PIL import Image

import app
from pipeline.manual_guidance import write_json
from pipeline.work_glossary import GlossaryStore

BASE = app.PROOF_DIR / "misc" / "glossary_ui"
app.SESSION_ROOT = BASE / "sessions"
app.INPUT_CACHE_DIR = BASE / "input_cache"
app.CV_CACHE_DIR = BASE / "cv_cache"
app.ANALYSIS_CACHE_DIR = BASE / "analysis_cache"


def fake_research(work):
    return {
        "work_matches": True, "summary": "UI 검증용 가상 작품입니다. 실제 작품 조사 결과가 아닙니다.",
        "evidence": [
            {"text": "雪의 한국어 표기는 유키이다.", "url": "https://publisher.example/characters", "title": "가상 출판사 인물 소개"},
            {"text": "雪의 한국어 표기는 유키이다.", "url": "https://wiki.test/characters", "title": "가상 위키 인물 소개"},
        ],
        "entries": [
            {"source": "雪", "target": "유키", "kind": "인물", "uncertain": False, "evidence_indices": [0, 1]},
            {"source": "星", "target": "호시", "kind": "인물", "uncertain": True, "note": "표기 확인 필요", "evidence_indices": []},
        ],
    }


service = GlossaryStore(BASE / "glossaries", researcher=fake_research)
app.glossary_store = lambda: service
sid = "glossary_ui_demo"
session = app.ensure_session(sid)
image = app.session_dir(sid) / "input" / "demo.jpg"
Image.new("RGB", (600, 900), "#faf9f4").save(image)
record = app.image_record(sid, image)
write_json(record["regions_json"], {"analysis_status": "complete", "ocr_region_count": 0, "regions": []})
write_json(record["text_groups_json"], [])
session.update(project_name="용어집 UI 검증", images=[{"id": "demo", "name": "demo.jpg", "input_path": str(image)}])
app.save_session(session)

if __name__ == "__main__":
    uvicorn.run(app.create_app(), host="127.0.0.1", port=7889)
