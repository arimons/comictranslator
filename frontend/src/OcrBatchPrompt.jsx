// 수동 작업(페이지 확인)이 60%, 80% 진행됐을 때 OCR·번역을 미리 돌릴지 묻는 알림과 진행 표시.
// 화면을 가리지 않고(비차단), 번역하는 동안에도 다른 페이지는 계속 편집할 수 있다.
export const OCR_PROMPT_MILESTONES = [60, 80];

// autoStart가 켜진 프로젝트는 묻지 않고 바로 시작하므로 알림 대신 진행 표시에 끄기 버튼만 둔다.
export default function OcrBatchPrompt({ show, pending, percent, promptOff, onPromptOff, onStart, onLater, onAutoStart, autoStart, onAutoOff, running, progress, selectedLocked }) {
  if (running) {
    return (
      <div className="ocr-batch-toast running" role="status">
        <strong>{autoStart ? "자동 " : "백그라운드 "}번역 중 {progress.done}/{progress.total}</strong>
        {progress.failed ? <span className="ocr-batch-failed"> · 실패 {progress.failed}</span> : null}
        {selectedLocked ? <small>이 페이지는 번역이 끝날 때까지 편집할 수 없습니다.</small> : <small>다른 페이지는 계속 편집할 수 있습니다.</small>}
        {autoStart ? <button className="ocr-batch-auto-off" onClick={onAutoOff} title="다음 묶음부터 다시 묻습니다. 지금 번역은 계속됩니다.">자동 번역 끄기</button> : null}
      </div>
    );
  }
  if (!show) return null;
  return (
    <div className="ocr-batch-toast" role="alertdialog" aria-label="번역 준비 알림">
      <strong>수동 작업이 {percent}% 진행됐습니다</strong>
      <p>확인한 {pending}장을 미리 OCR·번역해 둘까요? 남은 페이지를 확인하는 동안 번역이 끝납니다. 번역하는 동안에도 다른 페이지는 계속 편집할 수 있습니다. LLM 사용 비용이 발생합니다.</p>
      <div className="ocr-batch-actions">
        <button onClick={onLater}>나중에</button>
        <button onClick={onAutoStart} title="이 프로젝트는 다음부터 60%, 80%에서 묻지 않고 바로 번역합니다. 번역 중 표시에서 끌 수 있습니다.">앞으로 묻지 않고 번역</button>
        <button className="primary" onClick={onStart}>지금 번역</button>
      </div>
      <label className="ocr-batch-size">
        <input type="checkbox" checked={promptOff} onChange={(event) => onPromptOff(event.target.checked)} />
        <span>이 알림을 더 받지 않음</span>
      </label>
    </div>
  );
}
