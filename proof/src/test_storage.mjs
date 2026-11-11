import assert from "node:assert/strict";
import { loadFrontendModule } from "./frontend_module_loader.mjs";

const { readStored, writeStored } = await loadFrontendModule("frontend/src/storage.js");

// 브라우저 localStorage 대신 쓰는 메모리 저장소
const memory = new Map();
globalThis.window = {
  localStorage: {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => { memory.set(key, String(value)); },
  },
};

// 값이 없으면 기본값, 쓴 값은 JSON으로 되읽는다
assert.equal(readStored("ct.flag", true), true);
writeStored("ct.flag", false);
assert.equal(memory.get("ct.flag"), "false");
assert.equal(readStored("ct.flag", true), false);

// 검사 함수를 통과하지 못하거나 JSON이 깨졌으면 기본값을 쓴다
memory.set("ct.list", "\"text\"");
assert.deepEqual(readStored("ct.list", [], Array.isArray), []);
memory.set("ct.broken", "{");
assert.equal(readStored("ct.broken", 0), 0);

// 저장소 접근이 막혀 있어도 예외를 던지지 않는다
globalThis.window = { get localStorage() { throw new Error("blocked"); } };
assert.equal(readStored("ct.flag", "fallback"), "fallback");
assert.doesNotThrow(() => writeStored("ct.flag", 1));

console.log("화면 설정 저장소: 기본값·JSON 왕복·검사 실패·깨진 값·저장소 차단 통과");
