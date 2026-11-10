// 글자 입히기(overlay) 수동값을 저장 가능한 범위로 정리하는 순수 함수.
// App.jsx에서 그대로 옮겼고 proof/src/test_overlay_normalize.mjs가 검사한다.
import { OVERLAY_MIN_RECT_SIZE } from "./lib.js";

export function normalizedFontSizeDelta(value) {
  const parsed = Number(value || 0);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(-30, Math.min(30, Math.round(parsed)));
}

export function normalizedFontSize(value) {
  const parsed = Number(value || 0);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(8, Math.min(160, Math.round(parsed)));
}

export function normalizedFontWeight(value) {
  const parsed = Number(value || 0);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0;
  return Math.max(100, Math.min(1000, Math.round(parsed / 50) * 50));
}

export function normalizedStrokeWidth(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.min(12, Math.round(parsed)));
}

export function normalizeOverlayRegionOverride(value) {
  if (!value || typeof value !== "object") return {};
  const next = { ...value };
  if ("safe_text_rect" in next) {
    const rect = Array.isArray(next.safe_text_rect)
      ? next.safe_text_rect.slice(0, 4).map((item) => Math.round(Number(item) || 0))
      : null;
    if (rect && rect.length === 4 && rect[2] >= OVERLAY_MIN_RECT_SIZE && rect[3] >= OVERLAY_MIN_RECT_SIZE) {
      next.safe_text_rect = rect;
    } else {
      delete next.safe_text_rect;
    }
  }
  if ("font_size" in next) {
    const fontSize = normalizedFontSize(next.font_size);
    if (fontSize) next.font_size = fontSize;
    else delete next.font_size;
    delete next.font_size_delta;
  }
  if ("font_size_delta" in next) {
    const delta = normalizedFontSizeDelta(next.font_size_delta);
    if (delta) next.font_size_delta = delta;
    else delete next.font_size_delta;
  }
  if ("rotation_deg" in next) {
    const angle = Number(next.rotation_deg || 0);
    if (Number.isFinite(angle) && Math.abs(angle) > 0.1) {
      next.rotation_deg = Math.max(-45, Math.min(45, Math.round(angle)));
    } else {
      delete next.rotation_deg;
    }
  }
  if ("font_weight" in next) {
    const fontWeight = normalizedFontWeight(next.font_weight);
    if (fontWeight) next.font_weight = fontWeight;
    else delete next.font_weight;
  }
  ["font_color", "stroke_color"].forEach((key) => {
    if (key in next && !/^#[0-9a-fA-F]{6}$/.test(String(next[key] || ""))) delete next[key];
  });
  if ("stroke_width" in next) {
    const strokeWidth = normalizedStrokeWidth(next.stroke_width);
    if (strokeWidth !== null) next.stroke_width = strokeWidth;
    else delete next.stroke_width;
  }
  if ("segment_mode" in next && !["merge", "split"].includes(next.segment_mode)) {
    delete next.segment_mode;
  }
  if ("text_align" in next && !["left", "center", "right"].includes(next.text_align)) {
    delete next.text_align;
  }
  if (next.font_style !== "etc") {
    delete next.font_weight;
  }
  return next;
}

export function normalizeOverlayGuidance(value) {
  const regions = value?.regions && typeof value.regions === "object" ? value.regions : {};
  const eraserRects = Array.isArray(value?.eraser_rects)
    ? value.eraser_rects
      .filter((rect) => Array.isArray(rect) && rect.length >= 4)
      .map((rect) => rect.slice(0, 4).map((next) => Math.round(Number(next) || 0)))
      .filter((rect) => rect[2] >= 4 && rect[3] >= 4)
    : [];
  return {
    ...(value || {}),
    eraser_rects: eraserRects,
    regions: Object.fromEntries(
      Object.entries(regions).map(([key, region]) => [key, normalizeOverlayRegionOverride(region)])
    ),
  };
}
