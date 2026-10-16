"""Color analysis checks use synthetic images and a disposable miniature store."""

from contextlib import closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import color_analysis
from color_analysis import ColorAnalysis, ColorAnalysisError

try:
    from PIL import Image, ImageCms
except ImportError:
    Image = ImageCms = None


class TemporaryStore:
    def __init__(self, root):
        self.root = root
        self.previews = root / "previews"
        self.previews.mkdir()
        self.lock = threading.RLock()
        self.database = root / "album.sqlite3"
        with self.connect() as database:
            database.execute("CREATE TABLE photos (id TEXT PRIMARY KEY, sha256 TEXT, taken_at TEXT, favorite INTEGER)")

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.database)
        database.row_factory = sqlite3.Row
        try:
            with database:
                yield database
        finally:
            database.close()

    def add(self, image, taken_at="2025-05-03T09:10:11", favorite=False, **save_options):
        photo_id = uuid.uuid4().hex
        buffer = io.BytesIO()
        image.save(buffer, save_options.pop("format", "PNG"), **save_options)
        content = buffer.getvalue()
        (self.previews / (photo_id + ".jpg")).write_bytes(content)
        with self.connect() as database:
            database.execute("INSERT INTO photos VALUES (?,?,?,?)", (photo_id, hashlib.sha256(content).hexdigest(), taken_at, int(favorite)))
        return photo_id

    def change(self, photo_id, **fields):
        with self.lock, self.connect() as database:
            database.execute("UPDATE photos SET " + ",".join(field + "=?" for field in fields) + " WHERE id=?", (*fields.values(), photo_id))


@unittest.skipUnless(Image and ImageCms, "Pillow and its color management module are required")
class ColorAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = TemporaryStore(self.root)
        self.engine = ColorAnalysis(self.store)

    def tearDown(self):
        self.engine.close()
        self.temporary.cleanup()

    def solid(self, color, size=(60, 40), **fields):
        return self.store.add(Image.new("RGB", size, color), **fields)

    @staticmethod
    def shares(result):
        return {color["hex"]: color["share"] for color in result["periods"][0]["palette"]}

    def test_pixel_proportions_metrics_and_repeatability(self):
        image = Image.new("RGB", (192, 96), "red")
        image.paste("blue", (144, 0, 192, 96))
        photo_id = self.store.add(image)
        result = self.engine.analyze()
        self.assertEqual(self.shares(result), {"#FF0000": .75, "#0000FF": .25})
        metrics = result["periods"][0]["metrics"]
        self.assertAlmostEqual(metrics["brightness"], .75 * .2126 + .25 * .0722, places=5)
        self.assertEqual(metrics["saturation"], 1)
        self.assertEqual(metrics["warmth"], .75)
        self.assertEqual(result["periods"][0]["photo_ids"], [photo_id])
        self.assertEqual(result["periods"][0]["representative_ids"], [photo_id])
        self.assertEqual(result, self.engine.analyze())
        self.assertEqual(result["analyzed_photos"], 1)
        self.assertEqual(result["skipped_photos"], 0)

    def test_photos_are_equal_weight_despite_different_dimensions(self):
        self.solid("red", (1200, 800))
        self.solid("blue", (6, 4))
        result = self.engine.analyze()
        self.assertEqual(self.shares(result), {"#FF0000": .5, "#0000FF": .5})
        self.assertEqual(result["periods"][0]["metrics"]["warmth"], .5)

    def test_grayscale_black_white_and_empty_library(self):
        empty = self.engine.analyze()
        self.assertEqual(empty["periods"], [])
        self.assertEqual(empty["total_photos"], 0)
        for color in ("black", "white", (128, 128, 128)):
            self.solid(color)
        result = self.engine.analyze()
        self.assertEqual(set(self.shares(result)), {"#000000", "#FFFFFF", "#808080"})
        metrics = result["periods"][0]["metrics"]
        self.assertEqual(metrics["saturation"], 0)
        self.assertEqual(metrics["warmth"], .5)
        self.assertAlmostEqual(metrics["brightness"], (0 + 1 + 128 / 255) / 3, places=5)

    def test_month_quarter_year_order_and_undated_groups(self):
        self.solid("red", taken_at="2025-12-31T23:59:59")
        self.solid("blue", taken_at="2026-01-01T00:00:00")
        no_date = self.solid("white", taken_at="")
        malformed = self.solid("black", taken_at="2025-19-99")
        expected = {"month": ["2026-01", "2025-12", "undated"], "quarter": ["2026-Q1", "2025-Q4", "undated"], "year": ["2026", "2025", "undated"]}
        for period, keys in expected.items():
            with self.subTest(period=period):
                result = self.engine.analyze(period)
                self.assertEqual([group["key"] for group in result["periods"]], keys)
                self.assertEqual(result["undated_photos"], 2)
                self.assertEqual(set(result["periods"][-1]["photo_ids"]), {no_date, malformed})
                self.assertEqual(result["periods"][-1]["label"], "未注明日期")

    def test_metadata_favorites_and_deletion_are_fresh_without_resampling(self):
        photo_id = self.solid("orange")
        with patch.object(self.engine, "_sample", wraps=self.engine._sample) as sample:
            self.engine.analyze()
            self.assertEqual(sample.call_count, 1)
            self.assertEqual(self.engine.analyze(scope="favorites")["total_photos"], 0)
            self.store.change(photo_id, taken_at="2023-02-01T12:00:00", favorite=1)
            selected = self.engine.analyze(scope="favorites")
            self.assertEqual(selected["total_photos"], 1)
            self.assertEqual(selected["periods"][0]["key"], "2023-02")
            self.assertEqual(sample.call_count, 1)
            with self.store.connect() as database:
                database.execute("DELETE FROM photos WHERE id=?", (photo_id,))
            self.assertEqual(self.engine.analyze()["periods"], [])
            self.assertEqual(sample.call_count, 1)

    def test_missing_and_corrupt_previews_are_counted_even_after_cache_warming(self):
        valid = self.solid("red")
        missing = self.solid("green", taken_at="2024-03-01T12:00:00")
        corrupt = self.solid("blue", taken_at="2024-03-02T12:00:00")
        self.assertEqual(self.engine.analyze()["analyzed_photos"], 3)
        (self.store.previews / (missing + ".jpg")).unlink()
        (self.store.previews / (corrupt + ".jpg")).write_bytes(b"not an image")
        result = self.engine.analyze()
        self.assertEqual((result["total_photos"], result["analyzed_photos"], result["skipped_photos"]), (3, 1, 2))
        self.assertEqual(result["periods"][0]["representative_ids"], [valid])
        skipped = result["periods"][1]
        self.assertEqual(skipped["photo_count"], 2)
        self.assertEqual(set(skipped["photo_ids"]), {missing, corrupt})
        self.assertEqual(skipped["palette"], [])
        self.assertEqual(skipped["representative_ids"], [])
        self.assertEqual(skipped["metrics"], dict.fromkeys(color_analysis.METRIC_NAMES))

    def test_color_cards_match_contributing_photos_and_have_useful_representatives(self):
        red, blue, white = self.solid("red"), self.solid("blue"), self.solid("white")
        group = self.engine.analyze()["periods"][0]
        by_color = {color["hex"]: color for color in group["palette"]}
        for color, photo_id in (("#FF0000", red), ("#0000FF", blue), ("#FFFFFF", white)):
            self.assertEqual(by_color[color]["photo_ids"], [photo_id])
        self.assertEqual(set(group["representative_ids"]), {red, blue, white})
        self.assertAlmostEqual(sum(color["share"] for color in group["palette"]), 1)

    def test_palette_is_bounded_for_many_distinct_colors(self):
        for color in ("red", "green", "blue", "white", "black", "yellow", "cyan", "magenta", "orange", "purple"):
            self.solid(color)
        result = self.engine.analyze()
        group = result["periods"][0]
        self.assertLessEqual(len(group["palette"]), 5)
        self.assertTrue(all(color["photo_ids"] for color in group["palette"]))
        self.assertAlmostEqual(sum(color["share"] for color in group["palette"]), 1, places=6)
        self.assertTrue(all(0 <= value <= 1 for value in group["metrics"].values()))
        self.assertEqual(result, self.engine.analyze())

    def test_cache_survives_restart_and_invalidates_on_content_version_and_preview_change(self):
        photo_id = self.solid("red")
        self.engine.analyze()
        reopened = ColorAnalysis(self.store)
        with patch.object(reopened, "_sample", wraps=reopened._sample) as sample:
            self.assertEqual(self.shares(reopened.analyze()), {"#FF0000": 1})
            self.assertEqual(sample.call_count, 0)
            self.store.change(photo_id, sha256="a" * 64)
            reopened.analyze()
            self.assertEqual(sample.call_count, 1)
            with patch.object(color_analysis, "ANALYSIS_VERSION", "next-algorithm"):
                reopened.analyze()
                self.assertEqual(sample.call_count, 2)
            preview = self.store.previews / (photo_id + ".jpg")
            previous = preview.stat()
            Image.new("RGB", (60, 40), "blue").save(preview, "PNG")
            os.utime(preview, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
            self.assertEqual(self.shares(reopened.analyze()), {"#0000FF": 1})
            self.assertEqual(sample.call_count, 3)
        reopened.close()

    def test_corrupt_cache_database_or_entry_is_recomputable(self):
        self.solid("red")
        self.engine.cache_path.write_bytes(b"not sqlite")
        with patch.object(self.engine, "_sample", wraps=self.engine._sample) as sample:
            self.assertEqual(self.shares(self.engine.analyze()), {"#FF0000": 1})
            self.engine.analyze()
            self.assertEqual(sample.call_count, 1)
        self.engine.close()
        self.engine.cache_path.unlink()
        self.engine.analyze()
        with closing(sqlite3.connect(self.engine.cache_path)) as database, database:
            database.execute("UPDATE color_samples SET stats_json=?", (json.dumps({"swatches": [[255, 0, 0, 1]], "metrics": {"brightness": -4, "saturation": 1, "warmth": 1}}),))
        self.engine.close()
        self.assertTrue(0 <= self.engine.analyze()["periods"][0]["metrics"]["brightness"] <= 1)

    def test_embedded_icc_is_converted_to_srgb_and_transparency_is_white(self):
        lab_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
        self.store.add(Image.new("LAB", (60, 40), (128, 128, 128)), format="TIFF", icc_profile=lab_profile)
        group = self.engine.analyze()["periods"][0]
        self.assertEqual(group["photo_count"], 1)
        self.assertEqual(len(group["palette"]), 1)
        color = group["palette"][0]["hex"]
        channels = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
        self.assertLess(max(channels) - min(channels), 3)
        self.assertTrue(100 <= channels[0] <= 140)
        self.assertLess(group["metrics"]["saturation"], .03)
        self.assertAlmostEqual(group["metrics"]["warmth"], .5, places=2)
        self.store.add(Image.new("RGBA", (100, 60), (255, 0, 0, 0)), taken_at="2026-01-01")
        latest = self.engine.analyze()["periods"][0]
        self.assertEqual(latest["palette"][0]["hex"], "#FFFFFF")
        self.assertEqual(latest["metrics"], {"brightness": 1, "saturation": 0, "warmth": .5})

    def test_invalid_requests_have_http_friendly_errors(self):
        for period, scope in (("day", "all"), ("month", "private"), ([], "all"), ("year", None)):
            with self.subTest(period=period, scope=scope):
                with self.assertRaises(ColorAnalysisError) as caught:
                    self.engine.analyze(period, scope)
                self.assertEqual(caught.exception.status, 400)
                self.assertTrue(caught.exception.message)
        self.assertFalse(self.engine.cache_path.exists())

    def test_srgb_profile_preserves_rgb_and_bad_profile_only_skips_that_photo(self):
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        photo_id = self.store.add(Image.new("RGB", (60, 40), "red"), icc_profile=srgb)
        self.store.add(Image.new("RGB", (60, 40), "blue"), icc_profile=b"invalid ICC profile")
        result = self.engine.analyze()
        self.assertEqual((result["analyzed_photos"], result["skipped_photos"]), (1, 1))
        self.assertEqual(self.shares(result), {"#FF0000": 1})
        self.assertEqual(result["periods"][0]["representative_ids"], [photo_id])

    def test_concurrent_queries_share_bounded_single_photo_analysis(self):
        self.solid("red")
        entered, release = threading.Event(), threading.Event()
        original = self.engine._sample
        results, errors = [], []

        def slow_sample(path):
            entered.set()
            if not release.wait(2):
                raise AssertionError("analysis was not released")
            return original(path)

        def analyze():
            try:
                results.append(self.engine.analyze())
            except Exception as error:
                errors.append(error)

        with patch.object(self.engine, "_sample", side_effect=slow_sample) as sample:
            first, second = threading.Thread(target=analyze), threading.Thread(target=analyze)
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            release.set()
            first.join(3)
            second.join(3)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0], results[1])
            self.assertEqual(sample.call_count, 1)

    def test_pixel_analysis_does_not_hold_album_write_lock(self):
        self.solid("red")
        original = self.engine._sample
        acquired = threading.Event()

        def checked(path):
            def mutation():
                with self.store.lock:
                    acquired.set()
            thread = threading.Thread(target=mutation)
            thread.start()
            self.assertTrue(acquired.wait(1))
            thread.join(1)
            return original(path)

        with patch.object(self.engine, "_sample", checked):
            self.assertEqual(self.engine.analyze()["analyzed_photos"], 1)


if __name__ == "__main__":
    unittest.main()
