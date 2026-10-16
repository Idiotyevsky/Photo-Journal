"""Color analysis HTTP checks use synthetic photos and disposable album data."""

import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from PIL import Image

import server


class ColorsAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        ui = self.root / "ui"
        (ui / "assets").mkdir(parents=True)
        (ui / "audio").mkdir()
        (ui / "index.html").write_text("<h1>Temporary test album</h1>", encoding="utf-8")
        (ui / "audio" / "tracks.json").write_text('{"tracks":[]}', encoding="utf-8")
        application = self.root / "test.exe"
        application.write_bytes(b"MZ-disposable-colors-test")
        self.service = server.AlbumHTTPServer(0, self.root / "data", ui, application_file=application)
        self.thread = threading.Thread(target=self.service.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = f"http://127.0.0.1:{self.service.server_port}"

    def close_server(self):
        self.service.shutdown()
        self.service.server_close()
        self.thread.join(5)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.service.server_port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            content = response.read()
            response_headers = dict(response.getheaders())
            if content and "application/json" in response_headers.get("Content-Type", ""):
                content = json.loads(content)
            return response.status, response_headers, content
        finally:
            connection.close()

    def json_request(self, method, path, value):
        return self.request(method, path, json.dumps(value).encode("utf-8"), {"Content-Type": "application/json"})

    @staticmethod
    def image_bytes(color, size=(96, 64)):
        result = io.BytesIO()
        with Image.new("RGB", size, color) as image:
            image.save(result, "PNG")
        return result.getvalue()

    def import_photo(self, color, taken_at="2025-01-02T03:04:05", size=(96, 64)):
        data = self.image_bytes(color, size)
        query = urlencode({"filename": "合成照片.png", "modified": taken_at})
        status, _headers, result = self.request("POST", "/api/import?" + query, data, {"Content-Type": "image/png"})
        self.assertEqual(status, 201, result)
        self.assertFalse(result["duplicate"])
        return result["photo"], data

    def update_photo(self, photo, changes):
        status, _headers, result = self.json_request("POST", "/api/photos/" + photo["id"], changes)
        self.assertEqual(status, 200, result)
        return result["photo"]

    def summary(self, period="month", scope="all"):
        query = urlencode({"period": period, "scope": scope})
        status, headers, result = self.request("GET", "/api/colors?" + query)
        self.assertEqual(status, 200, result)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(result["period"], period)
        self.assertEqual(result["scope"], scope)
        self.assert_summary_schema(result)
        return result

    def assert_summary_schema(self, result):
        self.assertEqual(result["analyzed_photos"] + result["skipped_photos"], result["total_photos"])
        self.assertEqual(sum(group["photo_count"] for group in result["periods"]), result["total_photos"])
        self.assertEqual(result["undated_photos"], sum(group["photo_count"] for group in result["periods"] if group["key"] == "undated"))
        for group in result["periods"]:
            self.assertEqual(group["photo_count"], len(group["photo_ids"]))
            self.assertEqual(len(group["photo_ids"]), len(set(group["photo_ids"])))
            self.assertTrue(set(group["representative_ids"]) <= set(group["photo_ids"]))
            self.assertLessEqual(len(group["palette"]), 5)
            if group["palette"]:
                self.assertAlmostEqual(sum(color["share"] for color in group["palette"]), 1, delta=.02)
                for color in group["palette"]:
                    self.assertRegex(color["hex"], r"^#[0-9a-fA-F]{6}$")
                    self.assertGreater(color["share"], 0)
                    self.assertLessEqual(color["share"], 1)
                    self.assertTrue(set(color["photo_ids"]) <= set(group["photo_ids"]))
                for name in ("brightness", "saturation", "warmth"):
                    self.assertGreaterEqual(group["metrics"][name], 0)
                    self.assertLessEqual(group["metrics"][name], 1)
            else:
                self.assertEqual(group["representative_ids"], [])
                self.assertEqual(group["metrics"], {"brightness": None, "saturation": None, "warmth": None})

    def test_empty_defaults_valid_options_and_head_are_fresh_json(self):
        status, headers, empty = self.request("GET", "/api/colors")
        self.assertEqual(status, 200, empty)
        self.assertEqual(empty, {"period": "month", "scope": "all", "total_photos": 0, "analyzed_photos": 0, "skipped_photos": 0, "undated_photos": 0, "periods": []})
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        for period in ("month", "quarter", "year"):
            for scope in ("all", "favorites"):
                with self.subTest(period=period, scope=scope):
                    self.assertEqual(self.summary(period, scope)["periods"], [])
        status, head_headers, body = self.request("HEAD", "/api/colors?period=year&scope=favorites")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertIn("application/json", head_headers.get("Content-Type", ""))
        self.assertEqual(head_headers.get("Cache-Control"), "no-store")
        self.assertGreater(int(head_headers["Content-Length"]), 0)

    def test_month_quarter_year_and_duplicate_import_update_without_restarting(self):
        red, red_bytes = self.import_photo((245, 15, 20), "2024-01-02T10:00:00")
        self.assertEqual(self.summary()["total_photos"], 1)
        blue, _ = self.import_photo((15, 25, 245), "2024-04-03T10:00:00")
        green, _ = self.import_photo((20, 225, 35), "2025-01-04T10:00:00")
        self.assertEqual([group["key"] for group in self.summary()["periods"]], ["2025-01", "2024-04", "2024-01"])
        self.assertEqual([group["key"] for group in self.summary("quarter")["periods"]], ["2025-Q1", "2024-Q2", "2024-Q1"])
        year = self.summary("year")
        self.assertEqual([(group["key"], group["photo_count"]) for group in year["periods"]], [("2025", 1), ("2024", 2)])
        self.assertEqual(year["analyzed_photos"], 3)
        red_group = next(group for group in self.summary()["periods"] if group["key"] == "2024-01")
        dominant = red_group["palette"][0]["hex"]
        rgb = tuple(int(dominant[index:index + 2], 16) for index in (1, 3, 5))
        self.assertGreater(rgb[0], 200)
        self.assertLess(max(rgb[1:]), 80)
        status, _headers, duplicate = self.request("POST", "/api/import?" + urlencode({"filename": "重复.png", "modified": "2026-12-31T10:00:00"}), red_bytes, {"Content-Type": "image/png"})
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["photo"]["id"], red["id"])
        self.assertEqual(self.summary()["total_photos"], 3)
        self.assertEqual({photo_id for group in year["periods"] for photo_id in group["photo_ids"]}, {red["id"], blue["id"], green["id"]})

    def test_favorites_filter_reloads_current_metadata_on_every_request(self):
        red, _ = self.import_photo((240, 20, 30))
        blue, _ = self.import_photo((20, 30, 240))
        self.summary()
        self.assertEqual(self.summary(scope="favorites")["total_photos"], 0)
        self.update_photo(red, {"favorite": True})
        favorite = self.summary(scope="favorites")
        self.assertEqual(favorite["total_photos"], 1)
        self.assertEqual(favorite["periods"][0]["photo_ids"], [red["id"]])
        self.assertTrue(all(set(color["photo_ids"]) <= {red["id"]} for color in favorite["periods"][0]["palette"]))
        self.update_photo(blue, {"favorite": True})
        self.assertEqual(self.summary(scope="favorites")["total_photos"], 2)
        self.update_photo(red, {"favorite": False})
        self.assertEqual(self.summary(scope="favorites")["periods"][0]["photo_ids"], [blue["id"]])
        self.assertEqual(self.summary()["total_photos"], 2)

    def test_date_edits_regroup_and_legacy_missing_or_invalid_dates_are_last(self):
        red, _ = self.import_photo((235, 20, 30), "2024-01-02T10:00:00")
        blue, _ = self.import_photo((20, 30, 235), "2024-04-02T10:00:00")
        self.assertEqual(len(self.summary("year")["periods"]), 1)
        self.update_photo(blue, {"taken_at": "2026-03-04T10:00:00"})
        self.assertEqual([group["key"] for group in self.summary("year")["periods"]], ["2026", "2024"])
        # Only a disposable legacy row is altered directly; the public edit API
        # correctly rejects malformed dates, while old data still needs a home.
        for value in ("", "not-a-date", "2025-99-40T10:00:00"):
            with self.subTest(legacy_date=value):
                with self.service.store.lock, self.service.store.connect() as db:
                    db.execute("UPDATE photos SET taken_at = ? WHERE id = ?", (value, red["id"]))
                result = self.summary("quarter")
                self.assertEqual([group["key"] for group in result["periods"]], ["2026-Q1", "undated"])
                self.assertEqual(result["undated_photos"], 1)
                self.assertEqual(result["periods"][-1]["photo_ids"], [red["id"]])
                self.assertEqual(result["periods"][-1]["label"], "未注明日期")

    def test_deleted_photos_leave_every_group_palette_and_representative_list(self):
        red, _ = self.import_photo((230, 20, 40))
        blue, _ = self.import_photo((20, 40, 230))
        self.update_photo(red, {"favorite": True})
        self.summary()
        self.summary(scope="favorites")
        status, _headers, response = self.request("DELETE", "/api/photos/" + red["id"])
        self.assertEqual(status, 200, response)
        result = self.summary()
        self.assertEqual(result["total_photos"], 1)
        group = result["periods"][0]
        self.assertEqual(group["photo_ids"], [blue["id"]])
        self.assertNotIn(red["id"], group["representative_ids"])
        self.assertTrue(all(red["id"] not in color["photo_ids"] for color in group["palette"]))
        self.assertEqual(self.summary(scope="favorites")["total_photos"], 0)

    def test_missing_and_corrupted_previews_skip_even_after_successful_cache(self):
        red, _ = self.import_photo((225, 25, 45))
        blue, _ = self.import_photo((25, 45, 225))
        self.assertEqual(self.summary()["analyzed_photos"], 2)
        with self.service.store.lock:
            (self.service.store.previews / (red["id"] + ".jpg")).unlink()
            (self.service.store.previews / (blue["id"] + ".jpg")).write_bytes(b"not a preview")
        result = self.summary()
        self.assertEqual((result["total_photos"], result["analyzed_photos"], result["skipped_photos"]), (2, 0, 2))
        self.assertEqual(set(result["periods"][0]["photo_ids"]), {red["id"], blue["id"]})
        self.assertEqual(result["periods"][0]["palette"], [])
        self.assertEqual(result["periods"][0]["representative_ids"], [])
        status, _headers, photos = self.request("GET", "/api/photos")
        self.assertEqual(status, 200)
        self.assertEqual(len(photos["photos"]), 2, "analysis never removes a photo whose preview is damaged")

    def test_unknown_empty_repeated_or_invalid_options_are_rejected_before_analysis(self):
        invalid = ("period=", "scope=", "period", "scope", "period=week", "scope=none", "period=MONTH", "extra=1", "period=month&period=month", "scope=all&scope=favorites", "period=month&scope=all&extra=", "period=month&%70eriod=year")
        with patch.object(self.service.colors, "analyze", wraps=self.service.colors.analyze) as analyze:
            for query in invalid:
                with self.subTest(query=query):
                    status, _headers, result = self.request("GET", "/api/colors?" + query)
                    self.assertEqual(status, 400, result)
                    self.assertIsInstance(result.get("error"), str)
            analyze.assert_not_called()

    def test_origin_host_and_fetch_metadata_protect_color_results(self):
        denied = ({"Origin": "https://outside.example"}, {"Origin": self.origin + ".outside.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Host": "outside.example"})
        with patch.object(self.service.colors, "analyze", wraps=self.service.colors.analyze) as analyze:
            for headers in denied:
                with self.subTest(headers=headers):
                    status, response_headers, result = self.request("GET", "/api/colors", headers=headers)
                    self.assertEqual(status, 403, result)
                    self.assertIsInstance(result.get("error"), str)
                    self.assertNotIn("Access-Control-Allow-Origin", response_headers)
            analyze.assert_not_called()
        status, headers, result = self.request("GET", "/api/colors", headers={"Origin": self.origin, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200, result)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        status, _headers, body = self.request("HEAD", "/api/colors", headers={"Origin": "https://outside.example"})
        self.assertEqual(status, 403)
        self.assertEqual(body, b"")

    def test_analysis_failure_is_readable_json_and_does_not_mutate_album_data(self):
        red, _ = self.import_photo((220, 30, 50))
        blue, _ = self.import_photo((30, 50, 220))
        status, _headers, collection = self.json_request("POST", "/api/collections", {"title": "临时作品集"})
        self.assertEqual(status, 201)
        status, _headers, result = self.json_request("POST", "/api/collections/" + collection["collection"]["id"] + "/photos", {"photo_ids": [red["id"], blue["id"]]})
        self.assertEqual(status, 200, result)
        before_photos, before_collections = self.service.store.photos(), self.service.store.collections()
        media = [path for directory in (self.service.store.originals, self.service.store.previews) for path in directory.iterdir()]
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in media}
        for period in ("month", "quarter", "year"):
            self.summary(period)
        with patch.object(self.service.colors, "analyze", side_effect=RuntimeError("private internal details")):
            status, _headers, error = self.request("GET", "/api/colors")
            self.assertEqual(status, 500)
            self.assertIsInstance(error.get("error"), str)
            self.assertNotIn("private internal details", error["error"])
        self.assertEqual(self.service.store.photos(), before_photos)
        self.assertEqual(self.service.store.collections(), before_collections)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in media}, hashes)
        self.assertEqual(self.summary()["analyzed_photos"], 2, "a failed request cannot poison the next analysis")


if __name__ == "__main__":
    unittest.main(verbosity=2)
