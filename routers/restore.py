"""배경 복원·AI 글자 지우기 라우트(/api/restore/*).

처리기는 app.py 전역을 app_module.X로 호출 시점에 찾는다. 이유는 routers/__init__.py에 적었다.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


def build_router(app_module) -> APIRouter:
    router = APIRouter()

    @router.get("/api/restore/status")
    def restore_status() -> dict[str, Any]:
        from pipeline.background_restore import ENGINES, RESOLUTION_CHOICES

        return {
            "engines": list(ENGINES),
            "resolutions": list(RESOLUTION_CHOICES),
        }

    @router.get("/api/restore/info")
    def restore_info_endpoint(session_id: str, image_id: str) -> dict[str, Any]:
        return app_module.restore_info(app_module._restore_record(session_id, image_id))

    @router.post("/api/restore/auto-mask")
    def restore_auto_mask(req: app_module.RestoreAutoMaskRequest) -> dict[str, Any]:
        return app_module.build_restore_auto_mask(req)

    @router.post("/api/restore/mask")
    def restore_save_mask(req: app_module.RestoreMaskRequest) -> dict[str, Any]:
        import cv2

        from pipeline.background_restore import decode_mask_png

        record = app_module._restore_record(req.session_id, req.image_id)
        original = app_module._read_bgr(record["input_path"])
        mask = decode_mask_png(req.mask_png, original.shape[1], original.shape[0])
        Path(record["restore_dir"]).mkdir(parents=True, exist_ok=True)
        cv2.imwrite(record["restore_mask_png"], mask)
        return app_module.restore_info(app_module._restore_record(req.session_id, req.image_id))

    @router.post("/api/restore/ai-erase")
    def restore_ai_erase(req: app_module.AiEraseRequest) -> dict[str, Any]:
        return app_module.submit_ai_erase_job(req)

    @router.post("/api/restore/run")
    def restore_run(req: app_module.RestoreRunRequest) -> dict[str, Any]:
        return app_module.submit_restore_job(req)

    @router.get("/api/restore/job/{job_id}")
    def restore_job(job_id: str) -> dict[str, Any]:
        with app_module._RESTORE_JOBS_LOCK:
            job = app_module._RESTORE_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="복원 작업을 찾을 수 없습니다")
        return app_module._restore_job_public(job)

    @router.post("/api/restore/accept")
    def restore_accept(req: app_module.RestoreAcceptRequest) -> dict[str, Any]:
        return app_module.accept_restore_job(req)

    @router.post("/api/restore/patch/remove")
    def restore_patch_remove(req: app_module.RestorePatchRemoveRequest) -> dict[str, Any]:
        return app_module.remove_restore_patch(req)

    return router
