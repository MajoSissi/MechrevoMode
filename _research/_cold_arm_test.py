# -*- coding: utf-8 -*-
"""干净版武装实验：先把所有 OEM UI 组件清掉，确保真的是「冷」状态。

上一轮的教训：机器上一直有 SystrayComponent.exe（OEM 托盘组件）活着，
它在后台可能周期性地重新武装遥测 —— 这会让「基线」根本不干净，
导致把「本来就已武装」误判成「我的命令起效了」。
所以这次先清场，并且**连续断言 20 秒未武装**才继续。

实验设计（一步失败才进下一步）：
  B1  客户端订阅 System/# + Fan/# 后发 System_ON
  B2  客户端订阅 # 后发 System_ON
  B3  客户端无任何订阅，只发 System_ON
  B4  再补发 Fan/Control GETSTATUS + System_ON

结束后重新拉起官方控制台，把 OEM 托盘图标还给用户。
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


def alive_names():
    return sorted({v[0] for v in procs().values()})


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


def kill_many(names):
    fn = "_cmd_kill_many.txt"
    with open(fn, "w", encoding="utf-8") as f:
        for n in names:
            f.write("taskkill /F /IM %s\n" % n)
        f.write("exit /b 0\n")
    r = subprocess.run([sys.executable, "_elev.py", "@" + fn, "40"],
                       capture_output=True, text=True, timeout=120)
    lines = [l.strip() for l in r.stdout.splitlines()
             if "SUCCESS" in l or "not found" in l.lower() or "没有" in l]
    for l in lines:
        say("    " + l[:130])


class Client:
    def __init__(self, prefer=None):
        self.sock = None
        self.idx = prefer

    def connect(self, pool=None):
        pool = pool or ([self.idx] if self.idx else [7, 6, 5, 4])
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

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def subscribe(self, topics):
        if not topics:
            return
        p = struct.pack(">H", len(topics))
        for t in topics:
            p += struct.pack(">H", len(t)) + t.encode() + bytes([0])
        self.sock.sendall(bytes([0x82]) + enc_len(len(p)) + p)
        self.sock.settimeout(0.6)
        try:
            self.sock.recv(4096)
        except OSError:
            pass

    def publish(self, topic, payload):
        t = topic.encode()
        p = struct.pack(">H", len(t)) + t + payload.encode()
        self.sock.sendall(bytes([0x30]) + enc_len(len(p)) + p)

    def drain(self, seconds):
        """收消息并返回 (topic, payload) 列表 —— 既是观察者也是被测对象。"""
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
                    out.append((data[2:2 + tl].decode("utf-8", "replace"),
                                data[2 + tl:].decode("utf-8", "replace")))
            except OSError:
                continue
        return out


# 独立的旁观者：只订阅 System/FanInfo，用来判定遥测是否在流。
# 用独立 client，避免「订阅行为本身」成为被测变量。
class Watcher:
    def __init__(self):
        self.c = Client(4)
        self.buf = []

    def start(self):
        if not self.c.connect([4]):
            say("!! 旁观者连不上")
            return False
        self.c.subscribe(["System/FanInfo"])
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()
        return True

    def _loop(self):
        while True:
            self.buf += self.c.drain(1.0)

    def count(self, seconds):
        a = len(self.buf)
        time.sleep(seconds)
        n = sum(1 for tp, _ in self.buf[a:] if tp == "System/FanInfo")
        return n, self.buf[a:]


def main():
    wat = Watcher()
    if not wat.start():
        return
    say("旁观者就位（只订阅 System/FanInfo，client=%d）" % wat.c.idx)

    # ---------- 清场
    say("=== 清场：结束所有 OEM UI 组件 + GCUService ===")
    kill_many(["CCUWinUI.exe", "SystrayComponent.exe", "OSDTpDetect.exe",
               "GCUService.exe"])
    say("  等 GCUBridge 把 GCUService 拉回来并就绪…")
    t_kill = el()
    ready_at = None
    while el() - t_kill < 120:
        time.sleep(2.5)
        names = alive_names()
        # GCUService 就绪的标志：它重新发布 Fan/Status（会出现在旁观者的缓存里
        # 只有订阅了才看得到，这里改用「进程在 + 已过 40 秒」近似）
        if "GCUService.exe" in names and el() - t_kill > 40:
            ready_at = el()
            break
    say("  GCUService 已回来：%s" % ("是" if "GCUService.exe" in alive_names() else "否"))
    say("  当前相关进程: %s"
        % [n for n in alive_names()
           if any(k in n.lower() for k in ("gcu", "ccu", "systray", "osd"))])

    # ---------- 断言冷状态
    say("=== 断言未武装：连续观察 20 秒 ===")
    n, _ = wat.count(20)
    say("  20 秒内 FanInfo %d 条 → %s"
        % (n, "未武装 ✓（前提成立）" if n == 0 else "居然在流 ✗（清场不彻底）"))
    if n > 0:
        say("  仍在流说明还有别的组件在武装它，本次结论不可用。")

    pub = Client(7)
    if not pub.connect([7]):
        say("!! 发布端连不上")
        return
    say("发布端 client=UWPClient_%d" % pub.idx)

    def try_variant(label, subs, pubs, wait=28):
        say("=== %s ===" % label)
        t = el()
        if subs:
            pub.subscribe(subs)
        time.sleep(1.0)
        for tp, pl in pubs:
            pub.publish(tp, pl)
            say("    → %-28s %s" % (tp, pl))
            time.sleep(0.4)
        n, seen = wat.count(wait)
        if n > 0:
            first = next((tp for tp, _ in seen if tp == "System/FanInfo"), None)
            say("  >>> 武装成功！%d 条（延迟约 %.1fs）" % (n, el() - t))
            return True
        say("  >>> 未武装（%d 秒无遥测）" % wait)
        return False

    # ---------- B1
    if try_variant("B1 订阅 System/# + Fan/# 后发 System_ON",
                   ["System/#", "Fan/#"],
                   [("System/Control", '{"Action":"System_ON"}')]):
        done = True
    else:
        # ---------- B2
        pub.close()
        pub = Client(7)
        pub.connect([7])
        if try_variant("B2 订阅 # 后发 System_ON",
                       ["#"], [("System/Control", '{"Action":"System_ON"}')]):
            done = True
        else:
            # ---------- B3
            pub.close()
            pub = Client(7)
            pub.connect([7])
            if try_variant("B3 无任何订阅，只发 System_ON",
                           [], [("System/Control", '{"Action":"System_ON"}')]):
                done = True
            else:
                # ---------- B4
                done = try_variant(
                    "B4 无订阅，GETSTATUS + System_ON 组合",
                    [],
                    [("Fan/Control", '{"Action":"GETSTATUS"}'),
                     ("System/Control", '{"Action":"System_ON"}')])

    say("=== 恢复：重新拉起官方控制台 ===")
    subprocess.Popen(["explorer.exe", AUMID])
    time.sleep(12)
    say("  当前相关进程: %s"
        % [n for n in alive_names()
           if any(k in n.lower() for k in ("gcu", "ccu", "systray", "osd"))])
    n, _ = wat.count(8)
    say("  恢复后 8 秒内 FanInfo %d 条" % n)


if __name__ == "__main__":
    main()
