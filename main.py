"""SecureVault 入口。

用法（参数写法宽松：``-silent`` / ``--silent`` / ``/silent`` 都识别）：

    -silent    静默启动：只驻留托盘，不弹任何窗口（开机自启用的就是它）
    -debug     输出调试信息（默认全程静默，不打印任何东西）
    -help      显示帮助
    -updatecheck 逐个下载源检查一次更新，结果写到「数据目录」下的 update-check.txt
    -review    界面评审模式：跳过密码、写入演示数据后直接打开面板（开发用）
    -autotest  自动验收：跑一遍完整流程并把结果写入日志（开发用）
    -selftest  只跑逻辑自检（等价于 -autotest 但不启动界面）

``-review`` / ``-autotest`` / ``-selftest`` 使用独立数据目录 ``SecureVaultData-dev``，
不会碰正式数据。
"""

from __future__ import annotations

import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from securevault import APP_NAME, APP_VERSION  # noqa: E402

HELP_TEXT = """%s %s —— U 盘自动复制与监控工具

用法：SecureVault.exe [选项]

  -silent     静默启动：只驻留托盘，不显示窗口（开机自启使用）
  -debug      输出调试信息
  -help       显示本帮助
  -updatecheck 检查更新（结果写到 <数据目录>\\update-check.txt）
  -review     界面评审模式（开发用，使用独立数据目录）
  -autotest   自动验收（开发用，使用独立数据目录）
  -selftest   只跑逻辑自检（开发用，使用独立数据目录）

日常使用请不要加开发参数。左键单击托盘图标可以打开 / 解锁面板；
右键托盘菜单里的「退出」用于彻底退出程序。
""" % (APP_NAME, APP_VERSION)


def prepare_frozen_runtime() -> None:
    """单文件版启动前先把 Tcl/Tk 数据等出来。

    单文件 exe 运行时要先把内部压缩包解压到临时目录，而"解压"和"启动 Python"
    是**并行**的：如果 tkinter 在 Tcl 的数据（``tcl/init.tcl`` 与整个
    ``tcl/encoding`` 目录）解压完之前就初始化，程序会直接报
    「Can't find a usable init.tcl」启动失败。

    所以这里等到「关键文件都在」并且「文件总数连续几次不再增长」再继续。
    """
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", "")
    if not base:
        return
    tcl_dir, tk_dir = _tcl_dirs(base)
    # 新版 PyInstaller 的 onedir 布局（exe 旁边是 _internal\）不需要等解压：
    # 数据早就摆好了，直接返回，避免白等。
    if not os.path.isdir(os.path.join(tcl_dir, "encoding")) and \
            not os.path.isfile(os.path.join(tk_dir, "tk.tcl")):
        return
    if os.path.isdir(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.isdir(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir
    started = time.time()
    deadline = started + 60.0
    last_count = -1
    stable = 0
    while time.time() < deadline:
        ready = (os.path.isfile(os.path.join(tcl_dir, "init.tcl"))
                 and os.path.isfile(os.path.join(tk_dir, "tk.tcl"))
                 and _count_files(os.path.join(tcl_dir, "encoding")) >= 40
                 and os.path.isdir(os.path.join(tk_dir, "ttk")))
        count = _count_files(base)
        if ready and count == last_count and count > 100:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last_count = count
        time.sleep(0.1)
    waited = time.time() - started
    if os.environ.get("SV_TCL_DIAG"):
        _write_tcl_diag(base, tcl_dir, tk_dir, waited)


def _tcl_dirs(base: str):
    """找到 Tcl / Tk 数据目录：老版放在 ``tcl`` / ``tk``，新版放在
    ``_tcl_data`` / ``_tk_data``。"""
    for tcl_name, tk_name in (("tcl", "tk"), ("_tcl_data", "_tk_data")):
        tcl_dir = os.path.join(base, tcl_name)
        tk_dir = os.path.join(base, tk_name)
        if os.path.isfile(os.path.join(tcl_dir, "init.tcl")) or \
                os.path.isfile(os.path.join(tk_dir, "tk.tcl")):
            return tcl_dir, tk_dir
    return os.path.join(base, "tcl"), os.path.join(base, "tk")


def _count_files(root: str) -> int:
    """数一个目录下的文件总数（解压进度用；出错就返回 0）。"""
    total = 0
    try:
        for _dirpath, _dirnames, filenames in os.walk(root):
            total += len(filenames)
    except Exception:
        return 0
    return total


def _write_tcl_diag(base: str, tcl_dir: str, tk_dir: str, waited: float) -> None:
    """排查单文件版 Tcl 问题时把现场写到 exe 旁边（设置 SV_TCL_DIAG=1 才写）。"""
    try:
        lines = [
            "frozen=%s" % getattr(sys, "frozen", False),
            "executable=%s" % sys.executable,
            "_MEIPASS=%s" % base,
            "_MEIPASS(env)=%s" % os.environ.get("_MEIPASS2", ""),
            "TCL_LIBRARY=%s" % os.environ.get("TCL_LIBRARY", ""),
            "waited=%.2fs" % waited,
            "tcl dir exists=%s" % os.path.isdir(tcl_dir),
            "tk dir exists=%s" % os.path.isdir(tk_dir),
            "init.tcl exists=%s size=%s" % (
                os.path.isfile(os.path.join(tcl_dir, "init.tcl")),
                os.path.getsize(os.path.join(tcl_dir, "init.tcl"))
                if os.path.isfile(os.path.join(tcl_dir, "init.tcl")) else -1),
            "tk.tcl exists=%s" % os.path.isfile(os.path.join(tk_dir, "tk.tcl")),
        ]
        try:
            entries = sorted(os.listdir(base))
            lines.append("_MEIPASS entries(%d)=%s" % (len(entries), entries[:20]))
        except Exception as exc:
            lines.append("listdir failed: %s" % exc)
        try:
            tcl_files = sorted(os.listdir(tcl_dir))
            lines.append("tcl entries(%d)=%s" % (len(tcl_files), tcl_files[:20]))
        except Exception as exc:
            lines.append("tcl listdir failed: %s" % exc)
        target = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                              "tcl-diag.txt")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass


def parse_args(argv):
    flags = {"silent": False, "debug": False, "help": False, "review": False,
             "autotest": False, "selftest": False, "diag": False,
             "updatecheck": False}
    for raw in argv:
        token = raw.strip().lstrip("-/").lower()
        if token in ("h", "help", "?"):
            flags["help"] = True
        elif token in flags:
            flags[token] = True
        elif token == "test":
            flags["selftest"] = True
    return flags


def _message_box(title: str, text: str, kind: int = 0x10) -> None:
    """不依赖 Tk 的系统消息框（仅在启动早期出错时使用）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, kind)
    except Exception:
        pass


def run_update_check(dirs) -> int:
    """逐个下载源检查一次更新，结果写到数据目录下的 ``update-check.txt``。

    打包后没有控制台，这个文件就是排查"检查更新失败"现场的唯一出口。
    """
    from securevault import APP_VERSION
    from securevault.system import update

    lines = ["SecureVault %s 检查更新（本机时间 %s）"
             % (APP_VERSION, time.strftime("%Y-%m-%d %H:%M:%S"))]
    failed = 0
    for key, label, prefix in update.SOURCES:
        try:
            info = update.check_for_update(key)
        except Exception as exc:
            failed += 1
            lines.append("[失败] %s（%s）%s" % (label, prefix or "直连", exc))
            continue
        lines.append("[成功] %s（%s）" % (label, prefix or "直连"))
        at = update.version_tuple(info["latest"],)
        now = update.version_tuple(APP_VERSION)
        state = ("有新版本" if at > now else
                 "比当前版本旧（可以回退）" if at < now else "已是最新")
        lines.append("        最新版本：%s（当前 %s，%s）"
                     % (info["latest"], APP_VERSION, state))
        lines.append("        下载地址：%s" % info["url"])
        lines.append("        文件大小：%s" % update.describe_size(info["size"]))
    lines.append("")
    lines.append("结论：%d/%d 个下载源可用。" % (len(update.SOURCES) - failed,
                                              len(update.SOURCES)))
    target = os.path.join(dirs.root, "update-check.txt")
    try:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        _message_box(APP_NAME, "检查结果无法写入 %s：%s" % (target, exc))
        return 4
    print("\n".join(lines))
    return 0 if failed < len(update.SOURCES) else 4


def run_diagnostics() -> int:
    """排查用：打印 Tcl/Tk 初始化环境（只在开发时用，不影响正常启动）。"""
    import traceback

    print("frozen        =", getattr(sys, "frozen", False))
    print("executable    =", sys.executable)
    print("_MEIPASS      =", getattr(sys, "_MEIPASS", ""))
    print("_MEIPASS2 env =", os.environ.get("_MEIPASS2", ""))
    print("TCL_LIBRARY   =", os.environ.get("TCL_LIBRARY", ""))
    print("TK_LIBRARY    =", os.environ.get("TK_LIBRARY", ""))
    tcl_dir = os.environ.get("TCL_LIBRARY", "")
    if tcl_dir:
        init = os.path.join(tcl_dir, "init.tcl")
        print("init.tcl      =", init, os.path.isfile(init),
              os.path.getsize(init) if os.path.isfile(init) else -1)
        try:
            with open(init, "r", encoding="utf-8", errors="replace") as fh:
                print("init.tcl head =", fh.readline().strip()[:60])
        except Exception as exc:
            print("init.tcl read = FAILED", exc)
        print("encoding 文件数=", _count_files(os.path.join(tcl_dir, "encoding")))
    base = getattr(sys, "_MEIPASS", "")
    if base:
        print("_MEIPASS 文件数=", _count_files(base))
    try:
        import hashlib

        with open(os.path.join(tcl_dir, "init.tcl"), "rb") as fh:
            print("init.tcl sha1 =", hashlib.sha1(fh.read()).hexdigest())
        source = os.path.join(os.path.dirname(sys.executable), "tcl", "init.tcl")
        if os.path.isfile(source):
            with open(source, "rb") as fh:
                print("参考 init sha1=", hashlib.sha1(fh.read()).hexdigest())
    except Exception as exc:
        print("hash          = FAILED", exc)
    try:
        import _tkinter

        print("_tkinter      =", _tkinter.__file__)
        interpreter = _tkinter.create(None, "diag", "Tk", False, True, False)
        print("Tcl patchlevel=", interpreter.eval("info patchlevel"))
        print("tcl_library   =", interpreter.eval("set tcl_library"))
        print("init exists   =",
              interpreter.eval("file exists [file join $tcl_library init.tcl]"))
        try:
            interpreter.eval("source -encoding utf-8 [file join $tcl_library init.tcl]")
            print("source init.tcl = ok")
        except Exception as exc:
            print("source init.tcl = FAILED:", exc)
    except Exception:
        traceback.print_exc()
    _diag_native_tcl(base)
    try:
        import tkinter

        root = tkinter.Tk()
        print("Tk            = ok", root.tk.call("info", "patchlevel"))
        root.destroy()
    except Exception:
        traceback.print_exc()
    return 0


def _diag_native_tcl(base: str) -> None:
    """直接问 Tcl 到底哪里出错（拿 errorInfo），排查单文件版的 init.tcl 问题。"""
    import ctypes

    if not base:
        return
    for name in ("tcl86t.dll", "tcl86.dll"):
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        try:
            tcl = ctypes.WinDLL(path)
            tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
            tcl.Tcl_CreateInterp.restype = ctypes.c_void_p
            tcl.Tcl_Init.argtypes = [ctypes.c_void_p]
            tcl.Tcl_GetVar.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            tcl.Tcl_GetVar.restype = ctypes.c_char_p
            tcl.Tcl_Eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            tcl.Tcl_FindExecutable(sys.executable.encode("mbcs", "replace"))
            interp = tcl.Tcl_CreateInterp()
            rc = tcl.Tcl_Init(ctypes.c_void_p(interp))
            print("Tcl_Init rc   =", rc)
            for key in (b"errorInfo", b"tcl_library", b"tcl_patchLevel"):
                value = tcl.Tcl_GetVar(ctypes.c_void_p(interp), key, 0)
                text = value.decode("mbcs", "replace") if value else ""
                print("  %-14s= %s" % (key.decode(), text.replace("\n", "  |  ")[:300]))
            rc2 = tcl.Tcl_Eval(ctypes.c_void_p(interp),
                               b"source -encoding utf-8 [file join $tcl_library init.tcl]")
            print("source rc     =", rc2)
            value = tcl.Tcl_GetVar(ctypes.c_void_p(interp), b"errorInfo", 0)
            print("  errorInfo   =", (value or b"").decode("mbcs", "replace")
                  .replace("\n", "  |  ")[:400])
        except Exception as exc:
            print("native tcl diag failed:", exc)
        return


def main(argv=None) -> int:
    prepare_frozen_runtime()
    flags = parse_args(list(sys.argv[1:] if argv is None else argv))
    if flags["help"]:
        print(HELP_TEXT)
        return 0

    from securevault.core import paths

    dev = flags["review"] or flags["autotest"] or flags["selftest"]
    dirs = paths.resolve(dev)
    try:
        dirs.ensure()
    except paths.NotWritableError as exc:
        _message_box(APP_NAME, "程序所在目录不可写，无法保存数据：\n%s" % exc)
        return 3

    if flags["selftest"] or flags["autotest"]:
        from tools.selftest import run_selftest

        return run_selftest(dirs, verbose=flags["debug"] or flags["autotest"])

    if flags["diag"]:
        return run_diagnostics()

    if flags["updatecheck"]:
        return run_update_check(dirs)

    from securevault.system import single_instance

    if not single_instance.acquire():
        single_instance.notify_existing()
        return 0

    from securevault.ui.app import SecureVaultApp

    try:
        app = SecureVaultApp(dev=dev, silent=flags["silent"],
                             review=flags["review"])
    except Exception as exc:
        _message_box(APP_NAME, "启动失败：\n%s" % exc)
        return 4
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
