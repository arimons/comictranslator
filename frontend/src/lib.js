import { BOUNDARY_BASED, TEXT_BASED, regionProcessingBasis, usesEdgeBarrier } from "./regionTypes.js";
export { BOUNDARY_BASED, TEXT_BASED, regionProcessingBasis, regionTypeLabel } from "./regionTypes.js";

export const TOOL_LABELS = [
  ["select", "선택"],
  ["hand", "화면 이동"],
  ["tg-toggle", "TG 제외"],
  ["tg-split", "TG 분할"],
  ["wall", "Edge 장벽"],
  ["region-split", "영역 분할"],
  ["ubox", "텍스트 기반 영역"],
  ["bubble", "경계 기반 seed"],
];

// 단축키는 keymap.js가 단일 정의다.
export const TOOL_ICONS = {
  select: "⌖",
  hand: "✋",
  "tg-toggle": "T×",
  "tg-split": "T/",
  wall: "▱",
  "region-split": "╱",
  ubox: "□",
  bubble: "●",
};

export const OVERLAY_HANDLE_NAMES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
export const OVERLAY_MIN_RECT_SIZE = 12;
export const OVERLAY_REGION_COLORS = {
  [BOUNDARY_BASED]: "#38bdf8",
  edge_barrier: "#22c55e",
  [TEXT_BASED]: "#eab308",
};

export const WORKFLOW_TABS = [
  ["guidance", "영역 분석", "1"],
  ["ocr", "글자인식/번역", "2"],
  ["overlay", "글자 입히기", "3"],
  ["restore", "배경 복원", "5"],
  ["export", "내보내기", "4"],
];

export const FONT_STYLES = [
  ["normal", "N", "Normal", "가"],
  ["bold", "B", "Bold", "가"],
  ["impact", "I", "Impact", "가"],
  ["light", "L", "Light", "가"],
  ["memory_serif", "R", "Serif", "가"],
  ["serif_bold", "SB", "Serif Bold", "가"],
  ["handwritten", "H", "Handwritten", "가"],
  ["serif_emphasis", "E", "EFX", "가"],
  ["etc", "T", "Etc", "가"],
];
export const FONT_STYLE_IDS = FONT_STYLES.map(([id]) => id);
export const ETC_DEFAULT_FONT_NAME = "NanumBarunpenB";

export function api(path, options = {}) {
  const headers = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  return fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } }).then(async (res) => {
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text);
    }
    return res.json();
  });
}

export async function apiStream(path, options, onEvent) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok || !res.body) {
    const text = await res.text();
    throw new Error(text);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        finished = true;
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        onEvent(JSON.parse(line));
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) onEvent(JSON.parse(buffer));
  } finally {
    // onEvent가 throw해 중간에 빠져나오면 스트림 연결을 닫는다.
    if (!finished) reader.cancel().catch(() => {});
  }
}

// 세션별 OCR 설정(추가 프롬프트, 모델)을 localStorage에 보관한다. 접근 불가 환경에서는 조용히 무시한다.
function sessionSettingsKey(sessionId) {
  return `comic-translator.session-settings.${sessionId}`;
}

export function readSessionSettings(sessionId) {
  if (!sessionId) return {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(sessionSettingsKey(sessionId)) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function writeSessionSettings(sessionId, patch) {
  if (!sessionId) return;
  try {
    const next = { ...readSessionSettings(sessionId), ...patch };
    window.localStorage.setItem(sessionSettingsKey(sessionId), JSON.stringify(next));
  } catch {
    // 저장 실패는 무시
  }
}

export function removeSessionSettings(sessionId) {
  if (!sessionId) return;
  try {
    window.localStorage.removeItem(sessionSettingsKey(sessionId));
  } catch {
    // 삭제 실패는 무시
  }
}

export function rectOf(group) {
  return group?.tight_rect || group?.fallback_rect || group?.bounding_rect || [0, 0, 0, 0];
}

export function tgId(group) {
  return Number(group?.text_group_id ?? group?.cluster_id ?? -1);
}

export function imageGroups(image) {
  if (image?.processing_excluded) return [];
  return image?.has_effective_text_groups ? (image?.effective_text_groups || []) : (image?.text_groups || []);
}

export function defaultGuidance(image) {
  return {
    disabled_tg_ids: image?.guidance?.disabled_tg_ids || [],
    tg_splits: image?.guidance?.tg_splits || [],
    virtual_walls: image?.guidance?.virtual_walls || [],
    split_guides: image?.guidance?.split_guides || [],
    manual_regions: image?.guidance?.manual_regions || [],
    manual_seed_points: image?.guidance?.manual_seed_points || [],
    suppressed_regions: image?.guidance?.suppressed_regions || [],
  };
}

export function defaultOverlayGuidance(image) {
  return {
    regions: image?.overlay_guidance?.regions || {},
    eraser_rects: image?.overlay_guidance?.eraser_rects || [],
  };
}

export function cloneJson(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

export function regionId(region) {
  return Number(region?.region_id ?? -1);
}

export function overlayItemKey(item) {
  if (item?.overlay_key != null) return String(item.overlay_key);
  return String(regionId(item));
}

export function overlayItemLabel(item) {
  const rid = Number(item?.region_id ?? -1);
  const segment = item?.source_segment_index;
  return segment ? `R${rid}-${segment}` : `R${rid}`;
}

export function guidanceEdgeNotices(report) {
  const boxes = report?.manual_guidance?.edge_boxes || [];
  return boxes
    .filter((item) => item && (!item.ok || item.used_box_fallback))
    .map((item) => {
      const reason = String(item.reason || "unknown");
      const reasonText = {
        no_target_group: "겹치는 TG가 없어",
        edge_barrier_no_safe_mask: "안전한 flood mask를 만들지 못해",
        edge_barrier_auto_no_safe_mask: "안전한 flood mask를 만들지 못해",
        empty_edge_box: "edge box 크기가 비어 있어",
        empty_edge_region: "edge box 안 region이 비어 있어",
      }[reason] || `${reason} 때문에`;
      return {
        key: `${item.box_id || "edge"}-${item.box_index || 0}-${reason}`,
        title: `Edge ${item.box_index || ""}`.trim(),
        message: item.used_box_fallback
          ? `${reasonText} rescue에 실패했습니다. 지정한 box를 region으로 적용했습니다.`
          : `${reasonText} region을 만들지 못했습니다.`,
        detail: `rect ${JSON.stringify(item.rect || [])} · TG ${(item.anchor_ids || []).join(",") || "-"}`,
      };
    });
}

export function imageRegions(image) {
  if (image?.processing_excluded) return [];
  return image?.regions?.regions || [];
}

export function rectFromClusterDetails(cluster) {
  const rects = [];
  (cluster?.matched_cluster_details || []).forEach((detail) => {
    const rect = detail?.bounding_rect;
    if (Array.isArray(rect) && rect.length >= 4) rects.push(rect.slice(0, 4).map(Number));
  });
  if (!rects.length) return null;
  const x1 = Math.min(...rects.map((rect) => rect[0]));
  const y1 = Math.min(...rects.map((rect) => rect[1]));
  const x2 = Math.max(...rects.map((rect) => rect[0] + rect[2]));
  const y2 = Math.max(...rects.map((rect) => rect[1] + rect[3]));
  return [x1, y1, Math.max(1, x2 - x1), Math.max(1, y2 - y1)];
}

export function segmentRenderRect(cluster, siblings, baseRegion) {
  if (!cluster || cluster?.source_segment_index == null || !baseRegion) return null;
  const safeRect = baseRegion.safe_text_rect || baseRegion.bubble_rect || baseRegion.unboxed_rect || baseRegion.source_rect;
  if (!Array.isArray(safeRect) || safeRect.length < 4) return null;
  const detailItems = siblings
    .map((item) => ({ cluster: item, rect: rectFromClusterDetails(item) }))
    .filter((item) => item.rect && item.cluster?.source_segment_index != null);
  if (detailItems.length < 2) return null;
  const currentIndex = detailItems.findIndex((item) => Number(item.cluster.source_segment_index) === Number(cluster.source_segment_index));
  if (currentIndex < 0) return null;

  const [sx, sy, sw, sh] = safeRect.slice(0, 4).map(Number);
  const center = (item, axis) => {
    const [x, y, w, h] = item.rect;
    return axis === "x" ? x + w / 2 : y + h / 2;
  };
  const xSpread = Math.max(...detailItems.map((item) => center(item, "x"))) - Math.min(...detailItems.map((item) => center(item, "x")));
  const ySpread = Math.max(...detailItems.map((item) => center(item, "y"))) - Math.min(...detailItems.map((item) => center(item, "y")));
  const axis = xSpread >= ySpread ? "x" : "y";
  const ordered = [...detailItems].sort((a, b) => center(a, axis) - center(b, axis));
  const index = ordered.findIndex((item) => Number(item.cluster.source_segment_index) === Number(cluster.source_segment_index));
  const current = center(ordered[index], axis);
  const previous = index > 0 ? center(ordered[index - 1], axis) : null;
  const next = index + 1 < ordered.length ? center(ordered[index + 1], axis) : null;
  const clampValue = (value, min, max) => Math.min(max, Math.max(min, value));
  const centeredSpan = (centerValue, rawStart, rawEnd, minSize, maxSize, boundStart, boundEnd) => {
    const rawSize = rawEnd - rawStart;
    if (rawSize < minSize) return null;
    const size = Math.max(minSize, Math.min(rawSize, maxSize, boundEnd - boundStart));
    const start = clampValue(Math.round(centerValue - size / 2), boundStart, boundEnd - size);
    return [start, start + size];
  };

  if (axis === "x") {
    const left = clampValue(previous == null ? sx : Math.round((previous + current) / 2), sx, sx + sw);
    const right = clampValue(next == null ? sx + sw : Math.round((current + next) / 2), sx, sx + sw);
    const [_rx, ry, rw, rh] = ordered[index].rect;
    const minW = Math.max(32, Math.round(sw * 0.18));
    const maxW = Math.max(minW, Math.round(Math.max(rw * 4.2, sw * 0.34)));
    const xSpan = centeredSpan(current, left, right, minW, maxW, sx, sx + sw);
    if (!xSpan) return null;
    const padY = Math.max(30, Math.round(rh * 0.16));
    let top = clampValue(ry - padY, sy, sy + sh);
    let bottom = clampValue(ry + rh + padY, sy, sy + sh);
    const minH = Math.max(40, Math.round(sh * 0.28));
    const maxH = Math.max(minH, Math.round(Math.max(rh * 1.65, sh * 0.72)));
    const ySpan = centeredSpan(ry + rh / 2, top, bottom, minH, maxH, sy, sy + sh);
    if (!ySpan) return null;
    return [xSpan[0], ySpan[0], xSpan[1] - xSpan[0], ySpan[1] - ySpan[0]];
  }

  const top = clampValue(previous == null ? sy : Math.round((previous + current) / 2), sy, sy + sh);
  const bottom = clampValue(next == null ? sy + sh : Math.round((current + next) / 2), sy, sy + sh);
  const [rx, _ry, rw, rh] = ordered[index].rect;
  const minH = Math.max(32, Math.round(sh * 0.18));
  const maxH = Math.max(minH, Math.round(Math.max(rh * 4.2, sh * 0.34)));
  const ySpan = centeredSpan(current, top, bottom, minH, maxH, sy, sy + sh);
  if (!ySpan) return null;
  const padX = Math.max(24, Math.round(rw * 0.22));
  let left = clampValue(rx - padX, sx, sx + sw);
  let right = clampValue(rx + rw + padX, sx, sx + sw);
  const minW = Math.max(40, Math.round(sw * 0.28));
  const maxW = Math.max(minW, Math.round(Math.max(rw * 1.65, sw * 0.72)));
  const xSpan = centeredSpan(rx + rw / 2, left, right, minW, maxW, sx, sx + sw);
  if (!xSpan) return null;
  return [xSpan[0], ySpan[0], xSpan[1] - xSpan[0], ySpan[1] - ySpan[0]];
}

export function overlayControlItems(image, overlayGuidanceOverride = null) {
  if (image?.processing_excluded) return [];
  const layoutRegions = image?.overlay_layout?.regions;
  const guidanceRegions = (overlayGuidanceOverride || image?.overlay_guidance || {})?.regions || {};
  const baseRegions = imageRegions(image);
  const byRegionId = new Map(baseRegions.map((region) => [regionId(region), region]));
  const segmentedClusters = integratedClusters(image)
    .filter((cluster) => cluster?.source_region_id != null && cluster?.source_segment_index != null);
  const bySegmentKey = new Map(segmentedClusters.map((cluster) => [`${Number(cluster.source_region_id)}:${Number(cluster.source_segment_index)}`, cluster]));
  const siblingsByRegion = new Map();
  segmentedClusters.forEach((cluster) => {
    const rid = Number(cluster.source_region_id);
    siblingsByRegion.set(rid, [...(siblingsByRegion.get(rid) || []), cluster]);
  });
  const segmentedRegionIds = new Set(segmentedClusters.map((cluster) => Number(cluster.source_region_id)));
  const layoutMergedRegionIds = new Set();
  if (layoutRegions && typeof layoutRegions === "object") {
    Object.entries(layoutRegions).forEach(([key, layout]) => {
      if (String(key).includes(":")) return;
      const rid = Number(layout?.region_id ?? key);
      if (Number.isFinite(rid)) layoutMergedRegionIds.add(rid);
    });
  }
  const items = [];
  const seen = new Set();

  function segmentModeForRegionId(rid) {
    const value = guidanceRegions?.[String(rid)]?.segment_mode;
    return value === "merge" || value === "split" ? value : "auto";
  }

  function pushItem(key, layout, cluster = null) {
    const rawKey = String(key);
    if (seen.has(rawKey)) return;
    const segmentCluster = cluster || bySegmentKey.get(rawKey) || null;
    const rid = Number(layout?.region_id ?? segmentCluster?.source_region_id ?? rawKey.split(":")[0]);
    const base = byRegionId.get(rid) || {};
    const segmentRect = segmentRenderRect(segmentCluster, siblingsByRegion.get(rid) || [], base);
    const layoutRect = layout?.bbox;
    const isManualLayoutRect = layout?.bbox_source === "manual_overlay_safe_text_rect";
    const rect = (
      (isManualLayoutRect ? layoutRect : null)
      || segmentRect
      || layoutRect
      || segmentCluster?.safe_text_rect
      || segmentCluster?.union_bbox
      || segmentCluster?.bounding_rect
      || base.safe_text_rect
      || base.bubble_rect
      || base.unboxed_rect
      || base.source_rect
      || null
    );
    seen.add(rawKey);
    const outlineRect = base.unboxed_rect || base.bubble_rect || base.source_rect || rect;
    items.push({
      ...base,
      overlay_key: rawKey,
      region_id: rid,
      source_segment_index: layout?.source_segment_index ?? segmentCluster?.source_segment_index ?? null,
      sentence_id: layout?.sentence_id ?? segmentCluster?.sentence_id ?? null,
      matched_cluster: layout?.matched_cluster || segmentCluster?.matched_cluster || base.anchor_ids || [],
      safe_text_rect: rect,
      align_reference_rect: base.bubble_rect || base.unboxed_rect || base.source_rect || rect,
      region_outline_rect: outlineRect,
      bubble_rect: rect || base.bubble_rect,
      source_rect: rect || base.source_rect,
      overlay_layout: layout || {
        text_source: segmentCluster?.text_translated || segmentCluster?.text || segmentCluster?.ocr_text || "",
        text_layout: segmentCluster?.text_translated || segmentCluster?.text || segmentCluster?.ocr_text || "",
        bbox: rect,
      },
    });
  }

  if (layoutRegions && typeof layoutRegions === "object" && Object.keys(layoutRegions).length) {
    Object.entries(layoutRegions).forEach(([key, layout]) => {
      const rid = Number(layout?.region_id ?? String(key).split(":")[0]);
      if (!String(key).includes(":") && segmentedRegionIds.has(rid)) return;
      pushItem(key, layout, bySegmentKey.get(String(key)) || null);
    });
  }

  segmentedClusters.forEach((cluster) => {
    const rid = Number(cluster.source_region_id);
    const mode = segmentModeForRegionId(rid);
    if (mode === "merge") return;
    if (mode === "auto" && layoutMergedRegionIds.has(rid)) return;
    pushItem(`${rid}:${Number(cluster.source_segment_index)}`, null, cluster);
  });

  baseRegions.forEach((region) => {
    const rid = regionId(region);
    if (!segmentedRegionIds.has(rid)) pushItem(String(rid), null, null);
  });

  return items.sort((a, b) => {
    const ra = Number(a.region_id ?? 0);
    const rb = Number(b.region_id ?? 0);
    if (ra !== rb) return ra - rb;
    return Number(a.source_segment_index || 0) - Number(b.source_segment_index || 0);
  });
}

export function overlayRegionColor(region) {
  const basis = regionProcessingBasis(region);
  return OVERLAY_REGION_COLORS[usesEdgeBarrier(region) ? "edge_barrier" : basis] || OVERLAY_REGION_COLORS[BOUNDARY_BASED];
}

export function rectsApproximatelyEqual(a, b, tolerance = 3) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length < 4 || b.length < 4) return false;
  return a.slice(0, 4).every((value, index) => Math.abs(Number(value) - Number(b[index])) <= tolerance);
}

export function regionDisplayRect(region) {
  return region?.bubble_rect || region?.unboxed_rect || region?.source_rect || region?.safe_text_rect || null;
}

export function integratedClusters(image) {
  const data = image?.integrated;
  if (Array.isArray(data)) return data;
  if (data?.clusters && Array.isArray(data.clusters)) return data.clusters;
  return [];
}

export function canRenderOverlayImage(image) {
  return Boolean(
    image?.has_integrated_json
    && !image?.integrated_skipped
    && !image?.integrated?.skipped
    && image?.ocr_status?.status !== "skipped"
  );
}

// 세션 목록은 요약(summary)만 담고, 선택한 페이지의 큰 JSON은 /api/session/{id}/page/{image}로 받는다.
export const PAGE_DETAIL_FIELDS = [
  "text_groups",
  "effective_text_groups",
  "regions",
  "guidance",
  "guidance_report",
  "overlay_guidance",
  "overlay_layout",
  "integrated",
];

export function imageSummaryOf(record) {
  if (!record) return record;
  const summary = { ...record };
  PAGE_DETAIL_FIELDS.forEach((key) => {
    delete summary[key];
  });
  summary.detail_loaded = false;
  return summary;
}

export function hasAnalysisResult(image) {
  if (Array.isArray(image?.regions?.regions)) return true;
  return Boolean(image?.has_analysis);
}

export function imageNeedsAnalysis(image) {
  return Boolean(image && !image.processing_excluded && !hasAnalysisResult(image));
}

export function regionCluster(image, region) {
  const key = overlayItemKey(region);
  if (String(key).includes(":")) {
    const [ridText, segmentText] = String(key).split(":");
    const rid = Number(ridText);
    const segment = Number(segmentText);
    const bySegment = integratedClusters(image).find((cluster) => (
      Number(cluster.source_region_id) === rid && Number(cluster.source_segment_index) === segment
    ));
    if (bySegment) return bySegment;
  }
  const rid = regionId(region);
  const byRegionId = integratedClusters(image).find((cluster) => Number(cluster.source_region_id) === rid);
  if (byRegionId) return byRegionId;
  const anchors = new Set((region?.anchor_ids || []).map((id) => Number(id)));
  if (!anchors.size) return null;
  return integratedClusters(image).find((cluster) => (cluster.matched_cluster || []).some((id) => anchors.has(Number(id)))) || null;
}

export function regionSegmentSiblingCount(image, region) {
  const rid = regionId(region);
  if (!Number.isFinite(rid)) return 0;
  return integratedClusters(image).filter((cluster) => (
    Number(cluster?.source_region_id) === rid
    && cluster?.source_segment_index != null
  )).length;
}

export function baseRegionText(image, region) {
  const layoutText = image?.overlay_layout?.regions?.[overlayItemKey(region)]?.text_layout;
  if (typeof layoutText === "string" && layoutText.trim()) return layoutText.trim();
  const sourceText = region?.overlay_layout?.text_source;
  if (typeof sourceText === "string" && sourceText.trim()) return sourceText.trim();
  const cluster = regionCluster(image, region);
  return (cluster?.text_translated || cluster?.text || cluster?.ocr_text || "").trim();
}

export function inferredRegionFontStyle(image, region) {
  const cluster = regionCluster(image, region);
  const hint = cluster?.render_hint_final || cluster?.render_hint || cluster?.render_hint_raw || "";
  if (hint === "sfx" || hint === "efx") return "serif_emphasis";
  if (hint === "semibold") return "bold";
  if (hint === "serif_bold") return "serif_bold";
  return FONT_STYLE_IDS.includes(hint) ? hint : "normal";
}

export function inferredRegionWritingMode(image, region) {
  const cluster = regionCluster(image, region);
  const orientation = String(cluster?.orientation || region?.orientation || "").toLowerCase();
  if (orientation === "horizontal") return "horizontal";
  if (orientation === "vertical") return "vertical";
  return "";
}

export function overlayWritingModeChoice(override) {
  if (override?.force_horizontal) return "horizontal";
  if (override?.force_vertical) return "vertical";
  return "auto";
}

export function overlayItemFontSize(item) {
  const raw = item?.overlay_layout?.font_size;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : 0;
}

export function overlayRegionValue(overlayGuidance, rid) {
  return overlayGuidance?.regions?.[String(rid)] || {};
}

export function overlayRegionBaseKey(region) {
  const rid = regionId(region);
  return Number.isFinite(rid) && rid >= 0 ? String(rid) : overlayItemKey(region).split(":")[0];
}

export function overlayEraserRects(overlayGuidance) {
  return Array.isArray(overlayGuidance?.eraser_rects) ? overlayGuidance.eraser_rects : [];
}
