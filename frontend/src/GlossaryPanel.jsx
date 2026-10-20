import { useEffect, useRef, useState } from "react";
import { api } from "./lib.js";

const STATUS = { idle: "조사 대기", researching: "저장 자료 정리 중", ready: "사용 준비 완료", failed: "조사 실패" };
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
    {entry.evidence?.length ? <details><summary>자료 인용 근거 {entry.evidence.length}개</summary>
      {entry.evidence.map((cite, index) => <div className="glossary-citation" key={`${cite.url}-${index}`}>
        <a href={cite.url} target="_blank" rel="noopener noreferrer">{cite.title}</a><p>{cite.text}</p>
      </div>)}
    </details> : <p>원문·한국어 표기를 함께 뒷받침하는 자료 인용이 없습니다.</p>}
  </article>;
}

export default function GlossaryPanel({ sessionId, projectName, ocrBusy, analysisReady, autoMatch = true }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [works, setWorks] = useState([]);
  const [matching, setMatching] = useState(null);
  const [query, setQuery] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceText, setSourceText] = useState("");
  const queryDirty = useRef(false);
  const autoMatched = useRef("");
  const mutation = useRef(0);
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

  function applyPayload(result) {
    setData(result.glossary);
    if (result.works) setWorks(result.works);
    if (result.matching) {
      setMatching(result.matching);
      if (!queryDirty.current) setQuery(result.matching.query || "");
    }
  }

  useEffect(() => {
    const token = ++generation.current;
    setData(null); setMatching(null); setLoaded(false); setError(""); setChanging(false); setSavedId("");
    queryDirty.current = false; autoMatched.current = ""; setQuery(""); mutation.current += 1;
    setSourceUrl(""); setSourceText(""); setAuthor(""); setEdition(""); setTitle(""); setOpen(false); setBusy(false);
    if (!sessionId) return;
    api(`/api/glossary/${encodeURIComponent(sessionId)}`).then((result) => {
      if (token !== generation.current) return;
      applyPayload(result); setLoaded(true);
    }).catch((err) => { if (token === generation.current) { setError(err.message); setLoaded(true); } });
    return () => { generation.current += 1; };
  }, [sessionId]);

  useEffect(() => {
    if (!open || !sessionId || !loaded) return;
    let alive = true;
    const version = mutation.current;
    api(`/api/glossary/${encodeURIComponent(sessionId)}`).then((result) => {
      if (alive && version === mutation.current) applyPayload(result);
    }).catch((err) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, [open, sessionId, loaded]);

  useEffect(() => {
    if (data?.status !== "researching" && matching?.status !== "searching") return;
    let alive = true;
    let timer;
    async function poll() {
      try {
        const version = mutation.current;
        const result = await api(`/api/glossary/${encodeURIComponent(sessionId)}`);
        if (!alive || version !== mutation.current) return;
        applyPayload(result); setError("");
        if (result.glossary?.status === "researching" || result.matching?.status === "searching") timer = setTimeout(poll, 2500);
      } catch (err) {
        if (alive) { setError(err.message); timer = setTimeout(poll, 5000); }
      }
    }
    timer = setTimeout(poll, 2000);
    return () => { alive = false; clearTimeout(timer); };
  }, [sessionId, data?.status, matching?.status]);

  useEffect(() => {
    if (!autoMatch || !loaded || data || !sessionId || busy || matching?.status === "searching" || matching?.status === "failed" || matching?.status === "empty") return;
    const key = `${sessionId}:${analysisReady ? "ready" : "pending"}`;
    if (autoMatched.current === key) return;
    autoMatched.current = key;
    post("/api/glossary/match", {}, false);
  }, [sessionId, loaded, analysisReady, Boolean(data), matching?.status, autoMatch]);

  async function post(path, body, closeForm = true) {
    const sid = sessionId;
    const token = generation.current;
    mutation.current += 1;
    setBusy(true); setError("");
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify({ session_id: sid, ...body }) });
      if (sessionRef.current !== sid || generation.current !== token) return;
      applyPayload(result);
      if (closeForm) setChanging(false);
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
  const searching = matching?.status === "searching";
  const disabled = busy || ocrBusy || researching;
  const matchLabel = !loaded || busy ? "작품 매칭 중…" : searching ? "작품 검색 중…" : matching?.status === "failed" ? "작품 연결 확인" : matching?.candidates?.length ? "작품 후보 확인" : matching?.status === "waiting_cv" ? "작품 확인 · CV 대기" : "작품 연결 · URL 자료 추가";
  const workTitle = data?.work?.display_title || data?.work?.title;
  const glossaryLabel = data?.status === "failed" ? "용어집 조사 실패 · 다시 시도" : researching ? "용어집 조사 중…" : `용어집 적용 ${applied.length} · 검토 ${review.length}`;
  const renderTerm = (entry) => <Term key={entry.id} entry={entry} busy={disabled} onReview={(id, decision, target) => post("/api/glossary/review", { work_id: data.work_id, entry_id: id, decision, target })} />;

  return <>
    <button className={`glossary-toggle ${data ? "linked" : ""}`} title={data ? `연결 작품: ${workTitle} · ${glossaryLabel}` : matchLabel} onClick={() => setOpen(true)} disabled={!sessionId} aria-label={data ? `연결 작품: ${workTitle}. ${glossaryLabel}` : matchLabel}>
      <strong>{workTitle || matchLabel} ▾</strong>
      {data ? <span>{searching ? "다른 작품 검색 중…" : matching?.status === "failed" ? "재검색 실패 · 현재 연결 유지" : glossaryLabel}</span> : null}
    </button>
    {open && <div className="glossary-backdrop" onClick={() => setOpen(false)}>
      <section ref={dialogRef} className="glossary-dialog" role="dialog" aria-modal="true" aria-label="작품별 번역 용어집" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
        if (event.key === "Escape") { event.stopPropagation(); setOpen(false); }
        if (event.key === "Tab") {
          const focusable = [...dialogRef.current.querySelectorAll("button, input, textarea, select, summary, a[href], iframe")].filter((el) => !el.disabled && el.getClientRects().length);
          const first = focusable[0], last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }}>
        <header className="glossary-heading"><h2>작품별 번역 용어집</h2><button onClick={() => setOpen(false)} aria-label="용어집 닫기">닫기</button></header>
        {!loaded ? <p>용어집을 불러오는 중…</p> : null}
        {error ? <p role="alert" className="glossary-error">{error}</p> : null}
        {loaded ? <section className="work-search" aria-label="작품 후보와 검색">
          <form onSubmit={(event) => { event.preventDefault(); post("/api/glossary/match", { query }, false); }}>
            <label>저장된 작품 검색어<input maxLength={300} value={query} placeholder={projectName || "작품명·작가명·제목 일부"} onChange={(event) => { queryDirty.current = true; setQuery(event.target.value); }} /></label>
            <div className="row"><button type="submit" disabled={busy || searching || !query.trim()}>저장된 작품 찾기</button>
              <button type="button" disabled={busy} onClick={() => { queryDirty.current = false; setQuery(matching?.hints?.query || ""); }}>CV 키워드 가져오기</button></div>
          </form>
          <p>앞쪽 페이지의 기존 CV와 저장된 작품만 대조합니다. 유료 웹 검색은 하지 않습니다. 새 작품은 직접 지정한 뒤 자료 URL을 추가하세요.</p>
          {matching?.hints?.pages?.length ? <details><summary>식별 근거 · {matching.hints.pages.join(", ")}페이지 CV</summary><p>{matching.hints.labels.join(" · ")}</p></details> : <p>CV가 준비되면 작품 매칭을 시도합니다. 검색어를 직접 입력해도 됩니다.</p>}
          {matching?.error ? <p role="alert" className="glossary-error">{matching.error}{data ? " 현재 작품 연결은 유지됩니다." : ""}</p> : null}
          {matching?.status === "empty" ? <p>저장된 작품 후보가 없습니다. 작품을 직접 지정하고 자료 URL을 추가하세요.</p> : null}
          {matching?.candidates?.map((candidate) => <article className="work-candidate" key={candidate.id}>
            <strong>{candidate.work.display_title || candidate.work.title}</strong><p>{candidate.work.author} · {candidate.work.edition}</p><p>{candidate.reason}</p>
            {candidate.matched_keywords?.length ? <p>일치 단서: {candidate.matched_keywords.join(" · ")}</p> : null}
            {candidate.sources?.map((cite, index) => <a key={index} href={cite.url} target="_blank" rel="noopener noreferrer">{cite.title} ↗ </a>)}
            <button disabled={busy || ocrBusy || candidate.work_id === data?.work_id} onClick={() => post("/api/glossary/confirm", candidate.work_id ? { work_id: candidate.work_id } : candidate.work)}>
              {candidate.work_id === data?.work_id ? "현재 연결된 작품" : candidate.work_id ? "이 작품 용어집 연결" : "이 작품으로 확인 · 연결"}
            </button>
          </article>)}

          <button onClick={() => setChanging((value) => !value)} disabled={busy || ocrBusy}>{changing ? "직접 지정 닫기" : "다른 작품 직접 지정 / 기존 용어집 선택"}</button>
        </section> : null}
        {loaded && changing ? <form onSubmit={(event) => { event.preventDefault(); post("/api/glossary/confirm", savedId ? { work_id: savedId } : { title, author, edition }); }}>
          <p>작품을 연결한 뒤 자료 URL을 추가하세요. 작품 연결만으로는 API를 호출하지 않습니다.</p>
          {works.length ? <label>기존 작품 용어집 재사용<select value={savedId} onChange={(event) => setSavedId(event.target.value)}>
            <option value="">새 작품 조사</option>
            {works.map((item) => <option key={item.work_id} value={item.work_id}>{[item.work.title, item.work.author, item.work.edition].filter(Boolean).join(" · ")}</option>)}
          </select></label> : null}
          {!savedId ? <>
            <label>조사할 작품명<input required maxLength={200} value={title} placeholder={projectName || "정확한 작품명을 입력하세요"} onChange={(event) => setTitle(event.target.value)} /></label>
            <label>작가 (동명 작품 구분)<input maxLength={200} value={author} onChange={(event) => setAuthor(event.target.value)} /></label>
            <label>원작 / 외전 / 판본 (선택)<input maxLength={200} value={edition} placeholder="예: 원작 만화, 특정 외전명" onChange={(event) => setEdition(event.target.value)} /></label>
          </> : null}
          <p>작품명·작가·판본으로 용어집을 구분합니다. OpenAI는 저장한 자료의 용어 정리를 직접 실행할 때만 사용합니다.</p>
          <div className="row"><button className="primary" disabled={busy || ocrBusy || (!savedId && !title.trim())}>{savedId ? "이 작품으로 확인 · 재사용" : "이 작품으로 확인 · 연결"}</button>{changing ? <button type="button" onClick={() => setChanging(false)}>취소</button> : null}</div>
        </form> : null}
        {data && !changing ? <>
          <div className="glossary-work"><strong>{[data.work.title, data.work.author, data.work.edition].filter(Boolean).join(" · ")}</strong><span>{STATUS[data.status]} · v{data.version}</span></div>
          <div className="row"><button disabled={busy || ocrBusy} onClick={() => setChanging(true)}>작품 변경</button></div>
          <section className="work-search" aria-label="작품 URL 자료">
            <h3>작품 URL 자료 · {data.sources?.length || 0}/6</h3>
            <form onSubmit={(event) => { event.preventDefault(); post("/api/glossary/source", { work_id: data.work_id, url: sourceUrl, text: sourceText }, false); }}>
              <label>자료 URL<input type="url" required maxLength={2000} value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://… 작품·등장인물 소개" /></label>
              <details><summary>자동 수집이 안 되면 본문 직접 붙여 넣기</summary><label>출처에서 복사한 본문<textarea maxLength={16000} rows={5} value={sourceText} onChange={(event) => setSourceText(event.target.value)} /></label><p>붙여 넣은 자료에서 만든 용어는 자동 적용하지 않고 검토에 둡니다.</p></details>
              <button disabled={disabled || !sourceUrl.trim()}>{busy ? "자료 처리 중…" : sourceText.trim() ? "본문 저장" : "URL 자료 수집 · 저장"}</button>
            </form>
            <p>작품당 최대 6개, 문서당 본문 16,000자. URL 수집에는 OpenAI를 사용하지 않습니다. 같은 자료는 저장본을 재사용합니다.</p>
            {data.sources?.map((source) => <article className="work-candidate" key={source.url}>
              <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a>
              <p>{source.mode === "paste" ? "직접 저장" : "URL 수집"} · {source.text.length.toLocaleString()}자{source.truncated ? " · 일부만 저장" : ""} · {new Date(source.fetched_at).toLocaleString()}</p>
              <details><summary>저장한 본문 확인</summary><p className="glossary-summary">{source.text}</p></details>
              <div className="row"><button disabled={disabled} onClick={() => post("/api/glossary/source", { work_id: data.work_id, url: source.requested_url || source.url, refresh: true }, false)}>URL에서 다시 수집</button><button disabled={disabled} onClick={() => post("/api/glossary/source", { work_id: data.work_id, url: source.url, remove: true }, false)}>자료 삭제</button></div>
            </article>)}
            <button disabled={disabled || !data.sources?.length} onClick={() => post("/api/glossary/research", {}, false)}>저장 자료로 용어 정리</button>
            <p>저장한 본문과 작품 정보만 OpenAI에 전달합니다. 정리 시 일반 생성 비용·할당량이 적용됩니다. 같은 자료와 모델의 성공 결과가 있으면 API 없이 재사용합니다.</p>
          </section>
          {researching ? <p role="status">저장한 자료의 작품·등장인물·용어를 정리하고 있습니다. 번역은 조사가 끝난 뒤 시작할 수 있습니다.</p> : null}
          {data.error ? <p role="alert" className="glossary-error">{data.error}</p> : null}
          {data.status === "ready" && !data.work_matches ? <p className="glossary-error">작품 일치가 확인되지 않아 자동 적용을 보류했습니다. 작품명을 확인하거나 개별 용어를 검토하세요.</p> : null}
          {data.routing ? <p>용어 정리 모델: {data.routing.model} · {data.routing.reason}</p> : null}
          {data.summary ? <details><summary>작품·등장인물 참고 정보 (번역에 자동 전달하지 않음)</summary><p className="glossary-summary">{data.summary}</p></details> : null}
          <p>적용 {applied.length}개 · 검토 필요 {review.length}개 · 제외 {excluded.length}개. 수동 번역 지시문을 우선합니다. 기존 번역은 그대로이며, 변경된 용어집은 다음 번역부터 사용합니다.</p>
          <p>같은 인물·용어로 확인되면 나무위키 대표 표기로 통일해 자동 적용합니다. 사용자 확정 표기를 우선하며, 누구를 가리키는지 불확실한 항목만 검토합니다. 전체 이름과 약칭도 각각 같은 표기 기준을 사용합니다.</p>
          {review.length ? <div><h3>충돌·불확실 항목만 검토</h3>{review.map(renderTerm)}</div> : null}
          <details><summary>적용 중인 용어 {applied.length}개</summary>{applied.map(renderTerm)}</details>
          {excluded.length ? <details><summary>제외한 용어 {excluded.length}개</summary>{excluded.map(renderTerm)}</details> : null}
          {data.status === "ready" && !entries.length ? <p>확인된 용어가 없습니다. 일본어 원문과 한국어 표기를 함께 확인할 수 있는 자료를 추가하세요.</p> : null}

        </> : null}
      </section>
    </div>}
  </>;
}
