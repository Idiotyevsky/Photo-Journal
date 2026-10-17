"""Transactional initialization of app-owned data; never touches import sources."""
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import re
import shutil
import threading
import uuid

LOG = logging.getLogger(__name__)
NAMES = ('originals', 'previews', 'music', '.delete-staging', '.video-exports',
         '.color-cache.sqlite3', '.color-cache.sqlite3-wal', '.color-cache.sqlite3-shm')
TABLES = ('collection_routes', 'collection_photos', 'collections', 'photos',
          'journals', 'journal_deletions', 'atlas_route', 'music_tracks', 'app_settings')


class RequestGate:
    """Normal requests run concurrently; reset only starts when none are in flight."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.resetting = False

    @contextmanager
    def enter(self, exclusive=False):
        from server import ApiError
        with self.lock:
            if self.resetting or (exclusive and self.active):
                raise ApiError(409, '相册仍有操作进行中，请等待导入、导出或下载完成后重试。')
            if exclusive:
                self.resetting = True
            else:
                self.active += 1
        try:
            yield
        finally:
            with self.lock:
                if exclusive:
                    self.resetting = False
                else:
                    self.active -= 1


def safe_tree(path, root):
    """Validate every deletion target before staging or recursive removal."""
    if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
        raise OSError('Unsafe initialization path')
    if path.is_dir():
        for child in path.iterdir():
            safe_tree(child, root)


def clean_staged(staged):
    # Keep the recovery manifest until the last byte of staged media is gone.
    for child in staged.iterdir():
        if child.name == 'manifest.json':
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    (staged / 'manifest.json').unlink(missing_ok=True)
    staged.rmdir()


def recover(store):
    """A durable manifest + SQLite token decide rollback versus committed cleanup."""
    staged = store.root / '.initialization'
    if not staged.exists() and not staged.is_symlink():
        return
    safe_tree(staged, store.root)
    try:
        manifest = json.loads((staged / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        # No media can have moved until a complete manifest is durably written.
        if not set(child.name for child in staged.iterdir()) - {'manifest.json'}:
            shutil.rmtree(staged)
            return
        raise
    if not isinstance(manifest, dict) or set(manifest) != {'token', 'names'} or not isinstance(manifest['token'], str) or not re.fullmatch('[a-f0-9]{32}', manifest['token']) or not isinstance(manifest['names'], list) or len(set(manifest['names'])) != len(manifest['names']) or any(name not in NAMES for name in manifest['names']):
        raise OSError('Invalid initialization recovery record')
    if set(child.name for child in staged.iterdir()) - {'manifest.json', *manifest['names']}:
        raise OSError('Unexpected initialization recovery files')
    with store.connect() as db:
        row = db.execute("SELECT value_json FROM app_settings WHERE key='initialization-token'").fetchone()
    committed = bool(row and row[0] == manifest['token'])
    if not committed:
        for name in manifest['names']:
            source, destination = staged / name, store.root / name
            if source.exists():
                if destination.exists():
                    # Startup creates only these two empty directories before recovery.
                    if name not in ('originals', 'previews') or not destination.is_dir() or any(destination.iterdir()):
                        raise OSError('Initialization recovery destination already exists')
                    destination.rmdir()
                source.replace(destination)
    clean_staged(staged)


def reset(server, value):
    from server import ApiError
    if value != {'confirmation': '清空相册'}:
        raise ApiError(400, '请先输入「清空相册」并确认初始化。')
    store, jobs = server.store, server.video_jobs
    # RequestGate excludes API readers/writers; the job lock excludes background exports.
    with jobs._lock, server.colors._lock, store.lock:
        if any(job.readers or (job.thread and job.thread.is_alive()) for job in jobs._jobs.values()):
            raise ApiError(409, '视频仍在制作或下载，请结束任务后再初始化。')
        staged = store.root / '.initialization'
        try:
            recover(store)
            safe_tree(store.database, store.root)
            names = [name for name in NAMES if (store.root / name).exists() or (store.root / name).is_symlink()]
            for name in names:
                safe_tree(store.root / name, store.root)
            token = uuid.uuid4().hex
            staged.mkdir()
            # Write the full plan before the first rename, including crash recovery.
            with (staged / 'manifest.json').open('w', encoding='utf-8') as output:
                json.dump({'token': token, 'names': names}, output)
                output.flush()
                import os
                os.fsync(output.fileno())
            try:
                for name in names:
                    (store.root / name).replace(staged / name)
                with store.connect() as db:
                    for table in TABLES:
                        db.execute('DELETE FROM ' + table)
                    db.execute("INSERT INTO app_settings(key,value_json) VALUES('initialization-token',?)", (token,))
                    db.commit()
            except Exception:
                recover(store)
                raise
            jobs._jobs.clear()
            jobs._active = None
            server.colors._memory.clear()
            with server.online_music._lock:
                server.online_music._searches.clear()
                server.online_music._records.clear()
                server.online_music._urls.clear()
            for directory in (store.originals, store.previews):
                directory.mkdir(exist_ok=True)
            # Committed cleanup may be retried on next launch if Windows holds a file.
            cleanup_pending = False
            try:
                recover(store)
            except OSError:
                cleanup_pending = True
                LOG.warning('Initialization committed; cleanup will retry at startup', exc_info=True)
            try:
                with store.connect() as db:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    db.execute('VACUUM')
            except Exception:
                LOG.warning('Initialization committed; database compaction deferred', exc_info=True)
            return {'initialized': True, 'cleanup_pending': cleanup_pending}
        except OSError:
            raise ApiError(409, '初始化未能完成，请关闭占用相册文件的程序后重试；未提交的资料会保留。') from None
