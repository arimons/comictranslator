import { useEffect, useRef, useState } from "react";

export default function ServerStatus() {
  const [state, setState] = useState({ color: "yellow", label: "서버 연결 확인 중" });
  const checkRef = useRef(null);
  useEffect(() => {
    let disposed = false, pending = false, failures = 0, controller;
    const check = async () => {
      if (pending || document.hidden) return;
      pending = true;
      controller = new AbortController();
      const started = performance.now();
      const timeout = setTimeout(() => controller.abort(), 3000);
      try {
        const response = await fetch("/api/health", { signal: controller.signal, cache: "no-store" });
        if (!response.ok || (await response.json()).status !== "ok") throw new Error("unavailable");
        failures = 0;
        const latency = Math.round(performance.now() - started);
        if (!disposed) setState({ color: latency >= 1200 ? "yellow" : "green", label: `서버 ${latency >= 1200 ? "응답 지연" : "정상 응답"} · ${latency}ms` });
      } catch {
        failures += 1;
        if (!disposed) setState({ color: failures >= 2 ? "red" : "yellow", label: failures >= 2 ? "서버 연결 실패 · 자동 재확인 중" : "서버 응답을 확인하지 못함 · 재확인 중" });
      } finally { clearTimeout(timeout); pending = false; }
    };
    checkRef.current = check;
    check();
    const timer = setInterval(check, 10000);
    document.addEventListener("visibilitychange", check);
    return () => { disposed = true; clearInterval(timer); controller?.abort(); document.removeEventListener("visibilitychange", check); };
  }, []);
  return <button type="button" className={`server-status server-status-${state.color}`} data-health={state.color}
    onClick={() => checkRef.current?.()} aria-label={state.label}
    title={`${state.label}\n10초마다 서버 연결 확인 · 클릭하여 다시 확인\n분석 작업의 진행·성공 여부와는 별개입니다.`}>
    <span aria-hidden="true" />
  </button>;
}
