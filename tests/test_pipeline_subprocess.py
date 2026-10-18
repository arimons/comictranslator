# 파이프라인 subprocess 실행과 외부 API 응답 처리의 경계 조건을 검증한다.

import subprocess
import sys
import time

import pytest
import requests

from pipeline import cloud_vision_api
from pipeline.gemini_ocr_translate_v5 import _extract_json_response
from pipeline.pipeline_runner import stream_subprocess


def test_stream_subprocess_survives_non_utf8_output():
    code = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe bytes\\n'); sys.stdout.flush()"

    returncode, lines = stream_subprocess([sys.executable, "-c", code], timeout=10)

    assert returncode == 0
    assert lines[0].startswith("ok ")


def test_stream_subprocess_times_out_on_partial_line():
    # 개행 없이 멈춘 자식도 timeout 안에 종료돼야 한다.
    code = "import sys, time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(30)"
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        stream_subprocess([sys.executable, "-c", code], timeout=1)

    assert time.monotonic() - started < 10


def test_stream_subprocess_lines_arrive_before_child_exits():
    code = "import time; print('first', flush=False); time.sleep(1.5); print('second')"
    arrivals = []
    started = time.monotonic()

    stream_subprocess([sys.executable, "-c", code], timeout=10, on_line=lambda line: arrivals.append(time.monotonic() - started))

    # PYTHONUNBUFFERED가 없으면 첫 줄도 자식 종료 시점(1.5초 뒤)에야 도착한다.
    assert arrivals[0] < 1.0


@pytest.mark.parametrize(
    "response_text, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('설명입니다.\n```json\n{"a": 1}\n```\n끝', {"a": 1}),
    ],
)
def test_extract_json_response_handles_code_fences(response_text, expected):
    assert _extract_json_response(response_text) == expected


def test_cloud_vision_connection_error_does_not_leak_api_key(monkeypatch):
    def fail_post(url, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(cloud_vision_api.requests, "post", fail_post)

    with pytest.raises(RuntimeError) as excinfo:
        cloud_vision_api._post("SECRET_KEY_123", {})

    assert "SECRET_KEY_123" not in str(excinfo.value)
