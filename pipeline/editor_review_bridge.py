"""UI와 독립적인 로컬 검토 데이터 어댑터. 파이프라인 실행·기존 파일 수정은 하지 않는다."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from pipeline.source_library import SourceLibrary, digest as source_digest
from pipeline.paths import for_repo
from pipeline.project_paths import manifest_path, manifest_files, runtime_json, transform_paths
from pipeline.region_types import regions_with_processing_basis

Artifact = Literal["original", "masked", "final", "analysis", "ocr_sheet", "region_mask"]
JSON_SUFFIXES = {
    "text_groups": "text_groups", "effective_text_groups": "text_groups_effective",
    "regions": "regions", "integrated": "integrated", "guidance": "manual_guidance",
    "overlay_guidance": "overlay_guidance", "overlay_layout": "overlay_layout",
}
MAX_FILE_BYTES = 64 * 1024 * 1024


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region_id: int | None = Field(default=None, ge=1)
    artifact: Artifact = "original"
    description: str = Field(min_length=1, max_length=4000)
    interpretation: str = Field(default="", max_length=4000)


class CandidateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["tg_off", "tg_split", "region_split", "boundary_seed", "edge", "text_box",
                    "region_off", "edit_text", "adjust_layout", "restore_background", "review_only"]
    region_id: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=4000)
    # 아직 실행하지 않는 초안이다. 원본 픽셀 좌표 등 향후 편집에 필요한 값만 담는다.
    parameters: dict[str, Any] = Field(default_factory=dict)


class ReviewStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.paths = for_repo(self.root)
        self.sessions = self.paths.projects
        self.allowed = tuple(path.resolve() for path in (
            self.sessions, self.paths.input_cache, self.paths.analysis_cache, self.paths.cv_cache))
        self.session_anchor = self.sessions.resolve()
        if any(not path.is_relative_to(self.paths.home) for path in self.allowed):
            raise ValueError("세션·캐시 루트가 데이터 홈 밖을 가리킵니다")

    def _safe(self, path: Path, *, proposals=False) -> Path:
        resolved = path.resolve()
        roots = (self.session_anchor,) if proposals else self.allowed
        if not any(resolved.is_relative_to(root) for root in roots) and (proposals or not SourceLibrary(self.paths.home).allowed_source(resolved, self.sessions)):
            raise ValueError("허용된 세션·캐시 경로 밖의 파일입니다")
        return resolved

    def _read(self, path: Path) -> bytes | None:
        resolved = self._safe(path)
        try:
            with resolved.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"파일 크기 제한 초과: {path.name}")
        return data

    def _json(self, path: Path) -> dict:
        raw = self._read(path)
        if raw is None:
            raise ValueError("존재하지 않는 프로젝트입니다")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("세션 manifest 형식이 올바르지 않습니다")
        return runtime_json(path, value)

    def _session(self, session_id: str) -> tuple[Path, dict]:
        if not re.fullmatch(r"[\w-]{1,160}", session_id):
            raise ValueError("올바르지 않은 session_id입니다")
        base = self.sessions / session_id
        # 프로젝트 폴더 자체의 외부 symlink도 거부한다.
        self._safe(base, proposals=True)
        return base, self._json(manifest_path(base))

    def list_projects(self, offset=0, limit=30) -> dict:
        self._pagination(offset, limit)
        entries, skipped = [], []
        for path in manifest_files(self.sessions):
            try:
                _, manifest = self._session(path.parent.name)
                pages = manifest.get("images", [])
                entries.append((path.stat().st_mtime_ns, {
                    "session_id": path.parent.name, "name": manifest.get("project_name", ""),
                    "page_count": len(pages), "first_image_id": pages[0].get("id") if pages else None,
                }))
            except (ValueError, OSError):
                skipped.append(path.parent.name)
        entries.sort(key=lambda item: (-item[0], item[1]["session_id"]))
        return {"projects": [v for _, v in entries[offset:offset+limit]], "total": len(entries),
                "next_offset": offset+limit if offset+limit < len(entries) else None,
                "skipped_project_ids": skipped}

    @staticmethod
    def _pagination(offset, limit):
        if not 0 <= offset or not 1 <= limit <= 200:
            raise ValueError("offset은 0 이상, limit은 1~200이어야 합니다")

    def _page_paths(self, session_id: str, image_id: str) -> tuple[Path, dict, dict[str, Path]]:
        base, manifest = self._session(session_id)
        if (not image_id or image_id in {".", ".."} or len(image_id) > 240
                or any(c in image_id for c in "/\\\x00") or any(ord(c) < 32 for c in image_id)):
            raise ValueError("올바르지 않은 image_id입니다")
        page = next((p for p in manifest.get("images", []) if p.get("id") == image_id), None)
        if page is None:
            raise ValueError("프로젝트에 등록되지 않은 페이지입니다")
        original = Path(page["input_path"])
        if page.get("source_ref"):
            candidate=SourceLibrary(self.paths.home).target(page["source_ref"])
            try:
                if candidate and candidate.is_file() and source_digest(candidate)==page.get("image_sha256"):
                    original=candidate
                elif page.get("source_fallback_path") and Path(page["source_fallback_path"]).is_file() and source_digest(Path(page["source_fallback_path"]))==page.get("image_sha256"):
                    original=Path(page["source_fallback_path"])
                else:raise ValueError("원본 폴더를 다시 연결하세요")
            except OSError as exc:raise ValueError("원본 폴더를 다시 연결하세요") from exc
        if not original.is_absolute():
            original = base / original
        if original.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
            raise ValueError("원본 이미지 확장자가 올바르지 않습니다")
        paths = {key: base / "work/json" / f"{image_id}_{suffix}.json" for key, suffix in JSON_SUFFIXES.items()}
        paths.update({
            "restore_state": base / "work/restore" / image_id / "state.json",
            "page_policy": base / "work/page_policy" / f"{image_id}.json",
            "original": original,
            "masked": base / "work/masked" / f"{image_id}_masked.jpg",
            "final": base / "output/final" / f"{image_id}_final.jpg",
            "analysis": base / "work/vis" / f"{image_id}_bubble_vis.jpg",
            "ocr_sheet": base / "work/sheets" / f"{image_id}_indexed_sheet.jpg",
        })
        for path in paths.values():
            self._safe(path)
        return base, page, paths

    def _snapshot(self, session_id, image_id):
        base, page, paths = self._page_paths(session_id, image_id)
        base_paths = dict(paths)

        def signature():
            result = {}
            for key, path in paths.items():
                resolved = self._safe(path)
                try:
                    stat = resolved.stat()
                    result[key] = (str(resolved), stat.st_ino, stat.st_size, stat.st_mtime_ns)
                except FileNotFoundError:
                    result[key] = None
            return result

        for _ in range(3):
            paths = dict(base_paths)
            raw_regions = self._read(paths["regions"])
            try:
                discovered = runtime_json(paths["regions"], json.loads(raw_regions)) if raw_regions is not None else None
            except (ValueError, UnicodeError):
                discovered = None
            for region in self._regions(discovered):
                if region.get("mask_ref"):
                    mask = Path(region["mask_ref"])
                    if mask.suffix.lower() != ".png":
                        raise ValueError("경계 마스크는 PNG만 지원합니다")
                    paths[f"region_mask:{region['region_id']}"] = mask if mask.is_absolute() else base / mask
            before = signature()
            blobs = {key: self._read(path) for key, path in paths.items()}
            if before == signature() and blobs["regions"] == raw_regions:
                break
        else:
            raise ValueError("ページ更新中です。少し待って再取得してください")
        if blobs["original"] is None:
            raise ValueError("원본 이미지가 없습니다")
        with Image.open(io.BytesIO(blobs["original"])) as image:
            dimensions = list(image.size)
        hashes = {key: hashlib.sha256(blob).hexdigest() if blob is not None else None for key, blob in blobs.items()}
        revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        payload, issues = {}, []
        for key in [*JSON_SUFFIXES, "restore_state", "page_policy"]:
            raw = blobs[key]
            if raw is None:
                payload[key] = None
                continue
            try:
                payload[key] = runtime_json(paths[key], json.loads(raw))
            except (ValueError, UnicodeError):
                payload[key] = None
                issues.append({"kind": "invalid_json", "artifact": key})
        payload["regions"] = regions_with_processing_basis(payload["regions"])
        return base, page, paths, blobs, payload, issues, dimensions, revision, hashes

    @staticmethod
    def _regions(payload):
        values = payload.get("regions", []) if isinstance(payload, dict) else []
        return [r for r in values if isinstance(r, dict) and isinstance(r.get("region_id"), int)] if isinstance(values, list) else []

    def inspect_page(self, session_id: str, image_id: str | None = None, offset=0, limit=100) -> dict:
        if image_id is None:
            self._pagination(offset, limit)
            _, manifest = self._session(session_id)
            pages = manifest.get("images", [])
            return {"session_id": session_id, "pages": [
                {"image_id": p["id"], "name": p.get("name", p["id"])} for p in pages[offset:offset+limit]],
                "total": len(pages), "next_offset": offset+limit if offset+limit < len(pages) else None}
        base, page, paths, blobs, payload, issues, dimensions, revision, hashes = self._snapshot(session_id, image_id)
        proposals = []
        directory = self._safe(base / "work/reviews" / image_id, proposals=True)
        for path in sorted(directory.glob("*.json"))[:100]:
            try:
                proposal = self._json(path)
                proposals.append({"proposal_id": proposal["proposal_id"], "stale": proposal["base_revision"] != revision,
                                  "quality_verdict": proposal["quality_verdict"]})
            except (ValueError, KeyError, OSError):
                issues.append({"kind": "invalid_proposal", "name": path.name})
        return {"schema_version": 1, "session_id": session_id, "image_id": image_id,
                "page_revision": revision, "original_sha256": hashes["original"], "original_size": dimensions,
                "coordinate_space": "original_pixels", "payload": payload, "issues": issues,
                "artifacts": {key: {"available": blobs[key] is not None, "sha256": hashes[key]}
                              for key in ["original", "masked", "final", "analysis", "ocr_sheet"]},
                "processing_excluded": (payload["page_policy"] or {}).get("excluded") is True,
                "review_proposals": proposals, "quality_verdict": "unreviewed"}

    def view_region(self, session_id: str, image_id: str, region_id: int | None = None,
                    artifact: Artifact = "original", margin=24, max_edge=1600,
                    expected_revision: str | None = None) -> tuple[dict, bytes]:
        if not 0 <= margin <= 512 or not 128 <= max_edge <= 4096:
            raise ValueError("margin은 0~512, max_edge는 128~4096이어야 합니다")
        _, _, paths, blobs, payload, _, original_size, revision, _ = self._snapshot(session_id, image_id)
        if expected_revision is not None and expected_revision != revision:
            raise ValueError("페이지 revision이 바뀌었습니다. inspect_page로 다시 확인하세요")
        regions = self._regions(payload["regions"])
        region = next((r for r in regions if r.get("region_id") == region_id), None) if region_id is not None else None
        if region_id is not None and region is None:
            raise ValueError("존재하지 않는 region_id입니다")
        if artifact == "region_mask":
            if region is None or not region.get("mask_ref"):
                raise ValueError("이 영역에는 저장된 경계 마스크가 없습니다")
            path = Path(region["mask_ref"])
            if path.suffix.lower() != ".png":
                raise ValueError("경계 마스크는 PNG만 지원합니다")
            data = blobs.get(f"region_mask:{region['region_id']}")
        else:
            if artifact not in {"original", "masked", "final", "analysis", "ocr_sheet"}:
                raise ValueError("지원하지 않는 artifact입니다")
            data = blobs[artifact]
        if data is None:
            raise ValueError("아직 생성되지 않은 이미지입니다")
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            size = list(image.size)
            sheet = artifact == "ocr_sheet"
            if not sheet and size != original_size:
                raise ValueError("원본과 이미지 크기가 달라 좌표를 안전하게 대응할 수 없습니다")
            rect = None
            if region is not None:
                rect = (region.get("indexed_sheet_rect") if sheet else
                        region.get("bubble_rect") or region.get("unboxed_rect") or region.get("source_rect") or region.get("erase_rect"))
                if not isinstance(rect, list) or len(rect) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in rect):
                    raise ValueError("영역의 crop 좌표가 올바르지 않습니다")
                x, y, w, h = rect
                if w <= 0 or h <= 0:
                    raise ValueError("영역의 크기는 양수여야 합니다")
                box = [max(0, math.floor(x-margin)), max(0, math.floor(y-margin)),
                       min(size[0], math.ceil(x+w+margin)), min(size[1], math.ceil(y+h+margin))]
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise ValueError("영역이 이미지 밖에 있습니다")
            else:
                box = [0, 0, *size]
            crop = image.crop(box).convert("RGB")
            full_size = list(crop.size)
            crop.thumbnail((max_edge, max_edge))
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            meta = {"session_id": session_id, "image_id": image_id, "region_id": region_id,
                    "artifact": artifact, "page_revision": revision, "coordinate_space": "indexed_sheet_pixels" if sheet else "original_pixels",
                    "crop_rect": [box[0], box[1], box[2]-box[0], box[3]-box[1]],
                    "source_size": size, "returned_size": list(crop.size),
                    "scale": [crop.width/full_size[0], crop.height/full_size[1]], "region": region,
                    "quality_verdict": "unreviewed"}
        return meta, buffer.getvalue()

    def propose_review(self, session_id: str, image_id: str, base_revision: str,
                       observations: list[Observation], candidate_actions: list[CandidateAction]) -> dict:
        if not observations or len(observations) > 100 or len(candidate_actions) > 100:
            raise ValueError("관찰은 1~100개, 후보 동작은 100개 이하여야 합니다")
        base, _, _, _, payload, _, _, revision, _ = self._snapshot(session_id, image_id)
        if base_revision != revision:
            raise ValueError("페이지 revision이 바뀌었습니다. inspect_page로 다시 확인하세요")
        ids = {r["region_id"] for r in self._regions(payload["regions"])}
        for item in [*observations, *candidate_actions]:
            if item.region_id is not None and item.region_id not in ids:
                raise ValueError("제안에 존재하지 않는 region_id가 있습니다")
        content = {"schema_version": 1, "session_id": session_id, "image_id": image_id,
                   "base_revision": base_revision, "coordinate_space": "original_pixels",
                   "observations": [o.model_dump() for o in observations],
                   "candidate_actions": [a.model_dump() for a in candidate_actions],
                   "needs_human_check": True, "quality_verdict": "unreviewed", "status": "proposed"}
        canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        if len(canonical) > 128 * 1024:
            raise ValueError("제안 내용이 128KiB를 초과했습니다")
        proposal_id = hashlib.sha256(canonical).hexdigest()[:32]
        directory = base / "work/reviews" / image_id
        self._safe(directory, proposals=True).mkdir(parents=True, exist_ok=True)
        target = directory / f"{proposal_id}.json"
        self._safe(target, proposals=True)
        content.update(proposal_id=proposal_id, created_at=datetime.now(timezone.utc).isoformat())
        # 완성된 파일을 독점 link로 게시해 중간 JSON 노출과 같은 제안 덮어쓰기를 막는다.
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
                temp_path = Path(stream.name)
                json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp_path, target)
            except FileExistsError:
                existing = self._json(target)
                comparable = {k: v for k, v in existing.items() if k not in {"created_at", "proposal_id"}}
                if json.dumps(comparable, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() != canonical:
                    raise ValueError("기존 제안 파일이 일치하지 않아 덮어쓰지 않았습니다")
                content = existing
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return {"proposal": content, "proposal_path": str(target.relative_to(self.root)), "applied": False}
