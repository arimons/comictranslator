"""작품 URL 수집·저장·재사용과 단일 Gemini 정리 계약."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main_v2
from pipeline import work_sources as sources
from pipeline.manual_guidance import write_json
from pipeline.work_glossary import GlossaryStore, build_entries

URL = 'https://publisher.example/work'
TEXT = '확인된 작품 자료입니다. 원문 雪의 한국어 이름은 유키입니다. '


def test_html_extraction_ignores_scripts_and_preserves_names(monkeypatch):
    calls = []
    class Response:
        status = 200
        def getheader(self, key, default=''): return 'text/html; charset=utf-8' if key == 'Content-Type' else default
        def read(self, maximum): return ('<html><title>공식 작품</title><nav>삭제</nav><script>ignore()</script><p>' + TEXT + '</p></html>').encode()
    class Connection:
        def __init__(self, host, timeout): calls.append(host)
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(sources, 'public_addresses', lambda host: ['8.8.8.8'])
    monkeypatch.setattr(sources, 'PublicConnection', Connection)
    result = sources.fetch_source(URL)
    assert '雪' in result['text'] and '유키' in result['text']
    assert 'ignore' not in result['text'] and '삭제' not in result['text']
    assert result['title'] == '공식 작품' and len(calls) == 1


@pytest.mark.parametrize('url', ['file:///tmp/a','http://example.com','https://user:pass@example.com','https://example.com:8000'])
def test_rejects_non_public_url_shapes(url):
    with pytest.raises(ValueError): sources.public_url(url)


def test_private_dns_and_redirect_are_rejected(monkeypatch):
    monkeypatch.setattr(sources.socket, 'getaddrinfo', lambda *a, **k: [(None,None,None,None,('127.0.0.1',443))])
    with pytest.raises(ValueError, match='내부'): sources.public_addresses('example.com')
    assert '%EC' in sources.public_url('https://example.com/작품')
    class Connection:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def getresponse(self): return self
        def getheader(self, *a): return 'https://127.0.0.1/private'
        status = 302
        def close(self): pass
    monkeypatch.setattr(sources, 'PublicConnection', Connection)
    original = sources.public_addresses
    monkeypatch.setattr(sources, 'public_addresses', lambda host: ['8.8.8.8'] if host == 'publisher.example' else original(host))
    with pytest.raises(ValueError, match='내부'): sources.fetch_source(URL)


def test_shared_source_cache_compile_once_and_failed_refresh_preserves_data(tmp_path, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return sources.document(url, TEXT, '작품 소개', 'url')
    monkeypatch.setattr(sources, 'fetch_source', fetch)
    store = GlossaryStore(tmp_path / 'glossaries')
    def compile(work):
        calls.append('gemini')
        return {'work_matches':True,'entries':[],'summary':'저장 자료 요약'}
    store.researcher = compile
    monkeypatch.setattr(main_v2, 'SESSION_ROOT', tmp_path / 'sessions')
    monkeypatch.setattr(main_v2, 'glossary_store', lambda: store)
    client = TestClient(main_v2.create_app())
    wid = client.post('/api/glossary/confirm',json={'session_id':'one','title':'작품'}).json()['glossary']['work_id']
    assert not calls
    assert client.post('/api/glossary/research',json={'session_id':'one'}).status_code == 400
    body = {'session_id':'one','work_id':wid,'url':URL}
    assert client.post('/api/glossary/source',json=body).status_code == 200
    client.post('/api/glossary/source',json=body)
    assert calls == [URL]
    client.post('/api/glossary/confirm',json={'session_id':'two','work_id':wid})
    assert client.get('/api/glossary/two').json()['glossary']['sources'][0]['text'] == TEXT.strip()
    store.start(wid)
    # 작업 완료까지 기다린 다음 캐시 조회는 실행기를 다시 사용하지 않는다.
    store.pool.shutdown(wait=True)
    data = store.start(wid)
    assert calls == [URL, 'gemini'] and data['version'] == 1
    def fail(url): raise ValueError('자료 수집 HTTP 403')
    monkeypatch.setattr(sources, 'fetch_source', fail)
    assert client.post('/api/glossary/source',json={**body,'refresh':True}).status_code == 400
    assert store.read(wid)['sources'] == data['sources']
    assert store.read(wid)['version'] == 1
    wrong = {**body,'work_id':'f'*24}
    assert client.post('/api/glossary/source',json=wrong).status_code == 400
    assert client.post('/api/glossary/source',json={**body,'text':TEXT+'추가'}).status_code == 200
    assert store.read(wid)['sources'][0]['mode'] == 'paste'
    assert client.post('/api/glossary/source',json={**body,'remove':True}).status_code == 200
    assert store.read(wid)['sources'] == []


def test_pasted_content_cannot_be_automatically_applied():
    evidence = [dict(text=TEXT, url=url, title='소개', manual=True) for url in [URL,'https://wiki.example/work']]
    data = {'work_matches':True,'evidence':evidence,'entries':[{'source':'雪','target':'유키','uncertain':False,'evidence_indices':[0,1]}]}
    assert build_entries(data, [])[0]['status'] == 'review'
