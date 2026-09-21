"""共享数据结构与枚举（不含任何业务逻辑）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from . import fileutil


# ---------------------------------------------------------------------------
# 运行模式
# ---------------------------------------------------------------------------

class Mode:
    OFF = "off"
    MONITOR = "monitor"
    COPY = "copy"

    LABELS = {OFF: "关闭", MONITOR: "监控模式", COPY: "复制模式"}

    @staticmethod
    def label(value: str) -> str:
        return Mode.LABELS.get(value, "关闭")


# ---------------------------------------------------------------------------
# 事件与动作
# ---------------------------------------------------------------------------

class Event:
    ARRIVAL = "arrival"
    REMOVAL = "removal"

    @staticmethod
    def label(value: str) -> str:
        return "移除" if value == Event.REMOVAL else "接入"


class Action:
    NONE = "none"
    MONITOR = "monitor"
    COPY = "copy"
    EXCLUDED = "excluded"
    ERROR = "error"

    LABELS = {NONE: "未处理", MONITOR: "仅记录", COPY: "已复制",
              EXCLUDED: "已排除", ERROR: "出错"}

    @staticmethod
    def label(value: str) -> str:
        return Action.LABELS.get(value, "未处理")


# ---------------------------------------------------------------------------
# 设备
# ---------------------------------------------------------------------------

@dataclass
class DeviceInfo:
    letter: str = ""
    name: str = ""
    bus_type: str = ""
    model: str = ""
    vendor: str = ""
    vid: str = ""
    pid: str = ""
    usb_serial: str = ""
    disk_serial: str = ""
    fs: str = ""
    capacity: int = 0
    free: int = 0
    physical_drive: int = -1
    device_instance: str = ""
    volume_guid: str = ""
    device_path: str = ""
    is_removable: bool = False
    system_time: str = ""

    @property
    def display_name(self) -> str:
        return self.name or self.model or self.letter

    @property
    def vid_pid(self) -> str:
        return "%s:%s" % (self.vid, self.pid) if self.vid and self.pid else ""

    @property
    def capacity_text(self) -> str:
        return fileutil.human_size(self.capacity)

    def folder_name(self, aliases: "List[Alias]") -> str:
        """复制出的文件夹名：有别名时是 ``<原名>_<别名>``，否则就是原名。"""
        for alias in aliases:
            if alias.alias and alias.matches(self):
                return fileutil.sanitize_name(
                    "%s_%s" % (self.display_name, alias.alias))
        return fileutil.sanitize_name(self.display_name)

    @property
    def base_folder_name(self) -> str:
        """不带别名的原始文件夹名。"""
        return fileutil.sanitize_name(self.display_name)

    def device_key(self) -> str:
        """设备的稳定标识（增量索引用它区分不同设备）。"""
        for value in (self.disk_serial, self.usb_serial):
            text = (value or "").strip()
            if text:
                return "sn:" + text.upper()
        guid = (self.volume_guid or "").strip()
        if guid:
            return "vg:" + guid.upper()
        name = (self.name or "").strip()
        if name:
            return "vol:" + name.upper()
        return "letter:" + (self.letter or "")


@dataclass
class Record:
    id: int = 0
    time: str = ""
    event: str = Event.ARRIVAL
    action: str = Action.NONE
    note: str = ""
    files: int = 0
    bytes_copied: int = 0
    dest: str = ""
    elapsed: int = 0
    device: DeviceInfo = field(default_factory=DeviceInfo)


# ---------------------------------------------------------------------------
# 别名（工具 2）
# ---------------------------------------------------------------------------

MATCH_FIELDS = [
    ("volume", "卷标"),
    ("model", "设备型号"),
    ("vid", "USB 厂商 ID"),
    ("pid", "USB 产品 ID"),
    ("usbSerial", "USB 序列号"),
    ("diskSerial", "磁盘序列号"),
    ("instance", "设备实例 ID"),
]

MATCH_FIELD_LABELS = dict(MATCH_FIELDS)


@dataclass
class Alias:
    id: int = 0
    match: str = "volume"
    value: str = ""
    alias: str = ""
    remark: str = ""

    def matches(self, device: DeviceInfo) -> bool:
        value = (self.value or "").strip()
        if not value:
            return False
        field_map = {
            "volume": device.name,
            "model": device.model,
            "vid": device.vid,
            "pid": device.pid,
            "usbSerial": device.usb_serial,
            "diskSerial": device.disk_serial,
            "instance": device.device_instance,
        }
        actual = (field_map.get(self.match, "") or "").strip()
        return actual.lower() == value.lower()

    def device_value(self, device: DeviceInfo) -> str:
        return {
            "volume": device.name,
            "model": device.model,
            "vid": device.vid,
            "pid": device.pid,
            "usbSerial": device.usb_serial,
            "diskSerial": device.disk_serial,
            "instance": device.device_instance,
        }.get(self.match, "")


# ---------------------------------------------------------------------------
# 设备排除规则
# ---------------------------------------------------------------------------

RULE_TYPES = [
    ("busType", "总线类型"),
    ("vid", "USB 厂商 ID"),
    ("pid", "USB 产品 ID"),
    ("usbSerial", "USB 序列号"),
    ("diskSerial", "磁盘序列号"),
    ("model", "设备型号"),
    ("volume", "卷标"),
    ("fs", "文件系统"),
    ("letter", "盘符"),
    ("physDisk", "物理磁盘号"),
    ("instance", "设备实例 ID"),
    ("capacity", "容量"),
]

RULE_TYPE_LABELS = dict(RULE_TYPES)


@dataclass
class ExcludeRule:
    id: int = 0
    type: str = "vid"
    value: str = ""
    remark: str = ""
    created_at: str = ""

    def matches(self, device: DeviceInfo) -> bool:
        value = (self.value or "").strip()
        if not value:
            return False

        def equals(text: str) -> bool:
            return (text or "").strip().lower() == value.lower()

        kind = self.type
        if kind == "busType":
            return equals(device.bus_type)
        if kind == "vid":
            return equals(device.vid) or equals(device.vid_pid)
        if kind == "pid":
            return equals(device.pid)
        if kind == "usbSerial":
            return equals(device.usb_serial)
        if kind == "diskSerial":
            return equals(device.disk_serial)
        if kind == "model":
            return equals(device.model) or fileutil.match_name(value, device.model)
        if kind == "volume":
            return equals(device.name) or fileutil.match_name(value, device.name)
        if kind == "fs":
            return equals(device.fs)
        if kind == "letter":
            return equals(device.letter.rstrip(":")) or equals(device.letter)
        if kind == "physDisk":
            return device.physical_drive >= 0 and equals(str(device.physical_drive))
        if kind == "instance":
            return equals(device.device_instance) or (
                value.lower() in (device.device_instance or "").lower()
            )
        if kind == "capacity":
            return match_capacity(value, device.capacity)
        return False


def parse_capacity(expr: str) -> Optional[int]:
    """把 ``64GB`` / ``512MB`` 解析成字节数；非法返回 None。"""
    text = (expr or "").strip().upper().replace(" ", "")
    for suffix, factor in (("TB", 1024 ** 4), ("GB", 1024 ** 3),
                           ("MB", 1024 ** 2), ("KB", 1024)):
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            break
    else:
        suffix, factor, number = "", 0, ""
    if not factor:
        # 纯数字按 MB 处理，与需求中的示例一致。
        number, factor = text, 1024 ** 2
    try:
        return int(float(number) * factor)
    except (TypeError, ValueError):
        return None


def match_capacity(expr: str, actual: int) -> bool:
    """容量表达式匹配：``512MB``（±2% 容差）/ ``>=64GB`` / ``<2GB`` 等。"""
    text = (expr or "").strip()
    if not text:
        return False
    for op in (">=", "<=", ">", "<", "=", "=="):
        if text.startswith(op):
            target = parse_capacity(text[len(op):])
            if target is None:
                return False
            if op in ("=", "=="):
                return abs(actual - target) <= max(target * 0.02, 1)
            if op == ">=":
                return actual >= target
            if op == "<=":
                return actual <= target
            if op == ">":
                return actual > target
            return actual < target
    target = parse_capacity(text)
    if target is None:
        return False
    return abs(actual - target) <= max(target * 0.02, 1)


# ---------------------------------------------------------------------------
# 名单
# ---------------------------------------------------------------------------

LIST_KINDS = [
    ("excludeName", "排除的文件/文件夹名"),
    ("excludeExt", "排除的扩展名"),
    ("includeExt", "只复制这些扩展名"),
]

LIST_KIND_LABELS = dict(LIST_KINDS)


@dataclass
class NameList:
    id: int = 0
    kind: str = "excludeName"
    value: str = ""
    remark: str = ""
    created_at: str = ""


# ---------------------------------------------------------------------------
# 复制配置
# ---------------------------------------------------------------------------

@dataclass
class FileFilter:
    exclude_names: List[str] = field(default_factory=list)
    exclude_exts: List[str] = field(default_factory=list)
    include_exts: List[str] = field(default_factory=list)
    use_include: bool = False
    skip_hidden: bool = False
    skip_zero_byte: bool = False
    max_file_size_mb: int = 0


@dataclass
class CopyStats:
    scanned: int = 0
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_copied: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# 定时切换
# ---------------------------------------------------------------------------

ALL_DAYS = 0x7F
DAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass
class ScheduleSlot:
    id: int = 0
    start: str = "22:00"
    end: str = "06:00"
    mode: str = Mode.COPY
    days: int = ALL_DAYS
    enabled: bool = True
    remark: str = ""

    def days_text(self) -> str:
        if self.days in (0, ALL_DAYS):
            return "每天"
        picked = [DAY_LABELS[i] for i in range(7) if self.days & (1 << i)]
        return " ".join(picked) if picked else "每天"

    def active_at(self, now) -> bool:
        try:
            sh, sm = [int(x) for x in (self.start or "").split(":")]
            eh, em = [int(x) for x in (self.end or "").split(":")]
        except Exception:
            return False
        if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
            return False
        hour, minute, weekday = time_parts(now)
        cur = hour * 60 + minute
        start, end = sh * 60 + sm, eh * 60 + em
        day_on = lambda d: bool(self.days & (1 << (d % 7)))
        if start == end:
            return self.days in (0, ALL_DAYS) or day_on(weekday)
        if start < end:
            if not (start <= cur < end):
                return False
            return self.days in (0, ALL_DAYS) or day_on(weekday)
        # 跨午夜
        if cur >= start:
            return self.days in (0, ALL_DAYS) or day_on(weekday)
        if cur < end:
            return self.days in (0, ALL_DAYS) or day_on((weekday + 6) % 7)
        return False


def time_parts(now):
    """从 datetime / struct_time 里取出 (小时, 分钟, 星期[0=周一])。"""
    if hasattr(now, "tm_hour"):
        return now.tm_hour, now.tm_min, now.tm_wday
    if hasattr(now, "hour") and hasattr(now, "weekday"):
        return now.hour, now.minute, now.weekday()
    try:
        hour = int(now[3])
        minute = int(now[4])
        weekday = int(now[6])
        return hour, minute, (weekday + 6) % 7
    except Exception:
        return 0, 0, 0


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    manual_mode: str = Mode.MONITOR
    autostart: bool = True
    log_retention: int = 14
    desktop_src: str = ""
    desktop_dest: str = ""
    copy_dest: str = ""
    schedule_enable: bool = True
    record_removal: bool = True
    minimize_to_tray: bool = True
    background_monitor: bool = True
    #: 触屏设备上点输入框时自动弹出屏幕键盘（没有触摸设备的电脑上无影响）
    touch_keyboard: bool = True
    confirm_before_copy: bool = False
    #: 复制前检查"目标文件是否还在"：目标被误删时按下面的策略处理
    verify_dest_exists: bool = True
    #: recopy = 重新复制；skip = 不复制
    verify_dest_action: str = "recopy"
    #: 是否排除本机内置硬盘（默认排除，解锁需要密码确认，不能删除这条规则）
    exclude_internal_disks: bool = True
    last_page: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        base = cls()
        for key, value in (data or {}).items():
            if hasattr(base, key):
                setattr(base, key, value)
        try:
            base.log_retention = int(base.log_retention)
        except (TypeError, ValueError):
            base.log_retention = 14
        if base.log_retention not in (7, 14, 30):
            base.log_retention = 14
        if base.manual_mode not in (Mode.OFF, Mode.MONITOR, Mode.COPY):
            base.manual_mode = Mode.MONITOR
        if base.verify_dest_action not in ("recopy", "skip"):
            base.verify_dest_action = "recopy"
        return base


LOG_RETENTION_OPTIONS = (7, 14, 30)


def now_rfc3339() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def human_duration_ms(ms: int) -> str:
    if ms < 1000:
        return "%d 毫秒" % ms
    seconds = ms / 1000.0
    if seconds < 60:
        return "%.1f 秒" % seconds
    minutes = int(seconds // 60)
    return "%d 分 %d 秒" % (minutes, int(seconds - minutes * 60))
