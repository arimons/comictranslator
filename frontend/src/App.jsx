import { useEffect, useMemo, useRef, useState } from "react";
import EditorCanvas from "./EditorCanvas.jsx";
import OverlayCanvas from "./OverlayCanvas.jsx";
import RegionTypeBadge from "./RegionTypeBadge.jsx";
import { TEXT_BASED, regionProcessingBasis, regionTypeLabel, regionDetectionLabel, regionBindingKey } from "./regionTypes.js";
import ShortcutHelp from "./ShortcutHelp.jsx";
import SourceLinks from "./SourceLinks.jsx";
import StartScreen from "./StartScreen.jsx";
import ImagePreview from "./ImagePreview.jsx";
import GuidanceToolIcon from "./GuidanceToolIcon.jsx";
import OcrBatchPrompt, { OCR_PROMPT_MILESTONES } from "./OcrBatchPrompt.jsx";
import ImageGenerationPanel from "./ImageGenerationPanel.jsx";
import AiErasePanel from "./AiErasePanel.jsx";
import ServerStatus from "./ServerStatus.jsx";
import PagePreparation from "./PagePreparation.jsx";
import GlossaryPanel from "./GlossaryPanel.jsx";
import SpeakerPicker from "./SpeakerPicker.jsx";
import DialogueList, { dialogueFilterMatches } from "./DialogueList.jsx";
import FloatingPanel from "./FloatingPanel.jsx";
import { KEYMAP, eventMatches, entryInContext, entryVisibleOnPlatform, shortcutFor, shortcutById } from "./keymap.js";
import {
  TOOL_LABELS,
  overlayRegionColor,
  imageGroups,
  tgId,
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
  overlayGuidancePayload,
  baseRegionText,
  regionCluster,
  inferredRegionFontStyle,
  inferredRegionWritingMode,
  overlayWritingModeChoice,
  overlayItemFontSize,
  overlayRegionValue,
  overlayRegionBaseKey,
  readSessionSettings,
  writeSessionSettings,
  removeSessionSettings,
  imageRegions,
} from "./lib.js";
import { recordTargetsRegion, regionIsExcluded } from "./regionExclusion.js";

// 글자 입히기 편집 기록 상한과, 같은 종류의 연속 변경을 한 칸으로 묶는 시간
const OVERLAY_HISTORY_LIMIT = 100;
const OVERLAY_HISTORY_MERGE_MS = 1500;

const ENGINE_NAMES = { lama: "LaMa", telea: "Telea" };
// 그리기 도구를 고르면 캔버스 아래에 띄우는 안내. 도구를 한 번 쓰면 선택 도구로 돌아가며 함께 사라진다.
const TOOL_HINTS = {
  "tg-split": "나눌 TG 위로 선을 그으세요",
  wall: "글자 영역이 번지는 곳을 상자로 드래그해 막으세요",
  bubble: "말풍선 안쪽 빈 곳을 클릭하세요",
  "region-split": "나눌 경계를 따라 선을 그으세요",
  ubox: "글자를 감싸도록 상자를 드래그하세요",
  mask: "놓친 곳을 칠하세요(흰칠 + OCR 반영) · 드래그는 붓, 클릭은 다각형(첫 점·Enter로 닫기), Shift+드래그는 사각형",
};
// 지우기 보정 붓 굵기(원본 px) 단계. [ ]로 한 단계씩 바꾼다.
const MASK_BRUSH_STEPS = [2, 4, 6, 8, 12, 16, 24, 32, 48, 64];
const RESUME_KEY = "comic-translator-resume-after-restart";
// 새로고침해도 열려 있던 프로젝트로 돌아가도록 탭 단위(sessionStorage)로 기억한다.
const OPEN_WORKSPACE_KEY = "comic-translator-open-workspace";

// 브라우저 저장소는 막혀 있을 수 있어 모든 읽기·쓰기를 감싼다. 값이 없거나 올바르지 않으면 기본값을 쓴다.
function readStored(key, fallback, valid = () => true) {
  try {
    const value = JSON.parse(window.localStorage.getItem(key));
    return value !== null && valid(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* 저장소를 쓸 수 없어도 기능은 동작한다 */ }
}

// 화자·번역 메모는 영역 참조(region_ref)로 저장한다. 참조를 주지 않는 구버전 응답만 구형 영역 키를 쓴다.
const regionStoreKey = (region) => region?.region_ref || regionBindingKey(region);

export default function App() {
  const [sessionId, setSessionId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [images, setImages] = useState([]);
  // 선택한 페이지의 전체 레코드(regions, integrated 등). 목록(images)은 요약만 담는다.
  const [pageDetail, setPageDetail] = useState(null);
  const [recentSessions, setRecentSessions] = useState([]);
  const [recentLoadState, setRecentLoadState] = useState("loading");
  const [selectedId, setSelectedId] = useState("");
  const [folderPath, setFolderPath] = useState("");
  const [status, setStatus] = useState("");
  const [ocrLog, setOcrLog] = useState("");
  const [exportLog, setExportLog] = useState("");
  const [busyTasks, setBusyTasks] = useState({});
  const taskLocksRef = useRef(new Set());
  const [activeTab, setActiveTab] = useState("guidance");
  const [workspaceStarted, setWorkspaceStarted] = useState(false);
  const generationReviewMode = Boolean(new URLSearchParams(window.location.search).get("generation_review"));
  useEffect(() => {
    // 스크린샷용 임시 hash를 남기지 않고 현재 화면 이름을 표시한다.
    const hash = workspaceStarted ? "#editor" : "#home";
    if (window.location.hash !== hash) window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
  }, [workspaceStarted]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [tool, setTool] = useState("select");
  const [maskBrushWidth, setMaskBrushWidth] = useState(12);
  const [selectedTg, setSelectedTg] = useState(null);
  const [selectedGuidanceRegionId, setSelectedGuidanceRegionId] = useState(null);
  const [selectedRegionId, setSelectedRegionId] = useState(null);
  // Shift/Cmd+클릭으로 더한 추가 선택. 글꼴·크기 같은 공통 스타일만 한꺼번에 바꾼다.
  const [extraSelectedIds, setExtraSelectedIds] = useState([]);
  const [guidance, setGuidance] = useState(defaultGuidance(null));
  const [overlayGuidance, setOverlayGuidance] = useState(defaultOverlayGuidance(null));
  const [layers, setLayers] = useState({ tg: true, regions: true, guidance: true });
  const [extraPrompt, setExtraPrompt] = useState("");
  const [ocrModels, setOcrModels] = useState([]);
  const [ocrDefaultModel, setOcrDefaultModel] = useState("");
  const [imageModels, setImageModels] = useState({ openai: "gpt-image-2.5-sunburst" });
  const [ocrModel, setOcrModel] = useState("");
  const [fonts, setFonts] = useState([]);
  const [fontPath, setFontPath] = useState("");
  const [analyzeWorkers, setAnalyzeWorkers] = useState(4);
  const [analysisPolicy, setAnalysisPolicy] = useState("reuse");
  const [importNameFocusRequest, setImportNameFocusRequest] = useState(0);
  const [imageStatuses, setImageStatuses] = useState({});
  const [clearOutputsConfirm, setClearOutputsConfirm] = useState(false);
  const [routeBusy, setRouteBusy] = useState(false);
  const [generationStatus, setGenerationStatus] = useState(null);
  const [guidanceCompareMode, setGuidanceCompareMode] = useState("auto");
  const [aiEraseCandidateUrl, setAiEraseCandidateUrl] = useState(null);
  const [aiEraseConfirmOpen, setAiEraseConfirmOpen] = useState(false);
  // 불러온 가로 이미지를 분석 시작 때 나눌지(기본 켜짐). 표지처럼 가운데 한 장인 이미지는 가운데만 남긴다.
  const [splitLandscape, setSplitLandscape] = useState(() => readStored("ct.splitLandscape", true, (v) => typeof v === "boolean"));
  // 확인한 페이지(영역 분석 탭에서 보고 넘어간 페이지)가 쌓이면 번역을 백그라운드로 돌릴지 묻는다.
  const [reviewedIds, setReviewedIds] = useState(() => new Set());
  const [ocrPromptOff, setOcrPromptOff] = useState(() => readStored("ct.ocrPromptOff", false, (v) => typeof v === "boolean"));
  // 알림을 이미 처리한 가장 높은 진행률 기준(0, 60, 80). 세션별로 기억한다.
  const [ocrMilestone, setOcrMilestone] = useState(0);
  // 대사 검수 상태: `${페이지 id}|${overlay 키}` -> { status: "ok"|"ask", text }. text는 검수 당시 번역문이라 번역이 바뀌면 미검수로 돌아간다.
  const [dialogueReview, setDialogueReview] = useState({});
  const [dialogueFilter, setDialogueFilter] = useState("all");
  // 프로젝트별 '앞으로 묻지 않고 번역' 표시. LLM 비용이 드는 일이라 다른 프로젝트로 번지지 않게 세션 단위로 둔다.
  const [ocrAutoStart, setOcrAutoStart] = useState(false);
  const ocrAutoStartingRef = useRef(false);
  // 대사 편집을 ⌘Enter로 확정한 영역. 반영된 새 번역문으로 확인을 다시 기록한다.
  const dialogueConfirmAfterCommitRef = useRef(null);
  const [bgOcr, setBgOcr] = useState({ running: false, total: 0, done: 0, failed: 0, ids: [] });
  const prevPageRef = useRef({ id: null, ready: false });
  const [autoOverlayPreview, setAutoOverlayPreview] = useState(true);
  const [exportImageModel, setExportImageModel] = useState("gpt-image-2.5-sunburst");
  const [exportTextModel, setExportTextModel] = useState("gpt-6.1-terra");
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
  // 작품 메모(인물 표)와 현재 페이지의 TG별 화자 지정
  const [workMemo, setWorkMemo] = useState(null);
  const [speakerByTg, setSpeakerByTg] = useState({});
  const [speakerByRegion, setSpeakerByRegion] = useState({});
  // R별 번역 메모(영역 키 → 글). 다음 OCR/번역 때 '영역별 번역 가이드'로 붙는다.
  const [contextByRegion, setContextByRegion] = useState({});
  const [contextDraft, setContextDraft] = useState(null);
  const [speakerPickerOpen, setSpeakerPickerOpen] = useState(false);
  // 영역 분석 상단 바의 펼침 메뉴("history" | "more" | null). 바깥을 누르면 닫는다.
  const [guidanceMenu, setGuidanceMenu] = useState(null);
  const guidanceMenuRef = useRef(null);
  useEffect(() => {
    if (!guidanceMenu) return undefined;
    const onDown = (event) => { if (!guidanceMenuRef.current?.contains(event.target)) setGuidanceMenu(null); };
    const onKey = (event) => { if (event.key === "Escape") setGuidanceMenu(null); };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [guidanceMenu]);
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
  // 다음 페이지 미리 분석: 한 번에 한 장만, 화면을 잠그지 않고 돈다
  const prefetchPromiseRef = useRef(null);
  const prefetchFailedRef = useRef(new Set());
  const [prefetchingId, setPrefetchingId] = useState("");
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
  // id는 파일명 stem이라 세션이 달라도 겹칠 수 있다. 세션별 input_path로 페이지를 구분한다.
  const selectedKey = selected ? (selected.input_path || selected.id) : "";
  // 페이지 전환 직후 요약만 있을 때와 상세가 도착했을 때를 구분해, 상세 도착 시 편집 상태를 다시 초기화한다.
  const guidanceDirty = Boolean(selected?.detail_loaded && !guidanceEquals(guidance, defaultGuidance(selected)));
  const selectedDetailKey = selected ? `${selectedKey}|${selected.detail_loaded ? "detail" : "summary"}` : "";
  const effectiveOcrModel = ocrModels.some((model) => model.id === ocrModel) ? ocrModel : ocrDefaultModel;
  // 이미지 작업(글자 지우기·통번역 재생성)은 OCR·번역 모델의 제공자를 따라간다
  const ocrProvider = ocrModels.find((model) => model.id === effectiveOcrModel)?.provider || "openai";
  const guidanceEditorUrl = selected?.input_url || null;
  const generatedCandidates = generationStatus?.session_id === sessionId && generationStatus?.image_id === selected?.id ? generationStatus.results || [] : [];
  const latestGeneratedCandidate = generatedCandidates.at(-1);
  const generationRecommended = selected?.analysis_route !== "standard" && (selected?.image_generation_recommendation?.priority === "recommended" || selected?.analysis_skip_reason === "cover_image_gen_redraw");
  // 처리 제외 페이지는 원본 그대로 출력하므로, 마스크·OCR 시트·최종 출력 모두 지우기 전 깨끗한 원본으로 보여 준다(분석 파일은 남겨 둔다).
  const pageExcluded = Boolean(selected?.processing_excluded);
  const guidanceCompareTabs = [
    ["ai_erase", "AI 지움 후보", pageExcluded ? null : aiEraseCandidateUrl],
    ["generated", "생성 결과", pageExcluded ? null : latestGeneratedCandidate?.preview_url],
    // 확정한 배경 복원 패치(AI 지움 포함)가 있으면 그것까지 얹은 현재 지우기 결과를 보여 준다.
    ["masked", "마스크", pageExcluded ? selected?.input_url : selected?.restore_preview_url || selected?.masked_url],
    ["source", "원본", selected?.input_url],
    ["final", "최종 출력", pageExcluded ? selected?.input_url : selected?.final_url],
    ["sheet", "OCR 시트", pageExcluded ? selected?.input_url : selected?.indexed_sheet_url],
  ];
  const preferredCompareMode = guidanceCompareMode === "auto" ? (aiEraseCandidateUrl ? "ai_erase" : latestGeneratedCandidate && generationRecommended ? "generated" : "masked") : guidanceCompareMode;
  const guidanceCompareTab = guidanceCompareTabs.find(([id, _label, url]) => id === preferredCompareMode && url) || guidanceCompareTabs.find(([_id, _label, url]) => url) || guidanceCompareTabs[0];
  const effectiveGuidanceCompareMode = guidanceCompareTab?.[0] || guidanceCompareMode;
  const guidanceCompareUrl = guidanceCompareTab?.[2] || (pageExcluded ? null : selected?.masked_url) || selected?.input_url || null;
  useEffect(() => { setGuidanceCompareMode("auto"); setClearOutputsConfirm(false); }, [selectedKey]);
  const selectedToolLabel = TOOL_LABELS.find(([id]) => id === tool)?.[1] || tool;
  const disabledCount = guidance.disabled_tg_ids?.length || 0;
  const splitCount = guidance.tg_splits?.length || 0;
  const textBasedGuideCount = (guidance.manual_regions || []).filter((item) => regionProcessingBasis(item) === TEXT_BASED).length;
  const suppressedCount = guidance.suppressed_regions?.length || 0;
  const overlayRegions = overlayControlItems(selected, overlayGuidance);
  // 수동값 키는 영역 참조라 화면에는 현재 R 번호 라벨로 바꿔 보여 준다.
  const overlayKeyLabel = (key) => {
    const item = overlayRegions.find((region) => overlayItemKey(region) === String(key));
    return item ? overlayItemLabel(item) : "R?";
  };
  // 화자 단축키 1-8 = 작품 메모 인물 표 순서(주인공 1번), 9 = 기타(전체 목록 팝업)
  const allSpeakerCharacters = workMemo?.characters || [];
  const speakerCharacters = allSpeakerCharacters.slice(0, 8);
  const guidanceRegions = useMemo(() => imageRegions(selected)
    .filter((region) => !regionIsExcluded(region, guidance))
    .sort((a, b) => regionId(a) - regionId(b)), [selected, guidance]);
  const speakerLabels = useMemo(() => {
    const names = Object.fromEntries((workMemo?.characters || []).map((row) => [row.id, row.name]));
    names.narration = "내레이션";
    const lead = (workMemo?.characters || []).find((row) => row.protagonist);
    const labels = {};
    guidanceRegions.forEach((region) => {
      const key = regionStoreKey(region);
      const assigned = Object.hasOwn(speakerByRegion,key) ? speakerByRegion[key] : (region.anchor_ids || []).map((id) => speakerByTg[String(id)]).find((id) => names[id]);
      // 텍스트 기반 영역의 자동 기본값은 괄호로 구분한다.
      if (assigned) labels[regionId(region)] = names[assigned];
      else if (lead && regionProcessingBasis(region) === TEXT_BASED) labels[regionId(region)] = `(${lead.name})`;
    });
    return labels;
  }, [workMemo, speakerByTg, speakerByRegion, guidanceRegions]);
  const selectedGuidanceRegion = selectedGuidanceRegionId != null
    ? guidanceRegions.find(region => regionId(region) === Number(selectedGuidanceRegionId)) || null
    : null;
  // 재분석으로 R 번호가 바뀌어도 선택은 같은 대사(region_ref)를 따라간다. 따라갈 영역이 없으면 다른 번호에 붙이지 않고 선택을 푼다.
  const selectedGuidanceRefRef = useRef(null);
  useEffect(() => {
    const wanted = selectedGuidanceRefRef.current;
    if (!wanted || selectedGuidanceRegionId == null) return;
    const current = guidanceRegions.find((region) => regionId(region) === Number(selectedGuidanceRegionId));
    if (current?.region_ref === wanted) return;
    const moved = guidanceRegions.find((region) => region.region_ref === wanted);
    if (moved) setSelectedGuidanceRegionId(regionId(moved));
    else if (guidanceRegions.some((region) => region.region_ref)) { selectedGuidanceRefRef.current = null; setSelectedGuidanceRegionId(null); }
  }, [guidanceRegions]);
  useEffect(() => {
    selectedGuidanceRefRef.current = selectedGuidanceRegion?.region_ref || null;
  }, [selectedGuidanceRegionId, selectedGuidanceRegion?.region_ref]);
  // 텍스트 기반 영역의 지우기 엔진: 자동 판정(Telea/LaMa) 또는 사용자가 LaMa·Telea로 고정.
  // 고정은 변경사항 적용 때 Step 3에서 반영되므로, 화면의 가이드와 저장된 가이드를 따로 본다.
  const selectedIsTextBased = Boolean(selectedGuidanceRegion && regionProcessingBasis(selectedGuidanceRegion) === TEXT_BASED);
  const selectedRegionKey = selectedGuidanceRegion ? regionBindingKey(selectedGuidanceRegion) : "";
  const engineOverrideOf = (source) => (source?.inpaint_engine_overrides || []).find((item) => selectedGuidanceRegion && recordTargetsRegion(item, selectedGuidanceRegion))?.engine || null;
  const enginePending = selectedIsTextBased ? engineOverrideOf(guidance) : null;
  const engineSaved = selectedIsTextBased ? engineOverrideOf(selected?.guidance) : null;
  const lamaForcedPending = enginePending === "lama";
  // 자동일 때 마지막 분석에서 실제로 쓰인 엔진을 함께 보여 준다.
  const autoEngineUsed = selectedIsTextBased && selectedGuidanceRegion.inpaint_engine_source !== "forced"
    ? ENGINE_NAMES[selectedGuidanceRegion.inpaint_engine_used] || ""
    : "";
  const selectedCvGroups = imageGroups(selected).filter((group) => (selectedGuidanceRegion?.anchor_ids || [selectedTg]).map(Number).includes(tgId(group)));
  const selectedCvText = selectedCvGroups.map((group) => group.text || (group.lines || []).map((line) => line.text || (line.fragments || []).map((fragment) => fragment.text || "").join("")).filter(Boolean).join("\n")).filter(Boolean).join("\n\n");
  const selectedPageId = selected?.id || "";

  useEffect(() => {
    setSpeakerByTg({});
    setSpeakerByRegion({});
    setContextByRegion({});
    if (!sessionId || !selectedPageId) return undefined;
    let alive = true;
    api(`/api/speakers/${encodeURIComponent(sessionId)}/${encodeURIComponent(selectedPageId)}`)
      .then((result) => { if (alive) { setSpeakerByTg(result.by_tg || {}); setSpeakerByRegion(result.by_region_ref || result.by_region || {}); setContextByRegion(result.context_by_region_ref || result.context_by_region || {}); } })
      .catch(() => {});
    return () => { alive = false; };
  }, [sessionId, selectedPageId]);
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
  // 여러 Region 선택: 존재하는 Region만 남기고 기본 선택을 맨 앞에 둔다.
  const groupKeys = selectedRegion
    ? [overlayItemKey(selectedRegion), ...extraSelectedIds.filter((id) => overlayRegions.some((region) => overlayItemKey(region) === id))]
    : [];
  const isGroupSelection = groupKeys.length > 1;
  const groupRegions = groupKeys.map((key) => overlayRegions.find((region) => overlayItemKey(region) === key)).filter(Boolean);
  const groupValue = (pick) => {
    const values = groupRegions.map((region) => pick(overlayRegionValue(overlayGuidance, overlayItemKey(region)), region));
    return values.every((value) => value === values[0]) ? values[0] : null;
  };
  const groupFontSize = isGroupSelection ? groupValue((o, region) => Number(o.font_size || overlayItemFontSize(region) || 0)) : null;
  const groupFontStyle = isGroupSelection ? groupValue((o, region) => o.font_style || inferredRegionFontStyle(selected, region)) : null;
  const statusLabel = { idle: "대기", unanalyzed: "분석 전", queued: "대기", running: "진행", done: "완료", failed: "실패", skipped: "대상 아님" };
  const inputBusy = Boolean(busyTasks.input);
  const analyzeBusy = Boolean(busyTasks.analyze);
  const guidanceBusy = Boolean(busyTasks.guidance);
  const ocrBusy = Boolean(busyTasks.ocr) || bgOcr.running;
  const overlayBusy = Boolean(busyTasks.overlay);
  const exportBusy = Boolean(busyTasks.export);
  // 백그라운드 번역 중에는 번역 중인 페이지만 편집을 잠근다(다른 페이지는 계속 편집할 수 있다).
  const selectedInBackgroundOcr = bgOcr.running && bgOcr.ids.includes(selected?.id);
  const anyBusy = routeBusy || Object.values(busyTasks).some(Boolean) || selectedInBackgroundOcr;
  const currentIndex = selected ? Math.max(0, images.findIndex((img) => img.id === selected.id)) : -1;
  const needsAttentionImages = images.filter((img) => {
    // 분석 전·대상 아님은 할 일이 남은 것이지 문제가 아니다. 실제로 실패한 페이지만 센다.
    return imageStatuses[img.id]?.status === "failed" || img.ocr_status?.status === "failed";
  });
  const ocrReadyImages = images.filter((img) => {
    const state = ocrStateForImage(img);
    return !img.processing_excluded && state === "idle" && Boolean(img.indexed_sheet_url) && !img.final_url;
  });
  // 확인한 페이지: 영역 분석 탭에서 분석이 끝난 페이지를 적용하지 않은 변경 없이 넘기면 센다.
  // 세션별로 브라우저에 기억해 새로고침해도 이어진다. 저장소를 못 쓰면 이번 화면에서만 센다.
  useEffect(() => {
    setReviewedIds(new Set(readStored(`ct.reviewed.${sessionId || ""}`, [], Array.isArray)));
    setOcrAutoStart(Boolean(sessionId) && readStored(`ct.ocrAutoStart.${sessionId}`, false, (v) => typeof v === "boolean"));
    setDialogueReview(readStored(`ct.dialogueReview.${sessionId || ""}`, {}, (v) => v && typeof v === "object" && !Array.isArray(v)));
    setOcrMilestone(Number(readStored(`ct.ocrMilestone.${sessionId || ""}`, 0, (v) => OCR_PROMPT_MILESTONES.includes(v) || v === 0)) || 0);
  }, [sessionId]);
  useEffect(() => {
    const prev = prevPageRef.current;
    if (prev.id && prev.sid === sessionId && prev.id !== selected?.id && prev.ready) {
      setReviewedIds((current) => {
        if (current.has(prev.id)) return current;
        const next = new Set(current).add(prev.id);
        writeStored(`ct.reviewed.${sessionId || ""}`, [...next]);
        return next;
      });
    }
    prevPageRef.current = {
      sid: sessionId,
      id: selected?.id || null,
      ready: Boolean(activeTab === "guidance" && selected?.detail_loaded && selected?.indexed_sheet_url && !selected?.processing_excluded && !guidanceDirty),
    };
  });
  const ocrBatchCandidates = ocrReadyImages.filter((img) => reviewedIds.has(img.id) && img.id !== selected?.id);
  // 수동 작업 진행률: 처리 대상 페이지 중 확인한 페이지 비율. 60%, 80%를 넘을 때 한 번씩 묻는다.
  const ocrTargetImages = images.filter((img) => !img.processing_excluded);
  const ocrProgressPct = ocrTargetImages.length
    ? Math.round((ocrTargetImages.filter((img) => reviewedIds.has(img.id)).length / ocrTargetImages.length) * 100)
    : 0;
  const ocrReachedMilestone = Math.max(0, ...OCR_PROMPT_MILESTONES.filter((value) => ocrProgressPct >= value));
  const showOcrPrompt = !ocrPromptOff && !bgOcr.running && !ocrBusy
    && ocrReachedMilestone > ocrMilestone && ocrBatchCandidates.length > 0;
  useEffect(() => {
    if (!ocrAutoStart || !showOcrPrompt || ocrAutoStartingRef.current) return;
    ocrAutoStartingRef.current = true;
    startBackgroundOcr().finally(() => { ocrAutoStartingRef.current = false; });
  }, [ocrAutoStart, showOcrPrompt]);
  useEffect(() => {
    const rid = dialogueConfirmAfterCommitRef.current;
    if (!rid || !selected) return;
    dialogueConfirmAfterCommitRef.current = null;
    const row = dialogueRows().find((item) => item.rid === String(rid));
    if (row && row.status !== "ok") setDialogueStatus(rid, "ok", { toggle: false });
  });
  // 대기열: 바로 번역할 수 있는 페이지 다음에 영역 분석 전 페이지를 둔다
  const ocrUnanalyzedImages = images.filter((img) => ocrStateForImage(img) === "unanalyzed");
  const ocrQueueImages = [...ocrReadyImages, ...ocrUnanalyzedImages];
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
  const missingFinalImages = images.filter((img) => !img.processing_excluded && (!img.final_url || isImageGenerationFallback(img)));
  const selectedExportMissingIds = exportGenerateIds.filter((id) => missingFinalImages.some((img) => img.id === id));
  const exportFocusedImage = images.find((img) => img.id === exportFocusedId) || missingFinalImages[0] || selected;
  const exportFocusedIsGenerated = isImageGenerationFallback(exportFocusedImage);
  const exportPreviewUrl = exportFocusedImage?.final_url || exportFocusedImage?.input_url || "";
  // Region List는 Tab 순서와 같은 읽는 순서(파이프라인이 매긴 region id 순)로 고정한다.
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
    setExtraSelectedIds((items) => (items.length ? [] : items));
  }

  // 여러 Region 선택에 더하거나 뺀다. 첫 번째가 기본 선택(Selected Region 표시)이다.
  function toggleRegionInGroup(rid) {
    const key = rid ? String(rid) : "";
    if (!key) return;
    const primary = selectedRegionIdRef.current ? String(selectedRegionIdRef.current) : "";
    if (!primary) {
      selectedRegionIdRef.current = key;
      setSelectedRegionId(key);
      return;
    }
    if (key === primary) {
      const [nextPrimary, ...rest] = extraSelectedIds;
      if (!nextPrimary) return;
      selectedRegionIdRef.current = nextPrimary;
      setSelectedRegionId(nextPrimary);
      setExtraSelectedIds(rest);
      return;
    }
    setExtraSelectedIds((items) => (items.includes(key) ? items.filter((item) => item !== key) : [...items, key]));
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

  // 여러 Region에 같은 스타일 patch를 한 번의 편집(Undo 한 단계)으로 적용한다. 글자 내용은 건드리지 않는다.
  function updateOverlayRegions(keys, patch, historyKey = null) {
    const targets = [...new Set(keys.filter(Boolean).map(String))];
    if (!targets.length) return;
    setOverlayGuidanceTracked((current) => {
      const regions = { ...((current || {}).regions || {}) };
      targets.forEach((key) => {
        const previousRegion = normalizeOverlayRegionOverride(regions[key] || {});
        const patchValue = typeof patch === "function" ? patch(previousRegion, key) : patch;
        regions[key] = normalizeOverlayRegionOverride({ ...previousRegion, ...(patchValue || {}) });
      });
      return { ...(current || { regions: {} }), regions };
    }, { historyKey });
  }

  function setOverlayRegionSegmentMode(region, mode) {
    if (!region || !["merge", "split"].includes(mode)) return;
    const key = overlayRegionBaseKey(region);
    updateOverlayRegion(key, { segment_mode: mode });
    setStatus(`R${regionId(region)} segment ${mode}`);
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
    showShortcutToast(`${region ? overlayItemLabel(region) : overlayKeyLabel(rid)} 쓰기 방향: ${WRITING_MODE_LABELS[next]}`);
  }

  // Delete: 위치·크기·회전 수동값만 자동으로 되돌린다. 대사와 스타일은 유지한다.
  function resetOverlayRegionLayout(rid) {
    if (!rid) return false;
    const region = overlayRegions.find((item) => overlayItemKey(item) === String(rid));
    const override = overlayRegionValue(overlayGuidance, rid);
    const label = region ? overlayItemLabel(region) : overlayKeyLabel(rid);
    if (!override.safe_text_rect && !override.rotation_deg) {
      setStatus(`${label} 수동 위치·크기·회전 없음`);
      return true;
    }
    updateOverlayRegion(rid, { safe_text_rect: null, rotation_deg: 0 });
    setStatus(`${label} 위치·크기·회전 자동으로 초기화`);
    return true;
  }

  function selectGuidanceRegion(region, index, total) {
    setSelectedTg(null);
    setSelectedGuidanceRegionId(regionId(region));
    editorCanvasRef.current?.ensureRegionVisible(region);
    setStatus(`R${regionId(region)} 선택 (${index + 1}/${total})`);
    return true;
  }

  // 영역 분석 탭: 읽는 순서(region 번호)로 이동. onlyBoundary면 경계 기반 영역만 고른다.
  // noWrap: 화자 지정 뒤 다음 R로 넘어갈 때는 마지막 R에서 처음으로 돌아가지 않는다
  function cycleGuidanceRegion(step, noWrap = false) {
    const list = guidanceRegions;
    if (!list.length) return false;
    const current = selectedGuidanceRegion ? regionId(selectedGuidanceRegion) : null;
    let next;
    if (current == null) next = step > 0 ? list[0] : list[list.length - 1];
    else if (step > 0) next = list.find((region) => regionId(region) > current);
    else next = [...list].reverse().find((region) => regionId(region) < current);
    if (!next) {
      if (noWrap) return false;
      next = step > 0 ? list[0] : list[list.length - 1];
    }
    return selectGuidanceRegion(next, list.indexOf(next), list.length);
  }

  async function assignSpeaker(slot) {
    const region = selectedGuidanceRegion;
    if (!region) {
      setStatus("화자를 지정할 영역을 먼저 선택하세요");
      return true;
    }
    let characterId = null;
    let name = "자동";
    if (slot === "narration") {
      characterId = "narration";
      name = "내레이션";
    } else if (slot === 9) {
      if (!allSpeakerCharacters.length) {
        setStatus(workMemo ? "작품 메모에 인물이 없습니다" : "작품 메모를 먼저 연결하세요");
        return true;
      }
      setSpeakerPickerOpen(true);
      return true;
    } else if (typeof slot === "object" && slot) {
      characterId = slot.id;
      name = slot.name;
    } else if (slot > 0) {
      const row = speakerCharacters[slot - 1];
      if (!row) {
        setStatus(workMemo ? `${slot}번 인물이 없습니다. 작품 메모에서 인물을 추가하세요` : "작품 메모를 먼저 연결하세요");
        return true;
      }
      characterId = row.id;
      name = row.name;
    }
    const pageId = selectedPageId;
    // 화면을 먼저 갱신하고 저장은 뒤에서 한다. 실패하면 서버 값으로 되돌린다.
    setSpeakerByRegion((current) => ({...current,[regionStoreKey(region)]:characterId || null}));
    setStatus(`R${regionId(region)} 화자: ${name}`);
    if (slot !== 0) cycleGuidanceRegion(1, true);
    try {
      const result = await api("/api/speakers", { method: "POST", body: JSON.stringify({
        session_id: sessionId, image_id: pageId, region_id: regionId(region), region_key: regionBindingKey(region), region_ref: region.region_ref || null, character_id: characterId,
      }) });
      if (selectedIdRef.current === pageId || !selectedIdRef.current) { setSpeakerByTg(result.by_tg || {}); setSpeakerByRegion(result.by_region_ref || result.by_region || {}); }
    } catch (err) {
      setStatus(`화자 저장 실패: ${err.message}`);
      api(`/api/speakers/${encodeURIComponent(sessionId)}/${encodeURIComponent(pageId)}`)
        .then((result) => { setSpeakerByTg(result.by_tg || {}); setSpeakerByRegion(result.by_region_ref || result.by_region || {}); }).catch(() => {});
    }
    return true;
  }

  // 대사 리스트 행: overlayRegions 순서(읽는 순서)를 그대로 쓴다. 키는 영역 참조 기반 overlay 키다.
  function dialogueRows() {
    if (!selected) return [];
    return overlayRegions.map((region) => {
      const rid = overlayItemKey(region);
      const override = overlayRegionValue(overlayGuidance, rid);
      const translation = String(override.text_override ?? baseRegionText(selected, region) ?? "").trim();
      const original = String(regionCluster(selected, region)?.text || "").trim();
      const saved = dialogueReview[`${selected.id}|${rid}`];
      const warnings = [];
      if (!translation) warnings.push("빈 번역");
      return {
        rid,
        region,
        label: overlayItemLabel(region),
        original,
        translation,
        warnings,
        status: saved && saved.text === translation ? saved.status : "none",
      };
    });
  }

  function setDialogueStatus(rid, status, { toggle = true } = {}) {
    const row = dialogueRows().find((item) => item.rid === String(rid));
    if (!row || !selected) return false;
    const key = `${selected.id}|${row.rid}`;
    setDialogueReview((current) => {
      const next = { ...current };
      if (toggle && row.status === status) delete next[key];
      else next[key] = { status, text: row.translation };
      writeStored(`ct.dialogueReview.${sessionId || ""}`, next);
      return next;
    });
    return true;
  }

  function moveDialogue(step) {
    const rows = dialogueRows();
    const visible = rows.filter((row) => dialogueFilterMatches(row, dialogueFilter));
    if (!visible.length) return false;
    const currentKey = selectedRegionRefValue();
    const fullIndex = rows.findIndex((row) => row.rid === String(currentKey));
    // 필터 밖 행이 선택돼 있으면 전체 순서에서 그 앞뒤에 있는 보이는 행으로 간다.
    const candidates = visible.filter((row) => {
      const index = rows.indexOf(row);
      return step > 0 ? index > fullIndex : index < fullIndex;
    });
    const target = fullIndex < 0 ? (step > 0 ? visible[0] : visible[visible.length - 1]) : (step > 0 ? candidates[0] : candidates[candidates.length - 1]);
    if (!target) {
      setStatus(step > 0 ? "마지막 대사입니다" : "첫 대사입니다");
      return true;
    }
    setSelectedRegionTracked(target.rid);
    overlayCanvasRef.current?.ensureRegionVisible(target.rid);
    setStatus(`${target.label} 선택 (${rows.indexOf(target) + 1}/${rows.length})`);
    return true;
  }

  // Y: 고칠 게 없으면 확인으로 표시하고 다음 대사로 넘어간다.
  function confirmDialogueAndNext() {
    const rid = selectedRegionRefValue();
    if (!rid || !setDialogueStatus(rid, "ok", { toggle: false })) return false;
    moveDialogue(1);
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
    setStatus(`${overlayKeyLabel(rid)} style ${nextStyle}`);
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
      setStatus(`${region ? overlayItemLabel(region) : overlayKeyLabel(rid)} font size ${nextSize}`);
      return { font_size: nextSize };
    }, `size:${rid}`);
  }

  // 스타일 편집은 여러 선택이면 모두에, 아니면 기본 선택에만 적용한다.
  function updateOverlayStyle(patch, historyKey = null) {
    if (isGroupSelection) updateOverlayRegions(groupKeys, patch, historyKey && `${historyKey}:group`);
    else if (selectedRegionRefValue()) updateOverlayRegion(selectedRegionRefValue(), patch, historyKey && `${historyKey}:${selectedRegionRefValue()}`);
  }

  function adjustOverlayStyleFontSize(delta) {
    if (!isGroupSelection) {
      adjustOverlayFontSize(selectedRegionRefValue(), delta);
      return;
    }
    // 각 박스의 현재 크기에서 같은 만큼 올리고 내린다. 크기를 맞추려면 숫자 칸을 쓴다.
    updateOverlayRegions(groupKeys, (current, key) => {
      const region = overlayRegions.find((item) => overlayItemKey(item) === key);
      return { font_size: normalizedFontSize(Number(current.font_size || overlayItemFontSize(region) || 24) + delta) };
    }, "size:group");
    setStatus(`${groupKeys.length}개 font size ${delta > 0 ? "+" : ""}${delta}`);
  }

  function setOverlayStyleFontSize(value) {
    const size = normalizedFontSize(value);
    if (!size) return;
    updateOverlayStyle({ font_size: size }, "size");
    if (isGroupSelection) setStatus(`${groupKeys.length}개 font size ${size}로 통일`);
  }

  function setOverlayWritingMode(rid, mode) {
    if (!rid) return;
    if (mode === "horizontal") {
      updateOverlayRegion(rid, { force_horizontal: true, force_vertical: false });
      setStatus(`${overlayKeyLabel(rid)} 가로쓰기`);
      return;
    }
    if (mode === "vertical") {
      updateOverlayRegion(rid, { force_vertical: true, force_horizontal: false });
      setStatus(`${overlayKeyLabel(rid)} 세로쓰기`);
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

  // AI 지움 후보가 생기면 비교 미리보기를 그 탭으로 돌리고, 사라지면 자동 선택으로 되돌린다.
  function handleAiEraseCandidate(url) {
    setAiEraseCandidateUrl(url || null);
    setGuidanceCompareMode(url ? "ai_erase" : "auto");
  }

  // 선택 페이지 상세를 다시 받아 반영한다(복원 패치 적용·해제 뒤 미리보기 갱신).
  async function reloadSelectedPage() {
    const sid = sessionIdRef.current;
    const imageId = selectedIdRef.current;
    if (!sid || !imageId) return;
    try {
      const data = await api(`/api/session/${encodeURIComponent(sid)}/page/${encodeURIComponent(imageId)}`);
      if (data?.image) applyImageRecord(data.image);
    } catch (err) {
      setStatus(String(err.message || err));
    }
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

  function loadRecentSessions() {
    setRecentLoadState("loading");
    api("/api/sessions/recent").then((data) => {
      setRecentSessions(data.sessions || []);
      setRecentLoadState("ready");
    }).catch(() => setRecentLoadState("error"));
  }

  useEffect(() => {
    // 세션은 첫 import/업로드 때 서버가 만든다. 앱을 열 때마다 빈 세션이 쌓이지 않게 여기서는 만들지 않는다.
    loadRecentSessions();
    api("/api/fonts").then((data) => {
      setFonts(data.fonts || []);
      setFontPath(data.default || "");
    }).catch(() => {});
    api("/api/ocr-models").then((data) => {
      setOcrModels(Array.isArray(data.models) ? data.models : []);
      setOcrDefaultModel(data.default || "");
      if (data.image_models) setImageModels(data.image_models);
    }).catch(() => {});
  }, []);

  // OCR·번역 제공자를 바꾸면 통번역 재생성 모델도 같은 제공자로 맞춘다(입력란에서 직접 고칠 수는 있다)
  useEffect(() => {
    const gemini = ocrProvider === "gemini";
    setExportImageModel((gemini ? imageModels.gemini : imageModels.openai) || "gpt-image-2.5-sunburst");
    setExportTextModel(gemini ? effectiveOcrModel : "gpt-6.1-terra");
  }, [ocrProvider, effectiveOcrModel, imageModels]);

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
    setExtraSelectedIds([]);
    resetOverlayHistory();
    // 페이지가 바뀌면 edit version을 한 단계 올리고 적용 완료로 맞춘다.
    // 이전 페이지 요청의 version은 새 페이지 version과 절대 같아지지 않는다.
    const nextVersion = overlayEditVersionRef.current + 1;
    overlayEditVersionRef.current = nextVersion;
    overlayAppliedVersionRef.current = nextVersion;
    setOverlayEditVersion(nextVersion);
    // 이전 페이지에서 대기 중이던 floating editor 적용 요청은 버린다.
    handledOverlayApplyRequestRef.current = overlayApplyRequest;
    setSelectedTg(null); setSelectedGuidanceRegionId(null);
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
      || generationReviewMode
      || (selected?.analysis_route !== "standard" && selected?.image_generation_recommendation?.priority === "recommended")
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
    if (prefetchingId === selected.id) {
      setStatus(`${selected.name} 미리 분석 중입니다. 끝나면 바로 표시됩니다.`);
      return;
    }
    if (autoAnalysisRequestedRef.current.has(requestKey)) return;
    autoAnalysisRequestedRef.current.add(requestKey);
    setStatus(`${selected.name} 분석 산출물이 없어 현재 파일 분석을 시작합니다.`);
    // 분석 결과가 없는 페이지라 Vision 결과 캐시가 있으면 그대로 쓴다
    analyze(selected.id, { force: false });
  }, [workspaceStarted, generationReviewMode, activeTab, selected?.id, selected?.image_sha256, selected?.regions, selected?.has_analysis, selected?.image_generation_recommendation?.priority, selected?.analysis_route, inputBusy, analyzeBusy, guidanceBusy, prefetchingId]);

  // 보고 있는 페이지 분석이 끝나고 화면 작업이 없을 때, 남은 미분석 페이지를 한 장씩 미리 분석한다.
  // 보던 페이지 뒤부터 끝까지, 이어서 앞쪽 순서로 돈다. 번역(OCR)은 네트워크 작업이라 함께 돌아도 막지 않는다.
  useEffect(() => {
    if (!workspaceStarted || generationReviewMode || !sessionId || !selected || prefetchPromiseRef.current) return;
    if (routeBusy || Object.values(busyTasks).some(Boolean)) return;
    // 보는 페이지 분석을 아직 시도하지 않았으면 그쪽이 먼저다. 시도했다가 실패했으면 멈추지 않고 이어간다.
    // 생성 권장 페이지는 자동 분석하지 않으므로 기다리지 않는다.
    const selectedSkipsAuto = selected.analysis_route !== "standard" && selected.image_generation_recommendation?.priority === "recommended";
    if (imageNeedsAnalysis(selected) && !selectedSkipsAuto && !autoAnalysisRequestedRef.current.has(`${selected.id}:${selected.image_sha256 || ""}`)) return;
    const start = images.findIndex((img) => img.id === selected.id);
    if (start < 0) return;
    const target = [...images.slice(start + 1), ...images.slice(0, start)].find((img) => {
      const key = `${img.id}:${img.image_sha256 || ""}`;
      return imageNeedsAnalysis(img)
        && !(img.analysis_route !== "standard" && img.image_generation_recommendation?.priority === "recommended")
        && !autoAnalysisRequestedRef.current.has(key)
        && !prefetchFailedRef.current.has(key);
    });
    if (target) prefetchAnalysis(target);
  }, [workspaceStarted, generationReviewMode, sessionId, selected?.id, selected?.regions, selected?.has_analysis, images, routeBusy, busyTasks, prefetchingId]);

  function prefetchAnalysis(target) {
    const sid = sessionId;
    const key = `${target.id}:${target.image_sha256 || ""}`;
    // 같은 페이지를 열어도 자동 분석이 겹쳐 돌지 않게 요청 기록을 먼저 남긴다
    autoAnalysisRequestedRef.current.add(key);
    setPrefetchingId(target.id);
    const run = apiStream(
      "/api/analyze-stream",
      { method: "POST", body: JSON.stringify({ session_id: sid, image_id: target.id, force: false, max_workers: 1 }) },
      (event) => {
        if (sessionIdRef.current !== sid) return;
        if (event.type === "image_status" && event.image) applyImageRecord(event.image);
        if (event.type === "error") throw new Error(event.message);
      },
    ).catch(() => {
      // 실패한 페이지는 미리 분석에서 빼고, 사용자가 열면 평소처럼 그 자리에서 분석한다
      autoAnalysisRequestedRef.current.delete(key);
      prefetchFailedRef.current.add(key);
    }).finally(() => {
      prefetchPromiseRef.current = null;
      setPrefetchingId("");
    });
    prefetchPromiseRef.current = run;
  }

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
    maskTool: () => activeTab === "guidance" && tool === "mask",
    maskPolygon: () => activeTab === "guidance" && tool === "mask" && Boolean(editorCanvasRef.current?.hasMaskPolygon()),
    nothingSelected: () => {
      if (activeTab === "overlay") return !selectedRegionRefValue() && !overlayCanvasRef.current?.hasSelectedEraser();
      if (activeTab === "guidance") return selectedTg == null && selectedGuidanceRegionId == null;
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
      if (nextTool === "mask") { startMaskTool(); return; }
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
      // 찍던 다각형이 있으면 그것만 먼저 취소한다(선택은 유지)
      if (editorCanvasRef.current?.cancelMaskPolygon()) return true;
      if (selectedTg != null || selectedGuidanceRegionId != null) {
        setSelectedTg(null); setSelectedGuidanceRegionId(null);
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
    "guidance.tg.delete": () => editorCanvasRef.current?.excludeSelection() ?? false,
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
    "guidance.cycleRegion": (_event, step) => cycleGuidanceRegion(step),
    "guidance.maskPolygonClose": () => editorCanvasRef.current?.closeMaskPolygon() ?? false,
    "guidance.maskBrush": (_event, step) => {
      const index = MASK_BRUSH_STEPS.findIndex((value) => value >= maskBrushWidth);
      const current = index < 0 ? MASK_BRUSH_STEPS.length - 1 : index;
      const next = MASK_BRUSH_STEPS[Math.max(0, Math.min(MASK_BRUSH_STEPS.length - 1, current + step))];
      setMaskBrushWidth(next);
      setStatus(`지우기 보정 붓 ${next}px`);
      return true;
    },
    "guidance.speaker": (event, slot) => {
      assignSpeaker(slot === "digit" ? Number(event.code.slice(-1)) : slot);
      return true;
    },
    "overlay.dialogue": (_event, step) => moveDialogue(step),
    "overlay.dialogue.confirm": () => confirmDialogueAndNext(),
    "overlay.dialogue.ask": () => {
      const rid = selectedRegionRefValue();
      return rid ? setDialogueStatus(rid, "ask") : false;
    },
    "overlay.openEditor": () => overlayCanvasRef.current?.openEditorForSelected() ?? false,
    "overlay.style": (_event, style) => {
      const rid = selectedRegionRefValue();
      updateOverlayRegion(rid, overlayFontStylePatch(style));
      setStatus(`${overlayKeyLabel(rid)} style ${style}`);
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
    // ⌘Enter: 반영 후 렌더하고, 이 대사를 확인으로 표시한 뒤 다음 대사로 넘어간다.
    "editor.commit": () => {
      const rid = selectedRegionRefValue();
      const committed = overlayCanvasRef.current?.commitEditor() ?? false;
      if (committed && rid) {
        dialogueConfirmAfterCommitRef.current = rid;
        moveDialogue(1);
      }
      return committed;
    },
    "editor.cancel": () => overlayCanvasRef.current?.closeEditor() ?? false,
    "help.toggle": () => setHelpOpen((open) => !open),
  };

  function handleShortcutKeyDown(event) {
    if (event.target?.closest?.("[role='dialog']")) return;
    // 화자 목록 팝업이 열려 있으면 포커스가 밖에 있어도 캔버스 단축키를 쓰지 않는다.
    if (speakerPickerOpen) {
      if (event.code === "Escape") setSpeakerPickerOpen(false);
      return;
    }
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
    const onKeyDown = (event) => {
      // 입력칸 밖의 ⌘A/Ctrl+A는 화면 글자 전체 선택만 일으키고 클릭을 방해하므로 막는다
      const target = event.target;
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName) || target?.isContentEditable;
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.code === "KeyA" && !typing) {
        event.preventDefault();
        return;
      }
      keyHandlerRef.current?.(event);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);

  // 미리보기 캔버스를 누르면 사이드 패널의 입력창에 남은 포커스를 풀어 방향키가 페이지 이동에 쓰이게 한다.
  useEffect(() => {
    const onPointerDown = (event) => {
      if (!event.target?.closest?.("#stageHost, #overlayStageHost")) return;
      const active = document.activeElement;
      if (!active || active === document.body || active.closest?.(".floating-text-editor")) return;
      const tag = active.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || active.isContentEditable) active.blur();
    };
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => window.removeEventListener("pointerdown", onPointerDown, true);
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
    if (taskLocksRef.current.has(kind)) return;
    taskLocksRef.current.add(kind);
    setBusyTasks((current) => ({ ...current, [kind]: true }));
    setStatus(`${label}...`);
    try {
      const result = await fn();
      setStatus(result?.logs || `${label} 완료`);
      return result;
    } catch (err) {
      setStatus(String(err.message || err));
    } finally {
      taskLocksRef.current.delete(kind);
      setBusyTasks((current) => ({ ...current, [kind]: false }));
    }
  }

  // 세션 id가 아직 없을 때 서버가 새 세션을 만들었다면 그 세션을 현재 세션으로 채택한다.
  async function adoptCreatedSession(requestSid, resultSid) {
    if (!requestSid && resultSid && resultSid !== sessionIdRef.current) {
      setSessionTracked(resultSid);
      const pendingName = projectName.trim();
      if (projectNameDirtyRef.current && pendingName) {
        await api("/api/session/meta", { method: "POST", body: JSON.stringify({ session_id: resultSid, project_name: pendingName }) })
          .then(() => { projectNameDirtyRef.current = false; })
          .catch(() => {});
      }
    }
  }

  // 폴더 선택 창에서 고른 경로는 state 반영 전에 바로 넘겨받는다. 버튼 클릭 이벤트는 경로로 쓰지 않는다.
  async function importPath(pathOverride) {
    const requestSid = sessionId;
    const path = typeof pathOverride === "string" ? pathOverride : folderPath;
    await runTask("input", "폴더 import", async () => {
      const result = await api("/api/import-path", { method: "POST", body: JSON.stringify({ session_id: requestSid, path, recursive: true }) });
      await adoptCreatedSession(requestSid, result.session_id);
      const imgs = await refresh(result.session_id);
      if (result.imported > 0) {
        scheduleLazyRefresh(result.session_id);
        setImportNameFocusRequest((request) => request + 1);
      }
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
      const folderName = files[0]?.webkitRelativePath?.split("/")[0] || "";
      const result = await api(`/api/upload?session_id=${requestSid}&source_folder_name=${encodeURIComponent(folderName)}`, { method: "POST", body: form });
      await adoptCreatedSession(requestSid, result.session_id);
      await refresh(result.session_id);
      if (result.imported > 0) {
        scheduleLazyRefresh(result.session_id);
        setImportNameFocusRequest((request) => request + 1);
      }
      setImageStatuses({});
      setActiveTab("guidance");
      if (input) input.value = "";
      return { logs: `${result.imported}개 이미지 업로드 완료. 캐시 확인은 백그라운드에서 진행 중` };
    });
  }

  // 서버 재시작 뒤: 보던 위치를 기억하고 새로고침한다. 새 화면이 같은 프로젝트를 다시 연다.
  function reloadAfterServerRestart() {
    try {
      if (sessionId) {
        window.sessionStorage.setItem(RESUME_KEY, JSON.stringify({ session_id: sessionId, image_id: selectedIdRef.current || selectedId, tab: activeTab, home: !workspaceStarted }));
      }
    } catch { /* 저장소를 못 쓰면 시작 화면으로 돌아간다 */ }
    window.location.reload();
  }

  // 프로젝트별로 마지막에 보던 페이지를 기억한다. 세션 전환 중 이전 세션의 선택이 섞이지 않도록 목록에 있는 페이지만 저장한다.
  useEffect(() => {
    if (!workspaceStarted || !sessionId || !selectedId) return;
    if (!images.some((img) => img.id === selectedId)) return;
    writeSessionSettings(sessionId, { lastImageId: selectedId });
  }, [workspaceStarted, sessionId, selectedId, images]);

  useEffect(() => {
    if (!workspaceStarted || !sessionId) return;
    try { window.sessionStorage.setItem(OPEN_WORKSPACE_KEY, JSON.stringify({ session_id: sessionId, tab: activeTab })); } catch { /* 저장소를 못 쓰면 새로고침 때 홈으로 간다 */ }
  }, [workspaceStarted, sessionId, activeTab]);

  function forgetOpenWorkspace() {
    try { window.sessionStorage.removeItem(OPEN_WORKSPACE_KEY); } catch { /* 무시 */ }
  }

  useEffect(() => {
    let resume = null;
    let reopened = false;
    try {
      resume = JSON.parse(window.sessionStorage.getItem(RESUME_KEY) || "null");
      window.sessionStorage.removeItem(RESUME_KEY);
      // 검토·미리보기 링크는 자기 세션을 직접 연다
      const query = new URLSearchParams(window.location.search);
      if (!resume?.session_id && !query.get("generation_review") && !query.get("page_preview")) {
        resume = JSON.parse(window.sessionStorage.getItem(OPEN_WORKSPACE_KEY) || "null");
        reopened = Boolean(resume?.session_id);
      }
    } catch { resume = null; }
    if (!resume?.session_id) return undefined;
    let cancelled = false;
    (async () => {
      try {
        // 홈에서 재시작했으면 편집기로 들어가지 않는다. 나누기·제외를 고르기 전에 분석이 시작되지 않게 한다.
        if (resume.home) {
          await openSessionForSetup({ session_id: resume.session_id });
          if (!cancelled) setStatus("서버를 재시작하고 준비 화면으로 다시 불러왔습니다");
          return;
        }
        await openRecentSession({ session_id: resume.session_id });
        if (cancelled) return;
        if (resume.image_id) { setSelectedId(resume.image_id); selectedIdRef.current = resume.image_id; }
        if (resume.tab) setActiveTab(resume.tab);
        if (!reopened) setStatus("서버를 재시작하고 프로젝트를 다시 불러왔습니다");
      } catch (error) {
        if (reopened) forgetOpenWorkspace();
        if (!cancelled) setStatus(error.message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function openRecentSession(recent) {
    if (!recent?.session_id) return;
    // 세션을 바꾸기 전에 읽어야 이전 세션 선택이 저장돼도 영향받지 않는다
    const lastImageId = readSessionSettings(recent.session_id).lastImageId;
    setSessionTracked(recent.session_id);
    selectedIdRef.current = "";
    const imgs = await refresh(recent.session_id);
    if (sessionIdRef.current !== recent.session_id) return;
    scheduleLazyRefresh(recent.session_id);
    const lastIndex = imgs?.length ? imgs.findIndex((img) => img.id === lastImageId) : -1;
    if (imgs?.length) {
      const target = imgs[Math.max(lastIndex, 0)];
      setSelectedId(target.id);
      selectedIdRef.current = target.id;
    }
    setWorkspaceStarted(true);
    setActiveTab("guidance");
    setStatus(lastIndex > 0 ? `${recent.session_id} 세션 열기 · 마지막으로 보던 ${lastIndex + 1}페이지` : `${recent.session_id} 세션 열기`);
  }

  // 편집기로 들어가지 않고 홈의 준비 화면(이미지 확인·제외, 가로 이미지 나누기)에 프로젝트를 불러온다.
  async function openSessionForSetup(recent) {
    if (!recent?.session_id) return;
    setRecentMenuId("");
    setSessionTracked(recent.session_id);
    selectedIdRef.current = "";
    setImageStatuses({});
    await refresh(recent.session_id);
    if (sessionIdRef.current !== recent.session_id) return;
    scheduleLazyRefresh(recent.session_id);
    setWorkspaceStarted(false);
    forgetOpenWorkspace();
    setStatus("준비 화면에서 열었습니다. 나누기·제외를 확인한 뒤 ‘분석하고 작업 시작’을 누르세요.");
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
      forgetOpenWorkspace();
    }
    setStatus("프로젝트를 삭제했습니다.");
  }

  function returnToStartPage() {
    if (!window.confirm("홈으로 돌아가시겠습니까? 현재 프로젝트는 저장된 상태로 유지됩니다.")) return;
    setWorkspaceStarted(false);
    forgetOpenWorkspace();
    setActiveTab("guidance");
    setAnalysisPolicy("reuse");
    api("/api/sessions/recent").then((data) => setRecentSessions(data.sessions || [])).catch(() => {});
    setStatus("홈으로 돌아왔습니다.");
  }

  async function startRegionAnalysis() {
    if (!images.length || analyzeBusy || inputBusy) return;
    const force = analysisPolicy === "refresh";
    if (force && !window.confirm(`${images.length}개 이미지를 다시 분석합니다. 기존 분석결과를 활용하지 않으며 Cloud Vision API 호출 비용이 발생할 수 있습니다. 진행할까요?`)) return;
    if (splitLandscape && images.some((image) => image.wide_spread_candidate && !image.parent_id)) {
      try {
        const result = await api("/api/pages/split", { method: "POST", body: JSON.stringify({ session_id: sessionId, include_worked: false }) });
        await adoptSplitResult(result);
      } catch (err) {
        setStatus(`가로 이미지를 나누지 못했습니다: ${String(err.message || err)}`);
        return;
      }
    }
    setWorkspaceStarted(true);
    setActiveTab("guidance");
    // 처음부터 다시 분석은 전체를 강제로 돈다. 그 외에는 바로 편집기로 들어가 보이는 페이지를 먼저 분석하고,
    // 나머지는 미리 분석이 화면을 잠그지 않고 한 장씩 이어간다.
    if (force) await analyze("all", { force });
  }

  function changeSplitLandscape(value) {
    setSplitLandscape(value);
    writeStored("ct.splitLandscape", value);
  }

  async function analyze(target = "all", options = {}) {
    const requestSid = sessionId;
    await runTask("analyze", "OCR 직전 분석", async () => {
      // 미리 분석 중인 페이지와 겹쳐 처리하지 않도록 끝날 때까지 기다린다
      if (prefetchPromiseRef.current) await prefetchPromiseRef.current;
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
        { method: "POST", body: JSON.stringify({ session_id: requestSid, image_id: target, force: options.force ?? (target !== "all"), max_workers: analyzeWorkers }) },
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

  async function switchToStandardAnalysis() {
    if (!selected || anyBusy || selected.processing_excluded || !pageDetailReady()) return;
    const sid = sessionId, id = selected.id;
    setRouteBusy(true);
    // 정책 응답이 도착한 뒤 자동 분석과 명시적 분석이 중복 실행되지 않게 한다.
    autoAnalysisRequestedRef.current.add(`${id}:${selected.image_sha256 || ""}`);
    try {
      const result = await api("/api/page-analysis-route", {method:"POST", body:JSON.stringify({session_id:sid, image_id:id, route:"standard"})});
      if (sessionIdRef.current !== sid) return;
      applyImageRecord(result.image);
      setGuidanceCompareMode("masked");
      await analyze(id, {force:false});
    } catch (err) { setStatus(`기존 방식 전환 실패: ${err.message}`); }
    finally { setRouteBusy(false); }
  }

  async function togglePageProcessing() {
    if (!selected || anyBusy) return;
    const sid = sessionId, id = selected.id;
    await runTask("guidance", "페이지 처리 상태 변경", async () => {
      const result = await api("/api/page-processing", {method:"POST", body:JSON.stringify({session_id:sid, image_id:id, excluded:!selected.processing_excluded})});
      if (sessionIdRef.current !== sid) return {};
      autoAnalysisRequestedRef.current.add(`${id}:${selected.image_sha256 || ""}`);
      applyImageRecord(result.image);
      return {logs:result.image.processing_excluded ? "페이지 처리에서 제외했습니다. 원본 그대로 출력합니다." : "처리 대상으로 복원했습니다. 기존 TG 편집과 분석 결과는 유지됩니다."};
    });
  }

  function preventPlainAnalysisEnter(event) {
    if ((event.key === "Enter" || event.code === "NumpadEnter") && !event.metaKey && !event.ctrlKey) event.preventDefault();
  }

  async function applyGuidance() {
    if (!selected || selected.processing_excluded || !pageDetailReady()) return;
    const requestSid = sessionId;
    const requestImageId = selected.id;
    await runTask("guidance", "변경사항 적용", async () => {
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
    await runTask("guidance", "영역 분석 산출물 삭제", async () => {
      const result = await api("/api/guidance/clear", { method: "POST", body: JSON.stringify({ session_id: requestSid, image_id: requestImageId }) });
      if (sessionIdRef.current !== requestSid) return { logs: "영역 분석 산출물 삭제 완료" };
      applyImageRecord(result.image);
      if (selectedIdRef.current === requestImageId) {
        const next = defaultGuidance(result.image);
        currentGuidanceRef.current = next;
        setGuidance(next);
        resetGuidanceHistory();
        setSelectedTg(null); setSelectedGuidanceRegionId(null);
      }
      return { logs: "영역 분석 산출물 삭제 완료" };
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
          overlay_guidance: overlayGuidancePayload(overlayGuidance),
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
            overlay_guidance: target === selected?.id && selected?.detail_loaded ? overlayGuidancePayload(overlayGuidance) : null,
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

  function dismissOcrPrompt() {
    // 지금 넘은 기준까지 처리한 것으로 두고, 다음 기준(80%)에서 다시 묻는다
    markOcrMilestone(ocrReachedMilestone);
  }

  function markOcrMilestone(value) {
    setOcrMilestone(value);
    if (sessionId) writeStored(`ct.ocrMilestone.${sessionId}`, value);
  }

  function setProjectOcrAutoStart(value) {
    setOcrAutoStart(value);
    if (sessionId) writeStored(`ct.ocrAutoStart.${sessionId}`, value);
  }

  function changeOcrPromptOff(value) {
    setOcrPromptOff(value);
    writeStored("ct.ocrPromptOff", value);
  }

  // 확인한 페이지만 모아 OCR·번역을 백그라운드로 돌린다. 작업 잠금(busyTasks)을 쓰지 않아 다른 페이지는 계속 편집할 수 있다.
  async function startBackgroundOcr() {
    // 지금 보고 있는 페이지는 아직 편집 중이므로 번역 대상에서 뺀다.
    const ids = ocrBatchCandidates.map((img) => img.id);
    if (!ids.length || bgOcr.running || ocrBusy) return;
    const requestSid = sessionId;
    markOcrMilestone(ocrReachedMilestone);
    setBgOcr({ running: true, total: ids.length, done: 0, failed: 0, ids });
    setImageStatuses((current) => {
      const next = { ...current };
      ids.forEach((id) => { next[id] = { phase: "ocr", status: "queued", detail: "대기" }; });
      return next;
    });
    setStatus(`확인한 ${ids.length}장의 OCR/번역을 백그라운드로 시작했습니다.`);
    const finished = new Set();
    try {
      await apiStream(
        "/api/run-final-stream",
        {
          method: "POST",
          body: JSON.stringify({
            session_id: requestSid,
            image_id: "batch",
            image_ids: ids,
            extra_prompt: extraPrompt,
            font_path: fontPath,
            overlay_guidance: null,
            replace: false,
            max_workers: analyzeWorkers,
            force: false,
            ocr_model: ocrModelForRequest(),
          }),
        },
        (event) => {
          if (sessionIdRef.current !== requestSid) return;
          if (event.type === "image_status") {
            setImageStatuses((current) => ({ ...current, [event.image_id]: { phase: "ocr", status: event.status, detail: event.detail || "" } }));
            if (event.image) applyImageRecord(event.image);
            if (["done", "skipped", "failed"].includes(event.status) && !finished.has(event.image_id)) {
              finished.add(event.image_id);
              setBgOcr((current) => ({ ...current, done: current.done + 1, failed: current.failed + (event.status === "failed" ? 1 : 0) }));
            }
          }
          if (event.type === "error") throw new Error(event.message);
          if (event.type === "complete") setImages(event.images || []);
        },
      );
      setStatus(`백그라운드 OCR/번역이 끝났습니다 (${finished.size}/${ids.length}장).`);
    } catch (err) {
      // 중간에 끊기면 끝나지 않은 페이지를 실패로 표시해 OCR 탭의 Retry Failed로 다시 돌릴 수 있게 한다
      setImageStatuses((current) => {
        const next = { ...current };
        ids.filter((id) => !finished.has(id)).forEach((id) => { next[id] = { phase: "ocr", status: "failed", detail: "중단됨" }; });
        return next;
      });
      setStatus(`백그라운드 OCR/번역이 중단됐습니다: ${String(err.message || err)}`);
    } finally {
      setBgOcr((current) => ({ ...current, running: false }));
    }
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
          overlay_guidance: overlayGuidancePayload(guidance || overlayGuidance),
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
          model: exportImageModel || "gpt-image-2.5-sunburst",
          prompt_guide: exportPromptGuide,
          analyze_first: exportAnalyzeFirst,
          text_model: exportTextModel || "gpt-6.1-terra",
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

  // 편집기에서 이 페이지 하나만 이미지 생성 모델로 통번역한다. 설정(모델·품질 등)은 내보내기 탭의 이미지 생성 설정을 따른다.
  async function translatePageWithImageModel() {
    if (!selected || selected.processing_excluded) return;
    const model = exportImageModel || "gpt-image-2.5-sunburst";
    const replace = Boolean(selected.final_url);
    if (!window.confirm(`${selected.name}을(를) ${model}로 통번역합니다.${replace ? " 지금 최종 출력을 새 생성 결과로 바꿉니다." : ""} 이미지 생성 비용이 발생합니다. 진행할까요?`)) return;
    setGuidanceMenu(null);
    await generateMissingFinals({ imageIds: [selected.id], force: replace });
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
    if (img.processing_excluded) return "skipped";
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
    // 영역 분석 전인 페이지는 실패가 아니라 아직 손대지 않은 대기열이다. 실패는 OCR·번역 호출이 실제로 실패한 경우만.
    if (!img.indexed_sheet_url) return "unanalyzed";
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
    return "영역 분석을 마치면 번역할 수 있습니다";
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
          <RegionTypeBadge region={region} />
          <div className="region-card-controls" onClick={(event) => event.stopPropagation()}>
            {hasSegmentSiblings ? (
              <div className="segment-mode-controls" title="같은 영역 안 segment 렌더 단위를 강제합니다">
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
        setSelectedTg(null); setSelectedGuidanceRegionId(null);
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
      const type = regionTypeLabel(item);
      const forcedLama = regionProcessingBasis(item) === TEXT_BASED && item.inpaint_engine === "lama";
      items.push({
        key: `region-${index}`,
        kind: "manual_regions",
        index,
        label: `${type} ${index + 1}`,
        detail: forcedLama ? "LaMa 강제" : item.source || "",
        isTextBased: regionProcessingBasis(item) === TEXT_BASED,
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
      items.push({ key: `region-off-${index}`, kind: "suppressed_regions", index, label: `R${item.display_region_id || item.region_id || item.source_region_id || "?"} 제외 기록`, detail: "자동 처리 대상에서 제외" });
    });
    (guidance.inpaint_engine_overrides || []).forEach((item, index) => {
      items.push({ key: `engine-${index}`, kind: "inpaint_engine_overrides", index, label: `R${item.display_region_id || "?"} ${ENGINE_NAMES[item.engine] || item.engine} 고정`, detail: `지우기 엔진을 ${ENGINE_NAMES[item.engine] || item.engine}로 고정` });
    });
    (guidance.custom_masks || []).forEach((item, index) => {
      // R에 붙인 기록은 R마다 한 줄, R 지정 없이 칠한 기록(가장 많이 겹치는 영역에 자동 배정)은 한 줄이다.
      const attached = Boolean(item.instance_ref || item.region_ref || item.target_key);
      const dormant = attached && !guidanceRegions.some((region) => recordTargetsRegion(item, region));
      const shapes = `붓 ${(item.strokes || []).length} · 다각형 ${(item.polygons || []).length} · 사각형 ${(item.rects || []).length}`;
      items.push({
        key: `custom-mask-${index}`, kind: "custom_masks", index,
        label: attached ? `R${item.display_region_id || "?"} 지우기 보정` : "지우기 보정(자동 배정)",
        detail: dormant ? `${shapes} · 영역 없음: 흰칠만` : shapes,
      });
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

  // 텍스트 기반 영역 지우기 엔진: 기본은 배경 분류(Telea/LaMa) 자동, 켜면 LaMa로 고정한다.
  function toggleTextBasedForcedLama(historyItem) {
    setGuidanceTracked((current) => {
      const next = cloneJson(current || defaultGuidance(null));
      const region = (next.manual_regions || [])[historyItem.index];
      if (!region || regionProcessingBasis(region) !== TEXT_BASED) return current;
      if (region.inpaint_engine === "lama") delete region.inpaint_engine;
      else region.inpaint_engine = "lama";
      return next;
    }, "텍스트 기반 영역 엔진 변경");
  }

  // 선택한 텍스트 기반 영역(자동·수동 모두)의 지우기 엔진을 고정(lama/telea)하거나 자동(null)으로 되돌린다.
  // 영역 키에 저장하며 변경사항 적용 때 반영된다.
  function setSelectedRegionEngine(engine) {
    const region = selectedGuidanceRegion;
    if (!region || regionProcessingBasis(region) !== TEXT_BASED) return;
    const key = regionBindingKey(region);
    const current = (guidance.inpaint_engine_overrides || []).find((item) => recordTargetsRegion(item, region))?.engine || null;
    if (current === engine) return;
    setGuidanceTracked((value) => {
      const next = cloneJson(value || defaultGuidance(null));
      const list = (next.inpaint_engine_overrides || []).filter((item) => !recordTargetsRegion(item, region));
      if (engine) list.push({ target_key: key, ...(region.region_instance_ref ? { instance_ref: region.region_instance_ref } : {}), engine, display_region_id: regionId(region) });
      next.inpaint_engine_overrides = list;
      return next;
    }, engine ? `${ENGINE_NAMES[engine]}로 고정` : "지우기 자동 판정");
    setStatus(`R${regionId(region)} 지우기: ${engine ? `${ENGINE_NAMES[engine]} 고정` : "자동 판정"} · 변경사항 적용(${shortcutById("guidance.apply")})으로 반영됩니다.`);
  }

  // 지우기 보정 도구를 켠다. R을 선택해 두었으면 그 R로 확대하고 칠한 것을 그 R의 OCR에 붙인다.
  // 선택이 없으면 분석 때 가장 많이 겹치는 영역에 붙는다. 칠한 곳은 어느 쪽이든 흰칠된다.
  function startMaskTool() {
    setTool("mask");
    if (selectedGuidanceRegion || selectedTg != null) editorCanvasRef.current?.zoomToSelection();
    setStatus(selectedGuidanceRegion
      ? `R${regionId(selectedGuidanceRegion)}에 지우기 보정 · 칠한 곳은 흰칠되고 이 R의 OCR에 들어갑니다`
      : "지우기 보정(자동 배정) · 칠한 곳은 흰칠되고 가장 많이 겹치는 영역의 OCR에 들어갑니다");
  }

  // 선택한 R이 바뀌면 메모 입력칸을 저장된 값으로 되돌린다.
  useEffect(() => { setContextDraft(null); }, [selectedRegionKey, selectedPageId]);
  const savedRegionContext = selectedGuidanceRegion ? contextByRegion[regionStoreKey(selectedGuidanceRegion)] || "" : "";
  const regionContextValue = contextDraft ?? savedRegionContext;

  // 입력칸을 벗어나면 저장한다. 번역은 실행하지 않는다.
  // 화자·번역 메모를 바꾼 뒤 이 페이지만 OCR·번역을 다시 돌린다(OCR 탭의 Force Current와 같음).
  // 적어 둔 메모가 아직 저장 전이면 먼저 저장해 이번 번역에 들어가게 한다.
  async function retranslateSelectedPage() {
    if (!selected) return;
    if (contextDraft != null) await saveRegionContext();
    await runFinalStream(selected.id, true);
  }

  // 영역이 바뀐 뒤에도 기존 번역을 그대로 쓴다. 알림만 끈다.
  async function keepStaleTranslation() {
    if (!selected) return;
    try {
      const result = await api("/api/ocr-stale/keep", { method: "POST", body: JSON.stringify({ session_id: sessionId, image_id: selected.id }) });
      if (result.image) applyImageRecord(result.image);
      setStatus("기존 번역을 유지합니다.");
    } catch (err) {
      setStatus(`기존 번역 유지 실패: ${err.message}`);
    }
  }

  async function saveRegionContext() {
    const region = selectedGuidanceRegion;
    if (!region || contextDraft == null || contextDraft.trim() === savedRegionContext) { setContextDraft(null); return; }
    const pageId = selectedPageId;
    try {
      const result = await api("/api/region-context", { method: "POST", body: JSON.stringify({
        session_id: sessionId, image_id: pageId, region_id: regionId(region), region_key: regionBindingKey(region), region_ref: region.region_ref || null, text: contextDraft,
      }) });
      if (selectedPageId === pageId) setContextByRegion(result.context_by_region_ref || result.context_by_region || {});
      setContextDraft(null);
      setStatus(`R${regionId(region)} 번역 메모 ${contextDraft.trim() ? "저장" : "삭제"} · 다음 OCR/번역 때 반영됩니다.`);
    } catch (err) {
      setStatus(`번역 메모 저장 실패: ${err.message}`);
    }
  }

  // 도구 모음의 LaMa 버튼: LaMa 고정을 켜고 끈다.
  function toggleSelectedRegionLama() {
    setSelectedRegionEngine(lamaForcedPending ? null : "lama");
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
    setGuidanceTracked(defaultGuidance({ ...selected, guidance: {} }), "가이드 전체 비우기");
    setSelectedTg(null); setSelectedGuidanceRegionId(null);
    setStatus("화면의 가이드를 모두 비웠습니다. 저장 결과는 변경사항 적용 시 변경되며, 적용 전에는 실행 취소로 되돌릴 수 있습니다.");
  }

  const guidanceHistory = guidanceHistoryItems();

  // 같은 프로젝트 안에서 가로 이미지를 나눈 결과를 화면에 반영한다.
  function splitSummary(result) {
    if (!result.split_pages) return result.skipped_worked?.length ? `분석·편집 흔적이 있는 ${result.skipped_worked.length}장은 나누지 않았습니다.` : "나눌 가로 이미지가 없습니다.";
    const parts = [`가로 이미지 ${result.split_pages}장을 ${result.created}쪽으로 나눴습니다`];
    if (result.center_kept) parts.push(`가운데 한 장만 남긴 이미지 ${result.center_kept}장 포함`);
    if (result.skipped_worked?.length) parts.push(`작업 흔적이 있는 ${result.skipped_worked.length}장은 건너뜀`);
    return `${parts.join(" · ")}.`;
  }

  async function adoptSplitResult(result) {
    setImages(result.images || []);
    setImageStatuses({});
    setStatus(splitSummary(result));
  }
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const reviewSession = query.get("generation_review");
    if (!reviewSession) return;
    let cancelled = false;
    (async () => {
      try {
        await openRecentSession({ session_id: reviewSession });
        if (!cancelled && query.get("image_id")) setSelectedId(query.get("image_id"));
      } catch (error) { if (!cancelled) setStatus(error.message); }
    })();
    return () => { cancelled = true; };
  }, []);
  const previewSessionId = new URLSearchParams(window.location.search).get("page_preview");
  const sourceLinksControl = (sessionId || previewSessionId) ? <SourceLinks key={sessionId || previewSessionId} sessionId={sessionId || previewSessionId} disabled={anyBusy} onChanged={() => refresh(sessionId || previewSessionId)} /> : null;
  // 가로 이미지 나누기는 분석 전에 한 번 쓰는 준비 단계라 시작 화면에서만 보여 준다.
  const pagePreparationControl = (sessionId || previewSessionId) ? <PagePreparation sessionId={sessionId || previewSessionId} disabled={anyBusy} onSplit={adoptSplitResult} onOverride={() => refresh(sessionId || previewSessionId)} autoOpen={Boolean(previewSessionId)} label="미리보기·페이지별 조정" className="link-button" /> : null;

  if (!workspaceStarted) {
    return <StartScreen
      images={images} recentSessions={recentSessions} projectName={projectName}
      recentLoadState={recentLoadState} loadRecentSessions={loadRecentSessions}
      importNameFocusRequest={importNameFocusRequest}
      pagePreparationControl={pagePreparationControl} sourceLinksControl={sourceLinksControl} pagePreviewMode={Boolean(previewSessionId)}
      onProjectNameChange={(value) => { projectNameDirtyRef.current = true; setProjectName(value); }}
      saveProjectName={saveProjectName} folderPath={folderPath} setFolderPath={setFolderPath}
      fileRef={fileRef} uploadFiles={uploadFiles} uploadFromInput={uploadFromInput} importPath={importPath}
      inputBusy={inputBusy} analyzeBusy={analyzeBusy} analyzeWorkers={analyzeWorkers} setAnalyzeWorkers={setAnalyzeWorkers}
      analysisPolicy={analysisPolicy} setAnalysisPolicy={setAnalysisPolicy} startRegionAnalysis={startRegionAnalysis}
      splitLandscape={splitLandscape} setSplitLandscape={changeSplitLandscape}
      removeLoadedImage={removeLoadedImage} openRecentSession={openRecentSession} openSessionForSetup={openSessionForSetup}
      recentMenuId={recentMenuId} setRecentMenuId={setRecentMenuId}
      renameRecentSession={renameRecentSession} deleteRecentSession={deleteRecentSession}
      status={status} setStatus={setStatus}
    />;
  }

  return (
    <div className={`app workspace-mode ${toolsOpen ? "" : "tools-collapsed"} ${activeTab === "overlay" ? "overlay-mode" : ""} ${activeTab === "ocr" ? "ocr-mode" : ""} ${activeTab === "export" ? "export-mode" : ""} ${activeTab === "guidance" ? "guidance-mode" : ""}`}>
      <header className="workspace-global-topbar">
        <div className="workspace-brand-group"><button className="workspace-brand" type="button" onClick={returnToStartPage} title="홈으로 돌아가기">Comic Translator Studio</button><ServerStatus onRestarted={reloadAfterServerRestart} /></div>
        <div className="project-identity">
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
        <GlossaryPanel sessionId={sessionId} projectName={projectName} ocrBusy={ocrBusy} analysisReady={images.some((image) => image.has_analysis)} onMemoChange={setWorkMemo} />
        </div>
        <nav className="workflow-tabs top-workflow-tabs">
          {WORKFLOW_TABS.map(([id, label]) => (
            <button key={id} className={activeTab === id ? "active" : ""} onClick={() => setActiveTab(id)} title={`${label} (${shortcutById(`tab.${id}`)})`}>
              {label}
            </button>
          ))}
        </nav>
        <div className="file-navigator top-file-nav">
          {sourceLinksControl}
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
            <button className="primary" onClick={applyGuidance} onKeyDown={preventPlainAnalysisEnter} disabled={guidanceBusy || analyzeBusy || !selected || selected.processing_excluded}>변경사항 적용 ({shortcutById("guidance.apply")})</button>
            <button onClick={() => runFinal()} disabled={ocrBusy || !selected || selected.processing_excluded}>Run Final</button>
          </div>
          <button className="wide" onClick={() => setActiveTab("export")} disabled={!sessionId || !images.length}>Export Page</button>
        </div>

        <div className="section">
          <h2>Status</h2>
          <div className="metric-grid">
            <div className="metric"><strong>{images.length}</strong><span>images</span></div>
            <div className="metric"><strong>{disabledCount}</strong><span>TG off</span></div>
            <div className="metric"><strong>{splitCount}</strong><span>TG split</span></div>
            <div className="metric"><strong>{textBasedGuideCount}</strong><span>텍스트 기반</span></div>
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
                <span>비교 미리보기</span>
                <div className="preview-tabs">
                  {guidanceCompareTabs.map(([id, label, url]) => (
                    <button key={id} className={effectiveGuidanceCompareMode === id ? "active" : ""} onClick={() => setGuidanceCompareMode(id)} disabled={!url}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {!selected?.processing_excluded && selected?.ocr_stale ? <div className="generation-preview-notice" role="status">
                <span>번역한 뒤 영역이 바뀌어 기존 번역이 지금 영역과 맞지 않을 수 있습니다.</span>
                <button type="button" onClick={retranslateSelectedPage} disabled={anyBusy}>현재 영역으로 다시 번역</button>
                <button type="button" onClick={keepStaleTranslation} disabled={anyBusy}>기존 번역 유지</button>
              </div> : null}
              {!selected?.processing_excluded && latestGeneratedCandidate ? <div className="generation-result-strip">
                <span title={latestGeneratedCandidate.stale ? "입력·번역·작품 메모 변경으로 재검토가 필요합니다. 등록 결과는 보존됩니다." : "등록된 생성 결과입니다."}><strong>생성 결과 {generatedCandidates.length}개</strong> · 출력 미반영{latestGeneratedCandidate.stale ? " · 재검토 필요" : ""}</span>
                {generationRecommended ? <details><summary>기존 방식으로 전환</summary><div>
                  <p>기존 CV는 재사용하며, 없으면 유료 CV 호출이 발생할 수 있습니다. 생성 결과와 원본은 보존합니다. 번역·이미지 생성은 실행하지 않습니다.</p>
                  <button type="button" onClick={switchToStandardAnalysis} onKeyDown={preventPlainAnalysisEnter} disabled={anyBusy || !selected?.detail_loaded}>기존 방식으로 처리 · CV/자동 분석</button>
                </div></details> : <span>기존 방식으로 처리 중</span>}
              </div> : !selected?.processing_excluded && (generationRecommended || selected?.analysis_route === "standard") ? <div className="generation-preview-notice" role="status">
                <strong>{generationRecommended ? "이미지 생성 권장 페이지" : "기존 방식으로 처리하는 페이지"}</strong>
                <p>{generationRecommended ? "현재 페이지는 자동 분석을 시작하지 않습니다. 생성 결과를 등록하거나 기존 CV·영역 분석 방식으로 전환할 수 있습니다." : "CV → TG → 처리 영역 분석을 사용합니다. 원본은 그대로 보관합니다."}</p>
                <p>등록된 생성 결과가 없습니다. 원본을 표시합니다.</p>
                {generationRecommended ? <><button type="button" className="primary" onClick={switchToStandardAnalysis} onKeyDown={preventPlainAnalysisEnter} disabled={anyBusy || !selected?.detail_loaded}>기존 방식으로 처리 · CV/자동 분석</button><small>기존 CV 결과가 있으면 재사용합니다. 없으면 유료 CV 호출이 발생할 수 있습니다. 번역·이미지 생성은 실행하지 않습니다.</small></> : null}
              </div> : null}
              <ImagePreview src={guidanceCompareUrl} />
            </section>

            <section className="viewer-card guidance-editor-card">
              <div className="card-head editor-head">
                <div className="editor-title">
                  <span>Analysis Workspace</span>
                  <strong>{selectedGuidanceRegionId != null ? `R${selectedGuidanceRegionId}` : selectedTg ? `TG${selectedTg}` : selectedToolLabel}</strong>
                </div>
                <div className="guidance-topbar" ref={guidanceMenuRef}>
                  <ImageGenerationPanel key={`${sessionId}:${selected?.id}`} sessionId={sessionId} image={selected} onStatus={setGenerationStatus} />
                  <span className={`guidance-change-state ${guidanceDirty ? "dirty" : ""}`} role="status">
                    {selected?.processing_excluded ? "페이지 처리 제외 · 원본 출력" : guidanceDirty ? "미적용 변경 있음" : selected?.has_analysis && selected?.ocr_region_count === 0 ? "처리 대상 없음 · 원본 유지" : "적용할 변경 없음"}
                  </span>
                  <button
                    className="guidance-retranslate"
                    onClick={retranslateSelectedPage}
                    disabled={anyBusy || ocrBusy || guidanceDirty || !selected?.indexed_sheet_url || selected.processing_excluded}
                    title={guidanceDirty ? "변경사항 적용을 먼저 하세요" : "이 페이지만 OCR·번역을 다시 실행합니다. 화자·번역 메모가 반영됩니다. LLM 비용이 발생합니다."}
                  >{selected?.has_integrated_json || selected?.final_url ? "이 페이지 다시 번역" : "이 페이지 번역"}</button>
                  <button className="primary guidance-apply" onClick={applyGuidance} onKeyDown={preventPlainAnalysisEnter} disabled={anyBusy || !selected?.detail_loaded || selected.processing_excluded} title="그어 둔 가이드로 마스크·OCR 영역을 다시 계산합니다. Cloud Vision·번역은 실행하지 않습니다.">변경사항 적용 <kbd>{shortcutById("guidance.apply")}</kbd></button>
                  <button className="icon-button" onClick={undoGuidance} disabled={anyBusy || (!guidanceUndoStack.length && !guidanceHistory.length)} aria-label="편집 실행 취소" title={`실행 취소 (${shortcutById("edit.undo")})`}>↶</button>
                  <button className="icon-button" onClick={redoGuidance} disabled={anyBusy || !guidanceRedoStack.length} aria-label="편집 다시 실행" title={`다시 실행 (${shortcutById("edit.redo")})`}>↷</button>
                  <div className="topbar-popover-wrap">
                    <button className={guidanceMenu === "history" ? "active" : ""} onClick={() => setGuidanceMenu(guidanceMenu === "history" ? null : "history")} aria-expanded={guidanceMenu === "history"}>기록 {guidanceHistory.length} ▾</button>
                    {guidanceMenu === "history" ? <div className="topbar-popover history-popover" role="dialog" aria-label="편집 기록">
                      <p className="hint">항목을 누르면 그 가이드를 지웁니다. 실행 취소로 되돌릴 수 있습니다.</p>
          {guidanceHistory.length ? (
            <div className="history-list side-history-list">
              {guidanceHistory.slice().reverse().map((item) => (
                <div key={item.key} className="history-row-wrap">
                  <button className="history-row" title={`${item.detail || item.label} 제거`} onClick={() => removeGuidanceHistoryItem(item)}>
                    <span>{item.label}</span>
                    {item.detail ? <em>{item.detail}</em> : null}
                  </button>
                  {item.isTextBased ? (
                    <button
                      className={`history-engine-toggle${item.forcedLama ? " active" : ""}`}
                      title={item.forcedLama ? "LaMa 강제 해제 (배경에 따라 자동 선택)" : "이 텍스트 기반 영역을 LaMa로 지우기"}
                      onClick={() => toggleTextBasedForcedLama(item)}
                    >
                      LaMa
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="history-empty side-history-empty">아직 추가한 가이드가 없습니다.</div>
          )}
                    </div> : null}
                  </div>
                  <div className="topbar-popover-wrap">
                    <button className={guidanceMenu === "more" ? "active" : ""} onClick={() => setGuidanceMenu(guidanceMenu === "more" ? null : "more")} aria-expanded={guidanceMenu === "more"} aria-label="더 보기" title="페이지 처리 제외 · 표시 설정 · 고급 편집">⋯</button>
                    {guidanceMenu === "more" ? <div className="topbar-popover more-popover" role="dialog" aria-label="더 보기">
                      <button type="button" className={selected?.processing_excluded ? "page-excluded-control" : ""} disabled={!selected || anyBusy} onClick={togglePageProcessing} title={selected?.processing_excluded ? "현재 페이지는 원본 그대로 출력합니다. 클릭하면 처리 대상으로 복원합니다." : "이 페이지를 처리에서 제외하고 원본 그대로 출력합니다."}>{selected?.processing_excluded ? "처리 대상으로 복원" : "페이지 처리 제외"}</button>
                      <button type="button" disabled={!selected || anyBusy || selected.processing_excluded} onClick={translatePageWithImageModel}
                        title={selected?.processing_excluded ? "처리 대상으로 복원한 뒤 쓸 수 있습니다" : "이미지 생성 모델로 이 페이지 전체를 한국어로 다시 그려 최종 출력으로 씁니다. 이미지 생성 비용이 발생합니다."}>AI로 통번역</button>
                      <details className="guidance-disclosure">
                        <summary>표시 설정{Object.values(layers).some((visible) => !visible) ? <span>일부 숨김</span> : null}</summary>
          <p className="hint">겹친 표시를 확인할 때만 끕니다. 분석·출력 결과는 바뀌지 않습니다.</p>
          <div className="region-color-legend"><span><i style={{color:"#22c55e"}}>●</i> Edge</span><span><i style={{color:"#1d4ed8"}}>●</i> Bubble</span><span><i style={{color:"#eab308"}}>●</i> Unboxed</span></div>
          <div className="toggles">
            {Object.entries({ tg: "텍스트 묶음 (TG)", regions: "처리 영역 (R)", guidance: "가이드" }).map(([key, label]) => (
              <label className="check" key={key}>
                <input type="checkbox" checked={layers[key]} onChange={(e) => setLayers({ ...layers, [key]: e.target.checked })} />
                {label}
              </label>
            ))}
          </div>
                      </details>
                      <details className="guidance-disclosure guidance-advanced">
                        <summary>고급 편집·초기화</summary>
          <details className="guidance-json-editor"><summary>가이드 JSON 직접 편집</summary>
          <textarea aria-label="영역 분석 JSON"
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
          </details>
          <div className="guidance-reset-actions">
            <p className="hint">저장된 가이드도 포함해 화면의 가이드를 모두 비웁니다. 변경사항 적용 전에는 저장 결과가 유지되고 실행 취소로 되돌릴 수 있습니다.</p>
            <button onClick={clearUnsavedGuidance} disabled={anyBusy || !selected?.detail_loaded || selected.processing_excluded}>가이드 전체 비우기</button>
            <p className="hint">이 페이지의 가이드·분석·번역·최종 이미지를 삭제합니다. 원본 이미지·CV·TG·등록된 생성 결과는 유지합니다.</p>
            {!clearOutputsConfirm ? <button className="danger" onClick={() => setClearOutputsConfirm(true)} disabled={anyBusy || !selected?.detail_loaded || selected.processing_excluded}>이 페이지 작업 결과 삭제…</button> : <div className="guidance-delete-confirm" role="group" aria-label="작업 결과 삭제 확인">
              <p>이 페이지의 저장된 작업 결과를 삭제할까요?</p>
              <button className="danger" onClick={() => { setClearOutputsConfirm(false); clearGuidanceOutputs(); }} disabled={anyBusy}>작업 결과 삭제</button>
              <button onClick={() => setClearOutputsConfirm(false)}>취소</button>
            </div>}
          </div>
                      </details>
                    </div> : null}
                  </div>
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
                selectedRegionId={selectedGuidanceRegionId}
                setSelectedRegionId={setSelectedGuidanceRegionId}
                onToolComplete={() => setTool("select")}
                setStatus={setStatus}
                speakerLabels={speakerLabels}
                maskBrushWidth={maskBrushWidth}
              />
              <div className="guidance-ai-dock">
        <AiErasePanel
          sessionId={sessionId}
          page={selected?.detail_loaded ? selected : null}
          disabled={anyBusy || selected?.processing_excluded}
          onCandidate={handleAiEraseCandidate}
          onApplied={reloadSelectedPage}
          setStatus={setStatus}
          ocrModel={effectiveOcrModel}
          imageModel={ocrProvider === "gemini" ? imageModels.gemini : imageModels.openai}
          confirmOpen={aiEraseConfirmOpen}
          onConfirmClose={() => setAiEraseConfirmOpen(false)}
          onRequestConfirm={() => setAiEraseConfirmOpen(true)}
        />
              </div>
              {selectedGuidanceRegion || selectedTg != null ? <FloatingPanel id="guidance-inspector" title={selectedGuidanceRegion ? `R${regionId(selectedGuidanceRegion)}` : `TG${selectedTg}`} subtitle={selectedGuidanceRegion ? regionDetectionLabel(selectedGuidanceRegion) : ""} className="guidance-inspector">
                <div className="inspector">
                  {selectedIsTextBased ? <div className="inspector-row">
                    <span className="inspector-label">지우기</span>
                    <div className="segmented" role="radiogroup" aria-label="지우기 방식">
                      {[[null, autoEngineUsed ? `자동 · ${autoEngineUsed}` : "자동"], ["lama", "LaMa"], ["telea", "Telea"]].map(([value, label]) => (
                        <button key={label} role="radio" aria-checked={enginePending === value} className={enginePending === value ? "active" : ""} disabled={anyBusy} onClick={() => setSelectedRegionEngine(value)}
                          title={value ? `${label}로 고정 (변경사항 적용 시 반영)` : "배경을 보고 Telea·LaMa를 자동으로 고릅니다"}>{label}</button>
                      ))}
                    </div>
                    {enginePending !== engineSaved ? <small className="inspector-pending">변경사항 적용 시 반영</small> : null}
                  </div> : null}
                  <div className="inspector-row">
                    <span className="inspector-label">원문 <small>CV</small></span>
                    <textarea aria-label="선택 영역 CV 텍스트" readOnly value={selectedCvText} placeholder="연결된 CV 텍스트가 없습니다." rows={3} />
                  </div>
                  {selectedGuidanceRegion ? <div className="inspector-row">
                    <span className="inspector-label">화자 <strong>{speakerLabels[regionId(selectedGuidanceRegion)] || "미지정"}</strong></span>
                    {speakerCharacters.length ? <div className="speaker-keys">
                      {speakerCharacters.map((row, index) => (
                        <button key={row.id} onClick={() => assignSpeaker(index + 1)} title={row.speech || row.personality || ""}>
                          <kbd>{index + 1}</kbd>{row.name}{row.protagonist ? " ★" : ""}
                        </button>
                      ))}
                      {allSpeakerCharacters.length > 0 ? <button onClick={() => assignSpeaker(9)}><kbd>9</kbd>기타{allSpeakerCharacters.length > 8 ? ` (${allSpeakerCharacters.length - 8})` : ""}…</button> : null}
                      <button onClick={() => assignSpeaker("narration")}><kbd>N</kbd>내레이션</button>
                      <button onClick={() => assignSpeaker(0)}><kbd>0</kbd>해제</button>
                    </div> : <p className="hint">상단 작품 메모에 인물을 추가하면 숫자키로 화자를 지정할 수 있습니다.</p>}
                  </div> : <p className="hint">TG만 선택했습니다. 화자는 R(처리 영역)을 선택해 지정합니다. 같은 곳을 한 번 더 클릭하면 R이 선택됩니다.</p>}
                  {selectedGuidanceRegion ? <div className="inspector-row">
                    <label className="inspector-label" htmlFor="region-context-input">번역 메모 <small>이 영역 번역에만 · 선택</small></label>
                    <textarea id="region-context-input" rows={2} maxLength={500} value={regionContextValue}
                      placeholder="예: 비꼬는 말투 / 앞 칸 대사에 대한 대답 / 효과음이라 짧게"
                      onChange={(event) => setContextDraft(event.target.value)}
                      onBlur={saveRegionContext}
                      onKeyDown={(event) => { if (event.key === "Escape") { setContextDraft(null); event.currentTarget.blur(); } }} />
                    <small className="inspector-note">{contextDraft != null && contextDraft.trim() !== savedRegionContext ? "칸을 벗어나면 저장" : savedRegionContext ? "저장됨 · 다음 번역 때 반영" : "작품 메모·화자와 함께 번역 가이드로 들어갑니다"}</small>
                  </div> : null}
                  <div className="inspector-actions">
                    <button onClick={() => editorCanvasRef.current?.excludeSelection()} disabled={anyBusy} title={`선택한 ${selectedGuidanceRegion ? "R" : "TG"}을 처리에서 제외 (${shortcutById("guidance.tg.delete")})`}>{selectedGuidanceRegion ? "이 영역 제외" : "이 TG 제외"} <kbd>{shortcutById("guidance.tg.delete")}</kbd></button>
                  </div>
                </div>
              </FloatingPanel> : null}
              {TOOL_HINTS[tool] && !selected?.processing_excluded ? <div className="tool-hint" role="status">
                <strong>{TOOL_LABELS.find(([id]) => id === tool)?.[1]} <kbd>{shortcutFor("guidance", "tool", tool)}</kbd></strong>
                <span>{tool === "mask" ? `${TOOL_HINTS.mask} · 굵기 ${maskBrushWidth}px ([ ])` : TOOL_HINTS[tool]}</span>
                <em>Esc 취소</em>
              </div> : null}
              <div className="floating-toolbox" aria-label="영역 분석 도구">
                {TOOL_LABELS.map(([id, label]) => (
                  <button
                    key={id}
                    className={`${tool === id ? "active" : ""} ${["tg-delete","wall"].includes(id) ? "tool-group-start" : ""}`}
                    onClick={() => id === "tg-delete" ? editorCanvasRef.current?.excludeSelection() : id === "mask" ? startMaskTool() : setTool(id)}
                    disabled={id === "tg-delete" && selectedTg == null && selectedGuidanceRegionId == null}
                    aria-pressed={id === "tg-delete" ? undefined : tool === id}
                    aria-label={label}
                    title={`${label} (${(id === "tg-delete" ? shortcutById("guidance.tg.delete") : shortcutFor("guidance", "tool", id))})`}
                  >
                    <span><GuidanceToolIcon tool={id} /></span>
                    <em>{(id === "tg-delete" ? shortcutById("guidance.tg.delete") : shortcutFor("guidance", "tool", id))}</em>
                  </button>
                ))}
                <button
                  className="tool-group-start lama-tool"
                  onClick={toggleSelectedRegionLama}
                  disabled={anyBusy || !selectedIsTextBased}
                  aria-pressed={lamaForcedPending}
                  aria-label="LaMa로 다시 지우기"
                  title={selectedIsTextBased
                    ? `선택한 텍스트 기반 영역을 LaMa로 ${lamaForcedPending ? "고정 해제" : "다시 지우기"} (변경사항 적용 시 반영)`
                    : "텍스트 기반 영역(R)을 선택하면 LaMa로 다시 지울 수 있습니다"}
                >
                  <span><GuidanceToolIcon tool="lama" /></span>
                  <em>LaMa</em>
                </button>
                <button
                  className="ai-erase-tool"
                  onClick={() => setAiEraseConfirmOpen(true)}
                  disabled={anyBusy || !selected?.detail_loaded || selected?.processing_excluded}
                  aria-label="AI 지우개"
                  title="AI 지우개: 이 페이지의 글자를 이미지 모델로 지웁니다 (확인 후 실행, 비용 발생)"
                >
                  <span><GuidanceToolIcon tool="ai-eraser" /></span>
                  <em>AI</em>
                </button>
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
                  <button onClick={() => selected && runFinalStream(selected.id)} disabled={ocrBusy || !selected || selected.processing_excluded || !selected.indexed_sheet_url}>Run Current</button>
                  <button onClick={() => selected && runFinalStream(selected.id, true)} disabled={ocrBusy || !selected || selected.processing_excluded || !selected.indexed_sheet_url} title="현재 파일의 기존 OCR 결과를 무시하고 다시 실행">Force Current</button>
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
                      <strong>Queue</strong>
                      <span title={`번역 가능 ${ocrReadyImages.length} · 분석 전 ${ocrUnanalyzedImages.length}`}>{ocrReadyImages.length}{ocrUnanalyzedImages.length ? ` + ${ocrUnanalyzedImages.length}` : ""}</span>
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
                      {ocrQueueImages.length ? ocrQueueImages.map(renderOcrImageButton) : <div className="empty compact-empty">대기 파일 없음</div>}
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
                      {ocrNeedsAttentionImages.length ? ocrNeedsAttentionImages.map(renderOcrImageButton) : <div className="empty compact-empty">OCR·번역 실패 없음</div>}
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
                    extraSelectedIds={extraSelectedIds}
                    onToggleRegion={toggleRegionInGroup}
                    setStatus={setStatus}
                    onRequestApply={() => setOverlayApplyRequest((count) => count + 1)}
                  />
              </div>
            </section>
          </div>
        ) : null}

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
          <h2>{activeTab === "guidance" ? "영역 편집" : "작업"}</h2>
          <button className="icon-button" onClick={() => setToolsOpen(!toolsOpen)} title={toolsOpen ? "패널 접기" : "패널 열기"}>
            {toolsOpen ? "›" : "‹"}
          </button>
        </div>

        {activeTab !== "guidance" ? <div className="page-processing-controls">
          <button type="button" className={selected?.processing_excluded ? "page-excluded-control" : ""} disabled={!selected || anyBusy} onClick={togglePageProcessing} title={selected?.processing_excluded ? "현재 페이지는 원본 그대로 출력합니다. 클릭하면 처리 대상으로 복원합니다." : "이 페이지를 처리에서 제외하고 원본 그대로 출력합니다."}>{selected?.processing_excluded ? "처리 대상으로 복원" : "페이지 처리 제외"}</button>
          <ImageGenerationPanel key={`${sessionId}:${selected?.id}`} sessionId={sessionId} image={selected} onStatus={setGenerationStatus} />
        </div> : null}

        {toolsOpen ? <>
        {activeTab === "overlay" ? <>
        <div className="section compact-section">
          <div className="row">
            <button className="primary" onClick={applyOverlay} disabled={overlayBusy || !canRenderOverlayImage(selected)} title={`Overlay 적용 (${shortcutById("overlay.apply")})`}>Apply Overlay</button>
            <button onClick={() => runFinal(true)} disabled={ocrBusy || !selected || selected.processing_excluded} title="현재 Additional Translation Prompt를 사용해 OCR/번역부터 다시 실행합니다.">Re-run OCR</button>
            {/* 기록은 ref라 편집·Undo마다 올라가는 overlayEditVersion 리렌더 때 개수를 다시 읽는다 */}
            <button className="icon-button" onClick={() => stepOverlayHistory(-1)} disabled={!overlayHistoryRef.current.undo.length} aria-label="Overlay 편집 실행 취소" title={`실행 취소 (${shortcutById("edit.undo")})`}>↶</button>
            <button className="icon-button" onClick={() => stepOverlayHistory(1)} disabled={!overlayHistoryRef.current.redo.length} aria-label="Overlay 편집 다시 실행" title={`다시 실행 (${shortcutById("edit.redo")})`}>↷</button>
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
              {isGroupSelection ? (
                <div className="overlay-group-card">
                  <strong>{groupKeys.length}개 선택됨</strong>
                  <span className="overlay-group-hint">글꼴·크기 같은 공통 스타일만 바꿉니다. 글자 내용은 그대로 둡니다.</span>
                  <div className="overlay-group-chips">
                    {groupRegions.map((region) => <span key={overlayItemKey(region)} className="overlay-group-chip">{overlayItemLabel(region)}</span>)}
                  </div>
                  <button type="button" onClick={() => setExtraSelectedIds([])}>추가 선택 해제</button>
                </div>
              ) : selectedRegion ? renderOverlayRegionCard(selectedRegion) : <div className="empty compact-empty">Region을 선택하세요. Shift/Cmd+클릭으로 여러 개를 고를 수 있습니다.</div>}
            </div>
            <div className="selected-overlay-pane overlay-style-section">
              <div className="side-section-head overlay-style-head">
                <h2>Selected Style</h2>
                <div className="font-size-control compact-font-size">
                  <button onClick={() => adjustOverlayStyleFontSize(-1)} disabled={!selectedRegionRefValue()}>−</button>
                  {isGroupSelection ? (
                    <input
                      type="number"
                      className="group-font-size-input"
                      min="8"
                      max="160"
                      placeholder="혼합"
                      key={`${groupKeys.join(",")}:${groupFontSize || 0}`}
                      defaultValue={groupFontSize || ""}
                      onBlur={(e) => { if (e.target.value && Number(e.target.value) !== groupFontSize) setOverlayStyleFontSize(e.target.value); }}
                      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
                      title="숫자를 입력하고 Enter를 누르면 선택한 박스의 글자 크기를 이 값으로 통일합니다"
                    />
                  ) : <span>Size {selectedFontSize || "-"}</span>}
                  <button onClick={() => adjustOverlayStyleFontSize(1)} disabled={!selectedRegionRefValue()}>+</button>
                </div>
              </div>
              <div className="font-style-toolbar">
                {FONT_STYLES.map(([id, _short, label, sample]) => (
                  <button
                    key={id}
                    className={(isGroupSelection ? groupFontStyle : selectedEffectiveFontStyle) === id ? "active" : ""}
                    onClick={() => updateOverlayStyle(overlayFontStylePatch(id))}
                    disabled={!selectedRegionRefValue()}
                    title={`${label} (${shortcutById(`overlay.style.${id}`)})`}
                  >
                    <span className={`font-style-sample sample-${id}`}>{sample}</span>
                    <span className="font-style-name">{label}</span>
                  </button>
                ))}
              </div>
              <div className="overlay-style-fields">
                {(isGroupSelection ? groupFontStyle : selectedEffectiveFontStyle) === "etc" ? (
                  <>
                    <div className="field compact-field">
                      <label>Etc font</label>
                      <select
                        value={(isGroupSelection ? groupValue((o) => o.font_path || "") : selectedOverlayOverride.font_path) || ""}
                        onChange={(e) => updateOverlayStyle({ font_path: e.target.value })}
                        disabled={!selectedRegionRefValue()}
                      >
                        <option value="">Default</option>
                        {fonts.map((font) => <option key={font.path} value={font.path}>{font.name}</option>)}
                      </select>
                    </div>
                    <div className="field compact-field">
                      <label>Weight</label>
                      <select
                        value={(isGroupSelection ? groupValue((o) => o.font_weight || "") : selectedOverlayOverride.font_weight) || ""}
                        onChange={(e) => updateOverlayStyle({ font_weight: e.target.value })}
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
                    value={isGroupSelection ? (groupValue((o) => (Number.isFinite(Number(o.stroke_width)) ? Number(o.stroke_width) : 2)) ?? "") : selectedStrokeWidth}
                    onChange={(e) => updateOverlayStyle({ stroke_width: e.target.value }, "stroke")}
                    disabled={!selectedRegionRefValue()}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="section compact-section">
          <DialogueList
            rows={dialogueRows()}
            selectedKey={selectedRegionRefValue()}
            extraKeys={extraSelectedIds}
            filter={dialogueFilter}
            onFilter={setDialogueFilter}
            onSelect={(rid, event) => {
              if (event && (event.shiftKey || event.metaKey || event.ctrlKey)) { toggleRegionInGroup(rid); return; }
              setSelectedRegionTracked(rid);
              overlayCanvasRef.current?.ensureRegionVisible(rid);
            }}
            onStatus={setDialogueStatus}
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
            : `System Status: ${guidanceBusy || analyzeBusy ? "Active" : "Idle"} | Tool: ${TOOL_LABELS.find(([id]) => id === tool)?.[1] || tool} | File: ${selected?.name || "-"}${prefetchingId ? ` | 미리 분석: ${images.find((img) => img.id === prefetchingId)?.name || prefetchingId}` : ""}`}
      </footer>
      {shortcutToast ? <div className="shortcut-toast" role="status" aria-live="polite">{shortcutToast}</div> : null}
      <SpeakerPicker
        open={speakerPickerOpen}
        characters={allSpeakerCharacters}
        regionLabel={selectedGuidanceRegion ? `R${regionId(selectedGuidanceRegion)}` : ""}
        onClose={() => setSpeakerPickerOpen(false)}
        onPick={(row) => { setSpeakerPickerOpen(false); assignSpeaker(row); }}
      />
      <OcrBatchPrompt
        show={showOcrPrompt && !ocrAutoStart}
        pending={ocrBatchCandidates.length}
        percent={ocrReachedMilestone}
        promptOff={ocrPromptOff}
        onPromptOff={changeOcrPromptOff}
        onStart={startBackgroundOcr}
        onLater={dismissOcrPrompt}
        autoStart={ocrAutoStart}
        onAutoStart={() => { setProjectOcrAutoStart(true); startBackgroundOcr(); }}
        onAutoOff={() => { setProjectOcrAutoStart(false); setStatus("자동 번역을 껐습니다. 다음 묶음부터 다시 묻습니다."); }}
        running={bgOcr.running}
        progress={bgOcr}
        selectedLocked={selectedInBackgroundOcr}
      />
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
