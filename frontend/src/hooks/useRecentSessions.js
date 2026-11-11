import { useRef, useState } from "react";
import { api } from "../lib.js";

// 홈은 12개만 먼저 받고, 목록 끝까지 스크롤하면 12개씩 이어 받는다.
const RECENT_PAGE_SIZE = 12;

// 홈 화면의 최근 프로젝트 목록: 정렬(브라우저에 기억), 이어 받기, 프로젝트 메뉴 열림 상태.
// App.jsx에서 그대로 옮겼다. 첫 목록은 App의 시작 effect가 loadRecentSessions()로 받는다.
export default function useRecentSessions() {
  const [recentSessions, setRecentSessions] = useState([]);
  const [recentLoadState, setRecentLoadState] = useState("loading");
  const [recentHasMore, setRecentHasMore] = useState(false);
  const [recentLoadingMore, setRecentLoadingMore] = useState(false);
  const recentCountRef = useRef(0);
  const [recentSort, setRecentSort] = useState(() => {
    try { return localStorage.getItem("recentSort") === "name" ? "name" : "recent"; } catch { return "recent"; }
  });
  const recentSortRef = useRef(recentSort);
  const [recentMenuId, setRecentMenuId] = useState("");

  function applyRecentSessions(sessions, hasMore) {
    recentCountRef.current = sessions.length;
    setRecentSessions(sessions);
    setRecentHasMore(Boolean(hasMore));
  }

  function loadRecentSessions(sort = recentSortRef.current) {
    setRecentLoadState("loading");
    api(`/api/sessions/recent?limit=${RECENT_PAGE_SIZE}&offset=0&sort=${sort}`).then((data) => {
      applyRecentSessions(data.sessions || [], data.has_more);
      setRecentLoadState("ready");
    }).catch(() => setRecentLoadState("error"));
  }

  // 이름 변경·삭제·홈 복귀 뒤 목록을 다시 받는다. 이미 불러온 개수만큼 받아 스크롤 위치가 줄어들지 않게 한다.
  function refreshRecentSessions() {
    const limit = Math.max(RECENT_PAGE_SIZE, recentCountRef.current);
    api(`/api/sessions/recent?limit=${limit}&offset=0&sort=${recentSortRef.current}`)
      .then((data) => applyRecentSessions(data.sessions || [], data.has_more))
      .catch(() => {});
  }

  function changeRecentSort(sort) {
    const next = sort === "name" ? "name" : "recent";
    recentSortRef.current = next;
    setRecentSort(next);
    try { localStorage.setItem("recentSort", next); } catch { /* 저장소를 못 쓰면 이번 방문에만 적용 */ }
    loadRecentSessions(next);
  }

  function loadMoreRecentSessions() {
    if (recentLoadingMore || !recentHasMore) return;
    setRecentLoadingMore(true);
    api(`/api/sessions/recent?limit=${RECENT_PAGE_SIZE}&offset=${recentCountRef.current}&sort=${recentSortRef.current}`).then((data) => {
      setRecentSessions((current) => {
        const seen = new Set(current.map((item) => item.session_id));
        const next = [...current, ...(data.sessions || []).filter((item) => !seen.has(item.session_id))];
        recentCountRef.current = next.length;
        return next;
      });
      setRecentHasMore(Boolean(data.has_more));
    }).catch(() => {}).finally(() => setRecentLoadingMore(false));
  }

  return {
    recentSessions, recentLoadState, recentHasMore, recentLoadingMore, recentSort, recentMenuId, setRecentMenuId,
    loadRecentSessions, refreshRecentSessions, changeRecentSort, loadMoreRecentSessions,
  };
}
