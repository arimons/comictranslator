"""처리 기준 용어와 저장 JSON의 과거 타입 사이의 호환 경계.

저장된 type은 bubble/unboxed를 유지한다. 새 처리 기준은 파생 정보이며,
둘이 충돌하면 기존 파이프라인과 같은 결과를 유지하도록 저장 type을 우선한다.
"""
from collections.abc import Mapping
from typing import Any

BOUNDARY_BASED = "boundary_based"
TEXT_BASED = "text_based"
LEGACY_TYPE_TO_BASIS = {"bubble": BOUNDARY_BASED, "unboxed": TEXT_BASED}
REGION_BASIS_LABELS = {BOUNDARY_BASED: "경계 기반", TEXT_BASED: "텍스트 기반"}


def region_processing_basis(region: Mapping[str, Any]) -> str | None:
    stored_type = region.get("type")
    legacy = LEGACY_TYPE_TO_BASIS.get(stored_type) if isinstance(stored_type, str) else None
    if legacy:
        return legacy
    basis = region.get("processing_basis")
    return basis if isinstance(basis, str) and basis in REGION_BASIS_LABELS else None


def region_type_label(region: Mapping[str, Any]) -> str:
    return REGION_BASIS_LABELS.get(region_processing_basis(region), "미분류")


def with_processing_basis(region: Mapping[str, Any]) -> dict[str, Any]:
    """원본·캐시를 수정하지 않고 API/신규 산출물용 파생 정보를 붙인다."""
    result = dict(region)
    basis = region_processing_basis(region)
    if basis is not None:
        result["processing_basis"] = basis
    return result


def regions_with_processing_basis(payload: Any) -> Any:
    if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
        return payload
    return {**payload, "regions": [with_processing_basis(r) if isinstance(r, dict) else r for r in payload["regions"]]}
