"""이미지 생성 후보를 유지하면서 기존 분석 경로를 선택하는 계약."""
import json
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
import pytest
import main_v2 as app
from pipeline import dialogue_extractor as extract
from pipeline import pipeline_runner as runner


def test_route_policy_preserves_candidate_and_exclusion(tmp_path,monkeypatch):
 monkeypatch.setattr(app,'SESSION_ROOT',tmp_path/'sessions')
 data=app.ensure_session('book');original=app.session_dir('book')/'input/00001.png';Image.new('RGB',(100,140),'white').save(original)
 data['images']=[app.image_manifest_item(original,app.file_sha256(original))];app.save_session(data)
 candidate=app.session_dir('book')/'work/image_generation/00001/result-test.png';candidate.parent.mkdir(parents=True);candidate.write_bytes(b'candidate')
 def forbidden(*a,**k):raise AssertionError('정책 저장은 유료 API나 분석을 실행하지 않는다')
 monkeypatch.setattr(app,'run_single_image',forbidden)
 client=TestClient(app.create_app());before=original.read_bytes()
 assert app.page_analysis_route('book','00001')=='auto'
 response=client.post('/api/page-analysis-route',json={'session_id':'book','image_id':'00001','route':'standard'})
 assert response.status_code==200 and response.json()['image']['analysis_route']=='standard'
 assert client.post('/api/page-processing',json={'session_id':'book','image_id':'00001','excluded':True}).status_code==200
 assert app.page_analysis_route('book','00001')=='standard'
 assert client.post('/api/page-analysis-route',json={'session_id':'book','image_id':'00001','route':'auto'}).status_code==409
 assert candidate.read_bytes()==b'candidate' and original.read_bytes()==before
 assert client.post('/api/page-analysis-route',json={'session_id':'book','image_id':'00001','route':'invalid'}).status_code==422


def test_standard_route_does_not_read_or_publish_default_analysis_caches():
 record={'analysis_route':'standard'}
 assert not app.hydrate_analysis_cache(record,'hash',False,print)
 assert not app.store_analysis_cache(record,'hash')
 assert not app.hydrate_guidance_output_cache(record,'hash',{},print)
 assert not app.store_guidance_output_cache(record,'hash',{})
 assert not app.store_guidance_cache(record,'hash')


def test_cover_standard_override_reaches_normal_detection(tmp_path,monkeypatch):
 original=tmp_path/'00001.png';Image.new('RGB',(100,140),'white').save(original)
 groups=tmp_path/'groups.json';groups.write_text('[]')
 args=dict(image_path=str(original),clusters_json=str(groups),output_masked_path=str(tmp_path/'masked.jpg'),output_json_path=str(tmp_path/'regions.json'),indexed_output_sheet_path=str(tmp_path/'sheet.jpg'))
 assert extract.build_dialogue_sheet(**args)['skip_reason']=='cover_image_gen_redraw'
 class DetectionReached(Exception):pass
 def reached(*a,**k):raise DetectionReached()
 monkeypatch.setattr(extract,'build_ocr_region_sheet',reached)
 with pytest.raises(DetectionReached):extract.build_dialogue_sheet(**args,force_standard=True)


def test_standard_route_reuses_cv_and_regenerates_cover_skip(tmp_path,monkeypatch):
 work=tmp_path/'work';source=tmp_path/'00001.png';Image.new('RGB',(100,140),'white').save(source)
 paths=runner._pipeline_paths(work,'00001')
 for k,v in paths.items():v.parent.mkdir(parents=True,exist_ok=True)
 paths['cv_json'].write_text('{}');paths['clusters_json'].write_text('[]')
 paths['regions_json'].write_text(json.dumps({'skip_reason':'cover_image_gen_redraw','regions':[]}))
 paths['masked_jpg'].write_bytes(b'old');paths['integrated_json'].write_text('{"skipped":true}')
 policy=work/'page_policy/00001.json';policy.parent.mkdir();policy.write_text('{"analysis_route":"standard"}')
 calls=[]
 def step(cmd,*a,**k):
  calls.append(cmd)
  assert 'pipeline.dialogue_extractor' in cmd and '--force-standard' in cmd
  paths['regions_json'].write_text('{"regions":[]}')
 monkeypatch.setattr(runner,'_run_step',step)
 ok,log=runner.run_single_image(source,work,tmp_path/'final.jpg',stop_after='pre_ocr')
 assert ok,log
 assert len(calls)==1 and not paths['integrated_json'].exists()
