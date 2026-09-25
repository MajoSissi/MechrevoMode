# 端到端验证「GCU 后端自愈」。
#
# 把机器打回「刚开机」的样子：
#   1. 停掉 GCUBridge 服务（broker 消失，13688 没人监听）
#   2. 杀掉 GCUService.exe（发布者消失）
# 然后启动新构建的 MechrevoMode.exe，只看它自己的日志能不能走完
# 「连不上 -> 拉起服务 -> 拉起发布者 -> 已连接 -> 状态更新」。
#
# 需要提权运行。跑完把环境留在可用状态（由被测程序自己恢复，这本身就是验证）。
import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _elev
import _gcu_state
import _pub_test

EXE = r"D:\User\Desktop\MechrevoMode\MechrevoMode.exe"
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
    print("=== 步骤 0：收起现有实例（单实例互斥体挡着，不先收掉起不来新的）===")
    for pid in _pub_test.pids_of("MechrevoMode.exe"):
        _pub_test.kill(pid, "MechrevoMode")
    time.sleep(2)

    print("\n=== 步骤 1：打回刚开机状态 ===")
    ok, msg = _pub_test.enable_debug_privilege()
    print("  启用 SeDebugPrivilege:", msg)

    # 用「直接结束服务进程」而不是 net stop：
    # 实测 net stop GCUBridge 会被它自己拒掉（它处理控制请求时抛异常，正是
    # 系统日志里那条事件 7023 的成因），而开机时它就是这么崩掉的。
    # 直接杀掉进程，才是对「开机后 broker 不在」最忠实的复现。
    print("  杀掉 GCUBridge.exe（复现开机崩溃）")
    for pid in _pub_test.pids_of("GCUBridge.exe"):
        _pub_test.kill(pid, "GCUBridge")
    for pid in _pub_test.pids_of("GCUService.exe"):
        _pub_test.kill(pid, "GCUService")
    time.sleep(4)

    print("  端口 13688 监听中:", _gcu_state.port_open())
    print("  GCUService.exe pids:", _pub_test.pids_of("GCUService.exe"))
    print("  GCUBridge.exe  pids:", _pub_test.pids_of("GCUBridge.exe"))
    print("  GCUBridge 服务状态:", _gcu_state.svc_state("GCUBridge"), "(1=Stopped 4=Running)")

    ok, txt = _pub_test.check(index=6, wait=6)
    print("  握手:", "有状态（意外）" if ok else "无状态（符合预期）")

    print("\n=== 步骤 2：启动新构建的 MechrevoMode.exe（提权）===")
    print("  " + EXE)
    off = log_size()
    rc = _pub_test.launch_elevated(EXE)
    print("  " + rc)

    print("\n=== 步骤 3：跟踪它自己的日志（最多 150 秒）===")
    t0 = time.time()
    got_status = False
    shown = 0
    while time.time() - t0 < 150:
        time.sleep(3)
        text = log_since(off)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > shown:
            for ln in lines[shown:]:
                print("  [%4.0fs] %s" % (time.time() - t0, ln))
            shown = len(lines)
        # 「状态更新」出现即代表真的拿到模式了
        if "状态更新" in text:
            got_status = True
            break

    print("\n=== 步骤 4：最终状态 ===")
    print("  端口 13688 监听中:", _gcu_state.port_open())
    print("  GCUService.exe pids:", _pub_test.pids_of("GCUService.exe"))
    print("  GCUBridge.exe  pids:", _pub_test.pids_of("GCUBridge.exe"))
    print("  GCUBridge 服务状态:", _gcu_state.svc_state("GCUBridge"), "(4=Running)")
    print("  MechrevoMode.exe pids:", _pub_test.pids_of("MechrevoMode.exe"))

    print()
    if got_status:
        print("=> PASS：程序自己把 GCU 后端拉起来了，并拿到了真实状态")
        return 0
    print("=> FAIL：150 秒内没走到「状态更新」，见上面的日志")
    return 1


if __name__ == "__main__":
    sys.exit(main())
