"""业务编排层：把「模式（手动 / 定时）→ 设备事件 → 排除判定 → 复制」串起来。

不依赖任何界面代码；界面通过回调订阅状态变化。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..core import paths
from ..core import fileutil
from ..core.model import (Action, Alias, DeviceInfo, Event, ExcludeRule, FileFilter,
                          Mode, NameList, Record, ScheduleSlot, Settings, CopyStats,
                          now_rfc3339)
from ..system import devices
from .copier import CopyEngine, CopyResult, resolve_dest_dir


class Engine:
    """运行时状态机。线程安全（内部只读快照 + 事件触发）。"""

    def __init__(self, store, logger, dirs, on_record=None, on_log=None,
                 on_status=None, on_busy=None) -> None:
        self.store = store
        self.logger = logger
        self.dirs = dirs
        self.on_record = on_record
        self.on_log = on_log
        self.on_status = on_status
        self.on_busy = on_busy

        self._lock = threading.RLock()
        self._settings = Settings()
        self._exclude: List[ExcludeRule] = []
        self._aliases: List[Alias] = []
        self._names: List[NameList] = []
        self._schedule: List[ScheduleSlot] = []
        self._manual_mode = Mode.MONITOR
        self._effective_mode = Mode.MONITOR
        #: 定时计划命中的时间段（命中期间不允许改手动模式）
        self._schedule_active = False
        self._schedule_hit = ""
        self._copying = False
        self._last_event = ""
        self._last_result = ""
        self._last_stats = CopyStats()
        self._last_copy_at = ""
        self._known: Dict[str, DeviceInfo] = {}
        self._running = False
        self._copy_lock = threading.Lock()
        self._trigger = threading.Event()
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._tick_thread: Optional[threading.Thread] = None
        self._watcher = None
        self._copy_executing = False
        self.reload()

    # -- 生命周期 --------------------------------------------------------

    def reload(self) -> None:
        if not self.store:
            return
        with self._lock:
            self._settings = self.store.load_settings()
            self._exclude = self.store.list_exclude_rules()
            self._aliases = self.store.list_aliases()
            self._names = self.store.list_lists()
            self._schedule = self.store.list_schedule()
            self._manual_mode = self._settings.manual_mode
        self.recompute()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="sv-monitor", daemon=True)
        self._monitor_thread.start()
        self._tick_thread = threading.Thread(
            target=self._tick_loop, name="sv-tick", daemon=True)
        self._tick_thread.start()
        self._start_watcher()
        self._log("info", "监控已启动，当前模式：%s" % Mode.label(self._effective_mode))

    def _start_watcher(self) -> None:
        try:
            from ..system.watcher import DeviceWatcher

            self._watcher = DeviceWatcher(self.trigger_scan)
            self._watcher.start()
        except Exception as exc:
            self._log("warn", "设备事件监听不可用，改用轮询：%s" % exc)

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_event.set()
        self._trigger.set()
        if self._watcher:
            try:
                self._watcher.stop()
            except Exception:
                pass
            self._watcher = None
        self._log("info", "监控已停止")

    def trigger_scan(self) -> None:
        """告诉引擎去重新比对一次盘符快照（插拔事件或轮询都会调用）。"""
        self._trigger.set()

    # -- 状态 ------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    @property
    def effective_mode(self) -> str:
        with self._lock:
            return self._effective_mode

    @property
    def manual_mode(self) -> str:
        with self._lock:
            return self._manual_mode

    @property
    def copying(self) -> bool:
        with self._lock:
            return self._copying

    @property
    def schedule_active(self) -> bool:
        """当前是否处于定时计划的生效时间段。"""
        with self._lock:
            return self._schedule_active

    @property
    def schedule_hit(self) -> str:
        """正在生效的时间段（``22:00-06:00``）；没命中时是空串。"""
        with self._lock:
            return self._schedule_hit

    @property
    def aliases(self) -> List[Alias]:
        with self._lock:
            return list(self._aliases)

    def state(self) -> Dict[str, object]:
        with self._lock:
            return {
                "manual_mode": self._manual_mode,
                "effective_mode": self._effective_mode,
                "copy_dest": self._settings.copy_dest,
                "copying": self._copying,
                "last_event": self._last_event,
                "last_result": self._last_result,
                "last_stats": self._last_stats,
                "last_copy_at": self._last_copy_at,
                "schedule_on": self._settings.schedule_enable,
                "schedule_active": self._schedule_active,
                "schedule_hit": self._schedule_hit,
                "next_switch": self._next_switch(),
                "rules": len(self._exclude),
                "aliases": len(self._aliases),
                "names": len(self._names),
            }

    def _notify_status(self, text: str = "") -> None:
        if self.on_status:
            try:
                self.on_status(text or self.status_text())
            except Exception:
                pass

    def status_text(self) -> str:
        state = self.state()
        text = "模式：%s" % Mode.label(state["effective_mode"])
        if state["effective_mode"] != state["manual_mode"]:
            text += "（手动：%s，定时生效中）" % Mode.label(state["manual_mode"])
        if state["copying"]:
            text += "｜正在复制…"
        if state["last_event"]:
            text += "｜最近事件：%s" % state["last_event"]
        return text

    # -- 定时切换 --------------------------------------------------------

    def recompute(self) -> str:
        now = datetime.now()
        with self._lock:
            mode = self._manual_mode
            hit = ""
            if self._settings.schedule_enable:
                for slot in self._schedule:
                    if slot.enabled and slot.active_at(now):
                        mode = slot.mode
                        hit = "%s-%s" % (slot.start, slot.end)
                        break
            changed = mode != self._effective_mode
            self._effective_mode = mode
            self._schedule_active = bool(hit)
            self._schedule_hit = hit
        if changed:
            if hit:
                self._log("info", "定时计划命中（%s），当前模式切换为：%s"
                          % (hit, Mode.label(mode)))
            else:
                self._log("info", "定时计划未命中，回到手动模式：%s" % Mode.label(mode))
            self._notify_status()
        return mode

    def _tick_loop(self) -> None:
        while not self._stop_event.wait(20.0):
            try:
                self.recompute()
            except Exception:
                pass

    def _next_switch(self) -> str:
        if not self._settings.schedule_enable:
            return ""
        now = datetime.now()
        cur = now.hour * 60 + now.minute
        weekday = now.weekday()  # 0 = 周一
        best_min = 1 << 30
        best = ""
        for slot in self._schedule:
            if not slot.enabled:
                continue
            try:
                hour, minute = [int(x) for x in slot.start.split(":")]
            except Exception:
                continue
            start = hour * 60 + minute
            for ahead in range(8):
                day = (weekday + ahead) % 7
                if slot.days not in (0, 0x7F) and not (slot.days & (1 << day)):
                    continue
                delta = ahead * 1440 + start - cur
                if delta <= 0:
                    break
                if delta < best_min:
                    best_min = delta
                    best = "%s 后切换到「%s」（%s - %s）" % (
                        _human_minutes(delta), Mode.label(slot.mode), slot.start, slot.end)
                break
        return best

    # -- 模式与设置 ------------------------------------------------------

    def set_manual_mode(self, mode: str) -> None:
        with self._lock:
            self._manual_mode = mode
            self._settings.manual_mode = mode
            settings = self._settings
        try:
            self.store.save_settings(settings)
        except Exception as exc:
            self._log("error", "保存设置失败：%s" % exc)
        self._log("info", "手动模式已设置为：%s" % Mode.label(mode))
        self.recompute()
        self._notify_status()

    def save_settings(self, settings: Settings) -> bool:
        try:
            self.store.save_settings(settings)
        except Exception as exc:
            self._log("error", "保存设置失败：%s" % exc)
            return False
        with self._lock:
            self._settings = settings
            self._manual_mode = settings.manual_mode
        if self.logger:
            self.logger.set_retention(settings.log_retention)
        self.recompute()
        self._notify_status()
        return True

    def build_filter(self) -> FileFilter:
        with self._lock:
            names = list(self._names)
        flt = FileFilter()
        for item in names:
            if item.kind == "excludeName":
                flt.exclude_names.append(item.value)
            elif item.kind == "excludeExt":
                flt.exclude_exts.append(item.value)
            elif item.kind == "includeExt":
                flt.include_exts.append(item.value)
        from ..core import fileutil

        flt.exclude_exts = fileutil.normalize_exts(flt.exclude_exts)
        flt.include_exts = fileutil.normalize_exts(flt.include_exts)
        flt.use_include = bool(flt.include_exts)
        return flt

    def match_exclude(self, device: DeviceInfo) -> Optional[ExcludeRule]:
        # 内置规则：默认排除本机内置硬盘（可在排除名单里解锁，不能删除）
        settings = self.settings
        if getattr(settings, "exclude_internal_disks", True):
            if not device.is_removable and (device.bus_type or "").upper() != "USB":
                return ExcludeRule(type="builtin", value="internalDisks",
                                   remark="系统内置规则：排除本机内置硬盘")
        with self._lock:
            rules = list(self._exclude)
        for rule in rules:
            if rule.matches(device):
                return rule
        return None

    def folder_name(self, device: DeviceInfo) -> str:
        with self._lock:
            aliases = list(self._aliases)
        return device.folder_name(aliases)

    def exclude_device(self, device: DeviceInfo) -> str:
        if not self.store:
            return "存储未就绪"
        if (device.disk_serial or "").strip():
            rule_type, value = "diskSerial", device.disk_serial
            remark = "来自监控记录：%s" % device.display_name
        elif (device.usb_serial or "").strip():
            rule_type, value = "usbSerial", device.usb_serial
            remark = "来自监控记录：%s" % device.display_name
        elif (device.name or "").strip():
            rule_type, value = "volume", device.name
            remark = "来自监控记录（按卷标）"
        else:
            rule_type, value = "letter", device.letter
            remark = "来自监控记录（仅盘符，插入位置变化后可能失效）"
        self.store.add_exclude_rule(ExcludeRule(
            type=rule_type, value=value, remark=remark, created_at=now_rfc3339()))
        self.reload()
        self._log("info", "已把设备 %s 加入排除名单（%s = %s）"
                  % (device.display_name, rule_type, value))
        return ""

    # -- 设备事件 --------------------------------------------------------

    def _monitor_loop(self) -> None:
        # 启动时补一次快照，把「程序启动前就插着」的设备也处理掉。
        self._stop_event.wait(1.0)
        try:
            self._scan_once(initial=True)
        except Exception as exc:
            self._log("warn", "启动快照失败：%s" % exc)
        while not self._stop_event.is_set():
            self._trigger.wait(5.0)
            self._trigger.clear()
            if self._stop_event.is_set():
                return
            try:
                self._scan_once()
            except Exception as exc:
                self._log("warn", "设备扫描失败：%s" % exc)

    def _scan_once(self, initial: bool = False) -> None:
        try:
            snapshot = devices.snapshot()
        except Exception:
            return
        known = self._known
        for letter, info in snapshot.items():
            if letter not in known:
                self.handle_event(letter, Event.ARRIVAL, info)
            elif _device_changed(known[letter], info):
                self.handle_event(letter, Event.ARRIVAL, info)
        for letter, info in list(known.items()):
            if letter not in snapshot:
                self.handle_event(letter, Event.REMOVAL, info)
        self._known = snapshot

    def handle_event(self, letter: str, kind: str, device: DeviceInfo) -> None:
        if not device.letter:
            device.letter = letter
        if not device.system_time:
            device.system_time = now_rfc3339()
        mode = self.effective_mode
        settings = self.settings

        if kind == Event.REMOVAL:
            self._set_last_event("移除 %s" % (device.letter or ""))
            self._log("info", "设备移除：%s (%s)" % (device.display_name, device.letter))
            if settings.record_removal and mode != Mode.OFF:
                self._save_record(Record(
                    time=now_rfc3339(), event=Event.REMOVAL, action=Action.NONE,
                    note="设备移除", device=device))
            self._notify_status()
            return

        self._set_last_event("接入 %s" % device.display_name)
        self._log("info", "设备接入：%s｜盘符 %s｜容量 %s｜总线 %s｜VID:PID %s｜序列号 %s"
                  % (device.display_name, device.letter, device.capacity_text,
                     device.bus_type, device.vid_pid, device.usb_serial or device.disk_serial))

        if mode == Mode.OFF:
            self._log("info", "当前模式为「关闭」，不做任何处理")
            self._notify_status()
            return

        rule = self.match_exclude(device)
        if rule is not None:
            note = "命中排除规则：%s = %s" % (rule.type, rule.value)
            self._log("info", "设备 %s 已排除，只记录不复制" % device.letter)
            self._save_record(Record(
                time=now_rfc3339(), event=Event.ARRIVAL, action=Action.EXCLUDED,
                note=note, device=device))
            self._notify_status()
            return

        if mode == Mode.MONITOR:
            self._log("info", "监控模式：仅记录，不复制")
            self._save_record(Record(
                time=now_rfc3339(), event=Event.ARRIVAL, action=Action.MONITOR,
                note="监控模式", device=device))
            self._notify_status()
            return

        dest_root = (settings.copy_dest or "").strip()
        if not dest_root:
            self._log("warn", "复制模式已开启，但尚未设置复制目标目录，本次只记录")
            self._save_record(Record(
                time=now_rfc3339(), event=Event.ARRIVAL, action=Action.ERROR,
                note="未设置复制目标目录", device=device))
            self._notify_status()
            return
        self.start_device_copy(device, dest_root)

    def start_device_copy(self, device: DeviceInfo, dest_root: str) -> bool:
        """在后台线程里复制一个设备（同一时刻只跑一个复制任务）。"""
        if self._copy_executing:
            self._log("warn", "已有复制任务在运行，本次跳过：%s" % device.letter)
            return False
        self._copy_executing = True
        thread = threading.Thread(
            target=self._run_copy, args=(device, dest_root), name="sv-copy", daemon=True)
        thread.start()
        return True

    def _run_copy(self, device: DeviceInfo, dest_root: str) -> None:
        try:
            self._do_copy(device, dest_root)
        except Exception as exc:
            self._log("error", "复制过程内部异常（已中断本次复制，程序继续运行）：%s" % exc)
        finally:
            self._copy_executing = False
            with self._lock:
                self._copying = False
            self._busy(False, "")
            self._notify_status()

    def _do_copy(self, device: DeviceInfo, dest_root: str, src_root: str = "",
                 folder: str = "", record: bool = True) -> CopyResult:
        src = src_root or (device.letter + "\\")
        sub = folder or self.resolve_target_folder(device, dest_root)
        dest_dir = resolve_dest_dir(dest_root, sub)
        self._busy(True, "正在复制 %s → %s" % (device.letter, dest_dir))
        with self._lock:
            self._copying = True
        self._log("info", "开始复制：%s → %s（增量）" % (src, dest_dir))

        engine = CopyEngine(
            store=self.store,
            on_log=self._log,
            on_progress=self._on_progress,
            device_key=device.device_key(),
            verify_dest_exists=self.settings.verify_dest_exists,
            verify_dest_action=self.settings.verify_dest_action,
        )
        result = engine.copy(src, dest_root, sub, self.build_filter(),
                             stop_event=self._stop_event)

        with self._lock:
            self._last_stats = result.stats
            self._last_copy_at = time.strftime("%Y-%m-%d %H:%M:%S")
        if result.errors:
            self._log("error", "复制失败：%s" % result.errors[0])
        self._log("info", "复制完成：" + result.summary)
        action = Action.COPY if result.stats.failed == 0 and not result.errors else Action.ERROR
        if record:
            record_item = Record(
                time=now_rfc3339(), event=Event.ARRIVAL, action=action,
                note=result.summary, files=result.stats.copied,
                bytes_copied=result.stats.bytes_copied,
                elapsed=result.stats.elapsed_ms, dest=result.dest_dir, device=device)
            self._save_record(record_item)
        return result

    # -- 别名改动后的目录改名 --------------------------------------------

    def alias_folder_names(self, device: DeviceInfo) -> Tuple[str, str]:
        """返回 (原名, 带别名的名字)。"""
        return device.base_folder_name, self.folder_name(device)

    def folder_name_for(self, device: DeviceInfo, aliases: List[Alias]) -> str:
        """用指定的别名列表算文件夹名（用来比较"改名前 / 改名后"）。"""
        for alias in aliases or []:
            if alias.alias and alias.matches(device):
                return fileutil.sanitize_name(
                    "%s_%s" % (device.display_name, alias.alias))
        return device.base_folder_name

    def _existing_folder_for(self, dest_root: str, device: DeviceInfo,
                             target: str) -> str:
        """在目标目录里找这台设备已有的文件夹（<原名> 或 <原名>_<旧别名>）。"""
        base = device.base_folder_name
        candidates = []
        try:
            for name in os.listdir(fileutil.long_path(dest_root)):
                if name == target:
                    return ""
                if name == base or name.startswith(base + "_"):
                    if os.path.isdir(fileutil.long_path(os.path.join(dest_root, name))):
                        candidates.append(name)
        except OSError:
            return ""
        if len(candidates) == 1:
            return candidates[0]
        # 多个候选时优先选 <原名> 本身
        if base in candidates:
            return base
        return ""

    def rename_folder(self, dest_root: str, old_name: str, new_name: str) -> bool:
        """把 dest_root\\old_name 改名为 dest_root\\new_name（新目录已存在则不合并）。"""
        src = os.path.join(dest_root, old_name)
        dst = os.path.join(dest_root, new_name)
        if not old_name or not new_name or old_name == new_name:
            return False
        if not os.path.isdir(fileutil.long_path(src)):
            return False
        if os.path.exists(fileutil.long_path(dst)):
            return False
        try:
            os.rename(fileutil.long_path(src), fileutil.long_path(dst))
        except OSError as exc:
            self._log("warn", "复制目录改名失败：%s → %s（%s）" % (old_name, new_name, exc))
            return False
        self._log("info", "已把复制目录改名：%s → %s" % (old_name, new_name))
        return True

    def rename_alias_folder(self, device: DeviceInfo, dest_root: str) -> str:
        """把 ``<原名>`` 目录改名成 ``<原名>_<别名>``（别名改动后继续往里面复制）。

        返回 "renamed:<新名>" / "skipped" / "" 之类的说明，供界面提示。
        """
        root = (dest_root or "").strip()
        if not root or not os.path.isdir(fileutil.long_path(root)):
            return ""
        base, target = self.alias_folder_names(device)
        if not base or base == target:
            return ""
        dst = os.path.join(root, target)
        if os.path.exists(fileutil.long_path(dst)):
            return "exists"
        old_name = self._existing_folder_for(root, device, target)
        if not old_name:
            return ""
        if self.rename_folder(root, old_name, target):
            return "renamed:%s" % target
        return ""

    def resolve_target_folder(self, device: DeviceInfo, dest_root: str) -> str:
        """决定这次该往哪个文件夹里复制，并顺手把老文件夹改名。

        规则（靠 ``device_folders`` 记录"上次用的文件夹名"）：

        * 上次用的名字 ≠ 现在算出来的名字 → 把老目录**改名**成新名字；
        * 现在这个名字的目录已经存在 → 直接用（不合并、不报错）；
        * 没有历史记录（升级上来的老数据）→ 尝试在目标目录里找 ``<原名>`` 或
          ``<原名>_<旧别名>`` 并改名。
        """
        want = self.folder_name(device)
        root = (dest_root or "").strip()
        if not root or not self.store:
            return want
        key = device.device_key()
        try:
            current = self.store.get_device_folder(key)
        except Exception:
            current = ""
        target_path = os.path.join(root, want)
        if current and current != want:
            if not os.path.exists(fileutil.long_path(target_path)):
                self.rename_folder(root, current, want)
        elif not current:
            found = self._existing_folder_for(root, device, want)
            if found and found != want:
                self.rename_folder(root, found, want)
        try:
            self.store.set_device_folder(key, want)
        except Exception:
            pass
        return want

    def rename_after_alias_change(self, old_aliases: List[Alias],
                                  new_aliases: List[Alias]) -> List[str]:
        """别名新增 / 修改 / 删除之后，把所有已知设备的复制目录改成新名字。

        例如 ``<原名>_<旧别名>`` → ``<原名>_<新别名>``，``<原名>`` → ``<原名>_<别名>``。
        """
        dest_root = (self.settings.copy_dest or "").strip()
        if not dest_root or not os.path.isdir(fileutil.long_path(dest_root)):
            return []
        done: List[str] = []
        for device in list(self._known.values()):
            old_name = self.folder_name_for(device, old_aliases)
            new_name = self.folder_name_for(device, new_aliases)
            if old_name == new_name:
                continue
            renamed = self.rename_folder(dest_root, old_name, new_name)
            if not renamed:
                # 记录里存着"上次用的文件夹名"，用它兜底（老数据也对得上）
                try:
                    stored = self.store.get_device_folder(device.device_key())
                except Exception:
                    stored = ""
                if stored and stored != new_name:
                    renamed = self.rename_folder(dest_root, stored, new_name)
            if not renamed:
                found = self._existing_folder_for(dest_root, device, new_name)
                if found and found != new_name:
                    renamed = self.rename_folder(dest_root, found, new_name)
            try:
                self.store.set_device_folder(device.device_key(), new_name)
            except Exception:
                pass
            if renamed:
                done.append(new_name)
        return done

    def rename_alias_folders_for_known_devices(self) -> List[str]:
        """别名改动后，对所有已知设备尝试改名，返回改成功的目录名列表。"""
        dest_root = (self.settings.copy_dest or "").strip()
        if not dest_root:
            return []
        done = []
        for device in list(self._known.values()):
            note = self.rename_alias_folder(device, dest_root)
            if note.startswith("renamed:"):
                done.append(note.split(":", 1)[1])
        return done

    def desktop_copy(self, dev_mode: bool = False):
        """工具 1：桌面整理复制（返回 CopyResult 或 None）。"""
        settings = self.settings
        src = (settings.desktop_src or "").strip() or paths.desktop_dir()
        dest = (settings.desktop_dest or "").strip() or (settings.copy_dest or "").strip()
        if not dest:
            return None, "尚未设置目标目录（可在设置里指定，或使用与 U 盘复制相同的目标）"
        if not os.path.isdir(src):
            return None, "源目录不可访问：%s" % src
        placeholder = DeviceInfo(letter="桌面", name="桌面")
        result = self._do_copy(placeholder, dest, src_root=src, folder="桌面", record=False)
        return result, ""

    def _on_progress(self, info) -> None:
        if info.get("phase") == "copy":
            self._busy(True, "正在复制 %d 个文件…" % int(info.get("copied", 0)))

    # -- 内部小工具 ------------------------------------------------------

    def _save_record(self, record: Record) -> None:
        if not self.store:
            return
        try:
            record.id = self.store.insert_record(record)
        except Exception as exc:
            self._log("error", "写入监控记录失败：%s" % exc)
            return
        if self.on_record:
            try:
                self.on_record(record)
            except Exception:
                pass

    def _set_last_event(self, text: str) -> None:
        with self._lock:
            self._last_event = time.strftime("%H:%M:%S ") + text

    def _busy(self, busy: bool, text: str) -> None:
        with self._lock:
            if text:
                self._last_result = text
        if self.on_busy:
            try:
                self.on_busy(busy, text)
            except Exception:
                pass

    def _log(self, level: str, message: str) -> None:
        if self.logger:
            method = getattr(self.logger, level, None)
            if callable(method):
                method(message)
            else:
                self.logger.info(message)
        if self.on_log:
            try:
                self.on_log(level, message)
            except Exception:
                pass


def _device_changed(old: DeviceInfo, new: DeviceInfo) -> bool:
    return (old.name != new.name or old.capacity != new.capacity
            or old.fs != new.fs or old.volume_guid != new.volume_guid)


def _human_minutes(minutes: int) -> str:
    if minutes < 60:
        return "%d 分钟" % minutes
    hours, mins = divmod(minutes, 60)
    return "%d 小时" % hours if mins == 0 else "%d 小时 %d 分钟" % (hours, mins)
