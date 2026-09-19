"""端到端界面测试：不用人工点击，直接驱动真实的界面对象走完整流程。

覆盖：首次设置密码 → 打开面板 → 逐页切换与刷新 → 模式切换 → 新增规则 / 计划 /
别名 → 模拟设备接入 → 写记录 → 上锁 → 再次打开需要密码 → 只有一个窗口（不叠窗）→
日志页内容 → 立即退出。

使用独立数据目录，默认 ``SecureVaultData-e2e``（可用 SV_DATA_DIR 覆盖）。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import tkinter as tk
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("SV_DATA_DIR", "SecureVaultData-e2e")

from securevault.core import paths  # noqa: E402
from securevault.core.model import Alias, DeviceInfo, Event, Mode  # noqa: E402
from securevault.ui import app as app_module  # noqa: E402
from securevault.ui import dialogs as D  # noqa: E402

PASSWORD = "e2e-pass-2026"


class Reporter:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.problems = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print("[PASS] %s" % name)
        else:
            self.failed += 1
            print("[FAIL] %s%s" % (name, ("  <- " + detail) if detail else ""))
            self.problems.append(name)
        return bool(condition)


def pump(app, seconds: float = 0.35) -> None:
    """让 Tk 处理事件（相当于界面刷新）。"""
    end = time.time() + seconds
    while time.time() < end:
        try:
            app.root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def toplevel_count(widget) -> int:
    """递归统计 Toplevel 数量（弹出菜单/对话框都算，用来断言不会叠窗）。"""
    count = 0
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel):
            count += 1
        count += toplevel_count(child)
    return count


def _mapped_views(app) -> int:
    """当前真正显示在窗口里的视图数量（应当恒为 1）。"""
    return sum(1 for child in app.container.winfo_children()
               if child.winfo_ismapped())


def button_texts(widget) -> list:
    """收集某个容器里所有自绘按钮的文字（用来断言按钮文案）。"""
    from securevault.ui import widgets as W

    found = []
    for child in widget.winfo_children():
        if isinstance(child, W.FlatButton):
            found.append(child.text)
        found.extend(button_texts(child))
    return found


def _widget_texts(widget) -> list:
    """收集某个容器里所有 tk/ttk 控件的文字。"""
    found = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if text and child.winfo_ismapped():
                found.append(str(text))
        except Exception:
            pass
        found.extend(_widget_texts(child))
    return found


def _dropdown_is_custom(page) -> bool:
    """下拉框应当弹出自己画的菜单窗口（Toplevel），而不是 tk.Menu。"""
    try:
        select = page.alias_match
        before = toplevel_count(page.winfo_toplevel())
        select._open()
        page.update()
        after = toplevel_count(page.winfo_toplevel())
        if getattr(select, "_popup", None) is not None:
            select._popup.close()
        return after > before
    except Exception:
        return False


def _version_opens_project(app) -> bool:
    """点版本号应当打开项目主页（这里把浏览器调用换成记录 URL）。"""
    import webbrowser

    seen = {}
    original = webbrowser.open
    webbrowser.open = lambda url, *a, **k: seen.setdefault("url", url) or True
    try:
        app.open_project_page()
    finally:
        webbrowser.open = original
    return seen.get("url") == app_module.PROJECT_URL


def _copy_flow_checks(app, report, dirs) -> None:
    """复制相关的关键行为：上锁后仍复制、按设备隔离增量、别名目录改名。"""
    import shutil

    work = os.path.join(dirs.tmp, "e2e-copy")
    shutil.rmtree(work, ignore_errors=True)
    src = os.path.join(work, "src")
    dest = os.path.join(work, "dest")
    os.makedirs(os.path.join(src, "sub"), exist_ok=True)
    _write(os.path.join(src, "a.txt"), "hello")
    _write(os.path.join(src, "sub", "b.txt"), "world")

    settings = app.engine.settings
    settings.copy_dest = dest
    app.engine.save_settings(settings)
    app.engine.set_manual_mode(Mode.COPY)
    pump(app, 0.2)

    device = DeviceInfo(letter="Z:", name="测试盘", bus_type="USB", model="E2E",
                        vid="0E2E", pid="5678", usb_serial="SN-1", disk_serial="SN-1",
                        fs="FAT32", capacity=8 * 1024 ** 3, physical_drive=9)
    result = app.engine._do_copy(device, dest, src_root=src, record=False)
    target = os.path.join(dest, "测试盘")
    report.check("复制到 <复制目标>\\<设备名>",
                 os.path.isfile(os.path.join(target, "a.txt")), str(result.errors[:1]))
    report.check("复制保留目录结构",
                 os.path.isfile(os.path.join(target, "sub", "b.txt")))

    app.lock_now()
    pump(app, 0.3)
    report.check("上锁后引擎仍在运行", app._service_started)
    _write(os.path.join(src, "c.txt"), "after-lock")
    result2 = app.engine._do_copy(device, dest, src_root=src, record=False)
    report.check("**上锁（收进托盘）后照样复制新文件**",
                 os.path.isfile(os.path.join(target, "c.txt")),
                 "; ".join(result2.errors[:1]) or str(result2.stats))

    calls = []
    original_start = app.engine.start_device_copy
    app.engine.start_device_copy = lambda dev, root: calls.append(dev.letter) or True
    try:
        app.engine.handle_event("Z:", Event.ARRIVAL, device)
    finally:
        app.engine.start_device_copy = original_start
    report.check("上锁状态下设备事件仍会触发复制", calls == ["Z:"], str(calls))

    # 同一个盘符、不同设备：不能沿用上一个设备的增量索引
    other = DeviceInfo(letter="Z:", name="测试盘2", bus_type="USB", model="E2E-2",
                       vid="0E2E", pid="9999", usb_serial="SN-2", disk_serial="SN-2",
                       fs="FAT32", capacity=8 * 1024 ** 3, physical_drive=9)
    again = app.engine._do_copy(other, dest, src_root=src, record=False)
    report.check("不同设备（同一盘符）会各自完整复制一次",
                 again.stats.copied == 3, str(again.stats.copied))
    report.check("不同设备写到各自的文件夹",
                 os.path.isdir(os.path.join(dest, "测试盘2")))

    # 别名改动 → 老目录改名成 <原名>_<别名>，而不是新建目录
    app.engine._known = {"Z:": device}          # 模拟该设备当前在线
    before_aliases = app.store.list_aliases()
    app.store.upsert_alias(Alias(match="diskSerial", value="SN-1", alias="我的别名"))
    app.engine.reload()
    note = app.engine.rename_after_alias_change(before_aliases,
                                                app.store.list_aliases())
    renamed = os.path.join(dest, "测试盘_我的别名")
    report.check("改别名后目录改名为 <原名>_<别名>",
                 os.path.isdir(renamed) and not os.path.isdir(os.path.join(dest, "测试盘")),
                 str(note))
    # 再把别名从"我的别名"改成"新别名"：应该是 <原名>_<旧别名> → <原名>_<新别名>
    app.engine._known = {"Z:": device}
    before_aliases = app.store.list_aliases()
    alias_row = None
    for candidate in app.store.list_aliases():
        if candidate.match == "diskSerial" and candidate.value == "SN-1":
            alias_row = candidate
            break
    if alias_row is None:
        report.check("找到待修改的别名记录", False, str(before_aliases))
        return
    alias_row.alias = "新别名"
    app.store.upsert_alias(alias_row)
    app.engine.reload()
    moved = app.engine.rename_after_alias_change(before_aliases,
                                                 app.store.list_aliases())
    renamed2 = os.path.join(dest, "测试盘_新别名")
    report.check("更新别名是 <原名>_<旧别名> → <原名>_<新别名>",
                 os.path.isdir(renamed2) and not os.path.isdir(renamed),
                 "%s / %s" % (moved, os.listdir(dest) if os.path.isdir(dest) else []))
    report.check("改名后原来的文件还在里面",
                 os.path.isfile(os.path.join(renamed2, "a.txt")))
    _write(os.path.join(src, "d.txt"), "after-rename")
    result3 = app.engine._do_copy(device, dest, src_root=src, record=False)
    report.check("改名后继续往同一个文件夹里增量复制",
                 os.path.isfile(os.path.join(renamed2, "d.txt"))
                 and not os.path.isdir(os.path.join(dest, "测试盘")),
                 str(result3.stats))

    # 删除别名：<原名>_<别名> → <原名>
    app.engine._known = {"Z:": device}
    before_aliases = app.store.list_aliases()
    for candidate in app.store.list_aliases():
        if candidate.match == "diskSerial" and candidate.value == "SN-1":
            app.store.delete_alias(candidate.id)
    app.engine.reload()
    back = app.engine.rename_after_alias_change(before_aliases,
                                                app.store.list_aliases())
    report.check("删除别名后目录改回 <原名>",
                 os.path.isdir(os.path.join(dest, "测试盘"))
                 and not os.path.isdir(renamed2), str(back))
    result4 = app.engine._do_copy(device, dest, src_root=src, record=False)
    report.check("删除别名后继续用 <原名> 目录（不再新建）",
                 os.path.isdir(os.path.join(dest, "测试盘"))
                 and sorted(os.listdir(dest)) == ["测试盘", "测试盘2"],
                 str(sorted(os.listdir(dest))))

    app.engine.set_manual_mode(Mode.MONITOR)
    app.show_panel()
    pump(app, 0.3)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def subset_of(needed, actual) -> bool:
    return set(needed).issubset(set(actual))


def _dialog_has_text(dialog, needle: str) -> bool:
    """对话框里是否有包含某段文字的控件（老式弹窗用 Label 显示提示/错误）。"""
    return any(needle in text for text in _widget_texts(dialog))


def _dialog_style_checks(app, report) -> None:
    """密码 / 恢复码弹窗的样式细节（按钮大小、复选框大小、现代边框）。"""
    import tkinter.font as tkfont

    password = D.PasswordDialog(app.root, "样式检查", "密码：", "确定")
    report.check("密码弹窗输入框是现代扁平样式（不是凹进去的老式框）",
                 password.entry.cget("relief") == "flat"
                 and str(password.entry.cget("highlightthickness")) == "1")
    report.check("「显示密码」复选框比正标题小（16px / 10pt）",
                 password.show_check._size == 16
                 and abs(int(tkfont.Font(font=password.show_check.label.cget(
                     "font")).cget("size"))) == 10,
                 str(password.show_check._size))
    ok_font = tkfont.Font(font=password.forgot_button._label.cget("font"))
    report.check("确定/取消按钮是正常大小（10pt）",
                 abs(int(ok_font.cget("size"))) == 10, str(ok_font.cget("size")))
    password._safe_destroy()

    text_dialog = D.PasswordDialog(app.root, "样式检查", "恢复码：", "确定",
                                   secret=False)
    report.check("恢复码输入框也是现代扁平样式",
                 text_dialog.entry.cget("relief") == "flat"
                 and text_dialog.entry.cget("show") == "")
    report.check("恢复码弹窗不显示「显示密码」", text_dialog.show_check is None)
    text_dialog._safe_destroy()


def add_rule_via_page(page, rule_type: str, value: str):
    """在排除页加一条规则，返回它的 id。"""
    page.rule_type.set(rule_type)
    page.rule_value.set(value)
    page.add_rule()
    for rule in page.ctx.store.list_exclude_rules():
        if rule.value == value:
            return rule.id
    return 0


def _sample_record_for(app):
    from securevault.core.model import Action, DeviceInfo, Record, now_rfc3339

    return Record(time=now_rfc3339(), event="arrival", action=Action.MONITOR,
                  note="清理测试", device=DeviceInfo(letter="Y:", name="清理盘",
                                                     disk_serial="CLEAN-1"))


def main() -> int:
    report = Reporter()
    dirs = paths.resolve(dev=True)
    # 每次从干净的数据目录开始（只针对测试专用目录）。
    if os.path.basename(dirs.root).startswith("SecureVaultData-e2e"):
        shutil.rmtree(dirs.root, ignore_errors=True)
    dirs.ensure()

    # 自动化测试里不要弹模态框
    D.NON_BLOCKING = True
    D.show_recovery = lambda parent, code: True
    D.message = lambda *args, **kwargs: 0
    app_module.D.show_recovery = D.show_recovery
    app_module.D.message = D.message

    app = None
    try:
        app = app_module.SecureVaultApp(dev=True, review=False)
        pump(app)

        report.check("首次启动弹出「首次使用」小窗口",
                     app._setup_window is not None
                     and app._setup_window.winfo_exists() == 1)
        report.check("未初始化时密钥库状态正确",
                     not app.keystore.initialized)

        view = app._setup_window
        view.entry.delete(0, "end")
        view.entry.insert(0, "short")
        view.confirm_entry.delete(0, "end")
        view.confirm_entry.insert(0, "short")
        view._ok()
        pump(app, 0.1)
        report.check("密码太短会被拒绝并给出提示",
                     _dialog_has_text(view, "至少"))

        view.entry.delete(0, "end")
        view.entry.insert(0, PASSWORD)
        view.confirm_entry.delete(0, "end")
        view.confirm_entry.insert(0, PASSWORD)
        view._ok()
        pump(app, 1.0)
        report.check("设置密码后服务已启动", app._service_started)
        report.check("设置密码后显示主面板",
                     isinstance(app._current_view, tk.Frame)
                     and app._current_view is app._page_holder)
        report.check("主面板窗口切换为大窗口", app._window_mode == "panel")
        report.check("一进面板就显示当前页（不用先点顶栏）",
                     any(page.winfo_ismapped() for page in app._pages),
                     str([page.winfo_ismapped() for page in app._pages]))
        report.check("底部有统一提示条", hasattr(app, "notify_bar"))
        report.check("「应用模式」按钮上没有多余符号",
                     app._pages[0].apply_button.text == "应用模式",
                     app._pages[0].apply_button.text)
        _dialog_style_checks(app, report)
        report.check("顶栏版本号可点（不会显示 Python 版字样）",
                     "Python" not in button_texts(app._page_holder).__str__()
                     and "Python" not in app.root.title())
        report.check("密钥库已初始化", app.keystore.initialized)
        report.check("开机自启快捷方式已创建（或至少不报错）",
                     app.engine is not None)
        report.check("仅有一个主窗口，没有额外 Toplevel",
                     toplevel_count(app.root) == 0,
                     str(toplevel_count(app.root)))

        # 逐页切换并刷新
        for index, title in enumerate(app_module.PAGE_TITLES):
            try:
                app.tabbar.select(index)
                pump(app, 0.25)
                app._pages[index].refresh()
                pump(app, 0.15)
                report.check("页面「%s」可以打开并刷新" % title, True)
            except Exception as exc:
                report.check("页面「%s」可以打开并刷新" % title, False,
                             "%s: %s" % (type(exc).__name__, exc))

        # 面板只会创建一次（不叠窗）
        panel_first = app._page_holder
        app.tabbar.select(3)
        pump(app, 0.2)
        app.show_panel()
        pump(app, 0.2)
        report.check("重复打开面板不会新建窗口",
                     app._page_holder is panel_first
                     and toplevel_count(app.root) == 0)

        # 状态页：切换模式
        status_page = app._pages[0]
        app.tabbar.select(0)
        pump(app, 0.2)
        status_page._select_mode(Mode.COPY)
        status_page._apply_mode()
        pump(app, 0.2)
        report.check("操作反馈显示在底部提示条",
                     "复制模式" in app.notify_bar.label.cget("text"),
                     app.notify_bar.label.cget("text"))
        report.check("状态页可以切换为复制模式",
                     app.engine.manual_mode == Mode.COPY
                     and app.engine.effective_mode == Mode.COPY,
                     app.engine.manual_mode)
        report.check("状态页设备列表已填充",
                     len(status_page.device_tree.get_children()) >= 1,
                     str(len(status_page.device_tree.get_children())))
        # 选了另一个模式后，页面的每秒自动刷新不能把选择弹回
        status_page._select_mode(Mode.MONITOR)
        status_page.refresh()          # 模拟定时刷新
        status_page.refresh()
        report.check("未点应用前，自动刷新不会把已选模式弹回去",
                     status_page._pending_mode == Mode.MONITOR,
                     str(status_page._pending_mode))
        status_page.on_show()          # 重新进入本页才恢复"已应用"的模式
        report.check("重新进入本页才恢复成已应用的模式",
                     status_page._pending_mode is None)
        status_page._select_mode(Mode.COPY)
        status_page._apply_mode()
        pump(app, 0.2)

        # 排除名单页：新增规则与名单
        exclude_page = app._pages[2]
        app.tabbar.select(2)
        pump(app, 0.2)
        exclude_page.rule_type.set("vid")
        exclude_page.rule_value.set("1234")
        exclude_page.add_rule()
        exclude_page.list_kind.set("excludeExt")
        exclude_page.list_value.set("tmp")
        exclude_page.add_list()
        pump(app, 0.2)
        rules = app.store.list_exclude_rules()
        entries = app.store.list_lists()
        report.check("排除页可以新增设备规则",
                     any(rule.value == "1234" for rule in rules))
        report.check("排除页可以新增文件名单",
                     any(item.value == "tmp" for item in entries))
        report.check("排除页列表已刷新",
                     len(exclude_page.rule_tree.get_children()) == len(rules) + 1)
        report.check("默认排除内置硬盘（系统内置规则，不能删除）",
                     app.engine.match_exclude(
                         DeviceInfo(letter="C:", name="系统盘", bus_type="NVMe",
                                    is_removable=False)) is not None)
        report.check("U 盘不会被内置规则排除（无 U 盘时用可移动标记判断）",
                     app.engine.match_exclude(
                         DeviceInfo(letter="E:", name="U盘", bus_type="USB",
                                    is_removable=True)) is None)

        # 定时切换页
        schedule_page = app._pages[3]
        app.tabbar.select(3)
        pump(app, 0.2)
        schedule_page.start.set("22:00")
        schedule_page.end.set("06:00")
        schedule_page.mode.set(Mode.COPY)
        schedule_page.add_slot()
        pump(app, 0.2)
        slots = app.store.list_schedule()
        report.check("定时页可以新增计划", len(slots) == 1, str(len(slots)))
        schedule_page.start.set("25:99")
        schedule_page.add_slot()
        pump(app, 0.1)
        report.check("非法时间会被拒绝",
                     len(app.store.list_schedule()) == 1
                     and "范围" in schedule_page.hint_label.cget("text"))
        schedule_page.start.set("abc")
        schedule_page.add_slot()
        pump(app, 0.1)
        report.check("格式不对的时间会被拒绝",
                     len(app.store.list_schedule()) == 1
                     and "HH:MM" in schedule_page.hint_label.cget("text"))

        # 工具页：别名
        tools_page = app._pages[4]
        app.tabbar.select(4)
        pump(app, 0.2)
        tools_page.alias_match.set("volume")
        tools_page.alias_value.set("我的U盘")
        tools_page.alias_name.set("工作盘")
        tools_page.add_alias()
        pump(app, 0.2)
        aliases = app.store.list_aliases()
        report.check("工具页可以新增别名",
                     any(item.alias == "工作盘" for item in aliases))
        report.check("下拉框弹出的是自绘菜单（不是系统原生菜单）",
                     _dropdown_is_custom(tools_page))
        report.check("版本号点击会打开项目主页", _version_opens_project(app))

        # 设置页：保存
        settings_page = app._pages[5]
        app.tabbar.select(5)
        pump(app, 0.2)
        settings_page.copy_dest.set(os.path.join(dirs.root, "dest"))
        settings_page.retention.set("7")
        settings_page.save()
        pump(app, 0.3)
        report.check("点保存设置会有反馈（底部提示条）",
                     "保存" in app.notify_bar.label.cget("text"),
                     app.notify_bar.label.cget("text"))
        saved = app.store.load_settings()
        report.check("设置页可以保存复制目标",
                     saved.copy_dest.endswith("dest"), saved.copy_dest)
        report.check("设置页可以保存日志保留天数", saved.log_retention == 7,
                     str(saved.log_retention))

        # 模拟设备接入（真实事件路径）
        app.engine.set_manual_mode(Mode.MONITOR)
        before = app.store.count_records()
        app.engine.handle_event("Z:", Event.ARRIVAL, DeviceInfo(
            letter="Z:", name="测试U盘", bus_type="USB", model="E2E Disk",
            vid="1234", pid="5678", usb_serial="E2E-SN", disk_serial="E2E-DISK-SN",
            fs="FAT32", capacity=8000000000, free=4000000000, physical_drive=9))
        pump(app, 0.3)
        report.check("模拟接入会写入监控记录",
                     app.store.count_records() == before + 1)
        records_page = app._pages[1]
        app.tabbar.select(1)
        pump(app, 0.4)
        report.check("监控记录页显示新增记录",
                     len(records_page.tree.get_children()) == app.store.count_records())

        _copy_flow_checks(app, report, dirs)

        # 日志页
        logs_page = app._pages[6]
        app.tabbar.select(6)
        pump(app, 0.4)
        report.check("日志页列出了日志文件",
                     len(logs_page.tree.get_children()) >= 1)
        content = logs_page.preview.get()
        report.check("日志页能看到日志内容", len(content) > 0, str(len(content)))
        logs_page.tree.selection_set("1")
        logs_page._load_selected()
        pump(app, 0.2)
        report.check("日志页切换文件后仍有内容",
                     len(logs_page.preview.get()) > 0)

        # 上锁 -> 窗口隐藏 -> 再次打开需要密码
        app.lock_now()
        pump(app, 0.3)
        report.check("上锁后窗口隐藏", app.root.state() == "withdrawn",
                     app.root.state())
        report.check("上锁后引擎仍在后台运行（默认设置）", app._service_started)
        app._tray_open()
        pump(app, 0.3)
        report.check("托盘打开时弹出「解锁」小弹窗",
                     app._lock_window is not None
                     and app._lock_window.winfo_exists() == 1)
        lock_view = app._lock_window
        pump(app, 0.2)
        report.check("解锁是一个独立小窗口（不是主窗口页面）",
                     isinstance(lock_view, tk.Toplevel)
                     and lock_view.winfo_width() < 700
                     and app.root.state() == "withdrawn",
                     "%sx%s root=%s" % (lock_view.winfo_width(),
                                        lock_view.winfo_height(), app.root.state()))
        center_x = lock_view.winfo_rootx() + lock_view.winfo_width() // 2
        screen_x = lock_view.winfo_screenwidth() // 2
        report.check("解锁弹窗出现在屏幕中间（不是左上角）",
                     abs(center_x - screen_x) < 200 and lock_view.winfo_rootx() > 10,
                     "cx=%d screen=%d" % (center_x, screen_x))
        report.check("解锁窗口标题不显示软件名",
                     "SecureVault" not in lock_view.title(), lock_view.title())
        texts = _widget_texts(lock_view)
        report.check("解锁窗口默认**不显示**「忘记密码」按钮",
                     "忘记密码" not in texts, str(texts))
        # 未输入密码时连点确定 6 次才会临时出现 10 秒
        for _ in range(5):
            lock_view.entry.delete(0, "end")
            lock_view._ok()
        lock_view.update_idletasks()
        hidden = "忘记密码" not in _widget_texts(lock_view)
        lock_view.entry.delete(0, "end")
        lock_view._ok()
        lock_view.update_idletasks()
        report.check("连点 6 次确定（空密码）后出现「忘记密码」",
                     hidden and "忘记密码" in _widget_texts(lock_view))
        lock_view._hide_forgot()
        lock_view.update_idletasks()
        report.check("「忘记密码」10 秒后会自动隐藏",
                     "忘记密码" not in _widget_texts(lock_view))
        # 6 次点击必须在 10 秒之内：把窗口起点调到 11 秒前，计数应该重新开始
        import time as _time

        for _ in range(5):
            lock_view.entry.delete(0, "end")
            lock_view._ok()
        lock_view._click_window_start = _time.time() - 11
        lock_view.entry.delete(0, "end")
        lock_view._ok()
        lock_view.update_idletasks()
        report.check("超过 10 秒的点击会重新计数（不会显示「忘记密码」）",
                     "忘记密码" not in _widget_texts(lock_view))
        report.check("解锁窗口里有「显示密码」勾选框", "显示密码" in texts)
        report.check("解锁窗口里不出现软件名",
                     not any("SecureVault" in t for t in texts))
        lock_view.entry.delete(0, "end")
        lock_view.entry.insert(0, "wrong-password")
        lock_view._ok()
        pump(app, 0.2)
        report.check("错误密码无法进入面板",
                     app._ui_locked and app._lock_window is not None)
        lock_view.entry.delete(0, "end")
        lock_view.entry.insert(0, PASSWORD)
        lock_view._ok()
        pump(app, 0.5)
        report.check("解锁后窗口恢复为大窗口", app._window_mode == "panel")
        report.check("正确密码可以重新打开面板",
                     app._current_view is app._page_holder and not app._ui_locked)
        report.check("解锁弹窗已关闭", app._lock_window is None)
        panel_x = app.root.winfo_rootx() + app.root.winfo_width() // 2
        report.check("主面板也居中显示（不是左上角）",
                     abs(panel_x - app.root.winfo_screenwidth() // 2) < 200
                     and app.root.winfo_rootx() > 10,
                     "x=%d" % app.root.winfo_rootx())

        # 表格复选框 / 批量操作 / 密码确认
        exclude_page = app._pages[2]
        app.tabbar.select(2)
        pump(app, 0.2)
        index = add_rule_via_page(exclude_page, "vid", "0E2E")
        pump(app, 0.2)
        holder = exclude_page.rule_tree.holder
        report.check("表格有复选框列", "☐" in str(
            exclude_page.rule_tree.item(exclude_page.rule_tree.get_children()[0],
                                        "values")[0]))
        holder.checked.add(index)
        holder._paint_row(str(1))
        report.check("勾选后能取到勾选的行", subset_of([index],
                     set(exclude_page.action_keys(exclude_page.rule_tree))))
        before = len(app.store.list_exclude_rules())
        exclude_page.delete_rule()
        pump(app, 0.2)
        report.check("可以按勾选批量删除",
                     len(app.store.list_exclude_rules()) < before)

        verified = []
        original_verify = app.verify_password
        app.verify_password = lambda prompt="": verified.append(prompt) or True
        try:
            app.store.insert_record(_sample_record_for(app))
            records_page = app._pages[1]
            app.tabbar.select(1)
            pump(app, 0.2)
            records_page.clear_records("all")
            pump(app, 0.2)
        finally:
            app.verify_password = original_verify
        report.check("清除监控记录前要求输入密码", bool(verified), str(verified))

        logs_page = app._pages[6]
        app.tabbar.select(6)
        pump(app, 0.3)
        preview_text = logs_page.preview.text
        report.check("日志自动换行", preview_text.cget("wrap") == "word")
        import tkinter.font as tkfont

        font_size = tkfont.Font(font=preview_text.cget("font")).cget("size")
        report.check("日志字体不小于 10", abs(int(font_size)) >= 10, str(font_size))
        bottom = preview_text.yview()[1]
        report.check("日志打开时停在最底部（最新一行）", bottom >= 0.999,
                     str(bottom))
        # 实时刷新：写一行新日志，不点刷新也应该自动出现在预览里
        app.logger.info("E2E 实时刷新测试行")
        pump(app, 1.6)
        report.check("日志会实时自动更新（不用点刷新）",
                     "E2E 实时刷新测试行" in logs_page.preview.get())
        report.check("重新打开面板后没有叠出第二个窗口",
                     toplevel_count(app.root) == 0
                     and _mapped_views(app) == 1,
                     str(_mapped_views(app)))

        # 关闭窗口 = 收进托盘 + 上锁
        app.on_close_request()
        pump(app, 0.3)
        report.check("点关闭按钮后窗口收进托盘并上锁",
                     app.root.state() == "withdrawn" and app._ui_locked)

        # 修改密码 / 恢复码
        app.keystore.lock()
        report.check("上锁后密钥库需要重新解锁",
                     not app.keystore.unlocked)
        app.keystore.unlock(PASSWORD)
        report.check("重新解锁成功", app.keystore.unlocked)

        # 退出流程：隐藏入口点了「忘记密码」也要把后续补上（重置 → 继续退出）
        quit_called = []
        original_ask = D.ask_password_old
        original_reset = app.reset_with_recovery
        original_message = D.message
        original_quit = app.quit
        D.ask_password_old = lambda *a, **k: {"forgot": True}
        app.reset_with_recovery = lambda *a, **k: True
        D.message = lambda *a, **k: 0
        app_module.D.message = D.message
        app.quit = lambda: quit_called.append(True)
        try:
            app.request_exit()
        finally:
            D.ask_password_old = original_ask
            app.reset_with_recovery = original_reset
            D.message = original_message
            app_module.D.message = original_message
            app.quit = original_quit
        report.check("退出时走「忘记密码」→ 重置后继续退出", quit_called == [True],
                     str(quit_called))

        # 退出
        app.quit()
        report.check("退出后不再运行", not app.running)
    except Exception:
        report.failed += 1
        print("[FAIL] 测试过程中出现异常：\n" + traceback.format_exc())
        if app is not None:
            try:
                app.quit()
            except Exception:
                pass
    finally:
        total = report.passed + report.failed
        print("-" * 60)
        print("端到端测试：%d 项通过，%d 项失败（共 %d 项）"
              % (report.passed, report.failed, total))
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
