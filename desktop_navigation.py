"""Keep the album in its native window and open websites in the user's browser."""

import logging
import threading
from urllib.parse import urlsplit
import webbrowser

LOG = logging.getLogger("shiguang.desktop")


class ExternalLinks:
    def __init__(self, browser, origin):
        self.browser = browser
        self.origin = urlsplit(origin)

    def is_local(self, uri):
        try:
            target = urlsplit(uri)
            return (target.scheme, target.hostname, target.port) == (
                self.origin.scheme, self.origin.hostname, self.origin.port
            ) and target.username is None and target.password is None
        except ValueError:
            return False

    def open_external(self, uri):
        try:
            target = urlsplit(uri)
            if target.scheme not in ("http", "https") or not target.hostname or target.username is not None:
                return
            target.port  # Reject malformed ports before handing a URL to Windows.
            if any(ord(char) < 32 or ord(char) == 127 for char in uri):
                return
        except ValueError:
            return

        def launch():
            try:
                if not webbrowser.open(uri, new=2):
                    LOG.warning("The system browser could not open an external link")
            except Exception:
                LOG.exception("Could not open an external link in the system browser")

        threading.Thread(target=launch, name="album-external-link", daemon=True).start()

    def on_navigation_starting(self, _sender, args):
        uri = str(args.Uri)
        if self.is_local(uri):
            return
        args.Cancel = True
        self.open_external(uri)

    def on_new_window_requested(self, _sender, args):
        args.Handled = True
        uri = str(args.Uri)
        if self.is_local(uri):
            self.browser.load_url(uri)
        else:
            self.open_external(uri)


def install_external_links(window, origin):
    from System import Action

    browser = window.native.browser
    handler = ExternalLinks(browser, origin)

    def attach():
        core = browser.webview.CoreWebView2
        core.NewWindowRequested -= browser.on_new_window_request
        replaced = False
        try:
            core.NewWindowRequested += handler.on_new_window_requested
            replaced = True
            core.NavigationStarting += handler.on_navigation_starting
        except Exception:
            if replaced:
                core.NewWindowRequested -= handler.on_new_window_requested
            core.NewWindowRequested += browser.on_new_window_request
            raise

    window.native.Invoke(Action(attach))
    return handler
