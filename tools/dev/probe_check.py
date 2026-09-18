"""开发工具：打印本机所有磁盘的探测结果（验证 device 探测层）。"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from securevault.system import devices  # noqa: E402


def main() -> int:
    start = time.time()
    snap = devices.snapshot()
    print("snapshot elapsed: %.2fs, drives: %d" % (time.time() - start, len(snap)))
    for letter in sorted(snap):
        info = snap[letter]
        print("-" * 70)
        print("盘符        :", letter)
        print("卷标        :", info.name)
        print("文件系统    :", info.fs)
        print("总线类型    :", info.bus_type)
        print("型号        :", info.model)
        print("厂商        :", info.vendor)
        print("VID:PID     :", info.vid_pid)
        print("USB 序列号  :", info.usb_serial)
        print("磁盘序列号  :", info.disk_serial)
        print("容量/剩余   :", info.capacity, "/", info.free)
        print("物理磁盘号  :", info.physical_drive)
        print("设备实例 ID :", info.device_instance)
        print("卷 GUID     :", info.volume_guid)
        print("设备路径    :", info.device_path)
        print("可移动      :", info.is_removable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
