# -*- coding: utf-8 -*-
"""GCU 后端与「风扇转速断供」的时序取证。

回答三个问题：
  1. GCUBridge / GCUService 是什么时候起来的（开机？还是被谁拉起的？）
  2. 每次 MechrevoMode 启动后，「连上 broker」到「首个真实转速」隔了多久
  3. 这段时间里程序自己做了什么自愈动作

进程存在性一律走 CreateToolhelp32Snapshot —— 实测 QueryFullProcessImageNameW
对 GCUBridge/GCUService 会因权限拿不到路径，据此判断「进程没在跑」是错的。
"""

import ctypes
import datetime
import os
import re
import struct
import sys
from ctypes import wintypes

LOG = r"D:\User\OneDrive\Programm\MechrevoMode\data\log.txt"

k32 = ctypes.WinDLL("kernel32", use_last_error=True)


# ---------------------------------------------------------------- 进程

class PE32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def snapshot():
    """返回 [(pid, ppid, name)]，用 Toolhelp 快照，不受 ACL 影响。"""
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if snap == -1:
        return []
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    out = []
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        out.append((e.th32ProcessID, e.th32ParentProcessID,
                    e.szExeFile.decode("gbk", "replace")))
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


def proc_start_time(pid):
    """进程启动时刻（本地时间）。取不到返回 None。

    GetProcessTimes 的第 4/5 个参数是内核/用户 CPU 时间，**不是**退出时间；
    实测个别进程（如 OSDTpDetect）会返回越界的 FILETIME，直接喂给
    fromtimestamp 会抛 OSError，所以这里必须校验结果落在合理区间。
    """
    h = k32.OpenProcess(0x1000, False, pid)          # QUERY_LIMITED_INFORMATION
    if not h:
        h = k32.OpenProcess(0x0400, False, pid)      # 退一步用 QUERY_INFORMATION
    if not h:
        return None
    c, e, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    ok = k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                             ctypes.byref(k), ctypes.byref(u))
    k32.CloseHandle(h)
    if not ok:
        return None
    ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime
    if ticks == 0:
        return None
    unix = ticks / 10_000_000 - 11644473600
    try:
        t = datetime.datetime.fromtimestamp(unix)
    except (OSError, OverflowError, ValueError):
        return None
    # 合理区间：本世纪内、且不在未来
    if not (datetime.datetime(2020, 1, 1) <= t <= datetime.datetime.now()):
        return None
    return t


def boot_time():
    """系统开机时刻 = GetTickCount64 反推。"""
    k32.GetTickCount64.restype = ctypes.c_ulonglong
    return datetime.datetime.now() - datetime.timedelta(
        milliseconds=k32.GetTickCount64())


# ---------------------------------------------------------------- 日志

TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ")

HEAL_KEYS = ["未就绪", "已拉起", "拉起 GCUBridge", "无法连接", "收不到任何状态",
             "无法自动恢复", "已在运行"]


def read_lines():
    with open(LOG, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def parse_sessions(lines):
    sessions = []
    cur = None
    for l in lines:
        m = TS_RE.match(l)
        if not m:
            continue
        t = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if "启动 提权" in l:
            cur = {"start": t, "connect": None, "status": None,
                   "rpm": None, "heal": [], "end": None}
            sessions.append(cur)
        if cur is None:
            continue
        cur["end"] = t
        if "已连接 GCU" in l and cur["connect"] is None:
            cur["connect"] = t
        if "状态更新" in l and cur["status"] is None:
            cur["status"] = t
        if "🌀" in l:
            mm = re.search(r"🌀(\d+)RPM", l)
            if mm and cur["rpm"] is None:
                cur["rpm"] = t
        if any(k in l for k in HEAL_KEYS):
            cur["heal"].append(l)
    return sessions


def fmt(s, st, key):
    v = s.get(key)
    if not v:
        return "—"
    return f"{(v - st).total_seconds():.0f}s"


def main():
    bt = boot_time()
    print(f"系统开机时刻（推算）: {bt:%Y-%m-%d %H:%M:%S}")
    print(f"当前时刻            : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    print()

    procs = snapshot()
    print(f"=== 进程快照 {len(procs)} 个，其中 GCU 相关 ===")
    for pid, ppid, name in sorted(procs):
        if any(k in name.lower() for k in ("gcu", "mechrevo", "aistone", "osd")):
            st = proc_start_time(pid)
            sts = f"{st:%Y-%m-%d %H:%M:%S}" if st else "（取不到启动时刻）"
            print(f"  pid={pid:<6} ppid={ppid:<6} {name:<20} 启动于 {sts}")
    print()

    lines = read_lines()
    sessions = parse_sessions(lines)
    print(f"=== 共解析出 {len(sessions)} 次启动 ===")
    hdr = f"{'启动时刻':<21}{'连上broker':>10}{'首个状态':>10}{'首个真转速':>11}{'存活':>8}{'自愈':>5}"
    print(hdr)
    print("-" * len(hdr))
    for s in sessions:
        alive = s["end"] - s["start"] if s["end"] else None
        print(f"{str(s['start']):<21}"
              f"{fmt(s, s['start'], 'connect'):>10}"
              f"{fmt(s, s['start'], 'status'):>10}"
              f"{fmt(s, s['start'], 'rpm'):>11}"
              f"{(str(int(alive.total_seconds())) + 's') if alive else '—':>8}"
              f"{len(s['heal']):>5}")
    print()

    # 「首个真转速」相对「启动」的耗时分布 —— 这是用户感受到的延迟
    gaps = [((s["rpm"] - s["start"]).total_seconds())
            for s in sessions if s["rpm"]]
    gaps = [g for g in gaps if g >= 0]
    if gaps:
        gaps.sort()
        print(f"=== 「启动 → 首个真实转速」耗时（{len(gaps)} 次有数据）===")
        print(f"  最小 {gaps[0]:.0f}s  中位 {gaps[len(gaps)//2]:.0f}s  "
              f"最大 {gaps[-1]:.0f}s")
        buckets = {"<10s": 0, "10~60s": 0, "60~120s": 0, ">120s": 0}
        for g in gaps:
            if g < 10:
                buckets["<10s"] += 1
            elif g < 60:
                buckets["10~60s"] += 1
            elif g < 120:
                buckets["60~120s"] += 1
            else:
                buckets[">120s"] += 1
        print("  " + "  ".join(f"{k}:{v}" for k, v in buckets.items()))
    print()

    print("=== 各会话的自愈/异常明细 ===")
    for s in sessions:
        if s["heal"]:
            print(f"--- 启动 {s['start']} ---")
            for l in s["heal"][:14]:
                print("   ", l)
            if len(s["heal"]) > 14:
                print(f"    …（另有 {len(s['heal']) - 14} 条同类）")


if __name__ == "__main__":
    main()
