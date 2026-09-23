"""应用主外壳：窗口、锁定 / 首次设置、主面板、托盘与引擎的联动。"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox
from typing import Callable, Optional

from .. import APP_TITLE, APP_VERSION, PROJECT_URL
from ..core import fileutil, paths
from ..core.model import Mode
from ..crypto import keystore as ks
from ..storage.logger import Logger
from ..storage.store import Store, archive_legacy_database
from ..system import autostart, single_instance, touch as touchinput, update
from ..copy.engine import Engine
from . import dialogs as D
from . import theme as T
from . import widgets as W
from .pages import (AboutPage, ExcludePage, LogsPage, RecordsPage, SchedulePage,
                    SettingsPage, StatusPage, ToolsPage)

try:  # pywin32 缺失时程序仍然可用（只是没有托盘图标）
    from ..system.tray import TrayIcon
except Exception:  # pragma: no cover - 取决于运行环境
    TrayIcon = None  # type: ignore[assignment]

PAGE_TITLES = ["状态", "监控记录", "排除名单", "定时切换", "工具", "设置", "日志",
               "关于"]
PAGE_CLASSES = [StatusPage, RecordsPage, ExcludePage, SchedulePage,
                ToolsPage, SettingsPage, LogsPage, AboutPage]


def enable_dpi_awareness() -> float:
    """声明进程 DPI 感知并返回系统 DPI（在创建窗口之前调用）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    try:
        return float(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return 96.0


def work_area() -> tuple:
    """返回主屏工作区 (left, top, right, bottom)。"""
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    try:
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return 0, 0, 1920, 1080


def validate_recovery_code(text: str) -> str:
    """校验恢复码的格式；返回空串表示格式正确，否则返回错误说明。"""
    raw = "".join(ch for ch in (text or "") if ch not in "- \t\r\n")
    if not raw:
        return "请输入恢复码。"
    if len(raw) != 12:
        return "恢复码应该是 12 位（形如 XXXX-XXXX-XXXX），当前是 %d 位。" % len(raw)
    bad = sorted({ch.upper() for ch in raw if ch.upper() not in ks.RECOVERY_CHARSET})
    if bad:
        return ("恢复码里出现了不该有的字符：%s（注意容易看错的 I、O、0、1 不在其中）"
                % " ".join(bad))
    compact = (text or "").replace(" ", "")
    if "-" in compact:
        groups = compact.split("-")
        if len(groups) != 3 or any(len(group) != 4 for group in groups):
            return "恢复码的分段应该是 XXXX-XXXX-XXXX。"
    return ""


def _human_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%d 秒" % seconds
    minutes, rest = divmod(seconds, 60)
    if rest == 0:
        return "%d 分钟" % minutes
    return "%d 分 %d 秒" % (minutes, rest)


class AppContext:
    """页面使用的共享上下文。"""

    def __init__(self, app: "SecureVaultApp") -> None:
        self.app = app
        self.dirs = app.dirs
        self.fonts = app.fonts
        self._queue: "queue.Queue" = app.ui_queue

    @property
    def store(self) -> Store:
        return self.app.store

    @property
    def engine(self) -> Engine:
        return self.app.engine

    @property
    def logger(self) -> Logger:
        return self.app.logger

    def ui(self, callback: Callable, *args) -> None:
        """把回调排到界面线程执行。"""
        self._queue.put((callback, args))

    def run_bg(self, work: Callable[[], None]) -> None:
        """在后台线程执行耗时操作（结果用 ctx.ui 回到界面线程）。"""
        threading.Thread(target=_safe_call, args=(work,), daemon=True).start()

    def log(self, level: str, message: str) -> None:
        if self.app.logger:
            method = getattr(self.app.logger, level, None)
            if callable(method):
                method(message)

    def refresh_status(self) -> None:
        self.app.refresh_status_bar()

    def notify(self, text: str, level: str = "info") -> None:
        """统一的底部提示（所有操作反馈都走这里）。"""
        self.app.notify(text, level)


def _safe_call(work: Callable[[], None]) -> None:
    try:
        work()
    except Exception:
        pass


class SecureVaultApp:
    """唯一根窗口：锁屏 / 首次设置 / 主面板都挂在同一个 Tk 根窗口上。"""

    def __init__(self, dev: bool = False, silent: bool = False,
                 review: bool = False) -> None:
        self.dev = dev
        self.silent = silent
        self.review = review
        self.dirs = paths.resolve(dev)
        # 上次「下载并覆盖」留下的旧文件在这里清掉（程序已经换成新的了）
        try:
            update.cleanup_backups(self.dirs.exe_dir)
        except Exception:
            pass
        dpi = enable_dpi_awareness()
        self.scale = max(0.75, min(3.0, dpi / 96.0))
        T.set_scale(self.scale)

        self.store: Optional[Store] = None
        self.logger: Optional[Logger] = None
        self.engine: Optional[Engine] = None
        self.tray: Optional[TrayIcon] = None
        self.keystore: Optional[ks.KeyStore] = None
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.running = True
        self._service_started = False
        self._ui_locked = False
        self._page_holder = None
        self._pages = []
        self._window_mode = ""
        self._revealed = False
        self._lock_window = None
        self._setup_window = None

        self.root = tk.Tk()
        self.root.withdraw()
        try:
            # 先把整个窗口设成全透明：这样在设好尺寸、摆好内容之前，
            # 屏幕上不会先闪一下左上角的默认小窗口。
            self.root.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.root.title(APP_TITLE)
        self.root.configure(bg=T.BG)
        self.fonts = T.init_fonts(self.root)
        T.configure_ttk(self.root)
        self._set_icon()
        self._apply_scaling()
        self._apply_window_mode("lock", force=True)

        self.ctx = AppContext(self)
        # 按钮回调里出错时：写日志 + 在底部提示，不再"点了没反应"。
        W.BUTTON_ERROR_HOOK = self._on_button_error
        self._activate_message = single_instance.register_message()

        try:
            archive = archive_legacy_database(self.dirs)
        except Exception:
            archive = None
        self._legacy_archive = archive

        self.container = tk.Frame(self.root, bg=T.BG)
        self.container.pack(fill="both", expand=True)
        self._current_view = None

        self.root.protocol("WM_DELETE_WINDOW", self.on_close_request)
        self.root.bind("<Escape>", lambda _e: self.lock_now())
        self.root.bind("<F5>", lambda _e: self.refresh_page())
        self.root.bind("<Map>", self._on_map)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.bind("<<SecureVaultActivate>>", lambda _e: self.show_lock())

        self._start_tray()
        self._open_keystore()
        self._poll_queue()

    # ------------------------------------------------------------------
    # 初始化辅助
    # ------------------------------------------------------------------

    def _set_icon(self) -> None:
        icon = T.icon_path()
        if not icon:
            return
        try:
            self.root.iconbitmap(default=icon)
        except Exception:
            pass

    def _apply_scaling(self) -> None:
        try:
            self.root.tk.call("tk", "scaling", self.scale * 96.0 / 72.0)
        except Exception:
            pass

    def _window_size(self, mode: str):
        """返回某种窗口形态的 (宽, 高, x, y)（像素）。"""
        left, top, right, bottom = work_area()
        avail_w = max(640, right - left)
        avail_h = max(480, bottom - top)
        if mode == "lock":
            width = min(T.px(T.LOCK_WIDTH), avail_w - T.px(20))
            height = min(T.px(T.LOCK_HEIGHT), avail_h - T.px(40))
        elif mode == "setup":
            width = min(T.px(T.SETUP_WIDTH), avail_w - T.px(20))
            height = min(T.px(T.SETUP_HEIGHT), avail_h - T.px(40))
        else:
            width = min(T.px(T.DEFAULT_WIDTH), avail_w - T.px(40))
            height = min(T.px(T.DEFAULT_HEIGHT), avail_h - T.px(60))
            width = max(width, min(T.px(T.MIN_WIDTH), avail_w))
            height = max(height, min(T.px(T.MIN_HEIGHT), avail_h))
        x = left + max(0, (avail_w - width) // 2)
        y = top + max(0, (avail_h - height) // 3)
        return int(width), int(height), int(x), int(y)

    def _apply_window_mode(self, mode: str, force: bool = False) -> None:
        """锁屏 / 首次设置用小窗口，主面板用大窗口。"""
        if mode == self._window_mode and not force:
            return
        self._window_mode = mode
        width, height, x, y = self._window_size(mode)
        self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))
        if mode == "panel":
            left, top, right, bottom = work_area()
            self.root.resizable(True, True)
            self.root.maxsize(32767, 32767)
            self.root.minsize(min(T.px(760), right - left),
                              min(T.px(480), bottom - top))
        else:
            self.root.resizable(False, False)
            self.root.minsize(width, height)
            self.root.maxsize(width, height)
        self.root.update_idletasks()

    def _reveal(self) -> None:
        """显示窗口（此时尺寸与内容都已就绪，所以不会闪）。"""
        # 每次显示都重新居中：从托盘点开时窗口应该出现在屏幕中间。
        self._apply_window_mode(self._window_mode or "panel", force=True)
        try:
            self.root.attributes("-alpha", 1.0)
        except Exception:
            pass
        self.root.deiconify()
        self.root.lift()
        self._revealed = True

    def open_path(self, path: str) -> None:
        """用资源管理器打开一个文件夹 / 文件（以前这个方法漏了，点了没反应）。"""
        target = str(path or "").strip()
        if not target:
            self.notify("路径为空，无法打开。", "warn")
            return
        try:
            if not os.path.exists(target):
                os.makedirs(target, exist_ok=True)
        except OSError as exc:
            self.notify("路径不存在且无法创建：%s（%s）" % (target, exc), "error")
            return
        try:
            os.startfile(target)  # noqa: S606 - Windows 专用
        except Exception as exc:
            self.notify("打开失败：%s（%s）" % (target, exc), "error")
            return
        self.notify("已打开：%s" % target, "success")

    def _start_tray(self) -> None:
        if TrayIcon is None:
            self._tray_error = "缺少 pywin32，托盘图标不可用"
            return
        self._tray_error = ""
        icon = T.icon_path() or ""
        try:
            self.tray = TrayIcon(icon, APP_TITLE, self._on_tray_open,
                                 self._on_tray_exit)
            self.tray.start()
        except Exception as exc:  # 托盘起不来也不能影响程序本身
            self.tray = None
            self._tray_error = "托盘图标不可用：%s" % exc

    def _open_keystore(self) -> None:
        try:
            self.keystore = ks.KeyStore(self.dirs)
        except Exception as exc:
            self.root.deiconify()
            messagebox.showerror(APP_TITLE, "密钥库无法读取：\n%s\n\n"
                                 "为避免覆盖你的数据，程序将退出。" % exc)
            self.quit()
            return
        if not self.keystore.initialized:
            if self.review:
                self.keystore.initialize("review-pass-2026")
                self._seed_demo()
                self.start_service(show_panel=True)
                return
            self.show_setup()
            return
        if self.review:
            try:
                self.keystore.unlock("review-pass-2026")
            except Exception:
                pass
            if self.keystore.unlocked:
                self._seed_demo()
                self.start_service(show_panel=True)
                return
        if self.keystore.auto_unlock():
            # 有 DPAPI 密钥副本：后台直接跑起来（面板仍然是关着的）
            if self.start_service(show_panel=False):
                return
        # 启动不弹密码窗口：只驻留托盘。要打开面板请点托盘图标
        # （或者再双击一次 exe —— 单实例会把已有实例叫醒并弹出解锁窗口）。
        self._ui_locked = True
        self.root.withdraw()
        return

    def _seed_demo(self) -> None:
        """评审模式用的演示数据（只写开发数据目录）。"""
        try:
            self.logger = Logger(self.dirs, 14)
            store = Store(self.dirs.db_file, self.keystore.dek(), self.dirs.tmp)
        except Exception:
            return
        try:
            has_records = store.count_records() > 0
            has_rules = bool(store.list_exclude_rules())
            has_aliases = bool(store.list_aliases())
            has_slots = bool(store.list_schedule())
            if not has_records:
                for index, sample in enumerate(_DEMO_RECORDS):
                    from ..core.model import DeviceInfo, Record

                    store.insert_record(Record(
                        time=sample["time"], event=sample["event"],
                        action=sample["action"], note=sample["note"],
                        files=sample.get("files", 0),
                        bytes_copied=sample.get("bytes", 0),
                        elapsed=sample.get("elapsed", 0),
                        dest=sample.get("dest", ""),
                        device=DeviceInfo(**sample["device"])))
            if not has_rules:
                from ..core.model import ExcludeRule

                store.add_exclude_rule(ExcludeRule(
                    type="volume", value="备份盘", remark="评审演示规则"))
            if not has_aliases:
                from ..core.model import Alias

                store.upsert_alias(Alias(match="volume", value="我的U盘",
                                         alias="工作资料盘", remark="演示别名"))
            if not has_slots:
                from ..core.model import Mode, ScheduleSlot

                store.add_schedule(ScheduleSlot(start="22:00", end="06:00",
                                                mode=Mode.COPY, days=0x7F,
                                                remark="夜里自动复制"))
            settings = store.load_settings()
            if not settings.copy_dest:
                settings.copy_dest = os.path.join(self.dirs.root, "demo-dest")
                store.save_settings(settings)
        finally:
            store.close()

    # ------------------------------------------------------------------
    # 视图切换（锁屏 / 首次设置 / 主面板）
    # ------------------------------------------------------------------

    def _swap_view(self, view: tk.Frame, title: str = APP_TITLE,
                   mode: str = "panel") -> None:
        previous = self._current_view
        if previous is not None and previous is not view:
            previous.pack_forget()
            # 主面板要缓存复用（这是「不叠窗」的关键），临时视图用完即销毁。
            if previous is not self._page_holder:
                try:
                    previous.destroy()
                except Exception:
                    pass
        self._current_view = view
        self._apply_window_mode(mode)
        view.pack(fill="both", expand=True)
        self.root.title(title)
        self.root.update_idletasks()
        self._reveal()

    def show_lock(self) -> None:
        """显示解锁界面（打开面板一律需要密码）。"""
        if not self.keystore:
            return
        if not self.keystore.initialized:
            self.show_setup()
            return
        if self._service_started and not self._ui_locked and self._current_view is not None:
            self.show_panel()
            return
        if self._lock_window is not None and self._lock_window.winfo_exists():
            self._lock_window.lift()
            return
        self._ui_locked = True
        # 解锁是一个独立的**小弹窗**（原生控件、老一点的样式），不是主窗口里的页面。
        self.root.withdraw()
        self._lock_window = D.PasswordDialog(
            self.root, "解锁", "请输入密码：", "确定",
            validator=self._validate_unlock_password,
            on_accept=lambda _value: self._on_unlocked(),
            on_forgot=self._on_forgot_password)
        self._lock_window.reveal()
        self._forget_window_if_closed("_lock_window")

    def show_setup(self) -> None:
        if self._setup_window is not None and self._setup_window.winfo_exists():
            self._setup_window.lift()
            return
        self.root.withdraw()
        self._setup_window = D.PasswordDialog(
            self.root, "首次使用", "请设置主密码（至少 8 位）：", "确定",
            confirm_label="请再次输入密码：", require_length=8,
            note="忘记密码时可以用恢复码找回；恢复码会在设置完成后只显示一次。",
            on_accept=self._on_setup_done)
        self._setup_window.reveal()
        self._forget_window_if_closed("_setup_window")

    def _on_unlocked(self) -> None:
        self._lock_window = None
        self._after_unlock()

    def _on_forgot_password(self) -> None:
        self._lock_window = None
        # reset_with_recovery 内部会自己打开面板
        self.reset_with_recovery(open_panel=True)

    def _on_setup_done(self, password: str) -> None:
        self._setup_window = None
        self.complete_setup(password or "")

    def _validate_unlock_password(self, password: str) -> str:
        """解锁弹窗的校验：正确返回空串，否则返回要显示的错误文字。"""
        return self.unlock_with_password(password)

    def _forget_window_if_closed(self, attribute: str) -> None:
        window = getattr(self, attribute, None)
        if window is None:
            return
        try:
            alive = bool(window.winfo_exists())
        except Exception:
            alive = False
        if not alive:
            setattr(self, attribute, None)

    def show_panel(self) -> None:
        if not self._service_started:
            self.show_lock()
            return
        for window in (self._lock_window, self._setup_window):
            if window is not None and window.winfo_exists():
                try:
                    window.destroy()
                except Exception:
                    pass
        self._lock_window = None
        self._setup_window = None
        self._ui_locked = False
        self._swap_view(self._panel(), APP_TITLE, mode="panel")
        self.refresh_status_bar()

    def _panel(self) -> tk.Frame:
        if self._page_holder is not None:
            return self._page_holder
        panel = tk.Frame(self.container, bg=T.BG)

        header = tk.Frame(panel, bg=T.CARD)
        header.pack(fill="x")
        tk.Frame(header, bg=T.BORDER, height=1).pack(side="bottom", fill="x")
        title_box = tk.Frame(header, bg=T.CARD)
        title_box.pack(side="left", padx=(T.px(T.PAD), 0), pady=T.px(10))
        tk.Label(title_box, text=APP_TITLE, bg=T.CARD, fg=T.TEXT,
                 font=self.fonts.get("title")).pack(side="left")
        # 版本号：点一下就打开项目主页
        version = tk.Label(title_box, text="v%s" % APP_VERSION, bg=T.CARD,
                           fg=T.ACCENT, font=self.fonts.get("small"),
                           cursor="hand2")
        version.pack(side="left", padx=(T.px(8), 0), pady=(T.px(6), 0))
        version.bind("<Button-1>", lambda _e: self.open_project_page())
        version.bind("<Enter>", lambda _e: version.configure(fg=T.ACCENT_HOVER))
        version.bind("<Leave>", lambda _e: version.configure(fg=T.ACCENT))
        actions = tk.Frame(header, bg=T.CARD)
        actions.pack(side="right", padx=(0, T.px(T.PAD)), pady=T.px(8))
        W.FlatButton(actions, text="立即上锁", kind="default", command=self.lock_now,
                     font=self.fonts.get("base")).pack(side="right")
        W.FlatButton(actions, text="刷新当前页", kind="ghost", command=self.refresh_page,
                     font=self.fonts.get("base")).pack(side="right", padx=(0, T.px(8)))

        self.tabbar = W.TabBar(panel, PAGE_TITLES, self._switch_page,
                               font=self.fonts.get("tab"))
        self.tabbar.pack(fill="x")

        holder = tk.Frame(panel, bg=T.BG)
        holder.pack(fill="both", expand=True)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        self._pages = []
        for index, page_class in enumerate(PAGE_CLASSES):
            page = page_class(holder, self.ctx)
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            self._pages.append(page)

        # 底部自下而上：三段状态栏在最底下，提示条压在它上面
        self.statusbar = W.StatusBar(panel, font=self.fonts.get("small"))
        self.statusbar.pack(fill="x", side="bottom")
        self.notify_bar = W.NotifyBar(panel, font=self.fonts.get("small"))
        self.notify_bar.pack(fill="x", side="bottom")

        # 一进来就把上次的页面显示出来（不用先点一下顶栏）
        index = 0
        try:
            index = int(self.engine.settings.last_page) if self.engine else 0
        except Exception:
            index = 0
        if not 0 <= index < len(self._pages):
            index = 0
        self.tabbar.select(index)

        self._page_holder = panel
        return panel

    def _switch_page(self, index: int) -> None:
        if not self._pages:
            return
        # 先把要显示的页面刷新好，再显示 —— 否则会先闪一下空页面再填内容。
        try:
            self._holder_bg = self._pages[index].cget("bg")
        except Exception:
            pass
        try:
            self._pages[index].on_show()
        except Exception:
            pass
        try:
            self._pages[index].update_idletasks()
        except Exception:
            pass
        for position, page in enumerate(self._pages):
            if position == index:
                page.grid()
            else:
                page.grid_remove()
        if self.engine:
            settings = self.engine.settings
            settings.last_page = index
            try:
                self.engine.store.save_settings(settings)
            except Exception:
                pass

    def refresh_page(self) -> None:
        if not self._pages:
            return
        index = self.tabbar.current if hasattr(self, "tabbar") else 0
        try:
            self._pages[index].refresh()
            self.notify("已刷新「%s」页。" % PAGE_TITLES[index], "success")
        except Exception as exc:
            self.notify("刷新失败：%s" % exc, "error")

    # ------------------------------------------------------------------
    # 服务生命周期
    # ------------------------------------------------------------------

    def start_service(self, show_panel: bool = True) -> bool:
        if self._service_started:
            return True
        try:
            self.dirs.ensure()
            self.logger = Logger(self.dirs, 14)
            dek = self.keystore.dek()
            self.store = Store(self.dirs.db_file, dek, self.dirs.tmp)
            settings = self.store.load_settings()
            self.logger.set_retention(settings.log_retention)
            # 触屏：点输入框是否自动弹出屏幕键盘（没有触摸设备的电脑上无影响）
            touchinput.set_enabled(bool(getattr(settings, "touch_keyboard", True)))
            if self.keystore.auto_unlock_supported():
                if settings.background_monitor and not self.keystore.auto_unlock_enabled():
                    self.keystore.enable_auto_unlock()
                elif not settings.background_monitor and self.keystore.auto_unlock_enabled():
                    self.keystore.disable_auto_unlock()
            self.engine = Engine(
                store=self.store, logger=self.logger, dirs=self.dirs,
                on_record=lambda _r: self.ctx.ui(self._on_record),
                on_status=lambda text: self.ctx.ui(self._set_status_text, text),
            )
            if show_panel:
                self.engine.start()
            else:
                self.engine.start()
            self._service_started = True
            self.logger.info("SecureVault %s 已解锁并启动（数据目录：%s）"
                             % (APP_VERSION, self.dirs.root))
            if self._legacy_archive:
                self.logger.warn("检测到旧版（Go）数据库，已归档到：%s"
                                 % self._legacy_archive)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, "启动失败：\n%s" % exc)
            return False
        if show_panel:
            self.show_panel()
            if self._legacy_archive:
                D.message(self.root, "旧版数据已归档",
                          "检测到旧版本的加密数据库，已原样移动到下面的目录（没有删除）：",
                          ("知道了",), detail=self._legacy_archive)
        else:
            self._ui_locked = True
            self.root.withdraw()
        return True

    def lock_now(self) -> None:
        """上锁：面板消失、需要重新输密码；按设置决定是否继续后台监控。"""
        if not self._service_started:
            return
        settings = self.engine.settings
        if not settings.background_monitor:
            self.stop_service()
        self._ui_locked = True
        self.root.withdraw()
        if self.logger:
            self.logger.info("面板已上锁（后台监控：%s）"
                             % ("继续" if settings.background_monitor else "已停止"))

    def stop_service(self) -> None:
        if self.engine:
            try:
                self.engine.stop()
            except Exception:
                pass
        if self.store:
            try:
                self.store.close()
            except Exception:
                pass
            self.store = None
        self._service_started = False

    def apply_background_setting(self, enabled: bool) -> None:
        if not self.keystore:
            return
        try:
            if enabled:
                if self.keystore.unlocked:
                    self.keystore.enable_auto_unlock()
            else:
                self.keystore.disable_auto_unlock()
        except Exception:
            pass

    def apply_autostart(self, enabled: bool) -> None:
        target = paths.launch_script()
        ok = autostart.set_enabled(enabled, target)
        if self.logger:
            self.logger.info("开机自启已%s（%s）"
                             % ("开启" if enabled else "关闭", target if enabled else ""))
        return ok

    # ------------------------------------------------------------------
    # 安全操作
    # ------------------------------------------------------------------

    def change_password(self) -> None:
        old = D.ask_password_old(self.root, "修改密码", "请输入当前密码：", "下一步",
                                 show_forgot=True)
        if old is None:
            return
        if isinstance(old, dict) and old.get("forgot"):
            # 忘记当前密码：走恢复码重置（重置时就会设定新密码），然后回到本页
            if self.reset_with_recovery(open_panel=False):
                self.notify("密码已通过恢复码重置，新密码已经生效。", "success")
                D.message(self.root, "已重置",
                          "密码已通过恢复码重置为新密码。\n"
                          "如需再改一次，请重新点「修改密码…」。", ("确定",))
                self.refresh_page()
            return
        if isinstance(old, dict):
            return
        new = D.ask_password_old(self.root, "修改密码", "请输入新密码（至少 8 位）：",
                                 "确定", confirm_label="请再次输入新密码：",
                                 require_length=8)
        if new is None:
            return
        if isinstance(new, dict):
            return
        try:
            self.keystore.change_password(old, new)
        except ks.WrongPassword:
            D.message(self.root, "修改失败", "当前密码不正确。", ("确定",), kind="error")
            return
        except ks.PasswordTooShort as exc:
            D.message(self.root, "修改失败", str(exc), ("确定",), kind="warn")
            return
        except ks.LockedOut as exc:
            D.message(self.root, "已临时锁定", str(exc), ("确定",), kind="warn")
            return
        except Exception as exc:
            D.message(self.root, "修改失败", str(exc), ("确定",), kind="error")
            return
        if self.logger:
            self.logger.info("密码已修改")
        self.notify("密码已更新，恢复码保持不变。", "success")
        D.message(self.root, "修改成功", "密码已更新，恢复码保持不变。", ("确定",))
        self.refresh_page()

    def rotate_recovery(self) -> None:
        password = D.ask_password_old(self.root, "重新生成恢复码",
                                      "请输入当前密码（旧恢复码将立即失效）：", "确定",
                                      show_forgot=True)
        if password is None:
            return
        if isinstance(password, dict) and password.get("forgot"):
            # 忘记密码：用恢复码重置——重置本身就会轮换出新的恢复码并显示出来
            if self.reset_with_recovery(open_panel=False):
                self.notify("密码与恢复码都已通过恢复码重置。", "success")
                self.refresh_page()
            return
        if isinstance(password, dict):
            return
        try:
            code = self.keystore.rotate_recovery(password)
        except ks.WrongPassword:
            D.message(self.root, "失败", "密码不正确。", ("确定",), kind="error")
            return
        except Exception as exc:
            D.message(self.root, "失败", str(exc), ("确定",), kind="error")
            return
        if self.logger:
            self.logger.info("已重新生成恢复码")
        self.notify("已生成新的恢复码。", "success")
        D.show_recovery(self.root, code)

    def reset_with_recovery(self, open_panel: bool = True) -> bool:
        """忘记密码：先校验恢复码本身，通过后才让设置新密码。

        ``open_panel=False`` 时不打开主面板（用于"退出确认 / 危险操作确认"这类流程，
        重置完密码后继续原来的操作）。返回是否重置成功。
        """
        while True:
            code = D.ask_text_old(
                self.root, "忘记密码", "请输入恢复码（12 位，XXXX-XXXX-XXXX）：",
                note="恢复码不区分大小写，横线可有可无；"
                     "校验通过后就可以设置新密码。")
            if code is None:
                return False
            error = validate_recovery_code(code)
            if error:
                D.message(self.root, "恢复码格式不对", error, ("重新输入",),
                          kind="warn")
                continue
            try:
                # 先真的拿它去解一次密钥库：解不开就直接拒绝，不会走到改密码那一步。
                self.keystore.unlock_with_recovery(code)
            except ks.WrongRecoveryCode:
                D.message(self.root, "恢复码不正确", "这个恢复码解不开密钥库，请重新输入。",
                          ("重新输入",), kind="error")
                continue
            except ks.LockedOut as exc:
                D.message(self.root, "已临时锁定", str(exc), ("确定",), kind="warn")
                return False
            except Exception as exc:
                D.message(self.root, "校验失败", str(exc), ("确定",), kind="error")
                return False
            break

        new = D.ask_password_old(self.root, "设置新密码",
                                 "恢复码校验通过。请输入新密码（至少 8 位）：",
                                 "确定", confirm_label="请再次输入新密码：",
                                 require_length=8)
        if new is None or isinstance(new, dict):
            return False
        try:
            fresh = self.keystore.reset_with_recovery(code, new)
        except Exception as exc:
            D.message(self.root, "重置失败", str(exc), ("确定",), kind="error")
            return False
        D.message(self.root, "重置成功", "密码已重置，并轮换出新的恢复码。", ("确定",))
        D.show_recovery(self.root, fresh)
        if open_panel:
            self._after_unlock()
        return True

    def _after_unlock(self) -> None:
        if not self._service_started:
            if not self.start_service(show_panel=False):
                return
        self.show_panel()
        if self.logger:
            self.logger.info("已解锁并打开面板")

    def unlock_with_password(self, password: str) -> str:
        """返回空串表示成功，否则返回错误文本。"""
        try:
            self.keystore.unlock(password)
        except ks.WrongPassword:
            # 按用户要求分级提示：前两次只说密码错误，剩 3 次开始报次数，
            # 剩 2 次及以下再加上封禁时长。
            return self.keystore.status().failure_message()
        except ks.LockedOut as exc:
            return "已封禁：请在 %s 后重试" % _human_seconds(exc.seconds)
        except Exception as exc:
            return str(exc)
        return ""

    def verify_password(self, prompt: str = "请输入密码确认：") -> bool:
        """核对身份（用于清除日志 / 清除记录等危险操作）。

        **不计入失败次数** —— 只有"打开软件时输密码"才计数。
        这个弹窗同样有隐藏的「忘记密码」入口（空密码 10 秒内连点确定 6 次），
        点了之后走恢复码重置流程，重置成功就视为身份已确认。
        """
        result = D.ask_password_old(self.root, "确认身份", prompt, "确定",
                                    validator=self._validate_identity)
        if result is None:
            return False
        if isinstance(result, dict) and result.get("forgot"):
            if self.reset_with_recovery(open_panel=False):
                self.notify("密码已通过恢复码重置，操作继续。", "success")
                return True
            return False
        return True

    def _validate_identity(self, password: str) -> str:
        """核对身份用的校验（不计失败次数）。"""
        try:
            self.keystore.unlock(password, count_failure=False)
        except ks.WrongPassword:
            return "密码不正确"
        except ks.LockedOut as exc:
            return "已封禁：请在 %s 后重试" % _human_seconds(exc.seconds)
        except Exception as exc:
            return str(exc)
        return ""

    def complete_setup(self, password: str) -> bool:
        try:
            code = self.keystore.initialize(password)
        except ks.PasswordTooShort as exc:
            D.message(self.root, "密码太短", str(exc), ("确定",), kind="warn")
            return False
        except Exception as exc:
            D.message(self.root, "初始化失败", str(exc), ("确定",), kind="error")
            return False
        if not D.show_recovery(self.root, code):
            D.message(self.root, "提示", "恢复码已关闭；如果没保存，可以在"
                                        "「设置 → 重新生成恢复码」里生成新的。",
                      ("确定",), kind="warn")
        self.start_service(show_panel=True)
        return True

    # ------------------------------------------------------------------
    # 关闭 / 退出
    # ------------------------------------------------------------------

    def on_close_request(self) -> None:
        """点标题栏 × ：收进托盘并上锁（不退出进程）。"""
        if self._service_started:
            self.lock_now()
        else:
            self.root.withdraw()
            self._ui_locked = True

    def _on_unmap(self, event) -> None:
        if event.widget is not self.root:
            return
        if not self._service_started:
            return
        settings = self.engine.settings
        if not settings.minimize_to_tray:
            return
        if self.root.state() == "iconic":
            self.root.after(10, self.lock_now)

    def _on_map(self, _event=None) -> None:
        pass

    def request_exit(self) -> None:
        """托盘「退出」：已设置过密码时要求验证。"""
        if self.keystore and self.keystore.initialized:
            result = D.ask_password_old(self.root, "退出程序",
                                        "退出前请输入密码：", "退出",
                                        validator=self._validate_identity,
                                        force_exit="强制退出",
                                        on_force_exit=self.force_exit_dialog)
            if result is None:
                return
            if isinstance(result, dict) and result.get("force"):
                return
            if isinstance(result, dict) and result.get("forgot"):
                # 隐藏入口：用恢复码重置密码，然后继续原来的"退出"流程
                if not self.reset_with_recovery(open_panel=False):
                    return
                if D.message(self.root, "继续退出",
                             "密码已通过恢复码重置。现在退出程序吗？",
                             ("退出", "取消"), kind="question") != 0:
                    return
        self.quit()

    # ------------------------------------------------------------------
    # 强制退出（忘记密码、打不开面板时的出口）
    # ------------------------------------------------------------------

    def force_exit_dialog(self) -> None:
        """弹确认框；确认后强退程序并关掉开机自启。"""
        answer = D.message(
            self.root, "强制退出",
            "强制退出会立即关闭 SecureVault，并关掉「开机自动启动」。\n"
            "下次开机不会自动运行，需要手动双击 SecureVault.exe 才能启动。",
            ("强制退出", "取消"), kind="warn")
        if answer != 0:
            return
        self.force_exit()

    def force_exit(self) -> None:
        """不校验密码，直接关掉开机自启并退出程序。"""
        self.disable_autostart()
        try:
            if self.logger:
                self.logger.warn("执行了强制退出：开机自启已关闭")
        except Exception:
            pass
        self.quit()

    def disable_autostart(self) -> bool:
        """关掉开机自启：既删快捷方式，也把设置里的开关同步关掉。"""
        ok = False
        try:
            ok = bool(autostart.disable())
        except Exception:
            ok = False
        try:
            if self.engine:
                settings = self.engine.settings
                if settings.autostart:
                    settings.autostart = False
                    self.engine.store.save_settings(settings)
        except Exception:
            pass
        return ok

    def restart_now(self) -> None:
        """「下载并覆盖」完成后重启程序。"""
        target = paths.launch_script()
        self.quit()
        update.restart(target)

    def quit(self) -> None:
        self.running = False
        try:
            if self.logger:
                self.logger.info("程序退出")
        except Exception:
            pass
        self.stop_service()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.logger:
            try:
                self.logger.close()
            except Exception:
                pass
        # 先把还在排队的定时任务停掉，再销毁窗口（否则会冒出 Tcl 后台错误）
        self._stop_pending_timers()
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _stop_pending_timers(self) -> None:
        """取消界面队列轮询与各页面的定时刷新。"""
        pending = getattr(self, "_poll_after", None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except Exception:
                pass
            self._poll_after = None
        for page in self._pages or []:
            watch = getattr(page, "_watch_id", None)
            if watch is None:
                continue
            try:
                page.after_cancel(watch)
            except Exception:
                pass
            try:
                page._watch_id = None
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 托盘回调（来自托盘线程 -> 必须回到界面线程）
    # ------------------------------------------------------------------

    def _on_tray_open(self) -> None:
        self.ui_queue.put((self._tray_open, ()))

    def _on_tray_exit(self) -> None:
        self.ui_queue.put((self.request_exit, ()))

    def _tray_open(self) -> None:
        if self._service_started and not self._ui_locked:
            self.root.deiconify()
            self.root.lift()
            return
        if self._service_started and not self.keystore.unlocked:
            self.show_lock()
            return
        self.show_lock()

    # ------------------------------------------------------------------
    # 界面轮询
    # ------------------------------------------------------------------

    def _on_record(self) -> None:
        if self._pages and hasattr(self, "tabbar") and self.tabbar.current == 1:
            self.refresh_page()

    def _on_button_error(self, exc: BaseException, source: str = "") -> None:
        """任何按钮里抛出的异常都要能被看见（以前被静默吞掉）。"""
        text = "操作失败%s：%s" % (("（%s）" % source) if source else "", exc)
        try:
            if self.logger:
                self.logger.error(text)
        except Exception:
            pass
        try:
            self.notify(text, "error")
        except Exception:
            pass

    def _set_status_text(self, text: str) -> None:
        if hasattr(self, "statusbar"):
            self.statusbar.set(0, text)

    def notify(self, text: str, level: str = "info", timeout: int = 0) -> None:
        """在底部提示条显示一条消息（界面还没建好时忽略）。"""
        if hasattr(self, "notify_bar") and self.notify_bar is not None:
            try:
                self.notify_bar.show(text, level, timeout)
            except Exception:
                pass

    def open_project_page(self) -> None:
        """点版本号跳转项目主页。"""
        try:
            webbrowser.open(PROJECT_URL)
        except Exception:
            pass
        self.notify("已打开项目主页：%s" % PROJECT_URL)

    def refresh_status_bar(self) -> None:
        if not hasattr(self, "statusbar") or not self.engine:
            return
        state = self.engine.state()
        self.statusbar.set(0, self.engine.status_text())
        dest = state["copy_dest"] or "未设置复制目标"
        self.statusbar.set(1, "复制目标：%s" % dest)
        if state["copying"]:
            self.statusbar.set(2, "正在复制…")
        elif state["last_copy_at"]:
            self.statusbar.set(2, "上次复制：%s" % state["last_copy_at"])
        else:
            self.statusbar.set(2, "待机")

    def _poll_queue(self) -> None:
        if not self.running:
            return
        try:
            while True:
                callback, args = self.ui_queue.get_nowait()
                try:
                    callback(*args)
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            self.refresh_status_bar()
            if self._pages and self.tabbar.current == 0:
                self._pages[0].refresh()
        except Exception:
            pass
        self._poll_after = self.root.after(1000, self._poll_queue)

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------

    def run(self) -> int:
        self.root.mainloop()
        return 0


# ---------------------------------------------------------------------------
# 评审模式演示数据（只写开发数据目录 SecureVaultData-dev）
# ---------------------------------------------------------------------------

_DEMO_RECORDS = [
    {
        "time": "2026-09-17T09:12:03", "event": "arrival", "action": "copy",
        "note": "复制 128 个（跳过 12，失败 0），共 1.24 GB，耗时 32.1 秒",
        "files": 128, "bytes": 1331437568, "elapsed": 32100,
        "dest": "D:\\Backup\\工作资料盘",
        "device": {
            "letter": "E:", "name": "我的U盘", "bus_type": "USB",
            "model": "Kingston DataTraveler 3.0", "vendor": "Kingston",
            "vid": "0951", "pid": "1666", "usb_serial": "60A44C4139F1E011",
            "disk_serial": "5CDF_B803_81A0_4EE8", "fs": "exFAT",
            "capacity": 128849018880, "free": 64512819200, "physical_drive": 1,
            "device_instance": "USB\\VID_0951&PID_1666\\60A44C4139F1E011",
            "volume_guid": "\\\\?\\Volume{11111111-2222-3333-4444-555555555555}\\",
            "device_path": "\\\\?\\usb#vid_0951&pid_1666#60a44c4139f1e011#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}",
            "is_removable": True, "system_time": "2026-09-17T09:12:03",
        },
    },
    {
        "time": "2026-09-17T09:31:47", "event": "removal", "action": "none",
        "note": "设备移除",
        "device": {
            "letter": "E:", "name": "我的U盘", "bus_type": "USB",
            "model": "Kingston DataTraveler 3.0", "vid": "0951", "pid": "1666",
            "usb_serial": "60A44C4139F1E011", "disk_serial": "5CDF_B803_81A0_4EE8",
            "fs": "exFAT", "capacity": 128849018880, "free": 64512819200,
            "physical_drive": 1, "is_removable": True,
            "system_time": "2026-09-17T09:31:47",
        },
    },
    {
        "time": "2026-09-18T08:02:11", "event": "arrival", "action": "excluded",
        "note": "命中排除规则：卷标 = 备份盘",
        "device": {
            "letter": "F:", "name": "备份盘", "bus_type": "USB",
            "model": "Samsung BAR Plus", "vendor": "Samsung", "vid": "090C",
            "pid": "1000", "usb_serial": "0372516030001234",
            "disk_serial": "025123456789ABCD", "fs": "NTFS",
            "capacity": 268435456000, "free": 120000000000, "physical_drive": 2,
            "is_removable": True, "system_time": "2026-09-18T08:02:11",
        },
    },
    {
        "time": "2026-09-18T10:14:52", "event": "arrival", "action": "monitor",
        "note": "监控模式",
        "device": {
            "letter": "G:", "name": "SanDisk", "bus_type": "USB",
            "model": "SanDisk Ultra", "vid": "0781", "pid": "5581",
            "usb_serial": "4C530001120912114331", "disk_serial": "AA0102030405",
            "fs": "FAT32", "capacity": 32010903552, "free": 10737418240,
            "physical_drive": 3, "is_removable": True,
            "system_time": "2026-09-18T10:14:52",
        },
    },
    {
        "time": "2026-09-18T11:03:20", "event": "arrival", "action": "error",
        "note": "未设置复制目标目录",
        "device": {
            "letter": "H:", "name": "", "bus_type": "USB",
            "model": "Generic Mass Storage", "vid": "13FE", "pid": "4200",
            "usb_serial": "", "disk_serial": "1234567890AB", "fs": "FAT32",
            "capacity": 8000000000, "free": 4000000000, "physical_drive": 4,
            "is_removable": True, "system_time": "2026-09-18T11:03:20",
        },
    },
]
