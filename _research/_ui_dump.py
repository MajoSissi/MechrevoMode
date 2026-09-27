# 把设置界面的所有子控件（类名 / ID / 文本 / 位置）按屏幕位置排出来。
#
# 为什么要按位置排：静态文本控件的 ID 都是 0，光看 ID 认不出哪一行是哪一列；
# 而界面上「原始模式」这类只读列没有别的读取手段。按 (y, x) 排完，
# 就能像看表格一样核对每一行的每一列。
#
# 只读探测，不改任何东西；窗口隐藏着也能枚举（EnumChildWindows 不要求可见）。
# 被测程序是提权的，UIPI 会让普通进程的消息发不进去，所以本脚本要提权运行。
import ctypes
import sys
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
OUTER = "MechrevoModeMainWnd"

WM_GETTEXT = 0x000D
SMTO_ABORTIFHUNG = 0x0002

u32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
u32.SendMessageTimeoutW.restype = wintypes.LPARAM
u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u32.FindWindowW.restype = wintypes.HWND


def wtext(h):
    """读另一个进程的控件文本。用 SendMessageTimeout 而不是 SendMessage：
    目标卡住时不会把本脚本一起挂死。
    （探测脚本不要 import 别的测试脚本来复用这类小工具 —— 那个脚本的 main
      一旦没关在 __main__ 里，import 的瞬间就会把它的副作用全跑一遍。）"""
    buf = ctypes.create_unicode_buffer(1024)
    u32.SendMessageTimeoutW(h, WM_GETTEXT, 1024,
                            ctypes.cast(buf, ctypes.c_void_p).value,
                            SMTO_ABORTIFHUNG, 800, None)
    return buf.value


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


def children(hwnd):
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, cls, 256)
        r = RECT()
        u32.GetWindowRect(h, ctypes.byref(r))
        out.append({
            "hwnd": h,
            "cls": cls.value,
            "id": u32.GetDlgCtrlID(h),
            "text": wtext(h),
            "x": r.l, "y": r.t, "w": r.r - r.l, "h": r.b - r.t,
            "vis": bool(u32.IsWindowVisible(h)),
        })
        return True

    u32.EnumChildWindows(hwnd, cb, 0)
    return out


def main():
    hwnd = u32.FindWindowW(OUTER, None)
    if not hwnd:
        print("!! 找不到 %s（程序没在跑？）" % OUTER, flush=True)
        return 1
    r = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    print("主窗口 hwnd = 0x%X  位置 (%d,%d) 尺寸 %dx%d  可见=%s"
          % (hwnd, r.l, r.t, r.r - r.l, r.b - r.t, bool(u32.IsWindowVisible(hwnd))), flush=True)

    rows = sorted(children(hwnd), key=lambda c: (c["y"], c["x"]))
    print("\n%-10s %-6s %-5s %-22s %s" % ("类名", "ID", "可见", "位置", "文本"), flush=True)
    print("-" * 78, flush=True)
    for c in rows:
        print("%-10s %-6d %-5s (%4d,%4d) %3dx%-3d  %r"
              % (c["cls"], c["id"], "Y" if c["vis"] else "n",
                 c["x"], c["y"], c["w"], c["h"], c["text"]), flush=True)
    print("\n共 %d 个子控件" % len(rows), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
