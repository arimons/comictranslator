import { useEffect, useRef, useState } from "react";
import Konva from "konva";
import { rectOf, tgId, imageGroups, defaultGuidance, regionId, imageRegions, regionDisplayRect } from "./lib.js";

export default function EditorCanvas({ image, baseUrl, guidance, setGuidance, undoGuidance, redoGuidance, tool, layers, selectedTg, setSelectedTg, setStatus }) {
  const hostRef = useRef(null);
  const stageRef = useRef(null);
  const imageObjRef = useRef(null);
  const transformRef = useRef({ scale: 1, x: 0, y: 0, w: 1, h: 1 });
  const viewRef = useRef({ imageKey: "", scale: 1, x: 0, y: 0, hostWidth: 0, hostHeight: 0 });
  const dragRef = useRef(null);
  const toolRef = useRef(tool);
  const imageRef = useRef(image);
  const guidanceRef = useRef(guidance);
  const layersRef = useRef(layers);
  const selectedTgRef = useRef(selectedTg);
  const undoGuidanceRef = useRef(undoGuidance);
  const redoGuidanceRef = useRef(redoGuidance);
  const dragPreviewRef = useRef(null);
  // stage 핸들러는 mount 시점 클로저라 baseUrl은 ref로 읽어야 현재 페이지 key가 나온다.
  const baseUrlRef = useRef(baseUrl);
  baseUrlRef.current = baseUrl;
  const [dragPreview, setDragPreview] = useState(null);

  useEffect(() => {
    toolRef.current = tool;
    imageRef.current = image;
    guidanceRef.current = guidance;
    layersRef.current = layers;
    selectedTgRef.current = selectedTg;
    undoGuidanceRef.current = undoGuidance;
    redoGuidanceRef.current = redoGuidance;
    dragPreviewRef.current = dragPreview;
  }, [tool, image, guidance, layers, selectedTg, undoGuidance, redoGuidance, dragPreview]);

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

  function fitTransform(width, height, img) {
    const scale = Math.min(width / img.width, height / img.height) * 0.98;
    return {
      scale,
      x: (width - img.width * scale) / 2,
      y: (height - img.height * scale) / 2,
    };
  }

  function imageKey() {
    return `${imageRef.current?.id || ""}:${baseUrlRef.current || imageRef.current?.final_url || imageRef.current?.input_url || ""}`;
  }

  function resetView(width, height, img) {
    const fit = fitTransform(width, height, img);
    viewRef.current = {
      imageKey: imageKey(),
      scale: fit.scale,
      x: fit.x,
      y: fit.y,
      hostWidth: width,
      hostHeight: height,
    };
    return viewRef.current;
  }

  function ensureView(width, height, img) {
    const current = viewRef.current;
    const key = imageKey();
    if (current.imageKey !== key || !Number.isFinite(current.scale)) {
      return resetView(width, height, img);
    }
    if (current.hostWidth !== width || current.hostHeight !== height) {
      viewRef.current = clampView({
        ...current,
        imageKey: key,
        hostWidth: width,
        hostHeight: height,
      }, width, height, img);
      return viewRef.current;
    }
    return current;
  }

  function clampView(view, width, height, img) {
    const scaledW = img.width * view.scale;
    const scaledH = img.height * view.scale;
    const margin = 80;
    let x = view.x;
    let y = view.y;
    if (scaledW <= width) {
      x = (width - scaledW) / 2;
    } else {
      x = Math.min(margin, Math.max(width - scaledW - margin, x));
    }
    if (scaledH <= height) {
      y = (height - scaledH) / 2;
    } else {
      y = Math.min(margin, Math.max(height - scaledH - margin, y));
    }
    return { ...view, x, y };
  }

  function zoomAt(stage, deltaY) {
    const img = imageObjRef.current;
    const host = hostRef.current;
    if (!img || !host) return;
    const pointer = stage.getPointerPosition();
    if (!pointer) return;
    const width = host.clientWidth || 800;
    const height = host.clientHeight || 600;
    const old = transformRef.current;
    const fit = fitTransform(width, height, img);
    const zoomFactor = deltaY > 0 ? 1 / 1.16 : 1.16;
    const minScale = fit.scale;
    const maxScale = fit.scale * 10;
    const nextScale = Math.min(maxScale, Math.max(minScale, old.scale * zoomFactor));
    const imagePoint = {
      x: (pointer.x - old.x) / old.scale,
      y: (pointer.y - old.y) / old.scale,
    };
    viewRef.current = clampView({
      imageKey: imageKey(),
      scale: nextScale,
      x: pointer.x - imagePoint.x * nextScale,
      y: pointer.y - imagePoint.y * nextScale,
      hostWidth: width,
      hostHeight: height,
    }, width, height, img);
    setStatus(`Zoom ${Math.round((nextScale / fit.scale) * 100)}%`);
    redraw();
  }

  function panBy(deltaX, deltaY) {
    const img = imageObjRef.current;
    const host = hostRef.current;
    if (!img || !host) return;
    const width = host.clientWidth || 800;
    const height = host.clientHeight || 600;
    const current = viewRef.current;
    viewRef.current = clampView({
      ...current,
      imageKey: imageKey(),
      x: current.x + deltaX,
      y: current.y + deltaY,
      hostWidth: width,
      hostHeight: height,
    }, width, height, img);
    redraw();
  }

  function stagePointer(stage) {
    const pointer = stage.getPointerPosition();
    return pointer ? { x: pointer.x, y: pointer.y } : null;
  }

  function findTgAt(point) {
    if (layersRef.current?.tg === false) return null;
    const disabled = new Set((guidanceRef.current || {}).disabled_tg_ids || []);
    return [...imageGroups(imageRef.current)].reverse().find((group) => {
      if (disabled.has(tgId(group))) return false;
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
    if (hit.kind === "manual_seed_points") return "Bubble seed";
    if (hit.kind === "manual_regions") return hit.item?.type === "unboxed" ? "Ubox guide" : "Bubble guide";
    if (hit.kind === "suppressed_regions") return "Auto region suppress";
    if (hit.kind === "virtual_walls") return "Edge barrier";
    if (hit.kind === "split_guides") return "Split guide";
    if (hit.kind === "tg_splits") return `TG${hit.item?.text_group_id || ""} split`;
    return "Guidance";
  }

  function findGuideAt(point) {
    if (layersRef.current?.guidance === false) return null;
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

    const suppressed = [...(current.suppressed_regions || [])].reverse();
    for (const item of suppressed) {
      const rect = item.rect || item.bubble_rect || item.unboxed_rect || item.source_rect;
      if (rectContains(rect, point)) return { kind: "suppressed_regions", item };
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

  function rightClickDelete(stage, evt) {
    evt.evt?.preventDefault();
    evt.evt?.stopPropagation?.();
    evt.cancelBubble = true;
    const point = pointerToImage(stage);
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
    const id = tgId(group);
    const latest = guidanceRef.current || defaultGuidance(null);
    const disabled = new Set(latest.disabled_tg_ids || []);
    if (disabled.has(id)) return;
    disabled.add(id);
    const next = { ...latest, disabled_tg_ids: Array.from(disabled).sort((a, b) => a - b) };
    if (Number(selectedTgRef.current) === id) {
      selectedTgRef.current = null;
      setSelectedTg(null);
    }
    setGuidanceWithRightClickUndo(next, `TG${id} 삭제`);
  }

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
    const isUbox = kind === "ubox";
    const item = isUbox
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
    setStatus(isUbox ? "Manual Ubox 추가" : "Manual bubble 추가");
  }

  function addBubbleSeed(point) {
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
    setStatus("Bubble seed 추가");
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

    const view = ensureView(width, height, img);
    transformRef.current = { scale: view.scale, x: view.x, y: view.y, w: img.width, h: img.height };
    base.add(new Konva.Image({ image: img, x: view.x, y: view.y, width: img.width * view.scale, height: img.height * view.scale }));

    const disabled = new Set(currentGuidance.disabled_tg_ids || []);
    if (currentLayers.tg) {
      imageGroups(currentImage).forEach((group) => {
        const id = tgId(group);
        if (disabled.has(id)) return;
        const r = toStageRect(rectOf(group));
        const selected = Number(selectedTgRef.current) === id;
        const rect = new Konva.Rect({
          ...r,
          stroke: selected ? "#111827" : "#2563eb",
          strokeWidth: selected ? 3 : 1.6,
          fill: "rgba(37,99,235,0.06)",
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
        overlay.add(new Konva.Text({
          text: `TG${id}`,
          x: r.x,
          y: Math.max(2, r.y - 18),
          fontSize: 12,
          fill: "#111827",
          listening: false,
        }));
      });
    }

    if (currentLayers.regions) {
      (currentImage?.regions?.regions || []).forEach((region) => {
        const rect = region.bubble_rect || region.unboxed_rect || region.erase_rect;
        if (!rect) return;
        const r = toStageRect(rect);
        const isUnboxed = region.type === "unboxed";
        const color = isUnboxed ? "#b45309" : "#0f766e";
        overlay.add(new Konva.Rect({
          ...r,
          stroke: color,
          strokeWidth: 1.5,
          fill: isUnboxed ? "rgba(180,83,9,0.035)" : "rgba(15,118,110,0.035)",
          dash: isUnboxed ? [6, 4] : undefined,
        }));
        overlay.add(new Konva.Label({ x: r.x, y: Math.max(2, r.y - 20), listening: false }).add(
          new Konva.Tag({ fill: color, cornerRadius: 2 }),
          new Konva.Text({
            text: `${isUnboxed ? "U" : "R"}${regionId(region)}`,
            fontSize: 12,
            fontStyle: "bold",
            fill: "#ffffff",
            padding: 3,
          }),
        ));
        if (region.safe_text_rect) {
          overlay.add(new Konva.Rect({ ...toStageRect(region.safe_text_rect), stroke: "#111827", strokeWidth: 1, dash: [4, 3] }));
        }
        if (Array.isArray(region.contour) && region.contour.length > 1) {
          const points = region.contour.flatMap((pt) => toStagePoint(pt));
          overlay.add(new Konva.Line({ points, closed: true, stroke: "#0f766e", strokeWidth: 1.4, opacity: 0.8 }));
        }
      });
    }

    const splitGuidesToDraw = [];
    if (currentLayers.guidance) {
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
        const isUbox = item.type === "unboxed";
        const rect = isUbox ? item.unboxed_rect || item.rect : item.bubble_rect || item.rect;
        if (!rect) return;
        overlay.add(new Konva.Rect({
          ...toStageRect(rect),
          stroke: isUbox ? "#b45309" : "#111827",
          strokeWidth: 2,
          dash: [7, 4],
          fill: isUbox ? "rgba(180,83,9,0.08)" : "rgba(17,24,39,0.06)",
        }));
      });
      (currentGuidance.suppressed_regions || []).forEach((item) => {
        const rect = item.rect || item.bubble_rect || item.unboxed_rect || item.source_rect;
        if (!rect) return;
        const stageRect = toStageRect(rect);
        overlay.add(new Konva.Rect({
          ...stageRect,
          stroke: "#dc2626",
          strokeWidth: 2,
          dash: [5, 4],
          fill: "rgba(220,38,38,0.10)",
        }));
        overlay.add(new Konva.Text({
          text: "off",
          x: stageRect.x + 4,
          y: stageRect.y + 4,
          fontSize: 13,
          fill: "#dc2626",
          fontStyle: "bold",
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
      const isUbox = currentDragPreview.tool === "ubox";
      const isWall = currentDragPreview.tool === "wall";
      const color = isUbox ? "#b45309" : currentDragPreview.tool === "bubble" ? "#111827" : isWall ? "#b91c1c" : "#b45309";
      if (isRectRegion || isWall) {
        overlay.add(new Konva.Rect({
          ...stageRectFromPoints(currentDragPreview.start, currentDragPreview.end),
          stroke: color,
          strokeWidth: 2.5,
          dash: [8, 4],
          fill: isUbox ? "rgba(180,83,9,0.10)" : isWall ? "rgba(185,28,28,0.08)" : "rgba(17,24,39,0.10)",
        }));
      } else {
        drawGuideLine(overlay, [[currentDragPreview.start.x, currentDragPreview.start.y], [currentDragPreview.end.x, currentDragPreview.end.y]], color, isWall ? "wall" : "split", { prominent: currentDragPreview.tool === "region-split" || currentDragPreview.tool === "tg-split" });
      }
    }

    splitGuidesToDraw.forEach((item) => drawGuideLine(overlay, item.line, item.color, item.label, { prominent: true }));

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
    const preventContextMenu = (event) => event.preventDefault();
    host.addEventListener("contextmenu", preventContextMenu);
    stage.content?.addEventListener("contextmenu", preventContextMenu);
    const observer = new ResizeObserver(() => redraw());
    observer.observe(host);

    stage.on("wheel", (evt) => {
      evt.evt.preventDefault();
      zoomAt(stage, evt.evt.deltaY);
    });
    stage.on("contentContextmenu", (evt) => {
      evt.evt.preventDefault();
    });
    stage.on("dblclick dbltap", () => {
      const img = imageObjRef.current;
      if (!img) return;
      resetView(host.clientWidth || 800, host.clientHeight || 600, img);
      setStatus("Zoom fit");
      redraw();
    });
    stage.on("mousedown touchstart", (evt) => {
      const button = evt.evt?.button;
      if (button === 2) {
        rightClickDelete(stage, evt);
        return;
      }
      if (button === 1) {
        evt.evt.preventDefault();
        const pointer = stagePointer(stage);
        if (!pointer) return;
        dragRef.current = { mode: "pan", startPointer: pointer, lastPointer: pointer };
        setStatus("Pan 휠클릭 드래그");
        return;
      }
      const currentTool = toolRef.current;
      if (!["tg-split", "wall", "region-split", "ubox"].includes(currentTool)) return;
      const point = pointerToImage(stage);
      const group = findTgAt(point);
      dragRef.current = { mode: "draw", start: point, tg: group ? tgId(group) : selectedTgRef.current };
      setDragPreview({ tool: currentTool, start: point, end: point });
    });
    stage.on("mousemove touchmove", () => {
      if (!dragRef.current) return;
      if (dragRef.current.mode === "pan") {
        const pointer = stagePointer(stage);
        if (!pointer) return;
        const last = dragRef.current.lastPointer || pointer;
        panBy(pointer.x - last.x, pointer.y - last.y);
        dragRef.current.lastPointer = pointer;
        return;
      }
      const currentTool = toolRef.current;
      if (!["tg-split", "wall", "region-split", "ubox"].includes(currentTool)) return;
      setDragPreview({ tool: currentTool, start: dragRef.current.start, end: pointerToImage(stage) });
    });
    stage.on("mouseup touchend", () => {
      if (!dragRef.current) return;
      if (dragRef.current.mode === "pan") {
        dragRef.current = null;
        return;
      }
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
      const point = pointerToImage(stage);
      if (currentTool === "bubble") {
        addBubbleSeed(point);
        return;
      }
      if (currentTool === "region-off") {
        const region = findRegionAt(point);
        if (!region) {
          setStatus("삭제할 region 없음");
          return;
        }
        suppressRegion(region);
        return;
      }
      if (currentTool !== "select" && currentTool !== "tg-toggle") return;
      const group = findTgAt(point);
      if (!group) return;
      const id = tgId(group);
      setSelectedTg(id);
      selectedTgRef.current = id;
      setStatus(`TG${id} 선택`);
      if (currentTool === "tg-toggle") toggleTg(id);
    });
    const onKeyDown = (event) => {
      const tagName = event.target?.tagName?.toLowerCase();
      if (tagName === "input" || tagName === "textarea" || tagName === "select" || event.target?.isContentEditable) return;
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.key?.toLowerCase() !== "z") return;
      event.preventDefault();
      event.stopPropagation();
      if (event.shiftKey) redoGuidanceRef.current?.();
      else undoGuidanceRef.current?.();
    };
    document.addEventListener("keydown", onKeyDown, true);

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      host.removeEventListener("contextmenu", preventContextMenu);
      stage.content?.removeEventListener("contextmenu", preventContextMenu);
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
  }, [image, guidance, tool, layers, selectedTg, dragPreview]);

  return <div id="stageHost" tabIndex="0" className={["tg-split", "wall", "region-split", "ubox", "bubble"].includes(tool) ? "drawing" : ""} ref={hostRef} />;
}
