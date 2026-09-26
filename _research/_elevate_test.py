# 提权重启实测（自包含）：
#   1) 勾选「以管理员身份运行」-> 确认框按「是」
#   2) 观察旧进程是否退出、新进程是否起来
#   3) 从日志确认新进程 提权=true
# 全过程用 Unicode API，避免 cmd 破坏中文标题。
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

# 本文件是**脚本**：主体全写在模块级，import 它会把里面的副作用原地重跑一遍
# （曾因此把用户的「开机自启」开关无声地来回拨了两次）。
# 这行让误 import 立刻失败，而不是安静地把事情做一遍。
# 要复用里面的函数，请先把它抽到共用模块，而不是 import 这个文件。
if __name__ != "__main__":
    raise ImportError("这是脚本不是模块，请用 python 直接运行")

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

BM_CLICK = 0x00F5
BM_GETCHECK = 0x00F0
WM_COMMAND = 0x0111
IDYES = 6

from _paths import DATA as HOME  # 数据目录见 _paths.py（程序同目录\data）
LOG = os.path.join(HOME, "log.txt")
EXE = r"D:\User\Desktop\MechrevoMode\MechrevoMode.exe"


def read_log():
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def log_since(mark):
    return read_log()[mark:]


def find(cls, title=None):
    return user32.FindWindowW(cls, title)


def msgboxes():
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        b = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, b, 256)
        if b.value == "#32770":
            t = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, t, 256)
            out.append((hwnd, t.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


def alive():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MechrevoMode.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True)
    return "MechrevoMode.exe" in out.stdout


print("=== 0) 启动程序 ===")
subprocess.Popen(["cmd", "/c", "start", "", EXE, "-show"])
for _ in range(30):
    time.sleep(1)
    if find("MechrevoModeMainWnd"):
        break
main = find("MechrevoModeMainWnd")
tray = find("MechrevoModeTrayWnd")
print("main=%s tray=%s" % (main, tray))
if not main:
    sys.exit("主界面没起来")

mark = len(read_log())
print("日志基线长度 =", mark)
print(log_since(mark).strip() or "(无新日志)")

print("\n=== 1) 点击复选框 101 ===")
user32.ShowWindow(main, 5)
ctl = user32.GetDlgItem(main, 101)
print("ctl=%s check(before)=%s" % (ctl, user32.SendMessageW(ctl, BM_GETCHECK, 0, 0)))
user32.PostMessageW(ctl, BM_CLICK, 0, 0)
time.sleep(1.5)
print("check(after)=%s   对话框=%s"
      % (user32.SendMessageW(ctl, BM_GETCHECK, 0, 0), msgboxes()))

print("\n=== 2) 确认框按「是」 ===")
hit = False
for hwnd, title in msgboxes():
    if "机械革命" in title:
        print("命中 %r hwnd=%d -> 发送 IDYES" % (title, hwnd))
        user32.SendMessageW(hwnd, WM_COMMAND, IDYES, 0)
        hit = True
        break
if not hit:
    print("!! 没有找到确认对话框")

print("\n=== 3) 观察 8 秒 ===")
for i in range(8):
    time.sleep(1)
    print("  t+%ds  main=%-10s tray=%-10s 进程在=%s"
          % (i + 1, find("MechrevoModeMainWnd"), find("MechrevoModeTrayWnd"), alive()))

print("\n=== 4) 本次操作产生的日志 ===")
print(log_since(mark).strip() or "(无)")
