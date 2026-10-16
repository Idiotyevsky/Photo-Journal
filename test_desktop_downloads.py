"""Typed save-dialog checks use disposable paths and a fake Windows dialog."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop_downloads import VideoDownloadDialog, install_video_downloads


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


if __name__ == "__main__":
    unittest.main()
