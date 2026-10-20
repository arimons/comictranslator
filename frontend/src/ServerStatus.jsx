import { useEffect, useRef, useState } from "react";

export default function ServerStatus() {
  const [state, setState] = useState({ color: "yellow", label: "서버 연결 확인 중" });
  const checkRef = useRef(null);
  // 이 화면을 연 시점의 번들 버전. 서버가 새 번들을 알려 주면 새로고침이 필요하다.
  const bundleRef = useRef(null);
  const restartingRef = useRef(false);
  useEffect(() => {
    let disposed = false, pending = false, failures = 0, controller;
    const check = async () => {
      if (pending || document.hidden || restartingRef.current) return;
      pending = true;
      controller = new AbortController();
      const started = performance.now();
      const timeout = setTimeout(() => controller.abort(), 3000);
      try {
        const response = await fetch("/api/health", { signal: controller.signal, cache: "no-store" });
        const health = response.ok ? await response.json() : null;
        if (health?.status !== "ok") throw new Error("unavailable");
        failures = 0;
        const latency = Math.round(performance.now() - started);
        if (bundleRef.current == null) bundleRef.current = health.bundle ?? null;
        const stale = health.stale_code || [];
        if (disposed) return;
        if (stale.length) {
          setState({ color: "orange", badge: "서버 재시작 필요", label: `서버를 켠 뒤 코드가 바뀌었습니다. 클릭하면 재시작합니다 · ${stale.slice(0, 4).join(", ")}${stale.length > 4 ? ` 외 ${stale.length - 4}개` : ""}` });
        } else if (health.bundle != null && bundleRef.current != null && health.bundle !== bundleRef.current) {
          setState({ color: "blue", badge: "새로고침", reload: true, label: "화면 코드가 새로 빌드됐습니다. 클릭하면 새로고침합니다" });
        } else {
          setState({ color: latency >= 1200 ? "yellow" : "green", label: `서버 ${latency >= 1200 ? "응답 지연" : "정상 응답"} · ${latency}ms` });
        }
      } catch {
        failures += 1;
        if (!disposed) setState({ color: failures >= 2 ? "red" : "yellow", label: failures >= 2 ? "서버 연결 실패 · 자동 재확인 중" : "서버 응답을 확인하지 못함 · 재확인 중" });
      } finally { clearTimeout(timeout); pending = false; }
    };
    checkRef.current = check;
    check();
    const timer = setInterval(check, 5000);
    document.addEventListener("visibilitychange", check);
    return () => { disposed = true; clearInterval(timer); controller?.abort(); document.removeEventListener("visibilitychange", check); };
  }, []);
  // 서버가 자기 프로세스를 같은 명령으로 다시 띄운다. 진행 중인 작업이 있으면 한 번 더 묻는다.
  async function restartServer() {
    if (!window.confirm("서버를 재시작할까요? 몇 초 걸립니다.")) return;
    try {
      let response = await fetch("/api/server/restart", { method: "POST" });
      if (response.status === 409) {
        const detail = (await response.json()).detail;
        if (!window.confirm(`${detail}\n그래도 재시작할까요?`)) return;
        response = await fetch("/api/server/restart?force=true", { method: "POST" });
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (error) {
      setState({ color: "red", label: `재시작 요청 실패 · ${error.message}` });
      return;
    }
    restartingRef.current = true;
    setState({ color: "yellow", badge: "재시작 중…", label: "서버를 재시작하는 중입니다" });
    await new Promise((resolve) => setTimeout(resolve, 1200));
    for (let i = 0; i < 30; i += 1) {
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        if (response.ok) break;
      } catch { /* 아직 뜨는 중 */ }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    restartingRef.current = false;
    checkRef.current?.();
  }

  function onClick() {
    if (state.reload) window.location.reload();
    else if (state.color === "orange") restartServer();
    else checkRef.current?.();
  }

  return <button type="button" className={`server-status server-status-${state.color}`} data-health={state.color}
    onClick={onClick} aria-label={state.label}
    title={`${state.label}\n5초마다 서버 연결·코드 버전 확인 · 클릭하여 다시 확인\n분석 작업의 진행·성공 여부와는 별개입니다.`}>
    <span aria-hidden="true" />
    {state.badge ? <em>{state.badge}</em> : null}
  </button>;
}
