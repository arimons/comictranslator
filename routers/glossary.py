"""작품 메모(용어집) 연결·저장·초안과 나무위키 조회 라우트.

처리기는 app.py 전역을 app_module.X로 호출 시점에 찾는다. 이유는 routers/__init__.py에 적었다.
"""

from fastapi import APIRouter, HTTPException


def build_router(app_module) -> APIRouter:
    router = APIRouter()

    @router.get("/api/glossary/{session_id}")
    def read_glossary(session_id: str) -> dict:
        try:
            return app_module.glossary_payload(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/glossary/bind")
    def bind_glossary(req: app_module.GlossaryBindRequest) -> dict:
        try:
            if not app_module.session_file(req.session_id).is_file():
                raise ValueError("프로젝트를 찾을 수 없습니다.")
            store = app_module.glossary_store()
            wid = store.read(req.work_id)["work_id"] if req.work_id else store.create(req.title)
            auto = req.matched_by == "cv"
            app_module.write_json(app_module.glossary_binding_path(req.session_id), {"work_id": wid, "matched_by": "cv" if auto else "user",
                                                               "hits": [str(hit)[:40] for hit in req.hits[:12]] if auto else []})
            return app_module.glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/glossary/unbind")
    def unbind_glossary(req: app_module.GlossaryBindRequest) -> dict:
        try:
            if not app_module.session_file(req.session_id).is_file():
                raise ValueError("프로젝트를 찾을 수 없습니다.")
            # 작품 없이 진행. 'none'을 남겨 CV 자동 연결이 다시 붙지 않게 한다.
            app_module.write_json(app_module.glossary_binding_path(req.session_id), {"work_id": None, "matched_by": "none", "hits": []})
            return app_module.glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def bound_work(session_id: str, work_id: str) -> None:
        data = app_module.session_glossary(session_id)
        if not data or data["work_id"] != work_id:
            raise ValueError("연결된 작품이 바뀌었습니다. 작품 메모를 다시 여세요.")

    @router.post("/api/glossary/save")
    def save_glossary(req: app_module.GlossarySaveRequest) -> dict:
        try:
            bound_work(req.session_id, req.work_id)
            app_module.glossary_store().save(req.work_id, req.model_dump())
            return app_module.glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/namu-lookup")
    def lookup_namu(q: str = "") -> dict:
        from pipeline.work_sources import namu_lookup
        try:
            return namu_lookup(q)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError:
            raise HTTPException(status_code=502, detail="나무위키에 연결하지 못했습니다. 직접 검색해 주소를 붙여 넣으세요.") from None

    @router.post("/api/glossary/draft")
    def draft_glossary(req: app_module.GlossaryDraftRequest) -> dict:
        try:
            bound_work(req.session_id, req.work_id)
            if len(req.text) > 16000:
                raise ValueError("붙여 넣는 자료는 16000자 이내로 입력하세요.")
            app_module.glossary_store().draft(req.work_id, req.url, req.text)
            return app_module.glossary_payload(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            # SDK 예외에는 키/요청 정보가 들어갈 수 있어 원문을 노출하지 않는다.
            raise HTTPException(status_code=400, detail="초안 생성에 실패했습니다. URL 접근이 막히면 본문을 직접 붙여 넣고, OpenAI 키·한도를 확인하세요.") from None

    return router
