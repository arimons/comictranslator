// 플랫폼마다 달라지는 문자/이모지 대신 같은 굵기의 선 아이콘을 쓴다.
export default function GuidanceToolIcon({ tool }) {
  const paths = {
    select:<path d="M5 3v17l5-5 4 7 3-2-4-7h8Z" />,
    hand:<><path d="M8 12V6a1.5 1.5 0 0 1 3 0v5-7a1.5 1.5 0 0 1 3 0v7-5a1.5 1.5 0 0 1 3 0v6-3a1.5 1.5 0 0 1 3 0v7c0 4-3 6-6 6h-2c-2 0-3-1-4-3l-4-5a1.5 1.5 0 0 1 2-2l2 2" /></>,
    'tg-delete':<><rect x="3" y="4" width="15" height="15" rx="2" /><path d="M6 8h8M6 12h5M15 17h7" /></>,
    'tg-split':<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M12 2v20" strokeDasharray="2 3" /><path d="M6 9h3m6 0h3M6 15h3m6 0h3" /></>,
    wall:<><path d="M6 3v18M9 3v18M3 7h3m-3 5h3m-3 5h3M14 6h6v12h-6" /><path d="m14 10-3 2 3 2" /></>,
    'region-split':<><rect x="3" y="4" width="18" height="16" rx="4" /><path d="m17 2-10 20" strokeDasharray="3 3" /></>,
    ubox:<><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 8h8m-4 0v9m-2 0h4" /></>,
    lama:<><path d="M5 19 19 5" /><path d="m14 5 5 5" /><path d="M4 14h4M6 12v4" /></>,
    'ai-eraser':<><path d="m4 15 9-9 5 5-7 7H7Z" /><path d="m9 10 5 5M13 20h8" /><path d="M19 2v4M17 4h4" /></>,
    bubble:<><path d="M20 9c2 5-2 11-8 11H5l-3 2 1-6C-1 7 5 2 12 3c4 0 7 2 8 6Z" /><circle cx="12" cy="11" r="2" fill="currentColor" /></>,
  };
  return <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[tool]}</svg>;
}
