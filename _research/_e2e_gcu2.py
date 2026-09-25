# 端到端验证「第二分支」：broker 在、但发布者不在。
#
# 第一个测试打回的是「服务也挂了」，发现服务一起来就有别的组件顺手把
# GCUService 也拉起来了，于是没走到本程序自己拉起发布者那一段。
# 这里只杀 GCUService（broker 保持运行），逼程序走：
#   周期检查发现「连得上但收不到状态」-> ensureBackend -> launchHidden -> 恢复
#
# 需要提权运行。
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
        print("!! MechrevoMode 没在跑，先跑一遍 _e2e_gcu.py")
        return 1
    if not _gcu_state.port_open():
        print("!! 13688 没在监听，前提不成立")
        return 1

    print("=== 步骤 0：基线 ===")
    ok, txt = _pub_test.check(index=6, wait=6)
    print("  握手:", "有状态" if ok else "无状态")
    print("  GCUService.exe pids:", _pub_test.pids_of("GCUService.exe"))

    print("\n=== 步骤 1：只杀掉 GCUService.exe（broker 不动）===")
    off = log_size()
    for pid in _pub_test.pids_of("GCUService.exe"):
        _pub_test.kill(pid, "GCUService")
    time.sleep(3)
    print("  GCUService.exe pids:", _pub_test.pids_of("GCUService.exe"))
    print("  端口 13688 监听中:", _gcu_state.port_open(), "(应仍为 True)")

    print("\n=== 步骤 2：跟踪日志（最多 200 秒）===")
    t0 = time.time()
    recovered = False
    shown = 0
    launched_by_us = False
    while time.time() - t0 < 200:
        time.sleep(3)
        text = log_since(off)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > shown:
            for ln in lines[shown:]:
                print("  [%4.0fs] %s" % (time.time() - t0, ln))
            shown = len(lines)
        if "已拉起 GCUService.exe" in text:
            launched_by_us = True
        if "状态更新" in text:
            recovered = True
            break

    print("\n=== 步骤 3：最终状态 ===")
    print("  GCUService.exe pids:", _pub_test.pids_of("GCUService.exe"))
    print("  端口 13688 监听中:", _gcu_state.port_open())
    print("  握手:", "有状态" if _pub_test.check(index=6, wait=8)[0] else "无状态")
    print()
    print("  程序自己拉起了发布者:", launched_by_us)
    if recovered:
        print("=> PASS：broker 在、发布者不在时也能自愈")
        return 0
    print("=> FAIL：200 秒内没走完恢复流程")
    return 1


if __name__ == "__main__":
    sys.exit(main())
