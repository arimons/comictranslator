# 끝난 복원·AI 지우기 작업 기록이 상시 서버에서 계속 쌓이지 않도록 정리하는 규칙을 검증한다.

import app
import pytest
from fastapi.testclient import TestClient

NOW = 1_000_000.0
HOUR = 3600.0


@pytest.fixture
def jobs(monkeypatch):
    table: dict = {}
    monkeypatch.setattr(app, "_RESTORE_JOBS", table)
    return table


def _job(job_id, status, finished_ago=None, created_ago=0.0):
    job = {"job_id": job_id, "status": status, "created_at": NOW - created_ago, "_job_dir": "/nonexistent"}
    if finished_ago is not None:
        job["finished_at"] = NOW - finished_ago
    return job


def test_old_finished_job_is_pruned_but_running_and_recent_ones_are_kept(jobs):
    jobs["old_done"] = _job("old_done", "done", finished_ago=25 * HOUR, created_ago=26 * HOUR)
    jobs["old_error"] = _job("old_error", "error", finished_ago=30 * HOUR, created_ago=30 * HOUR)
    # 오래전에 시작했어도 아직 실행·대기 중이면 남긴다.
    jobs["running"] = _job("running", "running", created_ago=48 * HOUR)
    jobs["queued"] = _job("queued", "queued", created_ago=48 * HOUR)
    # 방금 끝나 아직 확정하지 않은 후보는 남긴다.
    jobs["recent_done"] = _job("recent_done", "done", finished_ago=2 * HOUR, created_ago=2 * HOUR)

    assert app.prune_restore_jobs(now=NOW) == ["old_done", "old_error"]
    assert set(jobs) == {"running", "queued", "recent_done"}


def test_count_cap_drops_oldest_finished_first_and_spares_fresh_jobs(jobs, monkeypatch):
    monkeypatch.setattr(app, "_RESTORE_JOB_MAX_FINISHED", 2)
    jobs["a"] = _job("a", "done", finished_ago=5 * HOUR)
    jobs["b"] = _job("b", "done", finished_ago=4 * HOUR)
    jobs["c"] = _job("c", "done", finished_ago=10 * 60)  # 1시간이 안 됐으므로 상한을 넘어도 남긴다
    jobs["d"] = _job("d", "done", finished_ago=5 * 60)
    jobs["r"] = _job("r", "running")

    assert app.prune_restore_jobs(now=NOW) == ["a", "b"]
    assert set(jobs) == {"c", "d", "r"}


def test_poll_and_accept_keep_working_for_kept_jobs(jobs):
    client = TestClient(app.create_app())
    jobs["recent_done"] = _job("recent_done", "done", finished_ago=60)
    jobs["old_done"] = _job("old_done", "done", finished_ago=48 * HOUR)

    app.prune_restore_jobs(now=NOW)

    assert client.get("/api/restore/job/recent_done").json()["status"] == "done"
    assert client.get("/api/restore/job/old_done").status_code == 404
