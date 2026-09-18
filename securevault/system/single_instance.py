"""单实例保护：命名互斥体 + 广播「激活」消息。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .. import ACTIVATE_MESSAGE, APP_MUTEX

ERROR_ALREADY_EXISTS = 183
HWND_BROADCAST = 0xFFFF

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]

_handle = 0


def acquire() -> bool:
    """尝试成为唯一实例；已经是唯一实例返回 True。"""
    global _handle
    # 关键：先把 last-error 清零，再用 use_last_error=True 的私有槽读取。
    # 直接调 GetLastError() 会读到线程里上一次调用的残留值（CreateMutexW 成功时
    # 不保证把它清零），曾经因此把「新建成功」误判成「已有实例」而直接退出。
    ctypes.set_last_error(0)
    _handle = _kernel32.CreateMutexW(None, False, APP_MUTEX)
    if not _handle:
        # 连互斥体都建不出来（极罕见）时，宁可照常启动，也不要拒绝启动。
        return True
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def notify_existing() -> None:
    """广播消息，让已有实例把解锁窗口弹出来。"""
    try:
        user32 = ctypes.windll.user32
        message = user32.RegisterWindowMessageW(ctypes.c_wchar_p(ACTIVATE_MESSAGE))
        if message:
            user32.PostMessageW(wintypes.HWND(HWND_BROADCAST), message, 0, 0)
    except Exception:
        pass


def register_message() -> int:
    try:
        return int(ctypes.windll.user32.RegisterWindowMessageW(
            ctypes.c_wchar_p(ACTIVATE_MESSAGE)))
    except Exception:
        return 0
