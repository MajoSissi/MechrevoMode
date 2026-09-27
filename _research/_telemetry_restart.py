# 重启 GCUService.exe 之后，遥测（System/FanInfo 等）会不会恢复？
#
# 已知：16:59 遥测正常（2.00 秒一条），17:10 之后一条都没有，而 GCUService 进程
# 一直是同一个 pid 15892。所以要么是它的在途状态坏了，要么是某个外部条件被关掉了。
# 先排除「进程自身状态坏了」这一条：让它重启一次再看。
#
# 风险很低：GCUService 只是「读传感器 + 下发风扇表」，风扇曲线最终写在 EC 里；
# 而且 GCUBridge 会监护重生它（本项目的自愈逻辑也专门做过这件事）。
import json
import os
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _proclog

HOST, PORT = "127.0.0.1", 13688
WATCH = ["System/CpuInfo", "System/FanInfo", "System/GpuInfo"]


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
    cid = "UWPClient_%d" % index
    s = socket.create_connection((HOST, PORT), timeout=5)
    vh = enc_str("MQTT") + bytes([0x04]) + bytes([0xC2]) + struct.pack(">H", 25)
    payload = (enc_str(cid) + enc_str("UWPClient_User_%d" % index)
               + enc_str("UWPClient_Pwd888881772688_%d" % index))
    s.sendall(bytes([0x10]) + enc_len(len(vh) + len(payload)) + vh + payload)
    r = read_packet(s, 5)
    if not r or r[0] != 2 or r[2][1] != 0:
        s.close()
        return None
    return s


def sample(seconds):
    """订阅 3 个 Info 主题，观察若干秒，返回 {topic: 条数}"""
    s = connect(5)
    if not s:
        return None
    sub = struct.pack(">H", len(WATCH))
    for t in WATCH:
        sub += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub)) + sub)
    read_packet(s, 5)
    seen = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = read_packet(s, max(0.05, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        d = r[2]
        tl = struct.unpack(">H", d[:2])[0]
        t = d[2:2 + tl].decode("utf-8", "replace")
        n, _l = seen.get(t, (0, b""))
        seen[t] = (n + 1, d[2 + tl:])
    s.close()
    return seen


def gcu_procs():
    return {n: [p for p, x in _proclog.procs() if x.lower() == n.lower()]
            for n in ("GCUBridge.exe", "GCUService.exe")}


def main():
    print("重启前进程:", gcu_procs(), flush=True)
    print("\n[1] 重启前的基线（观察 8 秒）")
    print("   ", sample(8), flush=True)

    print("\n[2] 结束 GCUService.exe，等 GCUBridge 把它重生")
    r = subprocess.run(["taskkill", "/F", "/IM", "GCUService.exe"],
                       capture_output=True, text=True, errors="replace")
    print("    taskkill:", (r.stdout + r.stderr).strip(), flush=True)

    for i in range(12):
        time.sleep(10)
        pr = gcu_procs()
        print("    +%3ds  %s" % ((i + 1) * 10, pr), flush=True)
        if pr["GCUService.exe"]:
            break

    print("\n[3] 新生后观察 20 秒")
    for i in range(2):
        time.sleep(10)
        print("   ", sample(10), flush=True)
    print("\n最终进程:", gcu_procs(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
