# 找「什么动作能让 GCU 重新开始推 System/* 遥测」。
#
# 已知事实：
#   * 16:59 遥测正常（2.00 秒一条，_sysinfo_verify.py 有记录）
#   * 17:10 之后一个序号都收不到，而 Fan/Status（GETSTATUS 的直接应答）一直正常
#   * 官方控制台 L-Mechrevo.exe 当时没在运行，只有 SystrayComponent.exe
# 所以猜遥测是被某个动作 / 某个客户端打开或关闭的。这里逐个候选试。
#
# 每试一个动作：连上、订阅 3 个 Info 主题、发动作、观察 N 秒、报条数，
# 顺便把收到的**所有**主题打出来（万一它回在别的主题上）。
import json
import struct
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 13688
WATCH = ["System/CpuInfo", "System/FanInfo", "System/GpuInfo", "Monitor/Status",
         "GamingMonitor/Status", "System/HardwareInfo",
         # Fan/Status 是 GETSTATUS 的直接应答，拿它当**对照组**：
         # 它要是一条都不来，说明这轮探测本身就没连对，结论不可信。
         "Fan/Status", "Tray/Status"]
OBSERVE = 7.0

CANDIDATES = [
    ("System/Control", "GAMINGMONITOR_ENABLE"),
    ("GamingMonitor/Control", "GAMINGMONITOR_ENABLE"),
    ("GamingMonitor/Control", "ENABLE"),
    ("GamingMonitor/Control", "START"),
    ("Monitor/Control", "START"),
    ("System/Control", "START"),
    ("System/Control", "INIT"),
    ("System/Control", "GETINFO"),
    ("Fan/Control", "GETSTATUS"),
]


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


def connect(index=6):
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


def wait_for(count, seconds, label):
    """订阅 + 观察，返回 {topic: (条数, 最后一条原文)}"""
    s = connect()
    if not s:
        print("  !! 连不上 broker")
        return None
    sub = struct.pack(">H", len(WATCH))
    for t in WATCH:
        sub += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub)) + sub)
    read_packet(s, 5)

    if count is not None:
        body = enc_str(count[0]) + count[1].encode("utf-8")
        s.sendall(bytes([0x30]) + enc_len(len(body)) + body)

    seen = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = read_packet(s, max(0.05, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        d = r[2]
        tl = struct.unpack(">H", d[:2])[0]
        topic = d[2:2 + tl].decode("utf-8", "replace")
        n, _ = seen.get(topic, (0, b""))
        seen[topic] = (n + 1, d[2 + tl:])
    s.close()

    infos = sum(v[0] for k, v in seen.items() if "/CpuInfo" in k or "/FanInfo" in k or "/GpuInfo" in k)
    print("  %-46s Info=%d  全部主题=%s" % (label, infos, {k: v[0] for k, v in seen.items()}))
    return seen


def main():
    print("=== 基线（不发任何动作）===")
    wait_for(None, OBSERVE, "只订阅")

    print("\n=== 逐个候选动作（每个观察 %.0f 秒）===" % OBSERVE)
    for topic, act in CANDIDATES:
        wait_for((topic, json.dumps({"Action": act})), OBSERVE, "%s <- %s" % (topic, act))

    print("\n=== 试完再测一次基线 ===")
    wait_for(None, OBSERVE, "只订阅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
