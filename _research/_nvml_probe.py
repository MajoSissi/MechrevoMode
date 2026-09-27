# 探测本机 NVML 能提供什么：GPU 温度 / 功耗 / 风扇转速（百分比与 RPM）。
# NVML 随 NVIDIA 驱动安装（本机 C:/Windows/System32/nvml.dll），无需管理员权限。
import ctypes
from ctypes import wintypes

nvml = ctypes.WinDLL("nvml.dll")

# 先看有哪些符号存在 —— 不同驱动版本导出的函数集不一样，
# 不能假定 RPM 接口一定在（存在才用，不存在就退回百分比）。
CAND = [
    "nvmlInit_v2", "nvmlShutdown",
    "nvmlDeviceGetCount_v2", "nvmlDeviceGetHandleByIndex_v2",
    "nvmlDeviceGetName", "nvmlDeviceGetTemperature", "nvmlDeviceGetPowerUsage",
    "nvmlDeviceGetPowerManagementLimit", "nvmlDeviceGetFanSpeed",
    "nvmlDeviceGetFanSpeed_v2", "nvmlDeviceGetNumFans", "nvmlDeviceGetFanSpeedRPM",
    "nvmlDeviceGetUtilizationRates", "nvmlDeviceGetClockInfo",
    "nvmlDeviceGetMemoryInfo", "nvmlDeviceGetTemperatureThreshold",
]
print("=== 符号存在性 ===")
for n in CAND:
    try:
        getattr(nvml, n)
        print("  OK   %s" % n)
    except AttributeError:
        print("  --   %s（不存在）" % n)

print()
try:
    r = nvml.nvmlInit_v2()
    print("nvmlInit_v2 rc=%d" % r)
except Exception as e:
    print("init 失败:", e)
    raise SystemExit(1)

n = ctypes.c_uint()
nvml.nvmlDeviceGetCount_v2(ctypes.byref(n))
print("设备数 = %d" % n.value)

for i in range(n.value):
    h = ctypes.c_void_p()
    nvml.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h))
    buf = ctypes.create_string_buffer(96)
    nvml.nvmlDeviceGetName(h, buf, 96)
    print("\n--- 设备 %d: %s" % (i, buf.value.decode()))

    t = ctypes.c_uint()
    if nvml.nvmlDeviceGetTemperature(h, 0, ctypes.byref(t)) == 0:
        print("    温度 = %d °C" % t.value)

    p = ctypes.c_uint()
    rp = nvml.nvmlDeviceGetPowerUsage(h, ctypes.byref(p))
    print("    nvmlDeviceGetPowerUsage rc=%d 值=%d mW (%.1f W)" % (rp, p.value, p.value / 1000.0))

    lim = ctypes.c_uint()
    if nvml.nvmlDeviceGetPowerManagementLimit(h, ctypes.byref(lim)) == 0:
        print("    功耗上限 = %.1f W" % (lim.value / 1000.0))

    fs = ctypes.c_uint()
    rfs = nvml.nvmlDeviceGetFanSpeed(h, ctypes.byref(fs))
    print("    nvmlDeviceGetFanSpeed rc=%d 值=%d %%（占最大转速）" % (rfs, fs.value))

    nf = ctypes.c_uint()
    rnf = nvml.nvmlDeviceGetNumFans(h, ctypes.byref(nf))
    print("    nvmlDeviceGetNumFans rc=%d 值=%d" % (rnf, nf.value))
    if rnf == 0:
        for j in range(nf.value):
            v = ctypes.c_uint()
            rc = nvml.nvmlDeviceGetFanSpeed_v2(h, j, ctypes.byref(v))
            print("      fan[%d] v2 rc=%d = %d %%" % (j, rc, v.value))

    # RPM 接口：有就试，没有就明确记下来
    try:
        nvml.nvmlDeviceGetFanSpeedRPM
        rpm = ctypes.c_uint()
        rr = nvml.nvmlDeviceGetFanSpeedRPM(h, 0, ctypes.byref(rpm))
        print("    nvmlDeviceGetFanSpeedRPM rc=%d = %d RPM" % (rr, rpm.value))
    except AttributeError:
        print("    （无 RPM 接口，只有百分比）")

u = ctypes.c_uint()
h0 = ctypes.c_void_p()
nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h0))

class Util(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]
ut = Util()
if nvml.nvmlDeviceGetUtilizationRates(h0, ctypes.byref(ut)) == 0:
    print("\nGPU 利用率 = %d%%, 显存利用率 = %d%%" % (ut.gpu, ut.memory))

nvml.nvmlShutdown()
