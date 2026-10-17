#!/usr/bin/env python3
"""A private, loopback-only photo album. Original photos never leave this computer."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import tempfile
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit
import uuid
import zipfile

from video_jobs import VideoJobError, VideoJobManager
from color_analysis import ColorAnalysis, ColorAnalysisError
from online_music import OnlineMusic, OnlineMusicError

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:
    Image = ImageOps = None
    UnidentifiedImageError = OSError


ROOT = Path(__file__).resolve().parent
MAX_PHOTO_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_ATLAS_JSON_BYTES = 3 * 1024 * 1024
MAX_MUSIC_BYTES = 100 * 1024 * 1024
AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac"}
AUDIO_IMPORT_TYPES = {"application/octet-stream", *AUDIO_MIME.values(), "audio/x-wav", "audio/wave", "audio/x-m4a", "audio/x-flac", "application/ogg"}
# Some cameras store a primary JPEG plus another image in an MPO container.
# Keep that original intact at the usual JPEG path; preview its first frame.
FORMATS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp", "TIFF": ".tif"}
IMPORT_TYPES = {"application/octet-stream", "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "image/bmp", "image/x-ms-bmp", "image/tiff"}
CAPTURE_FIELDS = ("aperture", "shutter", "iso", "focal_length", "exposure_bias")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_text(value, limit: int, field: str = "文字") -> str:
    if not isinstance(value, str):
        raise ApiError(400, f"{field}格式不正确。")
    if len(value) > limit:
        raise ApiError(400, f"{field}过长，最多可输入 {limit} 个字符。")
    return value.strip().replace("\x00", "")


def journal_key(value) -> str:
    key = clean_text(value, 650, "手记标识")
    if not key.startswith(("trip:", "month:")) or not key.split(":", 1)[1]:
        raise ApiError(400, "手记标识格式不正确。")
    return key


def country_code(value) -> str:
    country = clean_text("" if value is None else value, 2, "国家代码")
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ApiError(400, "国家代码应为空或两个大写英文字母，例如 CN、FR、GB。")
    return country


def city_name(value) -> str:
    return clean_text("" if value is None else value, 100, "城市名称")


def _mp3_frame(data: bytes, offset: int) -> tuple[int, int, int] | None:
    """Return the complete Layer III frame's size, MPEG version and sample rate."""
    if offset < 0 or offset + 4 > len(data):
        return None
    header = int.from_bytes(data[offset:offset + 4], "big")
    version, layer = (header >> 19) & 3, (header >> 17) & 3
    bitrate_index, sample_index = (header >> 12) & 15, (header >> 10) & 3
    if header >> 21 != 0x7FF or version == 1 or layer != 1 or bitrate_index in (0, 15) or sample_index == 3:
        return None
    bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if version == 3 else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    frequency = (44100, 48000, 32000)[sample_index] // (1 if version == 3 else 2 if version == 2 else 4)
    frame_size = (144000 if version == 3 else 72000) * bitrates[bitrate_index] // frequency + ((header >> 9) & 1)
    if frame_size < 4 or offset + frame_size > len(data):
        return None
    return frame_size, version, frequency


def audio_header_valid(data: bytes, extension: str) -> bool:
    """Check the actual audio/container header without executing an external decoder."""
    if extension == ".mp3":
        offset = 0
        tagged = data.startswith(b"ID3")
        if tagged:
            allowed_flags = {2: 0xC0, 3: 0xE0, 4: 0xF0}
            if len(data) < 10 or data[3] not in allowed_flags or data[4] == 255 or data[5] & ~allowed_flags[data[3]] or any(value & 128 for value in data[6:10]):
                return False
            offset = 10 + sum(value << shift for value, shift in zip(data[6:10], (21, 14, 7, 0)))
            if data[3] == 4 and data[5] & 16:
                offset += 10
            if offset > len(data):
                return False
        # Preserve exact-position compatibility, including short one-frame files.
        if _mp3_frame(data, offset) is not None:
            return True
        if not tagged:
            return False
        # Some tag writers leave padding outside the declared ID3 size. Search
        # at most 64 KiB, requiring three adjacent complete frames to avoid
        # accepting an accidental sync word. Bitrate/channel/padding may vary.
        scan_end = min(len(data) - 4, offset + 64 * 1024)
        candidate = data.find(b"\xff", offset + 1, scan_end + 1)
        while candidate != -1:
            position, signature, complete = candidate, None, 0
            for _ in range(3):
                frame = _mp3_frame(data, position)
                if frame is None or (signature is not None and frame[1:] != signature):
                    break
                signature = frame[1:]
                position += frame[0]
                complete += 1
            if complete == 3:
                return True
            candidate = data.find(b"\xff", candidate + 1, scan_end + 1)
        return False
    if extension == ".wav":
        if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return False
        limit = int.from_bytes(data[4:8], "little") + 8
        if limit > len(data):
            return False
        position, have_format, have_samples = 12, False, False
        while position + 8 <= limit:
            kind, length = data[position:position + 4], int.from_bytes(data[position + 4:position + 8], "little")
            start = position + 8
            if start + length > limit:
                return False
            if kind == b"fmt " and length >= 16:
                codec, channels, rate = struct.unpack_from("<HHI", data, start)
                have_format = codec > 0 and 0 < channels <= 32 and rate > 0
            if kind == b"data" and length > 0:
                have_samples = True
            position = start + length + (length & 1)
        return have_format and have_samples
    if extension == ".aac":
        if len(data) < 7 or data[0] != 255 or data[1] & 0xF6 != 0xF0 or (data[2] >> 2) & 15 == 15:
            return False
        length = ((data[3] & 3) << 11) | (data[4] << 3) | (data[5] >> 5)
        return (7 if data[1] & 1 else 9) < length <= len(data)
    if extension == ".ogg":
        if len(data) < 28 or data[:5] != b"OggS\x00":
            return False
        segments = data[26]
        start = 27 + segments
        if not segments or start > len(data) or start + sum(data[27:start]) > len(data):
            return False
        return data[start:].startswith((b"\x01vorbis", b"OpusHead", b"\x7fFLAC"))
    if extension == ".flac":
        if len(data) < 44 or data[:4] != b"fLaC" or data[4] & 127 != 0 or int.from_bytes(data[5:8], "big") != 34:
            return False
        position = 4
        while position + 4 <= len(data):
            last = bool(data[position] & 128)
            length = int.from_bytes(data[position + 1:position + 4], "big")
            position += 4 + length
            if position > len(data):
                return False
            if last:
                return position + 2 <= len(data) and data[position] == 255 and data[position + 1] & 0xFE == 0xF8
        return False
    if extension == ".m4a":
        position, valid_type, audio_handler, samples = 0, False, False, False
        while position + 8 <= len(data):
            length, kind = int.from_bytes(data[position:position + 4], "big"), data[position + 4:position + 8]
            header_size = 8
            if length == 1:
                if position + 16 > len(data):
                    return False
                length, header_size = int.from_bytes(data[position + 8:position + 16], "big"), 16
            elif length == 0:
                length = len(data) - position
            if length < header_size or position + length > len(data):
                return False
            if kind == b"ftyp":
                body = data[position + header_size:position + length]
                valid_type = len(body) >= 8 and any(brand in body for brand in (b"M4A ", b"M4B ", b"isom", b"iso2", b"mp41", b"mp42", b"qt  "))
            elif kind == b"moov":
                audio_handler = data.find(b"soun", position + header_size, position + length) >= 0
            elif kind == b"mdat" and length > header_size:
                samples = True
            position += length
        return valid_type and audio_handler and samples
    return False


def local_date(value: str, field: str = "拍摄时间") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, f"请填写有效的{field}。")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed.isoformat(timespec="seconds")
    except (ValueError, OverflowError):
        raise ApiError(400, f"{field}格式不正确。") from None


def exif_text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "").strip().replace("\x00", "")[:500]


def gps_coord(value, reference, negative) -> float | None:
    try:
        degrees, minutes, seconds = (float(part) for part in value)
        result = degrees + minutes / 60 + seconds / 3600
        if exif_text(reference).upper() in negative:
            result = -result
        return round(result, 7) if math.isfinite(result) else None
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None


def exif_number(value) -> float | None:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None


def display_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def capture_settings(exif, details) -> dict:
    def number(tag):
        return exif_number(details.get(tag, exif.get(tag)))

    aperture, shutter = number(33437), number(33434)
    iso, focal, bias = number(34855), number(37386), number(37380)
    return {
        "aperture": f"f/{display_number(aperture)}" if aperture is not None and aperture > 0 else None,
        "shutter": (f"1/{display_number(1 / shutter)} s" if shutter < 1 else f"{display_number(shutter)} s") if shutter is not None and shutter > 0 else None,
        "iso": display_number(iso) if iso is not None and iso > 0 else None,
        "focal_length": f"{display_number(focal)} mm" if focal is not None and focal > 0 else None,
        "exposure_bias": f"{'+' if bias > 0 else ''}{display_number(bias)} EV" if bias is not None else None,
    }


def image_metadata(data: bytes, modified: str | None) -> tuple[dict, bytes, str]:
    if Image is None:
        raise ApiError(503, "照片处理需要 Pillow，请使用项目提供的启动方式运行相册。")
    taken_at = local_date(modified, "文件时间") if modified else now()
    info = {"taken_at": taken_at, "date_source": "file", "camera": "", "lens": "", "lat": None, "lon": None}
    try:
        with Image.open(io.BytesIO(data)) as source:
            extension = FORMATS.get(source.format)
            if not extension:
                raise ApiError(415, "暂时支持 JPG、PNG、WebP、GIF、BMP 和 TIFF 照片。")
            if source.width * source.height > 80_000_000:
                raise ApiError(413, "这张照片的像素过大，请导入不超过 8000 万像素的版本。")
            exif = source.getexif()
            try:
                details = exif.get_ifd(34665)
            except (AttributeError, ValueError, TypeError, SyntaxError):
                details = {}
            for tag in (36867, 36868, 306):
                stamp = exif_text(details.get(tag) or exif.get(tag))
                try:
                    info["taken_at"] = datetime.strptime(stamp[:19], "%Y:%m:%d %H:%M:%S").isoformat(timespec="seconds")
                    info["date_source"] = "exif"
                    break
                except ValueError:
                    pass
            make, model = exif_text(exif.get(271)), exif_text(exif.get(272))
            info["camera"] = model if make.lower() in model.lower() else " ".join(filter(None, (make, model)))
            info["lens"] = exif_text(details.get(42036) or exif.get(42036))
            info.update(capture_settings(exif, details))
            try:
                gps = exif.get_ifd(34853)
                lat = gps_coord(gps.get(2), gps.get(1), {"S"})
                lon = gps_coord(gps.get(4), gps.get(3), {"W"})
                if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                    info["lat"], info["lon"] = lat, lon
            except (AttributeError, ValueError, TypeError, SyntaxError):
                pass
            # The original remains byte-for-byte intact; only the display copy is rotated.
            # RGB previews keep their source color space so color-managed browsers
            # can render Adobe RGB and other embedded RGB profiles faithfully.
            # A CMYK/gray profile must not be attached after a plain RGB conversion.
            icc_profile = source.info.get("icc_profile") if source.mode in {"RGB", "RGBA", "P"} else None
            source.seek(0)
            upright = ImageOps.exif_transpose(source)
            info["width"], info["height"] = upright.size
            upright.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            if upright.mode in ("RGBA", "LA") or (upright.mode == "P" and "transparency" in upright.info):
                rgba = upright.convert("RGBA")
                preview = Image.new("RGB", rgba.size, "white")
                preview.paste(rgba, mask=rgba.getchannel("A"))
            else:
                preview = upright.convert("RGB")
            buffer = io.BytesIO()
            save_options = {"icc_profile": icc_profile} if isinstance(icc_profile, bytes) else {}
            preview.save(buffer, "JPEG", quality=88, optimize=True, **save_options)
            return info, buffer.getvalue(), extension
    except ApiError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise ApiError(400, "无法读取这张照片，文件可能已损坏或格式不受支持。") from None


class AlbumStore:
    def __init__(self, data_dir: Path, web_dir: Path = ROOT / "ui"):
        self.root = Path(data_dir).resolve()
        self.web_dir = Path(web_dir).resolve()
        self.originals = self.root / "originals"
        self.previews = self.root / "previews"
        self.music_dir = self.root / "music"
        self.delete_staging = self.root / ".delete-staging"
        self.originals.mkdir(parents=True, exist_ok=True)
        self.previews.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "album.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS photos (
                    id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL, extension TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '', caption TEXT NOT NULL DEFAULT '',
                    place TEXT NOT NULL DEFAULT '', trip TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '',
                    taken_at TEXT NOT NULL, date_source TEXT NOT NULL,
                    imported_at TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                    camera TEXT NOT NULL DEFAULT '', lens TEXT NOT NULL DEFAULT '',
                    aperture TEXT, shutter TEXT, iso TEXT, focal_length TEXT, exposure_bias TEXT,
                    lat REAL, lon REAL, favorite INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS journals (
                    key TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS journal_deletions (
                    key TEXT PRIMARY KEY, deleted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', place TEXT NOT NULL DEFAULT '',
                    cover_photo_id TEXT REFERENCES photos(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_photos (
                    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    photo_id TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                    added_at TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(collection_id, photo_id)
                );
                CREATE INDEX IF NOT EXISTS collection_photos_by_photo ON collection_photos(photo_id);
                CREATE TABLE IF NOT EXISTS collection_routes (
                    collection_id TEXT PRIMARY KEY REFERENCES collections(id) ON DELETE CASCADE,
                    stops_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS atlas_route (
                    key TEXT PRIMARY KEY, stops_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS music_tracks (
                    id TEXT PRIMARY KEY, origin TEXT NOT NULL,
                    title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
                    original_name TEXT NOT NULL DEFAULT '', extension TEXT NOT NULL DEFAULT '',
                    sha256 TEXT UNIQUE, size INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1, removed INTEGER NOT NULL DEFAULT 0,
                    imported_at TEXT NOT NULL
                );
            """)
            # Existing albums keep their rows and originals; new read-only EXIF
            # fields start as NULL until a photo is imported with those settings.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(photos)")}
            for name in CAPTURE_FIELDS:
                if name not in columns:
                    db.execute(f"ALTER TABLE photos ADD COLUMN {name} TEXT")
            for name in ("country", "city"):
                if name not in columns:
                    db.execute(f"ALTER TABLE photos ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            member_columns = {row["name"] for row in db.execute("PRAGMA table_info(collection_photos)")}
            if "position" not in member_columns:
                if not db.in_transaction:
                    db.execute("BEGIN")
                db.execute("ALTER TABLE collection_photos ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
                # Keep the previous gallery's newest-first presentation when an
                # existing album gains its first explicit portfolio order.
                members = db.execute("SELECT cp.collection_id,cp.photo_id FROM collection_photos cp JOIN photos p ON p.id=cp.photo_id ORDER BY cp.collection_id,p.taken_at DESC,p.imported_at DESC,p.id").fetchall()
                positions = {}
                for member in members:
                    position = positions.get(member["collection_id"], 0)
                    db.execute("UPDATE collection_photos SET position=? WHERE collection_id=? AND photo_id=?", (position, member["collection_id"], member["photo_id"]))
                    positions[member["collection_id"]] = position + 1
        from album_reset import recover
        recover(self)
        self.originals.mkdir(exist_ok=True)
        self.previews.mkdir(exist_ok=True)
        self.recover_deletions()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def settings_record(db) -> dict:
        result = {"theme": "classic", "music_volume": 0.22}
        row = db.execute("SELECT value_json FROM app_settings WHERE key = 'default'").fetchone()
        if row:
            try:
                stored = json.loads(row["value_json"])
                if isinstance(stored, dict):
                    if stored.get("theme") in {"classic", "italian"}:
                        result["theme"] = stored["theme"]
                    volume = stored.get("music_volume")
                    if not isinstance(volume, bool) and isinstance(volume, (int, float)) and math.isfinite(volume) and 0 <= volume <= 1:
                        result["music_volume"] = volume
            except (TypeError, ValueError, OverflowError):
                pass
        return result

    def settings(self) -> dict:
        with self.connect() as db:
            return self.settings_record(db)

    def opening_lines_record(self, db) -> list[dict]:
        row = db.execute("SELECT value_json FROM app_settings WHERE key = 'opening-lines'").fetchone()
        if row:
            try:
                lines = json.loads(row['value_json'])
                if not isinstance(lines, list) or len(lines) > 1000:
                    raise ValueError
                ids = set()
                for line in lines:
                    if not isinstance(line, dict) or set(line) != {'id', 'text', 'origin'} or not isinstance(line['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', line['id']) or line['id'] in ids or line['origin'] not in {'builtin', 'custom'} or not isinstance(line['text'], str) or not 1 <= len(line['text']) <= 120:
                        raise ValueError
                    ids.add(line['id'])
                return lines
            except (TypeError, ValueError, KeyError):
                raise ApiError(500, '开场白资料暂时无法读取，请保留相册资料并重试。') from None
        source = self.web_dir / 'daily-quotes.json'
        if not source.is_file():
            source = ROOT / 'ui' / 'daily-quotes.json'
        texts = json.loads(source.read_text(encoding='utf-8'))
        return [{'id': f'builtin_{index:03d}', 'text': text, 'origin': 'builtin'} for index, text in enumerate(texts, 1)]

    def opening_lines(self) -> dict:
        with self.lock, self.connect() as db:
            return {'lines': self.opening_lines_record(db)}

    @staticmethod
    def save_opening_lines(db, lines):
        db.execute("INSERT INTO app_settings(key,value_json) VALUES('opening-lines',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(lines, ensure_ascii=False),))

    def add_opening_line(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or set(changes) != {'text'}:
            raise ApiError(400, '请填写一句开场白。')
        text = clean_text(changes['text'], 120, '开场白')
        if not text:
            raise ApiError(400, '开场白不能为空。')
        with self.lock, self.connect() as db:
            lines = self.opening_lines_record(db)
            if any(line['text'] == text for line in lines):
                raise ApiError(409, '这句开场白已经在库中。')
            if len(lines) >= 1000:
                raise ApiError(400, '开场白库最多保存 1000 句。')
            lines.append({'id': 'custom_' + uuid.uuid4().hex, 'text': text, 'origin': 'custom'})
            self.save_opening_lines(db, lines)
            return {'lines': lines}

    def delete_opening_line(self, line_id: str) -> dict:
        with self.lock, self.connect() as db:
            lines = self.opening_lines_record(db)
            remaining = [line for line in lines if line['id'] != line_id]
            if len(remaining) == len(lines):
                raise ApiError(404, '这句开场白已不存在。')
            self.save_opening_lines(db, remaining)
            return {'lines': remaining}

    def update_settings(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or set(changes) - {"theme", "music_volume"}:
            raise ApiError(400, "设置中包含不支持的字段。")
        if "theme" in changes and changes["theme"] not in ("classic", "italian"):
            raise ApiError(400, "请选择经典或意大利主题。")
        if "music_volume" in changes:
            volume = changes["music_volume"]
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                raise ApiError(400, "音乐音量应为 0 到 1 之间的数值。")
            try:
                valid = math.isfinite(volume) and 0 <= volume <= 1
            except OverflowError:
                valid = False
            if not valid:
                raise ApiError(400, "音乐音量应为 0 到 1 之间的数值。")
        with self.lock, self.connect() as db:
            result = {**self.settings_record(db), **changes}
            db.execute("INSERT INTO app_settings(key,value_json) VALUES('default',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(result, allow_nan=False),))
            return result

    def builtin_music(self) -> list[dict]:
        try:
            manifest = json.loads((self.web_dir / "audio" / "tracks.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        tracks, ids = [], set()
        for value in manifest.get("tracks", []) if isinstance(manifest, dict) else []:
            if not isinstance(value, dict):
                continue
            track_id, url = value.get("id"), value.get("url")
            if not isinstance(track_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", track_id) or track_id in ids:
                continue
            if not isinstance(url, str) or not re.fullmatch(r"/audio/[a-z0-9-]+\.mp3", url):
                continue
            ids.add(track_id)
            tracks.append({**value, "id": track_id, "title": str(value.get("title") or track_id), "artist": str(value.get("artist") or ""), "url": url, "origin": "builtin", "enabled": True})
        return tracks

    def music_records(self, db) -> list[dict]:
        rows = db.execute("SELECT * FROM music_tracks ORDER BY imported_at DESC,id").fetchall()
        overrides = {row["id"]: row for row in rows if row["origin"] == "builtin"}
        tracks = []
        for builtin in self.builtin_music():
            value = {key: item for key, item in builtin.items() if key != "sha256"}
            if builtin["id"] in overrides:
                value["title"] = overrides[builtin["id"]]["title"]
                value["enabled"] = bool(overrides[builtin["id"]]["enabled"])
            tracks.append(value)
        for row in rows:
            if row["origin"] == "imported" and not row["removed"]:
                tracks.append({"id": row["id"], "title": row["title"], "artist": row["artist"], "url": f"/media/music/{row['id']}{row['extension']}", "origin": "imported", "enabled": bool(row["enabled"]), "bytes": row["size"], "original_name": row["original_name"], "imported_at": row["imported_at"]})
        return tracks

    def music(self) -> dict:
        with self.connect() as db:
            return {"tracks": self.music_records(db)}

    def require_music(self, db, track_id: str) -> dict:
        track = next((track for track in self.music_records(db) if track["id"] == track_id), None)
        if not track:
            raise ApiError(404, "这首音乐不在曲库中。")
        return track

    def import_music(self, data: bytes, filename: str, *, title: str | None = None, artist: str = "") -> dict:
        if not data:
            raise ApiError(400, "音乐文件为空。")
        if len(data) > MAX_MUSIC_BYTES:
            raise ApiError(413, "每首音乐最多可导入 100 MB。")
        if not isinstance(filename, str) or any(ord(character) < 32 for character in filename):
            raise ApiError(400, "音乐文件名格式不正确。")
        filename = clean_text(filename, 255, "音乐文件名")
        if not filename or filename in {".", ".."} or any(character in filename for character in "/\\:") or any(ord(character) < 32 for character in filename):
            raise ApiError(400, "音乐文件名不正确，请选择文件而非文件路径。")
        extension = Path(filename).suffix.lower()
        title = clean_text(title, 120, "音乐名称") if title is not None else Path(filename).stem[:120]
        artist = clean_text(artist, 120, "音乐作者")
        if extension not in AUDIO_MIME:
            raise ApiError(415, "支持导入 MP3、WAV、M4A、AAC、OGG 和 FLAC 音乐。")
        if not audio_header_valid(data, extension):
            raise ApiError(400, "无法识别音乐文件头，文件可能已损坏或扩展名与内容不符。")
        digest = hashlib.sha256(data).hexdigest()
        with self.lock, self.connect() as db:
            builtin = next((track for track in self.builtin_music() if track.get("sha256") == digest), None)
            if builtin:
                return {"track": self.require_music(db, builtin["id"]), "duplicate": True}
            existing = db.execute("SELECT * FROM music_tracks WHERE sha256 = ? AND origin = 'imported'", (digest,)).fetchone()
            if existing:
                if existing["removed"]:
                    db.execute("UPDATE music_tracks SET removed = 0,enabled = 1 WHERE id = ?", (existing["id"],))
                return {"track": self.require_music(db, existing["id"]), "duplicate": True, "restored": bool(existing["removed"])}
            track_id = uuid.uuid4().hex
            self.music_dir.mkdir(exist_ok=True)
            target = self.music_dir / f"{track_id}{extension}"
            try:
                with target.open("xb") as content:
                    content.write(data)
                db.execute("INSERT INTO music_tracks(id,origin,title,artist,original_name,extension,sha256,size,imported_at) VALUES(?,'imported',?,?,?,?,?,?,?)", (track_id, title or "未命名音乐", artist, filename, extension, digest, len(data), now()))
                result = {"track": self.require_music(db, track_id), "duplicate": False}
                # Explicit commit keeps a failed commit inside the file rollback block.
                db.commit()
                return result
            except Exception:
                target.unlink(missing_ok=True)
                raise

    def update_music(self, track_id: str, changes: dict) -> dict:
        if not isinstance(changes, dict) or set(changes) - {"title", "enabled"}:
            raise ApiError(400, "音乐资料中包含不支持的字段。")
        values = {}
        if "title" in changes:
            values["title"] = clean_text(changes["title"], 120, "音乐名称")
            if not values["title"]:
                raise ApiError(400, "请填写音乐名称。")
        if "enabled" in changes:
            if not isinstance(changes["enabled"], bool):
                raise ApiError(400, "音乐启用状态格式不正确。")
            values["enabled"] = int(changes["enabled"])
        with self.lock, self.connect() as db:
            track = self.require_music(db, track_id)
            if track["origin"] == "builtin":
                db.execute("INSERT OR IGNORE INTO music_tracks(id,origin,title,artist,imported_at) VALUES(?,'builtin',?,?,?)", (track_id, track["title"], track["artist"], now()))
            if values:
                db.execute("UPDATE music_tracks SET " + ",".join(f"{key} = ?" for key in values) + " WHERE id = ?", (*values.values(), track_id))
            return {"track": self.require_music(db, track_id)}

    def remove_music(self, track_id: str) -> dict:
        with self.lock, self.connect() as db:
            track = self.require_music(db, track_id)
            if track["origin"] != "imported":
                raise ApiError(400, "内置音乐可以停用，不能从应用中移除。")
            db.execute("UPDATE music_tracks SET removed = 1,enabled = 0 WHERE id = ?", (track_id,))
            return {"tracks": self.music_records(db), "removed_id": track_id}

    def music_path(self, track_id: str, extension: str) -> Path:
        with self.connect() as db:
            row = db.execute("SELECT extension FROM music_tracks WHERE id = ? AND origin = 'imported' AND removed = 0", (track_id,)).fetchone()
            if not row or row["extension"] != extension:
                raise ApiError(404, "这首音乐已从曲库移除。")
        return self.music_dir / f"{track_id}{extension}"

    def backup_media_path(self, directory: Path, name: str, label: str) -> Path:
        path = directory / name
        try:
            # Only fixed, database-owned filenames are eligible. Reject links
            # and junctions before opening any user-controlled filesystem path.
            if any(part.is_symlink() or getattr(part, "is_junction", lambda: False)() for part in (directory, path)):
                raise ValueError
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root) or not resolved.is_file():
                raise ValueError
        except (OSError, ValueError, RuntimeError):
            raise ApiError(409, f"无法生成完整迁移包：{label}副本缺失或路径不安全，请检查相册资料后重试。") from None
        return resolved

    @contextmanager
    def backup_archive(self, application_file: Path):
        application_file = Path(application_file)
        if not application_file.is_file() or application_file.is_symlink() or getattr(application_file, "is_junction", lambda: False)():
            raise ApiError(503, "未找到拾光相册程序，无法生成可直接运行的迁移包。请从完整的桌面程序中导出。")
        # Keep temporary archives outside album-data, and remove both the
        # database snapshot and ZIP after a complete or interrupted download.
        with tempfile.TemporaryDirectory(prefix="shiguang-backup-") as temporary:
            snapshot_path = Path(temporary) / "album.sqlite3"
            archive_path = Path(temporary) / "album-backup.zip"
            with self.lock:
                self.backup_media_path(self.root, "album.sqlite3", "相册数据库")
                with self.connect() as source, closing(sqlite3.connect(snapshot_path)) as snapshot:
                    source.backup(snapshot)
                    # The archived database must be self-contained, without a
                    # WAL/SHM dependency on the running album's connections.
                    snapshot.execute("PRAGMA journal_mode=DELETE")
                    photos = snapshot.execute("SELECT id,extension,filename FROM photos ORDER BY id").fetchall()
                    music = snapshot.execute("SELECT id,extension,title FROM music_tracks WHERE origin = 'imported' ORDER BY id").fetchall()
                media = []
                for photo_id, extension, filename in photos:
                    if not re.fullmatch(r"[a-f0-9]{32}", photo_id) or extension not in FORMATS.values():
                        raise ApiError(409, "照片资料中的文件标识异常，无法生成完整迁移包。")
                    media.append((self.backup_media_path(self.originals, photo_id + extension, f"原图“{filename}”"), "originals/" + photo_id + extension))
                    media.append((self.backup_media_path(self.previews, photo_id + ".jpg", f"预览图“{filename}”"), "previews/" + photo_id + ".jpg"))
                for track_id, extension, title in music:
                    if not re.fullmatch(r"[a-f0-9]{32}", track_id) or extension not in AUDIO_MIME:
                        raise ApiError(409, "音乐资料中的文件标识异常，无法生成完整迁移包。")
                    media.append((self.backup_media_path(self.music_dir, track_id + extension, f"导入音乐“{title}”"), "music/" + track_id + extension))
                prefix = "拾光相册/"
                # JPEG/MP3 and the one-file executable are already compressed.
                # ZIP_STORED keeps large albums fast and permits ZIP64 sizes.
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    archive.write(application_file, prefix + "拾光相册.exe")
                    archive.write(snapshot_path, prefix + "album-data/album.sqlite3")
                    for folder in ("originals", "previews", "music"):
                        archive.writestr(prefix + "album-data/" + folder + "/", b"")
                    for path, relative in media:
                        archive.write(path, prefix + "album-data/" + relative)
                    archive.writestr(prefix + "迁移说明.txt", (
                        "拾光相册 · 完整迁移包\r\n\r\n"
                        "1. 将压缩包中的整个“拾光相册”文件夹解压到新电脑的可写目录。\r\n"
                        "2. 保持“拾光相册.exe”和“album-data”文件夹放在一起，双击程序即可使用。\r\n"
                        "3. 照片、导入音乐、曲名、启用状态、音量、作品集、手记、足迹及其他资料均已包含。\r\n"
                        "4. 请勿直接在压缩包内运行程序。内置音乐已包含在程序中，不需要原来的照片或音乐路径。\r\n"
                        "5. 适用于 Windows 10/11；桌面窗口需要 Microsoft Edge WebView2 运行时。\r\n\r\n"
                        "本包为导出时的独立副本，不会改变原来的相册。\r\n"
                    ).encode("utf-8-sig"))
            # Release the write lock before the potentially slow HTTP transfer.
            yield archive_path

    @staticmethod
    def photo(row) -> dict:
        result = dict(row)
        extension = result.pop("extension")
        result.pop("sha256", None)
        result["favorite"] = bool(result["favorite"])
        result["country"] = result.get("country") or ""
        result["city"] = result.get("city") or result.get("place") or ""
        result["url"] = f"/media/originals/{result['id']}{extension}"
        result["thumbnail_url"] = f"/media/previews/{result['id']}.jpg"
        return result

    def photos(self) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM photos ORDER BY taken_at DESC, imported_at DESC, id").fetchall()
        photos = [self.photo(row) for row in rows]
        return {"photos": photos, "storage_bytes": sum(photo["size"] for photo in photos)}

    def framed_photo(self, photo_id: str, assets_dir: Path, preview: bool = False) -> tuple[bytes, dict]:
        # Keep the original available until rendering finishes, even when a
        # different request tries to remove this photo at the same time.
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
            if not row or not re.fullmatch(r"[a-f0-9]{32}", photo_id) or row["extension"] not in FORMATS.values():
                raise ApiError(404, "这张照片不存在。")
            try:
                original = self.backup_media_path(self.originals, photo_id + row["extension"], "原图")
            except ApiError:
                raise ApiError(404, "原图文件不存在或路径不安全，无法导出白边照片。") from None
            # Import lazily so normal album browsing remains available if the
            # optional export renderer cannot be loaded.
            from photo_export import render_framed_photo
            photo = self.photo(row)
            return render_framed_photo(original, photo, assets_dir, preview=preview), photo

    def import_photo(self, data: bytes, filename: str, modified: str | None, place: str = "", trip: str = "", collection_id: str | None = None, country: str = "", city: str = "") -> dict:
        if not data:
            raise ApiError(400, "照片文件为空。")
        if len(data) > MAX_PHOTO_BYTES:
            raise ApiError(413, "每张照片最多可导入 100 MB。")
        filename = clean_text(re.split(r"[/\\]", filename)[-1], 255, "文件名")
        if not filename or filename in (".", ".."):
            raise ApiError(400, "缺少有效的照片文件名。")
        place, trip = clean_text(place, 300, "地点"), clean_text(trip, 300, "旅行名称")
        country, city = country_code(country), city_name(city)
        if city:
            place = city
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            with self.connect() as db:
                if collection_id is not None:
                    self.require_collection(db, collection_id)
                existing = db.execute("SELECT * FROM photos WHERE sha256 = ?", (digest,)).fetchone()
                if existing:
                    result = {"photo": self.photo(existing), "duplicate": True}
                    if collection_id is not None:
                        self.attach_collection_photos(db, collection_id, [existing["id"]])
                        result["collection"] = self.collection(db, collection_id)
                    return result
            info, preview, extension = image_metadata(data, modified)
            photo_id = uuid.uuid4().hex
            original_path = self.originals / f"{photo_id}{extension}"
            preview_path = self.previews / f"{photo_id}.jpg"
            values = {
                "id": photo_id, "sha256": digest, "filename": filename, "extension": extension,
                "title": Path(filename).stem, "caption": "", "place": place, "trip": trip,
                "country": country, "city": city,
                "imported_at": now(), "favorite": 0, "size": len(data), **info,
            }
            try:
                original_path.write_bytes(data)
                preview_path.write_bytes(preview)
                with self.connect() as db:
                    columns = ",".join(values)
                    placeholders = ",".join("?" for _ in values)
                    db.execute(f"INSERT INTO photos ({columns}) VALUES ({placeholders})", tuple(values.values()))
                    row = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
                    result = {"photo": self.photo(row), "duplicate": False}
                    if collection_id is not None:
                        self.attach_collection_photos(db, collection_id, [photo_id])
                        result["collection"] = self.collection(db, collection_id)
            except Exception:
                original_path.unlink(missing_ok=True)
                preview_path.unlink(missing_ok=True)
                raise
            return result

    @staticmethod
    def require_collection(db, collection_id: str):
        if not isinstance(collection_id, str) or not re.fullmatch(r"[a-f0-9]{32}", collection_id):
            raise ApiError(404, "这个作品集不存在。")
        row = db.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if not row:
            raise ApiError(404, "这个作品集不存在。")
        return row

    def collection(self, db, collection_id: str) -> dict:
        result = dict(self.require_collection(db, collection_id))
        rows = db.execute("SELECT cp.photo_id FROM collection_photos cp JOIN photos p ON p.id = cp.photo_id WHERE cp.collection_id = ? ORDER BY cp.position, cp.added_at, cp.rowid", (collection_id,)).fetchall()
        result["photo_ids"] = [row["photo_id"] for row in rows]
        result["photo_count"] = len(rows)
        cover_id = result["cover_photo_id"] if result["cover_photo_id"] in result["photo_ids"] else next(iter(result["photo_ids"]), None)
        result["cover_url"] = f"/media/previews/{cover_id}.jpg" if cover_id else None
        route = self.route_record(db, collection_id)
        result["route_stops"] = route["stops"]
        result["route_updated_at"] = route["updated_at"]
        return result

    def collections(self) -> dict:
        with self.connect() as db:
            ids = db.execute("SELECT id FROM collections ORDER BY updated_at DESC, created_at DESC, id").fetchall()
            return {"collections": [self.collection(db, row["id"]) for row in ids]}

    @staticmethod
    def route_record(db, collection_id: str) -> dict:
        row = db.execute("SELECT stops_json,updated_at FROM collection_routes WHERE collection_id = ?", (collection_id,)).fetchone()
        return {"collection_id": collection_id, "stops": json.loads(row["stops_json"]) if row else [], "updated_at": row["updated_at"] if row else None}

    def get_route(self, collection_id: str) -> dict:
        with self.connect() as db:
            self.require_collection(db, collection_id)
            return {"route": self.route_record(db, collection_id)}

    @staticmethod
    def atlas_record(db) -> dict:
        row = db.execute("SELECT stops_json,updated_at FROM atlas_route WHERE key = 'default'").fetchone()
        stops = json.loads(row["stops_json"]) if row else []
        for stop in stops:
            stop["country"] = stop.get("country") or ""
            stop["city"] = stop.get("city") or ""
        return {"stops": stops, "updated_at": row["updated_at"] if row else None}

    def atlas(self) -> dict:
        with self.connect() as db:
            return self.atlas_record(db)

    def update_atlas(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or set(changes) != {"stops"} or not isinstance(changes["stops"], list):
            raise ApiError(400, "足迹资料格式不正确。")
        if len(changes["stops"]) > 200:
            raise ApiError(400, "足迹地图最多可添加 200 个地点。")
        stops, ids = [], set()
        for stop in changes["stops"]:
            if not isinstance(stop, dict) or set(stop) - {"id", "name", "lat", "lon", "note", "date", "photo_place", "country", "city"}:
                raise ApiError(400, "足迹地点中包含不支持的字段。")
            supplied_id = stop.get("id")
            stop_id = clean_text(uuid.uuid4().hex if supplied_id is None else supplied_id, 64, "足迹标识")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stop_id) or stop_id in ids:
                raise ApiError(400, "足迹标识不正确或重复，请重新添加地点。")
            ids.add(stop_id)
            name = clean_text(stop.get("name", ""), 100, "地点名称")
            note = clean_text(stop.get("note", ""), 500, "地点笔记")
            supplied_place = stop.get("photo_place")
            photo_place = clean_text("" if supplied_place is None else supplied_place, 300, "关联拍摄地点")
            country, city = country_code(stop.get("country")), city_name(stop.get("city"))
            if not name:
                raise ApiError(400, "请填写每个足迹地点的名称。")
            coordinates = {}
            for key, limit in (("lat", 90), ("lon", 180)):
                value = stop.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ApiError(400, "请填写有效的纬度和经度数值。")
                try:
                    number = float(value)
                except (ValueError, OverflowError):
                    raise ApiError(400, "纬度或经度超出有效范围。") from None
                if not math.isfinite(number) or not -limit <= number <= limit:
                    raise ApiError(400, "纬度应在 -90 到 90 之间，经度应在 -180 到 180 之间。")
                coordinates[key] = number
            date = stop.get("date")
            if date is None:
                date = ""
            if not isinstance(date, str):
                raise ApiError(400, "足迹日期格式应为 YYYY-MM-DD。")
            date = date.strip()
            if date:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    raise ApiError(400, "足迹日期格式应为 YYYY-MM-DD。")
                try:
                    datetime.strptime(date, "%Y-%m-%d")
                except ValueError:
                    raise ApiError(400, "足迹日期不存在，请检查后重试。") from None
            stops.append({"id": stop_id, "name": name, **coordinates, "note": note, "date": date, "photo_place": photo_place, "country": country, "city": city})
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO atlas_route(key,stops_json,updated_at) VALUES('default',?,?) ON CONFLICT(key) DO UPDATE SET stops_json=excluded.stops_json,updated_at=excluded.updated_at", (json.dumps(stops, ensure_ascii=False, allow_nan=False), now()))
            return self.atlas_record(db)

    def update_route(self, collection_id: str, changes: dict) -> dict:
        if not isinstance(changes, dict) or set(changes) != {"stops"} or not isinstance(changes["stops"], list):
            raise ApiError(400, "路线资料格式不正确。")
        if len(changes["stops"]) > 30:
            raise ApiError(400, "每份作品集最多可添加 30 个路线站点。")
        stops, ids = [], set()
        for stop in changes["stops"]:
            if not isinstance(stop, dict) or set(stop) - {"id", "name", "note", "photo_id"}:
                raise ApiError(400, "路线站点中包含不支持的字段。")
            supplied_id = stop.get("id")
            stop_id = clean_text(uuid.uuid4().hex if supplied_id is None else supplied_id, 64, "站点标识")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stop_id) or stop_id in ids:
                raise ApiError(400, "站点标识不正确或重复，请重新添加站点。")
            ids.add(stop_id)
            name = clean_text(stop.get("name", ""), 100, "站点名称")
            note = clean_text(stop.get("note", ""), 500, "站点笔记")
            if not name:
                raise ApiError(400, "请填写每个路线站点的名称。")
            photo_id = stop.get("photo_id")
            if photo_id == "":
                photo_id = None
            if photo_id is not None and (not isinstance(photo_id, str) or not re.fullmatch(r"[a-f0-9]{32}", photo_id)):
                raise ApiError(400, "站点关联的照片格式不正确。")
            stops.append({"id": stop_id, "name": name, "note": note, "photo_id": photo_id})
        with self.lock, self.connect() as db:
            self.require_collection(db, collection_id)
            for stop in stops:
                if stop["photo_id"] is not None and not db.execute("SELECT 1 FROM collection_photos WHERE collection_id = ? AND photo_id = ?", (collection_id, stop["photo_id"])).fetchone():
                    raise ApiError(400, "站点关联的照片必须属于这个作品集。")
            timestamp = now()
            db.execute("INSERT INTO collection_routes(collection_id,stops_json,updated_at) VALUES(?,?,?) ON CONFLICT(collection_id) DO UPDATE SET stops_json=excluded.stops_json,updated_at=excluded.updated_at", (collection_id, json.dumps(stops, ensure_ascii=False), timestamp))
            db.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (timestamp, collection_id))
            return {"route": self.route_record(db, collection_id), "collection": self.collection(db, collection_id)}

    @staticmethod
    def detach_route_photos(db, photo_ids: list[str], collection_id: str | None = None):
        if not photo_ids:
            return
        wanted = set(photo_ids)
        rows = db.execute("SELECT * FROM collection_routes" + (" WHERE collection_id = ?" if collection_id is not None else ""), (collection_id,) if collection_id is not None else ()).fetchall()
        for row in rows:
            stops = json.loads(row["stops_json"])
            if not any(stop.get("photo_id") in wanted for stop in stops):
                continue
            for stop in stops:
                if stop.get("photo_id") in wanted:
                    stop["photo_id"] = None
            timestamp = now()
            db.execute("UPDATE collection_routes SET stops_json = ?, updated_at = ? WHERE collection_id = ?", (json.dumps(stops, ensure_ascii=False), timestamp, row["collection_id"]))
            db.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (timestamp, row["collection_id"]))

    @staticmethod
    def collection_values(changes: dict, creating: bool = False) -> dict:
        allowed = {"title": 300, "description": 20000, "place": 300}
        if not isinstance(changes, dict) or set(changes) - (set(allowed) | (set() if creating else {"cover_photo_id"})):
            raise ApiError(400, "作品集资料中包含不支持的字段。")
        if creating and "title" not in changes:
            raise ApiError(400, "请填写作品集名称。")
        values = {key: clean_text(value, allowed[key], "作品集名称" if key == "title" else "作品集资料") for key, value in changes.items() if key in allowed}
        if "title" in values and not values["title"]:
            raise ApiError(400, "请填写作品集名称。")
        return values

    def create_collection(self, changes: dict) -> dict:
        values = self.collection_values(changes, creating=True)
        collection_id, timestamp = uuid.uuid4().hex, now()
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO collections(id,title,description,place,created_at,updated_at) VALUES(?,?,?,?,?,?)", (collection_id, values["title"], values.get("description", ""), values.get("place", ""), timestamp, timestamp))
            return {"collection": self.collection(db, collection_id)}

    def update_collection(self, collection_id: str, changes: dict) -> dict:
        values = self.collection_values(changes)
        with self.lock, self.connect() as db:
            self.require_collection(db, collection_id)
            if "cover_photo_id" in changes:
                cover_id = changes["cover_photo_id"]
                if cover_id is not None and (not isinstance(cover_id, str) or not db.execute("SELECT 1 FROM collection_photos WHERE collection_id = ? AND photo_id = ?", (collection_id, cover_id)).fetchone()):
                    raise ApiError(400, "封面照片必须属于这个作品集。")
                values["cover_photo_id"] = cover_id
            if values:
                values["updated_at"] = now()
                db.execute("UPDATE collections SET " + ",".join(f"{key} = ?" for key in values) + " WHERE id = ?", (*values.values(), collection_id))
            return {"collection": self.collection(db, collection_id)}

    @staticmethod
    def requested_photo_ids(changes: dict) -> list[str]:
        if not isinstance(changes, dict) or set(changes) != {"photo_ids"} or not isinstance(changes["photo_ids"], list):
            raise ApiError(400, "请选择要整理的照片。")
        ids = changes["photo_ids"]
        if len(ids) > 5000:
            raise ApiError(400, "每次最多可整理 5000 张照片。")
        if any(not isinstance(photo_id, str) or not re.fullmatch(r"[a-f0-9]{32}", photo_id) for photo_id in ids):
            raise ApiError(400, "照片标识格式不正确。")
        return list(dict.fromkeys(ids))

    @staticmethod
    def validate_photo_ids(db, photo_ids: list[str]):
        found = set()
        for start in range(0, len(photo_ids), 500):
            chunk = photo_ids[start:start + 500]
            rows = db.execute("SELECT id FROM photos WHERE id IN (" + ",".join("?" for _ in chunk) + ")", chunk).fetchall()
            found.update(row["id"] for row in rows)
        if len(found) != len(photo_ids):
            raise ApiError(404, "部分照片不存在，请刷新相册后重试。")

    @staticmethod
    def attach_collection_photos(db, collection_id: str, photo_ids: list[str]) -> int:
        count = 0
        timestamp = now()
        last = db.execute("SELECT MAX(position) FROM collection_photos WHERE collection_id=?", (collection_id,)).fetchone()[0]
        position = (last if last is not None else -1) + 1
        for photo_id in photo_ids:
            added = db.execute("INSERT OR IGNORE INTO collection_photos(collection_id,photo_id,added_at,position) VALUES(?,?,?,?)", (collection_id, photo_id, timestamp, position)).rowcount
            count += added
            position += added
        if count:
            db.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (timestamp, collection_id))
        return count

    def reorder_collection_photos(self, collection_id: str, changes: dict) -> dict:
        if not isinstance(changes, dict) or "photo_ids" not in changes or set(changes) - {"photo_ids", "expected_photo_ids"}:
            raise ApiError(400, "请提交作品集的完整照片顺序。")
        for key in changes:
            ids = changes[key]
            if not isinstance(ids, list) or len(ids) > 5000 or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value) for value in ids) or len(set(ids)) != len(ids):
                raise ApiError(400, "照片顺序需要包含互不重复的有效照片，每次最多编排 5000 张。")
        with self.lock, self.connect() as db:
            current = self.collection(db, collection_id)
            ids = changes["photo_ids"]
            if set(ids) != set(current["photo_ids"]) or ("expected_photo_ids" in changes and changes["expected_photo_ids"] != current["photo_ids"]):
                raise ApiError(409, "作品集照片或顺序已有变化，请重新打开编排窗口后重试。")
            if ids != current["photo_ids"]:
                db.executemany("UPDATE collection_photos SET position=? WHERE collection_id=? AND photo_id=?", [(position, collection_id, photo_id) for position, photo_id in enumerate(ids)])
                db.execute("UPDATE collections SET updated_at=? WHERE id=?", (now(), collection_id))
            return {"collection": self.collection(db, collection_id)}

    def add_collection_photos(self, collection_id: str, changes: dict) -> dict:
        ids = self.requested_photo_ids(changes)
        with self.lock, self.connect() as db:
            self.require_collection(db, collection_id)
            self.validate_photo_ids(db, ids)
            count = self.attach_collection_photos(db, collection_id, ids)
            return {"collection": self.collection(db, collection_id), "added_count": count}

    def remove_collection_photos(self, collection_id: str, changes: dict) -> dict:
        ids = self.requested_photo_ids(changes)
        with self.lock, self.connect() as db:
            current = self.require_collection(db, collection_id)
            self.validate_photo_ids(db, ids)
            count = 0
            for photo_id in ids:
                count += db.execute("DELETE FROM collection_photos WHERE collection_id = ? AND photo_id = ?", (collection_id, photo_id)).rowcount
            if count:
                self.detach_route_photos(db, ids, collection_id)
                db.execute("UPDATE collections SET cover_photo_id = ?, updated_at = ? WHERE id = ?", (None if current["cover_photo_id"] in ids else current["cover_photo_id"], now(), collection_id))
            return {"collection": self.collection(db, collection_id), "removed_count": count}

    def delete_collection(self, collection_id: str) -> dict:
        with self.lock, self.connect() as db:
            self.require_collection(db, collection_id)
            db.execute("DELETE FROM collection_photos WHERE collection_id = ?", (collection_id,))
            db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        return {"deleted": True}

    def update_photo(self, photo_id: str, changes: dict) -> dict:
        allowed = {"title": 300, "caption": 20000, "place": 300, "trip": 300}
        if not isinstance(changes, dict) or set(changes) - (set(allowed) | {"taken_at", "favorite", "country", "city"}):
            raise ApiError(400, "照片资料中包含不支持的字段。")
        values = {}
        for key, value in changes.items():
            if key in allowed:
                values[key] = clean_text(value, allowed[key])
            elif key == "country":
                values[key] = country_code(value)
            elif key == "city":
                values[key] = city_name(value)
            elif key == "taken_at":
                values[key] = local_date(value)
            elif key == "favorite":
                if not isinstance(value, bool):
                    raise ApiError(400, "收藏状态格式不正确。")
                values[key] = int(value)
        if "city" in values:
            # Explicit city edits also update the legacy place field. An empty
            # city-only edit clears both; legacy callers may still supply place.
            if values["city"] or "place" not in values:
                values["place"] = values["city"]
        elif "place" in values:
            # Keep old place-only clients useful after a new client saved city.
            values["city"] = values["place"] if len(values["place"]) <= 100 else ""
        with self.lock, self.connect() as db:
            current = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
            if not current:
                raise ApiError(404, "这张照片不存在。")
            if "taken_at" in values and values["taken_at"] != current["taken_at"]:
                values["date_source"] = "manual"
            if values:
                assignments = ",".join(f"{key} = ?" for key in values)
                db.execute(f"UPDATE photos SET {assignments} WHERE id = ?", (*values.values(), photo_id))
            return {"photo": self.photo(db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone())}

    def delete_photo(self, photo_id: str) -> dict:
        with self.lock:
            staged = None
            try:
                with self.connect() as db:
                    row = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
                    if not row:
                        raise ApiError(404, "这张照片不存在。")
                    # Same-volume renames keep both copies recoverable until SQLite commits.
                    # The directory name is the recovery record, written before either move.
                    self.delete_staging.mkdir(exist_ok=True)
                    staged = self.delete_staging / f"{photo_id}{row['extension']}-{uuid.uuid4().hex}"
                    staged.mkdir()
                    for source, name in self.staging_files(staged, photo_id, row["extension"]):
                        if source.exists():
                            source.replace(staged / name)
                    self.detach_route_photos(db, [photo_id])
                    db.execute("UPDATE collections SET cover_photo_id = NULL, updated_at = ? WHERE cover_photo_id = ?", (now(), photo_id))
                    db.execute("UPDATE collections SET updated_at = ? WHERE id IN (SELECT collection_id FROM collection_photos WHERE photo_id = ?)", (now(), photo_id))
                    db.execute("DELETE FROM collection_photos WHERE photo_id = ?", (photo_id,))
                    db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
                # Exiting the connection context above commits the database first.
            except Exception:
                if staged is not None:
                    try:
                        self.restore_staged(staged, photo_id, row["extension"])
                    except OSError:
                        # Keep all recoverable bytes for a later startup if Windows
                        # still has one of the staged files open.
                        raise ApiError(500, "移除未完成，照片副本仍已保留。请关闭占用照片的程序后重新启动相册。") from None
                raise
            self.clean_staged(staged)
        return {"deleted": True}

    def staging_files(self, staged: Path, photo_id: str, extension: str):
        return ((self.originals / f"{photo_id}{extension}", "original"),
                (self.previews / f"{photo_id}.jpg", "preview"))

    def restore_staged(self, staged: Path, photo_id: str, extension: str):
        failures = []
        for destination, name in self.staging_files(staged, photo_id, extension):
            source = staged / name
            try:
                if source.exists():
                    if destination.exists():
                        raise FileExistsError("A recovered photo already exists; preserve both copies.")
                    source.replace(destination)
            except OSError as error:
                failures.append(error)
        if failures:
            raise failures[0]
        self.clean_staged(staged)

    @staticmethod
    def clean_staged(staged: Path):
        # Only our two fixed staging filenames are eligible for deletion.
        # If cleanup is blocked by Windows, startup recovery will retry it.
        for name in ("original", "preview"):
            try:
                (staged / name).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            staged.rmdir()
        except OSError:
            pass

    def recover_deletions(self):
        if not self.delete_staging.is_dir():
            return
        with self.lock, self.connect() as db:
            for staged in self.delete_staging.iterdir():
                match = re.fullmatch(r"([a-f0-9]{32})(\.(?:jpg|png|webp|gif|bmp|tif))-[a-f0-9]{32}", staged.name)
                if not match or not staged.is_dir() or staged.resolve().parent != self.delete_staging.resolve():
                    continue
                photo_id, extension = match.group(1, 2)
                if db.execute("SELECT 1 FROM photos WHERE id = ?", (photo_id,)).fetchone():
                    try:
                        self.restore_staged(staged, photo_id, extension)
                    except OSError:
                        print("有照片副本等待恢复，请关闭占用照片的程序后重新启动相册。", flush=True)
                else:
                    self.clean_staged(staged)

    def journals(self) -> dict:
        with self.lock, self.connect() as db:
            return {
                "journals": [dict(row) for row in db.execute("SELECT * FROM journals ORDER BY updated_at DESC, key").fetchall()],
                "deleted_journal_keys": [row["key"] for row in db.execute("SELECT key FROM journal_deletions ORDER BY key").fetchall()],
            }

    def update_journal(self, key: str, changes: dict) -> dict:
        key = journal_key(key)
        if not isinstance(changes, dict) or set(changes) - {"title", "note"}:
            raise ApiError(400, "手记中包含不支持的字段。")
        with self.lock, self.connect() as db:
            previous = db.execute("SELECT * FROM journals WHERE key = ?", (key,)).fetchone()
            title = clean_text(changes.get("title", previous["title"] if previous else ""), 300, "手记标题")
            note = clean_text(changes.get("note", previous["note"] if previous else ""), 50000, "手记正文")
            db.execute("INSERT INTO journals(key,title,note,updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET title=excluded.title,note=excluded.note,updated_at=excluded.updated_at", (key, title, note, now()))
            db.execute("DELETE FROM journal_deletions WHERE key = ?", (key,))
            return {"journal": dict(db.execute("SELECT * FROM journals WHERE key = ?", (key,)).fetchone())}

    def delete_journal(self, key: str) -> dict:
        key = journal_key(key)
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM journals WHERE key = ?", (key,))
            db.execute("INSERT INTO journal_deletions(key,deleted_at) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET deleted_at=excluded.deleted_at", (key, now()))
        return {"deleted": key}


class AlbumHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, data_dir: Path, web_dir: Path = ROOT / "ui", application_file: Path | None = None):
        self.web_dir = Path(web_dir).resolve()
        self.application_file = Path(application_file) if application_file is not None else (Path(sys.executable) if getattr(sys, "frozen", False) else ROOT / "拾光相册.exe")
        self.store = AlbumStore(data_dir, self.web_dir)
        self.colors = ColorAnalysis(self.store)
        self.video_jobs = VideoJobManager(self.store, self.web_dir / "assets")
        self.online_music = OnlineMusic(self.store)
        from album_reset import RequestGate
        self.request_gate = RequestGate()
        super().__init__(("127.0.0.1", port), AlbumHandler)

    def server_close(self):
        try:
            if hasattr(self, "video_jobs"):
                self.video_jobs.close()
        finally:
            try:
                if hasattr(self, "colors"):
                    self.colors.close()
            finally:
                super().server_close()


class AlbumHandler(BaseHTTPRequestHandler):
    server_version = "LocalPhotoAlbum/1.0"

    def do_GET(self):
        self.dispatch()

    def do_HEAD(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def do_OPTIONS(self):
        self.dispatch()

    def security_check(self, require_same_origin: bool = False):
        try:
            host = urlsplit("//" + self.headers.get("Host", ""))
            if host.hostname not in {"localhost", "127.0.0.1", "::1"} or (host.port or 80) != self.server.server_port or host.username or host.password or host.path or host.query or host.fragment:
                raise ValueError
        except ValueError:
            raise ApiError(403, "相册仅允许通过本机地址访问。") from None
        if require_same_origin or self.command in {"POST", "DELETE", "OPTIONS"}:
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers['Host']}":
                raise ApiError(403, "为保护本地照片，已拒绝来自其他网页的操作。")
            fetch_site = self.headers.get("Sec-Fetch-Site")
            if fetch_site == "cross-site" or (require_same_origin and fetch_site not in {None, "none", "same-origin"}):
                raise ApiError(403, "为保护本地照片，已拒绝来自其他网页的操作。")

    def dispatch(self):
        exclusive = self.command == 'POST' and unquote(urlsplit(self.path).path) == '/api/initialize'
        try:
            with self.server.request_gate.enter(exclusive):
                self.dispatch_request()
        except ApiError as error:
            self.close_connection = True
            self.send_json(error.status, {'error': error.message})

    def dispatch_request(self):
        try:
            target = urlsplit(self.path)
            path = unquote(target.path)
            framed = re.fullmatch(r"/api/photos/([a-f0-9]{32})/framed", path)
            video_create = re.fullmatch(r"/api/collections/([a-f0-9]{32})/video", path)
            video_preview = re.fullmatch(r"/api/collections/([a-f0-9]{32})/video-preview", path)
            video_job = re.fullmatch(r"/api/video-jobs/([a-f0-9]{32})(/file)?", path)
            self.security_check(require_same_origin=path in {"/api/backup", "/api/colors", "/daily-quotes.js", "/api/initialize"} or path.startswith(("/api/music/online/", "/api/opening-lines")) or any(item is not None for item in (framed, video_create, video_preview, video_job)))
            query = parse_qs(target.query, keep_blank_values=True)
            store = self.server.store
            if self.command in {"GET", "HEAD"}:
                if path == "/api/health":
                    self.send_json(200, {"app": "shiguang", "project_dir": str(ROOT), "data_dir": str(store.root), "pid": os.getpid()})
                elif path == "/api/photos":
                    self.send_json(200, store.photos())
                elif path == "/api/colors":
                    if set(query) - {"period", "scope"} or ("period" in query and query["period"] not in (["month"], ["quarter"], ["year"])) or ("scope" in query and query["scope"] not in (["all"], ["favorites"])):
                        raise ApiError(400, "请选择有效的色彩分析时期与作品范围。")
                    self.send_json(200, self.server.colors.analyze(period=query.get("period", ["month"])[0], scope=query.get("scope", ["all"])[0]))
                elif path == "/api/journals":
                    self.send_json(200, store.journals())
                elif path == "/api/collections":
                    self.send_json(200, store.collections())
                elif path == "/api/atlas":
                    self.send_json(200, store.atlas())
                elif path == "/api/settings":
                    self.send_json(200, store.settings())
                elif path == '/api/opening-lines':
                    if query:
                        raise ApiError(400, '开场白列表不需要额外参数。')
                    self.send_json(200, store.opening_lines())
                elif path == '/daily-quotes.js':
                    data = ('window.ShiguangDailyQuotes=Object.freeze(' + json.dumps([line['text'] for line in store.opening_lines()['lines']]) + ');').encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/javascript; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.common_headers(len(data))
                    if self.command != 'HEAD':
                        self.wfile.write(data)
                elif path == "/api/music":
                    self.send_json(200, store.music())
                elif video_preview:
                    if set(query) - {"order"} or ("order" in query and query["order"] not in (["collection"], ["oldest"], ["newest"])):
                        raise ApiError(400, "请选择有效的视频照片顺序。")
                    data = self.server.video_jobs.preview(video_preview.group(1), query.get("order", ["collection"])[0])
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.common_headers(len(data))
                    if self.command != "HEAD":
                        self.wfile.write(data)
                elif video_job:
                    if video_job.group(2):
                        if set(query) - {"download"} or ("download" in query and query["download"] not in (["0"], ["1"])):
                            raise ApiError(400, "视频下载参数应为 0 或 1。")
                        self.send_video_file(video_job.group(1), download=query.get("download") == ["1"])
                    else:
                        self.send_json(200, {"job": self.server.video_jobs.get(video_job.group(1))})
                elif re.fullmatch(r"/api/collections/[a-f0-9]{32}/route", path):
                    self.send_json(200, store.get_route(path.split("/")[3]))
                elif path == "/api/export":
                    self.send_json(200, {"version": 1, "exported_at": now(), **store.photos(), **store.journals(), **store.collections(), "atlas": store.atlas(), "settings": store.settings(), "music": store.music(), "opening_lines": store.opening_lines()['lines']}, attachment="相册资料.json")
                elif path == "/api/backup":
                    self.send_backup()
                elif framed:
                    if set(query) - {"preview"} or ("preview" in query and query["preview"] not in (["0"], ["1"])):
                        raise ApiError(400, "白边导出预览参数应为 0 或 1。")
                    self.send_framed_photo(framed.group(1), preview=query.get("preview") == ["1"])
                elif path.startswith("/api/"):
                    raise ApiError(404, "没有找到这个接口。")
                else:
                    self.send_file(path)
            elif self.command == "POST":
                if path == '/api/initialize':
                    if target.query:
                        raise ApiError(400, '初始化不接受额外参数。')
                    from album_reset import reset
                    result = reset(self.server, self.read_json(1024))
                    clear_profile = getattr(self.server, 'clear_profile', None)
                    if clear_profile is not None:
                        try:
                            clear_profile()
                        except Exception:
                            result['cleanup_pending'] = True
                    self.send_json(200, result)
                elif video_create:
                    self.send_json(202, {"job": self.server.video_jobs.start(video_create.group(1), self.read_json())})
                elif path == "/api/settings":
                    self.send_json(200, store.update_settings(self.read_json()))
                elif path == '/api/opening-lines':
                    self.send_json(201, store.add_opening_line(self.read_json(8192)))
                elif path in {"/api/music/online/search", "/api/music/online/preview", "/api/music/online/import"}:
                    methods = {"search": self.server.online_music.search, "preview": self.server.online_music.preview, "import": self.server.online_music.import_track}
                    result = methods[path.rsplit("/", 1)[1]](self.read_json(8192))
                    self.send_json(201 if path.endswith("/import") and not result.get("duplicate") else 200, result)
                elif path == "/api/music/import":
                    content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type not in AUDIO_IMPORT_TYPES:
                        raise ApiError(415, "请直接上传受支持的音乐文件。")
                    result = store.import_music(self.read_body(MAX_MUSIC_BYTES, "每首音乐最多可导入 100 MB。"), query.get("filename", [""])[0])
                    self.send_json(200 if result["duplicate"] else 201, result)
                elif re.fullmatch(r"/api/music/[a-z0-9][a-z0-9_-]{0,63}/remove", path):
                    self.send_json(200, store.remove_music(path.split("/")[3]))
                elif re.fullmatch(r"/api/music/[a-z0-9][a-z0-9_-]{0,63}", path):
                    self.send_json(200, store.update_music(path.rsplit("/", 1)[1], self.read_json()))
                elif path == "/api/import":
                    content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type not in IMPORT_TYPES:
                        raise ApiError(415, "请直接上传受支持的照片文件。")
                    data = self.read_body(MAX_PHOTO_BYTES)
                    result = store.import_photo(data, query.get("filename", [""])[0], query.get("modified", [None])[0], query.get("place", [""])[0], query.get("trip", [""])[0], query.get("collection_id", [None])[0], query.get("country", [""])[0], query.get("city", [""])[0])
                    self.send_json(200 if result["duplicate"] else 201, result)
                elif path == "/api/collections":
                    self.send_json(201, store.create_collection(self.read_json()))
                elif path == "/api/atlas":
                    self.send_json(200, store.update_atlas(self.read_json(MAX_ATLAS_JSON_BYTES)))
                elif re.fullmatch(r"/api/collections/[a-f0-9]{32}/route", path):
                    self.send_json(200, store.update_route(path.split("/")[3], self.read_json()))
                elif re.fullmatch(r"/api/collections/[a-f0-9]{32}/photos", path):
                    self.send_json(200, store.add_collection_photos(path.split("/")[3], self.read_json()))
                elif re.fullmatch(r"/api/collections/[a-f0-9]{32}/order", path):
                    self.send_json(200, store.reorder_collection_photos(path.split("/")[3], self.read_json(512 * 1024)))
                elif re.fullmatch(r"/api/collections/[a-f0-9]{32}", path):
                    self.send_json(200, store.update_collection(path.rsplit("/", 1)[1], self.read_json()))
                elif re.fullmatch(r"/api/photos/[a-f0-9]{32}", path):
                    self.send_json(200, store.update_photo(path.rsplit("/", 1)[1], self.read_json()))
                elif path.startswith("/api/journals/"):
                    self.send_json(200, store.update_journal(path[len("/api/journals/"):], self.read_json()))
                else:
                    raise ApiError(404, "没有找到这个接口。")
            elif self.command == 'DELETE' and re.fullmatch(r'/api/opening-lines/[a-z][a-z0-9_-]{0,63}', path):
                self.send_json(200, store.delete_opening_line(path.rsplit('/', 1)[1]))
            elif self.command == "DELETE" and video_job and not video_job.group(2):
                self.send_json(200, {"job": self.server.video_jobs.cancel(video_job.group(1))})
            elif self.command == "DELETE" and re.fullmatch(r"/api/collections/[a-f0-9]{32}/photos", path):
                self.send_json(200, store.remove_collection_photos(path.split("/")[3], self.read_json()))
            elif self.command == "DELETE" and re.fullmatch(r"/api/collections/[a-f0-9]{32}", path):
                self.send_json(200, store.delete_collection(path.rsplit("/", 1)[1]))
            elif self.command == "DELETE" and re.fullmatch(r"/api/photos/[a-f0-9]{32}", path):
                self.send_json(200, store.delete_photo(path.rsplit("/", 1)[1]))
            elif self.command == "DELETE" and path.startswith("/api/journals/"):
                self.send_json(200, store.delete_journal(path[len("/api/journals/"):]))
            else:
                raise ApiError(405, "不支持这个操作。")
        except (ApiError, VideoJobError, ColorAnalysisError, OnlineMusicError) as error:
            self.close_connection = True
            self.send_json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            self.close_connection = True
            self.send_json(500, {"error": "本地相册暂时无法完成操作，请检查磁盘空间后重试。"})

    def read_body(self, maximum: int, too_large_message: str | None = None) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "不支持这种上传方式。")
        length = self.headers.get("Content-Length")
        if length is None:
            raise ApiError(411, "上传文件缺少长度信息。")
        try:
            size = int(length)
        except ValueError:
            raise ApiError(400, "上传长度格式不正确。") from None
        if size < 0:
            raise ApiError(400, "上传长度格式不正确。")
        if size > maximum:
            raise ApiError(413, too_large_message or ("每张照片最多可导入 100 MB。" if maximum == MAX_PHOTO_BYTES else "保存的文字过长。"))
        self.connection.settimeout(60)
        data = self.rfile.read(size)
        if len(data) != size:
            raise ApiError(400, "文件上传不完整，请重试。")
        return data

    def read_json(self, maximum: int = MAX_JSON_BYTES) -> dict:
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ApiError(415, "资料需要使用 JSON 格式。")
        try:
            value = json.loads(self.read_body(maximum).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "资料格式不正确，请重新输入。") from None

    def send_json(self, status: int, value: dict, attachment: str | None = None):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if attachment:
            self.send_header("Content-Disposition", "attachment; filename=album-metadata.json; filename*=UTF-8''" + quote(attachment))
        self.common_headers(len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def common_headers(self, length: int):
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def send_backup(self):
        with self.server.store.backup_archive(self.server.application_file) as path:
            with path.open("rb") as content:
                filename = "拾光相册完整迁移包-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", "attachment; filename=shiguang-backup.zip; filename*=UTF-8''" + quote(filename))
                self.send_header("Cache-Control", "no-store")
                self.common_headers(os.fstat(content.fileno()).st_size)
                if self.command != "HEAD":
                    self.stream_backup(content)

    def stream_backup(self, content):
        while chunk := content.read(1024 * 1024):
            self.wfile.write(chunk)

    def send_framed_photo(self, photo_id: str, preview: bool):
        data, photo = self.server.store.framed_photo(photo_id, self.server.web_dir / "assets", preview=preview)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "no-store")
        if preview:
            self.send_header("Content-Disposition", "inline")
        else:
            title = photo.get("title", "").strip() or Path(photo["filename"]).stem
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title)[:100].strip(" .") or "照片"
            filename = title + "-白边.jpg"
            self.send_header("Content-Disposition", "attachment; filename=photo-framed.jpg; filename*=UTF-8''" + quote(filename))
        self.common_headers(len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_video_file(self, job_id: str, download: bool):
        with self.server.video_jobs.open_file(job_id) as (content, filename):
            disposition = ("attachment" if download else "inline") + "; filename=collection-slideshow.mp4; filename*=UTF-8''" + quote(filename)
            self.send_open_file(content, "video/mp4", "no-store", disposition)

    def send_file(self, path: str):
        music = re.fullmatch(r"/media/music/([a-f0-9]{32})(\.(?:mp3|wav|m4a|aac|ogg|flac))", path)
        if music:
            base = self.server.store.music_dir
            name = self.server.store.music_path(music.group(1), music.group(2)).name
        elif path.startswith("/media/"):
            match = re.fullmatch(r"/media/(originals|previews)/([a-f0-9]{32}\.(?:jpg|png|webp|gif|bmp|tif))", path)
            if not match:
                raise ApiError(404, "没有找到这张照片。")
            base = self.server.store.originals if match.group(1) == "originals" else self.server.store.previews
            name = match.group(2)
        else:
            base = self.server.web_dir
            name = "index.html" if path == "/" else path.lstrip("/")
            if "\\" in name or any(part.startswith(".") for part in name.split("/")) or ":" in name:
                raise ApiError(404, "没有找到这个页面。")
        full_path = (base / name).resolve()
        if not full_path.is_relative_to(base) or not full_path.is_file():
            raise ApiError(404, "文件不存在，可能已经从相册移除。")
        content_type = AUDIO_MIME.get(full_path.suffix.lower()) or mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        try:
            content = full_path.open("rb")
        except OSError:
            raise ApiError(404, "文件暂时无法读取，请稍后重试。") from None
        with content:
            self.send_open_file(content, content_type, "private, max-age=3600" if path.startswith("/media/") else "no-cache")

    def send_open_file(self, content, content_type: str, cache_control: str, disposition: str | None = None):
        size = os.fstat(content.fileno()).st_size
        start, end, status = 0, size - 1, 200
        requested_range = self.headers.get("Range")
        if requested_range is not None:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range.strip(), flags=re.IGNORECASE)
            try:
                if not match or not any(match.groups()) or not size:
                    raise ValueError
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                    if start >= size or end < start:
                        raise ValueError
                else:
                    suffix = int(last)
                    if suffix <= 0:
                        raise ValueError
                    start = max(0, size - suffix)
                status = 206
            except ValueError:
                data = json.dumps({"error": "请求的音频或文件范围无效。"}, ensure_ascii=False).encode("utf-8")
                self.send_response(416)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.common_headers(len(data))
                if self.command != "HEAD":
                    self.wfile.write(data)
                return
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", cache_control)
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.common_headers(length)
        if self.command != "HEAD":
            content.seek(start)
            remaining = length
            while remaining:
                chunk = content.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


if __name__ == "__main__":
    raise SystemExit("这是桌面应用的内部组件。请运行 desktop.py，或双击「拾光相册.exe」打开相册。")
