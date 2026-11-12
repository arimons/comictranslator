import { useEffect, useRef, useState } from "react";
import { api } from "./lib.js";

// 분석 탭: LaMa·Telea 지우기 결과가 아쉬운 곳을 이미지 편집 모델로 다시 지운다.
// 영역(R)을 선택해 두었으면 그 영역 둘레만 보내 그 영역만 지우고(영역 모드), 아니면 페이지 전체를 보낸다.
// 결과는 후보로 미리보기에 띄우고, 적용하면 배경 복원 패치로 저장된다(영역·OCR은 그대로).
const AI_ERASE_ENGINE = "ai-erase";

// 이미지 모델 이름으로 어느 회사 API로 나가는지 알려 준다. 번역 경로(예: Vertex)와 달라질 수 있어 화면에 밝혀 둔다.
function providerOf(imageModel) {
  return String(imageModel || "").startsWith("gemini") ? "Google Gemini" : "OpenAI";
}

function patchLabel(patch) {
  if (patch.region_id != null) return `영역 R${patch.region_id}`;
  if (patch.scope === "regions") return patch.sfx_count > 0 ? `쪽 전체 · 대화 영역 + 효과음 ${patch.sfx_count}개` : "쪽 전체 · 대화 영역만";
  return "쪽 전체 · 모든 글자";
}

function fmtUsage(meta) {
  const usage = meta?.usage || {};
  const sec = meta?.timing?.total_sec;
  const cost = usage.estimated_cost_usd;
  return [sec != null ? `${Number(sec).toFixed(0)}초` : null, cost != null ? `약 $${Number(cost).toFixed(3)}` : null].filter(Boolean).join(" · ");
}

export default function AiErasePanel({ sessionId, page, region, disabled, onCandidate, onApplied, onPatches, onSfxFound, setStatus, ocrModel, imageModel, confirmOpen, onConfirmClose, onRequestConfirm }) {
  const [job, setJob] = useState(null);
  const [applied, setApplied] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // 카드의 X로 닫아 둔 상태. 새 작업이 시작되거나 적용된 목록이 바뀌면 다시 보인다.
  const [dismissed, setDismissed] = useState(false);
  // 쪽 전체 모드에서 지울 범위: regions_sfx=대화 영역 + 효과음 찾기(효과음 위치도 함께 찾아 지우고 영역 후보로 추가),
  // regions=영역(R) 안의 글자만(효과음 등은 원본 유지), page=쪽 전체 글자
  const [scope, setScope] = useState("regions_sfx");
  const pageKey = page ? `${sessionId}:${page.id}` : "";

  // 적용된 AI 지움 목록을 화면의 영역 정보 패널이 볼 수 있게 알린다(영역 옵션의 "AI" 선택 상태 표시용).
  useEffect(() => { onPatches?.(applied); setDismissed(false); }, [applied]);

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
          const sfxNote = next.meta?.sfx_error ? ` 효과음 찾기는 실패했습니다(${next.meta.sfx_error}).` : next.meta?.sfx_count ? ` 효과음 ${next.meta.sfx_count}개를 찾아 함께 지웠습니다.` : "";
          setStatus?.(`AI 글자 지우기 후보가 준비됐습니다 (${fmtUsage(next.meta)}).${sfxNote} 미리보기에서 확인 후 적용하세요.`);
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
      const regionId = region?.id ?? null;
      const started = await api("/api/restore/ai-erase", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.id, ocr_model: ocrModel || null, region_id: regionId, scope: regionId == null ? (scope === "regions_sfx" ? "regions" : scope) : "page", include_sfx: regionId == null && scope === "regions_sfx" }) });
      setJob(started);
      setDismissed(false);
      onCandidate?.(null);
      setStatus?.(regionId != null ? `R${regionId} 영역만 AI로 지웁니다. 20초 안팎 걸립니다.` : "AI 글자 지우기를 시작했습니다. 20초 안팎 걸립니다.");
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }

  async function apply() {
    if (!job || job.status !== "done") return;
    setBusy(true);
    try {
      let info = await api("/api/restore/accept", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.id, job_id: job.job_id }) });
      // 쪽 전체 패치는 하나만 둔다. 쪽 전체 결과를 적용할 때만 이전 쪽 전체 패치를 교체한다.
      // 영역 패치는 서버가 같은 영역 것만 교체하므로 다른 영역·쪽 전체 패치는 건드리지 않는다.
      if (job.meta?.region_id == null) {
        for (const old of (info.patches || []).filter((p) => p.engine === AI_ERASE_ENGINE && p.id !== job.job_id && p.region_id == null)) {
          info = await api("/api/restore/patch/remove", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.id, patch_id: old.id }) });
        }
      }
      setApplied((info.patches || []).filter((p) => p.engine === AI_ERASE_ENGINE));
      setJob(null);
      onCandidate?.(null);
      setStatus?.(job.meta?.region_id != null ? `R${job.meta.region_id} 영역의 AI 지움을 적용했습니다. 글자 입히기 렌더에 반영됩니다.` : "AI로 지운 배경을 적용했습니다. 글자 입히기 렌더에 반영됩니다.");
      onApplied?.();
      // 함께 찾은 효과음은 텍스트 기반 영역 후보로 가이드에 추가한다(변경사항 적용을 눌러야 영역이 만들어진다).
      if (job.meta?.sfx_items?.length) onSfxFound?.(job.meta.sfx_items);
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
      setStatus?.("AI 지움을 되돌렸습니다. 기본 지우기 결과로 돌아갑니다.");
      onApplied?.();
    } catch (exc) {
      setError(String(exc.message || exc));
    } finally {
      setBusy(false);
    }
  }

  // 확인 창: 비용이 드는 작업이라 한 번 더 묻는다. 기본 포커스는 취소에 둔다.
  const cancelRef = useRef(null);
  useEffect(() => {
    if (!confirmOpen) return undefined;
    cancelRef.current?.focus();
    const onKey = (event) => { if (event.key === "Escape") { event.stopPropagation(); onConfirmClose?.(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [confirmOpen]);

  async function confirmStart() {
    onConfirmClose?.();
    await start();
  }

  if (!page) return null;
  const done = job?.status === "done";
  // 대기 상태에서는 아무것도 그리지 않는다. 시작은 도구 모음의 AI 지우개에서 한다.
  const showCard = Boolean(running || done || error || (applied.length && !dismissed));
  return (
    <>
      {showCard ? (
        <div className="section compact-section ai-erase-section">
          <div className="ai-erase-head">
            <h2>AI 글자 지우기</h2>
            {!running ? <button className="icon-button ai-erase-close" onClick={() => { setDismissed(true); setError(""); }} aria-label="이 카드 닫기" title="이 카드를 닫습니다(적용된 AI 지움은 그대로 유지됩니다)">×</button> : null}
          </div>
          {running ? <p className="hint">지우는 중… 20초 안팎 걸립니다.</p> : null}
          {applied.map((patch) => (
            <div className="ai-erase-applied" key={patch.id}>
              <span>{patchLabel(patch)} 적용됨{patch.accepted_at ? ` · ${patch.accepted_at.replace("T", " ").slice(5, 16)}` : ""}</span>
              <button className="danger" onClick={() => remove(patch.id)} disabled={busy || running}
                title="이 AI 지움을 취소하고 기본 지우기 결과(LaMa 등)로 되돌립니다">되돌리기</button>
            </div>
          ))}
          {done ? (
            <>
              <p className="hint">후보: {fmtUsage(job.meta)} · 미리보기의 "AI 지움 후보" 탭에서 확인하세요.</p>
              <div className="side-action-grid">
                <button className="primary" onClick={apply} disabled={busy}>적용</button>
                <button onClick={discard} disabled={busy}>버리기</button>
              </div>
            </>
          ) : null}
          {error ? <div className="restore-error">{error}</div> : null}
        </div>
      ) : null}
      {confirmOpen ? (
        <div className="ai-erase-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onConfirmClose?.(); }}>
          <div className="ai-erase-dialog" role="dialog" aria-modal="true" aria-labelledby="ai-erase-title">
            <h2 id="ai-erase-title">{region ? `R${region.id} 영역을 AI로 지울까요?` : "AI로 글자를 지울까요?"}</h2>
            <p>{region
              ? "마스크 결과가 아쉬운 영역에만 쓰세요. 선택한 영역 둘레만 이미지 모델로 보내 글자를 지운 배경을 받고, 글자 획 주변에만 합성합니다. 다른 영역은 다시 보내거나 바꾸지 않습니다."
              : "마스크 결과가 아쉬운 페이지에만 쓰세요. 원본 페이지를 이미지 모델로 보내 글자를 지운 배경을 받습니다. 영역과 OCR은 그대로이고, 번역은 글자 입히기에서 얹습니다."}</p>
            <dl className="ai-erase-facts">
              <div><dt>경로</dt><dd>{providerOf(imageModel)}<small> {imageModel || "-"} · 번역 경로와 따로 정해집니다</small></dd></div>
              <div><dt>시간</dt><dd>20초 안팎</dd></div>
              <div><dt>비용</dt><dd>{region ? "한 영역에 약 2센트" : "한 장에 약 3~5센트"} (외부 API 요금이 발생합니다)</dd></div>
            </dl>
            {!region ? (
              <fieldset className="ai-erase-scope">
                <legend>지울 범위</legend>
                <label><input type="radio" name="ai-erase-scope" checked={scope === "regions_sfx"} onChange={() => setScope("regions_sfx")} /> 대화 영역 + 효과음 <small>대화 영역을 지우고, 같은 요청에서 그림 위 효과음의 위치도 찾아 함께 지웁니다. 찾은 효과음은 영역 후보로 추가되어 글자 입히기에서 번역을 올릴 수 있습니다. 위치 찾기에 모델 호출이 한 번 더 듭니다(센트 미만).</small></label>
                <label><input type="radio" name="ai-erase-scope" checked={scope === "regions"} onChange={() => setScope("regions")} /> 대화 영역만 <small>영역(R) 안의 글자만 지웁니다. 영역 밖의 효과음·그림 속 글자는 원본 그대로 둡니다.</small></label>
                <label><input type="radio" name="ai-erase-scope" checked={scope === "page"} onChange={() => setScope("page")} /> 쪽 전체 글자 <small>효과음을 포함해 모델이 찾은 모든 글자를 지웁니다.</small></label>
              </fieldset>
            ) : null}
            {applied.some((p) => region ? p.region_id === region.id : p.region_id == null) ? <p className="ai-erase-note">{region ? "이 영역에 이미 적용된 AI 지움이 있습니다. 새 결과를 적용하면 교체됩니다." : "이미 적용된 쪽 전체 AI 지움이 있습니다. 새 결과를 적용하면 교체됩니다."}</p> : null}
            <div className="ai-erase-actions">
              <button ref={cancelRef} onClick={() => onConfirmClose?.()}>취소</button>
              <button className="primary" onClick={confirmStart} disabled={disabled || running || busy}>{region ? `R${region.id}만 AI로 지우기` : "AI로 글자 지우기"}</button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
