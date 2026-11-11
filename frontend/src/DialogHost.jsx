import { useEffect, useRef, useState } from "react";
import { subscribeDialog } from "./dialogs.js";

// askConfirm()/askText() 요청을 받아 확인창을 그린다. 홈과 편집기 화면에 하나씩 둔다.
// Enter는 확인, Esc와 바깥 클릭은 취소다. 되돌릴 수 없는 동작(danger)은 취소 버튼에 먼저 초점을 둔다.
export default function DialogHost() {
  const [dialog, setDialog] = useState(null);
  const [text, setText] = useState("");
  const confirmRef = useRef(null);
  const cancelRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => subscribeDialog(setDialog), []);

  useEffect(() => {
    if (!dialog) return undefined;
    setText(dialog.initialValue || "");
    const focusTimer = setTimeout(() => {
      if (dialog.kind === "text") { inputRef.current?.focus(); inputRef.current?.select(); }
      else (dialog.danger ? cancelRef.current : confirmRef.current)?.focus();
    }, 0);
    // 창이 떠 있는 동안 편집기 단축키가 같이 반응하지 않도록 키 입력을 여기서 끊는다.
    const onKey = (event) => {
      event.stopPropagation();
      if (event.key === "Escape") { event.preventDefault(); dialog.resolve(dialog.kind === "text" ? null : false); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => { clearTimeout(focusTimer); window.removeEventListener("keydown", onKey, true); };
  }, [dialog]);

  if (!dialog) return null;
  const cancel = () => dialog.resolve(dialog.kind === "text" ? null : false);
  const confirm = () => dialog.resolve(dialog.kind === "text" ? text : true);
  return (
    <div className="app-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) cancel(); }}>
      <form className="app-dialog" role="alertdialog" aria-modal="true" aria-label={dialog.title || "확인"} onSubmit={(event) => { event.preventDefault(); confirm(); }}>
        {dialog.title ? <h2>{dialog.title}</h2> : null}
        {dialog.message ? <p>{dialog.message}</p> : null}
        {dialog.kind === "text" ? <input ref={inputRef} value={text} onChange={(event) => setText(event.target.value)} placeholder={dialog.placeholder || ""} /> : null}
        <div className="app-dialog-actions">
          <button ref={cancelRef} type="button" onClick={cancel}>{dialog.cancelLabel || "취소"}</button>
          <button ref={confirmRef} type="submit" className={dialog.danger ? "danger" : "primary"}>{dialog.confirmLabel || "확인"}</button>
        </div>
      </form>
    </div>
  );
}
