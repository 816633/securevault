"""设备插拔监听。

实现方式：
  1) 后台线程里创建一个隐藏窗口，注册 ``GUID_DEVINTERFACE_VOLUME`` 通知，
     收到 ``WM_DEVICECHANGE`` 就回调一次（带 500ms 去抖）；
  2) 同一线程另起一个轮询定时器（默认 5 秒）做兜底，
     保证通知注册失败或系统漏发消息时仍然能发现设备变化。

回调只通知「有变化」，具体是哪个盘插拔由上层比较快照得出 —— 这样最稳，
不需要解析系统广播结构体，也不会因为设备没有盘符而漏事件。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

import win32api
import win32con
import win32gui

from .devices import GUID, GUID_DEVINTERFACE_VOLUME

WM_DEVICECHANGE = 0x0219
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000
DBT_DEVTYP_DEVICEINTERFACE = 0x00000005


class DEV_BROADCAST_DEVICEINTERFACE(ctypes.Structure):
    _fields_ = [
        ("dbcc_size", wintypes.DWORD),
        ("dbcc_devicetype", wintypes.DWORD),
        ("dbcc_reserved", wintypes.DWORD),
        ("dbcc_classguid", GUID),
        ("dbcc_name", ctypes.c_wchar * 1),
    ]


class DeviceWatcher:
    """设备变化监听器。``on_change`` 会被多次调用，上层需要自行去抖。"""

    WINDOW_CLASS = "SecureVaultDeviceWatch"

    def __init__(self, on_change: Callable[[], None], poll_interval: float = 5.0) -> None:
        self._on_change = on_change
        self._poll_interval = max(1.0, float(poll_interval))
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._hwnd = 0
        self._last_fire = 0.0
        self._registered = False
        self._lock = threading.Lock()

    # -- 对外接口 --------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="sv-devwatch", daemon=True)
        self._thread.start()
        poller = threading.Thread(target=self._poll_loop, name="sv-devpoll", daemon=True)
        poller.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass

    @property
    def registered(self) -> bool:
        return self._registered

    # -- 内部 ------------------------------------------------------------

    def _fire(self) -> None:
        now = time.time()
        with self._lock:
            if now - self._last_fire < 0.5:
                return
            self._last_fire = now
        try:
            self._on_change()
        except Exception:
            pass

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            self._fire()

    def _run(self) -> None:
        try:
            self._create_window()
        except Exception:
            self._registered = False
            return

    def _create_window(self) -> None:
        message_map = {WM_DEVICECHANGE: self._on_device_change,
                       win32con.WM_CLOSE: self._on_close,
                       win32con.WM_DESTROY: self._on_destroy}
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = self.WINDOW_CLASS
        wc.lpfnWndProc = message_map
        wc.style = win32con.CS_VREDRAW | win32con.CS_HREDRAW
        wc.hbrBackground = win32con.COLOR_WINDOW
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wc.hIcon = 0
        try:
            class_atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            class_atom = self.WINDOW_CLASS
        self._hwnd = win32gui.CreateWindow(
            class_atom, "SecureVaultDeviceWatch", 0, 0, 0, 0, 0, 0, 0,
            wc.hInstance, None,
        )
        self._register_notification()
        # 启动时先触发一次，避免漏掉程序启动前就插着的设备。
        self._fire()
        win32gui.PumpMessages()

    def _register_notification(self) -> None:
        info = DEV_BROADCAST_DEVICEINTERFACE()
        info.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE)
        info.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
        info.dbcc_reserved = 0
        info.dbcc_classguid = GUID_DEVINTERFACE_VOLUME
        try:
            ctypes.windll.user32.RegisterDeviceNotificationW(
                wintypes.HWND(self._hwnd), ctypes.byref(info),
                DEVICE_NOTIFY_WINDOW_HANDLE,
            )
            self._registered = True
        except Exception:
            self._registered = False

    def _on_device_change(self, hwnd, msg, wparam, lparam):
        if wparam in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE):
            self._fire()
        return 1

    def _on_close(self, hwnd, msg, wparam, lparam):
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        self._hwnd = 0
        win32gui.PostQuitMessage(0)
        return 0
