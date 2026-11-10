import { useEffect } from "react";
import { writeSessionSettings } from "../lib.js";
import { OPEN_WORKSPACE_KEY, RESUME_KEY, readResumeTarget } from "../workspaceResume.js";

// 서버 재시작·새로고침 뒤 보던 프로젝트로 돌아가기와, 프로젝트별 마지막으로 보던 페이지 기억.
// App.jsx에서 그대로 옮겼다. effect 세 개는 원래 순서대로 App이 이 훅을 부르는 자리에서 돈다.
export default function useWorkspaceResume({
  workspaceStarted, sessionId, selectedId, images, activeTab, selectedIdRef,
  setSelectedId, setActiveTab, setStatus, openSessionForSetup, openRecentSession,
}) {
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
    const { resume, reopened } = readResumeTarget(window);
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

  return { reloadAfterServerRestart, forgetOpenWorkspace };
}
