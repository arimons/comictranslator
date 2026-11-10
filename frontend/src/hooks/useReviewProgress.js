import { useEffect, useRef, useState } from "react";
import { OCR_PROMPT_MILESTONES } from "../OcrBatchPrompt.jsx";
import { readStored, writeStored } from "../storage.js";
import { reviewProgress } from "../reviewProgress.js";

// 확인한 페이지 집계와 백그라운드 번역 알림, 대사 검수 상태(세션별로 브라우저에 기억).
// App.jsx에서 그대로 옮겼다. effect 네 개(세션 값 읽기 → 확인 기록 → 마지막 페이지 알림 → 자동 번역)는
// 원래 순서대로 App이 이 훅을 부르는 자리에서 돈다. 번역 실행(startBackgroundOcr)은 App이 넘긴다.
export default function useReviewProgress({
  sessionId, selected, activeTab, guidanceDirty, images, ocrReadyImages, bgOcr, ocrBusy, startBackgroundOcr,
}) {
  // 확인한 페이지(영역 분석 탭에서 보고 넘어간 페이지)가 쌓이면 번역을 백그라운드로 돌릴지 묻는다.
  const [reviewedIds, setReviewedIds] = useState(() => new Set());
  const [ocrPromptOff, setOcrPromptOff] = useState(() => readStored("ct.ocrPromptOff", false, (v) => typeof v === "boolean"));
  // 알림을 이미 처리한 가장 높은 진행률 기준(0, 60, 80). 세션별로 기억한다.
  const [ocrMilestone, setOcrMilestone] = useState(0);
  // 대사 검수 상태: `${페이지 id}|${overlay 키}` -> { status: "ok"|"ask", text }. text는 검수 당시 번역문이라 번역이 바뀌면 미검수로 돌아간다.
  const [dialogueReview, setDialogueReview] = useState({});
  // 프로젝트별 '앞으로 묻지 않고 번역' 표시. LLM 비용이 드는 일이라 다른 프로젝트로 번지지 않게 세션 단위로 둔다.
  const [ocrAutoStart, setOcrAutoStart] = useState(false);
  const ocrAutoStartingRef = useRef(false);
  const prevPageRef = useRef({ id: null, ready: false });

  // 확인한 페이지: 영역 분석 탭에서 분석이 끝난 페이지를 적용하지 않은 변경 없이 넘기면 센다.
  // 세션별로 브라우저에 기억해 새로고침해도 이어진다. 저장소를 못 쓰면 이번 화면에서만 센다.
  useEffect(() => {
    setReviewedIds(new Set(readStored(`ct.reviewed.${sessionId || ""}`, [], Array.isArray)));
    setOcrAutoStart(Boolean(sessionId) && readStored(`ct.ocrAutoStart.${sessionId}`, false, (v) => typeof v === "boolean"));
    setDialogueReview(readStored(`ct.dialogueReview.${sessionId || ""}`, {}, (v) => v && typeof v === "object" && !Array.isArray(v)));
    setOcrMilestone(Number(readStored(`ct.ocrMilestone.${sessionId || ""}`, 0, (v) => OCR_PROMPT_MILESTONES.includes(v) || v === 0)) || 0);
  }, [sessionId]);
  useEffect(() => {
    const prev = prevPageRef.current;
    if (prev.id && prev.sid === sessionId && prev.id !== selected?.id && prev.ready) {
      setReviewedIds((current) => {
        if (current.has(prev.id)) return current;
        const next = new Set(current).add(prev.id);
        writeStored(`ct.reviewed.${sessionId || ""}`, [...next]);
        return next;
      });
    }
    prevPageRef.current = {
      sid: sessionId,
      id: selected?.id || null,
      ready: Boolean(activeTab === "guidance" && selected?.detail_loaded && selected?.indexed_sheet_url && !selected?.processing_excluded && !guidanceDirty),
    };
  });
  const { ocrBatchCandidates, ocrTargetImages, ocrReachedMilestone, ocrTargetLastId } = reviewProgress({
    images, ocrReadyImages, reviewedIds, selectedId: selected?.id, milestones: OCR_PROMPT_MILESTONES,
  });
  const showOcrPrompt = !ocrPromptOff && !bgOcr.running && !ocrBusy
    && ocrReachedMilestone > ocrMilestone && ocrBatchCandidates.length > 0;
  const [lastPagePrompt, setLastPagePrompt] = useState(null);
  const lastPagePromptForRef = useRef(null);
  useEffect(() => {
    if (!selected?.id || selected.id !== ocrTargetLastId) { lastPagePromptForRef.current = null; setLastPagePrompt(null); return; }
    if (lastPagePromptForRef.current === `${sessionId}:${selected.id}` || bgOcr.running || ocrBusy) return;
    const restIds = ocrReadyImages.filter((img) => img.id !== selected.id).map((img) => img.id);
    const selfReady = ocrReadyImages.some((img) => img.id === selected.id);
    if (!restIds.length && !selfReady) return;
    lastPagePromptForRef.current = `${sessionId}:${selected.id}`;
    setLastPagePrompt({ restIds, selfId: selfReady ? selected.id : null, unanalyzed: ocrTargetImages.filter((img) => !img.indexed_sheet_url).length });
  }, [selected?.id, ocrTargetLastId, images, bgOcr.running, ocrBusy]);
  function translateRemainingFromLastPage(includeSelf) {
    const prompt = lastPagePrompt;
    setLastPagePrompt(null);
    if (!prompt) return;
    const ids = [...prompt.restIds, ...(includeSelf && prompt.selfId ? [prompt.selfId] : [])];
    if (ids.length) startBackgroundOcr(ids);
  }
  useEffect(() => {
    if (!ocrAutoStart || !showOcrPrompt || ocrAutoStartingRef.current) return;
    ocrAutoStartingRef.current = true;
    startBackgroundOcr().finally(() => { ocrAutoStartingRef.current = false; });
  }, [ocrAutoStart, showOcrPrompt]);

  function dismissOcrPrompt() {
    // 지금 넘은 기준까지 처리한 것으로 두고, 다음 기준(80%)에서 다시 묻는다
    markOcrMilestone(ocrReachedMilestone);
  }

  function markOcrMilestone(value) {
    setOcrMilestone(value);
    if (sessionId) writeStored(`ct.ocrMilestone.${sessionId}`, value);
  }

  function setProjectOcrAutoStart(value) {
    setOcrAutoStart(value);
    if (sessionId) writeStored(`ct.ocrAutoStart.${sessionId}`, value);
  }

  function changeOcrPromptOff(value) {
    setOcrPromptOff(value);
    writeStored("ct.ocrPromptOff", value);
  }

  return {
    dialogueReview, setDialogueReview, ocrPromptOff, ocrAutoStart, lastPagePrompt, setLastPagePrompt,
    ocrBatchCandidates, ocrReachedMilestone, showOcrPrompt,
    translateRemainingFromLastPage, dismissOcrPrompt, markOcrMilestone, setProjectOcrAutoStart, changeOcrPromptOff,
  };
}
