import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import Konva from "konva";
import { createPortal } from "react-dom";
import { rectOf, tgId, imageGroups, defaultGuidance, regionId, imageRegions, regionDisplayRect } from "./lib.js";
import { createViewport } from "./viewportControls.js";
import { placeCanvasLabels, promotedTgIds } from "./canvasLabels.js";
import { regionIsExcluded, tgIsExcluded } from "./regionExclusion.js";
import { TEXT_BASED, regionProcessingBasis, regionTypeLabel } from "./regionTypes.js";

const EditorCanvas = forwardRef(function EditorCanvas({ image, baseUrl, guidance, setGuidance, tool, layers, selectedTg, setSelectedTg, setStatus, speakerLabels }, ref) {
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
    const sourceRegionId = region.source_order ?? region.source_region_id ?? region.region_id;
    const duplicate = (latest.suppressed_regions || []).some((item) => {
      const itemSourceId = item.source_region_id ?? item.source_order;
      return itemSourceId != null && sourceRegionId != null && Number(itemSourceId) === Number(sourceRegionId);
    });
    if (duplicate) {
      setStatus(`R${regionId(region)} suppress 이미 있음`);
      return false;
    }
    const nextItem = {
      id: `suppress_${Date.now()}`,
      type: "region_suppress",
      source_region_id: sourceRegionId,
      display_region_id: region.region_id,
      source_method: region.source_method || region.method || "",
      source_region_type: region.source_region_type || region.type || "",
      anchor_ids: region.anchor_ids || [],
      rect,
    };
    const next = { ...latest, suppressed_regions: [...(latest.suppressed_regions || []), nextItem] };
    setGuidanceWithRightClickUndo(next, `R${regionId(region)} 자동 region 삭제`);
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

  // Delete/Backspace: 선택한 TG가 있으면 끄고, 없으면 포인터 아래 항목을 지운다.
  function deleteSelection() {
    if (imageRef.current?.processing_excluded) return false;
    const selectedId = Number(selectedTgRef.current);
    if (selectedTgRef.current != null && Number.isFinite(selectedId)) {
      disableTg(selectedId);
      return true;
    }
    const stage = stageRef.current;
    if (!stage || !pointerInsideRef.current || !stage.getPointerPosition()) {
      setStatus("삭제할 선택 없음 (TG를 선택하거나 지울 항목 위에 포인터를 두세요)");
      return false;
    }
    deleteAtPoint(pointerToImage(stage));
    return true;
  }

  function selectedTgRect() {
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
      const rect = selectedTgRect();
      if (!rect) {
        setStatus("확대할 선택 TG 없음");
        return false;
      }
      return vpRef.current.zoomToRect(rect);
    },
    deleteSelection,
  }));

  function toggleTg(id) {
    const latest = guidanceRef.current || defaultGuidance(null);
    const current = new Set(latest.disabled_tg_ids || []);
    if (current.has(id)) current.delete(id);
    else current.add(id);
    const next = { ...latest, disabled_tg_ids: Array.from(current).sort((a, b) => a - b) };
    guidanceRef.current = next;
    setGuidance(next);
    setStatus(current.has(id) ? `TG${id} 제외` : `TG${id} 포함`);
  }

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
    const latest = guidanceRef.current || defaultGuidance(null);
    const id = `edge_box_${Date.now()}`;
    const nextRect = [x1, y1, x2 - x1, y2 - y1];
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
    setStatus(keptWalls.length < (latest.virtual_walls || []).length ? "겹친 Edge 교체" : "Edge barrier box 추가");
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
        rect.on("click tap", (evt) => {
          evt.cancelBubble = true;
          setSelectedTg(id);
          selectedTgRef.current = id;
          setStatus(`TG${id} 선택`);
          if (toolRef.current === "tg-toggle") toggleTg(id);
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
        const color = isTextBased ? "#b45309" : "#0f766e";
        const regionSelected = (region.anchor_ids || []).map(Number).includes(Number(selectedTgRef.current));
        overlay.add(new Konva.Rect({
          ...r,
          stroke: color,
          strokeWidth: regionSelected ? 3 : 1.2,
          opacity: regionSelected ? 1 : .65,
          fill: "transparent",
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
        if (Array.isArray(region.contour) && region.contour.length > 1) {
          const points = region.contour.flatMap((pt) => toStagePoint(pt));
          overlay.add(new Konva.Line({ points, closed: true, stroke: color, strokeWidth: 1.2, opacity: 0.55 }));
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
      (currentGuidance.virtual_walls || []).forEach((item) => drawGuideLine(overlay, item.line || item.polyline, "#b91c1c", "edge"));
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
      const color = isTextBased ? "#b45309" : currentDragPreview.tool === "bubble" ? "#111827" : isWall ? "#b91c1c" : "#b45309";
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
      const node = new Konva.Label({x:label.x, y:label.y, listening:false, name:"annotation-label",
        opacity:label.outside || label.selected ? .95 : .46});
      node.add(new Konva.Tag({fill:label.outside ? "#f8fafc" : "#ffffff", opacity:label.outside ? .95 : .35, cornerRadius:3}));
      node.add(new Konva.Text({text, fontSize:11, fontStyle:label.kind === "region" ? "bold" : "normal",
        fill:label.color, padding:3, width:label.width, height:label.height, ellipsis:true, wrap:"none"}));
      overlay.add(node);
    });
    stage.draw();
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
      if (findGuideAt(point) || findTgAt(point)) return;
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
      if (currentTool === "tg-split") addLine("tg_split", start, end, tg);
      if (currentTool === "wall") addEdgeBarrierBox(start, end);
      if (currentTool === "region-split") addLine("split", start, end);
      if (currentTool === "ubox") addManualRegion("ubox", start, end);
    });
    stage.on("click tap", (evt) => {
      if (evt.evt?.button === 2) {
        evt.evt.preventDefault();
        evt.cancelBubble = true;
        return;
      }
      const currentTool = toolRef.current;
      if (imageRef.current?.processing_excluded) return;
      const point = pointerToImage(stage);
      if (currentTool === "bubble") {
        addBoundarySeed(point);
        return;
      }
      if (currentTool !== "select" && currentTool !== "tg-toggle") return;
      const group = findTgAt(point);
      // TG 밖의 말풍선 안쪽을 눌러도 그 영역의 첫 TG를 선택해 화자 지정 대상으로 삼는다.
      const region = !group && currentTool === "select" ? findRegionAt(point) : null;
      if (!group && !region?.anchor_ids?.length) return;
      const id = group ? tgId(group) : Number(region.anchor_ids[0]);
      setSelectedTg(id);
      selectedTgRef.current = id;
      setStatus(`TG${id} 선택`);
      if (currentTool === "tg-toggle") toggleTg(id);
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
  }, [image, guidance, tool, layers, selectedTg, dragPreview, speakerLabels]);

  const menuAction = action => { setContextMenu(null); action(); };
  const automaticRegion = contextMenu?.region && !String(contextMenu.region.source_method || contextMenu.region.method || "").startsWith("manual_");
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
      {contextMenu.group && <button role="menuitem" onClick={() => menuAction(() => disableTg(tgId(contextMenu.group)))}>이 TG 제외</button>}
      <button role="menuitem" disabled={!automaticRegion} title={contextMenu.region && !automaticRegion ? "직접 지정한 영역은 해당 가이드를 삭제하세요" : "현재 자동 처리 영역 제외"} onClick={() => menuAction(() => suppressRegion(contextMenu.region))}>이 처리 영역 제외</button>
      <button role="menuitem" onClick={() => menuAction(() => vpRef.current.fit())}>화면 맞춤</button>
    </div>, document.body)}
  </>;
});

export default EditorCanvas;
