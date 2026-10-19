"""새 처리 기준 용어를 도입해도 기존 타입·캐시·영역 대응을 유지한다."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from pipeline.region_types import region_processing_basis, regions_with_processing_basis


CASES = [
    ({"type": "bubble", "source_method": "flood"}, "boundary_based"),
    ({"type": "bubble", "source_method": "manual_edge_barrier_rescue"}, "boundary_based"),
    ({"type": "unboxed", "source_method": "bbox_fallback"}, "text_based"),
    ({"type": "unboxed", "inpaint_engine": "lama"}, "text_based"),
    ({"type": "bubble", "processing_basis": "text_based"}, "boundary_based"),
    ({"type": "unboxed", "processing_basis": "boundary_based"}, "text_based"),
    ({"processing_basis": "text_based"}, "text_based"),
    ({"processing_basis": "boundary_based"}, "boundary_based"),
    ({"type": "bubble", "quality_flags": ["unboxed_fallback_candidate"]}, "boundary_based"),
    ({"type": "toString"}, None),
    ({"type": []}, None),
    ({"processing_basis": {}}, None),
    ({}, None),
]


@pytest.mark.parametrize("region,expected", CASES)
def test_processing_basis_preserves_stored_type(region, expected):
    assert region_processing_basis(region) == expected


def test_frontend_and_backend_agree_on_legacy_and_new_records():
    node = shutil.which("node")
    assert node, "프론트엔드 호환 검증에는 Node가 필요합니다."
    source = Path(__file__).resolve().parents[1] / "frontend/src/regionTypes.js"
    # 프론트엔드 package.json의 모듈 설정을 바꾸지 않고 순수 용어 모듈을 읽는다.
    js = '''import fs from 'node:fs';
const source = fs.readFileSync(process.argv[1], 'utf8');
const mod = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
let input = ''; for await (const chunk of process.stdin) input += chunk;
console.log(JSON.stringify(JSON.parse(input).map(r => mod.regionProcessingBasis(r))));'''
    result = subprocess.run([node, "--input-type=module", "-e", js, str(source)],
                            input=json.dumps([region for region, _ in CASES]), text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [expected for _, expected in CASES]


def test_projection_does_not_mutate_legacy_artifacts():
    payload = {"regions": [{"region_id": 4, "type": "unboxed", "unboxed_rect": [1, 2, 3, 4]}]}
    original = copy.deepcopy(payload)
    result = regions_with_processing_basis(payload)
    assert payload == original
    assert result["regions"][0] == {**original["regions"][0], "processing_basis": "text_based"}


def test_page_read_projects_cached_types_without_rewriting_cache(tmp_path, monkeypatch):
    import main_v2
    from fastapi.testclient import TestClient
    from PIL import Image

    monkeypatch.setattr(main_v2, "SESSION_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(main_v2, "ANALYSIS_CACHE_DIR", tmp_path / "cache")
    session = main_v2.ensure_session("legacy")
    image = main_v2.SESSION_ROOT / "legacy/input/page.jpg"
    Image.new("RGB", (100, 150), "white").save(image)
    session["images"] = [{"id": "page", "name": "page.jpg", "input_path": str(image)}]
    main_v2.save_session(session)
    record = main_v2.image_record("legacy", image)
    cache = main_v2.ANALYSIS_CACHE_DIR / "old/regions.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"regions": [
        {"region_id": 1, "type": "bubble", "source_method": "manual_edge_barrier_rescue"},
        {"region_id": 4, "type": "unboxed", "unboxed_rect": [1, 2, 3, 4]},
    ]}))
    target = Path(record["regions_json"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(cache)
    before = (cache.read_bytes(), cache.stat().st_mtime_ns, target.lstat().st_ino)
    result = TestClient(main_v2.create_app()).get("/api/session/legacy/page/page")
    assert result.status_code == 200
    regions = result.json()["image"]["regions"]["regions"]
    assert [r["processing_basis"] for r in regions] == ["boundary_based", "text_based"]
    assert [r["type"] for r in regions] == ["bubble", "unboxed"]
    assert [r["region_id"] for r in regions] == [1, 4]
    assert before == (cache.read_bytes(), cache.stat().st_mtime_ns, target.lstat().st_ino)
