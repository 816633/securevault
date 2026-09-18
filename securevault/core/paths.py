"""统一管理「数据只写在程序所在文件夹」这条硬约束。

任何模块都不允许自行拼接数据路径，必须通过本模块的 :class:`Dirs`。
"""

from __future__ import annotations

import os
import sys
import uuid

#: 正式数据目录名。
DIR_NAME = "SecureVaultData"
#: 开发 / 自检模式使用的独立数据目录名（与正式数据彻底隔离）。
DEV_DIR_NAME = "SecureVaultData-dev"


class NotWritableError(Exception):
    """程序所在目录不可写（例如放在只读介质上）。"""


def app_dir() -> str:
    """返回程序所在目录（打包成 exe 后是 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # securevault/core/paths.py -> 上溯到项目根目录
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def app_path() -> str:
    """返回程序自身路径（打包后是 exe，源码运行是入口脚本）。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.join(app_dir(), "main.py")


def launch_script() -> str:
    """返回开机自启快捷方式应当启动的目标。

    打包后是 exe；源码运行时是 ``SecureVault.pyw``（无控制台窗口）。
    """
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    pyw = os.path.join(app_dir(), "SecureVault.pyw")
    return pyw if os.path.exists(pyw) else os.path.join(app_dir(), "main.py")


class Dirs:
    """全部数据位置，都位于程序目录之下的 ``SecureVaultData``。"""

    def __init__(self, base_dir: str, dir_name: str = DIR_NAME) -> None:
        self.exe_dir = os.path.abspath(base_dir)
        self.exe_path = app_path()
        self.dir_name = dir_name or DIR_NAME
        self.root = os.path.join(self.exe_dir, self.dir_name)
        self.logs = os.path.join(self.root, "logs")
        self.tmp = os.path.join(self.root, "tmp")

    # -- 创建与可写性探测 ------------------------------------------------

    def ensure(self, probe: bool = True) -> "Dirs":
        for path in (self.root, self.logs, self.tmp):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as exc:
                raise NotWritableError("无法创建数据目录 %s: %s" % (path, exc)) from exc
        if probe:
            # 真正写一个小文件，比检查权限位可靠。
            probe_path = os.path.join(self.tmp, ".writetest")
            try:
                with open(probe_path, "w", encoding="ascii") as fh:
                    fh.write("ok")
                os.remove(probe_path)
            except OSError as exc:
                raise NotWritableError(
                    "程序所在目录不可写 %s: %s" % (self.exe_dir, exc)
                ) from exc
        return self

    # -- 具体文件 --------------------------------------------------------

    @property
    def keystore_file(self) -> str:
        return os.path.join(self.root, "keystore.json")

    @property
    def db_file(self) -> str:
        return os.path.join(self.root, "vault.db")

    def log_file(self, day: str) -> str:
        return os.path.join(self.logs, day + ".txt")

    @property
    def legacy_dir(self) -> str:
        return os.path.join(self.root, "legacy-go")

    def tmp_path(self, prefix: str) -> str:
        return os.path.join(self.tmp, "%s-%s" % (prefix, uuid.uuid4().hex[:16]))


def resolve(dev: bool = False) -> Dirs:
    """按模式返回数据目录对象（dev=True 使用独立的开发数据目录）。"""
    override = os.environ.get("SV_DATA_DIR", "").strip()
    if override:
        # 自动化测试用的隔离数据目录（例如 <exe 目录>\\SecureVaultData-e2e）。
        return Dirs(app_dir(), override)
    return Dirs(app_dir(), DEV_DIR_NAME if dev else DIR_NAME)


def desktop_dir() -> str:
    """返回当前用户桌面目录（兼容 OneDrive 重定向）。"""
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x10
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:
            if buf.value and os.path.isdir(buf.value):
                return buf.value
    except Exception:
        pass
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    cand = os.path.join(profile, "Desktop")
    return cand if os.path.isdir(cand) else profile
