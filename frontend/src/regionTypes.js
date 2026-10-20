// 저장 형식과 화면 용어의 호환 경계. 기존 type / tool ID / rect 키는 바꾸지 않는다.
export const BOUNDARY_BASED = "boundary_based";
export const TEXT_BASED = "text_based";
export const REGION_BASIS_LABELS = {
  [BOUNDARY_BASED]: "경계 기반",
  [TEXT_BASED]: "텍스트 기반",
};
const LEGACY_TYPE_TO_BASIS = { bubble: BOUNDARY_BASED, unboxed: TEXT_BASED };

export function regionProcessingBasis(region) {
  // 실제 처리에 쓰는 과거 type을 우선해 새 파생 필드가 낡았을 때도 같은 의미로 읽는다.
  const legacy = typeof region?.type === "string" && Object.hasOwn(LEGACY_TYPE_TO_BASIS, region.type) ? LEGACY_TYPE_TO_BASIS[region.type] : null;
  if (legacy) return legacy;
  const basis = region?.processing_basis;
  return typeof basis === "string" && Object.hasOwn(REGION_BASIS_LABELS, basis) ? basis : null;
}

export function regionTypeLabel(region) {
  return REGION_BASIS_LABELS[regionProcessingBasis(region)] || "미분류";
}

export function usesEdgeBarrier(region) {
  // Edge는 타입이 아니라 검출 방법이다. 색상 구분이 필요한 경우에만 별도로 조회한다.
  const methods = [region?.source_method, region?.method, region?.source_region_type,
    region?.region_type_guess, ...(Array.isArray(region?.quality_flags) ? region.quality_flags : [])].join(" ").toLowerCase();
  return regionProcessingBasis(region) !== TEXT_BASED && (methods.includes("edge_barrier") || methods.includes("manual_edge"));
}

// 처리 기준은 두 가지, 화면의 세 색은 Edge 검출 방법까지 함께 구분한다.
export function regionDetectionLabel(region) {
  if (regionProcessingBasis(region) === TEXT_BASED) return "텍스트 기반 · Unboxed";
  if (usesEdgeBarrier(region)) return "경계 기반 · Edge";
  if (regionProcessingBasis(region) === BOUNDARY_BASED) return "경계 기반 · Bubble";
  return "미분류";
}
