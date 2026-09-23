"""触屏支持：识别触摸设备，并在点输入框时唤起 Windows 屏幕键盘。

只在**本机确实有触摸设备**且用户没有关掉这项功能时才会唤起屏幕键盘；
没有触摸设备的电脑上所有调用都是空操作。

唤起顺序（都是系统自带组件，不经过 cmd / powershell，也不会出现控制台窗口）：

1. ``ITipInvocation::Toggle`` —— 让系统直接把触摸键盘显示出来（最可靠，
   也是 Windows 官方给桌面程序的方式）；
2. ``TabTip.exe`` —— Windows 10/11 的触摸键盘进程；这个程序带 ``uiAccess`` 标记，
   直接 CreateProcess 会被系统拒绝（错误 740），所以改用 ShellExecute 让外壳代劳，
   这样不会弹 UAC、也不需要管理员权限；
3. ``osk.exe`` —— 老式屏幕键盘，前面两个不可用时兜底。
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

#: 触摸键盘的 COM 接口（CLSID_TipInvocation / IID_ITipInvocation）
CLSID_TIP_INVOCATION = "{4ce576fa-83dc-4f88-951c-9d0782b4e376}"
IID_ITIP_INVOCATION = "{37c994e7-432b-4834-a2f7-dce1f13b834b}"
CLSCTX_INPROC_SERVER = 0x1

#: 触摸/笔"翻译"成鼠标消息时，Windows 会在消息附加信息里打上这个签名
TOUCH_SIGNATURE = 0xFF515700


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
    # 两种信号只要有一个成立就算触摸设备：有的整机/驱动只报其中一个
    _touch = count > 0 or (digitizer & TOUCH_BITS) != 0
    return _touch


def last_input_is_touch() -> bool:
    """**当前这条消息**是不是触摸/笔产生的（鼠标点击返回 False）。

    触屏点击会被系统"翻译"成鼠标消息，但附加信息里带着 ``0xFF5157xx`` 签名；
    鼠标（含触摸板）点出来的消息没有这个签名。只有带签名的才弹屏幕键盘，
    这样有触摸屏的笔记本用鼠标点输入框时不会莫名其妙弹出键盘。

    ``SV_TOUCH_CLICK=1`` 可强制当作触摸点击（自动化测试用）。
    """
    forced = os.environ.get("SV_TOUCH_CLICK", "").strip()
    if forced in ("1", "true", "yes", "on"):
        return True
    if forced in ("0", "false", "no", "off"):
        return False
    try:
        import ctypes

        extra = int(ctypes.windll.user32.GetMessageExtraInfo()) & 0xFFFFFFFF
    except Exception:
        return False
    return (extra & 0xFFFFFF00) == TOUCH_SIGNATURE


def show_keyboard_for_touch() -> bool:
    """触摸点击才唤起屏幕键盘：鼠标点击、程序自己聚焦都不弹。"""
    if not _enabled or not touch_available():
        return False
    if not last_input_is_touch():
        return False
    return show_keyboard()


def keyboard_path() -> str:
    """返回系统屏幕键盘的路径（找不到返回空串）。"""
    found = keyboard_paths()
    return found[0] if found else ""


def keyboard_paths() -> list:
    """按优先级返回可用的屏幕键盘程序（先触摸键盘，后老式屏幕键盘）。"""
    candidates = []
    common = os.environ.get("CommonProgramFiles") or r"C:\Program Files\Common Files"
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    candidates.append(os.path.join(common, "microsoft shared", "ink", "TabTip.exe"))
    candidates.append(os.path.join(windir, "System32", "osk.exe"))
    found = []
    for path in candidates:
        try:
            if os.path.isfile(path):
                found.append(path)
        except Exception:
            continue
    return found


def show_keyboard(force: bool = False) -> bool:
    """唤起屏幕键盘；返回是否真的成功（没有触摸设备时返回 False）。"""
    global _last_launch
    if not force and not _enabled:
        return False
    if not force and not touch_available():
        return False
    with _lock:
        now = time.time()
        if now - _last_launch < LAUNCH_INTERVAL:
            return False
        _last_launch = now
    # 1) 先让系统自己把触摸键盘显示出来
    if toggle_touch_keyboard():
        return True
    # 2) 再退回直接启动键盘进程
    for path in keyboard_paths():
        if _launch(path):
            return True
    return False


def _guid(text: str):
    """把 ``{xxxx-....}`` 形式的字符串转成 GUID 结构。"""
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    # GUID 文本里前三段是"按顺序写出来"的（不是小端），最后 8 字节才是原始字节
    raw = text.strip().strip("{}").replace("-", "")
    if len(raw) != 32:
        raise ValueError("GUID 长度不对：%s" % text)
    guid = GUID()
    guid.Data1 = int(raw[0:8], 16)
    guid.Data2 = int(raw[8:12], 16)
    guid.Data3 = int(raw[12:16], 16)
    tail = bytes.fromhex(raw[16:32])
    for index in range(8):
        guid.Data4[index] = tail[index]
    return guid


def toggle_touch_keyboard() -> bool:
    """用 ``ITipInvocation::Toggle`` 让系统显示触摸键盘；失败返回 False。"""
    try:
        import ctypes
        from ctypes import wintypes

        ole32 = ctypes.windll.ole32
        try:
            ole32.CoInitialize(None)
        except Exception:
            pass
        clsid = _guid(CLSID_TIP_INVOCATION)
        iid = _guid(IID_ITIP_INVOCATION)
        instance = ctypes.c_void_p()
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER, ctypes.byref(iid),
            ctypes.byref(instance))
        if result != 0 or not instance:
            return False
        vtable = ctypes.cast(
            instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        toggle = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                    wintypes.HWND)(vtable[3])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        try:
            return toggle(instance, hwnd) == 0
        finally:
            try:
                release(instance)
            except Exception:
                pass
    except Exception:
        return False


def _launch(path: str) -> bool:
    """启动键盘程序：先直接起；被系统拒绝（需要提升权限）时交给外壳起。"""
    try:
        subprocess.Popen(
            [path],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            startupinfo=_StartupInfo(),
        )
        return True
    except OSError as exc:
        if getattr(exc, "winerror", 0) != 740:      # 740 = ERROR_ELEVATION_REQUIRED
            return _shell_execute(path)
    except Exception:
        pass
    return _shell_execute(path)


def _shell_execute(path: str) -> bool:
    """用 ShellExecuteW 启动（TabTip / osk 带 uiAccess，只能这样起）。"""
    try:
        import ctypes

        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        value = shell32.ShellExecuteW(None, "open", path, None, None, 5)
        return bool(value) and int(value) > 32
    except Exception:
        return False
