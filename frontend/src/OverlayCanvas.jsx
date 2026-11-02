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
  FONT_STYLES,
} from "./lib.js";
import RegionTypeBadge from "./RegionTypeBadge.jsx";
import { TEXT_BASED, regionProcessingBasis, regionTypeLabel } from "./regionTypes.js";
import { createViewport } from "./viewportControls.js";
import { shortcutFor } from "./keymap.js";

// 클릭/더블클릭의 미세한 흔들림을 이동으로 저장하지 않기 위한 화면 px 기준
const DRAG_COMMIT_THRESHOLD_PX = 3;

// 회전 핸들을 Shift와 함께 끌면 이 각도 단위로 맞춘다.
const ROTATION_SNAP_DEG = 15;

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
  const floatingEditorRef = useRef(null);
  const floatingEditorStateRef = useRef(null);
  const onFocusRectRef = useRef(null);
  onFocusRectRef.current = onFocusRect;
  const focusOpenedAtRef = useRef(0);
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
    const rects = [
      selectedBoxRect(region, override),
      regionDisplayRect(region),
      region?.region_outline_rect,
    ].filter((rect) => Array.isArray(rect) && rect.length >= 4 && rect[2] > 0 && rect[3] > 0);
    if (!rects.length) return null;
    const x1 = Math.max(0, Math.min(...rects.map((r) => r[0])));
    const y1 = Math.max(0, Math.min(...rects.map((r) => r[1])));
    const x2 = Math.min(img.width, Math.max(...rects.map((r) => r[0] + r[2])));
    const y2 = Math.min(img.height, Math.max(...rects.map((r) => r[1] + r[3])));
    if (x2 <= x1 || y2 <= y1) return null;
    return [x1, y1, x2 - x1, y2 - y1];
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
      left: Math.round(stageRect.x + offsetX) + inset,
      top: Math.round(stageRect.y + offsetY) + inset,
      width: Math.max(20, Math.round(stageRect.width) - inset * 2),
      height: Math.max(20, Math.round(stageRect.height) - inset * 2),
    };
  }

  // 박스를 눌렀을 때: 그 박스로 확대하고, 같은 자리·같은 글자 크기의 편집 박스를 겹쳐 띄운다.
  function openFloatingEditor(rid) {
    if (!rid) return;
    const key = String(rid);
    if (floatingEditorStateRef.current?.rid === key) return;
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === key);
    const rect = region ? regionDisplayRect(region) : null;
    if (!region || !rect) return;
    const override = overlayRegionValue(overlayRef.current, key);
    suppressStagePointerUntilRef.current = Date.now() + 350;
    dragRef.current = null;
    clearPreviewRect();
    selectRegionId(key);
    selectedRegionRef.current = key;
    vpRef.current.zoomToRect(rect, 0.5);
    focusOpenedAtRef.current = Date.now();
    const text = override.text_override ?? baseRegionText(imageRef.current, region);
    const fontImg = Number(override.font_size) || overlayItemFontSize(region) || 24;
    const fontPx = Math.max(9, Math.round(fontImg * transformRef.current.scale));
    setFloatingDraft(text);
    setFloatingEditor({ rid: key, stage: toStageRect(rect), fontPx, fontImg });
    onFocusRectRef.current?.(rect);
  }

  // fit이 true면 확대를 풀고 전체 보기로 되돌린다(편집 창이 열려 있었을 때만).
  function closeFloatingEditor({ fit = false } = {}) {
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
  function toggleFocusEditorFromClick(key) {
    const open = floatingEditorStateRef.current;
    if (open) {
      // 더블클릭의 두 번째 클릭이 방금 연 편집 창을 바로 닫지 않게 한다.
      if (open.rid === key && Date.now() - focusOpenedAtRef.current < 450) return;
      closeFloatingEditor({ fit: true });
      return;
    }
    openFloatingEditor(key);
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
    openEditorFor: (key) => { if (key == null) return false; openFloatingEditor(String(key)); return true; },
    openEditorForSelected: () => {
      const key = selectedRegionRef.current;
      const rect = regionRectByKey(key);
      if (!rect) return false;
      openFloatingEditor(String(key));
      return true;
    },
    closeEditor: () => {
      if (!floatingEditor) return false;
      closeFloatingEditor({ fit: true });
      setStatus("대사 편집 닫기");
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
    if (editing?.rid) {
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
            openFloatingEditor(rid);
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
          openFloatingEditor(rid);
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
        const label = new Konva.Label({ x: lx, y: Math.max(0, ly - 18) });
        label.opacity(selected ? 0.95 : 0.55);
        label.add(new Konva.Tag({ fill: selected ? "#ef4444" : regionColor }));
        label.add(new Konva.Text({ text: labelText, fill: "white", padding: 3, fontSize: 11, fontStyle: "bold" }));
        layer.add(label);
        const badge = new Konva.Label({ x: lx + label.width() + 3, y: Math.max(0, ly - 18), listening: false });
        badge.add(new Konva.Tag({ fill: regionColor, cornerRadius: 3 }));
        badge.add(new Konva.Text({ text: regionTypeLabel(region), fill: "white", padding: 3, fontSize: 11 }));
        layer.add(badge);
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
      const point = pointerToImage(stage);
      const hit = findRegionAt(point);
      if (!hit) {
        // 빈 곳 더블클릭은 화면 맞춤
        vp.fit();
        return;
      }
      const key = overlayItemKey(hit);
      openFloatingEditor(key);
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
    window.addEventListener("mousemove", onWindowMove, true);
    window.addEventListener("mouseup", onWindowUp, true);
    return () => {
      vp.detach();
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onWindowMove, true);
      window.removeEventListener("mouseup", onWindowUp, true);
      stage.destroy();
      stageRef.current = null;
    };
  }, []);

  useEffect(() => {
    closeFloatingEditor();
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
      if (floatingEditorStateRef.current) draw();
    };
    img.src = maskUrl;
    return () => {
      cancelled = true;
      img.onload = null;
    };
  }, [maskUrl]);

  useEffect(() => {
    draw();
  }, [image, overlayGuidance, selectedRegionId, extraSelectedIds, eraserActive, selectedEraser, floatingEditor]);

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
      {floatingEditor && editorRegion ? (() => {
        const override = overlayRegionValue(overlayGuidance, floatingEditor.rid);
        const choice = overlayWritingModeChoice(override);
        const vertical = choice === "vertical";
        const fontImg = Number(override.font_size) || floatingEditor.fontImg;
        const fontPx = Math.max(9, Math.round(fontImg * transformRef.current.scale));
        const liveRect = selectedBoxRect(editorRegion, override);
        const box = focusEditorBox(liveRect ? toStageRect(liveRect) : floatingEditor.stage);
        const align = override.text_align || "center";
        const style = override.font_style || inferredRegionFontStyle(image, editorRegion);
        const color = /^#[0-9a-f]{6}$/i.test(String(override.font_color || "")) ? override.font_color : "#000000";
        // 렌더와 같은 줄 간격(글자 크기 x 1.14)으로, 글 묶음을 박스 가운데에 놓는다.
        const lineHeightPx = fontPx * 1.14;
        const lineCount = Math.max(1, String(floatingDraft || "").split("\n").length);
        const blockExtent = lineHeightPx * (lineCount - 1) + fontPx;
        const crossPad = Math.max(0, ((vertical ? box.width : box.height) - blockExtent) / 2);
        const serif = /serif/.test(style);
        const fontWeight = style === "light" ? 300 : /bold|impact|emphasis/.test(style) ? 800 : 500;
        const patch = (value, key) => updateRegionOverride(floatingEditor.rid, value, key);
        const stop = (event) => event.stopPropagation();
        const guard = {
          onPointerDownCapture: (event) => { event.stopPropagation(); suppressStagePointerUntilRef.current = Date.now() + 800; dragRef.current = null; },
          onMouseDownCapture: (event) => { event.stopPropagation(); suppressStagePointerUntilRef.current = Date.now() + 800; dragRef.current = null; },
          onMouseUpCapture: stop,
          onPointerDown: (event) => { event.stopPropagation(); suppressStagePointerUntilRef.current = Date.now() + 800; },
          onMouseDown: stop,
          onMouseUp: stop,
          onClick: stop,
          onDoubleClick: stop,
        };
        return (
          <div ref={floatingEditorRef} className="inplace-editor" style={{ left: box.left, top: box.top, width: box.width, height: box.height }} {...guard}>
            <div className="inplace-toolbar" style={{ left: -Math.min(Math.max(0, box.left + 700 - ((hostRef.current?.clientWidth || 800) - 12)), Math.max(0, box.left - 12)) }}>
              <strong>{overlayItemLabel(editorRegion)}</strong>
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
              <span className="inplace-group" title="정렬">
                {[["left", "좌"], ["center", "중"], ["right", "우"]].map(([id, label]) => (
                  <button key={id} type="button" className={align === id ? "active" : ""} onClick={() => patch({ text_align: id })}>{label}</button>
                ))}
              </span>
              <span className="inplace-group" title="쓰기 방향">
                <button type="button" className={vertical ? "" : "active"} onClick={() => patch({ force_horizontal: true, force_vertical: false })}>가로</button>
                <button type="button" className={vertical ? "active" : ""} onClick={() => patch({ force_vertical: true, force_horizontal: false })}>세로</button>
              </span>
              <span className="inplace-group" title="글꼴 스타일">
                {FONT_STYLES.filter(([id]) => id !== "etc").map(([id, short, label]) => (
                  <button key={id} type="button" className={style === id ? "active" : ""} title={label} onClick={() => patch({ font_style: id, font_path: "", custom_font_path: "", font_weight: "" })}>{short}</button>
                ))}
              </span>
              <span className="inplace-group" title="글자색">
                <button type="button" className="swatch" style={{ background: "#000000" }} onClick={() => patch({ font_color: "#000000" })} aria-label="글자 검정" />
                <button type="button" className="swatch" style={{ background: "#ffffff" }} onClick={() => patch({ font_color: "#ffffff" })} aria-label="글자 흰색" />
                {"EyeDropper" in window ? (
                  <button type="button" title="캔버스에서 글자색 찍기" onClick={async () => {
                    try { const r = await new window.EyeDropper().open(); if (r?.sRGBHex) patch({ font_color: r.sRGBHex }); } catch (_e) { /* 취소 */ }
                  }}>스포이드</button>
                ) : null}
              </span>
              <button type="button" className="icon-button" onClick={() => closeFloatingEditor({ fit: true })} title="닫고 원래 배율로 (Esc)">×</button>
            </div>
            {/* 입력하는 대로 바로 반영된다. Esc는 닫기, Cmd/Ctrl+Enter는 렌더 후 다음 대사로(App의 전역 디스패처). */}
            <textarea
              autoFocus
              wrap="off"
              spellCheck={false}
              value={floatingDraft}
              className={vertical ? "vertical" : ""}
              style={{
                fontSize: fontPx,
                lineHeight: `${lineHeightPx}px`,
                fontFamily: serif ? '"SourceHanSerifK-Bold", RIDIBatang, "Noto Serif KR", serif' : undefined,
                fontWeight,
                textAlign: align,
                ...(vertical
                  ? { paddingLeft: crossPad, paddingRight: crossPad, paddingTop: 0, paddingBottom: 0 }
                  : { paddingTop: crossPad, paddingBottom: 0, paddingLeft: 0, paddingRight: 0 }),
                color,
                WebkitTextStroke: `${Math.max(0, (Number.isFinite(Number(override.stroke_width)) ? Number(override.stroke_width) : 2) * transformRef.current.scale * 2)}px ${/^#[0-9a-f]{6}$/i.test(String(override.stroke_color || "")) ? override.stroke_color : "#ffffff"}`,
              }}
              onFocus={(event) => { const end = event.target.value.length; event.target.setSelectionRange(end, end); }}
              onChange={(event) => {
                const value = event.target.value;
                setFloatingDraft(value);
                updateRegionOverride(floatingEditor.rid, { text_override: value }, `text:${floatingEditor.rid}`);
              }}
              placeholder="번역문을 입력하세요"
            />
          </div>
        );
      })() : null}
    </div>
  );
});

export default OverlayCanvas;
