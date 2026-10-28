import { useEffect, useRef, useState } from "react";
import { api } from "./lib.js";

// 작품 메모: 등장인물(성격·말투)과 고유 용어. 번역 프롬프트의 참고 정보로만 쓴다.
// 인물 표 순서가 영역 분석 탭의 화자 단축키 순서다(주인공 1번, 나머지 2-8번, 9번은 기타 목록).

const EMPTY_CHARACTER = { name: "", original: "", aliases: "", personality: "", speech: "", protagonist: false };
const EMPTY_TERM = { korean: "", original: "", note: "" };

function TableInput({ value, onChange, label, wide = false }) {
  return <input className={wide ? "wide" : ""} aria-label={label} value={value || ""} maxLength={300} onChange={(event) => onChange(event.target.value)} />;
}

export default function GlossaryPanel({ sessionId, projectName, ocrBusy, analysisReady, onMemoChange }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [works, setWorks] = useState([]);
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [title, setTitle] = useState("");
  const [workId, setWorkId] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceText, setSourceText] = useState("");
  // 작품명으로 찾은 나무위키 주소. found=false면 정확한 문서가 없어 검색 결과 주소다.
  const [namu, setNamu] = useState(null);
  const [namuBusy, setNamuBusy] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [changing, setChanging] = useState(false);
  const [binding, setBinding] = useState(null);
  const [match, setMatch] = useState(null);
  const autoBound = useRef("");
  // 자동 저장: 칸에서 포커스가 빠지거나 순서·주인공·삭제를 바꾸면 바로 저장한다.
  const draftRef = useRef(null);
  const editVersion = useRef(0);
  const saving = useRef(false);
  const savePending = useRef(false);
  const [saveState, setSaveState] = useState("saved");
  const sessionRef = useRef(sessionId);
  const dialogRef = useRef(null);
  sessionRef.current = sessionId;

  // 서버가 나무위키 이동 검색으로 문서 주소만 찾는다(본문 수집·OpenAI 호출 없음).
  async function findNamu() {
    const query = (draftRef.current?.title || data?.title || "").trim();
    if (!query) return;
    setNamuBusy(true); setError("");
    try {
      const result = await api(`/api/namu-lookup?q=${encodeURIComponent(query)}`);
      setNamu(result);
      if (result.found) setSourceUrl(result.url);
    } catch (err) { setError(String(err.message || err)); }
    finally { setNamuBusy(false); }
  }

  function applyPayload(result) {
    setData(result.glossary || null);
    setWorks(result.works || []);
    setBinding(result.binding || null);
    setMatch(result.match || null);
    setDraftValue(result.glossary ? structuredClone(result.glossary) : null);
    setDirty(false);
    setSaveState("saved");
    onMemoChange?.(result.glossary || null);
  }

  function setDraftValue(next) {
    draftRef.current = next;
    setDraft(next);
  }

  useEffect(() => {
    setData(null); setDraft(null); setDirty(false); setError(""); setOpen(false); setChanging(false);
    onMemoChange?.(null);
    if (!sessionId) return undefined;
    let alive = true;
    api(`/api/glossary/${encodeURIComponent(sessionId)}`)
      .then((result) => { if (alive) applyPayload(result); })
      .catch((err) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, [sessionId]);

  // 연결 전: 분석이 끝나 CV가 생기면 다시 조회한다. 확실한 후보 1개면 자동 연결한다.
  useEffect(() => {
    if (!sessionId || data || binding?.matched_by === "none" || !analysisReady || match?.has_cv) return undefined;
    let alive = true;
    api(`/api/glossary/${encodeURIComponent(sessionId)}`)
      .then((result) => { if (alive) applyPayload(result); })
      .catch(() => {});
    return () => { alive = false; };
  }, [sessionId, analysisReady, Boolean(data), match?.has_cv]);

  useEffect(() => {
    const best = match?.candidates?.[0];
    if (data || binding?.matched_by === "none" || !best?.strong || busy || autoBound.current === sessionId) return;
    autoBound.current = sessionId;
    post("/api/glossary/bind", { work_id: best.work_id, matched_by: "cv", hits: best.hits }, "bind");
  }, [match, Boolean(data), sessionId]);

  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    dialogRef.current?.querySelector("input, button")?.focus();
    return () => previous?.isConnected && previous.focus();
  }, [open]);

  async function post(path, body, label) {
    const sid = sessionId;
    setBusy(label); setError("");
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify({ session_id: sid, ...body }) });
      if (sessionRef.current !== sid) return;
      applyPayload(result);
      setChanging(false);
    } catch (err) {
      if (sessionRef.current === sid) setError(err.message);
    } finally {
      if (sessionRef.current === sid) setBusy("");
    }
  }

  function edit(mutator, { commit = false } = {}) {
    const next = structuredClone(draftRef.current);
    mutator(next);
    setDraftValue(next);
    editVersion.current += 1;
    setDirty(true);
    setSaveState("pending");
    if (commit) requestSave();
  }

  function requestSave() {
    if (!draftRef.current?.work_id) return;
    if (saving.current) { savePending.current = true; return; }
    runSave();
  }

  async function runSave() {
    const sid = sessionId;
    const sent = draftRef.current;
    const version = editVersion.current;
    saving.current = true;
    setSaveState("saving");
    try {
      const result = await api("/api/glossary/save", { method: "POST", body: JSON.stringify({
        session_id: sid, work_id: sent.work_id, title: sent.title, characters: sent.characters, terms: sent.terms, notes: sent.notes || "",
      }) });
      if (sessionRef.current !== sid) return;
      const saved = result.glossary;
      setData(saved);
      onMemoChange?.(saved);
      // 편집 중인 표는 그대로 두고, 새 인물에 서버가 붙인 id만 옮겨 화자 지정이 같은 인물을 가리키게 한다.
      const ids = Object.fromEntries((saved?.characters || []).map((row) => [row.name.replace(/\s+/g, "").toLowerCase(), row.id]));
      const current = draftRef.current;
      if (current?.characters?.some((row) => !row.id && ids[row.name.replace(/\s+/g, "").toLowerCase()])) {
        const next = structuredClone(current);
        next.characters.forEach((row) => { row.id = row.id || ids[row.name.replace(/\s+/g, "").toLowerCase()]; });
        setDraftValue(next);
      }
      if (editVersion.current === version) { setDirty(false); setSaveState("saved"); }
      setError("");
    } catch (err) {
      if (sessionRef.current === sid) { setError(`자동 저장 실패: ${err.message}`); setSaveState("error"); }
    } finally {
      saving.current = false;
      if (savePending.current) { savePending.current = false; runSave(); }
    }
  }

  function updateRow(list, index, field, value) {
    if (field === "protagonist") {
      // 서버와 같은 규칙: 주인공은 한 명이고 1번 자리로 간다.
      edit((next) => {
        next.characters.forEach((row, i) => { row.protagonist = i === index; });
        const [row] = next.characters.splice(index, 1);
        next.characters.unshift(row);
      }, { commit: true });
      return;
    }
    edit((next) => { next[list][index][field] = value; });
  }

  // 키 칸에서 고른 번호 자리로 옮긴다. 주인공은 1번 고정, 9(기타)는 8번 뒤 첫 자리.
  function placeCharacter(index, slot) {
    edit((next) => {
      const [row] = next.characters.splice(index, 1);
      const lead = next.characters.some((item) => item.protagonist) ? 1 : 0;
      const target = Math.max(lead, Math.min(next.characters.length, slot - 1));
      next.characters.splice(target, 0, row);
    }, { commit: true });
  }

  function moveCharacter(index, step) {
    edit((next) => {
      const target = index + step;
      if (target < 0 || target >= next.characters.length) return;
      [next.characters[index], next.characters[target]] = [next.characters[target], next.characters[index]];
    }, { commit: true });
  }

  function close() {
    if (dirty) requestSave();
    setOpen(false);
  }

  const characters = draft?.characters || [];
  const terms = draft?.terms || [];
  const candidates = match?.candidates || [];
  const best = candidates[0];
  const autoLinked = binding?.matched_by === "cv";
  const declined = binding?.matched_by === "none";
  const chipText = data ? data.title : declined ? "작품 없음" : best ? `${best.title}?` : "작품 미연결";
  const chipTitle = data
    ? `작품: ${data.title}${autoLinked ? " (CV 자동 연결)" : ""} · 인물 ${data.characters.length} · 용어 ${data.terms.length}`
    : best && !declined ? "앞쪽 페이지 CV로 찾은 후보입니다. 눌러서 확인 후 연결하세요" : "눌러서 이 프로젝트의 작품을 연결하세요";
  const status = data
    ? (autoLinked ? `앞쪽 페이지 CV 자동 연결 · 근거 ${(binding.hits || []).slice(0, 6).join(", ")}` : "직접 연결")
    : declined ? "작품 없이 진행 중 (자동 연결 안 함)"
      : best ? `CV 후보: ${best.title} (일치 ${best.score}개). 확인 후 연결하세요`
        : match?.has_cv ? "앞쪽 페이지 CV와 일치하는 작품 메모가 없습니다" : "분석이 끝나면 CV로 작품을 찾습니다";
  const otherWorks = works.filter((item) => !candidates.some((row) => row.work_id === item.work_id));
  const showOptions = changing || !data;
  const bind = (body) => post("/api/glossary/bind", body, "bind");

  return <>
    <button className={`work-chip ${data ? "linked" : best && !declined ? "suggest" : ""}`} title={chipTitle} onClick={() => setOpen(true)} disabled={!sessionId}>
      <span>작품</span><strong>{chipText}</strong>
    </button>
    {open && <div className="glossary-backdrop" onClick={close}>
      <section ref={dialogRef} className="glossary-dialog memo" role="dialog" aria-modal="true" aria-label="작품 메모" onClick={(event) => event.stopPropagation()}
        onBlur={(event) => { if (dirty && ["INPUT", "TEXTAREA"].includes(event.target.tagName)) requestSave(); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.stopPropagation(); close(); }
          if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); if (dirty) requestSave(); }
        }}>
        <header className="glossary-heading">
          <h2>작품 메모</h2>
          <div className="row">
            {data ? <span className={`memo-save-state ${saveState}`} role="status">{{ saved: "자동 저장됨", pending: "편집 중", saving: "저장 중…", error: "저장 실패" }[saveState]}</span> : null}
            <button onClick={close} aria-label="작품 메모 닫기">닫기</button>
          </div>
        </header>
        {error ? <p role="alert" className="glossary-error">{error}</p> : null}

        <section className="memo-link" aria-label="연결된 작품">
          <div className="memo-link-current">
            <div><span>이 프로젝트의 작품</span><strong>{data ? data.title : "연결 안 됨"}</strong><small>{status}</small></div>
            {data ? <button disabled={Boolean(busy)} onClick={() => setChanging((value) => !value)}>{changing ? "변경 닫기" : "변경"}</button> : null}
          </div>
          {showOptions ? <div className="memo-link-options">
            {candidates.length ? <div>
              <h4>CV로 찾은 후보 <small>앞쪽 페이지 글자에 메모의 원문 이름·용어가 나온 수</small></h4>
              {candidates.map((row) => <button key={row.work_id} disabled={Boolean(busy) || row.work_id === data?.work_id} onClick={() => bind({ work_id: row.work_id })}>
                <strong>{row.title}{row.work_id === data?.work_id ? " (현재)" : ""}</strong><span>일치 {row.score}개 · {row.hits.slice(0, 6).join(", ")}</span>
              </button>)}
            </div> : null}
            {otherWorks.length ? <div>
              <h4>다른 작품 메모에서 고르기</h4>
              <div className="row">
                <select value={workId} onChange={(event) => setWorkId(event.target.value)} aria-label="작품 메모 선택">
                  <option value="">선택…</option>
                  {otherWorks.map((item) => <option key={item.work_id} value={item.work_id}>{item.title}</option>)}
                </select>
                <button disabled={Boolean(busy) || !workId} onClick={() => bind({ work_id: workId })}>연결</button>
              </div>
            </div> : null}
            <div>
              <h4>새 작품으로 등록</h4>
              <form className="row" onSubmit={(event) => { event.preventDefault(); bind({ title }); }}>
                <input value={title} maxLength={200} placeholder="작품명 (예: 내 사망플래그가 그칠 줄 모른다)" onChange={(event) => setTitle(event.target.value)} aria-label="새 작품명" />
                <button disabled={Boolean(busy) || !title.trim()}>등록하고 연결</button>
              </form>
            </div>
            {data || !declined ? <button className="memo-unlink" disabled={Boolean(busy)} onClick={() => post("/api/glossary/unbind", {}, "bind")}>작품 없이 진행 (연결 해제, 자동 연결 안 함)</button> : null}
          </div> : null}
        </section>

        {draft ? <>
          <label className="memo-title-label">표시 이름<input className="memo-title" value={draft.title} maxLength={200} onChange={(event) => edit((next) => { next.title = event.target.value; })} /></label>

          <h3>등장인물 · 말투 <small>번호 = 영역 분석 탭 화자 단축키 · 9 기타(전체 목록) · N 내레이션 · 0 해제</small></h3>
          <div className="memo-table characters">
            <div className="memo-row head"><span>키</span><span>이름</span><span>원문</span><span>호칭·약칭</span><span>성격</span><span>말투</span><span>주인공</span><span /></div>
            {characters.map((row, index) => <div className="memo-row" key={row.id || `new-${index}`}>
              {row.protagonist ? <span className="memo-key">1</span> : <select className="memo-key" aria-label={`${row.name || "인물"} 단축키 번호`} value={index < 8 ? index + 1 : 9} onChange={(event) => placeCharacter(index, Number(event.target.value))}>
                {[2, 3, 4, 5, 6, 7, 8].map((n) => <option key={n} value={n}>{n}</option>)}
                <option value={9}>9 기타</option>
              </select>}
              <TableInput label="이름" value={row.name} onChange={(value) => updateRow("characters", index, "name", value)} />
              <TableInput label="원문 표기" value={row.original} onChange={(value) => updateRow("characters", index, "original", value)} />
              <TableInput label="호칭·약칭" value={row.aliases} onChange={(value) => updateRow("characters", index, "aliases", value)} />
              <TableInput label="성격" wide value={row.personality} onChange={(value) => updateRow("characters", index, "personality", value)} />
              <TableInput label="말투" wide value={row.speech} onChange={(value) => updateRow("characters", index, "speech", value)} />
              <input type="radio" name="memo-protagonist" aria-label={`${row.name || "인물"} 주인공`} checked={Boolean(row.protagonist)} onChange={() => updateRow("characters", index, "protagonist", true)} />
              <span className="memo-actions">
                <button aria-label="위로" disabled={index === 0} onClick={() => moveCharacter(index, -1)}>↑</button>
                <button aria-label="아래로" disabled={index === characters.length - 1} onClick={() => moveCharacter(index, 1)}>↓</button>
                <button aria-label="삭제" onClick={() => edit((next) => { next.characters.splice(index, 1); }, { commit: true })}>×</button>
              </span>
            </div>)}
          </div>
          <button onClick={() => edit((next) => { next.characters.push({ ...EMPTY_CHARACTER }); })}>+ 인물</button>
          <p>저장하면 주인공이 1번으로 올라갑니다. 텍스트 기반 영역은 따로 지정하지 않으면 주인공 속마음으로 보내고, 내레이션인지는 번역 모델이 함께 판단합니다.</p>

          <h3>고유 용어</h3>
          <div className="memo-table terms">
            <div className="memo-row head"><span>원문</span><span>한국어</span><span>메모</span><span /></div>
            {terms.map((row, index) => <div className="memo-row" key={`term-${index}`}>
              <TableInput label="원문" value={row.original} onChange={(value) => updateRow("terms", index, "original", value)} />
              <TableInput label="한국어" value={row.korean} onChange={(value) => updateRow("terms", index, "korean", value)} />
              <TableInput label="메모" wide value={row.note} onChange={(value) => updateRow("terms", index, "note", value)} />
              <span className="memo-actions"><button aria-label="삭제" onClick={() => edit((next) => { next.terms.splice(index, 1); }, { commit: true })}>×</button></span>
            </div>)}
          </div>
          <button onClick={() => edit((next) => { next.terms.push({ ...EMPTY_TERM }); })}>+ 용어</button>

          <label>작품 메모 (배경·호칭 규칙 등, 선택)<textarea rows={3} maxLength={4000} value={draft.notes || ""} onChange={(event) => edit((next) => { next.notes = event.target.value; })} /></label>

          <details className="memo-draft" onToggle={(event) => {
            // 처음 펼칠 때 주소가 비어 있으면 작품명으로 나무위키 문서를 찾아 둔다
            if (event.currentTarget.open && !sourceUrl.trim() && !namu && !namuBusy) findNamu();
          }}>
            <summary>URL로 초안 채우기 (OpenAI 1회)</summary>
            <form onSubmit={(event) => {
              event.preventDefault();
              if (dirty) { requestSave(); setError("편집 내용을 저장하는 중입니다. 잠시 뒤 다시 누르세요."); return; }
              post("/api/glossary/draft", { work_id: data.work_id, url: sourceUrl, text: sourceText }, "draft");
            }}>
              <label>자료 URL (나무위키 등)<input type="url" required maxLength={2000} value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://namu.wiki/w/…" /></label>
              <div className="memo-namu">
                <button type="button" onClick={findNamu} disabled={namuBusy || !(draft.title || data.title || "").trim()}>{namuBusy ? "찾는 중…" : "작품명으로 나무위키 찾기"}</button>
                {namu && (namu.found
                  ? <span>‘{namu.query}’ 문서를 넣었습니다. <a href={namu.url} target="_blank" rel="noopener noreferrer">열어서 맞는지 확인 ↗</a></span>
                  : <span>정확히 맞는 문서가 없습니다. <a href={namu.url} target="_blank" rel="noopener noreferrer">나무위키 검색 결과 열기 ↗</a>에서 맞는 문서 주소를 복사해 붙여 넣으세요.</span>)}
              </div>
              <label>자동 수집이 막히면 본문 붙여 넣기 (선택)<textarea rows={4} maxLength={16000} value={sourceText} onChange={(event) => setSourceText(event.target.value)} /></label>
              <button disabled={Boolean(busy) || ocrBusy || !sourceUrl.trim()}>{busy === "draft" ? "초안 만드는 중…" : "초안 채우기"}</button>
            </form>
            <p>새 인물·용어만 추가하고, 이미 있는 인물은 빈 칸만 채웁니다. 결과는 직접 고쳐서 쓰세요.</p>
          </details>
          <p>변경한 메모는 다음 번역부터 반영됩니다. 이미 번역한 페이지는 다시 번역해야 바뀝니다.</p>
        </> : null}
      </section>
    </div>}
  </>;
}
