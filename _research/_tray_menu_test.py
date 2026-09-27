# 弹出托盘右键菜单，把菜单项的文本与状态读回来。
#
# 两个前提（都是踩过的坑）：
# 1. 必须**提权**运行。目标 UI 是提权进程，UIPI 会把普通进程发来的消息静默丢掉，
#    表现是「PostMessage 返回成功但什么事都没发生」。
# 2. TrackPopupMenuEx 是**模态**的，消息循环被它占着 —— 所以只能先 PostMessage
#    触发（非阻塞），再回头去找菜单窗口，不能在 SendMessage 里等。
#
# 菜单窗口的类名是 "#32768"；用 MN_GETHMENU 从它手里换出 HMENU，
# 再用 GetMenuItemInfoW 逐项读文本和 fState（判断灰/禁用/勾选/分隔线）。
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_CLOSE = 0x0010
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_RBUTTONUP = 0x0205
MN_GETHMENU = 0x01E1
TRAY_UID = 1

MIIM_STATE = 0x00000001
MIIM_ID = 0x00000002
MIIM_STRING = 0x00000040
MIIM_FTYPE = 0x00000100
MFT_SEPARATOR = 0x00000800
MFS_GRAYED = 0x00000003  # MFS_GRAYED(0x1) | MFS_DISABLED(0x2)

u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u32.FindWindowW.restype = wintypes.HWND
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.PostMessageW.restype = wintypes.BOOL
u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.SendMessageW.restype = wintypes.LPARAM
u32.GetMenuItemCount.argtypes = [wintypes.HMENU]
u32.GetMenuItemCount.restype = ctypes.c_int
u32.GetMenuItemInfoW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.BOOL, ctypes.c_void_p]
u32.GetMenuItemInfoW.restype = wintypes.BOOL
k32.GetLastError.argtypes = []
k32.GetLastError.restype = wintypes.DWORD


class MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("fMask", wintypes.UINT),
        ("fType", wintypes.UINT),
        ("fState", wintypes.UINT),
        ("wID", wintypes.UINT),
        ("pad0", wintypes.UINT),      # 让后面的 HMENU 落到 8 字节边界
        ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HANDLE),
        ("hbmpUnchecked", wintypes.HANDLE),
        ("dwItemData", ctypes.c_ulonglong),
        ("dwTypeData", wintypes.LPWSTR),
        ("cch", wintypes.UINT),
        ("pad1", wintypes.UINT),
        ("hbmpItem", wintypes.HANDLE),
    ]


def state_text(fState):
    out = []
    if fState & 0x1:
        out.append("灰")
    if fState & 0x2:
        out.append("禁用")
    if fState & 0x8:
        out.append("勾选")
    return "|".join(out) or "-"


def main():
    print("MENUITEMINFOW sizeof =", ctypes.sizeof(MENUITEMINFOW), "(x64 应为 80)", flush=True)

    hwnd = u32.FindWindowW("MechrevoModeTrayWnd", None)
    if not hwnd:
        print("!! 找不到托盘窗口 MechrevoModeTrayWnd（程序没在跑？）", flush=True)
        return 1
    print("托盘窗口 hwnd = 0x%X" % hwnd, flush=True)

    ok = u32.PostMessageW(hwnd, WM_TRAY, TRAY_UID, WM_RBUTTONUP)
    print("PostMessage(WM_TRAY/RBUTTONUP) 返回 %s" % ok, flush=True)

    mwnd = 0
    t0 = time.time()
    while time.time() - t0 < 5:
        mwnd = u32.FindWindowW("#32768", None)
        if mwnd:
            break
        time.sleep(0.1)
    if not mwnd:
        print("!! 5 秒内没出现菜单窗口", flush=True)
        return 1
    print("菜单窗口 hwnd = 0x%X" % mwnd, flush=True)

    hmenu = u32.SendMessageW(mwnd, MN_GETHMENU, 0, 0)
    if not hmenu:
        print("!! MN_GETHMENU 失败 GetLastError=%d" % k32.GetLastError(), flush=True)
        u32.PostMessageW(mwnd, WM_CLOSE, 0, 0)
        return 1

    n = u32.GetMenuItemCount(hmenu)
    print("菜单项数 = %d" % n, flush=True)
    print("-" * 66, flush=True)

    rows = []
    for i in range(n):
        buf = ctypes.create_unicode_buffer(256)
        mii = MENUITEMINFOW()
        mii.cbSize = ctypes.sizeof(MENUITEMINFOW)
        mii.fMask = MIIM_STRING | MIIM_STATE | MIIM_ID | MIIM_FTYPE
        mii.dwTypeData = ctypes.cast(buf, wintypes.LPWSTR)
        mii.cch = 255
        if not u32.GetMenuItemInfoW(hmenu, i, True, ctypes.byref(mii)):
            print("  [%2d] <读取失败 GetLastError=%d>" % (i, k32.GetLastError()), flush=True)
            continue
        if mii.fType & MFT_SEPARATOR:
            print("  [%2d] ────── 分隔线" % i, flush=True)
            rows.append(("SEP", "", ""))
            continue
        txt = buf.value
        print("  [%2d] %-28s id=%-5d state=%s" % (i, repr(txt), mii.wID, state_text(mii.fState)),
              flush=True)
        rows.append((str(mii.wID), txt, state_text(mii.fState)))

    print("-" * 66, flush=True)

    # 收尾：必须关掉，否则模态循环一直占着，程序再也收不到消息
    u32.PostMessageW(mwnd, WM_CLOSE, 0, 0)
    time.sleep(0.3)

    # 判定
    # 「信息行」的特征：id=0 且被禁用。期望结构是
    #   若干信息行 → 分隔线 → 模式项(id>=1000, 可点击)
    lead = []          # 开头连续的信息行
    sep_at = None      # 第一个分隔线的下标
    for idx, (idv, txt, st) in enumerate(rows):
        # 注意：分隔线在 rows 里记的是 idv="SEP"（文本位为空串），别搞错字段
        if idv == "SEP":
            if sep_at is None:
                sep_at = idx
            continue
        if sep_at is not None:
            continue
        lead.append((idv, txt, st))

    after = rows[sep_at + 1:] if sep_at is not None else []
    lead_ok = lead and all(idv == "0" and "禁用" in st for idv, _t, st in lead)
    tail_ok = all(idv != "SEP" and int(idv) >= 1000 for idv, _t, _s in after)
    has_cpu = any(t.startswith("CPU") for _i, t, _s in rows)
    has_gpu = any(t.startswith("GPU") for _i, t, _s in rows)
    # 功耗行用 "- " 列表符表示归属于上面的温度行
    indent_ok = any(t.startswith("- ") for _i, t, _s in rows)

    print("  信息行(顶部 %d 行): %s" % (len(lead), [t for _i, t, _s in lead]), flush=True)
    print("  其后紧跟分隔线 = %s" % (sep_at == len(lead)), flush=True)
    print("  分隔线之后全是模式项 = %s  %s" % (tail_ok, [t for _i, t, _s in after]), flush=True)
    print("  CPU 行存在 = %s，GPU 行存在 = %s" % (has_cpu, has_gpu), flush=True)
    print("  有「- 」前缀的功耗行 = %s" % indent_ok, flush=True)

    ok_all = bool(lead) and lead_ok and tail_ok and (sep_at == len(lead)) \
        and has_cpu and has_gpu and indent_ok
    if ok_all:
        print()
        print("=> PASS：顶部为 CPU/GPU 温度行 + 「- 」列表式的功耗行，灰色不可点击", flush=True)
        return 0
    print()
    print("=> 未完全通过", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
