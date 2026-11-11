"""routers/ 분리 뒤에도 서버가 한 벌의 전역만 쓰는지, 라우트 등록 순서가 매칭에 영향을 주지 않는지 확인한다."""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app

ROOT = Path(__file__).resolve().parents[1]


def test_routes_see_patched_module_globals(monkeypatch):
    # 처리기는 app 전역을 호출 시점에 찾으므로 시험의 바꿔 끼우기가 그대로 적용된다.
    monkeypatch.setattr(app, "stale_code_files", lambda: ["probe.py"])
    response = TestClient(app.create_app()).get("/api/health")
    assert response.status_code == 200
    assert response.json()["stale_code"] == ["probe.py"]


def test_no_two_routes_compete_for_the_same_request():
    # 라우트는 묶음별로 등록된다. 같은 메서드에서 서로의 경로를 받아 버리는 쌍이 없어야 등록 순서가 동작을 바꾸지 않는다.
    routes = [route for route in app.create_app().routes if isinstance(route, APIRoute)]
    clashes = []
    for first in routes:
        for second in routes:
            if first is second or not (first.methods & second.methods):
                continue
            if first.path_regex.match(re.sub(r"\{[^}]+\}", "x", second.path)):
                clashes.append((first.path, second.path))
    assert clashes == []


def test_script_mode_keeps_one_copy_of_server_state(tmp_path):
    # python app.py로 띄우면 모듈 이름이 __main__이다. 라우터가 app을 다시 import하면 전역(작업 표·캐시)이 둘로 갈린다.
    probe = textwrap.dedent(
        f"""
        import json, runpy, sys, types
        from fastapi.testclient import TestClient

        sys.path.insert(0, {str(ROOT)!r})
        result = {{}}

        def fake_run(server_app, host, port):
            main_module = sys.modules["__main__"]
            main_module.stale_code_files = lambda: ["main-probe.py"]
            result["health"] = TestClient(server_app).get("/api/health").json()["stale_code"]
            result["app_imported"] = "app" in sys.modules
            result["routers_loaded"] = "routers.system" in sys.modules

        sys.modules["uvicorn"] = types.SimpleNamespace(run=fake_run)
        sys.argv = ["app.py", "--no-browser", "--port", "1"]
        runpy.run_path({str(ROOT / "app.py")!r}, run_name="__main__")
        print(json.dumps(result))
        """
    )
    env = {**os.environ, "COMIC_TRANSLATOR_HOME": str(tmp_path)}
    completed = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {"health": ["main-probe.py"], "app_imported": False, "routers_loaded": True}
