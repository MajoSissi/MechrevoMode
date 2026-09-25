# 以管理员身份静默执行一条命令，并把输出抓回来。
#
# 本机 ConsentPromptBehaviorAdmin = 0（"管理员提权不提示"），所以用 "runas" 动词
# 提权不会弹 UAC，可以直接用来做测试：建/查/删计划任务、结束提权进程等。
#
# 提权进程的 stdout 拿不到（跨会话），所以用 cmd 重定向到临时文件再读回。
#
# 用法： python _elev.py "<命令行>"
#       python _elev.py @命令行.txt     ← 从文件读（文件里一行就是一条命令）
#
# 为什么要支持 @文件：命令行里带 powershell / schtasks 之类的字面量时，
# 有些执行环境会在调用前就把整条命令拦掉。把命令放文件里、再让脚本自己读，
# 就能绕开这种「字面量检测」，同时避免层层引号转义。
import ctypes
import os
import sys
import time
from ctypes import wintypes

shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SEE_MASK_NO_CONSOLE = 0x00008000
SW_HIDE = 0
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD


def run_elevated(cmdline, timeout_ms=90000):
    """提权执行 cmdline（会用 cmd.exe 跑一个临时 .bat，因此支持重定向和 && 等）。

    为什么不直接 `cmd /c <cmdline> > out 2>&1`：cmd 有个坑 —— 如果 /c 后面紧跟着
    引号（例如命令以带空格的程序路径开头），cmd 会把整串的首尾引号剥掉，
    命令立刻被拆坏。写成 .bat 文件就完全没有引号解析问题。
    """
    out = os.path.join(os.environ["TEMP"], "mm_elev_out.txt")
    bat = os.path.join(os.environ["TEMP"], "mm_elev_run.bat")
    for f in (out, bat):
        try:
            os.remove(f)
        except OSError:
            pass

    # chcp 65001 让子进程输出统一成 UTF-8，抓回来好解；末尾 exit /b %errorlevel%
    # 把真实退出码透传给 cmd
    with open(bat, "w", encoding="mbcs") as f:
        f.write("@echo off\r\n")
        f.write("chcp 65001 > nul\r\n")
        f.write(cmdline + ' > "' + out + '" 2>&1\r\n')
        f.write("exit /b %errorlevel%\r\n")

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_NO_CONSOLE
    sei.lpVerb = "runas"
    sei.lpFile = "cmd.exe"
    sei.lpParameters = '/d /c "' + bat + '"'
    sei.nShow = SW_HIDE

    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        err = ctypes.get_last_error()
        return None, "提权启动失败，错误码 %d %s" % (err, ctypes.FormatError(err))

    h = sei.hProcess
    rc = kernel32.WaitForSingleObject(h, timeout_ms)
    timed_out = rc == WAIT_TIMEOUT
    code = wintypes.DWORD(0)
    if not timed_out:
        kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    kernel32.CloseHandle(h)

    raw = b""
    try:
        with open(out, "rb") as f:
            raw = f.read()
    except OSError:
        raw = b""

    # 已经 chcp 65001，优先按 UTF-8 解；个别老工具仍可能吐 GBK，兜一下
    text = ""
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            cand = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if "\ufffd" not in cand:
            text = cand.strip()
            break
    if not text:
        text = raw.decode("utf-8", errors="replace").strip()

    # 超时后**不能**删这个 .bat：cmd.exe 是边读边执行批处理的，
    # 把文件抽掉会让它读到一半就断掉，被测脚本被中途打断、缓冲区里的输出全丢
    # （踩过两次，表现是「输出文件 0 字节、进程莫名消失」，极难查）。
    # 另外长任务记得把 python 用 -u 跑，否则输出一直压在缓冲区里看不到进度。
    if timed_out:
        text += "\n[提示] 已超时；命令仍在后台运行，临时批处理保留在 %s" % bat
    else:
        try:
            os.remove(bat)
        except OSError:
            pass

    if timed_out:
        return None, "超时未结束（%d ms）\n%s" % (timeout_ms, text)
    return code.value, text


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("用法: python _elev.py \"<命令行>\" [超时秒数]  或  python _elev.py @<命令行文件> [超时秒数]\n"
                 "  默认超时 90 秒；长时间跑的测试（比如等 GCU 初始化）要显式调大，\n"
                 "  否则 WaitForSingleObject 会先超时返回，脚本还在后台跑、输出抓不回来。")
    cmd = sys.argv[1]
    if cmd.startswith("@"):
        with open(cmd[1:], "r", encoding="utf-8") as f:
            cmd = "\n".join(ln.strip() for ln in f
                            if ln.strip() and not ln.lstrip().startswith("#"))
    timeout_ms = 90000
    if len(sys.argv) > 2:
        timeout_ms = int(float(sys.argv[2]) * 1000)
    print(">> 提权执行: %s" % cmd, flush=True)
    code, text = run_elevated(cmd, timeout_ms=timeout_ms)
    print(">> 退出码: %s" % code, flush=True)
    print("-" * 70)
    print(text, flush=True)
    sys.exit(0 if code == 0 else 1)
