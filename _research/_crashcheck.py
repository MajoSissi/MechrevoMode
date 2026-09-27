# 查「MechrevoMode 进程为什么无声消失」：先分清是整体蓝屏/重启，还是只有本进程崩了。
#
# 判据：
# - GetTickCount64 给出系统已运行时长。若几分钟前才开机，说明发生了重启（多半是 BugCheck）。
# - WER 事件（Application Error / 1000 / 1001）能告诉我们是不是只有本进程崩了。
import ctypes
import datetime
import subprocess
import sys

EVENT_IDS = "1000,1001,41,6008,1074"


def uptime():
    ms = ctypes.windll.kernel32.GetTickCount64()
    boot = datetime.datetime.now() - datetime.timedelta(milliseconds=ms)
    return ms / 60000.0, boot


def events():
    q = ("*[System[(EventID=1000 or EventID=1001 or EventID=41 or EventID=6008 or EventID=1074)]]")
    r = subprocess.run(
        ["wevtutil", "qe", "System", "/q:" + q, "/c:10",
         "/rd:true", "/f:text", "/e:Application", "/e:System"],
        capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        # /e 参数不合法时退化成只查 System
        r = subprocess.run(["wevtutil", "qe", "System", "/c:10", "/rd:true", "/f:text"],
                           capture_output=True, text=True, errors="replace")
    return r.stdout or r.stderr


def crash_reports():
    """读 WER 目录：进程崩溃会在这里留下 .wer 文件"""
    import glob
    import os
    out = []
    for pat in (r"C:\ProgramData\Microsoft\Windows\WER\ReportArchive\*",
                r"C:\ProgramData\Microsoft\Windows\WER\ReportQueue\*"):
        for d in glob.glob(pat):
            if "MechrevoMode" in os.path.basename(d) or "RyzenAdj" in os.path.basename(d).lower():
                out.append((os.path.getmtime(d), d))
    out.sort(reverse=True)
    return out[:8]


def main():
    mins, boot = uptime()
    print("系统已运行 = %.1f 分钟（开机于 %s）" % (mins, boot.strftime("%Y-%m-%d %H:%M:%S")))
    print()
    print("=== 最近的崩溃报告（WER 目录）===")
    reps = crash_reports()
    if not reps:
        print("  没有 MechrevoMode / RyzenAdj 相关的 WER 报告")
    for t, d in reps:
        print("  %s  %s" % (datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S"), d))
    print()
    print("=== 最近 10 条系统事件 ===")
    print(events()[:4000])


if __name__ == "__main__":
    sys.exit(main())
