import assert from "node:assert/strict";
import { loadFrontendModule } from "./frontend_module_loader.mjs";

const {
  normalizedFontSize, normalizedFontWeight, normalizedStrokeWidth,
  normalizeOverlayRegionOverride, normalizeOverlayGuidance,
} = await loadFrontendModule("frontend/src/overlayNormalize.js");

// 글자 크기·굵기·외곽선은 허용 범위로 자르고 반올림한다
assert.equal(normalizedFontSize(3), 8);
assert.equal(normalizedFontSize(500), 160);
assert.equal(normalizedFontSize("abc"), 0);
assert.equal(normalizedFontWeight(430), 450);
assert.equal(normalizedFontWeight(0), 0);
assert.equal(normalizedStrokeWidth(""), 0);
assert.equal(normalizedStrokeWidth("x"), null);
assert.equal(normalizedStrokeWidth(20), 12);

// 영역 수동값: 작은 안전 영역·잘못된 색·허용 밖 정렬은 지우고, 글자 크기를 정하면 크기 증감값은 버린다
assert.deepEqual(normalizeOverlayRegionOverride(null), {});
assert.deepEqual(
  normalizeOverlayRegionOverride({ safe_text_rect: [1.4, 2.6, 5, 40], font_size: 30, font_size_delta: 4, font_color: "red", text_align: "justify", rotation_deg: 80 }),
  { font_size: 30, rotation_deg: 45 },
);
assert.deepEqual(normalizeOverlayRegionOverride({ safe_text_rect: [1.4, 2.6, 40, 40] }).safe_text_rect, [1, 3, 40, 40]);

// 굵기는 etc 글꼴에만 남는다
assert.deepEqual(normalizeOverlayRegionOverride({ font_style: "etc", font_weight: 720 }), { font_style: "etc", font_weight: 700 });
assert.deepEqual(normalizeOverlayRegionOverride({ font_style: "normal", font_weight: 720 }), { font_style: "normal" });

// 지우개 사각형은 4px 미만을 버리고 정수로 맞춘다
assert.deepEqual(
  normalizeOverlayGuidance({ eraser_rects: [[0, 0, 2, 9], [1.2, 1.8, 10.4, 10], "bad"], regions: { a: { text_align: "left" } } }),
  { eraser_rects: [[1, 2, 10, 10]], regions: { a: { text_align: "left" } } },
);

console.log("글자 입히기 수동값 정리: 범위 자르기·잘못된 값 제거·etc 굵기·지우개 사각형 통과");
