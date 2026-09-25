# 把新构建装到用户路径并重启（不跑完整 e2e，只做部署 + 冒烟）。
#
# 三个要点（都是踩过的坑）：
# 1. 必须先结束旧实例，否则目标 exe 被占用，根本覆盖不了。
# 2. 不能从本脚本直接启动被测程序 —— 它会挂在本脚本的进程树里，脚本一结束就被回收。
#    用 schtasks /Run 让任务计划服务托管。
# 3. schtasks /Change 不要用（本任务带 LogonTrigger + InteractiveToken，会卡住等凭据）。
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pub_test

SRC = r"D:\User\Desktop\MechrevoMode\MechrevoMode.exe"
INSTALLED = r"D:\User\OneDrive\Programm\MechrevoMode\MechrevoMode.exe"
TASK = "MechrevoMode"
LOG = os.path.join(os.environ["APPDATA"], "MechrevoMode", "log.txt")


def main():
    for pid in _pub_test.pids_of("MechrevoMode.exe"):
        print("  结束旧实例 pid=%d -> %s" % (pid, _pub_test.kill(pid, "MechrevoMode")), flush=True)
    time.sleep(2)
    shutil.copy2(SRC, INSTALLED)
    print("  已部署 %s (%d 字节)" % (INSTALLED, os.path.getsize(INSTALLED)), flush=True)

    off = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True,
                       text=True, errors="replace", timeout=60)
    print("  schtasks /Run rc=%d %s" % (r.returncode, (r.stdout + r.stderr).strip()), flush=True)

    t0 = time.time()
    while time.time() - t0 < 90:
        time.sleep(3)
        with open(LOG, "rb") as f:
            f.seek(off)
            txt = f.read().decode("utf-8", "replace")
        if "启动完成" in txt:
            for ln in txt.splitlines():
                if ln.strip():
                    print("  " + ln, flush=True)
            print("  GCU 相关进程:", _pub_test.pids_of("GCUBridge.exe"),
                  _pub_test.pids_of("GCUService.exe"), flush=True)
            return 0
        if "!! PANIC" in txt:
            print("  !! 启动后 panic", flush=True)
            for ln in txt.splitlines():
                if ln.strip():
                    print("  " + ln, flush=True)
            return 1
    print("  !! 90 秒内没等到「启动完成」", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
