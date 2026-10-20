"""원본 직접 참조, 호스트 재연결, 기존 캐시 보존과 읽기 경계를 검증한다."""
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image
from fastapi.testclient import TestClient
import app
from pipeline.source_library import SourceLibrary,digest,atomic_link
from pipeline.manual_guidance import load_json,write_json
from pipeline.editor_review_bridge import ReviewStore
from pipeline.page_preparation import build_plan,materialize_plan


@pytest.fixture
def setup(tmp_path,monkeypatch):
    home=tmp_path/'data';monkeypatch.setattr(app,'SESSION_ROOT',home/'projects')
    monkeypatch.setattr(app,'INPUT_CACHE_DIR',home/'cache/input')
    monkeypatch.setattr(app,'ANALYSIS_CACHE_DIR',home/'cache/analysis')
    monkeypatch.setattr(app,'CV_CACHE_DIR',home/'cache/cv')
    root=tmp_path/'original';root.mkdir()
    image=root/'page.png';Image.new('RGB',(60,80),'white').save(image)
    return home,root,image


def test_direct_source_no_copy_serve_only_registered_and_delete_leaves_original(setup):
    home,root,image=setup
    record=app.copy_images_into_session([image],'book')[0]
    alias=Path(record['input_path'])
    assert alias.is_symlink() and alias.resolve()==image.resolve()
    assert not (home/'cache/input').exists()
    client=TestClient(app.create_app())
    assert client.get(app.file_url(alias)).status_code==200
    other=root/'secret.png';Image.new('RGB',(60,80),'black').save(other)
    assert client.get('/api/file',params={'path':str(other)}).status_code==403
    assert ReviewStore(home.parent).view_region('book','page')[1]
    app.remove_session_image('book','page')
    assert image.is_file() and not alias.exists()


def test_source_change_blocks_stale_pixels_without_fallback(setup):
    home,root,image=setup
    item=app.copy_images_into_session([image],'book')[0]
    Image.new('RGB',(60,80),'black').save(image)
    data=app.ensure_session('book');item=data['images'][0]
    assert item['source_status']=='missing' and not Path(item['input_path']).exists()
    assert not app.analyze_one_image('book',item,1,1,False,lambda e:None)
    assert TestClient(app.create_app()).get('/api/file',params={'path':str(image)}).status_code==403


def test_legacy_relink_backup_fallback_and_host_mapping(setup,tmp_path):
    home,root,image=setup
    data=app.ensure_session('book');base=app.session_dir('book')
    cache=home/'cache/input/page.png';cache.parent.mkdir(parents=True);cache.write_bytes(image.read_bytes())
    alias=base/'input/page.png';atomic_link(cache,alias)
    data['images']=[app.image_manifest_item(alias,digest(image))];app.save_session(data)
    original_cache=cache.read_bytes()
    lib=app.source_library();plan=lib.preview(data,root)
    assert plan['matched']==1
    lib.apply(data,base,plan)
    assert alias.resolve()==image.resolve() and cache.read_bytes()==original_cache
    portable=json.loads((base/'project.json').read_text())
    assert not Path(portable['images'][0]['source_fallback_path']).is_absolute()
    assert '/original/' not in (base/'project.json').read_text()
    second=SourceLibrary(home,host_id='other-host')
    second.refresh(load_json(base/'project.json'),base)
    assert alias.resolve()==cache.resolve()
    assert list((base/'work/source_history').glob('*/manifest-before.json'))
    # 같은 페이지를 다른 기기의 새 폴더에 다시 연결한다. 결과·캐시 ID는 동일하다.
    new=tmp_path/'mini-original';new.mkdir();(new/'renamed.png').write_bytes(image.read_bytes())
    data=load_json(base/'project.json');newplan=second.preview(data,new);second.apply(data,base,newplan)
    assert alias.resolve()==(new/'renamed.png').resolve() and data['images'][0]['id']=='page'
    assert cache.read_bytes()==original_cache


def test_wrong_hash_and_stale_preview_make_no_input_changes(setup):
    home,root,image=setup
    app.copy_images_into_session([image],'book');data=app.ensure_session('book');base=app.session_dir('book')
    lib=app.source_library();plan=lib.preview(data,root);before=(base/'project.json').read_bytes();link=(base/'input/page.png').readlink()
    Image.new('RGB',(60,80),'black').save(image)
    with pytest.raises(ValueError,match='바뀌었습니다'):lib.apply(data,base,plan)
    assert (base/'project.json').read_bytes()==before and (base/'input/page.png').readlink()==link


def test_relink_write_failure_restores_owned_input(setup,monkeypatch):
    home,root,image=setup
    data=app.ensure_session('book');base=app.session_dir('book');alias=base/'input/page.png';alias.write_bytes(image.read_bytes())
    data['images']=[app.image_manifest_item(alias,digest(image))];app.save_session(data)
    lib=app.source_library();plan=lib.preview(data,root)
    import pipeline.source_library as module
    original=module.write_json
    def failing(path,value):
        if Path(path)==base/'project.json':raise OSError('disk fixture')
        return original(path,value)
    monkeypatch.setattr(module,'write_json',failing)
    with pytest.raises(OSError):lib.apply(data,base,plan)
    assert alias.is_file() and not alias.is_symlink() and alias.read_bytes()==image.read_bytes()
    assert not load_json(base/'project.json')['images'][0].get('source_ref')


def test_keep_reference_but_split_is_owned(setup):
    home,root,image=setup
    records=app.copy_images_into_session([image],'book')
    wide=root/'wide.png';Image.new('RGB',(160,80),'black').save(wide)
    records+=app.copy_images_into_session([wide],'book')
    plan=build_plan(records);out=materialize_plan(plan,home/'projects/prepared/input')
    keep=next(p for p in out if p['split_policy']=='keep')
    assert keep['path'].is_symlink() and keep['source_ref']
    split=[p for p in out if p['split_policy']=='split']
    assert len(split)==2 and all(p['input_storage']=='derived' and not p['path'].is_symlink() and not p.get('source_ref') for p in split)


def test_api_relink_and_unknown_project_no_create(setup):
    home,root,image=setup
    app.copy_images_into_session([image],'book');client=TestClient(app.create_app())
    assert client.get('/api/sources/unknown').status_code==404
    assert not (home/'projects/unknown').exists()
    plan=client.post('/api/sources/preview',json={'session_id':'book','folder':str(root)}).json()
    assert plan['matched']==1
    response=client.post('/api/sources/relink',json={'session_id':'book','folder':str(root),'revision':plan['revision']})
    assert response.status_code==200 and response.json()['status']['connected']==1
    before=(home/'projects/book/project.json').stat().st_mtime_ns
    client.get('/api/sources/book');client.get('/api/sources/book')
    assert (home/'projects/book/project.json').stat().st_mtime_ns==before


def test_same_layout_rebind_keeps_portable_root_id_for_other_host(setup,tmp_path):
    home,root,image=setup
    app.copy_images_into_session([image],'book');base=app.session_dir('book')
    original=load_json(base/'project.json');old_id=original['images'][0]['source_ref']['root_id']
    mini=tmp_path/'mini';mini.mkdir();(mini/'page.png').write_bytes(image.read_bytes())
    lib=SourceLibrary(home,host_id='mini')
    lib.apply(original,base,lib.preview(original,mini))
    assert original['images'][0]['source_ref']['root_id']==old_id
    assert lib.target(original['images'][0]['source_ref'])==mini/'page.png'
    assert app.source_library().target(original['images'][0]['source_ref'])==image


def test_migration_preview_and_apply_preserve_caches_and_owned_crops(setup,monkeypatch):
    home,root,image=setup
    import scripts.migrate_input_sources as module
    monkeypatch.setattr(module,'assert_stopped',lambda:None)
    data=app.ensure_session('legacy');base=app.session_dir('legacy')
    cache=home/'cache/input/page.png';cache.parent.mkdir(parents=True);cache.write_bytes(image.read_bytes())
    alias=base/'input/page.png';atomic_link(cache,alias)
    crop=base/'input/crop.png';atomic_link(cache,crop)
    data['images']=[app.image_manifest_item(alias,digest(cache)),app.image_manifest_item(crop,digest(cache),{'split_policy':'center_crop'})]
    app.save_session(data)
    result=base/'output/final/page_final.jpg';result.write_bytes(b'final retained')
    before=(base/'project.json').read_bytes();link=alias.readlink()
    preview=module.migrate(home,[root],False)
    assert preview['linked']==1 and (base/'project.json').read_bytes()==before and alias.readlink()==link
    report=module.migrate(home,[root],True)
    assert report['linked']==1 and not crop.is_symlink() and crop.read_bytes()==cache.read_bytes()
    assert result.read_bytes()==b'final retained' and cache.exists()
    assert alias.resolve()==image.resolve()
    second=module.migrate(home,[root],True)
    assert second['linked']==0
