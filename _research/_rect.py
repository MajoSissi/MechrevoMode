# 只读：读取主界面窗口矩形，与「工作区居中」位置比对。
# 用法: python _rect.py
import ctypes
from ctypes import wintypes

u = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    u.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


def find(cls, title):
    return u.FindWindowW(cls, title)


hwnd_main = find("MechrevoModeMainWnd", None)
hwnd_tray = find("MechrevoModeTrayWnd", None)
print("main hwnd =", hwnd_main)
print("tray hwnd =", hwnd_tray)

if hwnd_main:
    r = RECT()
    u.GetWindowRect(hwnd_main, ctypes.byref(r))
    w, h = r.r - r.l, r.b - r.t
    print("main rect = (%d,%d)-(%d,%d)  size=%dx%d" % (r.l, r.t, r.r, r.b, w, h))
    print("main visible =", bool(u.IsWindowVisible(hwnd_main)))

    sr = RECT()
    u.SystemParametersInfoW(0x0030, 0, ctypes.byref(sr), 0)
    sw, sh = sr.r - sr.l, sr.b - sr.t
    print("workarea   = (%d,%d)-(%d,%d)  %dx%d" % (sr.l, sr.t, sr.r, sr.b, sw, sh))

    ex, ey = sr.l + (sw - w) // 2, sr.t + (sh - h) // 2
    print("expected   = (%d,%d)" % (ex, ey))
    print("delta      = (%+d,%+d)" % (r.l - ex, r.t - ey))
    print("monitor    =", u.MonitorFromWindow(hwnd_main, 2))
