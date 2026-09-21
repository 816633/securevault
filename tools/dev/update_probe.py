"""开发工具：离线验证更新包的解包 / 覆盖流程（用已有 zip 当样本，不联网）。

用法：

    python tools\\dev\\update_probe.py <更新包.zip>
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from securevault.system import update  # noqa: E402


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    zip_path = os.path.abspath(argv[0])
    work = tempfile.mkdtemp(prefix="sv-update-probe-")
    target = os.path.join(work, "target")
    os.makedirs(target)
    with open(os.path.join(target, "SecureVault.exe"), "w", encoding="utf-8") as fh:
        fh.write("old")
    with open(os.path.join(target, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("keep")
    try:
        staging, count = update.stage(zip_path, work)
        print("解包文件数      =", count)
        print("有 SecureVault.exe =", os.path.isfile(os.path.join(staging, "SecureVault.exe")))
        print("有 tcl 目录      =", os.path.isdir(os.path.join(staging, "tcl")))
        print("有请先读我.txt   =", os.path.exists(os.path.join(staging, "请先读我.txt")))
        result = update.apply_staging(staging, target)
        print("覆盖结果        = 覆盖 %d，新增 %d，失败 %d，合计 %d"
              % (result["replaced"], result["added"], len(result["failed"]),
                 result["total"]))
        print("用户文件还在    =", os.path.isfile(os.path.join(target, "keep.txt")))
        print("exe 已被替换    =", os.path.getsize(os.path.join(target, "SecureVault.exe")) > 100000)
        print("目标目录条目数  =", len(os.listdir(target)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
