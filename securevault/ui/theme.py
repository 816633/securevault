"""配色、字体与 DPI 缩放。

风格约束（来自需求）：
  * Windows 10 观感：浅灰底 + 白卡片 + 1px 细边框 + 标准蓝强调色；
  * **不使用圆角**；按钮、输入框、复选框、下拉框全部自绘扁平直角样式。
"""

from __future__ import annotations

import os
from typing import Optional

# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------

BG = "#F0F0F0"            # 窗口底色
BG_ALT = "#F7F7F7"        # 次级底色（列表斑马纹 / 底部状态栏）
CARD = "#FFFFFF"          # 卡片 / 输入框底色
BORDER = "#D8D8D8"        # 常规边框
BORDER_STRONG = "#B4B4B4"  # 强调一些的边框
TEXT = "#1B1B1B"          # 正文
TEXT_DIM = "#6B6B6B"      # 次要文字
TEXT_DISABLED = "#A0A0A0"  # 禁用文字

ACCENT = "#0078D4"        # Win10 标准蓝
ACCENT_HOVER = "#1A88DB"
ACCENT_PRESSED = "#0067B8"
ACCENT_SOFT = "#CCE4F7"   # 选中底色

BTN_BG = "#E4E4E4"        # 普通按钮
BTN_BG_HOVER = "#EAF3FB"
BTN_BG_PRESSED = "#CCE4F7"
BTN_BORDER = "#ADADAD"
BTN_BORDER_HOVER = "#0078D4"
BTN_TEXT = "#1B1B1B"
BTN_DISABLED_BG = "#F0F0F0"

DANGER = "#C42B1C"
DANGER_HOVER = "#D13438"
DANGER_PRESSED = "#A4262C"

SUCCESS = "#0F7B0F"
WARN = "#9D5D00"

HEADER_BG = "#F3F3F3"
TAB_BG = "#EAEAEA"
TAB_ACTIVE_BG = "#FFFFFF"

FONT_FAMILY = "Microsoft YaHei UI"
FONT_FALLBACK = "Microsoft YaHei"
MONO_FAMILY = "Consolas"

# 面板尺寸（DIP，会按屏幕 DPI 与工作区缩放）
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 720
MIN_WIDTH = 840
MIN_HEIGHT = 540
# 锁屏 / 首次设置用小窗口（不占据整个屏幕）
LOCK_WIDTH = 400
LOCK_HEIGHT = 260
SETUP_WIDTH = 470
SETUP_HEIGHT = 380
PAD = 12
GAP = 8

_scale = 1.0


def set_scale(value: float) -> None:
    global _scale
    _scale = max(0.6, min(3.0, float(value)))


def scale() -> float:
    return _scale


def px(value: float) -> int:
    """把 DIP 换算成当前 DPI 下的像素。"""
    return int(round(value * _scale))


def pick_font_family(root=None) -> str:
    """优先使用「微软雅黑 UI」，缺失时退回可用字体。"""
    try:
        import tkinter.font as tkfont

        families = set(tkfont.families(root))
        for name in (FONT_FAMILY, FONT_FALLBACK, "Segoe UI", "Tahoma"):
            if name in families:
                return name
    except Exception:
        pass
    return "Tahoma"


def init_fonts(root) -> dict:
    """创建并返回全局字体对象。"""
    import tkinter.font as tkfont

    family = pick_font_family(root)
    return {
        "family": family,
        "base": tkfont.Font(root=root, family=family, size=9),
        "small": tkfont.Font(root=root, family=family, size=8),
        "bold": tkfont.Font(root=root, family=family, size=9, weight="bold"),
        "title": tkfont.Font(root=root, family=family, size=13, weight="bold"),
        "subtitle": tkfont.Font(root=root, family=family, size=10, weight="bold"),
        "tab": tkfont.Font(root=root, family=family, size=10),
        "mono": tkfont.Font(root=root, family=MONO_FAMILY, size=10),
        "mono_small": tkfont.Font(root=root, family=MONO_FAMILY, size=9),
    }


def configure_ttk(root) -> None:
    """把 ttk 控件（列表 / 滚动条）也改成扁平直角风格。"""
    from tkinter import ttk

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # 滚动条：细、扁平、直角
    style.configure(
        "SV.Vertical.TScrollbar",
        gripcount=0, background="#C6C6C6", darkcolor=BG, lightcolor=BG,
        troughcolor="#F5F5F5", bordercolor="#F5F5F5", arrowcolor="#5A5A5A",
        relief="flat", arrowsize=px(11), width=px(12),
    )
    style.map("SV.Vertical.TScrollbar",
              background=[("active", "#A8A8A8"), ("pressed", "#8E8E8E")])
    style.configure(
        "SV.Horizontal.TScrollbar",
        gripcount=0, background="#C6C6C6", darkcolor=BG, lightcolor=BG,
        troughcolor="#F5F5F5", bordercolor="#F5F5F5", arrowcolor="#5A5A5A",
        relief="flat", arrowsize=px(11),
    )
    # 列表：白底、无网格线、整行选中、斑马纹
    style.configure(
        "SV.Treeview",
        background=CARD, fieldbackground=CARD, foreground=TEXT,
        borderwidth=0, relief="flat", rowheight=px(24), font=("Microsoft YaHei UI", 9),
    )
    style.map("SV.Treeview",
              background=[("selected", ACCENT_SOFT)],
              foreground=[("selected", TEXT)])
    style.configure(
        "SV.Treeview.Heading",
        background=HEADER_BG, foreground=TEXT, relief="flat", borderwidth=0,
        padding=(px(6), px(4)), font=("Microsoft YaHei UI", 9),
    )
    style.map("SV.Treeview.Heading", background=[("active", "#E9E9E9")])
    style.layout("SV.Treeview", [
        ("Treeview.treearea", {"sticky": "nswe"}),
    ])
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
    root.option_add("*Menu.background", CARD)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", ACCENT_SOFT)
    root.option_add("*Menu.activeForeground", TEXT)
    root.option_add("*Menu.borderWidth", 1)
    root.option_add("*Menu.relief", "solid")


def icon_path() -> Optional[str]:
    """返回应用图标文件路径。"""
    candidates = []
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    candidates.append(os.path.join(root, "assets", "securevault.ico"))
    if getattr(os.sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(os.sys.executable))
        candidates.insert(0, os.path.join(base, "assets", "securevault.ico"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def color_for_level(level: str) -> str:
    return {
        "DEBUG": TEXT_DIM,
        "INFO": TEXT,
        "WARN": WARN,
        "ERROR": DANGER,
    }.get((level or "").upper(), TEXT)
