# 端到端验证「开机自启动」开关：通过真实界面勾选/取消并保存，检查
#   1) 计划任务与注册表 Run 项是否按预期互斥联动
#   2) config.json 的 auto_start 是否正确
#   3) 界面状态栏是否给出机制提示
#
# 因为被测程序是提权的（UIPI 会丢弃来自普通权限进程的窗口消息），
# 本脚本必须由 _elev.py 提权运行。
import ctypes
import json
import os
import subprocess
import sys
import time
import winreg
from ctypes import wintypes

user32 = ctypes.windll.user32

BM_SETCHECK = 0x00F1
BST_UNCHECKED = 0
BST_CHECKED = 1
BM_GETCHECK = 0x00F0
BM_CLICK = 0x00F5
WM_GETTEXT = 0x000D
SMTO_ABORTIFHUNG = 0x0002

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM

UID_AUTOSTART, UID_SAVE, UID_STATUS = 100, 900, 910
CFG = os.path.expandvars(r"%APPDATA%\MechrevoMode\config.json")
TASK = "MechrevoMode"
RUNKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def p(*a):
    print(*a, flush=True)


def find(cls, title=None):
    return user32.FindWindowW(cls, title)


def wtext(h):
    buf = ctypes.create_unicode_buffer(1024)
    user32.SendMessageTimeoutW(h, WM_GETTEXT, 1024,
                              ctypes.cast(buf, ctypes.c_void_p).value,
                              SMTO_ABORTIFHUNG, 800, None)
    return buf.value


def task_exe():
    """返回计划任务登记的程序路径；不存在返回 ''"""
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/XML"],
                       capture_output=True)
    if r.returncode != 0:
        return ""
    out = r.stdout.decode("utf-8", errors="replace")
    if "<Command>" not in out:
        out = r.stdout.decode("gbk", errors="replace")
    i = out.find("<Command>")
    if i < 0:
        return ""
    j = out.find("</Command>", i)
    return out[i + 9:j].strip() if j > 0 else ""


def run_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUNKEY) as k:
            v, _t = winreg.QueryValueEx(k, "MechrevoMode")
            return v
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def cfg_autostart():
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f).get("auto_start")
    except Exception:
        return None


def state():
    return {"task": task_exe(), "run": run_value(),
            "cfg_auto_start": cfg_autostart()}


main = find("MechrevoModeMainWnd")
if not main:
    sys.exit("主界面没找到 —— 程序没在运行？")
p("主窗口 = %d" % main)

chk = user32.GetDlgItem(main, UID_AUTOSTART)
btn = user32.GetDlgItem(main, UID_SAVE)
lbl = user32.GetDlgItem(main, UID_STATUS)
p("勾选框=%d 保存按钮=%d 状态栏=%d" % (chk, btn, lbl))
p("当前勾选状态 = %s" % ("已勾选" if user32.SendMessageW(chk, BM_GETCHECK) else "未勾选"))

p("\n初始状态: %s" % state())

results = []


def do(label, want_checked):
    user32.SendMessageW(chk, BM_SETCHECK, BST_CHECKED if want_checked else BST_UNCHECKED, 0)
    time.sleep(0.3)
    user32.PostMessageW(btn, BM_CLICK, 0, 0)
    time.sleep(2.5)  # schtasks 走进程，给足时间
    st = state()
    status_text = wtext(lbl)
    p("\n=== %s ===" % label)
    p("  勾选状态  : %s" % ("已勾选" if user32.SendMessageW(chk, BM_GETCHECK) else "未勾选"))
    p("  计划任务  : %s" % (st["task"] or "(无)"))
    p("  注册表Run : %s" % (st["run"] or "(无)"))
    p("  auto_start: %s" % st["cfg_auto_start"])
    p("  状态栏    : %r" % status_text)

    if want_checked:
        ok = (st["task"] != "" and st["run"] == ""
              and st["cfg_auto_start"] is True)
        p("  => %s（要求：有任务、无 Run 项、auto_start=true）" % ("通过" if ok else "不通过"))
    else:
        ok = (st["task"] == "" and st["run"] == ""
              and st["cfg_auto_start"] is False)
        p("  => %s（要求：任务与 Run 项都清空、auto_start=false）" % ("通过" if ok else "不通过"))
    results.append((label, ok))
    return st


# 先关：验证「关闭」把两种机制都清掉
do("第 1 步：取消勾选并保存", False)
# 再开：验证需要提权时走计划任务、且不重复写 Run 项
do("第 2 步：重新勾选并保存", True)

p("\n" + "=" * 62)
p("总结")
p("=" * 62)
for label, ok in results:
    p("  %-28s %s" % (label, "PASS" if ok else "FAIL"))
p("  最终状态: %s" % state())
sys.exit(0 if all(ok for _l, ok in results) else 1)
