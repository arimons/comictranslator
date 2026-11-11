import { useEffect, useRef, useState } from "react";
import { apiStream, imageNeedsAnalysis } from "../lib.js";
import { readStored, writeStored } from "../storage.js";
import { PREFETCH_LOOKAHEAD, PREFETCH_SETTING_KEY, earlyAnalysisProgress, pickEarlyAnalysisTarget, pickPrefetchTarget, prefetchRequestKey } from "../prefetchPlan.js";

// 영역 분석 탭에서 보고 있는 페이지의 자동 분석과, 다음 페이지 미리 분석(설정, 기본 켜짐, 뒤 PREFETCH_LOOKAHEAD장까지).
// App.jsx에서 그대로 옮겼다. 두 effect는 원래 순서(자동 분석 → 미리 분석)를 지키고,
// App이 이 훅을 부르는 자리가 곧 두 effect가 도는 순서다. 자동 분석 요청 기록(autoAnalysisRequestedRef)은
// 기존 방식 전환·페이지 처리 제외도 쓰므로 App이 갖고 넘겨준다.
export default function useAutoAnalysis({
  workspaceStarted, generationReviewMode, activeTab, selected, sessionId, images,
  inputBusy, analyzeBusy, guidanceBusy, routeBusy, busyTasks,
  autoAnalysisRequestedRef, sessionIdRef, taskLocksRef, setStatus, analyze, applyImageRecord,
  analysisPolicy, hydrationPendingRef,
}) {
  // 다음 페이지 미리 분석(기본 켜짐). Cloud Vision 호출만 들어 부담이 적어 보던 페이지 뒤 몇 장을 미리 준비한다.
  // 사용자가 끈 값이 저장돼 있으면 그 값을 따른다.
  const [prefetchEnabled, setPrefetchEnabled] = useState(() => readStored(PREFETCH_SETTING_KEY, true, (v) => typeof v === "boolean"));
  // 다음 페이지 미리 분석: 한 번에 한 장만, 화면을 잠그지 않고 돈다
  const prefetchPromiseRef = useRef(null);
  const prefetchFailedRef = useRef(new Set());
  const [prefetchingId, setPrefetchingId] = useState("");

  useEffect(() => {
    if (
      !workspaceStarted
      || generationReviewMode
      || (selected?.analysis_route !== "standard" && selected?.image_generation_recommendation?.priority === "recommended")
      || activeTab !== "guidance"
      || !selected
      || inputBusy
      || analyzeBusy
      || guidanceBusy
      || !imageNeedsAnalysis(selected)
    ) {
      return;
    }
    const requestKey = `${selected.id}:${selected.image_sha256 || ""}`;
    if (prefetchingId === selected.id) {
      setStatus(`${selected.name} 미리 분석 중입니다. 끝나면 바로 표시됩니다.`);
      return;
    }
    if (autoAnalysisRequestedRef.current.has(requestKey)) return;
    autoAnalysisRequestedRef.current.add(requestKey);
    setStatus(`${selected.name} 분석 산출물이 없어 현재 파일 분석을 시작합니다.`);
    // 분석 결과가 없는 페이지라 Vision 결과 캐시가 있으면 그대로 쓴다
    analyze(selected.id, { force: false });
  }, [workspaceStarted, generationReviewMode, activeTab, selected?.id, selected?.image_sha256, selected?.regions, selected?.has_analysis, selected?.image_generation_recommendation?.priority, selected?.analysis_route, inputBusy, analyzeBusy, guidanceBusy, prefetchingId]);

  // 보고 있는 페이지 분석이 끝나고 화면 작업이 없을 때, 그 뒤 미분석 페이지를 한 장씩 미리 분석한다.
  // 보던 페이지 바로 뒤 PREFETCH_LOOKAHEAD장까지만 보고 앞쪽으로 돌아가지 않는다. 백그라운드 번역은 네트워크 작업이라 함께 돌아도 막지 않는다.
  useEffect(() => {
    // 설정이 꺼져 있으면 미리 분석 요청을 새로 보내지 않는다. 이미 진행 중인 한 장은 끝까지 두고 결과만 반영한다.
    if (!prefetchEnabled) return;
    if (!workspaceStarted || generationReviewMode || !sessionId || !selected || prefetchPromiseRef.current) return;
    if (routeBusy || Object.entries(busyTasks).some(([kind, value]) => value && kind !== "export")) return;
    // 같은 커밋에서 바로 앞 자동 분석 effect가 보는 페이지 분석을 막 시작했으면 busyTasks는 아직 이전 값이다.
    // 잠금(taskLocksRef)은 바로 바뀌므로 그것도 보고, 분석 요청이 동시에 두 개 나가지 않게 한다.
    if ([...taskLocksRef.current].some((kind) => kind !== "export")) return;
    const target = pickPrefetchTarget({
      enabled: prefetchEnabled,
      images,
      selected,
      requested: autoAnalysisRequestedRef.current,
      failed: prefetchFailedRef.current,
      needsAnalysis: imageNeedsAnalysis,
    });
    if (target) prefetchAnalysis(target);
  }, [prefetchEnabled, workspaceStarted, generationReviewMode, sessionId, selected?.id, selected?.regions, selected?.has_analysis, images, routeBusy, busyTasks, prefetchingId]);

  // 등록 직후 미리 분석: 방금 만든 프로젝트(earlySessionId)에서만, 홈 준비 화면에 있는 동안 앞쪽 페이지를 한 장씩 분석한다.
  // 기존 프로젝트를 열 때는 켜지지 않는다. 편집기에 들어가면 끄고, 그 뒤는 위의 자동 분석·미리 분석이 이어받는다.
  // 요청은 미리 분석과 같은 한 장 잠금(prefetchPromiseRef)을 써서 /api/analyze-stream이 동시에 둘 나가지 않는다.
  // '처음부터 다시 분석'을 고르면 새 요청을 보내지 않는다(진행 중인 한 장만 끝난다). 시작할 때 전체를 강제로 다시 분석한다.
  const [earlySessionId, setEarlySessionId] = useState("");
  function startEarlyAnalysis(sid) {
    setEarlySessionId(sid || "");
  }
  useEffect(() => {
    if (workspaceStarted && earlySessionId) setEarlySessionId("");
  }, [workspaceStarted, earlySessionId]);
  const earlyActive = Boolean(earlySessionId) && !workspaceStarted && !generationReviewMode && sessionId === earlySessionId;
  useEffect(() => {
    if (!earlyActive || analysisPolicy === "refresh" || prefetchPromiseRef.current) return;
    // 업로드 직후 서버가 이전 분석 캐시를 연결하는 동안은 기다린다(캐시가 있으면 Cloud Vision을 다시 부르지 않게)
    if (hydrationPendingRef?.current) return;
    if (routeBusy || Object.entries(busyTasks).some(([kind, value]) => value && kind !== "export")) return;
    if ([...taskLocksRef.current].some((kind) => kind !== "export")) return;
    const target = pickEarlyAnalysisTarget({
      images,
      prefetchEnabled,
      requested: autoAnalysisRequestedRef.current,
      failed: prefetchFailedRef.current,
      needsAnalysis: imageNeedsAnalysis,
    });
    if (target) prefetchAnalysis(target);
  }, [earlyActive, analysisPolicy, prefetchEnabled, images, routeBusy, busyTasks, prefetchingId]);
  const earlyProgress = earlyActive ? {
    ...earlyAnalysisProgress({ images, prefetchEnabled, failed: prefetchFailedRef.current, needsAnalysis: imageNeedsAnalysis, currentId: prefetchingId }),
    paused: analysisPolicy === "refresh",
  } : null;

  function prefetchAnalysis(target) {
    const sid = sessionId;
    const key = prefetchRequestKey(target);
    // 같은 페이지를 열어도 자동 분석이 겹쳐 돌지 않게 요청 기록을 먼저 남긴다
    autoAnalysisRequestedRef.current.add(key);
    setPrefetchingId(target.id);
    const run = apiStream(
      "/api/analyze-stream",
      { method: "POST", body: JSON.stringify({ session_id: sid, image_id: target.id, force: false, max_workers: 1 }) },
      (event) => {
        if (sessionIdRef.current !== sid) return;
        if (event.type === "image_status" && event.image) applyImageRecord(event.image);
        if (event.type === "error") throw new Error(event.message);
      },
    ).catch(() => {
      // 실패한 페이지는 미리 분석에서 빼고, 사용자가 열면 평소처럼 그 자리에서 분석한다
      autoAnalysisRequestedRef.current.delete(key);
      prefetchFailedRef.current.add(key);
    }).finally(() => {
      prefetchPromiseRef.current = null;
      setPrefetchingId("");
    });
    prefetchPromiseRef.current = run;
  }

  function changePrefetchEnabled(value) {
    setPrefetchEnabled(value);
    writeStored(PREFETCH_SETTING_KEY, value);
    setStatus(value ? `다음 페이지 미리 분석을 켰습니다. 보고 있는 페이지 뒤 ${PREFETCH_LOOKAHEAD}장까지 미리 분석합니다.` : `다음 페이지 미리 분석을 껐습니다.${prefetchingId ? " 진행 중인 한 장만 마치고 멈춥니다." : ""}`);
  }

  return { prefetchEnabled, prefetchingId, prefetchPromiseRef, changePrefetchEnabled, startEarlyAnalysis, earlyProgress };
}
