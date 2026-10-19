import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./lib.js";

// 배경 글자 복원 탭: 문맥 영역(사각형)과 제거 마스크(브러시)를 따로 편집하고 LaMa로 복원한다.

const RESOLUTIONS = [
  ["512", "긴 변 512"],
  ["768", "긴 변 768"],
  ["original", "원본 크기"],
];
const RESTORE_TOOLS = [
  ["brush", "칠하기", "B"],
  ["eraser", "지우기", "E"],
  ["context", "문맥 영역", "C"],
  ["seed", "글자 영역 추가", "T"],
  ["pan", "이동", "H"],
];
const DEFAULT_SETTINGS = {
  engine: "lama",
  resolution: "original",
  feather_px: 0,
  source: "masked",
};
const MAX_UNDO = 20;

function clampRect(rect, width, height) {
  if (!rect) return null;
  let [x, y, w, h] = rect.map((v) => Math.round(Number(v) || 0));
  if (w < 0) { x += w; w = -w; }
  if (h < 0) { y += h; h = -h; }
  const x1 = Math.max(0, x);
  const y1 = Math.max(0, y);
  const x2 = Math.min(width, x + w);
  const y2 = Math.min(height, y + h);
  if (x2 - x1 < 2 || y2 - y1 < 2) return null;
  return [x1, y1, x2 - x1, y2 - y1];
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`이미지를 불러오지 못했습니다: ${url}`));
    img.src = url;
  });
}

// 흑백(불투명) 마스크 PNG → 흰색+알파 마스크 캔버스로 변환
async function maskImageToCanvas(url, canvas, mode = "replace") {
  const img = await loadImage(url);
  const tmp = document.createElement("canvas");
  tmp.width = canvas.width;
  tmp.height = canvas.height;
  const tctx = tmp.getContext("2d");
  tctx.imageSmoothingEnabled = false;
  tctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  const data = tctx.getImageData(0, 0, tmp.width, tmp.height);
  const px = data.data;
  for (let i = 0; i < px.length; i += 4) {
    const on = px[i] > 127 && (px[i + 3] > 127);
    px[i] = 255; px[i + 1] = 255; px[i + 2] = 255;
    px[i + 3] = on ? 255 : 0;
  }
  tctx.putImageData(data, 0, 0);
  const ctx = canvas.getContext("2d");
  if (mode === "replace") ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.globalCompositeOperation = "source-over";
  ctx.drawImage(tmp, 0, 0);
}

function countMaskPixels(canvas) {
  if (!canvas) return 0;
  const px = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
  let count = 0;
  for (let i = 3; i < px.length; i += 4) if (px[i] > 127) count += 1;
  return count;
}

function maskBBox(canvas) {
  const { width, height } = canvas;
  const px = canvas.getContext("2d").getImageData(0, 0, width, height).data;
  let x1 = width, y1 = height, x2 = -1, y2 = -1;
  for (let y = 0; y < height; y += 1) {
    const row = y * width * 4;
    for (let x = 0; x < width; x += 1) {
      if (px[row + x * 4 + 3] > 127) {
        if (x < x1) x1 = x;
        if (x > x2) x2 = x;
        if (y < y1) y1 = y;
        if (y > y2) y2 = y;
      }
    }
  }
  return x2 < 0 ? null : [x1, y1, x2 - x1 + 1, y2 - y1 + 1];
}

function fmtSec(value) {
  return value === null || value === undefined ? "-" : `${Number(value).toFixed(1)}s`;
}

export function useRestore({ sessionId, image, active, setStatus }) {
  const [info, setInfo] = useState(null);
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [maskParams, setMaskParams] = useState({ dilate_px: 4, polarity: "auto", threshold: "", min_area: 10 });
  const [contextRect, setContextRect] = useState(null);
  const [seedRects, setSeedRects] = useState([]);
  const [tool, setTool] = useState("brush");
  const [brushSize, setBrushSize] = useState(8);
  const [viewBase, setViewBase] = useState("original");
  const [showMask, setShowMask] = useState(true);
  const [jobs, setJobs] = useState([]);
  const [maskPixels, setMaskPixels] = useState(0);
  const [maskVersion, setMaskVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const maskCanvasRef = useRef(null);
  const undoRef = useRef([]);
  const imageKey = image ? `${sessionId}:${image.id}` : "";

  const bumpMask = useCallback(() => {
    setMaskVersion((v) => v + 1);
    setMaskPixels(countMaskPixels(maskCanvasRef.current));
  }, []);

  const refreshInfo = useCallback(async () => {
    if (!sessionId || !image) return null;
    const next = await api(`/api/restore/info?session_id=${encodeURIComponent(sessionId)}&image_id=${encodeURIComponent(image.id)}`);
    setInfo(next);
    return next;
  }, [sessionId, image?.id]);

  // 이미지가 바뀌면 상태와 마스크를 새로 읽는다
  useEffect(() => {
    if (!active || !sessionId || !image) return;
    let cancelled = false;
    (async () => {
      try {
        setError("");
        const next = await refreshInfo();
        if (cancelled || !next) return;
        const canvas = document.createElement("canvas");
        canvas.width = next.width;
        canvas.height = next.height;
        maskCanvasRef.current = canvas;
        undoRef.current = [];
        if (next.mask_url) await maskImageToCanvas(next.mask_url, canvas);
        if (cancelled) return;
        setContextRect(next.state?.context_rect || null);
        setSeedRects((next.state?.extra_seed_rects || []).map((rect) => ({ rect, source: "user" })));
        setSettings((s) => ({ ...s, source: next.has_masked ? s.source : "original" }));
        if (next.state?.mask_params) {
          setMaskParams((p) => ({ ...p, ...next.state.mask_params, threshold: next.state.mask_params.threshold ?? "" }));
        }
        bumpMask();
      } catch (exc) {
        if (!cancelled) setError(String(exc.message || exc));
      }
    })();
    return () => { cancelled = true; };
  }, [active, imageKey]);

  // 진행 중인 작업 폴링 (화면은 멈추지 않는다)
  const hasActiveJob = jobs.some((job) => job.status === "queued" || job.status === "running");
  useEffect(() => {
    if (!hasActiveJob) return undefined;
    const timer = setInterval(async () => {
      const pending = jobs.filter((job) => job.status === "queued" || job.status === "running");
      const updates = await Promise.all(pending.map((job) => api(`/api/restore/job/${job.job_id}`).catch(() => null)));
      setJobs((current) => current.map((job) => updates.find((u) => u && u.job_id === job.job_id) || job));
    }, 800);
    return () => clearInterval(timer);
  }, [hasActiveJob, jobs]);

  function pushUndo() {
    const canvas = maskCanvasRef.current;
    if (!canvas) return;
    undoRef.current.push(canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height));
    if (undoRef.current.length > MAX_UNDO) undoRef.current.shift();
  }

  function undoMask() {
    const canvas = maskCanvasRef.current;
    const snapshot = undoRef.current.pop();
    if (!canvas || !snapshot) return;
    canvas.getContext("2d").putImageData(snapshot, 0, 0);
    bumpMask();
    setStatus?.("마스크 되돌리기");
  }

  function clearMask() {
    const canvas = maskCanvasRef.current;
    if (!canvas) return;
    pushUndo();
    canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    bumpMask();
  }

  function maskParamsBody() {
    const threshold = maskParams.threshold === "" || maskParams.threshold === null ? null : Number(maskParams.threshold);
    return { dilate_px: Number(maskParams.dilate_px), polarity: maskParams.polarity, threshold, min_area: Number(maskParams.min_area) };
  }

  async function autoMask() {
    if (!sessionId || !image) return;
    setBusy(true);
    setError("");
    try {
      const userSeeds = seedRects.filter((s) => s.source === "user").map((s) => s.rect);
      const res = await api("/api/restore/auto-mask", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, image_id: image.id, ...maskParamsBody(), use_region_seeds: true, extra_seed_rects: userSeeds }),
      });
      setInfo(res);
      pushUndo();
      if (res.mask_url) await maskImageToCanvas(res.mask_url, maskCanvasRef.current);
      setSeedRects(res.seeds || []);
      if (!contextRect && res.suggested_context_rect) {
        setContextRect(res.suggested_context_rect);
        setStatus?.("문맥 영역을 마스크 기준으로 제안했습니다. 컷 경계에 맞게 조정하세요.");
      } else {
        setStatus?.(`자동 마스크 ${res.mask_pixels}px`);
      }
      bumpMask();
    } catch (exc) {
      setError(String(exc.message || exc));
    } finally {
      setBusy(false);
    }
  }

  // 새 글자 영역은 편집 중인 마스크에 합친다(손으로 고친 부분은 유지)
  async function addSeedRect(rect) {
    if (!sessionId || !image || !rect) return;
    try {
      const res = await api("/api/restore/auto-mask", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, image_id: image.id, ...maskParamsBody(), use_region_seeds: false, extra_seed_rects: [rect], save: false }),
      });
      pushUndo();
      if (res.mask_png) await maskImageToCanvas(res.mask_png, maskCanvasRef.current, "merge");
      setSeedRects((current) => [...current, { rect, source: "user" }]);
      bumpMask();
      setStatus?.(`글자 영역 추가: 획 ${res.mask_pixels}px`);
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }

  function suggestContextFromMask(margin = 96) {
    const canvas = maskCanvasRef.current;
    if (!canvas) return;
    const bbox = maskBBox(canvas);
    if (!bbox) {
      setStatus?.("마스크가 비어 있습니다");
      return;
    }
    setContextRect(clampRect([bbox[0] - margin, bbox[1] - margin, bbox[2] + margin * 2, bbox[3] + margin * 2], canvas.width, canvas.height));
  }

  function fullPageContext() {
    if (info) setContextRect([0, 0, info.width, info.height]);
  }

  async function runEngine(engine, overrides = {}) {
    if (!sessionId || !image) return null;
    if (!contextRect) {
      setError("문맥 영역을 먼저 지정하세요 (C 도구로 해당 컷을 사각형으로 감싸기)");
      return null;
    }
    if (!maskPixels) {
      setError("제거 마스크가 비어 있습니다");
      return null;
    }
    setError("");
    const body = {
      session_id: sessionId,
      image_id: image.id,
      engine,
      context_rect: contextRect,
      mask_png: maskCanvasRef.current.toDataURL("image/png"),
      resolution: settings.resolution,
      feather_px: Number(settings.feather_px),
      source: settings.source,
      ...overrides,
    };
    const job = await api("/api/restore/run", { method: "POST", body: JSON.stringify(body) });
    setJobs((current) => [{ ...job, batch: overrides.batch || job.job_id }, ...current]);
    setStatus?.(`${engine.toUpperCase()} 복원 시작`);
    return job;
  }

  async function run(engine = settings.engine) {
    try {
      await runEngine(engine);
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }

  async function accept(job) {
    try {
      const next = await api("/api/restore/accept", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: image.id, job_id: job.job_id }) });
      setInfo(next);
      setJobs((current) => current.map((j) => (j.job_id === job.job_id ? { ...j, accepted: true } : j)));
      setViewBase("current");
      setStatus?.("복원 결과를 확정했습니다. '글자 입히기' 렌더에 반영됩니다.");
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }

  async function removePatch(patchId) {
    try {
      setInfo(await api("/api/restore/patch/remove", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: image.id, patch_id: patchId }) }));
    } catch (exc) {
      setError(String(exc.message || exc));
    }
  }

  return {
    info, settings, setSettings, maskParams, setMaskParams,
    contextRect, setContextRect, seedRects, tool, setTool, brushSize, setBrushSize, viewBase, setViewBase,
    showMask, setShowMask, jobs: jobs.filter((job) => job.image_id === image?.id), maskPixels, maskVersion, busy, error, setError,
    maskCanvasRef, pushUndo, undoMask, clearMask, bumpMask, autoMask, addSeedRect, suggestContextFromMask, fullPageContext,
    run, accept, removePatch, hasActiveJob,
  };
}

export function RestoreStage({ restore, setStatus }) {
  const hostRef = useRef(null);
  const canvasRef = useRef(null);
  const baseImgRef = useRef(null);
  const viewRef = useRef({ scale: 1, x: 0, y: 0, key: "" });
  const dragRef = useRef(null);
  const spaceRef = useRef(false);
  const hoverRef = useRef(null);
  const tintRef = useRef(null);
  const [baseVersion, setBaseVersion] = useState(0);
  const [lightbox, setLightbox] = useState(null);
  const { info, maskCanvasRef, contextRect, seedRects, tool, brushSize, viewBase, showMask, maskVersion } = restore;

  const baseUrl = !info ? null
    : viewBase === "current" ? (info.preview_url || info.masked_url || info.input_url)
      : viewBase === "masked" ? (info.masked_url || info.input_url)
        : info.input_url;

  useEffect(() => {
    if (!baseUrl) return;
    let cancelled = false;
    loadImage(baseUrl).then((img) => {
      if (cancelled) return;
      baseImgRef.current = img;
      setBaseVersion((v) => v + 1);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [baseUrl]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    const img = baseImgRef.current;
    if (!canvas || !host) return;
    const dpr = window.devicePixelRatio || 1;
    const w = host.clientWidth;
    const h = host.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = getComputedStyle(host).getPropertyValue("--restore-stage-bg") || "#e5e7eb";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (!img || !info) return;
    const view = viewRef.current;
    const key = `${info.image_id}:${info.width}x${info.height}`;
    if (view.key !== key) {
      const scale = Math.min(w / info.width, h / info.height) * 0.96;
      viewRef.current = { key, scale, x: (w - info.width * scale) / 2, y: (h - info.height * scale) / 2 };
    }
    const { scale, x, y } = viewRef.current;
    ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * x, dpr * y);
    ctx.imageSmoothingEnabled = scale < 1;
    ctx.drawImage(img, 0, 0, info.width, info.height);
    const mask = maskCanvasRef.current;
    if (mask && showMask) {
      let tint = tintRef.current;
      if (!tint || tint.width !== mask.width || tint.height !== mask.height) {
        tint = document.createElement("canvas");
        tint.width = mask.width;
        tint.height = mask.height;
        tintRef.current = tint;
      }
      const tctx = tint.getContext("2d");
      tctx.globalCompositeOperation = "source-over";
      tctx.clearRect(0, 0, tint.width, tint.height);
      tctx.drawImage(mask, 0, 0);
      tctx.globalCompositeOperation = "source-in";
      tctx.fillStyle = "rgba(230, 20, 60, 0.55)";
      tctx.fillRect(0, 0, tint.width, tint.height);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(tint, 0, 0);
    }
    const line = 1 / scale;
    (seedRects || []).forEach((seed) => {
      const [sx, sy, sw, sh] = seed.rect;
      ctx.strokeStyle = seed.source === "user" ? "rgba(234,179,8,0.95)" : "rgba(234,179,8,0.6)";
      ctx.setLineDash([4 * line, 3 * line]);
      ctx.lineWidth = line;
      ctx.strokeRect(sx, sy, sw, sh);
    });
    if (contextRect) {
      const [cx, cy, cw, ch] = contextRect;
      ctx.setLineDash([8 * line, 4 * line]);
      ctx.lineWidth = 2 * line;
      ctx.strokeStyle = "rgba(37,99,235,0.95)";
      ctx.strokeRect(cx, cy, cw, ch);
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(37,99,235,0.95)";
      ctx.font = `${12 * line}px sans-serif`;
      ctx.fillText(`문맥 ${cw}×${ch}`, cx + 3 * line, cy - 4 * line);
    }
    ctx.setLineDash([]);
    const drag = dragRef.current;
    if (drag && drag.rect) {
      const [rx, ry, rw, rh] = drag.rect;
      ctx.strokeStyle = drag.mode === "context" ? "rgba(37,99,235,0.95)" : "rgba(234,179,8,0.95)";
      ctx.lineWidth = 2 * line;
      ctx.strokeRect(rx, ry, rw, rh);
    }
    const hover = hoverRef.current;
    if (hover && (tool === "brush" || tool === "eraser")) {
      ctx.beginPath();
      ctx.arc(hover.x, hover.y, brushSize / 2, 0, Math.PI * 2);
      ctx.lineWidth = line;
      ctx.strokeStyle = tool === "brush" ? "rgba(230,20,60,0.95)" : "rgba(17,24,39,0.9)";
      ctx.stroke();
    }
  }, [info, contextRect, seedRects, tool, brushSize, showMask, maskCanvasRef]);

  useEffect(() => { draw(); }, [draw, baseVersion, maskVersion]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const observer = new ResizeObserver(() => draw());
    observer.observe(host);
    return () => observer.disconnect();
  }, [draw]);

  function toImage(event) {
    const rect = canvasRef.current.getBoundingClientRect();
    const { scale, x, y } = viewRef.current;
    return { x: (event.clientX - rect.left - x) / scale, y: (event.clientY - rect.top - y) / scale };
  }

  function paintSegment(from, to) {
    const mask = maskCanvasRef.current;
    if (!mask) return;
    const ctx = mask.getContext("2d");
    ctx.globalCompositeOperation = tool === "eraser" ? "destination-out" : "source-over";
    ctx.strokeStyle = "#fff";
    ctx.fillStyle = "#fff";
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = brushSize;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x + 0.01, to.y + 0.01);
    ctx.stroke();
    ctx.globalCompositeOperation = "source-over";
  }

  function onPointerDown(event) {
    if (!info) return;
    canvasRef.current.setPointerCapture(event.pointerId);
    const point = toImage(event);
    if (event.button === 1 || event.button === 2 || tool === "pan" || spaceRef.current) {
      dragRef.current = { mode: "pan", sx: event.clientX, sy: event.clientY, ox: viewRef.current.x, oy: viewRef.current.y };
      return;
    }
    if (tool === "brush" || tool === "eraser") {
      restore.pushUndo();
      dragRef.current = { mode: "paint", last: point };
      paintSegment(point, point);
      draw();
      return;
    }
    dragRef.current = { mode: tool, start: point, rect: [point.x, point.y, 0, 0] };
  }

  function onPointerMove(event) {
    const point = toImage(event);
    hoverRef.current = point;
    const drag = dragRef.current;
    if (drag?.mode === "pan") {
      viewRef.current = { ...viewRef.current, x: drag.ox + event.clientX - drag.sx, y: drag.oy + event.clientY - drag.sy };
    } else if (drag?.mode === "paint") {
      paintSegment(drag.last, point);
      drag.last = point;
    } else if (drag?.start) {
      drag.rect = [drag.start.x, drag.start.y, point.x - drag.start.x, point.y - drag.start.y];
    }
    draw();
  }

  function onPointerUp() {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag || !info) return;
    if (drag.mode === "paint") {
      restore.bumpMask();
    } else if (drag.mode === "context") {
      const rect = clampRect(drag.rect, info.width, info.height);
      if (rect) {
        restore.setContextRect(rect);
        setStatus?.(`문맥 영역 ${rect[2]}×${rect[3]}`);
      }
    } else if (drag.mode === "seed") {
      const rect = clampRect(drag.rect, info.width, info.height);
      if (rect) restore.addSeedRect(rect);
    }
    draw();
  }

  function onWheel(event) {
    event.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const view = viewRef.current;
    const factor = Math.exp(-event.deltaY * 0.0015);
    const scale = Math.min(16, Math.max(0.05, view.scale * factor));
    viewRef.current = { ...view, scale, x: mx - ((mx - view.x) * scale) / view.scale, y: my - ((my - view.y) * scale) / view.scale };
    draw();
  }

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  });

  useEffect(() => {
    function isTyping(event) {
      const tag = event.target?.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target?.isContentEditable;
    }
    function down(event) {
      if (isTyping(event)) return;
      if (event.code === "Space") { spaceRef.current = true; event.preventDefault(); return; }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); restore.undoMask(); return; }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const found = RESTORE_TOOLS.find(([, , key]) => key.toLowerCase() === event.key.toLowerCase());
      if (found) { restore.setTool(found[0]); return; }
      if (event.key === "[") restore.setBrushSize((v) => Math.max(1, v - 2));
      if (event.key === "]") restore.setBrushSize((v) => Math.min(80, v + 2));
      if (event.key.toLowerCase() === "m") restore.setShowMask((v) => !v);
    }
    function up(event) {
      if (event.code === "Space") spaceRef.current = false;
    }
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [restore]);

  const doneJobs = restore.jobs.filter((job) => job.status === "done");
  const latestBatch = restore.jobs[0]?.batch;
  const compareJobs = (latestBatch ? restore.jobs.filter((job) => job.batch === latestBatch) : []).slice().reverse();
  const reference = compareJobs.find((job) => job.status === "done") || doneJobs[0];

  return (
    <div className="restore-workspace">
      <section className="viewer-card restore-stage-card">
        <div className="card-head editor-head restore-stage-head">
          <div className="editor-title">
            <span>배경 글자 복원</span>
            <strong>{info ? `${info.width}×${info.height}${info.is_grayscale ? " · 흑백" : " · 컬러"}` : "-"}</strong>
          </div>
          <div className="restore-toolbar">
            {RESTORE_TOOLS.map(([id, label, key]) => (
              <button key={id} className={tool === id ? "active" : ""} onClick={() => restore.setTool(id)} title={`${label} (${key})`}>
                {label} <kbd>{key}</kbd>
              </button>
            ))}
            <label className="restore-brush">
              브러시 {brushSize}px
              <input type="range" min="1" max="80" value={brushSize} onChange={(e) => restore.setBrushSize(Number(e.target.value))} />
            </label>
            <select value={viewBase} onChange={(e) => restore.setViewBase(e.target.value)} title="바탕 이미지">
              <option value="original">원본</option>
              <option value="masked">Step 3 결과</option>
              <option value="current">확정 패치 반영</option>
            </select>
            <button className={showMask ? "active" : ""} onClick={() => restore.setShowMask(!showMask)} title="마스크 표시 (M)">마스크 <kbd>M</kbd></button>
          </div>
        </div>
        <div className="restore-stage-body" ref={hostRef}>
          <canvas
            ref={canvasRef}
            className={`restore-canvas tool-${tool}`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={() => { hoverRef.current = null; draw(); }}
            onContextMenu={(e) => e.preventDefault()}
          />
          {!info ? <div className="empty restore-empty">이미지를 선택하세요</div> : null}
        </div>
      </section>
      <section className="viewer-card restore-compare-card">
        <div className="card-head">
          <div className="editor-title">
            <span>비교</span>
            <strong>{compareJobs.length ? compareJobs.map((job) => job.engine.toUpperCase()).join(" vs ") : "결과 없음"}</strong>
          </div>
          <span className="restore-hint">이미지를 누르면 확대합니다. 마스크 바깥 변경 픽셀은 항상 0이어야 합니다.</span>
        </div>
        <div className="restore-compare-strip">
          {reference?.result ? (
            <>
              <figure onClick={() => setLightbox({ url: reference.result.crop_before_url, title: "원본(문맥 영역)" })}>
                <img src={reference.result.crop_before_url} alt="원본" />
                <figcaption>원본</figcaption>
              </figure>
              <figure onClick={() => setLightbox({ url: reference.result.crop_mask_url, title: "제거 마스크" })}>
                <img src={reference.result.crop_mask_url} alt="마스크" />
                <figcaption>마스크 {reference.meta?.mask_pixels}px</figcaption>
              </figure>
            </>
          ) : null}
          {compareJobs.map((job) => (
            <figure key={job.job_id} className={`restore-job-figure status-${job.status}`} onClick={() => job.result && setLightbox({ url: job.result.crop_after_url, title: `${job.engine.toUpperCase()} ${job.job_id}` })}>
              {job.result ? <img src={job.result.crop_after_url} alt={job.engine} /> : (
                <div className="restore-job-placeholder">
                  {job.status === "error" ? "실패" : job.status === "queued" ? "대기" : `${job.progress?.step || 0}/${job.progress?.total || "-"}`}
                </div>
              )}
              <figcaption>
                {job.engine.toUpperCase()} · {job.meta?.params?.resolution || ""}
                {job.meta ? ` · 복원 ${fmtSec(job.meta.timing?.infer_sec)}` : ""}
                {job.meta ? ` · 바깥 변경 ${job.meta.outside_mask_changed_pixels}` : ""}
              </figcaption>
            </figure>
          ))}
        </div>
      </section>
      {lightbox ? (
        <div className="notice-backdrop" role="presentation" onClick={() => setLightbox(null)}>
          <div className="restore-lightbox" role="dialog" aria-label={lightbox.title} onClick={(e) => e.stopPropagation()}>
            <div className="notice-head">
              <strong>{lightbox.title}</strong>
              <button className="icon-button" onClick={() => setLightbox(null)} title="닫기">×</button>
            </div>
            <div className="restore-lightbox-body">
              <img src={lightbox.url} alt={lightbox.title} />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function RestoreControls({ restore }) {
  const { info, settings, setSettings, maskParams, setMaskParams, contextRect, jobs, maskPixels, busy, error } = restore;
  const set = (key) => (event) => setSettings((s) => ({ ...s, [key]: event.target.value }));
  const setMask = (key) => (event) => setMaskParams((p) => ({ ...p, [key]: event.target.value }));

  return (
    <>
      <div className="section compact-section">
        <h2>제거 마스크</h2>
        <div className="restore-form-grid">
          <label>확장(px)
            <input type="number" min="0" max="32" value={maskParams.dilate_px} onChange={setMask("dilate_px")} />
          </label>
          <label>극성
            <select value={maskParams.polarity} onChange={setMask("polarity")}>
              <option value="auto">자동</option>
              <option value="dark">어두운 글자</option>
              <option value="bright">밝은 글자</option>
            </select>
          </label>
          <label>임계값
            <input type="number" min="0" max="255" placeholder="기본" value={maskParams.threshold} onChange={setMask("threshold")} />
          </label>
          <label>최소 성분
            <input type="number" min="1" max="500" value={maskParams.min_area} onChange={setMask("min_area")} />
          </label>
        </div>
        <p className="restore-hint">확장은 외곽선·그림자·안티앨리어싱까지 덮는 폭입니다. 자동 마스크는 손으로 고친 마스크를 덮어씁니다(⌘Z로 되돌리기).</p>
        <div className="side-action-grid">
          <button onClick={restore.autoMask} disabled={busy || !info}>자동 마스크</button>
          <button onClick={restore.undoMask} disabled={!info}>되돌리기</button>
          <button className="danger" onClick={restore.clearMask} disabled={!info}>마스크 비우기</button>
          <span className="restore-metric">{maskPixels.toLocaleString()} px</span>
        </div>
      </div>

      <div className="section compact-section">
        <h2>문맥 영역</h2>
        <p className="restore-hint">해당 컷 전체를 C 도구로 감싸세요. 말풍선 경계는 컷 경계가 아닙니다.</p>
        <div className="restore-context-value">{contextRect ? `[${contextRect.join(", ")}]` : "지정 안 됨"}</div>
        <div className="side-action-grid">
          <button onClick={() => restore.suggestContextFromMask()} disabled={!info}>마스크 + 여백</button>
          <button onClick={restore.fullPageContext} disabled={!info}>페이지 전체</button>
        </div>
      </div>

      <div className="section compact-section">
        <h2>복원 설정</h2>
        <div className="restore-form-grid">
          <label>처리 해상도
            <select value={settings.resolution} onChange={set("resolution")}>
              {RESOLUTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
          <label>경계 블렌딩(px)
            <input type="number" min="0" max="8" value={settings.feather_px} onChange={set("feather_px")} />
          </label>
          <label className="restore-span-2">입력 페이지
            <select value={settings.source} onChange={set("source")}>
              <option value="masked" disabled={!info?.has_masked}>현재 결과(Step 3 + 확정 패치)</option>
              <option value="original">원본</option>
            </select>
          </label>
        </div>
        <div className="side-action-grid">
          <button className="primary" onClick={() => restore.run()} disabled={!info}>복원 실행</button>
        </div>
        {error ? <div className="restore-error">{error}</div> : null}
      </div>

      <div className="section compact-section">
        <h2>작업</h2>
        <div className="restore-job-list">
          {jobs.length ? jobs.map((job) => (
            <div key={job.job_id} className={`restore-job status-${job.status}`}>
              <div className="restore-job-head">
                <strong>{job.engine.toUpperCase()}</strong>
                <span>{job.status}</span>
              </div>
              {job.status === "running" || job.status === "queued" ? (
                <div className="task-progress-bar"><i style={{ left: 0, width: `${Math.round(100 * (job.progress?.step || 0) / Math.max(1, job.progress?.total || 1))}%`, animation: "none" }} /></div>
              ) : null}
              {job.meta ? (
                <div className="restore-job-meta">
                  {job.meta.transform.model_w}×{job.meta.transform.model_h} · 로딩 {fmtSec(job.meta.timing.load_sec)} · 복원 {fmtSec(job.meta.timing.infer_sec)}
                  {job.meta.mask_pixels_dropped_outside_context ? ` · 문맥 밖 마스크 ${job.meta.mask_pixels_dropped_outside_context}px 제외` : ""}
                </div>
              ) : null}
              {job.status === "error" ? (
                <>
                  <div className="restore-error">{job.error}</div>
                  <div className="restore-hint">기존 결과는 그대로입니다.</div>
                  <button onClick={() => restore.run()}>다시 시도</button>
                </>
              ) : null}
              {job.status === "done" ? (
                <button className="primary" onClick={() => restore.accept(job)} disabled={job.accepted}>{job.accepted ? "확정됨" : "이 결과 확정"}</button>
              ) : null}
            </div>
          )) : <div className="empty compact-empty">아직 실행한 작업이 없습니다</div>}
        </div>
      </div>

      <div className="section compact-section">
        <h2>확정된 패치</h2>
        <p className="restore-hint">'글자 입히기' 렌더 때 마스크 내부에만 합성됩니다.</p>
        <div className="restore-job-list">
          {(info?.patches || []).length ? info.patches.map((patch) => (
            <div key={patch.id} className="restore-job">
              <div className="restore-job-head">
                <strong>{patch.engine.toUpperCase()}</strong>
                <span>[{(patch.rect || []).join(", ")}]</span>
              </div>
              <button className="danger" onClick={() => restore.removePatch(patch.id)}>제거</button>
            </div>
          )) : <div className="empty compact-empty">없음</div>}
        </div>
      </div>
    </>
  );
}
