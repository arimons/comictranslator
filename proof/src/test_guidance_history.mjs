import assert from "node:assert/strict";
import { loadFrontendModule } from "./frontend_module_loader.mjs";

const { ENGINE_NAMES, guidanceEquals, guidanceHistoryItems, stripGuidanceHistoryItem } = await loadFrontendModule("frontend/src/guidanceHistory.js");

assert.equal(ENGINE_NAMES.lama, "LaMa");
assert.equal(guidanceEquals(null, {}), true);
assert.equal(guidanceEquals({ a: 1 }, { a: 2 }), false);

const guidance = {
  manual_seed_points: [{ point: [10.4, 20.6] }],
  virtual_walls: [{ box_id: "w1", source: "drag" }, { source: "drag" }],
  manual_regions: [{ type: "unboxed", inpaint_engine: "lama" }],
  disabled_tg_ids: [3],
  inpaint_engine_overrides: [{ display_region_id: 2, engine: "telea" }],
  custom_masks: [{ region_ref: "ref-gone", display_region_id: 5, strokes: [{}], polygons: [], rects: [] }, { strokes: [], polygons: [{}], rects: [] }],
};
const items = guidanceHistoryItems(guidance, []);

// 기록 목록은 종류별 고정 순서로 펼친다
assert.deepEqual(items.map((item) => item.key), ["seed-0", "wall-0", "wall-1", "region-0", "tg-off-3", "engine-0", "custom-mask-0", "custom-mask-1"]);
assert.equal(items[0].detail, "10, 21");
assert.equal(items[1].label, "Edge w1");
assert.equal(items[3].isTextBased, true);
assert.equal(items[3].detail, "LaMa 강제");
assert.equal(items[5].label, "R2 Telea 고정");
// R에 붙인 지우기 보정인데 그 R이 없으면 흰칠만 된다고 알린다. R 지정 없는 기록은 자동 배정이다
assert.match(items[6].detail, /영역 없음: 흰칠만$/);
assert.equal(items[7].label, "지우기 보정(자동 배정)");

// 항목 빼기: box_id가 있는 벽은 id로, 끈 TG는 번호로, 나머지는 위치로 뺀다. 원래 가이드는 바꾸지 않는다
const noWall = stripGuidanceHistoryItem(guidance, items[1]);
assert.deepEqual(noWall.virtual_walls, [{ source: "drag" }]);
assert.equal(guidance.virtual_walls.length, 2);
assert.deepEqual(stripGuidanceHistoryItem(guidance, items[4]).disabled_tg_ids, []);
assert.deepEqual(stripGuidanceHistoryItem(guidance, items[7]).custom_masks.length, 1);
// 종류가 없으면 복사본만 돌려준다
assert.deepEqual(stripGuidanceHistoryItem(guidance, null), guidance);

console.log("가이드 편집 기록: 목록 순서·라벨·지우기 보정 상태·항목 빼기 통과");
