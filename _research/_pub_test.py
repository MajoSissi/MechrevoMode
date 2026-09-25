# 验证「broker 在、但没人发布状态」时，把 GCUService.exe 拉起来能否恢复。
#
# 这就是修复方案里第二步的等价复现：先杀掉现有 GCUService，确认拿不到状态，
# 再按候选顺序拉起一个，看多久能恢复到有状态。跑完自动把环境复原。
#
# 需要提权（GCUService 是提权进程，杀它/起它都要管理员）。
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import _elev
import _gcu_state

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = ctypes.c_void_p
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
k32.CloseHandle.argtypes = [ctypes.c_void_p]

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# ---------------------------------------------------------------- 提权辅助
#
# 结束 SYSTEM 名下的服务进程需要 SeDebugPrivilege。管理员组的令牌里默认带着它，
# 但**处于禁用状态**，必须显式启用，否则 OpenProcess 直接返回 5（拒绝访问）。
ad = ctypes.WinDLL("advapi32", use_last_error=True)


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD),
                ("Privileges", LUID_AND_ATTRIBUTES * 1)]


def enable_debug_privilege():
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    ad.OpenProcessToken.restype = wintypes.BOOL
    ad.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(ctypes.c_void_p)]
    ad.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                         ctypes.POINTER(LUID)]
    ad.AdjustTokenPrivileges.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]

    TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x0020, 0x0008
    SE_PRIVILEGE_ENABLED = 0x0002

    h = ctypes.c_void_p()
    if not ad.OpenProcessToken(k32.GetCurrentProcess(),
                               TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(h)):
        return False, "OpenProcessToken err=%d" % ctypes.get_last_error()
    try:
        luid = LUID()
        if not ad.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            return False, "LookupPrivilegeValue err=%d" % ctypes.get_last_error()
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        if not ad.AdjustTokenPrivileges(h, False, ctypes.byref(tp), 0, None, None):
            return False, "AdjustTokenPrivileges err=%d" % ctypes.get_last_error()
        if ctypes.get_last_error() != 0:
            # 这个函数成功了也可能留着 ERROR_NOT_ALL_ASSIGNED(1300)
            return False, "AdjustTokenPrivileges 未完全生效 err=%d" % ctypes.get_last_error()
        return True, "ok"
    finally:
        k32.CloseHandle(h)

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

CANDIDATES = [
    r"D:\Tools\L-Mechrevo\GCU\AiStoneService\MyControlCenter\GCUService.exe",
    r"C:\Program Files\OEM\机械革命控制中心\AiStoneService\MyControlCenter\GCUService.exe",
]


def pids_of(name):
    # 用 Toolhelp 快照而不是 tasklist：不依赖外部进程，也不会被输出编码坑。
    return [pid for pid, n in _gcu_state.processes() if n.lower() == name.lower()]


def kill(pid, who):
    h = k32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        print("   x 打不开 %s pid=%d err=%d" % (who, pid, ctypes.get_last_error()))
        return False
    try:
        ok = k32.TerminateProcess(h, 1)
        print("   %s %s pid=%d -> %s" % ("已终止" if ok else "终止失败", who, pid,
                                         "ok" if ok else "err=%d" % ctypes.get_last_error()))
        return bool(ok)
    finally:
        k32.CloseHandle(h)


def check(index=7, wait=6):
    """跑一次握手探针，返回 (是否有状态, 输出文本)"""
    r = subprocess.run([PY, os.path.join(HERE, "_gcu_check.py"), str(index)],
                       capture_output=True, text=True, errors="replace", timeout=wait + 20)
    return r.returncode == 0, r.stdout.strip()


def launch_elevated(exe):
    """不提权等待地启动一个进程（GCUService 需要管理员令牌）"""
    # 复用 _elev 的提权启动，但不等待：这里直接写一份不等版本的调用
    sei = _elev.SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(_elev.SHELLEXECUTEINFOW)
    sei.fMask = 0x00000040 | 0x00000100 | 0x00008000  # NOCLOSEPROCESS|NOASYNC|NO_CONSOLE
    sei.lpVerb = "runas"
    sei.lpFile = exe
    sei.lpDirectory = os.path.dirname(exe)
    sei.nShow = 0
    ok = _elev.shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        return "启动失败 err=%d" % ctypes.get_last_error()
    if sei.hProcess:
        _elev.kernel32.CloseHandle(sei.hProcess)
    return "已启动"


def main():
    print("=== 步骤 0：当前基线 ===")
    alive = pids_of("GCUService.exe")
    print("  GCUService.exe pids =", alive)
    ok, txt = check()
    print("  基线握手 =>", "有状态" if ok else "无状态")
    print("   " + txt.replace("\n", "\n   "))

    print("\n=== 步骤 1：杀掉所有 GCUService.exe（模拟刚开机）===")
    for pid in alive:
        kill(pid, "GCUService")
    time.sleep(3)
    print("  剩余 pids =", pids_of("GCUService.exe"))
    ok, txt = check()
    print("  握手 =>", "有状态" if ok else "无状态（符合预期）")
    print("   " + txt.replace("\n", "\n   "))
    if ok:
        print("  !! 还有别的发布者，本测试的前提不成立")

    print("\n=== 步骤 2：按候选顺序拉起 GCUService.exe，观察多久恢复 ===")
    for cand in CANDIDATES:
        if not os.path.isfile(cand):
            print("  [跳过] 不存在 %s" % cand)
            continue
        print("  -> %s" % cand)
        print("     %s" % launch_elevated(cand))
        t0 = time.time()
        got = False
        while time.time() - t0 < 75:
            time.sleep(5)
            ok, txt = check(index=7, wait=8)
            el = time.time() - t0
            print("     +%4.0fs %s" % (el, "有状态 ✔" if ok else "还没状态"))
            if ok:
                got = True
                print("     " + txt.replace("\n", "\n     "))
                break
        if got:
            print("  => 该候选可用，耗时 %.0f 秒" % (time.time() - t0))
            print("\n=== 结束：发布者已重新起来，环境回到可用状态 ===")
            return 0
        print("  => 该候选 %d 秒内没出状态，换下一个" % 75)
        for pid in pids_of("GCUService.exe"):
            kill(pid, "上一步启动的 GCUService")

    print("\n!! 所有候选都没成功")
    return 1


if __name__ == "__main__":
    sys.exit(main())
