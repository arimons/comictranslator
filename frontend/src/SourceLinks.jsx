import { useEffect, useState } from 'react';
import { api } from './lib.js';

export default function SourceLinks({ sessionId, disabled, onChanged }) {
  const [folder,setFolder]=useState('');
  const [plan,setPlan]=useState(null);
  const [status,setStatus]=useState(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const load=()=>api(`/api/sources/${encodeURIComponent(sessionId)}`).then(setStatus).catch(e=>setError(e.message));
  useEffect(()=>{let active=true;api(`/api/sources/${encodeURIComponent(sessionId)}`).then(s=>{if(active)setStatus(s);}).catch(()=>{});return()=>{active=false;};},[sessionId]);
  async function preview(){setBusy(true);setError('');setPlan(null);try{setPlan(await api('/api/sources/preview',{method:'POST',body:JSON.stringify({session_id:sessionId,folder})}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function apply(){setBusy(true);setError('');try{await api('/api/sources/relink',{method:'POST',body:JSON.stringify({session_id:sessionId,folder,revision:plan.revision})});setPlan(null);await load();await onChanged?.();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <details className="source-link-control" onToggle={e=>{if(e.currentTarget.open)load();}}>
    <summary onClick={e=>{if(disabled||busy)e.preventDefault();}}>{status?.missing?'원본 폴더 재연결 필요':'원본 연결'}</summary>
    <div className="source-link-body">
      <strong>이 기기의 원본 폴더</strong>
      <p>일반 페이지는 원본을 직접 읽습니다. 분할·크롭 결과와 기존 분석·편집 값은 유지합니다.</p>
      {status&&<p role="status">원본 연결 {status.connected}장 · 이전 사본 사용 {status.fallback}장 · 원본 없음 {status.missing}장 · 프로젝트/이전 입력 {status.owned}장</p>}
      <label>원본 폴더 경로<input aria-label="원본 폴더 경로" placeholder="예: /Volumes/만화/작품/3권" value={folder} disabled={busy||disabled} onChange={e=>{setFolder(e.target.value);setPlan(null);}} /></label>
      <button disabled={!folder.trim()||busy||disabled} onClick={preview}>{busy?'확인 중…':'원본 확인'}</button>
      {plan&&<div><p>동일 파일 {plan.matched}장 · 분할/크롭 보관 {plan.owned}장 · 미일치 {plan.unmatched}장</p><p>SHA-256이 일치한 페이지만 연결합니다. 미일치 페이지의 기존 입력은 유지합니다.</p><button className="primary" disabled={!plan.matched||busy||disabled} onClick={apply}>일치한 {plan.matched}장 연결</button></div>}
      {error&&<p role="alert">{error}</p>}
      <small>맥 미니에서도 이 메뉴에서 폴더를 지정하세요. 경로 설정은 기기별로 저장됩니다. 기존 입력 캐시는 자동 삭제하지 않습니다.</small>
    </div>
  </details>;
}
