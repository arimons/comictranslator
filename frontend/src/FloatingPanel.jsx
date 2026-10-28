import { useEffect, useLayoutEffect, useRef, useState } from "react";

// 캔버스 위에 띄우는 패널. 머리줄을 끌어 옮기고, 접을 수 있고, 위치·접힘을 브라우저에 기억한다.
// 창 전체(position: fixed)에서 움직여 캔버스가 좁을 때 미리보기 쪽으로도 옮길 수 있다.
// 처음에는 부모 요소의 오른쪽 위에 붙고, 옮긴 뒤에는 창 오른쪽 끝에서의 거리로 기억해
// 창 폭이 바뀌어도 오른쪽에 남는다.
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
  const [pos, setPos] = useState(null);
  const [collapsed, setCollapsed] = useState(Boolean(saved.current?.collapsed));

  function clamp(next) {
    const el = ref.current;
    if (!el) return next;
    const maxX = Math.max(MARGIN, window.innerWidth - el.offsetWidth - MARGIN);
    const maxY = Math.max(MARGIN, window.innerHeight - 40);
    return { x: Math.min(Math.max(MARGIN, next.x), maxX), y: Math.min(Math.max(MARGIN, next.y), maxY) };
  }

  // 예전(부모 기준) 저장값은 쓰지 않고 새 기준으로 다시 잡는다
  const viewportSaved = saved.current?.scope === "viewport";
  const fromRightRef = useRef(viewportSaved && Number.isFinite(saved.current?.fromRight) ? saved.current.fromRight : null);
  const yRef = useRef(viewportSaved && Number.isFinite(saved.current?.y) ? saved.current.y : null);

  function placeFromRight() {
    const el = ref.current;
    if (!el) return;
    if (fromRightRef.current == null || yRef.current == null) {
      const box = el.parentElement?.getBoundingClientRect();
      if (box) {
        fromRightRef.current = window.innerWidth - box.right + 12;
        yRef.current = box.top + top;
      }
    }
    setPos(clamp({ x: window.innerWidth - el.offsetWidth - (fromRightRef.current ?? 12), y: yRef.current ?? top }));
  }

  // 창 크기가 바뀌면 오른쪽 거리를 지킨 채 화면 안으로 다시 넣는다.
  useLayoutEffect(() => {
    placeFromRight();
    const onResize = () => placeFromRight();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (fromRightRef.current != null) save(id, { scope: "viewport", fromRight: fromRightRef.current, y: yRef.current, collapsed });
  }, [id, collapsed]);

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
    const el = ref.current;
    if (el && pos) {
      fromRightRef.current = window.innerWidth - el.offsetWidth - pos.x;
      yRef.current = pos.y;
      save(id, { scope: "viewport", fromRight: fromRightRef.current, y: yRef.current, collapsed });
    }
  }

  return (
    <section
      ref={ref}
      className={`floating-panel ${collapsed ? "collapsed" : ""} ${className}`}
      style={pos ? { left: pos.x, top: pos.y } : { visibility: "hidden", left: 0, top: 0 }}
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
