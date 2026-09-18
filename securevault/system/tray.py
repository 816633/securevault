"""托盘图标：左键单击打开面板，右键菜单只有「退出」。

托盘运行在独立线程里（自己创建隐藏窗口并跑消息循环）；
所有回调都会被投递到上层，由界面线程处理，避免跨线程操作 Tk。

Explorer 崩溃重启后会收到 ``TaskbarCreated`` 消息，此时自动重新添加图标。
图标创建失败（例如开机自启时 Explorer 还没准备好）不会退出程序，会每秒重试。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import win32api
import win32con
import win32gui

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

WM_TRAYICON = win32con.WM_USER + 20


class TrayIcon:
    WINDOW_CLASS = "SecureVaultTrayWnd"

    def __init__(self, icon_path: str, tooltip: str,
                 on_open: Callable[[], None], on_exit: Callable[[], None]) -> None:
        self.icon_path = icon_path
        self.tooltip = tooltip
        self._on_open = on_open
        self._on_exit = on_exit
        self._hwnd = 0
        self._hicon = 0
        self._added = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._taskbar_created = 0
        self._last_error = ""

    # -- 对外接口 --------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sv-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._added

    # -- 内部 ------------------------------------------------------------

    def _run(self) -> None:
        try:
            message_map = {
                WM_TRAYICON: self._on_tray_event,
                win32con.WM_CLOSE: self._on_close,
                win32con.WM_DESTROY: self._on_destroy,
                win32con.WM_COMMAND: self._on_command,
            }
            self._taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
            message_map[self._taskbar_created] = self._on_taskbar_created

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
                class_atom, "SecureVaultTray", 0, 0, 0, 0, 0, 0, 0,
                wc.hInstance, None,
            )
            self._load_icon()
            self._add_icon()
            threading.Thread(target=self._retry_loop, name="sv-tray-retry",
                             daemon=True).start()
            win32gui.PumpMessages()
        except Exception as exc:
            self._last_error = str(exc)

    def _load_icon(self) -> None:
        self._hicon = 0
        try:
            if self.icon_path and win32api.GetFileAttributes(self.icon_path) is not None:
                self._hicon = win32gui.LoadImage(
                    0, self.icon_path, win32con.IMAGE_ICON, 0, 0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
                )
        except Exception:
            self._hicon = 0
        if not self._hicon:
            self._hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

    def _notify_data(self, message: int) -> None:
        try:
            win32gui.Shell_NotifyIcon(message, (
                self._hwnd, 1, NIF_MESSAGE | NIF_ICON | NIF_TIP,
                WM_TRAYICON, self._hicon, self.tooltip,
            ))
            self._added = message != NIM_DELETE
        except Exception as exc:
            self._added = False
            self._last_error = str(exc)

    def _add_icon(self) -> None:
        self._notify_data(NIM_ADD)

    def _retry_loop(self) -> None:
        """托盘图标加不上时每秒重试（Explorer 未就绪等）。"""
        while not self._stop.wait(1.0):
            if not self._added and self._hwnd:
                self._notify_data(NIM_ADD)

    def _on_taskbar_created(self, hwnd, msg, wparam, lparam):
        self._added = False
        self._notify_data(NIM_ADD)
        return 0

    def _on_tray_event(self, hwnd, msg, wparam, lparam):
        if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
            self._safe(self._on_open)
        elif lparam == win32con.WM_RBUTTONUP:
            self._show_menu()
        return 0

    def _show_menu(self) -> None:
        try:
            menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(menu, win32con.MF_STRING, 1001, "退出")
            pos = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(self._hwnd)
            win32gui.TrackPopupMenu(
                menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
                pos[0], pos[1], 0, self._hwnd, None,
            )
            win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
            win32gui.DestroyMenu(menu)
        except Exception:
            pass

    def _on_command(self, hwnd, msg, wparam, lparam):
        if wparam == 1001:
            self._safe(self._on_exit)
        return 0

    def _safe(self, callback) -> None:
        try:
            callback()
        except Exception:
            pass

    def _on_close(self, hwnd, msg, wparam, lparam):
        self._notify_data(NIM_DELETE)
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        self._hwnd = 0
        win32gui.PostQuitMessage(0)
        return 0
