# 取指定 pid 的可执行文件完整路径。
#
# 用 PROCESS_QUERY_LIMITED_INFORMATION —— 这个权限被专门设计成可以跨完整性级别
# 拿路径（非管理员也能读提权进程的映像名），所以比 WMI/CIM 可靠：
# CIM 对高完整性进程会直接返回空串。
import ctypes
import sys
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = ctypes.c_void_p
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
k32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
k32.CloseHandle.argtypes = [ctypes.c_void_p]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def path_of(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "<OpenProcess 失败 err=%d>" % ctypes.get_last_error()
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return "<QueryFullProcessImageName 失败 err=%d>" % ctypes.get_last_error()
        return buf.value
    finally:
        k32.CloseHandle(h)


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print("pid=%-7s %s" % (a, path_of(int(a))))
