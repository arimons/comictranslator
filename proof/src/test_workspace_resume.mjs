import assert from "node:assert/strict";
import { loadFrontendModule } from "./frontend_module_loader.mjs";

const { RESUME_KEY, OPEN_WORKSPACE_KEY, readResumeTarget } = await loadFrontendModule("frontend/src/workspaceResume.js");

function fakeWindow(items, search = "") {
  const store = new Map(Object.entries(items).map(([key, value]) => [key, JSON.stringify(value)]));
  return {
    store,
    location: { search },
    sessionStorage: {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      removeItem: (key) => { store.delete(key); },
    },
  };
}

// 서버 재시작 기록이 있으면 그것을 쓰고 한 번 읽은 뒤 지운다. 열려 있던 프로젝트 기록은 남긴다
const restart = fakeWindow({ [RESUME_KEY]: { session_id: "s1", tab: "ocr" }, [OPEN_WORKSPACE_KEY]: { session_id: "s2" } });
assert.deepEqual(readResumeTarget(restart), { resume: { session_id: "s1", tab: "ocr" }, reopened: false });
assert.equal(restart.store.has(RESUME_KEY), false);
assert.equal(restart.store.has(OPEN_WORKSPACE_KEY), true);

// 재시작 기록이 없으면 새로고침 전에 열려 있던 프로젝트를 다시 연다
assert.deepEqual(readResumeTarget(fakeWindow({ [OPEN_WORKSPACE_KEY]: { session_id: "s2", tab: "guidance" } })), { resume: { session_id: "s2", tab: "guidance" }, reopened: true });

// 검토·미리보기 링크로 열었으면 열려 있던 프로젝트로 돌아가지 않는다
assert.equal(readResumeTarget(fakeWindow({ [OPEN_WORKSPACE_KEY]: { session_id: "s2" } }, "?generation_review=s3")).resume, null);
assert.equal(readResumeTarget(fakeWindow({ [OPEN_WORKSPACE_KEY]: { session_id: "s2" } }, "?page_preview=s3")).resume, null);

// 아무 기록도 없거나, 깨진 값이거나, 저장소 접근이 막혀 있으면 다시 열지 않는다
assert.deepEqual(readResumeTarget(fakeWindow({})), { resume: null, reopened: false });
const broken = fakeWindow({});
broken.store.set(RESUME_KEY, "{");
assert.equal(readResumeTarget(broken).resume, null);
assert.equal(readResumeTarget({ location: { search: "" }, get sessionStorage() { throw new Error("blocked"); } }).resume, null);

console.log("작업 화면 다시 열기: 재시작 기록 우선·한 번만 사용, 열려 있던 프로젝트, 검토 링크 제외, 저장소 오류 통과");
