"""开发小工具：检查主页「应用模式」按钮上是否还有多余的符号。"""

from __future__ import annotations

import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main() -> int:
    path = os.path.join(BASE_DIR, "securevault", "ui", "pages", "status.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    for match in re.finditer(r'text="([^"]*应用模式[^"]*)"', source):
        print("按钮文字 =", repr(match.group(1)))
    print("文件里含 ✔ :", "\u2714" in source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
