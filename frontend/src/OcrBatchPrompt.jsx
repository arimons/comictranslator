// 확인한 페이지가 일정 수 쌓이면 OCR·번역을 백그라운드로 돌릴지 묻는 알림과 진행 표시.
// 화면을 가리지 않고(비차단), 번역하는 동안에도 다른 페이지는 계속 편집할 수 있다.
export const OCR_BATCH_SIZES = [8, 10, 12, 16];

export default function OcrBatchPrompt({ show, pending, size, onSize, onStart, onLater, running, progress, selectedLocked }) {
  if (running) {
    return (
      <div className="ocr-batch-toast running" role="status">
        <strong>백그라운드 번역 중 {progress.done}/{progress.total}</strong>
        {progress.failed ? <span className="ocr-batch-failed"> · 실패 {progress.failed}</span> : null}
        {selectedLocked ? <small>이 페이지는 번역이 끝날 때까지 편집할 수 없습니다.</small> : <small>다른 페이지는 계속 편집할 수 있습니다.</small>}
      </div>
    );
  }
  if (!show) return null;
  return (
    <div className="ocr-batch-toast" role="alertdialog" aria-label="번역 준비 알림">
      <strong>확인한 페이지 {pending}장이 번역 준비됐습니다</strong>
      <p>OCR·번역을 백그라운드로 시작할까요? 번역하는 동안에도 다른 페이지는 계속 편집할 수 있습니다. LLM 사용 비용이 발생합니다.</p>
      <div className="ocr-batch-actions">
        <button onClick={onLater}>나중에</button>
        <button className="primary" onClick={onStart}>지금 번역</button>
      </div>
      <label className="ocr-batch-size">
        <span>알림 기준</span>
        <select value={size} onChange={(event) => onSize(Number(event.target.value))}>
          {OCR_BATCH_SIZES.map((value) => <option key={value} value={value}>{value}장마다</option>)}
          <option value={0}>알리지 않음</option>
        </select>
      </label>
    </div>
  );
}
