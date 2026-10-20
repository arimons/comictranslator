"""작업 데이터 홈을 옮겨도 원본·마스크·생성 후보의 대응을 유지한다."""
import json
import shutil
from pathlib import Path
from PIL import Image
from pipeline.manual_guidance import load_json, write_json
from pipeline.project_paths import manifest_files, manifest_path
from pipeline.editor_review_bridge import ReviewStore
from pipeline.image_generation_bridge import ImageGenerationStore


def test_new_project_relative_paths_survive_home_move(tmp_path):
    root = tmp_path / "old"
    base = root / "data/projects/book"
    base.mkdir(parents=True)
    write_json(base / "project.json", {"images": [{"id": "page", "input_path": str(base / "input/page.png")}]})
    (base / "input").mkdir()
    Image.new("RGB", (100, 200)).save(base / "input/page.png")
    mask = base / "work/masks/page_R1_mask.png"
    mask.parent.mkdir(parents=True)
    Image.new("L", (100, 200), 255).save(mask)
    write_json(base / "work/json/page_regions.json", {"regions": [
        {"region_id": 1, "type": "bubble", "bubble_rect": [0, 0, 100, 200], "mask_ref": str(mask)}]})
    raw = json.loads((base / "project.json").read_text())
    assert raw["images"][0]["input_path"] == "input/page.png"
    assert json.loads((base / "work/json/page_regions.json").read_text())["regions"][0]["mask_ref"] == "work/masks/page_R1_mask.png"
    moved = tmp_path / "new"
    shutil.move(root, moved)
    store = ReviewStore(moved)
    page = store.inspect_page("book", "page")
    assert page["artifacts"]["original"]["available"]
    assert store.view_region("book", "page", 1, artifact="region_mask")[1]
    loaded = load_json(moved / "data/projects/book/project.json")
    assert Path(loaded["images"][0]["input_path"]).is_file()


def test_legacy_manifest_read_and_current_manifest_precedence(tmp_path):
    base = tmp_path / "book"
    write_json(base / "session.json", {"images": [], "project_name": "old"})
    assert manifest_path(base).name == "session.json"
    write_json(base / "project.json", {"images": [], "project_name": "new"})
    assert len(manifest_files(tmp_path)) == 1
    assert load_json(manifest_path(base))["project_name"] == "new"


def test_generated_candidate_metadata_uses_project_paths(tmp_path):
    base = tmp_path / "data/projects/book"
    write_json(base / "project.json", {"images": []})
    meta = base / "work/image_generation/page/result-123.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    ImageGenerationStore._write(meta, {"preview_path": str(meta.with_suffix('.png'))})
    assert json.loads(meta.read_text())["preview_path"] == "work/image_generation/page/result-123.png"
