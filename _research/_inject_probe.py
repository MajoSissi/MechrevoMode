# 对照实验：一条连接订阅 System/FanInfo + Fan/Status，另一条连接往 System/FanInfo 发布。
# 用来区分两种可能：
#   A. broker 根本不把第三方客户端发的 System/FanInfo 转发出去（ACL / 只认官方发布者）
#   B. broker 转发正常，是 MechrevoMode 没订阅上
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688
WATCH = "System/FanInfo"
CTRL = "Fan/Status"


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
        if s:
            s.close()
        return None
    return s


def subscribe(s, topics):
    sub = struct.pack(">H", len(topics))
    for t in topics:
        sub += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub)) + sub)
    return read_packet(s, 5)


def publish(s, topic, body, retain=False):
    p = enc_str(topic) + body.encode("utf-8")
    flags = 0x31 if retain else 0x30
    s.sendall(bytes([flags]) + enc_len(len(p)) + p)


def main():
    sub = connect(7)
    pub = connect(8)
    if not sub or not pub:
        print("!! 起不来两条连接", flush=True)
        return 2
    r = subscribe(sub, [WATCH, CTRL])
    print("订阅 %s + %s -> SUBACK %r" % (WATCH, CTRL, list(r[2]) if r else None), flush=True)

    body = '{"CpuFanDuty":55,"GpuFanDuty":55,"CpuFanRpm":3111,"GpuFanRpm":2222}'
    publish(pub, WATCH, body)
    print("已从 UWPClient_8 发布 %s -> %s" % (WATCH, body), flush=True)

    deadline = time.time() + 8
    got = []
    while time.time() < deadline:
        r = read_packet(sub, max(0.1, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        data = r[2]
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        got.append((topic, data[2 + tl:].decode("utf-8", "replace")[:120]))

    if not got:
        print("=> 订阅端一条都没收到：broker 不转发第三方发布的 System/FanInfo（可能被 ACL 拦），"
              "所以这条注入路线本身无效，不能用来判断程序有没有订阅上。", flush=True)
    else:
        for t, b in got:
            print("  收到 [%s] %s" % (t, b), flush=True)
        print("=> broker 转发正常。若此时程序日志仍无变化，说明是程序侧没订阅上。", flush=True)
    sub.close()
    pub.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
