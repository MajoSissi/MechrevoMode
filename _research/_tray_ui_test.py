# 验证托盘交互（必须**提权**运行，目标进程是提权的，UIPI 会吞掉普通进程的消息）：
#   1. 托盘提示（tooltip）显示两行「CPU/GPU - 温度墙 - 功耗」，且不含模式名
#   2. 右键零延迟弹菜单；菜单里没有灰色信息行
#   3. 左键单击/双击 = 立刻打开设置界面（**不能再等 500ms 双击判定窗口**）
#
# 第 3 条是重点：曾经做成「单击弹菜单 + 双击开界面」，因为单击必须先等满
# GetDoubleClickTime() 才能确定不是双击，实测迟钝得没法用，已改成左键直接开界面。
#
# 说明：提示文本归 Explorer 的提示窗口所有，没有可靠的跨进程读取手段，
# 所以这里走两条路 —— 程序自己落的「托盘提示 ->」日志（精确内容）
# ＋ 把鼠标挪到图标上截屏（肉眼确认真的显示出来了）。
import ctypes
import os
import struct
import sys
import time
import zlib
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    u32.SetProcessDPIAware()

WM_CLOSE = 0x0010
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
MN_GETHMENU = 0x01E1
TRAY_UID = 1
SW_HIDE = 0

MIIM_STATE = 0x00000001
MIIM_ID = 0x00000002
MIIM_STRING = 0x00000040
MIIM_FTYPE = 0x00000100
MFT_SEPARATOR = 0x00000800

from _paths import LOG  # 数据目录见 _paths.py（程序同目录\data）
OUTDIR = os.path.dirname(os.path.abspath(__file__))

u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u32.FindWindowW.restype = wintypes.HWND
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.SendMessageW.restype = wintypes.LPARAM
u32.GetMenuItemCount.argtypes = [wintypes.HMENU]
u32.GetMenuItemCount.restype = ctypes.c_int
u32.GetMenuItemInfoW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.BOOL, ctypes.c_void_p]


class MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("fMask", wintypes.UINT),
        ("fType", wintypes.UINT),
        ("fState", wintypes.UINT),
        ("wID", wintypes.UINT),
        ("pad0", wintypes.UINT),
        ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HANDLE),
        ("hbmpUnchecked", wintypes.HANDLE),
        ("dwItemData", ctypes.c_ulonglong),
        ("dwTypeData", wintypes.LPWSTR),
        ("cch", wintypes.UINT),
        ("pad1", wintypes.UINT),
        ("hbmpItem", wintypes.HANDLE),
    ]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID)]


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


def state_text(fs):
    out = []
    if fs & 0x1:
        out.append("灰")
    if fs & 0x2:
        out.append("禁用")
    if fs & 0x8:
        out.append("勾选")
    return "|".join(out) or "-"


def read_menu():
    """把当前弹出菜单的项读回来；没有菜单则返回 None。"""
    mwnd = u32.FindWindowW("#32768", None)
    if not mwnd:
        return None
    hmenu = u32.SendMessageW(mwnd, MN_GETHMENU, 0, 0)
    if not hmenu:
        return None
    n = u32.GetMenuItemCount(hmenu)
    rows = []
    for i in range(n):
        buf = ctypes.create_unicode_buffer(256)
        mii = MENUITEMINFOW()
        mii.cbSize = ctypes.sizeof(MENUITEMINFOW)
        mii.fMask = MIIM_STRING | MIIM_STATE | MIIM_ID | MIIM_FTYPE
        mii.dwTypeData = ctypes.cast(buf, wintypes.LPWSTR)
        mii.cch = 255
        if not u32.GetMenuItemInfoW(hmenu, i, True, ctypes.byref(mii)):
            rows.append(("?", "<读取失败>", "?"))
            continue
        if mii.fType & MFT_SEPARATOR:
            rows.append(("SEP", "", ""))
            continue
        rows.append((str(mii.wID), buf.value, state_text(mii.fState)))
    return mwnd, rows


def close_menu(mwnd):
    u32.PostMessageW(mwnd, WM_CLOSE, 0, 0)
    time.sleep(0.35)


def tray_icon_rect(hwnd):
    nid = NOTIFYICONIDENTIFIER()
    nid.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
    nid.hWnd = hwnd
    nid.uID = TRAY_UID
    r = RECT()
    if shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(r)) != 0:
        return None
    return r


def write_png(path, w, h, rgb):
    """最小 PNG 编码器：环境里没有 Pillow，装一个只为了截屏不值当。"""
    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def grab(x, y, w, h, out):
    hdc = u32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020)  # SRCCOPY
    bi = BI()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h  # 负数 = 自上而下，省得再翻一遍
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    # 屏幕 DC 给的是 BGRA，PNG 要 RGB
    src = buf.raw
    rgb = bytearray(w * h * 3)
    rgb[0::3] = src[2::4]
    rgb[1::3] = src[1::4]
    rgb[2::3] = src[0::4]
    write_png(out, w, h, bytes(rgb))
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    u32.ReleaseDC(0, hdc)


def tail_tip():
    """从日志里取最后一条「托盘提示 ->」的内容（%q 转义后的一行）。"""
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return None, str(e)
    for ln in reversed(lines):
        if "托盘提示 ->" in ln:
            return ln.split("托盘提示 ->", 1)[1].strip(), None
    return None, "日志里找不到「托盘提示 ->」"


def main():
    fails = []
    hwnd = u32.FindWindowW("MechrevoModeTrayWnd", None)
    if not hwnd:
        print("!! 找不到 MechrevoModeTrayWnd（程序没在跑？）", flush=True)
        return 1
    print("托盘窗口 hwnd = 0x%X" % hwnd, flush=True)
    ui = u32.FindWindowW("MechrevoModeMainWnd", None)
    print("设置界面 hwnd = 0x%X" % ui, flush=True)

    # ---------------- 1. tooltip ----------------
    print("\n=== 1. 托盘悬浮提示 ===", flush=True)
    tip, err = tail_tip()
    if tip is None:
        fails.append("提示日志缺失：" + str(err))
        print("  !! " + str(err), flush=True)
    else:
        print("  日志中的提示 = %s" % tip, flush=True)
        # 结构约定：两行，形如 "CPU - 85°C - 38/38/45W" / "GPU - 87°C - 50W"
        lines = tip.strip('"').split("\\n")
        shape_ok = (len(lines) == 2
                    and lines[0].startswith("CPU - ")
                    and lines[1].startswith("GPU - ")
                    and all(" - " in ln and not ln.endswith(" - ") for ln in lines))
        # 模式名不得出现（要求提示里不再显示当前模式文本）
        bad = [n for n in ("静音", "均衡", "狂暴", "自定义", "办公") if n in tip]
        print("  两行「CPU/GPU - 温度 - 功耗」结构 = %s，行数 = %d，含模式名 = %s"
              % (shape_ok, len(lines), bad or "无"), flush=True)
        if not shape_ok:
            fails.append("提示不是两行「CPU/GPU - 温度 - 功耗」结构：%r" % tip)
        if bad:
            fails.append("提示里仍带模式名 %s" % bad)

    # 把鼠标挪到图标上，把提示真的显示出来截个图
    r = tray_icon_rect(hwnd)
    if r:
        print("  托盘图标 rect = (%d,%d)-(%d,%d)" % (r.l, r.t, r.r, r.b), flush=True)
        cx, cy = (r.l + r.r) // 2, (r.t + r.b) // 2
        u32.SetCursorPos(cx, cy)
        u32.mouse_event(0x0001, 0, 0, 0, 0)  # MOUSEEVENTF_MOVE，让悬停判定刷新
        time.sleep(1.8)
        sw = u32.GetSystemMetrics(0)
        sh = u32.GetSystemMetrics(1)
        gl, gt = max(0, r.l - 340), max(0, r.t - 160)
        gw, gh = min(sw - gl, (r.r + 30) - gl), min(sh - gt, (r.b + 24) - gt)
        shot = os.path.join(OUTDIR, "_tray_tooltip.png")
        grab(gl, gt, gw, gh, shot)
        print("  已截图 %s  (%dx%d @ %d,%d)" % (shot, gw, gh, gl, gt), flush=True)
        u32.SetCursorPos(sw // 2, sh // 2)  # 挪开，免得挡着后面的菜单
        time.sleep(0.4)
    else:
        print("  !! Shell_NotifyIconGetRect 失败，跳过截图", flush=True)

    # ---------------- 2. 右键 = 弹菜单（左键不再弹菜单） ----------------
    print("\n=== 2. 右键弹菜单 ===", flush=True)
    left = read_menu()
    if left:
        close_menu(left[0])
    u32.PostMessageW(hwnd, WM_TRAY, TRAY_UID, WM_RBUTTONUP)
    t0 = time.time()
    got = None
    while time.time() - t0 < 3.0:
        got = read_menu()
        if got:
            break
        time.sleep(0.01)
    dt = (time.time() - t0) * 1000
    if not got:
        fails.append("右键没有弹出菜单")
        print("  !! 3 秒内没有出现菜单", flush=True)
    else:
        mwnd, rows = got
        print("  菜单出现，用时约 %.0f ms" % dt, flush=True)
        for i, (idv, txt, st) in enumerate(rows):
            print("    [%2d] %-24s id=%-6s state=%s" % (i, repr(txt) if idv != "SEP" else "──────", idv, st),
                  flush=True)
        print("  菜单应在零延迟出现（不应有 500ms 的双击等待）", flush=True)
        if dt > 200:
            fails.append("菜单出现得太慢（%.0f ms），右键不该有延迟" % dt)
        # 菜单里不该再有灰色信息行：除分隔线外，每一项都必须是可点击的模式项
        info = [t for idv, t, _s in rows
                if idv != "SEP" and not (idv.isdigit() and int(idv) >= 1000)]
        print("  非模式项（信息行）= %s" % (info or "无"), flush=True)
        if info:
            fails.append("菜单里还有信息行：%s" % info)
        modes = [t for idv, t, _s in rows if idv.isdigit() and int(idv) >= 1000]
        print("  模式项 = %s" % modes, flush=True)
        if len(modes) < 2:
            fails.append("模式项只有 %d 个" % len(modes))
        close_menu(mwnd)

    # ---------------- 3. 左键 = 直接打开设置界面，必须立刻响应 ----------------
    print("\n=== 3. 左键单击 / 双击 = 打开设置界面 ===", flush=True)
    if ui:
        for label, msg in (("单击", WM_LBUTTONUP), ("双击", WM_LBUTTONDBLCLK)):
            u32.ShowWindow(ui, SW_HIDE)  # 先强制隐藏，这样「变可见」才说明是点开的
            time.sleep(0.4)
            u32.PostMessageW(hwnd, WM_TRAY, TRAY_UID, msg)
            vis, t0 = False, time.time()
            while time.time() - t0 < 3.0:
                if u32.IsWindowVisible(ui):
                    vis = True
                    break
                time.sleep(0.005)
            cost = (time.time() - t0) * 1000
            print("  左键%s后设置界面可见 = %s（用时 %.1f ms）" % (label, vis, cost), flush=True)
            if not vis:
                fails.append("左键%s没有打开设置界面" % label)
            # 这是这次改动的核心诉求：不能再有 500ms 的双击等待
            elif cost > 200:
                fails.append("左键%s响应太慢（%.0f ms），仍在等双击判定窗口" % (label, cost))
            # 左键不该弹出菜单
            leftover = read_menu()
            print("    同时是否弹出菜单 = %s" % bool(leftover), flush=True)
            if leftover:
                fails.append("左键%s时弹出了菜单" % label)
                close_menu(leftover[0])
        u32.ShowWindow(ui, SW_HIDE)
    else:
        fails.append("找不到设置界面窗口，无法验证左键行为")

    print("\n" + "=" * 60, flush=True)
    if fails:
        for f in fails:
            print("FAIL: " + f, flush=True)
        return 1
    print("=> PASS：提示为两行 CPU/GPU 限制、右键零延迟弹菜单（3 项模式、无信息行）、"
          "左键单击/双击均立刻打开设置界面", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
