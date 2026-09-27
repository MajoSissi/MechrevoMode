# 纯被动监听：订阅 '#' 之后**什么都不发**，看 broker 上自然流动的报文。
#
# 用来回答「System/CpuInfo 这类实时遥测是周期性推送，还是必须提问才有」：
# 如果发 GETSTATUS 才出数据，说明要先触发；如果一直静默，说明发布方（GCUService）
# 本身没在推 —— 那就要看官方控制台在不在跑。
import json
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688


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
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    topics = sys.argv[2].split(",") if len(sys.argv) > 2 else ["#"]

    s = None
    used = -1
    for i in range(1, 12):
        s = connect(i)
        if s:
            used = i
            break
    if not s:
        print("!! 没有空闲 clientID", flush=True)
        return 2
    print("已连接 UWPClient_%d，订阅 %s，被动监听 %.0f 秒（不发任何消息）…"
          % (used, topics, wait), flush=True)

    payload = struct.pack(">H", len(topics))
    for t in topics:
        payload += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(payload)) + payload)
    r = read_packet(s, 5)
    print("SUBACK rc=%s" % (r[2][2] if r and len(r[2]) >= 3 else "?",), flush=True)

    seen = {}
    deadline = time.time() + wait
    while time.time() < deadline:
        r = read_packet(s, max(0.1, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        _t, _f, data = r
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        raw = data[2 + tl:]
        seen.setdefault(topic, []).append((time.time(), raw))

    s.close()
    print("\n---- 收到的 topic ----", flush=True)
    for t in sorted(seen):
        ts = [x[0] for x in seen[t]]
        span = ts[-1] - ts[0]
        rate = ("%.2f 秒/条" % (span / (len(ts) - 1))) if len(ts) > 1 else "仅 1 次"
        print("  %-34s ×%-4d %-14s %s" % (t, len(seen[t]), rate,
                                          seen[t][-1][1][:150].decode("utf-8", "replace")), flush=True)
    if not seen:
        print("  （什么都没有 —— 没有任何客户端在周期推送）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
