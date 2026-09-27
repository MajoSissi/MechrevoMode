# 按「正式实现要用的方式」验证遥测：只订阅三个 Info 主题，不动别的。
#
# 要回答三件事：
#   1. 只订阅这三个主题，数据会不会照常来（不是靠 '#' 通配符才来的）
#   2. 刷新频率是多少（决定我们在程序里能不能按秒读）
#   3. 每个字段的**完整**列表和类型 —— 尤其「功耗」到底在不在里面，
#      以及数值是字符串还是数字（本机实测 CpuTemperature 是 "62" 而 CpuFanRpm 是 3003）
import json
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688
WATCH = ["System/CpuInfo", "System/GpuInfo", "System/FanInfo", "System/MemoryInfo"]
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0


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


def main():
    s = None
    for i in range(1, 10):
        s = connect(i)
        if s:
            print("已连接 clientID=UWPClient_%d" % i, flush=True)
            break
    if not s:
        print("!! 无空闲 clientID", flush=True)
        return 2

    # 只订阅这四个具体主题（不使用 #），这与正式实现一致
    sub = struct.pack(">H", len(WATCH))
    for t in WATCH:
        sub += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub)) + sub)
    r = read_packet(s, 5)
    print("SUBACK = %r" % (list(r[2]) if r else None), flush=True)
    print("只订阅 %s，观察 %.0f 秒…\n" % (WATCH, DURATION), flush=True)

    stamps = {t: [] for t in WATCH}
    last = {}
    deadline = time.time() + DURATION
    while time.time() < deadline:
        r = read_packet(s, max(0.05, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        data = r[2]
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        if topic in stamps:
            stamps[topic].append(time.time())
            last[topic] = data[2 + tl:]
    s.close()

    print("=" * 74)
    print("刷新频率")
    print("=" * 74)
    for t in WATCH:
        ts = stamps[t]
        if len(ts) < 2:
            print("  %-20s 只收到 %d 条" % (t, len(ts)))
            continue
        span = ts[-1] - ts[0]
        gaps = [round((ts[i + 1] - ts[i]), 3) for i in range(len(ts) - 1)]
        print("  %-20s %2d 条 / %.1f 秒  平均 %.2f 秒一条  间隔 %s"
              % (t, len(ts), span, span / (len(ts) - 1), gaps))

    print()
    print("=" * 74)
    print("完整字段与类型（最后一次）")
    print("=" * 74)
    for t in WATCH:
        if t not in last:
            continue
        print("\n---- [%s]" % t)
        try:
            obj = json.loads(last[t].decode("utf-8"))
        except Exception as e:
            print("  解析失败 %s: %r" % (e, last[t][:200]))
            continue
        for k, v in obj.items():
            print("    %-20s %-10s %r" % (k, type(v).__name__, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
