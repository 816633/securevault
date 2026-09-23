"""全自绘扁平控件（直角、无系统原生外观）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Sequence, Tuple

from . import theme as T
from ..system import touch as TCH

#: 按钮回调里出错时会调用它（由 App 设置，用来写日志 + 底部提示）。
#: 以前异常被静默吞掉，导致"点了没反应"这种问题很难查。
BUTTON_ERROR_HOOK = None

#: 触屏拖动滚动期间置位：按钮据此忽略"按下 → 拖动"带来的误点击。
_DRAG_SCROLL = False


def drag_scrolling() -> bool:
    """当前是否正在用手指拖动页面（按钮的点击判定要跳过这种情况）。"""
    return _DRAG_SCROLL


def bind_touch_keyboard(widget) -> None:
    """触屏设备上：**用手指点**输入框才唤起系统屏幕键盘。

    用鼠标点不弹（有触摸屏的笔记本插着鼠标时不会莫名其妙冒键盘），
    程序自己聚焦（比如打开面板时自动聚焦）也不弹。
    """
    try:
        widget.bind("<ButtonPress-1>", lambda _e: TCH.show_keyboard_for_touch(),
                    add="+")
    except Exception:
        pass


def enable_touch_scroll(widget) -> None:
    """触屏：在列表 / 文本框里按住拖动时，滑的是它自己的内容，不是整页。

    鼠标拖动照旧（表格划选、文本框选字），互不干扰。
    """
    state = {"active": False, "y": 0, "moved": False}

    def on_press(event) -> None:
        state["active"] = bool(TCH.last_input_is_touch())
        state["y"] = event.y_root
        state["moved"] = False

    def on_move(event):
        if not state["active"]:
            return None
        delta = int(event.y_root) - int(state["y"])
        if not state["moved"]:
            if abs(delta) < ScrollArea.DRAG_THRESHOLD:
                return None
            state["moved"] = True
        if delta:
            _scroll_widget_by(widget, delta)
            state["y"] = event.y_root
        return "break"          # 不吃掉这一下就会变成"拖拽划选"

    def on_release(_event=None):
        state["active"] = False
        state["moved"] = False
        return None

    try:
        widget.bind("<ButtonPress-1>", on_press, add="+")
        widget.bind("<B1-Motion>", on_move, add="+")
        widget.bind("<ButtonRelease-1>", on_release, add="+")
    except Exception:
        pass


def _scroll_widget_by(widget, delta: int) -> None:
    """按像素滚动某个可滚动控件（表格 / 文本框都是 ``yview_moveto``）。"""
    try:
        first, last = widget.yview()
        span = max(1e-6, float(last) - float(first))
        height = max(1, int(widget.winfo_height()))
        target = float(first) - (float(delta) / height) * span
        widget.yview_moveto(max(0.0, min(1.0 - span, target)))
    except Exception:
        pass


def _scrollable_under(widget):
    """从指针下面的控件往上找，找它自己会滚动的那个（表格 / 文本框）。"""
    node = widget
    while node is not None:
        try:
            name = node.winfo_class()
        except Exception:
            name = ""
        if name in ("Treeview", "Text"):
            return node
        node = getattr(node, "master", None)
    return None


def report_button_error(exc: BaseException, source: str = "") -> None:
    hook = BUTTON_ERROR_HOOK
    if hook is not None:
        try:
            hook(exc, source)
            return
        except Exception:
            pass
    import traceback

    traceback.print_exception(type(exc), exc, exc.__traceback__)


def _drag_blocked(widget) -> bool:
    """这些控件自己要用拖动（表格划选 / 滚动条 / 文本框滚动），不做页面滑动。"""
    blocked = ("Treeview", "TScrollbar", "Scrollbar", "Text", "TCombobox",
               "Spinbox", "Scale")
    node = widget
    while node is not None:
        try:
            name = node.winfo_class()
        except Exception:
            name = ""
        if name in blocked:
            return True
        node = getattr(node, "master", None)
    return False


# ---------------------------------------------------------------------------
# 基础容器
# ---------------------------------------------------------------------------

class BorderBox(tk.Frame):
    """带 1px 边框的容器（直角）。"""

    def __init__(self, parent, border: str = T.BORDER, bg: str = T.CARD, **kwargs):
        super().__init__(parent, bg=border, bd=0, highlightthickness=0, **kwargs)
        self.inner = tk.Frame(self, bg=bg, bd=0, highlightthickness=0)
        self.inner.pack(fill="both", expand=True, padx=1, pady=1)

    def set_border(self, color: str) -> None:
        self.configure(bg=color)


class Card(BorderBox):
    """卡片：白底 + 1px 细边框 + 加粗标题（无圆角）。"""

    def __init__(self, parent, title: str = "", fonts=None, padx: int = None,
                 pady: int = None, bg: str = T.CARD):
        super().__init__(parent, border=T.BORDER, bg=bg)
        fonts = fonts or {}
        padx = T.px(12) if padx is None else padx
        pady = T.px(10) if pady is None else pady
        self.body = tk.Frame(self.inner, bg=bg)
        if title:
            head = tk.Frame(self.inner, bg=bg)
            head.pack(fill="x", padx=padx, pady=(pady, 0))
            tk.Label(
                head, text=title, bg=bg, fg=T.TEXT,
                font=fonts.get("subtitle"), anchor="w",
            ).pack(side="left")
            tk.Frame(self.inner, bg=T.BORDER, height=1).pack(
                fill="x", padx=padx, pady=(T.px(6), 0))
            self.body.pack(fill="both", expand=True, padx=padx, pady=(T.px(8), pady))
        else:
            self.body.pack(fill="both", expand=True, padx=padx, pady=pady)


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------

_BUTTON_KINDS = {
    "default": {
        "bg": T.BTN_BG, "fg": T.BTN_TEXT, "border": T.BTN_BORDER,
        "hover_bg": T.BTN_BG_HOVER, "hover_border": T.BTN_BORDER_HOVER,
        "press_bg": T.BTN_BG_PRESSED, "press_border": T.ACCENT_PRESSED,
    },
    "primary": {
        "bg": T.ACCENT, "fg": "#FFFFFF", "border": T.ACCENT,
        "hover_bg": T.ACCENT_HOVER, "hover_border": T.ACCENT_HOVER,
        "press_bg": T.ACCENT_PRESSED, "press_border": T.ACCENT_PRESSED,
    },
    "danger": {
        "bg": "#F3F3F3", "fg": T.DANGER, "border": "#C9C9C9",
        "hover_bg": "#FDF3F2", "hover_border": T.DANGER,
        "press_bg": "#F7DEDB", "press_border": T.DANGER_PRESSED,
    },
    "ghost": {
        "bg": T.BG, "fg": T.ACCENT, "border": T.BG,
        "hover_bg": "#E5F1FB", "hover_border": "#BBD9F2",
        "press_bg": "#CCE4F7", "press_border": T.ACCENT,
    },
}


class FlatButton(tk.Frame):
    """扁平按钮。``kind`` 取 default / primary / danger / ghost。"""

    def __init__(self, parent, text: str = "", command: Optional[Callable[[], None]] = None,
                 kind: str = "default", font=None, width: int = 0, height: int = 0,
                 padx: int = 14, pady: int = 6, anchor: str = "center", bg_parent=None):
        colors = _BUTTON_KINDS.get(kind, _BUTTON_KINDS["default"])
        super().__init__(parent, bg=colors["border"], bd=0, highlightthickness=0)
        self._colors = colors
        self._command = command
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._inner = tk.Frame(self, bg=colors["bg"], bd=0, highlightthickness=0)
        self._inner.pack(fill="both", expand=True, padx=1, pady=1)
        self._label = tk.Label(
            self._inner, text=text, bg=colors["bg"], fg=colors["fg"],
            font=font, padx=T.px(padx), pady=T.px(pady), anchor=anchor,
            justify="center", cursor="hand2",
        )
        self._label.pack(fill="both", expand=True)
        if width or height:
            self._inner.configure(width=T.px(width) if width else 1,
                                  height=T.px(height) if height else 1)
            self._inner.pack_propagate(False)
            self.configure(width=T.px(width) + 2 if width else 1,
                           height=T.px(height) + 2 if height else 1)
            self.pack_propagate(False)
        for widget in (self, self._inner, self._label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<ButtonPress-1>", self._on_press)
            widget.bind("<ButtonRelease-1>", self._on_release)

    # -- 状态 ------------------------------------------------------------

    def _apply(self) -> None:
        colors = self._colors
        if not self._enabled:
            bg, border, fg = T.BTN_DISABLED_BG, "#DCDCDC", T.TEXT_DISABLED
        elif self._pressed:
            bg, border = colors["press_bg"], colors["press_border"]
            fg = colors["fg"]
        elif self._hover:
            bg, border, fg = colors["hover_bg"], colors["hover_border"], colors["fg"]
        else:
            bg, border, fg = colors["bg"], colors["border"], colors["fg"]
        self.configure(bg=border)
        self._inner.configure(bg=bg)
        self._label.configure(bg=bg, fg=fg)

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        self._label.configure(cursor="hand2" if value else "arrow")
        self._apply()

    def set_text(self, text: str) -> None:
        self._label.configure(text=text)

    @property
    def text(self) -> str:
        return self._label.cget("text")

    def set_command(self, command: Callable[[], None]) -> None:
        self._command = command

    # -- 事件 ------------------------------------------------------------

    def _on_enter(self, _event=None) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._apply()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._apply()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self._apply()

    def _on_release(self, event=None) -> None:
        if not self._enabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._hover = True
        self._apply()
        # 触屏拖动滚动之后松手不算点击（否则滑动页面时会误触发按钮）
        if was_pressed and self._command and not drag_scrolling():
            try:
                self._command()
            except Exception as exc:
                report_button_error(exc, self.text)


# ---------------------------------------------------------------------------
# 输入框
# ---------------------------------------------------------------------------

class FlatEntry(BorderBox):
    """扁平输入框（1px 边框，聚焦时变强调色）。"""

    def __init__(self, parent, textvariable=None, show: str = "", width: int = 24,
                 font=None, justify: str = "left"):
        super().__init__(parent, border=T.BORDER, bg=T.CARD)
        self.var = textvariable or tk.StringVar()
        self.entry = tk.Entry(
            self.inner, textvariable=self.var, show=show, bd=0,
            highlightthickness=0, relief="flat", bg=T.CARD, fg=T.TEXT,
            insertbackground=T.TEXT, font=font, width=width, justify=justify,
            disabledbackground=T.BG_ALT, disabledforeground=T.TEXT_DIM,
        )
        self.entry.pack(fill="both", expand=True, padx=T.px(7), pady=T.px(5))
        self.entry.bind("<FocusIn>", lambda _e: self.set_border(T.ACCENT))
        self.entry.bind("<FocusOut>", lambda _e: self.set_border(T.BORDER))
        bind_touch_keyboard(self.entry)

    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value if value is not None else "")

    def focus_set(self) -> None:
        self.entry.focus_set()

    def set_enabled(self, value: bool) -> None:
        self.entry.configure(state="normal" if value else "disabled")

    def bind_return(self, callback: Callable[[], None]) -> None:
        self.entry.bind("<Return>", lambda _e: callback())

    def bind_change(self, callback: Callable[[], None]) -> None:
        self.var.trace_add("write", lambda *_a: callback())


class FlatText(BorderBox):
    """多行文本框。"""

    def __init__(self, parent, width: int = 40, height: int = 5, font=None,
                 wrap: str = "none", readonly: bool = False):
        super().__init__(parent, border=T.BORDER, bg=T.CARD)
        self.text = tk.Text(
            self.inner, bd=0, highlightthickness=0, relief="flat", bg=T.CARD,
            fg=T.TEXT, insertbackground=T.TEXT, font=font, wrap=wrap,
            width=width, height=height, padx=T.px(6), pady=T.px(4),
            state="disabled" if readonly else "normal",
        )
        scroll = ttk.Scrollbar(self.inner, orient="vertical",
                               command=self.text.yview, style="SV.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.bind("<FocusIn>", lambda _e: self.set_border(T.ACCENT))
        self.text.bind("<FocusOut>", lambda _e: self.set_border(T.BORDER))
        if not readonly:
            bind_touch_keyboard(self.text)
        enable_touch_scroll(self.text)

    def set_text(self, content: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.configure(state="disabled")

    def get(self) -> str:
        return self.text.get("1.0", "end-1c")


# ---------------------------------------------------------------------------
# 复选框 / 下拉框
# ---------------------------------------------------------------------------

class FlatCheck(tk.Frame):
    """自绘方形复选框（不用系统原生样式）。"""

    SIZE = 15

    def __init__(self, parent, text: str = "", value: bool = False, command=None,
                 font=None, bg: str = T.BG, size: int = 0):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self._size = size or self.SIZE
        self._value = bool(value)
        self._command = command
        self._enabled = True
        size = T.px(self._size)
        self.canvas = tk.Canvas(self, width=size, height=size, bg=bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(side="left")
        self.label = tk.Label(self, text=text, bg=bg, fg=T.TEXT, font=font,
                              cursor="hand2")
        self.label.pack(side="left", padx=(T.px(6), 0))
        for widget in (self.canvas, self.label):
            widget.bind("<Button-1>", self._toggle)
        self._draw()

    def _draw(self) -> None:
        size = T.px(self._size)
        self.canvas.delete("all")
        if self._value:
            fill = T.ACCENT if self._enabled else "#B9D5EC"
            self.canvas.create_rectangle(1, 1, size - 1, size - 1, fill=fill, outline=fill)
            self.canvas.create_line(
                size * 0.24, size * 0.52, size * 0.43, size * 0.72,
                size * 0.77, size * 0.29, fill="#FFFFFF",
                width=max(2, int(T.px(1.8))), capstyle="round",
            )
        else:
            border = T.BORDER_STRONG if self._enabled else "#D2D2D2"
            self.canvas.create_rectangle(1, 1, size - 1, size - 1, fill=T.CARD,
                                         outline=border)

    def _toggle(self, _event=None) -> None:
        if not self._enabled:
            return
        self._value = not self._value
        self._draw()
        if self._command:
            try:
                self._command(self._value)
            except Exception:
                pass

    @property
    def value(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = bool(value)
        self._draw()

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        self.canvas.configure(cursor="hand2" if value else "arrow")
        self.label.configure(cursor="hand2" if value else "arrow",
                             fg=T.TEXT if value else T.TEXT_DISABLED)
        self._draw()

    def set_bg(self, color: str) -> None:
        self.configure(bg=color)
        self.canvas.configure(bg=color)
        self.label.configure(bg=color)


class FlatSelect(BorderBox):
    """自绘下拉选择框：弹出的是自己画的菜单（不是系统原生菜单）。"""

    def __init__(self, parent, options: Sequence[Tuple[str, str]], value: str = "",
                 command=None, font=None, width: int = 0, bg: str = T.CARD):
        super().__init__(parent, border=T.BORDER, bg=bg)
        # 注意：不要用 self._options 这个名字 —— 那是 tkinter.Misc 的内部方法，
        # 覆盖它会让 pack/grid 直接报错。
        self._choices = list(options)
        self._command = command
        self._value = value or (self._choices[0][0] if self._choices else "")
        self._enabled = True
        if not width:
            # 自动按最长选项留足宽度（中文比英文宽，这里按字符数估算 + 余量）
            longest = max([len(text) for _key, text in self._choices] or [8])
            width = longest + 4
        self._width = width
        row = tk.Frame(self.inner, bg=bg)
        row.pack(fill="both", expand=True, padx=T.px(7), pady=T.px(4))
        self.label = tk.Label(row, text=self._label(), bg=bg, fg=T.TEXT, font=font,
                              anchor="w", cursor="hand2", width=width)
        self.label.pack(side="left", fill="x", expand=True)
        # 下拉箭头：自己画三角形，比字体里的 ▾ 大得多、也更清晰
        arrow_size = max(8, T.px(10))
        self.arrow = tk.Canvas(row, width=arrow_size, height=arrow_size, bg=bg,
                               highlightthickness=0, bd=0, cursor="hand2")
        self.arrow.pack(side="right", padx=(T.px(4), 0))
        self._arrow_size = arrow_size
        self._draw_arrow()
        for widget in (self, self.inner, row, self.label, self.arrow):
            widget.bind("<Button-1>", self._open)
        self._menu = None
        self._popup = None

    def _draw_arrow(self) -> None:
        size = self._arrow_size
        self.arrow.delete("all")
        half = size / 2.0
        top = size * 0.30
        self.arrow.create_polygon(
            size * 0.12, top, size * 0.88, top, half, size * 0.76,
            fill=T.TEXT_DIM if self._enabled else T.TEXT_DISABLED, outline="",
        )

    def set_bg(self, color: str) -> None:
        self.configure(bg=color)
        self.inner.configure(bg=color)
        self.label.configure(bg=color)
        self.arrow.configure(bg=color)

    def _label(self) -> str:
        for key, text in self._choices:
            if key == self._value:
                return text
        return self._value

    def _open(self, _event=None) -> None:
        if not self._enabled or not self._choices:
            return
        from .popup import PopupMenu

        if self._popup is not None:
            self._popup.close()
        popup = PopupMenu(
            self,
            [(text, (lambda k=key: self._choose(k))) for key, text in self._choices],
            font=self.label.cget("font"),
            width=max(len(text) for _key, text in self._choices) + 3,
            current=self._label(),
        )
        self._popup = popup
        popup.show(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())

    def _choose(self, key: str) -> None:
        self.set(key)
        if self._command:
            try:
                self._command(key)
            except Exception:
                pass

    @property
    def value(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value
        self.label.configure(text=self._label())

    def set_options(self, options: Sequence[Tuple[str, str]]) -> None:
        self._choices = list(options)
        self.label.configure(text=self._label())

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        self.label.configure(fg=T.TEXT if value else T.TEXT_DISABLED,
                             cursor="hand2" if value else "arrow")
        self._draw_arrow()


# ---------------------------------------------------------------------------
# 滚动容器
# ---------------------------------------------------------------------------

class ScrollArea(tk.Frame):
    """可上下滚动的容器（窗口变矮时页面内容仍然完整可见）。

    * 鼠标滚轮：指针在页面里就能滚；
    * **触屏 / 触控板：在页面里按住直接上下滑**（手指拖动，内容跟着走）；
      表格划选、滚动条、文本框这些"自己要用拖动"的控件不参与页面滑动。
    """

    #: 手指移动超过这么多像素才算"滑动"（避免把点击当成滑动）
    DRAG_THRESHOLD = 6

    def __init__(self, parent, bg: str = T.BG):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0, takefocus=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       command=self.canvas.yview,
                                       style="SV.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar_visible = False
        self._item_height = -1
        self.body = tk.Frame(self.canvas, bg=bg, bd=0, highlightthickness=0)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.body.bind("<Enter>", self._bind_wheel)
        self.body.bind("<Leave>", self._unbind_wheel)
        self._drag = None
        self._bind_drag()
        self._bind_configure()

    # -- 内部 ------------------------------------------------------------

    def _bind_configure(self) -> None:
        """内容后加进来时也要重新量一次高度（Configure 会冒泡到顶层窗口）。"""
        try:
            self.winfo_toplevel().bind("<Configure>", self._on_any_configure,
                                       add="+")
        except Exception:
            pass

    def _on_any_configure(self, event) -> None:
        if not self._contains(getattr(event, "widget", None)):
            return
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass
        self._sync_window_height()

    def _bind_drag(self) -> None:
        """把拖动事件挂到顶层窗口上：页面里任何位置按下都能滑动。"""
        try:
            top = self.winfo_toplevel()
        except Exception:
            return
        for sequence, handler in (("<ButtonPress-1>", self._drag_start),
                                  ("<B1-Motion>", self._drag_move),
                                  ("<ButtonRelease-1>", self._drag_end)):
            try:
                top.bind(sequence, handler, add="+")
            except Exception:
                pass

    def _drag_start(self, event) -> None:
        global _DRAG_SCROLL
        _DRAG_SCROLL = False
        self._drag = None
        widget = self._widget_under(event.x_root, event.y_root)
        if widget is None or not self._contains(widget) or _drag_blocked(widget):
            return
        self._drag = {"y": event.y_root, "start": event.y_root, "moved": False}

    def _drag_move(self, event) -> None:
        global _DRAG_SCROLL
        drag = self._drag
        if drag is None:
            return
        if not drag["moved"]:
            if abs(event.y_root - drag["start"]) < self.DRAG_THRESHOLD:
                return
            drag["moved"] = True
            _DRAG_SCROLL = True
        delta = event.y_root - drag["y"]
        if delta:
            self._scroll_pixels(delta)
            drag["y"] = event.y_root

    def _drag_end(self, _event=None) -> None:
        global _DRAG_SCROLL
        self._drag = None
        _DRAG_SCROLL = False

    def _widget_under(self, x_root: int, y_root: int):
        try:
            return self.winfo_containing(x_root, y_root)
        except Exception:
            return None

    def _contains(self, widget) -> bool:
        node = widget
        while node is not None:
            if node in (self, self.canvas, self.body):
                return True
            node = getattr(node, "master", None)
        return False

    def _scroll_pixels(self, delta: int) -> None:
        """按像素滚动：手指往下拖（delta > 0）就看到更早的内容。"""
        try:
            box = self.canvas.bbox("all")
            total = max(1, int((box[3] - box[1]) if box else 0))
            top = float(self.canvas.canvasy(0))
            self.canvas.yview_moveto(max(0.0, min(1.0, (top - delta) / float(total))))
        except Exception:
            pass

    def _on_scroll(self, first, last) -> None:
        needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        if needed and not self._scrollbar_visible:
            self.scrollbar.pack(side="right", fill="y")
            self._scrollbar_visible = True
        elif not needed and self._scrollbar_visible:
            self.scrollbar.pack_forget()
            self._scrollbar_visible = False
        self.scrollbar.set(first, last)

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        # 内容变高（例如后加的控件）时要同步 canvas 窗口项的高度，
        # 否则排在后面的控件分不到位置、会一直不显示。
        self._sync_window_height()

    def _on_canvas_configure(self, event) -> None:
        try:
            # 让内容宽度跟随窗口
            self.canvas.itemconfigure(self._window, width=event.width)
        except Exception:
            pass
        # 窗口比内容高时把内容拉到满高，避免出现空白带
        self._sync_window_height(event.height)

    def _sync_window_height(self, canvas_height: int = 0) -> None:
        """内容比窗口高时让 canvas 窗口项跟着内容长（否则后加的控件分不到位置），
        内容比窗口矮时把高度拉到窗口高，避免下面出现空白带。"""
        try:
            canvas_h = int(canvas_height or self.canvas.winfo_height())
            height = canvas_h if self.body.winfo_reqheight() <= canvas_h else 0
            if height != self._item_height:
                self._item_height = height
                self.canvas.itemconfigure(self._window, height=height)
        except Exception:
            pass

    def _bind_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        # 指针在"自己会滚的东西"上时（表格 / 文本框 / 滚动条），滚它自己，
        # 整页不要跟着一起动
        if _scrollable_under(getattr(event, "widget", None)) is not None:
            return
        if _drag_blocked(getattr(event, "widget", None)):
            return
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    # -- 对外 ------------------------------------------------------------

    def scroll_to_top(self) -> None:
        try:
            self.canvas.yview_moveto(0.0)
        except Exception:
            pass

    def refresh(self) -> None:
        self._on_body_configure()


# ---------------------------------------------------------------------------
# 标签条
# ---------------------------------------------------------------------------

CHECK_ON = "☑"
CHECK_OFF = "☐"


class CheckTree(tk.Frame):
    """带复选框列的表格。

    * 第 1 列是复选框：点一下切换，点表头全选 / 全不选；
    * ``selectmode="extended"``：支持 Ctrl / Shift 多选，也支持按住鼠标拖拽划选；
    * ``on_double`` / ``on_menu`` 由各页面挂自己的功能（右键菜单）。
    """

    def __init__(self, parent, columns, height: int = 12, stretch_column: str = "",
                 checkable: bool = True, on_double=None, on_menu=None):
        super().__init__(parent, bg=T.BORDER)
        self.checkable = checkable
        all_columns = ([("__sel", "", 34, "center")] if checkable else []) + list(columns)
        self.tree = ttk.Treeview(
            self, columns=[c[0] for c in all_columns], show="headings",
            style="SV.Treeview", height=height, selectmode="extended",
        )
        for key, title, width, anchor in all_columns:
            if key == "__sel":
                self.tree.heading("__sel", text=CHECK_OFF, anchor="center",
                                  command=self.toggle_all)
                self.tree.column("__sel", width=T.px(width), minwidth=T.px(width),
                                 anchor="center", stretch=False)
                continue
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=T.px(width), minwidth=T.px(40),
                             anchor=anchor, stretch=(key == stretch_column))
        bar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview,
                            style="SV.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.tree.tag_configure("odd", background=T.BG_ALT)
        self.tree.tag_configure("even", background=T.CARD)
        self.tree.tag_configure("checked", background="#EAF3FB")
        self.checked = set()          # 勾选行的业务 id
        self._keys = {}               # iid -> 业务 id
        if checkable:
            self.tree.bind("<Button-1>", self._on_click, add="+")
        # 触屏：在表格里按住拖动 = 滑动表格内容（鼠标拖动仍然是划选多行）
        enable_touch_scroll(self.tree)
        if on_double:
            self.tree.bind("<Double-1>", lambda _e: on_double())
        if on_menu:
            self.tree.bind("<Button-3>", on_menu)

    # -- 复选框 ----------------------------------------------------------

    def _on_click(self, event):
        try:
            if self.tree.identify_region(event.x, event.y) != "cell":
                return
            if self.tree.identify_column(event.x) != "#1":
                return
            iid = self.tree.identify_row(event.y)
            if iid:
                self._toggle(iid)
            return "break"
        except Exception:
            return

    def _toggle(self, iid: str) -> None:
        key = self._keys.get(iid, iid)
        if key in self.checked:
            self.checked.discard(key)
        else:
            self.checked.add(key)
        self._paint_row(iid)

    def _paint_row(self, iid: str) -> None:
        key = self._keys.get(iid, iid)
        values = list(self.tree.item(iid, "values"))
        if self.checkable and values:
            values[0] = CHECK_ON if key in self.checked else CHECK_OFF
            self.tree.item(iid, values=values)
        tags = [t for t in self.tree.item(iid, "tags") if t != "checked"]
        if key in self.checked:
            tags.append("checked")
        self.tree.item(iid, tags=tags)
        self._update_header()

    def _update_header(self) -> None:
        if not self.checkable:
            return
        total = len(self._keys)
        text = CHECK_ON if total and len(self.checked) >= total else CHECK_OFF
        self.tree.heading("__sel", text=text)

    def toggle_all(self) -> None:
        if not self.checkable:
            return
        if self._keys and len(self.checked) >= len(self._keys):
            self.checked.clear()
        else:
            self.checked = set(self._keys.values())
        for iid in self.tree.get_children():
            self._paint_row(iid)

    # -- 数据 ------------------------------------------------------------

    def set_rows(self, rows, keys=None) -> None:
        self.tree.delete(*self.tree.get_children())
        self._keys = {}
        for index, row in enumerate(rows):
            iid = str(index + 1)
            key = keys[index] if keys else iid
            self._keys[iid] = key
            values = list(row)
            if self.checkable:
                values = [CHECK_ON if key in self.checked else CHECK_OFF] + values
            self.tree.insert("", "end", iid=iid, values=tuple(values),
                             tags=("odd" if index % 2 else "even",))
        known = set(self._keys.values())
        self.checked = {key for key in self.checked if key in known}
        for iid in self.tree.get_children():
            if self._keys.get(iid) in self.checked:
                self._paint_row(iid)
        self._update_header()

    def checked_keys(self) -> list:
        order = [self._keys[iid] for iid in self.tree.get_children()]
        return [key for key in order if key in self.checked]

    def selected_keys(self) -> list:
        return [self._keys.get(iid, iid) for iid in self.tree.selection()]

    def action_keys(self) -> list:
        """批量操作取哪些行：有勾选就用勾选的，否则用鼠标选中的。"""
        return self.checked_keys() or self.selected_keys()

    def selected_index(self) -> int:
        selection = self.tree.selection()
        if not selection:
            return -1
        try:
            return int(selection[0]) - 1
        except ValueError:
            return -1

    def select_row(self, index: int) -> None:
        iid = str(index + 1)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)


class TabBar(tk.Frame):
    """扁平标签条：选中项白底 + 底部 2px 强调色下划线。"""

    def __init__(self, parent, titles: Sequence[str], command: Callable[[int], None],
                 font=None):
        super().__init__(parent, bg=T.TAB_BG)
        self._command = command
        self._tabs: List[tk.Frame] = []
        self._labels: List[tk.Label] = []
        self._underlines: List[tk.Frame] = []
        self._current = 0
        tk.Frame(self, bg=T.BORDER, height=1).pack(side="bottom", fill="x")
        for index, title in enumerate(titles):
            tab = tk.Frame(self, bg=T.TAB_BG, bd=0, highlightthickness=0,
                           cursor="hand2")
            tab.pack(side="left", fill="y")
            label = tk.Label(tab, text=title, bg=T.TAB_BG, fg=T.TEXT, font=font,
                             padx=T.px(16), pady=T.px(8), cursor="hand2")
            label.pack(side="top", fill="both", expand=True)
            underline = tk.Frame(tab, bg=T.TAB_BG, height=2)
            underline.pack(side="bottom", fill="x")
            for widget in (tab, label):
                widget.bind("<Button-1>", lambda _e, i=index: self.select(i))
                widget.bind("<Enter>", lambda _e, i=index: self._hover(i, True))
                widget.bind("<Leave>", lambda _e, i=index: self._hover(i, False))
            self._tabs.append(tab)
            self._labels.append(label)
            self._underlines.append(underline)
        self.select(0)

    def _paint(self) -> None:
        for index in range(len(self._tabs)):
            active = index == self._current
            bg = T.TAB_ACTIVE_BG if active else T.TAB_BG
            self._tabs[index].configure(bg=bg)
            self._labels[index].configure(bg=bg, fg=T.TEXT if active else "#4A4A4A")
            self._underlines[index].configure(bg=T.ACCENT if active else bg)

    def _hover(self, index: int, entering: bool) -> None:
        if index == self._current:
            return
        self._tabs[index].configure(bg="#E0E0E0" if entering else T.TAB_BG)
        self._labels[index].configure(bg="#E0E0E0" if entering else T.TAB_BG)
        self._underlines[index].configure(bg="#E0E0E0" if entering else T.TAB_BG)

    def select(self, index: int, notify: bool = True) -> None:
        if index < 0 or index >= len(self._tabs):
            return
        self._current = index
        self._paint()
        if notify and self._command:
            try:
                self._command(index)
            except Exception:
                pass

    @property
    def current(self) -> int:
        return self._current


# ---------------------------------------------------------------------------
# 状态栏 / 进度条 / 提示
# ---------------------------------------------------------------------------

class StatusBar(tk.Frame):
    """底部三段式状态栏。"""

    def __init__(self, parent, font=None):
        super().__init__(parent, bg=T.HEADER_BG)
        tk.Frame(self, bg=T.BORDER, height=1).pack(side="top", fill="x")
        row = tk.Frame(self, bg=T.HEADER_BG)
        row.pack(fill="x")
        self._labels: List[tk.Label] = []
        for index in range(3):
            if index:
                tk.Frame(row, bg=T.BORDER, width=1).pack(side="left", fill="y",
                                                         padx=T.px(2), pady=T.px(3))
            label = tk.Label(row, text="", bg=T.HEADER_BG, fg=T.TEXT_DIM,
                             font=font, anchor="w", padx=T.px(8), pady=T.px(3))
            label.pack(side="left", fill="x", expand=(index == 0))
            self._labels.append(label)

    def set(self, index: int, text: str) -> None:
        if 0 <= index < len(self._labels):
            self._labels[index].configure(text=text)


class NotifyBar(tk.Frame):
    """底部统一提示条：所有「点按钮之后的反馈」都显示在这里。

    级别：info（灰）/ success（绿）/ warn（橙）/ error（红）。
    info 与 success 会自动淡出，warn 与 error 会一直留着直到下一条提示。
    """

    LEVELS = {
        "info": (T.TEXT_DIM, "i"),
        "success": (T.SUCCESS, "√"),
        "warn": (T.WARN, "!"),
        "error": (T.DANGER, "×"),
    }
    IDLE = "就绪"

    def __init__(self, parent, font=None, bg: str = T.BG_ALT):
        super().__init__(parent, bg=bg)
        self._bg = bg
        tk.Frame(self, bg=T.BORDER, height=1).pack(side="top", fill="x")
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        self.marker = tk.Label(row, text="i", bg=bg, fg=T.TEXT_DIM, font=font,
                               width=2, anchor="center")
        self.marker.pack(side="left", padx=(T.px(6), 0), pady=T.px(3))
        self.label = tk.Label(row, text=self.IDLE, bg=bg, fg=T.TEXT_DIM, font=font,
                              anchor="w", justify="left")
        self.label.pack(side="left", fill="x", expand=True, padx=(0, T.px(6)))
        self._after_id = None

    def show(self, text: str, level: str = "info", timeout: int = 0) -> None:
        color, mark = self.LEVELS.get(level, self.LEVELS["info"])
        self.marker.configure(text=mark, fg=color)
        self.label.configure(text=text or self.IDLE, fg=color if text else T.TEXT_DIM)
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if timeout <= 0 and level in ("info", "success"):
            timeout = 6000
        if timeout > 0 and text:
            self._after_id = self.after(timeout, self.clear)

    def clear(self) -> None:
        self._after_id = None
        self.marker.configure(text="i", fg=T.TEXT_DIM)
        self.label.configure(text=self.IDLE, fg=T.TEXT_DIM)

class ProgressBar(tk.Canvas):
    """扁平进度条（直角）。"""

    def __init__(self, parent, width: int = 240, height: int = 6, bg: str = T.BG):
        super().__init__(parent, width=T.px(width), height=T.px(height), bg=bg,
                         bd=0, highlightthickness=0)
        self._value = 0.0
        self._height = T.px(height)
        self._width = T.px(width)
        self.bind("<Configure>", self._redraw)
        self._redraw()

    def set(self, value: float) -> None:
        self._value = max(0.0, min(1.0, float(value)))
        self._redraw()

    def _redraw(self, _event=None) -> None:
        self.delete("all")
        width = self.winfo_width() or self._width
        height = self.winfo_height() or self._height
        self.create_rectangle(0, 0, width, height, fill="#E1E1E1", outline="#E1E1E1")
        filled = int(width * self._value)
        if filled > 0:
            self.create_rectangle(0, 0, filled, height, fill=T.ACCENT, outline=T.ACCENT)


class Hint(tk.Label):
    """提示文字（默认灰色小字；可指定颜色）。"""

    def __init__(self, parent, text: str = "", font=None, color: str = T.TEXT_DIM,
                 bg: str = T.BG, **kwargs):
        super().__init__(parent, text=text, font=font, fg=color, bg=bg,
                         anchor="w", justify="left", **kwargs)

    def set_text(self, text: str, color: str = "") -> None:
        self.configure(text=text)
        if color:
            self.configure(fg=color)


def separator(parent, bg: str = T.BG) -> tk.Frame:
    frame = tk.Frame(parent, bg=bg)
    tk.Frame(frame, bg=T.BORDER, height=1).pack(fill="x")
    return frame


def labeled_row(parent, label: str, font=None, bg: str = T.BG, label_width: int = 12):
    """返回 (行容器, 值区域容器)：左侧固定宽度标签 + 右侧自适应内容。"""
    row = tk.Frame(parent, bg=bg)
    text = tk.Label(row, text=label, bg=bg, fg=T.TEXT_DIM, font=font,
                    anchor="w", width=label_width)
    text.pack(side="left", anchor="n")
    holder = tk.Frame(row, bg=bg)
    holder.pack(side="left", fill="x", expand=True)
    return row, holder
