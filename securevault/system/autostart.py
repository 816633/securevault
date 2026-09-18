"""开机自启：在「开始菜单 → 启动」里创建 / 删除快捷方式（不写注册表）。"""

from __future__ import annotations

import os
from typing import Optional

LINK_NAME = "SecureVault.lnk"


def startup_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming"
    )
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def link_path() -> str:
    return os.path.join(startup_dir(), LINK_NAME)


def enabled() -> bool:
    return os.path.isfile(link_path())


def enable(target: str, arguments: str = "-silent") -> bool:
    """创建 / 更新自启快捷方式；成功返回 True。"""
    if not target or not os.path.exists(target):
        return False
    try:
        import pythoncom  # type: ignore
        from win32com.client import Dispatch  # type: ignore
    except Exception:
        return False

    pythoncom.CoInitialize()
    try:
        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(link_path())
        shortcut.TargetPath = target
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = os.path.dirname(target)
        shortcut.WindowStyle = 1
        shortcut.Description = "SecureVault 开机自动运行（常驻托盘）"
        shortcut.Save()
        return True
    except Exception:
        return False
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def disable() -> bool:
    """删除自启快捷方式；本来就不存在时返回 True。"""
    path = link_path()
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception:
        return False


def set_enabled(on: bool, target: str) -> bool:
    return enable(target) if on else disable()
