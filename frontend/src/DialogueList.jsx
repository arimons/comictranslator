import { useEffect, useRef } from "react";
import RegionTypeBadge from "./RegionTypeBadge.jsx";

// 글자 입히기 탭의 대사 리스트. 읽는 순서로 고정하고 행마다 원문·번역·검수 상태·경고를 함께 보여 준다.
// 검수 상태는 번역문과 함께 저장되므로 번역이 바뀌면 자동으로 미검수로 돌아간다(App의 dialogueRows).
export const DIALOGUE_STATUS_LABEL = { none: "미검수", ok: "확인", ask: "질문" };
export const DIALOGUE_FILTERS = [
  ["all", "전체"],
  ["none", "미검수"],
  ["ask", "질문"],
  ["warn", "경고"],
];

export function dialogueFilterMatches(row, filter) {
  if (filter === "warn") return row.warnings.length > 0;
  if (filter === "all") return true;
  return row.status === filter;
}

export default function DialogueList({ rows, selectedKey, extraKeys = [], filter, onFilter, onSelect, onStatus }) {
  const activeRef = useRef(null);
  const visible = rows.filter((row) => dialogueFilterMatches(row, filter));
  const okCount = rows.filter((row) => row.status === "ok").length;
  const askCount = rows.filter((row) => row.status === "ask").length;

  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [selectedKey]);

  return (
    <div className="dialogue-list">
      <div className="dialogue-list-head">
        <strong>대사 {okCount}/{rows.length}</strong>
        {askCount ? <span className="dialogue-ask-count">질문 {askCount}</span> : null}
        <div className="dialogue-filters" role="group" aria-label="대사 필터">
          {DIALOGUE_FILTERS.map(([id, label]) => (
            <button key={id} type="button" className={filter === id ? "active" : ""} onClick={() => onFilter(id)}>{label}</button>
          ))}
        </div>
      </div>
      {visible.length ? visible.map((row) => {
        const active = row.rid === selectedKey;
        const grouped = extraKeys.includes(row.rid);
        return (
          <div
            key={row.rid}
            ref={active ? activeRef : null}
            className={`dialogue-row status-${row.status} ${active ? "active" : ""} ${grouped ? "grouped" : ""}`}
            onClick={(event) => onSelect(row.rid, event)}
          >
            <div className="dialogue-row-head">
              <strong>{row.label}</strong>
              <RegionTypeBadge region={row.region} />
              {row.warnings.map((warning) => <span key={warning} className="dialogue-warning">{warning}</span>)}
              <span className="dialogue-status-actions" onClick={(event) => event.stopPropagation()}>
                <button type="button" className={row.status === "ok" ? "active ok" : ""} onClick={() => onStatus(row.rid, "ok")} title="확인 (Y)">확인</button>
                <button type="button" className={row.status === "ask" ? "active ask" : ""} onClick={() => onStatus(row.rid, "ask")} title="질문 표시 (Q)">질문</button>
              </span>
            </div>
            <p className="dialogue-original" lang="ja">{row.original || "원문 없음"}</p>
            <p className="dialogue-translation">{row.translation || "번역 없음"}</p>
          </div>
        );
      }) : <div className="empty compact-empty">조건에 맞는 대사 없음</div>}
    </div>
  );
}
