# 把托盘悬浮提示的**实际文本**从 Explorer 的提示控件里读回来。
#
# 试过但走不通的两条路（别再走）：
#   * 截图：提示窗口是 DWM 合成的分层窗口，GetDC(0)+BitBlt 抓不到它，
#     截出来只有任务栏，干干净净什么都没有。
#   * 悬停后读窗口标题：SetCursorPos 挪过去也不会让 Shell 真的开始悬浮计时
#     （悬停判定不是靠 WM_MOUSEMOVE 就够了），窗口标题一直是空的。
#
# 真正可靠的是 TTM_GETTEXT：向所有 tooltips_class32 窗口按几种可能的
# (hWnd, uId, uFlags) 组合去问「绑在托盘图标上的工具提示是什么」——
# Shell 内部怎么填 TOOLINFO 没有公开约定，所以把几种常见组合都试一遍，
# 谁能返回含 CPU 的文本就是以谁为准。
#
# 只读，不需要提权。
import ctypes
import sys
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    u32.SetProcessDPIAware()

TTM_GETTEXT = 0x0400 + 56
TTF_IDISHWND = 0x0001
TRAY_UID = 1

u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u32.FindWindowW.restype = wintypes.HWND
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.SendMessageW.restype = wintypes.LPARAM

ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID)]


class RECT(ctypes.Structure):
    _fields_ = [("l", wintypes.LONG), ("t", wintypes.LONG),
                ("r", wintypes.LONG), ("b", wintypes.LONG)]


class TOOLINFO(ctypes.Structure):
    # x64 = 72 字节。cbSize 写错会直接返回 FALSE，什么都没得看。
    _fields_ = [("cbSize", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("hwnd", wintypes.HWND), ("uId", ctypes.c_void_p),
                ("rect", RECT), ("hinst", wintypes.HINSTANCE),
                ("lpszText", wintypes.LPWSTR), ("lParam", wintypes.LPARAM),
                ("lpReserved", ctypes.c_void_p)]


def class_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def tooltip_windows():
    out = []

    def visit(hwnd, _lp):
        if class_of(hwnd) == "tooltips_class32":
            out.append(hwnd)
        return True

    u32.EnumWindows(ENUMPROC(visit), 0)
    return out


def ask(hwnd_tt, owner, uid, flags):
    buf = ctypes.create_unicode_buffer(512)
    ti = TOOLINFO()
    ti.cbSize = ctypes.sizeof(TOOLINFO)
    ti.uFlags = flags
    ti.hwnd = owner
    ti.uId = uid
    ti.lpszText = ctypes.cast(buf, wintypes.LPWSTR)
    r = u32.SendMessageW(hwnd_tt, TTM_GETTEXT, 0, ctypes.addressof(ti))
    return (buf.value or None) if r else None


def main():
    print("sizeof(TOOLINFO) =", ctypes.sizeof(TOOLINFO), "(x64 应为 72)", flush=True)
    tray = u32.FindWindowW("MechrevoModeTrayWnd", None)
    if not tray:
        print("!! 找不到托盘窗口（程序没在跑？）", flush=True)
        return 1
    def hx(h):
        return "0x%X" % h if h else "无"

    taskbar = u32.FindWindowW("Shell_TrayWnd", None)
    notify = u32.FindWindowW("TrayNotifyWnd", None)
    print("托盘窗口=%s Shell_TrayWnd=%s TrayNotifyWnd=%s"
          % (hx(tray), hx(taskbar), hx(notify)), flush=True)

    tts = tooltip_windows()
    print("tooltips_class32 窗口数 = %d  %s" % (len(tts), ["0x%X" % h for h in tts]), flush=True)

    owners = [("托盘窗口", tray), ("Shell_TrayWnd", taskbar), ("TrayNotifyWnd", notify)]
    combos = []
    for oname, o in owners:
        if not o:
            continue
        combos.append((oname, o, TRAY_UID, 0))          # uId = 图标 uID
        combos.append((oname, o, o, TTF_IDISHWND))      # uId = 拥有者 hwnd

    found = None
    for tt in tts:
        for oname, owner, uid, flags in combos:
            txt = ask(tt, owner, uid, flags)
            if txt:
                tag = "0x%X/uid=%s/flags=0x%X" % (tt, "hwnd" if flags else str(uid), flags)
                print("  命中 %-14s %-34s -> %s" % (oname, tag, txt.replace("\n", "\\n")), flush=True)
                if "CPU" in txt and not found:
                    found = txt
    print("-" * 62, flush=True)
    if not found:
        print("=> FAIL：所有组合都问不出含 CPU 的提示文本", flush=True)
        return 1
    lines = found.split("\n")
    for i, ln in enumerate(lines):
        print("   [%d] %r" % (i, ln), flush=True)
    bad = [n for n in ("静音", "均衡", "狂暴", "自定义", "办公") if n in found]
    if len(lines) == 2 and lines[0].startswith("CPU - ") and lines[1].startswith("GPU - ") and not bad:
        print("=> PASS：Explorer 里存的就是两行「CPU/GPU - 温度 - 功耗」，无模式名", flush=True)
        return 0
    print("=> FAIL：结构不符（模式名残留=%s）" % (bad or "无"), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
