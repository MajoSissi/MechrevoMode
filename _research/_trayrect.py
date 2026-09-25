# 用 Shell_NotifyIconGetRect 精确定位本程序托盘图标的屏幕位置。
# 只读，不打扰用户。
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID)]


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


hwnd = user32.FindWindowW("MechrevoModeTrayWnd", None)
print("tray hwnd =", hwnd)

nid = NOTIFYICONIDENTIFIER()
nid.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
nid.hWnd = hwnd
nid.uID = 1
print("sizeof(NOTIFYICONIDENTIFIER) =", nid.cbSize)

r = RECT()
hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(r))
print("hr = 0x%08X" % (hr & 0xFFFFFFFF))
if hr == 0:
    print("our tray icon rect = (%d,%d)-(%d,%d)  size=%dx%d"
          % (r.l, r.t, r.r, r.b, r.r - r.l, r.b - r.t))
