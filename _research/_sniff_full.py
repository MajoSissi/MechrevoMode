# 订阅 GCU broker 的**全部** topic，把所有报文的完整原始内容落盘。
#
# 目的：确定「当前模式的功耗墙 / 温度墙」到底有没有在 MQTT 里发布。
# 这是本需求的前提 —— 数据拿不到就没法显示，也不能编。
#
# 手写最小 MQTT 3.1.1 客户端（不引第三方库）。关键细节：
#   * 剩余长度字段最多 4 字节、低 7 位有效、高位是「还有后续字节」标志。
#   * PUBLISH 的 topic 前面是 2 字节大端长度。
#   * 连接要用官方那套 clientID/用户名/密码，否则 broker 直接拒。
import json
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688
INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 3
WAIT = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
CLIENT_ID = "UWPClient_%d" % INDEX
USERNAME = "UWPClient_User_%d" % INDEX
PASSWORD = "UWPClient_Pwd888881772688_%d" % INDEX
TOPIC_CTRL = "Fan/Control"


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


def main():
    out = "_sniff_full.txt"
    lines = []

    def p(s=""):
        print(s, flush=True)
        lines.append(s)

    p("连接 %s:%d  clientID=%s" % (HOST, PORT, CLIENT_ID))
    s = socket.create_connection((HOST, PORT), timeout=5)

    vh = enc_str("MQTT") + bytes([0x04]) + bytes([0xC2]) + struct.pack(">H", 25)
    payload = enc_str(CLIENT_ID) + enc_str(USERNAME) + enc_str(PASSWORD)
    s.sendall(bytes([0x10]) + enc_len(len(vh) + len(payload)) + vh + payload)
    r = read_packet(s, 5)
    if not r or r[0] != 2 or r[2][1] != 0:
        p("  !! CONNACK 失败: %s" % (r,))
        return 2
    p("  CONNACK ok")

    # 通配符订阅：一次看清 broker 上到底有哪些 topic
    sub_payload = struct.pack(">H", 1) + enc_str("#") + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub_payload)) + sub_payload)
    r = read_packet(s, 5)
    p("  SUBACK rc=%s" % (r[2][2] if r and len(r[2]) >= 3 else "?",))

    body = b'{"Action":"GETSTATUS"}'
    pv = enc_str(TOPIC_CTRL)
    s.sendall(bytes([0x30]) + enc_len(len(pv) + len(body)) + pv + body)
    p("  已发 GETSTATUS，收集 %.0f 秒…" % WAIT)
    p("")

    seen = {}
    fields = {}
    deadline = time.time() + WAIT
    n = 0
    while time.time() < deadline:
        r = read_packet(s, max(0.1, deadline - time.time()))
        if not r:
            continue
        typ, _f, data = r
        if typ != 3:
            continue
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        raw = data[2 + tl:]
        n += 1
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            obj = None
        pretty = json.dumps(obj, ensure_ascii=False, indent=2) if isinstance(obj, dict) \
            else raw.decode("utf-8", "replace")
        p("---- #%d [%s] (%d 字节)" % (n, topic, len(raw)))
        p(pretty)
        p("")
        seen.setdefault(topic, raw)
        if isinstance(obj, dict):
            for k in obj:
                fields.setdefault(topic, set()).add(k)

    s.close()

    p("=" * 60)
    p("共 %d 条报文，topic 清单：" % n)
    for t in sorted(seen):
        p("  %-24s %d 字节" % (t, len(seen[t])))
    p("")
    p("各 topic 出现过的字段：")
    for t in sorted(fields):
        p("  %s" % t)
        for k in sorted(fields[t]):
            p("      %s" % k)

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n已写入 %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
