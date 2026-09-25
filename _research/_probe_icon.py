# 强制切换档位并抓取托盘图标，用来判定「字模渲染」是否真的会出错。
# wmProbe = WM_APP + 4 = 0x8004，wparam = 档位下标
import ctypes
import time

from PIL import Image

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

WM_PROBE = 0x8004
ICON_X, ICON_Y = 2178, 1558
ICON_W, ICON_H = 28, 30


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


def switch(idx):
    hwnd = user32.FindWindowW("MechrevoModeTrayWnd", None)
    user32.PostMessageW(hwnd, WM_PROBE, idx, 0)


def grab():
    gdi32 = ctypes.windll.gdi32
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, sw, sh)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, sw, sh, hdc, 0, 0, 0x00CC0020)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    class BI(ctypes.Structure):
        _fields_ = [("bmiHeader", BIH), ("bmiColors", ctypes.c_uint32 * 3)]

    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = sw
    bi.bmiHeader.biHeight = -sh
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(sw * sh * 4)
    gdi32.GetDIBits(mem, bmp, 0, sh, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (sw, sh), buf, "raw", "BGRA", 0, 1).convert("RGB")
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    return img


def show(img, tag):
    px = img.load()
    print("--- %s ---" % tag)
    for y in range(ICON_Y, ICON_Y + ICON_H):
        row = []
        for x in range(ICON_X, ICON_X + ICON_W):
            r, g, b = px[x, y]
            if r > 180 and g > 180 and b > 180:
                c = "#"
            elif r < 45 and g < 45 and b < 45:
                c = "."
            elif abs(r - g) < 30 and abs(g - b) < 30:
                c = "-"
            else:
                c = "o"
            row.append(c)
        print("".join(row))


order = [int(a) for a in __import__("sys").argv[1:]] or [0]
for i in order:
    switch(i)
    time.sleep(2.0)
    show(grab(), "档位下标 %d" % i)
