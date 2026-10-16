"""Video HTTP integration and lifecycle checks use disposable album fixtures."""

import hashlib
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote
import uuid
import wave
import zipfile

import server
from video_jobs import VideoJobManager, _MARKER, _MARKER_CONTENT

try:
    from PIL import Image
except ImportError:
    Image = None


class FakeVideoError(Exception):
    pass


@unittest.skipUnless(Image, "Pillow is needed for disposable photo fixtures")
class VideoAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.web = self.root / "ui"
        (self.web / "assets").mkdir(parents=True)
        (self.web / "index.html").write_text("Album", encoding="utf-8")
        (self.web / "audio").mkdir()
        (self.web / "audio" / "tracks.json").write_text(json.dumps({"tracks": [{"id": "hymn", "title": "Hymn", "url": "/audio/hymn.mp3"}]}), encoding="utf-8")
        self.app = self.root / "test.exe"
        self.app.write_bytes(b"MZ-test-video-application")
        self.service = server.AlbumHTTPServer(0, self.root / "data", self.web, application_file=self.app)
        self.thread = threading.Thread(target=self.service.serve_forever, daemon=True)
        self.thread.start()
        self.closed = False
        self.hold = False
        self.started, self.release = threading.Event(), threading.Event()
        self.calls = []
        self.movie = b"\0\0\0\x18ftypmp42" + bytes(range(256)) * 300
        preview = io.BytesIO()
        Image.new("RGB", (960, 540), "#d0c9bf").save(preview, "JPEG")
        self.preview_bytes = preview.getvalue()
        self.previewer = Mock(return_value=self.preview_bytes)
        self.module = types.SimpleNamespace(VideoExportError=FakeVideoError, encode_slideshow=self.encode, render_video_preview=self.previewer)
        self.module_patch = patch.dict(sys.modules, {"video_export": self.module})
        self.module_patch.start()
        self.collection = self.service.store.create_collection({"title": '我的旅行 / "片刻"'})["collection"]
        self.first, self.first_bytes = self.photo("red", "2024-01-02T03:04:05")
        self.second, self.second_bytes = self.photo("navy", "2025-06-07T08:09:10")
        self.service.store.add_collection_photos(self.collection["id"], {"photo_ids": [self.second["id"], self.first["id"]]})
        audio = io.BytesIO()
        with wave.open(audio, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * 80)
        self.audio = audio.getvalue()
        self.track = self.service.store.import_music(self.audio, "本地音乐.wav")["track"]
        self.start_path = "/api/collections/" + self.collection["id"] + "/video"
        self.preview_path = self.start_path + "-preview"

    def tearDown(self):
        self.release.set()
        if not self.closed:
            self.service.shutdown()
            self.service.server_close()
            self.thread.join(5)
        self.module_patch.stop()
        self.temp.cleanup()

    def photo(self, color, taken):
        buffer = io.BytesIO()
        Image.new("RGB", (80, 60), color).save(buffer, "JPEG")
        data = buffer.getvalue()
        return self.service.store.import_photo(data, color + ".jpg", taken)["photo"], data

    def request(self, method, path, payload=None, headers=None):
        supplied = dict(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            supplied["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.service.server_port, timeout=5)
        try:
            connection.request(method, path, body, supplied)
            response = connection.getresponse()
            data = response.read()
            response_headers = dict(response.getheaders())
            if "application/json" in response_headers.get("Content-Type", "") and data:
                data = json.loads(data)
            return response.status, response_headers, data
        finally:
            connection.close()

    def encode(self, slides, assets, music, output, options, progress, cancelled):
        call = {"slides": slides, "assets": assets, "music": music, "output": output, "options": options}
        self.calls.append(call)
        self.started.set()
        progress(23, "正在准备画面…")
        while self.hold and not self.release.wait(0.01):
            if cancelled.is_set():
                raise FakeVideoError("已取消")
        if cancelled.is_set():
            raise FakeVideoError("已取消")
        call["photo_bytes"] = [path.read_bytes() for path, _photo in slides]
        call["music_bytes"] = music.read_bytes() if music else None
        (output.parent / "frames").mkdir()
        (output.parent / "frames" / "frame.jpg").write_bytes(b"temporary-frame")
        output.write_bytes(self.movie)
        progress(99, "正在完成视频…")
        return {"duration": len(slides) * options["seconds_per_photo"] - (len(slides) - 1) * 0.8, "width": 1920, "height": 1080}

    def start(self, **options):
        status, _headers, data = self.request("POST", self.start_path, options)
        self.assertEqual(status, 202, data)
        return data["job"]

    def wait_job(self, job_id, expected="completed"):
        deadline = time.monotonic() + 5
        while True:
            status, _headers, data = self.request("GET", "/api/video-jobs/" + job_id)
            self.assertEqual(status, 200, data)
            job = data["job"]
            if job["status"] not in {"preparing", "encoding"}:
                self.assertEqual(job["status"], expected, job)
                return job
            self.assertLess(time.monotonic(), deadline, job)
            time.sleep(0.01)

    def test_job_orders_snapshots_defaults_downloads_and_ranges(self):
        originals = {path.name: hashlib.sha256(path.read_bytes()).digest() for path in self.service.store.originals.iterdir()}
        before = self.service.store.photos()
        self.service.store.update_music(self.track["id"], {"enabled": False})
        job = self.start(music_id=self.track["id"])
        finished = self.wait_job(job["id"])
        self.assertEqual((finished["photo_count"], finished["progress"], finished["duration"]), (2, 100, 9.2))
        call = self.calls[0]
        self.assertEqual([photo["id"] for _path, photo in call["slides"]], [self.second["id"], self.first["id"]])
        self.assertEqual(call["photo_bytes"], [self.second_bytes, self.first_bytes])
        self.assertEqual(call["music_bytes"], self.audio)
        self.assertEqual(call["options"], {"resolution": "4k", "seconds_per_photo": 5, "order": "collection", "music_id": self.track["id"], "volume": 0.5})
        self.assertEqual(call["assets"], self.web / "assets")
        directory = call["output"].parent
        self.assertFalse((directory / "sources").exists())
        self.assertFalse((directory / "frames").exists())
        status, headers, body = self.request("GET", finished["video_url"])
        self.assertEqual((status, body), (200, self.movie))
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertTrue(headers["Content-Disposition"].startswith("inline;"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, headers, body = self.request("GET", finished["download_url"], headers={"Range": "bytes=4-17"})
        self.assertEqual((status, body), (206, self.movie[4:18]))
        self.assertEqual(headers["Content-Range"], f"bytes 4-17/{len(self.movie)}")
        filename = unquote(headers["Content-Disposition"].split("filename*=UTF-8''")[1])
        self.assertTrue(filename.endswith("-幻灯片.mp4"))
        self.assertNotIn("/", filename)
        self.assertNotIn('"', filename)
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        status, headers, body = self.request("HEAD", finished["video_url"], headers={"Range": "bytes=-8"})
        self.assertEqual((status, body, headers["Content-Length"]), (206, b"", "8"))
        self.assertEqual(self.request("GET", finished["video_url"], headers={"Range": "bytes=999999-"})[0], 416)
        self.assertEqual(self.request("GET", finished["video_url"] + "?download=2")[0], 400)
        self.assertEqual(self.service.store.photos(), before)
        self.assertEqual(originals, {path.name: hashlib.sha256(path.read_bytes()).digest() for path in self.service.store.originals.iterdir()})

    def test_all_resolutions_reach_encoder_and_keep_download_format(self):
        for resolution in ('4k', '1440p', '1080p', '720p'):
            with self.subTest(resolution=resolution):
                job = self.start(resolution=resolution)
                finished = self.wait_job(job['id'])
                self.assertEqual(self.calls[-1]['options']['resolution'], resolution)
                status, headers, _body = self.request('HEAD', finished['download_url'])
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], 'video/mp4')
                self.assertTrue(headers['Content-Disposition'].endswith('.mp4'))

    def test_preview_selects_actual_first_photo_without_creating_job(self):
        status, headers, body = self.request("GET", self.preview_path)
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/jpeg", self.preview_bytes))
        self.assertEqual(self.previewer.call_args.args[1]["id"], self.second["id"])
        self.assertEqual(self.request("GET", self.preview_path + "?order=newest")[0], 200)
        self.assertEqual(self.previewer.call_args.args[1]["id"], self.second["id"])
        for query in ("order=other", "order=oldest&order=newest", "path=C:/private.jpg"):
            self.assertEqual(self.request("GET", self.preview_path + "?" + query)[0], 400)
        self.assertFalse(self.service.video_jobs.root.exists())

    def test_saved_portfolio_order_drives_default_preview_and_encoding(self):
        third,_=self.photo('green','2024-05-01T12:00:00')
        self.service.store.add_collection_photos(self.collection['id'],{'photo_ids':[third['id']]})
        wanted=[self.first['id'],self.second['id'],third['id']]
        self.service.store.reorder_collection_photos(self.collection['id'],{'photo_ids':wanted})
        self.assertEqual(self.request('GET',self.preview_path)[0],200)
        self.assertEqual(self.previewer.call_args.args[1]['id'],self.first['id'])
        completed=self.wait_job(self.start()['id'])
        self.assertEqual(completed['status'],'completed')
        self.assertEqual(self.calls[0]['options']['order'],'collection')
        self.assertEqual([photo['id'] for _,photo in self.calls[0]['slides']],wanted)
        self.assertEqual(self.request('GET',self.preview_path+'?order=oldest')[0],200)
        self.assertEqual(self.previewer.call_args.args[1]['id'],self.first['id'])

    def test_video_routes_strictly_reject_other_origins_and_unmanaged_ids(self):
        completed = self.wait_job(self.start()["id"])
        for method, path, body in (("POST", self.start_path, {}), ("GET", self.preview_path, None), ("GET", "/api/video-jobs/" + completed["id"], None), ("DELETE", "/api/video-jobs/" + completed["id"], None), ("GET", completed["video_url"], None)):
            for headers in ({"Origin": "https://example.invalid"}, {"Sec-Fetch-Site": "same-site"}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(method, path, body, headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/video-jobs/" + "0" * 32)[0], 404)
        self.assertEqual(self.request("GET", "/api/video-jobs/../../album.sqlite3/file")[0], 404)
        self.assertEqual(self.request("POST", "/api/collections/" + "0" * 32 + "/video", {})[0], 404)

    def test_invalid_options_empty_collections_and_imported_music_rules(self):
        invalid = [{"resolution": "8k"}, {"resolution": []}, {"seconds_per_photo": True}, {"seconds_per_photo": 5.0}, {"seconds_per_photo": 2}, {"order": "filename"}, {"music_id": "hymn"}, {"music_id": "../music"}, {"music_id": "0" * 32}, {"volume": True}, {"volume": -0.1}, {"volume": 1.1}, {"volume": float("nan")}, {"volume": float("inf")}, {"volume": "0.5"}, {"output_path": "C:/private.mp4"}]
        for options in invalid:
            with self.subTest(options=options):
                self.assertEqual(self.request("POST", self.start_path, options)[0], 400)
        self.service.store.remove_music(self.track["id"])
        self.assertEqual(self.request("POST", self.start_path, {"music_id": self.track["id"]})[0], 400)
        empty = self.service.store.create_collection({"title": "空作品集"})["collection"]
        self.assertEqual(self.request("POST", "/api/collections/" + empty["id"] + "/video", {})[0], 400)
        self.assertEqual(self.request("GET", "/api/collections/" + empty["id"] + "/video-preview")[0], 400)
        self.assertFalse(self.calls)
        self.assertFalse(self.service.video_jobs.root.exists())

    def test_photo_count_and_total_duration_are_bounded_before_file_work(self):
        for count, seconds in ((301, 3), (250, 8)):
            collection = self.service.store.create_collection({"title": "限制测试"})["collection"]
            with self.service.store.connect() as db:
                source = dict(db.execute("SELECT * FROM photos LIMIT 1").fetchone())
                for _ in range(count):
                    photo = {**source, "id": uuid.uuid4().hex, "sha256": uuid.uuid4().hex}
                    db.execute("INSERT INTO photos(" + ",".join(photo) + ") VALUES(" + ",".join("?" for _ in photo) + ")", tuple(photo.values()))
                    db.execute("INSERT INTO collection_photos(collection_id,photo_id,added_at) VALUES(?,?,?)", (collection["id"], photo["id"], server.now()))
            status, _headers, result = self.request("POST", "/api/collections/" + collection["id"] + "/video", {"seconds_per_photo": seconds})
            self.assertEqual(status, 400)
            self.assertIn("300" if count > 300 else "30", result["error"])
        self.assertFalse(self.service.video_jobs.root.exists())

    def test_single_active_job_cancel_and_unfinished_file(self):
        self.hold = True
        job = self.start()
        self.assertTrue(self.started.wait(3))
        self.assertEqual(self.request("POST", self.start_path, {})[0], 409)
        path = "/api/video-jobs/" + job["id"]
        self.assertEqual(self.request("GET", path + "/file")[0], 409)
        self.assertEqual(self.request("DELETE", path)[0], 200)
        cancelled = self.wait_job(job["id"], "cancelled")
        self.assertIsNone(cancelled["video_url"])
        self.assertIsNone(cancelled["error"])
        self.service.video_jobs._jobs[job["id"]].thread.join(3)
        self.assertFalse((self.service.video_jobs.root / job["id"]).exists())

    def test_encoding_uses_fixed_copies_after_album_edits_and_deletion(self):
        self.hold = True
        job = self.start(music_id=self.track["id"], order="newest", resolution="720p", seconds_per_photo=3, volume=0)
        self.assertTrue(self.started.wait(3))
        # Encoding is outside the album lock, so edits and deletion remain usable.
        self.assertEqual(self.request("POST", "/api/photos/" + self.second["id"], {"title": "导出开始后的改名"})[0], 200)
        self.assertEqual(self.request("DELETE", "/api/photos/" + self.first["id"])[0], 200)
        self.service.store.remove_music(self.track["id"])
        self.release.set()
        self.wait_job(job["id"])
        self.assertEqual(self.calls[0]["photo_bytes"], [self.second_bytes, self.first_bytes])
        self.assertEqual(self.calls[0]["slides"][0][1]["title"], self.second["title"])
        self.assertEqual(self.calls[0]["music_bytes"], self.audio)

    def test_copy_fallback_and_missing_or_escaped_media(self):
        self.hold = True
        with patch("video_jobs.os.link", side_effect=OSError("hard links unsupported")):
            job = self.start(music_id=self.track["id"])
            self.assertTrue(self.started.wait(3))
        self.assertEqual(self.request("DELETE", "/api/photos/" + self.first["id"])[0], 200)
        self.release.set()
        self.wait_job(job["id"])
        self.assertEqual(self.calls[0]["photo_bytes"], [self.second_bytes, self.first_bytes])
        original = self.service.store.originals / (self.second["id"] + ".jpg")
        parked = self.root / "parked.jpg"
        original.replace(parked)
        try:
            self.assertEqual(self.request("POST", self.start_path, {})[0], 404)
            self.assertEqual(self.request("GET", self.preview_path)[0], 404)
        finally:
            parked.replace(original)
        real_resolve = Path.resolve

        def redirect(path, *args, **kwargs):
            return self.app if path == original else real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", redirect):
            self.assertEqual(self.request("POST", self.start_path, {})[0], 404)

    def test_failure_exposes_safe_error_and_cleans_job_inputs(self):
        self.module.encode_slideshow = Mock(side_effect=FakeVideoError("音频无法解码，请换一首音乐。"))
        job = self.start()
        failed = self.wait_job(job["id"], "failed")
        self.assertEqual(failed["error"], "音频无法解码，请换一首音乐。")
        self.service.video_jobs._jobs[job["id"]].thread.join(3)
        self.assertFalse((self.service.video_jobs.root / job["id"]).exists())
        self.assertEqual(len(self.service.store.photos()["photos"]), 2)
        self.assertEqual(self.service.store.music_path(self.track["id"], ".wav").read_bytes(), self.audio)

    def test_result_retention_expiry_and_active_download_cleanup(self):
        first = self.wait_job(self.start()["id"])
        first_dir = self.service.video_jobs.root / first["id"]
        with self.service.video_jobs.open_file(first["id"]) as (content, _name):
            for _ in range(3):
                latest = self.wait_job(self.start()["id"])
            self.assertEqual(self.request("GET", "/api/video-jobs/" + first["id"])[0], 404)
            self.assertTrue(first_dir.exists())
            self.assertEqual(content.read(), self.movie)
        self.assertFalse(first_dir.exists())
        manager = self.service.video_jobs
        with manager._lock:
            manager._jobs[latest["id"]].finished_at = time.monotonic() - 3601
        self.assertEqual(self.request("GET", "/api/video-jobs/" + latest["id"])[0], 404)
        self.assertFalse((manager.root / latest["id"]).exists())
        self.assertLessEqual(len(list(manager.root.iterdir())), 3)

    def test_transient_videos_are_excluded_from_complete_backup(self):
        self.wait_job(self.start()["id"])
        status, _headers, body = self.request("GET", "/api/backup")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertFalse(any(".video-exports" in name or name.endswith(".mp4") for name in archive.namelist()))

    def test_shutdown_cancels_encoder_and_startup_only_removes_owned_directories(self):
        self.hold = True
        job = self.start()
        self.assertTrue(self.started.wait(3))
        self.service.shutdown()
        self.service.server_close()
        self.thread.join(5)
        self.closed = True
        workspace = self.service.video_jobs.root
        self.assertFalse((workspace / job["id"]).exists())
        unknown = workspace / "keep-user-data"
        unknown.mkdir()
        (unknown / "notes.txt").write_text("keep", encoding="utf-8")
        wrong_marker = workspace / uuid.uuid4().hex
        wrong_marker.mkdir()
        (wrong_marker / _MARKER).write_text("unrelated", encoding="ascii")
        owned = workspace / uuid.uuid4().hex
        owned.mkdir()
        (owned / _MARKER).write_text(_MARKER_CONTENT, encoding="ascii")
        (owned / "temporary.mp4").write_bytes(b"old-result")
        reopened = VideoJobManager(self.service.store, self.web / "assets")
        self.assertFalse(owned.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(wrong_marker.exists())
        reopened.close()


if __name__ == "__main__":
    unittest.main()
