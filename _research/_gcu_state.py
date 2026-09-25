# 采集 GCU 相关运行时状态：端口监听、关键进程、服务启动类型。
#
# 只读，不改动任何东西。用于建立「修复前」的事实基线，
# 以及修复后对比「程序能否自己把后端拉起来」。
import ctypes
import socket
import sys
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ad = ctypes.WinDLL("advapi32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def processes():
    """返回 [(pid, name)]"""
    out = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out.append((pe.th32ProcessID, pe.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return out


def port_open(host="127.0.0.1", port=13688, timeout=1.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def service_config(name):
    """读服务的 Start 类型与 PathName（只读注册表，无需管理员）"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\%s" % name) as k:
            def g(n):
                try:
                    return winreg.QueryValueEx(k, n)[0]
                except OSError:
                    return None
            return {"Start": g("Start"), "ImagePath": g("ImagePath"),
                    "ObjectName": g("ObjectName")}
    except OSError as e:
        return {"error": str(e)}


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [("dwServiceType", wintypes.DWORD),
                ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD),
                ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD),
                ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD)]


# 不设 restype 的话句柄会被截成 32 位（ctypes 默认 c_int），
# 于是 OpenServiceW 必然失败、状态查出来永远是 None —— 这个坑很隐蔽。
ad.OpenSCManagerW.restype = ctypes.c_void_p
ad.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
ad.OpenServiceW.restype = ctypes.c_void_p
ad.OpenServiceW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD]
ad.QueryServiceStatus.restype = wintypes.BOOL
ad.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]
ad.CloseServiceHandle.argtypes = [ctypes.c_void_p]
ad.CloseServiceHandle.restype = wintypes.BOOL


def svc_state(name):
    """QueryServiceStatus：1=Stopped 2=StartPending 3=StopPending 4=Running"""
    SC_MANAGER_CONNECT = 0x0001
    SERVICE_QUERY_STATUS = 0x0004
    h = ad.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not h:
        return None
    try:
        s = ad.OpenServiceW(h, name, SERVICE_QUERY_STATUS)
        if not s:
            print("     OpenServiceW 失败 err=%d" % ctypes.get_last_error())
            return None
        try:
            st = SERVICE_STATUS()
            if ad.QueryServiceStatus(s, ctypes.byref(st)):
                return st.dwCurrentState
            print("     QueryServiceStatus 失败 err=%d" % ctypes.get_last_error())
        finally:
            ad.CloseServiceHandle(s)
    finally:
        ad.CloseServiceHandle(h)
    return None


def main():
    print("=== 端口 13688 ===")
    print("  监听中:", port_open())

    print("\n=== 关键进程 ===")
    want = ("gcu", "systray", "thrm", "mechrevo", "controlcenter", "ccu")
    for pid, name in sorted(processes(), key=lambda x: x[1].lower()):
        low = name.lower()
        if any(w in low for w in want):
            print("  %-28s pid=%d" % (name, pid))

    print("\n=== 服务 GCUBridge ===")
    print("  当前状态:", svc_state("GCUBridge"), "(4=Running)")
    cfg = service_config("GCUBridge")
    for k, v in cfg.items():
        print("  %s = %s" % (k, v))

    print("\n=== GCUService.exe 存在的路径 ===")
    import os
    cands = [
        r"D:\Tools\L-Mechrevo\GCU\AiStoneService\MyControlCenter\GCUService.exe",
        r"C:\Program Files\OEM\机械革命控制中心\AiStoneService\MyControlCenter\GCUService.exe",
    ]
    ip = cfg.get("ImagePath") or ""
    if ip:
        base = os.path.dirname(ip.strip('"'))
        cands.insert(0, os.path.join(base, "MyControlCenter", "GCUService.exe"))
    for c in cands:
        print("  [%s] %s" % ("有" if os.path.isfile(c) else "无", c))

    return 0


if __name__ == "__main__":
    sys.exit(main())
