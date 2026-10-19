import { useEffect, useMemo, useRef, useState } from "react";
import EditorCanvas from "./EditorCanvas.jsx";
import OverlayCanvas from "./OverlayCanvas.jsx";
import { RestoreControls, RestoreStage, useRestore } from "./RestorePanel.jsx";
import ShortcutHelp from "./ShortcutHelp.jsx";
import { KEYMAP, eventMatches, entryInContext, entryVisibleOnPlatform, shortcutFor, shortcutById } from "./keymap.js";
import {
  TOOL_LABELS,
  TOOL_ICONS,
  OVERLAY_MIN_RECT_SIZE,
  WORKFLOW_TABS,
  FONT_STYLES,
  FONT_STYLE_IDS,
  ETC_DEFAULT_FONT_NAME,
  api,
  apiStream,
  defaultGuidance,
  defaultOverlayGuidance,
  cloneJson,
  regionId,
  overlayItemKey,
  overlayItemLabel,
  guidanceEdgeNotices,
  overlayControlItems,
  canRenderOverlayImage,
  imageNeedsAnalysis,
  imageSummaryOf,
  regionSegmentSiblingCount,
  baseRegionText,
  inferredRegionFontStyle,
  inferredRegionWritingMode,
  overlayWritingModeChoice,
  overlayItemFontSize,
  overlayRegionValue,
  overlayRegionBaseKey,
  readSessionSettings,
  writeSessionSettings,
  removeSessionSettings,
} from "./lib.js";

// 글자 입히기 편집 기록 상한과, 같은 종류의 연속 변경을 한 칸으로 묶는 시간
const OVERLAY_HISTORY_LIMIT = 100;
const OVERLAY_HISTORY_MERGE_MS = 1500;

export default function App() {
  const [sessionId, setSessionId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [images, setImages] = useState([]);
  // 선택한 페이지의 전체 레코드(regions, integrated 등). 목록(images)은 요약만 담는다.
  const [pageDetail, setPageDetail] = useState(null);
  const [recentSessions, setRecentSessions] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [folderPath, setFolderPath] = useState("");
  const [status, setStatus] = useState("");
  const [ocrLog, setOcrLog] = useState("");
  const [exportLog, setExportLog] = useState("");
  const [busyTasks, setBusyTasks] = useState({});
  const [activeTaskLabel, setActiveTaskLabel] = useState("");
  const [activeTab, setActiveTab] = useState("guidance");
  const [workspaceStarted, setWorkspaceStarted] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [tool, setTool] = useState("select");
  const [selectedTg, setSelectedTg] = useState(null);
  const [selectedRegionId, setSelectedRegionId] = useState(null);
  const [guidance, setGuidance] = useState(defaultGuidance(null));
  const [overlayGuidance, setOverlayGuidance] = useState(defaultOverlayGuidance(null));
  const [layers, setLayers] = useState({ tg: true, regions: true, guidance: true });
  const [extraPrompt, setExtraPrompt] = useState("");
  const [ocrModels, setOcrModels] = useState([]);
  const [ocrDefaultModel, setOcrDefaultModel] = useState("");
  const [ocrModel, setOcrModel] = useState("");
  const [fonts, setFonts] = useState([]);
  const [fontPath, setFontPath] = useState("");
  const [analyzeWorkers, setAnalyzeWorkers] = useState(4);
  const [imageStatuses, setImageStatuses] = useState({});
  const [guidanceCompareMode, setGuidanceCompareMode] = useState("masked");
  const [autoOverlayPreview, setAutoOverlayPreview] = useState(true);
  const [exportImageModel, setExportImageModel] = useState("gpt-image-2");
  const [exportTextModel, setExportTextModel] = useState("gpt-5.4-mini");
  const [exportImageQuality, setExportImageQuality] = useState("medium");
  const [exportImageSize, setExportImageSize] = useState("match_input");
  const [exportOutputFormat, setExportOutputFormat] = useState("jpeg");
  const [exportOutputCompression, setExportOutputCompression] = useState(92);
  const [exportPromptGuide, setExportPromptGuide] = useState("");
  const [exportAnalyzeFirst, setExportAnalyzeFirst] = useState(true);
  const [exportGenerateIds, setExportGenerateIds] = useState([]);
  const [exportFocusedId, setExportFocusedId] = useState("");
  const [overlayEditVersion, setOverlayEditVersion] = useState(0);
  const [overlayApplyRequest, setOverlayApplyRequest] = useState(0);
  const [toolsOpen, setToolsOpen] = useState(true);
  const [guidanceUndoStack, setGuidanceUndoStack] = useState([]);
  const [guidanceRedoStack, setGuidanceRedoStack] = useState([]);
  const [guidanceNotice, setGuidanceNotice] = useState(null);
  const [recentMenuId, setRecentMenuId] = useState("");
  // 글자 입히기 캔버스 도구: select | hand | eraser
  const [overlayTool, setOverlayTool] = useState("select");
  const [helpOpen, setHelpOpen] = useState(false);
  const [shortcutToast, setShortcutToast] = useState(null);
  const fileRef = useRef(null);
  const editorCanvasRef = useRef(null);
  const overlayCanvasRef = useRef(null);
  const keyHandlerRef = useRef(null);
  const shortcutToastTimerRef = useRef(null);
  // 글자 입히기 편집 기록(페이지 단위). 확정된 변경마다 직전 overlayGuidance를 쌓는다.
  const overlayHistoryRef = useRef({ undo: [], redo: [], lastKey: "", lastAt: 0 });
  const overlayGuidanceRef = useRef(overlayGuidance);
  overlayGuidanceRef.current = overlayGuidance;
  const sessionIdRef = useRef(sessionId);
  const selectedIdRef = useRef(selectedId);
  const imagesRef = useRef(images);
  const currentGuidanceRef = useRef(guidance);
  const autoOverlayTimerRef = useRef(null);
  const autoOverlayRunningRef = useRef(false);
  const autoOverlayQueuedRef = useRef(false);
  const overlayBootstrapRenderRef = useRef("");
  const overlayEditVersionRef = useRef(0);
  const overlayAppliedVersionRef = useRef(0);
  const handledOverlayApplyRequestRef = useRef(0);
  const selectedRegionIdRef = useRef(null);
  const projectNameDirtyRef = useRef(false);
  const autoAnalysisRequestedRef = useRef(new Set());
  const pageDetailRequestRef = useRef(0);
  const hydrationPendingRef = useRef(false);

  const baseSelected = useMemo(() => images.find((img) => img.id === selectedId) || images[0] || null, [images, selectedId]);
  // 목록 요약 위에 선택 페이지 상세를 얹는다. 요약 필드가 더 최신이므로 요약이 이긴다.
  const selected = useMemo(() => {
    if (!baseSelected) return null;
    if (
      pageDetail
      && pageDetail.id === baseSelected.id
      && (pageDetail.input_path || "") === (baseSelected.input_path || "")
    ) {
      return { ...pageDetail, ...baseSelected, detail_loaded: true };
    }
    return baseSelected;
  }, [baseSelected, pageDetail]);
  const restore = useRestore({ sessionId, image: selected, active: activeTab === "restore", setStatus });
  // id는 파일명 stem이라 세션이 달라도 겹칠 수 있다. 세션별 input_path로 페이지를 구분한다.
  const selectedKey = selected ? (selected.input_path || selected.id) : "";
  // 페이지 전환 직후 요약만 있을 때와 상세가 도착했을 때를 구분해, 상세 도착 시 편집 상태를 다시 초기화한다.
  const selectedDetailKey = selected ? `${selectedKey}|${selected.detail_loaded ? "detail" : "summary"}` : "";
  const effectiveOcrModel = ocrModels.some((model) => model.id === ocrModel) ? ocrModel : ocrDefaultModel;
  const guidanceEditorUrl = selected?.input_url || null;
  const guidanceCompareTabs = [
    ["masked", "Masked", selected?.masked_url],
    ["source", "Original", selected?.input_url],
    ["final", "Final", selected?.final_url],
    ["sheet", "OCR Sheet", selected?.indexed_sheet_url],
  ];
  const guidanceCompareTab = guidanceCompareTabs.find(([id, _label, url]) => id === guidanceCompareMode && url) || guidanceCompareTabs.find(([_id, _label, url]) => url) || guidanceCompareTabs[0];
  const effectiveGuidanceCompareMode = guidanceCompareTab?.[0] || guidanceCompareMode;
  const guidanceCompareUrl = guidanceCompareTab?.[2] || selected?.masked_url || selected?.input_url || null;
  const selectedToolLabel = TOOL_LABELS.find(([id]) => id === tool)?.[1] || tool;
  const disabledCount = guidance.disabled_tg_ids?.length || 0;
  const splitCount = guidance.tg_splits?.length || 0;
  const uboxCount = (guidance.manual_regions || []).filter((item) => item.type === "unboxed").length;
  const suppressedCount = guidance.suppressed_regions?.length || 0;
  const overlayRegions = overlayControlItems(selected, overlayGuidance);
  const selectedRegion = selectedRegionId
    ? (overlayRegions.find((region) => overlayItemKey(region) === String(selectedRegionId)) || null)
    : null;
  const selectedEffectiveFontStyle = selectedRegion
    ? (overlayRegionValue(overlayGuidance, overlayItemKey(selectedRegion)).font_style || inferredRegionFontStyle(selected, selectedRegion))
    : "normal";
  const selectedOverlayOverride = selectedRegion ? overlayRegionValue(overlayGuidance, overlayItemKey(selectedRegion)) : {};
  const selectedBaseFontSize = selectedRegion ? overlayItemFontSize(selectedRegion) : 0;
  const selectedFontSize = Number(selectedOverlayOverride.font_size || selectedBaseFontSize || 0);
  const selectedStrokeWidth = Number.isFinite(Number(selectedOverlayOverride.stroke_width))
    ? Number(selectedOverlayOverride.stroke_width)
    : 2;
  const statusLabel = { idle: "대기", queued: "대기", running: "진행", done: "완료", failed: "실패", skipped: "스킵" };
  const inputBusy = Boolean(busyTasks.input);
  const analyzeBusy = Boolean(busyTasks.analyze);
  const guidanceBusy = Boolean(busyTasks.guidance);
  const ocrBusy = Boolean(busyTasks.ocr);
  const overlayBusy = Boolean(busyTasks.overlay);
  const exportBusy = Boolean(busyTasks.export);
  const anyBusy = Object.values(busyTasks).some(Boolean);
  const currentIndex = selected ? Math.max(0, images.findIndex((img) => img.id === selected.id)) : -1;
  const needsAttentionImages = images.filter((img) => {
    const state = imageStatuses[img.id]?.status;
    return state === "failed" || state === "skipped" || !img.indexed_sheet_url;
  });
  const ocrReadyImages = images.filter((img) => {
    const state = ocrStateForImage(img);
    return state === "idle" && Boolean(img.indexed_sheet_url) && !img.final_url;
  });
  const ocrInProgressImages = images.filter((img) => {
    const state = ocrStateForImage(img);
    return state === "running" || state === "queued";
  });
  const successImages = images.filter((img) => {
    const state = ocrStateForImage(img);
    return state === "done" || state === "skipped";
  });
  const ocrNeedsAttentionImages = images.filter((img) => {
    const state = ocrStateForImage(img);
    return state === "failed";
  });
  const isImageGenerationFallback = (img) => (
    Boolean(img?.has_image_generation_fallback)
    || img?.export_recovery?.status === "generated"
    || img?.export_recovery?.source === "image_generation_fallback"
  );
  const finalImages = images.filter((img) => Boolean(img.final_url));
  const unresolvedFinalImages = images.filter((img) => !img.final_url);
  const missingFinalImages = images.filter((img) => !img.final_url || isImageGenerationFallback(img));
  const selectedExportMissingIds = exportGenerateIds.filter((id) => missingFinalImages.some((img) => img.id === id));
  const exportFocusedImage = images.find((img) => img.id === exportFocusedId) || missingFinalImages[0] || selected;
  const exportFocusedIsGenerated = isImageGenerationFallback(exportFocusedImage);
  const exportPreviewUrl = exportFocusedImage?.final_url || exportFocusedImage?.input_url || "";
  // Region List는 Tab 순서와 같은 읽는 순서(파이프라인이 매긴 region id 순)로 고정한다.
  const orderedOverlayRegions = overlayRegions;
  useEffect(() => {
    // 목록 순서를 고정했으므로, 선택이 바뀌면 그 행이 보이도록만 스크롤한다.
    document.querySelector(".region-quick-item.active")?.scrollIntoView({ block: "nearest" });
  }, [selectedRegionId]);
  const forceDropLabel = ocrBusy ? "OCR 실행 중에는 드롭할 수 없습니다." : "여기로 드롭하면 해당 파일만 강제 재실행합니다.";

  // 비동기 응답이 어느 세션 것인지 판별하려면 세션 id를 ref에도 즉시 반영해야 한다.
  function setSessionTracked(nextSessionId) {
    const sid = nextSessionId || "";
    sessionIdRef.current = sid;
    setSessionId(sid);
    pageDetailRequestRef.current += 1;
    setPageDetail(null);
    const saved = readSessionSettings(sid);
    setExtraPrompt(typeof saved.extraPrompt === "string" ? saved.extraPrompt : "");
    setOcrModel(typeof saved.ocrModel === "string" ? saved.ocrModel : "");
  }

  function updateExtraPrompt(value) {
    setExtraPrompt(value);
    writeSessionSettings(sessionIdRef.current, { extraPrompt: value });
  }

  function updateOcrModel(value) {
    setOcrModel(value);
    writeSessionSettings(sessionIdRef.current, { ocrModel: value });
  }

  // 서버 기본값과 같으면 null을 보내 백엔드 기본 동작(--model 생략)을 유지한다.
  function ocrModelForRequest() {
    return effectiveOcrModel && effectiveOcrModel !== ocrDefaultModel ? effectiveOcrModel : null;
  }

  function selectedRegionRefValue() {
    return selectedRegionId || selectedRegionIdRef.current || null;
  }

  function setSelectedRegionTracked(nextId) {
    selectedRegionIdRef.current = nextId || null;
    setSelectedRegionId(nextId || null);
  }

  function guidanceEquals(left, right) {
    return JSON.stringify(left || {}) === JSON.stringify(right || {});
  }

  function resetGuidanceHistory() {
    setGuidanceUndoStack([]);
    setGuidanceRedoStack([]);
  }

  function setGuidanceTracked(nextGuidance, label = "Guidance edit") {
    const previous = currentGuidanceRef.current || defaultGuidance(null);
    const resolved = typeof nextGuidance === "function" ? nextGuidance(previous) : nextGuidance;
    if (guidanceEquals(previous, resolved)) return;
    setGuidanceUndoStack((items) => [...items.slice(-29), { guidance: cloneJson(previous), label }]);
    setGuidanceRedoStack([]);
    currentGuidanceRef.current = cloneJson(resolved);
    setGuidance(cloneJson(resolved));
  }

  function undoGuidance() {
    const previousItem = guidanceUndoStack[guidanceUndoStack.length - 1];
    if (!previousItem) {
      if (removeLatestGuidanceHistoryItem()) return;
      setStatus("되돌릴 guidance 없음");
      return;
    }
    const current = currentGuidanceRef.current || defaultGuidance(null);
    setGuidanceUndoStack((items) => items.slice(0, -1));
    setGuidanceRedoStack((items) => [...items.slice(-29), { guidance: cloneJson(current), label: previousItem.label }]);
    currentGuidanceRef.current = cloneJson(previousItem.guidance);
    setGuidance(cloneJson(previousItem.guidance));
    setStatus(`Undo: ${previousItem.label}`);
  }

  function redoGuidance() {
    const nextItem = guidanceRedoStack[guidanceRedoStack.length - 1];
    if (!nextItem) {
      setStatus("다시 실행할 guidance 없음");
      return;
    }
    const current = currentGuidanceRef.current || defaultGuidance(null);
    setGuidanceRedoStack((items) => items.slice(0, -1));
    setGuidanceUndoStack((items) => [...items.slice(-29), { guidance: cloneJson(current), label: nextItem.label }]);
    currentGuidanceRef.current = cloneJson(nextItem.guidance);
    setGuidance(cloneJson(nextItem.guidance));
    setStatus(`Redo: ${nextItem.label}`);
  }

  function setGuidanceDirect(nextGuidance, statusText) {
    const resolved = cloneJson(nextGuidance || defaultGuidance(null));
    currentGuidanceRef.current = resolved;
    setGuidance(resolved);
    setStatus(statusText);
  }

  function normalizedFontSizeDelta(value) {
    const parsed = Number(value || 0);
    if (!Number.isFinite(parsed)) return 0;
    return Math.max(-30, Math.min(30, Math.round(parsed)));
  }

  function normalizedFontSize(value) {
    const parsed = Number(value || 0);
    if (!Number.isFinite(parsed)) return 0;
    return Math.max(8, Math.min(160, Math.round(parsed)));
  }

  function normalizedFontWeight(value) {
    const parsed = Number(value || 0);
    if (!Number.isFinite(parsed) || parsed <= 0) return 0;
    return Math.max(100, Math.min(1000, Math.round(parsed / 50) * 50));
  }

  function normalizedStrokeWidth(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    return Math.max(0, Math.min(12, Math.round(parsed)));
  }

  function normalizeOverlayRegionOverride(value) {
    if (!value || typeof value !== "object") return {};
    const next = { ...value };
    if ("safe_text_rect" in next) {
      const rect = Array.isArray(next.safe_text_rect)
        ? next.safe_text_rect.slice(0, 4).map((item) => Math.round(Number(item) || 0))
        : null;
      if (rect && rect.length === 4 && rect[2] >= OVERLAY_MIN_RECT_SIZE && rect[3] >= OVERLAY_MIN_RECT_SIZE) {
        next.safe_text_rect = rect;
      } else {
        delete next.safe_text_rect;
      }
    }
    if ("font_size" in next) {
      const fontSize = normalizedFontSize(next.font_size);
      if (fontSize) next.font_size = fontSize;
      else delete next.font_size;
      delete next.font_size_delta;
    }
    if ("font_size_delta" in next) {
      const delta = normalizedFontSizeDelta(next.font_size_delta);
      if (delta) next.font_size_delta = delta;
      else delete next.font_size_delta;
    }
    if ("rotation_deg" in next) {
      const angle = Number(next.rotation_deg || 0);
      if (Number.isFinite(angle) && Math.abs(angle) > 0.1) {
        next.rotation_deg = Math.max(-45, Math.min(45, Math.round(angle)));
      } else {
        delete next.rotation_deg;
      }
    }
    if ("font_weight" in next) {
      const fontWeight = normalizedFontWeight(next.font_weight);
      if (fontWeight) next.font_weight = fontWeight;
      else delete next.font_weight;
    }
    if ("stroke_width" in next) {
      const strokeWidth = normalizedStrokeWidth(next.stroke_width);
      if (strokeWidth !== null) next.stroke_width = strokeWidth;
      else delete next.stroke_width;
    }
    if ("segment_mode" in next && !["merge", "split"].includes(next.segment_mode)) {
      delete next.segment_mode;
    }
    if ("text_align" in next && !["left", "center", "right"].includes(next.text_align)) {
      delete next.text_align;
    }
    if (next.font_style !== "etc") {
      delete next.font_weight;
    }
    return next;
  }

  function normalizeOverlayGuidance(value) {
    const regions = value?.regions && typeof value.regions === "object" ? value.regions : {};
    const eraserRects = Array.isArray(value?.eraser_rects)
      ? value.eraser_rects
        .filter((rect) => Array.isArray(rect) && rect.length >= 4)
        .map((rect) => rect.slice(0, 4).map((next) => Math.round(Number(next) || 0)))
        .filter((rect) => rect[2] >= 4 && rect[3] >= 4)
      : [];
    return {
      ...(value || {}),
      eraser_rects: eraserRects,
      regions: Object.fromEntries(
        Object.entries(regions).map(([key, region]) => [key, normalizeOverlayRegionOverride(region)])
      ),
    };
  }

  function updateOverlayRegion(rid, patch, historyKey = null) {
    if (!rid) return;
    const key = String(rid);
    setOverlayGuidanceTracked((current) => {
      const previousRegion = normalizeOverlayRegionOverride(((current || {}).regions || {})[key] || {});
      const patchValue = typeof patch === "function" ? patch(previousRegion) : patch;
      return {
        ...(current || { regions: {} }),
        regions: {
          ...((current || {}).regions || {}),
          [key]: normalizeOverlayRegionOverride({
            ...previousRegion,
            ...(patchValue || {}),
          }),
        },
      };
    }, { historyKey });
  }

  function setOverlayRegionSegmentMode(region, mode) {
    if (!region || !["merge", "split"].includes(mode)) return;
    const key = overlayRegionBaseKey(region);
    updateOverlayRegion(key, { segment_mode: mode });
    setStatus(`R${key} segment ${mode}`);
  }

  function overlayImageBounds() {
    const allRects = overlayRegions
      .flatMap((item) => [item.safe_text_rect, item.unboxed_rect, item.bubble_rect, item.source_rect])
      .filter((item) => Array.isArray(item) && item.length === 4);
    const inferredWidth = Math.max(0, ...allRects.map((item) => Number(item[0]) + Number(item[2])));
    const inferredHeight = Math.max(0, ...allRects.map((item) => Number(item[1]) + Number(item[3])));
    return {
      width: Number(selected?.width || selected?.image_width || selected?.regions?.image_width || inferredWidth || 0),
      height: Number(selected?.height || selected?.image_height || selected?.regions?.image_height || inferredHeight || 0),
    };
  }

  function regionBaseSafeRect(region) {
    if (!region) return [0, 0, OVERLAY_MIN_RECT_SIZE, OVERLAY_MIN_RECT_SIZE];
    return region.safe_text_rect || region.unboxed_rect || region.bubble_rect || region.source_rect || [0, 0, OVERLAY_MIN_RECT_SIZE, OVERLAY_MIN_RECT_SIZE];
  }

  function regionSafeRectValue(region, override = overlayRegionValue(overlayGuidance, overlayItemKey(region))) {
    const rect = override.safe_text_rect || regionBaseSafeRect(region);
    return rect.slice(0, 4).map((value) => Math.round(Number(value) || 0));
  }

  function clampOverlayRect(rect) {
    const bounds = overlayImageBounds();
    const width = Math.max(OVERLAY_MIN_RECT_SIZE, Math.round(Number(rect[2]) || OVERLAY_MIN_RECT_SIZE));
    const height = Math.max(OVERLAY_MIN_RECT_SIZE, Math.round(Number(rect[3]) || OVERLAY_MIN_RECT_SIZE));
    if (bounds.width > 0 && bounds.height > 0) {
      const w = Math.min(width, bounds.width);
      const h = Math.min(height, bounds.height);
      return [
        Math.round(Math.min(Math.max(0, Number(rect[0]) || 0), Math.max(0, bounds.width - w))),
        Math.round(Math.min(Math.max(0, Number(rect[1]) || 0), Math.max(0, bounds.height - h))),
        w,
        h,
      ];
    }
    return [
      Math.round(Number(rect[0]) || 0),
      Math.round(Number(rect[1]) || 0),
      width,
      height,
    ];
  }

  function updateOverlaySafeRectField(rid, region, field, value) {
    const index = { x: 0, y: 1, w: 2, h: 3 }[field];
    if (index == null) return;
    const rect = regionSafeRectValue(region);
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    rect[index] = parsed;
    updateOverlayRegion(rid, { safe_text_rect: clampOverlayRect(rect) }, `rect:${rid}:${field}`);
  }

  function setOverlayTextAlign(rid, region, align) {
    if (!rid || !region || !["left", "center", "right"].includes(align)) return;
    updateOverlayRegion(rid, { text_align: align });
    setStatus(`${overlayItemLabel(region)} text ${align}`);
  }

  function bumpOverlayEditVersion() {
    setOverlayEditVersion((version) => {
      const nextVersion = version + 1;
      overlayEditVersionRef.current = nextVersion;
      return nextVersion;
    });
  }

  // options.historyKey: 같은 key의 연속 변경(타이핑, 회전 드래그, 방향키 이동)은 기록 한 칸으로 묶는다.
  function setOverlayGuidanceTracked(nextGuidance, options = {}) {
    setOverlayGuidance((current) => {
      const resolved = normalizeOverlayGuidance(
        typeof nextGuidance === "function" ? nextGuidance(current || { regions: {} }) : nextGuidance
      );
      if (guidanceEquals(current || {}, resolved || {})) return current;
      recordOverlayHistory(current, options?.historyKey);
      bumpOverlayEditVersion();
      return resolved;
    });
  }

  function resetOverlayHistory() {
    overlayHistoryRef.current = { undo: [], redo: [], lastKey: "", lastAt: 0 };
  }

  function recordOverlayHistory(previous, historyKey) {
    const history = overlayHistoryRef.current;
    const now = Date.now();
    const key = historyKey || "";
    const merge = Boolean(key)
      && history.lastKey === key
      && (key.startsWith("rotate:") || now - history.lastAt < OVERLAY_HISTORY_MERGE_MS);
    history.lastKey = key;
    history.lastAt = now;
    history.redo = [];
    if (merge) return;
    const snapshot = cloneJson(previous || {});
    const top = history.undo[history.undo.length - 1];
    // updater가 두 번 호출돼도 같은 스냅샷을 두 번 쌓지 않는다.
    if (top && guidanceEquals(top, snapshot)) return;
    history.undo = [...history.undo.slice(-(OVERLAY_HISTORY_LIMIT - 1)), snapshot];
  }

  function stepOverlayHistory(direction) {
    const history = overlayHistoryRef.current;
    const from = direction < 0 ? history.undo : history.redo;
    if (!from.length) {
      setStatus(direction < 0 ? "되돌릴 overlay 편집 없음" : "다시 실행할 overlay 편집 없음");
      return;
    }
    const target = from[from.length - 1];
    const current = cloneJson(overlayGuidanceRef.current || {});
    if (direction < 0) {
      history.undo = history.undo.slice(0, -1);
      history.redo = [...history.redo, current];
    } else {
      history.redo = history.redo.slice(0, -1);
      history.undo = [...history.undo, current];
    }
    history.lastKey = "";
    const next = normalizeOverlayGuidance(cloneJson(target));
    overlayGuidanceRef.current = next;
    setOverlayGuidance(next);
    // 일반 편집과 같은 경로로 auto preview가 다시 render한다.
    bumpOverlayEditVersion();
    setStatus(`${direction < 0 ? "Undo" : "Redo"} overlay (남은 undo ${history.undo.length} / redo ${history.redo.length})`);
  }

  function showShortcutToast(text) {
    setStatus(text);
    setShortcutToast(text);
    if (shortcutToastTimerRef.current) window.clearTimeout(shortcutToastTimerRef.current);
    shortcutToastTimerRef.current = window.setTimeout(() => setShortcutToast(null), 1600);
  }

  const WRITING_MODE_LABELS = { auto: "자동", horizontal: "가로", vertical: "세로" };

  // O 키: 자동 → 가로 → 세로 → 자동
  function cycleOverlayWritingMode(rid) {
    if (!rid) return;
    const region = overlayRegions.find((item) => overlayItemKey(item) === String(rid));
    const choice = overlayWritingModeChoice(overlayRegionValue(overlayGuidance, rid));
    const next = { auto: "horizontal", horizontal: "vertical", vertical: "auto" }[choice] || "auto";
    if (next === "horizontal") updateOverlayRegion(rid, { force_horizontal: true, force_vertical: false });
    else if (next === "vertical") updateOverlayRegion(rid, { force_vertical: true, force_horizontal: false });
    else updateOverlayRegion(rid, { force_horizontal: false, force_vertical: false });
    showShortcutToast(`${region ? overlayItemLabel(region) : `R${rid}`} 쓰기 방향: ${WRITING_MODE_LABELS[next]}`);
  }

  // Delete: 위치·크기·회전 수동값만 자동으로 되돌린다. 대사와 스타일은 유지한다.
  function resetOverlayRegionLayout(rid) {
    if (!rid) return false;
    const region = overlayRegions.find((item) => overlayItemKey(item) === String(rid));
    const override = overlayRegionValue(overlayGuidance, rid);
    const label = region ? overlayItemLabel(region) : `R${rid}`;
    if (!override.safe_text_rect && !override.rotation_deg) {
      setStatus(`${label} 수동 위치·크기·회전 없음`);
      return true;
    }
    updateOverlayRegion(rid, { safe_text_rect: null, rotation_deg: 0 });
    setStatus(`${label} 위치·크기·회전 자동으로 초기화`);
    return true;
  }

  function cycleOverlayRegion(step) {
    const keys = overlayRegions.map((item) => overlayItemKey(item));
    if (!keys.length) return false;
    const currentKey = selectedRegionRefValue();
    const index = currentKey ? keys.indexOf(String(currentKey)) : -1;
    const nextIndex = index < 0
      ? (step > 0 ? 0 : keys.length - 1)
      : (index + step + keys.length) % keys.length;
    const nextKey = keys[nextIndex];
    setSelectedRegionTracked(nextKey);
    overlayCanvasRef.current?.ensureRegionVisible(nextKey);
    const region = overlayRegions[nextIndex];
    setStatus(`${overlayItemLabel(region)} 선택 (${nextIndex + 1}/${keys.length})`);
    return true;
  }

  function updateFontPath(nextFontPath) {
    setFontPath(nextFontPath);
    if (activeTab === "overlay" && canRenderOverlayImage(selected)) bumpOverlayEditVersion();
  }

  function cycleOverlayFontStyle(rid, currentStyle) {
    if (!rid) return;
    const currentIndex = Math.max(0, FONT_STYLE_IDS.indexOf(currentStyle || "normal"));
    const nextStyle = FONT_STYLE_IDS[(currentIndex + 1) % FONT_STYLE_IDS.length];
    updateOverlayRegion(rid, overlayFontStylePatch(nextStyle));
    setStatus(`R${rid} style ${nextStyle}`);
  }

  function overlayFontStylePatch(style) {
    const patch = { font_style: style };
    if (style === "etc") {
      const defaultEtcFont = fonts.find((font) => font.name === ETC_DEFAULT_FONT_NAME);
      if (defaultEtcFont) patch.font_path = defaultEtcFont.path;
    }
    if (style !== "etc") {
      patch.font_path = "";
      patch.custom_font_path = "";
      patch.font_weight = "";
    }
    return patch;
  }

  function adjustOverlayFontSize(rid, delta) {
    if (!rid) return;
    const region = overlayRegions.find((item) => overlayItemKey(item) === String(rid));
    updateOverlayRegion(rid, (current) => {
      const baseSize = Number(current.font_size || overlayItemFontSize(region) || 24);
      const nextSize = normalizedFontSize(baseSize + delta);
      setStatus(`${region ? overlayItemLabel(region) : `R${rid}`} font size ${nextSize}`);
      return { font_size: nextSize };
    }, `size:${rid}`);
  }

  function setOverlayWritingMode(rid, mode) {
    if (!rid) return;
    if (mode === "horizontal") {
      updateOverlayRegion(rid, { force_horizontal: true, force_vertical: false });
      setStatus(`R${rid} 가로쓰기`);
      return;
    }
    if (mode === "vertical") {
      updateOverlayRegion(rid, { force_vertical: true, force_horizontal: false });
      setStatus(`R${rid} 세로쓰기`);
    }
  }

  function moveSelectedSafeRect(dx, dy) {
    const rid = selectedRegionRefValue();
    if (!rid || !selected) return false;
    const region = overlayRegions.find((item) => overlayItemKey(item) === String(rid));
    if (!region) return false;
    const current = overlayRegionValue(overlayGuidance, rid);
    const rect = current.safe_text_rect || region.safe_text_rect || region.unboxed_rect || region.bubble_rect || region.source_rect;
    if (!rect || rect.length !== 4) return false;
    const allRects = overlayRegions
      .flatMap((item) => [item.safe_text_rect, item.unboxed_rect, item.bubble_rect, item.source_rect])
      .filter((item) => Array.isArray(item) && item.length === 4);
    const inferredWidth = Math.max(0, ...allRects.map((item) => Number(item[0]) + Number(item[2])));
    const inferredHeight = Math.max(0, ...allRects.map((item) => Number(item[1]) + Number(item[3])));
    const imageWidth = Number(selected.width || selected.image_width || selected.regions?.image_width || inferredWidth || 0);
    const imageHeight = Number(selected.height || selected.image_height || selected.regions?.image_height || inferredHeight || 0);
    const moved = [Number(rect[0]) + dx, Number(rect[1]) + dy, Number(rect[2]), Number(rect[3])];
    const bounded = imageWidth > 0 && imageHeight > 0
      ? [
          Math.round(Math.min(Math.max(0, moved[0]), Math.max(0, imageWidth - moved[2]))),
          Math.round(Math.min(Math.max(0, moved[1]), Math.max(0, imageHeight - moved[3]))),
          Math.round(moved[2]),
          Math.round(moved[3]),
        ]
      : moved.map((value) => Math.round(value));
    updateOverlayRegion(rid, { safe_text_rect: bounded }, `nudge:${rid}`);
    setStatus(`${overlayItemLabel(region)} safe rect 이동 ${dx},${dy}`);
    return true;
  }

  async function refresh(sid = sessionIdRef.current) {
    if (!sid) return;
    const data = await api(`/api/session/${sid}`);
    // 요청 사이에 다른 세션을 열었으면 이전 세션 응답으로 이미지 목록을 덮어쓰지 않는다.
    if (sid !== sessionIdRef.current) return;
    if (!projectNameDirtyRef.current) {
      setProjectName(data.session?.project_name || "");
    }
    const nextImages = data.images || [];
    hydrationPendingRef.current = Boolean(data.hydration_pending);
    setImages(nextImages);
    if (nextImages.length) {
      const currentId = selectedIdRef.current || selectedId;
      const stillExists = currentId && nextImages.some((img) => img.id === currentId);
      const nextSelectedId = stillExists ? currentId : nextImages[0].id;
      if (selectedIdRef.current !== nextSelectedId || selectedId !== nextSelectedId) {
        setSelectedId(nextSelectedId);
      }
      selectedIdRef.current = nextSelectedId;
    } else if (selectedIdRef.current || selectedId) {
      selectedIdRef.current = "";
      setSelectedId("");
    }
    return nextImages;
  }

  // import 직후 서버가 백그라운드로 캐시를 연결하는 동안에만 요약 목록을 다시 받는다.
  // 요약 GET은 가볍고, hydration_pending이 false가 되면 멈춘다.
  function scheduleLazyRefresh(sid, maxRounds = 120, intervalMs = 1500) {
    let count = 0;
    const tick = async () => {
      if (sid !== sessionIdRef.current) return;
      count += 1;
      try {
        await refresh(sid);
      } catch (err) {
        setStatus(String(err.message || err));
      }
      if (hydrationPendingRef.current && count < maxRounds && sid === sessionIdRef.current) window.setTimeout(tick, intervalMs);
    };
    if (hydrationPendingRef.current) window.setTimeout(tick, intervalMs);
  }

  // 서버가 돌려준 전체 레코드 하나를 반영한다. 목록에는 요약만, 선택 페이지면 상세에도 넣는다.
  function applyImageRecord(record, { insertIfMissing = false } = {}) {
    if (!record?.id) return;
    const summary = imageSummaryOf(record);
    // 일부 응답 레코드는 manifest 없이 만들어져 hash가 비어 있다. 목록의 기존 hash를 지우지 않는다.
    if (summary.image_sha256 == null) delete summary.image_sha256;
    setImages((items) => {
      const found = items.some((item) => item.id === record.id);
      if (!found) return insertIfMissing ? [...items, summary] : items;
      return items.map((item) => (item.id === record.id ? { ...item, ...summary } : item));
    });
    if (record.detail_loaded !== false && record.id === selectedIdRef.current) {
      pageDetailRequestRef.current += 1;
      setPageDetail(record);
    }
  }

  async function saveProjectName(nextName = projectName) {
    if (!sessionId) return;
    const result = await api("/api/session/meta", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, project_name: nextName }),
    });
    projectNameDirtyRef.current = false;
    setProjectName(result.session?.project_name || "");
    api("/api/sessions/recent").then((data) => setRecentSessions(data.sessions || [])).catch(() => {});
  }

  useEffect(() => {
    // 세션은 첫 import/업로드 때 서버가 만든다. 앱을 열 때마다 빈 세션이 쌓이지 않게 여기서는 만들지 않는다.
    api("/api/sessions/recent").then((data) => {
      setRecentSessions(data.sessions || []);
    }).catch(() => {});
    api("/api/fonts").then((data) => {
      setFonts(data.fonts || []);
      setFontPath(data.default || "");
    }).catch(() => {});
    api("/api/ocr-models").then((data) => {
      setOcrModels(Array.isArray(data.models) ? data.models : []);
      setOcrDefaultModel(data.default || "");
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!sessionId || !baseSelected?.id) return;
    const sameDocument = pageDetail
      && pageDetail.id === baseSelected.id
      && (pageDetail.input_path || "") === (baseSelected.input_path || "");
    if (sameDocument && pageDetail.detail_rev === baseSelected.detail_rev) {
      // A→B→A처럼 되돌아온 경우, 아직 진행 중인 B 요청이 A의 상세를 덮어쓰지 않도록 무효화한다.
      pageDetailRequestRef.current += 1;
      return;
    }
    const requestId = pageDetailRequestRef.current + 1;
    pageDetailRequestRef.current = requestId;
    const requestSid = sessionId;
    const requestImageId = baseSelected.id;
    api(`/api/session/${encodeURIComponent(requestSid)}/page/${encodeURIComponent(requestImageId)}`)
      .then((data) => {
        // 그 사이 다른 페이지/세션으로 이동했거나 더 새 요청이 있으면 버린다.
        if (pageDetailRequestRef.current !== requestId || sessionIdRef.current !== requestSid) return;
        if (data?.image && data.image.id === selectedIdRef.current) setPageDetail(data.image);
      })
      .catch((err) => {
        if (pageDetailRequestRef.current === requestId) setStatus(String(err.message || err));
      });
  }, [sessionId, baseSelected?.id, baseSelected?.input_path, baseSelected?.detail_rev]);

  useEffect(() => {
    const next = defaultGuidance(selected);
    currentGuidanceRef.current = next;
    setGuidance(next);
    resetGuidanceHistory();
    setOverlayGuidance(defaultOverlayGuidance(selected));
    resetOverlayHistory();
    // 페이지가 바뀌면 edit version을 한 단계 올리고 적용 완료로 맞춘다.
    // 이전 페이지 요청의 version은 새 페이지 version과 절대 같아지지 않는다.
    const nextVersion = overlayEditVersionRef.current + 1;
    overlayEditVersionRef.current = nextVersion;
    overlayAppliedVersionRef.current = nextVersion;
    setOverlayEditVersion(nextVersion);
    // 이전 페이지에서 대기 중이던 floating editor 적용 요청은 버린다.
    handledOverlayApplyRequestRef.current = overlayApplyRequest;
    setSelectedTg(null);
    setSelectedRegionTracked(null);
  }, [selectedDetailKey]);

  useEffect(() => {
    currentGuidanceRef.current = guidance;
  }, [guidance]);

  useEffect(() => {
    selectedIdRef.current = selected?.id || selectedId || "";
  }, [selected?.id, selectedId]);

  useEffect(() => {
    selectedRegionIdRef.current = selectedRegionId || null;
  }, [selectedRegionId]);

  useEffect(() => {
    imagesRef.current = images;
  }, [images]);

  useEffect(() => {
    overlayEditVersionRef.current = overlayEditVersion;
  }, [overlayEditVersion]);

  useEffect(() => {
    if (
      !workspaceStarted
      || activeTab !== "guidance"
      || !selected
      || inputBusy
      || analyzeBusy
      || guidanceBusy
      || !imageNeedsAnalysis(selected)
    ) {
      return;
    }
    const requestKey = `${selected.id}:${selected.image_sha256 || ""}`;
    if (autoAnalysisRequestedRef.current.has(requestKey)) return;
    autoAnalysisRequestedRef.current.add(requestKey);
    setStatus(`${selected.name} 분석 산출물이 없어 현재 파일 분석을 시작합니다.`);
    analyze(selected.id);
  }, [workspaceStarted, activeTab, selected?.id, selected?.image_sha256, selected?.regions, selected?.has_analysis, inputBusy, analyzeBusy, guidanceBusy]);

  useEffect(() => {
    if (autoOverlayTimerRef.current) {
      window.clearTimeout(autoOverlayTimerRef.current);
      autoOverlayTimerRef.current = null;
    }
    if (
      !autoOverlayPreview
      || activeTab !== "overlay"
      || !canRenderOverlayImage(selected)
      || !selected?.detail_loaded
      || overlayBusy
      || overlayEditVersion <= overlayAppliedVersionRef.current
    ) {
      return undefined;
    }
    autoOverlayTimerRef.current = window.setTimeout(() => {
      autoOverlayTimerRef.current = null;
      runAutoOverlayPreview();
    }, 300);
    return () => {
      if (autoOverlayTimerRef.current) {
        window.clearTimeout(autoOverlayTimerRef.current);
        autoOverlayTimerRef.current = null;
      }
    };
  }, [autoOverlayPreview, activeTab, selected?.id, selected?.has_integrated_json, selected?.final_url, selected?.detail_loaded, overlayEditVersion, overlayBusy]);

  useEffect(() => {
    if (
      activeTab !== "overlay"
      || !canRenderOverlayImage(selected)
      || !selected?.detail_loaded
      || selected?.final_url
      || overlayBusy
    ) {
      return;
    }
    const key = `${selected.id}:${selected.integrated_json || ""}:${selected.overlay_guidance_json || ""}`;
    if (overlayBootstrapRenderRef.current === key) return;
    overlayBootstrapRenderRef.current = key;
    setStatus(`${selected.name} Overlay preview 생성 중`);
    applyOverlayRequest({
      image: selected,
      guidance: defaultOverlayGuidance(selected),
      font: fontPath,
      silent: true,
      version: overlayEditVersionRef.current,
    });
  }, [activeTab, selectedKey, selected?.has_integrated_json, selected?.final_url, selected?.detail_loaded, overlayBusy, fontPath]);

  // 전역 키보드 디스패처. 조합 판정과 표시는 keymap.js가, 동작은 아래 표가 맡는다.
  function selectPageByStep(step) {
    const items = imagesRef.current || [];
    if (!items.length) return false;
    const currentId = selectedIdRef.current || items[0].id;
    const index = Math.max(0, items.findIndex((item) => item.id === currentId));
    const nextIndex = Math.min(items.length - 1, Math.max(0, index + step));
    if (nextIndex === index) {
      setStatus(step > 0 ? "마지막 페이지입니다" : "첫 페이지입니다");
      return true;
    }
    setSelectedId(items[nextIndex].id);
    selectedIdRef.current = items[nextIndex].id;
    setStatus(`${items[nextIndex].name} 선택`);
    return true;
  }

  function activeCanvasRef() {
    if (activeTab === "guidance") return editorCanvasRef;
    if (activeTab === "overlay") return overlayCanvasRef;
    return null;
  }

  const shortcutConditions = {
    regionSelected: () => Boolean(selectedRegionRefValue()),
    nothingSelected: () => {
      if (activeTab === "overlay") return !selectedRegionRefValue() && !overlayCanvasRef.current?.hasSelectedEraser();
      if (activeTab === "guidance") return selectedTg == null;
      return true;
    },
    inFloatingEditor: (event) => Boolean(event.target?.closest?.(".floating-text-editor")),
    notInFloatingEditor: (event) => !event.target?.closest?.(".floating-text-editor"),
  };

  const shortcutActions = {
    "view.fit": () => activeCanvasRef()?.current?.fit(),
    "view.zoom100": () => activeCanvasRef()?.current?.zoom100(),
    "view.zoomSelection": () => activeCanvasRef()?.current?.zoomToSelection(),
    "view.zoomIn": () => activeCanvasRef()?.current?.zoomBy(1.25),
    "view.zoomOut": () => activeCanvasRef()?.current?.zoomBy(1 / 1.25),
    tab: (_event, tab) => {
      setActiveTab(tab);
      setStatus(`Tab ${WORKFLOW_TABS.find(([id]) => id === tab)?.[1] || tab}`);
    },
    "page.prev": () => selectPageByStep(-1),
    "page.next": () => selectPageByStep(1),
    tool: (_event, nextTool) => {
      if (activeTab === "overlay") {
        const resolved = nextTool === "eraser" && overlayTool === "eraser" ? "select" : nextTool;
        setOverlayTool(resolved);
        setStatus(`Tool ${resolved === "eraser" ? "Eraser" : resolved === "hand" ? "Hand" : "Select"}`);
        return;
      }
      setTool(nextTool);
      setStatus(`Tool ${TOOL_LABELS.find(([id]) => id === nextTool)?.[1] || nextTool}`);
    },
    escape: () => {
      if (activeTab === "overlay") {
        if (overlayCanvasRef.current?.clearEraserSelection()) return true;
        if (selectedRegionRefValue()) {
          setSelectedRegionTracked(null);
          setStatus("선택 해제");
          return true;
        }
        if (overlayTool !== "select") {
          setOverlayTool("select");
          setStatus("Tool Select");
          return true;
        }
        return false;
      }
      if (selectedTg != null) {
        setSelectedTg(null);
        setStatus("선택 해제");
        return true;
      }
      if (tool !== "select") {
        setTool("select");
        setStatus("Tool Select");
        return true;
      }
      return false;
    },
    undo: () => (activeTab === "overlay" ? stepOverlayHistory(-1) : undoGuidance()),
    redo: () => (activeTab === "overlay" ? stepOverlayHistory(1) : redoGuidance()),
    delete: () => {
      if (activeTab === "guidance") return editorCanvasRef.current?.deleteSelection() ?? false;
      if (overlayCanvasRef.current?.hasSelectedEraser()) return overlayCanvasRef.current.deleteSelectedEraser();
      const rid = selectedRegionRefValue();
      return rid ? resetOverlayRegionLayout(rid) : false;
    },
    "overlay.nudge": (event, step) => {
      const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.code];
      if (!dir) return false;
      moveSelectedSafeRect(dir[0] * step, dir[1] * step);
      return true;
    },
    "overlay.cycleRegion": (_event, step) => cycleOverlayRegion(step),
    "overlay.openEditor": () => overlayCanvasRef.current?.openEditorForSelected() ?? false,
    "overlay.style": (_event, style) => {
      const rid = selectedRegionRefValue();
      updateOverlayRegion(rid, overlayFontStylePatch(style));
      setStatus(`R${rid} style ${style}`);
    },
    "overlay.fontSize": (event, sign) => {
      // 일반 키보드의 + 는 Shift+= 라 1 step, 숫자패드 +/- 는 3 step
      adjustOverlayFontSize(selectedRegionRefValue(), sign * (event.shiftKey ? 1 : 3));
    },
    "overlay.orientation": () => cycleOverlayWritingMode(selectedRegionRefValue()),
    apply: (event) => {
      if (typeof event.target?.blur === "function" && event.target !== document.body) event.target.blur();
      if (activeTab === "guidance") {
        if (!guidanceBusy && !analyzeBusy && selected) applyGuidance();
        return true;
      }
      if (canRenderOverlayImage(selected) && !overlayBusy) applyOverlay();
      return true;
    },
    "editor.commit": () => overlayCanvasRef.current?.commitEditor() ?? false,
    "editor.cancel": () => overlayCanvasRef.current?.closeEditor() ?? false,
    "help.toggle": () => setHelpOpen((open) => !open),
  };

  function handleShortcutKeyDown(event) {
    // IME 조합 중(한글 입력 등)에는 어떤 단축키도 쓰지 않는다.
    if (event.isComposing || event.keyCode === 229) return;
    const target = event.target;
    const tag = target?.tagName?.toLowerCase();
    const isTextTarget = tag === "input" || tag === "textarea" || tag === "select" || Boolean(target?.isContentEditable);
    // 버튼·링크 등에 포커스가 있으면 일반 Enter는 그 컨트롤의 기본 동작에 맡긴다.
    const isInteractiveTarget = Boolean(target?.closest?.(
      "button, a[href], input, select, textarea, summary, [role='button'], [role='link'], [role='checkbox'], [role='radio'], [role='tab'], [role='menuitem'], [contenteditable='true']",
    ));
    if (helpOpen && event.code === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setHelpOpen(false);
      return;
    }
    // 배경 복원 탭은 RestorePanel이 자기 키를 처리한다. 여기서는 전역(탭·페이지·도움말)만 본다.
    const contexts = [];
    if (activeTab === "overlay" && overlayCanvasRef.current?.isEditorOpen()) contexts.push("floatingEditor");
    if (activeTab === "guidance" || activeTab === "overlay") contexts.push(activeTab);
    contexts.push("global");
    for (const context of contexts) {
      for (const entry of KEYMAP) {
        if (entry.passive || entry.kind !== "key" || !entry.action) continue;
        if (!entryInContext(entry, context) || !entryVisibleOnPlatform(entry)) continue;
        if (!eventMatches(entry, event)) continue;
        if (isTextTarget && !entry.allowInInput) continue;
        if (!entry.allowInInput && event.code === "Enter" && !event.metaKey && !event.ctrlKey && isInteractiveTarget) continue;
        if ((entry.when || []).some((name) => !shortcutConditions[name]?.(event))) continue;
        const handler = shortcutActions[entry.action];
        if (!handler) continue;
        const result = handler(event, entry.arg);
        // false를 돌려주면 처리하지 않은 것으로 보고 브라우저 기본 동작을 남긴다.
        if (result === false) return;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
    }
  }
  keyHandlerRef.current = handleShortcutKeyDown;

  useEffect(() => {
    const onKeyDown = (event) => keyHandlerRef.current?.(event);
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);

  useEffect(() => () => {
    if (shortcutToastTimerRef.current) window.clearTimeout(shortcutToastTimerRef.current);
  }, []);

  // floating text editor의 Cmd/Ctrl+Enter: 초안 반영과 같은 render에서 갱신된 overlayGuidance로 적용한다.
  // 다른 overlay 렌더가 진행 중이면 끝난 뒤 한 번 적용한다.
  useEffect(() => {
    if (overlayApplyRequest === handledOverlayApplyRequestRef.current) return;
    if (overlayBusy) return;
    handledOverlayApplyRequestRef.current = overlayApplyRequest;
    if (activeTab === "overlay" && canRenderOverlayImage(selected)) applyOverlay();
  }, [overlayApplyRequest, overlayBusy]);

  async function runTask(kind, label, fn) {
    setBusyTasks((current) => ({ ...current, [kind]: true }));
    setActiveTaskLabel(label);
    setStatus(`${label}...`);
    try {
      const result = await fn();
      setStatus(result?.logs || `${label} 완료`);
      return result;
    } catch (err) {
      setStatus(String(err.message || err));
    } finally {
      setBusyTasks((current) => ({ ...current, [kind]: false }));
      setActiveTaskLabel("");
    }
  }

  // 세션 id가 아직 없을 때 서버가 새 세션을 만들었다면 그 세션을 현재 세션으로 채택한다.
  function adoptCreatedSession(requestSid, resultSid) {
    if (!requestSid && resultSid && resultSid !== sessionIdRef.current) {
      setSessionTracked(resultSid);
      const pendingName = projectName.trim();
      if (projectNameDirtyRef.current && pendingName) {
        api("/api/session/meta", { method: "POST", body: JSON.stringify({ session_id: resultSid, project_name: pendingName }) })
          .then(() => { projectNameDirtyRef.current = false; })
          .catch(() => {});
      }
    }
  }

  async function importPath() {
    const requestSid = sessionId;
    await runTask("input", "폴더 import", async () => {
      const result = await api("/api/import-path", { method: "POST", body: JSON.stringify({ session_id: requestSid, path: folderPath, recursive: true }) });
      adoptCreatedSession(requestSid, result.session_id);
      const imgs = await refresh(result.session_id);
      if (result.imported > 0) scheduleLazyRefresh(result.session_id);
      setImageStatuses({});
      if (imgs?.length) {
        setSelectedId(imgs[0].id);
        selectedIdRef.current = imgs[0].id;
      }
      setActiveTab("guidance");
      return { logs: `${result.imported}개 이미지 import 완료. 캐시 확인은 백그라운드에서 진행 중` };
    });
  }

  async function uploadFromInput(input) {
    await uploadFiles(Array.from(input.files || []), input);
  }

  // 파일 input과 Start page 드롭 영역이 같은 업로드 경로를 쓴다.
  async function uploadFiles(files, input = null) {
    if (!files.length) return;
    const requestSid = sessionId;
    await runTask("input", "업로드", async () => {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      const result = await api(`/api/upload?session_id=${requestSid}`, { method: "POST", body: form });
      adoptCreatedSession(requestSid, result.session_id);
      await refresh(result.session_id);
      if (result.imported > 0) scheduleLazyRefresh(result.session_id);
      setImageStatuses({});
      setActiveTab("guidance");
      if (input) input.value = "";
      return { logs: `${result.imported}개 이미지 업로드 완료. 캐시 확인은 백그라운드에서 진행 중` };
    });
  }

  async function openRecentSession(recent) {
    if (!recent?.session_id) return;
    setSessionTracked(recent.session_id);
    selectedIdRef.current = "";
    const imgs = await refresh(recent.session_id);
    if (sessionIdRef.current !== recent.session_id) return;
    scheduleLazyRefresh(recent.session_id);
    if (imgs?.length) {
      setSelectedId(imgs[0].id);
      selectedIdRef.current = imgs[0].id;
    }
    setWorkspaceStarted(true);
    setActiveTab("guidance");
    setStatus(`${recent.session_id} 세션 열기`);
  }

  async function renameRecentSession(recent) {
    if (!recent?.session_id) return;
    const currentName = recent.project_name || "";
    const nextName = window.prompt("프로젝트 이름을 입력하세요.", currentName);
    if (nextName === null) return;
    const result = await api("/api/session/meta", {
      method: "POST",
      body: JSON.stringify({ session_id: recent.session_id, project_name: nextName }),
    });
    setRecentMenuId("");
    if (recent.session_id === sessionId) setProjectName(result.session?.project_name || "");
    api("/api/sessions/recent").then((data) => setRecentSessions(data.sessions || [])).catch(() => {});
    setStatus("프로젝트 이름을 변경했습니다.");
  }

  async function deleteRecentSession(recent) {
    if (!recent?.session_id) return;
    const label = recent.project_name || recent.session_id;
    if (!window.confirm(`${label} 프로젝트를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`)) return;
    const result = await api("/api/session/delete", {
      method: "POST",
      body: JSON.stringify({ session_id: recent.session_id }),
    });
    setRecentMenuId("");
    setRecentSessions(result.sessions || []);
    removeSessionSettings(recent.session_id);
    if (recent.session_id === sessionId) {
      setSessionTracked("");
      setProjectName("");
      setImages([]);
      setSelectedId("");
      selectedIdRef.current = "";
      setWorkspaceStarted(false);
    }
    setStatus("프로젝트를 삭제했습니다.");
  }

  function returnToStartPage() {
    if (!window.confirm("Start page로 돌아가시겠습니까? 현재 작업 세션은 저장된 상태로 유지됩니다.")) return;
    setWorkspaceStarted(false);
    setActiveTab("guidance");
    setStatus("Start page로 돌아왔습니다.");
  }

  async function startSpeechBubbleAnalysis() {
    if (!images.length || analyzeBusy || inputBusy) return;
    setWorkspaceStarted(true);
    setActiveTab("guidance");
    await analyze("all");
  }

  async function analyze(target = "all") {
    const requestSid = sessionId;
    await runTask("analyze", "OCR 직전 분석", async () => {
      const logLines = [];
      let completedImages = null;
      const initialStatuses = {};
      images.forEach((img) => {
        initialStatuses[img.id] = { phase: "analysis", status: target === "all" || target === img.id ? "queued" : "idle", detail: "" };
      });
      setImageStatuses(initialStatuses);
      const appendLog = (message) => {
        logLines.push(message);
        setStatus(logLines.join("\n"));
      };
      await apiStream(
        "/api/analyze-stream",
        { method: "POST", body: JSON.stringify({ session_id: requestSid, image_id: target, force: target !== "all", max_workers: analyzeWorkers }) },
        (event) => {
          // 다른 세션으로 바뀐 뒤의 이벤트는 현재 세션 상태에 반영하지 않는다.
          if (sessionIdRef.current !== requestSid) return;
          if (event.type === "log") appendLog(event.message);
          if (event.type === "image_status") {
            setImageStatuses((current) => ({
              ...current,
              [event.image_id]: { phase: "analysis", status: event.status, detail: event.detail || "" },
            }));
            if (event.image) {
              applyImageRecord(event.image, { insertIfMissing: true });
              if (event.status === "done" && !selectedIdRef.current) setSelectedId(event.image_id);
            }
          }
          if (event.type === "error") {
            appendLog(event.message);
            // 이미지 단위 실패는 해당 이미지만 실패로 표시하고 나머지 진행 상황은 계속 추적한다.
            if (event.image_id) {
              setImageStatuses((current) => ({
                ...current,
                [event.image_id]: { phase: "analysis", status: "failed", detail: "실패" },
              }));
              return;
            }
            throw new Error(event.message);
          }
          if (event.type === "complete") {
            completedImages = event.images || [];
            if (event.ok === false) appendLog("일부 이미지 분석 실패");
            appendLog(`${completedImages.length}개 분석 완료`);
          }
        },
      );
      if (sessionIdRef.current !== requestSid) return { logs: logLines.join("\n") };
      if (completedImages) {
        setImages(completedImages);
        const currentId = selectedIdRef.current || selected?.id;
        const stillExists = currentId && completedImages.some((img) => img.id === currentId);
        const nextSelected = stillExists ? currentId : (completedImages[0]?.id || "");
        setSelectedId(nextSelected);
        selectedIdRef.current = nextSelected;
      } else {
        await refresh(requestSid);
      }
      return { logs: logLines.join("\n") };
    });
  }

  // 목록 요약만 있고 페이지 상세가 아직 없으면 guidance가 비어 있어, 저장하면 기존 편집을 지운다.
  function pageDetailReady() {
    if (selected?.detail_loaded) return true;
    setStatus("페이지 정보를 불러오는 중입니다. 잠시 후 다시 시도하세요.");
    return false;
  }

  async function applyGuidance() {
    if (!selected || !pageDetailReady()) return;
    const requestSid = sessionId;
    const requestImageId = selected.id;
    await runTask("guidance", "말풍선 분석 적용", async () => {
      const result = await api("/api/guidance", { method: "POST", body: JSON.stringify({ session_id: requestSid, image_id: requestImageId, guidance }) });
      const refreshedImages = await refresh(requestSid);
      if (!refreshedImages || sessionIdRef.current !== requestSid) return { logs: "Step 3 재생성 완료 (다른 세션으로 이동해 화면 갱신 생략)" };
      // 목록은 요약만 담으므로 guidance 등 상세는 응답의 전체 레코드를 쓴다.
      const refreshedImage = result.image;
      applyImageRecord(refreshedImage);
      // 작업 중 다른 페이지로 이동했다면 선택을 되돌리지 않고, 보고 있는 페이지의 guidance도 건드리지 않는다.
      if (selectedIdRef.current === requestImageId) {
        const next = defaultGuidance(refreshedImage);
        currentGuidanceRef.current = next;
        setGuidance(next);
        resetGuidanceHistory();
      }
      const notices = guidanceEdgeNotices(result.image?.guidance_report || refreshedImage.guidance_report);
      if (notices.length) setGuidanceNotice({ title: "Edge Guidance", notices });
      return { logs: `Step 3 재생성 완료\nAnalysis: ${refreshedImage.bubble_vis_jpg}\nOCR Sheet: ${refreshedImage.indexed_sheet_jpg}\nMasked: ${refreshedImage.masked_jpg}` };
    });
  }

  async function clearGuidanceOutputs() {
    if (!selected) return;
    const requestSid = sessionId;
    const requestImageId = selected.id;
    await runTask("guidance", "말풍선 분석 산출물 삭제", async () => {
      const result = await api("/api/guidance/clear", { method: "POST", body: JSON.stringify({ session_id: requestSid, image_id: requestImageId }) });
      if (sessionIdRef.current !== requestSid) return { logs: "말풍선 분석 산출물 삭제 완료" };
      applyImageRecord(result.image);
      if (selectedIdRef.current === requestImageId) {
        const next = defaultGuidance(result.image);
        currentGuidanceRef.current = next;
        setGuidance(next);
        resetGuidanceHistory();
        setSelectedTg(null);
      }
      return { logs: "말풍선 분석 산출물 삭제 완료" };
    });
  }

  async function runFinal(force = false) {
    if (!selected || !pageDetailReady()) return;
    const requestSid = sessionId;
    const requestImageId = selected.id;
    const requestVersion = overlayEditVersionRef.current;
    await runTask("ocr", "OCR/Overlay", async () => {
      const result = await api("/api/run-final", {
        method: "POST",
        body: JSON.stringify({
          session_id: requestSid,
          image_id: requestImageId,
          extra_prompt: extraPrompt,
          font_path: fontPath,
          overlay_guidance: overlayGuidance,
          // 편집기는 전체 상태를 보내므로 병합하지 않고 교체한다(지운 값이 되살아나지 않게).
          replace: true,
          force,
          ocr_model: ocrModelForRequest(),
        }),
      });
      if (sessionIdRef.current !== requestSid) return { logs: `최종 렌더 완료\nFinal: ${result.image.final_jpg}` };
      applyImageRecord(result.image);
      // 같은 페이지에 머물러 있고 요청 이후 overlay 편집이 없을 때만 서버 값으로 교체한다.
      if (selectedIdRef.current === requestImageId && overlayEditVersionRef.current === requestVersion) {
        setOverlayGuidance(defaultOverlayGuidance(result.image));
      }
      return { logs: `최종 렌더 완료\nFinal: ${result.image.final_jpg}` };
    });
  }

  async function runFinalStream(target = "all", force = false) {
    const targetImage = target === "all" ? null : images.find((img) => img.id === target) || selected;
    if (target !== "all" && !targetImage) return;
    const requestSid = sessionId;
    const startSelectedId = selectedIdRef.current;
    await runTask("ocr", target === "all" ? "전체 OCR/번역" : "현재 이미지 OCR/번역", async () => {
      const logLines = [];
      setOcrLog("");
      const initialStatuses = {};
      images.forEach((img) => {
        const existingState = ocrStateForImage(img);
        const alreadyDone = existingState === "done" || existingState === "skipped";
        const shouldQueue = (target === "all" && (!alreadyDone || force)) || (target === "failed" && existingState === "failed") || target === img.id;
        initialStatuses[img.id] = { phase: "ocr", status: shouldQueue ? "queued" : existingState, detail: ocrDetailForImage(img) };
      });
      setImageStatuses(initialStatuses);
      const appendLog = (message) => {
        logLines.push(message);
        setOcrLog(logLines.join("\n"));
        setStatus(logLines.join("\n"));
      };
      await apiStream(
        "/api/run-final-stream",
        {
          method: "POST",
          body: JSON.stringify({
            session_id: requestSid,
            image_id: target,
            extra_prompt: extraPrompt,
            font_path: fontPath,
            overlay_guidance: target === selected?.id && selected?.detail_loaded ? overlayGuidance : null,
            replace: target === selected?.id && Boolean(selected?.detail_loaded),
            max_workers: analyzeWorkers,
            force,
            ocr_model: ocrModelForRequest(),
          }),
        },
        (event) => {
          if (sessionIdRef.current !== requestSid) return;
          if (event.type === "log") {
            appendLog(event.message);
            if (event.image_id) {
              const detail = String(event.message || "").replace(/^\[[^\]]+\]\s*/, "").slice(0, 120);
              setImageStatuses((current) => ({
                ...current,
                [event.image_id]: {
                  ...(current[event.image_id] || {}),
                  phase: "ocr",
                  status: ["done", "failed", "skipped"].includes(current[event.image_id]?.status) ? current[event.image_id].status : "running",
                  detail,
                },
              }));
            }
          }
          if (event.type === "final_error") appendLog(event.message);
          if (event.type === "image_status") {
            setImageStatuses((current) => ({
              ...current,
              [event.image_id]: { phase: "ocr", status: event.status, detail: event.detail || "" },
            }));
            if (event.image) {
              applyImageRecord(event.image);
            }
          }
          if (event.type === "error") {
            appendLog(event.message);
            throw new Error(event.message);
          }
          if (event.type === "complete") {
            setImages(event.images || []);
            if (!event.ok) appendLog("일부 이미지 OCR/번역 실패");
            appendLog(`${(event.images || []).length}개 이미지 상태 갱신`);
          }
        },
      );
      // 작업 중 사용자가 다른 페이지로 이동했다면 완료 시 선택을 되돌리지 않는다.
      if (targetImage?.id && sessionIdRef.current === requestSid && selectedIdRef.current === startSelectedId) {
        setSelectedId(targetImage.id);
        selectedIdRef.current = targetImage.id;
      }
      return { logs: logLines.join("\n") };
    });
  }

  async function applyOverlayRequest({ image, guidance, font, silent = false, version = overlayEditVersionRef.current } = {}) {
    const targetImage = image || selected;
    if (!targetImage) return null;
    const requestSid = sessionId;
    const run = async () => {
      const result = await api("/api/overlay/apply", {
        method: "POST",
        body: JSON.stringify({
          session_id: requestSid,
          image_id: targetImage.id,
          font_path: font ?? fontPath,
          overlay_guidance: guidance || overlayGuidance,
          // 편집기는 항상 전체 상태를 보낸다. 병합하면 실행 취소·초기화로 지운 키가 되살아난다.
          replace: true,
        }),
      });
      const isCurrentSession = sessionIdRef.current === requestSid;
      const isCurrentImage = isCurrentSession && selectedIdRef.current === targetImage.id;
      // 현재 페이지 + 요청 시점 이후 편집이 없을 때만 overlay 상태를 교체하고 적용 version을 올린다.
      const isCurrentEdit = isCurrentImage && overlayEditVersionRef.current === version;
      if (!isCurrentSession || (silent && isCurrentImage && !isCurrentEdit)) {
        return { logs: "오래된 Overlay preview 응답 무시", version, stale: true };
      }
      applyImageRecord(result.image);
      if (isCurrentEdit) {
        setOverlayGuidance(normalizeOverlayGuidance(defaultOverlayGuidance(result.image)));
        overlayAppliedVersionRef.current = Math.max(overlayAppliedVersionRef.current, version);
      }
      return { logs: `Overlay preview 갱신 완료\nFinal: ${result.image.final_jpg}`, version };
    };
    if (!silent) {
      return runTask("overlay", "Overlay 적용", run);
    }
    setBusyTasks((current) => ({ ...current, overlay: true }));
    try {
      const result = await run();
      if (!result?.stale) setStatus("Overlay preview 자동 갱신 완료");
      return result;
    } catch (err) {
      setStatus(String(err.message || err));
      return null;
    } finally {
      setBusyTasks((current) => ({ ...current, overlay: false }));
    }
  }

  async function applyOverlay() {
    if (!selected || !pageDetailReady()) return;
    await applyOverlayRequest();
  }

  async function runAutoOverlayPreview() {
    if (!autoOverlayPreview || activeTab !== "overlay" || !canRenderOverlayImage(selected)) return;
    if (autoOverlayRunningRef.current || overlayBusy) {
      autoOverlayQueuedRef.current = true;
      return;
    }
    autoOverlayRunningRef.current = true;
    autoOverlayQueuedRef.current = false;
    const version = overlayEditVersionRef.current;
    await applyOverlayRequest({
      image: selected,
      guidance: overlayGuidance,
      font: fontPath,
      silent: true,
      version,
    });
    autoOverlayRunningRef.current = false;
    if (autoOverlayQueuedRef.current || overlayEditVersionRef.current !== version) {
      autoOverlayQueuedRef.current = false;
      if (autoOverlayTimerRef.current) window.clearTimeout(autoOverlayTimerRef.current);
      autoOverlayTimerRef.current = window.setTimeout(() => {
        autoOverlayTimerRef.current = null;
        runAutoOverlayPreview();
      }, 300);
    }
  }

  function exportSession() {
    if (!sessionId) return;
    window.location.href = `/api/session/${encodeURIComponent(sessionId)}/export`;
    setStatus("작업 결과 zip 다운로드를 시작했습니다.");
  }

  function toggleExportGenerateId(imageId) {
    setExportGenerateIds((current) => (
      current.includes(imageId)
        ? current.filter((id) => id !== imageId)
        : [...current, imageId]
    ));
  }

  async function generateMissingFinals(options = {}) {
    if (!sessionId) {
      setStatus("세션이 없어 이미지 생성을 시작할 수 없습니다.");
      return;
    }
    const targetIds = options.imageIds || selectedExportMissingIds;
    const force = Boolean(options.force);
    if (!targetIds.length) {
      setStatus("이미지 생성 후보를 먼저 선택하세요.");
      setExportLog("이미지 생성 후보를 먼저 선택하세요.");
      return;
    }
    setExportLog(`선택된 후보 ${targetIds.length}개 이미지 생성 요청 준비${force ? " (retry)" : ""}`);
    const requestSid = sessionId;
    await runTask("export", "Missing final 이미지 생성", async () => {
      let latestImages = images;
      let generatedCount = 0;
      let failedCount = 0;
      const appendLog = (message) => {
        if (!message) return;
        setExportLog((current) => `${current ? `${current}\n` : ""}${message}`);
      };
      await apiStream("/api/export/generate-missing/stream", {
        method: "POST",
        body: JSON.stringify({
          session_id: requestSid,
          image_ids: targetIds,
          model: exportImageModel || "gpt-image-2",
          prompt_guide: exportPromptGuide,
          analyze_first: exportAnalyzeFirst,
          text_model: exportTextModel || "gpt-5.4-mini",
          quality: exportImageQuality,
          size: exportImageSize,
          output_format: exportOutputFormat,
          output_compression: exportOutputCompression,
          max_workers: analyzeWorkers,
          force,
        }),
      }, (event) => {
        if (event.type === "log") {
          appendLog(event.message);
          setStatus(event.message || "이미지 생성 진행 중");
          return;
        }
        if (event.type === "error") {
          failedCount += 1;
          appendLog(`ERROR ${event.message || "이미지 생성 실패"}`);
          setStatus(event.message || "이미지 생성 실패");
          return;
        }
        if (event.type === "complete") {
          latestImages = event.images || latestImages;
          generatedCount = event.generated?.length || 0;
          failedCount = event.failed?.length || failedCount;
          if (event.generated?.length) setExportFocusedId(event.generated[event.generated.length - 1]);
          appendLog(`완료. 생성 ${generatedCount}개, 실패 ${failedCount}개`);
        }
      });
      if (sessionIdRef.current === requestSid) {
        setImages(latestImages || []);
        setExportGenerateIds([]);
      }
      return { logs: `이미지 생성 완료. 생성 ${generatedCount}개, 실패 ${failedCount}개` };
    });
  }

  function selectImage(img) {
    if (!img?.id) return;
    setSelectedId(img.id);
    selectedIdRef.current = img.id;
    setStatus(`${img.name} 선택`);
  }

  function selectImageByIndex(index) {
    if (!images.length) return;
    const nextIndex = Math.min(images.length - 1, Math.max(0, index));
    selectImage(images[nextIndex]);
  }

  function ocrStateForImage(img) {
    const liveStatus = imageStatuses[img.id];
    const state = liveStatus?.phase === "ocr" ? liveStatus.status : null;
    if (state === "failed" || state === "running" || state === "queued" || state === "skipped") return state;
    if (state === "done" && img.final_url) return "done";
    const savedState = img.ocr_status?.status;
    if (savedState === "failed" || savedState === "skipped") return savedState;
    if (savedState === "done") return "done";
    if (img.integrated_skipped || img.integrated?.skipped) return "skipped";
    if (img.has_integrated_json) return "done";
    if (img.final_url) return "done";
    if (!img.indexed_sheet_url) return "failed";
    return "idle";
  }

  function ocrDetailForImage(img) {
    const liveStatus = imageStatuses[img.id];
    const detail = liveStatus?.phase === "ocr" ? liveStatus.detail : "";
    if (detail) return detail;
    if (img.ocr_status?.detail) return img.ocr_status.detail;
    if (img.ocr_status?.message) return img.ocr_status.message;
    if (img.integrated_skipped || img.integrated?.skipped) return img.integrated_message || img.integrated?.message || "OCR/번역 스킵";
    if (img.has_integrated_json) return "integrated.json 있음";
    if (img.final_url) return "final ready";
    if (img.indexed_sheet_url) return "ready";
    return "analysis 필요";
  }

  function ocrProgressForImage(img, state, detail) {
    if (state === "done" || state === "skipped") return 100;
    if (state === "queued" || state === "idle") return 0;
    const text = String(detail || "");
    const percentMatch = text.match(/(\d{1,3})\s*%/);
    if (percentMatch) return Math.max(0, Math.min(100, Number(percentMatch[1])));
    const stepMatch = text.match(/(\d+)\s*\/\s*(\d+)/);
    if (stepMatch) {
      const current = Number(stepMatch[1]);
      const total = Math.max(1, Number(stepMatch[2]));
      return Math.max(0, Math.min(100, Math.round((current / total) * 100)));
    }
    return state === "running" ? 38 : 0;
  }

  function renderOcrImageButton(img) {
    const state = ocrStateForImage(img);
    const detail = ocrDetailForImage(img);
    const progress = ocrProgressForImage(img, state, detail);
    return (
      <button
        key={img.id}
        className={`image-status ${state} ${selected?.id === img.id ? "active" : ""}`}
        draggable={!ocrBusy && Boolean(img.indexed_sheet_url)}
        onDragStart={(event) => {
          event.dataTransfer.setData("text/plain", img.id);
          event.dataTransfer.effectAllowed = "move";
        }}
        onClick={() => selectImage(img)}
      >
        <img className="image-status-thumb" src={img.input_url} alt="" loading="lazy" />
        <span>{img.name}</span>
        <strong>{statusLabel[state] || state}</strong>
        {detail ? <em>{detail}</em> : null}
        {state === "running" ? (
          <div className="ocr-progress" aria-hidden="true">
            <i style={{ width: `${progress}%` }} />
          </div>
        ) : null}
      </button>
    );
  }

  function renderOverlayRegionCard(region) {
    const rid = overlayItemKey(region);
    const override = overlayRegionValue(overlayGuidance, rid);
    const baseRid = overlayRegionBaseKey(region);
    const baseOverride = overlayRegionValue(overlayGuidance, baseRid);
    const segmentMode = baseOverride.segment_mode || "auto";
    const hasSegmentSiblings = regionSegmentSiblingCount(selected, region) > 1 || overlayRegions.filter((item) => regionId(item) === regionId(region)).length > 1 || region.source_segment_index != null;
    const writingChoice = overlayWritingModeChoice(override);
    const inferredWritingMode = inferredRegionWritingMode(selected, region);
    const text = override.text_override ?? baseRegionText(selected, region);
    const safeRectValue = regionSafeRectValue(region, override);
    const rotationValue = Math.round(Number(override.rotation_deg || 0) || 0);
    const textAlign = override.text_align || "center";
    return (
      <div key={rid} className={`region-card active`} onClick={() => setSelectedRegionTracked(rid)}>
        <div className="region-card-head">
          <strong>{overlayItemLabel(region)}</strong>
          <div className="region-card-controls" onClick={(event) => event.stopPropagation()}>
            {hasSegmentSiblings ? (
              <div className="segment-mode-controls" title="같은 bubble 안 segment 렌더 단위를 강제합니다">
                <button
                  type="button"
                  className={segmentMode === "merge" ? "active" : ""}
                  onClick={() => setOverlayRegionSegmentMode(region, "merge")}
                >
                  Merge
                </button>
                <button
                  type="button"
                  className={segmentMode === "split" ? "active" : ""}
                  onClick={() => setOverlayRegionSegmentMode(region, "split")}
                >
                  Split
                </button>
              </div>
            ) : null}
            <label className="writing-toggle" title={`자동 배치${inferredWritingMode ? ` · 추정 ${inferredWritingMode === "vertical" ? "세로" : "가로"}` : ""}`}>
              <input
                type="radio"
                name={`writing-mode-${rid}`}
                value="auto"
                checked={writingChoice === "auto"}
                onChange={() => updateOverlayRegion(rid, { force_horizontal: false, force_vertical: false })}
              />
              <span>자동</span>
            </label>
            <label className="writing-toggle" title="가로쓰기 강제">
              <input
                type="radio"
                name={`writing-mode-${rid}`}
                value="horizontal"
                checked={writingChoice === "horizontal"}
                onChange={() => setOverlayWritingMode(rid, "horizontal")}
              />
              <span>가로</span>
            </label>
            <label className="writing-toggle" title="세로쓰기 강제">
              <input
                type="radio"
                name={`writing-mode-${rid}`}
                value="vertical"
                checked={writingChoice === "vertical"}
                onChange={() => setOverlayWritingMode(rid, "vertical")}
              />
              <span>세로</span>
            </label>
          </div>
        </div>
        <div className="region-editor-grid">
          <textarea
            value={text}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => updateOverlayRegion(rid, { text_override: event.target.value }, `text:${rid}`)}
          />
          <div className="region-bbox-grid" onClick={(event) => event.stopPropagation()}>
            <div className="text-align-controls" title="safe rect 안에서 텍스트를 좌/중/우 정렬합니다">
              <button type="button" className={textAlign === "left" ? "active" : ""} onClick={() => setOverlayTextAlign(rid, region, "left")} title="텍스트 좌측 정렬">|←</button>
              <button type="button" className={textAlign === "center" ? "active" : ""} onClick={() => setOverlayTextAlign(rid, region, "center")} title="텍스트 중앙 정렬">↔</button>
              <button type="button" className={textAlign === "right" ? "active" : ""} onClick={() => setOverlayTextAlign(rid, region, "right")} title="텍스트 우측 정렬">→|</button>
            </div>
            {[
              ["x", "X", safeRectValue[0]],
              ["y", "Y", safeRectValue[1]],
              ["w", "W", safeRectValue[2]],
              ["h", "H", safeRectValue[3]],
            ].map(([field, label, value]) => (
              <label key={field}>
                <span>{label}</span>
                <input
                  type="number"
                  value={value}
                  min={field === "w" || field === "h" ? OVERLAY_MIN_RECT_SIZE : 0}
                  step="1"
                  onChange={(event) => updateOverlaySafeRectField(rid, region, field, event.target.value)}
                />
              </label>
            ))}
            <label>
              <span>°</span>
              <input
                type="number"
                value={rotationValue}
                min="-45"
                max="45"
                step="1"
                title="회전 각도"
                onChange={(event) => updateOverlayRegion(rid, { rotation_deg: event.target.value }, `rotation:${rid}`)}
              />
            </label>
          </div>
        </div>
      </div>
    );
  }

  async function removeLoadedImage(imageId) {
    if (!imageId || !sessionId) return;
    const target = images.find((img) => img.id === imageId);
    if (workspaceStarted && !window.confirm(`${target?.name || imageId} 파일을 이 세션에서 삭제하시겠습니까?`)) return;
    await runTask("input", "이미지 제거", async () => {
      const removedIndex = Math.max(0, images.findIndex((img) => img.id === imageId));
      const result = await api("/api/image/remove", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, image_id: imageId }),
      });
      const nextImages = result.images || [];
      if (result.session?.project_name != null && !projectNameDirtyRef.current) {
        setProjectName(result.session.project_name || "");
      }
      if (result.sessions) setRecentSessions(result.sessions);
      setImages(nextImages);
      setImageStatuses((current) => {
        const next = { ...current };
        delete next[imageId];
        return next;
      });
      if (selectedIdRef.current === imageId || !nextImages.some((img) => img.id === selectedIdRef.current)) {
        const nextSelected = nextImages[Math.min(removedIndex, nextImages.length - 1)] || null;
        setSelectedId(nextSelected?.id || "");
        selectedIdRef.current = nextSelected?.id || "";
        setSelectedTg(null);
        setSelectedRegionTracked(null);
      }
      return { logs: nextImages.length ? "이미지 제거 완료" : "이미지 제거 완료. 세션에 남은 파일이 없습니다." };
    });
  }

  function guidanceHistoryItems() {
    const items = [];
    (guidance.manual_seed_points || []).forEach((item, index) => {
      const point = item.point || item.seed || [];
      items.push({ key: `seed-${index}`, kind: "manual_seed_points", index, label: `Seed ${index + 1}`, detail: Array.isArray(point) ? `${Math.round(point[0])}, ${Math.round(point[1])}` : "" });
    });
    (guidance.virtual_walls || []).forEach((item, index) => {
      items.push({ key: `wall-${index}`, kind: "virtual_walls", index, boxId: item.box_id || "", label: item.box_id ? `Edge ${item.box_id}` : `Edge ${index + 1}`, detail: item.source || "" });
    });
    (guidance.split_guides || []).forEach((item, index) => {
      items.push({ key: `split-${index}`, kind: "split_guides", index, label: `Split line ${index + 1}`, detail: item.source || "" });
    });
    (guidance.manual_regions || []).forEach((item, index) => {
      const type = item.type === "unboxed" ? "Ubox" : "Bubble";
      const forcedLama = item.type === "unboxed" && item.inpaint_engine === "lama";
      items.push({
        key: `region-${index}`,
        kind: "manual_regions",
        index,
        label: `${type} ${index + 1}`,
        detail: forcedLama ? "LaMa 강제" : item.source || "",
        isUbox: item.type === "unboxed",
        forcedLama,
      });
    });
    (guidance.tg_splits || []).forEach((item, index) => {
      items.push({ key: `tg-split-${index}`, kind: "tg_splits", index, label: `TG${item.text_group_id || "?"} split`, detail: "" });
    });
    (guidance.disabled_tg_ids || []).forEach((id) => {
      items.push({ key: `tg-off-${id}`, kind: "disabled_tg_ids", id, label: `TG${id} off`, detail: "" });
    });
    (guidance.suppressed_regions || []).forEach((item, index) => {
      items.push({ key: `region-off-${index}`, kind: "suppressed_regions", index, label: `R${item.region_id || "?"} off`, detail: item.source || "" });
    });
    return items;
  }

  function stripGuidanceHistoryItem(baseGuidance, historyItem) {
    const next = cloneJson(baseGuidance || defaultGuidance(null));
    if (!historyItem?.kind) return next;
    if (historyItem.kind === "virtual_walls" && historyItem.boxId) {
      next.virtual_walls = (next.virtual_walls || []).filter((item) => item.box_id !== historyItem.boxId);
      return next;
    }
    if (historyItem.kind === "disabled_tg_ids") {
      next.disabled_tg_ids = (next.disabled_tg_ids || []).filter((id) => Number(id) !== Number(historyItem.id));
      return next;
    }
    const list = [...(next[historyItem.kind] || [])];
    if (historyItem.index >= 0 && historyItem.index < list.length) list.splice(historyItem.index, 1);
    next[historyItem.kind] = list;
    return next;
  }

  // Ubox 지우기 엔진: 기본은 배경 분류(Telea/LaMa) 자동, 켜면 LaMa로 고정한다.
  function toggleUboxForcedLama(historyItem) {
    setGuidanceTracked((current) => {
      const next = cloneJson(current || defaultGuidance(null));
      const region = (next.manual_regions || [])[historyItem.index];
      if (!region || region.type !== "unboxed") return current;
      if (region.inpaint_engine === "lama") delete region.inpaint_engine;
      else region.inpaint_engine = "lama";
      return next;
    }, "Ubox 엔진 변경");
  }

  function removeGuidanceHistoryItem(historyItem, options = {}) {
    if (!historyItem) return false;
    const current = currentGuidanceRef.current || guidance || defaultGuidance(null);
    const next = stripGuidanceHistoryItem(current, historyItem);
    if (guidanceEquals(current, next)) return false;
    if (options.fromUndo) {
      setGuidanceRedoStack((items) => [...items.slice(-29), { guidance: cloneJson(current), label: historyItem.label }]);
    } else {
      setGuidanceUndoStack((items) => [...items.slice(-29), { guidance: cloneJson(current), label: `Remove ${historyItem.label}` }]);
      setGuidanceRedoStack([]);
    }
    setGuidanceDirect(next, `${historyItem.label} 제거`);
    return true;
  }

  function removeLatestGuidanceHistoryItem() {
    const items = guidanceHistoryItems();
    return removeGuidanceHistoryItem(items[items.length - 1], { fromUndo: true });
  }

  function clearUnsavedGuidance() {
    if (!selected) return;
    setGuidanceTracked(defaultGuidance({ ...selected, guidance: {} }), "Clear unsaved");
    setSelectedTg(null);
    setStatus("화면 guidance 초기화. 저장하려면 Apply Analysis 필요");
  }

  const guidanceHistory = guidanceHistoryItems();

  if (!workspaceStarted) {
    return (
      <div className="app-shell import-mode">
        <header className="app-topbar">
          <div className="app-brand">Comic Translator Studio</div>
          <a className="app-manual-link" href="/manual" target="_blank" rel="noopener">사용 설명서</a>
        </header>
        <main className="import-screen">
          <section className="import-primary">
            <div className="import-title">
              <h1>Start Project</h1>
              <p>Load comic images to start analysis.</p>
            </div>
            <div className="import-card">
              <div
                className="import-drop"
                onDragOver={(event) => {
                  // 기본 동작(브라우저가 파일로 이동)을 막아야 drop 이벤트가 온다.
                  event.preventDefault();
                  event.dataTransfer.dropEffect = inputBusy ? "none" : "copy";
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  if (inputBusy) return;
                  uploadFiles(Array.from(event.dataTransfer.files || []));
                }}
              >
                <strong>Drop files here</strong>
                <span>or browse from your computer</span>
                <button className="primary" onClick={() => fileRef.current?.click()} disabled={inputBusy}>Load Files</button>
              </div>
              <div className="import-folder">
                <input value={folderPath} onChange={(e) => setFolderPath(e.target.value)} placeholder="/path/to/comic/folder" />
                <button onClick={importPath} disabled={inputBusy || !folderPath}>Load Folder</button>
              </div>
              <input ref={fileRef} type="file" multiple accept="image/*" hidden onChange={(e) => uploadFromInput(e.currentTarget)} />
            </div>
          </section>
          <aside className="import-config">
            <div className="section compact-section">
              <h2>Project Configuration</h2>
              <div className="field">
                <label>Project Name</label>
                <input
                  value={projectName}
                  onChange={(event) => {
                    projectNameDirtyRef.current = true;
                    setProjectName(event.target.value);
                  }}
                  onBlur={() => saveProjectName()}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                  }}
                  placeholder="예: ORE2 1권"
                />
              </div>
              <div className="field">
                <label>Sorting Rule</label>
                <select defaultValue="natural">
                  <option value="natural">Natural Order (Name)</option>
                </select>
              </div>
              <div className="field">
                <label>Analyze Workers</label>
                <input
                  type="number"
                  min="1"
                  max="12"
                  value={analyzeWorkers}
                  onChange={(e) => setAnalyzeWorkers(Math.max(1, Math.min(12, Number(e.target.value) || 1)))}
                />
              </div>
              <div className="toggles">
                <label className="check"><input type="radio" name="cache-policy" defaultChecked /> Use Cache</label>
                <label className="check"><input type="radio" name="cache-policy" /> Force Re-analyze</label>
              </div>
            </div>
            <div className="section">
              <div className="section-title-row">
                <h2>Loaded Files</h2>
              </div>
              {images.length ? (
                <div className="loaded-file-list">
                  {images.map((img, index) => (
                    <div key={img.id} className="loaded-file-row">
                      <span>{index + 1}</span>
                      <strong title={img.name}>
                        {img.name}
                        {img.page_side ? <em>{img.page_side}</em> : null}
                      </strong>
                      <div className="loaded-file-actions">
                        <button className="icon-button" onClick={() => removeLoadedImage(img.id)} disabled={inputBusy || analyzeBusy} title="목록에서 제거">×</button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty compact-empty">아직 불러온 파일이 없습니다.</div>
              )}
            </div>
            <div className="section">
              <h2>Recent Projects</h2>
              {recentSessions.length ? (
                <div className="recent-session-list">
                  {recentSessions.map((recent) => (
                    <div key={recent.session_id} className="recent-session-row-wrap">
                      <button className="recent-session-row" onClick={() => openRecentSession(recent)}>
                        {recent.thumbnail_url ? <img src={recent.thumbnail_url} alt="" /> : <span className="recent-session-thumb" />}
                        <strong>{recent.project_name || `${recent.image_count} images`}</strong>
                        <span title={recent.updated_at || recent.created_at}>
                          {recent.updated_at || recent.created_at}
                        </span>
                      </button>
                      <button
                        className="recent-session-menu-button"
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setRecentMenuId(recentMenuId === recent.session_id ? "" : recent.session_id);
                        }}
                        title="프로젝트 메뉴"
                      >
                        ...
                      </button>
                      {recentMenuId === recent.session_id ? (
                        <div className="recent-session-menu">
                          <button type="button" onClick={() => renameRecentSession(recent)}>Rename</button>
                          <button type="button" className="danger-text" onClick={() => deleteRecentSession(recent)}>Delete</button>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty compact-empty">최근 작업 없음</div>
              )}
            </div>
            <div className="import-action">
              <div>
                <strong>{images.length ? `${images.length} images loaded` : "No images loaded"}</strong>
                <span>{images.length ? "캐시가 있으면 바로 시각화하고, 없으면 분석을 진행합니다." : "파일이나 폴더를 먼저 불러오세요."}</span>
              </div>
              <button className="primary wide" onClick={startSpeechBubbleAnalysis} disabled={!images.length || inputBusy || analyzeBusy}>
                Analyze & Open 말풍선 분석
              </button>
            </div>
          </aside>
        </main>
        <footer className="status-bar">System Status: {inputBusy ? "Loading" : "Idle"} | Session: {sessionId || "-"}</footer>
      </div>
    );
  }

  return (
    <div className={`app workspace-mode ${toolsOpen ? "" : "tools-collapsed"} ${activeTab === "overlay" ? "overlay-mode" : ""} ${activeTab === "ocr" ? "ocr-mode" : ""} ${activeTab === "export" ? "export-mode" : ""} ${activeTab === "restore" ? "restore-mode" : ""}`}>
      <header className="workspace-global-topbar">
        <button className="workspace-brand" type="button" onClick={returnToStartPage} title="Start page로 돌아가기">
          Comic Translator Studio
        </button>
        <input
          className="project-name-input"
          value={projectName}
          onChange={(event) => {
            projectNameDirtyRef.current = true;
            setProjectName(event.target.value);
          }}
          onBlur={() => saveProjectName()}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
          placeholder="Untitled project"
        />
        <nav className="workflow-tabs top-workflow-tabs">
          {WORKFLOW_TABS.map(([id, label]) => (
            <button key={id} className={activeTab === id ? "active" : ""} onClick={() => setActiveTab(id)} title={`${label} (${shortcutById(`tab.${id}`)})`}>
              {label}
            </button>
          ))}
        </nav>
        <div className="file-navigator top-file-nav">
          <button onClick={() => selectImageByIndex(currentIndex - 1)} disabled={currentIndex <= 0}>‹</button>
          <select value={selected?.id || ""} onChange={(event) => selectImage(images.find((img) => img.id === event.target.value))}>
            {images.map((img, index) => <option key={img.id} value={img.id}>{`${index + 1}. ${img.name || img.id}`}</option>)}
          </select>
          <button onClick={() => selectImageByIndex(currentIndex + 1)} disabled={currentIndex < 0 || currentIndex >= images.length - 1}>›</button>
          <button
            className="danger-file-button"
            type="button"
            onClick={() => selected && removeLoadedImage(selected.id)}
            disabled={!selected || inputBusy || analyzeBusy || guidanceBusy}
            title="현재 파일을 세션에서 삭제"
          >
            ×
          </button>
          <button className="shortcut-help-button" type="button" onClick={() => setHelpOpen((open) => !open)} title={`단축키 도움말 (${shortcutById("help.toggle")})`}>?</button>
        </div>
      </header>
      <aside className={`sidebar ${sidebarOpen ? "open" : "collapsed"}`}>
        <div className="brand">
          {sidebarOpen ? <h1>Comic Review</h1> : <span className="side-label">Review</span>}
          <button className="icon-button" onClick={() => setSidebarOpen(!sidebarOpen)} title={sidebarOpen ? "사이드바 접기" : "사이드바 열기"}>
            {sidebarOpen ? "‹" : "›"}
          </button>
        </div>

        {sidebarOpen ? <>
        <div className="section">
          <h2>Input</h2>
          <div className="field">
            <label>로컬 폴더 경로</label>
            <input value={folderPath} onChange={(e) => setFolderPath(e.target.value)} placeholder="C:/Developer/..." />
          </div>
          <div className="row">
            <button onClick={importPath} disabled={inputBusy || !folderPath}>Import</button>
            <button onClick={() => fileRef.current?.click()} disabled={inputBusy}>Files</button>
          </div>
          <input ref={fileRef} type="file" multiple accept="image/*" hidden onChange={(e) => uploadFromInput(e.currentTarget)} />
        </div>

        <div className="section">
          <h2>Run</h2>
          <div className="row">
            <button className="primary" onClick={() => analyze("all")} disabled={analyzeBusy || guidanceBusy || !images.length}>Analyze All</button>
            <button onClick={() => selected && analyze(selected.id)} disabled={analyzeBusy || guidanceBusy || !selected}>One</button>
          </div>
          <div className="field">
            <label>Analyze workers</label>
            <input
              type="number"
              min="1"
              max="12"
              value={analyzeWorkers}
              onChange={(e) => setAnalyzeWorkers(Math.max(1, Math.min(12, Number(e.target.value) || 1)))}
            />
          </div>
          <div className="field">
            <label>Overlay font</label>
            <select value={fontPath} onChange={(e) => updateFontPath(e.target.value)}>
              <option value={fontPath}>{fontPath ? "Default" : "Font"}</option>
              {fonts.map((font) => <option key={font.path} value={font.path}>{font.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Extra prompt</label>
            <textarea value={extraPrompt} onChange={(e) => updateExtraPrompt(e.target.value)} />
          </div>
          <div className="row">
            <button className="primary" onClick={applyGuidance} disabled={guidanceBusy || analyzeBusy || !selected}>Apply Analysis</button>
            <button onClick={() => runFinal()} disabled={ocrBusy || !selected}>Run Final</button>
          </div>
          <button className="wide" onClick={() => setActiveTab("export")} disabled={!sessionId || !images.length}>Export Page</button>
        </div>

        <div className="section">
          <h2>Status</h2>
          <div className="metric-grid">
            <div className="metric"><strong>{images.length}</strong><span>images</span></div>
            <div className="metric"><strong>{disabledCount}</strong><span>TG off</span></div>
            <div className="metric"><strong>{splitCount}</strong><span>TG split</span></div>
            <div className="metric"><strong>{uboxCount}</strong><span>Ubox</span></div>
            <div className="metric"><strong>{suppressedCount}</strong><span>R off</span></div>
            <div className="metric"><strong>{selected?.regions?.regions?.length || 0}</strong><span>regions</span></div>
          </div>
          <div className="image-status-list">
            {images.map((img) => {
              const state = imageStatuses[img.id]?.status || "idle";
              const detail = imageStatuses[img.id]?.detail || "";
              return (
                <div key={img.id} className="image-status-row-wrap">
                  <button className={`image-status ${state} ${selected?.id === img.id ? "active" : ""}`} onClick={() => selectImage(img)}>
                    <span>{img.name}</span>
                    <strong>{statusLabel[state] || state}</strong>
                    {detail ? <em>{detail}</em> : null}
                  </button>
                  <button
                    className="image-status-delete"
                    type="button"
                    onClick={() => removeLoadedImage(img.id)}
                    disabled={inputBusy || analyzeBusy || guidanceBusy}
                    title="세션에서 파일 삭제"
                  >
                    ×
                  </button>
                </div>
              );
            })}
          </div>
          <div className="field">
            <div className="log">{status || "대기 중"}</div>
          </div>
        </div>
        </> : null}
      </aside>

      <main className="workspace">
        {activeTab === "guidance" ? (
        <div className="guidance-workspace">
          <div className="viewer-grid guidance-grid">
            <section className="preview-card guidance-preview-card">
              <div className="card-head">
                <span>Compare</span>
                <div className="preview-tabs">
                  {guidanceCompareTabs.map(([id, label, url]) => (
                    <button key={id} className={effectiveGuidanceCompareMode === id ? "active" : ""} onClick={() => setGuidanceCompareMode(id)} disabled={!url}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="preview-body fit-page">
                {guidanceCompareUrl ? <img src={guidanceCompareUrl} /> : <div className="empty">비교 이미지 없음</div>}
              </div>
            </section>

            <section className="viewer-card guidance-editor-card">
              <div className="card-head editor-head">
                <div className="editor-title">
                  <span>Analysis Workspace</span>
                  <strong>{selectedTg ? `TG${selectedTg}` : selectedToolLabel}</strong>
                </div>
                <div className="editor-meta">
                  <span>Original + live analysis overlay</span>
                  <span>{guidanceHistory.length} guides</span>
                </div>
              </div>
                <EditorCanvas
                  ref={editorCanvasRef}
                  image={selected}
                  baseUrl={guidanceEditorUrl}
                  guidance={guidance}
                  setGuidance={setGuidanceTracked}
                  tool={tool}
                  layers={layers}
                  selectedTg={selectedTg}
                setSelectedTg={setSelectedTg}
                setStatus={setStatus}
              />
              <div className="floating-toolbox" aria-label="말풍선 분석 도구">
                {TOOL_LABELS.map(([id, label]) => (
                  <button
                    key={id}
                    className={tool === id ? "active" : ""}
                    onClick={() => setTool(id)}
                    aria-label={label}
                    title={`${label} (${shortcutFor("guidance", "tool", id)})`}
                  >
                    <span>{TOOL_ICONS[id]}</span>
                    <em>{shortcutFor("guidance", "tool", id)}</em>
                  </button>
                ))}
              </div>
            </section>
          </div>
        </div>
        ) : null}

        {activeTab === "ocr" ? (
          <div className="ocr-workspace">
            <section className="viewer-card">
              <div className="ocr-job-header">
                <div className="ocr-job-title">
                  <h1>Job Queue Monitor</h1>
                  <span>Gemini OCR / Translation Pipeline</span>
                </div>
                <div className="ocr-job-actions">
                  <label>
                    Workers
                    <input
                      type="number"
                      min="1"
                      max="12"
                      value={analyzeWorkers}
                      onChange={(e) => setAnalyzeWorkers(Math.max(1, Math.min(12, Number(e.target.value) || 1)))}
                    />
                  </label>
                  <button onClick={() => selected && runFinalStream(selected.id)} disabled={ocrBusy || !selected || !selected.indexed_sheet_url}>Run Current</button>
                  <button onClick={() => selected && runFinalStream(selected.id, true)} disabled={ocrBusy || !selected || !selected.indexed_sheet_url} title="현재 파일의 기존 OCR 결과를 무시하고 다시 실행">Force Current</button>
                  <button className="danger" onClick={() => runFinalStream("failed")} disabled={ocrBusy || !ocrNeedsAttentionImages.length}>Retry Failed</button>
                  <button className="primary" onClick={() => runFinalStream("all")} disabled={ocrBusy || !images.length} title="이미 OCR 결과가 있는 파일은 건너뛰고 미완료 파일만 실행">Run Unfinished</button>
                </div>
              </div>
              <div className="ocr-panel">
                <div className="ocr-prompt-panel">
                  <label className="ocr-model-field">
                    OCR/Translation Model
                    <select
                      value={effectiveOcrModel}
                      onChange={(e) => updateOcrModel(e.target.value)}
                      disabled={ocrBusy || !ocrModels.length}
                      title={ocrModels.find((model) => model.id === effectiveOcrModel)?.note || undefined}
                    >
                      {ocrModels.map((model) => (
                        <option key={model.id} value={model.id} title={model.note || undefined}>
                          {model.label || model.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>Additional Translation Prompt</label>
                  <textarea
                    value={extraPrompt}
                    onChange={(e) => updateExtraPrompt(e.target.value)}
                    placeholder="번역 톤, 고유명사, 말투, 문맥 힌트를 적어두면 OCR/번역 요청에 함께 전달됩니다."
                  />
                </div>
                <div className="ocr-queue-sections">
                  <section className="ocr-queue-group ready">
                    <div className="ocr-queue-head">
                      <strong>Ready Files</strong>
                      <span>{ocrReadyImages.length}</span>
                    </div>
                    <div
                      className="image-status-list large ocr-drop-zone"
                      title={forceDropLabel}
                      onDragOver={(event) => {
                        if (ocrBusy) return;
                        event.preventDefault();
                        event.dataTransfer.dropEffect = "move";
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        if (ocrBusy) return;
                        const imageId = event.dataTransfer.getData("text/plain");
                        const image = images.find((item) => item.id === imageId);
                        if (!image?.indexed_sheet_url) return;
                        selectImage(image);
                        runFinalStream(image.id, true);
                      }}
                    >
                      {ocrReadyImages.length ? ocrReadyImages.map(renderOcrImageButton) : <div className="empty compact-empty">대기 파일 없음</div>}
                    </div>
                  </section>
                  <section className="ocr-queue-group progress">
                    <div className="ocr-queue-head">
                      <strong>In Progress</strong>
                      <span>{ocrInProgressImages.length}</span>
                    </div>
                    <div className="image-status-list large">
                      {ocrInProgressImages.length ? ocrInProgressImages.map(renderOcrImageButton) : <div className="empty compact-empty">진행 중인 파일 없음</div>}
                    </div>
                  </section>
                  <section className="ocr-queue-group success">
                    <div className="ocr-queue-head">
                      <strong>Success Files</strong>
                      <span>{successImages.length}</span>
                    </div>
                    <div className="image-status-list large">
                      {successImages.length ? successImages.map(renderOcrImageButton) : <div className="empty compact-empty">완료 파일 없음</div>}
                    </div>
                  </section>
                  <section className="ocr-queue-group attention">
                    <div className="ocr-queue-head">
                      <strong>Needs Retry</strong>
                      <span>{ocrNeedsAttentionImages.length}</span>
                    </div>
                    <div className="image-status-list large">
                      {ocrNeedsAttentionImages.length ? ocrNeedsAttentionImages.map(renderOcrImageButton) : <div className="empty compact-empty">재시도 대상 없음</div>}
                    </div>
                  </section>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {activeTab === "overlay" ? (
          <div className="overlay-grid">
            <section className="viewer-card overlay-stage-card overlay-final-card">
              <div className="card-head editor-head">
                <div className="editor-title">
                  <span>Original Image</span>
                  <strong>{selected?.name || "-"}</strong>
                </div>
              </div>
              <div className="preview-body overlay-preview-body">
                {selected?.input_url ? <img src={selected.input_url} /> : <div className="empty">원본 이미지 없음</div>}
              </div>
            </section>
            <section className="viewer-card overlay-stage-card overlay-source-card">
              <div className="card-head editor-head">
                <div className="editor-title">
                  <span>Overlay Result Editor</span>
                  <strong>{selectedRegion ? overlayItemLabel(selectedRegion) : "-"}</strong>
                </div>
              </div>
              <div className="overlay-stage-body">
                  <OverlayCanvas
                    ref={overlayCanvasRef}
                    tool={overlayTool}
                    setTool={setOverlayTool}
                    image={selected}
                    overlayGuidance={overlayGuidance}
                    setOverlayGuidance={setOverlayGuidanceTracked}
                    selectedRegionId={selectedRegionRefValue()}
                    setSelectedRegionId={setSelectedRegionTracked}
                    setStatus={setStatus}
                    onRequestApply={() => setOverlayApplyRequest((count) => count + 1)}
                  />
              </div>
            </section>
          </div>
        ) : null}

        {activeTab === "restore" ? <RestoreStage restore={restore} setStatus={setStatus} /> : null}

        {activeTab === "export" ? (
          <div className="export-workspace">
            <section className="export-left-panel">
              <div className="export-status-card">
                <div className="export-status-head">
                  <span>Project Status</span>
                  <strong>{finalImages.length} / {images.length}</strong>
                </div>
                <div className="export-progress-track">
                  <span style={{ width: `${images.length ? Math.round((finalImages.length / images.length) * 100) : 0}%` }} />
                </div>
                <div className="export-status-legend">
                  <span><i className="ready-dot" />Ready {finalImages.length}</span>
                  <span><i className="missing-dot" />Fallback candidates {missingFinalImages.length}</span>
                </div>
              </div>
              <div className="export-missing-list-head">
                <strong>Fallback Candidates</strong>
                <button
                  onClick={() => setExportGenerateIds(missingFinalImages.map((img) => img.id))}
                  disabled={!missingFinalImages.length}
                >
                  Select All
                </button>
              </div>
              <div className="export-missing-file-list">
                {missingFinalImages.length ? missingFinalImages.map((img) => (
                  <button
                    key={img.id}
                    className={`export-missing-file ${exportFocusedImage?.id === img.id ? "active" : ""}`}
                    onClick={() => setExportFocusedId(img.id)}
                  >
                    <div className="export-missing-thumb">
                      {img.input_url ? <img src={img.input_url} alt="" /> : <div className="export-thumb-empty" />}
                    </div>
                    <input
                      type="checkbox"
                      checked={selectedExportMissingIds.includes(img.id)}
                      onChange={(event) => {
                        event.stopPropagation();
                        toggleExportGenerateId(img.id);
                      }}
                      onClick={(event) => event.stopPropagation()}
                    />
                    <span>{img.name || img.id}</span>
                    <em>{isImageGenerationFallback(img) ? "generated fallback" : "original fallback"}</em>
                  </button>
                )) : <div className="empty compact-empty">Fallback 후보 없음</div>}
              </div>
              <button className="primary export-cta" onClick={exportSession} disabled={anyBusy || !sessionId || !images.length}>
                Export Final Zip
              </button>
            </section>
            <section className="export-center-panel">
              <section className="export-preview-panel">
                <div className="export-preview-head">
                  <div>
                    <span>{exportFocusedIsGenerated ? "Generated Preview" : "Send Preview"}</span>
                    <strong>{exportFocusedImage?.name || "-"}</strong>
                  </div>
                  <div className="export-preview-actions">
                    <button
                      onClick={() => exportFocusedImage && generateMissingFinals({ imageIds: [exportFocusedImage.id], force: true })}
                      disabled={anyBusy || !exportFocusedImage}
                    >
                      Retry
                    </button>
                    <button className="primary" onClick={exportSession} disabled={anyBusy || !sessionId || !images.length}>
                      Export to Final ZIP
                    </button>
                  </div>
                </div>
                <div className="export-preview-stage">
                  {exportPreviewUrl ? <img src={exportPreviewUrl} /> : <div className="empty">Preview 없음</div>}
                </div>
              </section>
            </section>
          </div>
        ) : null}
      </main>

      <aside className={`tools ${toolsOpen ? "open" : "collapsed"}`}>
        <div className="tools-head">
          <h2>{toolsOpen ? "Workspace" : "Work"}</h2>
          <button className="icon-button" onClick={() => setToolsOpen(!toolsOpen)} title={toolsOpen ? "패널 접기" : "패널 열기"}>
            {toolsOpen ? "›" : "‹"}
          </button>
        </div>

        {toolsOpen ? <>
        {activeTab === "overlay" ? <>
        <div className="section compact-section">
          <div className="row">
            <button className="primary" onClick={applyOverlay} disabled={overlayBusy || !canRenderOverlayImage(selected)} title={`Overlay 적용 (${shortcutById("overlay.apply")})`}>Apply Overlay</button>
            <button onClick={() => runFinal(true)} disabled={ocrBusy || !selected} title="현재 Additional Translation Prompt를 사용해 OCR/번역부터 다시 실행합니다.">Re-run OCR</button>
          </div>
          <label className="auto-preview-toggle">
            <input
              type="checkbox"
              checked={autoOverlayPreview}
              onChange={(event) => setAutoOverlayPreview(event.target.checked)}
            />
            <span>Auto preview 300ms</span>
          </label>
        </div>
        <div className="section selected-overlay-section">
          <div className="selected-overlay-grid">
            <div className="selected-overlay-pane overlay-region-section">
              <div className="side-section-head">
                <h2>Selected Region</h2>
              </div>
              {selectedRegion ? renderOverlayRegionCard(selectedRegion) : <div className="empty compact-empty">Region을 선택하세요</div>}
            </div>
            <div className="selected-overlay-pane overlay-style-section">
              <div className="side-section-head overlay-style-head">
                <h2>Selected Style</h2>
                <div className="font-size-control compact-font-size">
                  <button onClick={() => adjustOverlayFontSize(selectedRegionRefValue(), -1)} disabled={!selectedRegionRefValue()}>−</button>
                  <span>Size {selectedFontSize || "-"}</span>
                  <button onClick={() => adjustOverlayFontSize(selectedRegionRefValue(), 1)} disabled={!selectedRegionRefValue()}>+</button>
                </div>
              </div>
              <div className="font-style-toolbar">
                {FONT_STYLES.map(([id, _short, label, sample]) => (
                  <button
                    key={id}
                    className={selectedEffectiveFontStyle === id ? "active" : ""}
                    onClick={() => selectedRegionRefValue() && updateOverlayRegion(selectedRegionRefValue(), overlayFontStylePatch(id))}
                    disabled={!selectedRegionRefValue()}
                    title={`${label} (${shortcutById(`overlay.style.${id}`)})`}
                  >
                    <span className={`font-style-sample sample-${id}`}>{sample}</span>
                    <span className="font-style-name">{label}</span>
                  </button>
                ))}
              </div>
              <div className="overlay-style-fields">
                {selectedEffectiveFontStyle === "etc" ? (
                  <>
                    <div className="field compact-field">
                      <label>Etc font</label>
                      <select
                        value={selectedOverlayOverride.font_path || ""}
                        onChange={(e) => selectedRegionRefValue() && updateOverlayRegion(selectedRegionRefValue(), { font_path: e.target.value })}
                        disabled={!selectedRegionRefValue()}
                      >
                        <option value="">Default</option>
                        {fonts.map((font) => <option key={font.path} value={font.path}>{font.name}</option>)}
                      </select>
                    </div>
                    <div className="field compact-field">
                      <label>Weight</label>
                      <select
                        value={selectedOverlayOverride.font_weight || ""}
                        onChange={(e) => selectedRegionRefValue() && updateOverlayRegion(selectedRegionRefValue(), { font_weight: e.target.value })}
                        disabled={!selectedRegionRefValue()}
                      >
                        <option value="">Default</option>
                        {[100, 200, 300, 400, 500, 600, 700, 800, 900].map((weight) => (
                          <option key={weight} value={weight}>{weight}</option>
                        ))}
                      </select>
                    </div>
                  </>
                ) : (
                  <div className="field compact-field">
                    <label>Overlay font</label>
                    <select value={fontPath} onChange={(e) => updateFontPath(e.target.value)}>
                      <option value={fontPath}>{fontPath ? "Default" : "Font"}</option>
                      {fonts.map((font) => <option key={font.path} value={font.path}>{font.name}</option>)}
                    </select>
                  </div>
                )}
                <div className="field compact-field">
                  <label>Outline</label>
                  <input
                    type="number"
                    min="0"
                    max="12"
                    value={selectedStrokeWidth}
                    onChange={(e) => selectedRegionRefValue() && updateOverlayRegion(selectedRegionRefValue(), { stroke_width: e.target.value }, `stroke:${selectedRegionRefValue()}`)}
                    disabled={!selectedRegionRefValue()}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="section compact-section">
          <div className="region-quick-list">
            <div className="region-quick-head">
              <strong>Region List</strong>
              <span>{overlayRegions.length}</span>
            </div>
            {orderedOverlayRegions.length ? orderedOverlayRegions.map((region) => {
              const rid = overlayItemKey(region);
              const override = overlayRegionValue(overlayGuidance, rid);
              const style = override.font_style || inferredRegionFontStyle(selected, region);
              const styleShort = FONT_STYLES.find(([id]) => id === style)?.[1] || "N";
              const text = (override.text_override ?? baseRegionText(selected, region)).replace(/\s+/g, " ").trim();
              return (
                <button
                  key={rid}
                  className={`region-quick-item ${selectedRegionRefValue() === rid ? "active" : ""}`}
                  onClick={() => setSelectedRegionTracked(rid)}
                >
                  <strong>{overlayItemLabel(region)}</strong>
                  <span>{styleShort}</span>
                  <em>{text || "텍스트 없음"}</em>
                </button>
              );
            }) : <div className="empty compact-empty">Region 없음</div>}
          </div>
        </div>
        </> : null}

        {activeTab === "restore" ? <RestoreControls restore={restore} /> : null}

        {activeTab === "guidance" ? <>
        <div className="section compact-section">
          <h2>Actions</h2>
          <div className="side-action-grid">
            <button onClick={clearUnsavedGuidance} disabled={guidanceBusy || analyzeBusy || !selected}>Clear Unsaved</button>
            <button className="danger" onClick={clearGuidanceOutputs} disabled={guidanceBusy || analyzeBusy || !selected}>Delete Outputs</button>
            <button className="primary wide" onClick={applyGuidance} disabled={guidanceBusy || analyzeBusy || !selected} title={`말풍선 분석 적용 (${shortcutById("guidance.apply")})`}>Apply Analysis</button>
          </div>
        </div>

        <div className="section">
          <div className="history-head side-history-head">
            <strong>Action History</strong>
            <div>
              <button className="icon-button" onClick={undoGuidance} disabled={!guidanceUndoStack.length && !guidanceHistory.length} title={`Undo (${shortcutById("edit.undo")})`}>↶</button>
              <button className="icon-button" onClick={redoGuidance} disabled={!guidanceRedoStack.length} title={`Redo (${shortcutById("edit.redo")})`}>↷</button>
              <button className="icon-button" onClick={clearUnsavedGuidance} title="화면 guidance 초기화">×</button>
            </div>
          </div>
          {guidanceHistory.length ? (
            <div className="history-list side-history-list">
              {guidanceHistory.slice().reverse().map((item) => (
                <div key={item.key} className="history-row-wrap">
                  <button className="history-row" title={`${item.detail || item.label} 제거`} onClick={() => removeGuidanceHistoryItem(item)}>
                    <span>{item.label}</span>
                    {item.detail ? <em>{item.detail}</em> : null}
                  </button>
                  {item.isUbox ? (
                    <button
                      className={`history-engine-toggle${item.forcedLama ? " active" : ""}`}
                      title={item.forcedLama ? "LaMa 강제 해제 (배경에 따라 자동 선택)" : "이 Ubox를 LaMa로 지우기"}
                      onClick={() => toggleUboxForcedLama(item)}
                    >
                      LaMa
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="history-empty side-history-empty">No guidance yet</div>
          )}
        </div>

        <div className="section">
          <h2>Layers</h2>
          <div className="toggles">
            {Object.entries({ tg: "TG", regions: "Regions", guidance: "가이드" }).map(([key, label]) => (
              <label className="check" key={key}>
                <input type="checkbox" checked={layers[key]} onChange={(e) => setLayers({ ...layers, [key]: e.target.checked })} />
                {label}
              </label>
            ))}
          </div>
        </div>

        <div className="section">
          <h2>Selection</h2>
          <div className="metric">
            <strong>{selectedTg ? `TG${selectedTg}` : "-"}</strong>
            <span>selected TG</span>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={() => selectedTg && setGuidanceTracked({ ...guidance, disabled_tg_ids: Array.from(new Set([...(guidance.disabled_tg_ids || []), selectedTg])).sort((a, b) => a - b) }, `TG${selectedTg} off`)}>
              Off
            </button>
            <button onClick={() => selectedTg && setGuidanceTracked({ ...guidance, disabled_tg_ids: (guidance.disabled_tg_ids || []).filter((id) => id !== selectedTg) }, `TG${selectedTg} on`)}>
              On
            </button>
          </div>
        </div>

        <div className="section">
          <h2>말풍선 분석 JSON</h2>
          <textarea
            value={JSON.stringify(guidance, null, 2)}
            onChange={(e) => {
              try {
                setGuidanceTracked(JSON.parse(e.target.value), "Edit guidance JSON");
              } catch {
                setStatus("JSON 형식 오류");
              }
            }}
            style={{ minHeight: 280 }}
          />
        </div>
        </> : null}

        {activeTab === "ocr" ? <>
        <div className="section compact-section">
          <h2>OCR Queue</h2>
          <div className="metric-grid">
            <div className="metric"><strong>{ocrNeedsAttentionImages.length}</strong><span>retry</span></div>
            <div className="metric"><strong>{ocrReadyImages.length}</strong><span>ready</span></div>
            <div className="metric"><strong>{successImages.length}</strong><span>success</span></div>
            <div className="metric"><strong>{analyzeWorkers}</strong><span>workers</span></div>
          </div>
        </div>
        <div className="section ocr-log-section">
          <div className="side-section-head">
            <h2>OCR Log</h2>
            <span>{ocrBusy ? "running" : "idle"}</span>
          </div>
          <div className="log side-log">{ocrLog || status || "대기 중"}</div>
        </div>
        </> : null}
        {activeTab === "export" ? <>
        <div className="section export-log-section">
          <div className="side-section-head">
            <h2>Export Log</h2>
            <span>{exportBusy ? "running" : "idle"}</span>
          </div>
          <div className="log side-log">{exportLog || status || "대기 중"}</div>
        </div>
        <div className="section export-config-section">
          <div className="side-section-head">
            <h2>AI Recovery</h2>
            <span>{selectedExportMissingIds.length}/{missingFinalImages.length}</span>
          </div>
          <div className="export-side-config">
            <label className="field compact-field">
              <span>Image model</span>
              <input value={exportImageModel} onChange={(event) => setExportImageModel(event.target.value)} />
            </label>
            <div className="export-side-options">
              <label className="field compact-field">
                <span>Quality</span>
                <select value={exportImageQuality} onChange={(event) => setExportImageQuality(event.target.value)}>
                  {["low", "medium", "high", "auto"].map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label className="field compact-field">
                <span>Size</span>
                <select value={exportImageSize} onChange={(event) => setExportImageSize(event.target.value)}>
                  <option value="match_input">Match input 16px</option>
                  {["auto", "1024x1024", "1024x1536", "1536x1024", "2048x2048", "2048x1152", "3840x2160", "2160x3840"].map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label className="field compact-field">
                <span>Format</span>
                <select value={exportOutputFormat} onChange={(event) => setExportOutputFormat(event.target.value)}>
                  {["jpeg", "png", "webp"].map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label className="field compact-field">
                <span>Compression</span>
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={exportOutputCompression}
                  disabled={exportOutputFormat === "png"}
                  onChange={(event) => setExportOutputCompression(Number(event.target.value || 0))}
                />
              </label>
            </div>
            <label className="field compact-field">
              <span>Text guide model</span>
              <input value={exportTextModel} onChange={(event) => setExportTextModel(event.target.value)} disabled={!exportAnalyzeFirst} />
            </label>
            <label className="auto-preview-toggle">
              <input
                type="checkbox"
                checked={exportAnalyzeFirst}
                onChange={(event) => setExportAnalyzeFirst(event.target.checked)}
              />
              <span>번역/레이아웃 가이드 추출</span>
            </label>
            <label className="field compact-field">
              <span>Prompt guide</span>
              <textarea
                className="export-prompt-guide"
                value={exportPromptGuide}
                onChange={(event) => setExportPromptGuide(event.target.value)}
                placeholder="작품명, 인물 말투, 고유명사, 번역 톤, 레이아웃 유지 지시 등을 적어주세요."
              />
            </label>
            <button
              className="primary wide"
              onClick={generateMissingFinals}
              disabled={anyBusy || !selectedExportMissingIds.length}
              title={selectedExportMissingIds.length ? "선택한 후보만 이미지 생성" : "왼쪽 후보를 먼저 선택하세요"}
            >
              Generate Selected {selectedExportMissingIds.length ? `(${selectedExportMissingIds.length})` : ""}
            </button>
          </div>
        </div>
        <div className="section">
          <h2>Selected Fallback</h2>
          <div className="image-status-list">
            {selectedExportMissingIds.length ? missingFinalImages.filter((img) => selectedExportMissingIds.includes(img.id)).map((img) => (
              <button key={img.id} className="image-status warning" onClick={() => { selectImage(img); setActiveTab("overlay"); }}>
                <span>{img.name || img.id}</span>
                <strong>selected</strong>
              </button>
            )) : <div className="empty compact-empty">선택된 후보 없음</div>}
          </div>
        </div>
        <div className="section compact-section export-summary-section">
          <h2>Export Summary</h2>
          <div className="metric-grid">
            <div className="metric"><strong>{finalImages.length}</strong><span>ready</span></div>
            <div className="metric"><strong>{unresolvedFinalImages.length}</strong><span>missing final</span></div>
            <div className="metric"><strong>{missingFinalImages.length}</strong><span>fallback candidates</span></div>
            <div className="metric"><strong>{selectedExportMissingIds.length}</strong><span>selected</span></div>
          </div>
        </div>
        </> : null}
        </> : null}
      </aside>

      <footer className="status-bar">
        {activeTab === "ocr"
          ? `System Status: ${ocrBusy ? "Active" : "Idle"} | Workers: ${analyzeWorkers} | Needs Attention: ${needsAttentionImages.length} | Success: ${successImages.length}`
          : activeTab === "overlay"
            ? `System Status: ${overlayBusy ? "Rendering" : "Idle"} | File: ${selected?.name || "-"} | Region: ${selectedRegion ? overlayItemLabel(selectedRegion) : "-"}`
            : activeTab === "export"
              ? `System Status: ${anyBusy ? "Active" : "Idle"} | Final: ${finalImages.length}/${images.length} | Session: ${sessionId || "-"}`
            : activeTab === "restore"
              ? `System Status: ${restore.hasActiveJob ? "Restoring" : "Idle"} | File: ${selected?.name || "-"} | Mask: ${restore.maskPixels}px | Status: ${status || "-"}`
            : `System Status: ${guidanceBusy || analyzeBusy ? "Active" : "Idle"} | Tool: ${TOOL_LABELS.find(([id]) => id === tool)?.[1] || tool} | File: ${selected?.name || "-"}`}
      </footer>
      {anyBusy ? (
        <div className="task-progress-toast" role="status" aria-live="polite">
          <strong>{activeTaskLabel || "작업 진행 중"}</strong>
          <span>{selected?.name || "현재 이미지"}</span>
          <div className="task-progress-bar"><i /></div>
        </div>
      ) : null}
      {shortcutToast ? <div className="shortcut-toast" role="status" aria-live="polite">{shortcutToast}</div> : null}
      <ShortcutHelp open={helpOpen} activeTab={activeTab} onClose={() => setHelpOpen(false)} />
      {guidanceNotice ? (
        <div className="notice-backdrop" role="presentation" onClick={() => setGuidanceNotice(null)}>
          <div className="notice-dialog" role="dialog" aria-modal="true" aria-label={guidanceNotice.title} onClick={(event) => event.stopPropagation()}>
            <div className="notice-head">
              <strong>{guidanceNotice.title}</strong>
              <button className="icon-button" onClick={() => setGuidanceNotice(null)} title="닫기">×</button>
            </div>
            <div className="notice-list">
              {guidanceNotice.notices.map((item) => (
                <div className="notice-item" key={item.key}>
                  <strong>{item.title}</strong>
                  <p>{item.message}</p>
                  <em>{item.detail}</em>
                </div>
              ))}
            </div>
            <button className="primary wide" onClick={() => setGuidanceNotice(null)}>확인</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
