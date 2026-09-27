# -*- coding: utf-8 -*-
"""测试「用程序唤起官方控制台」的正确姿势 —— 这是兜底修复方案的前提。

前面实测：直接跑 ControlCenterU.exe 之后**没有产生任何新进程**，等于没起作用。
真正的 UI 是 UWP 包 ControlCenter3_h329z55cwnj8g，标准唤起方式是
    explorer.exe shell:AppsFolder\\ControlCenter3_h329z55cwnj8g!App
这里把几种方式都试一遍，看哪种能真的把进程拉起来。

顺带确认 FanInfo 当时是否在流 —— 用来判断「唤起控制台」能否武装遥测。
"""

import ctypes
import socket
import struct
import subprocess
import sys
import time

HOST, PORT = "127.0.0.1", 13688
AUMID = r"shell:AppsFolder\ControlCenter3_h329z55cwnj8g!App"
LAUNCHER = r"C:\Program Files\OEM\机械革命控制中心\GamingCenter\ControlCenterU.exe"

ntdll = ctypes.WinDLL("ntdll")
KEYS = ("ccu", "systray", "controlcenter", "gamingcenter", "mechrevo", "aistone")


def procs():
    size = 1 << 22
    while True:
        buf = ctypes.create_string_buffer(size)
        ret = ctypes.c_ulong(0)
        st = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(ret))
        if st == 0xC0000004:
            size *= 2
            continue
        if st != 0:
            raise OSError(hex(st))
        break
    blob = buf.raw
    off = 0
    out = {}
    while True:
        nxt = struct.unpack_from("<I", blob, off)[0]
        pid = struct.unpack_from("<Q", blob, off + 0x50)[0]
        ppid = struct.unpack_from("<Q", blob, off + 0x58)[0]
        ln = struct.unpack_from("<H", blob, off + 0x38)[0]
        bp = struct.unpack_from("<Q", blob, off + 0x40)[0]
        nm = (ctypes.string_at(bp, ln).decode("utf-16-le", "replace")
              if ln and bp else "")
        out[pid] = (nm, ppid)
        if nxt == 0:
            break
        off += nxt
    return out


def faninfo_flowing(seconds=4.0):
    """被动听 seconds 秒，返回收到几条 FanInfo。"""
    s = socket.create_connection((HOST, PORT), timeout=5)
    vh = b"\x00\x04MQTT\x04\xc2\x00\x19"
    for i in (8, 6, 7):
        cid = f"UWPClient_{i}"
        pay = (struct.pack(">H", len(cid)) + cid.encode()
               + struct.pack(">H", len("UWPClient_User_" + str(i))) + ("UWPClient_User_" + str(i)).encode()
               + struct.pack(">H", len("UWPClient_Pwd888881772688_" + str(i))) + ("UWPClient_Pwd888881772688_" + str(i)).encode())
        body = vh + pay
        try:
            s2 = socket.create_connection((HOST, PORT), timeout=5)
        except OSError:
            return -1
        s2.sendall(bytes([0x10, len(body)]) + body)
        time.sleep(0.3)
        try:
            r = s2.recv(16)
        except OSError:
            r = b""
        if len(r) >= 4 and r[3] == 0:
            s = s2
            break
        s2.close()
        s = None
    if s is None:
        return -1
    topic = b"System/FanInfo"
    payload = struct.pack(">H", 1) + struct.pack(">H", len(topic)) + topic + bytes([0])
    s.sendall(bytes([0x82, len(payload)]) + payload)
    time.sleep(0.3)
    s.settimeout(1.0)
    try:
        s.recv(64)
    except OSError:
        pass

    n = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        s.settimeout(max(0.2, seconds - (time.time() - t0)))
        try:
            head = s.recv(1)
            if not head:
                break
            mult, val = 1, 0
            for _ in range(4):
                b = s.recv(1)[0]
                val += (b & 0x7F) * mult
                if not (b & 0x80):
                    break
                mult *= 128
            data = b""
            while len(data) < val:
                chunk = s.recv(val - len(data))
                if not chunk:
                    break
                data += chunk
            if head[0] >> 4 == 3:
                n += 1
        except OSError:
            break
    s.close()
    return n


def relevant():
    return {pid: v for pid, v in procs().items()
            if any(k in v[0].lower() for k in KEYS)}


def show(title, d):
    print(f"  {title}:")
    if not d:
        print("    （无）")
        return
    for pid, (nm, ppid) in sorted(d.items()):
        print(f"    pid={pid:<6} ppid={ppid:<6} {nm}")


def main():
    print("=== 步骤 1：基线 ===")
    base = relevant()
    show("当前相关进程", base)
    n = faninfo_flowing(4)
    print(f"  4 秒内收到 FanInfo {n} 条  → "
          f"{'遥测在流（已武装）' if n > 0 else '遥测没流（未武装）' if n == 0 else '连不上 broker'}")

    mode = sys.argv[1] if len(sys.argv) > 1 else "aumid"

    if mode == "aumid":
        print("\n=== 步骤 2：用 explorer.exe 唤起 UWP（shell:AppsFolder）===")
        cmd = ["explorer.exe", AUMID]
    elif mode == "launcher":
        print("\n=== 步骤 2：直接跑 ControlCenterU.exe ===")
        cmd = [LAUNCHER]
    else:
        print("\n=== 步骤 2：（跳过启动，只看现状）===")
        cmd = None

    if cmd:
        try:
            p = subprocess.Popen(cmd)
            print(f"  已发起: {cmd}  (pid={p.pid})")
        except OSError as e:
            print(f"  发起失败: {e!r}")
        time.sleep(22)

    print("\n=== 步骤 3：启动之后 ===")
    after = relevant()
    newpids = set(after) - set(base)
    print(f"  相关进程 {len(base)} → {len(after)}；新增 {len(newpids)} 个")
    show("全部相关进程", after)
    if newpids:
        show("新增的", {p: after[p] for p in newpids})
    n2 = faninfo_flowing(6)
    print(f"  6 秒内收到 FanInfo {n2} 条  → "
          f"{'遥测在流' if n2 > 0 else '遥测没流' if n2 == 0 else '连不上 broker'}")


if __name__ == "__main__":
    main()
