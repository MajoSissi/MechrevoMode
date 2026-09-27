# -*- coding: utf-8 -*-
"""端到端验证修复：强制冷状态，看 MechrevoMode 是否自己唤醒遥测。

判据还是用产品自己的日志（不要拿 MQTT 客户端当探针 —— client ID 撞车会
给出自相矛盾的读数，这个坑已经踩过一次）：
  * 出现 "已启动官方控制台唤醒遥测"  → 修复逻辑触发了
  * 之后出现真实的 🌀<数字>RPM        → 遥测真的被唤醒
"""

import os
import re
import subprocess
import sys
import time

LOG = r"D:\User\OneDrive\Programm\MechrevoMode\data\log.txt"
T0 = time.time()


def el():
    return time.time() - T0


def say(m):
    print("[%7.2fs] %s" % (el(), m), flush=True)


def size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def tail(mark):
    try:
        with open(LOG, "rb") as f:
            f.seek(mark)
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


class Cursor:
    """按行号推进的日志游标。

    上一版每轮都重读 mark 之后的全部行，结果把「武装之前」那条转速行
    在武装之后又判了一次，得出「遥测已恢复」的假结论。必须只消费新增行。
    """

    def __init__(self):
        self.n = 0
        self._lines = []

    def new(self):
        lines = tail(0)
        out = lines[self.n:]
        self.n = len(lines)
        return out


RPM = re.compile(r"🌀(\d+)RPM")


def kill_many(names):
    fn = "_cmd_kill_many.txt"
    with open(fn, "w", encoding="utf-8") as f:
        for n in names:
            f.write("taskkill /F /IM %s\n" % n)
        f.write("exit /b 0\n")
    r = subprocess.run([sys.executable, "_elev.py", "@" + fn, "40"],
                       capture_output=True, text=True, timeout=120)
    for l in r.stdout.splitlines():
        if "SUCCESS" in l or "not found" in l.lower() or "没有" in l:
            say("    " + l.strip()[:120])


def main():
    cur = Cursor()
    cur.new()  # 先把已有行吃掉，之后只消费新增行
    say("=== 制造冷状态：结束 OEM UI 组件 + GCUService ===")
    kill_many(["CCUWinUI.exe", "SystrayComponent.exe", "OSDTpDetect.exe",
               "GCUService.exe"])

    armed_log = None
    rpm_at = None
    baseline_rpm_seen = False

    end = time.time() + 190
    while time.time() < end:
        time.sleep(3)
        for l in cur.new():
            ln = l.strip()
            if "唤醒遥测" in ln and armed_log is None:
                armed_log = ln
                say(">>> 修复逻辑触发：")
                say("    " + ln[:200])
            if "官方控制台" in ln and "静默" not in ln:
                say(">>> " + ln[:200])
            if "静默" in ln and "失败" in ln:
                say(">>> 唤醒失败日志：")
                say("    " + ln[:200])
            if "托盘提示 ->" in ln and RPM.search(ln):
                if armed_log is None:
                    baseline_rpm_seen = True
                elif rpm_at is None:
                    rpm_at = el()
                    say(">>> 遥测已恢复：")
                    say("    " + ln[:200])
        if rpm_at:
            break

    say("=== 结果 ===")
    say("  修复逻辑触发        : %s" % ("是" if armed_log else "否"))
    say("  遥测恢复            : %s" % ("是" if rpm_at else "否"))
    if baseline_rpm_seen and not armed_log:
        say("  注意：冷状态没成立（武装前就有真实转速），本次验证不成立")


if __name__ == "__main__":
    main()
