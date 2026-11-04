import { regionBindingKey } from "./regionTypes.js";
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
    if (item.target_key) return false;
    if (item.anchor_ids?.length && !item.anchor_ids.map(Number).includes(id)) return false;
    return rectCovered(bounds, item.rect || item.unboxed_rect || item.bubble_rect || item.source_rect);
  });
}

// 사각형 중심(geo)이나 충돌 접미사(@)로 만든 처리 인스턴스 참조는 정확히 비교할 수 없다.
export function instanceRefIsWeak(ref) {
  const text = String(ref || "");
  return !text || text.includes("|geo:") || text.includes("@") || text.includes("|method:") || text.startsWith("method:");
}

// 제외·LaMa 고정 기록이 이 영역을 가리키는지. 처리 인스턴스 참조(region_instance_ref)가 있으면 그것으로 정확히 비교한다.
// 같은 TG의 자동 영역을 제외해도 그 자리에 새로 만든 수동 영역에는 걸리지 않고, 사각형이 조금 달라져도 같은 인스턴스면 유지된다.
// 참조가 없거나 약한 기록은 구형 영역 키(target_key)로 비교한다.
export function recordTargetsRegion(item, region) {
  if (item?.instance_ref && region?.region_instance_ref && !instanceRefIsWeak(item.instance_ref)) {
    return item.instance_ref === region.region_instance_ref;
  }
  return Boolean(item?.target_key) && item.target_key === regionBindingKey(region);
}

export function regionIsExcluded(region, guidance) {
  if ((guidance?.suppressed_regions || []).some(item => recordTargetsRegion(item, region))) return true;
  if (String(region.source_method || region.method || "").startsWith("manual_")) return false;
  const disabled = new Set((guidance?.disabled_tg_ids || []).map(Number));
  if (region.anchor_ids?.length && region.anchor_ids.every(id => disabled.has(Number(id)))) return true;
  const bounds = region.unboxed_rect || region.bubble_rect || region.erase_rect || region.source_rect;
  return (guidance?.suppressed_regions || []).some(item => {
    if (item.target_key) return false;
    const r = item.rect || item.unboxed_rect || item.bubble_rect || item.source_rect;
    return rectCovered(bounds, r);
  });
}

// 예전에는 수동 가이드(텍스트 기반 박스·말풍선 박스)가 만든 R을 지워도 제외 기록만 남고 가이드는 그대로 남아,
// 영역은 숨겨진 채 가이드 박스만 캔버스에 보였다(R이 숨어 있어 선택해 지울 수도 없다). 그런 가이드를 열 때
// 지운 R을 만든 가이드와 쓸모없어진 제외 기록을 함께 정리한다. 크기 조절로 생긴 가이드는 원래 자동 영역을 제외로 남긴다.
export function pruneDeletedManualGuides(guidance) {
  const suppressed = guidance?.suppressed_regions || [];
  const guides = guidance?.manual_regions || [];
  const stamp = (id) => { const m = String(id || "").match(/(\d{10,})/); return m ? Number(m[1]) : NaN; };
  const dead = suppressed.filter((item) => {
    const method = String(item.source_method || "");
    return method.startsWith("manual_") && !method.startsWith("manual_edge");
  });
  const guideRect = (item) => item.unboxed_rect || item.bubble_rect || item.rect || null;
  const removeGuides = new Set();
  const usedSuppress = new Set();
  // 크기 조절 가이드의 원래 자동 영역이 제외돼 있으면, 그 가이드는 적용 전에 R을 지워 남은 것이다(제외 기록은 둔다).
  const suppressedKeys = new Set(suppressed.map((item) => item.target_key).filter(Boolean));
  guides.forEach((guide) => { if (guide.resize_source_key && suppressedKeys.has(guide.resize_source_key)) removeGuides.add(guide); });
  if (!dead.length && !removeGuides.size) return null;
  if (!guides.length) return null;
  dead.forEach((record) => {
    const origin = String(record.instance_ref || "").split("|")[0];
    let guide = origin ? guides.find((item) => item.id && String(item.id) === origin) : null;
    if (!guide && Array.isArray(record.rect)) {
      const [rx, ry, rw, rh] = record.rect;
      guide = guides.find((item) => {
        const box = guideRect(item);
        if (!Array.isArray(box) || box.length < 4 || removeGuides.has(item)) return false;
        const w = Math.min(box[0] + box[2], rx + rw) - Math.max(box[0], rx);
        const h = Math.min(box[1] + box[3], ry + rh) - Math.max(box[1], ry);
        return w > 0 && h > 0 && (w * h) / Math.max(1, rw * rh) >= 0.5 && stamp(item.id) < stamp(record.id);
      }) || null;
    }
    if (!guide) return;
    removeGuides.add(guide);
    usedSuppress.add(record);
  });
  if (!removeGuides.size) return null;
  const kept = suppressed.filter((item) => !usedSuppress.has(item));
  const revived = [...removeGuides]
    .filter((guide) => guide.resize_source_key && !kept.some((item) => item.target_key === guide.resize_source_key))
    .map((guide, index) => ({
      id: `suppress_${Date.now()}_${index}`,
      target_key: guide.resize_source_key,
      type: "region_suppress",
      source_method: "resize_source",
      source_region_type: "unboxed",
      anchor_ids: [],
      rect: guideRect(guide),
    }));
  return {
    next: { ...guidance, manual_regions: guides.filter((item) => !removeGuides.has(item)), suppressed_regions: [...kept, ...revived] },
    count: removeGuides.size,
  };
}

// 예전에는 수동 텍스트 기반 R의 크기를 바꿀 때 그 R을 만든 가이드를 못 찾으면 새 가이드를 더 만들었다.
// 새 가이드의 resize_source_key는 "수동 영역"의 키(방법 이름이 manual_로 시작)라, 그 키의 사각형과 겹치는 더 오래된
// 텍스트 기반 가이드가 대체된 옛 가이드다. 옛 가이드를 지우고, 새 가이드는 옛 가이드의 원래 자동 영역 키를 물려받는다.
export function pruneSupersededResizeGuides(guidance) {
  const guides = guidance?.manual_regions || [];
  if (guides.length < 2) return null;
  const stamp = (id) => { const m = String(id || "").match(/(\d{10,})/); return m ? Number(m[1]) : NaN; };
  const rectOf = (item) => item.unboxed_rect || item.rect || null;
  const textBased = (item) => ["unboxed", "text_based"].includes(String(item.type || item.processing_basis || ""));
  const iou = (a, b) => {
    const w = Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]);
    const h = Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]);
    if (w <= 0 || h <= 0) return 0;
    return (w * h) / (a[2] * a[3] + b[2] * b[3] - w * h);
  };
  const removed = new Set();
  const updated = new Map();
  [...guides].sort((a, b) => stamp(a.id) - stamp(b.id)).forEach((guide) => {
    if (!guide.resize_source_key || !textBased(guide)) return;
    let parsed;
    try { parsed = JSON.parse(guide.resize_source_key); } catch { return; }
    const [, method, keyRect] = Array.isArray(parsed) ? parsed : [];
    if (!String(method || "").startsWith("manual_") || !Array.isArray(keyRect) || keyRect.length < 4) return;
    const old = guides.find((item) => item !== guide && !removed.has(item) && textBased(item)
      && stamp(item.id) < stamp(guide.id) && Array.isArray(rectOf(item)) && iou(rectOf(item), keyRect) >= 0.5);
    if (!old) return;
    removed.add(old);
    const inherited = updated.get(old)?.resize_source_key ?? old.resize_source_key;
    const next = { ...(updated.get(guide) || guide) };
    if (inherited) next.resize_source_key = inherited; else delete next.resize_source_key;
    updated.set(guide, next);
  });
  if (!removed.size) return null;
  return {
    next: { ...guidance, manual_regions: guides.filter((item) => !removed.has(item)).map((item) => updated.get(item) || item) },
    count: removed.size,
  };
}
