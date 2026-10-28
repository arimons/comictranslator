import { useEffect, useState } from "react";
import { api } from "./lib.js";

const LAST_FOLDER_KEY = "ct.serverFolder";

// 서버(앱이 도는 컴퓨터)의 폴더를 둘러보고 고른다. 브라우저 파일 선택은 접속한 기기의 파일만 보여 주기 때문이다.
export default function ServerFolderPicker({ initialPath, onPick, onClose }) {
  const [listing, setListing] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function open(path) {
    setLoading(true);
    setError("");
    try {
      const data = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`);
      setListing(data);
      try { window.localStorage.setItem(LAST_FOLDER_KEY, data.path); } catch { /* 저장소를 못 써도 둘러보기는 된다 */ }
    } catch (err) {
      setError(String(err.message || err));
      // 처음 연 경로가 없으면 홈에서 다시 시작한다
      if (!listing && path) open("");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let start = initialPath || "";
    if (!start) {
      try { start = window.localStorage.getItem(LAST_FOLDER_KEY) || ""; } catch { start = ""; }
    }
    open(start);
    const onKey = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return <div className="glossary-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="glossary-dialog server-folder-dialog" role="dialog" aria-label="서버 폴더 선택">
      <h2>서버 폴더 선택</h2>
      <p className="home-hint">이 앱이 실행 중인 컴퓨터의 폴더입니다. 외장 디스크는 위쪽 버튼에 있습니다.</p>
      {listing && <div className="server-folder-roots">
        {listing.roots.map((root) => <button key={root.path} onClick={() => open(root.path)} disabled={loading} className={listing.path === root.path ? "active" : ""}>{root.name}</button>)}
      </div>}
      {listing && <div className="server-folder-path">
        <button onClick={() => open(listing.parent)} disabled={loading || !listing.parent} title="상위 폴더">↑</button>
        <code title={listing.path}>{listing.path}</code>
      </div>}
      {error && <p className="server-folder-error">{error}</p>}
      <div className="server-folder-list" aria-busy={loading}>
        {listing?.dirs.length === 0 && <div className="home-empty">하위 폴더 없음</div>}
        {listing?.dirs.map((dir) => <button key={dir.path} className="server-folder-row" onClick={() => open(dir.path)} disabled={loading}>📁 {dir.name}</button>)}
      </div>
      <div className="server-folder-actions">
        <span className="home-hint">{listing ? `이 폴더 이미지 ${listing.image_count}장 · 하위 폴더까지 불러옵니다` : "불러오는 중…"}</span>
        <button onClick={onClose}>취소</button>
        <button className="primary" onClick={() => onPick(listing.path)} disabled={!listing || loading}>이 폴더 불러오기</button>
      </div>
    </div>
  </div>;
}
