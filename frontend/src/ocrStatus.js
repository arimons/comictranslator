// 글자인식/번역 탭 대기열과 내보내기 탭에서 페이지 상태를 판정하는 순수 함수.
// imageStatuses는 진행 중인 작업이 보내 준 페이지별 상태(App.jsx의 imageStatuses)다.
// App.jsx에서 그대로 옮겼고 proof/src/test_ocr_status.mjs가 검사한다. 다른 모듈을 import하지 않는다.

export function ocrStateForImage(img, imageStatuses) {
  if (img.processing_excluded) return "skipped";
  const liveStatus = imageStatuses[img.id];
  const state = liveStatus?.phase === "ocr" ? liveStatus.status : null;
  if (state === "failed" || state === "running" || state === "queued" || state === "skipped") return state;
  if (state === "done" && img.final_url) return "done";
  const savedState = img.ocr_status?.status;
  if (savedState === "failed" || savedState === "skipped") return savedState;
  if (savedState === "done") return "done";
  if (img.integrated_skipped || img.integrated?.skipped) return "skipped";
  if (img.has_integrated_json) return "done";
  if (img.final_url) return "done";
  // 영역 분석 전인 페이지는 실패가 아니라 아직 손대지 않은 대기열이다. 실패는 OCR·번역 호출이 실제로 실패한 경우만.
  if (!img.indexed_sheet_url) return "unanalyzed";
  return "idle";
}

export function ocrDetailForImage(img, imageStatuses) {
  const liveStatus = imageStatuses[img.id];
  const detail = liveStatus?.phase === "ocr" ? liveStatus.detail : "";
  if (detail) return detail;
  if (img.ocr_status?.detail) return img.ocr_status.detail;
  if (img.ocr_status?.message) return img.ocr_status.message;
  if (img.integrated_skipped || img.integrated?.skipped) return img.integrated_message || img.integrated?.message || "OCR/번역 스킵";
  if (img.has_integrated_json) return "integrated.json 있음";
  if (img.final_url) return "final ready";
  if (img.indexed_sheet_url) return "ready";
  return "영역 분석을 마치면 번역할 수 있습니다";
}

export function ocrProgressForImage(img, state, detail) {
  if (state === "done" || state === "skipped") return 100;
  if (state === "queued" || state === "idle") return 0;
  const text = String(detail || "");
  const percentMatch = text.match(/(\d{1,3})\s*%/);
  if (percentMatch) return Math.max(0, Math.min(100, Number(percentMatch[1])));
  const stepMatch = text.match(/(\d+)\s*\/\s*(\d+)/);
  if (stepMatch) {
    const current = Number(stepMatch[1]);
    const total = Math.max(1, Number(stepMatch[2]));
    return Math.max(0, Math.min(100, Math.round((current / total) * 100)));
  }
  return state === "running" ? 38 : 0;
}

// 내보내기 탭: 최종 출력이 이미지 생성 모델로 만든 대체본인지
export const isImageGenerationFallback = (img) => (
  Boolean(img?.has_image_generation_fallback)
  || img?.export_recovery?.status === "generated"
  || img?.export_recovery?.source === "image_generation_fallback"
);
