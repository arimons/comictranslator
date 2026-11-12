import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import Konva from "konva";
import {
  OVERLAY_HANDLE_NAMES,
  OVERLAY_MIN_RECT_SIZE,
  overlayItemKey,
  overlayItemLabel,
  overlayControlItems,
  overlayRegionColor,
  rectsApproximatelyEqual,
  regionDisplayRect,
  canRenderOverlayImage,
  baseRegionText,
  overlayRegionValue,
  overlayEraserRects,
  overlayItemFontSize,
  overlayWritingModeChoice,
  inferredRegionFontStyle,
  overlayLayoutEntry,
  defaultOverlayGuidance,
  FONT_STYLES,
} from "./lib.js";
import RegionTypeBadge from "./RegionTypeBadge.jsx";
import { TEXT_BASED, regionProcessingBasis } from "./regionTypes.js";
import { createViewport } from "./viewportControls.js";
import { shortcutFor } from "./keymap.js";

// 툴바 아이콘: 정렬 줄 모양 / 가로·세로쓰기 / 글자·외곽선 "A"
const ALIGN_LINES = {
  left: [[3, 15], [3, 10], [3, 15], [3, 8]],
  center: [[3, 15], [6, 12], [3, 15], [7, 11]],
  right: [[3, 15], [8, 15], [3, 15], [10, 15]],
};

function AlignIcon({ kind }) {
  return (
    <svg width="18" height="16" viewBox="0 0 18 16" aria-hidden="true">
      {ALIGN_LINES[kind].map(([x1, x2], index) => (
        <line key={index} x1={x1} x2={x2} y1={3 + index * 3.4} y2={3 + index * 3.4} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      ))}
    </svg>
  );
}

function WritingIcon({ vertical }) {
  return vertical
    ? <span className="wm-icon vertical" aria-hidden="true"><i>가</i><i>나</i><i>다</i></span>
    : <span className="wm-icon" aria-hidden="true">가나다</span>;
}

function ColorLetterIcon({ color, outlined }) {
  return (
    <span className="color-letter" aria-hidden="true">
      <b className={outlined ? "outlined" : ""}>A</b>
      <i style={{ background: color }} />
    </span>
  );
}

const COLOR_PRESETS = [["#000000", "검정"], ["#ffffff", "흰색"]];

// 스타일 프리셋: 글자색·외곽선색·외곽선 두께를 한 번에 맞춘다. 기본값도 같은 기준이다(밝은 배경 1, 어두운 배경 2).
const STYLE_PRESETS = [
  { id: "light-bg", label: "밝은 배경", hint: "검정 글자 · 흰 외곽선 1", style: { font_color: "#000000", stroke_color: "#ffffff", stroke_width: 1 } },
  { id: "dark-bg", label: "어두운 배경", hint: "검정 글자 · 흰 외곽선 2", style: { font_color: "#000000", stroke_color: "#ffffff", stroke_width: 2 } },
];
// 직전에 쓴 글꼴·색·외곽선 조합을 기억해 다른 박스에 한 번에 붙인다. 브라우저에 저장하며 프로젝트와 무관하다.
const LAST_STYLE_KEY = "overlay-last-style";
const LAST_STYLE_KEYS = ["font_style", "font_path", "custom_font_path", "font_weight", "font_color", "stroke_color", "stroke_width"];

function readLastStyle() {
  try {
    const value = JSON.parse(window.localStorage.getItem(LAST_STYLE_KEY) || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

function writeLastStyle(value) {
  try { window.localStorage.setItem(LAST_STYLE_KEY, JSON.stringify(value)); } catch { /* 저장소를 못 쓰면 이번 화면에서만 유지 */ }
}

function describeLastStyle(style) {
  if (!style) return "아직 기억한 스타일이 없습니다";
  const parts = [];
  if (style.font_style) parts.push(style.font_style);
  if (style.font_color) parts.push(`글자 ${style.font_color}`);
  if (style.stroke_color) parts.push(`외곽선 ${style.stroke_color}`);
  if (style.stroke_width !== undefined && style.stroke_width !== "") parts.push(`두께 ${style.stroke_width}`);
  return parts.length ? `직전 스타일: ${parts.join(" · ")}` : "아직 기억한 스타일이 없습니다";
}

// 제자리 편집 글꼴: 렌더가 쓰는 폰트 파일(/fonts/<이름>)을 FontFace로 한 번만 불러 둔다.
const editorFontFaces = new Map();
const editorFontListeners = new Set();
let editorStyleFonts = null;

function editorFontFamily(fontPath) {
  const name = String(fontPath || "").replace(/\\/g, "/").split("/").pop();
  if (!name || typeof FontFace === "undefined") return null;
  let entry = editorFontFaces.get(name);
  if (!entry) {
    entry = { family: `overlay-edit-${editorFontFaces.size}`, ready: false };
    editorFontFaces.set(name, entry);
    const face = new FontFace(entry.family, `url("/fonts/${encodeURIComponent(name)}")`);
    face.load().then((loaded) => {
      document.fonts.add(loaded);
      entry.ready = true;
      editorFontListeners.forEach((fn) => fn());
    }).catch(() => { entry.failed = true; });
  }
  return entry.ready ? entry.family : null;
}

function loadEditorStyleFonts() {
  if (editorStyleFonts) return;
  editorStyleFonts = {};
  fetch("/api/fonts").then((res) => res.json()).then((data) => {
    editorStyleFonts = data?.styles || {};
    editorFontListeners.forEach((fn) => fn());
  }).catch(() => {});
}

// 렌더에 쓰인 수동값과 지금 수동값이 같은지(= 미리보기 이미지가 현재 편집 상태를 그린 것인지).
function overridesMatch(a, b) {
  const norm = (value) => (value === undefined || value === null || value === "" || value === false ? null : value);
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})].filter((key) => !key.startsWith("_")));
  for (const key of keys) {
    const x = norm(a?.[key]);
    const y = norm(b?.[key]);
    if (x === y) continue;
    if (x != null && y != null && typeof x !== "object" && Number.isFinite(Number(x)) && Number(x) === Number(y)) continue;
    if (JSON.stringify(x) !== JSON.stringify(y)) return false;
  }
  return true;
}

let measureCanvas = null;
function measureEditorText(font, letterSpacingPx, lines) {
  measureCanvas = measureCanvas || document.createElement("canvas");
  const ctx = measureCanvas.getContext("2d");
  ctx.font = font;
  try { ctx.letterSpacing = `${letterSpacingPx}px`; } catch (_e) { /* 구형 브라우저 */ }
  const probe = ctx.measureText("가Ag");
  return {
    widths: lines.map((line) => ctx.measureText(line).width),
    ascent: probe.fontBoundingBoxAscent,
    descent: probe.fontBoundingBoxDescent,
  };
}

// 제자리 편집에서 글자 입력을 가이던스(=미리보기 렌더 요청·실행 취소 기록)에 모아 반영하는 지연
const EDITOR_COMMIT_DELAY_MS = 120;
// 편집 중에도 캔버스를 다시 그릴 필요가 없는 영역 값(글·글자 스타일). 이것만 바뀌면 Konva 재구성을 건너뛴다.
const EDITOR_TEXT_STYLE_KEYS = new Set([
  "text_override", "font_size", "font_size_delta", "font_style", "font_path", "custom_font_path", "font_weight",
  "font_color", "stroke_color", "stroke_width", "text_align", "force_horizontal", "force_vertical",
]);

function onlyEditorTextStyleChanged(prev, next, rid) {
  if (!prev || !next || rid == null) return false;
  if (prev === next) return true;
  const prevKeys = Object.keys(prev);
  for (const key of new Set([...prevKeys, ...Object.keys(next)])) {
    if (key === "regions") continue;
    if (prev[key] !== next[key]) return false;
  }
  const a = prev.regions || {};
  const b = next.regions || {};
  for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (a[key] === b[key]) continue;
    if (key !== String(rid)) return false;
    const ra = a[key] || {};
    const rb = b[key] || {};
    for (const field of new Set([...Object.keys(ra), ...Object.keys(rb)])) {
      if (ra[field] !== rb[field] && !EDITOR_TEXT_STYLE_KEYS.has(field)) return false;
    }
  }
  return true;
}

// 클릭/더블클릭의 미세한 흔들림을 이동으로 저장하지 않기 위한 화면 px 기준
const DRAG_COMMIT_THRESHOLD_PX = 3;

// 회전 핸들을 Shift와 함께 끌면 이 각도 단위로 맞춘다.
const ROTATION_SNAP_DEG = 15;

const FOCUS_ON_CLICK_KEY = "comic-translator-overlay-focus-on-click";

const OverlayCanvas = forwardRef(function OverlayCanvas({ image, overlayGuidance, setOverlayGuidance, selectedRegionId, setSelectedRegionId, extraSelectedIds = [], onToggleRegion, onSelectRegions, onFocusRect, setStatus, onRequestApply, tool = "select", setTool }, ref) {
  const hostRef = useRef(null);
  const stageRef = useRef(null);
  const imageObjRef = useRef(null);
  // 글자를 지운 배경(복원 패치 반영본 또는 masked.jpg). 제자리 편집 중 구워진 글자를 가리는 데 쓴다.
  const maskImgRef = useRef(null);
  const transformRef = useRef({ scale: 1, x: 0, y: 0 });
  const dragRef = useRef(null);
  const dragSeqRef = useRef(0);
  const [viewTick, setViewTick] = useState(0);
  const [colorPopover, setColorPopover] = useState(null);
  const [lastStyle, setLastStyle] = useState(readLastStyle);
  const floatingEditorRef = useRef(null);
  const floatingEditorStateRef = useRef(null);
  const onFocusRectRef = useRef(null);
  onFocusRectRef.current = onFocusRect;
  const focusOpenedAtRef = useRef(0);
  // 박스를 한 번 누르면 그 박스로 확대한다(기본 켬). 끄면 선택만 한다. 글 편집은 더블클릭이나 Enter.
  const [focusOnClick, setFocusOnClick] = useState(() => {
    try { return window.localStorage.getItem(FOCUS_ON_CLICK_KEY) !== "0"; } catch { return true; }
  });
  const focusOnClickRef = useRef(focusOnClick);
  focusOnClickRef.current = focusOnClick;
  const overlayLayerRef = useRef(null);
  const previewNodeRef = useRef(null);
  const imageRef = useRef(image);
  const overlayRef = useRef(overlayGuidance);
  const selectedRegionRef = useRef(selectedRegionId);
  const extraSelectedRef = useRef(extraSelectedIds);
  extraSelectedRef.current = extraSelectedIds;
  const onToggleRegionRef = useRef(onToggleRegion);
  onToggleRegionRef.current = onToggleRegion;
  const onSelectRegionsRef = useRef(onSelectRegions);
  onSelectRegionsRef.current = onSelectRegions;
  const loadedPageKeyRef = useRef("");
  const [floatingEditor, setFloatingEditor] = useState(null);
  floatingEditorStateRef.current = floatingEditor;
  const [floatingDraft, setFloatingDraft] = useState("");
  // 편집 중 글자 배치 기준(열 때의 렌더 기록). editorSnapshotFrom 참고.
  const editorSnapshotRef = useRef(null);
  // 입력은 입력창 상태로 바로 그리고, 가이던스 반영은 잠깐 모아서(EDITOR_COMMIT_DELAY_MS) 한 번에 한다.
  const editorCommitTimerRef = useRef(null);
  const editorPendingTextRef = useRef(null);
  const editorComposingRef = useRef(false);
  const [editorCaret, setEditorCaret] = useState({ start: 0, end: 0, dir: "none", focused: false });
  const editorTextareaRef = useRef(null);
  useEffect(() => {
    // 프로그램으로 바꾼 선택(setSelectionRange 등)도 커서에 반영한다.
    const onSelectionChange = () => {
      const el = editorTextareaRef.current;
      if (!el) return;
      const focused = document.activeElement === el;
      setEditorCaret((current) => (
        current.start === el.selectionStart && current.end === el.selectionEnd && current.focused === focused
          ? current
          : { start: el.selectionStart, end: el.selectionEnd, dir: el.selectionDirection, focused }
      ));
    };
    document.addEventListener("selectionchange", onSelectionChange);
    return () => document.removeEventListener("selectionchange", onSelectionChange);
  }, []);
  const [, setFontTick] = useState(0);
  useEffect(() => {
    const listener = () => setFontTick((tick) => tick + 1);
    editorFontListeners.add(listener);
    loadEditorStyleFonts();
    return () => { editorFontListeners.delete(listener); };
  }, []);
  const eraserActive = tool === "eraser";
  const toolRef = useRef(tool);
  toolRef.current = tool;
  const eraserActiveRef = useRef(eraserActive);
  eraserActiveRef.current = eraserActive;
  // 지우개 도구에서 클릭으로 고른 eraser rect 번호. Delete로 지운다.
  const [selectedEraser, setSelectedEraser] = useState(null);
  const selectedEraserRef = useRef(null);
  const suppressStagePointerUntilRef = useRef(0);
  const setStatusRef = useRef(setStatus);
  setStatusRef.current = setStatus;
  const vpRef = useRef(null);
  if (!vpRef.current) {
    vpRef.current = createViewport({
      getHost: () => hostRef.current,
      getImage: () => imageObjRef.current,
      getKey: () => (imageObjRef.current ? viewKey(imageObjRef.current) : imageKey()),
      fitPadding: 0.96,
      redraw: () => draw(),
      onStatus: (text) => setStatusRef.current?.(text),
      isPanTool: () => toolRef.current === "hand",
    });
  }

  function selectEraserIndex(index) {
    selectedEraserRef.current = index;
    setSelectedEraser(index);
  }

  function selectRegionId(nextId) {
    setSelectedRegionId(nextId);
  }

  useEffect(() => {
    imageRef.current = image;
    overlayRef.current = overlayGuidance;
    selectedRegionRef.current = selectedRegionId;
  }, [image, overlayGuidance, selectedRegionId]);

  // 페이지 단위 key. final_url의 ?v= 버전이 바뀌어도 같은 페이지면 zoom/pan을 유지한다.
  function imageKey() {
    const current = imageRef.current || {};
    return `${current.id || ""}:${current.input_url || ""}`;
  }

  function viewKey(img) {
    return `${imageKey()}|${img.width}x${img.height}`;
  }

  function toStageRect(rect) {
    const t = transformRef.current;
    return { x: t.x + rect[0] * t.scale, y: t.y + rect[1] * t.scale, width: rect[2] * t.scale, height: rect[3] * t.scale };
  }

  function toStageRotatedRect(rect, angleDeg = 0) {
    const stageRect = toStageRect(rect);
    if (!angleDeg) return stageRect;
    return {
      x: stageRect.x + stageRect.width / 2,
      y: stageRect.y + stageRect.height / 2,
      width: stageRect.width,
      height: stageRect.height,
      offsetX: stageRect.width / 2,
      offsetY: stageRect.height / 2,
      rotation: angleDeg,
    };
  }

  function pointerToImage(stage) {
    const p = stage.getPointerPosition();
    const t = transformRef.current;
    return { x: Math.round((p.x - t.x) / t.scale), y: Math.round((p.y - t.y) / t.scale) };
  }

  function eventClientPoint(event) {
    const source = event?.evt || event;
    const point = source?.touches?.[0] || source?.changedTouches?.[0] || source;
    if (point?.clientX == null || point?.clientY == null) return null;
    return { x: point.clientX, y: point.clientY };
  }

  function eventToImage(event) {
    const container = stageRef.current?.container?.();
    if (!container) return null;
    const point = eventClientPoint(event);
    if (!point) return null;
    const bounds = container.getBoundingClientRect();
    const t = transformRef.current;
    return {
      x: Math.round((point.x - bounds.left - t.x) / t.scale),
      y: Math.round((point.y - bounds.top - t.y) / t.scale),
    };
  }

  function dragMovedEnough(drag, event) {
    if (!drag?.startClient) return true;
    const point = eventClientPoint(event);
    if (!point) return false;
    return Math.hypot(point.x - drag.startClient.x, point.y - drag.startClient.y) >= DRAG_COMMIT_THRESHOLD_PX;
  }

  function isFloatingEditorEvent(event) {
    const node = floatingEditorRef.current;
    // Konva 이벤트의 target은 DOM 노드가 아니라 Konva 노드라, 원래 DOM 이벤트(evt)의 target만 쓴다.
    const target = event?.evt?.target ?? event?.target ?? null;
    return Boolean(node && target instanceof Node && node.contains(target));
  }

  function setStageCursor(cursor) {
    const container = stageRef.current?.container?.();
    if (container) container.style.cursor = cursor;
  }

  function toggleEraserTool() {
    setTool?.(eraserActiveRef.current ? "select" : "eraser");
  }

  function resizeCursorForHandle(handle) {
    if (handle === "nw" || handle === "se") return "nwse-resize";
    if (handle === "ne" || handle === "sw") return "nesw-resize";
    if (handle === "n" || handle === "s") return "ns-resize";
    if (handle === "e" || handle === "w") return "ew-resize";
    return "default";
  }

  function rectFromPoints(start, end) {
    const x1 = Math.min(start.x, end.x);
    const y1 = Math.min(start.y, end.y);
    const x2 = Math.max(start.x, end.x);
    const y2 = Math.max(start.y, end.y);
    return [x1, y1, x2 - x1, y2 - y1];
  }

  function clampRect(rect, img) {
    const x1 = Math.max(0, Math.min(img.width, rect[0]));
    const y1 = Math.max(0, Math.min(img.height, rect[1]));
    const x2 = Math.max(0, Math.min(img.width, rect[0] + rect[2]));
    const y2 = Math.max(0, Math.min(img.height, rect[1] + rect[3]));
    const left = Math.round(Math.min(x1, x2));
    const top = Math.round(Math.min(y1, y2));
    return [
      left,
      top,
      Math.round(Math.abs(x2 - x1)),
      Math.round(Math.abs(y2 - y1)),
    ];
  }

  function clampMovedRect(rect, img) {
    const w = Math.min(Math.max(0, rect[2]), img.width);
    const h = Math.min(Math.max(0, rect[3]), img.height);
    const x = Math.min(Math.max(0, rect[0]), Math.max(0, img.width - w));
    const y = Math.min(Math.max(0, rect[1]), Math.max(0, img.height - h));
    return [Math.round(x), Math.round(y), Math.round(w), Math.round(h)];
  }

  function clampEditableRect(rect, img, fallbackRect = null) {
    if (!rect || !img) return null;
    const normalized = clampRect(rect, img);
    if (normalized[2] >= OVERLAY_MIN_RECT_SIZE && normalized[3] >= OVERLAY_MIN_RECT_SIZE) return normalized;
    if (fallbackRect && fallbackRect[2] >= OVERLAY_MIN_RECT_SIZE && fallbackRect[3] >= OVERLAY_MIN_RECT_SIZE) {
      return clampMovedRect(fallbackRect, img);
    }
    const w = Math.min(Math.max(OVERLAY_MIN_RECT_SIZE, normalized[2]), img.width);
    const h = Math.min(Math.max(OVERLAY_MIN_RECT_SIZE, normalized[3]), img.height);
    return clampMovedRect([normalized[0], normalized[1], w, h], img);
  }

  function selectedSafeRect() {
    const rid = selectedRegionRef.current;
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === String(rid));
    if (!region) return null;
    return regionDisplayRect(region);
  }

  function pointInRect(point, rect) {
    if (!rect) return false;
    const [x, y, w, h] = rect;
    return point.x >= x && point.x <= x + w && point.y >= y && point.y <= y + h;
  }

  function unrotatePoint(point, rect, angleDeg) {
    const [x, y, w, h] = rect;
    const cx = x + w / 2;
    const cy = y + h / 2;
    const angle = -Number(angleDeg || 0) * Math.PI / 180;
    const dx = point.x - cx;
    const dy = point.y - cy;
    return {
      x: cx + dx * Math.cos(angle) - dy * Math.sin(angle),
      y: cy + dx * Math.sin(angle) + dy * Math.cos(angle),
    };
  }

  function pointInRotatedRect(point, rect, angleDeg = 0) {
    return pointInRect(unrotatePoint(point, rect, angleDeg), rect);
  }

  function handlePoints(rect) {
    const [x, y, w, h] = rect;
    return {
      nw: [x, y],
      n: [x + w / 2, y],
      ne: [x + w, y],
      e: [x + w, y + h / 2],
      se: [x + w, y + h],
      s: [x + w / 2, y + h],
      sw: [x, y + h],
      w: [x, y + h / 2],
    };
  }

  function rotatedPoint(point, rect, angleDeg) {
    const angle = Number(angleDeg || 0) * Math.PI / 180;
    if (!angle) return point;
    const [x, y, w, h] = rect;
    const cx = x + w / 2;
    const cy = y + h / 2;
    const dx = point[0] - cx;
    const dy = point[1] - cy;
    return [
      cx + dx * Math.cos(angle) - dy * Math.sin(angle),
      cy + dx * Math.sin(angle) + dy * Math.cos(angle),
    ];
  }

  function rotationForRegion(rid) {
    const value = Number(overlayRegionValue(overlayRef.current, rid).rotation_deg || 0);
    return Number.isFinite(value) ? Math.max(-45, Math.min(45, value)) : 0;
  }

  function rotationHandlePoint(rect, angleDeg = 0) {
    const [x, y, w] = rect;
    const offset = Math.max(24, 30 / Math.max(0.1, transformRef.current.scale || 1));
    return rotatedPoint([x + w / 2, y - offset], rect, angleDeg);
  }

  function rotationHandleAt(point, rect, angleDeg = 0) {
    if (!rect) return false;
    const [hx, hy] = rotationHandlePoint(rect, angleDeg);
    const threshold = Math.max(8, 12 / Math.max(0.1, transformRef.current.scale || 1));
    return Math.hypot(point.x - hx, point.y - hy) <= threshold;
  }

  function rotationAngleForPoint(point, rect, snap = false) {
    const [x, y, w, h] = rect;
    const cx = x + w / 2;
    const cy = y + h / 2;
    let deg = Math.atan2(point.y - cy, point.x - cx) * 180 / Math.PI + 90;
    if (snap) deg = Math.round(deg / ROTATION_SNAP_DEG) * ROTATION_SNAP_DEG;
    return Math.max(-45, Math.min(45, Math.round(deg)));
  }

  function handleAt(point, rect) {
    if (!rect) return null;
    if (rect[2] < OVERLAY_MIN_RECT_SIZE || rect[3] < OVERLAY_MIN_RECT_SIZE) return null;
    const threshold = Math.max(6, 10 / Math.max(0.1, transformRef.current.scale));
    const points = handlePoints(rect);
    return OVERLAY_HANDLE_NAMES.find((name) => {
      const [x, y] = points[name];
      return Math.abs(point.x - x) <= threshold && Math.abs(point.y - y) <= threshold;
    }) || edgeHandleAt(point, rect, threshold);
  }

  function rotatedHandleAt(point, rect, angleDeg = 0) {
    if (!rect) return null;
    if (!angleDeg) return handleAt(point, rect);
    if (rect[2] < OVERLAY_MIN_RECT_SIZE || rect[3] < OVERLAY_MIN_RECT_SIZE) return null;
    const threshold = Math.max(6, 12 / Math.max(0.1, transformRef.current.scale));
    const points = handlePoints(rect);
    const corner = OVERLAY_HANDLE_NAMES.find((name) => {
      const [x, y] = rotatedPoint(points[name], rect, angleDeg);
      return Math.abs(point.x - x) <= threshold && Math.abs(point.y - y) <= threshold;
    });
    if (corner) return corner;
    return edgeHandleAt(unrotatePoint(point, rect, angleDeg), rect, threshold);
  }

  function edgeHandleAt(point, rect, threshold = Math.max(5, 8 / Math.max(0.1, transformRef.current.scale))) {
    if (!rect) return null;
    if (rect[2] < OVERLAY_MIN_RECT_SIZE || rect[3] < OVERLAY_MIN_RECT_SIZE) return null;
    const [x, y, w, h] = rect;
    const x2 = x + w;
    const y2 = y + h;
    const withinX = point.x >= x - threshold && point.x <= x2 + threshold;
    const withinY = point.y >= y - threshold && point.y <= y2 + threshold;
    if (Math.abs(point.x - x) <= threshold && withinY) return "w";
    if (Math.abs(point.x - x2) <= threshold && withinY) return "e";
    if (Math.abs(point.y - y) <= threshold && withinX) return "n";
    if (Math.abs(point.y - y2) <= threshold && withinX) return "s";
    return null;
  }

  function resizedRectFromHandle(startRect, handle, point) {
    const [x, y, w, h] = startRect;
    const x2 = x + w;
    const y2 = y + h;
    if (handle === "nw") return rectFromPoints({ x: x2, y: y2 }, point);
    if (handle === "n") return rectFromPoints({ x, y: y2 }, { x: x2, y: point.y });
    if (handle === "ne") return rectFromPoints({ x, y: y2 }, point);
    if (handle === "e") return rectFromPoints({ x, y }, { x: point.x, y: y2 });
    if (handle === "se") return rectFromPoints({ x, y }, point);
    if (handle === "s") return rectFromPoints({ x, y }, { x: x2, y: point.y });
    if (handle === "sw") return rectFromPoints({ x: x2, y }, point);
    if (handle === "w") return rectFromPoints({ x: x2, y }, { x: point.x, y: y2 });
    return startRect;
  }

  function regionDisplayRect(region) {
    const rid = overlayItemKey(region);
    const override = overlayRegionValue(overlayRef.current, rid);
    return override.safe_text_rect || region?.safe_text_rect || region?.bubble_rect || region?.unboxed_rect || region?.source_rect || null;
  }

  function findRegionAt(point) {
    const selectedKey = selectedRegionRef.current ? String(selectedRegionRef.current) : null;
    if (selectedKey) {
      const selectedRegion = overlayControlItems(imageRef.current, overlayRef.current).find((region) => overlayItemKey(region) === selectedKey);
      const selectedRect = selectedRegion ? regionDisplayRect(selectedRegion) : null;
      const selectedRotation = selectedRect ? rotationForRegion(selectedKey) : 0;
      if (
        selectedRegion
        && selectedRect
        && (
          rotationHandleAt(point, selectedRect, selectedRotation)
          || rotatedHandleAt(point, selectedRect, selectedRotation)
          || pointInRotatedRect(point, selectedRect, selectedRotation)
        )
      ) {
        return selectedRegion;
      }
    }
    return [...overlayControlItems(imageRef.current, overlayRef.current)].reverse().find((region) => {
      const rect = regionDisplayRect(region);
      if (!rect) return false;
      const [x, y, w, h] = rect;
      return point.x >= x && point.x <= x + w && point.y >= y && point.y <= y + h;
    });
  }

  function updateRegionOverride(rid, patch, historyKey = null) {
    const key = String(rid);
    setOverlayGuidance((current) => {
      const previousRegion = ((current || {}).regions || {})[key] || {};
      const patchValue = typeof patch === "function" ? patch(previousRegion) : patch;
      return {
        ...(current || { regions: {} }),
        regions: {
          ...((current || {}).regions || {}),
          [key]: {
            ...previousRegion,
            ...(patchValue || {}),
          },
        },
      };
    }, historyKey ? { historyKey } : undefined);
  }

  function addEraserRect(rect) {
    const img = imageObjRef.current;
    const clamped = img ? clampRect(rect, img) : rect;
    if (!clamped || clamped[2] < 4 || clamped[3] < 4) return;
    setOverlayGuidance((current) => ({
      ...(current || { regions: {} }),
      eraser_rects: [
        ...overlayEraserRects(current),
        clamped.map((value) => Math.round(Number(value) || 0)),
      ],
    }));
    setStatus(`지우개 ${clamped[2]}×${clamped[3]} 적용`);
  }

  function eraserIndexAt(point) {
    const rects = overlayEraserRects(overlayRef.current);
    for (let index = rects.length - 1; index >= 0; index -= 1) {
      if (pointInRect(point, rects[index])) return index;
    }
    return null;
  }

  function deleteSelectedEraser() {
    const index = selectedEraserRef.current;
    const rects = overlayEraserRects(overlayRef.current);
    if (index == null || index < 0 || index >= rects.length) {
      selectEraserIndex(null);
      return false;
    }
    setOverlayGuidance((current) => ({
      ...(current || { regions: {} }),
      eraser_rects: overlayEraserRects(current).filter((_rect, itemIndex) => itemIndex !== index),
    }));
    selectEraserIndex(null);
    setStatus(`지우개 영역 ${index + 1} 삭제`);
    return true;
  }

  // 선택 시 빨간 박스로 그리는 rect(draw()의 editableSafeRect와 같은 규칙). 편집 박스도 이 rect를 쓴다.
  function selectedBoxRect(region, override) {
    if (!region) return null;
    const regionRect = region.region_outline_rect || region.bubble_rect || region.unboxed_rect || region.source_rect;
    const safeRect = override?.safe_text_rect || region.safe_text_rect || regionRect;
    if (safeRect && (safeRect[2] < OVERLAY_MIN_RECT_SIZE || safeRect[3] < OVERLAY_MIN_RECT_SIZE)) {
      return region.safe_text_rect || regionRect || safeRect;
    }
    return safeRect || null;
  }

  // 편집 중 구워진 글자를 가릴 범위: 빨간 박스와 말풍선/영역 rect를 모두 덮는다(이미지 좌표).
  function editorCoverRect(region, override, img) {
    const entry = overlayLayoutEntry(imageRef.current, region);
    const drawn = Array.isArray(entry?.draw?.lines) ? entry.draw.lines : [];
    const pad = (Number(entry?.draw?.stroke_width) || 1) + 4;
    const lineH = Number(entry?.draw?.line_height) || Number(entry?.font_size) || 0;
    const rects = [
      selectedBoxRect(region, override),
      regionDisplayRect(region),
      region?.region_outline_rect,
      entry?.bbox,
      // 렌더가 박스 밖까지 그린 글자도 가린다.
      ...drawn.map((line) => [line.x - pad, line.y - pad, (Number(line.w) || 0) + pad * 2, lineH + pad * 2]),
    ].filter((rect) => Array.isArray(rect) && rect.length >= 4 && rect[2] > 0 && rect[3] > 0);
    if (!rects.length) return null;
    const x1 = Math.max(0, Math.min(...rects.map((r) => r[0])));
    const y1 = Math.max(0, Math.min(...rects.map((r) => r[1])));
    const x2 = Math.min(img.width, Math.max(...rects.map((r) => r[0] + r[2])));
    const y2 = Math.min(img.height, Math.max(...rects.map((r) => r[1] + r[3])));
    if (x2 <= x1 || y2 <= y1) return null;
    return [x1, y1, x2 - x1, y2 - y1];
  }

  // 편집 중 글자 배치의 기준. 편집을 연 시점(또는 스타일을 바꾼 뒤 그 렌더가 도착한 시점)의 렌더 기록으로 고정한다.
  // 타이핑으로 서버 미리보기가 갱신돼도 기준을 바꾸지 않아, 편집 중 글자가 렌더 결과로 바뀌며 움직이지 않는다.
  function editorSnapshotFrom(img, region, rid) {
    const entry = overlayLayoutEntry(img, region);
    const draw = entry?.draw && Array.isArray(entry.draw.lines) && entry.draw.lines.length ? entry.draw : null;
    const renderedOverride = defaultOverlayGuidance(img).regions?.[String(rid)] || {};
    return {
      draw,
      fontPath: entry?.font_path || "",
      fontSize: Number(draw?.font_size) || Number(entry?.font_size) || 0,
      inner: draw?.inner_rect || entry?.inner_rect || null,
      textLayout: typeof entry?.text_layout === "string" ? entry.text_layout : null,
      renderedOverride,
      styleSig: `${editorStyleSignature(renderedOverride)}|${Number(draw?.font_size) || Number(entry?.font_size) || 0}`,
    };
  }

  // 글 내용·글자 크기를 뺀 스타일 값(+ 렌더된 글자 크기). 이것이 바뀌었을 때만 새 렌더를 배치 기준으로 다시 받는다.
  // 글만 고친 경우의 새 렌더는 기준으로 받지 않으므로 편집 중 글자가 렌더 결과로 바뀌며 움직이지 않는다.
  function editorStyleSignature(override) {
    const rest = {};
    Object.keys(override || {}).sort().forEach((key) => {
      if (key.startsWith("_") || key === "text_override" || key === "font_size") return;
      const value = override[key];
      if (value === undefined || value === null || value === "" || value === false) return;
      rest[key] = value;
    });
    return JSON.stringify(rest);
  }

  // 편집 글자를 렌더가 그린 자리·글꼴·크기·외곽선 그대로 DOM으로 그린다(편집 중 그 영역은 항상 마스크 위의 이 글자).
  function editorTextGeometry({ snapshot, override, draft, box, innerFallback, fallbackFontImg }) {
    const t = transformRef.current;
    const s = t.scale || 1;
    const draw = snapshot?.draw || null;
    const rendered = snapshot?.renderedOverride || {};
    const styleFonts = editorStyleFonts || {};
    let fontPath = snapshot?.fontPath;
    const customFont = override.custom_font_path || override.font_path;
    if (customFont && customFont !== (rendered.custom_font_path || rendered.font_path)) fontPath = customFont;
    else if (override.font_style && override.font_style !== rendered.font_style && styleFonts[override.font_style]) fontPath = styleFonts[override.font_style];
    const family = editorFontFamily(fontPath);
    const renderedSize = Number(snapshot?.fontSize) || 0;
    const fontImg = Number(override.font_size) || renderedSize || fallbackFontImg || 24;
    const sizeRatio = fontImg / (Number(draw?.font_size) || fontImg);
    // 줄 간격: 렌더가 실제로 그린 줄 사이 거리(세로쓰기는 line_height보다 촘촘하다). 점(…) 줄은 따로 붙으므로 뺀다.
    let lineImg = Math.round(fontImg * 1.14);
    if (draw) {
      const isDotLine = (text) => /^[.．・…·\s]+$/.test(String(text || ""));
      const deltas = [];
      for (let i = 1; i < draw.lines.length; i += 1) {
        if (!isDotLine(draw.lines[i - 1].text) && !isDotLine(draw.lines[i].text)) deltas.push(draw.lines[i].y - draw.lines[i - 1].y);
      }
      deltas.sort((a, b) => a - b);
      const step = deltas.length ? deltas[Math.floor(deltas.length / 2)] : Number(draw.line_height);
      lineImg = step * sizeRatio;
    }
    const trackingImg = Number(draw?.tracking) || 0;
    const strokeImg = Number.isFinite(Number(override.stroke_width)) && override.stroke_width !== "" && override.stroke_width != null
      ? Number(override.stroke_width)
      : (Number.isFinite(Number(draw?.stroke_width)) ? Number(draw.stroke_width) : 1);
    const align = override.text_align || draw?.text_align || "center";
    const weight = override.font_style === "etc" && Number(override.font_weight) ? Number(override.font_weight) : null;
    const lines = String(draft ?? "").split("\n");
    const n = Math.max(1, lines.length);
    const inner = snapshot?.inner || innerFallback || [0, 0, 100, 100];

    const fontPx = fontImg * s;
    const lhPx = lineImg * s;
    const trackPx = trackingImg * s;
    const strokePx = strokeImg * s;
    const familyCss = family ? `"${family}", sans-serif` : "sans-serif";
    const fontCss = `${weight || 400} ${fontPx}px ${familyCss}`;
    const m = measureEditorText(fontCss, trackPx, lines);
    const asc = Number.isFinite(m.ascent) && m.ascent > 0 ? m.ascent : fontPx * 0.9;
    const desc = Number.isFinite(m.descent) && m.descent >= 0 ? m.descent : fontPx * 0.25;
    const pad = Math.ceil(strokePx) + 3;
    const maxW = Math.max(1, ...m.widths);

    // 렌더러는 가로·세로 모두 줄(세로는 한 글자씩의 줄)을 위에서 아래로 쌓아 그린다. 입력창도 같은 줄을 그대로 쌓는다.
    // 세로 위치: 렌더처럼 글 묶음의 잉크 가운데를 같은 자리에 둔다(줄 수가 바뀌어도). 기준선은 첫 줄 y + ascent.
    let firstY;
    let ascentImg;
    if (draw) {
      const rn = draw.lines.length;
      const rSize = Number(draw.font_size) || fontImg;
      ascentImg = (Number(draw.ascent) || rSize * 0.9) * sizeRatio;
      if (Number.isFinite(Number(draw.block_y)) && Number.isFinite(Number(draw.visual_top))) {
        const vt = Number(draw.visual_top);
        const vb = Number(draw.visual_bottom);
        const rStep = rn > 1 ? (draw.lines[rn - 1].y - draw.lines[0].y) / (rn - 1) : lineImg / sizeRatio;
        const center = Number(draw.block_y) + (vt + vb) / 2;
        // 첫 줄 잉크 윗변과 마지막 줄 잉크 아랫변의 줄 기준 위치는 크기에 비례한다고 본다.
        const top0 = vt * sizeRatio;
        const bottomLast = (vb - (rn - 1) * rStep) * sizeRatio;
        firstY = center - (top0 + (n - 1) * lineImg + bottomLast) / 2;
      } else {
        const center = (draw.lines[0].y + draw.lines[rn - 1].y + rSize) / 2;
        firstY = n === rn && fontImg === rSize ? draw.lines[0].y : center - ((n - 1) * lineImg + fontImg) / 2;
      }
    } else {
      firstY = inner[1] + (inner[3] - ((n - 1) * lineImg + fontImg)) / 2;
      ascentImg = asc / s;
    }
    const baseline = t.y + (firstY + ascentImg) * s;
    const textTop = baseline - ((lhPx - (asc + desc)) / 2 + asc);
    const width = maxW + pad * 2 + 2;
    const height = n * lhPx + pad * 2;
    const top = textTop - pad;
    let left;
    if (align === "left") left = t.x + inner[0] * s - pad;
    else if (align === "right") left = t.x + (inner[0] + inner[2]) * s - width + pad;
    else left = t.x + (inner[0] + inner[2] / 2) * s - width / 2;
    const color = /^#[0-9a-f]{6}$/i.test(String(override.font_color || "")) ? override.font_color : "#000000";
    const strokeColor = /^#[0-9a-f]{6}$/i.test(String(override.stroke_color || "")) ? override.stroke_color : "#ffffff";
    const relLeft = left + box.offsetX - box.left;
    const relTop = top + box.offsetY - box.top;
    // 커서 위치 계산용: 줄마다 시작 x(입력창 안쪽 기준)와 글자 폭 측정.
    const contentW = width - pad * 2;
    const lineStartX = (index) => {
      const w = m.widths[index] || 0;
      if (align === "left") return pad;
      if (align === "right") return pad + contentW - w;
      return pad + (contentW - w) / 2;
    };
    return {
      fontImg,
      caretAt(offset) {
        const before = String(draft ?? "").slice(0, Math.max(0, offset));
        const row = before.split("\n").length - 1;
        const prefix = before.slice(before.lastIndexOf("\n") + 1);
        const prefixW = prefix ? measureEditorText(fontCss, trackPx, [prefix]).widths[0] : 0;
        return {
          left: relLeft + lineStartX(row) + prefixW,
          top: relTop + pad + row * lhPx + Math.max(0, (lhPx - (asc + desc)) / 2) - 1,
          height: Math.min(lhPx, asc + desc) + 2,
        };
      },
      style: {
        position: "absolute",
        left: relLeft,
        top: relTop,
        width,
        height,
        padding: pad,
        margin: 0,
        boxSizing: "border-box",
        fontFamily: familyCss,
        fontSize: fontPx,
        lineHeight: `${lhPx}px`,
        fontWeight: weight || "normal",
        fontVariationSettings: weight ? `"wght" ${weight}` : undefined,
        letterSpacing: trackPx ? `${trackPx}px` : undefined,
        textAlign: align,
        whiteSpace: "pre",
        overflow: "hidden",
        color,
        WebkitTextFillColor: color,
        WebkitTextStroke: strokePx > 0 ? `${strokePx * 2}px ${strokeColor}` : "0px transparent",
      },
    };
  }

  // 빨간 선택 박스(stage 좌표)를 그대로 텍스트 박스로 쓴다. 핸들을 잡을 수 있게 안쪽으로 살짝만 들인다.
  function focusEditorBox(stageRect) {
    const inset = 4;
    // 편집 박스는 wrap 기준 절대 위치라, 왼쪽 도구 막대만큼 밀린 stage host의 위치를 더한다.
    const host = hostRef.current;
    const wrap = host?.parentElement;
    const hostBounds = host?.getBoundingClientRect();
    const wrapBounds = wrap?.getBoundingClientRect();
    const offsetX = hostBounds && wrapBounds ? hostBounds.left - wrapBounds.left - (wrap.clientLeft || 0) : 0;
    const offsetY = hostBounds && wrapBounds ? hostBounds.top - wrapBounds.top - (wrap.clientTop || 0) : 0;
    return {
      offsetX,
      offsetY,
      left: Math.round(stageRect.x + offsetX) + inset,
      top: Math.round(stageRect.y + offsetY) + inset,
      width: Math.max(20, Math.round(stageRect.width) - inset * 2),
      height: Math.max(20, Math.round(stageRect.height) - inset * 2),
    };
  }

  // 박스를 눌렀을 때: 그 박스로 확대하고, 같은 자리·같은 글자 크기의 편집 박스를 겹쳐 띄운다.
  // edit가 false면 확대와 툴바만 띄우고 글 편집(입력창)은 켜지 않는다. 더블클릭·Enter가 edit를 켠다.
  function openFloatingEditor(rid, edit = false) {
    if (!rid) return;
    const key = String(rid);
    const already = floatingEditorStateRef.current;
    if (already?.rid === key) {
      if (edit && !already.editing) startEditing();
      return;
    }
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === key);
    const rect = region ? regionDisplayRect(region) : null;
    if (!region || !rect) return;
    const override = overlayRegionValue(overlayRef.current, key);
    suppressStagePointerUntilRef.current = Date.now() + 350;
    dragRef.current = null;
    clearPreviewRect();
    selectRegionId(key);
    selectedRegionRef.current = key;
    // 한쪽 축이 거의 찰 만큼 확대한다. 위쪽 툴바 자리로 약간 남긴다.
    vpRef.current.zoomToRect(rect, 0.72);
    focusOpenedAtRef.current = Date.now();
    const text = override.text_override ?? baseRegionText(imageRef.current, region);
    const fontImg = Number(override.font_size) || overlayItemFontSize(region) || 24;
    const fontPx = Math.max(9, Math.round(fontImg * transformRef.current.scale));
    editorSnapshotRef.current = editorSnapshotFrom(imageRef.current, region, key);
    editorPendingTextRef.current = null;
    setFloatingDraft(text);
    setFloatingEditor({ rid: key, stage: toStageRect(rect), fontPx, fontImg, editing: Boolean(edit) });
    onFocusRectRef.current?.(rect);
  }

  // 확대만 된 박스에서 글 편집을 시작한다. 지금 렌더를 기준으로 입력창 내용과 배치를 다시 잡는다.
  function startEditing() {
    const open = floatingEditorStateRef.current;
    if (!open || open.editing) return;
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === String(open.rid));
    if (!region) return;
    const override = overlayRegionValue(overlayRef.current, open.rid);
    editorSnapshotRef.current = editorSnapshotFrom(imageRef.current, region, open.rid);
    editorPendingTextRef.current = null;
    setFloatingDraft(override.text_override ?? baseRegionText(imageRef.current, region));
    setFloatingEditor({ ...open, editing: true });
  }

  // 편집만 끝내고 확대·툴바는 그대로 둔다.
  function stopEditing() {
    commitEditorText();
    editorComposingRef.current = false;
    setFloatingEditor((current) => (current ? { ...current, editing: false } : current));
  }

  // fit이 true면 확대를 풀고 전체 보기로 되돌린다(편집 창이 열려 있었을 때만).
  // 모아 둔 글 입력을 가이던스에 반영한다. 처음 고칠 때의 글자 크기 고정은 App의 setOverlayGuidance
  // (pinAutoLayoutOnFirstEdit)가 함께 한다.
  function commitEditorText() {
    clearTimeout(editorCommitTimerRef.current);
    editorCommitTimerRef.current = null;
    const open = floatingEditorStateRef.current;
    const value = editorPendingTextRef.current;
    editorPendingTextRef.current = null;
    if (!open || value == null) return;
    updateRegionOverride(open.rid, { text_override: value }, `text:${open.rid}`);
  }

  function scheduleEditorCommit() {
    clearTimeout(editorCommitTimerRef.current);
    editorCommitTimerRef.current = setTimeout(() => {
      if (!editorComposingRef.current) commitEditorText();
    }, EDITOR_COMMIT_DELAY_MS);
  }

  function closeFloatingEditor({ fit = false, discard = false } = {}) {
    setColorPopover(null);
    if (discard) {
      clearTimeout(editorCommitTimerRef.current);
      editorPendingTextRef.current = null;
    } else {
      commitEditorText();
    }
    editorComposingRef.current = false;
    const wasOpen = Boolean(floatingEditorStateRef.current);
    setFloatingEditor(null);
    setFloatingDraft("");
    if (wasOpen) onFocusRectRef.current?.(null);
    if (fit && wasOpen) vpRef.current.fit();
  }

  // 편집 내용은 입력하는 대로 이미 반영돼 있다. 닫고 전체 보기로 돌아간다.
  function applyFloatingEditor() {
    if (!floatingEditor?.rid) return false;
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === String(floatingEditor.rid));
    closeFloatingEditor({ fit: true });
    setStatus(`${region ? overlayItemLabel(region) : floatingEditor.rid} text 적용`);
    return true;
  }

  // 박스를 눌렀을 때의 확대 편집 전환: 열려 있으면(같은 박스든 다른 박스든) 닫고 원래 배율로, 닫혀 있으면 연다.
  // 박스를 한 번 눌렀을 때: 확대 옵션이 켜져 있으면 그 박스로 확대(다른 박스가 열려 있으면 그쪽으로 옮김),
  // 이미 그 박스에 있으면 그대로 둔다. 옵션이 꺼져 있으면 선택만 하고 열린 확대는 닫는다.
  function toggleFocusEditorFromClick(key) {
    const open = floatingEditorStateRef.current;
    if (!focusOnClickRef.current) {
      if (open && open.rid !== key) closeFloatingEditor({ fit: true });
      return;
    }
    if (open?.rid === key) return;
    openFloatingEditor(key, false);
  }

  function regionRectByKey(key) {
    if (key == null) return null;
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === String(key));
    return region ? regionDisplayRect(region) : null;
  }

  useImperativeHandle(ref, () => ({
    fit: () => vpRef.current.fit(),
    zoom100: () => vpRef.current.zoom100(),
    zoomBy: (factor) => vpRef.current.zoomBy(factor),
    panBy: (dx, dy) => vpRef.current.panBy(dx, dy),
    zoomToSelection: () => {
      const rect = regionRectByKey(selectedRegionRef.current);
      if (!rect) {
        setStatus("확대할 선택 region 없음");
        return false;
      }
      return vpRef.current.zoomToRect(rect);
    },
    ensureRegionVisible: (key) => {
      const rect = regionRectByKey(key);
      return rect ? vpRef.current.ensureVisible(rect) : false;
    },
    isEditorOpen: () => Boolean(floatingEditor),
    openEditorFor: (key) => { if (key == null) return false; openFloatingEditor(String(key), true); return true; },
    openEditorForSelected: () => {
      const key = selectedRegionRef.current;
      const rect = regionRectByKey(key);
      if (!rect) return false;
      openFloatingEditor(String(key), true);
      return true;
    },
    closeEditor: () => {
      if (!floatingEditor) return false;
      // 글 편집 중이면 편집만 끝내고 확대는 둔다. 한 번 더 누르면 닫고 전체 보기로 돌아간다.
      if (floatingEditor.editing) {
        stopEditing();
        setStatus("대사 편집 종료");
        return true;
      }
      closeFloatingEditor({ fit: true });
      setStatus("박스 확대 닫기");
      return true;
    },
    commitEditor: () => {
      // 초안을 먼저 overlay guidance에 반영한 뒤 Overlay 적용을 요청한다.
      if (!applyFloatingEditor()) return false;
      onRequestApply?.();
      return true;
    },
    hasSelectedEraser: () => selectedEraserRef.current != null,
    clearEraserSelection: () => {
      if (selectedEraserRef.current == null) return false;
      selectEraserIndex(null);
      return true;
    },
    deleteSelectedEraser,
  }));

  function setPreviewRect(preview) {
    const layer = overlayLayerRef.current;
    if (!layer || !preview) return;
    const previewRect = Array.isArray(preview) ? preview : preview.rect;
    if (!previewRect || previewRect[2] < 1 || previewRect[3] < 1) return;
    const isEraserPreview = !Array.isArray(preview) && preview.tool === "eraser";
    const isMarqueePreview = !Array.isArray(preview) && preview.tool === "marquee";
    if (!previewNodeRef.current || previewNodeRef.current.isDestroyed?.()) {
      previewNodeRef.current = new Konva.Rect({ listening: false });
      layer.add(previewNodeRef.current);
    }
    previewNodeRef.current.setAttrs({
      ...toStageRect(previewRect),
      fill: isEraserPreview ? "white" : (isMarqueePreview ? "#2563eb" : undefined),
      opacity: isEraserPreview ? 0.55 : (isMarqueePreview ? 0.15 : 1),
      stroke: isEraserPreview ? "#06b6d4" : (isMarqueePreview ? "#2563eb" : "#ef4444"),
      strokeWidth: 2,
      dash: [4, 3],
    });
    previewNodeRef.current.moveToTop();
    layer.batchDraw();
  }

  function clearPreviewRect() {
    if (previewNodeRef.current && !previewNodeRef.current.isDestroyed?.()) {
      previewNodeRef.current.destroy();
      previewNodeRef.current = null;
      overlayLayerRef.current?.batchDraw();
    }
  }

  function draw() {
    const host = hostRef.current;
    if (!host) return;
    const width = host.clientWidth || 800;
    const height = host.clientHeight || 600;
    const stage = stageRef.current || new Konva.Stage({ container: host, width, height });
    stageRef.current = stage;
    stage.width(width);
    stage.height(height);
    stage.destroyChildren();
    previewNodeRef.current = null;
    const layer = new Konva.Layer();
    overlayLayerRef.current = layer;
    stage.add(layer);

    const img = imageObjRef.current;
    if (!img || !imageRef.current) {
      const message = canRenderOverlayImage(imageRef.current)
        ? "Overlay preview 생성 중..."
        : "이미지를 불러오세요";
      layer.add(new Konva.Text({ text: message, x: 24, y: 24, fill: "#6b7280", fontSize: 14 }));
      layer.draw();
      return;
    }

    const view = vpRef.current.ensure(width, height, img);
    transformRef.current = { scale: view.scale, x: view.x, y: view.y };
    setViewTick((tick) => tick + 1);
    layer.add(new Konva.Image({ image: img, ...toStageRect([0, 0, img.width, img.height]) }));

    // 제자리 편집 중에는 미리보기에 구워진 그 영역의 글자를 지운 배경(마스크 이미지)으로 덮는다.
    // 그래야 편집 박스의 글자만 보이고 이중으로 겹치지 않는다.
    const editing = floatingEditorStateRef.current;
    if (editing?.rid && editing.editing) {
      const editRegion = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === String(editing.rid));
      const cover = editRegion ? editorCoverRect(editRegion, overlayRegionValue(overlayRef.current, editing.rid), img) : null;
      if (cover) {
        const stageCover = toStageRect(cover);
        const group = new Konva.Group({ clip: stageCover, listening: false });
        const mask = maskImgRef.current;
        if (mask) {
          const sx = mask.width / img.width;
          const sy = mask.height / img.height;
          group.add(new Konva.Image({
            image: mask,
            ...stageCover,
            crop: { x: cover[0] * sx, y: cover[1] * sy, width: cover[2] * sx, height: cover[3] * sy },
          }));
          // 렌더처럼 수동 지우개 rect는 흰색으로 채운다.
          overlayEraserRects(overlayRef.current).forEach((rect) => {
            if (Array.isArray(rect) && rect.length >= 4) group.add(new Konva.Rect({ ...toStageRect(rect), fill: "white" }));
          });
        } else {
          // 마스크 이미지가 없으면 흰색으로 덮는다(말풍선 대부분이 흰 바탕).
          group.add(new Konva.Rect({ ...stageCover, fill: "white" }));
        }
        layer.add(group);
      }
    }

    overlayEraserRects(overlayRef.current).forEach((rect, index) => {
      if (!Array.isArray(rect) || rect.length < 4) return;
      const isSelectedEraser = index === selectedEraserRef.current;
      layer.add(new Konva.Rect({
        ...toStageRect(rect),
        fill: "white",
        opacity: isSelectedEraser ? 0.6 : 0.42,
        stroke: isSelectedEraser ? "#0e7490" : "#06b6d4",
        strokeWidth: isSelectedEraser ? 2.5 : 1,
        dash: isSelectedEraser ? [] : [5, 4],
        listening: false,
      }));
    });

    overlayControlItems(imageRef.current, overlayRef.current).forEach((region) => {
      const rid = overlayItemKey(region);
      const labelText = overlayItemLabel(region);
      const selected = rid === String(selectedRegionRef.current);
      const grouped = !selected && extraSelectedRef.current.includes(rid);
      const processingBasis = regionProcessingBasis(region);
      const regionColor = overlayRegionColor(region);
      const regionRect = region.region_outline_rect || region.bubble_rect || region.unboxed_rect || region.source_rect;
      const override = overlayRegionValue(overlayRef.current, rid);
      const safeRect = selected
        ? (override.safe_text_rect || region.safe_text_rect || regionRect)
        : (override.safe_text_rect || region.safe_text_rect || region.bubble_rect || region.unboxed_rect || region.source_rect);
      const rotationDeg = Number(override.rotation_deg || 0) || 0;
      const editableSafeRect = safeRect && (safeRect[2] < OVERLAY_MIN_RECT_SIZE || safeRect[3] < OVERLAY_MIN_RECT_SIZE)
        ? (region.safe_text_rect || regionRect || safeRect)
        : safeRect;
      if (editableSafeRect) {
        if (grouped) {
          layer.add(new Konva.Rect({
            ...toStageRotatedRect(editableSafeRect, rotationDeg),
            fill: "#2563eb",
            opacity: 0.14,
            stroke: "#2563eb",
            strokeWidth: 1.5,
            listening: false,
          }));
        }
        if (selected) {
          const moveRect = new Konva.Rect({
            ...toStageRotatedRect(editableSafeRect, rotationDeg),
            fill: "#ef4444",
            opacity: 0.10,
            draggable: false,
          });
          moveRect.on("dblclick dbltap", (event) => {
            event.cancelBubble = true;
            openFloatingEditor(rid, true);
          });
          moveRect.on("mouseenter", () => setStageCursor("move"));
          moveRect.on("mouseleave", () => setStageCursor("crosshair"));
          layer.add(moveRect);
        }
        if (
          processingBasis !== TEXT_BASED
          && regionRect
          && !rectsApproximatelyEqual(regionRect, editableSafeRect)
        ) {
          layer.add(new Konva.Rect({
            ...toStageRotatedRect(regionRect, 0),
            stroke: regionColor,
            strokeWidth: selected ? 1.1 : 0.8,
            opacity: selected ? 0.34 : 0.20,
            dash: [3, 5],
            listening: false,
          }));
        }
        const safeBorder = new Konva.Rect({
          ...toStageRotatedRect(editableSafeRect, rotationDeg),
          stroke: selected ? "#ef4444" : regionColor,
          strokeWidth: selected ? 2 : processingBasis === TEXT_BASED ? 1.8 : 1.1,
          opacity: selected ? 0.95 : processingBasis === TEXT_BASED ? 0.82 : 0.50,
          dash: processingBasis === TEXT_BASED ? [] : [5, 4],
          hitStrokeWidth: selected ? 18 : 14,
          draggable: false,
        });
        safeBorder.on("dblclick dbltap", (event) => {
          event.cancelBubble = true;
          openFloatingEditor(rid, true);
        });
        if (selected) {
          safeBorder.on("mouseenter", () => setStageCursor("grab"));
          safeBorder.on("mouseleave", () => setStageCursor("crosshair"));
        }
        layer.add(safeBorder);
        if (selected) {
          Object.entries(handlePoints(editableSafeRect)).forEach(([name, point]) => {
            const [hx, hy] = rotatedPoint(point, editableSafeRect, rotationDeg);
            const size = 10;
            const stagePoint = toStageRect([hx, hy, 0, 0]);
            const handleNode = new Konva.Rect({
              x: stagePoint.x - size / 2,
              y: stagePoint.y - size / 2,
              width: size,
              height: size,
              fill: "white",
              stroke: "#ef4444",
              strokeWidth: 1.5,
              draggable: false,
              name: `handle-${name}`,
            });
            handleNode.on("mouseenter", () => setStageCursor(resizeCursorForHandle(name)));
            handleNode.on("mouseleave", () => setStageCursor("crosshair"));
            layer.add(handleNode);
          });
          const [topX, topY] = rotatedPoint([editableSafeRect[0] + editableSafeRect[2] / 2, editableSafeRect[1]], editableSafeRect, rotationDeg);
          const [rhx, rhy] = rotationHandlePoint(editableSafeRect, rotationDeg);
          const topStage = toStageRect([topX, topY, 0, 0]);
          const rotateStage = toStageRect([rhx, rhy, 0, 0]);
          layer.add(new Konva.Line({
            points: [topStage.x, topStage.y, rotateStage.x, rotateStage.y],
            stroke: "#ef4444",
            strokeWidth: 1.2,
            dash: [4, 4],
            listening: false,
          }));
          const rotateNode = new Konva.Circle({
            x: rotateStage.x,
            y: rotateStage.y,
            radius: 7,
            fill: "white",
            stroke: "#ef4444",
            strokeWidth: 1.8,
            draggable: false,
            name: "handle-rotate",
          });
          rotateNode.on("mouseenter", () => setStageCursor("grab"));
          rotateNode.on("mouseleave", () => setStageCursor("crosshair"));
          layer.add(rotateNode);
        }
      }
      const labelRect = editableSafeRect || regionRect;
      if (labelRect) {
        const labelStageRect = toStageRect(labelRect);
        const lx = labelStageRect.x;
        const ly = labelStageRect.y;
        // 오버레이에는 R 번호만 둔다. 처리 기준 배지(경계 기반·텍스트 기반)는 다른 박스 선택을 가려서 뺀다.
        const label = new Konva.Label({ x: lx, y: Math.max(0, ly - 18), listening: false });
        label.opacity(selected ? 0.95 : 0.55);
        label.add(new Konva.Tag({ fill: selected ? "#ef4444" : regionColor }));
        label.add(new Konva.Text({ text: labelText, fill: "white", padding: 3, fontSize: 11, fontStyle: "bold" }));
        layer.add(label);
      }
    });

    layer.draw();
  }

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.innerHTML = "";
    stageRef.current = new Konva.Stage({ container: host, width: host.clientWidth || 800, height: host.clientHeight || 600 });
    const stage = stageRef.current;
    const vp = vpRef.current;
    vp.attach();
    const onDoubleClick = (event) => {
      if (isFloatingEditorEvent(event)) return;
      // 첫 클릭이 방금 확대했으면 화면이 움직인 뒤라 두 번째 클릭 자리가 다른 박스일 수 있다. 확대한 박스를 편집한다.
      const justFocused = floatingEditorStateRef.current;
      if (justFocused && Date.now() - focusOpenedAtRef.current < 600) {
        openFloatingEditor(justFocused.rid, true);
        dragRef.current = null;
        clearPreviewRect();
        return;
      }
      const point = pointerToImage(stage);
      const hit = findRegionAt(point);
      if (!hit) {
        // 빈 곳 더블클릭은 화면 맞춤
        vp.fit();
        return;
      }
      const key = overlayItemKey(hit);
      openFloatingEditor(key, true);
      dragRef.current = null;
      clearPreviewRect();
    };
    const onDown = (event) => {
      if (isFloatingEditorEvent(event)) return;
      if (Date.now() < suppressStagePointerUntilRef.current) return;
      const point = pointerToImage(stage);
      const img = imageObjRef.current;
      if (eraserActiveRef.current) {
        closeFloatingEditor();
        dragRef.current = {
          startClient: eventClientPoint(event),
          start: point,
          active: true,
          mode: "eraser",
          imageSize: img ? { width: img.width, height: img.height } : null,
        };
        setPreviewRect({ tool: "eraser", rect: [point.x, point.y, 1, 1] });
        return;
      }
      const hit = findRegionAt(point);
      const hitKey = hit ? overlayItemKey(hit) : null;
      const domEvent = event?.evt || event;
      if (hit && (domEvent?.shiftKey || domEvent?.metaKey || domEvent?.ctrlKey) && onToggleRegionRef.current) {
        // Shift/Cmd+클릭: 여러 박스를 선택에 더하거나 뺀다(이동·크기 조절 없음).
        onToggleRegionRef.current(hitKey);
        dragRef.current = null;
        clearPreviewRect();
        return;
      }
      const previousSelectedKey = selectedRegionRef.current ? String(selectedRegionRef.current) : null;
      const wasSelectedHit = Boolean(hitKey && previousSelectedKey === String(hitKey));
      if (!hit && onSelectRegionsRef.current) {
        // 빈 곳을 끌면 사각형 안의 박스를 한꺼번에 고른다. Shift/Cmd를 누르고 시작하면 기존 선택에 더한다.
        const additive = Boolean(domEvent?.shiftKey || domEvent?.metaKey || domEvent?.ctrlKey);
        if (!additive) {
          selectRegionId(null);
          selectedRegionRef.current = null;
        }
        closeFloatingEditor({ fit: true });
        dragSeqRef.current += 1;
        dragRef.current = {
          id: dragSeqRef.current,
          start: point,
          startClient: eventClientPoint(event),
          moved: false,
          active: true,
          mode: "marquee",
          additive,
          imageSize: null,
        };
        clearPreviewRect();
        return;
      }
      if (hit) {
        selectRegionId(hitKey);
        selectedRegionRef.current = hitKey;
      } else {
        selectRegionId(null);
        selectedRegionRef.current = null;
        closeFloatingEditor({ fit: true });
      }
      const safeRect = hit ? regionDisplayRect(hit) : selectedSafeRect();
      const rotationDeg = hit ? rotationForRegion(hitKey) : 0;
      const rotateHit = wasSelectedHit ? rotationHandleAt(point, safeRect, rotationDeg) : false;
      const handle = wasSelectedHit && !rotateHit ? rotatedHandleAt(point, safeRect, rotationDeg) : null;
      const moveHit = wasSelectedHit && pointInRotatedRect(point, safeRect, rotationDeg);
      const mode = rotateHit ? "rotate" : (handle ? "resize" : (moveHit ? "move" : "select"));
      dragSeqRef.current += 1;
      dragRef.current = {
        id: dragSeqRef.current,
        start: point,
        startClient: eventClientPoint(event),
        moved: false,
        active: Boolean(wasSelectedHit && (rotateHit || handle || moveHit)),
        mode,
        handle,
        hitKey,
        startRect: safeRect,
        startRotation: rotationDeg,
        lastAngle: rotationDeg,
        imageSize: img ? { width: img.width, height: img.height } : null,
      };
      clearPreviewRect();
    };
    const dragRectAt = (point) => {
      if (!dragRef.current?.active) return;
      const drag = dragRef.current;
      let rect = null;
      if (drag.mode === "eraser") {
        rect = rectFromPoints(drag.start, point);
      } else if (drag.mode === "rotate") {
        return null;
      } else if (drag.mode === "resize" && drag.startRect && drag.handle) {
        rect = resizedRectFromHandle(drag.startRect, drag.handle, point);
      } else if (drag.mode === "move" && drag.startRect) {
        const dx = point.x - drag.start.x;
        const dy = point.y - drag.start.y;
        rect = [drag.startRect[0] + dx, drag.startRect[1] + dy, drag.startRect[2], drag.startRect[3]];
      } else {
        rect = rectFromPoints(drag.start, point);
      }
      if (rect && drag.imageSize) {
        rect = drag.mode === "move"
          ? clampMovedRect(rect, drag.imageSize)
          : clampEditableRect(rect, drag.imageSize, drag.startRect);
      }
      return rect;
    };
    const onMove = (event) => {
      if (isFloatingEditorEvent(event)) return;
      if (Date.now() < suppressStagePointerUntilRef.current) return;
      const drag = dragRef.current;
      // move/resize/rotate는 화면 기준 3px 이상 움직인 뒤부터 반영한다.
      if (drag?.active && drag.mode !== "eraser" && !drag.moved) {
        if (!dragMovedEnough(drag, event)) return;
        drag.moved = true;
      }
      const point = eventToImage(event) || pointerToImage(stage);
      const rect = dragRectAt(point);
      if (rect && rect[2] >= 4 && rect[3] >= 4) {
        setPreviewRect(drag?.mode === "eraser" || drag?.mode === "marquee" ? { tool: drag.mode, rect } : rect);
      } else if (drag?.mode === "rotate" && drag.startRect && selectedRegionRef.current) {
        const angle = rotationAngleForPoint(point, drag.startRect, Boolean((event?.evt || event)?.shiftKey));
        // window/stage mousemove가 같은 이벤트로 두 번 들어오므로 같은 각도는 다시 커밋하지 않는다.
        if (angle === drag.lastAngle) return;
        drag.lastAngle = angle;
        // 한 번의 회전 드래그는 실행 취소 기록 한 칸으로 묶는다.
        updateRegionOverride(selectedRegionRef.current, { rotation_deg: angle }, `rotate:${drag.id}`);
        setStatus(`${selectedRegionRef.current} rotation ${angle}°`);
      }
    };
    const onUp = (event) => {
      if (isFloatingEditorEvent(event)) return;
      if (Date.now() < suppressStagePointerUntilRef.current) {
        dragRef.current = null;
        clearPreviewRect();
        return;
      }
      const end = eventToImage(event) || pointerToImage(stage);
      const drag = dragRef.current;
      const rect = drag?.active ? dragRectAt(end) : null;
      // 클릭/더블클릭(이동 없음)은 safe rect·회전을 manual override로 저장하지 않는다.
      const moved = Boolean(drag?.moved || (drag?.active && dragMovedEnough(drag, event)));
      if (drag?.mode === "eraser") {
        if (rect && rect[2] >= 4 && rect[3] >= 4 && moved) {
          addEraserRect(rect);
          selectEraserIndex(null);
        } else {
          // 끌지 않고 클릭하면 그 자리의 지우개 영역을 고른다 (Delete로 삭제).
          const index = eraserIndexAt(end);
          selectEraserIndex(index);
          if (index != null) setStatus(`지우개 영역 ${index + 1} 선택 (Delete로 삭제)`);
        }
      } else if (drag?.mode === "marquee") {
        if (moved && rect && rect[2] >= 4 && rect[3] >= 4) {
          const keys = overlayControlItems(imageRef.current, overlayRef.current)
            .filter((region) => {
              const box = regionDisplayRect(region);
              return box && box[0] < rect[0] + rect[2] && box[0] + box[2] > rect[0] && box[1] < rect[1] + rect[3] && box[1] + box[3] > rect[1];
            })
            .map((region) => overlayItemKey(region));
          if (keys.length) {
            onSelectRegionsRef.current?.(keys, drag.additive);
            setStatus(`${keys.length}개 박스 선택`);
          }
        }
      } else if (drag?.hitKey && !moved && (drag.mode === "select" || drag.mode === "move")) {
        // 움직이지 않은 클릭: 박스로 확대해 편집하거나, 열려 있으면 닫고 원래 배율로 돌아간다.
        toggleFocusEditorFromClick(String(drag.hitKey));
      } else if (drag?.mode === "rotate") {
        if (moved && drag.startRect && selectedRegionRef.current) {
          const angle = rotationAngleForPoint(end, drag.startRect, Boolean((event?.evt || event)?.shiftKey));
          if (angle !== drag.lastAngle) {
            updateRegionOverride(selectedRegionRef.current, { rotation_deg: angle }, `rotate:${drag.id}`);
            setStatus(`${selectedRegionRef.current} rotation ${angle}°`);
          }
        }
      } else if (
        moved
        && rect
        && rect[2] >= 4
        && rect[3] >= 4
        && selectedRegionRef.current
        && !rectsApproximatelyEqual(rect, drag.startRect, 0)
      ) {
        updateRegionOverride(selectedRegionRef.current, { safe_text_rect: rect });
        const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === String(selectedRegionRef.current));
        const labelText = region ? overlayItemLabel(region) : selectedRegionRef.current;
        setStatus(`${labelText} safe rect ${drag?.mode === "resize" ? "크기 조절" : drag?.mode === "move" ? "이동" : "조정"}`);
      }
      dragRef.current = null;
      clearPreviewRect();
    };
    stage.on("dblclick dbltap", onDoubleClick);
    stage.on("mousedown touchstart", onDown);
    stage.on("mousemove touchmove", onMove);
    stage.on("mouseup touchend", onUp);
    const resize = () => draw();
    const onWindowMove = (event) => {
      if (isFloatingEditorEvent(event)) return;
      if (!dragRef.current?.active) return;
      onMove(event);
    };
    const onWindowUp = (event) => {
      if (isFloatingEditorEvent(event)) return;
      if (!dragRef.current?.active) return;
      onUp(event);
    };
    window.addEventListener("resize", resize);
    // 창 크기는 그대로인데 원본 창을 접거나 패널 폭이 바뀌어 캔버스 자리만 달라져도 다시 맞춘다.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => resize());
    observer?.observe(host);
    window.addEventListener("mousemove", onWindowMove, true);
    window.addEventListener("mouseup", onWindowUp, true);
    return () => {
      vp.detach();
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onWindowMove, true);
      window.removeEventListener("mouseup", onWindowUp, true);
      stage.destroy();
      stageRef.current = null;
    };
  }, []);

  useEffect(() => {
    // 다른 페이지로 바뀐 뒤라 모아 둔 입력을 새 페이지에 쓰지 않는다.
    closeFloatingEditor({ discard: true });
    selectEraserIndex(null);
  }, [image?.id, image?.input_url]);

  useEffect(() => {
    // 도구가 바뀌면 커서와 지우개 선택을 정리한다.
    setStageCursor(tool === "eraser" ? "cell" : "crosshair");
    if (tool !== "eraser") selectEraserIndex(null);
    vpRef.current.refreshCursor();
  }, [tool]);

  useEffect(() => {
    // 실행 취소 등으로 eraser rect가 줄면 범위를 벗어난 선택을 지운다.
    const index = selectedEraserRef.current;
    if (index != null && index >= overlayEraserRects(overlayGuidance).length) selectEraserIndex(null);
  }, [overlayGuidance]);

  useEffect(() => {
    const pageKey = imageKey();
    // 다른 페이지면 이전 이미지를 바로 내리고, 같은 페이지의 새 preview 버전이면 로드될 때까지 이전 이미지를 유지한다.
    if (loadedPageKeyRef.current !== pageKey) imageObjRef.current = null;
    const url = image?.final_url || (canRenderOverlayImage(image) ? "" : image?.input_url);
    if (!url) {
      imageObjRef.current = null;
      draw();
      return undefined;
    }
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      imageObjRef.current = img;
      loadedPageKeyRef.current = pageKey;
      draw();
    };
    img.src = url;
    return () => {
      cancelled = true;
      img.onload = null;
    };
  }, [image?.final_url, image?.has_integrated_json, image?.input_url, image?.id]);

  const maskUrl = image?.restore_preview_url || image?.masked_url || "";
  useEffect(() => {
    maskImgRef.current = null;
    if (!maskUrl) return undefined;
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      maskImgRef.current = img;
      if (floatingEditorStateRef.current?.editing) draw();
    };
    img.src = maskUrl;
    return () => {
      cancelled = true;
      img.onload = null;
    };
  }, [maskUrl]);

  const drawnGuidanceRef = useRef(null);
  const drawDepsRef = useRef(null);
  useEffect(() => {
    // 편집 중 그 영역의 글·글자 스타일만 바뀐 경우에는 캔버스(영역 상자·덮개)가 그대로라 전체 재구성을 건너뛴다.
    const deps = [image, selectedRegionId, extraSelectedIds, eraserActive, selectedEraser, floatingEditor];
    const sameOthers = drawDepsRef.current && deps.every((value, index) => value === drawDepsRef.current[index]);
    const rid = floatingEditorStateRef.current?.rid;
    drawDepsRef.current = deps;
    const prevGuidance = drawnGuidanceRef.current;
    drawnGuidanceRef.current = overlayGuidance;
    if (sameOthers && rid != null && onlyEditorTextStyleChanged(prevGuidance, overlayGuidance, rid)) return;
    draw();
  }, [image, overlayGuidance, selectedRegionId, extraSelectedIds, eraserActive, selectedEraser, floatingEditor]);

  useEffect(() => () => clearTimeout(editorCommitTimerRef.current), []);

  const editorRegion = floatingEditor
    ? overlayControlItems(image, overlayGuidance).find((region) => overlayItemKey(region) === String(floatingEditor.rid))
    : null;

  return (
    <div className={`overlay-canvas-wrap${eraserActive ? " eraser-active" : ""}`}>
      <div className="overlay-local-toolbar" aria-label="Overlay tools">
        <button
          type="button"
          className={eraserActive ? "active" : ""}
          onClick={(event) => {
            event.preventDefault();
            toggleEraserTool();
          }}
          title={`지우개 (${shortcutFor("overlay", "tool", "eraser")}). 드래그해 영역 추가, 클릭으로 영역 선택 후 Delete`}
        >
          ⌫
        </button>
      </div>
      <div id="overlayStageHost" ref={hostRef} />
      <label className="focus-on-click" title="켜면 박스를 한 번 눌렀을 때 그 박스로 확대합니다. 끄면 선택만 합니다. 글 편집은 더블클릭이나 Enter입니다.">
        <input
          type="checkbox"
          checked={focusOnClick}
          onChange={(event) => {
            const next = event.target.checked;
            setFocusOnClick(next);
            try { window.localStorage.setItem(FOCUS_ON_CLICK_KEY, next ? "1" : "0"); } catch { /* 저장 실패는 무시 */ }
            if (!next && floatingEditorStateRef.current && !floatingEditorStateRef.current.editing) closeFloatingEditor({ fit: true });
          }}
        />
        클릭 시 박스 확대
      </label>
      {floatingEditor && editorRegion ? (() => {
        const override = overlayRegionValue(overlayGuidance, floatingEditor.rid);
        const choice = overlayWritingModeChoice(override, overlayLayoutEntry(image, editorRegion));
        const liveRect = selectedBoxRect(editorRegion, override);
        const box = focusEditorBox(liveRect ? toStageRect(liveRect) : floatingEditor.stage);
        const align = override.text_align || "center";
        const style = override.font_style || inferredRegionFontStyle(image, editorRegion);
        // 스타일(가로·세로, 글꼴, 크기 등)을 바꾼 뒤 그 렌더가 도착했을 때만 배치 기준을 새 렌더로 바꾼다.
        // 글만 고친 렌더는 받지 않는다. 직접 고친 글이 없으면 입력창 내용도 새 렌더의 줄로 맞춘다(세로 전환 등).
        if (editorPendingTextRef.current == null && !editorComposingRef.current) {
          const fresh = editorSnapshotFrom(image, editorRegion, floatingEditor.rid);
          if (fresh.styleSig !== editorSnapshotRef.current?.styleSig && overridesMatch(fresh.renderedOverride, override)) {
            editorSnapshotRef.current = fresh;
            const renderedDraft = fresh.textLayout?.trim();
            if (override.text_override == null && renderedDraft && renderedDraft !== floatingDraft) setFloatingDraft(renderedDraft);
          }
          // 실행 취소·쓰기 방향 전환 등으로 저장된 글이 바뀌면 입력창도 따라간다(입력 중인 글이 없을 때만).
          if (typeof override.text_override === "string" && override.text_override !== floatingDraft) setFloatingDraft(override.text_override);
        }
        const geo = editorTextGeometry({ snapshot: editorSnapshotRef.current, override, draft: floatingDraft, box, innerFallback: liveRect, fallbackFontImg: floatingEditor.fontImg });
        const fontImg = geo.fontImg;
        const patch = (value, key) => {
          commitEditorText();
          updateRegionOverride(floatingEditor.rid, value, key);
          // 글꼴·색·외곽선을 바꾸면 그 조합을 기억해 둔다(비운 값은 기억하지 않는다).
          const picked = {};
          for (const name of LAST_STYLE_KEYS) {
            if (name in (value || {}) && value[name] !== "" && value[name] != null) picked[name] = value[name];
          }
          if (Object.keys(picked).length) {
            const merged = { ...(readLastStyle() || {}), ...picked };
            writeLastStyle(merged);
            setLastStyle(merged);
          }
        };
        // 직전 스타일을 붙일 때는 글꼴 관련 값을 한꺼번에 맞춘다(스타일 버튼과 같은 규칙: 폰트 경로·굵기는 비움).
        const applyLastStyle = () => {
          const last = readLastStyle();
          if (!last) return;
          const value = {};
          if (last.font_style) Object.assign(value, { font_style: last.font_style, font_path: last.font_path || "", custom_font_path: last.custom_font_path || "", font_weight: last.font_weight || "" });
          for (const name of ["font_color", "stroke_color", "stroke_width"]) if (last[name] !== undefined && last[name] !== "") value[name] = last[name];
          if (Object.keys(value).length) patch(value);
        };
        const colorKey = colorPopover === "stroke" ? "stroke_color" : "font_color";
        const hexOr = (value, fallback) => (/^#[0-9a-f]{6}$/i.test(String(value || "")) ? value : fallback);
        const textColor = hexOr(override.font_color, "#000000");
        const outlineColor = hexOr(override.stroke_color, "#ffffff");
        const strokeNow = Number.isFinite(Number(override.stroke_width)) && override.stroke_width !== "" && override.stroke_width != null
          ? Number(override.stroke_width)
          : (Number.isFinite(Number(editorSnapshotRef.current?.draw?.stroke_width)) ? Number(editorSnapshotRef.current.draw.stroke_width) : 1);
        const syncCaret = (el, focused = document.activeElement === el) => {
          setEditorCaret((current) => (
            current.start === el.selectionStart && current.end === el.selectionEnd && current.focused === focused
              ? current
              : { start: el.selectionStart, end: el.selectionEnd, dir: el.selectionDirection, focused }
          ));
        };
        const caretOffset = editorCaret.dir === "backward" ? editorCaret.start : editorCaret.end;
        const caret = editorCaret.focused && editorCaret.start === editorCaret.end ? geo.caretAt(caretOffset) : null;
        const stop = (event) => event.stopPropagation();
        const guard = {
          onPointerDownCapture: (event) => {
            event.stopPropagation();
            suppressStagePointerUntilRef.current = Date.now() + 800;
            dragRef.current = null;
            if (colorPopover && !(event.target instanceof Element && event.target.closest(".inplace-popover-anchor"))) setColorPopover(null);
          },
          onMouseDownCapture: (event) => { event.stopPropagation(); suppressStagePointerUntilRef.current = Date.now() + 800; dragRef.current = null; },
          onMouseUpCapture: stop,
          onPointerDown: (event) => { event.stopPropagation(); suppressStagePointerUntilRef.current = Date.now() + 800; },
          onMouseDown: stop,
          onMouseUp: stop,
          onClick: stop,
          onDoubleClick: stop,
        };
        return (
          <>
          <div className="inplace-toolbar-dock" {...guard}>
            <div className="inplace-toolbar">
              <div className="inplace-row">
                <strong className="inplace-name">{overlayItemLabel(editorRegion)}</strong>
                <span className="inplace-group inplace-size" title="글자 크기">
                  <button type="button" onClick={() => patch({ font_size: Math.max(8, fontImg - 1) }, `size:${floatingEditor.rid}`)}>−</button>
                  <input
                    type="number"
                    min="8"
                    max="160"
                    key={fontImg}
                    defaultValue={fontImg}
                    onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); event.stopPropagation(); }}
                    onBlur={(event) => {
                      const value = Math.round(Number(event.target.value));
                      if (Number.isFinite(value) && value >= 8 && value <= 160 && value !== fontImg) patch({ font_size: value }, `size:${floatingEditor.rid}`);
                      else event.target.value = fontImg;
                    }}
                  />
                  <button type="button" onClick={() => patch({ font_size: Math.min(160, fontImg + 1) }, `size:${floatingEditor.rid}`)}>+</button>
                </span>
                <span className="inplace-sep" />
                <span className="inplace-group" role="group" aria-label="정렬">
                  {[["left", "왼쪽 정렬"], ["center", "가운데 정렬"], ["right", "오른쪽 정렬"]].map(([id, label]) => (
                    <button key={id} type="button" className={`icon ${align === id ? "active" : ""}`} title={label} aria-label={label} onClick={() => patch({ text_align: id })}><AlignIcon kind={id} /></button>
                  ))}
                </span>
                <span className="inplace-sep" />
                <span className="inplace-group" role="group" aria-label="쓰기 방향">
                  <button type="button" className={choice === "horizontal" ? "active" : ""} title="가로쓰기" onClick={() => patch({ force_horizontal: true, force_vertical: false })}><WritingIcon />가로쓰기</button>
                  <button type="button" className={choice === "vertical" ? "active" : ""} title="세로쓰기" onClick={() => patch({ force_vertical: true, force_horizontal: false })}><WritingIcon vertical />세로쓰기</button>
                </span>
                <span className="inplace-sep" />
                <span className="inplace-group inplace-popover-anchor">
                  <button type="button" className={`icon wide ${colorPopover === "font" ? "active" : ""}`} title="글자색" aria-label="글자색" onClick={() => setColorPopover((cur) => (cur === "font" ? null : "font"))}><ColorLetterIcon color={textColor} /></button>
                  <button type="button" className={`icon wide ${colorPopover === "stroke" ? "active" : ""}`} title="외곽선색" aria-label="외곽선색" onClick={() => setColorPopover((cur) => (cur === "stroke" ? null : "stroke"))}><ColorLetterIcon color={outlineColor} outlined /></button>
                  {colorPopover ? (
                    <div className="color-popover" role="dialog" aria-label={colorPopover === "font" ? "글자색" : "외곽선색"}>
                      <div className="color-popover-title">{colorPopover === "font" ? "글자색" : "외곽선색"}</div>
                      <div className="color-popover-row">
                        {COLOR_PRESETS.map(([hex, label]) => (
                          <button key={hex} type="button" className="swatch" style={{ background: hex }} title={label} aria-label={label} onClick={() => { patch({ [colorKey]: hex }); setColorPopover(null); }} />
                        ))}
                        {"EyeDropper" in window ? (
                          <button type="button" title="화면에서 색 찍기" onClick={async () => {
                            try { const r = await new window.EyeDropper().open(); if (r?.sRGBHex) patch({ [colorKey]: r.sRGBHex }); } catch (_e) { /* 취소 */ }
                            setColorPopover(null);
                          }}>스포이드</button>
                        ) : null}
                        <label className="color-custom" title="직접 선택">
                          <input type="color" value={colorPopover === "font" ? textColor : outlineColor} onChange={(event) => patch({ [colorKey]: event.target.value }, `${colorKey}:${floatingEditor.rid}`)} />
                        </label>
                        <button type="button" title="기본색으로" onClick={() => { patch({ [colorKey]: "" }); setColorPopover(null); }}>기본</button>
                      </div>
                    </div>
                  ) : null}
                </span>
                <span className="inplace-group inplace-size" title="외곽선 두께">
                  <span className="inplace-mini-label">외곽선</span>
                  <button type="button" onClick={() => patch({ stroke_width: Math.max(0, strokeNow - 1) }, `stroke:${floatingEditor.rid}`)}>−</button>
                  <input
                    type="number"
                    min="0"
                    max="12"
                    key={`stroke-${strokeNow}`}
                    defaultValue={strokeNow}
                    onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); event.stopPropagation(); }}
                    onBlur={(event) => {
                      const value = Math.round(Number(event.target.value));
                      if (Number.isFinite(value) && value >= 0 && value <= 12 && value !== strokeNow) patch({ stroke_width: value }, `stroke:${floatingEditor.rid}`);
                      else event.target.value = strokeNow;
                    }}
                  />
                  <button type="button" onClick={() => patch({ stroke_width: Math.min(12, strokeNow + 1) }, `stroke:${floatingEditor.rid}`)}>+</button>
                </span>
                <button type="button" className="icon-button inplace-close" onClick={() => closeFloatingEditor({ fit: true })} title="닫고 원래 배율로 (Esc)">×</button>
              </div>
              <div className="inplace-row presets" role="group" aria-label="스타일 프리셋">
                <span className="inplace-mini-label">프리셋</span>
                {STYLE_PRESETS.map((preset) => {
                  const active = textColor.toLowerCase() === preset.style.font_color && outlineColor.toLowerCase() === preset.style.stroke_color && strokeNow === preset.style.stroke_width;
                  return (
                    <button key={preset.id} type="button" className={`preset-chip ${active ? "active" : ""}`} title={`${preset.hint} (글자색·외곽선색·두께를 한 번에 맞춥니다)`} onClick={() => patch(preset.style)}>{preset.label}</button>
                  );
                })}
                <button type="button" className="preset-chip" disabled={!lastStyle} title={describeLastStyle(lastStyle)} onClick={applyLastStyle}>직전 스타일</button>
              </div>
              <div className="inplace-row styles" role="group" aria-label="글꼴 스타일">
                {FONT_STYLES.filter(([id]) => id !== "etc").map(([id, _short, label]) => {
                  const family = editorFontFamily(editorStyleFonts?.[id]);
                  return (
                    <button key={id} type="button" className={`style-sample ${style === id ? "active" : ""}`} title={label} aria-label={label} onClick={() => patch({ font_style: id, font_path: "", custom_font_path: "", font_weight: "" })}>
                      <span style={family ? { fontFamily: `"${family}", sans-serif` } : undefined}>가</span>
                      <small>{label}</small>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
          {floatingEditor.editing ? <div ref={floatingEditorRef} className="inplace-editor" style={{ left: box.left, top: box.top, width: box.width, height: box.height }} {...guard}>
            {/* 입력하는 대로 바로 반영된다. Esc는 닫기, Cmd/Ctrl+Enter는 렌더 후 다음 대사로(App의 전역 디스패처). */}
            <textarea
              ref={editorTextareaRef}
              key={floatingEditor.rid}
              autoFocus
              wrap="off"
              spellCheck={false}
              value={floatingDraft}
              className="inplace-text"
              style={geo.style}
              onFocus={(event) => {
                if (!event.target.dataset.focusedOnce) {
                  event.target.dataset.focusedOnce = "1";
                  const end = event.target.value.length;
                  event.target.setSelectionRange(end, end);
                }
                syncCaret(event.target, true);
              }}
              onBlur={(event) => { syncCaret(event.target, false); commitEditorText(); }}
              onSelect={(event) => syncCaret(event.target)}
              onKeyUp={(event) => syncCaret(event.target)}
              onMouseUp={(event) => syncCaret(event.target)}
              onCompositionStart={() => { editorComposingRef.current = true; }}
              onCompositionEnd={() => { editorComposingRef.current = false; scheduleEditorCommit(); }}
              onChange={(event) => {
                // 화면(입력창 글자)은 바로 바꾸고, 가이던스 반영·미리보기 요청은 잠깐 모아서 한다.
                const value = event.target.value;
                setFloatingDraft(value);
                syncCaret(event.target);
                editorPendingTextRef.current = value;
                if (!editorComposingRef.current) scheduleEditorCommit();
              }}
              placeholder="번역문을 입력하세요"
            />
            {caret ? (
              <div
                key={`${caretOffset}:${floatingDraft.length}`}
                className="inplace-caret"
                style={{ left: Math.round(caret.left) - 2, top: caret.top, height: caret.height }}
              />
            ) : null}
          </div> : null}
          </>
        );
      })() : null}
    </div>
  );
});

export default OverlayCanvas;
