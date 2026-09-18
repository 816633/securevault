"""自绘弹出菜单（下拉框与右键菜单都用它，避免系统原生菜单的外观）。

实现要点：
  * 用 ``overrideredirect`` 的 Toplevel 自己画边框和条目，直角扁平；
  * ``grab_set`` 抢焦点，点外面 / Esc / 失焦都会自动关闭；
  * 关闭时一定会 ``grab_release`` 并销毁窗口，不会留下"点不了"的幽灵窗口。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional, Sequence, Tuple

from . import theme as T

SEPARATOR = None


class PopupMenu:
    """一个轻量的自绘弹出菜单。"""

    def __init__(self, parent, items: Sequence[Tuple[str, Callable]], font=None,
                 width: int = 0, current: str = "", on_close: Optional[Callable] = None):
        self.parent = parent
        self.items = list(items)
        self.font = font
        self.width = width
        self.current = current
        self.on_close = on_close
        self.window: Optional[tk.Toplevel] = None

    # -- 显示 / 关闭 -----------------------------------------------------

    def show(self, x: int, y: int) -> None:
        self.close()
        window = tk.Toplevel(self.parent)
        window.withdraw()
        window.overrideredirect(True)
        window.configure(bg=T.BORDER_STRONG)
        try:
            window.attributes("-topmost", True)
        except Exception:
            pass
        self.window = window

        inner = tk.Frame(window, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        rows: List[tk.Widget] = []
        for index, item in enumerate(self.items):
            text, callback = item
            marked = bool(self.current) and text == self.current
            label = tk.Label(
                inner, text=("● " if marked else "   ") + text,
                bg=T.CARD, fg=T.ACCENT if marked else T.TEXT,
                font=self.font, anchor="w", padx=T.px(10), pady=T.px(4),
                cursor="hand2", width=self.width or 0,
            )
            label.pack(fill="x")
            label.bind("<Enter>", lambda _e, w=label: w.configure(bg=T.ACCENT_SOFT))
            label.bind("<Leave>", lambda _e, w=label: w.configure(bg=T.CARD))
            label.bind("<Button-1>", lambda _e, cb=callback: self._choose(cb))
            rows.append(label)

        window.update_idletasks()
        width = max(window.winfo_reqwidth(), (self.width or 0) * T.px(7))
        height = window.winfo_reqheight()
        screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
        x = max(0, min(x, screen_w - width - 4))
        if y + height > screen_h - 4:
            y = max(0, y - height)
        window.geometry("%dx%d+%d+%d" % (width, height, x, y))
        window.deiconify()
        try:
            window.grab_set()
        except Exception:
            pass
        window.bind("<Escape>", lambda _e: self.close())
        window.bind("<FocusOut>", lambda _e: self.close())
        window.bind("<Button-1>", self._maybe_close)
        window.focus_set()

    def _maybe_close(self, event) -> None:
        """点击落在菜单外面就关闭（grab 之后外面的事件也会送到这里）。"""
        if self.window is None:
            return
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        if not (0 <= event.x <= width and 0 <= event.y <= height):
            self.close()

    def _choose(self, callback) -> None:
        self.close()
        if callback:
            try:
                callback()
            except Exception:
                pass

    def close(self) -> None:
        window, self.window = self.window, None
        if window is None:
            return
        try:
            window.grab_release()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass


def show_menu(parent, items, x: int, y: int, font=None, current: str = "") -> None:
    """一次性弹出一个菜单（右键菜单用）。"""
    PopupMenu(parent, items, font=font, current=current).show(x, y)
