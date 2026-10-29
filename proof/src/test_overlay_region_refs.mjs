// 글자 입히기 탭이 R 번호가 아니라 영역 참조(region_ref)로 영역·수동값·선택을 잡는지 확인한다. 합성 fixture만 쓰고 서버 요청은 없다.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const b64=(text)=>`data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const regionTypes=b64(readFileSync('frontend/src/regionTypes.js','utf8'));
const lib=await import(b64(readFileSync('frontend/src/lib.js','utf8').replaceAll('./regionTypes.js',regionTypes)));
const {overlayGuidancePayload,overlayControlItems,overlayItemKey,overlayItemLabel,defaultOverlayGuidance,regionCluster,regionSegmentSiblingCount,baseRegionText,overlayRegionBaseKey}=lib;

const rectA=[10,10,100,80], rectC=[10,200,100,80], rectD=[200,200,100,80];
// 번역 시점: 묶음 1=TG1, 2=TG2, 3=TG3. 지금: TG2가 비활성화되어 R1=TG1, R2=TG3 (번호가 어긋난다).
const image={
  regions:{regions:[
    {region_id:1,type:'bubble',anchor_ids:[1],bubble_rect:rectA,region_ref:'tg:1'},
    {region_id:2,type:'bubble',anchor_ids:[3],bubble_rect:rectC,region_ref:'tg:3'},
  ]},
  integrated:{clusters:[
    {source_region_id:1,matched_cluster:[1],union_bbox:rectA,text_translated:'첫째',resolved_region_ref:'tg:1'},
    {source_region_id:2,matched_cluster:[2],union_bbox:[200,10,100,80],text_translated:'죽은 대사',resolved_region_ref:null,region_vanished:true},
    {source_region_id:3,matched_cluster:[3],union_bbox:rectC,text_translated:'셋째',resolved_region_ref:'tg:3'},
  ]},
  overlay_layout:{regions:{
    '1':{region_id:1,region_ref:'tg:1',overlay_ref_key:'tg:1',text_layout:'첫째',bbox:rectA},
    '3':{region_id:2,region_ref:'tg:3',overlay_ref_key:'tg:3',text_layout:'셋째',bbox:rectC},
  }},
  overlay_guidance_v1:{key_scheme:'region_ref_v1',regions_by_ref:{'tg:3':{font_size:33}},unmatched_regions:{'tg:2':{text_override:''}}},
  overlay_guidance:{regions:{'2':{text_override:''},'3':{font_size:33}}},
};

// F1: 번역 번호 ≠ 현재 번호일 때 현재 영역 외곽선을 고르고, 사라진 대사는 항목이 되지 않는다.
const guidance=defaultOverlayGuidance(image);
assert.deepEqual(Object.keys(guidance.regions),['tg:3']);
const items=overlayControlItems(image,guidance);
assert.deepEqual(items.map(overlayItemKey),['tg:1','tg:3']);
const third=items.find((item)=>overlayItemKey(item)==='tg:3');
assert.equal(third.region_id,2);
assert.equal(overlayItemLabel(third),'R2');
assert.deepEqual(third.region_outline_rect,rectC);
assert.equal(guidance.regions[overlayItemKey(third)].font_size,33);
assert.equal(regionCluster(image,third).text_translated,'셋째');
assert.equal(baseRegionText(image,third),'셋째');
assert.equal(overlayRegionBaseKey(third),'tg:3');
console.log('F1: 번역 번호와 현재 번호가 달라도 수동값·외곽선·번역문이 같은 대사에 붙음, 사라진 대사는 제외');

// F2: 재분석으로 번호가 바뀌어도(영역 하나가 앞에 추가) 참조 키 선택이 같은 대사를 가리킨다.
const reanalyzed={
  ...image,
  regions:{regions:[
    {region_id:1,type:'unboxed',anchor_ids:[9],unboxed_rect:rectD,region_ref:'tg:9'},
    {region_id:2,type:'bubble',anchor_ids:[1],bubble_rect:rectA,region_ref:'tg:1'},
    {region_id:3,type:'bubble',anchor_ids:[3],bubble_rect:rectC,region_ref:'tg:3'},
  ]},
  overlay_layout:{regions:{
    '1':{region_id:2,region_ref:'tg:1',overlay_ref_key:'tg:1',text_layout:'첫째',bbox:rectA},
    '3':{region_id:3,region_ref:'tg:3',overlay_ref_key:'tg:3',text_layout:'셋째',bbox:rectC},
  }},
};
const selectedKey=overlayItemKey(third);
const after=overlayControlItems(reanalyzed,defaultOverlayGuidance(reanalyzed));
const stillSelected=after.find((item)=>overlayItemKey(item)===selectedKey);
assert.ok(stillSelected,'선택한 대사가 재분석 뒤에도 있어야 함');
assert.equal(stillSelected.region_id,3);
assert.equal(overlayItemLabel(stillSelected),'R3');
assert.equal(after.find((item)=>overlayItemKey(item)==='tg:9').overlay_layout.text_layout,'');
console.log('F2: 재분석으로 번호가 바뀌어도 참조 키 선택·값이 같은 대사를 유지');

// F3: 한 영역의 세그먼트는 `ref#k` 키로 나뉘고 형제 수가 영역 참조로 계산된다.
const segmented={
  regions:{regions:[{region_id:4,type:'bubble',anchor_ids:[10,13],bubble_rect:rectA,region_ref:'tg:10,13'}]},
  integrated:{clusters:[
    {source_region_id:7,source_segment_index:1,matched_cluster:[10],union_bbox:[10,10,100,40],text_translated:'앞',resolved_region_ref:'tg:10,13'},
    {source_region_id:7,source_segment_index:2,matched_cluster:[13],union_bbox:[10,50,100,40],text_translated:'뒤',resolved_region_ref:'tg:10,13'},
  ]},
  overlay_layout:{regions:{
    '7:1':{region_id:4,region_ref:'tg:10,13',overlay_ref_key:'tg:10,13#1',source_segment_index:1,text_layout:'앞',bbox:[10,10,100,40]},
    '7:2':{region_id:4,region_ref:'tg:10,13',overlay_ref_key:'tg:10,13#2',source_segment_index:2,text_layout:'뒤',bbox:[10,50,100,40]},
  }},
  overlay_guidance_v1:{regions_by_ref:{'tg:10,13#2':{text_override:'수정'}}},
};
const segItems=overlayControlItems(segmented,defaultOverlayGuidance(segmented));
assert.deepEqual(segItems.map(overlayItemKey),['tg:10,13#1','tg:10,13#2']);
assert.deepEqual(segItems.map(overlayItemLabel),['R4-1','R4-2']);
assert.equal(regionSegmentSiblingCount(segmented,segItems[0]),2);
assert.equal(regionCluster(segmented,segItems[1]).text_translated,'뒤');
assert.equal(overlayRegionBaseKey(segItems[1]),'tg:10,13');
console.log('F3: 세그먼트 항목 키·라벨·형제 수·번역문이 영역 참조로 계산됨');

// F4: 번역이 없는 영역도 항목이 되고 번호 충돌로 다른 번역을 가져오지 않는다.
const untranslated={
  regions:{regions:[{region_id:1,type:'bubble',anchor_ids:[9],bubble_rect:rectD,region_ref:'tg:9'}]},
  integrated:{clusters:[{source_region_id:1,matched_cluster:[1],union_bbox:rectA,text_translated:'다른 대사',resolved_region_ref:'tg:1'}]},
  overlay_layout:{regions:{}},
};
const emptyItems=overlayControlItems(untranslated,defaultOverlayGuidance(untranslated));
assert.deepEqual(emptyItems.map(overlayItemKey),['tg:9']);
assert.equal(regionCluster(untranslated,emptyItems[0]),null);
assert.equal(baseRegionText(untranslated,emptyItems[0]),'');
console.log('F4: 번역 없는 영역은 같은 번호의 다른 번역을 가져오지 않음');

// F5: 서버로 보내는 수동값에는 영역 참조 키 방식 표시가 붙는다(빈 regions replace도 새 번들로 식별).
assert.equal(overlayGuidancePayload({regions:{}}).key_scheme,'region_ref_v1');
assert.deepEqual(overlayGuidancePayload({regions:{'tg:1':{font_size:1}}}).regions,{'tg:1':{font_size:1}});
console.log('F5: 저장 요청에 key_scheme 표시');
