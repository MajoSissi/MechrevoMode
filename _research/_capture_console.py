# -*- coding: utf-8 -*-
"""抓「启动官方控制台」前后 broker 上的全部报文，找出武装遥测的那条命令。

思路：周期遥测（每 2 秒那几条）是已知噪音，用 topic+payload 去重压掉；
真正有价值的是一次性出现的、带 Action 的报文 —— 那才是控制台下的命令。

用法: python _capture_console.py [总秒数] [启动控制台的延迟秒]
"""

import socket
import struct
import subprocess
import sys
import time

HOST, PORT = "127.0.0.1", 13688
CONSOLE = r"C:\Program Files\OEM\机械革命控制中心\GamingCenter\ControlCenterU.exe"

# 已知的周期遥测，单独统计、不逐条打印
PERIODIC = {"System/FanInfo", "System/CpuInfo", "System/GpuInfo",
            "System/MemoryInfo", "System/NetworkInfo", "System/DiskInfo"}


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
    total = float(sys.argv[1]) if len(sys.argv) > 1 else 55.0
    launch_at = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

    s, used = None, -1
    for i in range(1, 12):
        s = connect(i)
        if s:
            used = i
            break
    if not s:
        print("!! 没有空闲 clientID")
        return 2

    topics = ["#"]
    payload = struct.pack(">H", len(topics))
    for t in topics:
        payload += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(payload)) + payload)
    read_packet(s, 5)
    print("已连接 UWPClient_%d，订阅 #，抓 %.0f 秒；将在 +%.0fs 启动控制台"
          % (used, total, launch_at), flush=True)

    t0 = time.time()
    launched = False
    events = []          # (t, topic, payload)
    counts = {}

    while True:
        el = time.time() - t0
        if el >= total:
            break
        if not launched and el >= launch_at:
            launched = True
            try:
                subprocess.Popen([CONSOLE], cwd=CONSOLE.rsplit("\\", 1)[0])
                print("  >>> 已启动控制台: %s" % CONSOLE, flush=True)
            except Exception as e:
                print("  >>> 启动控制台失败: %r" % (e,), flush=True)

        r = read_packet(s, max(0.1, min(1.0, total - el)))
        if not r or r[0] != 3:
            continue
        _t, _f, data = r
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        raw = data[2 + tl:]
        el = time.time() - t0
        counts[topic] = counts.get(topic, 0) + 1
        events.append((el, topic, raw))
        if topic not in PERIODIC:
            print("  +%6.2fs  %-32s %s"
                  % (el, topic, raw[:200].decode("utf-8", "replace")), flush=True)

    s.close()
    print("\n==== 全部主题统计 ====", flush=True)
    for t in sorted(counts):
        tag = "（周期遥测）" if t in PERIODIC else ""
        print("  %-34s ×%-5d %s" % (t, counts[t], tag), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
