# 验证「浏览…」是否把当前「程序」框的内容预填进文件名输入框。
# 做法：读 config.json 的 run_path -> 启动程序 -> 点浏览 -> 递归枚举对话框所有子窗口文本，
# 看 run_path 是否出现。
#
# 关键：跨进程读控件文本必须自己发 WM_GETTEXT，且必须用 SendMessageTimeoutW
# 带 SMTO_ABORTIFHUNG，否则碰上忙的窗口会直接把本脚本挂死。
import ctypes
import json
import os
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
WM_GETTEXT = 0x000D
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
IDCANCEL = 2
SMTO_ABORTIFHUNG = 0x0002

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]

EXE = r"D:\User\Desktop\MechrevoMode\MechrevoMode.exe"
CFG = os.path.expandvars(r"%APPDATA%\MechrevoMode\config.json")

UID_RUN_PATH, UID_RUN_BROWSE = 111, 112


def p(*a):
    print(*a, flush=True)


def class_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def text_of(h):
    """跨进程安全地取控件文本；拿不到就返回空串。"""
    b = ctypes.create_unicode_buffer(1024)
    res = ctypes.c_ulong(0)
    user32.SendMessageTimeoutW(h, WM_GETTEXT, 1024,
                               ctypes.cast(b, ctypes.c_void_p).value,
                               SMTO_ABORTIFHUNG, 400,
                               ctypes.byref(res))
    return b.value


def find(cls, title=None):
    return user32.FindWindowW(cls, title)


def all_dialogs():
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, lp):
        t = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, t, 512)
        if class_of(h) == "#32770" and "选择要启动的程序" in t.value:
            out.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return out


cfg = {}
try:
    with open(CFG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
except Exception as e:
    p("读 config 失败:", e)
run_path = cfg.get("run_path") or ""
p("config.run_path = %r" % run_path)

p("\n=== 启动程序 ===")
subprocess.Popen(["cmd", "/c", "start", "", EXE, "-show"])
main = 0
for _ in range(30):
    time.sleep(1)
    main = find("MechrevoModeMainWnd")
    if main:
        break
if not main:
    sys.exit("主界面没起来")
p("main =", main)

p("「程序」框当前内容 = %r" % text_of(user32.GetDlgItem(main, UID_RUN_PATH)))

p("\n=== 点击「浏览…」 ===")
user32.PostMessageW(user32.GetDlgItem(main, UID_RUN_BROWSE), BM_CLICK, 0, 0)

dlg = 0
for _ in range(12):
    time.sleep(0.5)
    ds = all_dialogs()
    if ds:
        dlg = ds[0]
        break
if not dlg:
    sys.exit("!! 没有弹出对话框")

time.sleep(1.0)  # 等 DirectUI 把内容填好
p("对话框 hwnd =", dlg)

kids = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def cbk(h, lp):
    kids.append(h)
    return True


user32.EnumChildWindows(dlg, cbk, 0)
p("\n--- 对话框内 %d 个子窗口中带文本的 ---" % len(kids))
texts = []
for h in kids:
    t = text_of(h)
    if t:
        texts.append((class_of(h), t))
        p("  [%s] %r" % (class_of(h), t[:80]))

p("\n--- 判定 ---")
if run_path and any(t == run_path or run_path in t for _c, t in texts):
    p("OK 文件名输入框正确预填为 %r" % run_path)
else:
    p("!! 在对话框里没找到 %r —— 预填没生效" % run_path)
    p("   出现的文本:", [t for _c, t in texts])

p("\n--- 关闭对话框 ---")
user32.SendMessageW(dlg, WM_COMMAND, IDCANCEL, 0)
time.sleep(0.8)

p("\n=== 收尾 ===")
tray = find("MechrevoModeTrayWnd")
if tray:
    user32.PostMessageW(tray, WM_CLOSE, 0, 0)
    time.sleep(1.5)
for _ in range(3):
    ds = all_dialogs()
    if not ds:
        break
    for h in ds:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    time.sleep(0.8)
p("残留对话框:", all_dialogs() or "无")
