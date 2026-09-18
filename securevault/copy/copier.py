"""增量复制引擎。

约束（与需求一致）：
  * 全程静默：不调用 cmd / powershell / robocopy，不弹任何窗口；
  * 单个文件失败只记录、重试，不中断整批；任何内部异常都不会让进程崩溃；
  * 复制前先算「待复制字节数 vs 目标剩余空间」，不足则不开始；
    复制过程中定期复查，不足则安全停止（已复制的文件保持有效）；
  * 增量判断只看 (大小, 修改时间)，不做全文件哈希，保证轻量。
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from ..core import fileutil
from ..core.model import CopyStats, FileFilter

COPY_BUFFER = 256 * 1024
PROGRESS_INTERVAL = 0.1
SPACE_CHECK_EVERY = 200
MAX_ERRORS = 200
DEFAULT_WORKERS = 4
MAX_WORKERS = 16


@dataclass
class CopyResult:
    stats: CopyStats = field(default_factory=CopyStats)
    dest_dir: str = ""
    errors: List[str] = field(default_factory=list)
    canceled: bool = False

    @property
    def summary(self) -> str:
        note = "复制 %d 个（跳过 %d，失败 %d），共 %s，耗时 %s" % (
            self.stats.copied, self.stats.skipped, self.stats.failed,
            fileutil.human_size(self.stats.bytes_copied),
            _seconds_text(self.stats.elapsed_ms),
        )
        return note + ("（已取消）" if self.canceled else "")


def _seconds_text(ms: int) -> str:
    if ms < 1000:
        return "%d 毫秒" % ms
    return "%.1f 秒" % (ms / 1000.0)


def should_copy_info(path: str, name: str, size: int, flt: FileFilter) -> bool:
    """按文件过滤规则判断是否参与复制。"""
    if fileutil.match_any(flt.exclude_names, name):
        return False
    ext = fileutil.ext_of(name)
    if flt.exclude_exts and ext in fileutil.normalize_exts(flt.exclude_exts):
        return False
    if flt.use_include:
        include = fileutil.normalize_exts(flt.include_exts)
        if include and ext not in include:
            return False
    if flt.skip_zero_byte and size == 0:
        return False
    if flt.max_file_size_mb > 0 and size > flt.max_file_size_mb * 1024 * 1024:
        return False
    if flt.skip_hidden and fileutil.is_hidden_or_system(path):
        return False
    return True


def resolve_dest_dir(dest_root: str, sub_folder: str) -> str:
    """计算最终目标目录 ``destRoot\\subFolder``；拒绝任何试图逃出 destRoot 的取值。"""
    root = (dest_root or "").strip()
    if not root:
        return ""
    root = os.path.abspath(root)
    sub = (sub_folder or "").strip()
    if not sub:
        return root
    full = os.path.abspath(os.path.join(root, sub))
    try:
        rel = os.path.relpath(full, root)
    except ValueError:
        return root
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return root
    return full


def _unique_path(path: str) -> str:
    if not os.path.isfile(fileutil.long_path(path)):
        return path
    directory = os.path.dirname(path)
    stem, ext = os.path.splitext(os.path.basename(path))
    for index in range(1, 65536):
        candidate = os.path.join(directory, "%s (%d)%s" % (stem, index, ext))
        if not os.path.isfile(fileutil.long_path(candidate)):
            return candidate
    return path


class CopyEngine:
    """一次复制任务 = 一个实例；可重复用于多次复制。"""

    def __init__(self, store=None, on_log: Optional[Callable[[str, str], None]] = None,
                 on_progress: Optional[Callable[[Dict[str, object]], None]] = None,
                 workers: int = DEFAULT_WORKERS, retries: int = 2,
                 verify_size: bool = True, preserve_time: bool = True,
                 conflict: str = "overwrite", device_key: str = "",
                 verify_dest_exists: bool = True,
                 verify_dest_action: str = "recopy") -> None:
        self.store = store
        self.device_key = device_key or ""
        #: 目标文件被误删时：True 时按 verify_dest_action 处理（recopy 重新复制）
        self.verify_dest_exists = bool(verify_dest_exists)
        self.verify_dest_action = verify_dest_action or "recopy"
        self.on_log = on_log
        self.on_progress = on_progress
        self.workers = max(1, min(MAX_WORKERS, workers or DEFAULT_WORKERS))
        self.retries = max(0, retries)
        self.verify_size = verify_size
        self.preserve_time = preserve_time
        self.conflict = conflict

    # -- 对外入口 --------------------------------------------------------

    def copy(self, src_root: str, dest_root: str, sub_folder: str,
             flt: Optional[FileFilter] = None, items: Optional[List[str]] = None,
             stop_event: Optional[threading.Event] = None,
             incremental: bool = True) -> CopyResult:
        flt = flt or FileFilter()
        result = CopyResult(dest_dir=resolve_dest_dir(dest_root, sub_folder))
        if not src_root or not os.path.isdir(fileutil.long_path(src_root)):
            result.errors.append("源目录不可访问：%s" % src_root)
            return result
        if not dest_root:
            result.errors.append("目标根目录为空")
            return result
        if stop_event is None:
            stop_event = threading.Event()
        try:
            os.makedirs(fileutil.long_path(dest_root), exist_ok=True)
        except OSError as exc:
            result.errors.append("目标目录不可用 %s：%s" % (dest_root, exc))
            return result
        try:
            free = fileutil.free_space(dest_root)
        except OSError as exc:
            result.errors.append("无法获取目标磁盘剩余空间：%s" % exc)
            return result

        started = time.time()
        state = _RunState(dest_dir=result.dest_dir, src_root=src_root)
        state.stop_event = stop_event
        candidates, scan_errors = self._scan(
            src_root, result.dest_dir, flt, incremental, stop_event, state, items
        )
        for message in scan_errors:
            self._add_error(result, message)
        if stop_event.is_set():
            result.canceled = True
            state.finish(result, started)
            return result

        pending = state.pending_bytes
        state.remaining = pending
        if pending > 0 and free < pending:
            result.errors.append(
                "目标磁盘剩余空间不足，需要 %s，可用 %s（%s）"
                % (fileutil.human_size(pending), fileutil.human_size(free), dest_root)
            )
            state.finish(result, started)
            return result

        try:
            if candidates:
                os.makedirs(fileutil.long_path(result.dest_dir), exist_ok=True)
        except OSError as exc:
            result.errors.append("无法创建目标子目录 %s：%s" % (result.dest_dir, exc))
            state.finish(result, started)
            return result

        self._copy_all(candidates, state, stop_event, result)
        state.finish(result, started)
        return result

    def scan_only(self, src_root: str, flt: Optional[FileFilter] = None) -> Tuple[int, int]:
        """只扫描不复制，返回 (文件数, 总字节数)。"""
        flt = flt or FileFilter()
        state = _RunState(dest_dir="", src_root=src_root)
        total_files = 0
        total_bytes = 0
        for path, name, size, _mtime in _walk(src_root, flt):
            total_files += 1
            total_bytes += size
        return total_files, total_bytes

    # -- 扫描 ------------------------------------------------------------

    def _scan(self, src_root: str, dest_dir: str, flt: FileFilter, incremental: bool,
              stop_event: threading.Event, state: "_RunState",
              items: Optional[List[str]]):
        candidates: List[Tuple[str, str, int, float]] = []
        errors: List[str] = []
        roots = [src_root]
        if items:
            roots = [item for item in items if item]
        for root in roots:
            for path, name, size, mtime in _walk(root, flt, errors, stop_event):
                if stop_event.is_set():
                    return candidates, errors
                state.scanned += 1
                state.total = state.scanned
                state.progress("scan", path, self.on_progress)
                mark = (self.store.get_mark(path, self.device_key)
                        if (self.store and incremental) else None)
                mod_unix = int(mtime)
                if (incremental and mark and mark["size"] == size
                        and mark["mod_unix"] == mod_unix):
                    if not self.verify_dest_exists:
                        state.skipped += 1
                        continue
                    dest_path = mark.get("dest_path") or ""
                    if dest_path and os.path.isfile(fileutil.long_path(dest_path)):
                        state.skipped += 1
                        continue
                    # 目标文件不在了：按用户设置决定"重新复制"还是"不复制"
                    if self.verify_dest_action == "skip":
                        state.skipped += 1
                        state.missing_dest += 1
                        continue
                    state.missing_dest += 1
                    self._log("info", "目标文件已不存在，重新复制：%s" % path)
                rel = self._relative(src_root, path)
                base = os.path.join(dest_dir, rel)
                dest = base
                if (incremental and mark and mark["dest_path"]
                        and self._within(dest_dir, mark["dest_path"])
                        and os.path.isfile(fileutil.long_path(mark["dest_path"]))):
                    dest = mark["dest_path"]
                elif os.path.isfile(fileutil.long_path(base)):
                    if self.conflict == "skip":
                        state.skipped += 1
                        continue
                    if self.conflict == "rename":
                        dest = _unique_path(base)
                candidates.append((path, dest, size, mtime))
                state.pending_bytes += size
        return candidates, errors

    @staticmethod
    def _relative(src_root: str, path: str) -> str:
        src_root = fileutil.from_long_path(src_root)
        path = fileutil.from_long_path(path)
        try:
            rel = os.path.relpath(path, src_root)
        except ValueError:
            return os.path.basename(path)
        if rel in (".", os.pardir) or rel.startswith(os.pardir + os.sep):
            return os.path.basename(path)
        return rel

    @staticmethod
    def _within(root: str, path: str) -> bool:
        if not root:
            return False
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            return False
        return not (rel == os.pardir or rel.startswith(os.pardir + os.sep))

    # -- 复制 ------------------------------------------------------------

    def _copy_all(self, candidates: List[Tuple[str, str, int, float]],
                  state: "_RunState", stop_event: threading.Event,
                  result: CopyResult) -> None:
        if not candidates:
            return
        if stop_event.is_set():
            result.canceled = True
            return
        workers = min(self.workers, len(candidates))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for item in candidates:
                if stop_event.is_set():
                    result.canceled = True
                    break
                futures.append(pool.submit(self._copy_one, item, state, stop_event, result))
            for future in futures:
                try:
                    future.result()
                except Exception:
                    pass

    def _copy_one(self, item: Tuple[str, str, int, float], state: "_RunState",
                  stop_event: threading.Event, result: CopyResult) -> None:
        src, dst, size, mtime = item
        if stop_event.is_set():
            return
        if os.path.abspath(fileutil.from_long_path(src)) == os.path.abspath(
                fileutil.from_long_path(dst)):
            state.failed += 1
            self._add_error(result, "源与目标为同一个文件，已跳过：%s" % src)
            return
        state.progress("copy", src, self.on_progress, copied=state.copied)
        last_error = ""
        current_size, current_mtime = size, mtime
        for attempt in range(self.retries + 1):
            if attempt:
                if not self._sleep_backoff(attempt, stop_event):
                    return
                try:
                    stat = os.stat(fileutil.long_path(src))
                    current_size, current_mtime = stat.st_size, stat.st_mtime
                except OSError:
                    return
            try:
                written = 0
                os.makedirs(fileutil.long_path(os.path.dirname(dst)), exist_ok=True)
                with open(fileutil.long_path(src), "rb", buffering=0) as fsrc:
                    with open(fileutil.long_path(dst), "wb", buffering=0) as fdst:
                        while True:
                            chunk = fsrc.read(COPY_BUFFER)
                            if not chunk:
                                break
                            fdst.write(chunk)
                            written += len(chunk)
                if self.verify_size and os.path.getsize(fileutil.long_path(dst)) != written:
                    raise IOError("大小校验不一致")
                if written != current_size:
                    raise IOError("源文件在复制期间发生变化")
                if self.preserve_time:
                    try:
                        stamp = time.time() if current_mtime <= 0 else current_mtime
                        os.utime(fileutil.long_path(dst), (stamp, stamp))
                    except OSError:
                        pass
                state.copied += 1
                state.bytes_copied += written
                state.remaining -= written
                if self.store:
                    try:
                        self.store.put_mark(src, current_size, int(current_mtime), dst,
                                            self.device_key)
                    except Exception:
                        pass
                self._space_guard(state, result)
                return
            except Exception as exc:
                last_error = str(exc)
                self._log("warn", "复制第 %d 次失败（%s）：%s" % (attempt + 1, src, exc))
                if stop_event.is_set():
                    return
        state.failed += 1
        self._add_error(result, "%s -> %s: %s" % (src, dst, last_error))
        self._log("error", "复制失败：%s -> %s: %s" % (src, dst, last_error))

    def _space_guard(self, state: "_RunState", result: CopyResult) -> None:
        state.processed += 1
        if state.processed % SPACE_CHECK_EVERY:
            return
        if not state.dest_dir:
            return
        try:
            free = fileutil.free_space(state.dest_dir)
        except OSError:
            return
        if free < state.remaining and not state.space_stop:
            state.space_stop = True
            message = ("目标磁盘剩余空间不足（剩余 %s，仍待复制 %s），已安全停止；"
                       "已复制的文件保持有效"
                       % (fileutil.human_size(free), fileutil.human_size(state.remaining)))
            self._add_error(result, message)
            self._log("error", message)
            state.stop_event.set()

    @staticmethod
    def _sleep_backoff(attempt: int, stop_event: threading.Event) -> bool:
        deadline = time.time() + attempt * 0.2
        while True:
            if stop_event.is_set():
                return False
            remain = deadline - time.time()
            if remain <= 0:
                return True
            time.sleep(min(remain, 0.02))

    # -- 辅助 ------------------------------------------------------------

    @staticmethod
    def _add_error(result: CopyResult, message: str) -> None:
        if len(result.errors) < MAX_ERRORS:
            result.errors.append(message)

    def _log(self, level: str, message: str) -> None:
        if self.on_log:
            try:
                self.on_log(level, message)
            except Exception:
                pass


class _RunState:
    """一次复制的运行状态。"""

    def __init__(self, dest_dir: str, src_root: str) -> None:
        self.dest_dir = dest_dir
        self.src_root = src_root
        self.scanned = 0
        self.copied = 0
        self.skipped = 0
        self.failed = 0
        self.bytes_copied = 0
        self.total = 0
        self.processed = 0
        self.pending_bytes = 0
        self.remaining = 0
        self.missing_dest = 0
        self.space_stop = False
        self.stop_event = threading.Event()
        self._last_progress = 0.0
        self._lock = threading.Lock()

    def progress(self, phase: str, current: str, callback, copied: int = 0,
                 force: bool = False) -> None:
        if not callback:
            return
        now = time.time()
        with self._lock:
            if not force and now - self._last_progress < PROGRESS_INTERVAL:
                return
            self._last_progress = now
        try:
            callback({
                "phase": phase, "current": current, "scanned": self.scanned,
                "copied": self.copied, "skipped": self.skipped,
                "failed": self.failed, "bytes": self.bytes_copied, "total": self.total,
            })
        except Exception:
            pass

    def finish(self, result: CopyResult, started: float) -> None:
        result.stats = CopyStats(
            scanned=self.scanned, copied=self.copied, skipped=self.skipped,
            failed=self.failed, bytes_copied=self.bytes_copied,
            elapsed_ms=int((time.time() - started) * 1000),
        )


def _walk(root: str, flt: FileFilter, errors: Optional[List[str]] = None,
          stop_event: Optional[threading.Event] = None):
    """深度优先遍历，逐个产出 (path, name, size, mtime)。"""
    stack = [root]
    errors = errors if errors is not None else []
    while stack:
        if stop_event is not None and stop_event.is_set():
            return
        current = stack.pop()
        try:
            if fileutil.is_reparse_point(current) and os.path.isdir(fileutil.long_path(current)):
                continue
            entries = list(os.scandir(fileutil.long_path(current)))
        except OSError as exc:
            errors.append("无法读取目录 %s：%s" % (current, exc))
            continue
        for entry in reversed(entries):
            try:
                if entry.is_dir(follow_symlinks=False):
                    if fileutil.match_any(flt.exclude_names, entry.name):
                        continue
                    if flt.skip_hidden and fileutil.is_hidden_or_system(entry.path):
                        continue
                    stack.append(fileutil.from_long_path(entry.path))
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not should_copy_info(entry.path, entry.name, stat.st_size, flt):
                continue
            yield fileutil.from_long_path(entry.path), entry.name, stat.st_size, stat.st_mtime

    if stop_event is not None and stop_event.is_set():
        return
