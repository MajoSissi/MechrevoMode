# -*- coding: utf-8 -*-
"""武装实验：在**未武装**状态下正确唤起控制台，抓出「武装」到底做了什么。

正确 AUMID 是从包的 AppxManifest.xml 里读出来的：
    PFN   = CCU.WinUI_wrbgcf7aesyd8
    AppId = App
    → shell:AppsFolder\\CCU.WinUI_wrbgcf7aesyd8!App

（厂商自带的 ControlCenterU.exe 里写死的却是旧包名 ControlCenter3_h329z55cwnj8g，
  所以那个启动器跑起来什么都不会发生 —— 之前白试一次。）

观察三样东西：
  1. 有没有新进程（特别是 CCUWinUI.exe 自己，以及它派生的子进程）
  2. broker 上有没有新的非周期报文（可能就是武装命令）
  3. FanInfo 什么时候开始流
"""

import ctypes
import socket
import struct
import subprocess
import threading
import time

HOST, PORT = "127.0.0.1", 13688
AUMID = r"shell:AppsFolder\CCU.WinUI_wrbgcf7aesyd8!App"
PERIODIC = {"System/FanInfo", "System/CpuInfo", "System/GpuInfo",
            "System/MemoryInfo", "System/NetworkInfo", "System/DiskInfo"}

ntdll = ctypes.WinDLL("ntdll")
T0 = time.time()


def el():
    return time.time() - T0


def say(m):
    print("[%7.2fs] %s" % (el(), m), flush=True)


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


class Observer:
    def __init__(self):
        self.events = []
        self.ready = threading.Event()
        self.stop = False
        self.sock = None

    def _connect(self):
        for i in (8, 9, 7, 6):
            try:
                s = socket.create_connection((HOST, PORT), timeout=5)
            except OSError:
                continue
            cid = b"UWPClient_%d" % i
            usr = b"UWPClient_User_%d" % i
            pwd = b"UWPClient_Pwd888881772688_%d" % i
            vh = b"\x00\x04MQTT\x04\xc2\x00\x19"
            pay = (struct.pack(">H", len(cid)) + cid
                   + struct.pack(">H", len(usr)) + usr
                   + struct.pack(">H", len(pwd)) + pwd)
            body = vh + pay
            s.sendall(bytes([0x10, len(body)]) + body)
            time.sleep(0.35)
            try:
                r = s.recv(16)
            except OSError:
                r = b""
            if len(r) >= 4 and r[3] == 0:
                self.sock = s
                return True
            s.close()
        return False

    def run(self):
        if not self._connect():
            say("!! 观察者连不上")
            self.ready.set()
            return
        t = b"#"
        p = struct.pack(">H", 1) + struct.pack(">H", len(t)) + t + bytes([0])
        self.sock.sendall(bytes([0x82, len(p)]) + p)
        self.sock.settimeout(0.5)
        try:
            self.sock.recv(64)
        except OSError:
            pass
        self.ready.set()
        while not self.stop:
            try:
                head = self.sock.recv(1)
                if not head:
                    break
                mult, val = 1, 0
                for _ in range(4):
                    b = self.sock.recv(1)[0]
                    val += (b & 0x7F) * mult
                    if not (b & 0x80):
                        break
                    mult *= 128
                data = b""
                while len(data) < val:
                    c = self.sock.recv(val - len(data))
                    if not c:
                        break
                    data += c
                if head[0] >> 4 != 3:
                    continue
                tl = struct.unpack(">H", data[:2])[0]
                tp = data[2:2 + tl].decode("utf-8", "replace")
                raw = data[2 + tl:].decode("utf-8", "replace")
                self.events.append((el(), tp, raw))
            except OSError:
                continue

    def fan_count(self, a, b):
        return sum(1 for t, tp, _ in self.events
                   if tp == "System/FanInfo" and a <= t <= b)


def main():
    obs = Observer()
    threading.Thread(target=obs.run, daemon=True).start()
    obs.ready.wait(15)

    say("=== 武装前确认 ===")
    a = el()
    time.sleep(6)
    n = obs.fan_count(a, el())
    say(f"6 秒内 FanInfo {n} 条 → {'已武装（实验前提不成立）' if n else '未武装（正确的前提）'}")

    before = procs()
    say("=== 唤起控制台 ===")
    try:
        subprocess.Popen(["explorer.exe", AUMID])
        say("已发起: explorer.exe " + AUMID)
    except OSError as e:
        say(f"发起失败: {e!r}")

    t_launch = el()
    armed = None
    for i in range(40):                      # 最多观察 100 秒
        time.sleep(2.5)
        cur = procs()
        for p in set(cur) - set(before):
            say(f"  新进程: pid={p} ppid={cur[p][1]} {cur[p][0]}")
        before = cur
        if obs.fan_count(t_launch, el()) > 0:
            armed = el()
            say(f"  >>> 遥测开始流动（+{armed - t_launch:.1f}s）")
            break
        if i == 7:
            say(f"  已过 {el() - t_launch:.0f}s，遥测还没流，继续等…")

    say("=== 结果 ===")
    if armed:
        say(f"唤起控制台后 {armed - t_launch:.1f} 秒遥测恢复 → 控制台确实是武装手段")
    else:
        say("唤起控制台 100 秒内遥测未恢复")

    say("---- 期间出现的非周期报文 ----")
    new = [(t, tp, pl) for t, tp, pl in obs.events
           if tp not in PERIODIC and t >= t_launch - 1]
    if not new:
        say("  （无）")
    for t, tp, pl in new:
        say("  +%7.2fs  %-32s %s" % (t, tp, pl[:230]))

    say("---- 周期遥测 ----")
    for tp in sorted(PERIODIC):
        ts = [t for t, t2, _ in obs.events if t2 == tp]
        if ts:
            say("  %-24s 共 %-3d 条  首条 +%.2fs" % (tp, len(ts), ts[0]))
        else:
            say("  %-24s 全程没有" % tp)

    obs.stop = True


if __name__ == "__main__":
    main()
