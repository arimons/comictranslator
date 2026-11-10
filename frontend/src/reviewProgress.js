// 확인한 페이지 집계: 백그라운드 번역 후보와 진행률 알림 기준을 계산하는 순수 함수.
// App.jsx에서 그대로 옮겼고 proof/src/test_review_progress.mjs가 검사한다. 다른 모듈을 import하지 않는다.
// milestones는 OcrBatchPrompt.jsx의 OCR_PROMPT_MILESTONES(60, 80)다.
export function reviewProgress({ images, ocrReadyImages, reviewedIds, selectedId, milestones }) {
  // 지금 보고 있는 페이지는 아직 편집 중이므로 번역 후보에서 뺀다
  const ocrBatchCandidates = ocrReadyImages.filter((img) => reviewedIds.has(img.id) && img.id !== selectedId);
  // 수동 작업 진행률: 처리 대상 페이지 중 확인한 페이지 비율. 60%, 80%를 넘을 때 한 번씩 묻는다.
  const ocrTargetImages = images.filter((img) => !img.processing_excluded);
  const ocrProgressPct = ocrTargetImages.length
    ? Math.round((ocrTargetImages.filter((img) => reviewedIds.has(img.id)).length / ocrTargetImages.length) * 100)
    : 0;
  const ocrReachedMilestone = Math.max(0, ...milestones.filter((value) => ocrProgressPct >= value));
  // 마지막 페이지에 도착하면 남은 페이지를 모두 번역할지 항상 묻는다(자동 번역·알림 끄기와 상관없이).
  const ocrTargetLastId = ocrTargetImages.length ? ocrTargetImages[ocrTargetImages.length - 1].id : null;
  return { ocrBatchCandidates, ocrTargetImages, ocrProgressPct, ocrReachedMilestone, ocrTargetLastId };
}
