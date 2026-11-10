import assert from "node:assert/strict";
import { loadFrontendModule } from "./frontend_module_loader.mjs";

const { ocrStateForImage, ocrDetailForImage, ocrProgressForImage, isImageGenerationFallback } = await loadFrontendModule("frontend/src/ocrStatus.js");

const none = {};
// 처리 제외 → 건너뜀, 분석 전 → unanalyzed(실패 아님), OCR 시트만 있으면 번역 대기(idle)
assert.equal(ocrStateForImage({ id: "a", processing_excluded: true }, none), "skipped");
assert.equal(ocrStateForImage({ id: "a" }, none), "unanalyzed");
assert.equal(ocrStateForImage({ id: "a", indexed_sheet_url: "s" }, none), "idle");
assert.equal(ocrStateForImage({ id: "a", has_integrated_json: true }, none), "done");
assert.equal(ocrStateForImage({ id: "a", ocr_status: { status: "failed" } }, none), "failed");

// 진행 중인 OCR 상태가 저장된 상태보다 앞선다. 분석 단계 상태는 OCR 판정에 쓰지 않는다
assert.equal(ocrStateForImage({ id: "a", has_integrated_json: true }, { a: { phase: "ocr", status: "running" } }), "running");
assert.equal(ocrStateForImage({ id: "a", indexed_sheet_url: "s" }, { a: { phase: "analysis", status: "failed" } }), "idle");
// 진행 상태가 done이어도 최종 이미지가 없으면 저장된 상태로 판정한다
assert.equal(ocrStateForImage({ id: "a", indexed_sheet_url: "s" }, { a: { phase: "ocr", status: "done" } }), "idle");

assert.equal(ocrDetailForImage({ id: "a" }, { a: { phase: "ocr", detail: "3/5" } }), "3/5");
assert.equal(ocrDetailForImage({ id: "a", indexed_sheet_url: "s" }, none), "ready");
assert.equal(ocrDetailForImage({ id: "a" }, none), "영역 분석을 마치면 번역할 수 있습니다");

// 진행률: 퍼센트 → 그대로, n/m → 비율, 실행 중인데 단서가 없으면 38
assert.equal(ocrProgressForImage({}, "running", "번역 72%"), 72);
assert.equal(ocrProgressForImage({}, "running", "2 / 8"), 25);
assert.equal(ocrProgressForImage({}, "running", ""), 38);
assert.equal(ocrProgressForImage({}, "done", ""), 100);
assert.equal(ocrProgressForImage({}, "queued", "50%"), 0);

assert.equal(isImageGenerationFallback({ export_recovery: { status: "generated" } }), true);
assert.equal(isImageGenerationFallback(null), false);

console.log("OCR·번역 페이지 상태: 판정 우선순위·상세 문구·진행률·생성 대체본 통과");
