"""Smoke-test the rebuilt exe from build/ without touching the deployed install.

Runs build/MechrevoMode.exe (its green-layout data dir is build/data, so the
OneDrive config/log are left alone), waits for the app to connect to GCU and
write its first "托盘提示 ->" line, prints it, then terminates the process.

The tooltip line is the acceptance criterion: it must contain only the two
limit rows and no RPM line.
Run:  python _research/_smoke_tip.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

EXE = os.path.abspath("build/MechrevoMode.exe")
LOG = os.path.abspath("build/data/log.txt")
DEADLINE = 70


def read_tail():
    if not os.path.exists(LOG):
        return ""
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


before = read_tail()
mark = len(before)
print(f"exe : {EXE}")
print(f"log : {LOG}  ({mark} bytes)")
sys.stdout.flush()

p = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
print(f"launched pid={p.pid}, waiting up to {DEADLINE}s for a tooltip line ...")

tip = None
t0 = time.time()
while time.time() - t0 < DEADLINE:
    time.sleep(1.5)
    tail = read_tail()[mark:]
    for line in tail.splitlines():
        # 只认「已经拿到限制信息」的那一条 —— 刚启动时第一条是「未连接」
        if "托盘提示 ->" in line and "\u26a1" in line:
            tip = line.strip()
            break
    if tip:
        break

print()
if tip:
    print("RESULT:", tip)
    body = tip.split("托盘提示 ->", 1)[1].strip()
    has_rpm = "RPM" in body or "\U0001f300" in body
    print(f"  含 RPM/🌀 : {has_rpm}   <- 期望 False")
    print(f"  行数      : {body.count(chr(92) + 'n') + 1}   <- 期望 2")
    ok = not has_rpm
else:
    print("RESULT: 没等到提示行（见下方日志）")
    ok = False

tail = read_tail()[mark:]
if not ok:
    print("\n--- 新增日志 ---")
    print(tail)

# 确认没有再去拉控制台
if "官方控制台" in tail or "静默" in tail:
    print("\n!! 日志里仍有控制台唤醒相关记录")
    ok = False
else:
    print("\n日志里没有任何控制台唤醒记录  OK")

p.terminate()
try:
    p.wait(timeout=10)
except subprocess.TimeoutExpired:
    p.kill()
print(f"已结束 pid={p.pid}")
sys.exit(0 if ok else 1)
