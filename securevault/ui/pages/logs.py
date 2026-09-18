"""日志页：日志文件列表 + 内容预览 + 清理。"""

from __future__ import annotations

import os
import tkinter as tk

from ...core import fileutil
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page


class LogsPage(Page):
    title = "日志"

    def build(self) -> None:
        outer = tk.Frame(self, bg=T.BG)
        outer.pack(fill="both", expand=True)

        card = W.Card(outer, "日志文件", self.fonts)
        card.pack(fill="both", expand=True, padx=T.px(T.PAD),
                   pady=(T.px(T.PAD), T.px(T.PAD)))

        head = tk.Frame(card.body, bg=T.CARD)
        head.pack(fill="x")
        self.retention_hint = self.hint(head, "", color=T.TEXT)
        self.retention_hint.configure(bg=T.CARD)
        self.retention_hint.pack(side="left")

        buttons = tk.Frame(card.body, bg=T.CARD)
        buttons.pack(fill="x", pady=(T.px(8), 0))
        W.FlatButton(buttons, text="刷新", kind="primary", command=self.refresh,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(buttons, text="打开日志文件夹", kind="default",
                     command=lambda: self.ctx.app.open_path(self.ctx.dirs.logs),
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(buttons, text="清除 7 天前的日志", kind="danger",
                     command=lambda: self._clear(7),
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(buttons, text="清除全部日志", kind="danger",
                     command=lambda: self._clear(0),
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        self.result_hint = self.hint(buttons, "")
        self.result_hint.configure(bg=T.CARD)
        self.result_hint.pack(side="left", padx=(T.px(10), 0))

        split = tk.Frame(card.body, bg=T.CARD)
        split.pack(fill="both", expand=True, pady=(T.px(10), 0))

        holder = self.make_tree(split, [
            ("day", "日期", 110, "center"),
            ("size", "大小", 90, "e"),
        ], height=14, checkable=False, on_menu=self._list_menu)
        holder.configure(width=T.px(220))
        holder.pack(side="left", fill="y")
        holder.pack_propagate(False)
        self.tree = holder.tree
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._load_selected())

        right = tk.Frame(split, bg=T.CARD)
        right.pack(side="left", fill="both", expand=True, padx=(T.px(10), 0))
        # 这一行是需要一直显示在页面里的标题（不是操作反馈），所以用真实标签。
        self.preview_title = W.Hint(right, "内容预览（默认显示末尾 300 行）",
                                    font=self.fonts.get("small"), color=T.TEXT,
                                    bg=T.CARD)
        self.preview_title.configure(bg=T.CARD)
        self.preview_title.pack(fill="x", pady=(0, T.px(6)))
        # 日志字体调大一点，并且自动换行（长行不用左右拖）
        self.preview = W.FlatText(right, width=78, height=20,
                                  font=self.fonts.get("mono"), wrap="word",
                                  readonly=True)
        self.preview.pack(fill="both", expand=True)
        self.preview.text.bind("<Button-3>", self._text_menu)

        self.files = []
        self._watch_id = None
        self._last_size = -1
        self._tick_count = 0

    # -- 实时刷新 --------------------------------------------------------

    def on_show(self) -> None:
        self.refresh()
        if self._watch_id is None:
            self._watch_id = self.after(1000, self._watch_tick)

    def _watch_tick(self) -> None:
        """每秒看一眼：日志文件变大了就自动更新（不用点刷新）。"""
        self._watch_id = None
        try:
            if self.winfo_ismapped():
                self._tick_count += 1
                if self._tick_count % 5 == 0:
                    days = [item.day for item in self.files]
                    if [item.day for item in self.ctx.logger.files()] != days:
                        self.refresh()
                        return
                self._reload_if_changed()
        except Exception:
            pass
        finally:
            self._watch_id = self.after(1000, self._watch_tick)

    def _reload_if_changed(self) -> None:
        item = self._selected_file()
        if item is None:
            return
        try:
            size = os.path.getsize(item.path)
        except OSError:
            return
        if size == self._last_size:
            return
        self._last_size = size
        at_bottom = True
        try:
            at_bottom = self.preview.text.yview()[1] >= 0.999
        except Exception:
            pass
        content = self.ctx.logger.read(item.day, 300)
        self.preview.set_text(content or "（该日志文件为空）")
        if at_bottom:
            try:
                self.preview.text.see("end")
            except Exception:
                pass
        self.preview_title.set_text("内容预览：%s（%s，实时）"
                                    % (item.day, fileutil.human_size(size)), T.TEXT)

    # -- 操作 ------------------------------------------------------------

    def refresh(self) -> None:
        logger = self.ctx.logger
        settings = self.ctx.engine.settings
        logger.set_retention(settings.log_retention)
        self.files = logger.files()
        rows = [[item.day, fileutil.human_size(item.size)] for item in self.files]
        self.fill_tree(self.tree, rows, keys=[item.day for item in self.files])
        self.retention_hint.set_text(
            "自动删除 %d 天以前的日志｜今天的日志：%s"
            % (settings.log_retention, logger.today_file))
        if self.files and not self.tree.selection():
            self.tree.selection_set("1")
            self._load_selected()
        elif not self.files:
            self.preview.set_text("（还没有日志文件）")

    def _selected_file(self):
        index = self.selected_index(self.tree)
        if index < 0 or index >= len(self.files):
            return None
        return self.files[index]

    def _load_selected(self) -> None:
        index = self.selected_index(self.tree)
        if index < 0 or index >= len(self.files):
            return
        item = self.files[index]
        content = self.ctx.logger.read(item.day, 300)
        self.preview.set_text(content or "（该日志文件为空）")
        try:
            self._last_size = os.path.getsize(item.path)
        except OSError:
            self._last_size = -1
        self.preview_title.set_text("内容预览：%s（%s）"
                                    % (item.day, fileutil.human_size(item.size)),
                                    T.TEXT)
        # 打开 / 刷新后自动停在最底部（最新日志）
        try:
            self.preview.text.see("end")
        except Exception:
            pass

    def _list_menu(self, event=None) -> None:
        from ..popup import show_menu

        if event is not None:
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
        item = self._selected_file()
        if item is None:
            self.ctx.notify("请先选一个日志文件。", "warn")
            return
        x = event.x_root if event is not None else self.winfo_pointerx()
        y = event.y_root if event is not None else self.winfo_pointery()
        show_menu(self, [
            ("打开这个日志文件", lambda: self.ctx.app.open_path(item.path)),
            ("复制文件路径", lambda: self._copy(item.path)),
            ("复制日志内容", lambda: self._copy(self.ctx.logger.read(item.day, 0))),
            ("打开日志文件夹", lambda: self.ctx.app.open_path(self.ctx.dirs.logs)),
        ], x, y, font=self.fonts.get("base"))

    def _text_menu(self, event=None) -> None:
        from ..popup import show_menu

        x = event.x_root if event is not None else self.winfo_pointerx()
        y = event.y_root if event is not None else self.winfo_pointery()
        item = self._selected_file()
        show_menu(self, [
            ("复制全部内容", lambda: self._copy(self.preview.get())),
            ("复制文件路径",
             lambda: self._copy(item.path if item else self.ctx.logger.today_file)),
            ("打开日志文件夹", lambda: self.ctx.app.open_path(self.ctx.dirs.logs)),
            ("刷新", self.refresh),
        ], x, y, font=self.fonts.get("base"))

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text or "")
        self.ctx.notify("已复制到剪贴板。", "success")

    def _clear(self, days: int) -> None:
        text = ("确定清除 7 天前的日志吗？" if days
                else "确定清除全部日志吗？（今天的日志会立刻重新创建）")
        if D.message(self, "清除日志", text, ("清除", "取消"), kind="warn") != 0:
            return
        # 危险操作先核对身份（不计入密码失败次数）
        if not self.ctx.app.verify_password("清除日志前请输入密码："):
            return
        removed = (self.ctx.logger.clear_before(days) if days
                   else self.ctx.logger.clear_all())
        self.result_hint.set_text("已删除 %d 个日志文件" % removed, T.SUCCESS)
        self.ctx.log("info", "已清除 %d 个日志文件" % removed)
        self.refresh()
