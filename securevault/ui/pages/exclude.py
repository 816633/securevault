"""排除名单页：设备排除规则 + 文件 / 扩展名名单。"""

from __future__ import annotations

import tkinter as tk

from ...core.model import (LIST_KIND_LABELS, LIST_KINDS, RULE_TYPE_LABELS, RULE_TYPES,
                           ExcludeRule, NameList, now_rfc3339)
from ...system import devices
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page

#: 扩展名名单里可以一键添加的常用项
COMMON_EXTS = [".tmp", ".log", ".bak", ".dll", ".sys", ".exe",
               ".mp4", ".iso", ".zip", ".rar"]


class ExcludePage(Page):
    title = "排除名单"

    def build(self) -> None:
        area = self.scroll_area()
        body = area.body

        # --- 设备规则 ---------------------------------------------------
        card = W.Card(body, "设备排除规则（规则之间是「或」，命中任意一条即只记录、不复制）",
                      self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.PAD), 0))

        form = tk.Frame(card.body, bg=T.CARD)
        form.pack(fill="x")
        tk.Label(form, text="属性", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.rule_type = W.FlatSelect(form, RULE_TYPES, RULE_TYPES[0][0],
                                      font=self.fonts.get("base"), width=16)
        self.rule_type.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(form, text="匹配值", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.rule_value = W.FlatEntry(form, width=20, font=self.fonts.get("base"))
        self.rule_value.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(form, text="备注", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.rule_remark = W.FlatEntry(form, width=16, font=self.fonts.get("base"))
        self.rule_remark.pack(side="left", padx=(T.px(6), T.px(10)))
        W.FlatButton(form, text="添加规则", kind="primary", command=self.add_rule,
                     font=self.fonts.get("base")).pack(side="left")

        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x", pady=(T.px(8), 0))
        W.FlatButton(row, text="从本机磁盘快速添加…", kind="default",
                     command=self.quick_add, font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row, text="删除选中", kind="danger", command=self.delete_rule,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row, text="清空规则", kind="danger", command=self.clear_rules,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        self.builtin_button = W.FlatButton(
            row, text="内置硬盘规则…", kind="default",
            command=self.toggle_builtin_rule, font=self.fonts.get("base"))
        self.builtin_button.pack(side="left", padx=(T.px(8), 0))
        self.rule_hint = self.hint(row, "容量支持 512MB / >=64GB / <=2GB 等写法")
        self.rule_hint.configure(bg=T.CARD)
        self.rule_hint.pack(side="left", padx=(T.px(10), 0))

        holder = self.make_tree(card.body, [
            ("type", "属性", 130, "w"),
            ("value", "匹配值", 260, "w"),
            ("remark", "备注", 260, "w"),
            ("created", "添加时间", 150, "center"),
        ], height=6, stretch_column="remark", on_double=self.copy_rule_value,
            on_menu=self._rule_menu)
        holder.pack(fill="both", expand=True, pady=(T.px(8), 0))
        self.rule_tree = holder.tree

        # --- 文件名单 ---------------------------------------------------
        card2 = W.Card(body, "排除 U 盘中的文件 / 文件夹名、扩展名", self.fonts)
        card2.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), T.px(T.PAD)))
        form2 = tk.Frame(card2.body, bg=T.CARD)
        form2.pack(fill="x")
        tk.Label(form2, text="名单类型", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.list_kind = W.FlatSelect(form2, LIST_KINDS, LIST_KINDS[0][0],
                                      font=self.fonts.get("base"), width=22,
                                      command=lambda _v: self.reload_lists())
        self.list_kind.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(form2, text="值", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.list_value = W.FlatEntry(form2, width=20, font=self.fonts.get("base"))
        self.list_value.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(form2, text="备注", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.list_remark = W.FlatEntry(form2, width=16, font=self.fonts.get("base"))
        self.list_remark.pack(side="left", padx=(T.px(6), T.px(10)))
        W.FlatButton(form2, text="添加", kind="primary", command=self.add_list,
                     font=self.fonts.get("base")).pack(side="left")

        row2 = tk.Frame(card2.body, bg=T.CARD)
        row2.pack(fill="x", pady=(T.px(8), 0))
        W.FlatButton(row2, text="删除选中", kind="danger", command=self.delete_list,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row2, text="清空该类", kind="danger", command=self.clear_lists,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        self.list_hint = self.hint(row2, "名称支持通配符 * 与 ?；扩展名带不带点都行")
        self.list_hint.configure(bg=T.CARD)
        self.list_hint.pack(side="left", padx=(T.px(10), 0))

        holder2 = self.make_tree(card2.body, [
            ("kind", "类型", 160, "w"),
            ("value", "值", 260, "w"),
            ("remark", "备注", 240, "w"),
            ("created", "添加时间", 150, "center"),
        ], height=6, stretch_column="remark", on_double=self.copy_list_value,
            on_menu=self._list_menu)
        holder2.pack(fill="both", expand=True, pady=(T.px(8), 0))
        self.list_tree = holder2.tree

        # 常用扩展名快捷添加（只在「扩展名」类名单下显示）
        self.quick_ext_row = tk.Frame(card2.body, bg=T.CARD)
        tk.Label(self.quick_ext_row, text="常用扩展名快捷添加：", bg=T.CARD,
                 fg=T.TEXT_DIM, font=self.fonts.get("small")).pack(side="left")
        for ext in COMMON_EXTS:
            button = W.FlatButton(self.quick_ext_row, text=ext, kind="ghost",
                                  font=self.fonts.get("small"), padx=8, pady=2)
            button.pack(side="left", padx=(T.px(4), 0))
            button.set_command(lambda e=ext: self._quick_add_ext(e))

        self.rules = []
        self.lists = []

    # -- 设备规则 --------------------------------------------------------

    def add_rule(self) -> None:
        value = self.rule_value.get().strip()
        if not value:
            self.rule_hint.set_text("请填写匹配值。", T.WARN)
            return
        self.ctx.store.add_exclude_rule(ExcludeRule(
            type=self.rule_type.value, value=value,
            remark=self.rule_remark.get().strip(), created_at=now_rfc3339()))
        self.ctx.log("info", "新增设备排除规则：%s = %s"
                     % (RULE_TYPE_LABELS.get(self.rule_type.value, self.rule_type.value),
                        value))
        self.rule_value.set("")
        self.rule_remark.set("")
        self.rule_hint.set_text("已添加规则：%s = %s" % (self.rule_type.value, value),
                                T.SUCCESS)
        self.ctx.engine.reload()
        self.reload()

    def delete_rule(self) -> None:
        keys = set(self.action_keys(self.rule_tree))
        targets = [rule for rule in self.rules if rule.id in keys]
        if not targets:
            index = self.selected_index(self.rule_tree)
            if index >= 0:
                targets = [self.rules[index]]
        if not targets:
            self.rule_hint.set_text("请先勾选或选中要删除的规则。", T.WARN)
            return
        if any(rule.id == 0 for rule in targets):
            self.ctx.notify("「内置硬盘」是系统内置规则，不能删除；"
                            "如果需要修改，请点「内置硬盘规则…」。", "warn")
            targets = [rule for rule in targets if rule.id != 0]
            if not targets:
                return
        if D.message(self, "删除规则",
                     "确定删除选中的 %d 条排除规则吗？" % len(targets),
                     ("删除", "取消"), kind="warn") != 0:
            return
        for rule in targets:
            self.ctx.store.delete_exclude_rule(rule.id)
        self.rule_hint.set_text("已删除 %d 条规则。" % len(targets), T.SUCCESS)
        self.ctx.engine.reload()
        self.reload()

    def clear_rules(self) -> None:
        if not self.rules:
            return
        if D.message(self, "清空规则", "确定清空全部设备排除规则吗？",
                     ("清空", "取消"), kind="warn") != 0:
            return
        self.ctx.store.clear_exclude_rules()
        self.ctx.engine.reload()
        self.rule_hint.set_text("已清空全部规则。", T.SUCCESS)
        self.reload()

    def quick_add(self) -> None:
        self.rule_hint.set_text("正在读取本机磁盘…", T.TEXT_DIM)

        def work() -> None:
            try:
                snapshot = devices.snapshot()
            except Exception:
                snapshot = {}
            self.ctx.ui(self._show_quick_add, snapshot)

        self.ctx.run_bg(work)

    def _show_quick_add(self, snapshot) -> None:
        dialog = D.BaseDialog(self, "从本机磁盘快速添加", width=560)
        tk.Label(dialog.body, text="选中任意一项即可加入排除名单：", bg=T.BG,
                 fg=T.TEXT, font=self.fonts.get("base"), anchor="w").pack(fill="x")
        holder = {"done": False}
        area = W.ScrollArea(dialog.body)
        area.pack(fill="both", expand=True, pady=(T.px(8), 0))
        for letter in sorted(snapshot):
            info = snapshot[letter]
            block = tk.Frame(area.body, bg=T.BG)
            block.pack(fill="x", pady=(T.px(6), T.px(2)))
            tk.Label(block, text="%s  %s  %s" % (letter, info.name or "-",
                                                 info.model or "-"),
                     bg=T.BG, fg=T.TEXT, font=self.fonts.get("base"),
                     anchor="w").pack(fill="x")
            options = [("diskSerial", "磁盘序列号", info.disk_serial),
                       ("usbSerial", "USB 序列号", info.usb_serial),
                       ("vid", "USB 厂商 ID", info.vid),
                       ("pid", "USB 产品 ID", info.pid),
                       ("volume", "卷标", info.name),
                       ("capacity", "容量", "%d" % info.capacity)]
            grid = tk.Frame(block, bg=T.BG)
            grid.pack(fill="x", pady=(T.px(2), 0))
            # 两个一排，自动换行，不会挤成一团
            grid.grid_columnconfigure(0, weight=1, uniform="quick")
            grid.grid_columnconfigure(1, weight=1, uniform="quick")
            column = 0
            for rule_type, label, value in options:
                if not value:
                    continue
                text = "按%s排除（%s）" % (label, str(value)[:28])
                button = W.FlatButton(grid, text=text, kind="ghost",
                                      font=self.fonts.get("small"), padx=8, pady=4,
                                      anchor="w")
                button.grid(row=column // 2, column=column % 2, sticky="ew",
                            padx=(0, T.px(6)), pady=T.px(2))
                button.set_command(
                    lambda t=rule_type, v=value, l=label:
                    _quick_pick(self, dialog, holder, t, v, l))
                column += 1
        W.FlatButton(dialog.body, text="关闭", kind="default",
                     command=dialog.destroy, font=self.fonts.get("base")).pack(
            anchor="e", pady=(T.px(10), 0))
        dialog.finish()
        if holder["done"]:
            self.ctx.engine.reload()
            self.reload()

    # -- 文件名单 --------------------------------------------------------

    def add_list(self) -> None:
        value = self.list_value.get().strip()
        if not value:
            self.list_hint.set_text("请填写要添加的值。", T.WARN)
            return
        kind = self.list_kind.value
        self.ctx.store.add_list(kind, value, self.list_remark.get().strip())
        self.ctx.log("info", "新增名单：%s = %s" % (kind, value))
        self.list_value.set("")
        self.list_remark.set("")
        self.list_hint.set_text("已添加：%s" % value, T.SUCCESS)
        self.ctx.engine.reload()
        self.reload_lists()

    def delete_list(self) -> None:
        keys = set(self.action_keys(self.list_tree))
        targets = [item for item in self.lists if item.id in keys]
        if not targets:
            index = self.selected_index(self.list_tree)
            if index >= 0:
                targets = [self.lists[index]]
        if not targets:
            self.list_hint.set_text("请先勾选或选中要删除的条目。", T.WARN)
            return
        if D.message(self, "删除名单",
                     "确定删除选中的 %d 条名单吗？" % len(targets),
                     ("删除", "取消"), kind="warn") != 0:
            return
        for item in targets:
            self.ctx.store.delete_list(item.id)
        self.list_hint.set_text("已删除 %d 条。" % len(targets), T.SUCCESS)
        self.ctx.engine.reload()
        self.reload_lists()

    # -- 右键菜单与快捷添加 ----------------------------------------------

    def _rule_menu(self, event=None) -> None:
        from ..popup import show_menu

        if event is not None:
            row = self.rule_tree.identify_row(event.y)
            if row:
                self.rule_tree.selection_set(row)
        index = self.selected_index(self.rule_tree)
        if index < 0 or index >= len(self.rules):
            self.ctx.notify("请先选一条规则。", "warn")
            return
        rule = self.rules[index]
        if rule.id == 0:
            enabled = getattr(self.ctx.engine.settings, "exclude_internal_disks", True)
            show_menu(self, [
                ("复制说明", lambda: self._copy(rule.value)),
                ("解锁内置硬盘规则（需密码）" if enabled else "恢复默认（需密码）",
                 self.toggle_builtin_rule),
            ], event.x_root if event else self.winfo_pointerx(),
                event.y_root if event else self.winfo_pointery(),
                font=self.fonts.get("base"))
            return
        show_menu(self, [
            ("复制匹配值", lambda: self._copy(rule.value)),
            ("删除这条规则", self.delete_rule),
            ("清空全部规则", self.clear_rules),
        ], event.x_root if event else self.winfo_pointerx(),
            event.y_root if event else self.winfo_pointery(),
            font=self.fonts.get("base"))

    def _list_menu(self, event=None) -> None:
        from ..popup import show_menu

        if event is not None:
            row = self.list_tree.identify_row(event.y)
            if row:
                self.list_tree.selection_set(row)
        index = self.selected_index(self.list_tree)
        if index < 0 or index >= len(self.lists):
            self.ctx.notify("请先选一条名单。", "warn")
            return
        item = self.lists[index]
        show_menu(self, [
            ("复制值", lambda: self._copy(item.value)),
            ("删除这一条", self.delete_list),
            ("清空该类名单", self.clear_lists),
        ], event.x_root if event else self.winfo_pointerx(),
            event.y_root if event else self.winfo_pointery(),
            font=self.fonts.get("base"))

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text or "")
        self.ctx.notify("已复制：%s" % (text or ""), "success")

    def _quick_add_ext(self, ext: str) -> None:
        kind = self.list_kind.value
        existing = {item.value.lower() for item in self.ctx.store.list_lists(kind)}
        if ext.lower() in existing:
            self.ctx.notify("%s 已经在名单里了。" % ext, "warn")
            return
        self.ctx.store.add_list(kind, ext, "快捷添加")
        self.ctx.engine.reload()
        self.ctx.notify("已添加 %s" % ext, "success")
        self.reload_lists()

    # -- 非破坏性的双击动作 ----------------------------------------------

    def copy_rule_value(self) -> None:
        index = self.selected_index(self.rule_tree)
        if index < 0 or index >= len(self.rules):
            return
        self._copy(self.rules[index].value)

    def copy_list_value(self) -> None:
        index = self.selected_index(self.list_tree)
        if index < 0 or index >= len(self.lists):
            return
        self._copy(self.lists[index].value)

    # -- 内置硬盘规则（默认排除，不能删除） --------------------------------

    def toggle_builtin_rule(self) -> None:
        settings = self.ctx.engine.settings
        enabled = getattr(settings, "exclude_internal_disks", True)
        if enabled:
            answer = D.message(
                self, "解锁内置硬盘规则",
                "解锁后，本机的内置硬盘（C:、D: 等固定磁盘）也会被当成可复制设备。\n"
                "这可能导致把整块硬盘的内容复制到目标目录，**可能会报错**，"
                "也**不建议解锁**。\n\n确实要解锁吗？",
                ("解锁", "取消"), kind="warn")
            if answer != 0:
                return
            if not self.ctx.app.verify_password("解锁内置硬盘规则前请输入密码："):
                return
            settings.exclude_internal_disks = False
            if self.ctx.engine.save_settings(settings):
                self.ctx.notify("已解锁：内置硬盘不再被排除，请谨慎使用。", "warn")
                self.ctx.log("warn", "用户解锁了内置硬盘规则")
        else:
            if not self.ctx.app.verify_password("恢复默认内置硬盘规则前请输入密码："):
                return
            settings.exclude_internal_disks = True
            if self.ctx.engine.save_settings(settings):
                self.ctx.notify("已恢复默认：排除内置硬盘。", "success")
                self.ctx.log("info", "用户恢复了内置硬盘排除规则")
        self.reload_rules()

    def clear_lists(self) -> None:
        kind = self.list_kind.value
        if D.message(self, "清空名单", "确定清空「%s」的全部条目吗？"
                     % LIST_KIND_LABELS.get(kind, kind), ("清空", "取消"),
                     kind="warn") != 0:
            return
        self.ctx.store.clear_lists(kind)
        self.ctx.engine.reload()
        self.list_hint.set_text("已清空。", T.SUCCESS)
        self.reload_lists()

    # -- 刷新 ------------------------------------------------------------

    def refresh(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.reload_rules()
        self.reload_lists()

    def reload_rules(self) -> None:
        settings = self.ctx.engine.settings
        enabled = getattr(settings, "exclude_internal_disks", True)
        builtin = ExcludeRule(
            id=0, type="builtin", value="内置硬盘（本机固定磁盘）",
            remark=("系统内置规则：默认排除内置硬盘，不能删除"
                    if enabled else
                    "系统内置规则：已解锁，内置硬盘不会被排除（不建议）"),
            created_at="")
        self.rules = [builtin] + self.ctx.store.list_exclude_rules()
        rows = []
        for rule in self.rules:
            if rule.id == 0:
                rows.append(["系统内置", rule.value, rule.remark,
                             "默认启用" if enabled else "已解锁"])
                continue
            rows.append([RULE_TYPE_LABELS.get(rule.type, rule.type), rule.value,
                         rule.remark or "-",
                         (rule.created_at or "").replace("T", " ")[:19]])
        self.fill_tree(self.rule_tree, rows, keys=[rule.id for rule in self.rules])
        self.builtin_button.set_text("恢复默认（排除内置硬盘）" if not enabled
                                     else "解锁内置硬盘规则…")
        self.rule_hint.set_text("共 %d 条规则（含 1 条系统内置）" % len(rows))

    def reload_lists(self) -> None:
        self.lists = self.ctx.store.list_lists()
        rows = [[LIST_KIND_LABELS.get(item.kind, item.kind), item.value,
                 item.remark or "-", (item.created_at or "").replace("T", " ")[:19]]
                for item in self.lists]
        self.fill_tree(self.list_tree, rows, keys=[item.id for item in self.lists])
        self.list_hint.set_text("共 %d 条名单条目" % len(rows))
        # 扩展名类名单才显示"常用扩展名快捷添加"
        if self.list_kind.value in ("excludeExt", "includeExt"):
            if not self.quick_ext_row.winfo_ismapped():
                self.quick_ext_row.pack(fill="x", pady=(T.px(6), 0),
                                        before=self.list_tree.master)
        else:
            self.quick_ext_row.pack_forget()


def _quick_pick(page, dialog, holder, rule_type: str, value: str, label: str) -> None:
    page.ctx.store.add_exclude_rule(ExcludeRule(
        type=rule_type, value=value, remark="来自本机磁盘快速添加（%s）" % label,
        created_at=now_rfc3339()))
    holder["done"] = True
    page.ctx.log("info", "快速添加排除规则：%s = %s" % (rule_type, value))
    dialog.destroy()
