# 看目标进程还在不在 + 打印日志尾部（用 ctypes 直接读 psapi，绕开 tasklist 可能被安全策略拦掉的问题）
import ctypes
import os
import sys
import time
from ctypes import wintypes

LOG = r"D:\User\OneDrive\Programm\MechrevoMode\data\log.txt"
NAME = "MechrevoMode.exe"

TH32CS_SNAPPROCESS = 0x2
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


def procs():
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = k.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out.append((e.th32ProcessID, e.szExeFile))
        ok = k.Process32NextW(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    return out


def main():
    hits = [(p, n) for p, n in procs() if n.lower() == NAME.lower()]
    print("MechrevoMode 进程 =", hits or "未运行")
    for n in ("GCUBridge.exe", "GCUService.exe"):
        print("  %-18s = %s" % (n, [p for p, x in procs() if x.lower() == n.lower()] or "未运行"))
    print()
    with open(LOG, "rb") as f:
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    print("日志共 %d 行，尾部 25 行：" % len(lines))
    for ln in lines[-25:]:
        print("  " + ln)
    print()
    print("文件修改时间 = %s，当前 = %s（差 %.1f 秒）"
          % (time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(LOG))),
             time.strftime("%H:%M:%S"), time.time() - os.path.getmtime(LOG)))


if __name__ == "__main__":
    sys.exit(main())
