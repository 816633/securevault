"""加密 SQLite 存储。

敏感字段逐字段 AES-256-GCM 加密后以 BLOB 存储（布局 ``nonce || ciphertext || tag``，
附加数据固定为 ``SecureVault|field|v1``），只有时间、事件、动作、容量等
非敏感字段保留明文列以便 SQL 排序与过滤。

密钥：增量索引用 HMAC-SHA256(dek) 做盲索引，路径本身不落明文。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from ..core import fileutil
from ..core import model as M
from ..crypto import aead

FIELD_AAD = b"SecureVault|field|v1"
BLIND_LABEL = b"SecureVault|field|v1|blind-index"
MAX_SCAN_ROWS = 20000
SCHEMA_VERSION = 2

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS meta (
           key TEXT PRIMARY KEY,
           value TEXT NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS records (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           time TEXT NOT NULL,
           event TEXT NOT NULL,
           action TEXT NOT NULL,
           files INTEGER NOT NULL DEFAULT 0,
           byte_count INTEGER NOT NULL DEFAULT 0,
           elapsed INTEGER NOT NULL DEFAULT 0,
           note BLOB,
           dest BLOB,
           letter BLOB,
           name BLOB,
           bus_type BLOB,
           model BLOB,
           vendor BLOB,
           vid BLOB,
           pid BLOB,
           usb_serial BLOB,
           disk_serial BLOB,
           fs BLOB,
           capacity INTEGER NOT NULL DEFAULT 0,
           free_bytes INTEGER NOT NULL DEFAULT 0,
           physical_drive INTEGER NOT NULL DEFAULT -1,
           device_instance BLOB,
           volume_guid BLOB,
           device_path BLOB,
           is_removable INTEGER NOT NULL DEFAULT 0
       )""",
    "CREATE INDEX IF NOT EXISTS idx_records_time ON records(time)",
    "CREATE INDEX IF NOT EXISTS idx_records_event ON records(event)",
    """CREATE TABLE IF NOT EXISTS exclude_rules (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           rule_type TEXT NOT NULL,
           value BLOB,
           remark BLOB,
           created_at TEXT NOT NULL
       )""",
    "CREATE INDEX IF NOT EXISTS idx_exclude_rules_type ON exclude_rules(rule_type)",
    """CREATE TABLE IF NOT EXISTS name_lists (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           kind TEXT NOT NULL,
           value BLOB,
           remark BLOB,
           created_at TEXT NOT NULL
       )""",
    "CREATE INDEX IF NOT EXISTS idx_name_lists_kind ON name_lists(kind)",
    """CREATE TABLE IF NOT EXISTS aliases (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           match_field TEXT NOT NULL,
           value BLOB,
           alias BLOB,
           remark BLOB
       )""",
    """CREATE TABLE IF NOT EXISTS schedule (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           start_time TEXT NOT NULL,
           end_time TEXT NOT NULL,
           mode TEXT NOT NULL,
           days INTEGER NOT NULL DEFAULT 127,
           enabled INTEGER NOT NULL DEFAULT 1,
           remark BLOB
       )""",
    """CREATE TABLE IF NOT EXISTS settings_kv (
           key TEXT PRIMARY KEY,
           value BLOB
       )""",
    """CREATE TABLE IF NOT EXISTS file_marks (
           device_key TEXT NOT NULL DEFAULT '',
           source_key TEXT NOT NULL,
           source_path BLOB,
           size INTEGER NOT NULL DEFAULT 0,
           mod_unix INTEGER NOT NULL DEFAULT 0,
           dest_path BLOB,
           copied_at TEXT NOT NULL,
           PRIMARY KEY (device_key, source_key)
       )""",
    "CREATE INDEX IF NOT EXISTS idx_marks_device ON file_marks(device_key)",
    """CREATE TABLE IF NOT EXISTS device_folders (
           device_key TEXT PRIMARY KEY,
           folder TEXT NOT NULL,
           updated_at TEXT NOT NULL
       )""",
]

SETTINGS_KEY = "settings"


class StoreError(Exception):
    pass


class Store:
    """加密数据库句柄。所有方法都可在多线程下调用。"""

    def __init__(self, db_path: str, dek: bytes, tmp_dir: str = "") -> None:
        if len(dek) != 32:
            raise StoreError("数据密钥长度不正确")
        self.path = db_path
        self.tmp_dir = tmp_dir
        self._dek = bytes(dek)
        self._blind_key = hmac.new(self._dek, BLIND_LABEL, hashlib.sha256).digest()
        self._lock = threading.RLock()
        try:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        except OSError:
            pass
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=1")
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)
            self._migrate_marks()
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def _migrate_marks(self) -> None:
        """老版本的增量索引没有 device_key（不同设备会互相干扰），直接重建。"""
        try:
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(file_marks)")}
        except Exception:
            return
        if "device_key" in columns:
            return
        self._conn.execute("DROP TABLE IF EXISTS file_marks")
        for stmt in _SCHEMA:
            if "file_marks" in stmt:
                self._conn.execute(stmt)

    # -- 生命周期 --------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass

    @staticmethod
    def is_compatible(db_path: str) -> bool:
        """判断已有数据库文件是否为本程序可用的格式。"""
        if not os.path.exists(db_path):
            return True
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
        except Exception:
            return False
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            names = {row[0] for row in rows}
            return "meta" in names and "records" in names
        except Exception:
            return False
        finally:
            conn.close()

    # -- 加解密 ----------------------------------------------------------

    def _seal(self, text: str) -> Optional[bytes]:
        return aead.seal_text(self._dek, text, FIELD_AAD)

    def _open(self, blob) -> str:
        return aead.open_text(self._dek, blob, FIELD_AAD)

    def _blind(self, value: str) -> str:
        return hmac.new(
            self._blind_key, value.strip().lower().encode("utf-8"), hashlib.sha256
        ).hexdigest()

    # -- 监控记录 --------------------------------------------------------

    def insert_record(self, record: M.Record) -> int:
        dev = record.device
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO records (
                       time, event, action, files, byte_count, elapsed, note, dest,
                       letter, name, bus_type, model, vendor, vid, pid, usb_serial,
                       disk_serial, fs, capacity, free_bytes, physical_drive,
                       device_instance, volume_guid, device_path, is_removable)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.time or M.now_rfc3339(), record.event, record.action,
                    int(record.files), int(record.bytes_copied), int(record.elapsed),
                    self._seal(record.note), self._seal(record.dest),
                    self._seal(dev.letter), self._seal(dev.name), self._seal(dev.bus_type),
                    self._seal(dev.model), self._seal(dev.vendor), self._seal(dev.vid),
                    self._seal(dev.pid), self._seal(dev.usb_serial), self._seal(dev.disk_serial),
                    self._seal(dev.fs), int(dev.capacity), int(dev.free),
                    int(dev.physical_drive), self._seal(dev.device_instance),
                    self._seal(dev.volume_guid), self._seal(dev.device_path),
                    1 if dev.is_removable else 0,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    _RECORD_COLUMNS = (
        "id, time, event, action, files, byte_count, elapsed, note, dest, letter, name, "
        "bus_type, model, vendor, vid, pid, usb_serial, disk_serial, fs, capacity, "
        "free_bytes, physical_drive, device_instance, volume_guid, device_path, is_removable"
    )

    def _row_to_record(self, row) -> M.Record:
        dev = M.DeviceInfo(
            letter=self._open(row[9]),
            name=self._open(row[10]),
            bus_type=self._open(row[11]),
            model=self._open(row[12]),
            vendor=self._open(row[13]),
            vid=self._open(row[14]),
            pid=self._open(row[15]),
            usb_serial=self._open(row[16]),
            disk_serial=self._open(row[17]),
            fs=self._open(row[18]),
            capacity=int(row[19] or 0),
            free=int(row[20] or 0),
            physical_drive=int(row[21] if row[21] is not None else -1),
            device_instance=self._open(row[22]),
            volume_guid=self._open(row[23]),
            device_path=self._open(row[24]),
            is_removable=bool(row[25]),
        )
        return M.Record(
            id=int(row[0]),
            time=row[1],
            event=row[2],
            action=row[3],
            files=int(row[4] or 0),
            bytes_copied=int(row[5] or 0),
            elapsed=int(row[6] or 0),
            note=self._open(row[7]),
            dest=self._open(row[8]),
            device=dev,
        )

    def query_records(self, keyword: str = "", field: str = "", event: str = "",
                      since: str = "", page: int = 1, page_size: int = 100):
        """返回 (rows, total)。关键词过滤在解密后进行（记录量级很小）。"""
        clauses: List[str] = []
        params: List[object] = []
        if event:
            clauses.append("event = ?")
            params.append(event)
        if since:
            clauses.append("time >= ?")
            params.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = "SELECT %s FROM records%s ORDER BY time DESC, id DESC LIMIT %d" % (
            self._RECORD_COLUMNS, where, MAX_SCAN_ROWS,
        )
        with self._lock:
            raw = self._conn.execute(sql, params).fetchall()
        rows = [self._row_to_record(row) for row in raw]
        needle = (keyword or "").strip().lower()
        if needle:
            rows = [r for r in rows if self._match_keyword(r, needle, field)]
        total = len(rows)
        page = max(1, int(page or 1))
        size = max(1, min(500, int(page_size or 100)))
        start = (page - 1) * size
        return rows[start:start + size], total

    @staticmethod
    def _match_keyword(record: M.Record, needle: str, field: str) -> bool:
        dev = record.device
        fields = {
            "model": [dev.model],
            "volume": [dev.name],
            "vid": [dev.vid, dev.vid_pid],
            "pid": [dev.pid, dev.vid_pid],
            "serial": [dev.usb_serial, dev.disk_serial],
            "letter": [dev.letter],
        }
        if field and field in fields:
            values = fields[field]
        else:
            values = [
                record.time, record.note, record.dest, dev.letter, dev.name, dev.bus_type,
                dev.model, dev.vendor, dev.vid, dev.pid, dev.usb_serial, dev.disk_serial,
                dev.fs, dev.device_instance, dev.volume_guid, dev.device_path,
            ]
        return any(needle in (value or "").lower() for value in values)

    def count_records(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM records").fetchone()
        return int(row[0] if row else 0)

    def delete_records_before(self, since: str = "") -> int:
        with self._lock:
            if since:
                cur = self._conn.execute("DELETE FROM records WHERE time < ?", (since,))
            else:
                cur = self._conn.execute("DELETE FROM records")
            self._conn.commit()
        return int(cur.rowcount or 0)

    # -- 排除规则 --------------------------------------------------------

    def list_exclude_rules(self) -> List[M.ExcludeRule]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, rule_type, value, remark, created_at FROM exclude_rules ORDER BY id"
            ).fetchall()
        return [
            M.ExcludeRule(id=int(r[0]), type=r[1], value=self._open(r[2]),
                          remark=self._open(r[3]), created_at=r[4])
            for r in rows
        ]

    def add_exclude_rule(self, rule: M.ExcludeRule) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO exclude_rules (rule_type, value, remark, created_at) VALUES (?,?,?,?)",
                (rule.type, self._seal(rule.value), self._seal(rule.remark),
                 rule.created_at or M.now_rfc3339()),
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    def delete_exclude_rule(self, rule_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM exclude_rules WHERE id = ?", (int(rule_id),))
            self._conn.commit()

    def clear_exclude_rules(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM exclude_rules")
            self._conn.commit()

    # -- 名单 ------------------------------------------------------------

    def list_lists(self, kind: str = "") -> List[M.NameList]:
        sql = "SELECT id, kind, value, remark, created_at FROM name_lists"
        params: Sequence[object] = ()
        if kind:
            sql += " WHERE kind = ?"
            params = (kind,)
        sql += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            M.NameList(id=int(r[0]), kind=r[1], value=self._open(r[2]),
                       remark=self._open(r[3]), created_at=r[4])
            for r in rows
        ]

    def add_list(self, kind: str, value: str, remark: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO name_lists (kind, value, remark, created_at) VALUES (?,?,?,?)",
                (kind, self._seal(value), self._seal(remark), M.now_rfc3339()),
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    def delete_list(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM name_lists WHERE id = ?", (int(item_id),))
            self._conn.commit()

    def clear_lists(self, kind: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM name_lists WHERE kind = ?", (kind,))
            self._conn.commit()

    # -- 别名 ------------------------------------------------------------

    def list_aliases(self) -> List[M.Alias]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, match_field, value, alias, remark FROM aliases ORDER BY id"
            ).fetchall()
        return [
            M.Alias(id=int(r[0]), match=r[1], value=self._open(r[2]),
                    alias=self._open(r[3]), remark=self._open(r[4]))
            for r in rows
        ]

    def upsert_alias(self, alias: M.Alias) -> int:
        with self._lock:
            if alias.id:
                self._conn.execute(
                    "UPDATE aliases SET match_field=?, value=?, alias=?, remark=? WHERE id=?",
                    (alias.match, self._seal(alias.value), self._seal(alias.alias),
                     self._seal(alias.remark), int(alias.id)),
                )
                self._conn.commit()
                return int(alias.id)
            cur = self._conn.execute(
                "INSERT INTO aliases (match_field, value, alias, remark) VALUES (?,?,?,?)",
                (alias.match, self._seal(alias.value), self._seal(alias.alias),
                 self._seal(alias.remark)),
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    def delete_alias(self, alias_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM aliases WHERE id = ?", (int(alias_id),))
            self._conn.commit()

    # -- 定时计划 --------------------------------------------------------

    def list_schedule(self) -> List[M.ScheduleSlot]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, start_time, end_time, mode, days, enabled, remark "
                "FROM schedule ORDER BY start_time, id"
            ).fetchall()
        return [
            M.ScheduleSlot(id=int(r[0]), start=r[1], end=r[2], mode=r[3],
                           days=int(r[4]), enabled=bool(r[5]), remark=self._open(r[6]))
            for r in rows
        ]

    def add_schedule(self, slot: M.ScheduleSlot) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO schedule (start_time, end_time, mode, days, enabled, remark) "
                "VALUES (?,?,?,?,?,?)",
                (slot.start, slot.end, slot.mode, int(slot.days), 1 if slot.enabled else 0,
                 self._seal(slot.remark)),
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    def update_schedule(self, slot: M.ScheduleSlot) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE schedule SET start_time=?, end_time=?, mode=?, days=?, enabled=?, "
                "remark=? WHERE id=?",
                (slot.start, slot.end, slot.mode, int(slot.days),
                 1 if slot.enabled else 0, self._seal(slot.remark), int(slot.id)),
            )
            self._conn.commit()

    def delete_schedule(self, slot_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM schedule WHERE id = ?", (int(slot_id),))
            self._conn.commit()

    def clear_schedule(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM schedule")
            self._conn.commit()

    # -- 设置 ------------------------------------------------------------

    def load_settings(self) -> M.Settings:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings_kv WHERE key = ?", (SETTINGS_KEY,)
            ).fetchone()
        if not row or not row[0]:
            return M.Settings()
        raw = self._open(row[0])
        if not raw:
            return M.Settings()
        try:
            return M.Settings.from_dict(json.loads(raw))
        except Exception:
            return M.Settings()

    def save_settings(self, settings: M.Settings) -> None:
        payload = json.dumps(settings.to_dict(), ensure_ascii=False).encode("utf-8")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO settings_kv (key, value) VALUES (?, ?)",
                (SETTINGS_KEY, aead.seal(self._dek, payload, FIELD_AAD)),
            )
            self._conn.commit()

    # -- 增量索引 --------------------------------------------------------

    def _mark_key(self, device_key: str, source_path: str) -> str:
        """增量索引的盲索引键：**按设备隔离**，不同设备不会互相干扰。"""
        return self._blind("%s|%s" % (device_key or "", source_path.strip().lower()))

    def get_mark(self, source_path: str, device_key: str = ""):
        key = self._mark_key(device_key, source_path)
        with self._lock:
            row = self._conn.execute(
                "SELECT size, mod_unix, dest_path FROM file_marks "
                "WHERE device_key = ? AND source_key = ?",
                (device_key or "", key),
            ).fetchone()
        if not row:
            return None
        return {"size": int(row[0] or 0), "mod_unix": int(row[1] or 0),
                "dest_path": self._open(row[2])}

    def put_mark(self, source_path: str, size: int, mod_unix: int, dest_path: str,
                 device_key: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO file_marks "
                "(device_key, source_key, source_path, size, mod_unix, dest_path, copied_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (device_key or "", self._mark_key(device_key, source_path),
                 self._seal(source_path), int(size), int(mod_unix),
                 self._seal(dest_path), M.now_rfc3339()),
            )
            self._conn.commit()

    def delete_marks_for_device(self, device_key: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM file_marks WHERE device_key = ?", (device_key or "",))
            self._conn.commit()
        return int(cur.rowcount or 0)

    # -- 每台设备最近一次使用的复制文件夹名 --------------------------------
    #
    # 别名新增 / 修改 / 删除后要靠它知道"原来用的是哪个文件夹"，
    # 这样 <原名>_<旧别名> → <原名>_<新别名>、<原名>_<别名> → <原名> 都能改名而不是新建。

    def get_device_folder(self, device_key: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT folder FROM device_folders WHERE device_key = ?",
                (device_key or "",)).fetchone()
        return (row[0] if row else "") or ""

    def set_device_folder(self, device_key: str, folder: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO device_folders (device_key, folder, updated_at) "
                "VALUES (?,?,?)",
                (device_key or "", folder or "", M.now_rfc3339()))
            self._conn.commit()

    def count_marks(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM file_marks").fetchone()
        return int(row[0] if row else 0)

    def clear_marks(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM file_marks")
            self._conn.commit()
        return int(cur.rowcount or 0)

    # -- 统计 ------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        with self._lock:
            def count(table: str) -> int:
                try:
                    return int(self._conn.execute(
                        "SELECT COUNT(*) FROM %s" % table).fetchone()[0])
                except Exception:
                    return 0
            return {
                "records": count("records"),
                "exclude_rules": count("exclude_rules"),
                "aliases": count("aliases"),
                "marks": count("file_marks"),
                "schedule": count("schedule"),
            }

    def export_records_csv(self, dest_path: str, since: str = "") -> int:
        """导出全部记录为 CSV（UTF-8 BOM，Excel 打开中文不乱码）。"""
        import csv

        rows, _ = self.query_records(since=since, page=1, page_size=MAX_SCAN_ROWS)
        headers = [
            "时间", "事件", "处理结果", "盘符", "卷标", "总线类型", "型号", "厂商",
            "VID", "PID", "USB 序列号", "磁盘序列号", "文件系统", "总容量", "剩余容量",
            "物理磁盘号", "设备实例 ID", "卷 GUID", "设备路径", "是否可移动",
            "复制文件数", "复制字节数", "耗时(毫秒)", "目标目录", "备注",
        ]
        with open(fileutil.long_path(dest_path), "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            for rec in rows:
                dev = rec.device
                writer.writerow([
                    rec.time, M.Event.label(rec.event), M.Action.label(rec.action),
                    dev.letter, dev.name, dev.bus_type, dev.model, dev.vendor, dev.vid,
                    dev.pid, dev.usb_serial, dev.disk_serial, dev.fs,
                    fileutil.human_size(dev.capacity), fileutil.human_size(dev.free),
                    dev.physical_drive, dev.device_instance, dev.volume_guid,
                    dev.device_path, "是" if dev.is_removable else "否",
                    rec.files, rec.bytes_copied, rec.elapsed, rec.dest, rec.note,
                ])
        return len(rows)


def archive_legacy_database(dirs) -> Optional[str]:
    """把旧版（Go）数据库移到 ``legacy-go`` 子目录，返回归档目录或 None。

    绝不删除用户数据：只是改名搬走，需要的时候还能用旧版程序打开。
    """
    db = dirs.db_file
    if not os.path.exists(db) or Store.is_compatible(db):
        return None
    target = dirs.legacy_dir
    os.makedirs(fileutil.long_path(target), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        src = db + suffix
        if os.path.exists(src):
            try:
                os.replace(fileutil.long_path(src),
                           fileutil.long_path(os.path.join(target, "vault-%s.db%s" % (stamp, suffix))))
            except Exception:
                pass
    return target
