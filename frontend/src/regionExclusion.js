// 제외 기록은 재분석 때 유지하되, 적용 완료한 삭제 표시를 캔버스에 계속 남기지 않는다.
export function pendingRegionExclusions(image, guidance) {
  const applied = new Set((image?.guidance?.suppressed_regions || []).map(item => item.id || JSON.stringify(item)));
  return (guidance?.suppressed_regions || []).filter(item => !applied.has(item.id || JSON.stringify(item)));
}

function rectCovered(bounds, r) {
  if (!r || !bounds) return false;
  const [x,y,w,h] = bounds;
  const overlap = Math.max(0,Math.min(x+w,r[0]+r[2])-Math.max(x,r[0])) * Math.max(0,Math.min(y+h,r[1]+r[3])-Math.max(y,r[1]));
  return (x+w/2>=r[0] && x+w/2<=r[0]+r[2] && y+h/2>=r[1] && y+h/2<=r[1]+r[3]) || overlap/Math.max(1,w*h)>=.5;
}

export function tgIsExcluded(group, guidance) {
  const id = Number(group.text_group_id ?? group.cluster_id);
  if ((guidance?.disabled_tg_ids || []).map(Number).includes(id)) return true;
  const bounds = group.fallback_rect || group.tight_rect || group.bounding_rect;
  return (guidance?.suppressed_regions || []).some(item => {
    if (item.anchor_ids?.length && !item.anchor_ids.map(Number).includes(id)) return false;
    return rectCovered(bounds, item.rect || item.unboxed_rect || item.bubble_rect || item.source_rect);
  });
}

export function regionIsExcluded(region, guidance) {
  if (String(region.source_method || region.method || "").startsWith("manual_")) return false;
  const disabled = new Set((guidance?.disabled_tg_ids || []).map(Number));
  if (region.anchor_ids?.length && region.anchor_ids.every(id => disabled.has(Number(id)))) return true;
  const bounds = region.unboxed_rect || region.bubble_rect || region.erase_rect || region.source_rect;
  return (guidance?.suppressed_regions || []).some(item => {
    const r = item.rect || item.unboxed_rect || item.bubble_rect || item.source_rect;
    return rectCovered(bounds, r);
  });
}
