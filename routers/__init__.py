"""편집기 서버(app.py)의 API 라우트를 묶음별로 나눈 패키지.

각 모듈은 build_router(app_module)로 APIRouter를 만들고, app.create_app()이 등록한다.
처리기는 app.py의 도우미·상수를 import 시점에 복사하지 않고 app_module.X로 호출 시점에 찾는다.
- 시험이 monkeypatch.setattr(app, "X", ...)로 바꾼 값을 처리기가 그대로 본다.
- python app.py로 띄우면 모듈 이름이 __main__이다. 여기서 import app을 하면 app.py가 한 번 더 실행돼
  작업 표(_RESTORE_JOBS)·캐시·잠금이 둘로 갈린다. create_app()이 자기 모듈 객체를 넘겨 이를 막는다.
그래서 이 패키지의 모듈은 app을 import하지 않는다.
"""
