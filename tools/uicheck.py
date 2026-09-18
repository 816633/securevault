"""界面自检：逐页截图 + 几何审计（越界 / 重叠 / 空尺寸），不需要人工点击。

用法：

    python tools\\uicheck.py [输出目录]

截图使用 PrintWindow（不需要桌面可见），审计用 Tk 自身的几何信息。
审计模式只使用开发数据目录 SecureVaultData-dev。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from securevault.ui.app import PAGE_TITLES, SecureVaultApp  # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


def capture_window(hwnd: int, path: str) -> bool:
    """用 PrintWindow 把窗口内容保存为 PNG。"""
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False
    window_dc = user32.GetWindowDC(wintypes.HWND(hwnd))
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    ok = user32.PrintWindow(wintypes.HWND(hwnd), mem_dc, PW_RENDERFULLCONTENT)
    if not ok:
        user32.PrintWindow(wintypes.HWND(hwnd), mem_dc, 0)
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(wintypes.HWND(hwnd), window_dc)
    try:
        from PIL import Image

        image = Image.frombuffer("RGBA", (width, height), buffer,
                                 "raw", "BGRA", 0, 1)
        image.convert("RGB").save(path)
        return True
    except Exception as exc:
        print("保存截图失败：%s" % exc)
        return False


def toplevel_hwnd(root) -> int:
    hwnd = root.winfo_id()
    top = user32.GetAncestor(wintypes.HWND(hwnd), 2)  # GA_ROOT
    return int(top or hwnd)


def audit_page(page, name: str, verbose: bool = False):
    """检查页面内控件是否越界、重叠、尺寸异常。"""
    page.update_idletasks()
    problems = []
    try:
        origin_x = page.winfo_rootx()
        origin_y = page.winfo_rooty()
        page_w = page.winfo_width()
        page_h = page.winfo_height()
    except Exception:
        return problems

    widgets = []

    def walk(widget):
        for child in widget.winfo_children():
            if not child.winfo_ismapped():
                continue
            widgets.append(child)
            walk(child)

    walk(page)
    boxes = {}
    for widget in widgets:
        try:
            x = widget.winfo_rootx() - origin_x
            y = widget.winfo_rooty() - origin_y
            w = widget.winfo_width()
            h = widget.winfo_height()
        except Exception:
            continue
        boxes[widget] = (x, y, w, h)
        if (w <= 1 and h <= 1) or (w <= 1 and h < 8) or (h <= 1 and w < 8):
            problems.append("%s：控件 %s 尺寸异常 %dx%d"
                            % (name, widget.winfo_class(), w, h))
        if x + w < -2 or x > page_w + 2:
            problems.append("%s：控件 %s 横向越界 x=%d w=%d（页面宽 %d）"
                            % (name, widget.winfo_class(), x, w, page_w))
        if y + h < -2:
            problems.append("%s：控件 %s 纵向越界 y=%d h=%d"
                            % (name, widget.winfo_class(), y, h))

    # 只比较同父控件的兄弟（嵌套容器的包含关系不算重叠）
    for widget, box in boxes.items():
        parent = widget.master
        if parent is None:
            continue
        for other in parent.winfo_children():
            if other is widget or other not in boxes:
                continue
            other_box = boxes[other]
            if _overlaps(box, other_box):
                if not _contains(other_box, box) and not _contains(box, other_box):
                    problems.append(
                        "%s：%s 与同级 %s 重叠 %s / %s"
                        % (name, widget.winfo_class(), other.winfo_class(),
                           box, other_box))
    if verbose:
        for widget, box in boxes.items():
            print("   %-18s %s" % (widget.winfo_class(), box))
    return problems


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx + 1 or bx + bw <= ax + 1
                or ay + ah <= by + 1 or by + bh <= ay + 1)


def _contains(outer, inner) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return (ox <= ix + 1 and oy <= iy + 1
            and ox + ow >= ix + iw - 1 and oy + oh >= iy + ih - 1)


def analyze_image(path: str) -> dict:
    """统计截图像素，判断界面是否真的画出来了（而不是一片黑或一片白）。"""
    result = {"path": path, "ok": False}
    try:
        from PIL import Image

        image = Image.open(path).convert("RGB")
        width, height = image.size
        # 抽样统计，足够快也足够准
        step = max(1, width // 400)
        pixels = [image.getpixel((x, y))
                  for y in range(0, height, step)
                  for x in range(0, width, step)]
        total = len(pixels)
        colors = set()
        dark = 0
        white = 0
        brightness = 0
        for r, g, b in pixels:
            brightness += (r + g + b) / 3.0
            if r < 24 and g < 24 and b < 24:
                dark += 1
            if r > 246 and g > 246 and b > 246:
                white += 1
            colors.add((r // 8, g // 8, b // 8))
        result.update({
            "width": width, "height": height,
            "mean_brightness": round(brightness / total, 1),
            "dark_ratio": round(dark / total, 4),
            "white_ratio": round(white / total, 4),
            "distinct_colors": len(colors),
            "ok": True,
        })
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = argv[0] if argv else os.path.join(BASE_DIR, ".uishots")
    os.makedirs(out_dir, exist_ok=True)

    app = SecureVaultApp(dev=True, review=True)
    app.root.update()
    app.root.deiconify()
    app.root.update()
    time.sleep(0.6)
    app.root.update()

    hwnd = toplevel_hwnd(app.root)
    problems = []
    for index, title in enumerate(PAGE_TITLES):
        try:
            app.tabbar.select(index)
        except Exception:
            pass
        app.root.update()
        app.root.update_idletasks()
        time.sleep(0.35)
        app.root.update()
        page = app._pages[index]
        problems.extend(audit_page(page, title, verbose=False))
        path = os.path.join(out_dir, "%d-%s.png" % (index, title))
        if capture_window(hwnd, path):
            stats = analyze_image(path)
            print("已保存：%s  %sx%s 亮度=%s 深色占比=%s 颜色数=%d"
                  % (path, stats.get("width"), stats.get("height"),
                     stats.get("mean_brightness"), stats.get("dark_ratio"),
                     stats.get("distinct_colors", 0)))
            if not stats.get("ok"):
                problems.append("%s：截图分析失败 %s" % (title, stats.get("error")))
            else:
                if stats["dark_ratio"] > 0.5:
                    problems.append("%s：界面有大面积黑块（深色占比 %.0f%%）"
                                    % (title, stats["dark_ratio"] * 100))
                if stats["distinct_colors"] < 8:
                    problems.append("%s：界面几乎是纯色，控件可能没画出来" % title)
                if stats["mean_brightness"] < 100:
                    problems.append("%s：界面整体过暗（亮度 %.0f）"
                                    % (title, stats["mean_brightness"]))
        else:
            print("截图失败：%s" % title)

    problems.extend(resize_pass(app, out_dir))

    print("-" * 60)
    if problems:
        print("界面审计发现 %d 个问题：" % len(problems))
        for item in problems:
            print("  - " + item)
    else:
        print("界面审计通过：没有越界、重叠或异常尺寸。")
    try:
        app.quit()
    except Exception:
        pass
    return 1 if problems else 0


def resize_pass(app, out_dir: str) -> list:
    """逐个尺寸改变窗口大小，检查内容是否立刻重排（不会出现空白页）。"""
    problems = []
    left, top, right, bottom = 0, 0, 0, 0
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        right, bottom = 1920, 1080
    sizes = [(1240, 780), (900, 620), (right - left - 20, bottom - top - 20),
             (860, 560)]
    print("窗口缩放检查：")
    for width, height in sizes:
        try:
            app.root.geometry("%dx%d+60+60" % (width, height))
            app.root.update()
            time.sleep(0.25)
            app.root.update()
        except Exception as exc:
            problems.append("缩放 %dx%d 失败：%s" % (width, height, exc))
            continue
        index = app.tabbar.current
        page = app._pages[index]
        if not page.winfo_ismapped():
            problems.append("缩放 %dx%d 后当前页不可见" % (width, height))
            continue
        issues = audit_page(page, "缩放%dx%d" % (width, height))
        problems.extend(issues)
        path = os.path.join(out_dir, "resize-%dx%d.png" % (width, height))
        ok = capture_window(toplevel_hwnd(app.root), path)
        stats = analyze_image(path) if ok else {}
        print("  %dx%d -> %s 尺寸异常=%d 亮度=%s 颜色数=%s"
              % (width, height, "已截图" if ok else "截图失败", len(issues),
                 stats.get("mean_brightness"), stats.get("distinct_colors")))
        if stats.get("ok") and stats["distinct_colors"] < 8:
            problems.append("缩放 %dx%d 后界面是纯色（内容没跟上）" % (width, height))
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
