"""开发工具：检查触摸设备探测与屏幕键盘唤起（触屏电脑上排查用）。

用法：python tools\\dev\\touch_probe.py [--launch]

不带参数时只报告状态；带 ``--launch`` 会真的试一次唤起屏幕键盘。
"""

from __future__ import annotations

import ctypes
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from securevault.system import touch  # noqa: E402


def main(argv) -> int:
    user32 = ctypes.windll.user32
    print("SV_TOUCH 环境变量 =", os.environ.get("SV_TOUCH", "（没设置）"))
    print("SM_MAXIMUMTOUCHES  =", user32.GetSystemMetrics(touch.SM_MAXIMUMTOUCHES))
    print("SM_DIGITIZER       =", user32.GetSystemMetrics(touch.SM_DIGITIZER))
    print("识别为触摸设备     =", touch.touch_available())
    print("开关（设置项）     =", touch.enabled())
    print("可用的键盘程序     =", touch.keyboard_paths())
    print("ITipInvocation     =", touch.CLSID_TIP_INVOCATION)
    if "--launch" in argv:
        print("COM 方式唤起       =", touch.toggle_touch_keyboard())
        print("整体唤起结果       =", touch.show_keyboard(force=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
