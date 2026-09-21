"""触屏支持：识别触摸设备，并在点输入框时唤起 Windows 屏幕键盘。

只在**本机确实有触摸设备**（``SM_MAXIMUMTOUCHES`` > 0）且用户没有关掉这项功能时
才会去启动屏幕键盘；没有触摸设备的电脑上所有调用都是空操作。

屏幕键盘由系统自带（先试 ``TabTip.exe``（Windows 10/11 触摸键盘），
没有就退回 ``osk.exe``）；启动方式是直接运行这个程序，
不经过 cmd / powershell，也不会出现任何控制台窗口。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

#: 由设置页 / 启动流程同步：False 时完全不碰屏幕键盘。
_enabled = True
#: 触摸设备探测结果缓存（None = 还没探测过）。
_touch: "bool | None" = None
_lock = threading.RLock()
_last_launch = 0.0

#: 两次唤起之间的最小间隔（秒），避免连续点输入框时反复拉起键盘。
LAUNCH_INTERVAL = 2.0

#: 触摸设备探测用的两个系统指标
SM_DIGITIZER = 94
SM_MAXIMUMTOUCHES = 95
#: SM_DIGITIZER 里与"触摸"有关的位（内置触摸 / 外接触摸 / 触摸就绪 / 多输入）
TOUCH_BITS = 0x04 | 0x10 | 0x40 | 0x80


if hasattr(subprocess, "STARTUPINFO"):  # pragma: no cover - Windows 专用
    class _StartupInfo(subprocess.STARTUPINFO):
        """把可能出现的窗口藏起来（只影响我们自己启动的进程）。"""

        def __init__(self) -> None:
            super().__init__()
            self.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self.wShowWindow = 0  # SW_HIDE
else:  # 其它平台上屏幕键盘功能本来就不可用
    class _StartupInfo(object):
        pass


def set_enabled(value: bool) -> None:
    """设置里「触屏：点输入框自动弹出屏幕键盘」的开关。"""
    global _enabled
    _enabled = bool(value)


def enabled() -> bool:
    return bool(_enabled)


def touch_available() -> bool:
    """本机是否有触摸设备（结果缓存；``SV_TOUCH=1/0`` 可强制指定）。"""
    global _touch
    if _touch is not None:
        return _touch
    forced = os.environ.get("SV_TOUCH", "").strip()
    if forced in ("1", "true", "yes", "on"):
        _touch = True
        return _touch
    if forced in ("0", "false", "no", "off"):
        _touch = False
        return _touch
    count = 0
    digitizer = 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        count = int(user32.GetSystemMetrics(SM_MAXIMUMTOUCHES))
        digitizer = int(user32.GetSystemMetrics(SM_DIGITIZER))
    except Exception:
        count = 0
        digitizer = 0
    _touch = count > 0 and (digitizer & TOUCH_BITS) != 0
    return _touch


def keyboard_path() -> str:
    """返回系统屏幕键盘的路径（找不到返回空串）。"""
    candidates = []
    common = os.environ.get("CommonProgramFiles") or r"C:\Program Files\Common Files"
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    candidates.append(os.path.join(common, "microsoft shared", "ink", "TabTip.exe"))
    candidates.append(os.path.join(windir, "System32", "osk.exe"))
    for path in candidates:
        try:
            if os.path.isfile(path):
                return path
        except Exception:
            continue
    return ""


def show_keyboard(force: bool = False) -> bool:
    """唤起屏幕键盘；返回是否真的启动了（没有触摸设备时返回 False）。"""
    global _last_launch
    if not force and not _enabled:
        return False
    if not force and not touch_available():
        return False
    path = keyboard_path()
    if not path:
        return False
    with _lock:
        now = time.time()
        if now - _last_launch < LAUNCH_INTERVAL:
            return False
        _last_launch = now
    return _launch(path)


def _launch(path: str) -> bool:
    try:
        subprocess.Popen(
            [path],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            startupinfo=_StartupInfo(),
        )
        return True
    except Exception:
        return False
