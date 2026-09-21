"""开发工具：不点界面直接跑一遍「首次设置 → 打开面板」并打印真实异常。"""

from __future__ import annotations

import os
import shutil
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("SV_DATA_DIR", "SecureVaultData-probe")

from securevault.core import paths  # noqa: E402
from securevault.ui import app as app_module  # noqa: E402
from securevault.ui import dialogs as D  # noqa: E402


def main() -> int:
    dirs = paths.resolve(dev=True)
    if os.path.basename(dirs.root).startswith("SecureVaultData-probe"):
        shutil.rmtree(dirs.root, ignore_errors=True)
    dirs.ensure()
    D.NON_BLOCKING = True
    D.show_recovery = lambda parent, code: True

    app = app_module.SecureVaultApp(dev=True)
    print("视图窗口 =", app._setup_window is not None)
    app.complete_setup("probe-pass-2026")
    print("服务已启动 =", app._service_started)
    print("面板已建立 =", app._page_holder is not None)
    print("页面数量   =", len(app._pages), app_module.PAGE_TITLES)
    if app._pages:
        for index, page in enumerate(app._pages):
            try:
                app.tabbar.select(index)
                app.root.update()
                page.refresh()
            except Exception:
                print("页面 %d 刷新失败：" % index)
                traceback.print_exc()
        about = app._pages[-1]
        print("关于页作者 =", about.info_rows["author"].cget("text"))
        print("关于页版本 =", about.info_rows["version"].cget("text"))
        print("关于页主页 =", about.info_rows["home"].cget("text"))
        print("下载源选项 =", [text for _key, text in about.source._choices])
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
