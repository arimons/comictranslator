import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./lib.js";

const modeLabel = { keep: "그대로", center_crop: "가운데 한 장만 (좌우 여백 제외)", split: "오른쪽 → 왼쪽 두 쪽" };

// 분할 위치(오른쪽 조각의 시작 x). 나누지 않는 페이지는 null.
const splitXOf = (page) => (page?.mode === "split" ? page.rects[0][0] : null);

export default function PagePreparation({ sessionId, disabled, onSplit, onOverride, autoOpen = false, label = "가로 이미지 나누기 미리보기", className = "" }) {
  const clipId = useId().replaceAll(":", "");
  const [open, setOpen] = useState(false);
  const [plan, setPlan] = useState(null);
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [includeWorked, setIncludeWorked] = useState(false);
  const opened = useRef(false);
  const trigger = useRef(null);
  const closeRef = useRef(null);
  const wasOpen = useRef(false);
  const sourceSvg = useRef(null);
  // 끌고 있는 동안의 분할선 x. 놓으면 서버에 저장한다.
  const [dragX, setDragX] = useState(null);
  const load = async () => {
    setOpen(true); setBusy(true); setError(""); setPlan(null); setIndex(0); setIncludeWorked(false);
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
      const result = await api("/api/pages/split", { method: "POST", body: JSON.stringify({ session_id: sessionId, include_worked: includeWorked }) });
      await onSplit(result);
      setOpen(false);
      const url = new URL(window.location.href); url.searchParams.delete("page_preview");
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    } catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); }
  };
  const page = plan?.pages[index];
  // 페이지 하나의 방식을 바꾸고 전체 장수를 그 차이만큼 맞춘다(200장 전체 미리보기를 다시 만들지 않는다).
  const saveOverride = async (mode, splitX) => {
    if (!page) return;
    setBusy(true); setError("");
    try {
      const result = await api("/api/pages/override", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: page.image_id, mode, split_x: splitX ?? null }) });
      setPlan((current) => {
        const old = current.pages[index];
        const pages = current.pages.map((item, n) => (n === index ? result.page : item));
        return {
          ...current, pages,
          output_count: current.output_count - old.outputs.length + result.page.outputs.length,
          changed_count: current.changed_count - (old.mode !== "keep") + (result.page.mode !== "keep"),
        };
      });
      onOverride?.();
    } catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); setDragX(null); }
  };
  const svgX = (event) => {
    const svg = sourceSvg.current;
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const x = Math.round(point.matrixTransform(svg.getScreenCTM().inverse()).x);
    return Math.min(page.width - 1, Math.max(1, x));
  };
  const lineDrag = page?.mode === "split" && !busy ? {
    onPointerDown: (event) => { event.currentTarget.setPointerCapture(event.pointerId); setDragX(svgX(event)); },
    onPointerMove: (event) => { if (dragX != null) setDragX(svgX(event)); },
    onPointerUp: (event) => { if (dragX != null) saveOverride("split", svgX(event)); },
  } : {};
  const shownSplitX = dragX ?? splitXOf(page);
  const choice = page?.override?.mode === "keep" ? "keep" : page?.override?.mode === "split" ? "split" : "auto";
  return <>
    <button type="button" ref={trigger} className={className} onClick={load} disabled={!sessionId || disabled} title="가로 이미지가 어떻게 나뉠지 보고, 페이지마다 분할선 위치나 나누지 않기를 정합니다">{label}</button>
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
        <header><div><h2 id="page-prep-title">가로 이미지 나누기 미리보기</h2><p>가로로 두 쪽이 붙은 이미지는 자동으로 오른쪽 → 왼쪽 두 쪽으로 나누고, 표지처럼 가운데에 한 장만 있으면 가운데만 남깁니다. 페이지마다 분할선을 끌어 옮기거나 ‘가로 그대로’를 고를 수 있으며, 고른 내용은 바로 저장돼 분석 시작 때 나누기에도 적용됩니다. 원본 파일은 그대로 둡니다.</p></div><button ref={closeRef} onClick={close} disabled={busy} aria-label="페이지 미리보기 닫기">×</button></header>
        {error && <p className="page-prep-error" role="alert">{error}</p>}
        {!plan && <div className="page-prep-loading">{busy ? "이미지 크기와 여백을 확인하는 중… 외부 API는 호출하지 않습니다." : <button onClick={load}>미리보기 다시 불러오기</button>}</div>}
        {plan && <>
          <div className="page-prep-summary"><strong>원본 {plan.source_count}장 → 정리 후 {plan.output_count}장</strong><span>나눌 이미지 {plan.changed_count}장 · 일본식 읽기 순서</span></div>
          <nav className="page-prep-nav" aria-label="원본 페이지 선택">
            <button disabled={index === 0 || busy} onClick={() => setIndex(index - 1)}>이전</button>
            <select value={index} disabled={busy} aria-label="분할 미리보기 페이지" onChange={(event) => setIndex(Number(event.target.value))}>
              {plan.pages.map((item, n) => <option key={item.image_id} value={n}>{item.source_name} · {modeLabel[item.mode]}{item.override ? " (직접 지정)" : ""}</option>)}
            </select><button disabled={index >= plan.pages.length - 1 || busy} onClick={() => setIndex(index + 1)}>다음</button>
          </nav>
          {(page.width / page.height >= 1.25 || page.override) && <div className="page-prep-modes" role="radiogroup" aria-label="이 페이지 나누기 방식">
            <button role="radio" aria-checked={choice === "auto"} className={choice === "auto" ? "active" : ""} disabled={busy} onClick={() => saveOverride("auto")}>자동 ({modeLabel[page.auto_mode]})</button>
            <button role="radio" aria-checked={choice === "split"} className={choice === "split" ? "active" : ""} disabled={busy} onClick={() => saveOverride("split", splitXOf(page) ?? Math.floor(page.width / 2))}>분할선 직접 지정</button>
            <button role="radio" aria-checked={choice === "keep"} className={choice === "keep" ? "active" : ""} disabled={busy} onClick={() => saveOverride("keep")}>가로 그대로</button>
            {page.mode === "split" && <span className="page-prep-mode-hint">원본 이미지에서 분할선을 끌거나 클릭해 위치를 옮깁니다</span>}
          </div>}
          <div className="page-prep-images">
            <figure className="page-prep-source"><figcaption>원본 · {page.source_name}</figcaption><div><svg ref={sourceSvg} viewBox={`0 0 ${page.width} ${page.height}`} role="img" aria-label="분할 위치가 표시된 원본" className={page.mode === "split" ? "page-prep-draggable" : ""} {...lineDrag}><image href={page.source_url} width={page.width} height={page.height} />{page.mode === "center_crop" && page.outputs.map((item, n) => <rect key={n} x={item.crop_rect[0]} y={item.crop_rect[1]} width={item.crop_rect[2]} height={item.crop_rect[3]} fill="none" stroke="#2563eb" strokeWidth="3" vectorEffect="non-scaling-stroke" />)}{shownSplitX != null && <>
              <rect x={shownSplitX} y="0" width={page.width - shownSplitX} height={page.height} fill="none" stroke="#2563eb" strokeWidth="3" vectorEffect="non-scaling-stroke" />
              <rect x="0" y="0" width={shownSplitX} height={page.height} fill="none" stroke="#0f766e" strokeWidth="3" vectorEffect="non-scaling-stroke" />
              <line x1={shownSplitX} x2={shownSplitX} y1="0" y2={page.height} stroke="#e11d48" strokeWidth="3" strokeDasharray="10 6" vectorEffect="non-scaling-stroke" />
            </>}</svg></div></figure>
            <div className="page-prep-outputs">{page.outputs.map((item, n) => <figure key={item.name}><figcaption>{n + 1}. {item.side === "right" ? "오른쪽" : item.side === "left" ? "왼쪽" : item.side === "center" ? "중앙" : "원본"} · {item.crop_rect[2]} × {item.crop_rect[3]}px</figcaption><div><svg viewBox={item.crop_rect.join(" ")} role="img" aria-label={`${page.source_name} ${n + 1}번째 결과`}><defs><clipPath id={`${clipId}-${n}`}><rect x={item.crop_rect[0]} y={item.crop_rect[1]} width={item.crop_rect[2]} height={item.crop_rect[3]} /></clipPath></defs><image href={page.source_url} width={page.width} height={page.height} clipPath={`url(#${clipId}-${n})`} /></svg></div></figure>)}</div>
          </div>
          <p className="page-prep-reason">{page.reason}</p>
          <footer><p>미리보기와 나누기에는 API 비용이 없습니다. 나눈 페이지를 분석하면 페이지 수만큼 API 호출이 필요할 수 있습니다.</p>{plan.pages.some((item) => item.has_work) && <label className="page-prep-worked"><input type="checkbox" checked={includeWorked} disabled={busy} onChange={(event) => setIncludeWorked(event.target.checked)} /> 분석·편집 흔적이 있는 페이지({plan.pages.filter((item) => item.has_work).length}장)도 나눕니다. 그 페이지의 기존 작업은 새 페이지로 이어지지 않습니다. 기본은 건너뜁니다.</label>}<div><button onClick={close} disabled={busy}>닫기</button><button className="primary" onClick={apply} disabled={busy || !plan.changed_count}>{busy ? "나누는 중…" : "이 프로젝트에 적용"}</button></div></footer>
        </>}
      </section>
    </div>, document.body)}
  </>;
}
