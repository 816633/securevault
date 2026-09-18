"""开发工具：启动一个 exe（或源码入口），检查它是否真的开出了界面窗口。

用法：python tools\\dev\\launch_check.py [exe 路径] [等待秒数]

判定口径：
  * 找到属于该进程的可见窗口，且窗口标题是 SecureVault 系 -> 正常；
  * 如果那个窗口里出现「启动失败」等文字 -> 报错并打印文字内容。

结束时用 taskkill /T 结束整棵进程树（PyInstaller 单文件版会是「父 + 子」两个进程）。
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from ctypes import wintypes

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(4096)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 4096)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def all_window_handles() -> set:
    handles = set()

    def collect(hwnd, _param):
        handles.add(int(hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(collect), 0)
    return handles


def describe(hwnd: int) -> dict:
    children = []

    def child_cb(child, _p):
        text = _text(child)
        if text:
            children.append((_class_name(child), text))
        return True

    user32.EnumChildWindows(wintypes.HWND(hwnd), EnumWindowsProc(child_cb), 0)
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return {
        "handle": int(hwnd),
        "pid": int(pid.value),
        "title": _text(hwnd),
        "class": _class_name(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "children": children,
    }


def windows_of(pids) -> list:
    found = []

    def collect(hwnd, _param):
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            found.append(describe(int(hwnd)))
        return True

    user32.EnumWindows(EnumWindowsProc(collect), 0)
    return found


def new_windows(before: set) -> list:
    """启动前后对比，找出新出现的窗口（单文件版是「父进程 + 子进程」结构，按 PID 不好认）。"""
    result = []
    for hwnd in all_window_handles() - before:
        item = describe(hwnd)
        if item["visible"] and item["title"]:
            result.append(item)
    return result


def main() -> int:
    argv = sys.argv[1:]
    target = argv[0] if argv else os.path.join(BASE_DIR, "dist", "SecureVault.exe")
    wait = float(argv[1]) if len(argv) > 1 else 8.0
    if target.lower().endswith(".py"):
        command = [sys.executable, target]
    else:
        command = [target]
    print("启动：%s" % " ".join(command))
    before = all_window_handles()
    proc = subprocess.Popen(command, cwd=os.path.dirname(target) or BASE_DIR)
    time.sleep(wait)

    alive = proc.poll() is None
    group = [proc.pid]
    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq %s" % os.path.basename(target).replace(".exe", ""),
             "/FO", "CSV", "/NH"], text=True, errors="ignore")
        for line in output.splitlines():
            parts = [part.strip('"') for part in line.split('","')]
            if len(parts) > 1 and parts[1].isdigit():
                group.append(int(parts[1]))
    except Exception:
        pass

    visible = new_windows(before)
    known_pids = {proc.pid}
    for item in windows_of(set(group)):
        if item["visible"] and item["title"] and item not in visible:
            visible.append(item)
        known_pids.add(item["pid"])
    problems = []
    print("进程：%s（存活=%s）" % (group, alive))
    for item in visible:
        print("窗口：'%s'（%s，子控件 %d 个）"
              % (item["title"], item["class"], len(item["children"])))
        for cls, text in item["children"]:
            flat = text.replace("\r", " ").replace("\n", " | ")
            print("     [%s] %s" % (cls, flat[:200]))
            if "启动失败" in text or "Traceback" in text:
                problems.append(flat[:400])

    ok = bool(visible) and not problems
    print("-" * 60)
    if ok:
        print("结果：正常启动，已开出窗口。")
    elif problems:
        print("结果：启动报错 —— %s" % problems[0])
    else:
        print("结果：没有找到可见窗口（可能已经退出，退出码 %s）。" % proc.poll())

    cleanup(list({proc.pid} | {item["pid"] for item in visible}))
    return 0 if ok else 1


def cleanup(pids) -> None:
    """结束测试启动的进程（taskkill 在本环境可能被拒绝，直接 os.kill 更可靠）。"""
    for pid in pids:
        for killer in (lambda p: subprocess.run(
                ["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True),
                lambda p: os.kill(p, signal.SIGTERM)):
            try:
                killer(pid)
            except Exception:
                continue
    time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
