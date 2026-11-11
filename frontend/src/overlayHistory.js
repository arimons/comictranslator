// 글자 입히기 편집 기록(페이지 단위 Undo/Redo). 확정된 변경마다 직전 overlayGuidance 스냅샷을 쌓는다.
// App.jsx에서 옮겼고 proof/src/test_overlay_history.mjs가 검사한다. 기록 객체는 ref에 두고 제자리에서 고친다.
import { cloneJson } from "./lib.js";
import { guidanceEquals } from "./guidanceHistory.js";

// 글자 입히기 편집 기록 상한과, 같은 종류의 연속 변경을 한 칸으로 묶는 시간
export const OVERLAY_HISTORY_LIMIT = 100;
export const OVERLAY_HISTORY_MERGE_MS = 1500;

export function emptyOverlayHistory() {
  return { undo: [], redo: [], lastKey: "", lastAt: 0 };
}

// historyKey: 같은 key의 연속 변경(타이핑, 회전 드래그, 방향키 이동)은 기록 한 칸으로 묶는다. 회전은 시간과 상관없이 묶는다.
export function recordOverlayHistory(history, previous, historyKey, now = Date.now()) {
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

// Undo(direction < 0)·Redo 한 칸: 되돌아갈 스냅샷을 꺼내고 지금 상태(current)를 반대쪽에 쌓는다.
// 꺼낼 기록이 있는지는 부르는 쪽이 먼저 확인한다.
export function takeOverlayHistoryStep(history, direction, current) {
  const from = direction < 0 ? history.undo : history.redo;
  const target = from[from.length - 1];
  if (direction < 0) {
    history.undo = history.undo.slice(0, -1);
    history.redo = [...history.redo, current];
  } else {
    history.redo = history.redo.slice(0, -1);
    history.undo = [...history.undo, current];
  }
  history.lastKey = "";
  return target;
}
