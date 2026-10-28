"""사용자가 지정한 공개 작품 URL의 본문만 제한적으로 수집한다."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 2_000_000
MAX_TEXT = 16_000


def public_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('로그인 정보가 없는 공개 HTTPS URL을 입력하세요.')
    if len(value) > 2000:
        raise ValueError('URL은 2000자 이내로 입력하세요.')
    return urlunsplit((parsed.scheme, parsed.netloc, quote(parsed.path or '/', safe='/%:@'), quote(parsed.query, safe='%=&?/:+@,;'), ''))


def public_addresses(host: str) -> list[str]:
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('내부 네트워크 주소는 자료로 수집할 수 없습니다.')
    return addresses


class PublicConnection(http.client.HTTPSConnection):
    """검증한 IP로 연결하되 인증서와 SNI는 원래 호스트로 검증한다."""
    def connect(self):
        address = public_addresses(self.host)[0]
        sock = socket.create_connection((address, 443), self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header'):
            self.skip += 1
        if tag == 'title':
            self.in_title = True
        if not self.skip and tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'section'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header') and self.skip:
            self.skip -= 1
        if tag == 'title':
            self.in_title = False
        if not self.skip and tag in ('p', 'div', 'li', 'tr', 'section'):
            self.parts.append('\n')

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.skip:
            self.parts.append(data)


def document(url: str, text: str, title: str, mode: str) -> dict:
    text = '\n'.join(re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip())
    if len(text) < 30:
        raise ValueError('읽을 수 있는 본문이 부족합니다. 필요한 내용을 직접 붙여 넣으세요.')
    return {'url': url, 'title': title[:200] or url, 'text': text[:MAX_TEXT], 'truncated': len(text) > MAX_TEXT,
            'mode': mode, 'content_hash': hashlib.sha256(text[:MAX_TEXT].encode()).hexdigest(),
            'fetched_at': datetime.now(timezone.utc).isoformat()}


def fetch_source(value: str) -> dict:
    url = public_url(value)
    for _ in range(4):
        parsed = urlsplit(url)
        # 리디렉션마다 검증하고 실제 연결도 검증된 IP에 고정한다.
        public_addresses(parsed.hostname)
        connection = PublicConnection(parsed.hostname, timeout=15)
        try:
            path = parsed.path + ('?' + parsed.query if parsed.query else '')
            connection.request('GET', path, headers={'User-Agent': 'ComicTranslator/1.0 (user-requested work reference)', 'Accept': 'text/html,text/plain', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = public_url(urljoin(url, response.getheader('Location', '')))
                continue
            if response.status != 200:
                raise ValueError(f'자료 수집 HTTP {response.status}. 원문을 확인한 뒤 필요한 본문을 직접 붙여 넣을 수 있습니다.')
            mime = response.getheader('Content-Type', '').lower()
            if not any(kind in mime for kind in ('text/html', 'text/plain', 'application/xhtml+xml')):
                raise ValueError('HTML 또는 텍스트 자료 URL만 지원합니다.')
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError('자료가 너무 큽니다(최대 2MB). 필요한 부분만 직접 붙여 넣으세요.')
            charset = re.search(r'charset=["\']?([\w-]+)', mime)
            try:
                decoded = body.decode(charset.group(1) if charset else 'utf-8', errors='replace')
            except LookupError:
                decoded = body.decode('utf-8', errors='replace')
            if 'text/plain' in mime:
                return document(url, decoded, parsed.hostname, 'url')
            parser = PageText()
            parser.feed(decoded)
            text = ''.join(parser.parts)
            if re.search(r'Just a moment|Verify you are human|Checking your browser|Access denied', ''.join(parser.title), re.I):
                raise ValueError('사이트가 자동 접근을 제한합니다. 필요한 본문을 직접 붙여 넣으세요.')
            return document(url, text, ''.join(parser.title), 'url')
        finally:
            connection.close()
    raise ValueError('리디렉션이 너무 많습니다. 최종 문서 URL을 입력하세요.')



NAMU_HOST = 'namu.wiki'


def namu_lookup(title: str) -> dict:
    """작품명으로 나무위키 문서 주소를 찾는다. 나무위키의 이동 검색(/Go)이 맞는 문서로 보내는 Location만 읽는다.

    정확한 문서가 없으면 나무위키 검색 결과 주소를 found=False로 돌려준다. 본문은 받지 않는다.
    """
    query = re.sub(r'\s+', ' ', title or '').strip()
    if not query:
        raise ValueError('작품명을 입력하세요.')
    if len(query) > 200:
        raise ValueError('작품명은 200자 이내로 입력하세요.')
    connection = PublicConnection(NAMU_HOST, timeout=10)
    try:
        # 나무위키는 기본 클라이언트 표시를 막는 경우가 있어 일반 브라우저와 같은 형식의 User-Agent를 쓴다.
        connection.request('GET', '/Go?q=' + quote(query, safe=''), headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36',
            'Accept': 'text/html', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        location = response.getheader('Location', '') if response.status in (301, 302, 303, 307, 308) else ''
        response.read(1)
    finally:
        connection.close()
    if not location:
        raise ValueError(f'나무위키 검색 응답 HTTP {response.status}. 직접 검색해 주소를 붙여 넣으세요.')
    url = urljoin(f'https://{NAMU_HOST}/', location)
    parsed = urlsplit(url)
    if parsed.hostname != NAMU_HOST:
        raise ValueError('나무위키 밖으로 이동하는 응답이라 사용하지 않습니다.')
    return {'url': public_url(url), 'found': parsed.path.startswith('/w/'), 'query': query}
