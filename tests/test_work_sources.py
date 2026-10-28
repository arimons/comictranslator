"""작품 URL 본문 수집의 공개 주소 제한."""
import pytest
from pipeline import work_sources as sources

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


@pytest.mark.parametrize('location, found', [
    ('/w/%EC%96%B4%EB%96%A4%20%EA%B3%BC%ED%95%99%EC%9D%98%20%EC%B4%88%EC%A0%84%EC%9E%90%ED%8F%AC', True),
    ('/Search?q=zz', False),
    ('https://evil.example/w/a', None),
])
def test_namu_lookup_reads_redirect_only(monkeypatch, location, found):
    requested = []
    class Response:
        status = 302
        def getheader(self, key, default=''): return location if key == 'Location' else default
        def read(self, maximum): return b''
    class Connection:
        def __init__(self, host, timeout): requested.append(host)
        def request(self, method, path, headers): requested.append(path)
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(sources, 'PublicConnection', Connection)
    if found is None:
        with pytest.raises(ValueError): sources.namu_lookup('어떤과학의 초전자포')
        return
    result = sources.namu_lookup('  어떤과학의   초전자포 ')
    assert requested[0] == 'namu.wiki' and requested[1].startswith('/Go?q=%EC%96%B4')
    assert result['found'] is found and result['url'].startswith('https://namu.wiki/')
    with pytest.raises(ValueError): sources.namu_lookup('  ')
