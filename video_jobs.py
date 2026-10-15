"""Bounded, temporary slideshow jobs; original album files remain read-only."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid


LOG = logging.getLogger(__name__)
_ID = re.compile(r"[a-f0-9]{32}")
_MARKER = ".shiguang-video-job"
_MARKER_CONTENT = "shiguang-video-export-v1"
_ACTIVE = {"preparing", "encoding"}
_RETENTION = 3600


class VideoJobError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


class _Cancelled(Exception):
    pass


@dataclass
class _Job:
    id: str
    directory: Path
    title: str
    options: dict
    photos: list
    music: dict | None
    duration: float
    status: str = "preparing"
    progress: float = 0
    message: str = "正在准备照片与音乐…"
    error: str | None = None
    finished_at: float | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    readers: int = 0
    retired: bool = False


class VideoJobManager:
    def __init__(self, store, assets_dir: Path):
        self.store, self.assets_dir = store, Path(assets_dir)
        self.root = store.root / ".video-exports"
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._active: str | None = None
        self._closed = False
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        # Recover only directories carrying our UUID name and ownership marker.
        # Unknown files, links, junctions and other data are never removed.
        if self.root.exists():
            try:
                self._ensure_root()
                for child in self.root.iterdir():
                    self._remove_directory(child)
            except (OSError, VideoJobError):
                LOG.warning("Video export workspace is unavailable", exc_info=True)

    @staticmethod
    def options(value: dict) -> dict:
        allowed = {"resolution", "seconds_per_photo", "order", "music_id", "volume"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise VideoJobError(400, "视频导出设置中包含不支持的字段。")
        result = {"resolution": "1080p", "seconds_per_photo": 5, "order": "oldest", "music_id": None, "volume": 0.5, **value}
        if result["resolution"] not in ("1080p", "720p"):
            raise VideoJobError(400, "请选择 1080p 或 720p 清晰度。")
        if type(result["seconds_per_photo"]) is not int or result["seconds_per_photo"] not in (3, 4, 5, 6, 8, 10):
            raise VideoJobError(400, "每张照片的时长应为 3、4、5、6、8 或 10 秒。")
        if result["order"] not in ("oldest", "newest"):
            raise VideoJobError(400, "请选择按拍摄时间由早到晚或由晚到早。")
        music_id = result["music_id"]
        if music_id is not None and (not isinstance(music_id, str) or not _ID.fullmatch(music_id)):
            raise VideoJobError(400, "请选择自行导入的背景音乐，或选择无配乐。")
        volume = result["volume"]
        try:
            valid_volume = not isinstance(volume, bool) and isinstance(volume, (int, float)) and math.isfinite(volume) and 0 <= volume <= 1
        except OverflowError:
            valid_volume = False
        if not valid_volume:
            raise VideoJobError(400, "音乐音量应为 0 到 1 之间的数值。")
        return result

    def _ensure_root(self):
        if self.root.is_symlink() or self.root.is_junction():
            raise VideoJobError(503, "视频临时目录路径不安全，请检查相册资料目录。")
        self.root.mkdir(exist_ok=True)
        if not self.root.is_dir() or self.root.resolve() != self.root:
            raise VideoJobError(503, "视频临时目录路径不安全，请检查相册资料目录。")

    def _owned_directory(self, path: Path) -> bool:
        try:
            if path.parent != self.root or not _ID.fullmatch(path.name):
                return False
            if self.root.is_symlink() or self.root.is_junction() or self.root.resolve() != self.root:
                return False
            if path.is_symlink() or path.is_junction() or not path.is_dir() or path.resolve() != path:
                return False
            marker = path / _MARKER
            return not marker.is_symlink() and marker.is_file() and marker.read_text(encoding="ascii") == _MARKER_CONTENT
        except (OSError, ValueError, RuntimeError):
            return False

    def _remove_directory(self, path: Path):
        if self._owned_directory(path):
            try:
                shutil.rmtree(path)
            except OSError:
                LOG.warning("Could not clean a video export workspace", exc_info=True)

    def _media(self, directory: Path, name: str, label: str) -> Path:
        try:
            return self.store.backup_media_path(directory, name, label)
        except Exception:
            raise VideoJobError(404, f"{label}副本缺失或路径不安全，无法导出视频。") from None

    def _collection(self, collection_id: str, order: str, db, limit: int = 301):
        if not isinstance(collection_id, str) or not _ID.fullmatch(collection_id):
            raise VideoJobError(404, "这份作品集不存在。")
        collection = db.execute("SELECT title FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if not collection:
            raise VideoJobError(404, "这份作品集不存在。")
        direction = "ASC" if order == "oldest" else "DESC"
        photos = db.execute("SELECT p.* FROM photos p JOIN collection_photos cp ON cp.photo_id = p.id WHERE cp.collection_id = ? ORDER BY p.taken_at " + direction + ",p.imported_at " + direction + ",p.id " + direction + " LIMIT ?", (collection_id, limit)).fetchall()
        if not photos:
            raise VideoJobError(400, "这份作品集还没有照片，请先添加照片。")
        return collection["title"], photos

    def _photo_source(self, row) -> Path:
        if not _ID.fullmatch(row["id"]) or row["extension"] not in (".jpg", ".png", ".webp", ".gif", ".bmp", ".tif"):
            raise VideoJobError(404, "照片文件标识异常，无法导出视频。")
        return self._media(self.store.originals, row["id"] + row["extension"], "原图")

    def preview(self, collection_id: str, order: str = "oldest") -> bytes:
        self.options({"order": order})
        with self.store.lock, self.store.connect() as db:
            _title, photos = self._collection(collection_id, order, db, limit=1)
            original = self._photo_source(photos[0])
            try:
                from video_export import render_video_preview, VideoExportError
            except ImportError:
                raise VideoJobError(503, "视频导出组件暂时不可用，请重新安装完整程序。") from None
            try:
                return render_video_preview(original, self.store.photo(photos[0]), self.assets_dir)
            except VideoExportError as error:
                raise VideoJobError(400, str(error)) from None

    def start(self, collection_id: str, value: dict) -> dict:
        options = self.options(value)
        self._prune()
        with self._lock:
            if self._closed:
                raise VideoJobError(503, "相册正在关闭，无法开始新的视频导出。")
            if self._active is not None:
                raise VideoJobError(409, "已有视频正在导出，请等待完成或先取消当前任务。")
        with self.store.lock, self.store.connect() as db:
            title, photos = self._collection(collection_id, options["order"], db)
            if len(photos) > 300:
                raise VideoJobError(400, "每次最多可导出 300 张照片。")
            duration = round(len(photos) * options["seconds_per_photo"] - max(0, len(photos) - 1) * 0.8, 2)
            if duration > 1800:
                raise VideoJobError(400, "视频总时长最多为 30 分钟，请减少照片或缩短每张停留时间。")
            records = []
            for row in photos:
                original = self._photo_source(row)
                records.append((original.name, self.store.photo(row)))
            music = None
            if options["music_id"] is not None:
                row = db.execute("SELECT * FROM music_tracks WHERE id = ? AND origin = 'imported' AND removed = 0", (options["music_id"],)).fetchone()
                if not row or row["extension"] not in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
                    raise VideoJobError(400, "请选择尚未移除的自行导入音乐；内置音乐不能用于视频配乐。")
                name = row["id"] + row["extension"]
                self._media(self.store.music_dir, name, "导入音乐")
                music = {"name": name}
        with self._lock:
            if self._closed or self._active is not None:
                raise VideoJobError(409, "已有视频正在导出，或相册正在关闭。")
            self._ensure_root()
            job_id = uuid.uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            try:
                (directory / _MARKER).write_text(_MARKER_CONTENT, encoding="ascii")
                job = _Job(job_id, directory, title, options, records, music, duration)
                self._jobs[job_id] = job
                self._active = job_id
                job.thread = threading.Thread(target=self._run, args=(job,), name="album-video-export", daemon=True)
                job.thread.start()
                if self._reaper is None:
                    self._reaper = threading.Thread(target=self._reap_loop, name="album-video-cleanup", daemon=True)
                    self._reaper.start()
                return self._public(job)
            except Exception:
                self._active = None
                self._jobs.pop(job_id, None)
                self._remove_directory(directory)
                raise

    def _snapshot_file(self, job: _Job, source_dir: Path, name: str, target: Path, label: str):
        if job.cancelled.is_set():
            raise _Cancelled
        # Each original is immutable. A same-volume hard link is quick and
        # survives an album deletion; fallback copies hold only this file's lock.
        with self.store.lock:
            source = self._media(source_dir, name, label)
            try:
                os.link(source, target)
            except OSError:
                # Exclusive creation also prevents following a pre-existing
                # target link if a temporary directory was tampered with.
                with source.open("rb") as incoming, target.open("xb") as outgoing:
                    while chunk := incoming.read(1024 * 1024):
                        if job.cancelled.is_set():
                            raise _Cancelled
                        outgoing.write(chunk)

    def _run(self, job: _Job):
        try:
            sources = job.directory / "sources"
            sources.mkdir()
            slides = []
            for name, photo in job.photos:
                target = sources / name
                self._snapshot_file(job, self.store.originals, name, target, "原图")
                slides.append((target, photo))
            music_path = None
            if job.music:
                music_path = sources / job.music["name"]
                self._snapshot_file(job, self.store.music_dir, job.music["name"], music_path, "导入音乐")
            if job.cancelled.is_set():
                raise _Cancelled
            with self._lock:
                job.status, job.message = "encoding", "正在制作视频…"
            from video_export import encode_slideshow
            result = encode_slideshow(slides, self.assets_dir, music_path, job.directory / "slideshow.mp4", dict(job.options), lambda value, message: self._progress(job, value, message), job.cancelled)
            if job.cancelled.is_set():
                raise _Cancelled
            output = job.directory / "slideshow.mp4"
            if not self._owned_directory(job.directory) or output.is_symlink() or output.is_junction() or not output.is_file() or output.stat().st_size == 0:
                raise VideoJobError(500, "视频文件未能完整生成，请重试。")
            self._trim_inputs(job)
            with self._lock:
                duration = result.get("duration", job.duration) if isinstance(result, dict) else job.duration
                if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0:
                    job.duration = round(duration, 2)
                job.status, job.progress, job.message = "completed", 100, "视频已准备好，可以播放或保存。"
        except Exception as error:
            with self._lock:
                if job.cancelled.is_set() or isinstance(error, _Cancelled):
                    job.status, job.message = "cancelled", "视频导出已取消。"
                else:
                    try:
                        from video_export import VideoExportError
                        known = isinstance(error, (VideoExportError, VideoJobError))
                    except ImportError:
                        known = isinstance(error, VideoJobError)
                    job.status = "failed"
                    job.error = str(error)[:500] if known else "视频导出未完成，请检查磁盘空间后重试。"
                    job.message = job.error
                    LOG.warning("Video export failed", exc_info=True)
        finally:
            with self._lock:
                job.finished_at = time.monotonic()
                if self._active == job.id:
                    self._active = None
                remove = self._closed or job.status != "completed"
            if remove:
                self._remove_directory(job.directory)
            self._prune()

    def _progress(self, job: _Job, value: float, message: str):
        with self._lock:
            if job.cancelled.is_set() or job.status not in _ACTIVE:
                return
            if isinstance(value, (int, float)) and math.isfinite(value):
                job.progress = max(job.progress, min(99, max(0, value)))
            if isinstance(message, str):
                job.message = message[:300]

    def _trim_inputs(self, job: _Job):
        if not self._owned_directory(job.directory):
            return
        for child in job.directory.iterdir():
            if child.name in {_MARKER, "slideshow.mp4"}:
                continue
            try:
                if child.is_symlink():
                    child.unlink()
                elif child.is_junction():
                    child.rmdir()
                elif child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                LOG.warning("Could not clean video intermediates", exc_info=True)

    def _public(self, job: _Job) -> dict:
        completed = job.status == "completed" and not job.retired
        file_url = f"/api/video-jobs/{job.id}/file"
        return {"id": job.id, "status": job.status, "progress": job.progress, "message": job.message, "photo_count": len(job.photos), "duration": job.duration, "download_url": file_url + "?download=1" if completed else None, "video_url": file_url if completed else None, "error": job.error}

    def _require(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id) if isinstance(job_id, str) and _ID.fullmatch(job_id) else None
        if not job:
            raise VideoJobError(404, "这个视频任务不存在或已过期，请重新导出。")
        return job

    def get(self, job_id: str) -> dict:
        self._prune()
        with self._lock:
            return self._public(self._require(job_id))

    def cancel(self, job_id: str) -> dict:
        self._prune()
        with self._lock:
            job = self._require(job_id)
            if job.status in _ACTIVE:
                job.cancelled.set()
                job.message = "正在取消视频导出…"
            return self._public(job)

    @contextmanager
    def open_file(self, job_id: str):
        self._prune()
        with self._lock:
            job = self._require(job_id)
            if job.status != "completed":
                raise VideoJobError(409, "视频尚未完成，暂时无法播放或下载。")
            output = job.directory / "slideshow.mp4"
            if not self._owned_directory(job.directory) or output.is_symlink() or output.is_junction() or not output.is_file():
                raise VideoJobError(404, "视频文件已清理，请重新导出。")
            content = output.open("rb")
            job.readers += 1
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", job.title)[:100].strip(" .") or "作品集"
        try:
            yield content, title + "-幻灯片.mp4"
        finally:
            content.close()
            with self._lock:
                job.readers -= 1
                remove = job.retired and job.readers == 0
            if remove:
                self._remove_directory(job.directory)

    def _prune(self):
        remove = []
        with self._lock:
            finished = [job for job in self._jobs.values() if job.finished_at is not None and job.id != self._active]
            completed = sorted((job for job in finished if job.status == "completed"), key=lambda job: job.finished_at, reverse=True)
            stale = {job.id for job in finished if time.monotonic() - job.finished_at >= _RETENTION}
            stale.update(job.id for job in completed[3:])
            # Bound failed/cancelled status records as well as video files.
            other = sorted((job for job in finished if job.status != "completed"), key=lambda job: job.finished_at, reverse=True)
            stale.update(job.id for job in other[20:])
            for job_id in stale:
                job = self._jobs.pop(job_id)
                job.retired = True
                if not job.readers:
                    remove.append(job.directory)
        for directory in remove:
            self._remove_directory(directory)

    def _reap_loop(self):
        while not self._stop.wait(30):
            self._prune()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            jobs = list(self._jobs.values())
            for job in jobs:
                job.cancelled.set()
        for job in jobs:
            if job.thread and job.thread.is_alive():
                job.thread.join(15)
        if self._reaper:
            self._reaper.join(2)
        with self._lock:
            self._jobs.clear()
            for job in jobs:
                job.retired = True
        for job in jobs:
            if not job.readers and not (job.thread and job.thread.is_alive()):
                self._remove_directory(job.directory)
