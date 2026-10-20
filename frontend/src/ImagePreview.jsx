import { useEffect, useRef, useState } from "react";

export default function ImagePreview({ src }) {
  const host = useRef(null);
  const imageRef = useRef(null);
  const [size, setSize] = useState({ width: 1, height: 1 });
  const [loaded, setLoaded] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [mode, setMode] = useState("fit");
  const [manualScale, setManualScale] = useState(1);
  const requestSrc = src && attempt ? `${src}${src.includes("?") ? "&" : "?"}preview_retry=${attempt}` : src;
  const image = loaded?.src === requestSrc ? loaded : { width: 0, height: 0 };
  function readImage(node) {
    if (node?.naturalWidth) setLoaded({ src: requestSrc, width: node.naturalWidth, height: node.naturalHeight });
  }
  useEffect(() => {
    setMode("fit");
    // 캐시된 이미지의 onLoad가 effect보다 먼저 와도 치수를 지우지 않는다.
    // 빠른 페이지 전환에서도 이미 로드된 치수를 복원한다.
    const node = imageRef.current;
    if (node?.complete) readImage(node);
  }, [requestSrc]);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const fit = Math.min((size.width - 20) / Math.max(image.width, 1), (size.height - 20) / Math.max(image.height, 1));
  const scale = Math.max(.02, mode === "fit" ? fit : mode === "width" ? (size.width - 20) / Math.max(image.width, 1) : manualScale);
  const zoom = (factor) => { setManualScale(Math.max(.02, Math.min(20, scale * factor))); setMode("manual"); };
  return <div className="scalable-preview">
    <div className="image-view-controls" aria-label="비교 이미지 확대">
      <button className={mode === "fit" ? "active" : ""} onClick={() => setMode("fit")}>전체 맞춤</button>
      <button className={mode === "width" ? "active" : ""} onClick={() => setMode("width")}>너비 맞춤</button>
      <button onClick={() => zoom(1 / 1.25)} disabled={!image.width} aria-label="비교 이미지 축소">−</button>
      <span>{!src ? "이미지 없음" : image.error ? "불러오기 실패" : image.width ? `${Math.round(scale * 100)}%` : "불러오는 중"}</span>
      <button onClick={() => zoom(1.25)} disabled={!image.width} aria-label="비교 이미지 확대">+</button>
    </div>
    <div className="scalable-preview-scroll" ref={host}>
      {image.error ? <div className="empty" role="status">미리보기를 불러오지 못했습니다. <button onClick={() => setAttempt((n) => n + 1)}>다시 불러오기</button></div> : null}
      {src ? <img key={requestSrc} ref={imageRef} src={requestSrc} alt="현재 페이지 비교 이미지" onLoad={(event) => readImage(event.currentTarget)}
        onError={() => setLoaded({ src: requestSrc, width: 0, height: 0, error: true })}
        style={image.width ? { width: image.width * scale, height: image.height * scale } : { display: "none" }} /> : <div className="empty">비교 이미지 없음</div>}
    </div>
  </div>;
}
