# -*- coding: utf-8 -*-
"""查 GCU 相关进程的**会话 ID** 与**创建时刻**。

为什么要看会话：GCUBridge 是 SYSTEM 服务（session 0），
ControlCenterU.exe（内部名 LaunchGCU）却用 CreateProcessAsUserWrapper
往**交互会话**里拉进程。如果读 EC 需要交互会话，那「必须开一次控制台」
就有了物理解释。

创建时刻不走 GetProcessTimes（对这几个进程拿不到），
改用 NtQuerySystemInformation(SystemProcessInformation) —— 不需要特殊权限。
"""

import ctypes
import datetime
import struct
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

OURS = ctypes.windll.kernel32.GetCurrentProcessId()


def session_id(pid):
    sid = wintypes.DWORD(0)
    if k32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
        return sid.value
    return None


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("padding", wintypes.DWORD),
                ("Buffer", ctypes.c_void_p)]


def nt_process_table():
    """返回 {pid: (create_datetime, name, ppid)}"""
    SystemProcessInformation = 5
    size = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(size)
        ret = ctypes.c_ulong(0)
        st = ntdll.NtQuerySystemInformation(
            SystemProcessInformation, buf, size, ctypes.byref(ret))
        if st == 0xC0000004:                     # STATUS_INFO_LENGTH_MISMATCH
            size *= 2
            if size > (1 << 28):
                raise RuntimeError("进程表太大")
            continue
        if st != 0:
            raise OSError(f"NtQuerySystemInformation 失败 0x{st & 0xFFFFFFFF:08X}")
        break

    out = {}
    off = 0
    blob = buf.raw
    while True:
        next_off = struct.unpack_from("<I", blob, off)[0]
        create_ticks = struct.unpack_from("<q", blob, off + 0x20)[0]
        pid = struct.unpack_from("<Q", blob, off + 0x50)[0]
        ppid = struct.unpack_from("<Q", blob, off + 0x58)[0]
        us = UNICODE_STRING.from_buffer_copy(blob[off + 0x38: off + 0x38 + 16])
        name = ""
        if us.Length and us.Buffer:
            raw = ctypes.string_at(us.Buffer, us.Length)
            name = raw.decode("utf-16-le", "replace")
        if create_ticks:
            unix = create_ticks / 10_000_000 - 11644473600
            try:
                t = datetime.datetime.fromtimestamp(unix)
            except (OSError, OverflowError, ValueError):
                t = None
        else:
            t = None
        out[pid] = (t, name, ppid)
        if next_off == 0:
            break
        off += next_off
    return out


def main():
    table = nt_process_table()
    print(f"当前进程表 {len(table)} 项；本进程 pid={OURS} 会话={session_id(OURS)}")
    print()

    print("=== GCU / 控制中心相关进程 ===")
    hdr = f"{'pid':>7} {'会话':>5}  {'创建时刻':<20} {'父进程':>8}  名字"
    print(hdr)
    print("-" * 78)
    for pid in sorted(table):
        t, name, ppid = table[pid]
        low = name.lower()
        if not any(k in low for k in ("gcu", "controlcenter", "gamingcenter",
                                      "mechrevo", "aistone", "osd", "launch")):
            continue
        sid = session_id(pid)
        pname = table.get(ppid, (None, "", 0))[1] if ppid else ""
        print(f"{pid:>7} {str(sid):>5}  "
              f"{(t.strftime('%Y-%m-%d %H:%M:%S') if t else '？'):<20} "
              f"{ppid:>8}  {name}   ← {pname}")
    print()

    print("=== 明显跑在 session 0 的 GCU 组件 ===")
    for pid in sorted(table):
        t, name, ppid = table[pid]
        if not any(k in name.lower() for k in ("gcu", "aistone")):
            continue
        if session_id(pid) == 0:
            print(f"  pid={pid} {name} 创建于 "
                  f"{t.strftime('%Y-%m-%d %H:%M:%S') if t else '？'}")
    print()

    print("=== 所有名字里带 GCU 的 Windows 服务（注册表） ===")
    import winreg
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SYSTEM\CurrentControlSet\Services")
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
                i += 1
            except OSError:
                break
            if "gcu" in name.lower() or "aistone" in name.lower():
                try:
                    k = winreg.OpenKey(root, name)
                    img = winreg.QueryValueEx(k, "ImagePath")[0]
                    start = winreg.QueryValueEx(k, "Start")[0]
                    typ = winreg.QueryValueEx(k, "Type")[0]
                    try:
                        obj = winreg.QueryValueEx(k, "ObjectName")[0]
                    except OSError:
                        obj = "?"
                    print(f"  {name}: Start={start} Type={typ} "
                          f"ObjectName={obj}")
                    print(f"      ImagePath={img}")
                except OSError as e:
                    print(f"  {name}: 读取失败 {e}")
        winreg.CloseKey(root)
    except OSError as e:
        print("  枚举服务失败:", e)


if __name__ == "__main__":
    main()
