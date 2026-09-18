"""页面基类与公共小工具。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence, Tuple

from .. import theme as T
from .. import widgets as W


class Page(tk.Frame):
    """标签页基类。子类实现 build 与 refresh。"""

    title = "页面"

    def __init__(self, parent, ctx):
        super().__init__(parent, bg=T.BG)
        self.ctx = ctx
        self.fonts = ctx.fonts
        self._built = False
        self.build()
        self._built = True

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        pass

    def on_show(self) -> None:
        self.refresh()

    def scroll_area(self) -> W.ScrollArea:
        area = W.ScrollArea(self)
        area.pack(fill="both", expand=True)
        return area

    def make_tree(self, parent, columns: Sequence[Tuple[str, str, int, str]],
                  height: int = 12, stretch_column: str = "", checkable: bool = True,
                  on_double=None, on_menu=None):
        """创建统一风格的表格（带复选框、支持拖拽多选）；每列是
        (列 id, 表头, 宽度 DIP, 对齐)。"""
        holder = W.CheckTree(parent, columns, height=height,
                             stretch_column=stretch_column, checkable=checkable,
                             on_double=on_double, on_menu=on_menu)
        holder.tree.holder = holder  # 让 fill_tree / selected_index 找得到它
        return holder

    def fill_tree(self, tree, rows, keys=None) -> None:
        holder = getattr(tree, "holder", None)
        if holder is not None:
            holder.set_rows(rows, keys)
            return
        tree.delete(*tree.get_children())
        for index, row in enumerate(rows):
            tree.insert("", "end", iid=str(index + 1), values=tuple(row),
                        tags=("odd" if index % 2 else "even",))

    def action_keys(self, tree) -> list:
        """批量操作要处理的行：优先勾选的，其次鼠标选中的。"""
        holder = getattr(tree, "holder", None)
        return holder.action_keys() if holder is not None else []

    def selected_index(self, tree) -> int:
        holder = getattr(tree, "holder", None)
        if holder is not None:
            return holder.selected_index()
        selection = tree.selection()
        if not selection:
            return -1
        try:
            return int(selection[0]) - 1
        except ValueError:
            return -1

    def button_row(self, parent, buttons) -> tk.Frame:
        """一行按钮；每项是 (文字, 回调, 样式)。"""
        row = tk.Frame(parent, bg=parent.cget("bg"))
        for text, callback, kind in buttons:
            W.FlatButton(row, text=text, kind=kind, command=callback,
                         font=self.fonts.get("base")).pack(side="left",
                                                           padx=(0, T.px(8)))
        return row

    def hint(self, parent, text: str = "", color: str = T.TEXT_DIM) -> W.Hint:
        """页面提示。

        返回的对象有两个身份：
          * 构造时给的文字是**静态说明**，仍然按原来位置摆在页面里；
          * 之后调用 ``set_text`` 的内容属于**操作反馈**，统一显示到窗口底部提示条，
            不再往卡片里塞（用户反馈：卡片里的提示看不清）。
        """
        return HintProxy(self, parent, text, color)


class HintProxy:
    """把页面提示的「静态说明」与「动态反馈」分开处理的代理对象。"""

    def __init__(self, page: "Page", parent, text: str, color: str) -> None:
        self._page = page
        self._widget = W.Hint(parent, text, font=page.fonts.get("small"),
                              color=color, bg=parent.cget("bg"))
        self._last = text

    # -- 静态说明：按原来的位置摆放 --------------------------------------

    def pack(self, *args, **kwargs):
        self._widget.pack(*args, **kwargs)
        return self

    def grid(self, *args, **kwargs):
        self._widget.grid(*args, **kwargs)
        return self

    def place(self, *args, **kwargs):
        self._widget.place(*args, **kwargs)
        return self

    def configure(self, **kwargs):
        self._widget.configure(**kwargs)
        return self

    config = configure

    def cget(self, key: str):
        if key == "text":
            return self._last
        return self._widget.cget(key)

    def __getattr__(self, item):
        return getattr(self._widget, item)

    # -- 动态反馈：送到窗口底部提示条 ------------------------------------

    def set_text(self, text: str, color: str = "") -> None:
        self._last = text or ""
        level = "info"
        if color == T.SUCCESS:
            level = "success"
        elif color == T.WARN:
            level = "warn"
        elif color == T.DANGER:
            level = "error"
        if text:
            self._page.ctx.notify(text, level)
