"""定时切换页：时间段 -> 模式（支持跨午夜与按星期生效）。"""

from __future__ import annotations

import tkinter as tk

from ...core.model import ALL_DAYS, DAY_LABELS, Mode, ScheduleSlot
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page

MODE_OPTIONS = [(Mode.COPY, "复制模式"), (Mode.MONITOR, "监控模式"), (Mode.OFF, "关闭")]


class SchedulePage(Page):
    title = "定时切换"

    def build(self) -> None:
        area = self.scroll_area()
        body = area.body

        card = W.Card(body, "新增 / 修改时间段", self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.PAD), 0))

        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x")
        tk.Label(row, text="起始", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.start = W.FlatEntry(row, width=7, font=self.fonts.get("base"))
        self.start.set("22:00")
        self.start.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(row, text="结束", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.end = W.FlatEntry(row, width=7, font=self.fonts.get("base"))
        self.end.set("06:00")
        self.end.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(row, text="模式", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.mode = W.FlatSelect(row, MODE_OPTIONS, Mode.COPY,
                                 font=self.fonts.get("base"), width=10)
        self.mode.pack(side="left", padx=(T.px(6), T.px(10)))
        tk.Label(row, text="备注", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.remark = W.FlatEntry(row, width=18, font=self.fonts.get("base"))
        self.remark.pack(side="left", padx=(T.px(6), 0))

        day_row = tk.Frame(card.body, bg=T.CARD)
        day_row.pack(fill="x", pady=(T.px(8), 0))
        tk.Label(day_row, text="生效日", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.day_checks = []
        for index, label in enumerate(DAY_LABELS):
            check = W.FlatCheck(day_row, label, True, font=self.fonts.get("base"),
                                bg=T.CARD)
            check.pack(side="left", padx=(T.px(8), 0))
            self.day_checks.append(check)
        self.enabled = W.FlatCheck(day_row, "启用", True, font=self.fonts.get("base"),
                                   bg=T.CARD)
        self.enabled.pack(side="left", padx=(T.px(16), 0))

        row2 = tk.Frame(card.body, bg=T.CARD)
        row2.pack(fill="x", pady=(T.px(10), 0))
        W.FlatButton(row2, text="添加计划", kind="primary", command=self.add_slot,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row2, text="保存修改", kind="default", command=self.update_slot,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row2, text="删除选中", kind="danger", command=self.delete_slot,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row2, text="清空全部", kind="danger", command=self.clear_slots,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        self.hint_label = self.hint(row2, "时间格式 HH:MM，跨午夜会自动识别")
        self.hint_label.configure(bg=T.CARD)
        self.hint_label.pack(side="left", padx=(T.px(10), 0))

        card2 = W.Card(body, "计划列表", self.fonts)
        card2.pack(fill="both", expand=True, padx=T.px(T.PAD),
                   pady=(T.px(T.GAP), 0))
        top = tk.Frame(card2.body, bg=T.CARD)
        top.pack(fill="x")
        self.schedule_enable = W.FlatCheck(
            top, "启用定时切换（命中时间段时以计划为准，否则用手动模式）",
            self.ctx.engine.settings.schedule_enable,
            font=self.fonts.get("base"), bg=T.CARD,
            command=self._toggle_schedule)
        self.schedule_enable.pack(side="left")

        holder = self.make_tree(card2.body, [
            ("start", "起始", 80, "center"),
            ("end", "结束", 80, "center"),
            ("mode", "模式", 100, "center"),
            ("days", "生效日", 220, "w"),
            ("state", "状态", 70, "center"),
            ("remark", "备注", 240, "w"),
        ], height=8, stretch_column="remark", on_double=self._on_select,
            on_menu=self._slot_menu)
        holder.pack(fill="both", expand=True, pady=(T.px(8), 0))
        self.tree = holder.tree
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self.fill_tree(self.tree, [], keys=[])

        self.slots = []
        self.editing_id = 0
        self.preview = self.hint(body, "")
        self.preview.configure(bg=T.BG)
        self.preview.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), T.px(T.PAD)))

    # -- 表单 ------------------------------------------------------------

    def _current_days(self) -> int:
        mask = 0
        for index, check in enumerate(self.day_checks):
            if check.value:
                mask |= 1 << index
        return mask or ALL_DAYS

    def _set_days(self, mask: int) -> None:
        for index, check in enumerate(self.day_checks):
            check.set(bool(mask & (1 << index)))

    def _read_form(self) -> ScheduleSlot:
        return ScheduleSlot(
            id=self.editing_id, start=self.start.get().strip(),
            end=self.end.get().strip(), mode=self.mode.value,
            days=self._current_days(), enabled=self.enabled.value,
            remark=self.remark.get().strip(),
        )

    def _validate(self, slot: ScheduleSlot) -> bool:
        """校验并把时间补成 HH:MM 形式（允许输入 8:00 这种写法）。"""
        normalized = []
        for value in (slot.start, slot.end):
            parts = value.split(":")
            if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
                self.hint_label.set_text("时间格式应为 HH:MM，例如 22:00", T.DANGER)
                return False
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                self.hint_label.set_text("时间超出范围（00:00 - 23:59）", T.DANGER)
                return False
            normalized.append("%02d:%02d" % (hour, minute))
        slot.start, slot.end = normalized[0], normalized[1]
        self.start.set(slot.start)
        self.end.set(slot.end)
        return True

    def add_slot(self) -> None:
        slot = self._read_form()
        slot.id = 0
        if not self._validate(slot):
            return
        self.ctx.store.add_schedule(slot)
        self.ctx.log("info", "新增定时计划：%s - %s -> %s"
                     % (slot.start, slot.end, Mode.label(slot.mode)))
        self.hint_label.set_text("已添加计划 %s - %s" % (slot.start, slot.end),
                                 T.SUCCESS)
        self.ctx.engine.reload()
        self.reload()

    def update_slot(self) -> None:
        if not self.editing_id:
            self.hint_label.set_text("请先在列表里选中一条计划。", T.WARN)
            return
        slot = self._read_form()
        if not self._validate(slot):
            return
        self.ctx.store.update_schedule(slot)
        self.hint_label.set_text("已保存修改。", T.SUCCESS)
        self.ctx.engine.reload()
        self.reload()

    def delete_slot(self) -> None:
        keys = set(self.action_keys(self.tree))
        targets = [slot for slot in self.slots if slot.id in keys]
        if not targets:
            index = self.selected_index(self.tree)
            if index >= 0:
                targets = [self.slots[index]]
        if not targets:
            self.hint_label.set_text("请先勾选或选中要删除的计划。", T.WARN)
            return
        for slot in targets:
            self.ctx.store.delete_schedule(slot.id)
        self.editing_id = 0
        self.hint_label.set_text("已删除 %d 条计划。" % len(targets), T.SUCCESS)
        self.ctx.engine.reload()
        self.reload()

    def _slot_menu(self, event=None) -> None:
        from ..popup import show_menu

        if event is not None:
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
        index = self.selected_index(self.tree)
        if index < 0 or index >= len(self.slots):
            self.ctx.notify("请先选一条计划。", "warn")
            return
        slot = self.slots[index]
        show_menu(self, [
            ("载入到上面的表单", self._on_select),
            ("启用 / 停用这条", lambda: self._toggle_slot(slot)),
            ("删除这条计划", self.delete_slot),
            ("清空全部计划", self.clear_slots),
        ], event.x_root if event else self.winfo_pointerx(),
            event.y_root if event else self.winfo_pointery(),
            font=self.fonts.get("base"))

    def _toggle_slot(self, slot) -> None:
        slot.enabled = not slot.enabled
        self.ctx.store.update_schedule(slot)
        self.ctx.engine.reload()
        self.ctx.notify("计划 %s - %s 已%s" % (slot.start, slot.end,
                                               "启用" if slot.enabled else "停用"),
                        "success")
        self.reload()

    def clear_slots(self) -> None:
        if not self.slots:
            return
        if D.message(self, "清空计划", "确定清空全部定时计划吗？", ("清空", "取消"),
                     kind="warn") != 0:
            return
        self.ctx.store.clear_schedule()
        self.editing_id = 0
        self.ctx.engine.reload()
        self.hint_label.set_text("已清空全部计划。", T.SUCCESS)
        self.reload()

    def _toggle_schedule(self, value: bool) -> None:
        settings = self.ctx.engine.settings
        settings.schedule_enable = value
        self.ctx.engine.save_settings(settings)
        self.hint_label.set_text("定时切换已%s" % ("启用" if value else "停用"),
                                 T.SUCCESS)
        self.refresh()

    def _on_select(self) -> None:
        index = self.selected_index(self.tree)
        if index < 0 or index >= len(self.slots):
            return
        slot = self.slots[index]
        self.editing_id = slot.id
        self.start.set(slot.start)
        self.end.set(slot.end)
        self.mode.set(slot.mode)
        self._set_days(slot.days)
        self.enabled.set(slot.enabled)
        self.remark.set(slot.remark)
        self.hint_label.set_text("正在编辑选中的计划，修改后点「保存修改」。")

    # -- 刷新 ------------------------------------------------------------

    def refresh(self) -> None:
        self.schedule_enable.set(self.ctx.engine.settings.schedule_enable)
        self.reload()
        state = self.ctx.engine.state()
        text = "当前生效模式：%s" % Mode.label(state["effective_mode"])
        if state["next_switch"]:
            text += "｜%s" % state["next_switch"]
        self.preview.set_text(text)

    def reload(self) -> None:
        self.slots = self.ctx.store.list_schedule()
        rows = [[slot.start, slot.end, Mode.label(slot.mode), slot.days_text(),
                 "启用" if slot.enabled else "停用", slot.remark or "-"]
                for slot in self.slots]
        self.fill_tree(self.tree, rows, keys=[slot.id for slot in self.slots])
