"""开发工具：启动真实应用跑一段时间的运行检查（托盘、后台监控、日志）。

用法：python tools\\dev\\runtime_check.py [秒数]
"""

from __future__ import annotations

import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("SV_DATA_DIR", "SecureVaultData-runtime")

from securevault.ui.app import SecureVaultApp  # noqa: E402
from securevault.core.model import Mode  # noqa: E402


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    app = SecureVaultApp(dev=True, review=True)
    app.root.update()
    print("窗口尺寸：%dx%d（缩放 %.2f）"
          % (app.root.winfo_width(), app.root.winfo_height(), app.scale))
    time.sleep(0.5)
    app.root.update()
    print("托盘图标可用：%s" % (app.tray.available if app.tray else False))
    print("生效模式：%s" % Mode.label(app.engine.effective_mode))
    before = app.store.count_records()
    print("启动时已有记录：%d" % before)
    end = time.time() + seconds
    while time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.05)
    after = app.store.count_records()
    print("运行 %.0f 秒后记录数：%d（新增 %d）" % (seconds, after, after - before))
    state = app.engine.state()
    print("最近事件：%s" % (state["last_event"] or "-"))
    print("状态栏：%s" % app.engine.status_text())
    log_path = app.logger.today_file
    print("今日日志：%s" % log_path)
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.read().strip().splitlines()[-6:]
        for line in tail:
            print("   " + line)
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
