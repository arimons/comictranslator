# 상주 서버용 LaMa 작업 프로세스. 서버는 모델을 직접 올리지 않고 이 프로세스에 ROI를 보내 결과를 받는다.
#
# 서버 프로세스 안에서 LaMa(torch·MPS)를 돌리면 입력 크기가 바뀔 때마다 작업 메모리가 쌓이고,
# 모델을 내려도 malloc이 쥔 조각은 운영체제로 돌아가지 않았다(docs/BATCH_MEMORY_GROWTH_NOTES.md).
# 작업 프로세스를 끝내면 모델·MPS 작업 메모리·malloc 조각이 모두 돌아온다.
#
# 수명: 처음 LaMa가 필요할 때 띄우고, 한 프로젝트를 작업하는 동안 유지한다.
# 프로젝트 홈으로 나갈 때(release) 또는 LAMA_IDLE_RELEASE_SEC(기본 3600초) 동안 쓰지 않으면 끝낸다.
# 다음 호출이 오면 다시 띄운다(모델 로드 수 초).
#
# 통신: 표준 입출력으로 길이(8바이트) + pickle 프레임을 주고받는다. 같은 사용자의 자식 프로세스끼리만 쓴다.
from __future__ import annotations

import logging
import os
import pickle
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
_HEADER = struct.Struct("<Q")
logger = logging.getLogger(__name__)


def _idle_release_sec() -> float:
    try:
        return max(60.0, float(os.environ.get("LAMA_IDLE_RELEASE_SEC", "3600")))
    except ValueError:
        return 3600.0


def _write_frame(stream: BinaryIO, obj: Any) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_HEADER.pack(len(data)))
    stream.write(data)
    stream.flush()


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise EOFError("LaMa 작업 프로세스와 연결이 끊겼습니다")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _read_frame(stream: BinaryIO) -> Any:
    (size,) = _HEADER.unpack(_read_exact(stream, _HEADER.size))
    return pickle.loads(_read_exact(stream, size))


class LamaWorkerClient:
    """SimpleLama처럼 lama(image, mask) -> PIL.Image로 부른다. 호출은 한 번에 하나씩 순서대로 처리한다."""

    def __init__(self, idle_release_sec: float | None = None, command: list[str] | None = None) -> None:
        self.command = command or [sys.executable, "-m", "pipeline.lama_worker"]
        self.idle_release_sec = idle_release_sec if idle_release_sec is not None else _idle_release_sec()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._last_used = 0.0
        self._started_at = 0.0
        self._watcher: threading.Thread | None = None
        self._stop = threading.Event()

    # ── 호출 ─────────────────────────────────────────────
    def __call__(self, image: Image.Image, mask: Image.Image) -> Image.Image:
        with self._lock:
            proc = self._ensure_started()
            try:
                _write_frame(proc.stdin, ("run", np.asarray(image), np.asarray(mask)))
                kind, payload = _read_frame(proc.stdout)
            except (EOFError, OSError, pickle.UnpicklingError) as exc:
                self._stop_locked("작업 프로세스 오류")
                raise RuntimeError(f"LaMa 작업 프로세스가 응답하지 않습니다: {exc}") from exc
            finally:
                self._last_used = time.monotonic()
        if kind == "err":
            raise RuntimeError(f"LaMa 실행 실패: {payload}")
        return Image.fromarray(payload)

    # ── 수명 ─────────────────────────────────────────────
    def _ensure_started(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._proc = subprocess.Popen(
            self.command,
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # 로그는 서버 로그로 그대로 보낸다
        )
        self._started_at = time.monotonic()
        logger.info(f"[lama] 작업 프로세스 시작 (pid {self._proc.pid})")
        self._start_watcher()
        return self._proc

    def _start_watcher(self) -> None:
        if self._watcher is not None and self._watcher.is_alive():
            return
        self._stop.clear()
        self._watcher = threading.Thread(target=self._watch_idle, name="lama-idle", daemon=True)
        self._watcher.start()

    def _watch_idle(self) -> None:
        interval = min(60.0, self.idle_release_sec / 4)
        while not self._stop.wait(interval):
            # 호출 중이면 잠금을 못 잡으니 다음 주기에 다시 본다
            if not self._lock.acquire(blocking=False):
                continue
            try:
                if not self._running_locked():
                    return
                if time.monotonic() - self._last_used >= self.idle_release_sec:
                    self._stop_locked(f"{self.idle_release_sec / 60:.0f}분 동안 사용 없음")
                    return
            finally:
                self._lock.release()

    def _running_locked(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _stop_locked(self, reason: str) -> bool:
        proc, self._proc = self._proc, None
        self._stop.set()
        if proc is None or proc.poll() is not None:
            return False
        try:
            _write_frame(proc.stdin, ("quit",))
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - 정상 종료가 안 되면 강제로 끝낸다
            proc.kill()
            proc.wait(timeout=5)
        logger.info(f"[lama] 작업 프로세스 종료 (pid {proc.pid}, {reason})")
        return True

    def release(self, reason: str = "요청") -> bool:
        """작업 프로세스를 끝내 메모리를 돌려준다. 진행 중인 호출이 있으면 끝날 때까지 기다린다. 끝냈으면 True."""
        with self._lock:
            return self._stop_locked(reason)

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._running_locked()
            now = time.monotonic()
            return {
                "running": running,
                "pid": self._proc.pid if running else None,
                "idle_sec": round(now - self._last_used, 1) if running and self._last_used else None,
                "uptime_sec": round(now - self._started_at, 1) if running else None,
                "idle_release_sec": self.idle_release_sec,
            }


_client: LamaWorkerClient | None = None
_client_lock = threading.Lock()


def shared_client() -> LamaWorkerClient:
    """서버 전체가 함께 쓰는 작업 프로세스 대리 객체(Step 3 재적용과 배경 복원이 같이 쓴다)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = LamaWorkerClient()
        return _client


def release_shared(reason: str = "요청") -> bool:
    with _client_lock:
        client = _client
    return client.release(reason) if client is not None else False


def shared_status() -> dict[str, Any]:
    with _client_lock:
        client = _client
    if client is None:
        return {"running": False, "pid": None, "idle_sec": None, "uptime_sec": None, "idle_release_sec": _idle_release_sec()}
    return client.status()


# ── 작업 프로세스 쪽 ─────────────────────────────────────
def _default_loader() -> Any:
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    from pipeline.dialogue_extractor import _load_lama_in_process

    return _load_lama_in_process()


def _serve(loader: Any = _default_loader) -> None:
    """요청이 올 때 loader()로 모델을 한 번 올리고, quit 또는 연결 종료까지 처리한다."""
    channel_in = sys.stdin.buffer
    channel_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # 라이브러리 print가 통신 채널을 더럽히지 않게 한다
    from pipeline.logging_setup import configure_logging

    configure_logging(stdout=sys.stderr, stderr=sys.stderr)  # 로그도 통신 채널(stdout)이 아니라 서버 로그로 간다
    lama = None
    while True:
        try:
            message = _read_frame(channel_in)
        except EOFError:
            return
        if message[0] == "quit":
            return
        _, image, mask = message
        try:
            if lama is None:
                lama = loader()
            out = lama(Image.fromarray(image), Image.fromarray(mask))
            _write_frame(channel_out, ("ok", np.asarray(out)))
        except Exception as exc:  # noqa: BLE001 - 실패는 호출한 쪽에 돌려준다
            _write_frame(channel_out, ("err", f"{type(exc).__name__}: {exc}"))


if __name__ == "__main__":
    _serve()
