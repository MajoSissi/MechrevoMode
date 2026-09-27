# -*- coding: utf-8 -*-
"""验证：我们自己发 System/Control {"Action":"System_ON"} 能否独立武装遥测。

不依赖控制台 —— 这才是能写进产品的结论。步骤：
  A. 结束 GCUService → 等它被 GCUBridge 拉回来并就绪 → 确认未武装
  B. **只发一条** System/Control {"Action":"System_ON"} → 看遥测是否恢复
  C. 顺便测：关掉控制台窗口后，遥测会不会掉（决定修复要不要周期性重发）
"""

import ctypes
import socket
import struct
import subprocess
import sys
import threading
import time

HOST, PORT = "127.0.0.1", 13688
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


def enc_len(n):
    o = b""
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        o += bytes([b])
        if n == 0:
            return o


def enc_str(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


class Client:
    """既能订阅、也能发布的极简 MQTT 客户端。"""

    def __init__(self, idx):
        self.idx = idx
        self.sock = None

    def connect(self):
        for i in ([self.idx] if self.idx else [8, 9, 7, 6]):
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
                self.idx = i
                return True
            s.close()
        return False

    def subscribe(self, topic):
        p = struct.pack(">H", 1) + struct.pack(">H", len(topic)) + topic.encode() + bytes([0])
        self.sock.sendall(bytes([0x82]) + enc_len(len(p)) + p)
        self.sock.settimeout(0.6)
        try:
            self.sock.recv(64)
        except OSError:
            pass

    def publish(self, topic, payload):
        t = topic.encode()
        p = struct.pack(">H", len(t)) + t + payload.encode()
        self.sock.sendall(bytes([0x30]) + enc_len(len(p)) + p)


class Observer:
    def __init__(self):
        self.events = []
        self.ready = threading.Event()
        self.stop = False

    def run(self):
        c = Client(0)
        if not c.connect():
            say("!! 观察者连不上")
            self.ready.set()
            return
        c.subscribe("#")
        self.sock = c.sock
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
                    ch = self.sock.recv(val - len(data))
                    if not ch:
                        break
                    data += ch
                if head[0] >> 4 != 3:
                    continue
                tl = struct.unpack(">H", data[:2])[0]
                tp = data[2:2 + tl].decode("utf-8", "replace")
                self.events.append((el(), tp, data[2 + tl:].decode("utf-8", "replace")))
            except OSError:
                continue

    def count(self, topic, a, b):
        return sum(1 for t, tp, _ in self.events if tp == topic and a <= t <= b)


def kill(name):
    fn = "_cmd_kill_tmp.txt"
    with open(fn, "w", encoding="utf-8") as f:
        f.write("taskkill /F /IM %s\n" % name)
    r = subprocess.run([sys.executable, "_elev.py", "@" + fn, "30"],
                       capture_output=True, text=True, timeout=90)
    out = " ".join(l for l in r.stdout.splitlines() if "SUCCESS" in l or "错误" in l
                   or "not found" in l.lower() or "PID" in l)
    say("  taskkill %s → %s" % (name, out.strip() or "(无输出)"))


def main():
    obs = Observer()
    threading.Thread(target=obs.run, daemon=True).start()
    obs.ready.wait(15)

    # ---------- A. 回到未武装
    say("=== A. 结束 GCUService，回到未武装 ===")
    kill("GCUService.exe")
    say("  等 GCUBridge 把它拉回来并就绪（实测约 40 秒）…")
    t_kill = el()
    for _ in range(28):
        time.sleep(2)
        p = procs()
        names = {v[0] for v in p.values()}
        if "GCUService.exe" in names and el() - t_kill > 45:
            break
    alive = sorted(n for n in {v[0] for v in procs().values()}
                   if "gcu" in n.lower() or "ccu" in n.lower() or "systray" in n.lower())
    say("  当前 GCU/CCU 进程: %s" % alive)
    a = el()
    time.sleep(8)
    n0 = obs.count("System/FanInfo", a, el())
    say("  A 阶段 8 秒内 FanInfo %d 条 → %s"
        % (n0, "未武装 ✓" if n0 == 0 else "居然还在流 ?!"))

    # ---------- B. 自己发 System_ON
    say("=== B. 只发一条 System/Control {\"Action\":\"System_ON\"} ===")
    pub = Client(0)
    if not pub.connect():
        say("  !! 发布端连不上")
        return
    t_pub = el()
    pub.publish("System/Control", '{"Action":"System_ON"}')
    say("  已发送（topic=System/Control）")

    armed_at = None
    for i in range(16):
        time.sleep(2.5)
        if obs.count("System/FanInfo", t_pub, el()) > 0:
            armed_at = el()
            break
    if armed_at:
        say("  >>> 遥测恢复，延迟 %.1f 秒 → **我们自己就能武装，不需要控制台** ✓"
            % (armed_at - t_pub))
    else:
        say("  >>> 40 秒内没恢复 ✗（可能还需要别的动作，或 GCUService 尚未就绪）")

    # ---------- C. 关掉控制台窗口，看遥测会不会掉
    say("=== C. 关掉控制台（CCUWinUI.exe），看遥测是否继续 ===")
    if "CCUWinUI.exe" in {v[0] for v in procs().values()}:
        kill("CCUWinUI.exe")
        a2 = el()
        time.sleep(12)
        n2 = obs.count("System/FanInfo", a2, el())
        say("  关掉后 12 秒内 FanInfo %d 条 → %s"
            % (n2, "遥测保持（不需要周期性重发）" if n2 > 0 else "遥测掉了（需要保活/重发）"))
    else:
        say("  控制台没在跑，跳过")

    say("---- 全程非周期报文 ----")
    for t, tp, pl in obs.events:
        if tp not in PERIODIC:
            say("  +%7.2fs  %-30s %s" % (t, tp, pl[:150]))

    obs.stop = True


if __name__ == "__main__":
    main()
