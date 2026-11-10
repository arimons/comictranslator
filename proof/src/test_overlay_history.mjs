import assert from "node:assert/strict";
import { loadFrontendModule } from "./frontend_module_loader.mjs";

const {
  OVERLAY_HISTORY_LIMIT, OVERLAY_HISTORY_MERGE_MS, emptyOverlayHistory, recordOverlayHistory, takeOverlayHistoryStep,
} = await loadFrontendModule("frontend/src/overlayHistory.js");

const state = (n) => ({ regions: { a: { font_size: n } } });

// 같은 key의 연속 변경은 묶음 시간 안이면 한 칸, 시간이 지나면 새 칸이다
const history = emptyOverlayHistory();
recordOverlayHistory(history, state(10), "size:a", 1000);
recordOverlayHistory(history, state(11), "size:a", 1000 + OVERLAY_HISTORY_MERGE_MS - 1);
assert.equal(history.undo.length, 1);
recordOverlayHistory(history, state(12), "size:a", 1000 + OVERLAY_HISTORY_MERGE_MS * 3);
assert.equal(history.undo.length, 2);

// 회전은 시간과 상관없이 묶고, key가 없으면 매번 새 칸이다
recordOverlayHistory(history, state(13), "rotate:a", 0);
recordOverlayHistory(history, state(14), "rotate:a", 999999);
assert.equal(history.undo.length, 3);
recordOverlayHistory(history, state(15), null, 1);
recordOverlayHistory(history, state(16), null, 1);
assert.equal(history.undo.length, 5);

// 같은 스냅샷은 두 번 쌓지 않는다(updater 두 번 호출)
recordOverlayHistory(history, state(16), null, 2);
assert.equal(history.undo.length, 5);

// 기록한 스냅샷은 복사본이다
const source = state(20);
recordOverlayHistory(history, source, null, 3);
source.regions.a.font_size = 99;
assert.equal(history.undo.at(-1).regions.a.font_size, 20);

// Undo는 마지막 스냅샷을 꺼내 지금 상태를 Redo로 옮기고, 새 기록은 Redo를 비운다
const undone = takeOverlayHistoryStep(history, -1, state(30));
assert.equal(undone.regions.a.font_size, 20);
assert.equal(history.redo.length, 1);
assert.equal(history.lastKey, "");
const redone = takeOverlayHistoryStep(history, 1, state(20));
assert.equal(redone.regions.a.font_size, 30);
assert.equal(history.redo.length, 0);
takeOverlayHistoryStep(history, -1, state(30));
recordOverlayHistory(history, state(40), null, 4);
assert.equal(history.redo.length, 0);

// 상한을 넘으면 오래된 기록부터 버린다
const capped = emptyOverlayHistory();
for (let i = 0; i < OVERLAY_HISTORY_LIMIT + 5; i += 1) recordOverlayHistory(capped, state(i), null, i);
assert.equal(capped.undo.length, OVERLAY_HISTORY_LIMIT);
assert.equal(capped.undo[0].regions.a.font_size, 5);

console.log("글자 입히기 편집 기록: 연속 변경 묶기·회전·중복 스냅샷·복사·Undo/Redo·상한 통과");
