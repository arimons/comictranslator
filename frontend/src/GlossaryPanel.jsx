import { useEffect, useRef, useState } from "react";
import { api } from "./lib.js";

const STATUS = { idle: "조사 대기", researching: "웹 조사 중", ready: "사용 준비 완료", failed: "조사 실패" };
const TERM_STATUS = { auto: "자동 적용", accepted: "수동 확정", rejected: "제외", review: "검토 필요" };

function Term({ entry, busy, onReview }) {
  const [target, setTarget] = useState(entry.target);
  useEffect(() => setTarget(entry.target), [entry.target]);
  return <article className={`glossary-term ${entry.status}`}>
    <div className="glossary-term-heading"><strong>{entry.source}</strong><span>{entry.kind} · {TERM_STATUS[entry.status]}</span></div>
    <div className="row">
      <input aria-label={`${entry.source} 한국어 표기`} value={target} maxLength={120} onChange={(event) => setTarget(event.target.value)} />
      <button onClick={() => onReview(entry.id, "accepted", target)} disabled={busy || !target.trim()}>적용</button>
      <button onClick={() => onReview(entry.id, "rejected")} disabled={busy || entry.status === "rejected"}>제외</button>
    </div>
    <p>{entry.reason}{entry.note ? ` · ${entry.note}` : ""}</p>
    {entry.evidence?.length ? <details><summary>검색 인용 근거 {entry.evidence.length}개</summary>
      {entry.evidence.map((cite, index) => <div className="glossary-citation" key={`${cite.url}-${index}`}>
        <a href={cite.url} target="_blank" rel="noopener noreferrer">{cite.title}</a><p>{cite.text}</p>
      </div>)}
    </details> : <p>원문·한국어 표기를 함께 뒷받침하는 검색 인용이 없습니다.</p>}
  </article>;
}

export default function GlossaryPanel({ sessionId, projectName, ocrBusy }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [works, setWorks] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [edition, setEdition] = useState("");
  const [savedId, setSavedId] = useState("");
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sessionRef = useRef(sessionId);
  const generation = useRef(0);
  const dialogRef = useRef(null);
  sessionRef.current = sessionId;

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    dialogRef.current?.querySelector("button")?.focus();
    return () => previous?.isConnected && previous.focus();
  }, [open]);

  useEffect(() => {
    const token = ++generation.current;
    setData(null); setLoaded(false); setError(""); setChanging(false); setSavedId("");
    setAuthor(""); setEdition(""); setTitle(""); setOpen(false); setBusy(false);
    if (!sessionId) return;
    api(`/api/glossary/${encodeURIComponent(sessionId)}`).then((result) => {
      if (token !== generation.current) return;
      setData(result.glossary); setWorks(result.works || []); setLoaded(true);
      setOpen(!result.glossary);
    }).catch((err) => { if (token === generation.current) { setError(err.message); setLoaded(true); } });
    return () => { generation.current += 1; };
  }, [sessionId]);

  useEffect(() => {
    if (!open || !sessionId) return;
    let alive = true;
    api(`/api/glossary/${encodeURIComponent(sessionId)}`).then((result) => {
      if (alive) { setData(result.glossary); setWorks(result.works || []); }
    }).catch((err) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, [open, sessionId]);

  useEffect(() => {
    if (data?.status !== "researching") return;
    let alive = true;
    let timer;
    async function poll() {
      try {
        const result = await api(`/api/glossary/${encodeURIComponent(sessionId)}`);
        if (!alive) return;
        setData(result.glossary); setWorks(result.works || []); setError("");
        if (result.glossary?.status === "researching") timer = setTimeout(poll, 2500);
      } catch (err) {
        if (alive) { setError(err.message); timer = setTimeout(poll, 5000); }
      }
    }
    timer = setTimeout(poll, 2000);
    return () => { alive = false; clearTimeout(timer); };
  }, [sessionId, data?.status]);

  async function post(path, body) {
    const sid = sessionId;
    const token = generation.current;
    setBusy(true); setError("");
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify({ session_id: sid, ...body }) });
      if (sessionRef.current !== sid || generation.current !== token) return;
      setData(result.glossary);
      if (result.works) setWorks(result.works);
      setChanging(false);
    } catch (err) {
      if (sessionRef.current === sid && generation.current === token) setError(err.message);
    } finally {
      if (sessionRef.current === sid && generation.current === token) setBusy(false);
    }
  }

  const researching = data?.status === "researching";
  const entries = data?.entries || [];
  const review = entries.filter((entry) => entry.status === "review");
  const applied = entries.filter((entry) => ["auto", "accepted"].includes(entry.status));
  const excluded = entries.filter((entry) => entry.status === "rejected");
  const disabled = busy || ocrBusy || researching;
  const renderTerm = (entry) => <Term key={entry.id} entry={entry} busy={disabled} onReview={(id, decision, target) => post("/api/glossary/review", { work_id: data.work_id, entry_id: id, decision, target })} />;

  return <>
    <button className="glossary-toggle" onClick={() => setOpen(true)} disabled={!sessionId}>
      {researching ? "용어집 조사 중…" : !data ? "작품 확인 / 용어집" : `용어집 · 적용 ${applied.length}${review.length ? ` · 검토 ${review.length}` : ""}`}
    </button>
    {open && <div className="glossary-backdrop" onClick={() => setOpen(false)}>
      <section ref={dialogRef} className="glossary-dialog" role="dialog" aria-modal="true" aria-label="작품별 번역 용어집" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
        if (event.key === "Escape") { event.stopPropagation(); setOpen(false); }
        if (event.key === "Tab") {
          const focusable = [...dialogRef.current.querySelectorAll("button, input, select, summary, a[href], iframe")].filter((el) => !el.disabled && el.getClientRects().length);
          const first = focusable[0], last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}>
        <header className="glossary-heading"><h2>작품별 번역 용어집</h2><button onClick={() => setOpen(false)} aria-label="용어집 닫기">닫기</button></header>
        {!loaded ? <p>용어집을 불러오는 중…</p> : null}
        {error ? <p role="alert" className="glossary-error">{error}</p> : null}
        {loaded && (!data || changing) ? <form onSubmit={(event) => { event.preventDefault(); post("/api/glossary/confirm", savedId ? { work_id: savedId } : { title, author, edition }); }}>
          <p>작품을 한 번 확인하면 웹 조사를 시작합니다. 조사 중에도 창을 닫고 이미지를 편집할 수 있습니다.</p>
          {works.length ? <label>기존 작품 용어집 재사용<select value={savedId} onChange={(event) => setSavedId(event.target.value)}>
            <option value="">새 작품 조사</option>
            {works.map((item) => <option key={item.work_id} value={item.work_id}>{[item.work.title, item.work.author, item.work.edition].filter(Boolean).join(" · ")}</option>)}
          </select></label> : null}
          {!savedId ? <>
            <label>조사할 작품명<input required maxLength={200} value={title} placeholder={projectName || "정확한 작품명을 입력하세요"} onChange={(event) => setTitle(event.target.value)} /></label>
            <label>작가 (동명 작품 구분)<input maxLength={200} value={author} onChange={(event) => setAuthor(event.target.value)} /></label>
            <label>원작 / 외전 / 판본 (선택)<input maxLength={200} value={edition} placeholder="예: 원작 만화, 특정 외전명" onChange={(event) => setEdition(event.target.value)} /></label>
          </> : null}
          <p>작품명·작가·판본만 외부 검색에 전달합니다. 이미지와 수동 번역 지시문은 보내지 않습니다. Gemini 검색 API 사용 비용이 발생할 수 있습니다.</p>
          <div className="row"><button className="primary" disabled={disabled || (!savedId && !title.trim())}>{savedId ? "이 작품으로 확인 · 재사용" : "이 작품으로 확인 · 조사 시작"}</button>{changing ? <button type="button" onClick={() => setChanging(false)}>취소</button> : null}</div>
        </form> : null}
        {data && !changing ? <>
          <div className="glossary-work"><strong>{[data.work.title, data.work.author, data.work.edition].filter(Boolean).join(" · ")}</strong><span>{STATUS[data.status]} · v{data.version}</span></div>
          <div className="row"><button disabled={disabled} onClick={() => setChanging(true)}>작품 변경</button><button disabled={disabled} onClick={() => post("/api/glossary/research", {})}>{data.status === "failed" ? "다시 조사" : "정보 다시 조사"}</button></div>
          {researching ? <p role="status">웹에서 작품·등장인물·용어를 조사하고 있습니다. 번역은 조사가 끝난 뒤 시작할 수 있습니다.</p> : null}
          {data.error ? <p role="alert" className="glossary-error">{data.error}</p> : null}
          {data.status === "ready" && !data.work_matches ? <p className="glossary-error">작품 일치가 확인되지 않아 자동 적용을 보류했습니다. 작품명을 확인하거나 개별 용어를 검토하세요.</p> : null}
          {data.summary ? <details><summary>작품·등장인물 참고 정보 (번역에 자동 전달하지 않음)</summary><p className="glossary-summary">{data.summary}</p></details> : null}
          <p>적용 {applied.length}개 · 검토 필요 {review.length}개 · 제외 {excluded.length}개. 수동 번역 지시문을 우선합니다. 기존 번역은 그대로이며, 변경된 용어집은 다음 번역부터 사용합니다.</p>
          {review.length ? <div><h3>충돌·불확실 항목만 검토</h3>{review.map(renderTerm)}</div> : null}
          <details><summary>적용 중인 용어 {applied.length}개</summary>{applied.map(renderTerm)}</details>
          {excluded.length ? <details><summary>제외한 용어 {excluded.length}개</summary>{excluded.map(renderTerm)}</details> : null}
          {data.status === "ready" && !entries.length ? <p>확인된 용어가 없습니다. 작품 정보를 보완해 다시 조사하거나 기존 번역 지시문에 직접 입력하세요.</p> : null}
          {data.search_suggestions ? <iframe className="glossary-search-suggestions" title="Google 검색 제안" sandbox="allow-popups allow-popups-to-escape-sandbox" srcDoc={data.search_suggestions} /> : null}
        </> : null}
      </section>
    </div>}
  </>;
}
