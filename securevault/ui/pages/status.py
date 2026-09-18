"""状态页：模式切换、运行状态、当前设备列表。"""

from __future__ import annotations

import tkinter as tk

from ...core import fileutil
from ...core.model import Mode
from ...system import devices
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page


class StatusPage(Page):
    title = "状态"

    def build(self) -> None:
        area = self.scroll_area()
        body = area.body

        card = W.Card(body, "运行模式", self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.PAD), 0))
        self.mode_buttons = {}
        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x")
        for mode in (Mode.COPY, Mode.MONITOR, Mode.OFF):
            button = W.FlatButton(
                row, text=Mode.label(mode), kind="default",
                command=lambda m=mode: self._select_mode(m),
                font=self.fonts.get("base"), padx=18, pady=7,
            )
            button.pack(side="left", padx=(0, T.px(8)))
            self.mode_buttons[mode] = button
        self.mode_hint = self.hint(card.body, "选择模式后点下面的「应用模式」生效。")
        self.mode_hint.pack(fill="x", pady=(T.px(8), 0))
        # 「应用模式」单独换行 + 更醒目的样式，不和模式按钮长一样
        apply_row = tk.Frame(card.body, bg=T.CARD)
        apply_row.pack(fill="x", pady=(T.px(8), 0))
        self.apply_button = W.FlatButton(
            apply_row, text="✔  应用模式", kind="primary",
            command=self._apply_mode, font=self.fonts.get("subtitle"),
            padx=22, pady=8)
        self.apply_button.pack(side="left")
        self.applied_label = tk.Label(apply_row, text="", bg=T.CARD, fg=T.TEXT_DIM,
                                      font=self.fonts.get("small"), anchor="w")
        self.applied_label.pack(side="left", padx=(T.px(12), 0))

        card2 = W.Card(body, "运行状态", self.fonts)
        card2.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), 0))
        self.status_rows = {}
        for key, label in (("effective", "生效模式"), ("manual", "手动模式"),
                           ("dest", "复制目标"), ("schedule", "定时计划"),
                           ("next", "下次切换"), ("event", "最近事件"),
                           ("last_copy", "上次复制"), ("counts", "规则统计")):
            row, holder = W.labeled_row(card2.body, label, self.fonts.get("base"),
                                        bg=T.CARD, label_width=10)
            row.pack(fill="x", pady=T.px(2))
            value = tk.Label(holder, text="-", bg=T.CARD, fg=T.TEXT,
                             font=self.fonts.get("base"), anchor="w", justify="left",
                             wraplength=T.px(620))
            value.pack(fill="x")
            self.status_rows[key] = value

        card3 = W.Card(body, "当前设备", self.fonts)
        card3.pack(fill="both", expand=True, padx=T.px(T.PAD),
                   pady=(T.px(T.GAP), T.px(T.PAD)))
        head = tk.Frame(card3.body, bg=T.CARD)
        head.pack(fill="x")
        W.FlatButton(head, text="刷新设备列表", kind="default",
                     command=self.refresh_devices,
                     font=self.fonts.get("base")).pack(side="left")
        self.device_hint = self.hint(head, "列出本机所有硬盘与 U 盘")
        self.device_hint.configure(bg=T.CARD)
        self.device_hint.pack(side="left", padx=(T.px(10), 0))

        columns = [
            ("letter", "盘符", 60, "center"),
            ("name", "卷标", 130, "w"),
            ("capacity", "总容量", 90, "e"),
            ("free", "剩余", 90, "e"),
            ("fs", "文件系统", 80, "center"),
            ("bus", "总线", 70, "center"),
            ("model", "型号", 200, "w"),
            ("vidpid", "VID:PID", 100, "center"),
            ("serial", "序列号", 190, "w"),
        ]
        holder = self.make_tree(card3.body, columns, height=6, stretch_column="model",
                                checkable=False, on_double=self.show_device_details,
                                on_menu=self._device_menu)
        holder.pack(fill="both", expand=True, pady=(T.px(8), 0))
        self.device_tree = holder.tree
        self._devices = []
        self._pending_mode = None
        self._loading = False

    def _select_mode(self, mode: str) -> None:
        self._pending_mode = mode
        self._paint_modes()
        self.mode_hint.set_text("已选择「%s」，点「应用模式」立即生效。" % Mode.label(mode),
                                T.TEXT_DIM)

    def _paint_modes(self) -> None:
        current = self._pending_mode or self.ctx.engine.manual_mode
        for mode, button in self.mode_buttons.items():
            button._colors = W._BUTTON_KINDS["primary" if mode == current else "default"]
            button._apply()

    def _apply_mode(self) -> None:
        mode = self._pending_mode
        if mode is None:
            self.mode_hint.set_text("请先选择一种模式。", T.WARN)
            return
        self.ctx.engine.set_manual_mode(mode)
        self._pending_mode = None
        self.mode_hint.set_text("已切换到「%s」。" % Mode.label(mode), T.SUCCESS)
        self.refresh()

    def refresh(self) -> None:
        state = self.ctx.engine.state()
        self._paint_modes()
        self.applied_label.configure(
            text="当前已应用：%s" % Mode.label(state["manual_mode"]))
        self.status_rows["effective"].configure(
            text="%s%s" % (Mode.label(state["effective_mode"]),
                           "（正在复制…）" if state["copying"] else ""))
        self.status_rows["manual"].configure(text=Mode.label(state["manual_mode"]))
        self.status_rows["dest"].configure(
            text=state["copy_dest"] or "（未设置，复制模式不会生效）")
        self.status_rows["schedule"].configure(
            text="已启用" if state["schedule_on"] else "已停用")
        self.status_rows["next"].configure(text=state["next_switch"] or "-")
        self.status_rows["event"].configure(text=state["last_event"] or "-")
        stats = state["last_stats"]
        if state["last_copy_at"]:
            self.status_rows["last_copy"].configure(
                text="%s｜复制 %d 个（跳过 %d，失败 %d），共 %s，耗时 %.1f 秒"
                % (state["last_copy_at"], stats.copied, stats.skipped, stats.failed,
                   fileutil.human_size(stats.bytes_copied), stats.elapsed_ms / 1000.0))
        else:
            self.status_rows["last_copy"].configure(text="尚未执行复制")
        try:
            records = self.ctx.store.count_records()
        except Exception:
            records = 0
        self.status_rows["counts"].configure(
            text="设备排除规则 %d 条｜别名 %d 条｜文件名单 %d 条｜监控记录 %d 条"
            % (state["rules"], state["aliases"], state["names"], records))

    # -- 当前设备 --------------------------------------------------------

    def _selected_device(self):
        index = self.selected_index(self.device_tree)
        if index < 0 or index >= len(self._devices):
            self.ctx.notify("请先在「当前设备」里选中一行。", "warn")
            return None
        return self._devices[index]

    def show_device_details(self) -> None:
        info = self._selected_device()
        if info is None:
            return
        rows = [
            ("盘符", info.letter), ("卷标", info.name), ("总线类型", info.bus_type),
            ("设备型号", info.model), ("厂商", info.vendor), ("VID", info.vid),
            ("PID", info.pid), ("USB 序列号", info.usb_serial),
            ("磁盘序列号", info.disk_serial), ("文件系统", info.fs),
            ("总容量", fileutil.human_size(info.capacity)),
            ("剩余容量", fileutil.human_size(info.free)),
            ("物理磁盘号", str(info.physical_drive)),
            ("设备实例 ID", info.device_instance), ("卷 GUID", info.volume_guid),
            ("设备路径", info.device_path),
            ("是否可移动", "是" if info.is_removable else "否"),
            ("采集时间", info.system_time),
        ]
        D.show_details(self, "设备参数 - %s" % (info.display_name or info.letter), rows)

    def _device_menu(self, event=None) -> None:
        from ..popup import show_menu

        if event is not None:
            row = self.device_tree.identify_row(event.y)
            if row:
                self.device_tree.selection_set(row)
        index = self.selected_index(self.device_tree)
        if index < 0 or index >= len(self._devices):
            self.ctx.notify("请先在「当前设备」里选中一行。", "warn")
            return
        x = event.x_root if event is not None else self.winfo_pointerx()
        y = event.y_root if event is not None else self.winfo_pointery()
        show_menu(self, [
            ("查看参数", self.show_device_details),
            ("复制序列号", self._copy_serial),
            ("复制型号", self._copy_model),
            ("加入排除名单（不再复制）", self._exclude_device),
            ("在资源管理器里打开", self._open_letter),
        ], x, y, font=self.fonts.get("base"))

    def _copy_serial(self) -> None:
        info = self._selected_device()
        if info is None:
            return
        value = info.usb_serial or info.disk_serial
        self.clipboard_clear()
        self.clipboard_append(value)
        self.ctx.notify("已复制序列号：%s" % (value or "（空）"), "success")

    def _copy_model(self) -> None:
        info = self._selected_device()
        if info is None:
            return
        self.clipboard_clear()
        self.clipboard_append(info.model or info.display_name)
        self.ctx.notify("已复制型号。", "success")

    def _exclude_device(self) -> None:
        info = self._selected_device()
        if info is None:
            return
        error = self.ctx.engine.exclude_device(info)
        self.ctx.notify(error or "已把 %s 加入排除名单。" % info.display_name,
                        "error" if error else "success")

    def _open_letter(self) -> None:
        info = self._selected_device()
        if info is None:
            return
        self.ctx.app.open_path("%s\\" % info.letter)

    def on_show(self) -> None:
        # 只有"重新进入本页"时才把未生效的临时选择清掉；
        # 页面每秒的自动刷新不能动它，否则刚点的模式会被弹回去。
        self._pending_mode = None
        self.refresh()
        self.refresh_devices()

    def refresh_devices(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.device_hint.set_text("正在读取设备信息…", T.TEXT_DIM)

        def work() -> None:
            try:
                snapshot = devices.snapshot()
            except Exception:
                snapshot = {}
            self.ctx.ui(self._show_devices, snapshot)

        self.ctx.run_bg(work)

    def _show_devices(self, snapshot) -> None:
        self._loading = False
        self._devices = [snapshot[key] for key in sorted(snapshot)]
        rows = []
        for info in self._devices:
            rows.append([
                info.letter, info.name or "-", fileutil.human_size(info.capacity),
                fileutil.human_size(info.free), info.fs or "-", info.bus_type or "-",
                info.model or "-", info.vid_pid or "-",
                info.usb_serial or info.disk_serial or "-",
            ])
        self.fill_tree(self.device_tree, rows)
        self.device_hint.set_text("共 %d 个卷" % len(rows), T.TEXT_DIM)
