import { useEffect, useRef, useState } from "react";

export default function ImagePreview({ src }) {
  const host = useRef(null);
  const [size, setSize] = useState({ width: 1, height: 1 });
  const [image, setImage] = useState({ width: 0, height: 0 });
  const [mode, setMode] = useState("fit");
  const [manualScale, setManualScale] = useState(1);
  useEffect(() => {
    setMode("fit");
    setImage({ width: 0, height: 0 });
  }, [src]);
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
      <span>{image.width ? `${Math.round(scale * 100)}%` : "불러오는 중"}</span>
      <button onClick={() => zoom(1.25)} disabled={!image.width} aria-label="비교 이미지 확대">+</button>
    </div>
    <div className="scalable-preview-scroll" ref={host}>
      {src ? <img key={src} src={src} alt="현재 페이지 비교 이미지" onLoad={(event) => setImage({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
        style={image.width ? { width: image.width * scale, height: image.height * scale } : { visibility: "hidden" }} /> : <div className="empty">비교 이미지 없음</div>}
    </div>
  </div>;
}
