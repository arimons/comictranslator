"""미리보기·APFS 백업·상대 링크 복구·롤백을 실제 작은 데이터로 검사한다."""
import json
from pathlib import Path
from PIL import Image
import pytest
from scripts.migrate_workspace import plan, preview, apply, rollback
from pipeline.editor_review_bridge import ReviewStore


def fixture(repo):
    root = repo / 'temp_output'
    base = root / 'v2_sessions/book'
    (base/'input').mkdir(parents=True)
    cache = root/'v2_input_cache/ab/page.png'
    cache.parent.mkdir(parents=True)
    Image.new('RGB',(60,80)).save(cache)
    (base/'input/page.png').symlink_to(cache)
    (base/'session.json').write_text(json.dumps({'project_name':'보존','images':[{'id':'page','input_path':str(base/'input/page.png')}]}))
    return base,cache


def test_preview_preserves_and_migration_rollback_roundtrip(tmp_path,monkeypatch):
    import scripts.migrate_workspace as migration
    # 실행 중 서버와 무관한 테스트 전용 홈만 사용한다.
    monkeypatch.setattr(migration,'assert_stopped',lambda:None)
    repo=tmp_path/'repo';home=tmp_path/'repo/data'
    base,cache=fixture(repo)
    original=(base/'session.json').read_bytes();pixels=cache.read_bytes()
    moves=plan(repo,home);report=preview(repo,home,moves)
    assert report['counts']['links']==1 and (base/'session.json').read_bytes()==original
    record=apply(repo,home,moves)
    assert record['status']=='complete' and record['broken_links']==[]
    project=home/'projects/book'
    assert not (project/'session.json').exists()
    assert json.loads((project/'project.json').read_text())['images'][0]['input_path']=='input/page.png'
    assert not Path((project/'input/page.png').readlink()).is_absolute()
    assert (project/'input/page.png').read_bytes()==pixels
    assert ReviewStore(repo).inspect_page('book','page')['artifacts']['original']['available']
    rollback(home/'migration.json')
    assert (base/'session.json').read_bytes()==original
    assert cache.read_bytes()==pixels
    assert list((home/'legacy').glob('rollback-*'))


def test_destination_collision_stops_before_backup(tmp_path,monkeypatch):
    import scripts.migrate_workspace as migration
    monkeypatch.setattr(migration,'assert_stopped',lambda:None)
    repo=tmp_path/'repo';home=repo/'data'
    base,_=fixture(repo)
    (home/'projects/book').mkdir(parents=True)
    with pytest.raises(RuntimeError,match='충돌'):apply(repo,home,plan(repo,home))
    assert base.is_dir() and not (home/'backups').exists()


def test_rollback_keeps_current_proof_sources(tmp_path,monkeypatch):
    import scripts.migrate_workspace as migration
    monkeypatch.setattr(migration,'assert_stopped',lambda:None)
    repo=tmp_path/'repo';home=tmp_path/'home';backup=home/'backups/old'
    for p in [repo/'proof/src',backup/'proof/src']:
        p.mkdir(parents=True)
    (repo/'proof/src/tool.py').write_text('current code')
    (backup/'proof/src/tool.py').write_text('old code')
    (backup/'proof/generated.txt').write_text('old data')
    journal=home/'migration.json'
    migration.write(journal,{'repo':str(repo),'home':str(home),'backup':str(backup),
                             'completed_moves':[],'roots':[str(repo/'proof')]})
    rollback(journal)
    assert (repo/'proof/src/tool.py').read_text()=='current code'
    assert (repo/'proof/generated.txt').read_text()=='old data'
