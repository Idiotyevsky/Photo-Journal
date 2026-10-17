"""Integration checks use only disposable images and a temporary album."""

import hashlib
import http.client
import io
import json
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
import wave
import zipfile
from unittest.mock import Mock, patch
from urllib.parse import quote, unquote

import server
import online_music

try:
    from PIL import Image
except ImportError:
    Image = None


class MP3HeaderTests(unittest.TestCase):
    # Real Layer III frame lengths; channel mode, padding and VBR may change.
    frame_320 = b"\xff\xfb\xe0\x00" + b"\0" * 1040
    frame_channels = b"\xff\xfb\xe0\x64" + b"\0" * 1040
    frame_padding = b"\xff\xfb\xe2\x64" + b"\0" * 1041
    frame_128 = b"\xff\xfb\x90\x00" + b"\0" * 413
    frame_48000 = b"\xff\xfb\xe4\x00" + b"\0" * 956
    frame_mpeg2 = b"\xff\xf3\x90\x00" + b"\0" * 257

    @staticmethod
    def tag(size=8, version=4):
        encoded = bytes((size >> shift) & 127 for shift in (21, 14, 7, 0))
        return b"ID3" + bytes((version, 0, 0)) + encoded + b"\0" * size

    def test_id3_padding_resynchronizes_continuous_frames_with_varying_headers(self):
        # Match the sample layout: declared audio offset 68136 + 2048 padding.
        prefix = self.tag(68126) + b"\0" * 2048
        self.assertEqual(len(prefix), 70184)
        frames = self.frame_320 + self.frame_channels + self.frame_padding
        self.assertTrue(server.audio_header_valid(prefix + frames, ".mp3"))
        self.assertTrue(server.audio_header_valid(self.tag() + b"\0" * 2048 + self.frame_320 + self.frame_128 + self.frame_padding, ".mp3"))
        self.assertFalse(server.audio_header_valid(prefix + frames, ".wav"))

    def test_exact_position_single_complete_frame_stays_compatible(self):
        self.assertTrue(server.audio_header_valid(self.frame_128, ".mp3"))
        self.assertTrue(server.audio_header_valid(self.tag() + self.frame_128, ".mp3"))
        self.assertFalse(server.audio_header_valid(self.frame_128[:-1], ".mp3"))
        self.assertFalse(server.audio_header_valid(b"", ".mp3"))

    def test_padding_does_not_accept_isolated_sync_or_truncated_frames(self):
        prefix = self.tag() + b"\0" * 2048
        for frames in (self.frame_320, self.frame_320 + self.frame_channels,
                       self.frame_320 + self.frame_channels + self.frame_padding[:-1],
                       self.frame_320 + b"junk" + self.frame_channels + self.frame_padding):
            with self.subTest(length=len(frames)):
                self.assertFalse(server.audio_header_valid(prefix + frames, ".mp3"))

    def test_resynchronization_requires_matching_version_and_sample_rate(self):
        prefix = self.tag() + b"\0" * 2048
        self.assertFalse(server.audio_header_valid(prefix + self.frame_320 + self.frame_48000 + self.frame_padding, ".mp3"))
        self.assertFalse(server.audio_header_valid(prefix + self.frame_320 + self.frame_mpeg2 + self.frame_padding, ".mp3"))

    def test_scan_is_bounded_and_invalid_or_truncated_id3_stays_rejected(self):
        frames = self.frame_320 + self.frame_channels + self.frame_padding
        self.assertTrue(server.audio_header_valid(self.tag() + b"\0" * (64 * 1024) + frames, ".mp3"))
        self.assertFalse(server.audio_header_valid(self.tag() + b"\0" * (64 * 1024 + 1) + frames, ".mp3"))
        self.assertFalse(server.audio_header_valid(b"\0" * 20 + frames, ".mp3"))
        valid_header = self.tag()[:10]
        self.assertFalse(server.audio_header_valid(valid_header[:9], ".mp3"))
        malformed = [b"ID3" + b"\0" * 7,
                     valid_header[:3] + b"\x01" + valid_header[4:],
                     valid_header[:5] + b"\x01" + valid_header[6:],
                     valid_header[:6] + b"\x80" + valid_header[7:],
                     self.tag(10000)[:10]]
        for header in malformed:
            with self.subTest(header=header):
                self.assertFalse(server.audio_header_valid(header + b"\0" * 2048 + frames, ".mp3"))


class OnlineMusicNetworkTests(unittest.TestCase):
    def test_all_official_sources_match_ui_and_resolve_cached_audio_paths(self):
        import re
        from urllib.parse import parse_qs, urlsplit
        expected={'netease','tencent','kuwo','tidal','qobuz','joox','bilibili','apple','ytmusic','spotify'}
        self.assertEqual(set(online_music.SOURCES),expected)
        source=(Path(__file__).resolve().parents[1]/'ui/online-music.js').read_text(encoding='utf-8')
        options=json.loads(re.search(r'const SOURCES=(\[.*?\]);',source).group(1).replace("'",'"'))
        self.assertEqual(dict(options),online_music.SOURCES)
        client=online_music.OnlineMusic(None)
        def fetch(url,*_args,**_kwargs):
            values=parse_qs(urlsplit(url).query)
            if values['types']==['search']:
                return json.dumps([{'id':'id','name':'Song','source':values['source'][0]}]).encode()
            return b'{"url":"cache/audio.m4a"}'
        with patch('online_music.fetch_bytes',side_effect=fetch),patch('online_music.public_url',side_effect=lambda value:value):
            for name in expected:
                with self.subTest(source=name):
                    result=client.search({'query':'Song','source':name})
                    self.assertEqual(result['tracks'][0]['source'],name)
                    self.assertEqual(client.preview({'token':result['tracks'][0]['token']})['url'],'https://music-api.gdstudio.xyz/cache/audio.m4a')

    def test_only_public_provider_hosts_and_https_redirects_are_accepted(self):
        public = [(2, 1, 6, '', ('8.8.8.8', 443))]
        with patch('online_music.socket.getaddrinfo', return_value=public):
            self.assertEqual(online_music.public_url('http://m801.music.126.net/song.mp3'), 'https://m801.music.126.net/song.mp3')
            for url in ('file:///secret', 'http://127.0.0.1/', 'https://evil.example/music',
                        'https://music.126.net.evil.example/a', 'https://user@music.126.net/a',
                        'https://music.126.net:123/a', 'https://music.126.net/\\secret'):
                with self.subTest(url=url), self.assertRaises(online_music.OnlineMusicError):
                    online_music.public_url(url)
        for ip in ('127.0.0.1', '192.168.1.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'):
            with patch('online_music.socket.getaddrinfo', return_value=[(2, 1, 6, '', (ip, 443))]):
                with self.assertRaises(online_music.OnlineMusicError):
                    online_music.public_url('https://music.126.net/a.mp3')

    def test_download_limits_truncation_and_redirect_destinations(self):
        for body, headers, maximum in ((b'a' * 11, {}, 10), (b'a', {'Content-Length': '11'}, 10), (b'a', {'Content-Length': '3'}, 10)):
            response = io.BytesIO(body); response.headers = headers
            opener = Mock(); opener.open.return_value = response
            with patch('online_music.public_url', side_effect=lambda value:value), patch('online_music.build_opener', return_value=opener):
                with self.assertRaises(online_music.OnlineMusicError):
                    online_music.fetch_bytes('https://music.126.net/a.mp3', maximum)
        with patch('online_music.public_url', side_effect=online_music.OnlineMusicError(502, 'unsafe')):
            with self.assertRaises(online_music.OnlineMusicError):
                online_music._Redirects().redirect_request(Mock(full_url='https://music.126.net/a'), None, 302, '', {}, 'http://localhost/private')


@unittest.skipUnless(Image, "Pillow is needed for image integration checks")
class AlbumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        web = self.root / "ui"
        web.mkdir()
        (web / "index.html").write_text("<h1>Album</h1>", encoding="utf-8")
        audio_dir = web / "audio"
        audio_dir.mkdir()
        self.builtin_audio = {}
        builtin_tracks = []
        for number, track_id in enumerate(("hymn", "the-long-way-home", "castles-in-the-sky")):
            data = b"\xff\xfb\x90\x00" + bytes([number]) * 413
            self.builtin_audio[track_id] = data
            (audio_dir / (track_id + ".mp3")).write_bytes(data)
            builtin_tracks.append({"id": track_id, "title": track_id.title(), "artist": "Scott Buckley", "url": "/audio/" + track_id + ".mp3", "source": "https://www.scottbuckley.com.au/library/" + track_id + "/", "license": "CC BY 4.0", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        (audio_dir / "tracks.json").write_text(json.dumps({"tracks": builtin_tracks}), encoding="utf-8")
        (self.root / "private.txt").write_text("secret", encoding="utf-8")
        self.application_file = self.root / "拾光相册.exe"
        self.application_file.write_bytes(b"MZ-test-desktop-application")
        self.server = server.AlbumHTTPServer(0, self.root / "data", web, application_file=self.application_file)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        photo = Image.new("RGB", (32, 20), "#6b8e85")
        buffer = io.BytesIO()
        photo.save(buffer, "JPEG")
        self.image = buffer.getvalue()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        content_type = response.getheader("Content-Type", "")
        result = json.loads(data) if "application/json" in content_type and data else data
        status = response.status
        connection.close()
        return status, result

    def import_image(self, body=None, filename="旅行.jpg", suffix=""):
        return self.request("POST", "/api/import?filename=" + quote(filename) + "&modified=2025-05-03T09:10:11" + suffix, body or self.image, {"Content-Type": "application/octet-stream"})

    def json_request(self, method, path, payload):
        return self.request(method, path, json.dumps(payload).encode(), {"Content-Type": "application/json"})

    def create_collection(self, title="旅行作品集", **fields):
        status, result = self.json_request("POST", "/api/collections", {"title": title, **fields})
        self.assertEqual(status, 201)
        return result["collection"]

    @staticmethod
    def wav_audio():
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * 80)
        return buffer.getvalue()

    def import_audio(self, data, filename="音乐.wav"):
        return self.request("POST", "/api/music/import?filename=" + quote(filename), data, {"Content-Type": "application/octet-stream"})

    def file_response(self, path, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    @contextmanager
    def backup_temporaries(self):
        original = tempfile.TemporaryDirectory
        paths, cleaned = [], threading.Event()

        @contextmanager
        def tracked_directory(*args, **kwargs):
            try:
                with original(*args, dir=self.root, **kwargs) as name:
                    paths.append(Path(name))
                    yield name
            finally:
                cleaned.set()

        with patch.object(server.tempfile, "TemporaryDirectory", tracked_directory):
            yield paths, cleaned

    def test_backup_zip_migrates_wal_database_photos_music_and_preferences(self):
        photo = self.import_image()[1]["photo"]
        audio = self.wav_audio()
        track = self.import_audio(audio, "本地曲目.wav")[1]["track"]
        edited = self.json_request("POST", "/api/music/" + track["id"], {"title": "迁移后的曲名", "enabled": False})[1]["track"]
        removed_audio = audio[:-2] + b"\x01\x02"
        removed = self.import_audio(removed_audio, "保留恢复.wav")[1]["track"]
        self.request("POST", "/api/music/" + removed["id"] + "/remove")
        self.json_request("POST", "/api/music/hymn", {"title": "保存的内置曲名", "enabled": False})
        self.json_request("POST", "/api/settings", {"theme": "italian", "music_volume": 0.37})
        collection = self.create_collection("随身作品集")
        self.json_request("POST", "/api/collections/" + collection["id"] + "/photos", {"photo_ids": [photo["id"]]})
        self.json_request("POST", "/api/journals/" + quote("trip:本地旅行"), {"title": "保留的手记", "note": "迁移正文"})
        self.json_request("POST", "/api/atlas", {"stops": [{"name": "扬州", "lat": 32.39, "lon": 119.41}]})
        for relative in (".webview/cache.dat", ".delete-staging/old/original", "desktop.log", ".desktop-instance.json", "unexpected.txt"):
            path = self.server.store.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"must not be in backup")
        # Keep committed changes in a live WAL so merely copying album.sqlite3
        # would fail to preserve this caption.
        with self.server.store.connect() as writer:
            writer.execute("UPDATE photos SET caption = ? WHERE id = ?", ("来自 WAL 的最新文字", photo["id"]))
            writer.commit()
            self.assertTrue(self.server.store.database.with_name("album.sqlite3-wal").exists())
            with self.backup_temporaries() as (temporary_paths, cleaned):
                status, headers, body = self.file_response("/api/backup")
                self.assertTrue(cleaned.wait(2))
                self.assertTrue(temporary_paths)
                self.assertTrue(all(not path.exists() for path in temporary_paths))
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])
        prefix = "拾光相册/"
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertTrue({prefix + "拾光相册.exe", prefix + "迁移说明.txt", prefix + "album-data/album.sqlite3"}.issubset(names))
            self.assertTrue(all(name.startswith(prefix) for name in names))
            self.assertFalse(any(any(excluded in name for excluded in (".webview", ".delete-staging", "desktop.log", ".desktop-instance", "-wal", "-shm", "unexpected")) for name in names))
            self.assertEqual(archive.read(prefix + "拾光相册.exe"), self.application_file.read_bytes())
            self.assertEqual(archive.read(prefix + "album-data/originals/" + photo["id"] + ".jpg"), self.image)
            self.assertEqual(archive.read(prefix + "album-data/music/" + track["id"] + ".wav"), audio)
            self.assertEqual(archive.read(prefix + "album-data/music/" + removed["id"] + ".wav"), removed_audio)
            archive.extractall(self.root / "new-location")
        migrated_root = self.root / "new-location" / "拾光相册"
        with closing(sqlite3.connect(migrated_root / "album-data" / "album.sqlite3")) as snapshot:
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(snapshot.execute("SELECT caption FROM photos WHERE id = ?", (photo["id"],)).fetchone()[0], "来自 WAL 的最新文字")
            self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM collection_photos").fetchone()[0], 1)
            self.assertEqual(snapshot.execute("SELECT note FROM journals").fetchone()[0], "迁移正文")
        migrated = server.AlbumStore(migrated_root / "album-data", self.server.web_dir)
        self.assertEqual(migrated.settings(), {"theme": "italian", "music_volume": 0.37})
        self.assertEqual(next(value for value in migrated.music()["tracks"] if value["id"] == track["id"]), edited)
        self.assertFalse(next(value for value in migrated.music()["tracks"] if value["id"] == "hymn")["enabled"])
        self.assertEqual(migrated.atlas()["stops"][0]["name"], "扬州")
        duplicate = migrated.import_music(audio, "另一名称.wav")
        self.assertTrue(duplicate["duplicate"])
        self.assertFalse(duplicate["track"]["enabled"])
        restored = migrated.import_music(removed_audio, "再次导入.wav")
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["track"]["title"], "保留恢复")
        self.assertEqual(migrated.music_path(track["id"], ".wav").read_bytes(), audio)

    def test_backup_refuses_missing_executable_original_preview_or_music(self):
        photo = self.import_image()[1]["photo"]
        track = self.import_audio(self.wav_audio())[1]["track"]
        original = self.server.store.originals / (photo["id"] + ".jpg")
        preview = self.server.store.previews / (photo["id"] + ".jpg")
        music = self.server.store.music_dir / (track["id"] + ".wav")
        for path, expected_status in ((self.application_file, 503), (original, 409), (preview, 409), (music, 409)):
            with self.subTest(file=path.name):
                parked = self.root / (path.name + ".parked")
                path.replace(parked)
                try:
                    with self.backup_temporaries() as (temporary_paths, cleaned):
                        status, result = self.request("GET", "/api/backup")
                        self.assertEqual(status, expected_status)
                        self.assertIn("error", result)
                        if temporary_paths:
                            self.assertTrue(cleaned.wait(2))
                        self.assertTrue(all(not item.exists() for item in temporary_paths))
                finally:
                    parked.replace(path)

    def test_backup_rejects_media_resolving_outside_album(self):
        photo = self.import_image()[1]["photo"]
        original = self.server.store.originals / (photo["id"] + ".jpg")
        outside = self.root / "private.txt"
        real_resolve = Path.resolve

        def redirected_resolve(path, *args, **kwargs):
            return outside if path == original else real_resolve(path, *args, **kwargs)

        with self.backup_temporaries() as (temporary_paths, cleaned), patch.object(Path, "resolve", redirected_resolve):
            self.assertEqual(self.request("GET", "/api/backup")[0], 409)
            self.assertTrue(cleaned.wait(2))
            self.assertTrue(all(not item.exists() for item in temporary_paths))
        self.assertEqual(original.read_bytes(), self.image)

    def test_backup_generation_failure_and_interrupted_transfer_clean_temporaries(self):
        self.import_image()
        with self.backup_temporaries() as (temporary_paths, cleaned), patch.object(zipfile.ZipFile, "write", side_effect=OSError("injected backup disk error")):
            self.assertEqual(self.request("GET", "/api/backup")[0], 500)
            self.assertTrue(cleaned.wait(2))
            self.assertTrue(all(not item.exists() for item in temporary_paths))
        with self.backup_temporaries() as (temporary_paths, cleaned), patch.object(server.AlbumHandler, "stream_backup", side_effect=ConnectionResetError("injected interrupted download")):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            try:
                connection.request("GET", "/api/backup")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                with self.assertRaises(http.client.IncompleteRead):
                    response.read()
            finally:
                connection.close()
            self.assertTrue(cleaned.wait(2))
            self.assertTrue(all(not item.exists() for item in temporary_paths))

    def test_backup_same_origin_validation_and_head_cleanup(self):
        for headers in ({"Origin": "https://example.invalid"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Host": "example.invalid"}):
            self.assertEqual(self.request("GET", "/api/backup", headers=headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/%62ackup", headers={"Origin": "https://example.invalid"})[0], 403)
        with self.backup_temporaries() as (temporary_paths, cleaned):
            status, headers, body = self.file_response("/api/backup", method="HEAD", headers={"Origin": "http://127.0.0.1:" + str(self.server.server_port), "Sec-Fetch-Site": "same-origin"})
            self.assertEqual((status, body), (200, b""))
            self.assertGreater(int(headers["Content-Length"]), 0)
            self.assertTrue(cleaned.wait(2))
            self.assertTrue(all(not item.exists() for item in temporary_paths))

    def test_backup_lock_keeps_photo_deletion_after_media_snapshot(self):
        photo = self.import_image()[1]["photo"]
        archive_started, release_archive, deletion_done = threading.Event(), threading.Event(), threading.Event()
        original_write = zipfile.ZipFile.write
        results, errors = [], []

        def paused_write(archive, *args, **kwargs):
            if not archive_started.is_set():
                archive_started.set()
                if not release_archive.wait(5):
                    raise TimeoutError("test archive was not released")
            return original_write(archive, *args, **kwargs)

        def download():
            try:
                results.append(self.request("GET", "/api/backup"))
            except Exception as error:
                errors.append(error)

        def delete_photo():
            try:
                self.server.store.delete_photo(photo["id"])
            except Exception as error:
                errors.append(error)
            finally:
                deletion_done.set()

        with patch.object(zipfile.ZipFile, "write", paused_write):
            downloading = threading.Thread(target=download)
            downloading.start()
            deleting = None
            try:
                self.assertTrue(archive_started.wait(3))
                deleting = threading.Thread(target=delete_photo)
                deleting.start()
                self.assertFalse(deletion_done.wait(0.1))
            finally:
                release_archive.set()
                downloading.join(5)
                if deleting:
                    deleting.join(5)
        self.assertFalse(errors)
        self.assertTrue(deletion_done.is_set())
        self.assertEqual(results[0][0], 200)
        with zipfile.ZipFile(io.BytesIO(results[0][1])) as archive:
            self.assertEqual(archive.read("拾光相册/album-data/originals/" + photo["id"] + ".jpg"), self.image)
            snapshot_path = self.root / "locked-snapshot.sqlite3"
            snapshot_path.write_bytes(archive.read("拾光相册/album-data/album.sqlite3"))
        with closing(sqlite3.connect(snapshot_path)) as snapshot:
            self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 1)
        self.assertEqual(self.server.store.photos()["photos"], [])

    def test_framed_photo_routes_preview_attachment_and_preserves_original(self):
        photo = self.import_image()[1]["photo"]
        self.json_request("POST", "/api/photos/" + photo["id"], {"title": '广州 / 雨后\\夕阳:\r\n"片刻"'})
        photo = self.server.store.photos()["photos"][0]
        original = self.server.store.originals / (photo["id"] + ".jpg")
        original_digest = hashlib.sha256(original.read_bytes()).digest()
        rendered = io.BytesIO()
        Image.new("RGB", (32, 30), "white").save(rendered, "JPEG")
        image = rendered.getvalue()
        render = Mock(return_value=image)
        path = "/api/photos/" + photo["id"] + "/framed"
        with patch.dict(sys.modules, {"photo_export": types.SimpleNamespace(render_framed_photo=render)}):
            status, headers, body = self.file_response(path)
            self.assertEqual((status, body), (200, image))
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(int(headers["Content-Length"]), len(image))
            disposition = headers["Content-Disposition"]
            self.assertTrue(disposition.startswith("attachment;"))
            filename = unquote(disposition.split("filename*=UTF-8''", 1)[1])
            self.assertTrue(filename.endswith("-白边.jpg"))
            self.assertIn("广州", filename)
            self.assertFalse(any(character in filename for character in '\\/:*?"<>|\r\n'))
            render.assert_called_once_with(original.resolve(), photo, self.server.web_dir / "assets", preview=False)
            render.reset_mock()
            status, headers, body = self.file_response(path + "?preview=1", headers={"Sec-Fetch-Site": "same-origin"})
            self.assertEqual((status, body, headers["Content-Disposition"]), (200, image, "inline"))
            self.assertTrue(render.call_args.kwargs["preview"])
            render.reset_mock()
            status, headers, body = self.file_response(path + "?preview=0", method="HEAD")
            self.assertEqual((status, body, headers["Content-Type"]), (200, b"", "image/jpeg"))
            self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
            self.assertEqual(render.call_args.kwargs, {"preview": False})
        self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), original_digest)
        self.assertEqual(self.request("GET", photo["url"])[1], self.image)
        self.assertEqual(self.server.store.photos()["photos"][0], photo)

    def test_framed_photo_rejects_bad_parameters_cross_origin_and_missing_photos(self):
        photo = self.import_image()[1]["photo"]
        path = "/api/photos/" + photo["id"] + "/framed"
        render = Mock(return_value=self.image)
        with patch.dict(sys.modules, {"photo_export": types.SimpleNamespace(render_framed_photo=render)}):
            for query in ("preview=", "preview=true", "preview=2", "preview=1&preview=0", "preview=1&other=1", "size=full"):
                with self.subTest(query=query):
                    self.assertEqual(self.request("GET", path + "?" + query)[0], 400)
            for headers in ({"Origin": "https://example.invalid"}, {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.request("GET", path, headers=headers)[0], 403)
            self.assertEqual(self.request("GET", "/api/photos/" + "0" * 32 + "/framed")[0], 404)
            self.assertEqual(self.request("GET", "/api/photos/not-a-photo/framed")[0], 404)
            original = self.server.store.originals / (photo["id"] + ".jpg")
            parked = original.with_suffix(".parked")
            original.replace(parked)
            try:
                self.assertEqual(self.request("GET", path)[0], 404)
            finally:
                parked.replace(original)
            real_resolve = Path.resolve

            def redirected_resolve(candidate, *args, **kwargs):
                return self.root / "private.txt" if candidate == original else real_resolve(candidate, *args, **kwargs)

            with patch.object(Path, "resolve", redirected_resolve):
                self.assertEqual(self.request("GET", path)[0], 404)
        render.assert_not_called()
        self.assertEqual(original.read_bytes(), self.image)

    def test_framed_photo_renderer_failure_keeps_data_and_releases_lock(self):
        photo = self.import_image()[1]["photo"]
        path = "/api/photos/" + photo["id"] + "/framed"
        original = self.server.store.originals / (photo["id"] + ".jpg")
        with patch.dict(sys.modules, {"photo_export": types.SimpleNamespace(render_framed_photo=Mock(side_effect=ValueError("injected render failure")))}):
            status, result = self.request("GET", path)
        self.assertEqual(status, 500)
        self.assertIn("error", result)
        self.assertEqual(original.read_bytes(), self.image)
        self.assertEqual(self.server.store.photos()["photos"][0], photo)
        acquired = self.server.store.lock.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:
            self.server.store.lock.release()

    def test_framed_photo_render_completes_before_concurrent_deletion(self):
        photo = self.import_image()[1]["photo"]
        started, finish, deleted = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []

        def render(original, *_args, **_kwargs):
            started.set()
            if not finish.wait(5):
                raise TimeoutError("test renderer was not released")
            return original.read_bytes()

        def download():
            try:
                results.append(self.request("GET", "/api/photos/" + photo["id"] + "/framed"))
            except Exception as error:
                errors.append(error)

        def remove():
            try:
                self.server.store.delete_photo(photo["id"])
            except Exception as error:
                errors.append(error)
            finally:
                deleted.set()

        with patch.dict(sys.modules, {"photo_export": types.SimpleNamespace(render_framed_photo=render)}):
            downloading = threading.Thread(target=download)
            downloading.start()
            deleting = None
            try:
                self.assertTrue(started.wait(3))
                deleting = threading.Thread(target=remove)
                deleting.start()
                self.assertFalse(deleted.wait(0.1))
            finally:
                finish.set()
                downloading.join(5)
                if deleting:
                    deleting.join(5)
        self.assertFalse(errors)
        self.assertEqual(results, [(200, self.image)])
        self.assertTrue(deleted.is_set())
        self.assertEqual(self.server.store.photos()["photos"], [])

    def test_import_preserves_original_deduplicates_and_persists(self):
        status, result = self.import_image(suffix="&place=" + quote("杭州") + "&trip=" + quote("春日江南"))
        self.assertEqual(status, 201)
        photo = result["photo"]
        self.assertFalse(result["duplicate"])
        self.assertEqual(photo["place"], "杭州")
        self.assertEqual(photo["trip"], "春日江南")
        self.assertEqual(photo["taken_at"], "2025-05-03T09:10:11")
        self.assertEqual(photo["date_source"], "file")
        self.assertEqual((photo["width"], photo["height"]), (32, 20))
        status, original = self.request("GET", photo["url"])
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(original).digest(), hashlib.sha256(self.image).digest())
        status, preview = self.request("GET", photo["thumbnail_url"])
        self.assertEqual(status, 200)
        self.assertEqual(Image.open(io.BytesIO(preview)).format, "JPEG")
        status, duplicate = self.import_image(filename="another.jpg")
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(photo["id"], duplicate["photo"]["id"])
        status, listed = self.request("GET", "/api/photos")
        self.assertEqual(len(listed["photos"]), 1)
        self.assertEqual(listed["storage_bytes"], len(self.image))
        reopened = server.AlbumStore(self.root / "data")
        self.assertEqual(reopened.photos()["photos"][0]["id"], photo["id"])

    def test_metadata_journals_export_and_delete(self):
        _, imported = self.import_image()
        photo = imported["photo"]
        status, edited = self.json_request("POST", "/api/photos/" + photo["id"], {"title": "湖边", "caption": "微风", "place": "西湖", "trip": "江南", "favorite": True, "taken_at": "2024-10-01T13:45:00"})
        self.assertEqual(status, 200)
        self.assertTrue(edited["photo"]["favorite"])
        self.assertEqual(edited["photo"]["date_source"], "manual")
        key = quote("trip:江南", safe="")
        status, result = self.json_request("POST", "/api/journals/" + key, {"title": "在江南", "note": "走过湖边。"})
        self.assertEqual(status, 200)
        self.assertEqual(result["journal"]["note"], "走过湖边。")
        status, export = self.request("GET", "/api/export")
        self.assertEqual(export["photos"][0]["title"], "湖边")
        self.assertEqual(export["journals"][0]["key"], "trip:江南")
        self.assertNotIn("sha256", export["photos"][0])
        status, deleted = self.request("DELETE", "/api/photos/" + photo["id"])
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.request("GET", photo["url"])[0], 404)
        self.assertEqual(self.request("GET", photo["thumbnail_url"])[0], 404)
        self.assertEqual(self.request("GET", "/api/photos")[1]["photos"], [])
        self.assertEqual(self.request("GET", "/api/journals")[1]["journals"][0]["note"], "走过湖边。")

    def test_delete_generated_journals_preserves_photos_collections_and_other_notes(self):
        trip = "江南/湖畔"
        first = self.import_image(suffix="&trip=" + quote(trip, safe=""))[1]["photo"]
        buffer = io.BytesIO()
        Image.new("RGB", (32, 20), "navy").save(buffer, "JPEG")
        second = self.import_image(body=buffer.getvalue(), filename="另一张.jpg")[1]["photo"]
        collection = self.create_collection("保留的作品集")
        self.json_request("POST", "/api/collections/" + collection["id"] + "/photos", {"photo_ids": [first["id"], second["id"]]})
        self.json_request("POST", "/api/journals/" + quote("trip:其他旅行", safe=""), {"title": "其他手记", "note": "保留正文"})
        photos_before = self.request("GET", "/api/photos")[1]
        collections_before = self.request("GET", "/api/collections")[1]
        keys = ["trip:" + trip, "month:2025-05"]
        for key in keys:
            path = "/api/journals/" + quote(key, safe="")
            for _ in range(2):
                self.assertEqual(self.request("DELETE", path), (200, {"deleted": key}))
        status, journals = self.request("GET", "/api/journals")
        self.assertEqual(status, 200)
        self.assertEqual(journals["deleted_journal_keys"], sorted(keys))
        self.assertEqual([item["key"] for item in journals["journals"]], ["trip:其他旅行"])
        self.assertEqual(journals["journals"][0]["note"], "保留正文")
        self.assertEqual(self.request("GET", "/api/photos")[1], photos_before)
        self.assertEqual(self.request("GET", "/api/collections")[1], collections_before)
        self.assertEqual(self.request("GET", first["url"]), (200, self.image))
        self.assertEqual(self.request("GET", second["url"]), (200, buffer.getvalue()))
        self.assertEqual(server.AlbumStore(self.server.store.root).journals(), journals)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journal_deletions WHERE deleted_at <> ''").fetchone()[0], 2)

    def test_delete_written_unicode_slash_journal_and_save_recreates_it(self):
        key = "trip:意大利/佛罗伦萨：清晨"
        path = "/api/journals/" + quote(key, safe="")
        self.assertEqual(self.json_request("POST", path, {"title": "旧手记", "note": "旧文字"})[0], 200)
        self.assertEqual(self.request("DELETE", path), (200, {"deleted": key}))
        self.assertEqual(self.request("GET", "/api/journals")[1], {"journals": [], "deleted_journal_keys": [key]})
        self.assertEqual(self.json_request("POST", path, {"title": False})[0], 400)
        self.assertEqual(self.json_request("POST", path, {"unsupported": "不能重建"})[0], 400)
        self.assertEqual(self.request("GET", "/api/journals")[1]["deleted_journal_keys"], [key])
        status, saved = self.json_request("POST", path, {"title": "重新启程", "note": "新的文字"})
        self.assertEqual(status, 200)
        self.assertEqual(saved["journal"]["key"], key)
        self.assertEqual(saved["journal"]["note"], "新的文字")
        persisted = server.AlbumStore(self.server.store.root).journals()
        self.assertEqual(persisted["journals"], [saved["journal"]])
        self.assertEqual(persisted["deleted_journal_keys"], [])

    def test_journal_deletion_rejects_invalid_keys_and_cross_origin_without_changes(self):
        key = "trip:应保留"
        path = "/api/journals/" + quote(key, safe="")
        self.json_request("POST", path, {"title": "不能删除", "note": "原文"})
        before = self.request("GET", "/api/journals")[1]
        for invalid in ("", "trip:", "month:", "journey:江南", "trip:" + "长" * 646):
            invalid_path = "/api/journals/" + quote(invalid, safe="")
            with self.subTest(key=invalid):
                self.assertEqual(self.request("DELETE", invalid_path)[0], 400)
                self.assertEqual(self.json_request("POST", invalid_path, {"note": "不能保存"})[0], 400)
        self.assertEqual(self.request("DELETE", path, headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("DELETE", path, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("GET", "/api/journals")[1], before)
        for invalid in (None, 123, {}):
            with self.subTest(non_string_key=invalid):
                with self.assertRaises(server.ApiError):
                    self.server.store.delete_journal(invalid)

    def test_journal_delete_and_rebuild_roll_back_together_on_database_failure(self):
        key = "trip:原子保存"
        path = "/api/journals/" + quote(key, safe="")
        saved = self.json_request("POST", path, {"title": "保留手记", "note": "事务前文字"})[1]["journal"]
        with self.server.store.connect() as db:
            db.execute("CREATE TRIGGER reject_journal_deletion BEFORE INSERT ON journal_deletions BEGIN SELECT RAISE(ABORT,'injected deletion failure'); END")
        self.assertEqual(self.request("DELETE", path)[0], 500)
        self.assertEqual(self.request("GET", "/api/journals")[1], {"journals": [saved], "deleted_journal_keys": []})
        with self.server.store.connect() as db:
            db.execute("DROP TRIGGER reject_journal_deletion")
        self.assertEqual(self.request("DELETE", path)[0], 200)
        with self.server.store.connect() as db:
            db.execute("CREATE TRIGGER reject_journal_rebuild BEFORE DELETE ON journal_deletions BEGIN SELECT RAISE(ABORT,'injected rebuild failure'); END")
        self.assertEqual(self.json_request("POST", path, {"title": "不应保存", "note": "新文字"})[0], 500)
        self.assertEqual(self.request("GET", "/api/journals")[1], {"journals": [], "deleted_journal_keys": [key]})

    def test_journal_deletions_are_in_json_export_and_complete_migration_snapshot(self):
        deleted = ["trip:江南/春天", "month:2025-05"]
        for key in deleted:
            self.assertEqual(self.request("DELETE", "/api/journals/" + quote(key, safe=""))[0], 200)
        self.json_request("POST", "/api/journals/" + quote("trip:仍在的手记", safe=""), {"note": "随迁移保留"})
        exported = self.request("GET", "/api/export")[1]
        self.assertEqual(exported["deleted_journal_keys"], sorted(deleted))
        status, archive_bytes = self.request("GET", "/api/backup")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            database = self.root / "migrated-journals" / "album.sqlite3"
            database.parent.mkdir()
            database.write_bytes(archive.read("拾光相册/album-data/album.sqlite3"))
        with closing(sqlite3.connect(database)) as snapshot:
            rows = snapshot.execute("SELECT key, deleted_at FROM journal_deletions ORDER BY key").fetchall()
            self.assertEqual([row[0] for row in rows], sorted(deleted))
            self.assertTrue(all(row[1] for row in rows))
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        migrated = server.AlbumStore(database.parent)
        self.assertEqual(migrated.journals(), {name: exported[name] for name in ("journals", "deleted_journal_keys")})

    def test_exif_orientation_date_camera_and_tiff_preview(self):
        photo = Image.new("RGB", (80, 40), "navy")
        exif = Image.Exif()
        exif[274] = 6
        exif[306] = "2020:01:01 00:00:00"
        exif[34665] = {36867: "2023:04:05 06:07:08", 42036: "Sample lens", 33437: 6.3, 33434: 1 / 320, 34855: 500, 37386: 151.0, 37380: -0.33}
        exif[34853] = {1: "N", 2: (30.0, 15.0, 0.0), 3: "E", 4: (120.0, 10.0, 0.0)}
        exif[271] = "Canon"
        exif[272] = "Canon Sample"
        buffer = io.BytesIO()
        photo.save(buffer, "JPEG", exif=exif)
        status, result = self.import_image(body=buffer.getvalue())
        self.assertEqual(status, 201)
        record = result["photo"]
        self.assertEqual(record["taken_at"], "2023-04-05T06:07:08")
        self.assertEqual(record["date_source"], "exif")
        status, unchanged = self.json_request("POST", "/api/photos/" + record["id"], {"taken_at": record["taken_at"], "caption": "Only the caption changed"})
        self.assertEqual(status, 200)
        self.assertEqual(unchanged["photo"]["date_source"], "exif")
        self.assertEqual(record["camera"], "Canon Sample")
        self.assertEqual(record["lens"], "Sample lens")
        self.assertEqual(record["aperture"], "f/6.3")
        self.assertEqual(record["shutter"], "1/320 s")
        self.assertEqual(record["iso"], "500")
        self.assertEqual(record["focal_length"], "151 mm")
        self.assertEqual(record["exposure_bias"], "-0.33 EV")
        self.assertEqual(record["lat"], 30.25)
        self.assertAlmostEqual(record["lon"], 120.1666667)
        self.assertEqual((record["width"], record["height"]), (40, 80))
        preview = self.request("GET", record["thumbnail_url"])[1]
        self.assertEqual(Image.open(io.BytesIO(preview)).size, (40, 80))
        tiff = io.BytesIO()
        photo.save(tiff, "TIFF")
        status, result = self.import_image(body=tiff.getvalue(), filename="film.tiff")
        self.assertEqual(status, 201)
        self.assertTrue(result["photo"]["url"].endswith(".tif"))
        self.assertTrue(result["photo"]["thumbnail_url"].endswith(".jpg"))

    def test_rejects_invalid_images_updates_and_cross_origin(self):
        self.assertEqual(self.import_image(body=b"not an image")[0], 400)
        self.assertEqual(self.request("POST", "/api/import?filename=test.svg", b"<svg/>", {"Content-Type": "image/svg+xml"})[0], 415)
        self.assertEqual(self.request("GET", "/api/photos", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/import?filename=x.jpg", self.image, {"Content-Type": "image/jpeg", "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("DELETE", "/api/photos/" + "a" * 32, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        _, imported = self.import_image()
        path = "/api/photos/" + imported["photo"]["id"]
        self.assertEqual(self.json_request("POST", path, {"taken_at": "bad date"})[0], 400)
        self.assertEqual(self.json_request("POST", path, {"favorite": "yes"})[0], 400)
        self.assertEqual(self.json_request("POST", path, {"url": "/private.txt"})[0], 400)
        self.assertEqual(self.request("POST", path, b"{", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("GET", "/api/photos")[1]["photos"][0]["date_source"], "file")

    def test_size_limit_pillow_requirement_and_health(self):
        status, result = self.request("POST", "/api/import?filename=huge.jpg", b"", {"Content-Type": "image/jpeg", "Content-Length": str(server.MAX_PHOTO_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertIn("100 MB", result["error"])
        with patch.object(server, "Image", None):
            status, result = self.import_image()
        self.assertEqual(status, 503)
        self.assertIn("Pillow", result["error"])
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["app"], "shiguang")
        self.assertEqual(health["project_dir"], str(server.ROOT))
        self.assertEqual(health["data_dir"], str((self.root / "data").resolve()))

    def test_delete_rolls_back_when_preview_is_locked(self):
        _, result = self.import_image()
        photo = result["photo"]
        store = self.server.store
        preview_path = store.previews / (photo["id"] + ".jpg")
        real_replace = Path.replace

        def locked_preview(path, destination):
            if path == preview_path:
                raise PermissionError("injected Windows preview lock")
            return real_replace(path, destination)

        with patch.object(Path, "replace", locked_preview):
            self.assertEqual(self.request("DELETE", "/api/photos/" + photo["id"])[0], 500)
        self.assertEqual(self.request("GET", photo["url"])[1], self.image)
        self.assertEqual(self.request("GET", photo["thumbnail_url"])[0], 200)
        self.assertEqual(self.request("GET", "/api/photos")[1]["photos"][0]["id"], photo["id"])
        self.assertTrue(self.import_image()[1]["duplicate"])
        self.assertEqual(list(store.delete_staging.iterdir()), [])

    def test_delete_rolls_back_on_database_delete_and_commit_failures(self):
        _, result = self.import_image()
        photo = result["photo"]
        store = self.server.store
        real_connect = store.connect
        collections = [self.create_collection("作品集一"), self.create_collection("作品集二")]
        for collection in collections:
            path = "/api/collections/" + collection["id"]
            self.assertEqual(self.json_request("POST", path + "/photos", {"photo_ids": [photo["id"]]})[0], 200)
            self.assertEqual(self.json_request("POST", path, {"cover_photo_id": photo["id"]})[0], 200)
            self.assertEqual(self.json_request("POST", path + "/route", {"stops": [{"id": "arrival", "name": "扬州", "note": "保留手写路线", "photo_id": photo["id"]}]})[0], 200)
        before_collections = self.request("GET", "/api/collections")[1]

        @contextmanager
        def failed_delete():
            with real_connect() as db:
                class DeleteFailure:
                    def execute(self, statement, *arguments):
                        if statement.startswith("DELETE FROM photos"):
                            raise sqlite3.OperationalError("injected delete failure")
                        return db.execute(statement, *arguments)
                yield DeleteFailure()

        @contextmanager
        def failed_commit():
            with real_connect() as db:
                yield db
                # An unsuccessful commit must roll back the DELETE transaction.
                raise sqlite3.OperationalError("injected commit failure")

        for failing_connection in (failed_delete, failed_commit):
            with self.subTest(failure=failing_connection.__name__):
                with patch.object(store, "connect", failing_connection):
                    self.assertEqual(self.request("DELETE", "/api/photos/" + photo["id"])[0], 500)
                self.assertEqual(self.request("GET", photo["url"])[1], self.image)
                self.assertEqual(self.request("GET", photo["thumbnail_url"])[0], 200)
                self.assertEqual(self.request("GET", "/api/photos")[1]["photos"][0]["id"], photo["id"])
                self.assertTrue(self.import_image()[1]["duplicate"])
                self.assertEqual(list(store.delete_staging.iterdir()), [])
                self.assertEqual(self.request("GET", "/api/collections")[1], before_collections)
        self.assertEqual(self.request("DELETE", "/api/photos/" + photo["id"])[0], 200)
        for collection in self.request("GET", "/api/collections")[1]["collections"]:
            self.assertEqual(collection["photo_ids"], [])
            self.assertIsNone(collection["cover_photo_id"])
            self.assertIsNone(collection["cover_url"])
            self.assertEqual(collection["route_stops"][0]["name"], "扬州")
            self.assertEqual(collection["route_stops"][0]["note"], "保留手写路线")
            self.assertIsNone(collection["route_stops"][0]["photo_id"])

    def test_startup_recovers_interrupted_delete_or_finishes_committed_delete(self):
        _, result = self.import_image()
        photo = result["photo"]
        store = self.server.store
        original_path = store.originals / (photo["id"] + ".jpg")
        preview_path = store.previews / (photo["id"] + ".jpg")

        def stage_files():
            staged = store.delete_staging / (photo["id"] + ".jpg-" + "b" * 32)
            staged.mkdir(parents=True)
            original_path.replace(staged / "original")
            preview_path.replace(staged / "preview")
            return staged

        staged = stage_files()
        server.AlbumStore(store.root)
        self.assertEqual(original_path.read_bytes(), self.image)
        self.assertTrue(preview_path.exists())
        self.assertFalse(staged.exists())
        self.assertEqual(self.request("GET", "/api/photos")[1]["photos"][0]["id"], photo["id"])

        staged = stage_files()
        with store.connect() as db:
            db.execute("DELETE FROM photos WHERE id = ?", (photo["id"],))
        server.AlbumStore(store.root)
        self.assertFalse(staged.exists())
        self.assertFalse(original_path.exists())
        self.assertFalse(preview_path.exists())
        self.assertEqual(self.request("GET", "/api/photos")[1]["photos"], [])

    def test_existing_album_additive_metadata_migration(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        with sqlite3.connect(legacy / "album.sqlite3") as db:
            db.execute("""CREATE TABLE photos (
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL, extension TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', caption TEXT NOT NULL DEFAULT '',
                place TEXT NOT NULL DEFAULT '', trip TEXT NOT NULL DEFAULT '',
                taken_at TEXT NOT NULL, date_source TEXT NOT NULL,
                imported_at TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                camera TEXT NOT NULL DEFAULT '', lens TEXT NOT NULL DEFAULT '',
                lat REAL, lon REAL, favorite INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL
            )""")
            values = {
                "id": "c" * 32, "sha256": hashlib.sha256(self.image).hexdigest(),
                "filename": "旧照片.jpg", "extension": ".jpg", "title": "早期相册",
                "caption": "原有说明", "place": "扬州", "trip": "江南",
                "taken_at": "2025-10-04T10:29:32", "date_source": "exif",
                "imported_at": "2026-10-15T10:00:00", "width": 32, "height": 20,
                "camera": "Panasonic DC-S5", "lens": "LUMIX S 70-300/F4.5-5.6",
                "lat": None, "lon": None, "favorite": 1, "size": len(self.image),
            }
            db.execute(f"INSERT INTO photos ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            db.execute("CREATE TABLE journals(key TEXT PRIMARY KEY,title TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO journals VALUES(?,?,?,?)", ("trip:江南", "原有手记", "不得改动的文字", "2026-10-15T10:00:00"))
        db.close()
        (legacy / "originals").mkdir()
        original = legacy / "originals" / (values["id"] + ".jpg")
        original.write_bytes(self.image)
        migrated = server.AlbumStore(legacy)
        photo = migrated.photos()["photos"][0]
        self.assertEqual(photo["title"], "早期相册")
        self.assertEqual(photo["caption"], "原有说明")
        self.assertEqual(photo["date_source"], "exif")
        self.assertEqual(photo["country"], "")
        self.assertEqual(photo["city"], "扬州")
        with migrated.connect() as db:
            old_row = db.execute("SELECT country,city FROM photos WHERE id = ?", (values["id"],)).fetchone()
            self.assertEqual(tuple(old_row), ("", ""))
        self.assertTrue(photo["favorite"])
        for name in server.CAPTURE_FIELDS:
            self.assertIsNone(photo[name])
        self.assertEqual(original.read_bytes(), self.image)
        self.assertEqual(migrated.collections(), {"collections": []})
        self.assertEqual(migrated.atlas(), {"stops": [], "updated_at": None})
        self.assertEqual(migrated.journals()["journals"][0]["note"], "不得改动的文字")
        self.assertEqual(migrated.journals()["deleted_journal_keys"], [])
        with migrated.connect() as db:
            self.assertEqual([row["name"] for row in db.execute("PRAGMA table_info(journal_deletions)")], ["key", "deleted_at"])
        new_collection = migrated.create_collection({"title": "新作品集"})["collection"]
        self.assertEqual(new_collection["route_stops"], [])
        self.assertIsNone(new_collection["route_updated_at"])
        migrated.add_collection_photos(new_collection["id"], {"photo_ids": [values["id"]]})
        # A second startup must not add duplicate columns or alter existing rows.
        self.assertEqual(server.AlbumStore(legacy).photos(), migrated.photos())
        self.assertEqual(server.AlbumStore(legacy).collections(), migrated.collections())

    def test_capture_settings_missing_and_long_exposure(self):
        self.assertEqual(server.capture_settings({}, {}), dict.fromkeys(server.CAPTURE_FIELDS))
        settings = server.capture_settings({}, {33434: 2.5, 33437: 8, 34855: (100,), 37380: 0.33})
        self.assertEqual(settings["shutter"], "2.5 s")
        self.assertEqual(settings["aperture"], "f/8")
        self.assertEqual(settings["iso"], "100")
        self.assertEqual(settings["exposure_bias"], "+0.33 EV")

    def test_rgb_color_profile_survives_preview_and_original(self):
        try:
            from PIL import ImageCms
        except ImportError:
            self.skipTest("Pillow color management is unavailable")
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        source = io.BytesIO()
        Image.new("RGB", (80, 60), "#5c8f73").save(source, "JPEG", icc_profile=profile)
        image = source.getvalue()
        status, result = self.import_image(body=image, filename="color-profile.jpg")
        self.assertEqual(status, 201)
        photo = result["photo"]
        original = self.request("GET", photo["url"])[1]
        self.assertEqual(original, image)
        with Image.open(io.BytesIO(original)) as opened:
            self.assertEqual(opened.info["icc_profile"], profile)
        preview = self.request("GET", photo["thumbnail_url"])[1]
        with Image.open(io.BytesIO(preview)) as opened:
            self.assertEqual(opened.mode, "RGB")
            self.assertEqual(opened.info["icc_profile"], profile)

    def test_camera_mpo_jpg_preserves_all_frames_metadata_profile_and_backup(self):
        from PIL import ImageCms
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        first = Image.new("RGB", (80, 60), "#cc3300")
        second = Image.new("RGB", (80, 60), "#0033cc")
        exif = Image.Exif()
        exif[271], exif[272], exif[274] = "Panasonic", "DC-S5", 6
        exif[34665] = {36867: "2025:07:08 09:10:11", 42036: "LUMIX sample lens", 33437: 8.0}
        buffer = io.BytesIO()
        first.save(buffer, "MPO", save_all=True, append_images=[second], exif=exif, icc_profile=profile, quality=95)
        original = buffer.getvalue()
        digest = hashlib.sha256(original).digest()
        with Image.open(io.BytesIO(original)) as source:
            self.assertEqual((source.format, source.n_frames), ("MPO", 2))
        status, imported = self.import_image(body=original, filename="P1000364.JPG")
        self.assertEqual(status, 201)
        photo = imported["photo"]
        self.assertTrue(photo["url"].endswith(".jpg"))
        self.assertEqual((photo["width"], photo["height"]), (60, 80))
        self.assertEqual(photo["camera"], "Panasonic DC-S5")
        self.assertEqual(photo["lens"], "LUMIX sample lens")
        self.assertEqual(photo["aperture"], "f/8")
        self.assertEqual((photo["taken_at"], photo["date_source"]), ("2025-07-08T09:10:11", "exif"))
        downloaded = self.request("GET", photo["url"])[1]
        self.assertEqual(hashlib.sha256(downloaded).digest(), digest)
        with Image.open(io.BytesIO(downloaded)) as source:
            self.assertEqual(source.n_frames, 2)
            self.assertEqual(source.getexif()[272], "DC-S5")
            self.assertEqual(source.info["icc_profile"], profile)
        preview = self.request("GET", photo["thumbnail_url"])[1]
        with Image.open(io.BytesIO(preview)) as opened:
            self.assertEqual((opened.format, opened.size), ("JPEG", (60, 80)))
            self.assertEqual(getattr(opened, "n_frames", 1), 1)
            self.assertEqual(opened.info["icc_profile"], profile)
            red, green, blue = opened.convert("RGB").getpixel((30, 40))
            self.assertGreater(red, 150)
            self.assertLess(blue, 30)
        status, duplicate = self.import_image(body=original, filename="same-camera-photo.JPG")
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["photo"]["id"], photo["id"])
        self.assertEqual(len(self.server.store.photos()["photos"]), 1)
        status, archive_bytes = self.request("GET", "/api/backup")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            restored = archive.read("拾光相册/album-data/originals/" + photo["id"] + ".jpg")
            self.assertEqual(hashlib.sha256(restored).digest(), digest)
            with Image.open(io.BytesIO(restored)) as source:
                self.assertEqual(source.n_frames, 2)
                self.assertEqual(source.info["icc_profile"], profile)

    def test_empty_collection_creation_rename_persistence_and_export(self):
        collection = self.create_collection("扬州习作", description="秋天的记录", place="扬州")
        self.assertEqual(collection["photo_ids"], [])
        self.assertEqual(collection["photo_count"], 0)
        self.assertIsNone(collection["cover_photo_id"])
        self.assertIsNone(collection["cover_url"])
        path = "/api/collections/" + collection["id"]
        status, edited = self.json_request("POST", path, {"title": "运河边", "description": "更新后的文字", "place": "江南"})
        self.assertEqual(status, 200)
        self.assertEqual(edited["collection"]["title"], "运河边")
        self.assertEqual(edited["collection"]["description"], "更新后的文字")
        self.assertEqual(self.json_request("POST", path, {"title": "不应保存", "cover_photo_id": "f" * 32})[0], 400)
        persisted = server.AlbumStore(self.server.store.root).collections()["collections"][0]
        self.assertEqual(persisted["title"], "运河边")
        self.assertEqual(persisted["photo_ids"], [])
        self.assertEqual(self.request("GET", "/api/export")[1]["collections"][0], persisted)
        self.assertEqual(self.json_request("POST", "/api/collections", {"title": " "})[0], 400)
        self.assertEqual(self.request("GET", "/api/photos")[1]["photos"], [])

    def test_import_to_collection_and_duplicate_attaches_without_metadata_change(self):
        first, second = self.create_collection("扬州"), self.create_collection("精选")
        status, result = self.import_image(suffix="&collection_id=" + first["id"] + "&place=" + quote("扬州") + "&trip=" + quote("秋游"))
        self.assertEqual(status, 201)
        photo = result["photo"]
        self.assertEqual(result["collection"]["photo_ids"], [photo["id"]])
        _, edited = self.json_request("POST", "/api/photos/" + photo["id"], {"caption": "保留的说明", "favorite": True})
        status, duplicate = self.import_image(filename="不同名称.jpg", suffix="&collection_id=" + second["id"] + "&place=" + quote("错误地点") + "&trip=" + quote("不同旅行"))
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["photo"], edited["photo"])
        self.assertEqual(duplicate["collection"]["photo_ids"], [photo["id"]])
        listed = self.request("GET", "/api/collections")[1]["collections"]
        self.assertEqual(len(listed), 2)
        self.assertTrue(all(row["photo_ids"] == [photo["id"]] for row in listed))
        self.assertEqual(len(list(self.server.store.originals.iterdir())), 1)
        other = io.BytesIO()
        Image.new("RGB", (20, 30), "blue").save(other, "JPEG")
        with patch.object(Path, "write_bytes", side_effect=AssertionError("An invalid collection must not write any photo files")):
            self.assertEqual(self.import_image(body=other.getvalue(), suffix="&collection_id=" + "f" * 32)[0], 404)
            self.assertEqual(self.import_image(suffix="&collection_id=")[0], 404)
        self.assertEqual(len(self.request("GET", "/api/photos")[1]["photos"]), 1)

    def test_collection_membership_validation_covers_removal_and_safe_delete(self):
        first, second = self.create_collection("旅行"), self.create_collection("珍藏")
        photo1 = self.import_image()[1]["photo"]
        other = io.BytesIO()
        Image.new("RGB", (40, 30), "red").save(other, "JPEG")
        photo2 = self.import_image(body=other.getvalue(), filename="第二张.jpg")[1]["photo"]
        path = "/api/collections/" + first["id"]
        status, added = self.json_request("POST", path + "/photos", {"photo_ids": [photo1["id"], photo1["id"], photo2["id"]]})
        self.assertEqual(status, 200)
        self.assertEqual(added["added_count"], 2)
        self.assertEqual(added["collection"]["photo_ids"], [photo1["id"], photo2["id"]])
        self.assertEqual(added["collection"]["cover_url"], photo1["thumbnail_url"])
        self.assertEqual(self.json_request("POST", path + "/photos", {"photo_ids": [photo1["id"]]})[1]["added_count"], 0)
        status, cover = self.json_request("POST", path, {"cover_photo_id": photo2["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(cover["collection"]["cover_url"], photo2["thumbnail_url"])
        second_path = "/api/collections/" + second["id"]
        self.json_request("POST", second_path + "/photos", {"photo_ids": [photo1["id"]]})
        self.assertEqual(self.json_request("POST", second_path, {"cover_photo_id": photo2["id"]})[0], 400)
        self.assertEqual(self.json_request("POST", second_path + "/photos", {"photo_ids": [photo2["id"], "f" * 32]})[0], 404)
        self.assertEqual(self.json_request("POST", second_path + "/photos", {"photo_ids": [photo1["id"]] * 5001})[0], 400)
        self.assertEqual(self.json_request("POST", second_path + "/photos", {"photo_ids": "bad"})[0], 400)
        self.assertEqual(self.json_request("POST", path + "/route", {"stops": [{"id": "memory", "name": "大运河", "note": "路过时拍下的倒影", "photo_id": photo2["id"]}]})[0], 200)
        status, removed = self.json_request("DELETE", path + "/photos", {"photo_ids": [photo2["id"]]})
        self.assertEqual(status, 200)
        self.assertEqual(removed["removed_count"], 1)
        self.assertIsNone(removed["collection"]["cover_photo_id"])
        self.assertEqual(removed["collection"]["cover_url"], photo1["thumbnail_url"])
        self.assertEqual(removed["collection"]["route_stops"], [{"id": "memory", "name": "大运河", "note": "路过时拍下的倒影", "photo_id": None}])
        self.assertEqual(self.request("GET", photo2["url"])[1], other.getvalue())
        self.json_request("POST", "/api/photos/" + photo1["id"], {"caption": "照片说明必须保留"})
        self.assertEqual(self.request("DELETE", path)[0], 200)
        self.assertEqual(self.request("GET", path + "/route")[0], 404)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collection_routes WHERE collection_id = ?", (first["id"],)).fetchone()[0], 0)
        self.assertEqual(self.request("GET", photo1["url"])[1], self.image)
        remaining = self.request("GET", "/api/collections")[1]["collections"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["photo_ids"], [photo1["id"]])
        listed_photos = self.request("GET", "/api/photos")[1]["photos"]
        self.assertEqual(len(listed_photos), 2)
        self.assertEqual(next(row for row in listed_photos if row["id"] == photo1["id"])["caption"], "照片说明必须保留")

    def test_membership_batch_rolls_back_after_mid_batch_database_failure(self):
        collection = self.create_collection("失败时保持为空")
        first = self.import_image()[1]["photo"]
        other = io.BytesIO()
        Image.new("RGB", (50, 20), "yellow").save(other, "JPEG")
        second = self.import_image(body=other.getvalue())[1]["photo"]
        store = self.server.store
        real_connect = store.connect

        @contextmanager
        def failing_batch():
            with real_connect() as db:
                class MembershipFailure:
                    inserted = 0

                    def execute(self, statement, *arguments):
                        if statement.startswith("INSERT OR IGNORE INTO collection_photos"):
                            self.inserted += 1
                            if self.inserted == 2:
                                raise sqlite3.OperationalError("injected second-membership failure")
                        return db.execute(statement, *arguments)
                yield MembershipFailure()

        with patch.object(store, "connect", failing_batch):
            self.assertEqual(self.json_request("POST", "/api/collections/" + collection["id"] + "/photos", {"photo_ids": [first["id"], second["id"]]})[0], 500)
        self.assertEqual(self.request("GET", "/api/collections")[1]["collections"][0]["photo_ids"], [])
        self.assertEqual(len(self.request("GET", "/api/photos")[1]["photos"]), 2)

    def test_manual_route_saves_reorders_clears_and_exports_without_coordinates(self):
        collection = self.create_collection("手绘扬州游记")
        path = "/api/collections/" + collection["id"] + "/route"
        status, empty = self.request("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(empty["route"]["stops"], [])
        self.assertIsNone(empty["route"]["updated_at"])
        photo = self.import_image(suffix="&collection_id=" + collection["id"])[1]["photo"]
        status, saved = self.json_request("POST", path, {"stops": [{"id": "city-arrival", "name": "扬州", "note": "上午抵达", "photo_id": photo["id"]}, {"name": "瘦西湖"}]})
        self.assertEqual(status, 200)
        stops = saved["route"]["stops"]
        self.assertEqual(stops[0]["id"], "city-arrival")
        self.assertEqual(stops[1]["name"], "瘦西湖")
        self.assertEqual(stops[1]["note"], "")
        self.assertIsNone(stops[1]["photo_id"])
        self.assertEqual(len(stops[1]["id"]), 32)
        self.assertEqual(saved["collection"]["route_stops"], stops)
        self.assertTrue(all(set(stop) == {"id", "name", "note", "photo_id"} for stop in stops))
        reordered = list(reversed(stops))
        reordered[0]["note"] = "下午散步"
        status, edited = self.json_request("POST", path, {"stops": reordered})
        self.assertEqual(status, 200)
        self.assertEqual(edited["route"]["stops"], reordered)
        reopened = server.AlbumStore(self.server.store.root)
        self.assertEqual(reopened.get_route(collection["id"])["route"]["stops"], reordered)
        self.assertEqual(self.request("GET", "/api/export")[1]["collections"][0]["route_stops"], reordered)
        status, cleared = self.json_request("POST", path, {"stops": []})
        self.assertEqual(status, 200)
        self.assertEqual(cleared["route"]["stops"], [])
        self.assertIsNotNone(cleared["route"]["updated_at"])
        self.assertEqual(self.request("GET", photo["url"])[1], self.image)

    def test_route_validation_is_atomic_and_limited_to_collection_photos(self):
        collection = self.create_collection("手动路线")
        path = "/api/collections/" + collection["id"] + "/route"
        original = [{"id": "original", "name": "原有站点", "note": "保持原有文字"}]
        self.assertEqual(self.json_request("POST", path, {"stops": original})[0], 200)
        before = self.request("GET", path)[1]
        invalid_routes = [
            {"stops": [{"name": str(i)} for i in range(31)]},
            {"stops": [{"name": "站" * 101}]},
            {"stops": [{"name": " "}]},
            {"stops": [{"name": "扬州", "note": "记" * 501}]},
            {"stops": [{"id": "same", "name": "一"}, {"id": "same", "name": "二"}]},
            {"stops": [{"id": "../bad", "name": "扬州"}]},
            {"stops": [{"id": False, "name": "扬州"}]},
            {"stops": [{"name": "扬州", "lat": 32.4, "lon": 119.4}]},
            {"stops": [{"name": "扬州", "photo_id": "f" * 32}]},
        ]
        outside = self.import_image()[1]["photo"]
        invalid_routes.append({"stops": [{"name": "不属于作品集的照片", "photo_id": outside["id"]}]})
        for payload in invalid_routes:
            with self.subTest(payload=payload):
                self.assertEqual(self.json_request("POST", path, payload)[0], 400)
                self.assertEqual(self.request("GET", path)[1], before)
        self.assertEqual(self.json_request("POST", "/api/collections/" + "f" * 32 + "/route", {"stops": []})[0], 404)
        status, maximum = self.json_request("POST", path, {"stops": [{"name": str(i)} for i in range(30)]})
        self.assertEqual(status, 200)
        self.assertEqual(len(maximum["route"]["stops"]), 30)

    def test_route_database_commit_failure_preserves_previous_route(self):
        collection = self.create_collection("事务路线")
        path = "/api/collections/" + collection["id"] + "/route"
        self.json_request("POST", path, {"stops": [{"id": "stay", "name": "保留站点"}]})
        before = self.request("GET", "/api/collections")[1]
        real_connect = self.server.store.connect

        @contextmanager
        def failed_commit():
            with real_connect() as db:
                yield db
                raise sqlite3.OperationalError("injected route commit failure")

        with patch.object(self.server.store, "connect", failed_commit):
            self.assertEqual(self.json_request("POST", path, {"stops": [{"name": "不应保存的新站点"}]})[0], 500)
        self.assertEqual(self.request("GET", "/api/collections")[1], before)

    def test_global_atlas_order_persistence_export_and_independence_from_photos(self):
        self.assertEqual(self.request("GET", "/api/atlas"), (200, {"stops": [], "updated_at": None}))
        collection = self.create_collection("扬州摄影")
        photo = self.import_image(suffix="&collection_id=" + collection["id"] + "&place=" + quote("扬州"))[1]["photo"]
        status, atlas = self.json_request("POST", "/api/atlas", {"stops": [{"id": "yangzhou", "name": "扬州", "lat": 32.39, "lon": 119.42, "note": "清晨的运河", "date": "2025-10-04", "photo_place": "扬州"}, {"name": "伦敦", "lat": 51.5, "lon": -0.12, "date": None, "photo_place": None}]})
        self.assertEqual(status, 200)
        self.assertIsNotNone(atlas["updated_at"])
        self.assertEqual(len(atlas["stops"]), 2)
        self.assertEqual(atlas["stops"][0]["id"], "yangzhou")
        self.assertEqual(len(atlas["stops"][1]["id"]), 32)
        self.assertEqual(atlas["stops"][1]["date"], "")
        self.assertEqual(atlas["stops"][1]["photo_place"], "")
        reordered = list(reversed(atlas["stops"]))
        reordered[0]["note"] = "再次访问时补上的笔记"
        status, saved = self.json_request("POST", "/api/atlas", {"stops": reordered})
        self.assertEqual(status, 200)
        self.assertEqual(saved["stops"], reordered)
        self.assertEqual(server.AlbumStore(self.server.store.root).atlas(), saved)
        self.assertEqual(self.request("GET", "/api/export")[1]["atlas"], saved)
        self.assertEqual(self.request("DELETE", "/api/photos/" + photo["id"])[0], 200)
        self.assertEqual(self.request("DELETE", "/api/collections/" + collection["id"])[0], 200)
        self.assertEqual(self.request("GET", "/api/atlas")[1], saved)
        status, cleared = self.json_request("POST", "/api/atlas", {"stops": []})
        self.assertEqual(status, 200)
        self.assertEqual(cleared["stops"], [])
        self.assertIsNotNone(cleared["updated_at"])

    def test_atlas_rejects_invalid_geo_dates_and_duplicate_ids_without_overwriting(self):
        valid = {"id": "existing", "name": "已有地点", "lat": 0, "lon": 0, "note": "原有笔记"}
        self.assertEqual(self.json_request("POST", "/api/atlas", {"stops": [valid]})[0], 200)
        before = self.request("GET", "/api/atlas")[1]
        invalid_stops = [
            {"lat": 90.01}, {"lat": -90.01}, {"lon": 180.01}, {"lon": -180.01},
            {"lat": float("nan")}, {"lon": float("inf")}, {"lon": float("-inf")},
            {"lat": True}, {"lon": "119.42"}, {"lat": None},
            {"name": " "}, {"name": "站" * 101}, {"note": "记" * 501},
            {"date": "2025-02-29"}, {"date": "2024-2-03"}, {"date": "2025-10-04T12:00:00"},
            {"date": False}, {"photo_place": "地" * 301}, {"photo_place": False},
            {"id": "bad/id"}, {"id": "a" * 65}, {"id": 0}, {"photo_id": "f" * 32},
        ]
        for changed in invalid_stops:
            payload = {**valid, **changed}
            with self.subTest(changed=changed):
                self.assertEqual(self.json_request("POST", "/api/atlas", {"stops": [payload]})[0], 400)
                self.assertEqual(self.request("GET", "/api/atlas")[1], before)
        self.assertEqual(self.json_request("POST", "/api/atlas", {"stops": [valid, valid]})[0], 400)
        self.assertEqual(self.json_request("POST", "/api/atlas", {"stops": [{"name": str(i), "lat": 0, "lon": 0} for i in range(201)]})[0], 400)
        self.assertEqual(self.request("GET", "/api/atlas")[1], before)
        status, maximum = self.json_request("POST", "/api/atlas", {"stops": [{"name": (str(i) + "地" * 100)[:100], "lat": 90 if i % 2 else -90, "lon": 180 if i % 2 else -180, "date": "2024-02-29", "note": "记" * 500, "photo_place": "地" * 300} for i in range(200)]})
        self.assertEqual(status, 200)
        self.assertEqual(len(maximum["stops"]), 200)

    def test_atlas_commit_failure_keeps_original_manual_footprints(self):
        self.json_request("POST", "/api/atlas", {"stops": [{"id": "stay", "name": "保留地点", "lat": 32, "lon": 119}]})
        before = self.request("GET", "/api/atlas")[1]
        real_connect = self.server.store.connect

        @contextmanager
        def failed_commit():
            with real_connect() as db:
                yield db
                raise sqlite3.OperationalError("injected atlas commit failure")

        with patch.object(self.server.store, "connect", failed_commit):
            self.assertEqual(self.json_request("POST", "/api/atlas", {"stops": []})[0], 500)
        self.assertEqual(self.request("GET", "/api/atlas")[1], before)

    def test_photo_country_city_import_edit_legacy_place_and_export(self):
        status, result = self.import_image(suffix="&country=CN&city=" + quote("扬州") + "&place=" + quote("旧地点"))
        self.assertEqual(status, 201)
        photo = result["photo"]
        self.assertEqual((photo["country"], photo["city"], photo["place"]), ("CN", "扬州", "扬州"))
        path = "/api/photos/" + photo["id"]
        status, edited = self.json_request("POST", path, {"country": "FR", "city": "巴黎", "place": "旧名称"})
        self.assertEqual(status, 200)
        self.assertEqual((edited["photo"]["country"], edited["photo"]["city"], edited["photo"]["place"]), ("FR", "巴黎", "巴黎"))
        status, duplicate = self.import_image(suffix="&country=GB&city=London")
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["photo"], edited["photo"])
        self.json_request("POST", path, {"country": "CN", "city": "上海"})
        status, legacy = self.json_request("POST", path, {"place": "南京"})
        self.assertEqual(status, 200)
        self.assertEqual((legacy["photo"]["city"], legacy["photo"]["place"]), ("南京", "南京"))
        self.assertEqual(legacy["photo"]["date_source"], "file")
        exported = self.request("GET", "/api/export")[1]["photos"][0]
        self.assertEqual((exported["country"], exported["city"]), ("CN", "南京"))
        self.assertEqual(self.request("GET", photo["url"])[1], self.image)
        status, cleared = self.json_request("POST", path, {"country": "", "city": ""})
        self.assertEqual(status, 200)
        self.assertEqual((cleared["photo"]["country"], cleared["photo"]["city"], cleared["photo"]["place"]), ("", "", ""))

    def test_photo_country_city_validation_before_import_and_atomic_edit(self):
        invalid_values = [{"country": "cn"}, {"country": "CHN"}, {"country": "C1"}, {"country": False}, {"city": "市" * 101}, {"city": 123}]
        with patch.object(Path, "write_bytes", side_effect=AssertionError("Invalid location fields must not write files")):
            self.assertEqual(self.import_image(suffix="&country=cn")[0], 400)
            self.assertEqual(self.import_image(suffix="&city=" + quote("市" * 101))[0], 400)
        self.assertEqual(self.request("GET", "/api/photos")[1]["photos"], [])
        photo = self.import_image(suffix="&place=" + quote("扬州"))[1]["photo"]
        self.assertEqual(photo["city"], "扬州")
        with self.server.store.connect() as db:
            self.assertEqual(db.execute("SELECT city FROM photos WHERE id = ?", (photo["id"],)).fetchone()[0], "")
        path = "/api/photos/" + photo["id"]
        for fields in invalid_values:
            with self.subTest(fields=fields):
                self.assertEqual(self.json_request("POST", path, {"caption": "不得部分保存", **fields})[0], 400)
                self.assertEqual(self.request("GET", "/api/photos")[1]["photos"][0], photo)

    def test_atlas_country_city_fields_legacy_defaults_and_validation(self):
        old_stops = [{"id": "old", "name": "扬州", "lat": 32.39, "lon": 119.42, "note": "原有记录", "date": "2025-10-04", "photo_place": "扬州"}]
        raw = json.dumps(old_stops, ensure_ascii=False)
        with self.server.store.connect() as db:
            db.execute("INSERT INTO atlas_route VALUES('default',?,?)", (raw, "2026-10-15T10:00:00"))
        legacy = server.AlbumStore(self.server.store.root).atlas()
        self.assertEqual(legacy["stops"][0]["country"], "")
        self.assertEqual(legacy["stops"][0]["city"], "")
        self.assertEqual(legacy["stops"][0]["note"], "原有记录")
        with self.server.store.connect() as db:
            self.assertEqual(db.execute("SELECT stops_json FROM atlas_route WHERE key = 'default'").fetchone()[0], raw)
        updated = {**old_stops[0], "country": "CN", "city": "扬州"}
        status, atlas = self.json_request("POST", "/api/atlas", {"stops": [updated, {"id": "london", "name": "英国", "lat": 51.5, "lon": -0.12, "country": "GB", "city": "London"}]})
        self.assertEqual(status, 200)
        self.assertEqual([(stop["country"], stop["city"]) for stop in atlas["stops"]], [("CN", "扬州"), ("GB", "London")])
        self.assertEqual(self.request("GET", "/api/export")[1]["atlas"], atlas)
        for invalid in ({"country": "cn"}, {"country": "China"}, {"country": "C1"}, {"country": 12}, {"city": "城" * 101}, {"city": False}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.json_request("POST", "/api/atlas", {"stops": [{**updated, **invalid}]})[0], 400)
                self.assertEqual(self.request("GET", "/api/atlas")[1], atlas)

    def test_opening_line_management_persists_exports_and_migrates(self):
        status, original = self.request('GET', '/api/opening-lines')
        self.assertEqual(status, 200); self.assertEqual(len(original['lines']), 100)
        builtin = original['lines'][0]['id']
        text = '在自己的时区里，慢慢发光。<script>珍惜今天</script>'
        status, added = self.json_request('POST', '/api/opening-lines', {'text': '  '+text+'  '})
        self.assertEqual(status, 201); self.assertEqual(len(added['lines']), 101)
        custom = added['lines'][-1]
        self.assertEqual(custom['origin'], 'custom'); self.assertEqual(custom['text'], text)
        self.assertEqual(self.json_request('POST', '/api/opening-lines', {'text': text})[0], 409)
        self.assertEqual(self.request('DELETE', '/api/opening-lines/'+builtin)[0], 200)
        current = self.server.store.opening_lines()
        self.assertEqual(len(current['lines']), 100)
        self.assertNotIn(builtin, [line['id'] for line in current['lines']])
        self.assertEqual(server.AlbumStore(self.server.store.root).opening_lines(), current)
        status, headers, body = self.file_response('/daily-quotes.js')
        self.assertEqual(status, 200); self.assertIn('application/javascript', headers['Content-Type'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        prefix = 'window.ShiguangDailyQuotes=Object.freeze('
        script = body.decode('utf-8'); self.assertTrue(script.startswith(prefix))
        self.assertEqual(json.loads(script[len(prefix):-2]), [line['text'] for line in current['lines']])
        self.assertEqual(self.request('GET', '/api/export')[1]['opening_lines'], current['lines'])
        with self.server.store.backup_archive(self.application_file) as path:
            with zipfile.ZipFile(path) as archive:
                directory = self.root/'migrated-opening'; directory.mkdir()
                (directory/'album.sqlite3').write_bytes(archive.read('拾光相册/album-data/album.sqlite3'))
        self.assertEqual(server.AlbumStore(directory).opening_lines(), current)
        self.assertEqual(self.request('DELETE', '/api/opening-lines/'+custom['id'])[0], 200)
        self.assertEqual(self.request('DELETE', '/api/opening-lines/'+custom['id'])[0], 404)
        with self.server.store.connect() as db:
            self.server.store.save_opening_lines(db, [])
        self.assertEqual(server.AlbumStore(self.server.store.root).opening_lines(), {'lines': []})
        self.assertEqual(self.file_response('/daily-quotes.js')[2], b'window.ShiguangDailyQuotes=Object.freeze([]);')

    def test_opening_line_validation_security_and_failed_commit_are_atomic(self):
        original = self.server.store.opening_lines()
        for value in ({}, {'text': ''}, {'text': '   '}, {'text': '\0'}, {'text': 123}, {'text': True}, {'text': '字'*121}, {'text': '句子', 'id': 'other'}):
            with self.subTest(value=value):
                self.assertEqual(self.json_request('POST', '/api/opening-lines', value)[0], 400)
                self.assertEqual(self.server.store.opening_lines(), original)
        for path in ('/api/opening-lines', '/daily-quotes.js'):
            self.assertEqual(self.request('GET', path, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('POST', '/api/opening-lines', json.dumps({'text': '不应写入'}).encode(), headers={'Origin': 'https://outside.example', 'Content-Type': 'application/json'})[0], 403)
        self.assertEqual(self.request('DELETE', '/api/opening-lines/'+original['lines'][0]['id'], headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('GET', '/api/opening-lines?unknown=1')[0], 400)
        real_connect = self.server.store.connect
        @contextmanager
        def failed_commit():
            with real_connect() as db:
                yield db
                raise sqlite3.OperationalError('injected opening-line commit failure')
        with patch.object(self.server.store, 'connect', failed_commit):
            self.assertEqual(self.json_request('POST', '/api/opening-lines', {'text': '不应留下半次修改'})[0], 500)
        self.assertEqual(self.server.store.opening_lines(), original)

    def test_settings_persist_partial_updates_validate_and_roll_back(self):
        self.assertEqual(self.request("GET", "/api/settings"), (200, {"theme": "classic", "music_volume": 0.22}))
        status, stored = self.json_request("POST", "/api/settings", {"theme": "italian", "music_volume": 0.35})
        self.assertEqual(status, 200)
        self.assertEqual(stored, {"theme": "italian", "music_volume": 0.35})
        self.assertEqual(server.AlbumStore(self.server.store.root).settings(), stored)
        for payload in ({"theme": "other", "music_volume": 0.5}, {"music_volume": True}, {"music_volume": -0.1}, {"music_volume": 1.1}, {"music_volume": float("nan")}, {"music_volume": float("inf")}, {"music_volume": "0.2"}, {"theme": "classic", "unknown": True}):
            with self.subTest(payload=payload):
                self.assertEqual(self.json_request("POST", "/api/settings", payload)[0], 400)
                self.assertEqual(self.request("GET", "/api/settings")[1], stored)
        real_connect = self.server.store.connect

        @contextmanager
        def failed_commit():
            with real_connect() as db:
                yield db
                raise sqlite3.OperationalError("injected settings commit failure")

        with patch.object(self.server.store, "connect", failed_commit):
            self.assertEqual(self.json_request("POST", "/api/settings", {"theme": "classic"})[0], 500)
        self.assertEqual(self.request("GET", "/api/settings")[1], stored)
        self.assertEqual(self.json_request("POST", "/api/settings", {"music_volume": 0})[1], {"theme": "italian", "music_volume": 0})
        self.assertEqual(self.request("GET", "/api/export")[1]["settings"], {"theme": "italian", "music_volume": 0})

    def test_music_import_dedupe_rename_disable_remove_and_restore(self):
        data = self.wav_audio()
        status, imported = self.import_audio(data, "午后.wav")
        self.assertEqual(status, 201)
        self.assertFalse(imported["duplicate"])
        track = imported["track"]
        self.assertEqual((track["title"], track["origin"], track["original_name"], track["bytes"]), ("午后", "imported", "午后.wav", len(data)))
        self.assertTrue(track["enabled"])
        self.assertEqual(self.request("GET", track["url"])[1], data)
        path = "/api/music/" + track["id"]
        status, edited = self.json_request("POST", path, {"title": "我的午后音乐", "enabled": False})
        self.assertEqual(status, 200)
        status, duplicate = self.import_audio(data, "不同名称.wav")
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["track"], edited["track"])
        self.assertEqual(len(list(self.server.store.music_dir.iterdir())), 1)
        self.assertEqual(self.request("GET", track["url"])[0], 200)
        reopened = server.AlbumStore(self.server.store.root, self.server.web_dir)
        self.assertEqual(next(value for value in reopened.music()["tracks"] if value["id"] == track["id"]), edited["track"])
        self.assertEqual(self.request("GET", "/api/export")[1]["music"], self.request("GET", "/api/music")[1])
        status, removed = self.request("POST", path + "/remove")
        self.assertEqual(status, 200)
        self.assertNotIn(track["id"], [value["id"] for value in removed["tracks"]])
        self.assertEqual(self.request("GET", track["url"])[0], 404)
        self.assertTrue((self.server.store.music_dir / (track["id"] + ".wav")).is_file())
        status, restored = self.import_audio(data)
        self.assertEqual(status, 200)
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["track"]["id"], track["id"])
        self.assertEqual(restored["track"]["title"], "我的午后音乐")
        self.assertTrue(restored["track"]["enabled"])

    def test_builtin_music_preferences_persist_and_builtin_cannot_be_removed(self):
        tracks = self.request("GET", "/api/music")[1]["tracks"]
        self.assertEqual(len(tracks), 3)
        self.assertTrue(all(track["origin"] == "builtin" and track["enabled"] for track in tracks))
        original = tracks[0]
        status, edited = self.json_request("POST", "/api/music/hymn", {"title": "安静的赞歌", "enabled": False})
        self.assertEqual(status, 200)
        self.assertFalse(edited["track"]["enabled"])
        self.assertEqual(edited["track"]["source"], original["source"])
        self.assertEqual(edited["track"]["license"], "CC BY 4.0")
        reopened = server.AlbumStore(self.server.store.root, self.server.web_dir)
        self.assertEqual(reopened.music()["tracks"][0], edited["track"])
        self.assertEqual(self.request("POST", "/api/music/hymn/remove")[0], 400)
        status, duplicate = self.import_audio(self.builtin_audio["hymn"], "原有内置.mp3")
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["track"], edited["track"])
        self.assertFalse(self.server.store.music_dir.exists())
        for payload in ({"title": ""}, {"title": "名" * 121}, {"enabled": "yes"}, {"artist": "不得改作者"}):
            self.assertEqual(self.json_request("POST", "/api/music/hymn", payload)[0], 400)
        self.assertEqual(self.request("GET", "/api/music")[1]["tracks"][0], edited["track"])

    def test_music_supported_headers_reject_fake_empty_traversal_and_oversized(self):
        def box(kind, body):
            return (len(body) + 8).to_bytes(4, "big") + kind + body
        ogg = bytearray(27)
        ogg[:5], ogg[26] = b"OggS\0", 1
        fixtures = {
            "sample.wav": self.wav_audio(),
            "sample.mp3": b"\xff\xfb\x90\x00" + b"\x03" * 413,
            "sample.aac": b"\xff\xf1\x50\x80\x01\x3f\xfc\0\0",
            "sample.ogg": bytes(ogg) + b"\x13OpusHead" + b"\0" * 11,
            "sample.flac": b"fLaC\x80\0\0\x22" + b"\0" * 34 + b"\xff\xf8\0\0",
            "sample.m4a": box(b"ftyp", b"M4A \0\0\0\0isom") + box(b"moov", b"soun") + box(b"mdat", b"\x21\x10"),
        }
        for filename, data in fixtures.items():
            with self.subTest(filename=filename):
                self.assertEqual(self.import_audio(data, filename)[0], 201)
                self.assertEqual(self.import_audio(b"plain text cannot be audio" * 5, filename)[0], 400)
        self.assertEqual(self.import_audio(b"", "empty.mp3")[0], 400)
        self.assertEqual(self.import_audio(b"ID3" + b"\0" * 30, "fake.mp3")[0], 400)
        self.assertEqual(self.import_audio(fixtures["sample.wav"], "wrong.mp3")[0], 400)
        for filename in ("../escape.wav", "folder\\escape.wav", "C:escape.wav", "bad\0name.wav"):
            self.assertEqual(self.import_audio(fixtures["sample.wav"], filename)[0], 400)
        self.assertEqual(self.import_audio(fixtures["sample.wav"], "unsupported.exe")[0], 415)
        status, error = self.request("POST", "/api/music/import?filename=huge.mp3", b"", {"Content-Type": "audio/mpeg", "Content-Length": str(server.MAX_MUSIC_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertIn("音乐", error["error"])
        self.assertFalse((self.root / "escape.wav").exists())

    def test_music_range_supports_builtin_imported_head_and_unsatisfied_requests(self):
        data = self.wav_audio()
        track = self.import_audio(data)[1]["track"]
        for path, expected, content_type in ((track["url"], data, "audio/wav"), ("/audio/hymn.mp3", self.builtin_audio["hymn"], "audio/mpeg")):
            with self.subTest(path=path):
                status, headers, body = self.file_response(path)
                self.assertEqual(status, 200)
                self.assertEqual(body, expected)
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertEqual(headers["Accept-Ranges"], "bytes")
                for value, begin, end in (("bytes=3-9", 3, 9), ("bytes=10-", 10, len(expected) - 1), ("bytes=-7", len(expected) - 7, len(expected) - 1), ("bytes=0-99999", 0, len(expected) - 1)):
                    status, headers, body = self.file_response(path, headers={"Range": value})
                    self.assertEqual(status, 206)
                    self.assertEqual(body, expected[begin:end + 1])
                    self.assertEqual(headers["Content-Range"], f"bytes {begin}-{end}/{len(expected)}")
                    self.assertEqual(int(headers["Content-Length"]), end - begin + 1)
                status, headers, body = self.file_response(path, method="HEAD", headers={"Range": "bytes=0-3"})
                self.assertEqual((status, body, headers["Content-Length"]), (206, b"", "4"))
                for value in ("bytes=999999-", "bytes=9-3", "bytes=-0", "bytes=0-1,4-5", "bad"):
                    status, headers, _ = self.file_response(path, headers={"Range": value})
                    self.assertEqual(status, 416)
                    self.assertEqual(headers["Content-Range"], f"bytes */{len(expected)}")
        self.assertEqual(self.request("GET", track["url"].replace(".wav", ".mp3"))[0], 404)
        self.assertEqual(self.request("GET", "/media/music/../album.sqlite3")[0], 404)

    def test_failed_music_insert_rolls_back_new_copy_and_keeps_photo_data(self):
        photo = self.import_image()[1]["photo"]
        with self.server.store.connect() as db:
            db.execute("CREATE TRIGGER reject_audio BEFORE INSERT ON music_tracks WHEN NEW.origin = 'imported' BEGIN SELECT RAISE(ABORT,'injected music insert failure'); END")
        self.assertEqual(self.import_audio(self.wav_audio())[0], 500)
        self.assertEqual(list(self.server.store.music_dir.iterdir()), [])
        self.assertEqual(len(self.request("GET", "/api/music")[1]["tracks"]), 3)
        self.assertEqual(self.request("GET", photo["url"])[1], self.image)

    def test_online_music_search_preview_import_and_migration_are_local(self):
        audio = self.wav_audio()
        calls=[]
        def fetch(url, maximum, **_kwargs):
            calls.append(url)
            if 'types=search' in url:
                return json.dumps([{'id':'track-1','name':'海边 / 晨光','artist':['摄影配乐'],'album':'旅途','source':'netease'}]).encode()
            if 'types=url' in url:
                return json.dumps({'url':'https://music.126.net/test.wav'}).encode()
            return audio
        with patch('online_music.fetch_bytes', side_effect=fetch), patch('online_music.public_url', side_effect=lambda value:value):
            status, found = self.json_request('POST','/api/music/online/search',{'query':'海边'})
            self.assertEqual(status,200); token=found['tracks'][0]['token']
            self.assertNotIn('id',found['tracks'][0])
            self.assertEqual(self.json_request('POST','/api/music/online/search',{'query':'海边'})[1],found)
            self.assertEqual(len(calls),1,'repeat searches use cache')
            self.assertEqual(self.server.store.music()['tracks'][-1]['origin'],'builtin')
            self.assertEqual(self.json_request('POST','/api/music/online/preview',{'token':token})[0],200)
            self.assertFalse(self.server.store.music_dir.exists(),'a preview creates no library file')
            status,result=self.json_request('POST','/api/music/online/import',{'token':token})
            self.assertEqual(status,201);track=result['track']
            self.assertEqual((track['title'],track['artist'],track['origin']),('海边 / 晨光','摄影配乐','imported'))
            self.assertEqual(self.server.store.music_path(track['id'],'.wav').read_bytes(),audio)
            self.server.store.update_music(track['id'],{'title':'自己改的曲名'})
            again=self.json_request('POST','/api/music/online/import',{'token':token})
            self.assertEqual(again[0],200);self.assertTrue(again[1]['duplicate']);self.assertEqual(again[1]['track']['title'],'自己改的曲名')
            self.assertEqual(sum('types=url' in url for url in calls),1,'preview and import reuse the resolved URL')
            with self.server.store.backup_archive(self.application_file) as archive:
                with zipfile.ZipFile(archive) as package:
                    music_name=next(name for name in package.namelist() if name.endswith('/music/'+track['id']+'.wav'))
                    self.assertEqual(package.read(music_name),audio)

    def test_online_music_rejects_bad_requests_expired_results_and_foreign_websites(self):
        with patch('online_music.fetch_bytes') as fetch:
            for value in ({'query':''},{'query':'x','source':'other'},{'query':'x','source':[]},{'query':'x','page':True},{'query':'x'*81}):
                self.assertEqual(self.json_request('POST','/api/music/online/search',value)[0],400)
            self.assertEqual(self.json_request('POST','/api/music/online/import',{'token':'missing'})[0],410)
            self.assertEqual(self.json_request('POST','/api/music/online/import',{'url':'https://music.126.net/a'})[0],400)
            for action in ('search','preview','import'):
                self.assertEqual(self.request('POST','/api/music/online/'+action,b'{}',{'Content-Type':'application/json','Origin':'https://evil.example'})[0],403)
            fetch.assert_not_called()

    def test_online_music_unavailable_audio_and_rate_limit_leave_library_unchanged(self):
        client=self.server.online_music; before=self.server.store.music()
        token='a'*32
        import time
        client._records[token]=(time.monotonic(),{'token':token,'id':'id','source':'netease','title':'test','artist':''})
        with patch('online_music.fetch_bytes', return_value=b'{"url":""}'):
            self.assertEqual(self.json_request('POST','/api/music/online/import',{'token':token})[0],404)
        with patch.object(client,'preview',return_value={'url':'https://music.126.net/a'}),patch('online_music.fetch_bytes',return_value=b'<html>not audio</html>'):
            self.assertEqual(self.json_request('POST','/api/music/online/import',{'token':token})[0],415)
        client._urls[token]=(time.monotonic(),'https://music.126.net/expired.mp3')
        with patch.object(client,'preview',return_value={'url':'https://music.126.net/expired.mp3'}),patch('online_music.fetch_bytes',side_effect=online_music.OnlineMusicError(502,'expired')):
            self.assertEqual(self.json_request('POST','/api/music/online/import',{'token':token})[0],502)
            self.assertNotIn(token,client._urls,'a failed download resolves a fresh URL on retry')
        client._requests.extend([time.monotonic()]*40)
        with patch('online_music.fetch_bytes') as fetch:
            self.assertEqual(self.json_request('POST','/api/music/online/search',{'query':'test'})[0],429);fetch.assert_not_called()
        self.assertEqual(self.server.store.music(),before)

    def test_path_traversal_and_static_boundary(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        for path in ("/../private.txt", "/%2e%2e/private.txt", "/..%5cprivate.txt", "/media/originals/../../private.txt", "/server.py", "/C:/Windows/win.ini", "/api/../private.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
