#!/usr/bin/env python3
"""세션 종료 시 문서-코드 동기화 검사를 돌리고, 실패하면 사용자에게 알린다.

성공하면 아무것도 출력하지 않는다(조용히 통과).
실패하면 systemMessage JSON을 내보내 터미널에 경고를 띄운다.
차단하지는 않는다 — 알림 용도다.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PYTHON = ROOT / ".venv" / "bin" / "python"

if not PYTHON.exists():
    sys.exit(0)  # venv가 없으면 조용히 통과

result = subprocess.run(
    [str(PYTHON), "-m", "pytest", "tests/test_docs_sync.py", "-q", "--no-header", "-p", "no:cacheprovider"],
    cwd=ROOT,
    capture_output=True,
    text=True,
)

if result.returncode == 0:
    sys.exit(0)

lines = [ln for ln in (result.stdout or "").splitlines() if "AssertionError" in ln or "FAILED" in ln]
detail = "\n".join(lines[:12]) or (result.stdout or "")[-800:]
print(json.dumps({
    "systemMessage": "⚠️ 문서-코드 동기화 검사 실패\n"
                     "코드 변경에 비해 문서가 뒤처졌습니다. docs/DOC_SOURCES.json 참고.\n\n"
                     + detail
}))
