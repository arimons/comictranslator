import { KEYMAP_GROUPS, formatCombo, helpEntriesFor } from "./keymap.js";

const TAB_TITLES = {
  guidance: "말풍선 분석",
  ocr: "글자인식/번역",
  overlay: "글자 입히기",
  export: "내보내기",
};

// ? 키로 여는 단축키 도움말. keymap.js에서 현재 탭에 해당하는 항목만 묶어 보여 준다.
export default function ShortcutHelp({ open, activeTab, onClose }) {
  if (!open) return null;
  const entries = helpEntriesFor(activeTab);
  const groups = KEYMAP_GROUPS
    .map((group) => [group, entries.filter((entry) => entry.group === group)])
    .filter(([, items]) => items.length);
  return (
    <div className="notice-backdrop shortcut-help-backdrop" role="presentation" onClick={onClose}>
      <div className="shortcut-help" role="dialog" aria-modal="true" aria-label="단축키 도움말" onClick={(event) => event.stopPropagation()}>
        <div className="notice-head">
          <strong>단축키 · {TAB_TITLES[activeTab] || activeTab}</strong>
          <button className="icon-button" onClick={onClose} title="닫기 (Esc)">×</button>
        </div>
        <div className="shortcut-help-groups">
          {groups.map(([group, items]) => (
            <section key={group} className="shortcut-help-group">
              <h3>{group}</h3>
              <dl>
                {items.map((entry) => (
                  <div key={entry.id} className={`shortcut-help-row ${entry.kind === "pointer" ? "pointer" : ""}`}>
                    <dt>
                      {entry.keys.map((combo, index) => (
                        <span key={index} className="shortcut-help-combo">
                          {combo.map((token, tokenIndex) => <kbd key={tokenIndex}>{formatCombo([token])}</kbd>)}
                        </span>
                      ))}
                    </dt>
                    <dd>{entry.label}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <p className="shortcut-help-foot">입력칸에 글자를 쓰는 중이거나 한글 조합 중에는 글자·숫자 단축키가 동작하지 않습니다.</p>
      </div>
    </div>
  );
}
