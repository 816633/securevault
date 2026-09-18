"""Windows DPAPI 封装：用当前用户凭据保护一份数据副本（用于开机自启后自动恢复后台监控）。

安全边界：副本只在本机、本用户下可解；拷到别的机器 / 别的账户都解不开。
密码与恢复码本身绝不落盘。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_to_bytes(blob: DATA_BLOB) -> bytes:
    size = int(blob.cbData)
    if not size or not blob.pbData:
        return b""
    return ctypes.string_at(blob.pbData, size)


def _free(blob: DATA_BLOB) -> None:
    if blob.pbData:
        ctypes.windll.kernel32.LocalFree(blob.pbData)
        blob.pbData = None
        blob.cbData = 0


def available() -> bool:
    try:
        ctypes.windll.crypt32
        return True
    except Exception:
        return False


def protect(plain: bytes) -> Optional[bytes]:
    """用当前用户凭据加密数据；失败返回 None。"""
    if not plain:
        return None
    buf = ctypes.create_string_buffer(plain, len(plain))
    blob_in = DATA_BLOB(len(plain), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        ctypes.c_wchar_p("SecureVault"),
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        return None
    try:
        return _blob_to_bytes(blob_out)
    finally:
        _free(blob_out)


def unprotect(blob: bytes) -> Optional[bytes]:
    """解开由 :func:`protect` 加密的数据；失败返回 None。"""
    if not blob:
        return None
    buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        return None
    try:
        return _blob_to_bytes(blob_out)
    finally:
        _free(blob_out)
