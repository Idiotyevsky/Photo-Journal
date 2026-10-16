"""Desktop lifecycle, file location and save dialogs use disposable fixtures."""

import json
import ctypes
import os
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import desktop
import server
from PIL import Image
from desktop import DesktopBridge
from desktop_files import reveal_in_explorer
from desktop_downloads import VideoDownloadDialog, install_video_downloads
from desktop_navigation import ExternalLinks, install_external_links
from server import AlbumStore


class FakeEvent:
    def __init__(self):
        self.handlers = []
        self.ready = False

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def is_set(self):
        return self.ready

    def set(self, *args):
        self.ready = True
        for handler in self.handlers:
            handler(*args)


class FakeWindow:
    def __init__(self):
        self.events = types.SimpleNamespace(loaded=FakeEvent(), closed=FakeEvent(), closing=FakeEvent(), resized=FakeEvent(), maximized=FakeEvent(), restored=FakeEvent())
        self.title = ""
        self.native = types.SimpleNamespace(is_fullscreen=False, Text="", WindowState="Normal")
        self.destroy = Mock()
        self.evaluate_js = Mock(return_value=False)
        self.toggle_fullscreen = Mock(side_effect=self._toggle)

    def _toggle(self):
        self.native.is_fullscreen = not self.native.is_fullscreen


class DesktopTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows data-directory mutex")
    def test_second_copy_activates_first_without_acquiring_album(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            first = desktop.SingleInstance(data_dir)
            second = desktop.SingleInstance(data_dir)
            try:
                self.assertTrue(first.acquire())
                with patch.object(second, "activate_existing") as activate:
                    self.assertFalse(second.acquire())
                    activate.assert_called_once()
                self.assertIsNone(second.handle)
            finally:
                second.release()
                first.release()
            self.assertFalse((data_dir / ".desktop-instance.json").exists())

    def test_frozen_album_path_does_not_follow_temporary_bundle(self):
        with patch.object(desktop.sys, "frozen", True, create=True), \
                patch.object(desktop.sys, "_MEIPASS", str(Path(tempfile.gettempdir()) / "temporary-bundle"), create=True), \
                patch.object(desktop.sys, "executable", str(Path(tempfile.gettempdir()) / "portable" / "拾光.exe")):
            resources, application = desktop.application_paths()
        self.assertNotEqual(resources, application)
        self.assertEqual(desktop.resolve_data_dir(None, application), application / "album-data")
        self.assertEqual(desktop.resolve_data_dir(Path("test-album"), application), application / "test-album")

    def test_native_close_does_not_evaluate_javascript_on_gui_thread(self):
        window = FakeWindow()
        controller = desktop.DesktopController(window, Path("unused"), "http://127.0.0.1:9999")
        controller.loaded.set()
        with patch.object(desktop.threading, "Thread") as worker:
            self.assertIs(controller.on_closing(), False)
            worker.return_value.start.assert_called_once()
        window.evaluate_js.assert_not_called()
        window.destroy.assert_not_called()

    def test_importing_keeps_window_open_and_safe_close_releases_it(self):
        window = FakeWindow()
        controller = desktop.DesktopController(window, Path("unused"), "http://127.0.0.1:9999")
        window.evaluate_js.return_value = True
        with patch.object(desktop, "native_message") as message:
            controller.check_before_close()
            message.assert_called_once()
        window.destroy.assert_not_called()
        self.assertFalse(controller.allow_close.is_set())
        window.evaluate_js.return_value = False
        controller.check_before_close()
        window.destroy.assert_called_once()
        self.assertTrue(controller.allow_close.is_set())
        self.assertIsNone(controller.on_closing())

    def test_smoke_report_checks_visible_heading_and_local_album(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            window = FakeWindow()
            origin = "http://127.0.0.1:9999"
            controller = desktop.DesktopController(window, data_dir, origin, data_dir / "report.json")
            payload = {"heading": "我的摄影集", "heading_visible": True, "photo_count": 0,
                       "journal_count": 0, "collection_count": 0, "origin": origin,
                       "health": {"app": "shiguang", "data_dir": str(data_dir)}}
            def evaluate(script, callback=None):
                if callback is None:
                    return False
                if script == desktop.SMOKE_SCRIPT:
                    callback(payload)
                elif script == desktop.FULLSCREEN_SMOKE_SCRIPT:
                    bridge = controller.fullscreen_api
                    initial = bridge.get_fullscreen()
                    entered = bridge.set_fullscreen(True)
                    confirmed = bridge.get_fullscreen()
                    exited = bridge.set_fullscreen(False)
                    final = bridge.get_fullscreen()
                    callback({"initial": initial, "entered": entered, "confirmed": confirmed, "exited": exited, "final": final,
                              "api_methods": ["get_fullscreen", "set_fullscreen"], "desktop_marker": True,
                              "module_entered": True, "module_exited": True})
            window.evaluate_js.side_effect = evaluate
            controller.run_smoke_test()
            result = json.loads((data_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["renderer"], "edgechromium")
            self.assertTrue(result["native_title_empty"])
            self.assertTrue(result["native_fullscreen"]["entered"]["fullscreen"])
            self.assertFalse(result["native_fullscreen"]["final"]["fullscreen"])
            self.assertEqual(window.toggle_fullscreen.call_count, 2)
            window.destroy.assert_called_once()

    def test_fullscreen_bridge_is_narrow_typed_and_idempotent(self):
        window = FakeWindow()
        bridge = desktop.FullscreenBridge()
        bridge._bind(window)
        self.assertEqual([name for name in dir(bridge) if not name.startswith("_")], ["get_fullscreen", "set_fullscreen"])
        self.assertTrue(all(name.startswith("_") for name in vars(bridge)))
        self.assertEqual(bridge.get_fullscreen(), {"fullscreen": False})
        self.assertEqual(bridge.set_fullscreen(False), {"fullscreen": False})
        window.toggle_fullscreen.assert_not_called()
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(TypeError):
                bridge.set_fullscreen(value)
        self.assertEqual(bridge.set_fullscreen(True), {"fullscreen": True})
        self.assertEqual(bridge.set_fullscreen(True), {"fullscreen": True})
        self.assertEqual(bridge.set_fullscreen(False), {"fullscreen": False})
        self.assertEqual(bridge.set_fullscreen(False), {"fullscreen": False})
        self.assertEqual(window.toggle_fullscreen.call_count, 2)

    def test_concurrent_fullscreen_requests_toggle_once_per_target_state(self):
        window = FakeWindow()
        bridge = desktop.FullscreenBridge()
        bridge._bind(window)
        original = window._toggle

        def slow_toggle():
            time.sleep(0.01)
            original()
        window.toggle_fullscreen.side_effect = slow_toggle
        results, errors = [], []
        for enabled in (True, False):
            barrier = threading.Barrier(8)

            def request():
                try:
                    barrier.wait(timeout=2)
                    results.append(bridge.set_fullscreen(enabled))
                except Exception as error:
                    errors.append(error)
            workers = [threading.Thread(target=request) for _ in range(8)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [{"fullscreen": True}] * 8 + [{"fullscreen": False}] * 8)
        self.assertEqual(window.toggle_fullscreen.call_count, 2)

    def test_native_state_not_maximize_state_drives_notifications_and_lifecycle(self):
        window = FakeWindow()
        bridge = desktop.FullscreenBridge()
        bridge._bind(window)
        window.events.loaded.set()
        window.native.WindowState = "Maximized"
        window.events.maximized.set()
        self.assertEqual(bridge.get_fullscreen(), {"fullscreen": False})
        window.evaluate_js.assert_not_called()
        window.native.is_fullscreen = True
        window.events.resized.set(1920, 1080)
        self.assertEqual(bridge.get_fullscreen(), {"fullscreen": True})
        self.assertIn("shiguangfullscreenchange", window.evaluate_js.call_args.args[0])
        self.assertIn('"fullscreen": true', window.evaluate_js.call_args.args[0])
        window.native.WindowState = "Normal"
        window.native.is_fullscreen = False
        window.events.restored.set()
        self.assertEqual(bridge.get_fullscreen(), {"fullscreen": False})
        self.assertIn('"fullscreen": false', window.evaluate_js.call_args.args[0])
        window.events.closed.set()
        self.assertEqual(bridge.get_fullscreen(), {"fullscreen": False})
        with self.assertRaises(RuntimeError):
            bridge.set_fullscreen(True)
        window.toggle_fullscreen.assert_not_called()

    def test_fullscreen_failure_does_not_report_a_successful_change(self):
        bridge = desktop.FullscreenBridge()
        with self.assertRaises(RuntimeError):
            bridge.get_fullscreen()
        window = FakeWindow()
        bridge._bind(window)
        window.toggle_fullscreen.side_effect = None
        with self.assertRaises(RuntimeError):
            bridge.set_fullscreen(True)
        self.assertEqual(bridge.get_fullscreen(), {"fullscreen": False})

    def test_normal_start_never_runs_fullscreen_smoke_cycle(self):
        window = FakeWindow()
        controller = desktop.DesktopController(window, Path("unused"), "http://127.0.0.1:9999")
        controller.loaded.set()
        with patch.object(desktop, "install_video_downloads", return_value=object()) as install, \
                patch.object(desktop, "install_external_links", return_value=object()) as links:
            controller.after_start()
            if os.name == "nt":
                install.assert_called_once_with(window, controller.origin)
                self.assertIsNotNone(controller.video_downloads)
                links.assert_called_once_with(window, controller.origin)
                self.assertIsNotNone(controller.external_links)
        controller.run_smoke_test()
        window.evaluate_js.assert_not_called()
        window.toggle_fullscreen.assert_not_called()
        window.destroy.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows empty-caption window enumeration")
    def test_empty_title_single_instance_chooses_main_pid_and_ignores_owned_dialogs(self):
        api = Mock()
        records = {101: (77, 0, (0, 0, 1440, 960)), 102: (77, 101, (0, 0, 1800, 1200)),
                   103: (88, 0, (0, 0, 2000, 1500)), 104: (77, 0, (0, 0, 80, 40))}

        def process(hwnd, pointer):
            ctypes.cast(pointer, ctypes.POINTER(desktop.wintypes.DWORD)).contents.value = records[hwnd][0]

        def rectangle(hwnd, pointer):
            rect = ctypes.cast(pointer, ctypes.POINTER(desktop.wintypes.RECT)).contents
            rect.left, rect.top, rect.right, rect.bottom = records[hwnd][2]
            return True

        api.GetWindowThreadProcessId.side_effect = process
        api.GetWindow.side_effect = lambda hwnd, command: records[hwnd][1]
        api.GetWindowRect.side_effect = rectangle
        api.IsWindowVisible.return_value = True
        api.EnumWindows.side_effect = lambda callback, _: [callback(hwnd, 0) for hwnd in records]
        self.assertEqual(desktop.SingleInstance._find_main_window(api, 77), 101)
        api.GetWindowTextW.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            instance = desktop.SingleInstance(Path(directory))
            instance.info_path.write_text(json.dumps({"pid": 77}), encoding="utf-8")
            api.IsIconic.return_value = False
            with patch.object(desktop.ctypes, "WinDLL", return_value=api):
                instance.activate_existing()
            api.SetForegroundWindow.assert_called_with(101)
            api.ShowWindow.assert_not_called()
            api.IsIconic.return_value = True
            with patch.object(desktop.ctypes, "WinDLL", return_value=api):
                instance.activate_existing()
            api.ShowWindow.assert_called_once_with(101, 9)

    def test_engine_failure_stops_owned_server_and_releases_mutex(self):
        stopped = threading.Event()
        service = types.SimpleNamespace(server_port=9999, store=Mock(), shutdown=Mock(side_effect=stopped.set),
                                        server_close=Mock(), serve_forever=lambda **kwargs: stopped.wait(5))
        window = FakeWindow()
        engine = types.SimpleNamespace(settings={}, create_window=Mock(return_value=window),
                                       start=Mock(side_effect=RuntimeError("test native failure")))
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "album"
            report = Path(directory) / "failure.json"
            with patch.dict(sys.modules, {"webview": engine}), \
                    patch.object(server, "AlbumHTTPServer", return_value=service), \
                    patch.object(desktop, "configure_logging"), \
                    patch.object(desktop.SingleInstance, "acquire", return_value=True), \
                    patch.object(desktop.SingleInstance, "release") as release:
                self.assertEqual(desktop.main(["--data-dir", str(data_dir), "--smoke-test", str(report)]), 1)
            service.shutdown.assert_called_once()
            service.server_close.assert_called_once()
            release.assert_called_once()
            self.assertTrue(stopped.is_set())
            self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["ok"])
            self.assertFalse(any(thread.name == "album-local-service" for thread in threading.enumerate()))
            args, kwargs = engine.create_window.call_args
            self.assertEqual(args, ("", "http://127.0.0.1:9999/?desktop=1"))
            self.assertFalse(kwargs["frameless"])
            self.assertFalse(kwargs["fullscreen"])
            self.assertIsInstance(kwargs["js_api"], desktop.FullscreenBridge)
            self.assertIsInstance(kwargs["js_api"], desktop.DesktopBridge)
            self.assertIs(kwargs["js_api"]._store, service.store)


class VideoDownloadDialogTests(unittest.TestCase):
    origin = "http://127.0.0.1:54321"
    url = origin + "/api/video-jobs/" + "a" * 32 + "/file?download=1"

    def setup_dialog(self, accepted=True, suggestion="C:\\Downloads\\北京-幻灯片.mp4"):
        dialog = SimpleNamespace(Dispose=Mock())
        def show(owner):
            self.assertIs(owner, browser.form)
            # Simulate the native default-extension behavior when a user
            # replaces the suggestion with a name that has no extension.
            dialog.FileName = str(self.destination / ("我的影片." + dialog.DefaultExt))
            return "ok" if accepted else "cancel"
        dialog.ShowDialog = Mock(side_effect=show)
        forms = SimpleNamespace(SaveFileDialog=Mock(return_value=dialog), DialogResult=SimpleNamespace(OK="ok"))
        browser = SimpleNamespace(form=object(), on_download_starting=Mock())
        handler = VideoDownloadDialog(browser, self.origin, forms, {"ALLOW_DOWNLOADS": True})
        args = SimpleNamespace(DownloadOperation=SimpleNamespace(Uri=self.url), ResultFilePath=suggestion, Cancel=False)
        return handler, args, dialog, forms, browser

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.destination = Path(self.temporary.name)

    def test_default_filter_extension_and_selected_destination_are_mp4(self):
        handler, args, dialog, forms, browser = self.setup_dialog()
        handler.on_download_starting(object(), args)
        self.assertEqual(dialog.Title, "保存作品集视频")
        self.assertEqual(dialog.Filter, "MP4 视频 (*.mp4)|*.mp4")
        self.assertEqual(dialog.FilterIndex, 1)
        self.assertEqual(dialog.DefaultExt, "mp4")
        self.assertTrue(dialog.AddExtension)
        self.assertTrue(dialog.OverwritePrompt)
        self.assertTrue(dialog.CheckPathExists)
        self.assertTrue(dialog.RestoreDirectory)
        self.assertEqual(args.ResultFilePath, str(self.destination / "我的影片.mp4"))
        self.assertFalse(args.Cancel)
        dialog.Dispose.assert_called_once()
        browser.on_download_starting.assert_not_called()
        self.assertFalse(list(self.destination.iterdir()), "the dialog only selects a destination; WebView2 owns the download")

    def test_unicode_suggestion_missing_suffix_and_windows_basename(self):
        for suggestion, expected in [(r"C:\Downloads\北京-幻灯片.mp4", "北京-幻灯片.mp4"),
                                     (r"C:\Downloads\我的旅行", "我的旅行.mp4"),
                                     ("旅行.MP4", "旅行.MP4"), ("file", "作品集-幻灯片.mp4"),
                                     ("", "作品集-幻灯片.mp4")]:
            with self.subTest(suggestion=suggestion):
                self.assertEqual(VideoDownloadDialog.filename(suggestion), expected)
        handler, args, dialog, forms, browser = self.setup_dialog(suggestion=str(self.destination / "旅行.mp4"))
        handler.on_download_starting(object(), args)
        self.assertEqual(dialog.InitialDirectory, str(self.destination))

    def test_cancel_does_not_change_path_or_create_any_file(self):
        handler, args, dialog, forms, browser = self.setup_dialog(accepted=False)
        before = args.ResultFilePath
        handler.on_download_starting(object(), args)
        self.assertTrue(args.Cancel)
        self.assertEqual(args.ResultFilePath, before)
        dialog.Dispose.assert_called_once()

    def test_other_exports_and_other_origins_keep_the_existing_handler(self):
        handler, args, dialog, forms, browser = self.setup_dialog()
        for uri in (self.origin + "/api/backup", self.origin + "/api/export", self.origin + "/api/photos/" + "b" * 32 + "/framed",
                    "https://outside.example/api/video-jobs/" + "a" * 32 + "/file?download=1",
                    self.url.replace(":54321", ":54322"), self.url + "&path=C:/private", self.url + "#fragment",
                    self.url.replace("a" * 32, "../private")):
            with self.subTest(uri=uri):
                args.DownloadOperation.Uri = uri
                handler.on_download_starting("sender", args)
                browser.on_download_starting.assert_called_with("sender", args)
        forms.SaveFileDialog.assert_not_called()

    def test_downloads_disabled_or_dialog_failure_cancel_safely(self):
        handler, args, dialog, forms, browser = self.setup_dialog()
        handler.settings["ALLOW_DOWNLOADS"] = False
        handler.on_download_starting(None, args)
        self.assertTrue(args.Cancel)
        forms.SaveFileDialog.assert_not_called()
        handler.settings["ALLOW_DOWNLOADS"] = True
        args.Cancel = False
        dialog.ShowDialog.side_effect = RuntimeError("dialog unavailable")
        with self.assertRaises(RuntimeError):
            handler.on_download_starting(None, args)
        self.assertTrue(args.Cancel)
        dialog.Dispose.assert_called_once()

    def test_adapter_replaces_only_one_windows_event_and_runs_on_gui_thread(self):
        class Event:
            def __init__(self, callbacks): self.callbacks = callbacks
            def __iadd__(self, value): self.callbacks.append(value); return self
            def __isub__(self, value): self.callbacks.remove(value); return self
        fallback = Mock()
        core = SimpleNamespace(DownloadStarting=Event([fallback]))
        browser = SimpleNamespace(on_download_starting=fallback, webview=SimpleNamespace(CoreWebView2=core))
        window = SimpleNamespace(native=SimpleNamespace(browser=browser, Invoke=Mock(side_effect=lambda action: action())))
        fake_forms = object()
        import sys
        with patch.dict(sys.modules, {"System": SimpleNamespace(Action=lambda function: function),
                                     "webview": SimpleNamespace(settings={"ALLOW_DOWNLOADS": True}),
                                     "webview.platforms.edgechromium": SimpleNamespace(WinForms=fake_forms)}):
            handler = install_video_downloads(window, self.origin)
        window.native.Invoke.assert_called_once()
        self.assertEqual(core.DownloadStarting.callbacks, [handler.on_download_starting])
        self.assertIs(handler.fallback, fallback)


class DesktopExternalLinkTests(unittest.TestCase):
    origin = 'http://127.0.0.1:54321'

    def test_app_start_enables_external_browser_before_creating_window(self):
        fake_webview=SimpleNamespace(settings={},create_window=Mock(return_value=FakeWindow()),start=Mock())
        fake_server=SimpleNamespace(server_port=54321,store=Mock(),serve_forever=Mock(),shutdown=Mock(),server_close=Mock())
        enabled=[]
        fake_webview.create_window.side_effect=lambda *_args,**_kwargs: enabled.append(fake_webview.settings.get('OPEN_EXTERNAL_LINKS_IN_BROWSER')) or FakeWindow()
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(sys.modules,{'webview':fake_webview}), \
                patch.object(desktop,'SingleInstance') as instance, \
                patch.object(desktop,'configure_logging'), \
                patch.object(server,'AlbumHTTPServer',return_value=fake_server):
            instance.return_value.acquire.return_value=True
            self.assertEqual(desktop.main(['--data-dir',folder]),0)
        self.assertEqual(enabled,[True])
        self.assertTrue(fake_webview.settings['ALLOW_DOWNLOADS'])
        self.assertFalse(fake_webview.settings['ALLOW_FILE_URLS'])
        fake_server.server_close.assert_called_once()

    def test_external_navigation_and_popups_open_once_without_replacing_album(self):
        browser = SimpleNamespace(load_url=Mock())
        handler = ExternalLinks(browser, self.origin)
        with patch('desktop_navigation.webbrowser.open', return_value=True) as opened, \
                patch('desktop_navigation.threading.Thread') as worker:
            worker.side_effect = lambda **options: SimpleNamespace(start=options['target'])
            for method, attribute in ((handler.on_navigation_starting, 'Cancel'), (handler.on_new_window_requested, 'Handled')):
                for url in ('https://music.gdstudio.xyz/', 'https://creativecommons.org/licenses/by/4.0/', 'https://www.naturalearthdata.com/'):
                    with self.subTest(method=method.__name__, url=url):
                        opened.reset_mock()
                        args = SimpleNamespace(Uri=url, Cancel=False, Handled=False)
                        method(None, args)
                        self.assertTrue(getattr(args, attribute))
                        opened.assert_called_once_with(url, new=2)
                        self.assertTrue(worker.call_args.kwargs['daemon'])
            browser.load_url.assert_not_called()
            opened.side_effect = OSError('browser unavailable')
            args = SimpleNamespace(Uri='https://music.gdstudio.xyz/', Cancel=False)
            with self.assertLogs('shiguang.desktop', level='ERROR'):
                handler.on_navigation_starting(None, args)
            self.assertTrue(args.Cancel, 'a failed browser launch still keeps the album visible')

    def test_album_downloads_remain_local_and_other_schemes_cannot_replace_window(self):
        browser = SimpleNamespace(load_url=Mock()); handler = ExternalLinks(browser, self.origin)
        with patch.object(handler, 'open_external') as opened:
            for suffix in ('/?desktop=1', '/#photos', '/api/backup', '/api/export', '/media/originals/a.jpg', '/api/video-jobs/'+'a'*32+'/file?download=1'):
                args = SimpleNamespace(Uri=self.origin+suffix, Cancel=False)
                handler.on_navigation_starting(None, args)
                self.assertFalse(args.Cancel)
            opened.assert_not_called()
            args = SimpleNamespace(Uri=self.origin+'/api/backup', Handled=False)
            handler.on_new_window_requested(None, args)
            self.assertTrue(args.Handled); browser.load_url.assert_called_once_with(args.Uri)
            for uri in ('http://127.0.0.1:54322/', 'http://localhost:54321/', 'https://127.0.0.1:54321/', 'http://127.0.0.1:54321@other.example/', 'http://127.0.0.1:invalid/'):
                args = SimpleNamespace(Uri=uri, Cancel=False)
                handler.on_navigation_starting(None, args)
                self.assertTrue(args.Cancel)
        with patch('desktop_navigation.threading.Thread') as worker:
            for uri in ('javascript:alert(1)', 'file:///C:/private.txt', 'data:text/html,hello', 'about:blank', 'https://user:password@example.com/', 'https://example.com:invalid/', 'https://example.com/\nwrong'):
                handler.open_external(uri)
            worker.assert_not_called()

    def test_event_adapter_uses_gui_thread_and_restores_popup_handler_on_failure(self):
        class Event:
            def __init__(self, callbacks=(), fail=False): self.callbacks=list(callbacks); self.fail=fail
            def __iadd__(self, callback):
                if self.fail: raise RuntimeError('subscription failed')
                self.callbacks.append(callback); return self
            def __isub__(self, callback): self.callbacks.remove(callback); return self
        fallback=Mock(); core=SimpleNamespace(NewWindowRequested=Event([fallback]), NavigationStarting=Event())
        browser=SimpleNamespace(on_new_window_request=fallback, webview=SimpleNamespace(CoreWebView2=core))
        window=SimpleNamespace(native=SimpleNamespace(browser=browser, Invoke=Mock(side_effect=lambda action:action())))
        with patch.dict(sys.modules, {'System':SimpleNamespace(Action=lambda function:function)}):
            handler=install_external_links(window, self.origin)
            self.assertEqual(core.NewWindowRequested.callbacks,[handler.on_new_window_requested])
            self.assertEqual(core.NavigationStarting.callbacks,[handler.on_navigation_starting])
            window.native.Invoke.assert_called_once()
            core.NewWindowRequested=Event([fallback]);core.NavigationStarting=Event(fail=True)
            with self.assertRaisesRegex(RuntimeError,'subscription failed'): install_external_links(window,self.origin)
            self.assertEqual(core.NewWindowRequested.callbacks,[fallback])


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
