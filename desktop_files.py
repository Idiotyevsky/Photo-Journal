"""Reveal an album-owned original in Windows Explorer without shell commands."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


def reveal_in_explorer(path: Path) -> None:
    if os.name != "nt":
        raise OSError("文件定位需要 Windows 桌面相册。")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    shell.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.c_void_p]
    shell.SHParseDisplayName.restype = ctypes.c_long
    shell.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.DWORD]
    shell.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    # pywebview bridge calls run on workers, each with its own COM apartment.
    initialized = ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE: already initialized
        raise OSError("无法初始化文件管理器。")
    item = ctypes.c_void_p()
    try:
        if shell.SHParseDisplayName(str(path), None, ctypes.byref(item), 0, None) < 0:
            raise OSError("无法定位这张照片。")
        if shell.SHOpenFolderAndSelectItems(item, 0, None, 0) < 0:
            raise OSError("文件管理器暂时无法打开。")
    finally:
        if item.value:
            ole.CoTaskMemFree(item)
        if initialized >= 0:
            ole.CoUninitialize()
