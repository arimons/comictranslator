import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.paths import DATA_HOME, PROJECTS_DIR, PROOF_DIR
from pipeline.project_paths import manifest_path, manifest_files
from pipeline.manual_guidance import load_json
"""Ore_3를 유료 처리 없이 기존 페이지 정리 API로 별도 프로젝트에 생성한다."""
from pathlib import Path
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import app
from fastapi.testclient import TestClient

SOURCE = "20260531_153718"
TITLE = "사망플래그 3권(자동분할)"


def original_snapshot():
    base = app.session_dir(SOURCE)
    return {str(path.relative_to(base)): [path.stat().st_size, path.stat().st_mtime_ns]
            for path in base.rglob("*") if path.is_file()}


def main():
    before = original_snapshot()
    manifest_before = app.session_file(SOURCE).read_bytes()
    with patch.object(app, "run_single_image", side_effect=AssertionError("유료 분석 실행 금지")), \
         patch.object(app, "redraw_image_with_openai", side_effect=AssertionError("이미지 생성 실행 금지")), \
         patch.object(app, "stream_subprocess", side_effect=AssertionError("파이프라인 실행 금지")):
        client = TestClient(app.create_app())
        response = client.post("/api/pages/preview", json={"session_id": SOURCE})
        response.raise_for_status()
        plan = response.json()
        assert (plan["source_count"], plan["output_count"]) == (83, 165)
        existing = []
        for path in manifest_files(app.SESSION_ROOT):
            data = json.loads(path.read_text())
            if data.get("project_name") == TITLE and data.get("parent_session_id") == SOURCE and data.get("page_preparation", {}).get("revision") == plan["revision"]:
                existing.append(data)
        if existing:
            target = existing[0]
        else:
            response = client.post("/api/pages/prepare", json={"session_id": SOURCE, "revision": plan["revision"]})
            response.raise_for_status()
            sid = response.json()["session_id"]
            response = client.post("/api/session/meta", json={"session_id": sid, "project_name": TITLE})
            response.raise_for_status()
            target = response.json()["session"]
    assert len(target["images"]) == 165
    assert all(Path(page["input_path"]).is_file() for page in target["images"])
    assert target["images"][0]["page_side"] == "center"
    assert [item["page_side"] for item in target["images"][1:3]] == ["right", "left"]
    assert before == original_snapshot(), "원본 프로젝트 파일 변경 감지"
    assert manifest_before == app.session_file(SOURCE).read_bytes()
    base = app.session_dir(target["session_id"])
    assert not list((base / "work").rglob("*.json")), "분석 산출물이 생성되면 안 됩니다."
    assert not list((base / "output").rglob("*.jpg"))
    report = {"session_id": target["session_id"], "project_name": TITLE, "parent_session_id": SOURCE,
              "image_count": 165, "original_files_unchanged": len(before), "analysis_started": False,
              "image_generation_started": False, "first_pages": target["images"][:3]}
    output = PROOF_DIR / 'misc/ore3-created-project.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != "first_pages"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
