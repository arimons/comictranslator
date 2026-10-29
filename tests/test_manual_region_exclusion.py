"""직접 선택해 제외한 수동 R이 다른 수동 R이나 자동 영역 전체로 번지지 않는다."""
import json
import cv2
import numpy as np
from pipeline.dialogue_extractor import build_dialogue_sheet, _invalidate_suppressed_regions
from pipeline.region_types import region_binding_key


def test_manual_bubble_exclusion_is_exact_and_keeps_other_region():
 original={'is_valid':True,'region_type_guess':'bubble','method':'manual_edge_barrier_rescue','bubble_rect':[10,20,40,50],'anchor_ids':[1]}
 other={**original,'bubble_rect':[20,20,40,50]}
 record={'type':'bubble','source_method':original['method'],'bubble_rect':original['bubble_rect']}
 _invalidate_suppressed_regions([original,other],[{'target_key':region_binding_key(record)}])
 assert not original['is_valid'] and other['is_valid']


def test_anchorless_manual_ubox_exclusion_restores_original(tmp_path,monkeypatch):
 monkeypatch.setattr('pipeline.dialogue_extractor._get_lama',lambda:(lambda roi,mask:roi))  # 기본 엔진이 LaMa라 실제 모델 대신 가짜를 쓴다
 original=np.full((180,220,3),255,np.uint8);cv2.putText(original,'TEXT',(40,95),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),2)
 path=tmp_path/'page.png';cv2.imwrite(str(path),original);groups=tmp_path/'groups.json';groups.write_text('[]')
 guide={'id':'manual-u','type':'unboxed','unboxed_rect':[30,60,130,50],'source_method':'manual_ubox_guide'}
 def run(suffix,excluded):
  return build_dialogue_sheet(str(path),str(groups),str(tmp_path/f'masked{suffix}.png'),str(tmp_path/f'regions{suffix}.json'),str(tmp_path/f'sheet{suffix}.jpg'),manual_regions=[guide],suppressed_regions=excluded)
 first=run('a',[]);assert len(first['regions'])==1
 excluded=run('b',[{'target_key':region_binding_key(first['regions'][0]),'rect':guide['unboxed_rect'],'anchor_ids':[]}])
 assert excluded['regions']==[]
 assert np.array_equal(cv2.imread(str(tmp_path/'maskedb.png')),original)


def test_manual_edge_exclusion_applies_to_actual_output(tmp_path):
 original=np.full((220,220,3),255,np.uint8);cv2.ellipse(original,(110,110),(85,95),0,0,360,(0,0,0),2);cv2.putText(original,'HI',(75,125),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),2)
 path=tmp_path/'page.png';cv2.imwrite(str(path),original);groups=tmp_path/'groups.json';groups.write_text(json.dumps([{'text_group_id':1,'fallback_rect':[70,85,70,50],'text':'HI','lines':[]}]))
 points=[[45,45],[175,45],[175,175],[45,175]]
 walls=[{'id':str(i),'box_id':'edge1','line':[points[i],points[(i+1)%4]]} for i in range(4)]
 def run(suffix,excluded):
  return build_dialogue_sheet(str(path),str(groups),str(tmp_path/f'mask{suffix}.png'),str(tmp_path/f'reg{suffix}.json'),str(tmp_path/f'sheet{suffix}.jpg'),manual_virtual_walls=walls,suppressed_regions=excluded)
 first=run('a',[]);manual=[r for r in first['regions'] if r.get('source_method','').startswith('manual_edge')];assert len(manual)==1
 target=manual[0];second=run('b',[{'target_key':region_binding_key(target),'rect':target['bubble_rect'],'anchor_ids':target['anchor_ids']}])
 assert second['regions']==[]
 assert np.array_equal(cv2.imread(str(tmp_path/'maskb.png')),original)


def test_auto_bubble_exact_exclusion_keeps_original(tmp_path,monkeypatch):
 original=np.full((220,220,3),255,np.uint8);cv2.ellipse(original,(110,110),(85,95),0,0,360,(0,0,0),2);cv2.putText(original,'HI',(75,125),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),2)
 path=tmp_path/'page.png';cv2.imwrite(str(path),original);groups=tmp_path/'groups.json';groups.write_text(json.dumps([{'text_group_id':1,'fallback_rect':[70,85,70,50],'text':'HI','lines':[]}]))
 mask=np.zeros(original.shape[:2],np.uint8);cv2.ellipse(mask,(110,110),(80,90),0,0,360,255,-1)
 import copy
 candidate={'region_id':1,'is_valid':True,'method':'flood','region_type_guess':'bubble','bubble_rect':[30,20,161,181],'anchor_ids':[1],'mask':mask}
 monkeypatch.setattr('pipeline.dialogue_extractor.build_ocr_region_sheet',lambda *a,**k:{'regions':[copy.deepcopy(candidate)]})
 def run(suffix,excluded):
  return build_dialogue_sheet(str(path),str(groups),str(tmp_path/f'mask{suffix}.png'),str(tmp_path/f'reg{suffix}.json'),str(tmp_path/f'sheet{suffix}.jpg'),suppressed_regions=excluded)
 first=run('a',[]);assert len(first['regions'])==1
 target=first['regions'][0];second=run('b',[{'target_key':region_binding_key(target),'rect':target['bubble_rect'],'anchor_ids':target['anchor_ids']}]);assert second['regions']==[]
 assert np.array_equal(cv2.imread(str(tmp_path/'maskb.png')),original)


def test_auto_bubble_exclusion_matches_rect_trimmed_to_mask(tmp_path,monkeypatch):
 # 검출 사각형이 마스크보다 커서 출력 사각형이 줄어드는 영역도, 화면이 저장한 출력 사각형 키로 제외돼야 한다
 original=np.full((220,220,3),255,np.uint8);cv2.ellipse(original,(110,110),(85,95),0,0,360,(0,0,0),2);cv2.putText(original,'HI',(75,125),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),2)
 path=tmp_path/'page.png';cv2.imwrite(str(path),original);groups=tmp_path/'groups.json';groups.write_text(json.dumps([{'text_group_id':1,'fallback_rect':[70,85,70,50],'text':'HI','lines':[]}]))
 mask=np.zeros(original.shape[:2],np.uint8);cv2.ellipse(mask,(110,110),(80,90),0,0,360,255,-1)
 import copy
 candidate={'region_id':1,'is_valid':True,'method':'flood','region_type_guess':'bubble','bubble_rect':[27,17,167,187],'anchor_ids':[1],'mask':mask}
 monkeypatch.setattr('pipeline.dialogue_extractor.build_ocr_region_sheet',lambda *a,**k:{'regions':[copy.deepcopy(candidate)]})
 def run(suffix,excluded):
  return build_dialogue_sheet(str(path),str(groups),str(tmp_path/f'mask{suffix}.png'),str(tmp_path/f'reg{suffix}.json'),str(tmp_path/f'sheet{suffix}.jpg'),suppressed_regions=excluded)
 first=run('a',[]);assert len(first['regions'])==1
 target=first['regions'][0];assert target['bubble_rect']!=candidate['bubble_rect']
 second=run('b',[{'target_key':region_binding_key(target),'rect':target['bubble_rect'],'anchor_ids':target['anchor_ids']}])
 assert second['regions']==[]
 assert np.array_equal(cv2.imread(str(tmp_path/'maskb.png')),original)
