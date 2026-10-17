"""Destructive initialization checks run ONLY against disposable synthetic albums."""
from contextlib import contextmanager
from io import BytesIO
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
import album_reset
from server import AlbumHTTPServer, AlbumStore, ROOT


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shiguang-reset-test-')
        self.root = Path(self.temp.name)
        self.executable = self.root / 'app.exe'
        self.executable.write_bytes(b'MZ-preserved-program')
        self.server = AlbumHTTPServer(0, self.root / 'disposable-album', application_file=self.executable)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.store = self.server.store
        image = BytesIO()
        Image.new('RGB', (40, 30), 'coral').save(image, 'JPEG')
        self.source = self.root / 'import-source.jpg'
        self.source.write_bytes(image.getvalue())
        self.photo = self.store.import_photo(image.getvalue(), self.source.name, None)['photo']
        self.store.update_settings({'theme': 'italian', 'music_volume': .6})
        self.store.add_opening_line({'text': '仅供临时相册测试的句子。'})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, method='POST', payload=None, headers=None, path='/api/initialize'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request(method, path, json.dumps(payload if payload is not None else {'confirmation': '清空相册'}).encode(), {'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        body = json.loads(response.read())
        status = response.status
        connection.close()
        return status, body

    def snapshot(self):
        with self.store.connect() as db:
            records = {name: [tuple(row) for row in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')] for name in album_reset.TABLES}
        files = {str(path.relative_to(self.store.root)): path.read_bytes() for directory in (self.store.originals, self.store.previews, self.store.music_dir) if directory.exists() for path in directory.rglob('*') if path.is_file()}
        return records, files

    def assert_empty(self):
        with self.store.connect() as db:
            for name in album_reset.TABLES:
                rows = db.execute('SELECT * FROM ' + name).fetchall()
                self.assertEqual(len(rows), 1 if name == 'app_settings' else 0, name)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.store.settings(), {'theme': 'classic', 'music_volume': .22})
        self.assertEqual(len(self.store.opening_lines()['lines']), 100)
        self.assertEqual(list(self.store.originals.iterdir()), [])
        self.assertEqual(list(self.store.previews.iterdir()), [])
        self.assertFalse((self.store.root / '.initialization').exists())

    def test_complete_reset_preserves_program_sources_builtins_and_can_import_again(self):
        collection = self.store.create_collection({'title': '测试作品集'})['collection']
        with self.store.connect() as db:
            self.store.attach_collection_photos(db, collection['id'], [self.photo['id']])
            db.execute("INSERT INTO journals VALUES('trip:test','title','note','today')")
            db.execute("INSERT INTO journal_deletions VALUES('month:2026-01','today')")
            db.execute("INSERT INTO atlas_route VALUES('default','[]','today')")
            db.execute("INSERT INTO collection_routes VALUES(?,'[]','today')", (collection['id'],))
            db.execute("INSERT INTO music_tracks(id,origin,title,imported_at) VALUES('test','imported','music','today')")
        self.store.music_dir.mkdir()
        (self.store.music_dir / 'test.mp3').write_bytes(b'only-test-audio')
        (self.store.root / '.color-cache.sqlite3').write_bytes(b'test-cache')
        for name in ('.video-exports', '.delete-staging'):
            (self.store.root / name).mkdir()
            (self.store.root / name / 'test').write_bytes(b'test-temp')
        builtin = ROOT / 'ui' / 'audio' / 'tracks.json'
        before = builtin.read_bytes()
        external = self.root / 'saved-work.jpg'
        external.write_bytes(b'already-exported')
        status, body = self.request()
        self.assertEqual((status, body), (200, {'initialized': True, 'cleanup_pending': False}))
        self.assert_empty()
        for name in album_reset.NAMES[2:]:
            self.assertFalse((self.store.root / name).exists(), name)
        self.assertEqual(self.executable.read_bytes(), b'MZ-preserved-program')
        self.assertEqual(self.source.read_bytes()[:2], b'\xff\xd8')
        self.assertEqual(external.read_bytes(), b'already-exported')
        self.assertEqual(builtin.read_bytes(), before)
        self.assertTrue(all(track['origin'] == 'builtin' for track in self.store.music()['tracks']))
        reopened = AlbumStore(self.store.root)
        self.assertEqual(reopened.photos()['photos'], [])
        self.assertFalse(reopened.import_photo(self.source.read_bytes(), 'new.jpg', None)['duplicate'])
        self.assertEqual(self.request()[0], 200)
        self.assert_empty()

    def test_confirmation_origin_method_and_parameters_cannot_accidentally_clear_data(self):
        before = self.snapshot()
        for payload in ({}, {'confirmation': True}, {'confirmation': '清空'}, {'confirmation': '清空相册', 'extra': True}, []):
            self.assertEqual(self.request(payload=payload)[0], 400)
        for headers in ({'Origin': 'https://example.com'}, {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}):
            self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(self.request(method='GET')[0], 404)
        self.assertEqual(self.request(path='/api/initialize?x=1')[0], 400)
        self.assertEqual(self.snapshot(), before)

    def test_failed_commit_restores_every_record_and_file(self):
        before, original = self.snapshot(), self.store.connect

        class FailedCommit:
            def __init__(self, db): self.db = db
            def __getattr__(self, name): return getattr(self.db, name)
            def commit(self): raise OSError('injected commit failure')

        @contextmanager
        def connect():
            with original() as db:
                yield FailedCommit(db)

        with patch.object(self.store, 'connect', connect):
            self.assertEqual(self.request()[0], 409)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.store.root / '.initialization').exists())

    def test_partial_move_failure_rolls_back_and_gate_releases(self):
        before, replace = self.snapshot(), Path.replace

        def failure(path, target):
            if path == self.store.previews: raise PermissionError('injected locked file')
            return replace(path, target)

        with patch.object(Path, 'replace', failure):
            self.assertEqual(self.request()[0], 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.request()[0], 200)
        self.assert_empty()

    def test_startup_recovers_interrupted_moves_or_cleans_committed_reset(self):
        before = self.snapshot()
        staged = self.store.root / '.initialization'
        staged.mkdir()
        token = 'a' * 32
        (staged / 'manifest.json').write_text(json.dumps({'token': token, 'names': ['originals', 'previews']}))
        self.store.originals.replace(staged / 'originals')
        AlbumStore(self.store.root)
        self.assertEqual(self.snapshot(), before)
        staged.mkdir()
        (staged / 'manifest.json').write_text(json.dumps({'token': token, 'names': ['originals', 'previews']}))
        self.store.originals.replace(staged / 'originals')
        self.store.previews.replace(staged / 'previews')
        with self.store.connect() as db:
            for table in album_reset.TABLES: db.execute('DELETE FROM ' + table)
            db.execute("INSERT INTO app_settings VALUES('initialization-token',?)", (token,))
        AlbumStore(self.store.root)
        self.assert_empty()
        staged.mkdir()
        (staged / 'manifest.json').write_text('{')
        AlbumStore(self.store.root)
        self.assert_empty()

    def test_inflight_requests_and_video_jobs_block_reset_without_changing_data(self):
        before = self.snapshot()
        for exclusive in (False, True):
            with self.server.request_gate.enter(exclusive):
                self.assertEqual(self.request()[0], 409)
        job = SimpleNamespace(readers=0, thread=SimpleNamespace(is_alive=lambda: True))
        self.server.video_jobs._jobs['test'] = job
        try:
            self.assertEqual(self.request()[0], 409)
            job.thread.is_alive = lambda: False
            job.readers = 1
            self.assertEqual(self.request()[0], 409)
        finally:
            self.server.video_jobs._jobs.clear()
        self.assertEqual(self.snapshot(), before)

    def test_linked_managed_paths_refused_before_any_mutation(self):
        before = self.snapshot()
        original = Path.is_junction
        with patch.object(Path, 'is_junction', lambda path: path == self.store.previews or original(path)):
            self.assertEqual(self.request()[0], 409)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
