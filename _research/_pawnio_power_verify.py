# 验证 MSR_PKG_ENERGY_STAT 读到的是**真**功耗：
# 空闲采样若干秒 → 起 8 个满载进程 → 继续采样 → 收工。
# 如果加压期间数值明显抬升，说明这个计数器确实在跟 CPU 吃电。
import ctypes
import os
import subprocess
import sys
import time

DLL = r"C:\Program Files\PawnIO\PawnIOLib.dll"
MOD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AMDFamily17.bin")

MSR_PWR_UNIT = 0xC0010299
MSR_PKG_ENERGY = 0xC001029B
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

# 加压工人：纯 Python 死循环，够用了
BURNER = "x=0\nwhile True:\n    x+=1\n"


def main():
    h = ctypes.c_void_p()
    assert lib.pawnio_open(ctypes.byref(h)) == 0, "pawnio_open 失败（需要管理员）"
    blob = open(MOD, "rb").read()
    assert lib.pawnio_load(h, blob, len(blob)) == 0, "模块加载失败"

    def read_msr(msr):
        inp = (ULONG64 * 1)(msr)
        out = (ULONG64 * 1)(0)
        n = ctypes.c_size_t(0)
        rc = lib.pawnio_execute(h, b"ioctl_read_msr", inp, 1, out, 1, ctypes.byref(n))
        if rc != 0:
            raise OSError("ioctl_read_msr 失败 hr=%08X" % (rc & 0xFFFFFFFF))
        return out[0]

    unit = read_msr(MSR_PWR_UNIT)
    e_j = 1.0 / (1 << ((unit >> 8) & 0x1F))
    print("PWR_UNIT=0x%08X -> 1 单位 = %.6g J" % (unit, e_j))

    burners = []
    prev = read_msr(MSR_PKG_ENERGY)
    t_prev = time.perf_counter()

    try:
        for i in range(1, 15):
            now = time.perf_counter()
            cur = read_msr(MSR_PKG_ENERGY)
            dt = now - t_prev
            w = ((cur - prev) & 0xFFFFFFFF) * e_j / dt
            tag = ""
            if i == 6:
                for _ in range(8):
                    burners.append(subprocess.Popen(
                        [sys.executable, "-c", BURNER],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                tag = "  <<< 起 8 个满载进程"
            if i == 11:
                for p in burners:
                    p.kill()
                burners = []
                tag = "  <<< 收工"
            print("  [%2d] %6.2f W   (dt=%.3fs)%s" % (i, w, dt, tag))
            prev, t_prev = cur, now
            time.sleep(1.0)
    finally:
        for p in burners:
            p.kill()
        lib.pawnio_close(h)


if __name__ == "__main__":
    sys.exit(main())
