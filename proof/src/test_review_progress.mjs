import assert from "node:assert/strict";
import { loadFrontendModule } from "./frontend_module_loader.mjs";

const { reviewProgress } = await loadFrontendModule("frontend/src/reviewProgress.js");

const milestones = [60, 80];
const page = (id, extra = {}) => ({ id, ...extra });
const images = [page("1"), page("2"), page("3", { processing_excluded: true }), page("4"), page("5"), page("6")];
const ready = [images[0], images[1], images[3]];

// 처리 제외 페이지는 진행률 분모에서 빠진다: 확인 3장 / 대상 5장 = 60%
const half = reviewProgress({ images, ocrReadyImages: ready, reviewedIds: new Set(["1", "2", "4"]), selectedId: "4", milestones });
assert.equal(half.ocrProgressPct, 60);
assert.equal(half.ocrReachedMilestone, 60);
// 지금 보고 있는 페이지(4)는 확인했어도 번역 후보에서 뺀다
assert.deepEqual(half.ocrBatchCandidates.map((img) => img.id), ["1", "2"]);
assert.equal(half.ocrTargetLastId, "6");

// 기준을 넘지 못하면 0, 80%를 넘으면 80
assert.equal(reviewProgress({ images, ocrReadyImages: ready, reviewedIds: new Set(["1"]), selectedId: "", milestones }).ocrReachedMilestone, 0);
assert.equal(reviewProgress({ images, ocrReadyImages: ready, reviewedIds: new Set(["1", "2", "4", "5"]), selectedId: "", milestones }).ocrReachedMilestone, 80);

// 처리 대상 페이지가 없으면 진행률 0, 마지막 페이지 없음
const empty = reviewProgress({ images: [page("x", { processing_excluded: true })], ocrReadyImages: [], reviewedIds: new Set(), selectedId: undefined, milestones });
assert.equal(empty.ocrProgressPct, 0);
assert.equal(empty.ocrTargetLastId, null);

console.log("확인한 페이지 집계: 진행률·알림 기준·보고 있는 페이지 제외·마지막 처리 대상 통과");
