// 다음 페이지 미리 분석에서 다음에 분석할 페이지를 고른다. 유료 분석 요청이 나갈지 여기서 정하므로
// 화면 상태와 떼어 순수 함수로 두고 proof/src/test_prefetch_plan.mjs에서 검사한다.
// 다른 모듈을 import하지 않는다(테스트가 data URL로 불러온다). 분석 필요 여부는 needsAnalysis로 받는다.

export const PREFETCH_SETTING_KEY = "ct.prefetchAnalysis";
// 보고 있는 페이지 뒤로 몇 장까지 미리 분석할지. 앞쪽 페이지로 돌아가지 않는다.
export const PREFETCH_LOOKAHEAD = 5;

export function prefetchRequestKey(image) {
  return `${image.id}:${image.image_sha256 || ""}`;
}

// 생성 권장 페이지는 자동 분석하지 않는다
function skipsAutoAnalysis(image) {
  return image.analysis_route !== "standard" && image.image_generation_recommendation?.priority === "recommended";
}

// 미리 분석할 페이지를 돌려준다. 설정이 꺼져 있거나 고를 페이지가 없으면 null이다.
// requested: 이미 자동 분석을 요청한 키, failed: 미리 분석이 실패한 키 (둘 다 Set)
// lookahead: 보고 있는 페이지 바로 뒤 몇 장 안에서만 고른다. 건너뛴 페이지가 있어도 범위를 늘리지 않는다.
export function pickPrefetchTarget({ enabled, images, selected, requested, failed, needsAnalysis, lookahead = PREFETCH_LOOKAHEAD }) {
  if (!enabled || !selected || !Array.isArray(images)) return null;
  // 보는 페이지 분석을 아직 시도하지 않았으면 그쪽이 먼저다. 시도했다가 실패했으면 멈추지 않고 이어간다.
  if (needsAnalysis(selected) && !skipsAutoAnalysis(selected) && !requested.has(prefetchRequestKey(selected))) return null;
  const start = images.findIndex((img) => img.id === selected.id);
  if (start < 0) return null;
  // 보던 페이지 바로 뒤 lookahead장만 읽는 순서대로 본다
  return images.slice(start + 1, start + 1 + lookahead).find((img) => {
    const key = prefetchRequestKey(img);
    return needsAnalysis(img) && !skipsAutoAnalysis(img) && !requested.has(key) && !failed.has(key);
  }) || null;
}

// ── 등록 직후 미리 분석(홈 준비 화면) ──
// 새 프로젝트에 이미지를 등록하면 편집기에 들어가기 전에 앞쪽 페이지를 한 장씩 분석한다.
// 1쪽은 항상, '다음 페이지 미리 분석'이 켜져 있으면 그 뒤 PREFETCH_LOOKAHEAD장(2〜6쪽)까지다.
// 가로 펼침 후보는 시작할 때 나눌 수 있어 미리 분석하지 않는다(나누면 분석이 헛돈다).

// 분석 대상에서 빠지는 페이지: 처리 제외, 생성 권장, 나누기를 기다리는 가로 펼침 후보
function skipsEarlyAnalysis(image) {
  return Boolean(image.processing_excluded) || skipsAutoAnalysis(image) || Boolean(image.wide_spread_candidate && !image.parent_id);
}

// 등록 직후 미리 분석이 볼 페이지 범위(읽는 순서의 앞쪽 몇 장). 범위 안에서 건너뛸 페이지가 있어도 범위를 늘리지 않는다.
export function earlyAnalysisWindow({ images, prefetchEnabled, lookahead = PREFETCH_LOOKAHEAD }) {
  if (!Array.isArray(images)) return [];
  return images.slice(0, prefetchEnabled ? 1 + lookahead : 1);
}

// 다음에 분석할 페이지. 없으면 null. requested·failed는 prefetchRequestKey의 Set이다.
export function pickEarlyAnalysisTarget({ images, prefetchEnabled, requested, failed, needsAnalysis, lookahead = PREFETCH_LOOKAHEAD }) {
  return earlyAnalysisWindow({ images, prefetchEnabled, lookahead }).find((img) => {
    const key = prefetchRequestKey(img);
    return !skipsEarlyAnalysis(img) && needsAnalysis(img) && !requested.has(key) && !failed.has(key);
  }) || null;
}

// 홈에 보여 줄 진행: total은 범위 안 분석 대상 수, done은 분석 결과가 생긴 수, failed는 실패한 수.
// current는 지금 분석 중인 페이지의 쪽 번호(1부터), 없으면 0이다.
export function earlyAnalysisProgress({ images, prefetchEnabled, failed, needsAnalysis, currentId = "", lookahead = PREFETCH_LOOKAHEAD }) {
  const targets = earlyAnalysisWindow({ images, prefetchEnabled, lookahead }).filter((img) => !skipsEarlyAnalysis(img));
  const done = targets.filter((img) => !needsAnalysis(img)).length;
  const failedCount = targets.filter((img) => needsAnalysis(img) && failed.has(prefetchRequestKey(img))).length;
  const index = currentId ? images.findIndex((img) => img.id === currentId) : -1;
  return { total: targets.length, done, failed: failedCount, current: index >= 0 ? index + 1 : 0 };
}
