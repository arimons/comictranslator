import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main_v2
from pipeline.manual_guidance import write_json
from pipeline.work_glossary import GlossaryStore
from pipeline.work_matching import WorkMatcher, cv_hints, existing_candidates

WORK = {'title': '俺の死亡フラグが留まるところを知らない', 'author': '原作：泉／漫画：乙須ミツヤ', 'edition': '漫画版'}
LABELS = ['まるとこ', '俺', '死亡', 'フ', '知ら', 'ない', '漫画', '乙須', 'ミツヤ', '原作', '泉', 'Ore', 'SHIBOU', 'FLAG', 'DL-Raw.Net', '2']


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(main_v2, 'SESSION_ROOT', tmp_path / 'sessions')
    store = GlossaryStore(tmp_path / 'glossaries', researcher=lambda work: {'entries': [], 'work_matches': True})
    matcher = WorkMatcher()
    monkeypatch.setattr(main_v2, 'glossary_store', lambda: store)
    monkeypatch.setattr(main_v2, 'WORK_MATCHER', matcher)
    session = main_v2.ensure_session('ore')
    session.update(project_name='Ore_2', images=[{'id': '00001', 'input_path': str(tmp_path / '00001.jpg')}])
    main_v2.save_session(session)
    write_json(main_v2.session_dir('ore') / 'work/json/00001_cv.json', [{'label': label} for label in LABELS])
    yield store, matcher, TestClient(main_v2.create_app())
    store.pool.shutdown(wait=True)


def test_hints_exclude_watermarks_and_only_use_first_available_cv(env):
    hints, _ = main_v2.work_match_inputs('ore')
    assert hints['has_cv'] and hints['pages'] == ['00001']
    assert 'Ore_2' not in hints['query']
    assert 'SHIBOU' in hints['query'] and 'ミツヤ' in hints['query']
    assert 'DL-Raw.Net' not in hints['labels'] and '2' not in hints['labels']


def test_ore_auto_reuses_confirmed_work_without_search_or_research(env):
    store, matcher, client = env
    wid = store.confirm(WORK)
    def forbidden(*args): raise AssertionError('로컬 연결은 API 호출을 하지 않아야 한다')
    matcher.searcher = forbidden
    store.researcher = forbidden
    preview = client.get('/api/glossary/ore').json()
    assert preview['glossary'] is None
    assert preview['matching']['candidates'][0]['strong']
    result = client.post('/api/glossary/match', json={'session_id': 'ore'}).json()
    assert result['glossary']['work_id'] == wid
    assert result['glossary']['status'] == 'idle'
    assert result['matching']['status'] == 'candidates'
    assert len(list(store.root.glob('*.json'))) == 1
    # 프로젝트 표시명을 바꿔도 작품 연결은 유지한다.
    client.post('/api/session/meta', json={'session_id': 'ore', 'project_name': '임의 프로젝트명'})
    assert client.get('/api/glossary/ore').json()['glossary']['work_id'] == wid


def test_title_without_author_is_only_a_candidate(env):
    hints, _ = main_v2.work_match_inputs('ore')
    hints['labels'] = ['死亡', '留まる', 'ところ', '知ら', 'ない', '原作', '漫画']
    candidates = existing_candidates(hints, [{'work_id': 'a' * 24, 'work': WORK}])
    assert candidates and not candidates[0]['strong']


def test_ambiguous_existing_candidates_require_confirmation(env):
    store, matcher, client = env
    one = store.confirm(WORK)
    two = store.confirm({**WORK, 'edition': '小説版'})
    result = client.post('/api/glossary/match', json={'session_id': 'ore'}).json()
    assert result['glossary'] is None
    assert len(result['matching']['candidates']) == 2
    response = client.post('/api/glossary/confirm', json={'session_id': 'ore', 'work_id': one})
    assert response.json()['glossary']['work_id'] == one
    assert one != two


def test_author_format_reuses_identity_and_preserves_old_id(env):
    store, _, _ = env
    first = store.confirm(WORK)
    second = store.confirm({**WORK, 'author': '乙須ミツヤ、泉'})
    assert first == second
    assert store.confirm({**WORK, 'author': '別の作者'}) != first


def test_no_cv_waits_without_paid_calls_and_does_not_create_unknown_sessions(env):
    _, matcher, client = env
    (main_v2.session_dir('ore') / 'work/json/00001_cv.json').unlink()
    result = client.post('/api/glossary/match', json={'session_id': 'ore'}).json()
    assert result['matching']['status'] == 'waiting_cv'
    assert client.get('/api/glossary/nonexistent').status_code == 400
    assert not main_v2.session_dir('nonexistent').exists()


def test_short_author_fragment_does_not_auto_link(env):
    hints, _ = main_v2.work_match_inputs('ore')
    hints['labels'] = ['死亡', '留まる', 'ところ', '知ら', 'ない', 'ミツヤ']
    result = existing_candidates(hints, [{'work_id': 'a' * 24, 'work': WORK}])
    assert result and not result[0]['strong']


def test_local_keyword_lookup_and_legacy_web_search_never_calls_provider(env):
    store, _, client = env
    wid = store.confirm(WORK)
    result = client.post('/api/glossary/match', json={'session_id':'ore','query':'死亡フラグ'}).json()
    assert result['matching']['candidates'][0]['work_id'] == wid
    assert result['matching']['query'] == '死亡フラグ'
    assert client.post('/api/glossary/match', json={'session_id':'ore','force_web':True}).status_code == 400
    assert client.get('/api/glossary/ore').json()['glossary'] is None
