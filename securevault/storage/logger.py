"""纯文本日志：按天一个文件，行格式 ``yyyy-mm-dd HH:MM:SS.mmm [级别] 内容``。

目录永远是 ``<数据目录>\\logs``；任何 IO 失败都静默丢弃（绝不弹窗、绝不崩溃）。
"""

from __future__ import annotations

import os
import threading
import time
from typing import List, Optional, Tuple

from ..core import fileutil

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
DEFAULT_RETENTION = 14


def _normalize_retention(days: int) -> int:
    return days if days in (7, 14, 30) else DEFAULT_RETENTION


def _valid_day(name: str) -> Optional[str]:
    if not name.lower().endswith(".txt"):
        return None
    day = name[:-4]
    if len(day) != 10:
        return None
    try:
        time.strptime(day, "%Y-%m-%d")
    except ValueError:
        return None
    return day


class LogFile:
    __slots__ = ("day", "path", "size")

    def __init__(self, day: str, path: str, size: int) -> None:
        self.day = day
        self.path = path
        self.size = size


class Logger:
    """线程安全的按天日志器。所有公开方法都不会抛异常。"""

    def __init__(self, dirs, retention: int = DEFAULT_RETENTION) -> None:
        self.logs_dir = dirs.logs
        self.retention = _normalize_retention(retention)
        self._lock = threading.RLock()
        self._handle = None
        self._handle_day = ""
        self._closed = False
        try:
            os.makedirs(fileutil.long_path(self.logs_dir), exist_ok=True)
        except Exception:
            pass
        self.cleanup()

    # -- 写 --------------------------------------------------------------

    def debug(self, message: str, *args) -> None:
        self._log("DEBUG", message, args)

    def info(self, message: str, *args) -> None:
        self._log("INFO", message, args)

    def warn(self, message: str, *args) -> None:
        self._log("WARN", message, args)

    def error(self, message: str, *args) -> None:
        self._log("ERROR", message, args)

    def _log(self, level: str, message: str, args) -> None:
        try:
            text = message % args if args else str(message)
        except Exception:
            text = str(message)
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        now = time.localtime()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", now) + ".%03d" % int(
            (time.time() % 1) * 1000
        )
        day = time.strftime("%Y-%m-%d", now)
        line = "%s [%-5s] %s\r\n" % (stamp, level, text)
        with self._lock:
            if self._closed:
                return
            if self._ensure_handle(day):
                try:
                    self._handle.write(line)
                    self._handle.flush()
                except Exception:
                    self._close_handle()

    def _ensure_handle(self, day: str) -> bool:
        if self._handle is not None and self._handle_day == day:
            return True
        if self._handle_day and self._handle_day != day:
            self.cleanup()
        self._close_handle()
        try:
            os.makedirs(fileutil.long_path(self.logs_dir), exist_ok=True)
            path = os.path.join(self.logs_dir, day + ".txt")
            self._handle = open(fileutil.long_path(path), "a", encoding="utf-8", newline="")
            self._handle_day = day
            return True
        except Exception:
            self._handle = None
            self._handle_day = ""
            return False

    def _close_handle(self) -> None:
        handle, self._handle = self._handle, None
        self._handle_day = ""
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    # -- 保留策略 --------------------------------------------------------

    def set_retention(self, days: int) -> None:
        with self._lock:
            self.retention = _normalize_retention(days)
        self.cleanup()

    def cleanup(self) -> int:
        cutoff = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - self.retention * 86400)
        )
        return self._clear(lambda item: item.day < cutoff)

    def clear_before(self, days: int) -> int:
        cutoff = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - max(0, days) * 86400)
        )
        return self._clear(lambda item: item.day < cutoff)

    def clear_all(self) -> int:
        return self._clear(lambda item: True)

    def _clear(self, predicate) -> int:
        removed = 0
        for item in self.files():
            if not predicate(item):
                continue
            with self._lock:
                if self._handle is not None and self._today_path() == item.path:
                    self._close_handle()
            try:
                os.remove(fileutil.long_path(item.path))
                removed += 1
            except Exception:
                continue
        return removed

    # -- 查询 ------------------------------------------------------------

    def files(self) -> List[LogFile]:
        out: List[LogFile] = []
        try:
            names = os.listdir(fileutil.long_path(self.logs_dir))
        except Exception:
            return out
        for name in names:
            day = _valid_day(name)
            if not day:
                continue
            path = os.path.join(self.logs_dir, name)
            try:
                size = os.path.getsize(fileutil.long_path(path))
            except Exception:
                size = 0
            out.append(LogFile(day, path, size))
        out.sort(key=lambda item: item.day, reverse=True)
        return out

    def read(self, day: str, tail_lines: int = 300) -> str:
        path = os.path.join(self.logs_dir, day + ".txt")
        try:
            with open(fileutil.long_path(path), "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except FileNotFoundError:
            return ""
        except Exception as exc:
            return "（读取日志失败：%s）" % exc
        if tail_lines <= 0:
            return text
        lines = text.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if len(lines) > tail_lines:
            lines = lines[-tail_lines:]
        return "\n".join(lines)

    def _today_path(self) -> str:
        return os.path.join(self.logs_dir, time.strftime("%Y-%m-%d") + ".txt")

    @property
    def today_file(self) -> str:
        return self._today_path()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._close_handle()
