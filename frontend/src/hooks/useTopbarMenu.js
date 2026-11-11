import { useEffect, useRef, useState } from "react";

// 영역 분석 상단 바의 펼침 메뉴("history" | "more" | null). 메뉴 바깥을 누르거나 Esc를 누르면 닫는다.
// guidanceMenuRef는 메뉴 버튼들을 감싼 요소에 붙인다. App.jsx에서 그대로 옮겼다.
export default function useTopbarMenu() {
  const [guidanceMenu, setGuidanceMenu] = useState(null);
  const guidanceMenuRef = useRef(null);
  useEffect(() => {
    if (!guidanceMenu) return undefined;
    const onDown = (event) => { if (!guidanceMenuRef.current?.contains(event.target)) setGuidanceMenu(null); };
    const onKey = (event) => { if (event.key === "Escape") setGuidanceMenu(null); };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [guidanceMenu]);
  return { guidanceMenu, setGuidanceMenu, guidanceMenuRef };
}
