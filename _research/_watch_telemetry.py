# -*- coding: utf-8 -*-
"""精确记录遥测主题的到达时刻（相对订阅时刻），用来判断「谁把它唤醒的」。

关键问题：System/FanInfo 是 GCUService 自发周期推送，还是被某个动作触发？
判据：
  * 订阅后**立刻**开始 2 秒节奏  → 已经处于「武装」状态，与本次订阅无关
  * 订阅后**隔了一段**才开始     → 要么是订阅触发，要么是它自己的轮询周期

用法: python _watch_telemetry.py <秒数> [主题,主题...]
"""

import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688

# 只关心这些；传空则监听 '#'
DEFAULT = ["System/FanInfo", "System/CpuInfo", "System/GpuInfo",
           "System/MemoryInfo", "System/NetworkInfo", "System/DiskInfo"]


def enc_len(n):
    out = b""
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out += bytes([b])
        if n == 0:
            return out


def enc_str(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def read_packet(sock, timeout):
    sock.settimeout(timeout)
    try:
        head = sock.recv(1)
        if not head:
            return None
        mult, val = 1, 0
        for _ in range(4):
            b = sock.recv(1)[0]
            val += (b & 0x7F) * mult
            if not (b & 0x80):
                break
            mult *= 128
        data = b""
        while len(data) < val:
            chunk = sock.recv(val - len(data))
            if not chunk:
                break
            data += chunk
        return head[0] >> 4, head[0] & 0x0F, data
    except (socket.timeout, OSError):
        return None


def connect(index):
    s = socket.create_connection((HOST, PORT), timeout=5)
    vh = enc_str("MQTT") + bytes([0x04]) + bytes([0xC2]) + struct.pack(">H", 25)
    payload = (enc_str("UWPClient_%d" % index) + enc_str("UWPClient_User_%d" % index)
               + enc_str("UWPClient_Pwd888881772688_%d" % index))
    s.sendall(bytes([0x10]) + enc_len(len(vh) + len(payload)) + vh + payload)
    r = read_packet(s, 5)
    if not r or r[0] != 2 or r[2][1] != 0:
        s.close()
        return None
    return s


def main():
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    topics = sys.argv[2].split(",") if len(sys.argv) > 2 else DEFAULT

    s, used = None, -1
    for i in range(1, 12):
        s = connect(i)
        if s:
            used = i
            break
    if not s:
        print("!! 没有空闲 clientID")
        return 2

    payload = struct.pack(">H", len(topics))
    for t in topics:
        payload += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(payload)) + payload)
    r = read_packet(s, 5)
    print("已连接 UWPClient_%d（SUBACK rc=%s），监听 %.0f 秒：%s"
          % (used, r[2][2] if r and len(r[2]) >= 3 else "?", wait, topics), flush=True)

    t0 = time.time()
    seen = {}
    while True:
        left = wait - (time.time() - t0)
        if left <= 0:
            break
        r = read_packet(s, max(0.1, left))
        if not r or r[0] != 3:
            continue
        _t, _f, data = r
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        raw = data[2 + tl:]
        now = time.time() - t0
        seen.setdefault(topic, []).append(now)
        if topic == "System/FanInfo":
            body = raw.decode("utf-8", "replace")
            try:
                rpm = struct.unpack(">H", b"\x00\x00")  # 占位，避免误用
            except Exception:
                print("!!!Z")
            print("  +%6.2fs  FanInfo  %s" % (now, body), flush=True)

    s.close()
    print("\n==== 汇总 ====", flush=True)
    for t in sorted(seen):
        ts = seen[t]
        first = ts[0]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        avg = sum(gaps) / len(gaps) if gaps else 0
        maxgap = max(gaps) if gaps else 0
        print("  %-24s 共 %-3d 条  首条 +%6.2fs  平均间隔 %.2fs  最大间隔 %.2fs"
              % (t, len(ts), first, avg, maxgap), flush=True)
    if not seen:
        print("  （一条都没有）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
