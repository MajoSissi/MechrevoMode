# 按窗口类名截图（只读 PrintWindow，不打扰交互）。
import ctypes
import sys
import time
from ctypes import wintypes

from PIL import Image

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


cls = sys.argv[1]
out = sys.argv[2]

hwnd = user32.FindWindowW(cls, None)
if not hwnd:
    sys.exit("window not found: " + cls)
user32.ShowWindow(hwnd, 5)
user32.SetForegroundWindow(hwnd)
time.sleep(0.6)

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
bi.bmiHeader.biHeight = -h
bi.bmiHeader.biPlanes = 1
bi.bmiHeader.biBitCount = 32
bi.bmiHeader.biCompression = 0
buf = ctypes.create_string_buffer(w * h * 4)
gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
img.save(out)
gdi32.DeleteObject(bmp)
gdi32.DeleteDC(mem)
user32.ReleaseDC(0, hdc)
print("saved", out)
