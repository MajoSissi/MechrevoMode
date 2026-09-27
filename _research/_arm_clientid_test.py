# -*- coding: utf-8 -*-
"""最后一轮定位：换 client ID / QoS / retain 试 System_ON，并录下控制台报文的原始标志位。

前情：
  * 冷状态下（20 秒确认无遥测），以 UWPClient_7 发 System_ON（QoS0/无 retain）
    无论是否订阅 System/# 都无效。
  * 但控制台启动后确实会发同一条命令，且随后遥测启动。
  → 差异只可能在：client ID、QoS、retain，或者还有别的随行动作。

本脚本先清场，再依次试 5 种变体（每个 20 秒判定），
最后启动控制台，把它那条 System_ON 的**原始标志位**打出来。
"""

import ctypes
import socket
import struct
import subprocess
import sys
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


def alive_names():
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
    out = set()
    while True:
        nxt = struct.unpack_from("<I", blob, off)[0]
        ln = struct.unpack_from("<H", blob, off + 0x38)[0]
        bp = struct.unpack_from("<Q", blob, off + 0x40)[0]
        if ln and bp:
            out.add(ctypes.string_at(bp, ln).decode("utf-16-le", "replace"))
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


def mqtt_publish(topic, payload, qos=0, retain=False):
    """按 MQTT 3.1.1 拼一条 PUBLISH。qos=1 时带上 packet id。"""
    t = topic.encode()
    body = struct.pack(">H", len(t)) + t
    if qos > 0:
        body += struct.pack(">H", 1)          # packet identifier
    body += payload.encode()
    flags = 0x30 | (qos << 1) | (1 if retain else 0)
    return bytes([flags]) + enc_len(len(body)) + body


class Conn:
    def __init__(self, idx):
        self.idx = idx
        self.sock = None
        self.buf = []

    def connect(self):
        try:
            s = socket.create_connection((HOST, PORT), timeout=5)
        except OSError:
            return False
        cid = b"UWPClient_%d" % self.idx
        usr = b"UWPClient_User_%d" % self.idx
        pwd = b"UWPClient_Pwd888881772688_%d" % self.idx
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

    def subscribe(self, topics):
        p = struct.pack(">H", len(topics))
        for t in topics:
            p += struct.pack(">H", len(t)) + t.encode() + bytes([0])
        self.sock.sendall(bytes([0x82]) + enc_len(len(p)) + p)
        self.sock.settimeout(0.6)
        try:
            self.sock.recv(8192)
        except OSError:
            pass

    def send(self, raw):
        self.sock.sendall(raw)

    def drain(self, seconds):
        out = []
        end = time.time() + seconds
        while time.time() < end:
            self.sock.settimeout(max(0.2, end - time.time()))
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
                if head[0] >> 4 == 3:
                    tl = struct.unpack(">H", data[:2])[0]
                    out.append((head[0], data[2:2 + tl].decode("utf-8", "replace"),
                                data[2 + tl:].decode("utf-8", "replace")))
            except OSError:
                continue
        return out

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


# 旁观者：只订阅 System/FanInfo，独立判定遥测是否在流
class Watcher:
    def __init__(self):
        self.c = Conn(4)
        self.buf = []

    def start(self):
        if not self.c.connect():
            return False
        self.c.subscribe(["System/FanInfo"])
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _loop(self):
        while True:
            self.buf += self.c.drain(1.0)

    def count(self, seconds):
        a = len(self.buf)
        time.sleep(seconds)
        return sum(1 for _f, tp, _p in self.buf[a:] if tp == "System/FanInfo")


def kill_many(names):
    fn = "_cmd_kill_many.txt"
    with open(fn, "w", encoding="utf-8") as f:
        for n in names:
            f.write("taskkill /F /IM %s\n" % n)
        f.write("exit /b 0\n")
    subprocess.run([sys.executable, "_elev.py", "@" + fn, "40"],
                   capture_output=True, text=True, timeout=120)


def main():
    wat = Watcher()
    if not wat.start():
        say("!! 旁观者连不上")
        return
    say("旁观者就位（client=4，只订阅 System/FanInfo）")

    say("=== 清场 ===")
    kill_many(["CCUWinUI.exe", "SystrayComponent.exe", "OSDTpDetect.exe",
               "GCUService.exe"])
    t_kill = el()
    while el() - t_kill < 110:
        time.sleep(2.5)
        if "GCUService.exe" in alive_names() and el() - t_kill > 45:
            break
    say("  相关进程: %s" % sorted(
        n for n in alive_names()
        if any(k in n.lower() for k in ("gcu", "ccu", "systray", "osd"))))
    say("=== 断言冷状态 20 秒 ===")
    n = wat.count(20)
    say("  FanInfo %d 条 → %s" % (n, "未武装 ✓" if n == 0 else "在流 ✗"))

    VARIANTS = [
        ("C1 client=1  QoS0 retain=0", 1, 0, False, [], False),
        ("C2 client=2  QoS0 retain=0", 2, 0, False, [], False),
        ("C3 client=7  QoS0 retain=1", 7, 0, True, [], False),
        ("C4 client=7  QoS1 retain=0", 7, 1, False, [], False),
        ("C5 client=1  订阅 System/# 后发", 1, 0, False, ["System/#"], False),
    ]

    winner = None
    for label, idx, qos, retain, subs, _ in VARIANTS:
        say("=== %s ===" % label)
        c = Conn(idx)
        if not c.connect():
            say("  连不上 client=%d（可能被占用）" % idx)
            continue
        if subs:
            c.subscribe(subs)
            time.sleep(0.8)
        t = el()
        c.send(mqtt_publish("System/Control", '{"Action":"System_ON"}',
                            qos=qos, retain=retain))
        n = wat.count(20)
        if n > 0:
            say("  >>> 武装成功！(%d 条，延迟 %.1fs)" % (n, el() - t))
            winner = label
            c.close()
            break
        say("  >>> 无效")
        c.close()

    say("=== 启动控制台，录原始标志位 ===")
    obs = Conn(7)
    if not obs.connect():
        say("!! 观察端连不上")
        return
    obs.subscribe(["#"])
    time.sleep(0.5)
    subprocess.Popen(["explorer.exe", AUMID])
    say("  已发起 explorer.exe " + AUMID)
    ev = obs.drain(45)

    say("---- System/Control 上的命令（含原始标志位）----")
    for flags, tp, pl in ev:
        if tp == "System/Control":
            qos = (flags >> 1) & 0x3
            say("  flags=0x%02X  qos=%d retain=%d dup=%d  %s"
                % (flags, qos, flags & 1, (flags >> 3) & 1, pl))

    say("---- 控制台启动后 45 秒内出现的非周期报文 ----")
    for flags, tp, pl in ev:
        if tp not in PERIODIC:
            say("  flags=0x%02X %-30s %s" % (flags, tp, pl[:130]))

    say("---- 周期遥测 ----")
    for tp in sorted(PERIODIC):
        ts = [1 for f, t2, _ in ev if t2 == tp]
        say("  %-24s %d 条" % (tp, len(ts)))
    say("  旁观者判定: 控制台启动后 25 秒内 FanInfo %d 条" % wat.count(25))
    say("结论: %s" % (("上述变体 %s 即可武装" % winner) if winner
                      else "MQTT 层所有变体都无效 → 必须靠拉起控制台组件"))


if __name__ == "__main__":
    main()
