"""自动验收：用真实代码路径跑一遍完整流程（不需要界面）。

覆盖：路径与可写性、密钥库（初始化 / 错密码 / 解锁 / 改密码 / 恢复码重置 / 冷却锁定）、
加密存储（往返、关键词搜索、分页、导出、明文泄漏检查）、按天日志、
增量复制（过滤、跳过、重名策略、空间保护）、设备实例 ID 解析、定时切换逻辑、
业务编排（模式 / 排除 / 别名 / 记录）。

结果写入 ``<数据目录>\\autotest.log``，控制台输出 PASS/FAIL 汇总。
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from securevault.core import fileutil, paths  # noqa: E402
from securevault.core import model as M  # noqa: E402
from securevault.copy.copier import CopyEngine, resolve_dest_dir  # noqa: E402
from securevault.copy.engine import Engine  # noqa: E402
from securevault.crypto import aead, keystore  # noqa: E402
from securevault.storage.logger import Logger  # noqa: E402
from securevault.storage.store import Store  # noqa: E402
from securevault.system import devices  # noqa: E402
from securevault.system import single_instance  # noqa: E402


def _moment(year, month, day, hour, minute):
    """构造一个用于定时切换判断的时间点。"""
    return datetime(year, month, day, hour, minute, 0)


class Runner:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.lines = []
        self.started = time.time()

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            self.record("PASS", name)
        else:
            self.failed += 1
            self.record("FAIL", name + ("  <- " + detail if detail else ""))
        return bool(condition)

    def record(self, level: str, text: str) -> None:
        line = "%s [%-4s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), level, text)
        self.lines.append(line)
        print(line)

    def section(self, title: str) -> None:
        self.record("----", title)


def run_selftest(dirs=None, verbose: bool = True) -> int:
    runner = Runner()
    dirs = dirs or paths.resolve(dev=True)
    dirs.ensure()
    work = tempfile.mkdtemp(prefix="sv-selftest-", dir=dirs.tmp)

    try:
        _test_paths(runner, dirs)
        _test_crypto(runner)
        _test_keystore(runner, dirs, work)
        _test_store(runner, dirs, work)
        _test_logger(runner, dirs)
        _test_copier(runner, work)
        _test_devices(runner)
        _test_engine(runner, dirs)
        _test_single_instance(runner)
        _test_update(runner)
        _test_touch(runner)
    except Exception:
        runner.record("FAIL", "自检过程中出现异常：\n" + traceback.format_exc())
        runner.failed += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)

    total = runner.passed + runner.failed
    summary = "自检完成：%d 项通过，%d 项失败（共 %d 项，耗时 %.1f 秒）" % (
        runner.passed, runner.failed, total, time.time() - runner.started)
    runner.record("----", summary)
    try:
        with open(os.path.join(dirs.root, "autotest.log"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(runner.lines) + "\n")
    except OSError:
        pass
    print(summary)
    return 0 if runner.failed == 0 else 1


# ---------------------------------------------------------------------------

def _test_paths(runner: Runner, dirs) -> None:
    runner.section("路径与工具函数")
    runner.check("数据目录位于程序目录之下",
                 dirs.root.startswith(dirs.exe_dir), dirs.root)
    runner.check("数据目录可写", os.path.isdir(dirs.tmp))
    runner.check("文件名净化：非法字符", fileutil.sanitize_name('a<b>c:d') == "a_b_c_d")
    runner.check("文件名净化：保留名", fileutil.sanitize_name("CON") == "CON_")
    runner.check("文件名净化：空值兜底", fileutil.sanitize_name("   ") == "Unknown")
    runner.check("通配符 * 匹配", fileutil.match_name("*.tmp", "a.TMP"))
    runner.check("通配符 ? 匹配", fileutil.match_name("file?.txt", "file1.txt"))
    runner.check("通配符不误报", not fileutil.match_name("*.tmp", "a.txt"))
    runner.check("扩展名归一化", fileutil.normalize_ext("*.TXT") == ".txt")
    runner.check("容量格式化", fileutil.human_size(1024 * 1024) == "1.00 MB")
    runner.check("长路径前缀", fileutil.long_path(r"D:\a\b").startswith("\\\\?\\"))
    runner.check("原子写", _atomic_write_ok(dirs))
    runner.check("剩余空间读取", fileutil.free_space(dirs.root) > 0)


def _test_single_instance(runner: Runner) -> None:
    """单实例互斥体：第一个实例能启动，第二个被拦下。"""
    runner.section("单实例保护")
    first = single_instance.acquire()
    second = single_instance.acquire()
    runner.check("第一个实例可以启动",
                 first, "已有 SecureVault 在运行？（互斥体被占用）")
    runner.check("第二个实例会被拦下（不会开出两个窗口）", not second)


def _atomic_write_ok(dirs) -> bool:
    path = os.path.join(dirs.tmp, "atomic-test.bin")
    fileutil.write_atomic(path, b"hello", dirs.tmp)
    ok = os.path.exists(path) and open(path, "rb").read() == b"hello"
    os.remove(path)
    return ok


def _test_crypto(runner: Runner) -> None:
    runner.section("AES-256-GCM / 密钥派生")
    runner.check("存在可用的 AES-GCM 实现", aead.available(), aead.backend_name())
    key = os.urandom(32)
    blob = aead.seal(key, b"secret-data", b"aad")
    runner.check("加解密往返", aead.open_(key, blob, b"aad") == b"secret-data")
    runner.check("错误 AAD 解密失败", _fails(lambda: aead.open_(key, blob, b"bad")))
    other = os.urandom(32)
    runner.check("错误密钥解密失败", _fails(lambda: aead.open_(other, blob, b"aad")))
    runner.check("每次 nonce 都不同", aead.seal(key, b"x")[:12] != aead.seal(key, b"x")[:12])
    runner.check("密文包含 nonce 与 tag", len(blob) == 12 + len(b"secret-data") + 16)
    _test_cng_fallback(runner, key)


def _test_cng_fallback(runner: Runner, key: bytes) -> None:
    """cryptography 缺失时会退回 Windows CNG，布局必须完全一致。"""
    original = aead._backend
    try:
        aead._backend = "cng"
        blob = aead.seal(key, b"cng-backend-test", b"aad")
        runner.check("CNG 后端加解密往返",
                     aead.open_(key, blob, b"aad") == b"cng-backend-test")
        runner.check("CNG 后端布局一致（nonce12+tag16）",
                     len(blob) == 12 + len(b"cng-backend-test") + 16)
        if aead._AESGCM is not None:
            aead._backend = "cryptography"
            runner.check("两种后端可以互相解密",
                         aead.open_(key, blob, b"aad") == b"cng-backend-test")
        else:
            runner.check("打包版只用 CNG 也能解密自己写的密文",
                         aead.open_(key, blob, b"aad") == b"cng-backend-test")
    except Exception as exc:
        runner.check("CNG 后端可用", False, str(exc))
    finally:
        aead._backend = original


def _fails(callback) -> bool:
    try:
        callback()
    except Exception:
        return True
    return False


def _test_keystore(runner: Runner, dirs, work: str) -> None:
    runner.section("密钥库（密码 / 恢复码）")
    sandbox = paths.Dirs(work, "ks-test")
    sandbox.ensure()
    store = keystore.KeyStore(sandbox, iterations=1000)
    runner.check("初始状态为未设置", store.status().state == keystore.STATE_UNINITIALIZED)
    runner.check("密码太短被拒绝", _fails(lambda: store.initialize("short")))
    code = store.initialize("Passw0rd!2026")
    runner.check("初始化返回恢复码", len(keystore.normalize_recovery(code)) == 12, code)
    runner.check("初始化后即解锁", store.status().state == keystore.STATE_UNLOCKED)
    dek1 = store.dek()
    runner.check("DEK 长度 32", len(dek1) == 32)
    store.lock()
    runner.check("上锁后状态为已锁定", store.status().state == keystore.STATE_LOCKED)
    runner.check("上锁后取 DEK 报错", _fails(store.dek))
    runner.check("错误密码被拒绝", _fails(lambda: store.unlock("wrong-password")))
    store.unlock("Passw0rd!2026")
    runner.check("正确密码解锁", store.status().state == keystore.STATE_UNLOCKED)
    runner.check("解锁后 DEK 一致", store.dek() == dek1)
    store.change_password("Passw0rd!2026", "NewPassw0rd!2026")
    store.lock()
    runner.check("旧密码失效", _fails(lambda: store.unlock("Passw0rd!2026")))
    store.unlock("NewPassw0rd!2026")
    runner.check("新密码可用", store.status().state == keystore.STATE_UNLOCKED)
    runner.check("改密码后 DEK 不变", store.dek() == dek1)
    store.lock()
    store.unlock_with_recovery(code.lower().replace("-", " "))
    runner.check("恢复码忽略大小写与横线", store.status().state == keystore.STATE_UNLOCKED)
    new_code = store.reset_with_recovery(code, "ResetPassw0rd!")
    runner.check("重置后轮换恢复码", keystore.normalize_recovery(new_code)
                 != keystore.normalize_recovery(code))
    runner.check("重置后仍可解锁", store.status().state == keystore.STATE_UNLOCKED)
    rotated = store.rotate_recovery("ResetPassw0rd!")
    runner.check("重新生成恢复码", len(keystore.normalize_recovery(rotated)) == 12)
    runner.check("旧恢复码失效", _fails(lambda: _recover(sandbox, new_code)))
    # 失败冷却
    store.lock()
    bad = 0
    for _ in range(6):
        try:
            store.unlock("nope")
        except keystore.LockedOut:
            bad = 1
            break
        except Exception:
            pass
    runner.check("连续 5 次失败后进入冷却", bad == 1)
    status = keystore.KeyStore(sandbox, iterations=1000).status()
    runner.check("封禁时长是 5 分钟（300 秒）",
                 240 <= status.lockout_remaining <= 300, status.lockout_remaining)
    runner.check("提示策略：第 1~2 次只显示密码错误",
                 _failure_message(1) == "密码错误" and _failure_message(2) == "密码错误",
                 "%s / %s" % (_failure_message(1), _failure_message(2)))
    runner.check("提示策略：第 3 次开始显示剩余次数 + 封禁时长",
                 "还剩 2 次" in _failure_message(3) and "5 分钟" in _failure_message(3),
                 _failure_message(3))
    runner.check("提示策略：还剩 1 次也显示次数 + 封禁时长",
                 "还剩 1 次" in _failure_message(4) and "5 分钟" in _failure_message(4),
                 _failure_message(4))
    runner.check("冷却状态被持久化",
                 keystore.KeyStore(sandbox, iterations=1000).status().lockout_remaining > 0)
    file_mode = os.path.exists(sandbox.keystore_file)
    runner.check("密钥库文件已写入", file_mode)
    with open(sandbox.keystore_file, "r", encoding="utf-8") as fh:
        raw = fh.read()
    runner.check("密钥库里没有明文密码", "ResetPassw0rd!" not in raw)
    runner.check("密钥库里没有明文恢复码",
                 keystore.normalize_recovery(rotated) not in raw.replace("-", ""))


def _failure_message(fail_count: int) -> str:
    """按指定的失败次数生成提示（复刻 KeyStore.Status.failure_message 的策略）。"""
    status = keystore.Status(state=keystore.STATE_LOCKED, fail_count=fail_count)
    return status.failure_message()


def _recover(sandbox, code: str) -> None:
    fresh = keystore.KeyStore(sandbox, iterations=1000)
    fresh.unlock_with_recovery(code)


def _test_store(runner: Runner, dirs, work: str) -> None:
    runner.section("加密数据库")
    db_path = os.path.join(work, "vault.db")
    dek = os.urandom(32)
    store = Store(db_path, dek, work)
    record = _sample_record()
    record_id = store.insert_record(record)
    runner.check("插入记录返回 id", record_id > 0)
    rows, total = store.query_records()
    runner.check("查询到 1 条记录", total == 1 and len(rows) == 1)
    got = rows[0]
    runner.check("设备序列号往返一致", got.device.disk_serial == "SERIAL-12345")
    runner.check("卷标往返一致", got.device.name == "我的U盘")
    runner.check("备注往返一致", got.note == "测试备注")
    runner.check("容量往返一致", got.device.capacity == 123456789)
    runner.check("按型号关键词搜索", store.query_records(keyword="kingston")[1] == 1)
    runner.check("按盘符字段搜索", store.query_records(keyword="E:", field="letter")[1] == 1)
    runner.check("不匹配的关键词返回 0 条", store.query_records(keyword="zzz")[1] == 0)
    runner.check("事件过滤", store.query_records(event=M.Event.ARRIVAL)[1] == 1)
    runner.check("移除事件过滤", store.query_records(event=M.Event.REMOVAL)[1] == 0)
    for index in range(5):
        item = _sample_record()
        item.note = "批量-%d" % index
        store.insert_record(item)
    runner.check("分页每页 2 条", len(store.query_records(page=1, page_size=2)[0]) == 2)
    _, page_total = store.query_records(page=1, page_size=2)
    runner.check("分页总数正确", page_total == 6, str(page_total))
    runner.check("日期过滤（未来时间）",
                 store.query_records(since="2999-01-01T00:00:00")[1] == 0)
    # 规则 / 名单 / 别名 / 计划 / 设置
    store.add_exclude_rule(M.ExcludeRule(type="vid", value="1234", remark="r"))
    runner.check("排除规则往返", store.list_exclude_rules()[0].value == "1234")
    store.add_list("excludeExt", ".tmp")
    runner.check("名单往返", store.list_lists()[0].value == ".tmp")
    store.upsert_alias(M.Alias(match="volume", value="U", alias="我的盘"))
    runner.check("别名往返", store.list_aliases()[0].alias == "我的盘")
    store.add_schedule(M.ScheduleSlot(start="22:00", end="06:00", mode=M.Mode.COPY))
    runner.check("定时计划往返", store.list_schedule()[0].end == "06:00")
    settings = M.Settings(copy_dest="D:\\dest", log_retention=30)
    store.save_settings(settings)
    runner.check("设置往返", store.load_settings().copy_dest == "D:\\dest")
    runner.check("日志保留天数往返", store.load_settings().log_retention == 30)
    # 增量索引
    store.put_mark("E:\\a.txt", 10, 1000, "D:\\dest\\a.txt")
    mark = store.get_mark("E:\\a.txt")
    runner.check("增量索引命中", mark is not None and mark["size"] == 10)
    runner.check("增量索引大小写不敏感", store.get_mark("e:\\A.TXT") is not None)
    runner.check("增量索引未命中", store.get_mark("E:\\b.txt") is None)
    # 增量索引必须按设备隔离：同一个路径在不同设备上互不影响
    runner.check("同一路径 + 另一个设备查不到索引",
                 store.get_mark("E:\\a.txt", "sn:OTHER-DISK") is None)
    store.put_mark("E:\\a.txt", 10, 1000, "D:\\dest\\a.txt", "sn:OTHER-DISK")
    runner.check("不同设备各自有独立索引",
                 store.get_mark("E:\\a.txt", "sn:OTHER-DISK") is not None
                 and store.get_mark("E:\\a.txt") is not None)
    runner.check("按设备删除索引",
                 store.delete_marks_for_device("sn:OTHER-DISK") == 1
                 and store.get_mark("E:\\a.txt", "sn:OTHER-DISK") is None)
    # CSV
    csv_path = os.path.join(work, "out.csv")
    count = store.export_records_csv(csv_path)
    with open(csv_path, "rb") as fh:
        head = fh.read(3)
    runner.check("导出 CSV 条数", count == 6, str(count))
    runner.check("CSV 带 UTF-8 BOM", head == b"\xef\xbb\xbf")
    # 明文泄漏检查
    store.close()
    with open(db_path, "rb") as fh:
        raw = fh.read()
    runner.check("数据库里搜不到明文序列号", b"SERIAL-12345" not in raw)
    runner.check("数据库里搜不到明文卷标",
                 "我的U盘".encode("utf-8") not in raw)
    runner.check("数据库文件可被识别为本程序格式", Store.is_compatible(db_path))
    store = Store(db_path, os.urandom(32), work)
    rows, _total = store.query_records()
    runner.check("用错误 DEK 读取会得到空文本",
                 rows and rows[0].device.name == "")
    store.close()


def _sample_record() -> M.Record:
    return M.Record(
        time=M.now_rfc3339(), event=M.Event.ARRIVAL, action=M.Action.MONITOR,
        note="测试备注", files=3, bytes_copied=1024, dest="D:\\dest", elapsed=120,
        device=M.DeviceInfo(
            letter="E:", name="我的U盘", bus_type="USB", model="Kingston DataTraveler",
            vendor="Kingston", vid="0951", pid="1666", usb_serial="USB-SN-9",
            disk_serial="SERIAL-12345", fs="exFAT", capacity=123456789, free=1000,
            physical_drive=2, device_instance="USB\\VID_0951&PID_1666\\SN9",
            volume_guid="\\\\?\\Volume{test}\\", device_path="\\\\.\\E:",
            is_removable=True, system_time=M.now_rfc3339()),
    )


def _test_logger(runner: Runner, dirs) -> None:
    runner.section("按天日志")
    logger = Logger(dirs, 7)
    logger.info("自检日志 %s", "第一条")
    logger.warn("自检日志第二条")
    day = time.strftime("%Y-%m-%d")
    path = dirs.log_file(day)
    runner.check("日志文件按天创建", os.path.exists(path))
    content = logger.read(day, 0)
    runner.check("日志内容可读", "自检日志" in content)
    runner.check("日志行格式含级别与时间",
                 "[INFO ]" in content and day in content)
    files = logger.files()
    runner.check("日志列表包含今天", any(item.day == day for item in files))
    logger.set_retention(30)
    runner.check("保留天数生效", logger.retention == 30)
    logger.close()


def _test_copier(runner: Runner, work: str) -> None:
    runner.section("增量复制引擎")
    src = os.path.join(work, "src")
    dest = os.path.join(work, "dest")
    store = Store(os.path.join(work, "marks.db"), os.urandom(32), work)
    os.makedirs(os.path.join(src, "sub"), exist_ok=True)
    os.makedirs(os.path.join(src, "skipme"), exist_ok=True)
    _write(os.path.join(src, "a.txt"), "hello")
    _write(os.path.join(src, "sub", "b.txt"), "world" * 100)
    _write(os.path.join(src, "c.tmp"), "temp")
    _write(os.path.join(src, "skipme", "d.txt"), "hidden")
    _write(os.path.join(src, "empty.bin"), "")

    flt = M.FileFilter(exclude_names=["skipme"], exclude_exts=[".tmp"],
                       skip_zero_byte=True)
    engine = CopyEngine(store, workers=2)
    result = engine.copy(src, dest, "U盘", flt)
    runner.check("复制没有错误", not result.errors, "; ".join(result.errors[:2]))
    runner.check("只复制符合条件的文件", result.stats.copied == 2,
                 str(result.stats.copied))
    runner.check("目标目录使用子文件夹名",
                 os.path.isfile(os.path.join(dest, "U盘", "a.txt")))
    runner.check("保留目录结构",
                 os.path.isfile(os.path.join(dest, "U盘", "sub", "b.txt")))
    runner.check("排除的扩展名未复制",
                 not os.path.exists(os.path.join(dest, "U盘", "c.tmp")))
    runner.check("排除的文件夹未复制",
                 not os.path.exists(os.path.join(dest, "U盘", "skipme")))
    runner.check("0 字节文件被跳过",
                 not os.path.exists(os.path.join(dest, "U盘", "empty.bin")))

    engine2 = CopyEngine(store, workers=1)
    again = engine2.copy(src, dest, "U盘", flt)
    runner.check("再次复制无增量（全部跳过）",
                 again.stats.copied == 0 and again.stats.skipped == 2,
                 "%d/%d" % (again.stats.copied, again.stats.skipped))

    _write(os.path.join(src, "a.txt"), "hello world（已修改）")
    engine3 = CopyEngine(store, workers=1)
    changed = engine3.copy(src, dest, "U盘", flt)
    runner.check("修改后的文件被重新复制", changed.stats.copied == 1,
                 str(changed.stats.copied))
    with open(os.path.join(dest, "U盘", "a.txt"), "r", encoding="utf-8") as fh:
        runner.check("重写后内容正确", fh.read() == "hello world（已修改）")

    renamed = CopyEngine(store, workers=1, conflict="rename")
    result2 = renamed.copy(src, dest, "另一个", flt, incremental=False)
    runner.check("换子目录后照常复制", result2.stats.copied == 2,
                 str(result2.stats.copied))

    only_png = M.FileFilter(include_exts=[".png"], use_include=True)
    runner.check("只复制指定扩展名时不复制 txt",
                 CopyEngine(store, workers=1).copy(
                     src, dest, "png", only_png, incremental=False).stats.copied == 0)

    runner.check("目标目录解析拒绝越界",
                 resolve_dest_dir(dest, "..\\..\\evil") == os.path.abspath(dest))
    runner.check("目标目录解析正常",
                 resolve_dest_dir(dest, "U盘") == os.path.abspath(os.path.join(dest, "U盘")))
    runner.check("源目录不存在时返回错误",
                 CopyEngine().copy(os.path.join(work, "nope"), dest, "x").errors != [])
    files = sum(len(names) for _root, _dirs, names in os.walk(dest))
    runner.check("目标目录里确实有文件", files >= 4, str(files))
    store.close()


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _test_devices(runner: Runner) -> None:
    runner.section("设备解析与探测")
    mask = devices.get_logical_drives()
    runner.check("能读到本机盘符掩码", mask > 0, str(mask))
    letters = devices.drive_mask_to_letters(mask)
    runner.check("盘符掩码换算", all(len(item) == 2 for item in letters), str(letters))
    vid, pid, serial = devices.parse_usb_instance_id(
        "USB\\VID_0951&PID_1666\\0123456789ABCDEF")
    runner.check("解析 VID/PID/序列号",
                 (vid, pid, serial) == ("0951", "1666", "0123456789ABCDEF"),
                 "%s/%s/%s" % (vid, pid, serial))
    vid, pid, serial = devices.parse_usb_instance_id(
        "\\\\?\\usb#vid_abcd&pid_1234#6&2B1A0E4F&0&4#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}")
    runner.check("接口路径形式解析 VID/PID", (vid, pid) == ("ABCD", "1234"))
    runner.check("端口占位串号判空", serial == "", serial)
    runner.check("USBSTOR 串号去掉实例后缀",
                 devices.clean_serial_component("SN123&0") == "SN123")
    runner.check("非 USB 设备返回空", devices.parse_usb_instance_id("SCSI\\DISK&VEN_A")[0] == "")
    runner.check("总线类型：USB", devices.bus_type_from_instance_id("USBSTOR\\DISK") == "USB")
    runner.check("总线类型：NVMe", devices.bus_type_from_instance_id("NVME\\X") == "NVMe")
    runner.check("总线类型：SATA", devices.bus_type_from_instance_id("SCSI\\X") == "SATA")
    runner.check("总线类型码转换", devices.bus_type_name(0x11) == "NVMe")
    snapshot = devices.snapshot()
    runner.check("能枚举到本机磁盘", len(snapshot) >= 1, str(len(snapshot)))
    for letter, info in sorted(snapshot.items()):
        runner.check("盘符 %s 有容量" % letter, info.capacity > 0)
        runner.check("盘符 %s 有文件系统" % letter, bool(info.fs))
        runner.check("盘符 %s 有物理磁盘号" % letter, info.physical_drive >= 0)
        runner.check("盘符 %s 有序列号" % letter,
                     bool(info.disk_serial or info.usb_serial))


def _test_engine(runner: Runner, dirs) -> None:
    runner.section("业务编排")
    work = tempfile.mkdtemp(prefix="sv-engine-", dir=dirs.tmp)
    store = Store(os.path.join(work, "engine.db"), os.urandom(32), work)
    logger = Logger(dirs, 14)
    engine = Engine(store=store, logger=logger, dirs=dirs)

    engine.set_manual_mode(M.Mode.MONITOR)
    runner.check("手动模式已保存", store.load_settings().manual_mode == M.Mode.MONITOR)
    device = _sample_record().device
    engine.handle_event("E:", M.Event.ARRIVAL, device)
    rows, total = store.query_records()
    runner.check("监控模式写入一条记录", total == 1, str(total))
    runner.check("记录动作是「仅记录」", rows[0].action == M.Action.MONITOR)

    engine.set_manual_mode(M.Mode.OFF)
    engine.handle_event("E:", M.Event.ARRIVAL, device)
    runner.check("关闭模式不写记录", store.query_records()[1] == 1)

    engine.set_manual_mode(M.Mode.COPY)
    store.add_exclude_rule(M.ExcludeRule(type="diskSerial", value="SERIAL-12345"))
    engine.reload()
    engine.handle_event("E:", M.Event.ARRIVAL, device)
    rows, _total = store.query_records()
    runner.check("命中排除规则时只记录", rows[0].action == M.Action.EXCLUDED)

    runner.check("排除规则匹配磁盘序列号",
                 engine.match_exclude(device) is not None)
    runner.check("排除规则不误命中（U 盘不会被内置硬盘规则命中）",
                 engine.match_exclude(M.DeviceInfo(
                     letter="F:", name="别的盘", bus_type="USB",
                     is_removable=True)) is None)

    store.clear_exclude_rules()
    engine.reload()
    store.upsert_alias(M.Alias(match="volume", value="我的U盘", alias="工作盘"))
    engine.reload()
    runner.check("有别名时文件夹名是 <原名>_<别名>",
                 engine.folder_name(device) == "我的U盘_工作盘",
                 engine.folder_name(device))
    runner.check("没有别名时文件夹名就是原名",
                 engine.folder_name(M.DeviceInfo(letter="F:", name="新盘")) == "新盘")
    runner.check("未知设备使用卷标", engine.folder_name(
        M.DeviceInfo(letter="F:", name="新盘")) == "新盘")
    runner.check("无卷标时用型号", engine.folder_name(
        M.DeviceInfo(letter="F:", model="SanDisk")) == "SanDisk")
    runner.check("都没有时用盘符", engine.folder_name(
        M.DeviceInfo(letter="F:")) == "F_")

    store.add_list("excludeExt", "tmp")
    store.add_list("includeExt", "txt")
    engine.reload()
    flt = engine.build_filter()
    runner.check("过滤器读到排除扩展名", ".tmp" in flt.exclude_exts, str(flt.exclude_exts))
    runner.check("过滤器读到只复制扩展名", flt.use_include)

    # 定时切换
    runner.check("跨午夜时间段命中（22:00-06:00 @23:30）",
                 M.ScheduleSlot(start="22:00", end="06:00").active_at(
                     _moment(2026, 1, 5, 23, 30)))
    runner.check("跨午夜时间段命中（凌晨 2 点）",
                 M.ScheduleSlot(start="22:00", end="06:00").active_at(
                     _moment(2026, 1, 6, 2, 0)))
    runner.check("跨午夜时间段不命中（中午）",
                 not M.ScheduleSlot(start="22:00", end="06:00").active_at(
                     _moment(2026, 1, 6, 12, 0)))
    runner.check("同日时间段命中",
                 M.ScheduleSlot(start="09:00", end="18:00").active_at(
                     _moment(2026, 1, 6, 12, 0)))
    runner.check("按星期生效（仅周一生效，周二不命中）",
                 not M.ScheduleSlot(start="09:00", end="18:00", days=1).active_at(
                     _moment(2026, 1, 6, 12, 0)))
    runner.check("容量表达式 >=", M.match_capacity(">=64GB", 64 * 1024 ** 3))
    runner.check("容量表达式 !>= ", not M.match_capacity(">=64GB", 2 * 1024 ** 3))
    runner.check("容量表达式精确值带容差", M.match_capacity("512MB", 512 * 1024 ** 2))
    runner.check("容量表达式 <=2GB", M.match_capacity("<=2GB", 1024 ** 3))
    runner.check("模式→文件夹名不会带非法字符",
                 fileutil.sanitize_name('我的:盘*') == "我的_盘_")

    runner.check("引擎状态可读", isinstance(engine.state(), dict))
    runner.check("设备标识优先用磁盘序列号",
                 M.DeviceInfo(disk_serial="ABC").device_key() == "sn:ABC")
    runner.check("没有序列号时用卷 GUID",
                 M.DeviceInfo(volume_guid="\\\\?\\Volume{x}\\").device_key()
                 .startswith("vg:"))
    runner.check("同一设备多次计算标识一致",
                 device.device_key() == device.device_key())
    logger.close()
    store.close()
    shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 在线更新（版本比较、地址拼接、解包与覆盖）
# ---------------------------------------------------------------------------

SAMPLE_URL = ("https://github.com/816633/securevault/releases/download/v2.4.1/"
              "SecureVault-2.4.1-win64-portable.zip")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _test_update(runner: Runner) -> None:
    runner.section("在线更新")
    from securevault.system import update

    runner.check("版本号比较：2.4.2 比 2.4.1 新", update.is_newer("v2.4.2", "2.4.1"))
    runner.check("版本号比较：同级不算更新", not update.is_newer("2.4.1", "v2.4.1"))
    runner.check("版本号比较：旧版本不算更新", not update.is_newer("2.3.9", "2.4.1"))
    runner.check("官方源地址与发布页一致",
                 update.download_url("2.4.1", "SecureVault-2.4.1-win64-portable.zip",
                                     "github") == SAMPLE_URL,
                 update.download_url("2.4.1", "SecureVault-2.4.1-win64-portable.zip",
                                     "github"))
    runner.check("CloudFlare 优选 IPv4 源地址正确",
                 update.build_url("v4", SAMPLE_URL)
                 == "https://v4.gh-proxy.org/" + SAMPLE_URL)
    runner.check("CloudFlare 全球源地址正确",
                 update.build_url("global", SAMPLE_URL)
                 == "https://gh-proxy.org/" + SAMPLE_URL)
    runner.check("三个下载源都有名字", len(update.source_options()) == 3)
    runner.check("默认下载源是加速源", update.DEFAULT_SOURCE != "github")

    work = tempfile.mkdtemp(prefix="sv-update-")
    try:
        package = os.path.join(work, "SecureVault-2.4.2-win64-portable.zip")
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("SecureVault/SecureVault.exe", "MZ" + "x" * 8192)
            archive.writestr("SecureVault/_internal/new.dll", "new-dll")
            archive.writestr("SecureVault/assets/securevault.ico", "icon")
            archive.writestr("SecureVault/请先读我.txt", "说明文件")
            archive.writestr("SecureVault/SecureVaultData/vault.db", "不该被覆盖")

        target = os.path.join(work, "target")
        os.makedirs(target)
        _write(os.path.join(target, "SecureVault.exe"), "old-exe")
        _write(os.path.join(target, "_internal", "old.dll"), "old-dll")
        _write(os.path.join(target, "SecureVaultData", "vault.db"), "我的数据")
        _write(os.path.join(target, "user-note.txt"), "用户自己的文件")

        staging, count = update.stage(package, work)
        runner.check("更新包会自动去掉顶层目录", count == 3, str(count))
        runner.check("更新包里有程序本体",
                     os.path.isfile(os.path.join(staging, "SecureVault.exe")))
        runner.check("说明文件不会被解出来",
                     not os.path.exists(os.path.join(staging, "请先读我.txt")))
        runner.check("数据目录不会被解出来",
                     not os.path.exists(os.path.join(staging, "SecureVaultData")))

        result = update.apply_staging(staging, target)
        runner.check("覆盖更新没有失败项", not result["failed"], str(result["failed"][:2]))
        runner.check("程序本体被替换",
                     _read(os.path.join(target, "SecureVault.exe")).startswith("MZ"))
        runner.check("新增文件写进去了",
                     os.path.isfile(os.path.join(target, "_internal", "new.dll")))
        runner.check("原有的其它文件保留",
                     os.path.isfile(os.path.join(target, "_internal", "old.dll")))
        runner.check("用户自己的文件不被动",
                     os.path.isfile(os.path.join(target, "user-note.txt")))
        runner.check("数据目录里的数据库没被动",
                     _read(os.path.join(target, "SecureVaultData", "vault.db"))
                     == "我的数据")

        # 只读 / 被占用的文件：先改名让位，再把新文件写进去
        readonly = os.path.join(target, "SecureVault.exe")
        os.chmod(readonly, stat.S_IREAD)
        second = update.apply_staging(staging, target)
        runner.check("只读文件也能替换（改名让位）", not second["failed"],
                     str(second["failed"][:2]))
        runner.check("替换后程序本体仍然是新版本",
                     _read(readonly).startswith("MZ"))
        os.chmod(readonly, stat.S_IWRITE | stat.S_IREAD)
        backups = update.cleanup_backups(target)
        runner.check("覆盖留下的旧文件会被清理", backups >= 1, str(backups))

        # 没有 SecureVault.exe 的压缩包必须拒绝覆盖
        bad = os.path.join(work, "bad.zip")
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("SecureVault/readme.md", "这不是程序包")
        error = ""
        try:
            update.stage(bad, work)
        except update.UpdateError as exc:
            error = str(exc)
        runner.check("缺程序本体的包会被拒绝", bool(error), error)

        # 越界路径必须挡住
        slip = os.path.join(work, "slip.zip")
        with zipfile.ZipFile(slip, "w") as archive:
            archive.writestr("SecureVault/SecureVault.exe", "MZ")
            archive.writestr("SecureVault/../evil.txt", "坏东西")
        blocked = ""
        try:
            update.stage(slip, work)
        except update.UpdateError as exc:
            blocked = str(exc)
        runner.check("压缩包里的越界路径会被挡住", bool(blocked), blocked)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _test_touch(runner: Runner) -> None:
    runner.section("触屏支持")
    from securevault.system import touch

    original_launch = touch._launch
    original_touch = touch._touch
    original_flag = os.environ.get("SV_TOUCH")
    calls = []
    touch._launch = lambda path: (calls.append(path), True)[1]
    try:
        touch.set_enabled(False)
        calls.clear()
        runner.check("关掉开关时不唤起屏幕键盘",
                     touch.show_keyboard() is False and not calls)
        touch.set_enabled(True)
        os.environ["SV_TOUCH"] = "1"
        touch._touch = None
        launched = touch.show_keyboard()
        runner.check("触摸设备上会唤起屏幕键盘", launched is True and bool(calls),
                     "%s / %s" % (launched, calls))
        touch._touch = None
        os.environ["SV_TOUCH"] = "0"
        runner.check("非触摸设备上不打扰用户", touch.touch_available() is False)
        runner.check("非触摸设备上不唤起键盘",
                     touch.show_keyboard() is False)
    finally:
        touch._launch = original_launch
        touch._touch = original_touch
        touch.set_enabled(True)
        if original_flag is None:
            os.environ.pop("SV_TOUCH", None)
        else:
            os.environ["SV_TOUCH"] = original_flag


if __name__ == "__main__":
    raise SystemExit(run_selftest())
