"""최종 렌더(OCR·번역 실행, 오버레이 적용), 내보내기, 이미지 생성 결과, 작업 파일 제공 라우트.

처리기는 app.py 전역을 app_module.X로 호출 시점에 찾는다. 이유는 routers/__init__.py에 적었다.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse


def build_router(app_module) -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-generation/{session_id}/{image_id}")
    def read_image_generation(session_id: str, image_id: str) -> dict:
        try:
            result = app_module.ImageGenerationStore(app_module.ROOT).status(session_id, image_id)
            for item in result["results"]:
                item["preview_url"] = app_module.file_url(item["preview_path"])
            return result
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/run-final")
    def run_final(req: app_module.RunFinalRequest) -> dict[str, Any]:
        if app_module.page_processing_excluded(req.session_id, req.image_id):
            return {"ok": True, "image": app_module.page_record(req.session_id, req.image_id)}
        try:
            context = app_module.session_glossary_snapshot(req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        source = app_module.get_image(req.session_id, req.image_id)
        try:
            refreshed = app_module.run_final_for_item(
                req.session_id,
                source,
                req.extra_prompt,
                req.font_path,
                req.tracking_offset,
                req.overlay_guidance,
                req.force,
                ocr_model=req.ocr_model,
                replace_overlay=req.replace,
                glossary_context=context,
            )
        except Exception as exc:
            record = app_module.image_record(req.session_id, Path(source["input_path"]), source.get("image_sha256"))
            app_module.mark_ocr_status(record, "failed", "실패", str(exc))
            raise
        return {"ok": True, "image": refreshed}

    @router.post("/api/run-final-stream")
    def run_final_stream(req: app_module.RunFinalRequest) -> StreamingResponse:
        return StreamingResponse(app_module.stream_run_final(req), media_type="application/x-ndjson")

    @router.post("/api/overlay/apply")
    def apply_overlay(req: app_module.OverlayRequest) -> dict[str, Any]:
        if app_module.page_processing_excluded(req.session_id, req.image_id):
            raise HTTPException(status_code=409, detail="처리 제외 페이지입니다. 처리 대상으로 복원한 뒤 편집하세요.")
        source = app_module.get_image(req.session_id, req.image_id)
        # 렌더에는 경로만 필요하다. 원본 전체를 여는 여백 probe는 하지 않고 manifest 크기 정보를 쓴다.
        record = app_module.image_record(req.session_id, Path(source["input_path"]), source.get("image_sha256"), source, probe_image=False, include_detail=False)
        app_module.store_overlay_guidance(record, req.overlay_guidance, req.replace)
        app_module.render_overlay_for_record(record, req.font_path, req.tracking_offset)
        return {"ok": True, "image": app_module.page_record(req.session_id, req.image_id)}

    @router.get("/api/session/{session_id}/export")
    def export_session(session_id: str) -> FileResponse:
        zip_path = app_module.build_session_export_zip(session_id)
        return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)

    @router.post("/api/export/generate-missing")
    def generate_missing_final_images(req: app_module.GenerateMissingFinalRequest) -> dict[str, Any]:
        return app_module.generate_missing_finals_with_openai(req)

    @router.post("/api/export/generate-missing/stream")
    def generate_missing_final_images_stream(req: app_module.GenerateMissingFinalRequest) -> StreamingResponse:
        return StreamingResponse(app_module.stream_generate_missing_finals(req), media_type="application/x-ndjson")

    @router.get("/api/file")
    def get_file(path: str) -> FileResponse:
        # 세션·캐시 폴더 아래의 이미지만 내려준다. 저장소 루트 전체를 열면 .env나 인증서 파일까지 읽힌다.
        resolved = Path(path).resolve()
        servable_roots = (app_module.SESSION_ROOT, app_module.INPUT_CACHE_DIR, app_module.ANALYSIS_CACHE_DIR)
        if resolved.suffix.lower() not in app_module.IMAGE_EXTS or not (any(
            resolved.is_relative_to(root.resolve()) for root in servable_roots
        ) or app_module.source_library().allowed_source(resolved, app_module.SESSION_ROOT)):
            raise HTTPException(status_code=403, detail="File outside workspace is not served")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(resolved)

    return router
