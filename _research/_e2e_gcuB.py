# 场景 B 单独验证：broker 在、发布者不在。
#
# 逼程序走「连得上却收不到状态」-> ensureBackend -> launchHidden 这条分支
# （场景 A 里 GCUService 是 GCUBridge 服务自己拉起来的，没走到我们这段代码）。
#
# 用法（提权）：python -u _e2e_gcuB.py
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gcu_state
import _pub_test

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


def main():
    if not _pub_test.pids_of("MechrevoMode.exe"):
        print("!! MechrevoMode 没在跑")
        return 1
    if not _gcu_state.port_open():
        print("!! 13688 没在监听，前提不成立")
        return 1

    ok, msg = _pub_test.enable_debug_privilege()
    print("SeDebugPrivilege:", msg, flush=True)

    print("\n=== 步骤 0：基线 ===", flush=True)
    ok, txt = _pub_test.check(index=6, wait=6)
    print("  握手:", "有状态" if ok else "无状态", flush=True)
    print("  GCUService pids:", _pub_test.pids_of("GCUService.exe"), flush=True)

    print("\n=== 步骤 1：只杀 GCUService（broker 保持运行）===", flush=True)
    off = log_size()
    for pid in _pub_test.pids_of("GCUService.exe"):
        _pub_test.kill(pid, "GCUService")
    time.sleep(3)
    print("  GCUService pids:", _pub_test.pids_of("GCUService.exe"), flush=True)
    print("  端口 13688:", _gcu_state.port_open(), "(应仍为 True)", flush=True)

    print("\n=== 步骤 2：跟踪日志（最多 210 秒）===", flush=True)
    t0 = time.time()
    shown = 0
    text = ""
    hit = None
    while time.time() - t0 < 210:
        time.sleep(3)
        text = log_since(off)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > shown:
            for ln in lines[shown:]:
                print("  [%4.0fs] %s" % (time.time() - t0, ln), flush=True)
            shown = len(lines)
        if "!! PANIC" in text:
            hit = "PANIC"
            break
        if "已拉起 GCUService.exe" in text and "状态更新" in text.split("已拉起")[-1]:
            hit = "OK"
            break
        if "已拉起 GCUService.exe" in text:
            hit = "LAUNCHED"
    if hit == "LAUNCHED":
        # 已经看到我们拉起它了，再给点时间等状态
        for _ in range(40):
            time.sleep(3)
            text = log_since(off)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if len(lines) > shown:
                for ln in lines[shown:]:
                    print("  [%4.0fs] %s" % (time.time() - t0, ln), flush=True)
                shown = len(lines)
            if "状态更新" in text.split("已拉起")[-1] or "!! PANIC" in text:
                hit = "OK" if "状态更新" in text.split("已拉起")[-1] else "PANIC"
                break

    print("\n=== 结果 ===", flush=True)
    launched_by_us = "已拉起 GCUService.exe" in text
    print("  程序自己拉起了发布者:", launched_by_us, flush=True)
    print("  GCUService pids:", _pub_test.pids_of("GCUService.exe"), flush=True)
    print("  端口 13688:", _gcu_state.port_open(), flush=True)
    print("  握手:", "有状态" if _pub_test.check(index=6, wait=8)[0] else "无状态", flush=True)
    print("  命中:", hit, flush=True)
    if launched_by_us and hit == "OK":
        print("\n=> 场景 B PASS", flush=True)
        return 0
    print("\n=> 场景 B FAIL", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
