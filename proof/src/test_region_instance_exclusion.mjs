// 제외·LaMa 고정 기록이 처리 인스턴스(region_instance_ref)로 영역을 가리키는지 확인한다. 합성 fixture만 쓰고 서버 요청은 없다.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const b64=(text)=>`data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const regionTypes=b64(readFileSync('frontend/src/regionTypes.js','utf8'));
const {recordTargetsRegion,regionIsExcluded,instanceRefIsWeak}=await import(b64(readFileSync('frontend/src/regionExclusion.js','utf8').replace('./regionTypes.js',regionTypes)));
const {regionBindingKey}=await import(regionTypes);

const auto={type:'bubble',source_method:'flood',bubble_rect:[10,10,80,80],anchor_ids:[6],region_instance_ref:'auto|tg:6'};
const edge={type:'bubble',source_method:'manual_edge_barrier_rescue',bubble_rect:[12,12,70,70],anchor_ids:[6],region_instance_ref:'edge_box_9|tg:6'};
const excludeAuto={instance_ref:'auto|tg:6',target_key:regionBindingKey(auto)};

// 같은 TG의 자동 영역 제외가 새로 그린 수동 영역에는 걸리지 않는다.
assert.equal(recordTargetsRegion(excludeAuto,auto),true);
assert.equal(recordTargetsRegion(excludeAuto,edge),false);
assert.equal(regionIsExcluded(edge,{suppressed_regions:[excludeAuto]}),false);
assert.equal(regionIsExcluded(auto,{suppressed_regions:[excludeAuto]}),true);
// 사각형·검출 방법이 달라져 구형 키가 어긋나도 같은 인스턴스면 계속 제외된다.
const moved={...auto,source_method:'bbox_fallback',bubble_rect:[20,20,50,50]};
assert.equal(recordTargetsRegion(excludeAuto,moved),true);
// 인스턴스 참조가 없는 예전 기록은 구형 키로 판단한다.
const legacy={target_key:regionBindingKey(auto)};
assert.equal(recordTargetsRegion(legacy,auto),true);
assert.equal(recordTargetsRegion(legacy,moved),false);
// 약한 참조(geo, 충돌 접미사)는 구형 키로 내려간다.
assert.equal(instanceRefIsWeak('auto|geo:b:60,50'),true);
assert.equal(instanceRefIsWeak('auto|tg:6@edge_box_1'),true);
assert.equal(instanceRefIsWeak('auto|tg:6'),false);
assert.equal(instanceRefIsWeak('method:manual_ubox_guide|tg:4'),true);
assert.equal(recordTargetsRegion({instance_ref:'method:manual_ubox_guide|tg:4',target_key:regionBindingKey(auto)},{...auto,region_instance_ref:'manual_ubox_1|tg:4'}),true);
assert.equal(recordTargetsRegion({instance_ref:'auto|geo:b:60,50',target_key:regionBindingKey(auto)},{...auto,region_instance_ref:'auto|geo:b:1,1'}),true);
console.log('제외·고정: 처리 인스턴스 참조로 자동/수동 같은 TG 구분, 사각형 변경에도 유지, 구형 기록·약한 참조는 구형 키 통과');
