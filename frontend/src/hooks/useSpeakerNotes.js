import { useEffect, useMemo, useState } from "react";
import { api, regionId } from "../lib.js";
import { TEXT_BASED, regionBindingKey, regionProcessingBasis } from "../regionTypes.js";

// 화자·번역 메모는 영역 참조(region_ref)로 저장한다. 참조를 주지 않는 구버전 응답만 구형 영역 키를 쓴다.
const regionStoreKey = (region) => region?.region_ref || regionBindingKey(region);

// 영역 분석 탭의 화자 지정(작품 메모 인물 표, 숫자 단축키)과 영역별 번역 메모.
// App.jsx에서 그대로 옮겼다. 페이지별 화자·메모를 읽는 effect와 메모 입력칸 초기화 effect는
// App이 이 훅을 부르는 자리에서 이 순서로 돈다(입력칸 초기화는 상태만 비우므로 자리가 바뀌어도 결과가 같다).
// 화자를 지정하면 다음 R로 넘어가므로 App의 cycleGuidanceRegion을 받는다.
export default function useSpeakerNotes({
  sessionId, selectedPageId, guidanceRegions, selectedGuidanceRegion, selectedRegionKey, selectedIdRef,
  setStatus, cycleGuidanceRegion,
}) {
  // 작품 메모(인물 표)와 현재 페이지의 TG별 화자 지정
  const [workMemo, setWorkMemo] = useState(null);
  const [speakerByTg, setSpeakerByTg] = useState({});
  const [speakerByRegion, setSpeakerByRegion] = useState({});
  // R별 번역 메모(영역 키 → 글). 다음 OCR/번역 때 '영역별 번역 가이드'로 붙는다.
  const [contextByRegion, setContextByRegion] = useState({});
  const [contextDraft, setContextDraft] = useState(null);
  const [speakerPickerOpen, setSpeakerPickerOpen] = useState(false);
  // 화자 단축키 1-8 = 작품 메모 인물 표 순서(주인공 1번), 9 = 기타(전체 목록 팝업)
  const allSpeakerCharacters = workMemo?.characters || [];
  const speakerCharacters = allSpeakerCharacters.slice(0, 8);
  const speakerLabels = useMemo(() => {
    const names = Object.fromEntries((workMemo?.characters || []).map((row) => [row.id, row.name]));
    names.narration = "내레이션";
    const lead = (workMemo?.characters || []).find((row) => row.protagonist);
    const labels = {};
    guidanceRegions.forEach((region) => {
      const key = regionStoreKey(region);
      const assigned = Object.hasOwn(speakerByRegion,key) ? speakerByRegion[key] : (region.anchor_ids || []).map((id) => speakerByTg[String(id)]).find((id) => names[id]);
      // 텍스트 기반 영역의 자동 기본값은 괄호로 구분한다.
      if (assigned) labels[regionId(region)] = names[assigned];
      else if (lead && regionProcessingBasis(region) === TEXT_BASED) labels[regionId(region)] = `(${lead.name})`;
    });
    return labels;
  }, [workMemo, speakerByTg, speakerByRegion, guidanceRegions]);

  useEffect(() => {
    setSpeakerByTg({});
    setSpeakerByRegion({});
    setContextByRegion({});
    if (!sessionId || !selectedPageId) return undefined;
    let alive = true;
    api(`/api/speakers/${encodeURIComponent(sessionId)}/${encodeURIComponent(selectedPageId)}`)
      .then((result) => { if (alive) { setSpeakerByTg(result.by_tg || {}); setSpeakerByRegion(result.by_region_ref || result.by_region || {}); setContextByRegion(result.context_by_region_ref || result.context_by_region || {}); } })
      .catch(() => {});
    return () => { alive = false; };
  }, [sessionId, selectedPageId]);

  // 선택한 R이 바뀌면 메모 입력칸을 저장된 값으로 되돌린다.
  useEffect(() => { setContextDraft(null); }, [selectedRegionKey, selectedPageId]);
  const savedRegionContext = selectedGuidanceRegion ? contextByRegion[regionStoreKey(selectedGuidanceRegion)] || "" : "";
  const regionContextValue = contextDraft ?? savedRegionContext;

  async function assignSpeaker(slot) {
    const region = selectedGuidanceRegion;
    if (!region) {
      setStatus("화자를 지정할 영역을 먼저 선택하세요");
      return true;
    }
    let characterId = null;
    let name = "자동";
    if (slot === "narration") {
      characterId = "narration";
      name = "내레이션";
    } else if (slot === 9) {
      if (!allSpeakerCharacters.length) {
        setStatus(workMemo ? "작품 메모에 인물이 없습니다" : "작품 메모를 먼저 연결하세요");
        return true;
      }
      setSpeakerPickerOpen(true);
      return true;
    } else if (typeof slot === "object" && slot) {
      characterId = slot.id;
      name = slot.name;
    } else if (slot > 0) {
      const row = speakerCharacters[slot - 1];
      if (!row) {
        setStatus(workMemo ? `${slot}번 인물이 없습니다. 작품 메모에서 인물을 추가하세요` : "작품 메모를 먼저 연결하세요");
        return true;
      }
      characterId = row.id;
      name = row.name;
    }
    const pageId = selectedPageId;
    // 화면을 먼저 갱신하고 저장은 뒤에서 한다. 실패하면 서버 값으로 되돌린다.
    setSpeakerByRegion((current) => ({...current,[regionStoreKey(region)]:characterId || null}));
    setStatus(`R${regionId(region)} 화자: ${name}`);
    if (slot !== 0) cycleGuidanceRegion(1, true);
    try {
      const result = await api("/api/speakers", { method: "POST", body: JSON.stringify({
        session_id: sessionId, image_id: pageId, region_id: regionId(region), region_key: regionBindingKey(region), region_ref: region.region_ref || null, character_id: characterId,
      }) });
      if (selectedIdRef.current === pageId || !selectedIdRef.current) { setSpeakerByTg(result.by_tg || {}); setSpeakerByRegion(result.by_region_ref || result.by_region || {}); }
    } catch (err) {
      setStatus(`화자 저장 실패: ${err.message}`);
      api(`/api/speakers/${encodeURIComponent(sessionId)}/${encodeURIComponent(pageId)}`)
        .then((result) => { setSpeakerByTg(result.by_tg || {}); setSpeakerByRegion(result.by_region_ref || result.by_region || {}); }).catch(() => {});
    }
    return true;
  }

  async function saveRegionContext() {
    const region = selectedGuidanceRegion;
    if (!region || contextDraft == null || contextDraft.trim() === savedRegionContext) { setContextDraft(null); return; }
    const pageId = selectedPageId;
    try {
      const result = await api("/api/region-context", { method: "POST", body: JSON.stringify({
        session_id: sessionId, image_id: pageId, region_id: regionId(region), region_key: regionBindingKey(region), region_ref: region.region_ref || null, text: contextDraft,
      }) });
      if (selectedPageId === pageId) setContextByRegion(result.context_by_region_ref || result.context_by_region || {});
      setContextDraft(null);
      setStatus(`R${regionId(region)} 번역 메모 ${contextDraft.trim() ? "저장" : "삭제"} · 다음 OCR/번역 때 반영됩니다.`);
    } catch (err) {
      setStatus(`번역 메모 저장 실패: ${err.message}`);
    }
  }

  return {
    setWorkMemo, speakerLabels, speakerCharacters, allSpeakerCharacters, speakerPickerOpen, setSpeakerPickerOpen,
    contextDraft, setContextDraft, savedRegionContext, regionContextValue, assignSpeaker, saveRegionContext,
  };
}
