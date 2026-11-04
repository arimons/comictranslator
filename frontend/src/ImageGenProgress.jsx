import { useEffect, useState } from "react";

// 이미지 생성 모델 통번역(재생성) 진행 표시. 화면을 가리지 않고(비차단), 생성하는 동안 다른 페이지를 계속 편집할 수 있다.
// 생성 중인 페이지만 끝날 때까지 편집을 막는다. 끝나면 결과 보기·닫기를 고른다.
export default function ImageGenProgress({ job, selectedLocked, onShow, onClose }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!job?.running) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [job?.running]);
  if (!job) return null;
  const elapsed = Math.max(0, Math.round(((job.running ? now : job.finishedAt || now) - job.startedAt) / 1000));
  const clock = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  const pages = job.names.length === 1 ? job.names[0] : `${job.names.length}장`;
  const recent = job.logs.slice(-4);
  return (
    <div className={`ocr-batch-toast image-gen-toast${job.running ? " running" : ""}`} role="status" aria-live="polite">
      <div className="image-gen-head">
        <strong>
          {job.running ? `이미지 통번역 중 · ${pages}` : job.failed ? `이미지 통번역 끝남 · 실패 ${job.failed}` : `이미지 통번역 완료 · ${pages}`}
        </strong>
        {!job.running ? <button className="icon-button" onClick={onClose} title="닫기">×</button> : null}
      </div>
      <small className="image-gen-meta">{job.model} · {clock}{job.running ? "" : ` · 생성 ${job.generated}개`}</small>
      {recent.length ? <ul className="image-gen-log">{recent.map((line, index) => <li key={`${index}-${line}`}>{line}</li>)}</ul> : null}
      {job.running
        ? (selectedLocked
          ? <small>이 페이지는 생성이 끝날 때까지 편집할 수 없습니다. 다른 페이지로 넘어가 편집해도 됩니다.</small>
          : <small>생성은 서버에서 계속됩니다. 다른 페이지는 계속 편집할 수 있습니다.</small>)
        : <div className="ocr-batch-actions">
            {job.generated && job.ids.length ? <button className="primary" onClick={onShow}>결과 보기</button> : null}
          </div>}
    </div>
  );
}
