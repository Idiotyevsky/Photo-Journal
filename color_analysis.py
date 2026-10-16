"""Deterministic, local-only color summaries of the album's managed previews.

Each decoded photo contributes one equal vote, regardless of its dimensions.
RGB bins (32-value-wide bins per channel) merge with weighted, deterministic color
clustering. The cache contains derived pixel statistics only; dates, favorites
and library membership are read afresh for every request.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import threading

try:
    from PIL import Image, ImageCms, ImageOps
except ImportError:
    Image = ImageCms = ImageOps = None


ANALYSIS_VERSION = "srgb-rgb32-equal96-v1"
SAMPLE_SIZE = 96
MAX_PREVIEW_BYTES = 20 * 1024 * 1024
MAX_PREVIEW_PIXELS = 20_000_000
METRIC_NAMES = ("brightness", "saturation", "warmth")


class ColorAnalysisError(ValueError):
    def __init__(self, message: str, status: int = 400):
        self.status, self.message = status, message
        super().__init__(message)


def _distance(left, right):
    red, green, blue = left[0] - right[0], left[1] - right[1], left[2] - right[2]
    return red * red + green * green + blue * blue


def _nearest(rgb, centers):
    return min(range(len(centers)), key=lambda index: (_distance(rgb, centers[index]), index))


def _cluster(points, maximum):
    """Weighted RGB clustering with reproducible seeding and near-color merge."""
    if not points:
        return []
    points = sorted(points, key=lambda point: (-point[3], *point[:3]))
    centers = [tuple(points[0][:3])]
    while len(centers) < min(maximum, len(points)):
        candidate = max(points, key=lambda point: (
            min(_distance(point, center) for center in centers) * math.sqrt(point[3]),
            point[3], tuple(-value for value in point[:3]),
        ))
        if min(_distance(candidate, center) for center in centers) < 24 ** 2:
            break
        centers.append(tuple(candidate[:3]))
    for _ in range(10):
        sums = [[0.0] * 4 for _ in centers]
        for point in points:
            total = sums[_nearest(point, centers)]
            for channel in range(3):
                total[channel] += point[channel] * point[3]
            total[3] += point[3]
        updated = [tuple(total[channel] / total[3] for channel in range(3))
                   for total in sums if total[3]]
        if len(updated) == len(centers) and all(_distance(a, b) < .01 for a, b in zip(updated, centers)):
            centers = updated
            break
        centers = updated
    # Nearby bins should not turn one visually continuous color into five cards.
    while len(centers) > 1:
        pairs = [(_distance(centers[a], centers[b]), a, b)
                 for a in range(len(centers)) for b in range(a + 1, len(centers))]
        distance, first, second = min(pairs)
        if distance >= 28 ** 2:
            break
        centers[first] = tuple((centers[first][channel] + centers[second][channel]) / 2 for channel in range(3))
        centers.pop(second)
    totals = [[0.0] * 4 for _ in centers]
    for point in points:
        total = totals[_nearest(point, centers)]
        for channel in range(3):
            total[channel] += point[channel] * point[3]
        total[3] += point[3]
    return sorted([tuple(total[channel] / total[3] for channel in range(3)) + (total[3],)
                   for total in totals if total[3]], key=lambda point: (-point[3], *point[:3]))


def _add_bin(bins, rgb, weight):
    key = tuple(min(7, max(0, int(channel) // 32)) for channel in rgb)
    total = bins.setdefault(key, [0.0] * 4)
    for channel in range(3):
        total[channel] += rgb[channel] * weight
    total[3] += weight


def _bin_points(bins):
    return [tuple(total[channel] / total[3] for channel in range(3)) + (total[3],)
            for _, total in sorted(bins.items()) if total[3]]


def _period(value, period):
    try:
        taken = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return "undated", "未注明日期"
    if period == "year":
        return f"{taken.year:04d}", f"{taken.year}年"
    if period == "quarter":
        quarter = (taken.month - 1) // 3 + 1
        return f"{taken.year:04d}-Q{quarter}", f"{taken.year}年第{quarter}季度"
    return f"{taken.year:04d}-{taken.month:02d}", f"{taken.year}年{taken.month}月"


class ColorAnalysis:
    def __init__(self, store):
        self.store = store
        self.root = Path(store.root).resolve()
        self.cache_path = self.root / ".color-cache.sqlite3"
        self._lock = threading.RLock()
        self._memory = OrderedDict()

    def close(self):
        """No persistent SQLite connection or background worker to stop."""
        with self._lock:
            self._memory.clear()

    @contextmanager
    def _cache_database(self):
        database = None
        try:
            if self.cache_path.is_symlink() or getattr(self.cache_path, "is_junction", lambda: False)():
                raise OSError("unsafe cache path")
            database = sqlite3.connect(self.cache_path, timeout=1)
            database.execute("CREATE TABLE IF NOT EXISTS color_samples (cache_key TEXT PRIMARY KEY, signature TEXT NOT NULL, stats_json TEXT NOT NULL)")
        except (OSError, sqlite3.Error):
            if database is not None:
                database.close()
            database = None
        try:
            yield database
        finally:
            if database is not None:
                try:
                    database.commit()
                except sqlite3.Error:
                    pass
                database.close()

    def _snapshot(self):
        # One short lock and one SELECT give a coherent date/favorite/hash view.
        with self.store.lock, self.store.connect() as database:
            return [dict(row) for row in database.execute(
                "SELECT id,sha256,taken_at,favorite FROM photos ORDER BY taken_at DESC, id"
            ).fetchall()]

    def _preview(self, photo):
        if not isinstance(photo["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", photo["id"]):
            raise ValueError("invalid photo id")
        directory = Path(self.store.previews)
        path = directory / (photo["id"] + ".jpg")
        with self.store.lock:
            if any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
                   for part in (directory, path)):
                raise OSError("unsafe preview")
            resolved = path.resolve(strict=True)
            if resolved.parent != directory.resolve() or not resolved.is_relative_to(self.root) or not resolved.is_file():
                raise OSError("unsafe preview")
            stat = resolved.stat()
            if not 0 < stat.st_size <= MAX_PREVIEW_BYTES:
                raise ValueError("oversized or empty preview")
            return resolved, f"{stat.st_size}:{stat.st_mtime_ns}"

    @staticmethod
    def _valid_stats(stats):
        if not isinstance(stats, dict) or set(stats) != {"swatches", "metrics"}:
            return False
        swatches, metrics = stats["swatches"], stats["metrics"]
        if not isinstance(swatches, list) or not 1 <= len(swatches) <= 8 or not isinstance(metrics, dict) or set(metrics) != set(METRIC_NAMES):
            return False
        for swatch in swatches:
            if not isinstance(swatch, list) or len(swatch) != 4 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in swatch):
                return False
            if any(not 0 <= value <= 255 for value in swatch[:3]) or not 0 < swatch[3] <= 1:
                return False
        if abs(sum(swatch[3] for swatch in swatches) - 1) > 1e-5:
            return False
        return all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in metrics.values())

    def _sample(self, path):
        # Copy a bounded preview while deletion is locked out, then do all color
        # management and decoding outside the album's mutation lock.
        with self.store.lock:
            with path.open("rb") as content:
                data = content.read(MAX_PREVIEW_BYTES + 1)
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError("oversized preview")
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_PREVIEW_PIXELS:
                raise ValueError("oversized preview dimensions")
            image = ImageOps.exif_transpose(source)
            alpha = image.convert("RGBA").getchannel("A") if "A" in image.getbands() or "transparency" in image.info else None
            profile = image.info.get("icc_profile")
            if profile:
                input_image = image if image.mode in {"RGB", "CMYK", "LAB", "L"} else image.convert("RGB")
                rgb = ImageCms.profileToProfile(input_image, ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
            else:
                rgb = image.convert("RGB")
            if alpha is not None:
                opaque = Image.new("RGB", rgb.size, "white")
                opaque.paste(rgb, mask=alpha)
                rgb = opaque
            # Stretching the entire frame to an equal sampling area changes no
            # spatial area proportions, and never crops or adds letterbox pixels.
            sampled = rgb.resize((SAMPLE_SIZE, SAMPLE_SIZE), Image.Resampling.BOX)
            colors = sampled.getcolors(SAMPLE_SIZE * SAMPLE_SIZE)
        bins, metrics = {}, dict.fromkeys(METRIC_NAMES, 0.0)
        for count, rgb in colors:
            weight = count / (SAMPLE_SIZE * SAMPLE_SIZE)
            _add_bin(bins, rgb, weight)
            red, green, blue = (channel / 255 for channel in rgb)
            maximum, minimum = max(red, green, blue), min(red, green, blue)
            metrics["brightness"] += weight * (.2126 * red + .7152 * green + .0722 * blue)
            metrics["saturation"] += weight * ((maximum - minimum) / maximum if maximum else 0)
            metrics["warmth"] += weight * (.5 + .5 * (red - blue))
        swatches = [list(point) for point in _cluster(_bin_points(bins), 8)]
        total_share = sum(swatch[3] for swatch in swatches)
        for swatch in swatches:
            swatch[3] /= total_share
        return {"swatches": swatches, "metrics": {key: min(1.0, max(0.0, value)) for key, value in metrics.items()}}

    def _photo_stats(self, photo, database):
        path, signature = self._preview(photo)
        fingerprint = photo["sha256"]
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ValueError("invalid original content hash")
        key = ANALYSIS_VERSION + ":" + fingerprint
        cached = self._memory.get(key)
        if cached and cached[0] == signature:
            self._memory.move_to_end(key)
            return cached[1]
        if database is not None:
            try:
                row = database.execute("SELECT stats_json FROM color_samples WHERE cache_key = ? AND signature = ?", (key, signature)).fetchone()
                if row:
                    stats = json.loads(row[0])
                    if self._valid_stats(stats):
                        self._remember(key, signature, stats)
                        return stats
            except (sqlite3.Error, ValueError, TypeError):
                pass
        stats = self._sample(path)
        self._remember(key, signature, stats)
        if database is not None:
            try:
                database.execute("INSERT OR REPLACE INTO color_samples VALUES(?,?,?)", (key, signature, json.dumps(stats, separators=(",", ":"), allow_nan=False)))
            except sqlite3.Error:
                pass
        return stats

    def _remember(self, key, signature, stats):
        self._memory[key] = signature, stats
        self._memory.move_to_end(key)
        while len(self._memory) > 512:
            self._memory.popitem(last=False)

    @staticmethod
    def _summarize(key, label, photos, samples):
        bins = {}
        for _, stats in samples:
            for red, green, blue, share in stats["swatches"]:
                _add_bin(bins, (red, green, blue), share)
        centers = _cluster(_bin_points(bins), 5)
        palette, contribution = [], {}
        for photo_id, stats in samples:
            shares = [0.0] * len(centers)
            for swatch in stats["swatches"]:
                shares[_nearest(swatch, centers)] += swatch[3]
            contribution[photo_id] = shares
        for index, center in enumerate(centers):
            ranked = sorted(contribution, key=lambda photo_id: (-contribution[photo_id][index], photo_id))
            contributors = [photo_id for photo_id in ranked if contribution[photo_id][index] >= .12 or contribution[photo_id][index] == max(contribution[photo_id])]
            if not contributors and ranked:
                contributors = ranked[:1]
            palette.append({"hex": "#" + "".join(f"{min(255, max(0, round(channel))):02X}" for channel in center[:3]),
                            "share": round(center[3] / len(samples), 6), "photo_ids": contributors})
        if palette:
            palette[-1]["share"] = round(1 - sum(color["share"] for color in palette[:-1]), 6)
        representatives = []
        for color in palette:
            representative = next((photo_id for photo_id in color["photo_ids"] if photo_id not in representatives), None)
            if representative:
                representatives.append(representative)
        metrics = {metric: round(sum(stats["metrics"][metric] for _, stats in samples) / len(samples), 6) if samples else None for metric in METRIC_NAMES}
        return {"key": key, "label": label, "photo_count": len(photos), "photo_ids": [photo["id"] for photo in photos],
                "palette": palette, "metrics": metrics, "representative_ids": representatives}

    def analyze(self, period="month", scope="all"):
        if not isinstance(period, str) or not isinstance(scope, str) or period not in {"month", "quarter", "year"} or scope not in {"all", "favorites"}:
            raise ColorAnalysisError("请选择按月、季度或年查看全部照片或收藏照片的色彩。")
        if Image is None or ImageCms is None:
            raise ColorAnalysisError("色彩分析需要本地 Pillow 图像组件。", 503)
        with self._lock, self._cache_database() as database:
            photos = [photo for photo in self._snapshot() if scope == "all" or photo["favorite"]]
            groups = {}
            for photo in photos:
                key, label = _period(photo["taken_at"], period)
                groups.setdefault(key, {"label": label, "photos": []})["photos"].append(photo)
            result = {"period": period, "scope": scope, "total_photos": len(photos), "analyzed_photos": 0,
                      "skipped_photos": 0, "undated_photos": len(groups.get("undated", {}).get("photos", [])), "periods": []}
            keys = sorted((key for key in groups if key != "undated"), reverse=True)
            if "undated" in groups:
                keys.append("undated")
            for key in keys:
                group, samples = groups[key], []
                for photo in group["photos"]:
                    try:
                        samples.append((photo["id"], self._photo_stats(photo, database)))
                    except (OSError, ValueError, RuntimeError, SyntaxError, Image.DecompressionBombError, ImageCms.PyCMSError):
                        result["skipped_photos"] += 1
                result["analyzed_photos"] += len(samples)
                result["periods"].append(self._summarize(key, group["label"], group["photos"], samples))
            return result
