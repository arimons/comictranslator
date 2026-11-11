"""페이지 처리 정책, 분석(Step 1-3)·수동 보정, 화자·R 번역 메모, OCR 재실행 알림 라우트.

처리기는 app.py 전역을 app_module.X로 호출 시점에 찾는다. 이유는 routers/__init__.py에 적었다.
"""

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse


def build_router(app_module) -> APIRouter:
    router = APIRouter()

    @router.post("/api/page-processing")
    def set_page_processing(req: app_module.PageProcessingRequest) -> dict:
        app_module.get_image(req.session_id, req.image_id)
        path = app_module.session_dir(req.session_id) / "work/page_policy" / f"{req.image_id}.json"
        policy = app_module.load_json(path, default={})
        app_module.write_json(path, {**policy, "excluded": req.excluded, "updated_at": dt.datetime.now().isoformat()})
        return {"image": app_module.page_record(req.session_id, req.image_id)}

    @router.post("/api/page-analysis-route")
    def set_page_analysis_route(req: app_module.PageAnalysisRouteRequest) -> dict:
        app_module.get_image(req.session_id, req.image_id)
        if app_module.page_processing_excluded(req.session_id, req.image_id):
            raise HTTPException(status_code=409, detail="처리 대상으로 복원한 뒤 전환하세요.")
        path = app_module.session_dir(req.session_id) / "work/page_policy" / f"{req.image_id}.json"
        policy = app_module.load_json(path, default={})
        app_module.write_json(path, {**policy, "analysis_route": req.route, "updated_at": dt.datetime.now().isoformat()})
        return {"image": app_module.page_record(req.session_id, req.image_id)}

    @router.get("/api/speakers/{session_id}/{image_id}")
    def read_speakers(session_id: str, image_id: str) -> dict:
        try:
            item = app_module.get_image(session_id, image_id)
            record = {**app_module.image_record(session_id, Path(item["input_path"]), item.get("image_sha256"), item, probe_image=False, include_detail=False), "session_id": session_id}
            stored = app_module.load_json(app_module.speakers_path(session_id, image_id), default={})
            store, _report, _regions = app_module.reconciled_speaker_store(record, stored)
            return {"by_tg": app_module.page_speaker_map(session_id, image_id), "by_region": stored.get("by_region", {}),
                    "context_by_region": stored.get("context_by_region", {}),
                    "by_region_ref": store.get("by_region_ref", {}),
                    "context_by_region_ref": store.get("context_by_region_ref", {})}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _speaker_target_region(req_session: str, req_image: str, region_ref: str | None, region_id: int | None, region_key: str | None):
        """요청이 가리키는 현재 영역. region_ref가 있으면 그것으로, 없으면 구형 region_id + region_key로 찾는다."""
        from pipeline.region_types import region_binding_key
        item = app_module.get_image(req_session, req_image)
        record = {**app_module.image_record(req_session, Path(item["input_path"]), item.get("image_sha256"), item, probe_image=False, include_detail=False), "session_id": req_session}
        stored = app_module.load_json(app_module.speakers_path(req_session, req_image), default={})
        store, _report, regions = app_module.reconciled_speaker_store(record, stored)
        if region_ref:
            region = next((r for r in regions if r.get("region_ref") == region_ref), None)
        elif region_id is not None:
            region = next((r for r in regions if r.get("region_id") == region_id), None)
        else:
            return stored, store, regions, None
        if region is None:
            raise ValueError("현재 페이지에 없는 처리 영역입니다.")
        if not region_ref and region_key is not None and region_key != region_binding_key(region):
            raise ValueError("처리 영역이 변경됐습니다. 다시 선택하세요.")
        return stored, store, regions, region

    @router.post("/api/speakers")
    def assign_speaker(req: app_module.SpeakerRequest) -> dict:
        try:
            app_module.get_image(req.session_id, req.image_id)
            cid = req.character_id
            if cid and cid != app_module.NARRATION and cid not in app_module.character_map(app_module.session_glossary(req.session_id)):
                raise ValueError("작품 메모에 없는 인물입니다.")
            from pipeline.region_types import region_binding_key
            stored, store, regions, region = _speaker_target_region(req.session_id, req.image_id, req.region_ref, req.region_id, req.region_key)
            by_tg, by_region = stored.get("by_tg", {}), dict(stored.get("by_region", {}))
            by_region_ref = dict(store.get("by_region_ref", {}))
            tg_ids = req.tg_ids
            if region is not None:
                # null은 기존 TG 지정으로 되돌아가지 않는 명시적인 R 지정 해제다.
                by_region[region_binding_key(region)] = cid
                by_region_ref[region["region_ref"]] = cid
                tg_ids = []
            else:
                # 기존 TG 전용 클라이언트의 새 지정이 이전 R 직접 지정보다 우선한다.
                for current in regions:
                    if set(current.get("anchor_ids") or []) & set(tg_ids):
                        by_region.pop(region_binding_key(current), None)
                        by_region_ref.pop(current["region_ref"], None)
            for tg in tg_ids:
                if cid:
                    by_tg[str(int(tg))] = cid
                else:
                    by_tg.pop(str(int(tg)), None)
            context_ref = store.get("context_by_region_ref", {})
            app_module.write_json(app_module.speakers_path(req.session_id, req.image_id), {**stored, "by_tg": by_tg, "by_region": by_region,
                                                                    "by_region_ref": by_region_ref, "context_by_region_ref": context_ref,
                                                                    **app_module._speaker_stash(stored, store)})
            return {"by_tg": by_tg, "by_region": by_region, "by_region_ref": by_region_ref,
                    "context_by_region": stored.get("context_by_region", {}), "context_by_region_ref": context_ref}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/ocr-stale/keep")
    def keep_stale_translation(req: app_module.PageRefRequest) -> dict:
        # 영역이 바뀐 뒤에도 기존 번역을 그대로 쓰기로 한 페이지. 번역 시점 서명을 현재 영역으로 갱신해 알림을 끈다.
        try:
            item = app_module.get_image(req.session_id, req.image_id)
            record = app_module.image_record(req.session_id, Path(item["input_path"]), item.get("image_sha256"))
            status = app_module.load_json(Path(record["ocr_status_json"]), default={})
            if status.get("status") == "done":
                status["regions_signature"] = app_module.regions_signature(app_module.load_json(Path(record["regions_json"]), default={}))
                app_module.write_json(Path(record["ocr_status_json"]), status)
            return {"ok": True, "image": app_module.page_record(req.session_id, req.image_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/region-context")
    def save_region_context(req: app_module.RegionContextRequest) -> dict:
        # R별 번역 메모. 저장만 하고 번역은 실행하지 않는다. 다음 OCR/번역 때 프롬프트에 붙는다.
        try:
            app_module.get_image(req.session_id, req.image_id)
            from pipeline.region_types import region_binding_key
            if not req.region_ref and req.region_id is None:
                raise ValueError("메모를 붙일 영역이 없습니다.")
            stored, store, _regions, region = _speaker_target_region(req.session_id, req.image_id, req.region_ref, req.region_id, req.region_key)
            key = region_binding_key(region)
            text = req.text.strip()
            if len(text) > app_module.REGION_CONTEXT_LIMIT:
                raise ValueError(f"번역 메모는 {app_module.REGION_CONTEXT_LIMIT}자 이내로 적어 주세요.")
            contexts = dict(stored.get("context_by_region", {}))
            contexts_ref = dict(store.get("context_by_region_ref", {}))
            if text:
                contexts[key] = text
                contexts_ref[region["region_ref"]] = text
            else:
                contexts.pop(key, None)
                contexts_ref.pop(region["region_ref"], None)
            app_module.write_json(app_module.speakers_path(req.session_id, req.image_id), {**stored, "context_by_region": contexts,
                                                                    "by_region_ref": store.get("by_region_ref", {}), "context_by_region_ref": contexts_ref,
                                                                    **app_module._speaker_stash(stored, store)})
            return {"context_by_region": contexts, "context_by_region_ref": contexts_ref}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/analyze")
    def analyze(req: app_module.AnalyzeRequest) -> dict[str, Any]:
        data = app_module.load_session(req.session_id)
        targets = app_module.sorted_image_records(data.get("images", [])) if req.image_id == "all" else [app_module.get_image(req.session_id, req.image_id)]
        logs: list[str] = []
        for item in targets:
            if app_module.page_processing_excluded(req.session_id, item["id"]):
                logs.append(f"[{item['id']}] 페이지 처리 제외 · 원본 출력")
                continue
            image_hash = app_module.ensure_record_hash(item)
            record = app_module.image_record(req.session_id, Path(item["input_path"]), image_hash)
            if app_module.hydrate_analysis_cache(record, image_hash, req.force, logs.append, allow_backfill=True):
                app_module.reapply_guidance_if_present(req.session_id, record, logs.append)
                continue
            cv_ready = app_module.hydrate_cv_cache(record, image_hash, req.force, logs.append, item)
            app_module._unlink_symlinked_analysis_outputs(record)
            stop_after = req.stop_after if req.stop_after in {"pre_ocr", "text_groups"} else "pre_ocr"
            ok, log = app_module.run_single_image(
                record["input_path"],
                record["work_dir"],
                record["final_jpg"],
                force=req.force,
                _skip_step1=cv_ready,
                stop_after=stop_after,
            )
            logs.append(log)
            if not ok:
                raise HTTPException(status_code=500, detail=log)
            app_module.store_cv_cache(record, image_hash)
            if stop_after == "pre_ocr":
                # Step 3 산출물이 없는 부분 분석은 분석 캐시에 넣지 않는다(다음 분석이 빈 결과를 재사용하지 않게).
                app_module.store_analysis_cache(record, image_hash)
                app_module.clear_guidance_cleared_marker(record)
                app_module.reapply_guidance_if_present(req.session_id, record, logs.append)
        app_module.save_session(data)
        images = app_module.session_image_summaries(req.session_id)
        return {"ok": True, "logs": "\n\n".join(logs), "images": images}

    @router.post("/api/analyze-stream")
    def analyze_stream(req: app_module.AnalyzeRequest) -> StreamingResponse:
        return StreamingResponse(app_module.stream_analyze(req), media_type="application/x-ndjson")

    @router.post("/api/guidance")
    def apply_guidance(req: app_module.GuidanceRequest) -> dict[str, Any]:
        refreshed = app_module.run_step3_with_effective_text_groups(req.session_id, req.image_id, req.guidance)
        source = app_module.get_image(req.session_id, req.image_id)
        image_hash = source.get("image_sha256") or app_module.ensure_record_hash(source)
        app_module.store_guidance_cache(refreshed, image_hash)
        app_module.store_guidance_output_cache(refreshed, image_hash, req.guidance)
        refreshed = app_module.rerender_final_after_analysis(req.session_id, req.image_id, source) or refreshed
        return {"ok": True, "image": refreshed}

    @router.post("/api/guidance/clear")
    def clear_guidance(req: app_module.ClearGuidanceRequest) -> dict[str, Any]:
        refreshed = app_module.clear_guidance_outputs(req.session_id, req.image_id)
        return {"ok": True, "image": refreshed}

    return router
