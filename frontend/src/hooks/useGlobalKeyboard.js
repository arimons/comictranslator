import { useEffect, useRef, useState } from "react";

// 편집기 전역 키보드: keydown을 캡처 단계에서 받아 App의 단축키 디스패처(keyHandlerRef.current)로 넘기고,
// 캔버스를 누르면 사이드 패널 입력칸의 포커스를 푼다. App.jsx에서 그대로 옮겼다.
// 디스패처 자체(shortcutActions)는 편집 상태 대부분을 쓰므로 App에 남긴다.
export default function useGlobalKeyboard(keyHandlerRef) {
  useEffect(() => {
    const onKeyDown = (event) => {
      // 입력칸 밖의 ⌘A/Ctrl+A는 화면 글자 전체 선택만 일으키고 클릭을 방해하므로 막는다
      const target = event.target;
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName) || target?.isContentEditable;
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.code === "KeyA" && !typing) {
        event.preventDefault();
        return;
      }
      keyHandlerRef.current?.(event);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);

  // 미리보기 캔버스를 누르면 사이드 패널의 입력창에 남은 포커스를 풀어 방향키가 페이지 이동에 쓰이게 한다.
  useEffect(() => {
    const onPointerDown = (event) => {
      if (!event.target?.closest?.("#stageHost, #overlayStageHost")) return;
      const active = document.activeElement;
      if (!active || active === document.body || active.closest?.(".floating-text-editor")) return;
      const tag = active.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || active.isContentEditable) active.blur();
    };
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => window.removeEventListener("pointerdown", onPointerDown, true);
  }, []);
}

// 단축키로 바꾼 값을 잠깐 띄우는 알림. 상태 표시줄에도 같은 문구를 남긴다. App.jsx에서 그대로 옮겼다.
export function useShortcutToast(setStatus) {
  const [shortcutToast, setShortcutToast] = useState(null);
  const shortcutToastTimerRef = useRef(null);

  function showShortcutToast(text) {
    setStatus(text);
    setShortcutToast(text);
    if (shortcutToastTimerRef.current) window.clearTimeout(shortcutToastTimerRef.current);
    shortcutToastTimerRef.current = window.setTimeout(() => setShortcutToast(null), 1600);
  }

  useEffect(() => () => {
    if (shortcutToastTimerRef.current) window.clearTimeout(shortcutToastTimerRef.current);
  }, []);

  return { shortcutToast, showShortcutToast };
}
