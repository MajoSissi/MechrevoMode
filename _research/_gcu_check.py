# 复刻本工具的 GCU 握手，判断「MQTT broker 单独在，是否就够用」。
#
# 做三件事：CONNECT(带用户名密码) → SUBSCRIBE Fan/Status + Tray/Status
# → PUBLISH Fan/Control {"Action":"GETSTATUS"}，然后收 6 秒看有没有状态回来。
#
# 手写最小 MQTT 3.1.1 客户端，避免依赖第三方库。
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688
TOPIC_FAN_STAT = "Fan/Status"
TOPIC_TRAY_STAT = "Tray/Status"
TOPIC_FAN_CTRL = "Fan/Control"

INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 3
CLIENT_ID = "UWPClient_%d" % INDEX
USERNAME = "UWPClient_User_%d" % INDEX
PASSWORD = "UWPClient_Pwd888881772688_%d" % INDEX


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
    """返回 (header_type, flags, payload) 或 None"""
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
    except socket.timeout:
        return None
    except OSError:
        return None


def main():
    print("连接 %s:%d  clientID=%s" % (HOST, PORT, CLIENT_ID), flush=True)
    s = socket.create_connection((HOST, PORT), timeout=5)

    # --- CONNECT ---
    vh = enc_str("MQTT") + bytes([0x04]) + bytes([0xC2]) + struct.pack(">H", 25)
    payload = enc_str(CLIENT_ID) + enc_str(USERNAME) + enc_str(PASSWORD)
    pkt = bytes([0x10]) + enc_len(len(vh) + len(payload)) + vh + payload
    s.sendall(pkt)

    r = read_packet(s, 5)
    if not r or r[0] != 2:
        print("  !! 没有收到 CONNACK:", r, flush=True)
        return 2
    rc = r[2][1]
    print("  CONNACK 返回码 = %d %s" % (rc, "(接受)" if rc == 0 else "(拒绝)"), flush=True)
    if rc != 0:
        return 2

    # --- SUBSCRIBE ---
    sub_payload = struct.pack(">H", 1) + enc_str(TOPIC_FAN_STAT) + bytes([0]) \
        + enc_str(TOPIC_TRAY_STAT) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub_payload)) + sub_payload)
    r = read_packet(s, 5)
    print("  SUBACK =", r[2].hex() if r else "无", flush=True)

    # --- PUBLISH GETSTATUS ---
    pub_vh = enc_str(TOPIC_FAN_CTRL)
    body = b'{"Action":"GETSTATUS"}'
    s.sendall(bytes([0x30]) + enc_len(len(pub_vh) + len(body)) + pub_vh + body)
    print("  已发起 GETSTATUS，等待状态推送…", flush=True)

    got = []
    deadline = time.time() + 6
    while time.time() < deadline:
        r = read_packet(s, max(0.1, deadline - time.time()))
        if not r:
            continue
        typ, _flags, data = r
        if typ == 3:  # PUBLISH
            tl = struct.unpack(">H", data[:2])[0]
            topic = data[2:2 + tl].decode("utf-8", "replace")
            body = data[2 + tl:]
            got.append((topic, body))
            print("  收到 [%s] %s" % (topic, body[:200].decode("utf-8", "replace")), flush=True)

    s.close()
    print()
    if got:
        print("=> 拿到状态，broker 侧可用（%d 条消息）" % len(got), flush=True)
        return 0
    print("=> 连接成功但【没有收到任何状态】—— 说明还需要后端进程（GCUService 等）", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
