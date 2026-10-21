import { useEffect, useState } from "react";
import { api } from "./lib.js";

// 분석 탭: LaMa·Telea 지우기 결과가 아쉬운 페이지를 이미지 편집 모델로 통째로 다시 지운다.
// 결과는 후보로 미리보기에 띄우고, 적용하면 페이지 전체 배경 복원 패치로 저장된다(영역·OCR은 그대로).
const AI_ERASE_ENGINE = "ai-erase";

function fmtUsage(meta) {
  const usage = meta?.usage || {};
  const sec = meta?.timing?.total_sec;
  const cost = usage.estimated_cost_usd;
  return [sec != null ? `${Number(sec).toFixed(0)}초` : null, cost != null ? `약 $${Number(cost).toFixed(3)}` : null].filter(Boolean).join(" · ");
}

export default function AiErasePanel({ sessionId, page, disabled, onCandidate, onApplied, setStatus }) {
  const [job, setJob] = useState(null);
  const [applied, setApplied] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pageKey = page ? `${sessionId}:${page.id}` : "";

  // 페이지가 바뀌면 후보를 비우고, 이미 적용된 AI 지움 패치가 있는지 확인한다.
  useEffect(() => {
    setJob(null);
    setError("");
    onCandidate?.(null);
    if (!sessionId || !page?.id || !page.restore_patch_count) {
      setApplied([]);
      return undefined;
    }
    let cancelled = false;
    api(`/api/restore/info?session_id=${encodeURIComponent(sessionId)}&image_id=${encodeURIComponent(page.id)}`)
      .then((info) => { if (!cancelled) setApplied((info.patches || []).filter((p) => p.engine === AI_ERASE_ENGINE)); })
      .catch(() => { if (!cancelled) setApplied([]); });
    return () => { cancelled = true; };
  }, [pageKey, page?.restore_patch_count]);

  // 진행 중인 작업 상태를 가져온다. 화면은 멈추지 않는다.
  const running = job && (job.status === "queued" || job.status === "running");
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(async () => {
      try {
        const next = await api(`/api/restore/job/${job.job_id}`);
        setJob(next);
        if (next.status === "done") {
          onCandidate?.(next.result?.crop_after_url || null);
          setStatus?.(`AI 글자 지우기 후보가 준비됐습니다 (${fmtUsage(next.meta)}). 미리보기에서 확인 후 적용하세요.`);
        } else if (next.status === "error") {
          setError(next.error || "실패했습니다");
        }
      } catch (exc) {
        setError(String(exc.message || exc));
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.job_id, running]);

  async function start() {
    setError("");
    try {
      const started = await api("/api/restore/ai-erase", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.id }) });
      setJob(started);
      onCandidate?.(null);
      setStatus?.("AI 글자 지우기를 시작했습니다. 20초 안팎 걸립니다.");
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }

  async function apply() {
    if (!job || job.status !== "done") return;
    setBusy(true);
    try {
      let info = await api("/api/restore/accept", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.id, job_id: job.job_id }) });
      // 페이지 전체 패치는 하나만 둔다. 이전 AI 지움 패치는 새 결과로 교체한다.
      for (const old of (info.patches || []).filter((p) => p.engine === AI_ERASE_ENGINE && p.id !== job.job_id)) {
        info = await api("/api/restore/patch/remove", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.id, patch_id: old.id }) });
      }
      setApplied((info.patches || []).filter((p) => p.engine === AI_ERASE_ENGINE));
      setJob(null);
      onCandidate?.(null);
      setStatus?.("AI로 지운 배경을 적용했습니다. 글자 입히기 렌더에 반영됩니다.");
      onApplied?.();
    } catch (exc) {
      setError(String(exc.message || exc));
    } finally {
      setBusy(false);
    }
  }

  function discard() {
    setJob(null);
    onCandidate?.(null);
    setStatus?.("AI 지움 후보를 버렸습니다.");
  }

  async function remove(patchId) {
    setBusy(true);
    try {
      const info = await api("/api/restore/patch/remove", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.id, patch_id: patchId }) });
      setApplied((info.patches || []).filter((p) => p.engine === AI_ERASE_ENGINE));
      setStatus?.("AI 지움을 해제했습니다. 기본 지우기 결과로 돌아갑니다.");
      onApplied?.();
    } catch (exc) {
      setError(String(exc.message || exc));
    } finally {
      setBusy(false);
    }
  }

  if (!page) return null;
  const done = job?.status === "done";
  return (
    <div className="section compact-section ai-erase-section">
      <h2>AI 글자 지우기</h2>
      <p className="hint">마스크 결과가 아쉬운 페이지만 씁니다. 원본 페이지를 이미지 모델로 보내 글자를 지운 배경을 받습니다(20초 안팎, 약 3센트). 영역·OCR은 그대로이고 번역은 글자 입히기로 얹습니다.</p>
      {applied.length ? (
        <div className="ai-erase-applied">
          <span>적용됨{applied[0].accepted_at ? ` · ${applied[0].accepted_at.replace("T", " ").slice(5, 16)}` : ""}</span>
          <button className="danger" onClick={() => remove(applied[0].id)} disabled={busy || running}>해제</button>
        </div>
      ) : null}
      {done ? (
        <>
          <p className="hint">후보: {fmtUsage(job.meta)} · 미리보기의 "AI 지움 후보" 탭에서 확인하세요.</p>
          <div className="side-action-grid">
            <button className="primary" onClick={apply} disabled={busy}>적용</button>
            <button onClick={discard} disabled={busy}>버리기</button>
          </div>
        </>
      ) : (
        <button className="wide" onClick={start} disabled={disabled || running || busy}>
          {running ? "지우는 중…" : applied.length ? "다시 지우기" : "AI로 글자 지우기"}
        </button>
      )}
      {error ? <div className="restore-error">{error}</div> : null}
    </div>
  );
}
