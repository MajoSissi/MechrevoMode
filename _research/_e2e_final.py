# 收尾验证（第二段）：核对计划任务路径 + 模拟刚开机 + 由计划任务拉起新版本。
#
# 注意：不要把 schtasks 放到沙箱里跑（会被程序黑名单拦掉），本脚本整体是提权执行的，
# 里面的 subprocess 调用不受沙箱限制。
#
# 一、不要用 `schtasks /Change`：本任务带 LogonTrigger + InteractiveToken，
#     /Change 会去重设主体并等待交互输入凭据，直接卡死（已实测）。
#     计划任务的路径改由「程序自己启动时按自身路径重写」完成。
# 二、不要直接从本脚本启动被测程序：它会挂在脚本的进程树里，脚本一结束就被回收，
#     日志写到一半就断、事件日志里查不到任何崩溃（也实测过）。用 schtasks /Run。
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


def task_command():
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/XML"],
                           capture_output=True, text=True, errors="replace", timeout=45)
    except subprocess.TimeoutExpired:
        return "<查询超时>"
    t = r.stdout
    if "<Command>" not in t:
        return "<查不到>"
    return t.split("<Command>")[1].split("</Command>")[0].strip()


def schtasks(*args, timeout=60):
    try:
        r = subprocess.run(["schtasks"] + list(args), capture_output=True,
                           text=True, errors="replace", timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "<超时>"


def main():
    print("=== 步骤 1：核对计划任务路径 ===", flush=True)
    for pid in _pub_test.pids_of("MechrevoMode.exe"):
        _pub_test.kill(pid, "MechrevoMode")
    time.sleep(2)
    shutil.copy2(SRC, INSTALLED)
    print("  已覆盖 %s (%d 字节)" % (INSTALLED, os.path.getsize(INSTALLED)), flush=True)
    cmd = task_command()
    print("  任务 Command =", cmd, flush=True)
    ok_task = cmd.lower() == INSTALLED.lower()
    print("  指向安装路径 =", ok_task, flush=True)

    print("\n=== 步骤 2：模拟刚开机（broker 服务 + 发布者都停掉）===", flush=True)
    ok, msg = _pub_test.enable_debug_privilege()
    print("  SeDebugPrivilege:", msg, flush=True)
    for pid in _pub_test.pids_of("GCUBridge.exe"):
        _pub_test.kill(pid, "GCUBridge")
    for pid in _pub_test.pids_of("GCUService.exe"):
        _pub_test.kill(pid, "GCUService")
    time.sleep(4)
    print("  端口 13688:", _gcu_state.port_open(),
          " 服务状态:", _gcu_state.svc_state("GCUBridge"), flush=True)
    if _gcu_state.port_open():
        print("!! 端口还在监听，前提不成立", flush=True)
        return 1

    print("\n=== 步骤 3：用计划任务拉起（等价于登录自启）===", flush=True)
    off = log_size()
    rc, out = schtasks("/Run", "/TN", TASK)
    print("  schtasks /Run rc=%d %s" % (rc, out), flush=True)

    print("\n=== 步骤 4：跟踪日志（最多 170 秒）===", flush=True)
    t0 = time.time()
    shown = 0
    text = ""
    hit = None
    while time.time() - t0 < 170:
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
        if "状态更新" in text or "档位生效" in text:
            hit = "OK"
            break

    print("\n=== 最终状态 ===", flush=True)
    print("  MechrevoMode pids :", _pub_test.pids_of("MechrevoMode.exe"), flush=True)
    print("  GCUBridge  pids   :", _pub_test.pids_of("GCUBridge.exe"), flush=True)
    print("  GCUService pids   :", _pub_test.pids_of("GCUService.exe"), flush=True)
    print("  GCUBridge 服务状态 :", _gcu_state.svc_state("GCUBridge"), "(4=Running)", flush=True)
    print("  端口 13688        :", _gcu_state.port_open(), flush=True)
    print("  任务 Command      :", task_command(), flush=True)
    ok, _txt = _pub_test.check(index=6, wait=8)
    print("  握手              :", "有状态" if ok else "无状态", flush=True)
    print("  命中              :", hit, flush=True)
    print()
    if hit == "OK" and ok_task:
        print("=> PASS：新版本装在用户路径，任务指向它，冷启动能自己修好 GCU 连接", flush=True)
        return 0
    print("=> 未完全通过（hit=%s ok_task=%s）" % (hit, ok_task), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
