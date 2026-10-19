# 문서가 가리키는 소스 파일이 실제로 존재하는지 검사하는 하네스
"""문서-코드 동기화 검사.

문서에 적힌 파일 경로가 실제 리포지토리에 존재하는지 확인한다.
코드를 옮기거나 지우고 문서를 갱신하지 않으면 이 테스트가 실패한다.

검사 규칙:
- `.py`, `.md`, `.html` 참조만 본다. `.json`은 산출물 이름 패턴이 많아 제외한다.
- 디렉터리가 명시된 참조(`proof/src/foo.py`)는 정확히 그 경로에 있어야 한다.
- 파일명만 있는 참조(`foo.py`)는 리포지토리 어디에든 있으면 통과한다.
- 의도적으로 남긴 과거 참조는 RETIRED_REFERENCES에 사유와 함께 등록한다.
- 단, '현재 구조' 트리(`├──`/`└──` 줄)의 파일 항목은 RETIRED_REFERENCES 예외 없이
  archive 밖에 실재해야 한다. 트리는 지금 구조를 선언하는 자리이기 때문이다.
"""
from __future__ import annotations

import html as html_module
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 검사 대상 문서
DOC_PATHS = [
    *sorted(ROOT.glob("docs/**/*.md")),
    *sorted(ROOT.glob("docs/**/*.html")),
    ROOT / "CLAUDE.md",
    ROOT / "AGENTS.md",
    ROOT / "README.md",
]

# 소스 파일 참조 패턴. 산출물(.json)은 {name}_cv.json 같은 템플릿이 많아 제외한다.
REFERENCE_PATTERN = re.compile(r"\b((?:[\w./-]+/)?[\w-]+\.(?:py|md|html))\b")

# 폐기됐지만 문서에 의도적으로 남겨둔 참조. {참조: 사유}
RETIRED_REFERENCES = {
    "main.py": "Gradio UI 폐기 이력 설명용 (archive/retired_gradio_ui/)",
    "run_test_pipeline.py": "Gradio UI와 함께 폐기된 실행 스크립트",
    "translate_cover.py": "구 표지 번역 경로. archive 보관",
    "gemini_image_redraw.py": "main.py 전용이었던 표지 재생성 모듈. archive 보관",
    "cluster_cv_v5.py": "text_group_builder_v2.py로 대체된 구 clustering",
    "apply_masking.py": "pipeline/archive/ 보존. 레거시 참고용",
    "floodfill_anchor_probe.py": "pipeline/archive/ 보존. 레거시 참고용",
    "difficulty_scorer.py": "파이프라인 미연결로 pipeline/archive/ 이전",
    "integrate_ocr_v3.py": "archive 이전 완료",
    "gemini_api.py": "archive 이전 완료",
    "bbox_utils.py": "archive 이전 완료",
    "preprocess_for_cv.py": "archive 이전 완료",
    "gemini_batch_api.py": "archive 이전 완료",
    "text_group_builder.py": "v2로 통합됨",
    # 아래 4개는 리포지토리에서 제거됐고, 문서에는 '이제 없다'는 설명으로만 등장한다.
    "run_full_batch.py": "구 proof 전체 배치 검증. 제거됨. 현재 배치는 run_pipeline.py",
    "run_gemini_ocr.py": "구 OCR sheet 검증. 메인 파이프라인으로 승격 후 제거됨",
    "run_inpaint_test.py": "구 inpaint 실험. pipeline/experimental로 흡수 후 제거됨",
    "run_masked_batch.py": "구 FloodFill+masking 단독 검증. 제거됨",
}


def _extract_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix != ".html":
        return raw
    stripped = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    return html_module.unescape(re.sub(r"<[^>]+>", " ", stripped))


def _reference_exists(reference: str, doc_path: Path) -> bool:
    if "/" in reference:
        # 경로가 명시된 참조는 그 자리에 정확히 있어야 한다.
        # 리포지토리 루트 기준과 문서 위치 기준(상대 링크) 둘 다 인정한다.
        return (ROOT / reference).exists() or (doc_path.parent / reference).exists()
    # 파일명만 있는 참조는 리포지토리 어디에든 있으면 된다.
    return any(ROOT.glob(f"**/{reference}"))


def _broken_references(path: Path) -> list[str]:
    text = _extract_text(path)
    # 공식 도움말 같은 외부 URL의 .html은 리포지토리 파일 참조가 아니다.
    text = re.sub(r"https?://[^\s<>\])]+", "", text)
    broken = []
    for reference in sorted(set(REFERENCE_PATTERN.findall(text))):
        if reference.startswith(("http", "www")) or reference.startswith("_"):
            continue
        if Path(reference).name in RETIRED_REFERENCES:
            continue
        if _reference_exists(reference, path):
            continue
        broken.append(reference)
    return broken


def test_external_html_links_are_not_local_references(tmp_path: Path) -> None:
    doc = tmp_path / "external.md"
    doc.write_text("[공식 도움말](https://helpx.adobe.com/photoshop/guide.html)\nmissing_local_file.py", encoding="utf-8")
    assert _broken_references(doc) == ["missing_local_file.py"]


@pytest.mark.parametrize("doc_path", [p for p in DOC_PATHS if p.exists()], ids=lambda p: p.name)
def test_doc_references_exist(doc_path: Path) -> None:
    """문서가 가리키는 소스 파일이 실제로 존재해야 한다."""
    broken = _broken_references(doc_path)
    assert not broken, (
        f"{doc_path.relative_to(ROOT)}가 존재하지 않는 파일을 가리킵니다: {broken}\n"
        f"코드를 옮겼다면 문서를 갱신하고, 의도적으로 남긴 과거 참조라면 "
        f"tests/test_docs_sync.py의 RETIRED_REFERENCES에 사유와 함께 등록하세요."
    )


# 현재 구조 트리를 담은 문서. 트리 줄의 파일 항목은 RETIRED_REFERENCES로 면제하지 않는다.
TREE_DOCS = [ROOT / "docs" / "CODEBASE_FILE_MAP.md", ROOT / "AGENTS.md"]
TREE_ENTRY_PATTERN = re.compile(r"[├└]──\s*([^\s#]+)")
# 파일 이름을 모을 때 건너뛰는 폴더. archive는 '현재 구조'가 아니고, 나머지는 의존성·생성물이다.
TREE_SKIP_DIRS = {"archive", ".git", ".venv", "node_modules", "__pycache__", "temp_output", "output"}


def _live_file_names() -> set[str]:
    """archive와 의존성·생성물 폴더를 뺀 리포지토리 파일 이름 집합."""
    import os

    names: set[str] = set()
    for _dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in TREE_SKIP_DIRS]
        names.update(filenames)
    return names


@pytest.mark.parametrize("doc_path", [p for p in TREE_DOCS if p.exists()], ids=lambda p: p.name)
def test_structure_tree_entries_exist(doc_path: Path) -> None:
    """'현재 구조' 트리에 적힌 파일은 archive 밖에 실재해야 한다(디렉터리·archive 항목 제외)."""
    live_names = _live_file_names()
    missing = []
    for line in doc_path.read_text(encoding="utf-8").splitlines():
        match = TREE_ENTRY_PATTERN.search(line)
        if not match:
            continue
        entry = match.group(1).rstrip(",")
        if entry.endswith("/") or "archive" in entry or not Path(entry).suffix:
            continue
        if Path(entry).name not in live_names:
            missing.append(entry)
    assert not missing, (
        f"{doc_path.relative_to(ROOT)}의 구조 트리에 현재 없는 파일이 있습니다: {missing}\n"
        f"폐기·보관된 파일은 트리에서 빼고, 필요하면 본문에 '보관됨'으로 적으세요."
    )


def test_retired_references_are_actually_absent() -> None:
    """RETIRED_REFERENCES 목록이 낡지 않도록, 되살아난 항목을 잡는다."""
    revived = [
        name
        for name in RETIRED_REFERENCES
        if any(
            candidate
            for candidate in ROOT.glob(f"**/{name}")
            if "archive" not in candidate.parts
            and not any(part.startswith(".venv") for part in candidate.parts)  # .venv 계열 가상환경 제외
        )
    ]
    assert not revived, (
        f"RETIRED_REFERENCES에 등록됐지만 실제 경로에 되살아난 파일이 있습니다: {revived}\n"
        f"해당 항목을 목록에서 제거하세요."
    )


# ---------------------------------------------------------------------------
# 2단계: 코드가 바뀌었는데 문서가 따라오지 않은 경우 검사
# ---------------------------------------------------------------------------

DOC_SOURCES_PATH = ROOT / "docs" / "DOC_SOURCES.json"


def _load_doc_sources() -> dict:
    import json

    return json.loads(DOC_SOURCES_PATH.read_text(encoding="utf-8"))["docs"]


def _last_commit_epoch(rel_path: str) -> int | None:
    """해당 경로를 마지막으로 수정한 커밋 시각. 이력이 없으면 None."""
    import subprocess

    result = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", rel_path],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = result.stdout.strip()
    return int(out) if out else None


def test_doc_sources_manifest_paths_exist() -> None:
    """매니페스트에 적힌 문서와 소스가 모두 실재해야 한다."""
    missing = []
    for doc, meta in _load_doc_sources().items():
        if not (ROOT / doc).exists():
            missing.append(f"문서 없음: {doc}")
        for source in meta.get("sources", []):
            if not (ROOT / source).exists():
                missing.append(f"소스 없음: {doc} → {source}")
    assert not missing, "docs/DOC_SOURCES.json이 실재하지 않는 경로를 가리킵니다:\n  " + "\n  ".join(missing)


def test_every_doc_is_declared_in_manifest() -> None:
    """docs/의 모든 문서가 매니페스트에 등록돼 있어야 한다."""
    declared = set(_load_doc_sources())
    actual = {
        str(p.relative_to(ROOT))
        for p in [*ROOT.glob("docs/*.md"), *ROOT.glob("docs/*.html")]
        if p.name != "DOC_SOURCES.json"
    }
    undeclared = sorted(actual - declared)
    assert not undeclared, (
        f"docs/DOC_SOURCES.json에 등록되지 않은 문서가 있습니다: {undeclared}\n"
        f"role과 sources를 정해 등록하세요."
    )


def test_canonical_docs_are_not_stale() -> None:
    """정본 문서는 선언한 소스보다 오래되면 안 된다."""
    stale = []
    for doc, meta in _load_doc_sources().items():
        if meta.get("role") != "canonical":
            continue
        doc_epoch = _last_commit_epoch(doc)
        if doc_epoch is None:
            continue  # 아직 커밋되지 않은 문서는 검사 대상이 아니다
        for source in meta.get("sources", []):
            source_epoch = _last_commit_epoch(source)
            if source_epoch is not None and source_epoch > doc_epoch:
                stale.append(f"{doc} ← {source}")
    assert not stale, (
        "소스가 문서보다 나중에 바뀌었습니다. 문서를 갱신하세요:\n  "
        + "\n  ".join(stale)
        + "\n\n문서 내용에 영향이 없는 변경이었다면, 문서를 함께 커밋해 시각을 맞추거나 "
        "docs/DOC_SOURCES.json의 sources에서 해당 파일을 빼세요."
    )


# 편집기 단축키: frontend/src/keymap.js의 각 항목이 매뉴얼 단축키 표에 같은 키로 적혀 있어야 한다.
KEYMAP_PATH = ROOT / "frontend" / "src" / "keymap.js"
EDITOR_MANUAL_PATH = ROOT / "docs" / "editor_manual.html"
# keymap.js 항목은 `{ id: "...", ... keys: [[...]], ...}` 꼴이고 id와 keys 사이에 중괄호가 없다.
KEYMAP_ENTRY_PATTERN = re.compile(r'\{\s*id:\s*"([^"]+)"[^{}]*?keys:\s*(\[\[.*?\]\])', re.S)


def _keymap_entries() -> dict[str, list[list[str]]]:
    import json

    source = KEYMAP_PATH.read_text(encoding="utf-8")
    return {entry_id: json.loads(keys) for entry_id, keys in KEYMAP_ENTRY_PATTERN.findall(source)}


def _manual_shortcut_rows() -> dict[str, set[str]]:
    text = EDITOR_MANUAL_PATH.read_text(encoding="utf-8")
    rows = {}
    for entry_id, body in re.findall(r'<tr data-shortcut="([^"]+)">(.*?)</tr>', text, flags=re.S):
        first_cell = re.search(r"<td>(.*?)</td>", body, flags=re.S)
        cell = first_cell.group(1) if first_cell else ""
        rows[entry_id] = {html_module.unescape(token).strip() for token in re.findall(r"<kbd>(.*?)</kbd>", cell)}
    return rows


def test_keymap_parsed() -> None:
    """정규식이 keymap.js를 제대로 읽는지 확인한다(항목이 비면 아래 검사가 의미 없어진다)."""
    entries = _keymap_entries()
    assert len(entries) >= 40, f"keymap.js 항목을 {len(entries)}개만 읽었습니다. 항목 형식을 확인하세요."
    assert "view.fit" in entries and entries["view.fit"] == [["Shift", "1"]]


def test_keymap_entries_documented_in_manual() -> None:
    """keymap.js의 모든 단축키가 editor_manual.html 단축키 표에 같은 키로 있어야 한다."""
    rows = _manual_shortcut_rows()
    problems = []
    for entry_id, combos in _keymap_entries().items():
        if entry_id not in rows:
            problems.append(f"{entry_id}: 매뉴얼에 <tr data-shortcut=\"{entry_id}\"> 행이 없음")
            continue
        missing = sorted({token for combo in combos for token in combo} - rows[entry_id])
        if missing:
            problems.append(f"{entry_id}: 매뉴얼 행에 키 {missing} 없음")
    stale = sorted(set(rows) - set(_keymap_entries()))
    if stale:
        problems.append(f"keymap.js에 없는 매뉴얼 행: {stale}")
    assert not problems, "단축키 표가 keymap.js와 다릅니다:\n" + "\n".join(problems)
