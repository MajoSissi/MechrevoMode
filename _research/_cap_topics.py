# -*- coding: utf-8 -*-
"""长时后台抓包：订阅 '#'，把**非周期**报文实时写进文件（周期遥测只计数）。

用于「结束 GCUService → 观察 → 唤起控制台」这类实验：进程会被杀来杀去，
必须有一个独立于被测对象的观察者一直在场。

用法: python _cap_topics.py <秒数> <输出文件> [clientIndex]
"""

import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688

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
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    outpath = sys.argv[2] if len(sys.argv) > 2 else "_cap_topics.log"
    idxs = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [8, 9, 7]

    s, used = None, -1
    for i in idxs:
        s = connect(i)
        if s:
            used = i
            break
    if not s:
        with open(outpath, "a", encoding="utf-8") as f:
            f.write("!! 没有空闲 clientID\n")
        return 2

    topics = ["#"]
    payload = struct.pack(">H", len(topics))
    for t in topics:
        payload += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(payload)) + payload)
    read_packet(s, 5)

    f = open(outpath, "a", encoding="utf-8")
    f.write("=== 抓包开始 client=%d 时长=%.0fs ===\n" % (used, wait))
    f.flush()

    t0 = time.time()
    counts = {}
    while True:
        el = time.time() - t0
        if el >= wait:
            break
        r = read_packet(s, max(0.1, min(1.0, wait - el)))
        if not r:
            continue
        if r[0] != 3:
            continue
        _t, _f, data = r
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        raw = data[2 + tl:]
        counts[topic] = counts.get(topic, 0) + 1
        if topic not in PERIODIC:
            f.write("+%7.2fs  %-32s %s\n"
                    % (el, topic, raw[:300].decode("utf-8", "replace")))
            f.flush()
        elif counts[topic] == 1:
            f.write("+%7.2fs  [周期首条] %-24s %s\n"
                    % (el, topic, raw[:160].decode("utf-8", "replace")))
            f.flush()

    f.write("=== 抓包结束；各主题计数 ===\n")
    for t in sorted(counts):
        f.write("   %-34s ×%d\n" % (t, counts[t]))
    f.flush()
    f.close()
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
