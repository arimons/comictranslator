# 문서가 가리키는 소스 파일이 실제로 존재하는지 검사하는 하네스
"""문서-코드 동기화 검사.

문서에 적힌 파일 경로가 실제 리포지토리에 존재하는지 확인한다.
코드를 옮기거나 지우고 문서를 갱신하지 않으면 이 테스트가 실패한다.

검사 규칙:
- `.py`, `.md`, `.html` 참조만 본다. `.json`은 산출물 이름 패턴이 많아 제외한다.
- 디렉터리가 명시된 참조(`proof/src/foo.py`)는 정확히 그 경로에 있어야 한다.
- 파일명만 있는 참조(`foo.py`)는 리포지토리 어디에든 있으면 통과한다.
- 의도적으로 남긴 과거 참조는 RETIRED_REFERENCES에 사유와 함께 등록한다.
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


@pytest.mark.parametrize("doc_path", [p for p in DOC_PATHS if p.exists()], ids=lambda p: p.name)
def test_doc_references_exist(doc_path: Path) -> None:
    """문서가 가리키는 소스 파일이 실제로 존재해야 한다."""
    broken = _broken_references(doc_path)
    assert not broken, (
        f"{doc_path.relative_to(ROOT)}가 존재하지 않는 파일을 가리킵니다: {broken}\n"
        f"코드를 옮겼다면 문서를 갱신하고, 의도적으로 남긴 과거 참조라면 "
        f"tests/test_docs_sync.py의 RETIRED_REFERENCES에 사유와 함께 등록하세요."
    )


def test_retired_references_are_actually_absent() -> None:
    """RETIRED_REFERENCES 목록이 낡지 않도록, 되살아난 항목을 잡는다."""
    revived = [
        name
        for name in RETIRED_REFERENCES
        if any(
            candidate
            for candidate in ROOT.glob(f"**/{name}")
            if "archive" not in candidate.parts and ".venv" not in candidate.parts
        )
    ]
    assert not revived, (
        f"RETIRED_REFERENCES에 등록됐지만 실제 경로에 되살아난 파일이 있습니다: {revived}\n"
        f"해당 항목을 목록에서 제거하세요."
    )
