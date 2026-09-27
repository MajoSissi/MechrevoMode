# -*- coding: utf-8 -*-
"""对照实验：结束 GCUService → 观察是否自发回来 → 唤起控制台 → 看遥测是否恢复。

背景（都是实测结论，不是推测）：
  * 开机后 GCUBridge/GCUService 都在跑，Fan/Status 立即有，但 System/FanInfo
    一条都没有，直到用户手动打开一次控制台。
  * 13688 上**没有任何第三方 MQTT 客户端**，FanInfo 是 GCUBridge 自己发的
    （GCUService 是它的子进程，走 IPC）。所以武装动作不在 MQTT 层。
  * 武装状态是**每次开机重置**的，且存在于长命的 GCUService 进程里
    —— 所以结束它 = 回到未武装状态。

实验全程有一个独立的 MQTT 观察者（订阅 #）在场，边观察边落盘，
只要有任何非周期报文出现就能抓到。
"""

import ctypes
import socket
import struct
import subprocess
import sys
import threading
import time

HOST, PORT = "127.0.0.1", 13688
AUMID = r"shell:AppsFolder\ControlCenter3_h329z55cwnj8g!App"
PERIODIC = {"System/FanInfo", "System/CpuInfo", "System/GpuInfo",
            "System/MemoryInfo", "System/NetworkInfo", "System/DiskInfo"}

ntdll = ctypes.WinDLL("ntdll")
KEYS = ("ccu", "systray", "gcu", "controlcenter", "gamingcenter",
        "mechrevo", "aistone", "osd")

T0 = time.time()


def el():
    return time.time() - T0


def say(msg):
    print("[%7.2fs] %s" % (el(), msg), flush=True)


# ---------------------------------------------------------------- 进程表

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
        ct = struct.unpack_from("<q", blob, off + 0x20)[0]
        ln = struct.unpack_from("<H", blob, off + 0x38)[0]
        bp = struct.unpack_from("<Q", blob, off + 0x40)[0]
        nm = (ctypes.string_at(bp, ln).decode("utf-16-le", "replace")
              if ln and bp else "")
        out[pid] = (nm, ppid, ct)
        if nxt == 0:
            break
        off += nxt
    return out


def gcu_procs():
    return {p: v for p, v in procs().items()
            if any(k in v[0].lower() for k in KEYS)}


def show(tag, d):
    say(f"{tag}（{len(d)} 个）:")
    for pid, (nm, ppid, ct) in sorted(d.items()):
        say(f"      pid={pid:<6} ppid={ppid:<6} {nm}")


# ---------------------------------------------------------------- MQTT 观察者

class Observer:
    def __init__(self):
        self.events = []          # (相对时刻, topic, payload)
        self.counts = {}
        self.stop = False
        self.sock = None
        self.ready = threading.Event()

    def _connect(self):
        for i in (8, 9, 7, 6):
            try:
                s = socket.create_connection((HOST, PORT), timeout=5)
            except OSError:
                continue
            cid = b"UWPClient_%d" % i
            user = b"UWPClient_User_%d" % i
            pwd = b"UWPClient_Pwd888881772688_%d" % i
            vh = b"\x00\x04MQTT\x04\xc2\x00\x19"
            pay = (struct.pack(">H", len(cid)) + cid
                   + struct.pack(">H", len(user)) + user
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
            say("!! 观察者连不上 broker")
            self.ready.set()
            return
        topic = b"#"
        payload = struct.pack(">H", 1) + struct.pack(">H", len(topic)) + topic + bytes([0])
        self.sock.sendall(bytes([0x82, len(payload)]) + payload)
        self.sock.settimeout(0.5)
        try:
            self.sock.recv(64)
        except OSError:
            pass
        say("观察者已就位（订阅 #，全程在场）")
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
                    chunk = self.sock.recv(val - len(data))
                    if not chunk:
                        break
                    data += chunk
                if head[0] >> 4 != 3:
                    continue
                tl = struct.unpack(">H", data[:2])[0]
                tp = data[2:2 + tl].decode("utf-8", "replace")
                raw = data[2 + tl:]
                self.events.append((el(), tp, raw.decode("utf-8", "replace")))
                self.counts[tp] = self.counts.get(tp, 0) + 1
            except OSError:
                continue
        try:
            self.sock.close()
        except OSError:
            pass

    def faninfo(self, t_from, t_to):
        """统计 [t_from, t_to] 区间内的 FanInfo 条数。"""
        return sum(1 for t, tp, _ in self.events
                   if tp == "System/FanInfo" and t_from <= t <= t_to)


# ---------------------------------------------------------------- 提权杀进程

def kill_gcuservice():
    with open("_cmd_kill_gcu.txt", "w", encoding="utf-8") as f:
        f.write("taskkill /F /IM GCUService.exe\n")
    r = subprocess.run([sys.executable, "_elev.py", "@_cmd_kill_gcu.txt", "30"],
                       capture_output=True, text=True, timeout=90)
    say("kill 输出: " + " / ".join(
        ln for ln in r.stdout.splitlines() if ln.strip())[:400])


def main():
    obs = Observer()
    threading.Thread(target=obs.run, daemon=True).start()
    obs.ready.wait(20)

    # ---------- Phase 0：基线
    say("=== Phase 0 基线 ===")
    base = gcu_procs()
    show("相关进程", base)
    t_a = el()
    time.sleep(6)
    t_b = el()
    n0 = obs.faninfo(t_a, t_b)
    say(f"基线 6 秒内 FanInfo {n0} 条 → {'已武装' if n0 > 0 else '未武装'}")
    if n0 == 0:
        say("！基线就已经是未武装状态，实验目标已达成，直接跳到唤起控制台")
    else:
        # ---------- Phase 1：结束 GCUService
        say("=== Phase 1 结束 GCUService.exe ===")
        kill_gcuservice()
        time.sleep(4)
        after_kill = gcu_procs()
        show("结束之后", after_kill)
        gone = set(base) - set(after_kill)
        say(f"消失的进程: {[base[p][0] for p in gone] or '（一个都没少，可能没杀掉）'}")

        # ---------- Phase 2：观察是否自发回来 + 遥测是否停
        say("=== Phase 2 观察 35 秒（是否自发回来 / 遥测是否停）===")
        respawned = None
        t_kill = el()
        for i in range(18):
            time.sleep(2)
            cur = gcu_procs()
            if respawned is None and any("gcu" in v[0].lower() for v in cur.values()):
                names = [v[0] for v in cur.values() if "gcu" in v[0].lower()]
                if names:
                    respawned = (el(), names)
                    say(f"  → GCU 进程回来了: {names}")
        n1 = obs.faninfo(t_kill, el())
        say(f"结束之后 {el() - t_kill:.0f} 秒内 FanInfo {n1} 条 → "
            f"{'仍在流（说明状态不在 GCUService）' if n1 > 0 else '停了（确认武装状态在 GCUService 进程里）'}")
        show("当前相关进程", gcu_procs())

    # ---------- Phase 3：唤起官方控制台
    say("=== Phase 3 用 explorer 唤起 UWP 控制台 ===")
    before = gcu_procs()
    try:
        subprocess.Popen(["explorer.exe", AUMID])
        say("已发起: explorer.exe " + AUMID)
    except OSError as e:
        say(f"发起失败: {e!r}")

    t_arm = el()
    for i in range(12):
        time.sleep(2.5)
        cur = gcu_procs()
        new = set(cur) - set(before)
        if new:
            for p in new:
                say(f"  → 新进程: pid={p} {cur[p][0]}")
            before = cur
        if obs.faninfo(t_arm, el()) > 0:
            say(f"  → 遥测已恢复！")
            break

    # ---------- Phase 4：总结
    say("=== Phase 4 总结 ===")
    n2 = obs.faninfo(t_arm, el())
    say(f"唤起控制台后 {el() - t_arm:.0f} 秒内 FanInfo {n2} 条 → "
        f"{'恢复' if n2 > 0 else '仍未恢复'}")
    show("最终相关进程", gcu_procs())

    say("==== 全程非周期报文（可能包含武装命令）====")
    nonperiodic = [(t, tp, pl) for t, tp, pl in obs.events if tp not in PERIODIC]
    if not nonperiodic:
        say("  （一条都没有 —— 武装动作确实不在 MQTT 层）")
    for t, tp, pl in nonperiodic:
        say("  +%7.2fs  %-32s %s" % (t, tp, pl[:200]))

    say("==== 周期遥测首条到达时刻 ====")
    for tp in sorted(PERIODIC):
        ts = [t for t, t2, _ in obs.events if t2 == tp]
        if ts:
            say("  %-24s 首条 +%.2fs  共 %d 条" % (tp, ts[0], len(ts)))
        else:
            say("  %-24s 全程没有" % tp)

    obs.stop = True


if __name__ == "__main__":
    main()
