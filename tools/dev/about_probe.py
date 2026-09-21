"""开发工具：检查「关于」页结果区的显隐与排布（不点界面，直接断言控件状态）。"""

from __future__ import annotations

import os
import shutil
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("SV_DATA_DIR", "SecureVaultData-probe")

from securevault.core import paths  # noqa: E402
from securevault.system import update as update_mod  # noqa: E402
from securevault.ui import app as app_module  # noqa: E402
from securevault.ui import dialogs as D  # noqa: E402


def pump(app, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.02)


def drain(app) -> None:
    while True:
        try:
            callback, args = app.ui_queue.get_nowait()
        except Exception:
            return
        callback(*args)


def main() -> int:
    dirs = paths.resolve(dev=True)
    if os.path.basename(dirs.root).startswith("SecureVaultData-probe"):
        shutil.rmtree(dirs.root, ignore_errors=True)
    dirs.ensure()
    D.NON_BLOCKING = True
    D.show_recovery = lambda parent, code: True
    D.message = lambda *a, **k: 0
    app_module.D.show_recovery = D.show_recovery
    app_module.D.message = D.message

    app = app_module.SecureVaultApp(dev=True)
    app.complete_setup("probe-pass-2026")
    pump(app, 0.5)
    index = app_module.PAGE_TITLES.index("关于")
    app.tabbar.select(index)
    pump(app, 0.4)
    page = app._pages[index]

    print("初始：结果区=%s 动作行=%s 按钮=%s"
          % (page.result_box.winfo_ismapped(), page.action_row.winfo_ismapped(),
             page.download_button.winfo_ismapped()))

    asset = "SecureVault-9.9.9-win64-portable.zip"
    info = {
        "source": "github", "source_label": update_mod.source_label("github"),
        "latest": "9.9.9", "tag": "v9.9.9", "name": "9.9.9",
        "notes": "## 本版主要改动\n\n- 测试改动",
        "published": "2026-09-20", "asset": asset, "size": 1024, "newer": True,
        "url": update_mod.download_url("9.9.9", asset),
        "official_url": update_mod.release_url("9.9.9", asset),
        "page": update_mod.RELEASES_PAGE,
    }
    original = update_mod.check_for_update
    update_mod.check_for_update = lambda source, timeout=30: info
    try:
        page.check_now()
        pump(app, 0.4)
        drain(app)
        pump(app, 0.4)
    finally:
        update_mod.check_for_update = original
    print("检查后：结果区=%s 动作行=%s 按钮=%s"
          % (page.result_box.winfo_ismapped(), page.action_row.winfo_ismapped(),
             page.download_button.winfo_ismapped()))
    print("动作行 pack =", page.action_row.pack_info() if page.action_row.winfo_manager() else "未 pack")
    print("按钮 pack  =", page.download_button.pack_info() if page.download_button.winfo_manager() else "未 pack")
    print("结果区子控件顺序 =", [child.__class__.__name__ for child in page.result_box.pack_slaves()])
    page.update_idletasks()
    pump(app, 0.3)
    print("再刷新后：动作行=%s 按钮=%s viewable=%s"
          % (page.action_row.winfo_ismapped(), page.download_button.winfo_ismapped(),
             page.action_row.winfo_viewable()))
    print("动作行尺寸 req=%sx%s 实际=%sx%s manager=%s"
          % (page.action_row.winfo_reqwidth(), page.action_row.winfo_reqheight(),
             page.action_row.winfo_width(), page.action_row.winfo_height(),
             page.action_row.winfo_manager()))
    print("动作行子控件 =", [(child.__class__.__name__, child.winfo_ismapped(),
                              child.winfo_manager())
                             for child in page.action_row.winfo_children()])
    print("结果区 manager=%s" % page.result_box.winfo_manager())
    print("结果区尺寸 =", page.result_box.winfo_width(), page.result_box.winfo_height())
    print("结果区 req =", page.result_box.winfo_reqwidth(), page.result_box.winfo_reqheight())
    area = page.winfo_children()[0]
    print("滚动区 body: 实际高=%s 需求高=%s 项高=%s 画布高=%s"
          % (area.body.winfo_height(), area.body.winfo_reqheight(),
             getattr(area, "_item_height", -1), area.canvas.winfo_height()))
    card2 = page.result_box.master.master   # card.body -> card.inner -> card
    print("卡片: 实际高=%s 需求高=%s" % (card2.winfo_height(), card2.winfo_reqheight()))
    print("更新内容框: 实际高=%s 需求高=%s mapped=%s"
          % (page.notes.winfo_height(), page.notes.winfo_reqheight(),
             page.notes.winfo_ismapped()))
    print("最新版本 =", page.latest_label.cget("text"))
    print("更新内容 =", repr(page.notes.get()[:40]))
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
