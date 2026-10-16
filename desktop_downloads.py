"""Match native save dialogs and filename suffixes to the actual download format."""

import ntpath
from pathlib import Path
import re
from urllib.parse import urlsplit


class AlbumDownloadDialog:
    IMAGE_TYPES = {
        'jpg': ('JPEG 图片 (*.jpg;*.jpeg)|*.jpg;*.jpeg', ('.jpg', '.jpeg')),
        'png': ('PNG 图片 (*.png)|*.png', ('.png',)),
        'webp': ('WebP 图片 (*.webp)|*.webp', ('.webp',)),
        'gif': ('GIF 图片 (*.gif)|*.gif', ('.gif',)),
        'bmp': ('BMP 图片 (*.bmp)|*.bmp', ('.bmp',)),
        'tif': ('TIFF 图片 (*.tif;*.tiff)|*.tif;*.tiff', ('.tif', '.tiff')),
    }
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
    def filename(suggestion, extension='mp4', aliases=('.mp4',), default='作品集-幻灯片'):
        name = ntpath.basename(str(suggestion)).strip(" .")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name)
        stem, suffix = ntpath.splitext(name)
        if name.lower() in ('', 'file', 'framed') or stem.lower() in ('file', 'framed'):
            return default + '.' + extension
        if suffix.lower() in aliases:
            return stem[:180-len(suffix)] + suffix
        return (stem if suffix else name)[:179-len(extension)] + '.' + extension

    def download_type(self, uri):
        if self.is_video(uri):
            return ('mp4', 'MP4 视频 (*.mp4)|*.mp4', ('.mp4',), '保存作品集视频', '作品集-幻灯片')
        try:
            target = urlsplit(str(uri))
            if (target.scheme, target.netloc) != (self.origin.scheme, self.origin.netloc) or target.fragment:
                return None
            if re.fullmatch(r'/api/photos/[a-f0-9]{32}/framed', target.path) and target.query in ('', 'preview=0'):
                extension, title, default = 'jpg', '保存白边作品', '照片-白边'
            else:
                match = re.fullmatch(r'/media/originals/[a-f0-9]{32}\.(jpg|png|webp|gif|bmp|tif)', target.path)
                if not match or target.query:
                    return None
                extension, title, default = match.group(1), '保存原图', '照片'
            filter_text, aliases = self.IMAGE_TYPES[extension]
            return (extension, filter_text, aliases, title, default)
        except ValueError:
            return None

    def on_download_starting(self, sender, args):
        kind = self.download_type(args.DownloadOperation.Uri)
        if kind is None:
            return self.fallback(sender, args)
        if not self.settings.get("ALLOW_DOWNLOADS", False):
            args.Cancel = True
            return
        dialog = self.forms.SaveFileDialog()
        try:
            extension, filter_text, aliases, title, default = kind
            dialog.Title = title
            dialog.Filter = filter_text
            dialog.FilterIndex = 1
            dialog.DefaultExt = extension
            dialog.AddExtension = True
            dialog.OverwritePrompt = True
            dialog.CheckPathExists = True
            dialog.RestoreDirectory = True
            dialog.FileName = self.filename(args.ResultFilePath, extension, aliases, default)
            directory = ntpath.dirname(str(args.ResultFilePath))
            if directory and Path(directory).is_dir():
                dialog.InitialDirectory = directory
            while dialog.ShowDialog(self.browser.form) == self.forms.DialogResult.OK:
                selected = str(dialog.FileName)
                corrected = ntpath.join(ntpath.dirname(selected), self.filename(selected, extension, aliases, default))
                # If a suffix was mistyped, show the corrected destination in
                # the native dialog again so its overwrite prompt still covers
                # the exact file WebView2 will write.
                if ntpath.normcase(corrected) != ntpath.normcase(selected):
                    dialog.FileName = corrected
                    continue
                args.ResultFilePath = selected
                break
            else:
                args.Cancel = True
        except Exception:
            args.Cancel = True
            raise
        finally:
            dialog.Dispose()


def install_downloads(window, origin):
    # pywebview 6.2.1's generic download dialog hardcodes *.*. Replace only
    # this window's handler, on its GUI thread, for photos and videos.
    from System import Action
    from webview import settings
    from webview.platforms.edgechromium import WinForms

    handler = AlbumDownloadDialog(window.native.browser, origin, WinForms, settings)

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
