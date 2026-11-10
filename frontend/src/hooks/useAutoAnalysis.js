import { useEffect, useRef, useState } from "react";
import { apiStream, imageNeedsAnalysis } from "../lib.js";
import { readStored, writeStored } from "../storage.js";
import { PREFETCH_SETTING_KEY, pickPrefetchTarget, prefetchRequestKey } from "../prefetchPlan.js";

// 영역 분석 탭에서 보고 있는 페이지의 자동 분석과, 남은 페이지 미리 분석(설정, 기본 꺼짐).
// App.jsx에서 그대로 옮겼다. 두 effect는 원래 순서(자동 분석 → 미리 분석)를 지키고,
// App이 이 훅을 부르는 자리가 곧 두 effect가 도는 순서다. 자동 분석 요청 기록(autoAnalysisRequestedRef)은
// 기존 방식 전환·페이지 처리 제외도 쓰므로 App이 갖고 넘겨준다.
export default function useAutoAnalysis({
  workspaceStarted, generationReviewMode, activeTab, selected, sessionId, images,
  inputBusy, analyzeBusy, guidanceBusy, routeBusy, busyTasks,
  autoAnalysisRequestedRef, sessionIdRef, setStatus, analyze, applyImageRecord,
}) {
  // 남은 페이지 미리 분석(기본 꺼짐). 프로젝트를 열거나 페이지를 넘기기만 해도 유료 분석이 나가지 않도록 사용자가 켤 때만 돈다.
  const [prefetchEnabled, setPrefetchEnabled] = useState(() => readStored(PREFETCH_SETTING_KEY, false, (v) => typeof v === "boolean"));
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

  // 보고 있는 페이지 분석이 끝나고 화면 작업이 없을 때, 남은 미분석 페이지를 한 장씩 미리 분석한다.
  // 보던 페이지 뒤부터 끝까지, 이어서 앞쪽 순서로 돈다. 번역(OCR)은 네트워크 작업이라 함께 돌아도 막지 않는다.
  useEffect(() => {
    // 설정이 꺼져 있으면 미리 분석 요청을 새로 보내지 않는다. 이미 진행 중인 한 장은 끝까지 두고 결과만 반영한다.
    if (!prefetchEnabled) return;
    if (!workspaceStarted || generationReviewMode || !sessionId || !selected || prefetchPromiseRef.current) return;
    if (routeBusy || Object.entries(busyTasks).some(([kind, value]) => value && kind !== "export")) return;
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
    setStatus(value ? "남은 페이지 미리 분석을 켰습니다." : `남은 페이지 미리 분석을 껐습니다.${prefetchingId ? " 진행 중인 한 장만 마치고 멈춥니다." : ""}`);
  }

  return { prefetchEnabled, prefetchingId, prefetchPromiseRef, changePrefetchEnabled };
}
