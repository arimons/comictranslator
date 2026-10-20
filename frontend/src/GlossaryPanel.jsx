import { useEffect, useRef, useState } from "react";
import { api } from "./lib.js";

// 작품 메모: 등장인물(성격·말투)과 고유 용어. 번역 프롬프트의 참고 정보로만 쓴다.
// 인물 표 순서가 영역 분석 탭의 화자 단축키 순서다(주인공 1번, 나머지 2-8번, 9번은 기타 목록).

const EMPTY_CHARACTER = { name: "", original: "", aliases: "", personality: "", speech: "", protagonist: false };
const EMPTY_TERM = { korean: "", original: "", note: "" };

function TableInput({ value, onChange, label, wide = false }) {
  return <input className={wide ? "wide" : ""} aria-label={label} value={value || ""} maxLength={300} onChange={(event) => onChange(event.target.value)} />;
}

export default function GlossaryPanel({ sessionId, projectName, ocrBusy, onMemoChange }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [works, setWorks] = useState([]);
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [title, setTitle] = useState("");
  const [workId, setWorkId] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [changing, setChanging] = useState(false);
  const sessionRef = useRef(sessionId);
  const dialogRef = useRef(null);
  sessionRef.current = sessionId;

  function applyPayload(result) {
    setData(result.glossary || null);
    setWorks(result.works || []);
    setDraft(result.glossary ? structuredClone(result.glossary) : null);
    setDirty(false);
    onMemoChange?.(result.glossary || null);
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

  function edit(mutator) {
    setDraft((current) => {
      const next = structuredClone(current);
      mutator(next);
      return next;
    });
    setDirty(true);
  }

  function updateRow(list, index, field, value) {
    edit((next) => {
      if (field === "protagonist") next.characters.forEach((row, i) => { row.protagonist = i === index; });
      else next[list][index][field] = value;
    });
  }

  function moveCharacter(index, step) {
    edit((next) => {
      const target = index + step;
      if (target < 0 || target >= next.characters.length) return;
      [next.characters[index], next.characters[target]] = [next.characters[target], next.characters[index]];
    });
  }

  const save = () => post("/api/glossary/save", {
    work_id: draft.work_id, title: draft.title, characters: draft.characters, terms: draft.terms, notes: draft.notes || "",
  }, "save");

  function close() {
    if (dirty && !window.confirm("저장하지 않은 작품 메모 변경이 있습니다. 닫을까요?")) return;
    if (dirty) { setDraft(structuredClone(data)); setDirty(false); }
    setOpen(false);
  }

  const characters = draft?.characters || [];
  const terms = draft?.terms || [];
  const label = data ? data.title : "작품 메모 연결";
  const summary = data ? `인물 ${data.characters.length} · 용어 ${data.terms.length}` : "인물·말투·용어";
  const showPicker = !data || changing;

  return <>
    <button className={`glossary-toggle ${data ? "linked" : ""}`} title={`${label} · ${summary}`} onClick={() => setOpen(true)} disabled={!sessionId}>
      <strong>{label} ▾</strong>
      <span>{summary}</span>
    </button>
    {open && <div className="glossary-backdrop" onClick={close}>
      <section ref={dialogRef} className="glossary-dialog memo" role="dialog" aria-modal="true" aria-label="작품 메모" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
        if (event.key === "Escape") { event.stopPropagation(); close(); }
        if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); if (dirty && !busy) save(); }
      }}>
        <header className="glossary-heading">
          <h2>작품 메모</h2>
          <div className="row">
            {data && !changing ? <button className="primary" disabled={!dirty || Boolean(busy) || ocrBusy} onClick={save}>{busy === "save" ? "저장 중…" : dirty ? "저장" : "저장됨"}</button> : null}
            <button onClick={close} aria-label="작품 메모 닫기">닫기</button>
          </div>
        </header>
        {error ? <p role="alert" className="glossary-error">{error}</p> : null}

        {showPicker ? <form className="memo-picker" onSubmit={(event) => {
          event.preventDefault();
          post("/api/glossary/bind", workId ? { work_id: workId } : { title: title || projectName }, "bind");
        }}>
          <p>같은 작품의 여러 권이 하나의 메모를 함께 씁니다.</p>
          {works.length ? <label>기존 작품 메모<select value={workId} onChange={(event) => setWorkId(event.target.value)}>
            <option value="">새 작품 만들기</option>
            {works.map((item) => <option key={item.work_id} value={item.work_id}>{item.title}</option>)}
          </select></label> : null}
          {!workId ? <label>작품명<input value={title} maxLength={200} placeholder={projectName || "작품명"} onChange={(event) => setTitle(event.target.value)} /></label> : null}
          <div className="row">
            <button className="primary" disabled={Boolean(busy) || (!workId && !(title || projectName).trim())}>{workId ? "이 메모 연결" : "만들고 연결"}</button>
            {changing ? <button type="button" onClick={() => setChanging(false)}>취소</button> : null}
          </div>
        </form> : null}

        {draft && !changing ? <>
          <div className="glossary-work">
            <input className="memo-title" aria-label="작품명" value={draft.title} maxLength={200} onChange={(event) => edit((next) => { next.title = event.target.value; })} />
            <button disabled={Boolean(busy)} onClick={() => setChanging(true)}>다른 작품 연결</button>
          </div>

          <h3>등장인물 · 말투 <small>번호 = 영역 분석 탭 화자 단축키 · 9 기타(전체 목록) · N 내레이션 · 0 해제</small></h3>
          <div className="memo-table characters">
            <div className="memo-row head"><span>키</span><span>이름</span><span>원문</span><span>호칭·약칭</span><span>성격</span><span>말투</span><span>주인공</span><span /></div>
            {characters.map((row, index) => <div className="memo-row" key={row.id || `new-${index}`}>
              <span className="memo-key">{index < 8 ? index + 1 : "9"}</span>
              <TableInput label="이름" value={row.name} onChange={(value) => updateRow("characters", index, "name", value)} />
              <TableInput label="원문 표기" value={row.original} onChange={(value) => updateRow("characters", index, "original", value)} />
              <TableInput label="호칭·약칭" value={row.aliases} onChange={(value) => updateRow("characters", index, "aliases", value)} />
              <TableInput label="성격" wide value={row.personality} onChange={(value) => updateRow("characters", index, "personality", value)} />
              <TableInput label="말투" wide value={row.speech} onChange={(value) => updateRow("characters", index, "speech", value)} />
              <input type="radio" name="memo-protagonist" aria-label={`${row.name || "인물"} 주인공`} checked={Boolean(row.protagonist)} onChange={() => updateRow("characters", index, "protagonist", true)} />
              <span className="memo-actions">
                <button aria-label="위로" disabled={index === 0} onClick={() => moveCharacter(index, -1)}>↑</button>
                <button aria-label="아래로" disabled={index === characters.length - 1} onClick={() => moveCharacter(index, 1)}>↓</button>
                <button aria-label="삭제" onClick={() => edit((next) => { next.characters.splice(index, 1); })}>×</button>
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
              <span className="memo-actions"><button aria-label="삭제" onClick={() => edit((next) => { next.terms.splice(index, 1); })}>×</button></span>
            </div>)}
          </div>
          <button onClick={() => edit((next) => { next.terms.push({ ...EMPTY_TERM }); })}>+ 용어</button>

          <label>작품 메모 (배경·호칭 규칙 등, 선택)<textarea rows={3} maxLength={4000} value={draft.notes || ""} onChange={(event) => edit((next) => { next.notes = event.target.value; })} /></label>

          <details className="memo-draft">
            <summary>URL로 초안 채우기 (OpenAI 1회)</summary>
            <form onSubmit={(event) => {
              event.preventDefault();
              if (dirty) { setError("먼저 변경 내용을 저장하세요."); return; }
              post("/api/glossary/draft", { work_id: data.work_id, url: sourceUrl, text: sourceText }, "draft");
            }}>
              <label>자료 URL (나무위키 등)<input type="url" required maxLength={2000} value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://namu.wiki/w/…" /></label>
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
