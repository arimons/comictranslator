import { useEffect, useRef, useState } from "react";
import { api } from "./lib.js";
import ImagePreview from "./ImagePreview.jsx";

export default function ImageGenerationPanel({ sessionId, image, onStatus }) {
  const [data, setData] = useState(null);
  const dialogRef = useRef(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [resultId, setResultId] = useState("");
  useEffect(() => {
    if (!sessionId || !image?.id || image.processing_excluded) return;
    let alive = true;
    let timer;
    async function read() {
      try {
        const next = await api(`/api/image-generation/${encodeURIComponent(sessionId)}/${encodeURIComponent(image.id)}`);
        if (alive) { setData(next); onStatus?.(next); setError(""); }
      } catch (err) { if (alive) setError(err.message); }
      if (alive) timer = window.setTimeout(read, 5000);
    }
    read();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [sessionId, image?.id, image?.processing_excluded, onStatus]);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    function escape(event) { if (event.key === "Escape") setOpen(false); }
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("keydown", escape); previous?.isConnected && previous.focus(); };
  }, [open]);
  if (!image || image.processing_excluded) return null;
  const recommendation = data?.recommendation;
  const results = data?.results || [];
  const result = results.find((item) => item.result_id === resultId) || results.at(-1);
  const label = recommendation?.priority === "recommended" ? "이미지 생성 권장" : recommendation?.priority === "optional" ? "이미지 생성 선택" : "이미지 생성 검토";
  const instruction = `이 프로젝트의 페이지를 Codex 내장 이미지 생성으로 한국어 번역하고, 생성 결과를 미검토 후보로 등록해줘. 원본과 기존 출력은 유지해줘.\nsession_id: ${sessionId}\nimage_id: ${image.id}\ninspect_page → prepare_image_generation → 내장 이미지 생성 → register_generated_image 순서로 진행해줘.`;
  return <div className="image-generation-bar">
    <button type="button" className={`generation-badge ${recommendation?.priority || "neutral"}`} onClick={() => setOpen(true)} title={recommendation?.reason || "자동 실행 없이 생성 요청과 결과를 확인합니다"}>{label}{results.length ? ` · 후보 ${results.length}` : ""}</button>
    <span>{recommendation?.stale ? "이전 판단 이후 페이지가 변경됨 · 다시 확인 필요" : recommendation?.reason || "필요한 페이지에만 생성 요청을 할 수 있습니다."}</span>
    {error ? <span role="status">연결 확인 필요: {error}</span> : null}
    {open ? <div className="generation-dialog-backdrop" onClick={() => setOpen(false)}><section role="dialog" aria-modal="true" aria-label="이미지 생성 검토" ref={dialogRef} className="generation-dialog" onKeyDown={(event) => {
      if (event.key === "Escape") { event.stopPropagation(); setOpen(false); }
      if (event.key === "Tab") {
        const focusable = Array.from(dialogRef.current.querySelectorAll('button,select,textarea,summary,a[href]')).filter(node => !node.disabled && node.getClientRects().length);
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }} onClick={(event) => event.stopPropagation()}>
      <div className="card-head"><strong>{image.name} · {label}</strong><button autoFocus onClick={() => setOpen(false)}>닫기</button></div>
      <p>{recommendation?.reason || "아직 이 페이지의 생성 권장 판단이 저장되지 않았습니다."}</p>
      <p>자동 생성하지 않습니다. 아래 요청을 Codex 대화에 전달하면 MCP로 자료를 준비하고 내장 생성 도구를 사용합니다. 생성 후보는 최종 출력에 자동 반영되지 않습니다.</p>
      <details><summary>Codex에 보낼 작업 요청</summary><textarea aria-label="Codex 이미지 생성 요청" readOnly value={instruction} onFocus={(event) => event.target.select()} /></details>
      {results.length ? <>
        <label>등록된 생성 후보 <select value={result?.result_id || ""} onChange={(event) => setResultId(event.target.value)}>{results.map((item) => <option key={item.result_id} value={item.result_id}>{item.created_at} · {item.size.join("×")}</option>)}</select></label>
        <p role="status">{result?.stale ? "입력·번역·용어집 변경됨 — 다시 검토 필요" : "미검토 후보 · 최종 출력 미반영"}{result?.aspect_ratio_warning ? " · 원본과 비율 차이가 있어 확인이 필요합니다." : ""}</p>
        <div className="generation-compare"><section><h3>원본</h3><ImagePreview src={image.input_url} /></section><section><h3>생성 후보</h3><ImagePreview src={result?.preview_url} /></section></div>
      </> : <p>아직 등록된 생성 결과가 없습니다.</p>}
    </section></div> : null}
  </div>;
}
