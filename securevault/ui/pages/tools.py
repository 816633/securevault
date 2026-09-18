"""工具页：桌面整理复制 + 设备别名。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog

from ...core import fileutil
from ...core.model import MATCH_FIELDS, MATCH_FIELD_LABELS, Alias
from ...system import devices
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page


class ToolsPage(Page):
    title = "工具"

    def build(self) -> None:
        area = self.scroll_area()
        body = area.body

        # --- 工具 1 -----------------------------------------------------
        card = W.Card(body, "工具 1 · 桌面整理复制", self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.PAD), 0))
        tk.Label(card.body, text="把桌面（或指定目录）里的所有内容增量复制到目标目录的「桌面」文件夹。",
                 bg=T.CARD, fg=T.TEXT_DIM, font=self.fonts.get("small"),
                 anchor="w").pack(fill="x")

        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x", pady=(T.px(8), 0))
        tk.Label(row, text="源目录", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.desktop_src = W.FlatEntry(row, width=42, font=self.fonts.get("base"))
        self.desktop_src.pack(side="left", padx=(T.px(6), T.px(6)))
        W.FlatButton(row, text="选择…", kind="default", command=self._pick_src,
                     font=self.fonts.get("base"), padx=10).pack(side="left")
        W.FlatButton(row, text="恢复默认", kind="ghost", command=self._default_src,
                     font=self.fonts.get("base"), padx=10).pack(side="left",
                                                               padx=(T.px(6), 0))

        row2 = tk.Frame(card.body, bg=T.CARD)
        row2.pack(fill="x", pady=(T.px(6), 0))
        tk.Label(row2, text="目标目录", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.desktop_dest = W.FlatEntry(row2, width=42, font=self.fonts.get("base"))
        self.desktop_dest.pack(side="left", padx=(T.px(6), T.px(6)))
        self.desktop_dest_pick = W.FlatButton(row2, text="选择…", kind="default",
                                              command=self._pick_dest,
                                              font=self.fonts.get("base"), padx=10)
        self.desktop_dest_pick.pack(side="left")

        row3 = tk.Frame(card.body, bg=T.CARD)
        row3.pack(fill="x", pady=(T.px(8), 0))
        self.use_same_dest = W.FlatCheck(row3, "使用与 U 盘复制相同的目标目录", False,
                                         font=self.fonts.get("base"), bg=T.CARD,
                                         command=self._toggle_same_dest)
        self.use_same_dest.pack(side="left")
        W.FlatButton(row3, text="保存路径", kind="default", command=self._save_paths,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(14), 0))
        # 「开始整理复制」单独换行，样式也和普通按钮区分开
        row3b = tk.Frame(card.body, bg=T.CARD)
        row3b.pack(fill="x", pady=(T.px(8), 0))
        self.start_desktop_button = W.FlatButton(
            row3b, text="▶  开始整理复制", kind="primary",
            command=self._start_desktop_copy, font=self.fonts.get("subtitle"),
            padx=20, pady=7)
        self.start_desktop_button.pack(side="left")
        self.hint(row3b, "把源目录里的内容增量复制到目标目录\\桌面").pack(
            side="left", padx=(T.px(12), 0))
        self.desktop_hint = self.hint(card.body, "")
        self.desktop_hint.configure(bg=T.CARD)
        self.desktop_hint.pack(fill="x", pady=(T.px(8), 0))

        # --- 工具 2 -----------------------------------------------------
        card2 = W.Card(body, "工具 2 · 设备别名（同时决定复制出的文件夹名）", self.fonts)
        card2.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), T.px(T.PAD)))

        form = tk.Frame(card2.body, bg=T.CARD)
        form.pack(fill="x")
        tk.Label(form, text="匹配属性", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.alias_match = W.FlatSelect(form, MATCH_FIELDS, "volume",
                                        font=self.fonts.get("base"), width=14)
        self.alias_match.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(form, text="匹配值", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.alias_value = W.FlatEntry(form, width=18, font=self.fonts.get("base"))
        self.alias_value.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(form, text="别名", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.alias_name = W.FlatEntry(form, width=14, font=self.fonts.get("base"))
        self.alias_name.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(form, text="备注", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.alias_remark = W.FlatEntry(form, width=14, font=self.fonts.get("base"))
        self.alias_remark.pack(side="left", padx=(T.px(6), 0))

        row4 = tk.Frame(card2.body, bg=T.CARD)
        row4.pack(fill="x", pady=(T.px(8), 0))
        W.FlatButton(row4, text="添加别名", kind="primary", command=self.add_alias,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row4, text="保存修改", kind="default", command=self.update_alias,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row4, text="删除选中", kind="danger", command=self.delete_alias,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row4, text="用本机磁盘填充匹配值…", kind="default",
                     command=self._fill_from_device,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))

        holder = self.make_tree(card2.body, [
            ("match", "匹配属性", 130, "w"),
            ("value", "匹配值", 220, "w"),
            ("alias", "别名", 180, "w"),
            ("remark", "备注", 220, "w"),
        ], height=6, stretch_column="remark", on_double=self.update_alias,
            on_menu=self._alias_menu)
        holder.pack(fill="both", expand=True, pady=(T.px(8), 0))
        self.alias_tree = holder.tree
        self.alias_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select_alias())
        self.alias_hint = self.hint(card2.body, "")
        self.alias_hint.configure(bg=T.CARD)
        self.alias_hint.pack(fill="x", pady=(T.px(8), 0))

        self.aliases = []
        self.editing_id = 0

    # -- 工具 1 ----------------------------------------------------------

    def _pick_src(self) -> None:
        path = filedialog.askdirectory(parent=self, title="选择源目录",
                                       initialdir=self.desktop_src.get() or os.path.expanduser("~"))
        if path:
            self.desktop_src.set(path)
            self.ctx.notify("已选择源目录：%s" % path, "success")

    def _pick_dest(self) -> None:
        path = filedialog.askdirectory(parent=self, title="选择目标目录",
                                       initialdir=self.desktop_dest.get() or "D:\\")
        if path:
            self.desktop_dest.set(path)
            self.use_same_dest.set(False)
            self.ctx.notify("已选择目标目录：%s" % path, "success")

    def _default_src(self) -> None:
        from ...core import paths

        self.desktop_src.set(paths.desktop_dir())

    def _toggle_same_dest(self, value: bool) -> None:
        self.desktop_dest.set_enabled(not value)
        self.desktop_dest_pick.set_enabled(not value)
        if value:
            self.desktop_dest.set(self.ctx.engine.settings.copy_dest or "")
            self.ctx.notify("已改为使用与 U 盘复制相同的目标目录（输入框与选择按钮已禁用）。",
                            "info")
        else:
            self.ctx.notify("可以单独指定桌面整理的目标目录了。", "info")

    def _save_paths(self) -> None:
        settings = self.ctx.engine.settings
        settings.desktop_src = self.desktop_src.get().strip()
        settings.desktop_dest = "" if self.use_same_dest.value else self.desktop_dest.get().strip()
        if self.ctx.engine.save_settings(settings):
            self.desktop_hint.set_text("路径已保存。", T.SUCCESS)
        else:
            self.desktop_hint.set_text("保存失败，详见日志。", T.DANGER)

    def _start_desktop_copy(self) -> None:
        self._save_paths()
        self.desktop_hint.set_text("正在整理复制…（后台进行，可继续操作其它页面）", T.TEXT_DIM)

        def work() -> None:
            try:
                result, error = self.ctx.engine.desktop_copy()
            except Exception as exc:
                result, error = None, str(exc)
            self.ctx.ui(self._on_desktop_done, result, error)

        self.ctx.run_bg(work)

    def _on_desktop_done(self, result, error: str) -> None:
        if error:
            self.desktop_hint.set_text(error, T.DANGER)
            return
        if result is None:
            self.desktop_hint.set_text("复制未执行。", T.WARN)
            return
        if result.errors:
            self.desktop_hint.set_text("复制结束但有问题：%s" % result.errors[0], T.WARN)
            return
        self.desktop_hint.set_text("完成：%s（目标：%s）"
                                   % (result.summary, result.dest_dir), T.SUCCESS)

    # -- 工具 2 ----------------------------------------------------------

    def _read_alias(self) -> Alias:
        return Alias(id=self.editing_id, match=self.alias_match.value,
                     value=self.alias_value.get().strip(),
                     alias=self.alias_name.get().strip(),
                     remark=self.alias_remark.get().strip())

    def add_alias(self) -> None:
        alias = self._read_alias()
        alias.id = 0
        if not alias.value or not alias.alias:
            self.alias_hint.set_text("匹配值与别名都要填写。", T.WARN)
            return
        old_aliases = self.ctx.store.list_aliases()
        self.ctx.store.upsert_alias(alias)
        self.ctx.engine.reload()
        renamed = self.ctx.engine.rename_after_alias_change(
            old_aliases, self.ctx.store.list_aliases())
        if renamed:
            self.alias_hint.set_text("已添加别名：%s；复制目录已改名为：%s"
                                     % (alias.alias, "、".join(renamed)), T.SUCCESS)
        else:
            self.alias_hint.set_text("已添加别名：%s（下次复制会写成 <原名>_%s）"
                                     % (alias.alias, alias.alias), T.SUCCESS)
        self._reset_alias_form()
        self.reload()

    def update_alias(self) -> None:
        if not self.editing_id:
            self.alias_hint.set_text("请先选中一行。", T.WARN)
            return
        alias = self._read_alias()
        if not alias.value or not alias.alias:
            self.alias_hint.set_text("匹配值与别名都要填写。", T.WARN)
            return
        old_aliases = self.ctx.store.list_aliases()
        self.ctx.store.upsert_alias(alias)
        self.ctx.engine.reload()
        renamed = self.ctx.engine.rename_after_alias_change(
            old_aliases, self.ctx.store.list_aliases())
        if renamed:
            self.alias_hint.set_text("别名已保存，复制目录已改名为：%s"
                                     % "、".join(renamed), T.SUCCESS)
        else:
            self.alias_hint.set_text("别名已保存（该设备当前不在线，改名会在下次接入时进行）。",
                                     T.SUCCESS)
        self.reload()

    def delete_alias(self) -> None:
        index = self.selected_index(self.alias_tree)
        if index < 0 or index >= len(self.aliases):
            self.alias_hint.set_text("请先选中一行。", T.WARN)
            return
        # 删别名后目录名要从 <原名>_<别名> 改回 <原名>
        old_aliases = self.ctx.store.list_aliases()
        self.ctx.store.delete_alias(self.aliases[index].id)
        self.editing_id = 0
        self.ctx.engine.reload()
        renamed = self.ctx.engine.rename_after_alias_change(
            old_aliases, self.ctx.store.list_aliases())
        if renamed:
            self.alias_hint.set_text("已删除别名，复制目录已改回：%s"
                                     % "、".join(renamed), T.SUCCESS)
        else:
            self.alias_hint.set_text("已删除别名（该设备当前不在线，"
                                     "改名会在下次接入复制时进行）。", T.SUCCESS)
        self._reset_alias_form()
        self.reload()

    def _on_select_alias(self) -> None:
        index = self.selected_index(self.alias_tree)
        if index < 0 or index >= len(self.aliases):
            return
        alias = self.aliases[index]
        self.editing_id = alias.id
        self.alias_match.set(alias.match)
        self.alias_value.set(alias.value)
        self.alias_name.set(alias.alias)
        self.alias_remark.set(alias.remark)

    def _reset_alias_form(self) -> None:
        self.editing_id = 0
        self.alias_value.set("")
        self.alias_name.set("")
        self.alias_remark.set("")

    def _fill_from_device(self) -> None:
        self.alias_hint.set_text("正在读取本机磁盘…", T.TEXT_DIM)

        def work() -> None:
            try:
                snapshot = devices.snapshot()
            except Exception:
                snapshot = {}
            values = []
            for letter in sorted(snapshot):
                info = snapshot[letter]
                for field, value in (
                        ("volume", info.name), ("model", info.model), ("vid", info.vid),
                        ("pid", info.pid), ("usbSerial", info.usb_serial),
                        ("diskSerial", info.disk_serial),
                        ("instance", info.device_instance)):
                    if value:
                        values.append((field, value, "%s · %s" % (letter,
                                                                  info.display_name)))
            self.ctx.ui(self._show_device_values, values)

        self.ctx.run_bg(work)

    def _show_device_values(self, values) -> None:
        if not values:
            self.alias_hint.set_text("没有可用的磁盘信息。", T.WARN)
            return
        dialog = D.BaseDialog(self, "选择匹配值", width=600)
        area = W.ScrollArea(dialog.body)
        area.pack(fill="both", expand=True)
        # 每个选项**整行**显示（不截断、不挤一起），窗口放不下时可以滚动
        for field, value, note in values:
            label = MATCH_FIELD_LABELS.get(field, field)
            button = W.FlatButton(
                area.body, text="%s：%s    （%s）" % (label, value, note),
                kind="ghost", font=self.fonts.get("base"), anchor="w", padx=10, pady=6)
            button.pack(fill="x", pady=T.px(3))
            button.set_command(lambda f=field, v=value: _fill_alias(self, dialog, f, v))
        W.FlatButton(dialog.body, text="取消", kind="default", command=dialog.destroy,
                     font=self.fonts.get("base")).pack(anchor="e", pady=(T.px(10), 0))
        dialog.finish()

    def _alias_menu(self, event=None) -> None:
        from ..popup import show_menu

        if event is not None:
            row = self.alias_tree.identify_row(event.y)
            if row:
                self.alias_tree.selection_set(row)
        index = self.selected_index(self.alias_tree)
        if index < 0 or index >= len(self.aliases):
            self.ctx.notify("请先选一条别名。", "warn")
            return
        alias = self.aliases[index]
        show_menu(self, [
            ("载入到上面的表单", self._on_select_alias),
            ("复制匹配值", lambda: self._copy(alias.value)),
            ("复制别名", lambda: self._copy(alias.alias)),
            ("删除这一条", self.delete_alias),
        ], event.x_root if event else self.winfo_pointerx(),
            event.y_root if event else self.winfo_pointery(),
            font=self.fonts.get("base"))

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text or "")
        self.ctx.notify("已复制：%s" % (text or ""), "success")

    # -- 刷新 ------------------------------------------------------------

    def refresh(self) -> None:
        settings = self.ctx.engine.settings
        self.desktop_src.set(settings.desktop_src or "")
        same = not settings.desktop_dest
        self.use_same_dest.set(same)
        self.desktop_dest.set(settings.desktop_dest or settings.copy_dest or "")
        self.desktop_dest.set_enabled(not same)
        self.desktop_dest_pick.set_enabled(not same)
        if not self.desktop_src.get():
            self._default_src()
        self.reload()

    def reload(self) -> None:
        self.aliases = self.ctx.store.list_aliases()
        rows = [[MATCH_FIELD_LABELS.get(alias.match, alias.match), alias.value,
                 alias.alias, alias.remark or "-"] for alias in self.aliases]
        self.fill_tree(self.alias_tree, rows, keys=[alias.id for alias in self.aliases])
        self.alias_hint.set_text("共 %d 条别名" % len(rows))


def _fill_alias(page, dialog, field: str, value: str) -> None:
    page.alias_match.set(field)
    page.alias_value.set(value)
    page.editing_id = 0
    dialog.destroy()
