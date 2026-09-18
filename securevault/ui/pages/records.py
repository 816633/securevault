"""监控记录页：查询、分页、导出 CSV、清除记录、一键加入排除名单。"""

from __future__ import annotations

import time
import tkinter as tk

from ...core import fileutil
from ...core.model import Action, Event
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page

FIELD_OPTIONS = [
    ("", "全部字段"), ("model", "设备型号"), ("volume", "卷标"), ("vid", "USB 厂商 ID"),
    ("pid", "USB 产品 ID"), ("serial", "序列号"), ("letter", "盘符"),
]
EVENT_OPTIONS = [("", "全部事件"), (Event.ARRIVAL, "接入"), (Event.REMOVAL, "移除")]
TIME_OPTIONS = [
    ("", "全部时间"), ("24", "最近 24 小时"), ("168", "最近 7 天"),
    ("720", "最近 30 天"),
]
PAGE_SIZE = 100


class RecordsPage(Page):
    title = "监控记录"

    def build(self) -> None:
        outer = tk.Frame(self, bg=T.BG)
        outer.pack(fill="both", expand=True)

        card = W.Card(outer, "查询条件", self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.PAD), 0))
        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x")
        tk.Label(row, text="关键词", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.keyword = W.FlatEntry(row, width=22, font=self.fonts.get("base"))
        self.keyword.pack(side="left", padx=(T.px(6), T.px(12)))
        self.keyword.bind_return(self.search)
        tk.Label(row, text="字段", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.field = W.FlatSelect(row, FIELD_OPTIONS, "", font=self.fonts.get("base"),
                                  width=12)
        self.field.pack(side="left", padx=(T.px(6), T.px(12)))
        tk.Label(row, text="事件", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.event = W.FlatSelect(row, EVENT_OPTIONS, "", font=self.fonts.get("base"),
                                  width=8)
        self.event.pack(side="left", padx=(T.px(6), T.px(12)))
        tk.Label(row, text="时间", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.since = W.FlatSelect(row, TIME_OPTIONS, "", font=self.fonts.get("base"),
                                  width=12)
        self.since.pack(side="left", padx=(T.px(6), 0))

        row2 = tk.Frame(card.body, bg=T.CARD)
        row2.pack(fill="x", pady=(T.px(8), 0))
        W.FlatButton(row2, text="查询", kind="primary", command=self.search,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row2, text="重置", kind="default", command=self.reset,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        self.result_hint = self.hint(row2, "")
        self.result_hint.configure(bg=T.CARD)
        self.result_hint.pack(side="left", padx=(T.px(12), 0))

        card2 = W.Card(outer, "记录列表（双击查看详情，右键更多操作）", self.fonts)
        card2.pack(fill="both", expand=True, padx=T.px(T.PAD), pady=(T.px(T.GAP), 0))
        columns = [
            ("time", "时间", 145, "center"),
            ("event", "事件", 55, "center"),
            ("action", "处理结果", 75, "center"),
            ("letter", "盘符", 55, "center"),
            ("name", "卷标", 110, "w"),
            ("model", "型号", 160, "w"),
            ("vidpid", "VID:PID", 90, "center"),
            ("serial", "序列号", 150, "w"),
            ("size", "容量", 80, "e"),
            ("files", "文件数", 60, "e"),
            ("note", "备注", 220, "w"),
        ]
        holder = self.make_tree(card2.body, columns, height=8, stretch_column="note")
        holder.pack(fill="both", expand=True)
        self.tree = holder.tree
        self.tree.bind("<Double-1>", lambda _e: self.show_details())
        self.tree.bind("<Button-3>", self._popup)

        card3 = W.Card(outer, "", self.fonts)
        card3.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), T.px(T.PAD)))
        row3 = tk.Frame(card3.body, bg=T.CARD)
        row3.pack(fill="x")
        W.FlatButton(row3, text="查看详情", kind="default", command=self.show_details,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row3, text="加入排除名单", kind="default",
                     command=self.exclude_selected,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row3, text="导出 CSV…", kind="default", command=self.export_csv,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))
        W.FlatButton(row3, text="清除记录…", kind="danger", command=self.clear_records,
                     font=self.fonts.get("base")).pack(side="left", padx=(T.px(8), 0))

        row4 = tk.Frame(card3.body, bg=T.CARD)
        row4.pack(fill="x", pady=(T.px(8), 0))
        W.FlatButton(row4, text="上一页", kind="default", command=self.prev_page,
                     font=self.fonts.get("base"), padx=12).pack(side="left")
        W.FlatButton(row4, text="下一页", kind="default", command=self.next_page,
                     font=self.fonts.get("base"), padx=12).pack(side="left",
                                                                padx=(T.px(6), 0))
        self.page_hint = self.hint(row4, "第 1 页")
        self.page_hint.configure(bg=T.CARD)
        self.page_hint.pack(side="left", padx=(T.px(12), 0))

        self.page_index = 1
        self.total = 0
        self.rows = []

    def _since_value(self) -> str:
        hours = self.since.value
        if not hours:
            return ""
        return time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(time.time() - int(hours) * 3600))

    def search(self, *_args) -> None:
        self.page_index = 1
        self.reload()

    def reset(self) -> None:
        self.keyword.set("")
        self.field.set("")
        self.event.set("")
        self.since.set("")
        self.page_index = 1
        self.reload()

    def reload(self) -> None:
        try:
            rows, total = self.ctx.store.query_records(
                keyword=self.keyword.get(), field=self.field.value,
                event=self.event.value, since=self._since_value(),
                page=self.page_index, page_size=PAGE_SIZE)
        except Exception as exc:
            self.result_hint.set_text("查询失败：%s" % exc, T.DANGER)
            return
        self.rows = rows
        self.total = total
        display = []
        for record in rows:
            dev = record.device
            display.append([
                _format_time(record.time), Event.label(record.event),
                Action.label(record.action), dev.letter or "-", dev.name or "-",
                dev.model or "-", dev.vid_pid or "-",
                dev.usb_serial or dev.disk_serial or "-",
                fileutil.human_size(dev.capacity), record.files,
                (record.note or "").replace("\n", " "),
            ])
        self.fill_tree(self.tree, display, keys=[record.id for record in rows])
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_index = min(self.page_index, pages)
        self.page_hint.set_text("第 %d / %d 页，共 %d 条" % (self.page_index, pages, total))
        self.result_hint.set_text("命中 %d 条" % total)

    def prev_page(self) -> None:
        if self.page_index > 1:
            self.page_index -= 1
            self.reload()
            self.ctx.notify("已切到第 %d 页。" % self.page_index, "info")
        else:
            self.ctx.notify("已经是第一页了。", "warn")

    def next_page(self) -> None:
        pages = max(1, (self.total + PAGE_SIZE - 1) // PAGE_SIZE)
        if self.page_index < pages:
            self.page_index += 1
            self.reload()
            self.ctx.notify("已切到第 %d 页。" % self.page_index, "info")
        else:
            self.ctx.notify("已经是最后一页了。", "warn")

    def refresh(self) -> None:
        self.reload()

    def _selected(self):
        index = self.selected_index(self.tree)
        if index < 0 or index >= len(self.rows):
            return None
        return self.rows[index]

    def _checked_records(self):
        """勾选（或鼠标选中）的行对应的记录。"""
        keys = set(self.action_keys(self.tree))
        if not keys:
            return []
        return [record for record in self.rows if record.id in keys]

    def show_details(self) -> None:
        record = self._selected()
        if record is None:
            self.result_hint.set_text("请先在列表里选中一行。", T.WARN)
            return
        dev = record.device
        rows = [
            ("记录时间", _format_time(record.time)),
            ("事件", Event.label(record.event)),
            ("处理结果", Action.label(record.action)),
            ("备注", record.note),
            ("盘符", dev.letter),
            ("卷标", dev.name),
            ("总线类型", dev.bus_type),
            ("设备型号", dev.model),
            ("厂商", dev.vendor),
            ("VID", dev.vid),
            ("PID", dev.pid),
            ("USB 序列号", dev.usb_serial),
            ("磁盘序列号", dev.disk_serial),
            ("文件系统", dev.fs),
            ("总容量", fileutil.human_size(dev.capacity)),
            ("剩余容量", fileutil.human_size(dev.free)),
            ("物理磁盘号", str(dev.physical_drive)),
            ("设备实例 ID", dev.device_instance),
            ("卷 GUID", dev.volume_guid),
            ("设备路径", dev.device_path),
            ("是否可移动", "是" if dev.is_removable else "否"),
            ("复制文件数", str(record.files)),
            ("复制字节数", fileutil.human_size(record.bytes_copied)),
            ("耗时", "%d 毫秒" % record.elapsed),
            ("目标目录", record.dest),
        ]
        D.show_details(self, "记录详情", rows)

    def exclude_selected(self) -> None:
        records = self._checked_records() or ([self._selected()] if self._selected() else [])
        if not records:
            self.ctx.notify("请先勾选或用鼠标选中要排除的记录。", "warn")
            return
        names = "、".join(sorted({r.device.display_name for r in records}))
        answer = D.message(
            self, "加入排除名单",
            "把 %d 个设备加入排除名单？" % len(records) if len(records) > 1
            else "把设备「%s」加入排除名单？" % names,
            ("加入名单", "取消"), kind="question",
            detail="加入后，这些设备的插拔只记录、不复制。\n%s" % names,
        )
        if answer != 0:
            return
        errors = []
        for record in records:
            error = self.ctx.engine.exclude_device(record.device)
            if error:
                errors.append(error)
        if errors:
            self.ctx.notify("、".join(errors), "error")
        else:
            self.ctx.notify("已把 %d 个设备加入排除名单。" % len(records), "success")

    def export_csv(self) -> None:
        from tkinter import filedialog

        name = "SecureVault-监控记录-%s.csv" % time.strftime("%Y%m%d-%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self, title="导出监控记录", defaultextension=".csv",
            initialfile=name, filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            count = self.ctx.store.export_records_csv(path, since=self._since_value())
        except Exception as exc:
            D.message(self, "导出失败", str(exc), ("确定",), kind="error")
            return
        self.result_hint.set_text("已导出 %d 条" % count, T.SUCCESS)
        D.message(self, "导出完成", "已导出 %d 条记录。" % count, ("确定",), detail=path)

    def clear_records(self, scope: str = "") -> None:
        """清除记录。``scope`` 为空时弹窗让用户选范围（测试可直接传 "all" / "7"）。"""
        options = [("7", "清除 7 天前的记录"), ("14", "清除 14 天前的记录"),
                   ("30", "清除 30 天前的记录"), ("all", "清除全部记录")]
        holder = {"value": ""}
        if scope:
            holder["value"] = scope
        else:
            dialog = D.BaseDialog(self, "清除监控记录", width=430)
            tk.Label(dialog.body, text="选择要清除的范围（不可撤销）：", bg=T.BG,
                     fg=T.TEXT, font=self.fonts.get("base"), anchor="w").pack(fill="x")
            for key, label in options:
                button = W.FlatButton(dialog.body, text=label, kind="danger",
                                      font=self.fonts.get("base"), anchor="w", padx=10)
                button.pack(fill="x", pady=(T.px(6), 0))
                button.set_command(lambda k=key: _choose(dialog, holder, k))
            W.FlatButton(dialog.body, text="取消", kind="default",
                         command=dialog.destroy,
                         font=self.fonts.get("base")).pack(anchor="e",
                                                           pady=(T.px(12), 0))
            dialog.finish()
        if not holder["value"]:
            return
        if holder["value"] == "all":
            since, text = "", "确定清除全部监控记录吗？此操作不可撤销。"
        else:
            days = int(holder["value"])
            since = time.strftime("%Y-%m-%dT%H:%M:%S",
                                  time.localtime(time.time() - days * 86400))
            text = "确定清除 %d 天前的监控记录吗？此操作不可撤销。" % days
        if D.message(self, "再次确认", text, ("清除", "取消"), kind="warn") != 0:
            return
        if not self.ctx.app.verify_password("清除监控记录前请输入密码："):
            return
        removed = self.ctx.store.delete_records_before(since)
        self.ctx.log("info", "已清除 %d 条监控记录" % removed)
        self.result_hint.set_text("已清除 %d 条记录。" % removed, T.SUCCESS)
        self.reload()

    def _popup(self, event) -> None:
        from ..popup import show_menu

        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        show_menu(self, [
            ("加入排除名单（不再复制此设备）", self.exclude_selected),
            ("查看详情", self.show_details),
            ("复制序列号", self.copy_serial),
            ("复制整行", self.copy_row),
        ], event.x_root, event.y_root, font=self.fonts.get("base"))

    def copy_serial(self) -> None:
        record = self._selected()
        if record is None:
            return
        self.clipboard_clear()
        self.clipboard_append(record.device.usb_serial or record.device.disk_serial)
        self.result_hint.set_text("已复制序列号", T.SUCCESS)

    def copy_row(self) -> None:
        record = self._selected()
        if record is None:
            return
        dev = record.device
        text = "\t".join([
            _format_time(record.time), Event.label(record.event),
            Action.label(record.action), dev.letter, dev.name, dev.model,
            dev.vid_pid, dev.usb_serial or dev.disk_serial, record.note,
        ])
        self.clipboard_clear()
        self.clipboard_append(text)
        self.result_hint.set_text("已复制整行", T.SUCCESS)


def _choose(dialog, holder, value: str) -> None:
    holder["value"] = value
    dialog.destroy()


def _format_time(value: str) -> str:
    text = (value or "").replace("T", " ")
    return text[:19] if text else "-"
