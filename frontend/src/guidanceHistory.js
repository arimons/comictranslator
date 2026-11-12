// 영역 분석 가이드(guidance) 편집 기록을 화면 목록으로 펼치고, 목록의 한 항목을 가이드에서 빼는 순수 함수.
// App.jsx에서 그대로 옮겼고 proof/src/test_guidance_history.mjs가 검사한다.
import { TEXT_BASED, regionProcessingBasis, regionTypeLabel } from "./regionTypes.js";
import { recordTargetsRegion } from "./regionExclusion.js";
import { cloneJson, defaultGuidance } from "./lib.js";

export const ENGINE_NAMES = { lama: "LaMa", telea: "Telea", stripe: "줄무늬" };

// 두 가이드가 같은지 JSON으로 비교한다(overlay 수동값 비교에도 쓴다).
export function guidanceEquals(left, right) {
  return JSON.stringify(left || {}) === JSON.stringify(right || {});
}

export function guidanceHistoryItems(guidance, guidanceRegions) {
  const items = [];
  (guidance.manual_seed_points || []).forEach((item, index) => {
    const point = item.point || item.seed || [];
    items.push({ key: `seed-${index}`, kind: "manual_seed_points", index, label: `Seed ${index + 1}`, detail: Array.isArray(point) ? `${Math.round(point[0])}, ${Math.round(point[1])}` : "" });
  });
  (guidance.virtual_walls || []).forEach((item, index) => {
    items.push({ key: `wall-${index}`, kind: "virtual_walls", index, boxId: item.box_id || "", label: item.box_id ? `Edge ${item.box_id}` : `Edge ${index + 1}`, detail: item.source || "" });
  });
  (guidance.split_guides || []).forEach((item, index) => {
    items.push({ key: `split-${index}`, kind: "split_guides", index, label: `Split line ${index + 1}`, detail: item.source || "" });
  });
  (guidance.manual_regions || []).forEach((item, index) => {
    const type = regionTypeLabel(item);
    const forcedLama = regionProcessingBasis(item) === TEXT_BASED && item.inpaint_engine === "lama";
    items.push({
      key: `region-${index}`,
      kind: "manual_regions",
      index,
      label: `${type} ${index + 1}`,
      detail: forcedLama ? "LaMa 강제" : item.source || "",
      isTextBased: regionProcessingBasis(item) === TEXT_BASED,
      forcedLama,
    });
  });
  (guidance.tg_splits || []).forEach((item, index) => {
    items.push({ key: `tg-split-${index}`, kind: "tg_splits", index, label: `TG${item.text_group_id || "?"} split`, detail: "" });
  });
  (guidance.disabled_tg_ids || []).forEach((id) => {
    items.push({ key: `tg-off-${id}`, kind: "disabled_tg_ids", id, label: `TG${id} off`, detail: "" });
  });
  (guidance.suppressed_regions || []).forEach((item, index) => {
    items.push({ key: `region-off-${index}`, kind: "suppressed_regions", index, label: `R${item.display_region_id || item.region_id || item.source_region_id || "?"} 제외 기록`, detail: "자동 처리 대상에서 제외" });
  });
  (guidance.inpaint_engine_overrides || []).forEach((item, index) => {
    items.push({ key: `engine-${index}`, kind: "inpaint_engine_overrides", index, label: `R${item.display_region_id || "?"} ${ENGINE_NAMES[item.engine] || item.engine} 고정`, detail: `지우기 엔진을 ${ENGINE_NAMES[item.engine] || item.engine}로 고정` });
  });
  (guidance.custom_masks || []).forEach((item, index) => {
    // R에 붙인 기록은 R마다 한 줄, R 지정 없이 칠한 기록(가장 많이 겹치는 영역에 자동 배정)은 한 줄이다.
    const attached = Boolean(item.instance_ref || item.region_ref || item.target_key);
    const dormant = attached && !guidanceRegions.some((region) => recordTargetsRegion(item, region));
    const shapes = `붓 ${(item.strokes || []).length} · 다각형 ${(item.polygons || []).length} · 사각형 ${(item.rects || []).length}`;
    items.push({
      key: `custom-mask-${index}`, kind: "custom_masks", index,
      label: `${attached ? `R${item.display_region_id || "?"} 지우기 보정` : "지우기 보정(자동 배정)"}${item.fill === "dots" ? " · 주변 도트" : ""}`,
      detail: dormant ? `${shapes} · 영역 없음: 흰칠만` : shapes,
    });
  });
  return items;
}

export function stripGuidanceHistoryItem(baseGuidance, historyItem) {
  const next = cloneJson(baseGuidance || defaultGuidance(null));
  if (!historyItem?.kind) return next;
  if (historyItem.kind === "virtual_walls" && historyItem.boxId) {
    next.virtual_walls = (next.virtual_walls || []).filter((item) => item.box_id !== historyItem.boxId);
    return next;
  }
  if (historyItem.kind === "disabled_tg_ids") {
    next.disabled_tg_ids = (next.disabled_tg_ids || []).filter((id) => Number(id) !== Number(historyItem.id));
    return next;
  }
  const list = [...(next[historyItem.kind] || [])];
  if (historyItem.index >= 0 && historyItem.index < list.length) list.splice(historyItem.index, 1);
  next[historyItem.kind] = list;
  return next;
}
