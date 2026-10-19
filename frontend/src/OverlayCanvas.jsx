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
} from "./lib.js";
import RegionTypeBadge from "./RegionTypeBadge.jsx";
import { TEXT_BASED, regionProcessingBasis, regionTypeLabel } from "./regionTypes.js";
import { createViewport } from "./viewportControls.js";
import { shortcutFor } from "./keymap.js";

// 클릭/더블클릭의 미세한 흔들림을 이동으로 저장하지 않기 위한 화면 px 기준
const DRAG_COMMIT_THRESHOLD_PX = 3;

// 회전 핸들을 Shift와 함께 끌면 이 각도 단위로 맞춘다.
const ROTATION_SNAP_DEG = 15;

const OverlayCanvas = forwardRef(function OverlayCanvas({ image, overlayGuidance, setOverlayGuidance, selectedRegionId, setSelectedRegionId, setStatus, onRequestApply, tool = "select", setTool }, ref) {
  const hostRef = useRef(null);
  const stageRef = useRef(null);
  const imageObjRef = useRef(null);
  const transformRef = useRef({ scale: 1, x: 0, y: 0 });
  const dragRef = useRef(null);
  const dragSeqRef = useRef(0);
  const floatingEditorRef = useRef(null);
  const overlayLayerRef = useRef(null);
  const previewNodeRef = useRef(null);
  const imageRef = useRef(image);
  const overlayRef = useRef(overlayGuidance);
  const selectedRegionRef = useRef(selectedRegionId);
  const loadedPageKeyRef = useRef("");
  const [floatingEditor, setFloatingEditor] = useState(null);
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
    const target = event?.target || event?.evt?.target || null;
    return Boolean(node && target && node.contains(target));
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

  function floatingEditorPositionForRect(rect) {
    const host = hostRef.current;
    if (!host || !rect) return { x: 16, y: 16 };
    const stageRect = toStageRect(rect);
    const hostW = host.clientWidth || 800;
    const hostH = host.clientHeight || 600;
    const popupW = 330;
    const popupH = 300;
    const gap = 12;
    const candidates = [
      { x: stageRect.x + stageRect.width + gap, y: stageRect.y },
      { x: stageRect.x - popupW - gap, y: stageRect.y },
      { x: stageRect.x, y: stageRect.y + stageRect.height + gap },
      { x: stageRect.x, y: stageRect.y - popupH - gap },
    ];
    const fits = candidates.find((item) => (
      item.x >= gap
      && item.y >= gap
      && item.x + popupW <= hostW - gap
      && item.y + popupH <= hostH - gap
    ));
    const chosen = fits || candidates[0];
    return {
      x: Math.round(Math.min(Math.max(gap, chosen.x), Math.max(gap, hostW - popupW - gap))),
      y: Math.round(Math.min(Math.max(gap, chosen.y), Math.max(gap, hostH - popupH - gap))),
    };
  }

  function openFloatingEditor(rid, rect) {
    if (!rid || !rect) return;
    const key = String(rid);
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === key);
    const override = overlayRegionValue(overlayRef.current, rid);
    const position = floatingEditorPositionForRect(rect);
    suppressStagePointerUntilRef.current = Date.now() + 350;
    dragRef.current = null;
    clearPreviewRect();
    selectRegionId(key);
    selectedRegionRef.current = key;
    setFloatingDraft(region ? (override.text_override ?? baseRegionText(imageRef.current, region)) : "");
    setFloatingEditor({ rid: key, ...position });
  }

  function closeFloatingEditor() {
    setFloatingEditor(null);
    setFloatingDraft("");
  }

  function applyFloatingEditor() {
    if (!floatingEditor?.rid) return false;
    updateRegionOverride(floatingEditor.rid, { text_override: floatingDraft });
    closeFloatingEditor();
    const region = overlayControlItems(imageRef.current, overlayRef.current).find((item) => overlayItemKey(item) === String(floatingEditor.rid));
    setStatus(`${region ? overlayItemLabel(region) : floatingEditor.rid} text 적용`);
    return true;
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
    openEditorForSelected: () => {
      const key = selectedRegionRef.current;
      const rect = regionRectByKey(key);
      if (!rect) return false;
      openFloatingEditor(String(key), rect);
      return true;
    },
    closeEditor: () => {
      if (!floatingEditor) return false;
      closeFloatingEditor();
      setStatus("대사 편집 취소");
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
    if (!previewNodeRef.current || previewNodeRef.current.isDestroyed?.()) {
      previewNodeRef.current = new Konva.Rect({ listening: false });
      layer.add(previewNodeRef.current);
    }
    previewNodeRef.current.setAttrs({
      ...toStageRect(previewRect),
      fill: isEraserPreview ? "white" : undefined,
      opacity: isEraserPreview ? 0.55 : 1,
      stroke: isEraserPreview ? "#06b6d4" : "#ef4444",
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
    layer.add(new Konva.Image({ image: img, ...toStageRect([0, 0, img.width, img.height]) }));

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
        if (selected) {
          const moveRect = new Konva.Rect({
            ...toStageRotatedRect(editableSafeRect, rotationDeg),
            fill: "#ef4444",
            opacity: 0.10,
            draggable: false,
          });
          moveRect.on("dblclick dbltap", (event) => {
            event.cancelBubble = true;
            openFloatingEditor(rid, editableSafeRect);
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
          openFloatingEditor(rid, editableSafeRect);
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
      openFloatingEditor(key, regionDisplayRect(hit));
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
      const previousSelectedKey = selectedRegionRef.current ? String(selectedRegionRef.current) : null;
      const wasSelectedHit = Boolean(hitKey && previousSelectedKey === String(hitKey));
      if (hit) {
        selectRegionId(hitKey);
        selectedRegionRef.current = hitKey;
      } else {
        selectRegionId(null);
        selectedRegionRef.current = null;
        closeFloatingEditor();
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
        setPreviewRect(drag?.mode === "eraser" ? { tool: "eraser", rect } : rect);
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

  useEffect(() => {
    draw();
  }, [image, overlayGuidance, selectedRegionId, eraserActive, selectedEraser]);

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
      {floatingEditor && editorRegion ? (
        <div
          ref={floatingEditorRef}
          className="floating-text-editor"
          style={{ left: floatingEditor.x, top: floatingEditor.y }}
          onPointerDownCapture={(event) => {
            event.stopPropagation();
            suppressStagePointerUntilRef.current = Date.now() + 800;
            dragRef.current = null;
          }}
          onMouseDownCapture={(event) => {
            event.stopPropagation();
            suppressStagePointerUntilRef.current = Date.now() + 800;
            dragRef.current = null;
          }}
          onMouseUpCapture={(event) => event.stopPropagation()}
          onPointerDown={(event) => {
            event.stopPropagation();
            suppressStagePointerUntilRef.current = Date.now() + 800;
          }}
          onMouseDown={(event) => event.stopPropagation()}
          onMouseUp={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
          onDoubleClick={(event) => event.stopPropagation()}
        >
          <div className="floating-text-editor-head">
            <strong>{overlayItemLabel(editorRegion)}</strong><RegionTypeBadge region={editorRegion} />
            <button type="button" className="icon-button" onClick={closeFloatingEditor} title="닫기">×</button>
          </div>
          {/* Esc(취소)와 Cmd/Ctrl+Enter(반영 후 render)는 App의 전역 디스패처가 처리한다. */}
          <textarea
            rows={7}
            autoFocus
            value={floatingDraft}
            onChange={(event) => setFloatingDraft(event.target.value)}
            placeholder="번역문을 입력하세요"
          />
          <div className="floating-text-editor-actions">
            <button type="button" onClick={closeFloatingEditor}>Cancel</button>
            <button type="button" className="primary" onClick={applyFloatingEditor}>Apply</button>
          </div>
        </div>
      ) : null}
    </div>
  );
});

export default OverlayCanvas;
