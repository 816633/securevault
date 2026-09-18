"""密钥库：主密码 / 恢复码 / 数据密钥（DEK）。

与旧版（Go）实现保持**完全一致的磁盘格式**，因此升级安装不会让已设置的密码失效：

    keystore.json
      version / createdAt / changedAt
      kdf      { algo, iter, salt }
      pwdWrap  { wrapped, verifier }   # 用密码派生的 KEK 包裹 DEK，verifier 用 KMAC 派生
      recWrap  { wrapped, verifier }
      failCount / lockedAt / lockedTill
      autoUnlock { wrapped, createdAt, note }   # 可选，DPAPI 保护的 DEK 副本

    KDF   : PBKDF2-HMAC-SHA256，随机 16 字节 salt，200000 次，派生 64 字节
            = KEK(32B) + KMAC(32B)
    包裹  : AES-256-GCM，随机 12 字节 nonce
            AAD = "SecureVault|keystore|v1|pwd" / "SecureVault|keystore|v1|rec"
    校验  : HMAC-SHA256(KMAC, label)，label = "SecureVault|keystore|v1|pwd|hmac" 等
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Optional, Tuple

from ..core import fileutil
from . import aead, dpapi

FILE_VERSION = 1
KDF_ALGO = "PBKDF2-HMAC-SHA256"
KDF_ITER = 200_000
KDF_SALT_LEN = 16
MAX_KDF_ITER = 5_000_000
KEY_LEN = 32
DERIVED_LEN = 64

AAD_PWD = b"SecureVault|keystore|v1|pwd"
AAD_REC = b"SecureVault|keystore|v1|rec"
HMAC_LABEL_PWD = b"SecureVault|keystore|v1|pwd|hmac"
HMAC_LABEL_REC = b"SecureVault|keystore|v1|rec|hmac"

FAILED_UNLOCK_LIMIT = 5
#: 连续失败达到上限后的封禁时长（用户要求 5 分钟）
LOCKOUT_SECONDS = 300

RECOVERY_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 I O 0 1

STATE_UNINITIALIZED = "uninitialized"
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"

_PWD_DOMAIN = b"SecureVault|keystore|v1|pwd-domain"
_REC_DOMAIN = b"SecureVault|keystore|v1|rec-domain"


class KeyStoreError(Exception):
    pass


class WrongPassword(KeyStoreError):
    pass


class WrongRecoveryCode(KeyStoreError):
    pass


class LockedOut(KeyStoreError):
    def __init__(self, seconds: int) -> None:
        super().__init__("尝试次数过多，请在 %d 秒后重试" % max(0, seconds))
        self.seconds = max(0, seconds)


class PasswordTooShort(KeyStoreError):
    pass


class AlreadyInitialized(KeyStoreError):
    pass


class NotInitialized(KeyStoreError):
    pass


class Locked(KeyStoreError):
    pass


class CorruptKeyStore(KeyStoreError):
    pass


def _b64(data: Optional[bytes]) -> str:
    return base64.b64encode(data or b"").decode("ascii")


def _unb64(text: Optional[str]) -> bytes:
    if not text:
        return b""
    try:
        return base64.b64decode(text)
    except Exception:
        return b""


def _derive(secret: str, salt: bytes, iterations: int) -> Tuple[bytes, bytes]:
    material = hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), salt, iterations, dklen=DERIVED_LEN
    )
    return material[:KEY_LEN], material[KEY_LEN:]


def _verifier(kmac: bytes, label: bytes) -> bytes:
    return hmac.new(kmac, label, hashlib.sha256).digest()


def _wrap(kek: bytes, dek: bytes, aad: bytes) -> bytes:
    return aead.seal(kek, dek, aad)


def _unwrap(kek: bytes, blob: bytes, aad: bytes) -> Optional[bytes]:
    try:
        return aead.open_(kek, blob, aad)
    except Exception:
        return None


def normalize_recovery(text: str) -> str:
    """恢复码归一化：去掉 ``-`` 与空格并转大写（比较时忽略大小写与横线）。"""
    return "".join(ch for ch in (text or "") if ch not in "- \t\r\n").upper()


def format_recovery(raw: str) -> str:
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def new_recovery_code() -> str:
    """生成 ``XXXX-XXXX-XXXX`` 形式的恢复码。"""
    return "-".join(
        "".join(secrets.choice(RECOVERY_CHARSET) for _ in range(4)) for _ in range(3)
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_time(text: str) -> Optional[float]:
    if not text:
        return None
    try:
        return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


class Status:
    def __init__(self, state: str, created_at: str = "", changed_at: str = "",
                 fail_count: int = 0, locked_till: str = "", auto_unlock: bool = False):
        self.state = state
        self.created_at = created_at
        self.changed_at = changed_at
        self.fail_count = fail_count
        self.locked_till = locked_till
        self.auto_unlock = auto_unlock

    @property
    def lockout_remaining(self) -> int:
        ts = _parse_time(self.locked_till)
        if not ts:
            return 0
        return max(0, int(ts - time.time()))

    @property
    def attempts_left(self) -> int:
        """还剩几次尝试机会（已封禁时为 0）。"""
        if self.lockout_remaining > 0:
            return 0
        return max(0, FAILED_UNLOCK_LIMIT - self.fail_count)

    def failure_message(self) -> str:
        """按用户要求的策略给出提示（共 5 次机会）：

        * 第 1、2 次输错：只说"密码错误"；
        * 第 3 次输错（还剩 2 次）：显示剩余次数 + 封禁时长；
        * 第 4 次输错（还剩 1 次）：显示剩余次数 + 封禁时长；
        * 第 5 次：封禁 5 分钟。
        """
        left = self.attempts_left
        if left >= 3:
            return "密码错误"
        if left == 2:
            return "密码错误（还剩 2 次，再错将封禁 %d 分钟）" % (LOCKOUT_SECONDS // 60)
        if left == 1:
            return "密码错误（还剩 1 次，再错将封禁 %d 分钟）" % (LOCKOUT_SECONDS // 60)
        return "密码错误次数过多"


class KeyStore:
    """密钥库。全部状态由 :class:`threading.RLock` 保护。"""

    def __init__(self, dirs, iterations: int = KDF_ITER) -> None:
        self.path = dirs.keystore_file
        self.tmp_dir = dirs.tmp
        self._lock = threading.RLock()
        self._state: dict = {}
        self._dek: Optional[bytes] = None
        self._iterations = iterations
        self._load()

    # -- 内部读写 --------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self.path):
                self._state = {}
                return
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception as exc:
                raise CorruptKeyStore("密钥库文件无法读取: %s" % exc) from exc
            if not isinstance(data, dict) or int(data.get("version", -1)) != FILE_VERSION:
                raise CorruptKeyStore("密钥库文件版本不受支持")
            self._state = data

    def _save(self, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
        fileutil.write_atomic(self.path, payload, self.tmp_dir)

    # -- 状态 ------------------------------------------------------------

    @property
    def initialized(self) -> bool:
        with self._lock:
            return bool(self._state.get("pwdWrap"))

    def status(self) -> Status:
        with self._lock:
            if not self._state.get("pwdWrap"):
                state = STATE_UNINITIALIZED
            elif self._dek is not None:
                state = STATE_UNLOCKED
            else:
                state = STATE_LOCKED
            return Status(
                state=state,
                created_at=self._state.get("createdAt", ""),
                changed_at=self._state.get("changedAt", ""),
                fail_count=int(self._state.get("failCount", 0) or 0),
                locked_till=self._state.get("lockedTill", ""),
                auto_unlock=bool(self._state.get("autoUnlock")),
            )

    def dek(self) -> bytes:
        with self._lock:
            if self._dek is None:
                raise Locked("密钥库处于锁定状态")
            return bytes(self._dek)

    @property
    def unlocked(self) -> bool:
        with self._lock:
            return self._dek is not None

    def lock(self) -> None:
        with self._lock:
            if self._dek is not None:
                self._dek = b""
            self._dek = None

    # -- 初始化 ----------------------------------------------------------

    def initialize(self, password: str) -> str:
        """首次设置密码，返回**只显示这一次**的恢复码。"""
        if len(password or "") < 8:
            raise PasswordTooShort("密码至少需要 8 位")
        with self._lock:
            if self._state.get("pwdWrap"):
                raise AlreadyInitialized("密钥库已经初始化")
            dek = secrets.token_bytes(KEY_LEN)
            code = new_recovery_code()
            state = self._build_state(password, code, dek)
            self._save(state)
            self._state = state
            self._dek = dek
            return code

    def _build_state(self, password: str, recovery_code: str, dek: bytes) -> dict:
        salt = secrets.token_bytes(KDF_SALT_LEN)
        iterations = self._iterations
        kek_p, kmac_p = _derive(password, salt, iterations)
        raw = normalize_recovery(recovery_code)
        kek_r, kmac_r = _derive(raw, salt, iterations)
        now = _now()
        return {
            "version": FILE_VERSION,
            "createdAt": now,
            "changedAt": now,
            "kdf": {"algo": KDF_ALGO, "iter": iterations, "salt": _b64(salt)},
            "pwdWrap": {
                "wrapped": _b64(_wrap(kek_p, dek, AAD_PWD)),
                "verifier": _b64(_verifier(kmac_p, HMAC_LABEL_PWD)),
            },
            "recWrap": {
                "wrapped": _b64(_wrap(kek_r, dek, AAD_REC)),
                "verifier": _b64(_verifier(kmac_r, HMAC_LABEL_REC)),
            },
            "failCount": 0,
            "lockedAt": "",
            "lockedTill": "",
        }

    # -- 解锁 ------------------------------------------------------------

    def _kdf_of(self, state: dict) -> Tuple[bytes, int]:
        kdf = state.get("kdf") or {}
        salt = _unb64(kdf.get("salt"))
        try:
            iterations = int(kdf.get("iter") or 0)
        except (TypeError, ValueError):
            iterations = 0
        if len(salt) < KDF_SALT_LEN or not 0 < iterations <= MAX_KDF_ITER:
            raise CorruptKeyStore("密钥库的 KDF 参数异常")
        return salt, iterations

    def _check_lockout(self, state: dict) -> None:
        till = state.get("lockedTill") or ""
        if not till:
            return
        ts = _parse_time(till)
        if ts and ts > time.time():
            raise LockedOut(int(ts - time.time()))
        # 冷却结束：清零计数。
        state["failCount"] = 0
        state["lockedTill"] = ""
        state["lockedAt"] = ""

    def _persist_failure(self, state: dict) -> None:
        count = int(state.get("failCount", 0) or 0) + 1
        state["failCount"] = count
        if count >= FAILED_UNLOCK_LIMIT:
            state["failCount"] = 0
            state["lockedAt"] = _now()
            state["lockedTill"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + LOCKOUT_SECONDS)
            )
        try:
            self._save(state)
        except Exception:
            pass
        self._state = state

    def _persist_success(self, state: dict) -> None:
        state["failCount"] = 0
        state["lockedAt"] = ""
        state["lockedTill"] = ""
        try:
            self._save(state)
        except Exception:
            pass
        self._state = state

    def unlock(self, password: str, count_failure: bool = True) -> None:
        """用密码解锁。``count_failure=False`` 时不记入失败次数（用于设置页里核对身份）。"""
        self._unlock_with(password, recovery=False, count_failure=count_failure)

    def unlock_with_recovery(self, code: str, count_failure: bool = True) -> None:
        self._unlock_with(normalize_recovery(code), recovery=True,
                          count_failure=count_failure)

    def _unlock_with(self, secret: str, recovery: bool,
                     count_failure: bool = True) -> None:
        with self._lock:
            state = dict(self._state)
            if not state.get("pwdWrap"):
                raise NotInitialized("尚未设置密码")
            self._check_lockout(state)
            salt, iterations = self._kdf_of(state)
            key = "recWrap" if recovery else "pwdWrap"
            aad = AAD_REC if recovery else AAD_PWD
            label = HMAC_LABEL_REC if recovery else HMAC_LABEL_PWD
            kek, kmac = _derive(secret, salt, iterations)
            info = state.get(key) or {}
            expected = _unb64(info.get("verifier"))
            if expected and not hmac.compare_digest(expected, _verifier(kmac, label)):
                if count_failure:
                    self._persist_failure(state)
                raise WrongRecoveryCode("恢复码不正确") if recovery else WrongPassword("密码不正确")
            dek = _unwrap(kek, _unb64(info.get("wrapped")), aad)
            if not dek or len(dek) != KEY_LEN:
                if count_failure:
                    self._persist_failure(state)
                raise WrongRecoveryCode("恢复码不正确") if recovery else WrongPassword("密码不正确")
            self._dek = dek
            self._persist_success(state)

    # -- 修改 / 重置 -----------------------------------------------------

    def change_password(self, old_password: str, new_password: str,
                        count_failure: bool = False) -> None:
        """改密码；恢复码保持不变。"""
        if len(new_password or "") < 8:
            raise PasswordTooShort("密码至少需要 8 位")
        self.unlock(old_password, count_failure=count_failure)
        with self._lock:
            state = dict(self._state)
            salt, iterations = self._kdf_of(state)
            rec_raw = None
            # 恢复码明文不可得，因此这里重新包裹 DEK：恢复码部分保持不变。
            kek, kmac = _derive(new_password, salt, iterations)
            dek = self.dek()
            state["pwdWrap"] = {
                "wrapped": _b64(_wrap(kek, dek, AAD_PWD)),
                "verifier": _b64(_verifier(kmac, HMAC_LABEL_PWD)),
            }
            state["changedAt"] = _now()
            state["failCount"] = 0
            state["lockedTill"] = ""
            state["lockedAt"] = ""
            if state.get("autoUnlock"):
                blob = dpapi.protect(dek)
                if blob:
                    state["autoUnlock"]["wrapped"] = _b64(blob)
                    state["autoUnlock"]["createdAt"] = _now()
            self._save(state)
            self._state = state

    def reset_with_recovery(self, code: str, new_password: str) -> str:
        """用恢复码重置密码，并轮换出新的恢复码。"""
        if len(new_password or "") < 8:
            raise PasswordTooShort("密码至少需要 8 位")
        self.unlock_with_recovery(code)
        with self._lock:
            dek = self.dek()
            new_code = new_recovery_code()
            state = self._build_state(new_password, new_code, dek)
            if self._state.get("autoUnlock"):
                blob = dpapi.protect(dek)
                if blob:
                    state["autoUnlock"] = {
                        "wrapped": _b64(blob),
                        "createdAt": _now(),
                        "note": "由 Windows 用户凭据（DPAPI）保护；只在本机本用户可以解开",
                    }
            self._save(state)
            self._state = state
            self._dek = dek
            return new_code

    def rotate_recovery(self, password: str, count_failure: bool = False) -> str:
        """重新生成恢复码（需密码）。"""
        self.unlock(password, count_failure=count_failure)
        with self._lock:
            state = dict(self._state)
            salt, iterations = self._kdf_of(state)
            dek = self.dek()
            code = new_recovery_code()
            kek, kmac = _derive(normalize_recovery(code), salt, iterations)
            state["recWrap"] = {
                "wrapped": _b64(_wrap(kek, dek, AAD_REC)),
                "verifier": _b64(_verifier(kmac, HMAC_LABEL_REC)),
            }
            state["changedAt"] = _now()
            self._save(state)
            self._state = state
            return code

    # -- 自动解锁（DPAPI） -----------------------------------------------

    def auto_unlock_supported(self) -> bool:
        return dpapi.available()

    def auto_unlock_enabled(self) -> bool:
        with self._lock:
            info = self._state.get("autoUnlock") or {}
            return bool(info.get("wrapped"))

    def enable_auto_unlock(self) -> bool:
        with self._lock:
            if self._dek is None:
                raise Locked("密钥库处于锁定状态")
            blob = dpapi.protect(self._dek)
            if not blob:
                return False
            state = dict(self._state)
            state["autoUnlock"] = {
                "wrapped": _b64(blob),
                "createdAt": _now(),
                "note": "由 Windows 用户凭据（DPAPI）保护；只在本机本用户可以解开",
            }
            self._save(state)
            self._state = state
            return True

    def disable_auto_unlock(self) -> None:
        with self._lock:
            if not self._state.get("autoUnlock"):
                return
            state = dict(self._state)
            state.pop("autoUnlock", None)
            self._save(state)
            self._state = state

    def auto_unlock(self) -> bool:
        """用 DPAPI 副本解锁（不需要密码）；失败返回 False。"""
        with self._lock:
            if self._dek is not None:
                return True
            info = self._state.get("autoUnlock") or {}
            blob = _unb64(info.get("wrapped"))
            if not blob:
                return False
            dek = dpapi.unprotect(blob)
            if not dek or len(dek) != KEY_LEN:
                return False
            self._dek = dek
            return True
