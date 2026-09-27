# 从外部戳一下 MechrevoMode 的隐藏窗口，判断问题出在哪一环：
#
#   1. 枚举该进程的顶层窗口 —— 窗口在不在、属于哪个线程
#   2. SendMessageTimeout(WM_NULL) —— UI 线程还活着吗（消息循环有没有卡住）
#   3. SendMessageTimeout(WM_TIMER, wParam=1) —— 手动投递一条定时器消息，
#      wndProc 里的探针会把它写进日志。日志里出现 [tip] 就说明
#      「派发没问题，是系统没在投递 WM_TIMER」；不出现就是「窗口过程根本没被调用」。
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_NULL = 0x0000
WM_TIMER = 0x0113
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM


def pid_of(name):
    """用 CreateToolhelp32Snapshot 找进程（避免依赖 tasklist）"""
    TH32CS_SNAPPROCESS = 0x2
    MAX_PATH = 260

    class PE32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * MAX_PATH),
        ]

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    out = []
    ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower() == name.lower():
            out.append(e.th32ProcessID)
        ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    return out


def windows_of(pid):
    found = []

    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            cls = ctypes.create_unicode_buffer(256)
            txt = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, txt, 256)
            found.append((hwnd, p.value, cls.value, txt.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def send(hwnd, msg, wparam, timeout_ms=1500):
    res = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(hwnd, msg, wparam, 0, SMTO_ABORTIFHUNG | SMTO_BLOCK,
                                    timeout_ms, ctypes.byref(res))
    if not ok:
        err = ctypes.get_last_error()
        return None, "超时/失败 (GetLastError=%d)" % err
    return res.value, "OK"


def main():
    pids = pid_of("MechrevoMode.exe")
    print("MechrevoMode pid =", pids or "未运行（先启动它再跑这个脚本）")
    if not pids:
        return
    for pid in pids:
        wins = windows_of(pid)
        print("  pid %d 有 %d 个顶层窗口" % (pid, len(wins)))
        target = None
        for hwnd, _p, cls, txt in wins:
            mark = ""
            if cls == "MechrevoModeTrayWnd":
                target = hwnd
                mark = "   <== 托盘窗口"
            print("    hwnd=0x%X class=%r title=%r%s" % (hwnd, cls, txt, mark))
        if target is None:
            print("    没找到 MechrevoModeTrayWnd —— 程序可能还没起完")
            continue

        print("\n  -> 对托盘窗口 hwnd=0x%X 逐条发消息" % target)
        # 各条消息在 wndProc 里走的路径不同，能定位卡在哪一环：
        #   WM_NULL     什么都不做            —— 只探活
        #   0x8002      wmStateChanged -> syncTray -> applyTip -> tooltip()
        #   0x8006      wmTipTick      -> refreshTip -> tooltip()
        #   WM_TIMER    系统定时器消息
        probes = [
            (WM_NULL, "WM_NULL（探活）"),
            (0x8002, "wmStateChanged -> syncTray/applyTip（走 tooltip 全流程）"),
            (0x8006, "wmTipTick -> refreshTip（探针会写 [tip] 日志）"),
            (WM_TIMER, "WM_TIMER"),
        ]
        for msg, label in probes:
            r, note = send(target, msg, 1 if msg in (0x8006, WM_TIMER) else 0)
            print("    %-52s -> %s" % (label, note if r is None else "OK r=%s" % r))
            time.sleep(0.3)
        print("\n  请查看日志里有没有 [tip] 开头的行。")


if __name__ == "__main__":
    sys.exit(main())
