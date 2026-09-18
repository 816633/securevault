"""AES-256-GCM 封装（密文布局：``nonce(12) || ciphertext || tag(16)``）。

优先使用 ``cryptography``；没有该库时自动退回 Windows CNG（bcrypt.dll），
两者输出格式完全一致，因此数据可以互相读取。
"""

from __future__ import annotations

import ctypes
import os
from typing import Optional, Tuple

NONCE_LEN = 12
TAG_LEN = 16

_backend = ""
_AESGCM = None
_BCRYPT = None


def _bcrypt():
    """加载 bcrypt.dll（CNG）。

    注意必须写全 ``bcrypt.dll``：打包成单文件 exe 后，解压目录里可能有一个叫
    ``bcrypt`` 的 Python 包目录，``ctypes.WinDLL("bcrypt")`` 会先命中那个目录而报
    「Failed to load dynlib/dll」。
    """
    global _BCRYPT
    if _BCRYPT is not None:
        return _BCRYPT
    last_error = None
    for name in ("bcrypt.dll", os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "bcrypt.dll")):
        try:
            _BCRYPT = ctypes.WinDLL(name, use_last_error=True)
            return _BCRYPT
        except Exception as exc:  # pragma: no cover - 取决于运行环境
            last_error = exc
    raise last_error if last_error else OSError("无法加载 bcrypt.dll")


def _load_backend() -> str:
    global _backend, _AESGCM
    if _backend:
        return _backend
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

        _AESGCM = AESGCM
        _backend = "cryptography"
        return _backend
    except Exception:
        pass
    try:
        _bcrypt()
        _backend = "cng"
        return _backend
    except Exception:
        _backend = "none"
        return _backend


def backend_name() -> str:
    return _load_backend()


def available() -> bool:
    return _load_backend() != "none"


# ---------------------------------------------------------------------------
# CNG（bcrypt.dll）实现
# ---------------------------------------------------------------------------

class _BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("dwInfoVersion", ctypes.c_ulong),
        ("pbNonce", ctypes.c_void_p),
        ("cbNonce", ctypes.c_ulong),
        ("pbAuthData", ctypes.c_void_p),
        ("cbAuthData", ctypes.c_ulong),
        ("pbTag", ctypes.c_void_p),
        ("cbTag", ctypes.c_ulong),
        ("pbMacContext", ctypes.c_void_p),
        ("cbMacContext", ctypes.c_ulong),
        ("cbAAD", ctypes.c_ulong),
        ("cbData", ctypes.c_ulonglong),
        ("dwFlags", ctypes.c_ulong),
    ]


BCRYPT_OBJECT_LENGTH = 0x00000001
BCRYPT_ENCRYPT = 0x00000002
BCRYPT_DECRYPT = 0x00000001
BCRYPT_AUTH_MODE_INFO_VERSION = 1
STATUS_SUCCESS = 0

_BCRYPT_ALG_HANDLE = ctypes.c_void_p
_BCRYPT_KEY_HANDLE = ctypes.c_void_p


class CNGError(Exception):
    pass


def _cng_alg():
    """打开 AES 算法提供者并切到 GCM 模式，返回 (alg, 密钥对象长度)。"""
    bcrypt = _bcrypt()
    bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_wchar_p, ctypes.c_wchar_p,
        ctypes.c_ulong,
    ]
    bcrypt.BCryptSetProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    bcrypt.BCryptGetProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
    ]
    bcrypt.BCryptGenerateSymmetricKey.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
    ]
    bcrypt.BCryptDestroyKey.argtypes = [ctypes.c_void_p]
    bcrypt.BCryptCloseAlgorithmProvider.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    bcrypt.BCryptEncrypt.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(_BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO),
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
    ]
    bcrypt.BCryptDecrypt.argtypes = list(bcrypt.BCryptEncrypt.argtypes)

    alg = _BCRYPT_ALG_HANDLE()
    if bcrypt.BCryptOpenAlgorithmProvider(
        ctypes.byref(alg), "AES", None, 0
    ) != STATUS_SUCCESS:
        raise CNGError("BCryptOpenAlgorithmProvider 失败")
    mode = "ChainingModeGCM"
    status = bcrypt.BCryptSetProperty(
        alg, "ChainingMode", ctypes.c_wchar_p(mode),
        ctypes.c_ulong((len(mode) + 1) * 2), 0,
    )
    if status != STATUS_SUCCESS:
        bcrypt.BCryptCloseAlgorithmProvider(alg, 0)
        raise CNGError("无法把算法切到 GCM 模式: 0x%08X" % (status & 0xFFFFFFFF))
    obj_len = ctypes.c_ulong(0)
    result = ctypes.c_ulong(0)
    bcrypt.BCryptGetProperty(
        alg, "ObjectLength", ctypes.byref(obj_len), ctypes.sizeof(obj_len),
        ctypes.byref(result), 0,
    )
    return alg, int(obj_len.value)


def _cng_key(alg, key: bytes, obj_len: int):
    bcrypt = _bcrypt()
    # 注意：BCryptGenerateSymmetricKey 会把密钥对象写进 obj_buf，
    # 返回的句柄指向这块内存，所以缓冲区必须一直存活到用完为止。
    obj_buf = ctypes.create_string_buffer(obj_len)
    key_handle = _BCRYPT_KEY_HANDLE()
    status = bcrypt.BCryptGenerateSymmetricKey(
        alg, ctypes.byref(key_handle), obj_buf, obj_len,
        ctypes.c_char_p(key), len(key), 0,
    )
    if status != STATUS_SUCCESS:
        raise CNGError("BCryptGenerateSymmetricKey 失败: 0x%08X" % (status & 0xFFFFFFFF))
    return key_handle, obj_buf


def _cng_encrypt(key: bytes, nonce: bytes, plain: bytes, aad: bytes) -> Tuple[bytes, bytes]:
    bcrypt = _bcrypt()
    alg, obj_len = _cng_alg()
    try:
        key_handle, key_buffer = _cng_key(alg, key, obj_len)
        try:
            tag = ctypes.create_string_buffer(TAG_LEN)
            nonce_buf = ctypes.create_string_buffer(nonce, len(nonce))
            aad_buf = ctypes.create_string_buffer(aad, len(aad)) if aad else None
            out_len = ctypes.c_ulong(0)
            info = _BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
            info.cbSize = ctypes.sizeof(info)
            info.dwInfoVersion = BCRYPT_AUTH_MODE_INFO_VERSION
            info.pbNonce = ctypes.cast(nonce_buf, ctypes.c_void_p)
            info.cbNonce = len(nonce)
            info.pbAuthData = ctypes.cast(aad_buf, ctypes.c_void_p) if aad_buf else None
            info.cbAuthData = len(aad)
            info.pbTag = ctypes.cast(tag, ctypes.c_void_p)
            info.cbTag = TAG_LEN
            out_buf = ctypes.create_string_buffer(len(plain) + TAG_LEN + 16)
            status = bcrypt.BCryptEncrypt(
                key_handle,
                ctypes.create_string_buffer(plain, len(plain) or 1),
                len(plain),
                ctypes.byref(info),
                None,
                0,
                out_buf,
                len(out_buf),
                ctypes.byref(out_len),
                0,
            )
            if status != STATUS_SUCCESS:
                raise CNGError("BCryptEncrypt 失败: 0x%08X" % (status & 0xFFFFFFFF))
            return out_buf.raw[: out_len.value], tag.raw[:TAG_LEN]
        finally:
            bcrypt.BCryptDestroyKey(key_handle)
            del key_buffer
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def _cng_decrypt(key: bytes, nonce: bytes, cipher: bytes, tag: bytes, aad: bytes) -> bytes:
    bcrypt = _bcrypt()
    alg, obj_len = _cng_alg()
    try:
        key_handle, key_buffer = _cng_key(alg, key, obj_len)
        try:
            tag_buf = ctypes.create_string_buffer(tag, len(tag))
            nonce_buf = ctypes.create_string_buffer(nonce, len(nonce))
            aad_buf = ctypes.create_string_buffer(aad, len(aad)) if aad else None
            out_len = ctypes.c_ulong(0)
            info = _BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
            info.cbSize = ctypes.sizeof(info)
            info.dwInfoVersion = BCRYPT_AUTH_MODE_INFO_VERSION
            info.pbNonce = ctypes.cast(nonce_buf, ctypes.c_void_p)
            info.cbNonce = len(nonce)
            info.pbAuthData = ctypes.cast(aad_buf, ctypes.c_void_p) if aad_buf else None
            info.cbAuthData = len(aad)
            info.pbTag = ctypes.cast(tag_buf, ctypes.c_void_p)
            info.cbTag = len(tag)
            out_buf = ctypes.create_string_buffer(len(cipher) + 16)
            status = bcrypt.BCryptDecrypt(
                key_handle,
                ctypes.create_string_buffer(cipher, len(cipher) or 1),
                len(cipher),
                ctypes.byref(info),
                None,
                0,
                out_buf,
                len(out_buf),
                ctypes.byref(out_len),
                0,
            )
            if status != STATUS_SUCCESS:
                raise CNGError("BCryptDecrypt 失败（密钥错误或数据被篡改）")
            return out_buf.raw[: out_len.value]
        finally:
            bcrypt.BCryptDestroyKey(key_handle)
            del key_buffer
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

class DecryptError(Exception):
    """解密失败（密钥不对或数据被篡改）。"""


def seal(key: bytes, plain: bytes, aad: bytes = b"") -> bytes:
    """加密并返回 ``nonce || ciphertext || tag``。"""
    if len(key) != 32:
        raise ValueError("AES-256-GCM 需要 32 字节密钥")
    nonce = os.urandom(NONCE_LEN)
    mode = _load_backend()
    if mode == "cryptography":
        blob = _AESGCM(key).encrypt(nonce, plain, aad or None)
        return nonce + blob
    if mode == "cng":
        cipher, tag = _cng_encrypt(key, nonce, plain, aad)
        return nonce + cipher + tag
    raise RuntimeError("当前环境缺少 AES-GCM 实现（cryptography 或 Windows CNG）")


def open_(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    """解密 ``nonce || ciphertext || tag``；失败抛 :class:`DecryptError`。"""
    if len(key) != 32:
        raise ValueError("AES-256-GCM 需要 32 字节密钥")
    if not blob or len(blob) < NONCE_LEN + TAG_LEN:
        raise DecryptError("密文长度不足")
    nonce = blob[:NONCE_LEN]
    body = blob[NONCE_LEN:]
    mode = _load_backend()
    try:
        if mode == "cryptography":
            return _AESGCM(key).decrypt(nonce, body, aad or None)
        if mode == "cng":
            return _cng_decrypt(key, nonce, body[:-TAG_LEN], body[-TAG_LEN:], aad)
    except DecryptError:
        raise
    except Exception as exc:
        raise DecryptError(str(exc)) from exc
    raise RuntimeError("当前环境缺少 AES-GCM 实现（cryptography 或 Windows CNG）")


def seal_text(key: bytes, text: str, aad: bytes = b"") -> Optional[bytes]:
    """加密文本；空串返回 None（数据库里存 NULL）。"""
    if not text:
        return None
    return seal(key, text.encode("utf-8"), aad)


def open_text(key: bytes, blob: Optional[bytes], aad: bytes = b"") -> str:
    """解密文本；空值或解密失败都返回空串（绝不抛异常给界面）。"""
    if not blob:
        return ""
    try:
        return open_(key, bytes(blob), aad).decode("utf-8", "replace")
    except Exception:
        return ""
