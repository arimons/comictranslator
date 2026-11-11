import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const { PREFETCH_LOOKAHEAD, pickPrefetchTarget, prefetchRequestKey } = await import(`data:text/javascript;base64,${readFileSync("frontend/src/prefetchPlan.js").toString("base64")}`);

// lib.js imageNeedsAnalysis와 같은 판정: 제외되지 않았고 분석 결과가 없으면 분석이 필요하다
const needsAnalysis = (img) => Boolean(img && !img.processing_excluded && !img.has_analysis);
const page = (id, extra = {}) => ({ id, image_sha256: `sha-${id}`, analysis_route: "standard", ...extra });
const images = [page("a", { has_analysis: true }), page("b", { has_analysis: true }), page("c"), page("d"), page("e", { has_analysis: true })];
const base = { images, selected: images[1], requested: new Set(), failed: new Set(), needsAnalysis };

// 설정이 꺼져 있으면 미리 분석할 페이지가 있어도 고르지 않는다
assert.equal(pickPrefetchTarget({ ...base, enabled: false }), null);
assert.equal(pickPrefetchTarget({ ...base, enabled: undefined }), null);

// 켜져 있으면 보던 페이지 다음의 미분석 페이지를 고른다
assert.equal(pickPrefetchTarget({ ...base, enabled: true })?.id, "c");

// 이미 요청했거나 실패한 페이지는 건너뛴다. 끝에 닿아도 앞쪽 페이지로 돌아가지 않는다
const requested = new Set([prefetchRequestKey(images[2])]);
assert.equal(pickPrefetchTarget({ ...base, enabled: true, requested })?.id, "d");
assert.equal(pickPrefetchTarget({ ...base, enabled: true, requested, failed: new Set([prefetchRequestKey(images[3])]) }), null);
assert.equal(pickPrefetchTarget({ ...base, enabled: true, selected: images[4] }), null);

// 보는 페이지 분석을 아직 시도하지 않았으면 그쪽이 먼저라 미리 분석하지 않는다. 시도한 뒤에는 이어간다.
assert.equal(pickPrefetchTarget({ ...base, enabled: true, selected: images[2] }), null);
assert.equal(pickPrefetchTarget({ ...base, enabled: true, selected: images[2], requested: new Set([prefetchRequestKey(images[2])]) })?.id, "d");

// 처리 제외·생성 권장 페이지는 미리 분석하지 않고, 생성 권장인 보는 페이지는 기다리지 않는다
const special = [page("x", { has_analysis: true }), page("y", { processing_excluded: true }), page("z", { analysis_route: "generation", image_generation_recommendation: { priority: "recommended" } }), page("w")];
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: special, selected: special[0] })?.id, "w");
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: special, selected: special[2] })?.id, "w");

// 보던 페이지 뒤 PREFETCH_LOOKAHEAD(5)장만 본다: 0번을 보면 1-5번 중에서 고르고 6번 이후는 고르지 않는다
assert.equal(PREFETCH_LOOKAHEAD, 5);
const long = [page("p0", { has_analysis: true }), ...Array.from({ length: 9 }, (_, i) => page(`p${i + 1}`))];
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: long, selected: long[0] })?.id, "p1");
const windowDone = new Set(long.slice(1, 6).map(prefetchRequestKey));
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: long, selected: long[0], requested: windowDone }), null);
// 범위 안 페이지가 분석됐거나 처리 제외·생성 권장이어도 범위를 늘리지 않는다
const skippedWindow = [
  page("q0", { has_analysis: true }), page("q1", { has_analysis: true }), page("q2", { processing_excluded: true }),
  page("q3", { analysis_route: "generation", image_generation_recommendation: { priority: "recommended" } }),
  page("q4", { has_analysis: true }), page("q5", { has_analysis: true }), page("q6"),
];
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: skippedWindow, selected: skippedWindow[0] }), null);
// 보는 페이지를 옮기면 범위도 따라간다
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: skippedWindow, selected: skippedWindow[1] })?.id, "q6");
// 범위 크기는 인자로 바꿀 수 있다
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: long, selected: long[0], requested: new Set([prefetchRequestKey(long[1])]), lookahead: 1 }), null);
assert.equal(pickPrefetchTarget({ ...base, enabled: true, images: long, selected: long[0], requested: windowDone, lookahead: 6 })?.id, "p6");

// 보는 페이지가 목록에 없으면 고르지 않는다
assert.equal(pickPrefetchTarget({ ...base, enabled: true, selected: page("missing", { has_analysis: true }) }), null);

console.log("미리 분석 대상: 꺼짐이면 없음, 뒤 5장 범위·앞쪽으로 돌지 않음·범위 고정·범위 인자, 요청/실패/제외/생성 권장 건너뜀, 보는 페이지 우선 통과");
