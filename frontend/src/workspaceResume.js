// 서버 재시작·새로고침 뒤 다시 열 프로젝트를 탭 단위 저장소(sessionStorage)에서 고른다.
// App.jsx의 시작 effect에서 그대로 옮겼고 proof/src/test_workspace_resume.mjs가 검사한다. 다른 모듈을 import하지 않는다.

export const RESUME_KEY = "comic-translator-resume-after-restart";
// 새로고침해도 열려 있던 프로젝트로 돌아가도록 탭 단위(sessionStorage)로 기억한다.
export const OPEN_WORKSPACE_KEY = "comic-translator-open-workspace";

// 서버 재시작 기록(RESUME_KEY)은 한 번 읽고 지운다. 없으면 열려 있던 프로젝트(OPEN_WORKSPACE_KEY)를 쓴다.
// win은 window다. 저장소 접근 자체가 막혀 예외가 나도 아무것도 다시 열지 않도록 접근을 try 안에서 한다.
// reopened: 재시작 기록이 아니라 열려 있던 프로젝트를 다시 여는 경우
export function readResumeTarget(win) {
  let resume = null;
  let reopened = false;
  try {
    resume = JSON.parse(win.sessionStorage.getItem(RESUME_KEY) || "null");
    win.sessionStorage.removeItem(RESUME_KEY);
    // 검토·미리보기 링크는 자기 세션을 직접 연다
    const query = new URLSearchParams(win.location.search);
    if (!resume?.session_id && !query.get("generation_review") && !query.get("page_preview")) {
      resume = JSON.parse(win.sessionStorage.getItem(OPEN_WORKSPACE_KEY) || "null");
      reopened = Boolean(resume?.session_id);
    }
  } catch { resume = null; }
  return { resume, reopened };
}
