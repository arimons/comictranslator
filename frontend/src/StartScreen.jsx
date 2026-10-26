import { useEffect, useRef } from "react";
import ServerStatus from "./ServerStatus.jsx";

function FolderIcon() {
  return <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z" /><path d="M3 10h18" /></svg>;
}

function workDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "작업일 정보 없음" : new Intl.DateTimeFormat("ko-KR", {
    year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

export default function StartScreen({
  images, recentSessions, projectName, onProjectNameChange, saveProjectName,
  recentLoadState, loadRecentSessions,
  importNameFocusRequest,
  pagePreparationControl,
  folderPath, setFolderPath, fileRef, uploadFiles, uploadFromInput, importPath,
  inputBusy, analyzeBusy, analyzeWorkers, setAnalyzeWorkers, analysisPolicy, setAnalysisPolicy,
  startRegionAnalysis, splitLandscape, setSplitLandscape, removeLoadedImage, openRecentSession, recentMenuId, setRecentMenuId,
  renameRecentSession, deleteRecentSession, status, setStatus,
}) {
  const folderRef = useRef(null);
  const nameRef = useRef(null);
  const handledFocusRequest = useRef(importNameFocusRequest);
  const busy = inputBusy || analyzeBusy;
  const analyzed = images.filter((image) => image.has_analysis).length;
  useEffect(() => {
    // 업로드 중에는 이름 입력란이 disabled다. 완료된 뒤 한 번만 이동해 재조회가 편집을 방해하지 않게 한다.
    if (inputBusy || handledFocusRequest.current === importNameFocusRequest) return;
    handledFocusRequest.current = importNameFocusRequest;
    nameRef.current?.focus();
    nameRef.current?.select();
  }, [inputBusy, importNameFocusRequest]);
  // 메뉴 작업의 오류도 홈 화면에 표시한다. 여기서는 분석·비용 호출을 직접 실행하지 않는다.
  const safely = (action) => Promise.resolve().then(action).catch((err) => setStatus(String(err.message || err)));

  return (
    <div className="app-shell import-mode">
      <header className="app-topbar">
        <div className="workspace-brand-group"><div className="app-brand">Comic Translator Studio</div><ServerStatus /></div>
        <a className="app-manual-link" href="/manual" target="_blank" rel="noopener">사용 설명서 ↗</a>
      </header>
      <main className="home-scroll">
        <div className="home-container">
          <div className="import-title">
            <h1>어떤 작품을 작업할까요?</h1>
            <p>새 이미지를 불러오거나, 최근 프로젝트를 이어서 작업하세요.</p>
          </div>
          <div className="import-screen">
            <section className="import-primary" aria-label="이미지 불러오기 및 최근 프로젝트">
              <section className="import-card" aria-labelledby="import-heading">
                <h2 id="import-heading">새 작업 시작</h2>
                <div className={`import-drop${inputBusy ? " is-loading" : ""}`}
                  onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = busy ? "none" : "copy"; }}
                  onDrop={(event) => { event.preventDefault(); if (!busy) uploadFiles(Array.from(event.dataTransfer.files || [])); }}>
                  <div className="import-drop-icon"><FolderIcon /></div>
                  <div className="import-drop-copy"><strong>{inputBusy ? "이미지를 불러오는 중…" : "만화 이미지를 여기에 놓으세요"}</strong><span>파일이나 폴더를 선택해도 됩니다.</span></div>
                  <div className="import-buttons">
                    <button className="primary" onClick={() => fileRef.current?.click()} disabled={busy}>파일 선택</button>
                    <button onClick={() => folderRef.current?.click()} disabled={busy}>폴더 선택</button>
                  </div>
                </div>
                <input ref={fileRef} type="file" multiple accept="image/*" hidden onChange={(event) => uploadFromInput(event.currentTarget)} />
                <input ref={folderRef} type="file" multiple webkitdirectory="" directory="" accept="image/*" hidden onChange={(event) => uploadFromInput(event.currentTarget)} />
                <p className="home-hint">파일·폴더 선택은 프로젝트에 업로드합니다. 복사 없이 사용하려면 아래 원본 폴더 경로를 지정하세요.</p>
                <details className="home-path" open>
                  <summary>원본 폴더 경로로 불러오기 · 복사 없이 참조</summary>
                  <div className="import-folder">
                    <input aria-label="로컬 폴더 경로" value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder="예: /Users/이름/만화/작품명" disabled={busy} onKeyDown={(event) => { if (event.key === "Enter" && folderPath.trim() && !busy) importPath(); }} />
                    <button onClick={importPath} disabled={busy || !folderPath.trim()}>불러오기</button>
                  </div>
                </details>
                {images.length > 0 && <div className="import-selection">
                  <div className="selection-summary"><strong>작업할 이미지 {images.length}장</strong><span>분석결과 있음 {analyzed}장</span></div>
                  <details className="selected-files">
                    <summary>선택한 이미지 확인·제외</summary>
                    <p className="home-hint">브라우저 파일 선택은 원래 경로를 알 수 없어 프로젝트에 보관합니다. 복사 없이 사용하려면 원본 폴더 경로로 불러오세요.</p><p className="home-hint">파일명의 숫자 순서로 정렬됩니다. 예: 1, 2, 10</p>
                    <div className="loaded-file-list">
                      {images.map((image, index) => <div key={image.id} className="loaded-file-row">
                        <span>{index + 1}</span><strong title={image.name}>{image.name}{image.page_side && <em>{image.page_side}</em>}</strong>
                        <button className="icon-button" onClick={() => removeLoadedImage(image.id)} disabled={busy} aria-label={`${image.name} 목록에서 제외`} title="작업 목록에서 제외">×</button>
                      </div>)}
                    </div>
                  </details>
                </div>}
                {pagePreparationControl && <div className="home-page-preparation">{pagePreparationControl}</div>}
              </section>

              <section className="home-recent" aria-labelledby="recent-heading">
                <div className="section-title-row"><h2 id="recent-heading">최근 프로젝트</h2><span className="home-hint">최근 작업순</span></div>
                {recentLoadState === "loading" ? <div className="home-empty" role="status">최근 프로젝트를 불러오는 중…</div>
                  : recentLoadState === "error" ? <div className="home-empty"><strong>최근 프로젝트를 불러오지 못했습니다</strong><button onClick={loadRecentSessions}>다시 시도</button></div>
                  : recentSessions.length ? <div className="recent-project-grid">
                  {recentSessions.map((recent) => {
                    const title = recent.project_name || recent.source_folder_name || "이름 없는 프로젝트";
                    const date = recent.updated_at || recent.created_at;
                    return <article className="recent-project-card" key={recent.session_id}>
                      <button className="recent-project-open" onClick={() => safely(() => openRecentSession(recent))} disabled={busy} aria-label={`${title} 이어서 작업`}>
                        <div className="recent-project-cover">
                          {recent.thumbnail_url ? <img src={recent.thumbnail_url} alt="" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; }} /> : <FolderIcon />}
                          <span className="recent-page-count">{recent.image_count}장</span>
                        </div>
                        <div className="recent-project-info">
                          <h3 title={title}>{title}</h3>
                          <time dateTime={date} title="프로젝트와 저장된 작업 파일의 마지막 변경 시각">{workDate(date)}</time>
                          <span className="recent-project-progress">결과 생성 <strong>{recent.final_count ?? 0} / {recent.image_count}장</strong></span>
                          <span className="recent-project-analysis">분석 {recent.analysis_count ?? 0} / {recent.image_count}장</span>
                        </div>
                      </button>
                      <button className="recent-session-menu-button" type="button" disabled={busy}
                        onClick={() => setRecentMenuId(recentMenuId === recent.session_id ? "" : recent.session_id)}
                        aria-label={`${title} 프로젝트 메뉴`} aria-expanded={recentMenuId === recent.session_id} title="프로젝트 메뉴">···</button>
                      {recentMenuId === recent.session_id && <>
                        <button className="recent-menu-dismiss" aria-label="프로젝트 메뉴 닫기" onClick={() => setRecentMenuId("")} />
                        <div className="recent-session-menu" onKeyDown={(event) => { if (event.key === "Escape") setRecentMenuId(""); }}>
                          <button onClick={() => safely(() => renameRecentSession(recent))}>이름 변경</button>
                          <button className="danger-text" onClick={() => safely(() => deleteRecentSession(recent))}>프로젝트 삭제</button>
                        </div>
                      </>}
                    </article>;
                  })}
                </div> : <div className="home-empty"><FolderIcon /><strong>아직 작업한 프로젝트가 없습니다</strong><span>이미지를 불러오면 여기에서 다시 열 수 있어요.</span></div>}
                <p className="home-hint recent-note">결과 생성 수는 저장된 결과 이미지 기준이며, 수동 검토 완료를 뜻하지 않습니다.</p>
              </section>
            </section>

            <aside className="import-config" aria-labelledby="settings-heading">
              <h2 id="settings-heading">작업 준비</h2>
              <div className="field">
                <label htmlFor="home-project-name">프로젝트 이름 <span className="home-optional">선택</span></label>
                <input ref={nameRef} id="home-project-name" value={projectName} disabled={busy} placeholder="예: 작품명 1권"
                  onChange={(event) => onProjectNameChange(event.target.value)} onBlur={() => safely(() => saveProjectName())}
                  onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
                <p className="home-hint">파일명 또는 폴더명이 자동으로 채워집니다. 원하는 작품명으로 바꿔주세요.</p>
              </div>
              <fieldset className="analysis-policy" disabled={busy}>
                <legend>분석 방법</legend>
                <label className={`policy-option${analysisPolicy === "reuse" ? " selected" : ""}`}>
                  <input type="radio" name="analysis-policy" value="reuse" checked={analysisPolicy === "reuse"} onChange={() => setAnalysisPolicy("reuse")} />
                  <span><strong>기존 분석결과 활용</strong><small>저장된 결과를 활용하고, 없는 이미지만 분석합니다.</small></span>
                </label>
                <label className={`policy-option${analysisPolicy === "refresh" ? " selected" : ""}`}>
                  <input type="radio" name="analysis-policy" value="refresh" checked={analysisPolicy === "refresh"} onChange={() => setAnalysisPolicy("refresh")} />
                  <span><strong>처음부터 다시 분석</strong><small>전체 이미지를 다시 분석합니다.<em>API 호출 비용이 발생할 수 있습니다.</em></small></span>
                </label>
              </fieldset>
              {(() => {
                const wideCount = images.filter((image) => image.wide_spread_candidate && !image.parent_id).length;
                if (!wideCount) return null;
                return <label className={`policy-option split-option${splitLandscape ? " selected" : ""}`}>
                  <input type="checkbox" checked={splitLandscape} disabled={busy} onChange={(event) => setSplitLandscape(event.target.checked)} />
                  <span><strong>가로 이미지 {wideCount}장을 두 쪽으로 나누기</strong><small>오른쪽 → 왼쪽 순서로 나눕니다. 표지처럼 가운데에 한 장만 있는 이미지는 가운데만 남깁니다. 분석 시작 때 적용되고 원본 파일은 그대로 둡니다.</small></span>
                </label>;
              })()}
              <details className="home-advanced"><summary>고급 설정</summary>
                <div className="field"><label htmlFor="home-workers">동시 분석 이미지 수</label><input id="home-workers" type="number" min="1" max="12" value={analyzeWorkers} disabled={busy} onChange={(event) => setAnalyzeWorkers(Math.max(1, Math.min(12, Number(event.target.value) || 1)))} /><p className="home-hint">기본 4장. 컴퓨터 성능에 따라 조절하세요.</p></div>
              </details>
              <div className="import-action">
                {images.length > 0 && <strong>{images.length}장 작업 준비</strong>}
                <button className="primary wide" onClick={startRegionAnalysis} disabled={!images.length || busy}>{analysisPolicy === "refresh" ? "다시 분석하고 열기" : "분석하고 작업 시작"}<span aria-hidden="true"> →</span></button>
                <p className="home-hint">새 분석에 필요한 외부 API 비용이 발생할 수 있습니다. 번역은 편집기에서 별도로 실행합니다.</p>
              </div>
            </aside>
          </div>
        </div>
      </main>
      <footer className="status-bar" role="status"><span className={`home-status-dot${inputBusy ? " busy" : ""}`} />{inputBusy ? "이미지 불러오는 중…" : status ? status.split("\n").filter(Boolean).at(-1) : "준비됨"}</footer>
    </div>
  );
}
