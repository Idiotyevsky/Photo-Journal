"""Native file actions accept existing album photo IDs and release Shell resources."""
import ctypes
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from desktop import DesktopBridge
from desktop_files import reveal_in_explorer
from server import AlbumStore


class DesktopPhotoLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / '相册 旅途,照片'
        self.store = AlbumStore(self.root)
        output = BytesIO()
        Image.new('RGB', (80, 60), 'navy').save(output, 'JPEG')
        self.photo = self.store.import_photo(output.getvalue(), '海边.jpg', None)['photo']
        self.original = self.store.originals / (self.photo['id'] + '.jpg')
        self.bridge = DesktopBridge(self.store)

    def tearDown(self):
        self.temp.cleanup()

    @patch('desktop_files.reveal_in_explorer')
    def test_only_database_owned_original_is_revealed_and_data_is_unchanged(self, reveal):
        before = self.store.photos()
        data = self.original.read_bytes()
        self.assertEqual(self.bridge.reveal_photo(self.photo['id']), {'ok': True})
        reveal.assert_called_once_with(self.original.resolve())
        self.assertEqual(self.store.photos(), before)
        self.assertEqual(self.original.read_bytes(), data)

    @patch('desktop_files.reveal_in_explorer')
    def test_paths_unknown_ids_and_corrupt_extensions_are_rejected(self, reveal):
        for value in (None, [], {}, str(self.original), '../original.jpg', '0' * 32):
            self.assertFalse(self.bridge.reveal_photo(value)['ok'])
        with self.store.connect() as db:
            db.execute('UPDATE photos SET extension=? WHERE id=?', ('/../../outside.jpg', self.photo['id']))
        self.assertFalse(self.bridge.reveal_photo(self.photo['id'])['ok'])
        reveal.assert_not_called()

    @patch('desktop_files.reveal_in_explorer')
    def test_missing_removed_linked_photos_and_closed_album_are_rejected(self, reveal):
        with patch.object(Path, 'is_symlink', return_value=True):
            self.assertFalse(self.bridge.reveal_photo(self.photo['id'])['ok'])
        self.original.unlink()
        self.assertFalse(self.bridge.reveal_photo(self.photo['id'])['ok'])
        with self.store.connect() as db:
            db.execute('DELETE FROM photos WHERE id=?', (self.photo['id'],))
        self.assertFalse(self.bridge.reveal_photo(self.photo['id'])['ok'])
        self.bridge._on_closed()
        self.assertFalse(self.bridge.reveal_photo(self.photo['id'])['ok'])
        reveal.assert_not_called()

    @patch('desktop_files.reveal_in_explorer', side_effect=OSError('shell failure'))
    def test_native_failure_becomes_a_readable_error(self, reveal):
        result = self.bridge.reveal_photo(self.photo['id'])
        self.assertFalse(result['ok'])
        self.assertIn('文件管理器', result['error'])
        self.assertNotIn(str(self.root), result['error'])


class WindowsShellTests(unittest.TestCase):
    def shell(self, initialized=0, parse=0, opened=0):
        shell, ole = Mock(), Mock()
        ole.CoInitializeEx.return_value = initialized
        def parse_path(_path, _context, out, _attributes, _requested):
            if parse >= 0:
                ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = 0x123456789
            return parse
        shell.SHParseDisplayName.side_effect = parse_path
        shell.SHOpenFolderAndSelectItems.return_value = opened
        return shell, ole

    def test_unicode_path_selects_exact_file_and_frees_pidl_and_com(self):
        shell, ole = self.shell()
        path = Path('D:/相册 旅途,照片/原图.jpg')
        with patch('desktop_files.os.name', 'nt'), patch('desktop_files.ctypes.WinDLL', side_effect=[shell, ole], create=True):
            reveal_in_explorer(path)
        self.assertEqual(shell.SHParseDisplayName.call_args.args[0], str(path))
        pointer, count, children, flags = shell.SHOpenFolderAndSelectItems.call_args.args
        self.assertEqual((pointer.value, count, children, flags), (0x123456789, 0, None, 0))
        ole.CoTaskMemFree.assert_called_once_with(pointer)
        ole.CoUninitialize.assert_called_once()

    def test_parse_or_open_failures_release_only_owned_resources(self):
        for initialized, parse, opened, frees, uninitializes in (
                (0, -1, 0, 0, 1), (1, 0, -1, 1, 1),
                (-2147417850, 0, -1, 1, 0), (-1, 0, 0, 0, 0)):
            with self.subTest(initialized=initialized, parse=parse, opened=opened):
                shell, ole = self.shell(initialized, parse, opened)
                path = Path('D:/照片.jpg')
                with patch('desktop_files.os.name', 'nt'), patch('desktop_files.ctypes.WinDLL', side_effect=[shell, ole], create=True):
                    with self.assertRaises(OSError):
                        reveal_in_explorer(path)
                self.assertEqual(ole.CoTaskMemFree.call_count, frees)
                self.assertEqual(ole.CoUninitialize.call_count, uninitializes)


if __name__ == '__main__':
    unittest.main()
