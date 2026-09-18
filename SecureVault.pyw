"""双击运行入口（.pyw 不显示控制台窗口）。"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
