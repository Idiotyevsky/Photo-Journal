#!/usr/bin/env python3
"""Native Windows host for 拾光. All album requests stay on loopback."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any


APP_NAME = "拾光"
TITLE = ""
LOG = logging.getLogger("shiguang.desktop")


def application_paths() -> tuple[Path, Path]:
    """Bundled resources may be temporary; the user's album never is."""
    source_root = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", source_root)).resolve(), Path(sys.executable).resolve().parent
    return source_root, source_root


def resolve_data_dir(value: Path | None, application_dir: Path) -> Path:
    if value is None:
        return application_dir / "album-data"
    return (value if value.is_absolute() else application_dir / value).resolve()


def native_message(message: str, *, error: bool = False, owner: int = 0) -> None:
    """Use the Windows message box without creating a second GUI toolkit."""
    if os.name == "nt":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(owner, message, APP_NAME, 0x10 if error else 0x40)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def configure_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "desktop.log"
    # A --windowed PyInstaller executable has no standard streams. Set these
    # before importing pywebview so its own logger can report native failures.
    if sys.stdout is None:
        sys.stdout = log_path.open("a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = log_path.open("a", encoding="utf-8", buffering=1)
    logging.basicConfig(
        filename=log_path,
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class SingleInstance:
    """One writer per album directory; another launch restores its window."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.info_path = data_dir / ".desktop-instance.json"
        self.handle = None
        self.kernel32 = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        identity = hashlib.sha256(os.path.normcase(str(self.data_dir.resolve())).encode("utf-8")).hexdigest()
        ctypes.set_last_error(0)
        self.handle = self.kernel32.CreateMutexW(None, True, "Local\\ShiguangAlbum-" + identity)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            self.kernel32.CloseHandle(self.handle)
            self.handle = None
            self.activate_existing()
            return False
        self.info_path.write_text(json.dumps({"pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
        return True

    @staticmethod
    def _find_main_window(user32, pid: int) -> int | None:
        """Find the visible, unowned main form by PID without relying on its title."""
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        candidates = []

        @callback_type
        def visit(hwnd, _):
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if process_id.value != pid or not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
                return True  # GW_OWNER excludes dialogs and auxiliary owned windows.
            rectangle = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
                area = max(1, (rectangle.right - rectangle.left) * (rectangle.bottom - rectangle.top))
                candidates.append((area, int(hwnd)))
            return True

        user32.EnumWindows(visit, 0)
        return max(candidates)[1] if candidates else None

    def activate_existing(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                pid = int(json.loads(self.info_path.read_text(encoding="utf-8"))["pid"])
            except (OSError, ValueError, TypeError, KeyError):
                time.sleep(0.1)
                continue
            found = self._find_main_window(user32, pid)
            if found:
                # Restoring an already maximized/fullscreen form would change its
                # native window state. Only restore a genuinely minimized form.
                if user32.IsIconic(found):
                    user32.ShowWindow(found, 9)  # SW_RESTORE
                user32.SetForegroundWindow(found)
                return
            time.sleep(0.1)
        native_message("这个相册已经打开，正在准备窗口。请稍候查看任务栏中的「拾光」。")

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            try:
                info = json.loads(self.info_path.read_text(encoding="utf-8"))
                if info.get("pid") == os.getpid():
                    self.info_path.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError):
                LOG.warning("Could not remove instance marker", exc_info=True)
        finally:
            self.kernel32.ReleaseMutex(self.handle)
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


class FullscreenBridge:
    """The page can read or request fullscreen, and nothing else on the host."""

    def __init__(self):
        self._window = None
        self._lock = threading.RLock()
        self._closed = False
        self._fullscreen = False

    def _bind(self, window: Any) -> None:
        with self._lock:
            self._window = window
            window.events.closed += self._on_closed
            for name in ("resized", "maximized", "restored"):
                event = getattr(window.events, name, None)
                if event is not None:
                    event += self._on_native_change

    def _read_native(self) -> bool:
        if self._closed:
            return False
        native = getattr(self._window, "native", None)
        value = getattr(native, "is_fullscreen", None)
        # pywebview's Window.fullscreen is only its startup flag. WinForms owns
        # the live boolean, independent of WindowState.Maximized/Restored.
        if not isinstance(value, bool):
            raise RuntimeError("桌面窗口尚未准备好。")
        return value

    def _notify_locked(self) -> None:
        if self._closed or self._window is None or not self._window.events.loaded.is_set():
            return
        try:
            detail = json.dumps({"fullscreen": self._fullscreen})
            self._window.evaluate_js("window.dispatchEvent(new CustomEvent('shiguangfullscreenchange',{detail:" + detail + "}));")
        except Exception:
            if not self._closed:
                LOG.warning("Could not notify fullscreen state", exc_info=True)

    def _sync_locked(self) -> bool:
        actual = self._read_native()
        if actual != self._fullscreen:
            self._fullscreen = actual
            self._notify_locked()
        return actual

    def _on_native_change(self, *_):
        with self._lock:
            if self._closed:
                return
            try:
                self._sync_locked()
            except RuntimeError:
                pass  # Native initialization can emit resize events before ready.

    def _on_closed(self, *_):
        with self._lock:
            self._closed = True
            self._fullscreen = False

    def get_fullscreen(self) -> dict:
        with self._lock:
            return {"fullscreen": self._sync_locked()}

    def set_fullscreen(self, enabled: bool) -> dict:
        if not isinstance(enabled, bool):
            raise TypeError("全屏状态必须为布尔值。")
        with self._lock:
            if self._closed:
                raise RuntimeError("桌面窗口已关闭。")
            actual = self._sync_locked()
            if actual != enabled:
                self._window.toggle_fullscreen()
                actual = self._sync_locked()
                if actual != enabled:
                    raise RuntimeError("桌面窗口未能完成全屏切换。")
            return {"fullscreen": actual}


SMOKE_SCRIPT = """
(async function () {
    const heading = document.querySelector('main h1');
    const bounds = heading && heading.getBoundingClientRect();
    const read = async path => {
        const response = await fetch(path, {cache: 'no-store'});
        if (!response.ok) throw new Error(path + ': ' + response.status);
        return response.json();
    };
    try {
        const [health, photos, journals, collections] = await Promise.all([
            read('/api/health'), read('/api/photos'), read('/api/journals'), read('/api/collections')
        ]);
        return {
            ready_state: document.readyState,
            heading: heading ? heading.textContent.trim() : '',
            heading_visible: Boolean(bounds && bounds.width > 0 && bounds.height > 0),
            photo_count: Array.isArray(photos.photos) ? photos.photos.length : null,
            journal_count: Array.isArray(journals.journals) ? journals.journals.length : null,
            collection_count: Array.isArray(collections.collections) ? collections.collections.length : null,
            health: health,
            origin: location.origin,
            viewport: {width: innerWidth, height: innerHeight}
        };
    } catch (error) { return {error: String(error)}; }
})()
"""

FULLSCREEN_SMOKE_SCRIPT = """
(async function () {
    const deadline = Date.now() + 5000;
    while (!window.pywebview?.api?.set_fullscreen && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 40));
    }
    const api = window.pywebview?.api;
    if (!api?.get_fullscreen || !api?.set_fullscreen) throw new Error('Native fullscreen bridge unavailable');
    const controls = window.ShiguangFullscreen;
    if (!controls?.initialize || !controls?.handleToggle) throw new Error('Page fullscreen controls unavailable');
    await controls.initialize();
    const initial = await api.get_fullscreen();
    try {
        const module_entered = await controls.handleToggle();
        const entered = await api.get_fullscreen();
        const confirmed = await api.get_fullscreen();
        const module_exited = await controls.handleToggle();
        const exited = await api.get_fullscreen();
        const final = await api.get_fullscreen();
        return {initial, entered, confirmed, exited, final, module_entered, module_exited,
                api_methods: Object.keys(api).sort(),
                desktop_marker: new URLSearchParams(location.search).get('desktop') === '1'};
    } finally { await api.set_fullscreen(false); }
})()
"""


class DesktopController:
    def __init__(self, window: Any, data_dir: Path, origin: str, report: Path | None = None, fullscreen_api: FullscreenBridge | None = None):
        self.window = window
        self.data_dir = data_dir
        self.origin = origin
        self.report = report
        self.fullscreen_api = fullscreen_api or FullscreenBridge()
        if fullscreen_api is None:
            self.fullscreen_api._bind(window)
        self.loaded = threading.Event()
        self.closed = threading.Event()
        self.allow_close = threading.Event()
        self.close_check = threading.Event()
        self.smoke_result: dict | None = None
        window.events.loaded += self.loaded.set
        window.events.closed += self.closed.set
        window.events.closing += self.on_closing

    def owner_handle(self) -> int:
        try:
            return int(self.window.native.Handle.ToInt64())
        except (AttributeError, TypeError, ValueError):
            return 0

    def on_closing(self):
        if self.allow_close.is_set() or not self.loaded.is_set():
            return None
        # Closing is raised on the native GUI thread. JavaScript evaluation must
        # run on a worker so WebView2 can complete its asynchronous callback.
        if not self.close_check.is_set():
            self.close_check.set()
            threading.Thread(target=self.check_before_close, name="album-close-check", daemon=True).start()
        return False

    def check_before_close(self) -> None:
        try:
            importing = self.window.evaluate_js(
                "Boolean(typeof window.shiguangIsImporting === 'function' && window.shiguangIsImporting())"
            )
            if importing:
                native_message("照片正在导入，请等待整理完成后再关闭相册。", owner=self.owner_handle())
                return
            self.allow_close.set()
            self.window.destroy()
        except Exception:
            if not self.closed.is_set():
                LOG.exception("Could not check import state before closing")
                native_message("暂时无法确认照片的整理状态，请稍候再关闭相册。", owner=self.owner_handle())
        finally:
            self.close_check.clear()

    def after_start(self) -> None:
        # pywebview invokes this function on a worker after starting its GUI.
        deadline = time.monotonic() + 45
        while not self.loaded.wait(0.2):
            if self.closed.is_set():
                if self.report:
                    self.write_report({"ok": False, "error": "窗口在页面加载完成前关闭。"})
                return
            if time.monotonic() >= deadline:
                result = {"ok": False, "error": "桌面页面未能在 45 秒内加载完成。"}
                LOG.error(result["error"])
                if self.report:
                    self.write_report(result)
                else:
                    native_message("相册窗口未能完成加载。请重新打开拾光；详细原因已记录在 album-data\\desktop.log。", error=True, owner=self.owner_handle())
                self.allow_close.set()
                self.window.destroy()
                return
        if self.report:
            self.run_smoke_test()

    def write_report(self, result: dict) -> None:
        self.smoke_result = {**result, "data_dir": str(self.data_dir), "pid": os.getpid(), "renderer": "edgechromium"}
        if self.report is None:
            return
        self.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.report.with_name(self.report.name + ".tmp")
        temporary.write_text(json.dumps(self.smoke_result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.report)

    def run_smoke_test(self) -> None:
        if self.report is None:
            return
        try:
            completed = threading.Event()
            values: list[Any] = []

            def receive(value):
                values.append(value)
                completed.set()

            self.window.evaluate_js(SMOKE_SCRIPT, callback=receive)
            if not completed.wait(15):
                raise TimeoutError("读取桌面页面与本地接口超时。")
            result = values[0]
            if not isinstance(result, dict):
                raise ValueError("桌面页面返回了无效的验证资料。")
            health = result.get("health") or {}
            fullscreen_completed = threading.Event()
            fullscreen_values: list[Any] = []

            def receive_fullscreen(value):
                fullscreen_values.append(value)
                fullscreen_completed.set()

            self.window.evaluate_js(FULLSCREEN_SMOKE_SCRIPT, callback=receive_fullscreen)
            if not fullscreen_completed.wait(20):
                raise TimeoutError("验证原生全屏切换超时。")
            fullscreen = fullscreen_values[0]
            if not isinstance(fullscreen, dict):
                raise ValueError("原生全屏验证返回了无效的状态。")
            result["native_fullscreen"] = fullscreen
            result["native_title_empty"] = self.window.title == "" and getattr(self.window.native, "Text", None) == ""
            result["ok"] = bool(
                result.get("heading_visible") and result.get("heading")
                and result.get("photo_count") is not None
                and result.get("journal_count") is not None
                and result.get("collection_count") is not None
                and health.get("app") == "shiguang"
                and health.get("data_dir") == str(self.data_dir)
                and result.get("origin") == self.origin
                and result["native_title_empty"]
                and fullscreen.get("desktop_marker")
                and fullscreen.get("api_methods") == ["get_fullscreen", "set_fullscreen"]
                and fullscreen.get("module_entered") is True
                and fullscreen.get("module_exited") is True
                and fullscreen.get("initial") == {"fullscreen": False}
                and fullscreen.get("entered") == {"fullscreen": True}
                and fullscreen.get("confirmed") == {"fullscreen": True}
                and fullscreen.get("exited") == {"fullscreen": False}
                and fullscreen.get("final") == {"fullscreen": False}
                and not result.get("error")
            )
            self.write_report(result)
        except Exception as error:
            LOG.exception("Desktop smoke test failed")
            self.write_report({"ok": False, "error": str(error)})
        finally:
            try:
                if not self.closed.is_set():
                    self.fullscreen_api.set_fullscreen(False)
            except Exception:
                LOG.warning("Could not restore window mode after smoke test", exc_info=True)
            self.allow_close.set()
            self.window.destroy()


def main(argv: list[str] | None = None) -> int:
    bundle_root, application_dir = application_paths()
    parser = argparse.ArgumentParser(description="拾光 · Windows 私人摄影相册")
    parser.add_argument("--data-dir", type=Path, help="相册保存目录；默认位于程序旁的 album-data")
    parser.add_argument("--smoke-test", type=Path, metavar="JSON_REPORT", help="验证桌面窗口与本地接口后自动关闭")
    args = parser.parse_args(argv)
    data_dir = resolve_data_dir(args.data_dir, application_dir)
    report = None
    if args.smoke_test:
        report = (args.smoke_test if args.smoke_test.is_absolute() else application_dir / args.smoke_test).resolve()
    instance = SingleInstance(data_dir)
    album_server = None
    server_thread = None
    controller = None
    try:
        configure_logging(data_dir)
        if not instance.acquire():
            return 0
        # Import after logging is ready, including in a frozen, console-free EXE.
        import webview
        from server import AlbumHTTPServer

        if not (bundle_root / "ui" / "index.html").is_file():
            raise FileNotFoundError("找不到相册页面文件，请保留完整的应用程序文件夹。")
        profile_dir = data_dir / ".webview"
        profile_dir.mkdir(parents=True, exist_ok=True)
        application_file = Path(sys.executable) if getattr(sys, "frozen", False) else application_dir / "拾光相册.exe"
        album_server = AlbumHTTPServer(0, data_dir, web_dir=bundle_root / "ui", application_file=application_file)
        server_thread = threading.Thread(
            target=album_server.serve_forever, kwargs={"poll_interval": 0.2},
            name="album-local-service", daemon=True,
        )
        server_thread.start()
        origin = f"http://127.0.0.1:{album_server.server_port}"
        LOG.info("Opening native album window: %s; data=%s", origin, data_dir)
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
        fullscreen_api = FullscreenBridge()
        window = webview.create_window(
            TITLE, origin + "/?desktop=1", width=1440, height=960, min_size=(1000, 720),
            resizable=True, frameless=False, confirm_close=False,
            background_color="#f8f9f6", text_select=True,
            fullscreen=False, js_api=fullscreen_api,
        )
        if window is None:
            raise RuntimeError("桌面窗口未能创建。")
        fullscreen_api._bind(window)
        controller = DesktopController(window, data_dir, origin, report, fullscreen_api)
        icon = bundle_root / "assets" / "app.ico"
        webview.start(
            controller.after_start, gui="edgechromium", debug=False,
            private_mode=False, storage_path=str(profile_dir), http_server=False,
            icon=str(icon) if icon.is_file() else None,
            localization={
                "global.ok": "确定", "global.cancel": "取消", "global.saveFile": "保存文件",
                "windows.fileFilter.allFiles": "所有文件", "windows.fileFilter.otherFiles": "其他文件类型",
            },
        )
        if report and not (controller.smoke_result or {}).get("ok"):
            return 1
        return 0
    except Exception as error:
        LOG.exception("Could not launch the native album")
        if report:
            try:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps({"ok": False, "error": str(error), "data_dir": str(data_dir)}, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                LOG.exception("Could not write smoke-test report")
        else:
            native_message(
                "拾光暂时未能打开。请确认已安装 Microsoft Edge WebView2 运行时，并保留完整的应用程序文件夹。\n\n"
                f"原因：{error}\n\n详细记录：{data_dir / 'desktop.log'}", error=True,
            )
        return 1
    finally:
        if album_server is not None:
            if server_thread is not None and server_thread.is_alive():
                album_server.shutdown()
                server_thread.join(timeout=5)
            album_server.server_close()
        instance.release()
        LOG.info("Native album closed; local service stopped")


if __name__ == "__main__":
    raise SystemExit(main())
