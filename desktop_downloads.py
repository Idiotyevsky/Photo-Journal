"""Use a typed Windows save dialog for the album's MP4 downloads."""

import ntpath
from pathlib import Path
import re
from urllib.parse import urlsplit


class VideoDownloadDialog:
    def __init__(self, browser, origin, forms, settings):
        self.browser, self.origin = browser, urlsplit(origin)
        self.forms, self.settings = forms, settings
        self.fallback = browser.on_download_starting

    def is_video(self, uri):
        try:
            target = urlsplit(str(uri))
            return (target.scheme, target.netloc) == (self.origin.scheme, self.origin.netloc) and bool(
                re.fullmatch(r"/api/video-jobs/[a-f0-9]{32}/file", target.path)
            ) and target.query in ("", "download=1") and not target.fragment
        except ValueError:
            return False

    @staticmethod
    def filename(suggestion):
        name = ntpath.basename(str(suggestion)).strip(" .")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name)[:180]
        if name.lower() in ("", "file", "file.mp4"):
            return "作品集-幻灯片.mp4"
        return name if name.lower().endswith(".mp4") else name + ".mp4"

    def on_download_starting(self, sender, args):
        if not self.is_video(args.DownloadOperation.Uri):
            return self.fallback(sender, args)
        if not self.settings.get("ALLOW_DOWNLOADS", False):
            args.Cancel = True
            return
        dialog = self.forms.SaveFileDialog()
        try:
            dialog.Title = "保存作品集视频"
            dialog.Filter = "MP4 视频 (*.mp4)|*.mp4"
            dialog.FilterIndex = 1
            dialog.DefaultExt = "mp4"
            dialog.AddExtension = True
            dialog.OverwritePrompt = True
            dialog.CheckPathExists = True
            dialog.RestoreDirectory = True
            dialog.FileName = self.filename(args.ResultFilePath)
            directory = ntpath.dirname(str(args.ResultFilePath))
            if directory and Path(directory).is_dir():
                dialog.InitialDirectory = directory
            if dialog.ShowDialog(self.browser.form) == self.forms.DialogResult.OK:
                args.ResultFilePath = dialog.FileName
            else:
                args.Cancel = True
        except Exception:
            args.Cancel = True
            raise
        finally:
            dialog.Dispose()


def install_video_downloads(window, origin):
    # pywebview 6.2.1's generic download dialog hardcodes *.*. Replace only
    # this window's handler, on its GUI thread, and delegate every other file.
    from System import Action
    from webview import settings
    from webview.platforms.edgechromium import WinForms

    handler = VideoDownloadDialog(window.native.browser, origin, WinForms, settings)

    def attach():
        core = handler.browser.webview.CoreWebView2
        core.DownloadStarting -= handler.fallback
        try:
            core.DownloadStarting += handler.on_download_starting
        except Exception:
            core.DownloadStarting += handler.fallback
            raise

    window.native.Invoke(Action(attach))
    return handler
