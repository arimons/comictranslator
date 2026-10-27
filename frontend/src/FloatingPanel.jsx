import { useEffect, useLayoutEffect, useRef, useState } from "react";

// 캔버스 위에 띄우는 패널. 머리줄을 끌어 옮기고, 접을 수 있고, 위치·접힘을 브라우저에 기억한다.
// 처음에는 부모(position: relative) 오른쪽 위에 붙는다.
const MARGIN = 8;

function readSaved(id) {
  try {
    return JSON.parse(window.localStorage.getItem(`floating-panel:${id}`) || "null");
  } catch {
    return null;
  }
}

function save(id, value) {
  try {
    window.localStorage.setItem(`floating-panel:${id}`, JSON.stringify(value));
  } catch { /* 저장소를 못 쓰면 이번 화면에서만 유지 */ }
}

export default function FloatingPanel({ id, title, subtitle, top = 56, className = "", children }) {
  const ref = useRef(null);
  const dragRef = useRef(null);
  const saved = useRef(readSaved(id));
  const [pos, setPos] = useState(saved.current?.pos || null);
  const [collapsed, setCollapsed] = useState(Boolean(saved.current?.collapsed));

  function clamp(next) {
    const el = ref.current;
    const parent = el?.offsetParent;
    if (!el || !parent) return next;
    const maxX = Math.max(MARGIN, parent.clientWidth - el.offsetWidth - MARGIN);
    const maxY = Math.max(MARGIN, parent.clientHeight - 40);
    return { x: Math.min(Math.max(MARGIN, next.x), maxX), y: Math.min(Math.max(MARGIN, next.y), maxY) };
  }

  // 저장된 위치가 없으면 오른쪽 위에 붙인다. 창 크기가 바뀌면 화면 안으로 다시 넣는다.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el?.offsetParent) return;
    if (!pos) setPos(clamp({ x: el.offsetParent.clientWidth - el.offsetWidth - 12, y: top }));
    else setPos(clamp(pos));
  }, []);

  useEffect(() => {
    const onResize = () => setPos((current) => (current ? clamp(current) : current));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (pos) save(id, { pos, collapsed });
  }, [id, pos, collapsed]);

  function onPointerDown(event) {
    if (event.button !== 0 || event.target.closest("button")) return;
    event.preventDefault();
    dragRef.current = { startX: event.clientX, startY: event.clientY, origin: pos || { x: 0, y: top } };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event) {
    const drag = dragRef.current;
    if (!drag) return;
    setPos(clamp({ x: drag.origin.x + event.clientX - drag.startX, y: drag.origin.y + event.clientY - drag.startY }));
  }

  function onPointerUp(event) {
    if (!dragRef.current) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  }

  return (
    <section
      ref={ref}
      className={`floating-panel ${collapsed ? "collapsed" : ""} ${className}`}
      style={pos ? { left: pos.x, top: pos.y } : { right: 12, top }}
      aria-label={title}
    >
      <header
        className="floating-panel-head"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        title="끌어서 옮기기"
      >
        <span className="floating-panel-grip" aria-hidden="true">⠿</span>
        <strong>{title}</strong>
        {subtitle ? <span className="floating-panel-sub">{subtitle}</span> : null}
        <button
          type="button"
          className="icon-button"
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? "패널 펼치기" : "패널 접기"}
          title={collapsed ? "펼치기" : "접기"}
        >
          {collapsed ? "▾" : "▴"}
        </button>
      </header>
      {collapsed ? null : <div className="floating-panel-body">{children}</div>}
    </section>
  );
}
