# 实测「浏览…」按钮：确认它处于可用状态，点击后能弹出文件选择对话框。
import ctypes
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
IDCANCEL = 2
WM_COMMAND = 0x0111

EXE = r"D:\User\Desktop\MechrevoMode\MechrevoMode.exe"
LOG = os.path.expandvars(r"%APPDATA%\MechrevoMode\log.txt")

# 控件 ID（与 ui.go 一致）
UID_RUN_PATH = 111
UID_RUN_BROWSE = 112
UID_RUN_ARGS = 113
UID_RUN_DELAY = 114
UID_RUN_ENABLE = 110
UID_RUN_ELEVATE = 115
UID_RUN_NOW = 116


def find(cls, title=None):
    return user32.FindWindowW(cls, title)


def class_of(hwnd):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, b, 256)
    return b.value


def title_of(hwnd):
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, b, 512)
    return b.value


def children_of_class(cls):
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        if class_of(hwnd) == cls:
            out.append((hwnd, title_of(hwnd)))
        return True

    user32.EnumWindows(cb, 0)
    return out


def read_log():
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


print("=== 启动程序 ===")
subprocess.Popen(["cmd", "/c", "start", "", EXE, "-show"])
main = 0
for _ in range(30):
    time.sleep(1)
    main = find("MechrevoModeMainWnd")
    if main:
        break
if not main:
    sys.exit("主界面没起来")
print("main =", main)

mark = len(read_log())

print("\n=== 启动命令区各控件的可用状态 ===")
for name, cid in [("程序路径", UID_RUN_PATH), ("浏览…", UID_RUN_BROWSE),
                  ("参数", UID_RUN_ARGS), ("延迟", UID_RUN_DELAY),
                  ("立即运行", UID_RUN_NOW),
                  ("需要管理员权限", UID_RUN_ELEVATE),
                  ("程序启动后运行命令", UID_RUN_ENABLE)]:
    h = user32.GetDlgItem(main, cid)
    print("  %-18s ctl=%-9d enabled=%s" % (name, h, bool(user32.IsWindowEnabled(h))))

print("\n=== 点击「浏览…」（第 1 次）===")
browse = user32.GetDlgItem(main, UID_RUN_BROWSE)


def click_and_catch(tag):
    """点一次浏览，等对话框出现，报告结果并取消掉。返回 (是否弹出, 文件名框内容)"""
    user32.PostMessageW(browse, BM_CLICK, 0, 0)
    h = 0
    for _ in range(12):
        time.sleep(0.5)
        boxes = [(x, t) for x, t in children_of_class("#32770")
                 if "选择要启动的程序" in t]
        if boxes:
            h = boxes[0][0]
            print("  [%s] 弹出文件对话框: hwnd=%d title=%r" % (tag, boxes[0][0], boxes[0][1]))
            break
    if not h:
        print("  [%s] !! 没有弹出对话框" % tag)
        print("  当前所有 #32770 窗口:", children_of_class("#32770"))
        return False, ""

    time.sleep(0.6)
    fname = ""
    edit = user32.GetDlgItem(h, 0x047C)  # edt1（文件名输入框）
    if edit:
        b = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(edit, b, 1024)
        fname = b.value
    print("  [%s] 文件名框内容 = %r" % (tag, fname))
    user32.SendMessageW(h, WM_COMMAND, IDCANCEL, 0)
    time.sleep(0.8)
    print("  [%s] 对话框已关闭:" % tag,
          not bool(find("#32770", "选择要启动的程序")))
    return True, fname


ok1, name1 = click_and_catch("第1次")

# 第二次：验证 ofnGoodSize 缓存生效 —— 日志里不应该再出现 CDERR_STRUCTSIZE
mark2 = len(read_log())
print("\n=== 点击「浏览…」（第 2 次，验证尺寸缓存）===")
ok2, name2 = click_and_catch("第2次")
delta2 = read_log()[mark2:].strip()
print("  第 2 次日志新增:", delta2 or "(无)")
if "CDERR_STRUCTSIZE" in delta2:
    print("  !! 缓存没生效，又撞了一次 160")
elif ok2:
    print("  OK 缓存生效：直接用了上次验证过的尺寸，没有再撞 CDERR_STRUCTSIZE")

print("\n=== 本次日志新增（累计）===")
new = read_log()[mark:]
print(new.strip() or "(无)")

print("\n=== 收尾：关闭程序 ===")
WM_CLOSE = 0x0010

# 万一上面判断失败，模态对话框可能还开着，先强关，否则它会把主窗口一直卡住
for _ in range(3):
    stray = [(h, t) for h, t in children_of_class("#32770")
             if "选择要启动的程序" in t]
    if not stray:
        break
    for h, _t in stray:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    time.sleep(0.8)
print("残留对话框:", [(h, t) for h, t in children_of_class("#32770")
                     if "选择要启动的程序" in t] or "无")

tray = find("MechrevoModeTrayWnd")
if tray:
    user32.PostMessageW(tray, WM_CLOSE, 0, 0)
    time.sleep(1.5)
print("剩余进程:", subprocess.run(
    ["tasklist", "/FI", "IMAGENAME eq MechrevoMode.exe", "/FO", "CSV", "/NH"],
    capture_output=True, text=True).stdout.strip()[:80])
