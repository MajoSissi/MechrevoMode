# 最终端到端验证：跑在「用户实际安装的那份 exe」上，由计划任务托管。
#
# 之所以不直接 ShellExecute 启动被测程序：那样起来的进程挂在本脚本的进程树里，
# 脚本一结束就被一起回收了（上一轮的 MechrevoMode 就是这么消失的，
# 事件日志里查不到任何崩溃记录，因为根本不是崩溃）。
# 走 schtasks /Run 由任务计划服务托管，进程与我们无关，能一直活着。
#
# 两个场景一次跑完：
#   A 服务+发布者都没了（等价于刚开机） -> 程序应自己把两者拉起来
#   B 只有发布者没了（broker 还在）      -> 程序应发现「连得上却收不到状态」并拉起它
#
# 需要提权运行。
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gcu_state
import _pub_test

SRC = r"D:\User\Desktop\MechrevoMode\MechrevoMode.exe"
INSTALLED = r"D:\User\OneDrive\Programm\MechrevoMode\MechrevoMode.exe"
TASK = "MechrevoMode"
LOG = os.path.join(os.environ["APPDATA"], "MechrevoMode", "log.txt")


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def log_since(off):
    with open(LOG, "rb") as f:
        f.seek(off)
        raw = f.read()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def watch(off, seconds, until, show_from=0):
    """打印新增日志，直到出现 until 中的任意关键字或超时。返回 (命中的关键字, 新偏移, 全文)"""
    t0 = time.time()
    shown = show_from
    text = ""
    while time.time() - t0 < seconds:
        time.sleep(3)
        # log_since 每次都是从 off 读起的累计内容，所以直接覆盖即可
        text = log_since(off)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > shown:
            for ln in lines[shown:]:
                print("  [%4.0fs] %s" % (time.time() - t0, ln))
            shown = len(lines)
        for kw in until:
            if kw in text:
                return kw, log_size(), text
    return None, log_size(), text


def main():
    print("=== 步骤 0：把新构建同步到用户实际路径 ===")
    if not os.path.isfile(SRC):
        print("!! 找不到", SRC)
        return 1
    if os.path.exists(INSTALLED):
        for pid in _pub_test.pids_of("MechrevoMode.exe"):
            _pub_test.kill(pid, "MechrevoMode")
        time.sleep(2)
        shutil.copy2(SRC, INSTALLED)
        print("  已覆盖:", INSTALLED)
    else:
        print("!! 目标不存在:", INSTALLED)
        return 1
    print("  大小:", os.path.getsize(INSTALLED), "字节")

    print("\n=== 步骤 1：场景 A —— 打回刚开机（服务+发布者都没了）===")
    ok, msg = _pub_test.enable_debug_privilege()
    print("  SeDebugPrivilege:", msg)
    for pid in _pub_test.pids_of("GCUBridge.exe"):
        _pub_test.kill(pid, "GCUBridge")
    for pid in _pub_test.pids_of("GCUService.exe"):
        _pub_test.kill(pid, "GCUService")
    time.sleep(4)
    print("  端口 13688:", _gcu_state.port_open(), " 服务状态:",
          _gcu_state.svc_state("GCUBridge"))

    off = log_size()
    print("\n  用计划任务拉起（目标是安装路径，由任务计划服务托管）")
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK],
                       capture_output=True, text=True, errors="replace")
    print("  schtasks /Run -> rc=%d %s" % (r.returncode, r.stdout.strip()))

    print("\n=== 步骤 2：跟踪日志（最多 160 秒）===")
    kw, off, _ = watch(off, 160, ["状态更新", "!! PANIC"])
    if kw is None:
        print("\n=> FAIL（场景 A）：没等到状态更新")
        return 1
    print("\n  命中: %s" % kw)
    print("  端口 13688:", _gcu_state.port_open(),
          " GCUService:", _pub_test.pids_of("GCUService.exe"),
          " 服务状态:", _gcu_state.svc_state("GCUBridge"))
    if kw == "!! PANIC":
        print("\n=> FAIL（场景 A）：出现 PANIC")
        return 1
    print("  => 场景 A PASS")

    print("\n=== 步骤 3：场景 B —— 只杀发布者（broker 保持运行）===")
    for pid in _pub_test.pids_of("GCUService.exe"):
        _pub_test.kill(pid, "GCUService")
    time.sleep(3)
    print("  GCUService pids:", _pub_test.pids_of("GCUService.exe"),
          " 端口 13688:", _gcu_state.port_open())
    off = log_size()  # 只看这一步之后的日志

    print("\n=== 步骤 4：跟踪日志（最多 220 秒）===")
    kw, _, text = watch(off, 220, ["状态更新", "!! PANIC"])
    launched_by_us = "已拉起 GCUService.exe" in text
    print("\n  程序自己拉起了发布者: %s" % launched_by_us)
    print("  GCUService pids:", _pub_test.pids_of("GCUService.exe"))
    print("  端口 13688:", _gcu_state.port_open())
    if kw == "状态更新" and launched_by_us:
        print("\n=> 场景 B PASS")
    else:
        print("\n=> 场景 B FAIL（命中=%s）" % kw)

    print("\n=== 最终状态 ===")
    print("  MechrevoMode pids:", _pub_test.pids_of("MechrevoMode.exe"))
    print("  GCUBridge pids:", _pub_test.pids_of("GCUBridge.exe"))
    print("  GCUService pids:", _pub_test.pids_of("GCUService.exe"))
    print("  GCUBridge 服务状态:", _gcu_state.svc_state("GCUBridge"))
    ok, txt = _pub_test.check(index=6, wait=8)
    print("  握手:", "有状态" if ok else "无状态")
    return 0 if (kw == "状态更新" and launched_by_us) else 1


if __name__ == "__main__":
    sys.exit(main())
