"""生成应用图标 assets\\securevault.ico（老式 Windows 3.1/95 风格的 3D 保险箱）。

特点：
  * 用硬边缘（不做抗锯齿）画出来，所以是「老软件」的像素观感；
  * 每个尺寸单独绘制（16/20/24/32/40/48/64/96/128/256），高 DPI 下不会糊；
  * 自己拼 ICO 容器（32 位色 DIB），兼容 Windows 与 PyInstaller。

用法：

    python tools\\genicon.py              # 生成 assets\\securevault.ico
    python tools\\genicon.py --preview    # 同时打印 16/32 像素的字符预览
"""

from __future__ import annotations

import io
import os
import struct
import sys

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(BASE_DIR, "assets", "securevault.ico")

SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
         (64, 64), (96, 96), (128, 128), (256, 256)]

# 经典 Windows 3.1/95 的 16 色调色板（越少越"老"）
BLACK = (0, 0, 0, 255)
OUTLINE = (0, 0, 0, 255)
FACE = (192, 192, 192, 255)          # 经典按钮灰
FACE_HL = (255, 255, 255, 255)       # 高光
FACE_SH = (128, 128, 128, 255)       # 阴影
FACE_DEEP = (64, 64, 64, 255)        # 深阴影
GOLD = (255, 216, 0, 255)
GOLD_DARK = (128, 96, 0, 255)
SILVER = (224, 224, 224, 255)
SCREEN = (0, 128, 128, 255)          # 老式青色
NAVY = (0, 0, 128, 255)

#: 大尺寸直接从 32×32 放大，保持方块像素感
NEAREST_BASE = 32


def _r(value: float) -> int:
    return int(round(value))


def draw_icon(size: int) -> Image.Image:
    """画出老式 3.5 寸软盘 + 小锁的图标（不做抗锯齿，保持像素感）。"""
    if size >= NEAREST_BASE:
        base = _draw_floppy(NEAREST_BASE)
        if size == NEAREST_BASE:
            return base
        return base.resize((size, size), Image.NEAREST)
    return _draw_floppy(size)


def _draw_floppy(size: int) -> Image.Image:
    """在给定尺寸上画软盘图标（32×32 为基准网格，整数坐标、硬边缘）。"""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    k = size / 32.0

    def box(x0, y0, x1, y1, fill=None, outline=None):
        draw.rectangle([_r(x0 * k), _r(y0 * k), _r(x1 * k) - 1, _r(y1 * k) - 1],
                       fill=fill, outline=outline)

    # 1) 盘体：右上角切角（3.5 寸软盘的经典形状）
    draw.polygon([_r(3 * k), _r(2 * k), _r(24 * k), _r(2 * k),
                  _r(29 * k), _r(7 * k), _r(29 * k), _r(30 * k),
                  _r(3 * k), _r(30 * k)], fill=FACE, outline=OUTLINE)
    # 2) 老式 3D 边：左上白高光、右下深阴影
    box(4, 3, 24, 4, fill=FACE_HL)
    box(4, 3, 5, 30, fill=FACE_HL)
    box(4, 29, 29, 30, fill=FACE_DEEP)
    box(28, 8, 29, 30, fill=FACE_DEEP)
    box(5, 28, 28, 29, fill=FACE_SH)
    box(27, 8, 28, 29, fill=FACE_SH)

    # 3) 金属滑片（带一个凹口）
    box(11, 3, 21, 13, fill=SILVER, outline=OUTLINE)
    box(13, 4, 16, 10, fill=FACE_SH, outline=OUTLINE)
    box(12, 4, 20, 5, fill=FACE_HL)

    # 4) 标签纸 + 两条蓝线
    box(6, 17, 26, 28, fill=FACE_HL, outline=OUTLINE)
    box(8, 20, 24, 21, fill=NAVY)
    box(8, 23, 20, 24, fill=NAVY)

    # 5) 金色小锁（挂在标签上）
    box(19, 11, 21, 17, fill=GOLD, outline=OUTLINE)     # 锁梁左侧
    box(25, 11, 27, 17, fill=GOLD, outline=OUTLINE)     # 锁梁右侧
    box(19, 11, 27, 13, fill=GOLD, outline=OUTLINE)     # 锁梁顶部
    box(18, 16, 28, 28, fill=GOLD, outline=OUTLINE)     # 锁体
    box(18, 16, 28, 17, fill=GOLD_DARK)
    box(18, 27, 28, 28, fill=GOLD_DARK)
    box(22, 19, 24, 25, fill=BLACK)                     # 钥匙孔
    box(22, 19, 24, 21, fill=BLACK)
    return image


def _legacy_tick(draw, cx, cy, r0, r1, angle_deg, k, color) -> None:
    import math

    angle = math.radians(angle_deg)
    draw.line([_r((cx + math.cos(angle) * r0) * k),
               _r((cy + math.sin(angle) * r0) * k),
               _r((cx + math.cos(angle) * r1) * k),
               _r((cy + math.sin(angle) * r1) * k)], fill=color, width=1)


def _circle(draw, image, cx, cy, radius, k, fill, outline) -> None:
    left = _r((cx - radius) * k)
    top = _r((cy - radius) * k)
    right = _r((cx + radius) * k) - 1
    bottom = _r((cy + radius) * k) - 1
    if right <= left:
        right = left + 1
    if bottom <= top:
        bottom = top + 1
    draw.ellipse([left, top, right, bottom], fill=fill, outline=outline)


def _tick(draw, cx, cy, r0, r1, angle_deg, k, color) -> None:
    import math

    angle = math.radians(angle_deg)
    x0 = cx + math.cos(angle) * r0
    y0 = cy + math.sin(angle) * r0
    x1 = cx + math.cos(angle) * r1
    y1 = cy + math.sin(angle) * r1
    draw.line([_r(x0 * k), _r(y0 * k), _r(x1 * k), _r(y1 * k)], fill=color, width=1)


# ---------------------------------------------------------------------------
# ICO 容器（32 位色 DIB，逐尺寸写入，避免缩放糊掉）
# ---------------------------------------------------------------------------

def build_ico(images) -> bytes:
    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    payloads = []
    for size, image in images:
        buffer = io.BytesIO()
        image.save(buffer, "dib")
        data = buffer.getvalue()
        # BITMAPINFOHEADER 里的 biHeight 要写成 2 倍高（ICO 约定：XOR + AND）
        data = data[:8] + struct.pack("<i", size[1] * 2) + data[12:]
        payloads.append((size, data))
    for (width, height), data in payloads:
        out.write(struct.pack("B", width if width < 256 else 0))
        out.write(struct.pack("B", height if height < 256 else 0))
        out.write(b"\0\0")              # 调色板数 + 保留（共 2 字节）
        out.write(struct.pack("<HH", 1, 32))  # 平面数 + 位深
        out.write(struct.pack("<II", len(data), offset))
        offset += len(data)
    for _size, data in payloads:
        out.write(data)
    return out.getvalue()


# ---------------------------------------------------------------------------
# 字符预览（方便在终端里核对图形）
# ---------------------------------------------------------------------------

def preview(image: Image.Image, size: int) -> str:
    palette = {
        BLACK: "#", OUTLINE: "#", FACE: ".", FACE_HL: "-", FACE_SH: ":",
        FACE_DEEP: ":", GOLD: "*", GOLD_DARK: "+", SILVER: "o", SCREEN: "=",
        (0, 0, 0, 0): " ",
    }
    rows = []
    for y in range(size):
        line = []
        for x in range(size):
            pixel = image.getpixel((x, y))
            best = " "
            for color, char in palette.items():
                if tuple(pixel) == tuple(color):
                    best = char
                    break
            else:
                best = "?"
            line.append(best)
        rows.append("".join(line))
    return "\n".join(rows)


def main() -> int:
    want_preview = "--preview" in sys.argv
    images = []
    for size in SIZES:
        images.append((size, draw_icon(size[0])))
    data = build_ico(images)
    os.makedirs(os.path.dirname(ICON_PATH), exist_ok=True)
    with open(ICON_PATH, "wb") as fh:
        fh.write(data)
    print("已生成 %s（%d 字节，%d 个尺寸：%s）"
          % (ICON_PATH, len(data), len(SIZES),
             " ".join(str(s[0]) for s in SIZES)))
    if want_preview:
        for size in (16, 32):
            print("-" * 40)
            print("尺寸 %dx%d：" % (size, size))
            print(preview(draw_icon(size), size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
