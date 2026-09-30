# 按窗口类名截图（只读 PrintWindow，不打扰交互），纯标准库输出 PNG。
#
# 与 _shotwin.py 的区别：不依赖 Pillow。本机托管 Python 没有 PIL，
# 而 PNG 本身用 zlib + struct 就能写，没必要为一张验证图去装包。
import ctypes
import struct
import sys
import time
import zlib
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class BI(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", ctypes.c_uint32 * 3)]


def write_png(path, w, h, bgra, flip=True):
    rows = []
    stride = w * 4
    order = range(h - 1, -1, -1) if flip else range(h)
    for y in order:                      # DIB 是自下而上，PNG 是自上而下
        off = y * stride
        row = bytearray(1)               # filter type 0
        for x in range(0, stride, 4):
            row += bgra[off + x + 2:off + x + 3]   # R
            row += bgra[off + x + 1:off + x + 2]   # G
            row += bgra[off + x + 0:off + x + 1]   # B
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main():
    cls, out = sys.argv[1], sys.argv[2]
    hwnd = user32.FindWindowW(cls, None)
    if not hwnd:
        print("window not found: " + cls)
        return 1
    user32.ShowWindow(hwnd, 5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)

    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.r - r.l, r.b - r.t
    print("hwnd=%d rect=(%d,%d) %dx%d" % (hwnd, r.l, r.t, w, h))

    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    if user32.PrintWindow(hwnd, mem, 2) == 0:
        user32.PrintWindow(hwnd, mem, 0)

    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h              # 负值 = 自上而下，省一次翻转
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    write_png(out, w, h, buf.raw, flip=False)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)
    print("saved", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
