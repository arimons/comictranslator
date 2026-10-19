import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./lib.js";

const modeLabel = { keep: "원본 유지", center_crop: "중앙 한 장", split: "오른쪽 → 왼쪽 두 장" };

export default function PagePreparation({ sessionId, disabled, onPrepared, autoOpen = false }) {
  const clipId = useId().replaceAll(":", "");
  const [open, setOpen] = useState(false);
  const [plan, setPlan] = useState(null);
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const opened = useRef(false);
  const trigger = useRef(null);
  const closeRef = useRef(null);
  const wasOpen = useRef(false);
  const load = async () => {
    setOpen(true); setBusy(true); setError(""); setPlan(null); setIndex(0);
    try { setPlan(await api("/api/pages/preview", { method: "POST", body: JSON.stringify({ session_id: sessionId }) })); }
    catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (autoOpen && !opened.current && sessionId) { opened.current = true; load(); } }, [autoOpen, sessionId]);
  useEffect(() => {
    if (open) closeRef.current?.focus();
    else if (wasOpen.current) trigger.current?.focus();
    wasOpen.current = open;
  }, [open]);
  const close = () => {
    if (busy) return;
    setOpen(false);
    const url = new URL(window.location.href); url.searchParams.delete("page_preview");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  };
  const apply = async () => {
    setBusy(true); setError("");
    try {
      const result = await api("/api/pages/prepare", { method: "POST", body: JSON.stringify({ session_id: sessionId, revision: plan.revision }) });
      await onPrepared(result);
      setOpen(false);
      const url = new URL(window.location.href); url.searchParams.delete("page_preview");
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    } catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); }
  };
  const page = plan?.pages[index];
  return <>
    <button type="button" ref={trigger} onClick={load} disabled={!sessionId || disabled}>페이지 정리 미리보기</button>
    {open && createPortal(<div className="page-prep-backdrop">
      <section className="page-prep-dialog" role="dialog" aria-modal="true" aria-labelledby="page-prep-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") close();
          if (event.key === "Tab") {
            const controls = [...event.currentTarget.querySelectorAll('button:not(:disabled),select:not(:disabled),a[href]')];
            const first = controls[0], last = controls.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
          }
        }}>
        <header><div><h2 id="page-prep-title">단일 페이지로 정리</h2><p>원본 프로젝트를 유지하고, 정리한 이미지를 새 프로젝트로 만듭니다.</p></div><button ref={closeRef} onClick={close} disabled={busy} aria-label="페이지 미리보기 닫기">×</button></header>
        {error && <p className="page-prep-error" role="alert">{error}</p>}
        {!plan && <div className="page-prep-loading">{busy ? "이미지 크기와 여백을 확인하는 중… 외부 API는 호출하지 않습니다." : <button onClick={load}>미리보기 다시 불러오기</button>}</div>}
        {plan && <>
          <div className="page-prep-summary"><strong>원본 {plan.source_count}장 → 정리 후 {plan.output_count}장</strong><span>변경 대상 {plan.changed_count}장 · 일본식 읽기 순서</span></div>
          <nav className="page-prep-nav" aria-label="원본 페이지 선택">
            <button disabled={index === 0 || busy} onClick={() => setIndex(index - 1)}>이전</button>
            <select value={index} disabled={busy} aria-label="분할 미리보기 페이지" onChange={(event) => setIndex(Number(event.target.value))}>
              {plan.pages.map((item, n) => <option key={item.image_id} value={n}>{item.source_name} · {modeLabel[item.mode]}</option>)}
            </select><button disabled={index >= plan.pages.length - 1 || busy} onClick={() => setIndex(index + 1)}>다음</button>
          </nav>
          <div className="page-prep-images">
            <figure className="page-prep-source"><figcaption>원본 · {page.source_name}</figcaption><div><svg viewBox={`0 0 ${page.width} ${page.height}`} role="img" aria-label="분할 위치가 표시된 원본"><image href={page.source_url} width={page.width} height={page.height} />{page.mode !== "keep" && page.outputs.map((item, n) => <rect key={n} x={item.crop_rect[0]} y={item.crop_rect[1]} width={item.crop_rect[2]} height={item.crop_rect[3]} fill="none" stroke={n === 0 ? "#2563eb" : "#0f766e"} strokeWidth="3" vectorEffect="non-scaling-stroke" />)}</svg></div></figure>
            <div className="page-prep-outputs">{page.outputs.map((item, n) => <figure key={item.name}><figcaption>{n + 1}. {item.side === "right" ? "오른쪽" : item.side === "left" ? "왼쪽" : item.side === "center" ? "중앙" : "원본"} · {item.crop_rect[2]} × {item.crop_rect[3]}px</figcaption><div><svg viewBox={item.crop_rect.join(" ")} role="img" aria-label={`${page.source_name} ${n + 1}번째 결과`}><defs><clipPath id={`${clipId}-${n}`}><rect x={item.crop_rect[0]} y={item.crop_rect[1]} width={item.crop_rect[2]} height={item.crop_rect[3]} /></clipPath></defs><image href={page.source_url} width={page.width} height={page.height} clipPath={`url(#${clipId}-${n})`} /></svg></div></figure>)}</div>
          </div>
          <p className="page-prep-reason">{page.reason}</p>
          <footer><p>미리보기와 이미지 분할에는 API 비용이 없습니다. 새 분석을 실행하면 나눈 페이지 수만큼 API 호출이 필요할 수 있습니다. 기존 분석·번역은 원본 프로젝트에 보존합니다.</p><div><button onClick={close} disabled={busy}>미리보기 닫기</button><button className="primary" onClick={apply} disabled={busy || !plan.changed_count}>{busy ? "새 프로젝트 생성 중…" : "새 단일 페이지 프로젝트 만들기"}</button></div></footer>
        </>}
      </section>
    </div>, document.body)}
  </>;
}
