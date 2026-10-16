"""Portfolio order, conflict handling and old-album migration use disposable data."""
import hashlib
from contextlib import contextmanager
import http.client
import io
import json
from pathlib import Path
import tempfile
import sqlite3
import threading
import unittest
import zipfile

from PIL import Image
import server


class CollectionOrderTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.application=self.root/'album.exe';self.application.write_bytes(b'MZ-test')
        self.service=server.AlbumHTTPServer(0,self.root/'data',application_file=self.application)
        self.thread=threading.Thread(target=self.service.serve_forever,daemon=True);self.thread.start()
        self.store=self.service.store
        self.photos=[];self.bytes=[]
        for index,color in enumerate(('red','blue','green','yellow')):
            buffer=io.BytesIO();Image.new('RGB',(30,20),color).save(buffer,'PNG');data=buffer.getvalue();self.bytes.append(data)
            self.photos.append(self.store.import_photo(data,f'{index}.png',f'2025-01-0{(3,1,2,4)[index]}T12:00:00')['photo'])
        self.ids=[photo['id'] for photo in self.photos]
        self.collection=self.store.create_collection({'title':'旅行编排'})['collection']
        self.cid=self.collection['id']
        self.store.add_collection_photos(self.cid,{'photo_ids':self.ids[:3]})

    def tearDown(self):
        self.service.shutdown();self.service.server_close();self.thread.join();self.temporary.cleanup()

    def request(self,path,payload,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.service.server_port,timeout=5)
        connection.request('POST',path,json.dumps(payload).encode(),{'Content-Type':'application/json',**(headers or {})})
        response=connection.getresponse();status=response.status;data=json.loads(response.read());connection.close();return status,data

    def current(self):
        with self.store.connect() as db:return self.store.collection(db,self.cid)

    def test_api_reorders_persistently_without_touching_photos_other_collections_or_cover(self):
        other=self.store.create_collection({'title':'另一份作品集'})['collection']
        self.store.add_collection_photos(other['id'],{'photo_ids':self.ids[:3]})
        self.store.update_collection(self.cid,{'cover_photo_id':self.ids[1]})
        before=self.store.photos();media={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.store.originals.iterdir()}
        wanted=[self.ids[2],self.ids[0],self.ids[1]]
        status,data=self.request(f'/api/collections/{self.cid}/order',{'photo_ids':wanted,'expected_photo_ids':self.ids[:3]})
        self.assertEqual(status,200);self.assertEqual(data['collection']['photo_ids'],wanted)
        self.assertEqual(data['collection']['cover_photo_id'],self.ids[1])
        reopened=server.AlbumStore(self.store.root,self.service.web_dir)
        with reopened.connect() as db:
            self.assertEqual(reopened.collection(db,self.cid)['photo_ids'],wanted)
            self.assertEqual(reopened.collection(db,other['id'])['photo_ids'],self.ids[:3])
            self.assertEqual([row[0] for row in db.execute('SELECT position FROM collection_photos WHERE collection_id=? ORDER BY position',(self.cid,))],[0,1,2])
        self.assertEqual(before,self.store.photos())
        self.assertEqual(media,{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.store.originals.iterdir()})

    def test_append_duplicate_import_and_remove_readd_preserve_order(self):
        wanted=list(reversed(self.ids[:3]));self.store.reorder_collection_photos(self.cid,{'photo_ids':wanted})
        result=self.store.import_photo(self.bytes[0],'same.png','2026-01-01T12:00:00',collection_id=self.cid)
        self.assertTrue(result['duplicate']);self.assertEqual(self.current()['photo_ids'],wanted)
        self.store.add_collection_photos(self.cid,{'photo_ids':[self.ids[0],self.ids[3]]})
        self.assertEqual(self.current()['photo_ids'],wanted+[self.ids[3]])
        self.store.remove_collection_photos(self.cid,{'photo_ids':[self.ids[0]]})
        self.store.add_collection_photos(self.cid,{'photo_ids':[self.ids[0]]})
        self.assertEqual(self.current()['photo_ids'],[self.ids[2],self.ids[1],self.ids[3],self.ids[0]])

    def test_invalid_or_incomplete_sequences_never_partially_update(self):
        before=self.current()
        invalid=[{},[],{'photo_ids':'wrong'},{'photo_ids':[self.ids[0],self.ids[0],self.ids[1]]},
                 {'photo_ids':[None]},{'photo_ids':[{}]},{'photo_ids':['../private']},
                 {'photo_ids':self.ids[:3],'extra':1},{'photo_ids':self.ids[:3],'expected_photo_ids':[self.ids[0]]*3}]
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(self.request(f'/api/collections/{self.cid}/order',value)[0],400)
                self.assertEqual(self.current(),before)
        for value in ([],self.ids[:2],[self.ids[0],self.ids[1],self.ids[3]]):
            self.assertEqual(self.request(f'/api/collections/{self.cid}/order',{'photo_ids':value})[0],409)
            self.assertEqual(self.current(),before)
        self.assertEqual(self.request('/api/collections/'+'0'*32+'/order',{'photo_ids':self.ids[:3]})[0],404)

    def test_stale_order_or_changed_membership_is_a_conflict(self):
        base=self.ids[:3];wanted=list(reversed(base))
        self.store.reorder_collection_photos(self.cid,{'photo_ids':wanted})
        self.assertEqual(self.request(f'/api/collections/{self.cid}/order',{'photo_ids':base,'expected_photo_ids':base})[0],409)
        self.assertEqual(self.current()['photo_ids'],wanted)
        self.store.add_collection_photos(self.cid,{'photo_ids':[self.ids[3]]})
        self.assertEqual(self.request(f'/api/collections/{self.cid}/order',{'photo_ids':base,'expected_photo_ids':wanted})[0],409)
        self.assertEqual(self.current()['photo_ids'],wanted+[self.ids[3]])

    def test_old_album_migrates_to_previous_gallery_order_once(self):
        photos_before=self.store.photos()
        with self.store.connect() as db:
            metadata=[tuple(row) for row in db.execute('SELECT collection_id,photo_id,added_at FROM collection_photos ORDER BY photo_id')]
            db.execute('ALTER TABLE collection_photos DROP COLUMN position')
        reopened=server.AlbumStore(self.store.root,self.service.web_dir)
        expected=[self.ids[0],self.ids[2],self.ids[1]]
        with reopened.connect() as db:
            self.assertEqual(reopened.collection(db,self.cid)['photo_ids'],expected)
            self.assertEqual(metadata,[tuple(row) for row in db.execute('SELECT collection_id,photo_id,added_at FROM collection_photos ORDER BY photo_id')])
        self.assertEqual(reopened.photos(),photos_before)
        wanted=list(reversed(expected));reopened.reorder_collection_photos(self.cid,{'photo_ids':wanted})
        again=server.AlbumStore(self.store.root,self.service.web_dir)
        with again.connect() as db:self.assertEqual(again.collection(db,self.cid)['photo_ids'],wanted)

    def test_complete_backup_carries_saved_order_and_originals(self):
        wanted=[self.ids[1],self.ids[0],self.ids[2]];self.store.reorder_collection_photos(self.cid,{'photo_ids':wanted})
        restored=self.root/'restored';restored.mkdir()
        with self.store.backup_archive(self.application) as archive_path:
            with zipfile.ZipFile(archive_path) as archive:
                member=next(name for name in archive.namelist() if name.endswith('/album-data/album.sqlite3'))
                (restored/'album.sqlite3').write_bytes(archive.read(member))
                self.assertTrue(any(name.endswith(self.ids[0]+'.png') for name in archive.namelist()))
        migrated=server.AlbumStore(restored,self.service.web_dir)
        with migrated.connect() as db:self.assertEqual(migrated.collection(db,self.cid)['photo_ids'],wanted)

    def test_interrupted_legacy_migration_rolls_back_schema_and_can_retry(self):
        with self.store.connect() as db:db.execute('ALTER TABLE collection_photos DROP COLUMN position')
        class BrokenMigration(server.AlbumStore):
            @contextmanager
            def connect(instance):
                with super().connect() as db:
                    class Proxy:
                        def __getattr__(self,name):return getattr(db,name)
                        def execute(self,sql,*args):
                            if sql.startswith('UPDATE collection_photos SET position='):raise sqlite3.OperationalError('interrupted migration')
                            return db.execute(sql,*args)
                    yield Proxy()
        with self.assertRaises(sqlite3.OperationalError):BrokenMigration(self.store.root,self.service.web_dir)
        with self.store.connect() as db:self.assertNotIn('position',[row['name'] for row in db.execute('PRAGMA table_info(collection_photos)')])
        retried=server.AlbumStore(self.store.root,self.service.web_dir)
        with retried.connect() as db:self.assertEqual(retried.collection(db,self.cid)['photo_ids'],[self.ids[0],self.ids[2],self.ids[1]])

    def test_other_websites_cannot_reorder_and_unchanged_save_is_noop(self):
        before=self.current()
        for headers in ({'Origin':'https://outside.example'},{'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request(f'/api/collections/{self.cid}/order',{'photo_ids':list(reversed(self.ids[:3]))},headers)[0],403)
        self.assertEqual(self.current(),before)
        self.assertEqual(self.request(f'/api/collections/{self.cid}/order',{'photo_ids':self.ids[:3]})[0],200)
        self.assertEqual(self.current(),before)


if __name__=='__main__':unittest.main()
