# print를 logging으로 바꾼 뒤에도 단계 subprocess의 진단 줄이 편집기 진행 로그에 같은 글자로 남는지 검증한다.

import io
import json
import logging
import sys

import pytest

from pipeline.logging_setup import configure_logging
from pipeline.pipeline_runner import stream_subprocess


def test_step_subprocess_streams_logger_lines_verbatim(tmp_path):
    cv = tmp_path / "cv.json"
    cv.write_text(json.dumps([
        {"box_2d": [100, 100, 140, 130], "label": "あ", "confidence": 1.0},
        {"box_2d": [140, 100, 180, 130], "label": "い", "confidence": 1.0},
    ]), encoding="utf-8")

    returncode, lines = stream_subprocess(
        [sys.executable, "-m", "pipeline.text_group_builder_v2", str(cv), str(tmp_path / "tg.json"), "1000"], timeout=60)

    assert returncode == 0
    # 접두어 없이 예전 print와 같은 글자로, CLI 결과 줄보다 먼저 나온다
    assert "  Furigana filter: 1 -> 1 lines" in lines
    assert lines[-1].startswith("Saved 1 text groups to ")


@pytest.fixture
def bare_root_logger():
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    root.handlers = [h for h in root.handlers if not getattr(h, "_comic_translator_handler", False)]
    yield root
    root.handlers, root.level = saved[0], saved[1]


def test_configure_logging_splits_streams_and_is_idempotent(bare_root_logger):
    out, err = io.StringIO(), io.StringIO()
    configure_logging(stdout=out, stderr=err)
    configure_logging(stdout=io.StringIO(), stderr=io.StringIO())  # 두 번째 호출은 처리기를 더 달지 않는다
    log = logging.getLogger("pipeline.example")

    log.info("정보 줄")
    log.warning("경고 줄")
    log.debug("보이지 않는 줄")

    assert out.getvalue() == "정보 줄\n"
    assert err.getvalue() == "경고 줄\n"
    assert sum(getattr(h, "_comic_translator_handler", False) for h in bare_root_logger.handlers) == 2
