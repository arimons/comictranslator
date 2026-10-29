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


def test_region_context_saved_by_region_key_and_added_to_prompt(tmp_path,monkeypatch):
 from pipeline.work_glossary import page_region_contexts, region_context_prompt
 monkeypatch.setattr(app,'ROOT',tmp_path);monkeypatch.setattr(app,'SESSION_ROOT',tmp_path/'sessions')
 data=app.ensure_session('book');original=app.session_dir('book')/'input/page.png';Image.new('RGB',(100,140),'white').save(original)
 data['images']=[app.image_manifest_item(original,app.file_sha256(original))];app.save_session(data)
 region={'region_id':3,'type':'bubble','source_method':'flood','bubble_rect':[10,20,30,40],'anchor_ids':[1]}
 app.write_json(Path(app.image_record('book',original)['regions_json']),{'regions':[region]})
 client=TestClient(app.create_app());key=region_binding_key(region)
 payload={'session_id':'book','image_id':'page','region_id':3,'region_key':key,'text':'  혼잣말이라 반말로  '}
 response=client.post('/api/region-context',json=payload);assert response.status_code==200,response.text
 assert response.json()['context_by_region']=={key:'혼잣말이라 반말로'}
 assert client.get('/api/speakers/book/page').json()['context_by_region']=={key:'혼잣말이라 반말로'}
 assert client.post('/api/region-context',json={**payload,'text':'가'*501}).status_code==400
 # R 번호가 바뀌어도 같은 범위면 그 R의 가이드로 들어간다
 contexts=page_region_contexts([{**region,'region_id':7}],{key:'혼잣말이라 반말로'})
 assert contexts==[{'region_id':7,'context':'혼잣말이라 반말로'}]
 assert 'region_id 7: 혼잣말이라 반말로' in region_context_prompt(contexts)
 assert region_context_prompt([])==''
 assert client.post('/api/region-context',json={**payload,'text':''}).json()['context_by_region']=={}


def _book(tmp_path, monkeypatch, regions):
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app, 'SESSION_ROOT', tmp_path / 'sessions')
    data = app.ensure_session('book')
    original = app.session_dir('book') / 'input/page.png'
    Image.new('RGB', (100, 140), 'white').save(original)
    data['images'] = [app.image_manifest_item(original, app.file_sha256(original))]
    app.save_session(data)
    app.write_json(Path(app.image_record('book', original)['regions_json']), {'regions': regions})
    return original, TestClient(app.create_app())


def test_page_speakers_and_contexts_prefer_region_ref_over_binding_key():
    from pipeline.region_refs import annotate_regions
    from pipeline.work_glossary import page_region_contexts
    memo = {'characters': [{'id': 'one', 'name': '한명'}, {'id': 'two', 'name': '두명'}]}
    region = {'region_id': 4, 'type': 'bubble', 'source_method': 'flood', 'bubble_rect': [10, 20, 30, 40], 'anchor_ids': [7]}
    old_rect_key = region_binding_key({**region, 'bubble_rect': [1, 2, 3, 4]})  # 재분석 전 사각형 키
    regions = annotate_regions([region])
    speakers = page_speakers(regions, {}, memo, {old_rect_key: 'one'}, {'tg:7': 'two'})
    assert speakers == [{'region_id': 4, 'speaker': '두명', 'source': 'manual'}]
    # 영역 참조 기록이 없으면 구형 키·TG 지정으로 내려간다.
    assert page_speakers(regions, {'7': 'one'}, memo, {}, {})[0]['speaker'] == '한명'
    assert page_region_contexts(regions, {}, {'tg:7': '메모'}) == [{'region_id': 4, 'context': '메모'}]


def test_api_speaker_and_context_follow_region_ref_after_rect_change(tmp_path, monkeypatch):
    region = {'region_id': 1, 'type': 'bubble', 'source_method': 'flood', 'bubble_rect': [10, 20, 30, 40], 'anchor_ids': [3]}
    original, client = _book(tmp_path, monkeypatch, [region])
    payload = {'session_id': 'book', 'image_id': 'page', 'region_ref': 'tg:3', 'region_id': 1, 'character_id': 'narration'}
    response = client.post('/api/speakers', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['by_region_ref'] == {'tg:3': 'narration'}
    assert response.json()['by_region'] == {region_binding_key(region): 'narration'}  # 구형 키도 함께 기록
    ctx = client.post('/api/region-context', json={'session_id': 'book', 'image_id': 'page', 'region_ref': 'tg:3', 'text': '혼잣말'})
    assert ctx.status_code == 200 and ctx.json()['context_by_region_ref'] == {'tg:3': '혼잣말'}
    # 재분석으로 사각형·번호가 바뀌어도(같은 TG) 지정과 메모가 따라간다.
    moved = {**region, 'region_id': 5, 'bubble_rect': [15, 25, 30, 45]}
    app.write_json(Path(app.image_record('book', original)['regions_json']), {'regions': [{'region_id': 1, 'type': 'bubble', 'anchor_ids': [9], 'bubble_rect': [60, 60, 20, 20]}, moved]})
    got = client.get('/api/speakers/book/page').json()
    assert got['by_region_ref'] == {'tg:3': 'narration'} and got['context_by_region_ref'] == {'tg:3': '혼잣말'}
    # 현재 페이지에 없는 참조는 거부한다. 번호로 대신 찾지 않는다.
    bad = client.post('/api/speakers', json={**payload, 'region_ref': 'tg:99'})
    assert bad.status_code == 400
    # 구형 요청(region_id + region_key)도 계속 받는다.
    legacy = client.post('/api/speakers', json={'session_id': 'book', 'image_id': 'page', 'region_id': 5,
                                                'region_key': region_binding_key(moved), 'character_id': None})
    assert legacy.status_code == 200 and legacy.json()['by_region_ref']['tg:3'] is None


def test_split_region_copies_speaker_to_children_on_read(tmp_path, monkeypatch):
    big = {'region_id': 1, 'type': 'bubble', 'source_method': 'flood', 'bubble_rect': [0, 0, 100, 100], 'anchor_ids': [10, 13]}
    original, client = _book(tmp_path, monkeypatch, [big])
    assert client.post('/api/speakers', json={'session_id': 'book', 'image_id': 'page', 'region_ref': 'tg:10,13', 'character_id': 'narration'}).status_code == 200
    children = [{**big, 'region_id': 1, 'anchor_ids': [10], 'bubble_rect': [0, 0, 50, 100]},
                {**big, 'region_id': 2, 'anchor_ids': [13], 'bubble_rect': [50, 0, 50, 100]}]
    app.write_json(Path(app.image_record('book', original)['regions_json']), {'regions': children})
    got = client.get('/api/speakers/book/page').json()
    assert got['by_region_ref'] == {'tg:10': 'narration', 'tg:13': 'narration'}
