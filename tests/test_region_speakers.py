"""R 단위 화자는 TG 유무와 재번호화에 독립적이고 기존 TG 기록과 호환된다."""
import json
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
import app as app
from pipeline.region_types import region_binding_key
from pipeline.work_glossary import page_speakers


def test_region_binding_survives_number_and_anchor_change():
 r={'region_id':1,'type':'unboxed','source_method':'manual_ubox_guide','unboxed_rect':[10,20,30,40],'anchor_ids':[5]}
 assert region_binding_key(r)==region_binding_key({**r,'region_id':9,'anchor_ids':[]})
 memo={'characters':[{'id':'one','name':'한명'},{'id':'two','name':'두명'}]}
 regions=[r,{**r,'region_id':2,'unboxed_rect':[60,20,30,40]}]
 by_region={region_binding_key(r):'two',region_binding_key(regions[1]):None}
 speakers=page_speakers(regions,{'5':'one'},memo,by_region)
 assert speakers==[{'region_id':1,'speaker':'두명','source':'manual'}]
 assert page_speakers([{**r,'anchor_ids':[]}],{},memo,by_region)[0]['speaker']=='두명'


def test_api_assigns_anchorless_region_without_touching_legacy_tg(tmp_path,monkeypatch):
 monkeypatch.setattr(app,'ROOT',tmp_path);monkeypatch.setattr(app,'SESSION_ROOT',tmp_path/'sessions')
 data=app.ensure_session('book');original=app.session_dir('book')/'input/page.png';Image.new('RGB',(100,140),'white').save(original)
 data['images']=[app.image_manifest_item(original,app.file_sha256(original))];app.save_session(data)
 region={'region_id':1,'type':'unboxed','source_method':'manual_ubox_guide','unboxed_rect':[10,20,30,40],'anchor_ids':[]}
 app.write_json(Path(app.image_record('book',original)['regions_json']),{'regions':[region]})
 app.write_json(app.speakers_path('book','page'),{'by_tg':{'3':'old'}})
 client=TestClient(app.create_app());payload={'session_id':'book','image_id':'page','region_id':1,'region_key':region_binding_key(region),'character_id':'narration'}
 response=client.post('/api/speakers',json=payload);assert response.status_code==200,response.text
 assert response.json()['by_tg']=={'3':'old'}
 assert response.json()['by_region']=={region_binding_key(region):'narration'}
 assert client.get('/api/speakers/book/page').json()==response.json()
 assert client.post('/api/speakers',json={**payload,'region_key':'changed'}).status_code==400
 response=client.post('/api/speakers',json={**payload,'character_id':None})
 assert response.json()['by_region'][region_binding_key(region)] is None
