"""프로젝트(세션) 목록·생성·삭제·메타, 페이지 나누기, 원본 연결, 폴더 탐색, 이미지 가져오기·올리기 라우트.

처리기는 app.py 전역을 app_module.X로 호출 시점에 찾는다. 이유는 routers/__init__.py에 적었다.
"""

import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile


def build_router(app_module) -> APIRouter:
    router = APIRouter()

    @router.post("/api/sessions/new")
    def create_session() -> dict[str, Any]:
        data = app_module.ensure_session()
        return {"session": data}

    @router.get("/api/sessions/recent")
    def recent_sessions(limit: int = 12, offset: int = 0, sort: str = "recent") -> dict[str, Any]:
        # 홈은 먼저 limit개만 받고, 스크롤하면 offset을 늘려 이어 받는다. total은 합쳐진 뒤 전체 프로젝트 수.
        limit, offset = max(1, min(limit, 200)), max(0, offset)
        sessions, total = app_module.recent_sessions_page(limit, offset, "name" if sort == "name" else "recent")
        return {"sessions": sessions, "total": total, "offset": offset, "has_more": offset + len(sessions) < total}

    def preparation_source(session_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id) or not app_module.session_file(session_id).is_file():
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
        data = app_module.load_json(app_module.session_file(session_id), default={})
        if not data.get("images"):
            raise HTTPException(status_code=400, detail="불러온 이미지가 없습니다.")
        return data

    @router.post("/api/pages/preview")
    def preview_pages(req: app_module.PagePreparationRequest) -> dict[str, Any]:
        source = preparation_source(req.session_id)
        try:
            plan = app_module.build_plan([item for item in app_module.sorted_image_records(source["images"]) if not app_module.is_split_page(item)], numbered=False)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        by_id = {item["id"]: item for item in source["images"]}
        for page in plan["pages"]:
            page["source_url"] = app_module.file_url(page["source_path"])
            page["has_work"] = page["mode"] != "keep" and app_module.page_has_work(req.session_id, by_id[page["image_id"]])
        return plan

    @router.post("/api/pages/override")
    def override_page_split(req: app_module.PageSplitOverrideRequest) -> dict[str, Any]:
        """페이지별 나누기 방식을 프로젝트에 저장한다. 미리보기와 분석 시작 때의 자동 나누기 모두 이 값을 따른다."""
        preparation_source(req.session_id)
        if req.mode not in {"auto", "keep", "split"} or (req.mode == "split" and req.split_x is None):
            raise HTTPException(status_code=400, detail="나누기 방식이 올바르지 않습니다.")
        data = app_module.ensure_session(req.session_id)
        item = next((image for image in data.get("images", []) if image["id"] == req.image_id), None)
        if item is None or app_module.is_split_page(item):
            raise HTTPException(status_code=404, detail="나눌 수 있는 원본 페이지가 아닙니다.")
        if req.mode == "auto":
            item.pop("split_override", None)
        else:
            item["split_override"] = {"mode": "keep"} if req.mode == "keep" else {"mode": "split", "split_x": int(req.split_x)}
        app_module.save_session(data)
        try:
            page = app_module.build_plan([item], numbered=False)["pages"][0]
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        page["source_url"] = app_module.file_url(page["source_path"])
        page["has_work"] = page["mode"] != "keep" and app_module.page_has_work(req.session_id, item)
        return {"page": page}

    @router.post("/api/pages/split")
    def split_pages(req: app_module.PageSplitRequest) -> dict[str, Any]:
        preparation_source(req.session_id)
        try:
            return app_module.split_wide_pages_in_session(req.session_id, include_worked=req.include_worked)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"같은 이름의 파일이 이미 있어 나눌 수 없습니다: {Path(str(exc)).name}") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/session/{session_id}")
    def read_session(session_id: str) -> dict[str, Any]:
        # 목록용 요약만 돌려준다. 페이지 상세(regions, integrated 등)는 /page/{image_id}로 받는다.
        data = app_module.ensure_session(session_id)
        images = app_module.session_image_summaries(session_id)
        return {"session": data, "images": images, "hydration_pending": app_module.load_hydration_pending(session_id)}

    @router.get("/api/session/{session_id}/page/{image_id}")
    def read_session_page(session_id: str, image_id: str) -> dict[str, Any]:
        return {"image": app_module.page_record(session_id, image_id)}

    @router.post("/api/session/meta")
    def update_session_meta(req: app_module.SessionMetaRequest) -> dict[str, Any]:
        data = app_module.ensure_session(req.session_id)
        data["project_name"] = req.project_name.strip()[:120]
        app_module.save_session(data)
        return {"session": data}

    @router.post("/api/session/ocr-model")
    def update_session_ocr_model(req: app_module.SessionOcrModelRequest) -> dict[str, Any]:
        from pipeline.gemini_ocr_translate_v5 import SUPPORTED_OCR_MODELS

        value = (req.ocr_model or "").strip()
        if value and value not in {item["id"] for item in SUPPORTED_OCR_MODELS}:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 OCR·번역 모델입니다: {value}")
        data = app_module.ensure_session(req.session_id)
        if value:
            data["ocr_model"] = value
        else:
            data.pop("ocr_model", None)
        app_module.save_session(data)
        return {"session": data}

    @router.post("/api/session/delete")
    def delete_session_endpoint(req: app_module.SessionDeleteRequest) -> dict[str, Any]:
        app_module.delete_session(req.session_id)
        return {"ok": True, "sessions": app_module.list_recent_sessions()}

    @router.get("/api/sources/{session_id}")
    def input_sources(session_id: str):
        if not app_module.session_file(session_id).is_file():raise HTTPException(status_code=404,detail="프로젝트가 없습니다")
        data=app_module.ensure_session(session_id)
        pages=data.get("images",[])
        return {"session_id":session_id,"total":len(pages),
            "connected":sum(p.get("source_status")=="connected" for p in pages),
            "fallback":sum(p.get("source_status")=="fallback" for p in pages),
            "missing":sum(p.get("source_status")=="missing" for p in pages),
            "owned":sum(not p.get("source_ref") for p in pages)}

    @router.post("/api/sources/preview")
    def preview_input_sources(req: app_module.SourceFolderRequest):
        req=req.model_dump()
        if not app_module.session_file(req["session_id"]).is_file():raise HTTPException(status_code=404,detail="프로젝트가 없습니다")
        try:
            return app_module.source_library().preview(app_module.ensure_session(req["session_id"]), req["folder"])
        except (OSError,ValueError,KeyError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.post("/api/sources/relink")
    def relink_input_sources(req: app_module.SourceFolderRequest):
        req=req.model_dump()
        if not app_module.session_file(req["session_id"]).is_file():raise HTTPException(status_code=404,detail="프로젝트가 없습니다")
        try:
            data=app_module.ensure_session(req["session_id"])
            plan=app_module.source_library().preview(data, req["folder"])
            if not req.get("revision") or req["revision"]!=plan["revision"]:
                raise HTTPException(status_code=409,detail="원본 또는 프로젝트가 변경됐습니다. 다시 확인하세요")
            result=app_module.source_library().apply(data,app_module.session_dir(req["session_id"]),plan)
            return {"matched":result["matched"],"unmatched":result["unmatched"],"status":input_sources(req["session_id"])}
        except (OSError,ValueError,KeyError) as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc

    @router.get("/api/fs/list")
    def list_server_folder(path: str = "") -> dict[str, Any]:
        return app_module.server_folder_listing(path)

    @router.post("/api/import-path")
    def import_path(req: app_module.ImportPathRequest) -> dict[str, Any]:
        src = Path(req.path).expanduser()
        if not app_module.server_path_allowed(src):
            raise HTTPException(status_code=403, detail=app_module.BROWSE_FORBIDDEN_DETAIL)
        data = app_module.ensure_session(req.session_id)
        # 폴더 안의 링크가 허용 범위 밖 파일을 가리키면 그 파일은 가져오지 않는다
        paths = [path for path in app_module.scan_images(src, req.recursive) if app_module.server_path_allowed(path)]
        records = app_module.copy_images_into_session(paths, data["session_id"])
        if records:
            data = app_module.ensure_session(data["session_id"])
            if src.is_dir():
                data["source_folder_name"] = src.name
            if not data.get("project_name"):
                data["project_name"] = (src.name if src.is_dir() else src.stem)[:120]
            app_module.save_session(data)
        return {"session_id": data["session_id"], "imported": len(records), "images": records}

    @router.post("/api/upload")
    def upload_files(session_id: str | None = None, files: list[UploadFile] = File(...), source_folder_name: str = "") -> dict[str, Any]:
        # 동기 함수로 둬서 해시·이미지 처리 동안 이벤트 루프를 막지 않고 threadpool에서 실행되게 한다.
        data = app_module.ensure_session(session_id)
        input_dir = app_module.session_dir(data["session_id"]) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        taken_ids = {item["id"] for item in data.get("images", [])}
        copied: list[Path] = []
        sorted_uploads = sorted(files, key=lambda upload: app_module.natural_name_key(upload.filename or ""))
        for index, upload in enumerate(sorted_uploads, start=1):
            name = app_module.safe_filename(upload.filename or f"upload_{index}.jpg")
            if Path(name).suffix.lower() not in app_module.IMAGE_EXTS:
                continue
            target = app_module.allocate_input_path(input_dir, name, index, taken_ids)
            # "xb": 기존 파일이나 symlink를 따라가 덮어쓰지 않는다.
            with target.open("xb") as f:
                shutil.copyfileobj(upload.file, f)
            copied.append(target)
        records = []
        current = app_module.ensure_session(data["session_id"])
        if copied:
            folder_name = source_folder_name.strip()[:120]
            if folder_name:
                current["source_folder_name"] = folder_name
            if not current.get("project_name"):
                current["project_name"] = folder_name or copied[0].stem[:120]
        known = {item["id"] for item in current.get("images", [])}
        for path in copied:
            if path.stem in known:
                continue
            image_hash = app_module.file_sha256(path)
            item = app_module.image_manifest_item(path, image_hash, {"input_storage":"upload"})
            current.setdefault("images", []).append(item)
            records.append(item)
        app_module.save_session(current)
        app_module.hydrate_load_cache_for_records(data["session_id"], records, sync_limit=app_module.IMPORT_CACHE_SYNC_LIMIT)
        return {"session_id": data["session_id"], "imported": len(records), "images": app_module.sorted_image_records(records)}

    @router.post("/api/image/remove")
    def remove_image(req: app_module.RemoveImageRequest) -> dict[str, Any]:
        images = app_module.remove_session_image(req.session_id, req.image_id)
        return {"ok": True, "session": app_module.ensure_session(req.session_id), "images": images, "sessions": app_module.list_recent_sessions()}

    return router
