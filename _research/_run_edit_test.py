# 需求 3 端到端验证：「程序」「参数」在不勾选「程序启动后运行命令」时：
#   1) 控件可用（没被灰掉）
#   2) 改完点「保存」能真正写进 config.json
# 跑完会把 config.json 原样还原。
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

BM_CLICK = 0x00F5
BM_GETCHECK = 0x00F0
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_CLOSE = 0x0010
SMTO_ABORTIFHUNG = 0x0002

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM

EXE = r"D:\User\Desktop\MechrevoMode\MechrevoMode.exe"
CFG = os.path.expandvars(r"%APPDATA%\MechrevoMode\config.json")
BAK = CFG + ".e2e-bak"

UID_RUN_ENABLE, UID_RUN_PATH, UID_RUN_BROWSE = 110, 111, 112
UID_RUN_ARGS, UID_RUN_DELAY, UID_RUN_NOW = 113, 114, 116
UID_SAVE = 900

NEW_PATH = r"C:\Windows\System32\notepad.exe"
NEW_ARGS = "--e2e-verify"


def p(*a):
    print(*a, flush=True)


def set_text(h, s):
    # 必须让 buffer 一直被本地变量持有：ctypes.cast(...).value 取到的只是地址整数，
    # 只传地址的话临时 buffer 会立刻被回收，等于把已释放内存传给了目标进程（乱码的根源）。
    buf = ctypes.create_unicode_buffer(s)
    addr = ctypes.cast(buf, ctypes.c_void_p).value
    user32.SendMessageTimeoutW(h, WM_SETTEXT, 0, addr,
                               SMTO_ABORTIFHUNG, 800, None)


def get_text(h):
    buf = ctypes.create_unicode_buffer(2048)
    addr = ctypes.cast(buf, ctypes.c_void_p).value
    user32.SendMessageTimeoutW(h, WM_GETTEXT, 2048, addr,
                               SMTO_ABORTIFHUNG, 800, None)
    return buf.value


def find(cls, title=None):
    return user32.FindWindowW(cls, title)


def load_cfg():
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------- 0. 备份并把 run_enabled 置为 False ----------
shutil.copy2(CFG, BAK)
cfg = load_cfg()
cfg["run_enabled"] = False
cfg["run_path"] = "subl"
cfg["run_args"] = "-w"
with open(CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
p("起始状态: run_enabled=%s run_path=%r run_args=%r"
  % (cfg.get("run_enabled"), cfg.get("run_path"), cfg.get("run_args")))

# ---------- 1. 启动 ----------
p("\n=== 启动程序 ===")
subprocess.Popen(["cmd", "/c", "start", "", EXE, "-show"])
main = 0
for _ in range(30):
    time.sleep(1)
    main = find("MechrevoModeMainWnd")
    if main:
        break
if not main:
    shutil.copy2(BAK, CFG)
    sys.exit("主界面没起来")
p("main =", main)

chk = user32.GetDlgItem(main, UID_RUN_ENABLE)
checked = user32.SendMessageW(chk, BM_GETCHECK, 0, 0)
p("「程序启动后运行命令」勾选状态 = %s" % ("勾选" if checked else "未勾选"))

p("\n=== 未勾选时，控件是否可编辑 ===")
ok_enabled = True
for name, cid in [("程序路径", UID_RUN_PATH), ("浏览…", UID_RUN_BROWSE),
                  ("参数", UID_RUN_ARGS), ("延迟", UID_RUN_DELAY)]:
    h = user32.GetDlgItem(main, cid)
    en = bool(user32.IsWindowEnabled(h))
    ok_enabled &= en
    p("  %-10s enabled=%s" % (name, en))

# ---------- 2. 直接改，不勾选 ----------
p("\n=== 未勾选「程序启动后运行命令」，直接修改并保存 ===")
ed_path = user32.GetDlgItem(main, UID_RUN_PATH)
ed_args = user32.GetDlgItem(main, UID_RUN_ARGS)
p("  修改前 程序=%r 参数=%r" % (get_text(ed_path), get_text(ed_args)))

set_text(ed_path, NEW_PATH)
set_text(ed_args, NEW_ARGS)
time.sleep(0.3)
back_path, back_args = get_text(ed_path), get_text(ed_args)
p("  写入后 程序=%r 参数=%r" % (back_path, back_args))

p("  点击「保存」…")
user32.PostMessageW(user32.GetDlgItem(main, UID_SAVE), BM_CLICK, 0, 0)
time.sleep(1.5)

after = load_cfg()
p("\n=== config.json 实际内容 ===")
p("  run_enabled = %s" % after.get("run_enabled"))
p("  run_path    = %r" % after.get("run_path"))
p("  run_args    = %r" % after.get("run_args"))

p("\n--- 判定 ---")
good = True
if back_path != NEW_PATH or back_args != NEW_ARGS:
    p("!! 控件写入没生效"); good = False
if after.get("run_path") != NEW_PATH:
    p("!! run_path 没保存"); good = False
if after.get("run_args") != NEW_ARGS:
    p("!! run_args 没保存"); good = False
if after.get("run_enabled"):
    p("!! run_enabled 被意外打开了"); good = False
if not ok_enabled:
    p("!! 有控件被灰掉"); good = False

# 保存后控件是否又被灰掉
still = all(bool(user32.IsWindowEnabled(user32.GetDlgItem(main, c)))
            for c in (UID_RUN_PATH, UID_RUN_BROWSE, UID_RUN_ARGS, UID_RUN_DELAY))
p("  保存后控件仍可用: %s" % still)
if not still:
    good = False

p("\n" + ("PASS 需求3：不勾选也能改、且能存进 config.json" if good else "FAIL 需求3"))

# ---------- 收尾：先关程序，再还原 config ----------
p("\n=== 收尾 ===")
tray = find("MechrevoModeTrayWnd")
if tray:
    user32.PostMessageW(tray, WM_CLOSE, 0, 0)
    time.sleep(2)
for _ in range(10):
    if not subprocess.run(["tasklist", "/FI", "IMAGENAME eq MechrevoMode.exe",
                           "/FO", "CSV", "/NH"],
                          capture_output=True, text=True).stdout.strip().startswith('"'):
        break
    time.sleep(0.5)
shutil.copy2(BAK, CFG)
os.remove(BAK)
restored = load_cfg()
p("已还原: run_enabled=%s run_path=%r run_args=%r"
  % (restored.get("run_enabled"), restored.get("run_path"), restored.get("run_args")))
