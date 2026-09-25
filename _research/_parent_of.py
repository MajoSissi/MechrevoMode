# 查 GCUService.exe / GCUBridge.exe 的父进程，弄清「谁在拉起后端」。
import ctypes
import sys
from ctypes import wintypes

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _gcu_state

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
TH32CS_SNAPPROCESS = 0x00000002


def parent_map():
    """返回 {pid: (name, parentPid)}"""
    out = {}
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.c_void_p(-1).value:
        return out
    try:
        pe = _gcu_state.PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out[pe.th32ProcessID] = (pe.szExeFile, pe.th32ParentProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return out


pm = parent_map()
for pid, (name, ppid) in sorted(pm.items(), key=lambda x: x[1][0].lower()):
    low = name.lower()
    if any(k in low for k in ("gcu", "systray", "ccu", "controlcenter", "ais", "thrm")):
        pname = pm.get(ppid, ("<已退出>", 0))[0]
        print("%-26s pid=%-7d 父=%s(%d)" % (name, pid, pname, ppid))
