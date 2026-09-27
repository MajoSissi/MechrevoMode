# -*- coding: utf-8 -*-
"""定位「武装」的**充分条件**：逐级试变体，最后完整录下控制台的握手。

已确认的事实：
  * 控制台启动后会发 System/Control {"Action":"System_ON"}，随后约 3 秒遥测开始
  * 但我们**单独发这一条**没用（试过，40 秒无反应）→ 它不是充分条件
  * 杀掉 GCUService 会掉武装；关掉控制台窗口也会掉（上一步实测）

所以要么需要成套命令，要么门槛不在 MQTT 层。这里逐级加码：

  V1  先订阅 System/# 再发 System_ON      —— 排除「订阅是前提」
  V2  完整重放控制台那批命令              —— 排除「成套才好使」
  V3  真启动控制台，录满 60 秒            —— 兜底 + 看它到底发几次

每一级之间用「扇速是否恢复」判定。
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
        ln = struct.unpack_from("<H", blob, off + 0x38)[0]
        bp = struct.unpack_from("<Q", blob, off + 0x40)[0]
        out[pid] = (ctypes.string_at(bp, ln).decode("utf-16-le", "replace")
                    if ln and bp else "")
        if nxt == 0:
            break
        off += nxt
    return out


def mqtt_packet(topic, payload, retain=False):
    t = topic.encode()
    p = struct.pack(">H", len(t)) + t + payload.encode()
    n = len(p)
    vb = b""
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        vb += bytes([b])
        if n == 0:
            break
    return bytes([0x30 | (1 if retain else 0)]) + vb + p


class Client:
    def __init__(self, prefer=None):
        self.sock = None
        self.idx = prefer

    def connect(self):
        pool = [self.idx] if self.idx else [8, 9, 7, 6, 5]
        for i in pool:
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
                self.sock, self.idx = s, i
                return True
            s.close()
        return False

    def subscribe(self, topics):
        p = struct.pack(">H", len(topics))
        for t in topics:
            p += struct.pack(">H", len(t)) + t.encode() + bytes([0])
        vb = b""
        n = len(p)
        while True:
            b = n % 128
            n //= 128
            if n > 0:
                b |= 0x80
            vb += bytes([b])
            if n == 0:
                break
        self.sock.sendall(bytes([0x82]) + vb + p)
        self.sock.settimeout(0.6)
        try:
            self.sock.recv(64)
        except OSError:
            pass

    def publish(self, topic, payload, retain=False):
        self.sock.sendall(mqtt_packet(topic, payload, retain))


class Observer:
    def __init__(self):
        self.events = []
        self.ready = threading.Event()
        self.stop = False

    def run(self):
        c = Client(8)
        if not c.connect():
            say("!! 观察者连不上")
            self.ready.set()
            return
        c.subscribe(["#"])
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
                self.events.append((el(), tp, head[0] & 1,
                                    data[2 + tl:].decode("utf-8", "replace")))
            except OSError:
                continue

    def count(self, topic, a, b=None):
        b = el() if b is None else b
        return sum(1 for t, tp, _r, _p in self.events
                   if tp == topic and a <= t <= b)


def wait_armed(obs, t_from, seconds, label):
    for i in range(int(seconds / 2.5)):
        time.sleep(2.5)
        if obs.count("System/FanInfo", t_from) > 0:
            say(f"  >>> {label} 起效了！延迟 {el() - t_from:.1f}s")
            return True
    say(f"  >>> {label} 无效（{seconds:.0f}s 内遥测没恢复）")
    return False


def main():
    obs = Observer()
    threading.Thread(target=obs.run, daemon=True).start()
    obs.ready.wait(15)

    p = sorted(n for n in {v for v in procs().values()} if "gcu" in n.lower())
    say("当前 GCU 进程: %s" % p)

    pub = Client(7)
    if not pub.connect():
        say("!! 发布端连不上")
        return
    say("发布端 client=UWPClient_%d" % pub.idx)

    # ---------------- V1：先订阅再发
    say("=== V1：订阅 System/# + Fan/# 后发 System_ON（并带 retain）===")
    t = el()
    pub.subscribe(["System/#", "Fan/#"])
    time.sleep(1.0)
    pub.publish("System/Control", '{"Action":"System_ON"}')
    ok1 = wait_armed(obs, t, 25, "V1")

    # ---------------- V2：完整重放控制台那批命令
    if not ok1:
        say("=== V2：完整重放控制台启动时那批命令 ===")
        batch = [
            ("Customize/Control", '{"Action":"GETSETUPINFO"}'),
            ("Customize/Control", '{"Action":"GETSUPPORT"}'),
            ("Customize/SupportControl", '{"Action":"GETSUPPORT"}'),
            ("Setting/Control", '{"Action":"GETSTATUS"}'),
            ("Fan/Control", '{"Action":"GETSTATUS"}'),
            ("System/Control", '{"Action":"System_ON"}'),
        ]
        t = el()
        for tp, pl in batch:
            pub.publish(tp, pl)
            say("    → %-28s %s" % (tp, pl))
            time.sleep(0.35)
        ok2 = wait_armed(obs, t, 30, "V2")
    else:
        ok2 = True

    # ---------------- V3：真启动控制台，录满 60 秒
    if not (ok1 or ok2):
        say("=== V3：真启动控制台，完整录 60 秒 ===")
        subprocess.Popen(["explorer.exe", AUMID])
        t = el()
        say("  已发起: explorer.exe " + AUMID)
        wait_armed(obs, t, 40, "V3")
        say("  再静观 20 秒，看它是否重复发命令…")
        time.sleep(20)

    # ---------------- 汇总
    say("---- 全程非周期报文（retain 列标出保留位）----")
    for t, tp, r, pl in obs.events:
        if tp not in PERIODIC:
            say("  +%7.2fs  %s %-30s %s" % (t, "R" if r else " ", tp, pl[:150]))

    say("---- System/Control 上的全部命令 ----")
    sc = [(t, pl) for t, tp, r, pl in obs.events if tp == "System/Control"]
    if not sc:
        say("  （无）")
    for t, pl in sc:
        say("  +%7.2fs  %s" % (t, pl))

    say("---- 周期遥测 ----")
    for tp in sorted(PERIODIC):
        ts = [t for t, t2, _r, _p in obs.events if t2 == tp]
        say("  %-24s %s" % (tp, ("共 %d 条 首条 +%.2fs" % (len(ts), ts[0]))
                            if ts else "全程没有"))
    obs.stop = True


if __name__ == "__main__":
    main()
