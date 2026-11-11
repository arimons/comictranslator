import { useRef, useState } from "react";

// 화면을 잠그는 작업(input·analyze·guidance·ocr·overlay·export) 진행 표시와 종류별 중복 실행 잠금.
// runTask는 시작·완료·오류 문구를 상태 표시줄(setStatus)에 남긴다. App.jsx에서 그대로 옮겼다.
export default function useBusyTasks(setStatus) {
  const [busyTasks, setBusyTasks] = useState({});
  const taskLocksRef = useRef(new Set());

  async function runTask(kind, label, fn) {
    if (taskLocksRef.current.has(kind)) return;
    taskLocksRef.current.add(kind);
    setBusyTasks((current) => ({ ...current, [kind]: true }));
    setStatus(`${label}...`);
    try {
      const result = await fn();
      setStatus(result?.logs || `${label} 완료`);
      return result;
    } catch (err) {
      setStatus(String(err.message || err));
    } finally {
      taskLocksRef.current.delete(kind);
      setBusyTasks((current) => ({ ...current, [kind]: false }));
    }
  }

  return { busyTasks, setBusyTasks, taskLocksRef, runTask };
}
