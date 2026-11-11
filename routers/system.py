"""서버 상태·재시작, LaMa 작업 프로세스, OCR 모델·글꼴 목록, 사용자 매뉴얼 라우트.

처리기는 app.py 전역을 app_module.X로 호출 시점에 찾는다. 이유는 routers/__init__.py에 적었다.
"""

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def build_router(app_module) -> APIRouter:
    router = APIRouter()

    @router.post("/api/server/restart")
    def restart_server(force: bool = False) -> dict[str, Any]:
        busy = app_module.busy_job_count()
        if busy and not force:
            raise HTTPException(status_code=409, detail=f"진행 중인 작업 {busy}개가 있습니다. 재시작하면 중단됩니다.")
        threading.Thread(target=app_module.restart_current_process, daemon=True, name="server-restart").start()
        return {"ok": True, "interrupted": busy}

    @router.post("/api/lama/release")
    def lama_release() -> dict[str, Any]:
        # 프로젝트 홈으로 나갈 때 화면이 부른다. 프로젝트 안에서는 모델을 살려 둔다.
        return app_module.release_lama_worker("프로젝트 홈으로 이동")

    @router.get("/api/lama/status")
    def lama_status() -> dict[str, Any]:
        from pipeline.lama_worker import shared_status

        return shared_status()

    @router.get("/api/health")
    def server_health() -> dict[str, Any]:
        # stale_code: 서버 시작 뒤 바뀐 Python 파일(재시작 필요). bundle: 화면 번들 버전(바뀌면 새로고침 필요).
        return {"status": "ok", "stale_code": app_module.stale_code_files(), "bundle": app_module.bundle_version()}

    @router.get("/api/ocr-models")
    def list_ocr_models() -> dict[str, Any]:
        """Step 4에서 선택 가능한 OCR/번역 모델 목록."""
        from pipeline.gemini_image import GEMINI_IMAGE_MODEL
        from pipeline.gemini_ocr_translate_v5 import MODEL_NAME, SUPPORTED_OCR_MODELS

        # 이미지 작업(글자 지우기·통번역 재생성)은 OCR·번역 모델의 제공자를 따라간다
        # 경로별 키가 .env에 있는지만 알려 준다(값은 보내지 않는다). 서버 재시작 때 읽은 값 기준이다.
        key_ready = {name: bool(os.getenv(env)) for name, env in
                     (("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("vertex", "VERTEX_API_KEY"))}
        return {"default": MODEL_NAME, "models": SUPPORTED_OCR_MODELS, "key_ready": key_ready,
                "image_models": {"openai": app_module.IMAGE_MODEL, "gemini": GEMINI_IMAGE_MODEL}}

    @router.get("/api/fonts")
    def list_fonts() -> dict[str, Any]:
        fonts_dir = app_module.ROOT / "Fonts"
        fonts = sorted([*fonts_dir.glob("*.ttf"), *fonts_dir.glob("*.otf")]) if fonts_dir.exists() else []
        # 편집기가 렌더와 같은 글꼴로 제자리 편집하도록 스타일별 실제 폰트 파일 이름도 알려 준다(/fonts/<이름>).
        from pipeline.render_text_overlay import font_path_for_style

        default_path = Path(app_module.DEFAULT_FONT)
        style_ids = ("normal", "bold", "impact", "light", "memory_serif", "serif_bold", "handwritten", "serif_emphasis")
        return {
            "default": app_module.DEFAULT_FONT,
            "fonts": [{"name": path.stem, "path": str(path)} for path in fonts],
            "styles": {style: font_path_for_style(style, default_path).name for style in style_ids},
        }

    @router.get("/manual")
    def editor_manual() -> FileResponse:
        # 사용자 매뉴얼은 docs/editor_manual.html 하나를 정본으로 두고 그대로 보여 준다.
        return FileResponse(app_module.ROOT / "docs" / "editor_manual.html", media_type="text/html")

    return router
