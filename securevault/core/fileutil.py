"""文件相关工具：原子写、Windows 文件名净化、通配符匹配、长路径、剩余空间。"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import tempfile
from typing import Iterable, List, Sequence

_INVALID_CHARS = '<>:"/\\|?*'
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


# ---------------------------------------------------------------------------
# 长路径
# ---------------------------------------------------------------------------

def long_path(path: str) -> str:
    """给绝对路径加 ``\\\\?\\`` 前缀以突破 MAX_PATH 限制。"""
    if not path or path.startswith("\\\\?\\"):
        return path
    if len(path) >= 3 and path[1] == ":" and path[2] in "\\/":
        return "\\\\?\\" + os.path.abspath(path)
    return path


def from_long_path(path: str) -> str:
    return path[4:] if path.startswith("\\\\?\\") else path


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------

def write_atomic(path: str, data: bytes, tmp_dir: str = "") -> None:
    """先写临时文件再改名，保证不会留下半截文件。"""
    directory = os.path.dirname(os.path.abspath(path))
    if tmp_dir:
        try:
            os.makedirs(tmp_dir, exist_ok=True)
        except OSError:
            tmp_dir = ""
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=tmp_dir or directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.replace(tmp, path)
            tmp = ""
        except OSError:
            # 目标被占用时退化为先删再改名。
            try:
                os.remove(path)
                os.replace(tmp, path)
                tmp = ""
            except OSError:
                raise
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 文件名净化
# ---------------------------------------------------------------------------

def sanitize_name(value: str, fallback: str = "Unknown") -> str:
    """把任意字符串净化成合法的 Windows 文件名。"""
    fallback = fallback or "Unknown"
    out_chars = []
    for ch in value or "":
        if ord(ch) < 32 or ch in _INVALID_CHARS:
            out_chars.append("_")
        else:
            out_chars.append(ch)
    out = "".join(out_chars).rstrip(" .").strip()
    if not out:
        return fallback
    if out.upper() in _RESERVED:
        out += "_"
    if len(out) > 100:
        out = out[:100].rstrip(" .")
        if not out:
            return fallback
    return out


# ---------------------------------------------------------------------------
# 通配符
# ---------------------------------------------------------------------------

def _wildcard_regex(pattern: str) -> "re.Pattern[str]":
    parts = ["^"]
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    parts.append("$")
    return re.compile("".join(parts), re.IGNORECASE | re.DOTALL)


_PATTERN_CACHE = {}


def match_name(pattern: str, name: str) -> bool:
    """通配符匹配（``*`` / ``?``），大小写不敏感，只用于单个文件/文件夹名。"""
    pat = (pattern or "").strip()
    if not pat:
        return False
    if pat == "*":
        return True
    regex = _PATTERN_CACHE.get(pat)
    if regex is None:
        regex = _wildcard_regex(pat)
        if len(_PATTERN_CACHE) < 4096:
            _PATTERN_CACHE[pat] = regex
    return bool(regex.match((name or "").strip()))


def match_any(patterns: Sequence[str], name: str) -> bool:
    return any(match_name(p, name) for p in patterns)


# ---------------------------------------------------------------------------
# 扩展名
# ---------------------------------------------------------------------------

def normalize_ext(value: str) -> str:
    """把扩展名统一成 ``.ext`` 小写形式，输入 ``.txt`` / ``txt`` / ``*.txt`` 都可以。"""
    text = (value or "").strip().lower().lstrip("*").lstrip(".")
    return "." + text if text else ""


def normalize_exts(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in values or []:
        ext = normalize_ext(item)
        if not ext or ext in seen:
            continue
        seen.add(ext)
        out.append(ext)
    return out


def ext_of(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


# ---------------------------------------------------------------------------
# 属性与磁盘空间
# ---------------------------------------------------------------------------

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def attrs_of(path: str) -> int:
    try:
        value = ctypes.windll.kernel32.GetFileAttributesW(long_path(path))
    except Exception:
        return 0
    if value == INVALID_FILE_ATTRIBUTES or value < 0:
        return 0
    return int(value)


def is_hidden_or_system(path: str) -> bool:
    return bool(attrs_of(path) & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))


def is_reparse_point(path: str) -> bool:
    return bool(attrs_of(path) & FILE_ATTRIBUTE_REPARSE_POINT)


class _ULARGE(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def free_space(path: str) -> int:
    """返回 path 所在卷的剩余可用字节数；失败抛 OSError。"""
    target = path
    while target and not os.path.exists(long_path(target)):
        parent = os.path.dirname(target.rstrip("\\/"))
        if parent == target:
            break
        target = parent
    if not target:
        target = os.path.abspath(os.sep)
    free_avail = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    total_free = ctypes.c_ulonglong(0)
    ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(long_path(target)),
        ctypes.byref(free_avail),
        ctypes.byref(total),
        ctypes.byref(total_free),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "无法获取磁盘剩余空间: %s" % target)
    return int(free_avail.value)


def copy_file_buffered(src: str, dst: str, buffer_size: int = 256 * 1024) -> int:
    """带缓冲的单文件复制，返回写入字节数。"""
    os.makedirs(long_path(os.path.dirname(dst)), exist_ok=True)
    written = 0
    with open(long_path(src), "rb", buffering=0) as fsrc:
        with open(long_path(dst), "wb", buffering=0) as fdst:
            while True:
                chunk = fsrc.read(buffer_size)
                if not chunk:
                    break
                fdst.write(chunk)
                written += len(chunk)
    return written


def human_size(num: float) -> str:
    """把字节数格式化为人类可读的容量文本。"""
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return "%d %s" % (int(value), units[idx])
    return "%.2f %s" % (value, units[idx])


def remove_tree(path: str) -> None:
    if os.path.isdir(long_path(path)):
        shutil.rmtree(long_path(path), ignore_errors=True)
    elif os.path.exists(long_path(path)):
        try:
            os.remove(long_path(path))
        except OSError:
            pass


def ensure_dir(path: str) -> None:
    if path:
        os.makedirs(long_path(path), exist_ok=True)
