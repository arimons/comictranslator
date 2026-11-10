"""진입점에서 한 번 부르는 로깅 설정. 라이브러리 모듈은 logging.getLogger(__name__)만 쓴다.

단계 스크립트는 하위 프로세스로 돌고 stdout·stderr가 합쳐져 편집기 진행 로그로 그대로 흘러가므로,
기본 형식은 메시지만 남긴다(예전 print와 같은 글자). 경고 이상은 stderr, 나머지는 stdout으로 보낸다.
"""
from __future__ import annotations

import logging
import sys
from typing import TextIO

PLAIN_FORMAT = "%(message)s"
SERVER_FORMAT = "%(levelname)s [%(name)s] %(message)s"
_MARK = "_comic_translator_handler"


class _BelowWarning(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


def configure_logging(fmt: str = PLAIN_FORMAT, level: int = logging.INFO,
                      stdout: TextIO | None = None, stderr: TextIO | None = None) -> None:
    """루트 로거에 처리기를 단다. 이 함수가 이미 달아 두었으면(두 번 불림) 그대로 둔다."""
    root = logging.getLogger()
    if any(getattr(handler, _MARK, False) for handler in root.handlers):
        return
    formatter = logging.Formatter(fmt)
    info = logging.StreamHandler(stdout or sys.stdout)
    info.addFilter(_BelowWarning())
    warn = logging.StreamHandler(stderr or sys.stderr)
    warn.setLevel(logging.WARNING)
    for handler in (info, warn):
        handler.setFormatter(formatter)
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(level)
