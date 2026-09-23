"""关于页：作者与项目地址、当前版本、检查更新与下载覆盖。"""

from __future__ import annotations

import os
import shutil
import sys
import tkinter as tk

from ... import APP_AUTHOR, APP_VERSION, PROJECT_URL
from ...system import update
from .. import dialogs as D
from .. import theme as T
from .. import widgets as W
from .base import Page


def plain_notes(text: str) -> str:
    """把发布说明里的 Markdown 装饰去掉，直接当纯文本看。"""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped and set(stripped) <= set("-|: "):      # 表格分隔行
            lines.append("")
            continue
        while line.lstrip().startswith("#"):
            line = line.lstrip()[1:]
        line = line.replace("**", "").replace("`", "")
        if stripped.startswith("- ") or stripped.startswith("* "):
            line = "· " + line.strip()[2:]
        lines.append(line.rstrip())
    result = "\n".join(lines).strip()
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result


class AboutPage(Page):
    title = "关于"

    def build(self) -> None:
        area = self.scroll_area()
        body = area.body
        self._info = None
        self._busy = False
        self._result_visible = False
        self._action_visible = False

        self._build_about_card(body)
        self._build_update_card(body)

    # -- 关于 ------------------------------------------------------------

    def _build_about_card(self, body) -> None:
        card = W.Card(body, "关于 SecureVault", self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.PAD), 0))
        self.info_rows = {}
        for key, label in (("name", "软件名称"), ("author", "作者"),
                           ("version", "当前版本"), ("home", "项目主页")):
            row, holder = W.labeled_row(card.body, label, self.fonts.get("base"),
                                        bg=T.CARD, label_width=9)
            row.pack(fill="x", pady=T.px(3))
            value = tk.Label(holder, text="-", bg=T.CARD, fg=T.TEXT,
                             font=self.fonts.get("base"), anchor="w",
                             justify="left", wraplength=T.px(560))
            value.pack(fill="x")
            self.info_rows[key] = value
        # 项目主页做成可点的链接
        link = self.info_rows["home"]
        link.configure(fg=T.ACCENT, cursor="hand2")
        link.bind("<Button-1>", lambda _e: self.ctx.app.open_project_page())
        link.bind("<Enter>", lambda _e: link.configure(fg=T.ACCENT_HOVER))
        link.bind("<Leave>", lambda _e: link.configure(fg=T.ACCENT))

        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x", pady=(T.px(10), 0))
        W.FlatButton(row, text="打开项目主页", kind="primary",
                     command=self.ctx.app.open_project_page,
                     font=self.fonts.get("base")).pack(side="left")
        W.FlatButton(row, text="复制项目地址", kind="default",
                     command=self._copy_home, font=self.fonts.get("base")
                     ).pack(side="left", padx=(T.px(8), 0))

    def _copy_home(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(PROJECT_URL)
        except Exception as exc:
            self.ctx.notify("复制失败：%s" % exc, "error")
            return
        self.ctx.notify("已复制项目地址：%s" % PROJECT_URL, "success")

    # -- 检查更新 --------------------------------------------------------

    def _build_update_card(self, body) -> None:
        card = W.Card(body, "检查更新", self.fonts)
        card.pack(fill="x", padx=T.px(T.PAD), pady=(T.px(T.GAP), T.px(T.PAD)))

        # 第一行：线路 + 检查更新按钮；结果区查完才展开
        row = tk.Frame(card.body, bg=T.CARD)
        row.pack(fill="x")
        tk.Label(row, text="线路", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base")).pack(side="left")
        self.source = W.FlatSelect(row, update.source_options(),
                                   update.DEFAULT_SOURCE, font=self.fonts.get("base"))
        self.source.pack(side="left", padx=(T.px(8), T.px(10)))
        self.check_button = W.FlatButton(row, text="检查更新", kind="primary",
                                         command=self.check_now,
                                         font=self.fonts.get("base"),
                                         padx=22, pady=8)
        self.check_button.pack(side="left")

        # ---- 结果区（检查完成后才 pack 出来）----------------------------
        self.result_box = tk.Frame(card.body, bg=T.CARD)
        tk.Frame(self.result_box, bg=T.BORDER, height=1).pack(fill="x",
                                                             pady=(T.px(12), 0))
        result = tk.Frame(self.result_box, bg=T.CARD)
        result.pack(fill="x", pady=(T.px(10), 0))
        tk.Label(result, text="最新版本", bg=T.CARD, fg=T.TEXT_DIM,
                 font=self.fonts.get("base"), width=9, anchor="w").pack(side="left")
        self.latest_label = tk.Label(result, text="", bg=T.CARD, fg=T.TEXT,
                                     font=self.fonts.get("base"), anchor="w",
                                     justify="left")
        self.latest_label.pack(side="left", fill="x", expand=True)
        self.detail_label = tk.Label(self.result_box, text="", bg=T.CARD,
                                     fg=T.TEXT_DIM,
                                     font=self.fonts.get("small"), anchor="w",
                                     justify="left", wraplength=T.px(760))
        self.detail_label.pack(fill="x", pady=(T.px(6), 0), padx=(T.px(0), 0))

        self.notes_title = tk.Label(self.result_box, text="更新内容", bg=T.CARD,
                                    fg=T.TEXT_DIM, font=self.fonts.get("base"),
                                    anchor="w")
        self.notes_title.pack(fill="x", pady=(T.px(12), 0))
        self.notes = W.FlatText(self.result_box, width=70, height=10,
                                font=self.fonts.get("base"), wrap="word",
                                readonly=True)
        self.notes.pack(fill="x", pady=(T.px(4), 0))

        # 下载单独一行：线路在上面选，这里只放按钮与进度
        self.action_row = tk.Frame(self.result_box, bg=T.CARD)
        self.download_button = W.FlatButton(self.action_row, text="下载并覆盖",
                                            kind="danger",
                                            command=self.download_and_apply,
                                            font=self.fonts.get("base"))
        self.download_button.pack(side="left")
        self.download_button.set_enabled(False)
        self.progress = W.ProgressBar(self.action_row, width=200, height=8,
                                      bg=T.CARD)
        self.progress.pack(side="left", padx=(T.px(12), T.px(8)), pady=T.px(6))
        self.progress_label = tk.Label(self.action_row, text="", bg=T.CARD,
                                       fg=T.TEXT_DIM,
                                       font=self.fonts.get("small"), anchor="w")
        self.progress_label.pack(side="left")

        W.Hint(
            self.result_box,
            "覆盖更新只会替换程序目录里的程序文件；SecureVaultData（记录、设置、"
            "密钥库、日志）不会被改动。",
            font=self.fonts.get("small"), color=T.TEXT_DIM, bg=T.CARD,
        ).pack(fill="x", pady=(T.px(8), 0))

    # -- 刷新 ------------------------------------------------------------

    def refresh(self) -> None:
        self.info_rows["name"].configure(text="SecureVault")
        self.info_rows["author"].configure(text=APP_AUTHOR)
        self.info_rows["version"].configure(text="v%s" % APP_VERSION)
        self.info_rows["home"].configure(text=PROJECT_URL)
        self._render_result()

    def on_show(self) -> None:
        self.refresh()

    def _asset_name(self) -> str:
        version = self._info["latest"] if self._info else APP_VERSION
        return "SecureVault-%s-win64-portable.zip" % version

    def _render_result(self) -> None:
        info = self._info
        if info is None:
            self.download_button.set_enabled(False)
            self._set_progress(0.0, "")
            return
        latest = info["latest"]
        older = update.version_tuple(latest) < update.version_tuple(APP_VERSION)
        if info["newer"]:
            text = "v%s　有新版本（当前 v%s）" % (latest, APP_VERSION)
            color = T.SUCCESS
        elif older:
            text = "v%s　比当前版本旧（当前 v%s），可以回退" % (latest, APP_VERSION)
            color = T.WARN
        else:
            text = "v%s　已是最新版本" % latest
            color = T.TEXT
        self.latest_label.configure(text=text, fg=color)
        details = []
        if info["published"]:
            details.append("发布时间 %s" % info["published"])
        details.append("包大小 %s" % update.describe_size(info["size"]))
        details.append(info["asset"])
        self.detail_label.configure(text="　｜　".join(details))
        changes = update.changes_only(info["notes"]) or plain_notes(info["notes"])
        self.notes.set_text(plain_notes(changes)
                            or "（这个版本没有填写更新说明）")
        # 有新版本可以升级；比线上版本新时也允许回退（下载前会再提示一次）
        self.download_button.set_text("回退到这个版本" if older else "下载并覆盖")
        self.download_button.set_enabled((bool(info["newer"]) or older)
                                         and not self._busy)
        self._render_action()

    def _render_action(self) -> None:
        """只有「有新版本」或「可以回退」时才显示下载按钮。"""
        info = self._info
        want = bool(info is not None and (info["newer"]
                                          or update.version_tuple(info["latest"])
                                          < update.version_tuple(APP_VERSION)))
        if want == self._action_visible:
            return
        self._action_visible = want
        if want:
            # 插在「更新内容」前面：一屏就能看到下载按钮
            self.action_row.pack(fill="x", pady=(T.px(10), 0),
                                 before=self.notes_title)
        else:
            self.action_row.pack_forget()

    def _set_progress(self, value: float, text: str) -> None:
        self.progress.set(value)
        self.progress_label.configure(text=text)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.check_button.set_enabled(not busy)
        self.source.set_enabled(not busy)
        if busy:
            self.download_button.set_enabled(False)
        else:
            self._render_result()

    def _show_result(self, show: bool) -> None:
        """结果区默认不显示，检查完成后才展开。"""
        if show == self._result_visible:
            return
        self._result_visible = show
        if show:
            self.result_box.pack(fill="x")
        else:
            self.result_box.pack_forget()

    # -- 检查 / 下载 ------------------------------------------------------

    def check_now(self, insecure: bool = False) -> None:
        if self._busy:
            return
        source = self.source.value
        self._set_busy(True)
        self.check_button.set_text("正在检查更新…")
        self.ctx.notify("正在检查更新…（线路：%s）" % update.source_label(source))
        self.ctx.log("info", "检查更新开始（线路：%s%s）"
                     % (update.source_label(source),
                        "，已忽略证书校验" if insecure else ""))

        def work() -> None:
            try:
                info = update.check_for_update(source, insecure=insecure)
            except Exception as exc:
                self.ctx.ui(self._on_checked, None, str(exc),
                            update.is_ssl_error(exc))
                return
            self.ctx.ui(self._on_checked, info, "", False)

        self.ctx.run_bg(work)

    def _on_checked(self, info, error: str, ssl_problem: bool = False) -> None:
        self.check_button.set_text("检查更新")
        self._set_busy(False)
        if info is None:
            self.ctx.log("warn", "检查更新失败：%s" % error)
            if self._info is None:      # 之前查过就保留上次结果，别闪没
                self._show_result(False)
            self.ctx.notify("检查更新失败：%s" % error.splitlines()[0], "error")
            if ssl_problem:
                # 证书问题的应急出口：只用于查询版本号，下载仍然校验证书
                answer = D.message(
                    self, "HTTPS 证书校验失败",
                    "%s\n\n程序自带了一份 CA 证书包，正常情况下不该出现这个问题。\n"
                    "要「忽略证书校验」重查一次吗？（只对这次查询有效，"
                    "下载更新包时仍然会校验证书。）" % error,
                    ("忽略证书校验重查", "取消"), kind="warn")
                if answer == 0:
                    self.check_now(insecure=True)
                return
            D.message(self, "检查更新失败",
                      "没能取到最新版本信息：\n%s\n\n"
                      "请检查网络后重试；如果一直失败，可以手动到项目主页的 "
                      "Releases 页面下载压缩包。" % error, ("确定",), kind="error")
            return
        self._info = info
        self._render_result()
        self._show_result(True)
        self.ctx.log("info", "检查更新完成：最新 v%s（当前 v%s）"
                     % (info["latest"], APP_VERSION))
        if info["newer"]:
            self.ctx.notify("发现新版本 v%s（当前 v%s），可以点「下载并覆盖」。"
                            % (info["latest"], APP_VERSION), "success")
        elif update.version_tuple(info["latest"]) < update.version_tuple(APP_VERSION):
            self.ctx.notify("线上最新发布是 v%s，比当前 v%s 旧（有需要可以回退）。"
                            % (info["latest"], APP_VERSION), "success")
        else:
            self.ctx.notify("已经是最新版本（v%s）。" % APP_VERSION, "success")

    def download_and_apply(self) -> None:
        info = self._info
        if self._busy:
            return
        if not getattr(sys, "frozen", False):
            self.ctx.notify("源码运行时不支持覆盖更新。", "warn")
            D.message(self, "这个功能在打包版里用",
                      "「下载并覆盖」会把压缩包里的程序文件写到程序目录；"
                      "当前是源码运行，程序目录里是源码。\n\n"
                      "请到打包版（含 SecureVault.exe 的文件夹）里使用。",
                      ("确定",), kind="warn")
            return
        if not info:
            self.ctx.notify("请先点「检查更新」。", "warn")
            return
        older = update.version_tuple(info["latest"]) < update.version_tuple(APP_VERSION)
        if not info["newer"] and not older:
            self.ctx.notify("当前已经是最新版本，不需要更新。")
            return
        if older:
            # 当前版本比线上新：允许回退，但要说清楚代价
            answer = D.message(
                self, "回退到旧版本",
                "线上最新发布是 v%s，你现在用的 v%s 更新。\n\n"
                "继续会用 v%s 覆盖当前程序（SecureVaultData 里的记录、设置、"
                "密钥库、日志不会被动），"
                "回退可能会丢掉新版本才有的功能与修复。\n\n确定要回退吗？"
                % (info["latest"], APP_VERSION, info["latest"]),
                ("回退到 v%s" % info["latest"], "取消"), kind="warn")
            if answer != 0:
                return
            self._start_download(info, update.download_url(
                info["latest"], info["asset"], self.source.value))
            return
        # 下载走"线路"下拉里当前选中的那条（检查之后改过线路也按最新的来）
        url = update.download_url(info["latest"], info["asset"], self.source.value)
        answer = D.message(
            self, "下载并覆盖",
            "将从下面的地址下载 v%s：\n%s\n\n下载完成后会把程序文件覆盖到：\n%s"
            % (info["latest"], url, self.ctx.dirs.exe_dir),
            ("下载并覆盖", "取消"), kind="question")
        if answer != 0:
            return
        self._start_download(info, url)

    def _start_download(self, info, url: str = "") -> None:
        work_dir = os.path.join(self.ctx.dirs.root, "update")
        zip_path = os.path.join(work_dir, info["asset"])
        url = url or info["url"]
        self._set_busy(True)
        self.ctx.notify("开始下载 v%s（%s）…" % (info["latest"],
                                              update.describe_size(info["size"])))
        self.ctx.log("info", "开始下载更新包：%s" % url)

        def report(done: int, total: int) -> None:
            self.ctx.ui(self._on_progress, done, total)

        def work() -> None:
            try:
                update.download(url, zip_path, progress=report)
                self.ctx.ui(self._on_progress_text, "正在解包…")
                staging, _count = update.stage(zip_path, work_dir)
                self.ctx.ui(self._on_progress_text, "正在覆盖程序文件…")
                result = update.apply_staging(staging, self.ctx.dirs.exe_dir)
            except Exception as exc:
                self.ctx.ui(self._on_applied, None, str(exc))
                return
            self.ctx.ui(self._on_applied, result, "")

        self.ctx.run_bg(work)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._set_progress(done / float(total),
                               "已下载 %s / %s" % (update.describe_size(done),
                                                   update.describe_size(total)))
        else:
            self._set_progress(0.0, "已下载 %s" % update.describe_size(done))

    def _on_progress_text(self, text: str) -> None:
        """下载 / 解包阶段的进度文字：显示在下载按钮旁边。"""
        self.progress_label.configure(text=text)

    def _on_applied(self, result, error: str) -> None:
        self._set_busy(False)
        if result is None:
            self.ctx.log("error", "更新失败：%s" % error)
            self.ctx.notify("更新失败：%s" % error, "error")
            D.message(self, "更新失败",
                      "下载或覆盖没有完成：\n%s\n\n原程序仍然可用，"
                      "可以换一个下载源重试。" % error, ("确定",), kind="error")
            return
        failed = result.get("failed") or []
        text = "共 %d 个文件：覆盖 %d 个，新增 %d 个，失败 %d 个。" % (
            result.get("total", 0), result.get("replaced", 0),
            result.get("added", 0), len(failed))
        self._set_progress(1.0, text)
        self.ctx.log("info", "更新包已覆盖：%s" % text)
        self._cleanup_download()
        if failed:
            detail = "\n".join("%s：%s" % (name, message) for name, message in failed[:5])
            self.ctx.notify("更新完成，但有 %d 个文件没能替换。" % len(failed), "warn")
            D.message(self, "更新完成（有警告）",
                      "%s\n\n下面这些文件被占用，没有替换成功：\n%s\n\n"
                      "程序仍然可用；可以关掉程序后再手动解压一次压缩包覆盖。"
                      % (text, detail), ("确定",), kind="warn")
            return
        self.ctx.notify("更新完成：%s" % text, "success")
        if D.message(self, "更新完成",
                     "%s\n\n现在重启程序，换成新版本吗？" % text,
                     ("立即重启", "稍后"), kind="question") == 0:
            self.ctx.app.restart_now()

    def _cleanup_download(self) -> None:
        """更新成功后清掉下载与解包留下的临时文件（省空间）。"""
        work_dir = os.path.join(self.ctx.dirs.root, "update")
        try:
            if os.path.isdir(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
