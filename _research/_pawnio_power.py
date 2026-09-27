# 用 PawnIO 的 AMDFamily17 模块读 AMD RAPL 能量计数器，验证「能不能拿到 CPU 包功耗」。
#
# 思路：MSR_PKG_ENERGY_STAT(0xC001029B) 是一个 32 位能量累加器，单位由
# MSR_PWR_UNIT(0xC0010299) 的 ESU 位域给出（1 单位 = 2^-ESU 焦耳）。
# 两次采样求差 / 时间差 = 平均功耗。这是 Linux amd_energy 驱动用的同一套机制，
# 不依赖 SMU PM 表，也不需要匹配 PM 表版本偏移。
import ctypes
import os
import sys
import time

DLL = r"C:\Program Files\PawnIO\PawnIOLib.dll"
MOD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AMDFamily17.bin")

MSR_PWR_UNIT = 0xC0010299
MSR_CORE_ENERGY = 0xC001029A
MSR_PKG_ENERGY = 0xC001029B
MSR_MPERF = 0x000000E7

ULONG64 = ctypes.c_uint64

lib = ctypes.WinDLL(DLL)
lib.pawnio_version.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
lib.pawnio_version.restype = ctypes.c_long
lib.pawnio_open.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
lib.pawnio_open.restype = ctypes.c_long
lib.pawnio_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
lib.pawnio_load.restype = ctypes.c_long
lib.pawnio_execute.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p,
    ctypes.POINTER(ULONG64), ctypes.c_size_t,
    ctypes.POINTER(ULONG64), ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
lib.pawnio_execute.restype = ctypes.c_long
lib.pawnio_close.argtypes = [ctypes.c_void_p]
lib.pawnio_close.restype = ctypes.c_long


def hr(x):
    return "%08X" % (x & 0xFFFFFFFF)


def main():
    print("提权 =", ctypes.windll.shell32.IsUserAnAdmin() != 0)

    v = ctypes.c_ulong()
    r = lib.pawnio_version(ctypes.byref(v))
    print("pawnio_version hr=%s  version=%d.%d.%d" % (hr(r), v.value >> 16, (v.value >> 8) & 0xFF, v.value & 0xFF))

    h = ctypes.c_void_p()
    r = lib.pawnio_open(ctypes.byref(h))
    print("pawnio_open    hr=%s  handle=%s" % (hr(r), h.value))
    if r != 0:
        return

    blob = open(MOD, "rb").read()
    r = lib.pawnio_load(h, blob, len(blob))
    print("pawnio_load    hr=%s  (AMDFamily17.bin %d 字节)" % (hr(r), len(blob)))
    if r != 0:
        lib.pawnio_close(h)
        return

    def read_msr(msr):
        inp = (ULONG64 * 1)(msr)
        out = (ULONG64 * 1)(0)
        n = ctypes.c_size_t(0)
        rc = lib.pawnio_execute(h, b"ioctl_read_msr", inp, 1, out, 1, ctypes.byref(n))
        return rc, out[0], n.value

    for name, msr in [("MSR_PWR_UNIT", MSR_PWR_UNIT),
                      ("MSR_CORE_ENERGY", MSR_CORE_ENERGY),
                      ("MSR_PKG_ENERGY", MSR_PKG_ENERGY),
                      ("MSR_MPERF", MSR_MPERF)]:
        rc, val, n = read_msr(msr)
        print("  %-16s 0x%08X -> hr=%s value=0x%016X (%d) out=%d"
              % (name, msr, hr(rc), val, val, n))

    rc, unit, _ = read_msr(MSR_PWR_UNIT)
    if rc != 0:
        print("读 MSR_PWR_UNIT 失败，无法换算单位")
        lib.pawnio_close(h)
        return

    esu = (unit >> 8) & 0x1F
    psu = unit & 0x0F
    tsu = (unit >> 16) & 0x0F
    e_j = 1.0 / (1 << esu)
    p_w = 1.0 / (1 << psu)
    print("PWR_UNIT=0x%08X  ESU=%d(1单位=%.3gJ)  PSU=%d(1单位=%.3gW)  TSU=%d"
          % (unit, esu, e_j, psu, p_w, tsu))

    print("\n---- 采样 6 次，间隔 1 秒 ----")
    prev_pkg = prev_core = None
    t_prev = None
    for i in range(6):
        rc1, pkg, _ = read_msr(MSR_PKG_ENERGY)
        rc2, core, _ = read_msr(MSR_CORE_ENERGY)
        now = time.perf_counter()
        if prev_pkg is not None:
            dt = now - t_prev
            dp = (pkg - prev_pkg) & 0xFFFFFFFF  # 32 位回绕
            dc = (core - prev_core) & 0xFFFFFFFF
            print("  [%d] PKG=%10d  Δ=%8d -> %6.2f W   |  CORE Δ=%8d -> %6.2f W   (dt=%.3fs)"
                  % (i, pkg, dp, dp * e_j / dt, dc, dc * e_j / dt, dt))
        else:
            print("  [%d] PKG=%10d CORE=%10d (基线)" % (i, pkg, core))
        prev_pkg, prev_core, t_prev = pkg, core, now
        if i != 5:
            time.sleep(1.0)

    lib.pawnio_close(h)
    print("完成")


if __name__ == "__main__":
    sys.exit(main())
