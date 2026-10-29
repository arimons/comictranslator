import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import Konva from "konva";
import { createPortal } from "react-dom";
import { rectOf, tgId, imageGroups, defaultGuidance, regionId, imageRegions, regionDisplayRect, overlayRegionColor } from "./lib.js";
import { createViewport } from "./viewportControls.js";
import { placeCanvasLabels, promotedTgIds } from "./canvasLabels.js";
import { regionIsExcluded, tgIsExcluded } from "./regionExclusion.js";
import { TEXT_BASED, regionProcessingBasis, regionTypeLabel, regionBindingKey, usesEdgeBarrier } from "./regionTypes.js";

const SELECTED_REGION_COLOR = "#e11d48";
// Edge는 검출 결과(초록)와 같은 계열로 벽선도 초록으로 그린다. 선택 강조색(빨강)과 겹치지 않게 한다.
const EDGE_WALL_COLOR = "#16a34a";

const EditorCanvas = forwardRef(function EditorCanvas({ image, baseUrl, guidance, setGuidance, tool, layers, selectedTg, setSelectedTg, selectedRegionId, setSelectedRegionId, onToolComplete, setStatus, speakerLabels }, ref) {
  const hostRef = useRef(null);
  const stageRef = useRef(null);
  const imageObjRef = useRef(null);
  const transformRef = useRef({ scale: 1, x: 0, y: 0, w: 1, h: 1 });
  const dragRef = useRef(null);
  // 포인터가 캔버스 위에 있을 때만 Delete 키가 포인터 아래 항목을 지운다.
  const pointerInsideRef = useRef(false);
  const toolRef = useRef(tool);
  const imageRef = useRef(image);
  const guidanceRef = useRef(guidance);
  const layersRef = useRef(layers);
  const selectedTgRef = useRef(selectedTg);
  const selectedRegionRef = useRef(selectedRegionId);
  const selectRegionCallbackRef = useRef(setSelectedRegionId);
  const completeToolRef = useRef(onToolComplete);
  const lastSelectionClick = useRef(null);
  const skipClickRef = useRef(false);
  selectedRegionRef.current = selectedRegionId;
  selectRegionCallbackRef.current = setSelectedRegionId;
  completeToolRef.current = onToolComplete;
  // region_id → 화자 이름. 라벨에 붙여 지정 상태를 캔버스에서 바로 본다.
  const speakerLabelsRef = useRef(speakerLabels);
  speakerLabelsRef.current = speakerLabels;
  const dragPreviewRef = useRef(null);
  // stage 핸들러는 mount 시점 클로저라 baseUrl은 ref로 읽어야 현재 페이지 key가 나온다.
  const baseUrlRef = useRef(baseUrl);
  baseUrlRef.current = baseUrl;
  const [dragPreview, setDragPreview] = useState(null);
  const [contextMenu, setContextMenu] = useState(null);
  const menuRef = useRef(null);
  useEffect(() => { setContextMenu(null); }, [image?.id, image?.input_url, guidance, tool]);
  useEffect(() => {
    if (!contextMenu) return;
    const previous = document.activeElement;
    menuRef.current?.querySelector('button:not(:disabled)')?.focus();
    const outside = event => { if (!menuRef.current?.contains(event.target)) setContextMenu(null); };
    const escape = event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setContextMenu(null); } };
    window.addEventListener("pointerdown", outside, true);
    window.addEventListener("keydown", escape, true);
    return () => {
      window.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("keydown", escape, true);
      previous?.isConnected && previous.focus();
    };
  }, [contextMenu]);
  const setStatusRef = useRef(setStatus);
  setStatusRef.current = setStatus;
  const vpRef = useRef(null);
  if (!vpRef.current) {
    vpRef.current = createViewport({
      getHost: () => hostRef.current,
      getImage: () => imageObjRef.current,
      getKey: () => imageKey(),
      fitPadding: 0.98,
      alignLeft: 70, // 왼쪽 도구 모음(12+46px) 바로 옆
      reserveRight: 324, // 선택 정보 패널(300px)이 이미지를 가리지 않게 비워 둔다
      redraw: () => redraw(),
      onStatus: (text) => setStatusRef.current?.(text),
      isPanTool: () => toolRef.current === "hand",
    });
  }

  useEffect(() => {
    toolRef.current = tool;
    imageRef.current = image;
    guidanceRef.current = guidance;
    layersRef.current = layers;
    selectedTgRef.current = selectedTg;
    dragPreviewRef.current = dragPreview;
    vpRef.current.refreshCursor();
  }, [tool, image, guidance, layers, selectedTg, dragPreview]);

  function toStageRect(rect) {
    const t = transformRef.current;
    return {
      x: t.x + rect[0] * t.scale,
      y: t.y + rect[1] * t.scale,
      width: rect[2] * t.scale,
      height: rect[3] * t.scale,
    };
  }

  function toStagePoint(pt) {
    const t = transformRef.current;
    return [t.x + pt[0] * t.scale, t.y + pt[1] * t.scale];
  }

  function stageRectFromPoints(start, end) {
    const x1 = Math.min(start.x, end.x);
    const y1 = Math.min(start.y, end.y);
    const x2 = Math.max(start.x, end.x);
    const y2 = Math.max(start.y, end.y);
    return toStageRect([x1, y1, x2 - x1, y2 - y1]);
  }

  function pointerToImage(stage) {
    const p = stage.getPointerPosition();
    const t = transformRef.current;
    return {
      x: Math.round((p.x - t.x) / t.scale),
      y: Math.round((p.y - t.y) / t.scale),
    };
  }

  function imageKey() {
    return `${imageRef.current?.id || ""}:${baseUrlRef.current || imageRef.current?.final_url || imageRef.current?.input_url || ""}`;
  }

  function findTgAt(point) {
    if (layersRef.current?.tg === false) return null;
    return [...imageGroups(imageRef.current)].reverse().find((group) => {
      if (tgIsExcluded(group, guidanceRef.current)) return false;
      const [x, y, w, h] = rectOf(group);
      return point.x >= x && point.x <= x + w && point.y >= y && point.y <= y + h;
    });
  }

  function imageHitRadius() {
    const scale = transformRef.current?.scale || 1;
    return Math.max(6, 12 / scale);
  }

  function rectContains(rect, point) {
    if (!Array.isArray(rect) || rect.length < 4) return false;
    return point.x >= rect[0] && point.x <= rect[0] + rect[2] && point.y >= rect[1] && point.y <= rect[1] + rect[3];
  }

  function distanceToSegment(point, a, b) {
    if (!Array.isArray(a) || !Array.isArray(b)) return Infinity;
    const ax = Number(a[0]);
    const ay = Number(a[1]);
    const bx = Number(b[0]);
    const by = Number(b[1]);
    if (![ax, ay, bx, by].every(Number.isFinite)) return Infinity;
    const dx = bx - ax;
    const dy = by - ay;
    const lenSq = dx * dx + dy * dy;
    if (!lenSq) return Math.hypot(point.x - ax, point.y - ay);
    const t = Math.max(0, Math.min(1, ((point.x - ax) * dx + (point.y - ay) * dy) / lenSq));
    return Math.hypot(point.x - (ax + t * dx), point.y - (ay + t * dy));
  }

  function guideLabel(hit) {
    if (hit.kind === "manual_seed_points") return "경계 기반 seed";
    if (hit.kind === "manual_regions") return `${regionTypeLabel(hit.item)} 가이드`;
    if (hit.kind === "suppressed_regions") return "Auto region suppress";
    if (hit.kind === "virtual_walls") return "Edge barrier";
    if (hit.kind === "split_guides") return "Split guide";
    if (hit.kind === "tg_splits") return `TG${hit.item?.text_group_id || ""} split`;
    return "Guidance";
  }

  function findGuideAt(point) {
    if (imageRef.current?.processing_excluded || layersRef.current?.guidance === false) return null;
    const current = guidanceRef.current || defaultGuidance(null);
    const radius = imageHitRadius();
    const seeds = [...(current.manual_seed_points || [])].reverse();
    for (const item of seeds) {
      const rawPoint = item.point || item.seed || item;
      if (!Array.isArray(rawPoint) || rawPoint.length < 2) continue;
      if (Math.hypot(point.x - rawPoint[0], point.y - rawPoint[1]) <= radius) return { kind: "manual_seed_points", item };
    }

    const regions = [...(current.manual_regions || [])].reverse();
    for (const item of regions) {
      const rect = item.unboxed_rect || item.bubble_rect || item.rect;
      if (rectContains(rect, point)) return { kind: "manual_regions", item };
    }

    const lineKinds = [
      ["virtual_walls", current.virtual_walls || []],
      ["split_guides", current.split_guides || []],
      ["tg_splits", current.tg_splits || []],
    ];
    for (const [kind, items] of lineKinds) {
      for (const item of [...items].reverse()) {
        const line = item.line || item.polyline;
        if (!Array.isArray(line) || line.length < 2) continue;
        if (distanceToSegment(point, line[0], line[line.length - 1]) <= radius) return { kind, item };
      }
    }
    return null;
  }

  function chooseTg(id) {
    selectedRegionRef.current = null; selectRegionCallbackRef.current(null);
    selectedTgRef.current = id; setSelectedTg(id);
    if (id != null) setStatus(`TG${id} 선택`);
  }

  function chooseRegion(region) {
    selectedTgRef.current = null; setSelectedTg(null);
    selectedRegionRef.current = region ? regionId(region) : null;
    selectRegionCallbackRef.current(selectedRegionRef.current);
    if (region) setStatus(`R${regionId(region)} 선택`);
  }

  function completeTool() {
    toolRef.current = "select";
    completeToolRef.current?.();
  }

  function findRegionAt(point) {
    if (layersRef.current?.regions === false) return null;
    return [...imageRegions(imageRef.current)].reverse().find((region) => {
      if (regionIsExcluded(region, guidanceRef.current)) return false;
      const rect = regionDisplayRect(region);
      return rectContains(rect, point);
    });
  }

  function removeGuide(hit) {
    const current = guidanceRef.current || defaultGuidance(null);
    if (hit.kind === "virtual_walls" && hit.item?.box_id) {
      return { ...current, virtual_walls: (current.virtual_walls || []).filter((item) => item.box_id !== hit.item.box_id) };
    }
    return {
      ...current,
      [hit.kind]: (current[hit.kind] || []).filter((item) => item.id !== hit.item?.id),
    };
  }

  function setGuidanceWithRightClickUndo(next, statusText) {
    guidanceRef.current = next;
    setGuidance(next, statusText);
    setStatus(statusText);
  }

  function suppressRegion(region) {
    const rect = regionDisplayRect(region);
    if (!region || !rect) {
      setStatus("삭제할 region 없음");
      return false;
    }
    const latest = guidanceRef.current || defaultGuidance(null);
    // 수동 Edge 영역은 벽 박스가 만든 것이다. 제외 기록만 남기면 벽이 그대로 남으므로 박스를 지운다.
    if (usesEdgeBarrier(region) && String(region.source_method || region.method || "").startsWith("manual_edge")) {
      const box = edgeBoxForRegion(latest, region);
      if (box) {
        const next = { ...latest, virtual_walls: (latest.virtual_walls || []).filter((item) => item.box_id !== box.boxId) };
        setGuidanceWithRightClickUndo(next, `R${regionId(region)} Edge 삭제`);
        chooseRegion(null);
        return true;
      }
    }
    const targetKey = regionBindingKey(region);
    const sourceRegionId = region.source_order ?? region.source_region_id ?? region.region_id;
    const duplicate = (latest.suppressed_regions || []).some((item) => {
      if (targetKey) return item.target_key === targetKey;
      if (item.target_key) return false;
      const itemSourceId = item.source_region_id ?? item.source_order;
      return itemSourceId != null && sourceRegionId != null && Number(itemSourceId) === Number(sourceRegionId);
    });
    if (duplicate) {
      setStatus(`R${regionId(region)} suppress 이미 있음`);
      return false;
    }
    const nextItem = {
      id: `suppress_${Date.now()}`,
      ...(targetKey ? {target_key: targetKey} : {}),
      type: "region_suppress",
      source_region_id: sourceRegionId,
      display_region_id: region.region_id,
      source_method: region.source_method || region.method || "",
      source_region_type: region.source_region_type || region.type || "",
      anchor_ids: region.anchor_ids || [],
      rect,
    };
    const next = { ...latest, suppressed_regions: [...(latest.suppressed_regions || []), nextItem] };
    setGuidanceWithRightClickUndo(next, `R${regionId(region)} 처리 영역 제외`);
    chooseRegion(null);
    return true;
  }

  function openContextMenu(stage, evt) {
    evt.evt?.preventDefault();
    evt.evt?.stopPropagation?.();
    evt.cancelBubble = true;
    const bounds = hostRef.current.getBoundingClientRect(), t = transformRef.current;
    const point = {x:Math.round((evt.evt.clientX-bounds.left-t.x)/t.scale), y:Math.round((evt.evt.clientY-bounds.top-t.y)/t.scale)};
    const excluded = imageRef.current?.processing_excluded;
    setContextMenu({x:Math.max(8,Math.min(window.innerWidth-258,evt.evt.clientX)),
      y:Math.max(8,Math.min(window.innerHeight-220,evt.evt.clientY)),
      group:excluded ? null : findTgAt(point), region:excluded ? null : findRegionAt(point),
      guide:excluded ? null : findGuideAt(point)});
  }

  function disableTg(id) {
    const latest = guidanceRef.current || defaultGuidance(null);
    const disabled = new Set(latest.disabled_tg_ids || []);
    if (disabled.has(id)) return false;
    disabled.add(id);
    const next = { ...latest, disabled_tg_ids: Array.from(disabled).sort((a, b) => a - b) };
    if (Number(selectedTgRef.current) === id) {
      selectedTgRef.current = null;
      setSelectedTg(null);
    }
    setGuidanceWithRightClickUndo(next, `TG${id} 삭제`);
    return true;
  }

  // Delete의 포인터 우선순위: guide → TG → 자동 region
  function deleteAtPoint(point) {
    const guideHit = findGuideAt(point);
    if (guideHit) {
      setGuidanceWithRightClickUndo(removeGuide(guideHit), `${guideLabel(guideHit)} 삭제`);
      return;
    }
    const group = findTgAt(point);
    if (!group) {
      const region = findRegionAt(point);
      if (!region) {
        setStatus("삭제할 TG/guidance/region 없음");
        return;
      }
      suppressRegion(region);
      return;
    }
    disableTg(tgId(group));
  }

  // D와 Delete는 선택한 TG/R에 적용하며 선택이 없으면 D는 아무것도 지우지 않는다.
  function excludeSelection() {
    if (imageRef.current?.processing_excluded) return false;
    if (selectedRegionRef.current != null) {
      const region = imageRegions(imageRef.current).find(r => regionId(r) === Number(selectedRegionRef.current));
      return region ? suppressRegion(region) : false;
    }
    if (selectedTgRef.current != null) return disableTg(Number(selectedTgRef.current));
    setStatus("제외할 TG 또는 R을 먼저 선택하세요");
    return false;
  }

  // Delete/Backspace: 선택한 TG/R을 제외하고, 없으면 포인터 아래 항목을 지운다.
  function deleteSelection() {
    if (imageRef.current?.processing_excluded) return false;
    if (selectedRegionRef.current != null || selectedTgRef.current != null) return excludeSelection();
    const stage = stageRef.current;
    if (!stage || !pointerInsideRef.current || !stage.getPointerPosition()) {
      setStatus("삭제할 선택 없음 (TG를 선택하거나 지울 항목 위에 포인터를 두세요)");
      return false;
    }
    deleteAtPoint(pointerToImage(stage));
    return true;
  }

  function selectedBounds() {
    if (selectedRegionRef.current != null) {
      const region = imageRegions(imageRef.current).find(r => regionId(r) === Number(selectedRegionRef.current));
      return region ? regionDisplayRect(region) : null;
    }
    const id = Number(selectedTgRef.current);
    if (selectedTgRef.current == null || !Number.isFinite(id)) return null;
    const group = imageGroups(imageRef.current).find((item) => tgId(item) === id);
    return group ? rectOf(group) : null;
  }

  useImperativeHandle(ref, () => ({
    fit: () => vpRef.current.fit(),
    zoom100: () => vpRef.current.zoom100(),
    zoomBy: (factor) => vpRef.current.zoomBy(factor),
    panBy: (dx, dy) => vpRef.current.panBy(dx, dy),
    ensureTgVisible: (id) => {
      const group = imageGroups(imageRef.current).find((item) => tgId(item) === Number(id));
      const rect = group ? rectOf(group) : null;
      return rect ? vpRef.current.ensureVisible(rect) : false;
    },
    zoomToSelection: () => {
      const rect = selectedBounds();
      if (!rect) {
        setStatus("확대할 선택 TG 없음");
        return false;
      }
      return vpRef.current.zoomToRect(rect);
    },
    deleteSelection,
    excludeSelection,
    ensureRegionVisible: region => vpRef.current.ensureVisible(regionDisplayRect(region)),
  }));

  function addLine(kind, start, end, tg) {
    if (Math.hypot(end.x - start.x, end.y - start.y) < 8) return;
    const latest = guidanceRef.current || defaultGuidance(null);
    const id = `${kind}_${Date.now()}`;
    if (kind === "tg_split") {
      const target = tg || selectedTgRef.current;
      if (!target) {
        setStatus("TG split 대상 TG를 먼저 선택하세요.");
        return;
      }
      const next = {
        ...latest,
        tg_splits: [
          ...(latest.tg_splits || []),
          { id, text_group_id: target, line: [[start.x, start.y], [end.x, end.y]], axis: "manual" },
        ],
      };
      guidanceRef.current = next;
      setGuidance(next);
      setStatus(`TG${target} split 추가`);
      return;
    }
    const key = kind === "wall" ? "virtual_walls" : "split_guides";
    const next = {
      ...latest,
      [key]: [...(latest[key] || []), { id, line: [[start.x, start.y], [end.x, end.y]], polyline: [[start.x, start.y], [end.x, end.y]] }],
    };
    guidanceRef.current = next;
    setGuidance(next);
    setStatus(kind === "wall" ? "Edge barrier 추가" : "Split guide 추가");
  }

  function rectsOverlap(a, b) {
    return a[0] < b[0] + b[2] && a[0] + a[2] > b[0] && a[1] < b[1] + b[3] && a[1] + a[3] > b[1];
  }

  function edgeWallRect(items) {
    const points = [];
    (items || []).forEach((item) => {
      const line = item.line || item.polyline;
      if (!Array.isArray(line) || line.length < 2) return;
      [line[0], line[line.length - 1]].forEach((point) => {
        if (Array.isArray(point) && point.length >= 2) points.push([Number(point[0]), Number(point[1])]);
      });
    });
    if (!points.length) return null;
    const xs = points.map((point) => point[0]).filter(Number.isFinite);
    const ys = points.map((point) => point[1]).filter(Number.isFinite);
    if (!xs.length || !ys.length) return null;
    const x1 = Math.min(...xs);
    const y1 = Math.min(...ys);
    const x2 = Math.max(...xs);
    const y2 = Math.max(...ys);
    return [x1, y1, x2 - x1, y2 - y1];
  }

  function removeOverlappingEdgeBoxes(existingWalls, nextRect) {
    const byBox = new Map();
    const loose = [];
    (existingWalls || []).forEach((item) => {
      if (item.box_id) {
        if (!byBox.has(item.box_id)) byBox.set(item.box_id, []);
        byBox.get(item.box_id).push(item);
      } else {
        loose.push(item);
      }
    });
    const keepBoxIds = new Set();
    byBox.forEach((items, boxId) => {
      const rect = edgeWallRect(items);
      if (!rect || !rectsOverlap(rect, nextRect)) keepBoxIds.add(boxId);
    });
    const keepLoose = loose.filter((item) => {
      const rect = edgeWallRect([item]);
      return !rect || !rectsOverlap(rect, nextRect);
    });
    return [
      ...Array.from(byBox.entries()).filter(([boxId]) => keepBoxIds.has(boxId)).flatMap(([, items]) => items),
      ...keepLoose,
    ];
  }

  function addEdgeBarrierBox(start, end) {
    const x1 = Math.min(start.x, end.x);
    const y1 = Math.min(start.y, end.y);
    const x2 = Math.max(start.x, end.x);
    const y2 = Math.max(start.y, end.y);
    if (x2 - x1 < 8 || y2 - y1 < 8) return;
    setEdgeBarrierBox([x1, y1, x2 - x1, y2 - y1]);
  }

  // Edge 박스를 가상 벽 4개로 저장한다. 겹치는 기존 박스는 교체한다(크기 조절도 이 경로).
  function setEdgeBarrierBox(nextRect, replaceMessage = "겹친 Edge 교체", addMessage = "Edge barrier box 추가") {
    const [x1, y1, w, h] = nextRect;
    const x2 = x1 + w;
    const y2 = y1 + h;
    const latest = guidanceRef.current || defaultGuidance(null);
    const id = `edge_box_${Date.now()}`;
    const keptWalls = removeOverlappingEdgeBoxes(latest.virtual_walls || [], nextRect);
    const walls = [
      [[x1, y1], [x2, y1]],
      [[x2, y1], [x2, y2]],
      [[x2, y2], [x1, y2]],
      [[x1, y2], [x1, y1]],
    ].map((line, index) => ({
      id: `${id}_${index + 1}`,
      box_id: id,
      line,
      polyline: line,
      source_method: "manual_edge_barrier_box",
    }));
    const next = {
      ...latest,
      virtual_walls: [...keptWalls, ...walls],
    };
    guidanceRef.current = next;
    setGuidance(next);
    setStatus(keptWalls.length < (latest.virtual_walls || []).length ? replaceMessage : addMessage);
  }

  function addManualRegion(kind, start, end) {
    const x1 = Math.min(start.x, end.x);
    const y1 = Math.min(start.y, end.y);
    const x2 = Math.max(start.x, end.x);
    const y2 = Math.max(start.y, end.y);
    if (x2 - x1 < 8 || y2 - y1 < 8) return;
    const latest = guidanceRef.current || defaultGuidance(null);
    const rect = [x1, y1, x2 - x1, y2 - y1];
    const isTextBased = kind === "ubox";
    const item = isTextBased
      ? { id: `manual_ubox_${Date.now()}`, type: "unboxed", unboxed_rect: rect, source_method: "manual_ubox_guide" }
      : { id: `manual_bubble_${Date.now()}`, type: "bubble", bubble_rect: rect, source_method: "manual_bubble_guide" };
    const next = {
      ...latest,
      manual_regions: [
        ...(latest.manual_regions || []),
        item,
      ],
    };
    guidanceRef.current = next;
    setGuidance(next);
    setStatus(isTextBased ? "텍스트 기반 영역 추가" : "경계 기반 영역 추가");
  }

  function addBoundarySeed(point) {
    const latest = guidanceRef.current || defaultGuidance(null);
    const next = {
      ...latest,
      manual_seed_points: [
        ...(latest.manual_seed_points || []),
        { id: `manual_seed_${Date.now()}`, type: "bubble_seed", point: [point.x, point.y], source_method: "manual_bubble_seed" },
      ],
    };
    guidanceRef.current = next;
    setGuidance(next);
    setStatus("경계 기반 seed 추가");
  }

  function redraw() {
    const host = hostRef.current;
    const stage = stageRef.current;
    const img = imageObjRef.current;
    if (!host || !stage) return;
    const currentImage = imageRef.current;
    const currentGuidance = guidanceRef.current || defaultGuidance(null);
    const currentLayers = layersRef.current || { tg: true, regions: true, guidance: true };
    const currentDragPreview = dragPreviewRef.current;
    const width = host.clientWidth || 800;
    const height = host.clientHeight || 600;
    stage.width(width);
    stage.height(height);
    stage.destroyChildren();

    const base = new Konva.Layer();
    const overlay = new Konva.Layer();
    stage.add(base);
    stage.add(overlay);

    if (!currentImage || !img) {
      overlay.add(new Konva.Text({ text: "이미지를 불러오세요", x: 24, y: 24, fill: "#6b7280", fontSize: 14 }));
      stage.draw();
      return;
    }

    const view = vpRef.current.ensure(width, height, img);
    transformRef.current = { scale: view.scale, x: view.x, y: view.y, w: img.width, h: img.height };
    base.add(new Konva.Image({ image: img, x: view.x, y: view.y, width: img.width * view.scale, height: img.height * view.scale }));

    const visibleRegions = currentLayers.regions && !currentImage.processing_excluded ? (currentImage?.regions?.regions || []).filter(region => !regionIsExcluded(region, currentGuidance)) : [];
    const promoted = promotedTgIds(visibleRegions);
    const labels = [];
    const labelWidth = (text, bold = false) => Math.ceil(new Konva.Text({text, fontSize:11, fontStyle:bold ? "bold" : "normal"}).getTextWidth()) + 10;
    const obstacles = [];
    const imageBounds = {x:view.x, y:view.y, width:img.width * view.scale, height:img.height * view.scale};
    if (currentLayers.tg && !currentImage.processing_excluded) {
      imageGroups(currentImage).forEach((group) => {
        const id = tgId(group);
        if (tgIsExcluded(group, currentGuidance)) return;
        const r = toStageRect(rectOf(group));
        const selected = Number(selectedTgRef.current) === id;
        const rect = new Konva.Rect({
          ...r,
          stroke: selected ? "#111827" : "#2563eb",
          strokeWidth: selected ? 2.5 : 1,
          opacity: selected ? 1 : promoted.has(id) ? .35 : .6,
          fill: "rgba(37,99,235,0.015)",
          listening: true,
        });
        overlay.add(rect);
        obstacles.push(r);
        // Region에 연결된 TG 번호는 중복 표시하지 않고, 선택한 TG만 따로 확인한다.
        if (!promoted.has(id) || selected) labels.push({id:`TG${id}`, text:`TG${id}`, kind:"tg", rect:r,
          color:"#2563eb", selected, width:labelWidth(`TG${id}`), height:18});
      });
    }

    if (currentLayers.regions) {
      visibleRegions.forEach((region) => {
        const rect = region.bubble_rect || region.unboxed_rect || region.erase_rect;
        if (!rect) return;
        const r = toStageRect(rect);
        const isTextBased = regionProcessingBasis(region) === TEXT_BASED;
        const regionSelected = selectedRegionRef.current != null && regionId(region) === Number(selectedRegionRef.current);
        // 선택한 R은 종류 색 대신 어느 종류에도 쓰지 않는 강조색으로 그려 같은 종류 옆에서도 바로 보이게 한다
        const color = regionSelected ? SELECTED_REGION_COLOR : overlayRegionColor(region);
        const hasContour = Array.isArray(region.contour) && region.contour.length > 1;
        const tint = `${color}${regionSelected ? "2b" : "17"}`;
        overlay.add(new Konva.Rect({
          ...r,
          stroke: color,
          strokeWidth: regionSelected ? 3 : 1.2,
          opacity: regionSelected ? 1 : .65,
          fill: hasContour ? "transparent" : tint,
          dash: isTextBased ? [6, 4] : undefined,
        }));
        const anchors = (region.anchor_ids || []).map(id => `TG${id}`);
        const id = `R${regionId(region)}`;
        const speaker = speakerLabelsRef.current?.[regionId(region)];
        const text = speaker ? `${id} ${speaker}` : id;
        const gutterText = anchors.length ? `${text} · ${anchors.slice(0,3).join(",")}${anchors.length > 3 ? "…" : ""}` : text;
        labels.push({id, text, gutterText, selected:regionSelected,
          kind:"region", rect:r, color, inlineWidth:labelWidth(text, true), width:Math.min(170, labelWidth(gutterText, true)), height:18});
        obstacles.push(r);
        if (region.safe_text_rect) {
          overlay.add(new Konva.Rect({ ...toStageRect(region.safe_text_rect), stroke: "#111827", strokeWidth: 1, opacity:.3, dash: [4, 3] }));
        }
        if (hasContour) {
          const points = region.contour.flatMap((pt) => toStagePoint(pt));
          // 분할 결과가 바로 보이도록 굵게 긋고, 어두운 그림 위에서도 보이게 흰 테두리를 깐다
          const width = regionSelected ? 3.5 : 2.5;
          overlay.add(new Konva.Line({ points, closed: true, stroke: "#ffffff", strokeWidth: width + 2.5, opacity: .7, lineJoin: "round", listening: false }));
          overlay.add(new Konva.Line({ points, closed: true, stroke: color, strokeWidth: width, fill: tint, opacity: regionSelected ? 1 : .9, lineJoin: "round" }));
        }
      });
    }

    const splitGuidesToDraw = [];
    if (currentLayers.guidance && !currentImage.processing_excluded) {
      (currentGuidance.tg_splits || []).forEach((item) => splitGuidesToDraw.push({
        line: item.line,
        color: "#7c2d12",
        label: `TG${item.text_group_id}`,
      }));
      const labeledEdgeBoxes = new Set();
      (currentGuidance.virtual_walls || []).forEach((item) => {
        // 박스는 벽 4개라 벽마다 라벨을 달면 사방에 "edge"가 찍힌다. 박스당 한 번만 단다.
        const labeled = Boolean(item.box_id) && labeledEdgeBoxes.has(item.box_id);
        if (item.box_id) labeledEdgeBoxes.add(item.box_id);
        drawGuideLine(overlay, item.line || item.polyline, EDGE_WALL_COLOR, labeled ? "" : "edge");
      });
      (currentGuidance.split_guides || []).forEach((item) => splitGuidesToDraw.push({
        line: item.line,
        color: "#f59e0b",
        label: "split",
      }));
      (currentGuidance.manual_regions || []).forEach((item) => {
        const isTextBased = regionProcessingBasis(item) === TEXT_BASED;
        const rect = isTextBased ? item.unboxed_rect || item.rect : item.bubble_rect || item.rect;
        if (!rect) return;
        overlay.add(new Konva.Rect({
          ...toStageRect(rect),
          stroke: isTextBased ? "#b45309" : "#111827",
          strokeWidth: 2,
          dash: [7, 4],
          fill: isTextBased ? "rgba(180,83,9,0.08)" : "rgba(17,24,39,0.06)",
        }));
      });
      (currentGuidance.manual_seed_points || []).forEach((item) => {
        const point = item.point || item.seed || item;
        if (!Array.isArray(point) || point.length < 2) return;
        const [x, y] = toStagePoint(point);
        overlay.add(new Konva.Circle({ x, y, radius: 5, fill: "#ec4899", stroke: "#111827", strokeWidth: 1.5 }));
        overlay.add(new Konva.Text({ text: "seed", x: x + 7, y: y + 5, fontSize: 12, fill: "#ec4899" }));
      });
    }

    if (currentDragPreview) {
      const isRectRegion = currentDragPreview.tool === "ubox";
      const isTextBased = currentDragPreview.tool === "ubox";
      const isWall = currentDragPreview.tool === "wall";
      const color = isTextBased ? "#b45309" : currentDragPreview.tool === "bubble" ? "#111827" : isWall ? EDGE_WALL_COLOR : "#b45309";
      if (isRectRegion || isWall) {
        overlay.add(new Konva.Rect({
          ...stageRectFromPoints(currentDragPreview.start, currentDragPreview.end),
          stroke: color,
          strokeWidth: 2.5,
          dash: [8, 4],
          fill: isTextBased ? "rgba(180,83,9,0.10)" : isWall ? "rgba(185,28,28,0.08)" : "rgba(17,24,39,0.10)",
        }));
      } else {
        drawGuideLine(overlay, [[currentDragPreview.start.x, currentDragPreview.start.y], [currentDragPreview.end.x, currentDragPreview.end.y]], color, isWall ? "wall" : "split", { prominent: currentDragPreview.tool === "region-split" || currentDragPreview.tool === "tg-split" });
      }
    }

    splitGuidesToDraw.forEach((item) => drawGuideLine(overlay, item.line, item.color, item.label, { prominent: true }));

    placeCanvasLabels(labels, obstacles, imageBounds, {width, height}).forEach(label => {
      if (label.outside) {
        const anchorX = Math.max(0, Math.min(width, imageBounds.x + imageBounds.width + 2));
        const anchorY = Math.max(0, Math.min(height, label.rect.y + label.rect.height / 2));
        overlay.add(new Konva.Line({points:[anchorX,anchorY,label.x-4,label.y+label.height/2],
          stroke:label.color, strokeWidth:.8, opacity:.24, listening:false}));
        if (label.kind === "region") overlay.add(new Konva.Text({text:label.text,
          x:label.rect.x, y:Math.max(2,label.rect.y-16), fontSize:11, fontStyle:"bold",
          fill:label.color, opacity:.25, listening:false}));
      }
      const text = label.outside ? label.gutterText || label.text : label.text;
      const node = new Konva.Label({x:label.x, y:label.y, listening:true, name:"annotation-label",
        opacity:label.outside || label.selected ? .95 : .46});
      node.add(new Konva.Tag({fill:label.outside ? "#f8fafc" : "#ffffff", opacity:label.outside ? .95 : .35, cornerRadius:3}));
      node.add(new Konva.Text({text, fontSize:11, fontStyle:label.kind === "region" ? "bold" : "normal",
        fill:label.color, padding:3, width:label.width, height:label.height, ellipsis:true, wrap:"none"}));
      node.on("click tap", evt => {
        if (toolRef.current !== "select") return;
        evt.cancelBubble = true;
        if (label.kind === "region") {
          const region = imageRegions(imageRef.current).find(r => `R${regionId(r)}` === label.id);
          if (region) chooseRegion(region);
        } else chooseTg(Number(label.id.replace("TG", "")));
      });
      overlay.add(node);
    });
    addTextRegionResizer(overlay, visibleRegions, currentGuidance);
    stage.draw();
  }

  // 선택한 텍스트 기반 영역(R)에 크기 조절 손잡이를 단다. 크기를 바꾸면 같은 자리의 수동 텍스트 영역 가이드로
  // 저장하고(이미 있으면 그 가이드를 고친다), 변경사항 적용 때 수동 영역이 같은 TG의 자동 영역을 대신한다.
  function manualResizeIndex(currentGuidance, region, key) {
    const rect = region.unboxed_rect || region.erase_rect || [];
    return (currentGuidance.manual_regions || []).findIndex((item) => {
      if (regionProcessingBasis(item) !== TEXT_BASED) return false;
      if (item.resize_source_key && item.resize_source_key === key) return true;
      const itemRect = item.unboxed_rect || item.rect || [];
      return region.source_method === "manual_ubox_guide" && itemRect.length >= 4
        && itemRect.slice(0, 4).every((value, index) => Math.abs(value - (rect[index] ?? NaN)) <= 1);
    });
  }

  // 화면에 그려진 Edge 벽선을 눌렀을 때 그 박스가 만든 Edge 영역. 영역이 벽 박스보다 작아도 벽선으로 고를 수 있다.
  function edgeRegionAtWall(point) {
    const current = guidanceRef.current || defaultGuidance(null);
    if (layersRef.current?.guidance === false || layersRef.current?.regions === false) return null;
    const radius = imageHitRadius();
    const walls = (current.virtual_walls || []).filter((item) => item.box_id);
    const hit = walls.find((item) => {
      const line = item.line || item.polyline;
      return Array.isArray(line) && line.length >= 2 && distanceToSegment(point, line[0], line[line.length - 1]) <= radius;
    });
    if (!hit) return null;
    const boxRect = edgeWallRect(walls.filter((item) => item.box_id === hit.box_id));
    if (!boxRect) return null;
    let best = null;
    let bestArea = 0;
    imageRegions(imageRef.current).forEach((region) => {
      if (!usesEdgeBarrier(region) || regionIsExcluded(region, current)) return;
      const rect = region.bubble_rect || region.erase_rect || region.source_rect;
      if (!rect) return;
      const w = Math.min(boxRect[0] + boxRect[2], rect[0] + rect[2]) - Math.max(boxRect[0], rect[0]);
      const h = Math.min(boxRect[1] + boxRect[3], rect[1] + rect[3]) - Math.max(boxRect[1], rect[1]);
      if (w > 0 && h > 0 && w * h > bestArea) { best = region; bestArea = w * h; }
    });
    return best;
  }

  // 영역과 가장 많이 겹치는 수동 Edge 박스(벽 4개)와 그 사각형.
  function edgeBoxForRegion(currentGuidance, region) {
    const regionRect = (region.bubble_rect || region.erase_rect || region.source_rect || []).slice(0, 4);
    if (regionRect.length < 4) return null;
    const byBox = new Map();
    (currentGuidance.virtual_walls || []).forEach((item) => {
      if (!item.box_id) return;
      if (!byBox.has(item.box_id)) byBox.set(item.box_id, []);
      byBox.get(item.box_id).push(item);
    });
    let best = null;
    let bestArea = 0;
    byBox.forEach((items, boxId) => {
      const boxRect = edgeWallRect(items);
      if (!boxRect || !rectsOverlap(boxRect, regionRect)) return;
      const w = Math.min(boxRect[0] + boxRect[2], regionRect[0] + regionRect[2]) - Math.max(boxRect[0], regionRect[0]);
      const h = Math.min(boxRect[1] + boxRect[3], regionRect[1] + regionRect[3]) - Math.max(boxRect[1], regionRect[1]);
      if (w * h > bestArea) { best = { boxId, rect: boxRect }; bestArea = w * h; }
    });
    return best;
  }

  // Edge 영역의 크기 조절 기준 상자. 수동 Edge 박스가 영역과 겹치면 그 박스를, 자동 검출이면 영역 사각형을 쓴다.
  function edgeBoxRectForRegion(currentGuidance, region) {
    const regionRect = (region.bubble_rect || region.erase_rect || region.source_rect || []).slice(0, 4);
    if (regionRect.length < 4) return null;
    return edgeBoxForRegion(currentGuidance, region)?.rect || regionRect;
  }

  function addTextRegionResizer(overlay, visibleRegions, currentGuidance) {
    // 상자 위에서 도구를 바꾸면 상자만 사라지고 mouseleave가 오지 않아 이동 커서가 남으므로 다시 그릴 때마다 지운다
    const container = stageRef.current?.container();
    if (container?.style.cursor === "move") container.style.cursor = "";
    if (toolRef.current !== "select" || selectedRegionRef.current == null || imageRef.current?.processing_excluded) return;
    const region = visibleRegions.find((item) => regionId(item) === Number(selectedRegionRef.current));
    if (!region) return;
    const isEdge = usesEdgeBarrier(region);
    if (regionProcessingBasis(region) !== TEXT_BASED && !isEdge) return;
    const key = regionBindingKey(region);
    let rect;
    if (isEdge) {
      rect = edgeBoxRectForRegion(currentGuidance, region);
    } else {
      const index = manualResizeIndex(currentGuidance, region, key);
      const manual = index >= 0 ? currentGuidance.manual_regions[index] : null;
      rect = manual ? manual.unboxed_rect || manual.rect : region.unboxed_rect || region.erase_rect;
    }
    if (!rect) return;
    const box = new Konva.Rect({ ...toStageRect(rect), stroke: "#b45309", strokeWidth: 1, draggable: true, name: "text-region-resize" });
    const transformer = new Konva.Transformer({
      nodes: [box], rotateEnabled: false, keepRatio: false, ignoreStroke: true, flipEnabled: false,
      anchorSize: 9, anchorStroke: "#b45309", borderStroke: "#b45309", borderDash: [4, 3],
      boundBoxFunc: (oldBox, nextBox) => (nextBox.width < 8 || nextBox.height < 8 ? oldBox : nextBox),
    });
    box.on("mouseenter", () => { stageRef.current.container().style.cursor = "move"; });
    box.on("mouseleave", () => { stageRef.current.container().style.cursor = ""; });
    box.on("dragend transformend", () => commitTextRegionResize(region, key, box));
    overlay.add(box);
    overlay.add(transformer);
  }

  function commitTextRegionResize(region, key, box) {
    const t = transformRef.current;
    const x1 = Math.max(0, Math.round((box.x() - t.x) / t.scale));
    const y1 = Math.max(0, Math.round((box.y() - t.y) / t.scale));
    const x2 = Math.min(t.w, Math.round((box.x() + box.width() * box.scaleX() - t.x) / t.scale));
    const y2 = Math.min(t.h, Math.round((box.y() + box.height() * box.scaleY() - t.y) / t.scale));
    skipClickRef.current = true;
    setTimeout(() => { skipClickRef.current = false; }, 0);
    if (x2 - x1 < 8 || y2 - y1 < 8) { redraw(); return; }
    const rect = [x1, y1, x2 - x1, y2 - y1];
    if (usesEdgeBarrier(region)) {
      setEdgeBarrierBox(rect, `R${regionId(region)} Edge 크기 변경 · 변경사항 적용 시 반영`, `R${regionId(region)} Edge 크기 변경 · 변경사항 적용 시 반영`);
      return;
    }
    const latest = guidanceRef.current || defaultGuidance(null);
    const list = [...(latest.manual_regions || [])];
    const index = manualResizeIndex(latest, region, key);
    if (index >= 0) list[index] = { ...list[index], unboxed_rect: rect };
    else list.push({ id: `manual_ubox_${Date.now()}`, type: "unboxed", unboxed_rect: rect, source_method: "manual_ubox_guide", resize_source_key: key });
    const next = { ...latest, manual_regions: list };
    guidanceRef.current = next;
    setGuidance(next);
    setStatus(`R${regionId(region)} 크기 변경 · 변경사항 적용 시 반영`);
  }

  function drawGuideLine(layer, rawLine, color, label, options = {}) {
    if (!rawLine || rawLine.length < 2) return;
    const [a, b] = rawLine;
    const [x1, y1] = toStagePoint(a);
    const [x2, y2] = toStagePoint(b);
    if (options.prominent) {
      layer.add(new Konva.Line({ points: [x1, y1, x2, y2], stroke: "#ffffff", strokeWidth: 8, opacity: 0.92, lineCap: "round" }));
    }
    layer.add(new Konva.Line({
      points: [x1, y1, x2, y2],
      stroke: color,
      strokeWidth: options.prominent ? 4.5 : 3,
      opacity: options.prominent ? 1 : 0.86,
      lineCap: "round",
    }));
    if (!label) return;
    layer.add(new Konva.Text({
      text: label,
      x: x2 + 6,
      y: y2 + 6,
      fontSize: options.prominent ? 14 : 12,
      fill: color,
      fontStyle: options.prominent ? "bold" : "normal",
    }));
  }

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const stage = new Konva.Stage({ container: host, width: host.clientWidth, height: host.clientHeight });
    stageRef.current = stage;
    const preventContextMenu = (event) => openContextMenu(stage, {evt:event});
    host.addEventListener("contextmenu", preventContextMenu);
    const observer = new ResizeObserver(() => redraw());
    observer.observe(host);

    const vp = vpRef.current;
    vp.attach();
    const onEnter = () => { pointerInsideRef.current = true; };
    const onLeave = () => { pointerInsideRef.current = false; };
    host.addEventListener("mouseenter", onEnter);
    host.addEventListener("mouseleave", onLeave);
    stage.on("contentContextmenu", (evt) => {
      evt.evt.preventDefault();
    });
    // 빈 곳 더블클릭만 화면 맞춤. TG나 guide 위 더블클릭은 그대로 둔다.
    stage.on("dblclick dbltap", () => {
      if (!imageObjRef.current || !stage.getPointerPosition()) return;
      const point = pointerToImage(stage);
      if (findGuideAt(point) || findTgAt(point) || findRegionAt(point)) return;
      vp.fit();
    });
    stage.on("mousedown touchstart", (evt) => {
      const button = evt.evt?.button;
      if (button === 2) {
        evt.evt?.preventDefault();
        return;
      }
      if (button === 1) return;
      const currentTool = toolRef.current;
      if (imageRef.current?.processing_excluded || !["tg-split", "wall", "region-split", "ubox"].includes(currentTool)) return;
      const point = pointerToImage(stage);
      const group = findTgAt(point);
      dragRef.current = { mode: "draw", start: point, tg: group ? tgId(group) : selectedTgRef.current };
      setDragPreview({ tool: currentTool, start: point, end: point });
    });
    stage.on("mousemove touchmove", () => {
      if (!dragRef.current) return;
      const currentTool = toolRef.current;
      if (imageRef.current?.processing_excluded || !["tg-split", "wall", "region-split", "ubox"].includes(currentTool)) return;
      setDragPreview({ tool: currentTool, start: dragRef.current.start, end: pointerToImage(stage) });
    });
    stage.on("mouseup touchend", () => {
      if (!dragRef.current) return;
      const end = pointerToImage(stage);
      const { start, tg } = dragRef.current;
      dragRef.current = null;
      setDragPreview(null);
      const currentTool = toolRef.current;
      const before = guidanceRef.current;
      if (currentTool === "tg-split") addLine("tg_split", start, end, tg);
      if (currentTool === "wall") addEdgeBarrierBox(start, end);
      if (currentTool === "region-split") addLine("split", start, end);
      if (currentTool === "ubox") addManualRegion("ubox", start, end);
      if (guidanceRef.current !== before) { skipClickRef.current = true; setTimeout(() => { skipClickRef.current = false; }, 0); completeTool(); }
    });
    stage.on("click tap", (evt) => {
      if (evt.evt?.button === 2) {
        evt.evt.preventDefault();
        evt.cancelBubble = true;
        return;
      }
      if (skipClickRef.current) { skipClickRef.current = false; return; }
      const currentTool = toolRef.current;
      if (imageRef.current?.processing_excluded) return;
      const point = pointerToImage(stage);
      if (currentTool === "bubble") {
        addBoundarySeed(point);
        completeTool();
        return;
      }
      if (currentTool !== "select") return;
      const wallRegion = edgeRegionAtWall(point);
      if (wallRegion) { chooseRegion(wallRegion); lastSelectionClick.current = null; return; }
      const groups = layersRef.current?.tg === false ? [] : [...imageGroups(imageRef.current)].reverse().filter(g => !tgIsExcluded(g,guidanceRef.current) && rectContains(rectOf(g),point));
      const regions = layersRef.current?.regions === false ? [] : [...imageRegions(imageRef.current)].reverse().filter(r => !regionIsExcluded(r,guidanceRef.current) && rectContains(regionDisplayRect(r),point));
      const hits = [...groups.map(group=>({kind:"tg",id:tgId(group),group})),...regions.map(region=>({kind:"region",id:regionId(region),region}))];
      if (!hits.length) { chooseTg(null); lastSelectionClick.current = null; return; }
      const last = lastSelectionClick.current;
      const repeat = last && Math.hypot(point.x-last.x,point.y-last.y)*(transformRef.current?.scale||1)<5 && Date.now()-last.time<1800;
      const current = hits.findIndex(hit => hit.kind === "tg" ? selectedTgRef.current != null && hit.id===Number(selectedTgRef.current) : selectedRegionRef.current != null && hit.id===Number(selectedRegionRef.current));
      const hit = evt.evt?.altKey && regions.length ? {kind:"region",region:regions[0]} : hits[repeat ? (current+1)%hits.length : 0];
      if (hit.kind === "tg") chooseTg(hit.id); else chooseRegion(hit.region);
      lastSelectionClick.current = {...point,time:Date.now()};
    });

    return () => {
      vp.detach();
      host.removeEventListener("mouseenter", onEnter);
      host.removeEventListener("mouseleave", onLeave);
      host.removeEventListener("contextmenu", preventContextMenu);
      observer.disconnect();
      stage.destroy();
      stageRef.current = null;
    };
  }, []);

  useEffect(() => {
    imageObjRef.current = null;
    const sourceUrl = baseUrl || image?.input_url;
    if (!sourceUrl) {
      redraw();
      return undefined;
    }
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      // 이전 페이지 이미지가 늦게 도착하면 현재 페이지를 덮어쓰지 않는다.
      if (cancelled) return;
      imageObjRef.current = img;
      redraw();
    };
    img.src = sourceUrl;
    return () => {
      cancelled = true;
      img.onload = null;
    };
  }, [image?.id, image?.input_url, baseUrl]);

  useEffect(() => {
    redraw();
  }, [image, guidance, tool, layers, selectedTg, selectedRegionId, dragPreview, speakerLabels]);

  const menuAction = action => { setContextMenu(null); action(); };
  const automaticRegion = contextMenu?.region;
  return <><div id="stageHost" tabIndex="0" className={["tg-split", "wall", "region-split", "ubox", "bubble"].includes(tool) ? "drawing" : ""} ref={hostRef} />
    {contextMenu && createPortal(<div role="menu" aria-label="영역 편집 메뉴" ref={menuRef} className="canvas-context-menu"
      style={{left:contextMenu.x,top:contextMenu.y}} onContextMenu={event => event.preventDefault()} onKeyDown={event => {
        if (event.key === "Tab") { setContextMenu(null); return; }
        if (!["ArrowDown","ArrowUp","Home","End"].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        const items = Array.from(menuRef.current.querySelectorAll('button:not(:disabled)'));
        const index = items.indexOf(document.activeElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length-1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[next]?.focus();
      }}>
      <div className="canvas-context-heading">{contextMenu.region ? `R${regionId(contextMenu.region)}` : "캔버스"}{contextMenu.group ? ` · TG${tgId(contextMenu.group)}` : ""}</div>
      {contextMenu.guide && <button role="menuitem" onClick={() => menuAction(() => setGuidanceWithRightClickUndo(removeGuide(contextMenu.guide), `${guideLabel(contextMenu.guide)} 삭제`))}>이 가이드 삭제</button>}
      {contextMenu.group && <button role="menuitem" onClick={() => menuAction(() => chooseTg(tgId(contextMenu.group)))}>이 TG 선택</button>}
      {contextMenu.region && <button role="menuitem" onClick={() => menuAction(() => chooseRegion(contextMenu.region))}>이 처리 영역 선택</button>}
      {contextMenu.group && <button role="menuitem" onClick={() => menuAction(() => disableTg(tgId(contextMenu.group)))}>이 TG 제외</button>}
      <button role="menuitem" disabled={!automaticRegion} title="이 처리 영역 제외" onClick={() => menuAction(() => suppressRegion(contextMenu.region))}>이 처리 영역 제외</button>
      <button role="menuitem" onClick={() => menuAction(() => vpRef.current.fit())}>화면 맞춤</button>
    </div>, document.body)}
  </>;
});

export default EditorCanvas;
