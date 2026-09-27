# 验证「信息随模式变化」：切到狂暴，看菜单里的限制值有没有跟着刷新，再切回来。
#
# 用 wmProbe（WM_APP+4，wparam = Items 下标）触发切换，等价于点托盘菜单里的那一项，
# 不用去操作真菜单 —— 但读结果仍然走「弹菜单 → MN_GETHMENU」这条路，验证的是真实 UI。
import ctypes
import sys
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
WM_CLOSE = 0x0010
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_PROBE = WM_APP + 4
WM_RBUTTONUP = 0x0205
MN_GETHMENU = 0x01E1
TRAY_UID = 1
MIIM_STATE = 0x00000001
MIIM_STRING = 0x00000040
MIIM_FTYPE = 0x00000100
MFT_SEPARATOR = 0x00000800

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


class MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("fMask", wintypes.UINT), ("fType", wintypes.UINT),
        ("fState", wintypes.UINT), ("wID", wintypes.UINT), ("pad0", wintypes.UINT),
        ("hSubMenu", wintypes.HMENU), ("hbmpChecked", wintypes.HANDLE),
        ("hbmpUnchecked", wintypes.HANDLE), ("dwItemData", ctypes.c_ulonglong),
        ("dwTypeData", wintypes.LPWSTR), ("cch", wintypes.UINT),
        ("pad1", wintypes.UINT), ("hbmpItem", wintypes.HANDLE),
    ]


def read_menu():
    mwnd = 0
    t0 = time.time()
    while time.time() - t0 < 5:
        mwnd = u32.FindWindowW("#32768", None)
        if mwnd:
            break
        time.sleep(0.1)
    if not mwnd:
        return None
    hmenu = u32.SendMessageW(mwnd, MN_GETHMENU, 0, 0)
    if not hmenu:
        u32.PostMessageW(mwnd, WM_CLOSE, 0, 0)
        return None
    n = u32.GetMenuItemCount(hmenu)
    items = []
    for i in range(n):
        buf = ctypes.create_unicode_buffer(256)
        mii = MENUITEMINFOW()
        mii.cbSize = ctypes.sizeof(MENUITEMINFOW)
        mii.fMask = MIIM_STRING | MIIM_STATE | MIIM_FTYPE
        mii.dwTypeData = ctypes.cast(buf, wintypes.LPWSTR)
        mii.cch = 255
        if not u32.GetMenuItemInfoW(hmenu, i, True, ctypes.byref(mii)):
            continue
        if mii.fType & MFT_SEPARATOR:
            items.append(("SEP", ""))
            continue
        mark = "  ✓" if mii.fState & 0x8 else ""
        items.append((buf.value, mark))
    u32.PostMessageW(mwnd, WM_CLOSE, 0, 0)
    time.sleep(0.3)
    return items


def show(index, label):
    if index is not None:
        print("  >> PostMessage(wmProbe, %d)  # 切到 %s" % (index, label), flush=True)
        u32.PostMessageW(HWND, WM_PROBE, index, 0)
        # GCU 切换后会**先推一条旧状态**（日志里能看到「档位生效 -> 上一个 Profile」
        # 然后才是新 Profile），固定几秒不够稳：等到日志里的「档位生效」刷出目标模式再读。
        # 这里退一步用固定 9 秒 + 重复弹菜单取第二次的值，比 4.5 秒可靠得多。
        time.sleep(9.0)
    u32.PostMessageW(HWND, WM_TRAY, TRAY_UID, WM_RBUTTONUP)
    items = read_menu()
    if not items:
        print("  !! 读不到菜单", flush=True)
        return []
    print("  菜单内容：", flush=True)
    for t, m in items:
        print("      %s%s" % ("──────" if t == "SEP" else repr(t), m), flush=True)
    return items


HWND = 0


def main():
    global HWND
    HWND = u32.FindWindowW("MechrevoModeTrayWnd", None)
    if not HWND:
        print("!! 找不到托盘窗口", flush=True)
        return 1
    print("hwnd = 0x%X" % HWND, flush=True)

    print("\n=== 1) 先看当前（应为「静音」档的限制）===", flush=True)
    a = show(None, "当前")

    print("\n=== 2) 切到「狂暴」(Items 下标 5) ===", flush=True)
    b = show(5, "狂暴")

    print("\n=== 3) 切回「静音」(Items 下标 3) ===", flush=True)
    c = show(3, "静音")

    def info_rows(items):
        """取分隔线之前的所有信息行（温度行 + 「- 」开头的功耗行都要算）"""
        out = []
        for t, _m in items:
            if t.startswith("────"):
                break
            out.append(t)
        return out

    ra, rb, rc = info_rows(a), info_rows(b), info_rows(c)
    print("\n" + "=" * 60, flush=True)
    print("  当前 :", ra, flush=True)
    print("  狂暴 :", rb, flush=True)
    print("  静音 :", rc, flush=True)
    print("", flush=True)

    changed = rb and rb != ra
    restored = rc and rc == ra
    print("  切到狂暴后信息有变化 = %s" % changed, flush=True)
    print("  切回静音后恢复原值   = %s" % restored, flush=True)

    if changed and restored:
        print("\n=> PASS：限制信息随模式实时刷新", flush=True)
        return 0
    print("\n=> 未完全通过（changed=%s restored=%s）" % (changed, restored), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
