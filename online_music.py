"""On-demand GD music search and bounded imports into the portable local library."""
from collections import deque
import ipaddress
import json
import re
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


API = 'https://music-api.gdstudio.xyz/api.php'
SOURCES = {'netease': '网易云', 'tencent': 'QQ音乐', 'kuwo': '酷我音乐',
           'tidal': 'TIDAL', 'qobuz': 'Qobuz', 'joox': 'JOOX',
           'bilibili': '哔哩哔哩', 'apple': 'Apple Music',
           'ytmusic': 'YouTube Music', 'spotify': 'Spotify'}
MEDIA_DOMAINS = ('gdstudio.xyz', 'music.126.net', 'music.163.com', 'joox.com',
                 'jooxmusic.com', 'qq.com', 'gtimg.com', 'bilivideo.com', 'bilivideo.cn', 'bilivideo.cloud',
                 'kuwo.cn', 'kuwo.com', 'tidal.com', 'tidalhifi.com', 'akamaized.net',
                 'qobuz.com', 'qobuz.net', 'apple.com', 'mzstatic.com',
                 'googlevideo.com', 'googleusercontent.com', 'scdn.co', 'spotifycdn.com')
MAX_BYTES = 100 * 1024 * 1024


class OnlineMusicError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def public_url(value):
    """Only provider/CDN HTTPS destinations, including every redirect hop."""
    try:
        if not isinstance(value, str) or len(value) > 6000 or re.search(r'[\x00-\x20\x7f\\]', value):
            raise ValueError
        parts = urlsplit(value)
        host = (parts.hostname or '').lower()
        if (parts.scheme not in ('https', 'http') or parts.username or parts.password or
                parts.port not in (None, 80, 443) or not any(host == domain or host.endswith('.' + domain) for domain in MEDIA_DOMAINS)):
            raise ValueError
        # Some providers still return HTTP links; require the same CDN over TLS.
        value = urlunsplit(('https', host, parts.path or '/', parts.query, ''))
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
            raise ValueError
        return value
    except (ValueError, OSError):
        raise OnlineMusicError(502, '音乐源返回了不可用的音频地址，请换一首或更换音乐源。') from None


class _Redirects(HTTPRedirectHandler):
    max_redirections = 4
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return super().redirect_request(request, fp, code, message, headers, public_url(urljoin(request.full_url, newurl)))


def fetch_bytes(url, maximum, *, seconds=35):
    url = public_url(url)
    request = Request(url, headers={'User-Agent': 'Photo-Journal/1.0', 'Accept-Encoding': 'identity',
                                   'Referer': 'https://www.bilibili.com/' if 'bilivideo.' in urlsplit(url).hostname else 'https://music.gdstudio.xyz/'})
    deadline = time.monotonic() + seconds
    try:
        with build_opener(_Redirects()).open(request, timeout=12) as response:
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > maximum):
                raise OnlineMusicError(413, '这首音乐超过可导入大小，请更换曲目或音质。')
            content = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise OnlineMusicError(504, '连接音乐源超时，请稍后重试。')
                chunk = response.read(min(64 * 1024, maximum + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > maximum:
                    raise OnlineMusicError(413, '这首音乐超过可导入大小，请更换曲目或音质。')
            if length is not None and len(content) != int(length):
                raise OnlineMusicError(502, '音频未能完整接收，请重新导入。')
            return bytes(content)
    except HTTPError as error:
        if error.code == 429:
            raise OnlineMusicError(429, '音乐站访问较频繁，请稍等一会儿再试。') from None
        raise OnlineMusicError(502, '音乐源暂时不可用，请稍后重试或更换音乐源。') from None
    except (URLError, OSError, TimeoutError):
        raise OnlineMusicError(502, '暂时无法连接音乐站，请检查网络后重试。') from None


def _text(value, maximum=120):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '')).strip()[:maximum]


class OnlineMusic:
    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._api_lock = threading.Lock()
        self._import_lock = threading.Lock()
        self._requests = deque()
        self._searches, self._records, self._urls = {}, {}, {}

    def _api(self, parameters):
        # Stay below the provider's published 50 calls / 5 minutes limit.
        with self._api_lock:
            now = time.monotonic()
            while self._requests and self._requests[0] < now - 300:
                self._requests.popleft()
            if len(self._requests) >= 40:
                raise OnlineMusicError(429, '在线音乐访问较频繁，请稍等几分钟后重试。')
            self._requests.append(now)
            raw = fetch_bytes(API + '?' + urlencode(parameters), 2 * 1024 * 1024)
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError):
                raise OnlineMusicError(502, '音乐站暂时没有返回有效结果，请稍后重试。') from None

    def _prune(self):
        now = time.monotonic()
        self._records = {key: item for key, item in self._records.items() if item[0] > now - 900}
        self._urls = {key: item for key, item in self._urls.items() if key in self._records and item[0] > now - 180}
        self._searches = {key: item for key, item in self._searches.items() if item[0] > now - 240}
        while len(self._records) > 600:
            self._records.pop(next(iter(self._records)))
        while len(self._searches) > 30:
            self._searches.pop(next(iter(self._searches)))

    def search(self, value):
        if not isinstance(value, dict) or set(value) - {'query', 'source', 'page'}:
            raise OnlineMusicError(400, '在线搜索参数不正确。')
        query, source, page = value.get('query'), value.get('source', 'netease'), value.get('page', 1)
        if (not isinstance(query, str) or not 1 <= len(query.strip()) <= 80 or re.search(r'[\x00-\x1f\x7f]', query) or
                not isinstance(source, str) or source not in SOURCES or type(page) is not int or not 1 <= page <= 50):
            raise OnlineMusicError(400, '请输入歌名、歌手或专辑，并选择有效的音乐源。')
        query = query.strip()
        with self._lock:
            self._prune()
            key = (query, source, page)
            if key in self._searches:
                return self._searches[key][1]
            raw = self._api({'types': 'search', 'source': source, 'name': query, 'count': 20, 'pages': page})
            if not isinstance(raw, list):
                raise OnlineMusicError(502, '这个音乐源暂时无法搜索，请更换音乐源后重试。')
            results = []
            for item in raw[:20]:
                if not isinstance(item, dict) or item.get('source', source) != source:
                    continue
                track_id, title = _text(item.get('id'), 256), _text(item.get('name'))
                if not track_id or not title:
                    continue
                artist = item.get('artist')
                artist = ' / '.join(_text(part, 60) for part in artist[:10]) if isinstance(artist, list) else _text(artist)
                record = {'token': uuid.uuid4().hex, 'title': title, 'artist': _text(artist),
                          'album': _text(item.get('album')), 'source': source}
                self._records[record['token']] = (time.monotonic(), {**record, 'id': track_id})
                results.append(record)
            result = {'tracks': results, 'page': page, 'has_more': len(raw) >= 20}
            self._searches[key] = (time.monotonic(), result)
            self._prune()
            return result

    def _record(self, value):
        if not isinstance(value, dict) or set(value) != {'token'} or not isinstance(value.get('token'), str):
            raise OnlineMusicError(400, '请选择搜索结果中的曲目。')
        self._prune()
        item = self._records.get(value['token'])
        if not item:
            raise OnlineMusicError(410, '这条搜索结果已过期，请重新搜索后选择。')
        return item[1]

    def preview(self, value):
        with self._lock:
            record = self._record(value)
            token = record['token']
            if token not in self._urls:
                raw = self._api({'types': 'url', 'source': record['source'], 'id': record['id'], 'br': 320})
                if not isinstance(raw, dict) or not raw.get('url'):
                    raise OnlineMusicError(404, '这首音乐暂时没有可用音频，可尝试更换音乐源。')
                self._urls[token] = (time.monotonic(), public_url(urljoin(API, raw['url'])))
            return {'url': self._urls[token][1]}

    def import_track(self, value):
        if not self._import_lock.acquire(blocking=False):
            raise OnlineMusicError(409, '已有在线音乐正在导入，请等待完成。')
        try:
            with self._lock:
                record = dict(self._record(value))
            url = self.preview(value)['url']
            try:
                data = fetch_bytes(url, MAX_BYTES, seconds=120)
            except OnlineMusicError:
                with self._lock:
                    self._urls.pop(record['token'], None)
                raise
            from server import AUDIO_MIME, audio_header_valid
            extension = next((suffix for suffix in AUDIO_MIME if audio_header_valid(data, suffix)), None)
            if extension is None:
                raise OnlineMusicError(415, '音乐源返回的内容不是可用音频，请更换曲目或音乐源。')
            stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', record['title']).strip(' .') or '在线音乐'
            return self.store.import_music(data, stem[:120] + extension, title=record['title'], artist=record['artist'])
        finally:
            self._import_lock.release()
