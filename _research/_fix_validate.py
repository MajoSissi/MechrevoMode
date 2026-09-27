# -*- coding: utf-8 -*-
"""用**产品本身**当判据做最终验证。

为什么换判据：之前用 MQTT 客户端当探针，结果两个客户端对同一时间段给出
互相矛盾的读数（一个 0 条、一个 23 条）—— 典型的 client ID 撞车被踢。
探针本身不可信，结论就不能要。

MechrevoMode.exe 的日志是最好的判据：它自带序号探测与重连，出的是
    ✅  CPU 🌀2753RPM      ← 拿到真值
    ❌  CPU 🌀--RPM        ← 没拿到遥测
一句话就能判定，而且不受我方探针干扰。

流程：
  1. 确保 MechrevoMode 在跑
  2. 结束 GCUService → 回到未武装 → 确认日志是 --
  3. 只启动 SystrayComponent.exe（OEM 托盘组件，无窗口）→ 看是否武装
  4. 若不行，启动官方控制台 UI → 看是否武装
"""

import ctypes
import os
import re
import subprocess
import sys
import time

LOG = r"D:\User\OneDrive\Programm\MechrevoMode\data\log.txt"
SYSTRAY = (r"C:\Program Files\WindowsApps"
           r"\CCU.WinUI_5.56.60.34_x64__wrbgcf7aesyd8\Win32\SystrayComponent.exe")
AUMID = r"shell:AppsFolder\CCU.WinUI_wrbgcf7aesyd8!App"

ntdll = ctypes.WinDLL("ntdll")
T0 = time.time()


def el():
    return time.time() - T0


def say(m):
    print("[%7.2fs] %s" % (el(), m), flush=True)


def alive():
    size = 1 << 22
    while True:
        buf = ctypes.create_string_buffer(size)
        ret = ctypes.c_ulong(0)
        st = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(ret))
        if st == 0xC0000004:
            size *= 2
            continue
        if st != 0:
            raise OSError(hex(st))
        break
    blob = buf.raw
    off = 0
    out = set()
    while True:
        nxt = struct_unpack_u32(blob, off)
        ln = int.from_bytes(blob[off + 0x38:off + 0x3A], "little")
        bp = int.from_bytes(blob[off + 0x40:off + 0x48], "little")
        if ln and bp:
            out.add(ctypes.string_at(bp, ln).decode("utf-16-le", "replace"))
        if nxt == 0:
            break
        off += nxt
    return out


def struct_unpack_u32(blob, off):
    return int.from_bytes(blob[off:off + 4], "little")


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def tail_new(mark):
    """读 mark 字节之后的新日志行。"""
    try:
        with open(LOG, "rb") as f:
            f.seek(mark)
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


RPM_RE = re.compile(r"🌀(\d+)RPM")


def wait_rpm(mark, seconds, label):
    """在新日志里等真实转速出现。"""
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(2.0)
        for l in tail_new(mark):
            if "托盘提示 ->" in l and RPM_RE.search(l):
                say("  >>> %s：拿到真实转速！" % label)
                say("      %s" % l.strip()[:180])
                return True
    lines = [l for l in tail_new(mark) if "托盘提示 ->" in l]
    last = lines[-1].strip()[:180] if lines else "（没有新的托盘提示行）"
    say("  >>> %s：%v 秒内没有真实转速" % (label, seconds))
    say("      最后一条: %s" % last)
    return False


def kill_many(names):
    fn = "_cmd_kill_many.txt"
    with open(fn, "w", encoding="utf-8") as f:
        for n in names:
            f.write("taskkill /F /IM %s\n" % n)
        f.write("exit /b 0\n")
    subprocess.run([sys.executable, "_elev.py", "@" + fn, "40"],
                   capture_output=True, text=True, timeout=120)


def elev_run(cmdline, timeout=40):
    fn = "_cmd_elev_tmp.txt"
    with open(fn, "w", encoding="utf-8") as f:
        f.write(cmdline + "\n")
    r = subprocess.run([sys.executable, "_elev.py", "@" + fn, str(timeout)],
                       capture_output=True, text=True, timeout=timeout + 60)
    return r.stdout


def main():
    names = alive()
    if "MechrevoMode.exe" not in names:
        say("MechrevoMode 没在跑，先用计划任务拉起来")
        say("  " + " ".join(
            l for l in elev_run("schtasks /Run /TN MechrevoMode", 45)
            .splitlines() if "SUCCESS" in l or "错误" in l).strip()[:120])
        time.sleep(12)
    say("MechrevoMode 在跑: %s" % ("是" if "MechrevoMode.exe" in alive() else "否"))

    # ---------- 回到未武装
    say("=== 结束 GCUService + OEM UI 组件，回到未武装 ===")
    kill_many(["CCUWinUI.exe", "SystrayComponent.exe", "OSDTpDetect.exe",
               "GCUService.exe"])
    mark = log_size()
    t_kill = el()
    while el() - t_kill < 110:
        time.sleep(2.5)
        if "GCUService.exe" in alive() and el() - t_kill > 45:
            break
    say("  相关进程: %s" % sorted(
        n for n in alive()
        if any(k in n.lower() for k in ("gcu", "ccu", "systray", "osd", "mechrevo"))))
    say("  未武装判定（20 秒）：")
    wait_rpm(mark, 20, "冷状态基线")

    # ---------- 只启动 SystrayComponent
    say("=== 只启动 SystrayComponent.exe（OEM 托盘组件，无窗口）===")
    say("  路径: %s" % SYSTRAY)
    say("  文件存在: %s" % os.path.exists(SYSTRAY))
    mark = log_size()
    try:
        subprocess.Popen([SYSTRAY])
        say("  已发起")
    except OSError as e:
        say("  直接启动失败: %r" % (e,))
        say("  改用提权启动")
        elev_run('start "" "%s"' % SYSTRAY, 30)
    time.sleep(6)
    say("  SystrayComponent 在跑: %s"
        % ("是" if "SystrayComponent.exe" in alive() else "否"))
    ok_sys = wait_rpm(mark, 40, "SystrayComponent")

    # ---------- 不行就启动官方控制台
    if not ok_sys:
        say("=== 启动官方控制台 UI ===")
        mark = log_size()
        subprocess.Popen(["explorer.exe", AUMID])
        say("  已发起: explorer.exe " + AUMID)
        ok_ui = wait_rpm(mark, 45, "控制台 UI")
    else:
        ok_ui = True

    say("=== 结论 ===")
    if ok_sys:
        say("  只启动 SystrayComponent.exe 就能武装 → 修复可以不弹窗")
    elif ok_ui:
        say("  必须启动控制台 UI 才能武装 → 修复需要拉起官方控制台（会闪一个窗口）")
    else:
        say("  两种都没能武装 —— 需要人工介入确认")


if __name__ == "__main__":
    main()
