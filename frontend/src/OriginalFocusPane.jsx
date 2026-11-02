import { useEffect, useRef, useState } from "react";

// 원본 이미지 패널. focusRect(이미지 좌표)가 있으면 캔버스의 확대 편집과 같은 영역으로 맞춰 확대한다.
export default function OriginalFocusPane({ src, focusRect }) {
  const hostRef = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [natural, setNatural] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => setSize({ width: host.clientWidth, height: host.clientHeight }));
    observer.observe(host);
    setSize({ width: host.clientWidth, height: host.clientHeight });
    return () => observer.disconnect();
  }, []);

  const ready = focusRect && natural.width && size.width && size.height;
  let transform = null;
  if (ready) {
    const [rx, ry, rw, rh] = focusRect.map(Number);
    // 캔버스 확대와 같은 비율(한쪽 축이 거의 차는 정도)로 맞춘다.
    const scale = Math.min(size.width / Math.max(1, rw), size.height / Math.max(1, rh)) * 0.72;
    transform = {
      scale,
      x: size.width / 2 - (rx + rw / 2) * scale,
      y: size.height / 2 - (ry + rh / 2) * scale,
      rect: [rx, ry, rw, rh],
    };
  }

  return (
    <div className="original-focus-pane" ref={hostRef}>
      {src ? (
        <div
          className={`original-focus-stage${transform ? " zoomed" : ""}`}
          style={transform
            ? { width: natural.width, height: natural.height, transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})` }
            : undefined}
        >
          <img
            src={src}
            alt=""
            onLoad={(event) => setNatural({ width: event.target.naturalWidth, height: event.target.naturalHeight })}
          />
          {transform ? (
            <div
              className="original-focus-box"
              style={{
                left: transform.rect[0],
                top: transform.rect[1],
                width: transform.rect[2],
                height: transform.rect[3],
                borderWidth: Math.max(1, 2 / transform.scale),
              }}
            />
          ) : null}
        </div>
      ) : <div className="empty">원본 이미지 없음</div>}
    </div>
  );
}
