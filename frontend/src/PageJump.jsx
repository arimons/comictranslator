import { useState } from "react";

// 쪽 번호를 입력해 바로 이동한다: [ 9 ] / 166. Enter나 칸을 벗어날 때 이동하고, 범위를 벗어난 번호는 가까운 쪽으로 맞춘다.
// 쪽이 바뀌면 부모가 key를 바꿔 입력칸을 현재 번호로 다시 채운다.
export default function PageJump({ current, total, disabled, onGo }) {
  const [draft, setDraft] = useState(String(current));

  function go() {
    const number = Number.parseInt(draft, 10);
    if (!Number.isFinite(number) || total < 1) {
      setDraft(String(current));
      return;
    }
    const target = Math.min(Math.max(number, 1), total);
    setDraft(String(target));
    if (target !== current) onGo(target);
  }

  return (
    <label className="page-jump" title="쪽 번호를 입력하고 Enter로 이동">
      <input
        type="text"
        inputMode="numeric"
        aria-label="이동할 쪽 번호"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value.replace(/[^0-9]/g, ""))}
        onFocus={(event) => event.target.select()}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); go(); event.target.blur(); }
          else if (event.key === "Escape") { setDraft(String(current)); event.target.blur(); }
          event.stopPropagation();
        }}
        onBlur={go}
      />
      <span>/ {total}</span>
    </label>
  );
}
