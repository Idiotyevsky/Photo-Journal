"""Desktop lifecycle checks use fake windows and disposable album directories."""

import json
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import desktop
import server


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
        with patch.object(desktop, "install_video_downloads", return_value=object()) as install:
            controller.after_start()
            if os.name == "nt":
                install.assert_called_once_with(window, controller.origin)
                self.assertIsNotNone(controller.video_downloads)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
