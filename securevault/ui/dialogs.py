"""模态对话框（全部使用自绘控件，保持统一的扁平风格）。"""

from __future__ import annotations

import os
import tkinter as tk
from typing import List, Optional, Sequence

from . import theme as T
from . import widgets as W

#: 自动化测试用：置为 True 后对话框不会阻塞等待（不进入嵌套事件循环）。
NON_BLOCKING = False


class BaseDialog(tk.Toplevel):
    """所有对话框的基类：居中、模态、直角扁平。"""

    def __init__(self, parent, title: str, width: int = 420, height: int = 0):
        super().__init__(parent, bg=T.BG)
        # 先藏起来：等尺寸算好、内容摆好再显示，避免先在左上角闪一下。
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.fonts = getattr(parent, "fonts", None) or T.init_fonts(self)
        icon = T.icon_path()
        if icon:
            try:
                self.iconbitmap(icon)
            except Exception:
                pass
        self.body = tk.Frame(self, bg=T.BG)
        self.body.pack(fill="both", expand=True, padx=T.px(18), pady=T.px(16))
        self._width = width
        self._height = height
        self.bind("<Escape>", lambda _e: self._escape())

    def _escape(self) -> None:
        self.result = None
        self.destroy()

    def finish(self) -> None:
        """计算尺寸、居中并 grab。"""
        self.update_idletasks()
        width = max(T.px(self._width), self.winfo_reqwidth())
        height = self.winfo_reqheight() if not self._height else T.px(self._height)
        parent = self.master
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            py = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        except Exception:
            px = py = 200
        px = max(0, min(px, self.winfo_screenwidth() - width - 10))
        py = max(0, min(py, self.winfo_screenheight() - height - 40))
        self.geometry("%dx%d+%d+%d" % (width, height, px, py))
        self.deiconify()
        self.lift()
        self.grab_set()
        self.focus_force()
        if not NON_BLOCKING:
            self.wait_window(self)


def _buttons_row(dialog: BaseDialog, specs: Sequence[str], kinds: Sequence[str],
                 callback) -> tk.Frame:
    row = tk.Frame(dialog, bg=T.BG)
    row.pack(fill="x", padx=T.px(18), pady=(0, T.px(16)))
    for index in reversed(range(len(specs))):
        button = W.FlatButton(
            row, text=specs[index], kind=kinds[index],
            command=lambda i=index: callback(i), font=dialog.fonts.get("base"),
            padx=16, pady=6,
        )
        button.pack(side="right", padx=(T.px(8), 0))
    return row


def message(parent, title: str, text: str, buttons: Sequence[str] = ("确定",),
            kind: str = "info", detail: str = "") -> int:
    """信息 / 警告 / 错误 / 询问对话框，返回被点击按钮的序号。"""
    dialog = BaseDialog(parent, title, width=460)
    fonts = dialog.fonts
    colors = {"info": T.ACCENT, "warn": T.WARN, "error": T.DANGER,
              "question": T.ACCENT}
    accent = colors.get(kind, T.ACCENT)
    head = tk.Frame(dialog.body, bg=T.BG)
    head.pack(fill="x")
    mark = {"info": "i", "warn": "!", "error": "×", "question": "?"}.get(kind, "i")
    tk.Label(head, text=mark, bg=accent, fg="#FFFFFF", font=fonts.get("subtitle"),
             width=2, height=1).pack(side="left", padx=(0, T.px(10)))
    text_box = tk.Frame(head, bg=T.BG)
    text_box.pack(side="left", fill="x", expand=True)
    tk.Label(text_box, text=text, bg=T.BG, fg=T.TEXT, font=fonts.get("base"),
             anchor="w", justify="left", wraplength=T.px(360)).pack(fill="x")
    if detail:
        tk.Label(text_box, text=detail, bg=T.BG, fg=T.TEXT_DIM,
                 font=fonts.get("small"), anchor="w", justify="left",
                 wraplength=T.px(360)).pack(fill="x", pady=(T.px(6), 0))
    result = {"index": len(buttons) - 1}

    def on_click(index: int) -> None:
        result["index"] = index
        dialog.destroy()

    # 按钮也要有样式区分：确认/危险操作用强调色，取消类用普通样式
    kinds = []
    for index, label in enumerate(buttons):
        cancel_like = index == len(buttons) - 1 and len(buttons) > 1
        if cancel_like or label in ("取消", "关闭", "再看看", "重新输入"):
            kinds.append("default")
        elif kind in ("warn", "error"):
            kinds.append("danger")
        else:
            kinds.append("primary")
    _buttons_row(dialog, list(buttons), kinds, on_click)
    dialog.finish()
    return result["index"]


def ask_password(parent, title: str, label: str, ok_text: str = "确定",
                 confirm_label: str = "", require_length: int = 0) -> Optional[str]:
    """密码输入框；取消返回 None。``confirm_label`` 非空时要求两次输入一致。"""
    dialog = BaseDialog(parent, title, width=430)
    fonts = dialog.fonts
    tk.Label(dialog.body, text=label, bg=T.BG, fg=T.TEXT, font=fonts.get("base"),
             anchor="w", justify="left", wraplength=T.px(390)).pack(fill="x")
    pwd = W.FlatEntry(dialog.body, show="●", width=28, font=fonts.get("base"))
    pwd.pack(fill="x", pady=(T.px(10), T.px(4)))
    confirm = None
    if confirm_label:
        tk.Label(dialog.body, text=confirm_label, bg=T.BG, fg=T.TEXT,
                 font=fonts.get("base"), anchor="w").pack(fill="x", pady=(T.px(6), 0))
        confirm = W.FlatEntry(dialog.body, show="●", width=28, font=fonts.get("base"))
        confirm.pack(fill="x", pady=(T.px(4), 0))
    error = W.Hint(dialog.body, "", font=fonts.get("small"), color=T.DANGER)
    error.pack(fill="x", pady=(T.px(6), 0))
    result = {"value": None}

    def submit() -> None:
        value = pwd.get()
        if require_length and len(value) < require_length:
            error.set_text("至少需要 %d 位" % require_length)
            return
        if confirm is not None and value != confirm.get():
            error.set_text("两次输入不一致")
            return
        result["value"] = value
        dialog.destroy()

    row = tk.Frame(dialog, bg=T.BG)
    row.pack(fill="x", padx=T.px(18), pady=(0, T.px(16)))
    W.FlatButton(row, text="取消", kind="default", font=fonts.get("base"),
                 command=dialog.destroy, padx=16, pady=6).pack(side="right")
    W.FlatButton(row, text=ok_text, kind="primary", font=fonts.get("base"),
                 command=submit, padx=16, pady=6).pack(side="right", padx=(0, T.px(8)))
    pwd.bind_return(submit)
    if confirm is not None:
        confirm.bind_return(submit)
    dialog.after(50, pwd.focus_set)
    dialog.finish()
    return result["value"]


def ask_text(parent, title: str, label: str, value: str = "", ok_text: str = "确定",
             width: int = 34) -> Optional[str]:
    dialog = BaseDialog(parent, title, width=430)
    fonts = dialog.fonts
    tk.Label(dialog.body, text=label, bg=T.BG, fg=T.TEXT, font=fonts.get("base"),
             anchor="w", justify="left", wraplength=T.px(390)).pack(fill="x")
    entry = W.FlatEntry(dialog.body, width=width, font=fonts.get("base"))
    entry.set(value)
    entry.pack(fill="x", pady=(T.px(10), 0))
    result = {"value": None}

    def submit() -> None:
        result["value"] = entry.get()
        dialog.destroy()

    row = tk.Frame(dialog, bg=T.BG)
    row.pack(fill="x", padx=T.px(18), pady=(0, T.px(16)))
    W.FlatButton(row, text="取消", kind="default", font=fonts.get("base"),
                 command=dialog.destroy, padx=16, pady=6).pack(side="right")
    W.FlatButton(row, text=ok_text, kind="primary", font=fonts.get("base"),
                 command=submit, padx=16, pady=6).pack(side="right", padx=(0, T.px(8)))
    entry.bind_return(submit)
    dialog.after(50, entry.focus_set)
    dialog.finish()
    return result["value"]


def show_recovery(parent, code: str) -> bool:
    """展示恢复码（只显示一次），返回是否已确认抄写。"""
    dialog = BaseDialog(parent, "SecureVault · 恢复码", width=520)
    fonts = dialog.fonts
    tk.Label(dialog.body, text="这是你的恢复码，只显示这一次", bg=T.BG, fg=T.TEXT,
             font=fonts.get("subtitle"), anchor="w").pack(fill="x")
    tk.Label(
        dialog.body,
        text="忘记密码时用它重置密码。请抄写或截图保存——关闭本窗口后程序不会再显示它。",
        bg=T.BG, fg=T.TEXT_DIM, font=fonts.get("base"), anchor="w", justify="left",
        wraplength=T.px(460),
    ).pack(fill="x", pady=(T.px(6), T.px(10)))
    box = W.BorderBox(dialog.body, border=T.ACCENT, bg=T.CARD)
    box.pack(fill="x")
    label = tk.Label(box.inner, text=code, bg=T.CARD, fg=T.ACCENT,
                     font=(fonts.get("mono").cget("family"), 18, "bold"),
                     pady=T.px(12))
    label.pack(fill="x")
    status = W.Hint(dialog.body, "", font=fonts.get("small"), color=T.SUCCESS)
    status.pack(fill="x", pady=(T.px(8), 0))

    def copy_code() -> None:
        dialog.clipboard_clear()
        dialog.clipboard_append(code)
        status.set_text("已复制到剪贴板", T.SUCCESS)

    def save_code() -> None:
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            parent=dialog, title="保存恢复码", defaultextension=".txt",
            initialfile="SecureVault-恢复码.txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("SecureVault 恢复码（%s）\n\n%s\n\n"
                         "忘记密码时用它重置密码；重置后会生成新的恢复码。\n"
                         % (code, code))
            status.set_text("已保存到：%s" % path, T.SUCCESS)
        except OSError as exc:
            status.set_text("保存失败：%s" % exc, T.DANGER)

    row = tk.Frame(dialog.body, bg=T.BG)
    row.pack(fill="x", pady=(T.px(10), 0))
    W.FlatButton(row, text="复制恢复码", kind="default", font=fonts.get("base"),
                 command=copy_code).pack(side="left")
    W.FlatButton(row, text="另存为 txt…", kind="default", font=fonts.get("base"),
                 command=save_code).pack(side="left", padx=(T.px(8), 0))
    result = {"ok": False}

    def confirm() -> None:
        answer = message(dialog, "确认", "你确定已经保存好恢复码了吗？",
                         ("我已保存", "再看看"), kind="question")
        if answer == 0:
            result["ok"] = True
            dialog.destroy()

    _buttons_row(dialog, ["我已保存恢复码"], ["primary"],
                 lambda _i: confirm())
    dialog.finish()
    return result["ok"]


def show_details(parent, title: str, rows: Sequence[Sequence[str]]) -> None:
    """表格化展示详情（两列：字段名 / 值）。"""
    dialog = BaseDialog(parent, title, width=560)
    fonts = dialog.fonts
    area = W.ScrollArea(dialog.body)
    area.pack(fill="both", expand=True)
    holder = area.body
    for index, pair in enumerate(rows):
        key, value = pair[0], pair[1]
        row = tk.Frame(holder, bg=T.BG if index % 2 == 0 else T.BG_ALT)
        row.pack(fill="x")
        tk.Label(row, text=key, bg=row.cget("bg"), fg=T.TEXT_DIM,
                 font=fonts.get("base"), width=14, anchor="w").pack(
            side="left", padx=(T.px(6), 0), pady=T.px(2))
        tk.Label(row, text=value or "-", bg=row.cget("bg"), fg=T.TEXT,
                 font=fonts.get("base"), anchor="w", justify="left",
                 wraplength=T.px(380)).pack(side="left", fill="x", expand=True,
                                            padx=(0, T.px(6)))
    _buttons_row(dialog, ["关闭"], ["primary"], lambda _i: dialog.destroy())
    dialog.finish()


# ---------------------------------------------------------------------------
# 老式（最原生）的密码 / 恢复码窗口
# ---------------------------------------------------------------------------

#: 老式弹窗的底色：正常窗口灰（不是 Windows 95 那种深灰，看起来"老一点"但不刺眼）
OLD_BG = "#F0F0F0"


def center_window(win, width: int, height: int, parent=None) -> None:
    """把窗口放到屏幕正中（或父窗口正中）。"""
    win.update_idletasks()
    if parent is not None:
        try:
            if parent.winfo_ismapped():
                cx = parent.winfo_rootx() + parent.winfo_width() // 2
                cy = parent.winfo_rooty() + parent.winfo_height() // 2
                win.geometry("%dx%d+%d+%d" % (width, height,
                                              max(0, cx - width // 2),
                                              max(0, cy - height // 2)))
                return
        except Exception:
            pass
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 3)
    win.geometry("%dx%d+%d+%d" % (width, height, x, y))


class OldStyleDialog(tk.Toplevel):
    """老式弹窗：原生输入框与按钮、普通窗口底色（不做美化，但也不回到 Win95 的样子）。"""

    def __init__(self, parent, title: str, width: int = 460, height: int = 230):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=OLD_BG)
        self.resizable(False, False)
        # 注意：父窗口被 withdraw 时不能再设 transient —— 否则弹窗自己也不会显示。
        try:
            if parent is not None and parent.winfo_ismapped():
                self.transient(parent)
        except Exception:
            pass
        self.result = None
        self._width = width
        self._height = height
        self.body = tk.Frame(self, bg=OLD_BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=14)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _ok(self) -> None:
        self.destroy()

    def reveal(self, parent=None) -> None:
        center_window(self, self._width, self._height, parent)
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except Exception:
            pass
        self.focus_force()
        if not NON_BLOCKING:
            self.wait_window(self)


class PasswordDialog(OldStyleDialog):
    """密码输入弹窗：现代扁平、但**不做多余美化**（按钮默认灰、文字默认色），
    尺寸与间距都放大，输入框和「显示密码」都比以前大。

    「忘记密码」是隐藏入口：密码框为空时**在 10 秒内连点确定 6 次**才会出现，
    显示 10 秒后自动隐藏。
    """

    CLICK_LIMIT = 6
    CLICK_WINDOW = 10.0
    SHOW_SECONDS = 10

    def __init__(self, parent, title: str = "请输入密码", label: str = "密码：",
                 ok_text: str = "确定", confirm_label: str = "",
                 require_length: int = 0, extra_button: str = "",
                 note: str = "", show_forgot: bool = False, validator=None,
                 on_accept=None, on_forgot=None, on_extra=None,
                 secret: bool = True):
        height = 400 if confirm_label else 320
        super().__init__(parent, title, width=520, height=height)
        self._require_length = require_length
        self.secret = bool(secret)
        self._extra = extra_button
        self._validator = validator
        self._on_accept = on_accept
        self._on_forgot = on_forgot
        self._on_extra = on_extra
        self.forgot_visible = show_forgot
        self._empty_clicks = 0
        self._click_window_start = 0.0
        self._forgot_timer = None
        self._error_label = None
        tk.Label(self.body, text=label, bg=OLD_BG, anchor="w",
                 justify="left", font=(None, 12)).pack(fill="x")
        self.entry = tk.Entry(self.body, show="*" if self.secret else "",
                              relief="flat", bd=1, highlightthickness=1,
                              highlightbackground="#B4B4B4", highlightcolor=T.ACCENT,
                              bg="#FFFFFF", fg="#1B1B1B",
                              insertbackground="#1B1B1B", width=30,
                              font=(None, 12))
        self.entry.pack(fill="x", ipady=6, pady=(8, 0))
        self.confirm_entry = None
        if confirm_label:
            tk.Label(self.body, text=confirm_label, bg=OLD_BG,
                     anchor="w", font=(None, 12)).pack(fill="x", pady=(14, 0))
            self.confirm_entry = tk.Entry(
                self.body, show="*" if self.secret else "",
                relief="flat", bd=1, highlightthickness=1,
                highlightbackground="#B4B4B4", highlightcolor=T.ACCENT,
                bg="#FFFFFF", fg="#1B1B1B", insertbackground="#1B1B1B", width=30,
                font=(None, 12))
            self.confirm_entry.pack(fill="x", ipady=6, pady=(8, 0))
        self.show_var = tk.BooleanVar(value=False)
        self.show_check = None
        if self.secret:
            # 比正标题（12pt）小一点点
            self.show_check = W.FlatCheck(self.body, "显示密码", False,
                                          command=self._toggle_show,
                                          font=(None, 10), bg=OLD_BG, size=16)
            self.show_check.pack(anchor="w", pady=(12, 0))
        if note:
            tk.Label(self.body, text=note, bg=OLD_BG, fg="#6B6B6B",
                     anchor="w", justify="left",
                     font=(None, 10),
                     wraplength=self._width - 60).pack(fill="x", pady=(10, 0))
        row = tk.Frame(self, bg=OLD_BG)
        row.pack(fill="x", padx=22, pady=(0, 16))
        # 「忘记密码」默认不显示：空密码时 10 秒内连点确定 6 次才会出现 10 秒
        self.forgot_button = W.FlatButton(row, text="忘记密码", kind="default",
                                          command=self._forgot, font=(None, 10),
                                          padx=14, pady=6)
        if show_forgot:
            self.forgot_button.pack(side="left")
        W.FlatButton(row, text=ok_text, kind="default", command=self._ok,
                     font=(None, 10), padx=16, pady=6).pack(side="right")
        W.FlatButton(row, text="取消", kind="default", command=self._cancel,
                     font=(None, 10), padx=16, pady=6).pack(side="right",
                                                             padx=(0, 8))
        if self._extra:
            W.FlatButton(row, text=self._extra, kind="default",
                         command=self._do_extra, font=(None, 10),
                         padx=14, pady=6).pack(side="left", padx=(8, 0))
        self.after(80, self.entry.focus_set)

    # -- 隐藏的「忘记密码」入口 ------------------------------------------

    def _forgot(self) -> None:
        self.result = {"forgot": True}
        if self._on_forgot:
            try:
                self._on_forgot()
            except Exception:
                pass
        self._safe_destroy()

    def _safe_destroy(self) -> None:
        try:
            self.destroy()
        except Exception:
            pass

    def _bump_empty_click(self) -> bool:
        """空密码点确定：**10 秒内**连点 6 次才显示「忘记密码」按钮。"""
        if not self.secret:
            return False
        import time as _time

        now = _time.time()
        if now - self._click_window_start > self.CLICK_WINDOW:
            self._click_window_start = now
            self._empty_clicks = 0
        self._empty_clicks += 1
        if self._empty_clicks >= self.CLICK_LIMIT:
            self._empty_clicks = 0
            self._click_window_start = 0.0
            self._show_forgot_temporarily()
            return True
        return False

    def _show_forgot_temporarily(self) -> None:
        self.forgot_button.pack(side="left")
        if self._forgot_timer is not None:
            try:
                self.after_cancel(self._forgot_timer)
            except Exception:
                pass
        self._forgot_timer = self.after(self.SHOW_SECONDS * 1000, self._hide_forgot)

    def _hide_forgot(self) -> None:
        self._forgot_timer = None
        try:
            self.forgot_button.pack_forget()
        except Exception:
            pass

    def _toggle_show(self, *_args) -> None:
        if self.show_check is None:
            return
        char = "" if self.show_check.value else "*"
        self.entry.configure(show=char)
        if self.confirm_entry is not None:
            self.confirm_entry.configure(show=char)

    def _do_extra(self) -> None:
        self.result = {"extra": True, "value": self.entry.get()}
        if self._on_extra:
            try:
                self._on_extra(self.result["value"])
            except Exception:
                pass
        self._safe_destroy()

    def _ok(self) -> None:
        value = self.entry.get()
        if not value:
            self._bump_empty_click()
            return
        if self._require_length and len(value) < self._require_length:
            message(self, "提示", "至少需要 %d 位。" % self._require_length,
                    ("确定",), kind="warn")
            return
        if self.confirm_entry is not None and value != self.confirm_entry.get():
            message(self, "提示", "两次输入不一致。", ("确定",), kind="warn")
            return
        if self._validator is not None:
            error = ""
            try:
                error = self._validator(value) or ""
            except Exception as exc:
                error = str(exc)
            if error:
                self._show_error(error)
                return
        self.result = {"extra": False, "value": value}
        if self._on_accept:
            try:
                self._on_accept(value)
            except Exception:
                pass
        self._safe_destroy()

    def _show_error(self, text: str) -> None:
        """在输入框下面显示错误提示（不动输入框内容，方便直接改）。"""
        if getattr(self, "_error_label", None) is None:
            self._error_label = tk.Label(self.body, text="", bg=OLD_BG,
                                         fg="#B00020", anchor="w", justify="left",
                                         font=(None, 10),
                                         wraplength=self._width - 60)
            self._error_label.pack(fill="x", pady=(12, 0))
        self._error_label.configure(text=text)
        try:
            self.entry.focus_set()
        except Exception:
            pass


def ask_password_old(parent, title: str = "请输入密码", label: str = "密码：",
                     ok_text: str = "确定", confirm_label: str = "",
                     require_length: int = 0, extra_button: str = "",
                     note: str = "", show_forgot: bool = False, validator=None,
                     on_accept=None, on_forgot=None, on_extra=None):
    """老式密码输入框。

    返回：普通字符串；``{"extra": True, ...}`` 表示点了额外按钮；
    ``{"forgot": True}`` 表示（隐藏入口）点了「忘记密码」；取消返回 None。
    """
    dialog = PasswordDialog(parent, title, label, ok_text, confirm_label,
                            require_length, extra_button, note, show_forgot,
                            validator, on_accept, on_forgot, on_extra)
    dialog.reveal(parent)
    if dialog.result is None:
        return None
    if dialog.result.get("forgot"):
        return {"forgot": True}
    if dialog.result.get("extra"):
        return {"extra": True, "value": dialog.result.get("value", "")}
    return dialog.result.get("value", "")


def ask_text_old(parent, title: str, label: str, value: str = "",
                 ok_text: str = "确定", width: int = 300):
    """单行文本输入（恢复码等）：和密码弹窗同一套**现代扁平**样式。

    ``width`` 参数保留是为了兼容旧调用（现在宽度由弹窗统一控制）。
    """
    dialog = PasswordDialog(parent, title, label, ok_text, secret=False)
    if value:
        dialog.entry.insert(0, value)
    dialog._ok_handler = dialog._ok
    dialog.reveal(parent)
    if dialog.result is None:
        return None
    return dialog.result.get("value", "")
