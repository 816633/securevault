"""SecureVault —— U 盘自动复制与监控工具（Python 重写版）。

包结构：

    securevault.core       纯逻辑（路径、文件工具、数据模型）
    securevault.crypto     密钥库与字段加密
    securevault.storage    加密 SQLite 存储与按天日志
    securevault.system     设备探测、设备事件监听、开机自启、托盘
    securevault.copy       增量复制引擎与业务编排
    securevault.ui         Tk 界面（Win10 扁平风格）

硬性约束（沿用需求）：
  * 所有数据只写在程序所在目录下的 ``SecureVaultData``；
  * 全程静默：不调用 cmd / powershell / robocopy，不弹系统窗口；
  * 任何异常都不允许让进程崩溃。
"""

from __future__ import annotations

APP_NAME = "SecureVault"
APP_VERSION = "2.4.2"
APP_TITLE = "SecureVault"
APP_MUTEX = "Local\\SecureVault_SingleInstance_PY_8F3A21"
ACTIVATE_MESSAGE = "SecureVaultActivate_8F3A21"

#: 作者与项目地址（界面「关于」页会显示，版本检查也用它拼地址）
APP_AUTHOR = "816633"
REPO_SLUG = "816633/securevault"
PROJECT_URL = "https://github.com/816633/securevault"

__all__ = ["APP_NAME", "APP_VERSION", "APP_TITLE", "APP_MUTEX", "ACTIVATE_MESSAGE",
           "APP_AUTHOR", "REPO_SLUG", "PROJECT_URL"]
