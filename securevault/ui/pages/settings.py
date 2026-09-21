"""设置页：复制与运行、安全、数据位置。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog

from ...core.model import LOG_RETENTION_OPTIONS, Mode
from ...system import autostart, touch as touchinput
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page


class SettingsPage(Page):
    title = "设置"

    def build(self) -> None:
        area = self.scroll_area()
        body = area.body

        # --- 复制与运行 -------------------------------------------------
        card = W.Card(body, "复制与运行", self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.PAD), 0))
        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x")
        tk.Label(row, text="U 盘复制目标根目录", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.copy_dest = W.FlatEntry(row, width=46, font=self.fonts.get("base"))
        self.copy_dest.pack(side="left", padx=(T.px(8), T.px(6)))
        W.FlatButton(row, text="选择…", kind="default", command=self._pick_dest,
                     font=self.fonts.get("base"), padx=10).pack(side="left")
        W.FlatButton(row, text="清空", kind="ghost", command=lambda: self.copy_dest.set(""),
                     font=self.fonts.get("base"), padx=10).pack(side="left",
                                                               padx=(T.px(6), 0))

        options = tk.Frame(card.body, bg=T.CARD)
        options.pack(fill="x", pady=(T.px(10), 0))
        self.autostart = W.FlatCheck(options, "开机自动启动（开始菜单 → 启动 里的快捷方式）",
                                     True, font=self.fonts.get("base"), bg=T.CARD)
        self.autostart.pack(anchor="w")
        self.schedule_enable = W.FlatCheck(
            options, "启用定时切换", True, font=self.fonts.get("base"), bg=T.CARD)
        self.schedule_enable.pack(anchor="w", pady=(T.px(4), 0))
        self.record_removal = W.FlatCheck(
            options, "记录设备移除事件", True, font=self.fonts.get("base"), bg=T.CARD)
        self.record_removal.pack(anchor="w", pady=(T.px(4), 0))
        self.minimize_to_tray = W.FlatCheck(
            options, "最小化窗口时收进托盘并上锁", True,
            font=self.fonts.get("base"), bg=T.CARD)
        self.minimize_to_tray.pack(anchor="w", pady=(T.px(4), 0))
        self.background = W.FlatCheck(
            options, "关闭面板后继续在后台监控与复制（推荐）", True,
            font=self.fonts.get("base"), bg=T.CARD)
        self.background.pack(anchor="w", pady=(T.px(4), 0))
        tk.Label(options, text="关闭该项后，关闭面板会停止引擎并清空内存密钥；"
                              "重启后必须先输入密码才会开始监控。",
                 bg=T.CARD, fg=T.TEXT_DIM, font=self.fonts.get("small"),
                 anchor="w", justify="left").pack(anchor="w", padx=(T.px(20), 0))
        self.touch_keyboard = W.FlatCheck(
            options, "触屏设备：点输入框时自动弹出屏幕键盘", True,
            font=self.fonts.get("base"), bg=T.CARD)
        self.touch_keyboard.pack(anchor="w", pady=(T.px(4), 0))
        self.verify_dest = W.FlatCheck(
            options, "复制前检查目标文件是否还在（被误删了就重新复制）", True,
            font=self.fonts.get("base"), bg=T.CARD,
            command=self._toggle_verify_dest)
        self.verify_dest.pack(anchor="w", pady=(T.px(4), 0))
        verify_row = tk.Frame(options, bg=T.CARD)
        verify_row.pack(anchor="w", padx=(T.px(20), 0), pady=(T.px(2), 0))
        tk.Label(verify_row, text="目标文件不存在时：", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("small")).pack(side="left")
        self.verify_action = W.FlatSelect(
            verify_row, [("recopy", "重新复制"), ("skip", "不复制")], "recopy",
            font=self.fonts.get("small"), width=10)
        self.verify_action.pack(side="left", padx=(T.px(6), 0))

        row2 = tk.Frame(card.body, bg=T.CARD)
        row2.pack(fill="x", pady=(T.px(10), 0))
        tk.Label(row2, text="日志保留天数", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.retention = W.FlatSelect(
            row2, [(str(value), "%d 天" % value) for value in LOG_RETENTION_OPTIONS],
            "14", font=self.fonts.get("base"), width=8)
        self.retention.pack(side="left", padx=(T.px(8), T.px(14)))
        W.FlatButton(row2, text="保存设置", kind="primary", command=self.save,
                     font=self.fonts.get("base")).pack(side="left")
        self.save_hint = self.hint(row2, "")
        self.save_hint.configure(bg=T.CARD)
        self.save_hint.pack(side="left", padx=(T.px(12), 0))

        # --- 安全 -------------------------------------------------------
        card2 = W.Card(body, "安全", self.fonts)
        card2.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), 0))
        row3 = tk.Frame(card2.body, bg=T.CARD)
        row3.pack(fill="x")
        W.FlatButton(row3, text="修改密码…", kind="primary",
                     command=self.ctx.app.change_password,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row3, text="重新生成恢复码…", kind="default",
                     command=self.ctx.app.rotate_recovery,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row3, text="立即上锁", kind="primary",
                     command=self.ctx.app.lock_now,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        # 这一行是常驻状态（不是操作反馈），所以用真实标签留在卡片里。
        self.security_hint = W.Hint(card2.body, "", font=self.fonts.get("small"),
                                    color=T.TEXT_DIM, bg=T.CARD)
        self.security_hint.pack(fill="x", pady=(T.px(10), 0))

        # --- 数据位置 ---------------------------------------------------
        card3 = W.Card(body, "数据位置", self.fonts)
        card3.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), T.px(T.PAD)))
        self.path_rows = {}
        for key, label in (("exe", "程序目录"), ("data", "数据目录"),
                           ("db", "数据库"), ("keystore", "密钥库"),
                           ("logs", "日志目录"), ("startup", "自启快捷方式")):
            row, holder = W.labeled_row(card3.body, label, self.fonts.get("base"),
                                        bg=T.CARD, label_width=11)
            row.pack(fill="x", pady=T.px(2))
            value = tk.Label(holder, text="-", bg=T.CARD, fg=T.TEXT,
                             font=self.fonts.get("small"), anchor="w", justify="left")
            value.pack(fill="x")
            self.path_rows[key] = value
        row4 = tk.Frame(card3.body, bg=T.CARD)
        row4.pack(fill="x", pady=(T.px(8), 0))
        W.FlatButton(row4, text="打开数据目录", kind="default",
                     command=lambda: self.ctx.app.open_path(self.ctx.dirs.root),
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row4, text="打开日志目录", kind="default",
                     command=lambda: self.ctx.app.open_path(self.ctx.dirs.logs),
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        self.hint(row4, "所有数据都只在程序所在目录，不写 C 盘其它位置").pack(
            side="left", padx=(T.px(12), 0))

    # -- 操作 ------------------------------------------------------------

    def _pick_dest(self) -> None:
        path = filedialog.askdirectory(
            parent=self, title="选择复制目标根目录",
            initialdir=self.copy_dest.get() or "D:\\")
        if path:
            self.copy_dest.set(path)
            self.ctx.notify("已选择复制目标：%s（记得点「保存设置」）" % path, "success")

    def _toggle_verify_dest(self, value: bool) -> None:
        self.verify_action.set_enabled(bool(value))
        self.ctx.notify("已%s「目标文件存在性检查」。" % ("开启" if value else "关闭"),
                        "info")

    def save(self) -> None:
        """保存设置。任何一步出错都要有明确反馈（用户反馈过"点了保存没反应"）。"""
        try:
            self._save_impl()
        except Exception as exc:
            if self.ctx.logger:
                try:
                    self.ctx.logger.error("保存设置失败：%s" % exc)
                except Exception:
                    pass
            self.save_hint.set_text("保存失败：%s" % exc, T.DANGER)
            self.ctx.notify("保存设置失败：%s" % exc, "error")

    def _save_impl(self) -> None:
        settings = self.ctx.engine.settings
        dest = self.copy_dest.get().strip()
        if dest and not os.path.isdir(dest):
            answer = D.message(self, "目标目录不存在",
                               "目录不存在：\n%s\n\n要现在创建吗？" % dest,
                               ("创建", "取消"), kind="question")
            if answer != 0:
                return
            try:
                os.makedirs(dest, exist_ok=True)
            except OSError as exc:
                self.save_hint.set_text("创建目录失败：%s" % exc, T.DANGER)
                return
        settings.copy_dest = dest
        settings.manual_mode = self.ctx.engine.manual_mode
        settings.log_retention = int(self.retention.value)
        settings.schedule_enable = self.schedule_enable.value
        settings.record_removal = self.record_removal.value
        settings.minimize_to_tray = self.minimize_to_tray.value
        settings.background_monitor = self.background.value
        settings.touch_keyboard = self.touch_keyboard.value
        settings.autostart = self.autostart.value
        settings.verify_dest_exists = self.verify_dest.value
        settings.verify_dest_action = self.verify_action.value
        if not self.ctx.engine.save_settings(settings):
            self.save_hint.set_text("保存失败，详见日志。", T.DANGER)
            self.ctx.notify("保存设置失败，详见日志。", "error")
            return
        try:
            self.ctx.app.apply_autostart(settings.autostart)
        except Exception as exc:
            self.ctx.notify("开机自启设置失败：%s" % exc, "warn")
        try:
            self.ctx.app.apply_background_setting(settings.background_monitor)
        except Exception as exc:
            self.ctx.notify("后台运行设置失败：%s" % exc, "warn")
        try:
            touchinput.set_enabled(settings.touch_keyboard)
        except Exception:
            pass
        self.save_hint.set_text("已保存。", T.SUCCESS)
        self.ctx.notify("设置已保存并生效。", "success")
        self.refresh_paths()
        self.ctx.refresh_status()

    # -- 刷新 ------------------------------------------------------------

    def refresh(self) -> None:
        settings = self.ctx.engine.settings
        self.copy_dest.set(settings.copy_dest)
        self.retention.set(str(settings.log_retention))
        self.schedule_enable.set(settings.schedule_enable)
        self.record_removal.set(settings.record_removal)
        self.minimize_to_tray.set(settings.minimize_to_tray)
        self.background.set(settings.background_monitor)
        self.touch_keyboard.set(getattr(settings, "touch_keyboard", True))
        self.verify_dest.set(getattr(settings, "verify_dest_exists", True))
        self.verify_action.set(getattr(settings, "verify_dest_action", "recopy"))
        self.verify_action.set_enabled(self.verify_dest.value)
        self.autostart.set(autostart.enabled())
        self.refresh_paths()

    def refresh_paths(self) -> None:
        dirs = self.ctx.dirs
        keystore = self.ctx.app.keystore
        status = keystore.status()
        self.path_rows["exe"].configure(text=dirs.exe_dir)
        self.path_rows["data"].configure(text=dirs.root)
        self.path_rows["db"].configure(text=dirs.db_file)
        self.path_rows["keystore"].configure(text=dirs.keystore_file)
        self.path_rows["logs"].configure(text=dirs.logs)
        link = autostart.link_path()
        self.path_rows["startup"].configure(
            text="%s（%s）" % (link, "已启用" if autostart.enabled() else "未启用"))
        mode = Mode.label(self.ctx.engine.effective_mode)
        auto = "已开启" if status.auto_unlock else "未开启"
        self.security_hint.set_text(
            "当前生效模式：%s｜后台自动解锁（DPAPI 密钥副本）：%s｜连续失败 %d 次"
            % (mode, auto, status.fail_count), T.TEXT_DIM)
