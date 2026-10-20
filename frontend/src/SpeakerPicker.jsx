import { useEffect, useRef, useState } from "react";

// 기타(9) 화자 선택. 작품 메모의 전체 인물을 검색해 고른다.
// role="dialog" 안의 키 입력은 App 전역 단축키가 무시한다.
export default function SpeakerPicker({ open, characters, regionLabel, onPick, onClose }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  if (!open) return null;
  const needle = query.trim().toLowerCase();
  const list = characters.filter((row) => !needle
    || [row.name, row.original, row.aliases].some((value) => String(value || "").toLowerCase().includes(needle)));
  const index = Math.min(active, Math.max(0, list.length - 1));

  function onKeyDown(event) {
    if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
    if (event.key === "ArrowDown") { event.preventDefault(); setActive((index + 1) % Math.max(1, list.length)); return; }
    if (event.key === "ArrowUp") { event.preventDefault(); setActive((index - 1 + list.length) % Math.max(1, list.length)); return; }
    // 한글 조합 중 Enter도 바로 고른다. 조합 중인 글자도 이미 검색어에 들어가 있다.
    if (event.key === "Enter" && list[index]) { event.preventDefault(); onPick(list[index]); }
  }

  return <div className="glossary-backdrop" onMouseDown={onClose}>
    <section className="speaker-picker" role="dialog" aria-modal="true" aria-label="화자 선택" onMouseDown={(event) => event.stopPropagation()} onKeyDown={onKeyDown}>
      <header><strong>{regionLabel} 화자</strong><span>검색 · ↑↓ · Enter · Esc</span></header>
      <input ref={inputRef} autoFocus value={query} placeholder="이름·원문·호칭 검색" onChange={(event) => { setQuery(event.target.value); setActive(0); }} />
      <ul>
        {list.map((row, i) => <li key={row.id}>
          <button className={i === index ? "active" : ""} onMouseEnter={() => setActive(i)} onClick={() => onPick(row)}>
            <strong>{row.name}{row.protagonist ? " ★" : ""}</strong>
            <span>{[row.aliases, row.speech].filter(Boolean).join(" · ")}</span>
          </button>
        </li>)}
        {!list.length ? <li className="empty">일치하는 인물이 없습니다</li> : null}
      </ul>
    </section>
  </div>;
}
