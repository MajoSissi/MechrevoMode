# 把托盘里**Explorer 实际绘制出来的**图标放大截出来，肉眼确认形状/白边/字模。
# 用途：-dump-icons 出的是我们自己算的位图，这里出的是真·系统渲染结果，
# 两者对得上才说明图标真的生效了（比如「图标没变」多半是 Shell 缓存旧图标）。
import ctypes
import os
import sys
import time
from ctypes import wintypes

import _tray_ui_test as T

u32 = T.u32
shell32 = T.shell32
OUTDIR = os.path.dirname(os.path.abspath(__file__))
ZOOM = int(sys.argv[1]) if len(sys.argv) > 1 else 8


def grab_rgb(x, y, w, h):
    hdc = u32.GetDC(0)
    mem = T.gdi32.CreateCompatibleDC(hdc)
    bmp = T.gdi32.CreateCompatibleBitmap(hdc, w, h)
    T.gdi32.SelectObject(mem, bmp)
    T.gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020)
    bi = T.BI()
    bi.bmiHeader.biSize = ctypes.sizeof(T.BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    T.gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    src = buf.raw
    rgb = bytearray(w * h * 3)
    rgb[0::3] = src[2::4]
    rgb[1::3] = src[1::4]
    rgb[2::3] = src[0::4]
    T.gdi32.DeleteObject(bmp)
    T.gdi32.DeleteDC(mem)
    u32.ReleaseDC(0, hdc)
    return bytes(rgb)


def analyze(w, h, rgb, bg):
    """量一下 Explorer 真正画出来的图标几何：包围盒、长宽比、白边厚度。

    眼睛看放大图会被骗（尤其圆角会让人觉得「变窄了」），这里直接数像素。
    """
    def is_bg(i):
        return (abs(rgb[i] - bg[0]) <= 12 and abs(rgb[i + 1] - bg[1]) <= 12
                and abs(rgb[i + 2] - bg[2]) <= 12)

    # 背景取左上角像素（托盘底色）
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if not is_bg((y * w + x) * 3):
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 < 0:
        print("!! 全是背景色，没找到图标", flush=True)
        return
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    print("包围盒 = (%d,%d)-(%d,%d)  %dx%d  宽高差 = %d"
          % (x0, y0, x1, y1, bw, bh, bw - bh), flush=True)

    # 过中心横扫一行，量白边厚度（连续接近白色的那几段）
    cy = (y0 + y1) // 2
    runs = []
    for x in range(x0, x1 + 1):
        i = (cy * w + x) * 3
        r, g, b = rgb[i], rgb[i + 1], rgb[i + 2]
        if r > 225 and g > 225 and b > 225:
            kind = "白"
        elif is_bg(i):
            kind = "底"
        else:
            kind = "彩"
        if runs and runs[-1][0] == kind:
            runs[-1][1] += 1
        else:
            runs.append([kind, 1])
    print("中心行 y=%d 颜色段: %s"
          % (cy, " ".join("%s×%d" % (k, n) for k, n in runs)), flush=True)
    print("（当前设计没有白边，所以白只应出现在中间：彩×N 白×M 彩×K）", flush=True)

    # 白色笔画（字模）的包围盒 —— 直接用眼睛估余量会被圆角误导，数像素才准
    wx0, wy0, wx1, wy1 = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            if rgb[i] > 225 and rgb[i + 1] > 225 and rgb[i + 2] > 225:
                wx0, wy0 = min(wx0, x), min(wy0, y)
                wx1, wy1 = max(wx1, x), max(wy1, y)
    if wx1 < 0:
        print("!! 没有白色像素，字模没画出来", flush=True)
        return
    print("笔画包围盒 = (%d,%d)-(%d,%d)  %dx%d"
          % (wx0, wy0, wx1, wy1, wx1 - wx0 + 1, wy1 - wy0 + 1), flush=True)
    print("笔画到图标边缘的余量: 左 %d 右 %d 上 %d 下 %d（图标 %dx%d @ %d,%d）"
          % (wx0 - x0, x1 - wx1, wy0 - y0, y1 - wy1, bw, bh, x0, y0), flush=True)


def main():
    hwnd = u32.FindWindowW("MechrevoModeTrayWnd", None)
    if not hwnd:
        print("!! 程序没在跑", flush=True)
        return 1
    r = T.tray_icon_rect(hwnd)
    if not r:
        print("!! Shell_NotifyIconGetRect 失败", flush=True)
        return 1
    print("托盘图标 rect = (%d,%d)-(%d,%d)  %dx%d"
          % (r.l, r.t, r.r, r.b, r.r - r.l, r.b - r.t), flush=True)

    w, h = r.r - r.l, r.b - r.t
    rgb = grab_rgb(r.l, r.t, w, h)
    bg = (rgb[0], rgb[1], rgb[2])
    print("托盘底色 = #%02X%02X%02X" % bg, flush=True)
    analyze(w, h, rgb, bg)

    bw, bh = w * ZOOM, h * ZOOM
    big = bytearray(bw * bh * 3)
    for y in range(bh):
        sy = y // ZOOM
        for x in range(bw):
            si = (sy * w + x // ZOOM) * 3
            di = (y * bw + x) * 3
            big[di:di + 3] = rgb[si:si + 3]
    out = os.path.join(OUTDIR, "_tray_icon_zoom.png")
    T.write_png(out, bw, bh, bytes(big))
    print("已放大 %dx -> %s (%dx%d)" % (ZOOM, out, bw, bh), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
