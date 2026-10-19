// 편집기 키보드·마우스 조작의 단일 정의.
// App의 keydown 디스패처, 툴바 tooltip, 단축키 도움말(?)이 모두 이 표를 읽는다.
// docs/editor_manual.html의 단축키 표는 tests/test_docs_sync.py가 이 파일과 대조한다.
//
// 항목 형식 (테스트가 정규식으로 읽으므로 id와 keys 사이에 중괄호를 두지 않는다)
//   id       고유 id. 매뉴얼 표의 <tr data-shortcut="id">와 짝을 이룬다.
//   context  "global"(모든 탭) | "guidance" | "overlay" | "floatingEditor" | "restore" 또는 그 배열
//   group    도움말 묶음 이름
//   label    도움말·매뉴얼에 보이는 설명
//   kind     "key" | "pointer" (pointer는 마우스·트랙패드 조작 설명용이라 디스패처가 무시한다)
//   keys     표시용 조합 목록. [["Ctrl", "Z"]]처럼 쓰고, Ctrl은 macOS에서 Cmd로 보여 준다.
//   when     조건 이름 목록. App 디스패처의 조건 표에서 평가한다.
//   match    실제 판정. code(event.code, 문자열 또는 배열) 또는 key(event.key),
//            mod(Ctrl/Cmd), shift(true/false/"any"), alt(true/false)
//   action   App 디스패처의 동작 id, arg는 동작 인자
//   allowInInput  입력칸에 포커스가 있어도 동작
//   passive  다른 컴포넌트(RestorePanel 등)가 직접 처리한다. 디스패처는 건너뛰고 도움말에만 보인다.

export const IS_MAC = typeof navigator !== "undefined"
  && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || "");

export const KEYMAP_GROUPS = ["보기/탐색", "탭·페이지", "도구", "편집", "스타일", "적용", "대사 편집", "도움말", "배경 복원"];

const DIGIT_CODES = (n) => [`Digit${n}`, `Numpad${n}`];

const CANVAS = ["guidance", "overlay"];

export const KEYMAP = [
  // 보기/탐색: 두 캔버스 공통
  { id: "view.pan.wheel", context: CANVAS, group: "보기/탐색", label: "화면 이동 (두 손가락 스크롤·마우스 휠, Shift+휠은 가로)", kind: "pointer", keys: [["휠"], ["Shift", "휠"]] },
  { id: "view.zoom.wheel", context: CANVAS, group: "보기/탐색", label: "확대/축소 (포인터 기준, 트랙패드 핀치 포함)", kind: "pointer", keys: [["Ctrl", "휠"], ["핀치"]] },
  { id: "view.pan.space", context: CANVAS, group: "보기/탐색", label: "누른 채 드래그해 화면 이동", kind: "pointer", keys: [["Space", "드래그"]] },
  { id: "view.pan.middle", context: CANVAS, group: "보기/탐색", label: "휠 버튼 드래그로 화면 이동", kind: "pointer", keys: [["휠 버튼", "드래그"]] },
  { id: "view.fit.dblclick", context: CANVAS, group: "보기/탐색", label: "빈 곳 더블클릭: 화면 맞춤", kind: "pointer", keys: [["더블클릭"]] },
  { id: "view.fit", context: CANVAS, group: "보기/탐색", label: "화면 맞춤", kind: "key", keys: [["Shift", "1"]], match: { code: DIGIT_CODES(1), shift: true }, action: "view.fit" },
  { id: "view.zoom100", context: CANVAS, group: "보기/탐색", label: "100% (실제 픽셀)", kind: "key", keys: [["Shift", "0"]], match: { code: DIGIT_CODES(0), shift: true }, action: "view.zoom100" },
  { id: "view.zoomSelection", context: CANVAS, group: "보기/탐색", label: "선택 항목으로 확대", kind: "key", keys: [["Shift", "2"]], match: { code: DIGIT_CODES(2), shift: true }, action: "view.zoomSelection" },
  { id: "view.fit.mod", context: CANVAS, group: "보기/탐색", label: "화면 맞춤 (브라우저 확대 대신)", kind: "key", keys: [["Ctrl", "0"]], allowInInput: true, match: { code: DIGIT_CODES(0), mod: true, shift: "any" }, action: "view.fit" },
  { id: "view.zoomIn", context: CANVAS, group: "보기/탐색", label: "확대", kind: "key", keys: [["Ctrl", "="]], allowInInput: true, match: { code: ["Equal", "NumpadAdd"], mod: true, shift: "any" }, action: "view.zoomIn" },
  { id: "view.zoomOut", context: CANVAS, group: "보기/탐색", label: "축소", kind: "key", keys: [["Ctrl", "-"]], allowInInput: true, match: { code: ["Minus", "NumpadSubtract"], mod: true, shift: "any" }, action: "view.zoomOut" },

  // 탭·페이지: 모든 탭
  { id: "tab.guidance", context: "global", group: "탭·페이지", label: "말풍선 분석 탭", kind: "key", keys: [["Alt", "1"]], match: { code: DIGIT_CODES(1), alt: true }, action: "tab", arg: "guidance" },
  { id: "tab.ocr", context: "global", group: "탭·페이지", label: "글자인식/번역 탭", kind: "key", keys: [["Alt", "2"]], match: { code: DIGIT_CODES(2), alt: true }, action: "tab", arg: "ocr" },
  { id: "tab.overlay", context: "global", group: "탭·페이지", label: "글자 입히기 탭", kind: "key", keys: [["Alt", "3"]], match: { code: DIGIT_CODES(3), alt: true }, action: "tab", arg: "overlay" },
  { id: "tab.export", context: "global", group: "탭·페이지", label: "내보내기 탭", kind: "key", keys: [["Alt", "4"]], match: { code: DIGIT_CODES(4), alt: true }, action: "tab", arg: "export" },
  { id: "tab.restore", context: "global", group: "탭·페이지", label: "배경 복원 탭", kind: "key", keys: [["Alt", "5"]], match: { code: DIGIT_CODES(5), alt: true }, action: "tab", arg: "restore" },
  { id: "page.prev", context: "global", group: "탭·페이지", label: "이전 페이지", kind: "key", keys: [["PgUp"], ["Alt", "←"]], match: [{ code: "PageUp" }, { code: "ArrowLeft", alt: true }], action: "page.prev" },
  { id: "page.prev.arrow", context: "global", group: "탭·페이지", label: "이전 페이지 (선택한 region·TG가 없을 때)", kind: "key", when: ["nothingSelected"], keys: [["←"]], match: { code: "ArrowLeft" }, action: "page.prev" },
  { id: "page.next.arrow", context: "global", group: "탭·페이지", label: "다음 페이지 (선택한 region·TG가 없을 때)", kind: "key", when: ["nothingSelected"], keys: [["→"]], match: { code: "ArrowRight" }, action: "page.next" },
  { id: "page.next", context: "global", group: "탭·페이지", label: "다음 페이지", kind: "key", keys: [["PgDn"], ["Alt", "→"]], match: [{ code: "PageDown" }, { code: "ArrowRight", alt: true }], action: "page.next" },

  // 도구
  { id: "guidance.tool.select", context: "guidance", group: "도구", label: "Select", kind: "key", keys: [["V"]], match: { code: "KeyV" }, action: "tool", arg: "select" },
  { id: "guidance.tool.hand", context: "guidance", group: "도구", label: "Hand (화면 이동)", kind: "key", keys: [["H"]], match: { code: "KeyH" }, action: "tool", arg: "hand" },
  { id: "guidance.tool.tg-toggle", context: "guidance", group: "도구", label: "TG Off", kind: "key", keys: [["D"]], match: { code: "KeyD" }, action: "tool", arg: "tg-toggle" },
  { id: "guidance.tool.tg-split", context: "guidance", group: "도구", label: "TG Split", kind: "key", keys: [["T"]], match: { code: "KeyT" }, action: "tool", arg: "tg-split" },
  { id: "guidance.tool.wall", context: "guidance", group: "도구", label: "Edge Wall", kind: "key", keys: [["E"]], match: { code: "KeyE" }, action: "tool", arg: "wall" },
  { id: "guidance.tool.region-split", context: "guidance", group: "도구", label: "Region Split", kind: "key", keys: [["L"]], match: { code: "KeyL" }, action: "tool", arg: "region-split" },
  { id: "guidance.tool.ubox", context: "guidance", group: "도구", label: "Ubox", kind: "key", keys: [["U"]], match: { code: "KeyU" }, action: "tool", arg: "ubox" },
  { id: "guidance.tool.bubble", context: "guidance", group: "도구", label: "Bubble", kind: "key", keys: [["B"]], match: { code: "KeyB" }, action: "tool", arg: "bubble" },
  { id: "guidance.tool.region-off", context: "guidance", group: "도구", label: "Region Off", kind: "key", keys: [["X"]], match: { code: "KeyX" }, action: "tool", arg: "region-off" },
  { id: "overlay.tool.select", context: "overlay", group: "도구", label: "Select", kind: "key", keys: [["V"]], match: { code: "KeyV" }, action: "tool", arg: "select" },
  { id: "overlay.tool.hand", context: "overlay", group: "도구", label: "Hand (화면 이동)", kind: "key", keys: [["H"]], match: { code: "KeyH" }, action: "tool", arg: "hand" },
  { id: "overlay.tool.eraser", context: "overlay", group: "도구", label: "지우개 (켜기/끄기)", kind: "key", keys: [["E"]], match: { code: "KeyE" }, action: "tool", arg: "eraser" },

  // 편집
  { id: "edit.escape.guidance", context: "guidance", group: "편집", label: "단계별 해제: 선택 해제 → Select 도구", kind: "key", keys: [["Esc"]], match: { code: "Escape", shift: "any" }, action: "escape" },
  { id: "edit.escape.overlay", context: "overlay", group: "편집", label: "단계별 해제: 대사 편집 닫기 → 선택 해제 → Select 도구", kind: "key", keys: [["Esc"]], match: { code: "Escape", shift: "any" }, action: "escape" },
  { id: "edit.undo", context: CANVAS, group: "편집", label: "실행 취소", kind: "key", keys: [["Ctrl", "Z"]], match: { code: "KeyZ", mod: true }, action: "undo" },
  { id: "edit.redo", context: CANVAS, group: "편집", label: "다시 실행", kind: "key", keys: [["Ctrl", "Shift", "Z"]], match: { code: "KeyZ", mod: true, shift: true }, action: "redo" },
  { id: "edit.redo.y", context: CANVAS, group: "편집", label: "다시 실행 (Windows·Linux)", kind: "key", platform: "nonMac", keys: [["Ctrl", "Y"]], match: { code: "KeyY", mod: true }, action: "redo" },
  { id: "guidance.delete", context: "guidance", group: "편집", label: "선택한 TG를 끄기. 선택이 없으면 포인터 아래 guide/TG/region 삭제 (우클릭과 같음)", kind: "key", keys: [["Delete"], ["Backspace"]], match: { code: ["Delete", "Backspace"] }, action: "delete" },
  { id: "guidance.rightClick", context: "guidance", group: "편집", label: "포인터 아래 guide/TG/region 삭제", kind: "pointer", keys: [["우클릭"]] },
  { id: "overlay.nudge", context: "overlay", group: "편집", label: "선택한 safe rect 1px 이동 (선택이 없으면 ←/→는 페이지 이동)", kind: "key", when: ["regionSelected"], keys: [["←"], ["→"], ["↑"], ["↓"]], match: { code: ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"] }, action: "overlay.nudge", arg: 1 },
  { id: "overlay.nudge.big", context: "overlay", group: "편집", label: "선택한 safe rect 10px 이동", kind: "key", when: ["regionSelected"], keys: [["Shift", "←"], ["Shift", "→"], ["Shift", "↑"], ["Shift", "↓"]], match: { code: ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"], shift: true }, action: "overlay.nudge", arg: 10 },
  { id: "overlay.nextRegion", context: "overlay", group: "편집", label: "다음 region 선택 (Region List 기본 순서)", kind: "key", keys: [["Tab"]], match: { code: "Tab" }, action: "overlay.cycleRegion", arg: 1 },
  { id: "overlay.prevRegion", context: "overlay", group: "편집", label: "이전 region 선택", kind: "key", keys: [["Shift", "Tab"]], match: { code: "Tab", shift: true }, action: "overlay.cycleRegion", arg: -1 },
  { id: "overlay.openEditor", context: "overlay", group: "편집", label: "선택한 region의 대사 편집 열기", kind: "key", when: ["regionSelected"], keys: [["Enter"]], match: { code: ["Enter", "NumpadEnter"] }, action: "overlay.openEditor" },
  { id: "overlay.delete", context: "overlay", group: "편집", label: "선택한 지우개 영역 삭제. 없으면 선택 region의 위치·크기·회전 수동값 초기화 (대사·스타일은 유지)", kind: "key", keys: [["Delete"], ["Backspace"]], match: { code: ["Delete", "Backspace"] }, action: "delete" },
  { id: "overlay.rotateSnap", context: "overlay", group: "편집", label: "회전 핸들 드래그 중 15° 단위로 맞춤", kind: "pointer", keys: [["Shift", "회전 드래그"]] },
  { id: "overlay.editor.dblclick", context: "overlay", group: "편집", label: "region 더블클릭: 대사 편집 열기", kind: "pointer", keys: [["region 더블클릭"]] },

  // 스타일 (글자 입히기)
  { id: "overlay.style.normal", context: "overlay", group: "스타일", label: "Normal 스타일", kind: "key", when: ["regionSelected"], keys: [["1"]], match: { code: DIGIT_CODES(1) }, action: "overlay.style", arg: "normal" },
  { id: "overlay.style.bold", context: "overlay", group: "스타일", label: "Bold 스타일", kind: "key", when: ["regionSelected"], keys: [["2"]], match: { code: DIGIT_CODES(2) }, action: "overlay.style", arg: "bold" },
  { id: "overlay.style.impact", context: "overlay", group: "스타일", label: "Impact 스타일", kind: "key", when: ["regionSelected"], keys: [["3"]], match: { code: DIGIT_CODES(3) }, action: "overlay.style", arg: "impact" },
  { id: "overlay.style.light", context: "overlay", group: "스타일", label: "Light 스타일", kind: "key", when: ["regionSelected"], keys: [["4"]], match: { code: DIGIT_CODES(4) }, action: "overlay.style", arg: "light" },
  { id: "overlay.style.memory_serif", context: "overlay", group: "스타일", label: "Serif 스타일", kind: "key", when: ["regionSelected"], keys: [["5"]], match: { code: DIGIT_CODES(5) }, action: "overlay.style", arg: "memory_serif" },
  { id: "overlay.style.serif_bold", context: "overlay", group: "스타일", label: "Serif Bold 스타일", kind: "key", when: ["regionSelected"], keys: [["6"]], match: { code: DIGIT_CODES(6) }, action: "overlay.style", arg: "serif_bold" },
  { id: "overlay.style.handwritten", context: "overlay", group: "스타일", label: "Handwritten 스타일", kind: "key", when: ["regionSelected"], keys: [["7"]], match: { code: DIGIT_CODES(7) }, action: "overlay.style", arg: "handwritten" },
  { id: "overlay.style.serif_emphasis", context: "overlay", group: "스타일", label: "EFX 스타일", kind: "key", when: ["regionSelected"], keys: [["8"]], match: { code: DIGIT_CODES(8) }, action: "overlay.style", arg: "serif_emphasis" },
  { id: "overlay.style.etc", context: "overlay", group: "스타일", label: "Etc 스타일", kind: "key", when: ["regionSelected"], keys: [["9"]], match: { code: DIGIT_CODES(9) }, action: "overlay.style", arg: "etc" },
  { id: "overlay.fontBigger", context: "overlay", group: "스타일", label: "글자 크기 +3 (Shift는 +1)", kind: "key", when: ["regionSelected"], keys: [["="], ["Shift", "="]], match: { code: ["Equal", "NumpadAdd"], shift: "any" }, action: "overlay.fontSize", arg: 1 },
  { id: "overlay.fontSmaller", context: "overlay", group: "스타일", label: "글자 크기 -3 (Shift는 -1)", kind: "key", when: ["regionSelected"], keys: [["-"], ["Shift", "-"]], match: { code: ["Minus", "NumpadSubtract"], shift: "any" }, action: "overlay.fontSize", arg: -1 },
  { id: "overlay.orientation", context: "overlay", group: "스타일", label: "쓰기 방향 순환: 자동 → 가로 → 세로", kind: "key", when: ["regionSelected"], keys: [["O"]], match: { code: "KeyO" }, action: "overlay.orientation" },

  // 적용
  { id: "guidance.apply", context: "guidance", group: "적용", label: "말풍선 분석 적용 (Apply Analysis)", kind: "key", keys: [["Ctrl", "Enter"]], allowInInput: true, match: { code: ["Enter", "NumpadEnter"], mod: true }, action: "apply" },
  { id: "overlay.apply", context: "overlay", group: "적용", label: "Overlay 적용 (Apply Overlay)", kind: "key", keys: [["Ctrl", "Enter"]], allowInInput: true, when: ["notInFloatingEditor"], match: { code: ["Enter", "NumpadEnter"], mod: true }, action: "apply" },

  // 대사 편집 팝업
  { id: "editor.commit", context: "floatingEditor", group: "대사 편집", label: "대사 반영 후 overlay render", kind: "key", keys: [["Ctrl", "Enter"]], allowInInput: true, when: ["inFloatingEditor"], match: { code: ["Enter", "NumpadEnter"], mod: true }, action: "editor.commit" },
  { id: "editor.cancel", context: "floatingEditor", group: "대사 편집", label: "편집 취소 (초안 버림)", kind: "key", keys: [["Esc"]], allowInInput: true, match: { code: "Escape", shift: "any" }, action: "editor.cancel" },

  // 도움말
  { id: "help.toggle", context: "global", group: "도움말", label: "단축키 도움말 열기/닫기", kind: "key", keys: [["?"]], match: [{ code: "Slash", shift: true }, { key: "?", shift: "any" }], action: "help.toggle" },

  // 배경 복원 탭: RestorePanel.jsx가 직접 처리한다. 전역 디스패처는 이 탭에서 탭·페이지·도움말 키만 처리한다.
  { id: "restore.brush", context: "restore", group: "배경 복원", label: "칠하기", kind: "key", passive: true, keys: [["B"]] },
  { id: "restore.eraser", context: "restore", group: "배경 복원", label: "지우기", kind: "key", passive: true, keys: [["E"]] },
  { id: "restore.context", context: "restore", group: "배경 복원", label: "문맥 영역", kind: "key", passive: true, keys: [["C"]] },
  { id: "restore.seed", context: "restore", group: "배경 복원", label: "글자 영역 추가", kind: "key", passive: true, keys: [["T"]] },
  { id: "restore.pan", context: "restore", group: "배경 복원", label: "이동 도구", kind: "key", passive: true, keys: [["H"]] },
  { id: "restore.mask", context: "restore", group: "배경 복원", label: "마스크 표시 켜기/끄기", kind: "key", passive: true, keys: [["M"]] },
  { id: "restore.brushSize", context: "restore", group: "배경 복원", label: "브러시 크기 줄이기/키우기", kind: "key", passive: true, keys: [["["], ["]"]] },
  { id: "restore.undo", context: "restore", group: "배경 복원", label: "마스크 되돌리기", kind: "key", passive: true, keys: [["Ctrl", "Z"]] },
  { id: "restore.panDrag", context: "restore", group: "배경 복원", label: "화면 이동 (휠 버튼·우클릭 드래그도 가능)", kind: "pointer", passive: true, keys: [["Space", "드래그"]] },
  { id: "restore.zoom", context: "restore", group: "배경 복원", label: "확대/축소", kind: "pointer", passive: true, keys: [["휠"]] },
];

function contextsOf(entry) {
  return Array.isArray(entry.context) ? entry.context : [entry.context];
}

export function entryInContext(entry, context) {
  return contextsOf(entry).includes(context);
}

export function entryVisibleOnPlatform(entry) {
  if (entry.platform === "nonMac") return !IS_MAC;
  if (entry.platform === "mac") return IS_MAC;
  return true;
}

function codeMatches(expected, code) {
  if (expected == null) return true;
  return Array.isArray(expected) ? expected.includes(code) : expected === code;
}

function singleMatch(match, event) {
  const mod = Boolean(event.metaKey || event.ctrlKey);
  if (Boolean(match.mod) !== mod) return false;
  if (Boolean(match.alt) !== Boolean(event.altKey)) return false;
  const shift = match.shift ?? false;
  if (shift !== "any" && Boolean(shift) !== Boolean(event.shiftKey)) return false;
  if (match.code != null && !codeMatches(match.code, event.code)) return false;
  if (match.key != null && match.key !== event.key) return false;
  return match.code != null || match.key != null;
}

export function eventMatches(entry, event) {
  if (!entry.match) return false;
  const list = Array.isArray(entry.match) ? entry.match : [entry.match];
  return list.some((match) => singleMatch(match, event));
}

// 키 토큰을 플랫폼 표기로 바꾼다. macOS는 기호, 그 밖은 영문 이름.
const MAC_TOKENS = { Ctrl: "⌘", Alt: "⌥", Shift: "⇧", Enter: "↩", Backspace: "⌫", Delete: "⌦", Esc: "esc" };

export function formatToken(token) {
  return IS_MAC ? (MAC_TOKENS[token] || token) : token;
}

export function formatCombo(combo) {
  return combo.map(formatToken).join(IS_MAC ? "" : "+");
}

export function formatKeys(entry) {
  return (entry.keys || []).map(formatCombo).join(" / ");
}

// 툴바 tooltip용: 컨텍스트와 동작/인자로 첫 키 조합을 찾는다.
export function shortcutFor(context, action, arg) {
  const entry = KEYMAP.find((item) => entryInContext(item, context) && item.action === action && (arg === undefined || item.arg === arg));
  return entry ? formatCombo(entry.keys[0]) : "";
}

export function shortcutById(id) {
  const entry = KEYMAP.find((item) => item.id === id);
  return entry ? formatKeys(entry) : "";
}

// 도움말에 보일 항목: 현재 탭 + 전역 (+ overlay 탭이면 대사 편집 팝업)
export function helpEntriesFor(activeTab) {
  const contexts = new Set(["global", activeTab]);
  if (activeTab === "overlay") contexts.add("floatingEditor");
  return KEYMAP.filter((entry) => entryVisibleOnPlatform(entry) && contextsOf(entry).some((context) => contexts.has(context)));
}
